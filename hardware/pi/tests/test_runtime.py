"""명령행 영상 모드의 실제 파일 처리와 종료 정리를 검증한다."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
from medikit_pi.__main__ import run_video, safely_close, safely_finish
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
