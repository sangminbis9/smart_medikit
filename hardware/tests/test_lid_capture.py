"""모의 개폐 입력과 합성 영상으로 두 단계 촬영 및 오류 시 사진 보존을 검증한다."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from itertools import count
import json
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from hardware import camera_preview, lid_capture
from hardware.package_detector import PackageDetection


class LidCaptureTests(unittest.TestCase):
    # 개인정보 없는 합성 포장과 임시 저장 위치를 준비한다.
    def setUp(self):
        self.output = self.enterContext(redirect_stdout(StringIO()))
        self.errors = self.enterContext(redirect_stderr(StringIO()))
        self.directory = Path(self.enterContext(TemporaryDirectory()))
        self.args = camera_preview.parse_args(["--simulate-lid", "--rotate", "90", "--backend", "msmf"])
        pattern = ((np.indices((240, 320)).sum(axis=0) // 4) % 2 * 100 + 75).astype(np.uint8)
        self.frame = np.repeat(pattern[:, :, None], 3, axis=2)
        self.empty_frame = np.full_like(self.frame, 120)
        self.target = PackageDetection(0, 0.9, (20, 20, 200, 180))
        self.detector = Mock()
        self.detector.detect.return_value = [self.target]
        self.detector.annotate.return_value = np.full_like(self.frame, 255)
        self.controller = lid_capture.LidCaptureController(self.args, self.detector, self.directory)

    # 포장 촬영 조건을 세 번 관측해 저장할 최신 원본과 검사 결과를 받는다.
    def package_candidate(self, start=0):
        candidate = None
        report = None
        for now in (start, start + 1, start + 2):
            _, candidate, report = self.controller.analyze(self.frame, now)
        self.assertIsNotNone(candidate)
        return candidate, report

    # 열린 세션에서 조건을 만족한 포장 사진을 한 장 보관한다.
    def save_package(self, start=0):
        candidate, report = self.package_candidate(start)
        revision = self.controller.revision
        self.assertTrue(self.controller.save_candidate(candidate, report, revision))

    # 저장된 JSON을 다시 읽어 메모리 상태와 별도로 영속 기록을 확인한다.
    def read_record(self, session):
        return json.loads((session.directory / "session.json").read_text(encoding="utf-8"))

    # 같은 세션에 포장 영역과 내부 전체 사진을 서로 다른 하위 폴더로 저장한다.
    def test_complete_pair_preserves_original_pixels_and_full_interior(self):
        self.controller.handle_command("1", 0)
        session = self.controller.session
        self.save_package()
        self.assertEqual(self.controller.phase, "wait_close")
        self.controller.handle_command("0", 3)
        for now in (3, 3.5, 4):
            display, candidate, report = self.controller.analyze(self.empty_frame, now)
        np.testing.assert_array_equal(display, self.empty_frame)
        np.testing.assert_array_equal(candidate, self.empty_frame)
        self.assertTrue(self.controller.save_candidate(candidate, report, self.controller.revision))
        self.assertEqual(self.controller.phase, "idle")
        self.assertIsNone(self.controller.session)
        record = self.read_record(session)
        self.assertEqual(record["status"], "complete")
        self.assertEqual(record["session_id"], session.data["session_id"])
        self.assertTrue(record["local_only"])
        for kind, shape in (("package", (160, 180, 3)), ("interior", self.frame.shape)):
            path = session.directory / record[kind]["path"]
            self.assertEqual(path.parent.name, kind)
            decoded = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
            self.assertEqual(decoded.shape, shape)
            self.assertLess(float(decoded.mean()), 200)
        self.assertEqual(self.detector.detect.call_count, 3)

    # 포장을 오래 치웠다가 다시 제시해도 같은 열림에서는 추가 저장하지 않는다.
    def test_package_saved_once_per_opening_even_after_absence(self):
        self.controller.handle_command("1", 0)
        session = self.controller.session
        self.save_package()
        self.detector.detect.return_value = []
        for now in (3, 4, 5):
            self.assertIsNone(self.controller.analyze(self.frame, now)[1])
        self.detector.detect.return_value = [self.target]
        for now in (6, 7, 8):
            self.assertIsNone(self.controller.analyze(self.frame, now)[1])
        self.assertFalse(self.controller.save_candidate(self.frame, None, self.controller.revision))
        self.assertEqual(len(list(session.directory.rglob("*.jpg"))), 1)
        self.assertEqual(self.detector.detect.call_count, 3)

    # 포장 사진 없이 닫혀도 내부 전체 사진을 확보하고 누락을 명시한다.
    def test_close_before_package_keeps_interior_only_as_incomplete(self):
        self.controller.handle_command("1", 0)
        session = self.controller.session
        self.controller.handle_command("0", 0.1)
        for now in (0.1, 0.6, 1.1):
            _, candidate, report = self.controller.analyze(self.empty_frame, now)
        self.assertTrue(self.controller.save_candidate(candidate, report, self.controller.revision))
        record = self.read_record(session)
        self.assertEqual(record["status"], "incomplete")
        self.assertIsNone(record["package"])
        self.assertIsNotNone(record["interior"])
        self.detector.detect.assert_not_called()

    # 초기 닫힘과 중복 신호는 새 세션이나 새로운 제한 시간을 만들지 않는다.
    def test_duplicate_signals_do_not_create_sessions_or_extend_timeout(self):
        self.controller.handle_command("0", 0)
        self.assertIsNone(self.controller.session)
        self.assertEqual(list(self.directory.iterdir()), [])
        self.controller.handle_command("1", 1)
        session = self.controller.session
        revision = self.controller.revision
        self.controller.handle_command("1", 2)
        self.assertIs(self.controller.session, session)
        self.assertEqual(self.controller.revision, revision)
        self.controller.handle_command("0", 3)
        revision = self.controller.revision
        self.controller.handle_command("0", 4)
        self.assertEqual(self.controller.closed_at, 3)
        self.assertEqual(self.controller.revision, revision)
        self.controller.check_timeout(18)
        self.assertEqual(self.read_record(session)["reason"], "interior_timeout")

    # 내부 촬영 도중 다시 열면 이전 포장을 보존하고 다른 세션을 시작한다.
    def test_reopening_does_not_attach_new_scene_to_previous_session(self):
        self.controller.handle_command("1", 0)
        old_session = self.controller.session
        self.save_package()
        self.controller.handle_command("0", 3)
        self.controller.handle_command("1", 3.1)
        new_session = self.controller.session
        self.assertNotEqual(old_session.data["session_id"], new_session.data["session_id"])
        old_record = self.read_record(old_session)
        self.assertEqual(old_record["status"], "incomplete")
        self.assertEqual(old_record["reason"], "reopened_before_interior")
        self.assertIsNotNone(old_record["package"])
        self.assertIsNone(old_record["interior"])
        self.assertEqual(self.controller.phase, "package")
        self.assertIsNone(new_session.data["package"])

    # 종료는 이미 확보한 포장을 삭제하지 않고 미완료 기록으로 남긴다.
    def test_quit_keeps_saved_package(self):
        self.controller.handle_command("1", 0)
        session = self.controller.session
        self.save_package()
        self.controller.handle_command("q", 3)
        self.assertFalse(self.controller.running)
        self.assertEqual(self.controller.phase, "idle")
        record = self.read_record(session)
        self.assertEqual(record["reason"], "interrupted")
        self.assertEqual(record["status"], "incomplete")
        self.assertTrue((session.directory / record["package"]["path"]).is_file())

    # 닫힘이나 재열림 전에 분석한 저장 후보를 이후 단계에 사용하지 않는다.
    def test_state_revision_rejects_stale_package_candidate(self):
        self.controller.handle_command("1", 0)
        session = self.controller.session
        candidate, report = self.package_candidate()
        revision = self.controller.revision
        self.controller.handle_command("0", 3)
        self.assertFalse(self.controller.save_candidate(candidate, report, revision))
        self.assertIsNone(session.data["package"])
        self.assertEqual(list(session.directory.rglob("*.jpg")), [])

    # 어둡거나 움직이는 내부를 억지로 저장하지 않고 제한 시간 후 누락을 남긴다.
    def test_timeout_preserves_package_without_saving_bad_interior(self):
        self.controller.handle_command("1", 0)
        session = self.controller.session
        self.save_package()
        self.controller.handle_command("0", 3)
        for now in (3, 4, 5):
            self.assertIsNone(self.controller.analyze(np.zeros_like(self.frame), now)[1])
        self.controller.check_timeout(17.9)
        self.assertEqual(self.controller.phase, "interior")
        self.controller.check_timeout(18)
        self.assertEqual(self.controller.phase, "idle")
        record = self.read_record(session)
        self.assertEqual(record["reason"], "interior_timeout")
        self.assertIsNotNone(record["package"])
        self.assertIsNone(record["interior"])

    # 사진 쓰기 실패는 촬영 완료로 표시하거나 다음 단계로 넘어가지 않는다.
    def test_photo_write_error_does_not_mark_saved(self):
        self.controller.handle_command("1", 0)
        session = self.controller.session
        candidate, report = self.package_candidate()
        with patch.object(camera_preview, "save_frame", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.controller.save_candidate(candidate, report, self.controller.revision)
        self.assertEqual(self.controller.phase, "package")
        self.assertIsNone(session.data["package"])
        self.controller.finish("error")
        self.assertEqual(self.read_record(session)["status"], "incomplete")

    # 사진 저장 후 JSON 갱신만 실패해도 확보한 이미지는 삭제하지 않는다.
    def test_metadata_error_keeps_written_photo_for_recovery(self):
        self.controller.handle_command("1", 0)
        session = self.controller.session
        candidate, report = self.package_candidate()
        with patch.object(session, "persist", side_effect=OSError("metadata unavailable")):
            with self.assertRaises(OSError):
                self.controller.save_candidate(candidate, report, self.controller.revision)
        self.assertEqual(len(list(session.directory.rglob("*.jpg"))), 1)
        self.controller.finish("error")
        record = self.read_record(session)
        self.assertEqual(record["status"], "incomplete")
        self.assertTrue((session.directory / record["package"]["path"]).is_file())

    # 잘못된 문자 입력은 현재 촬영 단계나 세션을 변경하지 않는다.
    def test_unknown_input_does_not_change_state(self):
        self.controller.handle_command("1", 0)
        session = self.controller.session
        revision = self.controller.revision
        self.controller.handle_command("unexpected", 1)
        self.assertIs(self.controller.session, session)
        self.assertEqual(self.controller.revision, revision)
        self.assertEqual(self.controller.phase, "package")


class LidRunnerTests(unittest.TestCase):
    # 모든 카메라·창 동작을 모의 처리하고 제한 없는 반복을 피하도록 가짜 시계를 둔다.
    def setUp(self):
        self.output = self.enterContext(redirect_stdout(StringIO()))
        self.errors = self.enterContext(redirect_stderr(StringIO()))
        self.directory = Path(self.enterContext(TemporaryDirectory()))
        self.args = camera_preview.parse_args([
            "--simulate-lid", "--rotate", "90", "--backend", "msmf", "--hold-seconds", "0.5",
        ])
        pattern = ((np.indices((240, 320)).sum(axis=0) // 4) % 2 * 100 + 75).astype(np.uint8)
        self.frame = np.repeat(pattern[:, :, None], 3, axis=2)
        self.target = PackageDetection(0, 0.9, (20, 20, 200, 180))
        self.detector = Mock()
        self.detector.detect.return_value = [self.target]
        self.detector.annotate.return_value = self.frame
        self.commands = Queue()
        self.clock = count(step=0.2)
        self.enterContext(patch.object(lid_capture.time, "monotonic", side_effect=self.tick))
        self.capture_factory = self.enterContext(patch.object(cv2, "VideoCapture"))
        self.capture = self.capture_factory.return_value
        self.capture.open.return_value = True
        self.capture.read.return_value = (True, self.frame)
        self.enterContext(patch.object(cv2, "namedWindow"))
        self.enterContext(patch.object(lid_capture, "make_session_panel", return_value=None))
        self.show = self.enterContext(patch.object(camera_preview, "show_preview"))
        self.keys = self.enterContext(patch.object(cv2, "waitKey", return_value=-1))
        self.enterContext(patch.object(cv2, "getWindowProperty", return_value=1))
        self.destroy = self.enterContext(patch.object(cv2, "destroyWindow"))

    # 각 시간 조회마다 짧은 간격을 진행한다.
    def tick(self):
        return next(self.clock)

    # 준비한 입력 큐와 임시 폴더로 장치 없이 실행한다.
    def run_simulation(self):
        return lid_capture.run_lid_simulation(self.args, self.detector, self.commands, self.directory)

    # 영속 세션 기록을 모두 읽는다.
    def records(self):
        return [json.loads(path.read_text(encoding="utf-8")) for path in self.directory.glob("*/session.json")]

    # 열림 신호가 없으면 영상 장치를 만들지도 않는다.
    def test_idle_quit_never_opens_camera(self):
        self.commands.put("q")
        self.assertEqual(self.run_simulation(), 0)
        self.capture_factory.assert_not_called()
        self.assertEqual(self.records(), [])

    # 카메라 연결에 실패하면 세션을 미완료로 기록하고 장치를 해제한다.
    def test_camera_open_failure_is_recorded_and_released(self):
        self.commands.put("1")
        self.capture.open.return_value = False
        self.assertEqual(self.run_simulation(), 1)
        self.capture.release.assert_called_once()
        self.capture.read.assert_not_called()
        self.assertEqual(self.records()[0]["reason"], "error")

    # 영상 단절은 이전 프레임을 저장하지 않고 세션과 창을 정리한다.
    def test_camera_read_failure_does_not_save_stale_image(self):
        self.commands.put("1")
        self.capture.read.return_value = (False, None)
        self.assertEqual(self.run_simulation(), 1)
        self.capture.release.assert_called_once()
        self.destroy.assert_called_once()
        self.detector.detect.assert_not_called()
        self.assertEqual(list(self.directory.rglob("*.jpg")), [])

    # 추론 중 닫힘이 도착하면 이미 준비된 포장 후보도 저장하지 않는다.
    def test_close_arriving_during_detection_discards_ready_package(self):
        self.commands.put("1")
        calls = 0

        # 세 번째 관측 중 닫힘을 알리고 다음 읽기에서 연결을 종료한다.
        def detect(frame):
            nonlocal calls
            calls += 1
            if calls == 3:
                self.commands.put("0")
                self.capture.read.return_value = (False, None)
            return [self.target]

        self.detector.detect.side_effect = detect
        self.assertEqual(self.run_simulation(), 1)
        self.assertEqual(calls, 3)
        record = self.records()[0]
        self.assertIsNone(record["package"])
        self.assertIsNotNone(record["closed_at"])
        self.assertEqual(list(self.directory.rglob("*.jpg")), [])

    # 추론 중 종료가 도착하면 같은 프레임의 자동 저장보다 종료를 우선한다.
    def test_quit_arriving_during_detection_discards_ready_package(self):
        self.commands.put("1")
        calls = 0

        # 세 번째 관측에서 저장 직전 종료 명령을 큐에 넣는다.
        def detect(frame):
            nonlocal calls
            calls += 1
            if calls == 3:
                self.commands.put("q")
            return [self.target]

        self.detector.detect.side_effect = detect
        self.assertEqual(self.run_simulation(), 0)
        self.assertEqual(calls, 3)
        self.assertEqual(self.records()[0]["reason"], "interrupted")
        self.assertEqual(list(self.directory.rglob("*.jpg")), [])
        self.capture.release.assert_called_once()

    # 준비된 후보가 있어도 창의 Esc 입력을 먼저 처리한다.
    def test_escape_precedes_candidate_save(self):
        self.commands.put("1")
        self.keys.side_effect = [-1, -1, 27]
        self.assertEqual(self.run_simulation(), 0)
        self.assertEqual(self.detector.detect.call_count, 3)
        self.assertEqual(list(self.directory.rglob("*.jpg")), [])
        self.capture.release.assert_called_once()

    # 사진 쓰기 오류는 반복 저장하지 않고 오류 세션으로 보존한다.
    def test_save_error_stops_runner_without_success_record(self):
        self.commands.put("1")
        with patch.object(camera_preview, "save_frame", side_effect=OSError("disk full")) as save:
            self.assertEqual(self.run_simulation(), 1)
        save.assert_called_once()
        self.capture.release.assert_called_once()
        record = self.records()[0]
        self.assertEqual(record["status"], "incomplete")
        self.assertEqual(record["reason"], "error")
        self.assertIsNone(record["package"])

    # 두 세션을 연속 촬영할 때 각 완료 뒤 카메라를 닫고 다음 열림에 다시 연다.
    def test_two_complete_sessions_release_and_reopen_camera(self):
        self.commands.put("1")
        original_save = camera_preview.save_frame
        saved_frames = []
        releases = 0
        reads = 0

        # 포장 저장이 끝난 뒤 사용자 닫힘 입력을 재현한다.
        def save(frame, directory, prefix="frame"):
            saved_frames.append((prefix, frame.copy()))
            path = original_save(frame, directory, prefix)
            if prefix == "package":
                self.commands.put("0")
            return path

        # 첫 종료 뒤 새 열림을 전달하고 두 번째 종료 뒤 프로그램을 끝낸다.
        def release():
            nonlocal releases
            releases += 1
            self.commands.put("1" if releases == 1 else "q")

        # 잘못된 상태 전이로 무한 반복하지 않도록 읽기 횟수를 제한한다.
        def read():
            nonlocal reads
            reads += 1
            return (True, self.frame) if reads < 40 else (False, None)

        self.capture.release.side_effect = release
        self.capture.read.side_effect = read
        with patch.object(camera_preview, "save_frame", side_effect=save):
            self.assertEqual(self.run_simulation(), 0)
        self.assertEqual(self.capture.open.call_count, 2)
        self.assertEqual(self.capture.release.call_count, 2)
        records = self.records()
        self.assertEqual(len(records), 2)
        self.assertEqual({record["status"] for record in records}, {"complete"})
        self.assertEqual(len({record["session_id"] for record in records}), 2)
        self.assertEqual([kind for kind, _ in saved_frames], ["package", "interior", "package", "interior"])
        rotated = cv2.rotate(self.frame, cv2.ROTATE_90_CLOCKWISE)
        for kind, saved in saved_frames:
            expected = self.target.crop(rotated) if kind == "package" else rotated
            np.testing.assert_array_equal(saved, expected)

    # 입력 종료는 닫힘 대신 종료로 전달하여 열린 화면을 내부 사진으로 만들지 않는다.
    def test_terminal_eof_emits_quit_not_close(self):
        commands = Queue()
        lid_capture.read_terminal_commands(commands, StringIO("1\n"))
        self.assertEqual(commands.get_nowait(), "1")
        self.assertEqual(commands.get_nowait(), "q")
        self.assertTrue(commands.empty())


if __name__ == "__main__":
    unittest.main()
