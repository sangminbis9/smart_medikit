"""합성 영상으로 개폐 상태와 촬영 성공 제한을 검증한다."""

from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest
from medikit_pi.camera import Frame
from medikit_pi.config import load_config
from medikit_pi.controller import CaptureController, Debouncer
from medikit_pi.detection import Detection, DetectionResult
from medikit_pi.gpio import MockBoard
from medikit_pi.outbox import Outbox


# 일정한 포장 후보를 반환하여 상태 머신의 영상 판단을 독립 검증한다.
class TestDetector:
    __test__ = False

    # 검출 시각을 외부 프레임 시각으로 유지한다.
    def __init__(self):
        self.result = None
        self.box = Detection((0.1, 0.1, 0.65, 0.85), 0.9, "medicine box")

    # 현재 프레임에서 후보가 관측되었다는 모의 결과를 만든다.
    def submit(self, frame, now):
        self.result = DetectionResult(now, (self.box,))

    # 최신 모의 검출을 반환한다.
    def latest(self, now):
        return self.result

    # 세션 간 검출을 초기화한다.
    def reset(self):
        self.result = None


# 실제 큐와 합성 영상을 결합한 촬영 시험 환경을 만든다.
@pytest.fixture
def rig(tmp_path):
    config = load_config(Path(__file__).parents[1] / "config.example.toml")
    outbox = Outbox(tmp_path)
    board = MockBoard()
    camera = Mock()
    detector = TestDetector()
    controller = CaptureController(config, camera, board, outbox, detector)
    base = np.zeros((180, 320, 3), dtype=np.uint8)
    package = base.copy()
    rng = np.random.default_rng(2)
    package[18:153, 32:208] = rng.integers(30, 250, (135, 176, 3), dtype=np.uint8)
    yield controller, camera, board, outbox, base, package
    outbox.close()


# 프레임과 실제 센서 상태를 같은 시각으로 전달한다.
def feed(rig, at, image, opened=True, focused=True):
    controller, camera, board, _, _, _ = rig
    board.opened = opened
    frame = Frame(image, focused, at, True)
    camera.capture.return_value = frame
    controller.sensor(opened, at)
    controller.frame(frame, at)


# 초기 안정화 후 준비 상태로 진입시킨다.
def warmup(rig):
    for index in range(12):
        feed(rig, index / 10, rig[4])
    assert rig[0].state == "READY"


# 배경에 없던 선명한 포장이 약 1초 관측되도록 한다.
def present(rig):
    for index in range(12, 32):
        feed(rig, index / 10, rig[5])


# 센서 접점의 반복 변화는 실제 개폐로 처리하지 않는다.
def test_debounce_rejects_bounce():
    debounce = Debouncer(0.08)
    assert not debounce.update(True, 0)
    assert not debounce.update(False, 0.03)
    assert not debounce.update(True, 0.05)
    assert debounce.update(True, 0.14)


# 한 세션의 포장 성공은 한 번이며 내부 사진과 같은 식별자로 저장된다.
def test_one_package_and_same_session_interior(rig):
    warmup(rig)
    present(rig)
    assert rig[0].state == "CAPTURED"
    assert rig[1].capture.call_count == 1
    for index in range(32, 48):
        feed(rig, index / 10, rig[5], opened=False)
    event = rig[3].head()
    assert event["package_capture_ok"] and event["interior_capture_ok"]
    assert rig[1].capture.call_count == 2
    assert rig[0].state == "CLOSED"


# 준비 전에 존재한 포장은 새 제시로 촬영하지 않는다.
def test_existing_background_not_captured(rig):
    for index in range(40):
        feed(rig, index / 10, rig[5])
    assert rig[0].state == "READY"
    rig[1].capture.assert_not_called()


# AF 또는 선명도가 불충분하면 포장 성공으로 세지 않는다.
def test_unfocused_package_not_success(rig):
    warmup(rig)
    for index in range(12, 40):
        feed(rig, index / 10, rig[5], focused=False)
    assert rig[0].state == "READY"
    rig[1].capture.assert_not_called()


# 화질 실패 후 다시 안정된 포장은 같은 세션에서 성공할 수 있다.
def test_capture_failure_can_retry(rig):
    warmup(rig)
    rig[1].capture.side_effect = OSError("camera")
    present(rig)
    assert rig[0].state == "READY"
    rig[1].capture.side_effect = None
    for index in range(32, 65):
        feed(rig, index / 10, rig[5])
    assert rig[0].state == "CAPTURED"


# 내부 초점 실패는 최대 세 번의 시도 창 후 포장 사진을 보존하고 끝난다.
def test_interior_exhausted_keeps_package(rig):
    warmup(rig)
    present(rig)
    for index in range(32, 135):
        feed(rig, index / 10, rig[4], opened=False, focused=False)
    event = rig[3].head()
    assert event["package_capture_ok"]
    assert not event["interior_capture_ok"]
    assert rig[0].close_attempts == 3


# 실제 사진 저장 실패도 내부 촬영 세 번 이상을 실행하지 않는다.
def test_interior_capture_failure_limited_to_three(rig):
    warmup(rig)
    rig[1].capture.side_effect = OSError("camera")
    for index in range(12, 60):
        feed(rig, index / 10, rig[5], opened=False)
    assert rig[1].capture.call_count == 3
    assert not rig[3].head()["interior_capture_ok"]


# 내부 촬영 중 다시 열면 이전 세션과 새 세션이 섞이지 않는다.
def test_reopen_finishes_previous_session(rig):
    warmup(rig)
    first = rig[0].session_id
    feed(rig, 1.2, rig[4], opened=False)
    feed(rig, 1.3, rig[4], opened=False)
    feed(rig, 1.4, rig[4], opened=True)
    feed(rig, 1.5, rig[4], opened=True)
    assert rig[0].session_id != first
    assert rig[3].head()["session_id"] == first


# 카메라 단절 중 닫힘도 제한된 시도 후 실패 세션으로 남긴다.
def test_camera_outage_does_not_stall_session(rig):
    warmup(rig)
    rig[0].sensor(False, 1.2)
    rig[0].sensor(False, 1.3)
    for at in (4.4, 7.5, 10.6):
        rig[0].camera_error(at)
    assert rig[0].state == "CLOSED"
    assert not rig[3].head()["interior_capture_ok"]


# 촬영 도중 센서가 변하면 고해상도 사진을 성공 처리하지 않는다.
def test_lid_change_during_capture_rejected(rig):
    warmup(rig)
    rig[2].is_open = Mock(side_effect=[True, False])
    assert not rig[0].capture("package", Frame(rig[5], True, 1.2))


# 지연된 빈 검출 결과가 작은 신규 포장을 현재 배경에 흡수하지 않는다.
def test_delayed_empty_detection_uses_its_own_frame(rig):
    warmup(rig)
    controller = rig[0]
    small = rig[4].copy()
    small[30:60, 30:60] = 180
    controller.detector.submit = Mock()
    controller.detector.result = DetectionResult(1.0, (), rig[4].copy())
    feed(rig, 1.2, small)
    assert np.array_equal(controller.background, rig[4][:, :, 0])


# 추론 예외의 빈 후보는 실제 빈 장면으로 받아들이지 않는다.
def test_inference_error_cannot_refresh_background(rig):
    warmup(rig)
    controller = rig[0]
    small = rig[4].copy()
    small[30:60, 30:60] = 180
    controller.detector.submit = Mock()
    controller.detector.result = DetectionResult(1.0, ())
    feed(rig, 1.2, small)
    assert np.array_equal(controller.background, rig[4][:, :, 0])


# 잘못된 서버 조회 주기와 무제한 재시도 설정을 거부한다.
@pytest.mark.parametrize(
    "old,new",
    [("poll_seconds = 2.5", "poll_seconds = 1.0"), ("close_retries = 2", "close_retries = 3")],
)
def test_invalid_config_rejected(tmp_path, old, new):
    source = (Path(__file__).parents[1] / "config.example.toml").read_text(encoding="utf-8")
    path = tmp_path / "bad.toml"
    path.write_text(source.replace(old, new), encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)
