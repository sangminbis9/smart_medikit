"""카메라 접근 없이 실시간 탐지 표시와 연결 실패·종료를 검증한다."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from medikit_pi.__main__ import main, parse_args
from medikit_pi.config import load_config
from medikit_pi.detection import Detection, DetectionResult
from medikit_pi.webcam import render_preview, run_webcam


# 웹캠·화면·비동기 모델을 합성 프레임으로 대체한다.
@pytest.fixture
def webcam_rig(monkeypatch, tmp_path):
    config = load_config(Path(__file__).parents[1] / "config.example.toml")
    config["aws"]["enabled"] = True
    config["data_dir"] = tmp_path / "must-not-exist"
    capture = Mock()
    capture.open.return_value = True
    capture.read.return_value = (True, np.full((90, 160, 3), 100, dtype=np.uint8))
    worker = Mock(error=None)
    worker.latest.return_value = None
    monkeypatch.setattr("medikit_pi.webcam.YOLOEDetector", Mock())
    monkeypatch.setattr("medikit_pi.webcam.AsyncDetector", Mock(return_value=worker))
    monkeypatch.setattr("medikit_pi.webcam.cv2.VideoCapture", Mock(return_value=capture))
    for name in ("namedWindow", "destroyWindow"):
        monkeypatch.setattr(f"medikit_pi.webcam.cv2.{name}", Mock())
    display = Mock()
    monkeypatch.setattr("medikit_pi.webcam.cv2.imshow", display)
    monkeypatch.setattr("medikit_pi.webcam.cv2.waitKey", Mock(return_value=ord("q")))
    monkeypatch.setattr("medikit_pi.webcam.cv2.getWindowProperty", Mock(return_value=1))
    args = SimpleNamespace(camera=0, backend="auto", confidence=None, interval=None)
    return config, args, capture, worker, display


# 정상 종료에서 입력·검출 자원을 반환하고 로컬 큐를 만들지 않는다.
def test_live_detection_does_not_create_capture_queue(webcam_rig):
    config, args, capture, worker, display = webcam_rig
    assert run_webcam(config, args) == 0
    capture.release.assert_called_once()
    worker.close.assert_called_once()
    worker.submit.assert_called_once()
    display.assert_called_once()
    assert not config["data_dir"].exists()


# 연결이 끊겼을 때 이전 영상을 다시 표시하거나 추론하지 않는다.
def test_disconnect_stops_without_display(webcam_rig):
    config, args, capture, worker, display = webcam_rig
    capture.read.return_value = (False, None)
    assert run_webcam(config, args) == 1
    worker.submit.assert_not_called()
    display.assert_not_called()
    capture.release.assert_called_once()


# 카메라 열기 실패도 핸들을 반환한다.
def test_camera_open_failure_releases_handle(webcam_rig):
    config, args, capture, worker, _ = webcam_rig
    capture.open.return_value = False
    assert run_webcam(config, args) == 1
    capture.release.assert_called_once()
    worker.submit.assert_not_called()


# 모델 오류 후 빈 후보를 정상 결과로 표시하지 않는다.
def test_inference_failure_exits(webcam_rig):
    config, args, capture, worker, display = webcam_rig
    worker.error = "RuntimeError"
    assert run_webcam(config, args) == 1
    display.assert_not_called()
    capture.release.assert_called_once()
    worker.close.assert_called_once()


# 창 닫기 버튼으로도 작업자를 종료한다.
def test_window_close_exits(webcam_rig, monkeypatch):
    monkeypatch.setattr("medikit_pi.webcam.cv2.waitKey", Mock(return_value=-1))
    monkeypatch.setattr("medikit_pi.webcam.cv2.getWindowProperty", Mock(return_value=0))
    assert run_webcam(*webcam_rig[:2]) == 0
    webcam_rig[3].close.assert_called_once()


# 현재 영상에는 상자를 그리지 않고 검출 원본에만 좌표를 표시한다.
def test_overlay_uses_detection_source_frame():
    current = np.full((100, 200, 3), 200, dtype=np.uint8)
    source = np.full_like(current, 30)
    candidate = Detection((0.2, 0.3, 0.7, 0.8), 0.85, "medicine box")
    rendered = render_preview(current, DetectionResult(1, (candidate,), source))
    assert np.array_equal(rendered[:, :200], current)
    assert np.array_equal(rendered[90, 390], source[90, 190])
    assert not np.array_equal(rendered[:, 200:], source)
    assert np.all(source == 30)
    assert np.all(render_preview(current, None)[:, 200:] == 0)


# 잘못된 입력은 카메라에 접근하기 전에 거부한다.
@pytest.mark.parametrize(
    "flags", [["--camera", "-1"], ["--confidence", "0"], ["--interval", "nan"]]
)
def test_invalid_webcam_options(flags):
    with pytest.raises(SystemExit):
        parse_args(["webcam", *flags])


# CLI가 Pi 실행 경로 대신 탐지 전용 모드로 전달하는지 확인한다.
def test_webcam_cli_dispatch(monkeypatch):
    runner = Mock(return_value=0)
    monkeypatch.setattr("medikit_pi.webcam.run_webcam", runner)
    config = Path(__file__).parents[1] / "config.example.toml"
    assert main(["--config", str(config), "webcam", "--camera", "1"]) == 0
    assert runner.call_args.args[1].camera == 1
