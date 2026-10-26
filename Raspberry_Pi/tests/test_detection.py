"""모델 준비 경계와 비동기 추론의 세션·시간 격리를 검증한다."""

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from medikit_pi.detection import (
    AsyncDetector,
    Detection,
    SynchronousDetector,
    YOLOEDetector,
    prepare_model,
)


# 작업자가 결과를 전달할 때까지 짧은 제한 시간 안에서 기다린다.
def await_result(worker: AsyncDetector, now: float):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        result = worker.latest(now)
        if result is not None:
            return result
        time.sleep(0.005)
    raise AssertionError("추론 작업자가 결과를 전달하지 않았습니다.")


class RecordingDetector:
    # 첫 추론을 멈춰 대기 영상 교체와 세션 전환을 재현한다.
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = []

    # 영상 첫 픽셀을 표식으로 반환하여 처리한 프레임을 구별한다.
    def predict(self, frame):
        value = int(frame[0, 0, 0])
        self.calls.append(value)
        if len(self.calls) == 1:
            self.started.set()
            if not self.release.wait(2):
                raise TimeoutError("테스트 추론 대기 시간이 초과되었습니다.")
        return [Detection((0.1, 0.1, 0.4, 0.4), 0.8, str(value))]


class DetectionTests(unittest.TestCase):
    # 비동기 추론 중에는 가장 최신 대기 프레임만 남는지 확인한다.
    def test_latest_only_queue_and_copy(self):
        detector = RecordingDetector()
        worker = AsyncDetector(detector, interval_seconds=0.1, max_age_seconds=5)
        try:
            frame = np.ones((8, 8, 3), dtype=np.uint8)
            worker.submit(frame, 0)
            self.assertTrue(detector.started.wait(2))
            worker.submit(frame * 2, 0.2)
            worker.submit(frame * 3, 0.4)
            frame[:] = 9
            detector.release.set()
            deadline = time.monotonic() + 2
            while len(detector.calls) < 2 and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(detector.calls, [1, 3])
            result = await_result(worker, 0.5)
            self.assertIn(result.detections[0].label, {"1", "3"})
        finally:
            detector.release.set()
            worker.close()

    # 새 세션에서 이전 세션의 늦은 검출이 재사용되지 않는지 확인한다.
    def test_reset_discards_in_flight_result(self):
        first_started, first_release = threading.Event(), threading.Event()
        second_started, second_release = threading.Event(), threading.Event()

        # 두 추론을 각각 멈춰 이전 결과가 공개되는 순간을 검사한다.
        def predict(frame):
            value = int(frame[0, 0, 0])
            started, release = (
                (first_started, first_release) if value == 1 else (second_started, second_release)
            )
            started.set()
            if not release.wait(2):
                raise TimeoutError("테스트 추론 대기 시간이 초과되었습니다.")
            return [Detection((0.1, 0.1, 0.4, 0.4), 0.8, str(value))]

        detector = Mock()
        detector.predict.side_effect = predict
        worker = AsyncDetector(detector, interval_seconds=0.1, max_age_seconds=5)
        try:
            worker.submit(np.ones((8, 8, 3), dtype=np.uint8), 0)
            self.assertTrue(first_started.wait(2))
            worker.reset()
            worker.submit(np.full((8, 8, 3), 2, dtype=np.uint8), 0.4)
            first_release.set()
            self.assertTrue(second_started.wait(2))
            self.assertIsNone(worker.latest(0.5))
            second_release.set()
            result = await_result(worker, 0.5)
            self.assertEqual(result.detections[0].label, "2")
        finally:
            first_release.set()
            second_release.set()
            worker.close()

    # 오래된 추론 결과와 미래 시각 결과는 촬영 판단에 쓰이지 않는지 확인한다.
    def test_stale_and_future_result_rejected(self):
        detector = Mock()
        detector.predict.return_value = []
        worker = AsyncDetector(detector, interval_seconds=0.4, max_age_seconds=1)
        try:
            worker.submit(np.zeros((8, 8, 3), dtype=np.uint8), 2)
            await_result(worker, 2.1)
            self.assertIsNone(worker.latest(1))
            self.assertIsNone(worker.latest(3.01))
        finally:
            worker.close()

    # 추론 예외가 작업자를 종료하거나 예외 본문을 노출하지 않는지 확인한다.
    def test_error_is_redacted_and_worker_recovers(self):
        detector = Mock()
        detector.predict.side_effect = [RuntimeError("민감할 수 있는 내부 응답"), []]
        worker = AsyncDetector(detector, interval_seconds=0.1)
        try:
            worker.submit(np.zeros((8, 8, 3), dtype=np.uint8), 0)
            await_result(worker, 0)
            self.assertEqual(worker.error, "RuntimeError")
            self.assertIsNone(worker.latest(0).source_frame)
            worker.submit(np.zeros((8, 8, 3), dtype=np.uint8), 2)
            await_result(worker, 2)
            self.assertIsNone(worker.error)
        finally:
            worker.close()

    # 파일 영상의 추론 주기와 세션 초기화가 영상 시각을 따르는지 확인한다.
    def test_video_scheduler_uses_video_time(self):
        detector = Mock()
        detector.predict.return_value = []
        worker = SynchronousDetector(detector, interval_seconds=0.4, max_age_seconds=0.8)
        frame = np.zeros((8, 8, 3), dtype=np.uint8)
        worker.submit(frame, 0)
        worker.submit(frame, 0.1)
        worker.submit(frame, 0.5)
        self.assertEqual(detector.predict.call_count, 2)
        self.assertEqual(worker.latest(0.6).captured_at, 0.5)
        self.assertIsNone(worker.latest(1.4))
        worker.reset()
        self.assertIsNone(worker.latest(0.6))

    # 준비 단계가 추가 학습 없이 설명 임베딩과 가중치만 저장하는지 확인한다.
    def test_prepare_applies_classes_without_training(self):
        model = Mock()
        ultralytics = SimpleNamespace(YOLOE=Mock(return_value=model))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "prepared.pt"
            with patch.dict(sys.modules, {"ultralytics": ultralytics}):
                self.assertEqual(
                    prepare_model("yoloe-26n-seg.pt", output, ["medicine box"]), output
                )
            model.set_classes.assert_called_once_with(["medicine box"])
            model.save.assert_called_once_with(str(output))
            model.train.assert_not_called()

    # 없는 모델을 자동 다운로드하여 촬영 시작을 막지 않는지 확인한다.
    def test_missing_prepared_model_fails_before_import(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(FileNotFoundError):
            YOLOEDetector(Path(directory) / "missing.pt")

    # 설명이 없는 가중치와 설정과 다른 모델은 명시적으로 거부하는지 확인한다.
    def test_requires_prepared_matching_model(self):
        model = SimpleNamespace(model=SimpleNamespace(pe=None), names={0: "box"}, to=Mock())
        modules = {
            "ultralytics": SimpleNamespace(YOLOE=Mock(return_value=model)),
            "torch": SimpleNamespace(set_num_threads=Mock()),
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prepared.pt"
            path.write_bytes(b"fake")
            with patch.dict(sys.modules, modules):
                with self.assertRaises(ValueError):
                    YOLOEDetector(path)
                model.model.pe = object()
                with self.assertRaises(ValueError):
                    YOLOEDetector(path, prompts=["bottle"])


if __name__ == "__main__":
    unittest.main()
