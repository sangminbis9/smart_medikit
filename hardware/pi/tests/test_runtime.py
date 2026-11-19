"""명령행 영상 모드의 실제 파일 처리와 종료 정리를 검증한다."""

import json
import logging
import queue
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
from medikit_pi.__main__ import (
    configure_camera_logging,
    run_video,
    safely_close,
    safely_finish,
    sync_loop,
)
from medikit_pi.config import load_config
from medikit_pi.detection import Detection
from medikit_pi.outbox import Outbox


class SyntheticDetector:
    # 모델 대신 합성 흰 포장을 찾는 테스트 전용 검출기를 준비한다.
    def __init__(self, *args):
        pass

    # 합성 프레임이 밝을 때만 포장 후보를 반환한다.
    def predict(self, frame):
        if frame.mean() > 10:
            return [Detection((0.1, 0.1, 0.8, 0.9), 0.9, "medicine box")]
        return []


# 합성 AVI·JSON 개폐 기록으로 촬영·저장·보고서까지 연결한다.
def test_video_command_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr("medikit_pi.detection.YOLOEDetector", SyntheticDetector)
    video = tmp_path / "test.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (320, 240))
    assert writer.isOpened()
    rng = np.random.default_rng(17)
    image = np.zeros((240, 320, 3), dtype=np.uint8)
    image[24:216, 32:256] = rng.integers(30, 250, (192, 224, 3), dtype=np.uint8)
    for index in range(90):
        writer.write(np.zeros_like(image) if index < 20 else image)
    writer.release()
    events = tmp_path / "events.json"
    events.write_text(json.dumps([{"at": 0, "open": True}, {"at": 6, "open": False}]))
    config = load_config(Path(__file__).parents[1] / "config.example.toml")
    config["aws"]["enabled"] = True
    args = SimpleNamespace(
        input=video,
        lid_events=events,
        data_dir=tmp_path / "queue",
        report=tmp_path / "report.json",
        show=False,
    )
    assert run_video(config, args) == 0
    outbox = Outbox(args.data_dir)
    try:
        event = outbox.head()
        assert event["package_capture_ok"] and event["interior_capture_ok"]
        assert not event["_uploaded"]
        assert event["sequence"] == 1
        for kind in ("package", "interior"):
            photo = cv2.imdecode(
                np.frombuffer((event["_directory"] / f"{kind}.jpg").read_bytes(), np.uint8),
                cv2.IMREAD_COLOR,
            )
            assert photo.shape == image.shape
    finally:
        outbox.close()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    assert report["frames"] == 90
    assert report["autofocus_simulated"] and not report["aws_enabled"]
    assert "CAPTURED" in [item["state"] for item in report["transitions"]]


# 저장 오류가 조명·카메라의 별도 자원 정리 흐름을 끊지 않는다.
def test_cleanup_survives_disk_and_device_errors():
    controller = Mock()
    controller.finish.side_effect = OSError("disk")
    device = Mock()
    device.close.side_effect = OSError("device")
    safely_finish(controller)
    safely_close(device)
    controller.finish.assert_called_once()
    device.close.assert_called_once()


# 서버 상태는 이해하기 쉽게 표시하고 같은 처리 중 메시지는 반복하지 않는다.
def test_server_status_logs_are_clear_and_do_not_repeat(caplog):
    worker = Mock()
    worker.step.side_effect = ["UPLOADED", "PENDING", "PROCESSING", "REVIEW_REQUIRED", "ERROR"]
    stop = Mock()
    stop.is_set.side_effect = [False] * 5 + [True]
    results = queue.SimpleQueue()
    with caplog.at_level(logging.INFO):
        sync_loop(worker, stop, results)
    assert "촬영 세션 정보를 서버에 전송했습니다" in caplog.text
    assert caplog.text.count("서버가 촬영 정보를 처리하고 있습니다") == 1
    assert "웹에서 확인해 주세요" in caplog.text
    assert "보관된 작업을 다시 시도합니다" in caplog.text
    assert [results.get() for _ in range(5)] == ["UPLOADED", "PENDING", "PROCESSING", "REVIEW_REQUIRED", "ERROR"]


# 외부 카메라의 평상시 정보만 줄이고 경고와 오류는 남긴다.
def test_camera_logging_keeps_warnings_and_custom_levels(monkeypatch, caplog):
    logger = logging.getLogger("picamera2")
    original_level = logger.level
    try:
        monkeypatch.delenv("LIBCAMERA_LOG_LEVELS", raising=False)
        monkeypatch.delenv("MEDIKIT_CAMERA_LOG_LEVEL", raising=False)
        configure_camera_logging()
        import os

        assert os.environ["LIBCAMERA_LOG_LEVELS"] == "*:WARN"
        with caplog.at_level(logging.INFO):
            logging.getLogger("picamera2.picamera2").info("synthetic-camera-info")
            logging.getLogger("picamera2.picamera2").warning("synthetic-camera-warning")
            logging.getLogger("picamera2.picamera2").error("synthetic-camera-error")
        assert "synthetic-camera-info" not in caplog.text
        assert "synthetic-camera-warning" in caplog.text
        assert "synthetic-camera-error" in caplog.text
        monkeypatch.setenv("LIBCAMERA_LOG_LEVELS", "*:DEBUG")
        monkeypatch.setenv("MEDIKIT_CAMERA_LOG_LEVEL", "INFO")
        configure_camera_logging()
        assert os.environ["LIBCAMERA_LOG_LEVELS"] == "*:DEBUG"
        assert logger.level == logging.INFO
    finally:
        logger.setLevel(original_level)
