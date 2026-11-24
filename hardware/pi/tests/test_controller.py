"""합성 영상으로 개폐 상태와 촬영 성공 제한을 검증한다."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest
from medikit_pi.camera import Frame
from medikit_pi.config import load_config
from medikit_pi.controller import CaptureController, Debouncer
from medikit_pi.detection import Detection, DetectionResult
from medikit_pi.gpio import MockBoard
from medikit_pi.outbox import Outbox
from medikit_pi.vision import capture_thresholds, crop_box, gray_frame, motion_score


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
    config["gpio"]["ring_pulse_seconds"] = 0.2
    outbox = Outbox(tmp_path)
    board = MockBoard()
    camera = Mock()
    detector = TestDetector()
    controller = CaptureController(config, camera, board, outbox, detector)
    base = np.full((180, 320, 3), 92, dtype=np.uint8)
    package = base.copy()
    rng = np.random.default_rng(2)
    package[18:153, 32:208] = rng.integers(30, 250, (135, 176, 3), dtype=np.uint8)
    yield controller, camera, board, outbox, base, package
    outbox.close()


# 프레임과 실제 센서 상태를 같은 시각으로 전달한다.
def feed(rig, at, image, opened=True, focused=True, still_image=None):
    controller, camera, board, _, _, _ = rig
    board.opened = opened
    frame = Frame(image, focused, at, True)
    camera.capture.return_value = frame if still_image is None else Frame(still_image, focused, at)
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


# 뚜껑을 열어도 카메라 영상이 도착해야 초록 신호가 켜지고, 프레임 없이도 제때 꺼진다.
def test_camera_start_signal_waits_for_frame_and_expires_during_poll(rig):
    controller, _, board, _, background, _ = rig
    board.opened = True
    controller.sensor(True, 0.0)
    controller.sensor(True, 0.1)
    controller.camera_error(0.2)
    assert controller.session_id is not None and board.color is None
    feed(rig, 0.3, background)
    assert board.color == "green"
    controller.sensor(True, 0.4)
    assert board.color == "green"
    controller.sensor(True, 0.51)
    assert board.color is None


# 열린 동안 탐지한 원본에만 박스를 표시하고 유효한 결과가 없으면 실시간 원본을 내보낸다.
def test_preview_pairs_boxes_with_detector_frame(rig):
    controller, _, _, _, current, detected = rig
    warmup(rig)
    box = controller.detector.box
    detections = (box,)
    controller.detector.submit = Mock()
    controller.detector.latest = Mock(
        side_effect=[
            DetectionResult(1.2, detections, detected),
            DetectionResult(0.0, detections, detected),
            DetectionResult(1.4, (), detected),
            DetectionResult(1.5, detections, None),
        ]
    )
    preview = Mock()
    for at in (1.2, 1.3, 1.4, 1.5):
        controller.frame(Frame(current, True, at), at, preview=preview)
    displayed = [call.args for call in preview.publish.call_args_list]
    assert len(displayed) == 4
    assert controller.detector.latest.call_count == controller.detector.submit.call_count == 4
    assert displayed[0][0] is detected and displayed[0][1] == detections
    assert all(len(args) == 1 and args[0] is current for args in displayed[1:])


# 닫힌 채 부팅하면 카메라 시작 신호 없이 내부 기준 사진만 백색으로 촬영한다.
def test_closed_baseline_uses_only_white_until_saved(rig):
    controller, _, board, outbox, _, interior = rig
    outbox.staged = True
    controller.initialize_baseline(0.0)
    assert board.color == "white"
    for index in range(15):
        feed(rig, index / 10, interior, opened=False)
    event = outbox.head()
    assert not event["package_capture_ok"] and event["interior_capture_ok"]
    assert [color for _, color in board.history] == ["white", None]


# 센서가 닫히면 진행 중인 초록 신호의 소등 시각을 넘겨도 내부 촬영 백색이 유지된다.
def test_close_preempts_camera_start_flash(rig):
    controller, _, board, _, background, _ = rig
    feed(rig, 0.0, background)
    feed(rig, 0.1, background)
    assert board.color == "green"
    feed(rig, 0.11, background, opened=False)
    feed(rig, 0.21, background, opened=False)
    assert board.color == "white"
    controller.sensor(False, 0.4)
    assert board.color == "white"


# 한 세션의 포장 성공은 한 번이며 내부 사진과 같은 식별자로 저장된다.
def test_one_package_and_same_session_interior(rig):
    rig[3].staged = True
    warmup(rig)
    present(rig)
    assert rig[0].state == "CAPTURED"
    assert rig[1].capture.call_count == 1
    for index in range(32, 48):
        feed(rig, index / 10, rig[5], opened=False)
    event = rig[3].head()
    assert event["package_capture_ok"] and event["interior_capture_ok"]
    assert event["movement_policy"] == "INVENTORY_STATE"
    assert datetime.fromisoformat(event["closed_at"]) >= datetime.fromisoformat(event["occurred_at"])
    assert rig[1].capture.call_count == 2
    assert rig[0].state == "CLOSED"


# 포장만 고해상도 검출 박스와 여유를 잘라 저장하고 내부 사진은 원본 크기를 유지한다.
def test_package_crop_and_interior_full_frame(rig):
    controller, _, _, outbox, _, package = rig
    warmup(rig)
    present(rig)
    for index in range(32, 48):
        feed(rig, index / 10, package, opened=False)
    event = outbox.head()
    package_bytes = (event["_directory"] / "package.jpg").read_bytes()
    interior_bytes = (event["_directory"] / "interior.jpg").read_bytes()
    cropped = cv2.imdecode(np.frombuffer(package_bytes, np.uint8), cv2.IMREAD_COLOR)
    interior = cv2.imdecode(np.frombuffer(interior_bytes, np.uint8), cv2.IMREAD_COLOR)
    assert cropped.shape == (163, 212, 3)
    assert interior.shape == package.shape
    assert controller.state == "CLOSED"


# 뚜껑과 사진 저장 상태를 실제로 일어난 순서에 맞게 알린다.
def test_capture_messages(rig, caplog):
    with caplog.at_level(logging.INFO):
        warmup(rig)
        present(rig)
        for index in range(32, 48):
            feed(rig, index / 10, rig[5], opened=False)
    assert "뚜껑이 열렸습니다" in caplog.text
    assert "촬영 준비 완료" in caplog.text
    assert "포장 감지: 신뢰도 90% · 연속 감지 " in caplog.text
    assert "약품 포장 감지 및 촬영 성공" in caplog.text
    assert "뚜껑이 닫혔습니다" in caplog.text
    assert "보관함 내부 촬영 성공" in caplog.text


# 파일 저장보다 먼저 수신한 고해상도 프레임 시각을 키에 사용한다.
def test_package_key_uses_frame_time(rig, monkeypatch):
    import medikit_pi.controller as module

    # 저장 시계가 프레임 수신보다 2초 늦은 상황을 고정한다.
    class FixedDatetime(datetime):
        # 시험에 사용할 고정된 UTC 시각을 반환한다.
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 19, 14, 7, 43, tzinfo=timezone.utc)

    rig[3].staged = True
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: 12.0))
    monkeypatch.setattr(module, "datetime", FixedDatetime)
    warmup(rig)
    rig[1].capture.side_effect = lambda: Frame(rig[5], True, 10.0)
    present(rig)
    event = rig[3].stage_head("OCR_REQUESTED")
    assert event["package_image_key"] == f"ocr-input/20261119_230741_{event['session_id']}_package.jpg"


# 준비 전에 존재한 포장은 새 제시로 촬영하지 않는다.
def test_existing_background_not_captured(rig):
    for index in range(40):
        feed(rig, index / 10, rig[5])
    assert rig[0].state == "READY"
    rig[1].capture.assert_not_called()


# 동일한 초점·무늬에서 밝기나 흑백 픽셀 비율만 달라지는 합성 프레임을 만든다.
def textured_frame(shape, low, high):
    pattern = np.indices(shape[:2]).sum(axis=0) % 2
    gray = np.where(pattern, low, high).astype(np.uint8)
    return np.repeat(gray[:, :, None], 3, axis=2)


# 1% 미만의 포장도 화질이 좋으면 노란불을 거쳐 사진을 한 번 저장한다.
def test_small_clear_package_is_saved(rig, caplog):
    controller, camera, board, outbox, base, _ = rig
    controller.detector.box = Detection((0.45, 0.4, 0.50, 0.5), 0.9, "bottle")
    image = base.copy()
    image[72:90, 144:160] = textured_frame((18, 16), 125, 190)
    warmup(rig)
    with caplog.at_level(logging.INFO):
        for index in range(12, 42):
            feed(rig, index / 10, image)
    session_id = controller.session_id
    controller.finish()
    event = outbox.head()
    assert event["session_id"] == session_id and event["package_capture_ok"]
    assert [color for _, color in board.history] == ["green", None, "yellow", "green", None]
    camera.capture.assert_called_once()
    assert any("촬영본" in line and "작음" in line for line in caplog.messages)


# 초점이 맞고 무늬가 선명해도 포장 여백을 포함한 영역의 노출이 나쁘면 촬영을 대기한다.
@pytest.mark.parametrize(
    ("low", "high"),
    [(35, 45), (220, 230), (25, 145), (130, 245)],
    ids=["low-mean", "high-mean", "dark-ratio", "bright-ratio"],
)
def test_poor_package_exposure_blocks_preview(rig, low, high):
    controller, camera, board, _, base, _ = rig
    # 프레임 대부분은 양호하여 전체 화면을 잘못 평가하면 작은 포장의 결함을 놓친다.
    controller.detector.box = Detection((0.37, 0.35, 0.6, 0.6), 0.9, "box")
    image = base.copy()
    image[55:115, 106:203] = textured_frame((60, 97), low, high)
    warmup(rig)
    for index in range(12, 40):
        feed(rig, index / 10, image)
    assert controller.state == "READY" and board.color == "yellow"
    camera.capture.assert_not_called()


# 손을 포함한 검출 박스가 화면 끝에 닿아도 포장이 선명하면 사진을 저장한다.
def test_package_margin_is_advisory(rig, caplog):
    controller, camera, _, outbox, _, package = rig
    controller.detector.box = Detection((0.0, 0.1, 0.65, 0.85), 0.9, "box")
    package[53:120, :32] = 135
    warmup(rig)
    with caplog.at_level(logging.INFO):
        present(rig)
    assert controller.state == "CAPTURED"
    camera.capture.assert_called_once()
    controller.finish()
    assert outbox.head()["package_capture_ok"]
    for stage in ("미리보기", "촬영본"):
        assert f"포장 {stage} 품질: 가장자리 여백 부족(참고)" in caplog.text


# 빈 배경에 초점이 안 맞거나 준비 중 움직여도 제한 시간 안에 탐색으로 진입한다.
@pytest.mark.parametrize("moving", [False, True], ids=["stable", "moving"])
def test_package_search_starts_without_background_focus(rig, moving):
    controller, camera, _, _, background, _ = rig
    for index in range(18):
        image = background + 25 if moving and index % 2 else background
        feed(rig, index / 10, image, focused=False)
        if controller.state == "READY":
            break
    assert controller.state == "READY"
    camera.capture.assert_not_called()


# 닫힘 미리보기에는 포장 후보 없이 전체 화면의 흰색 포화 비율을 적용한다.
def test_interior_glare_blocks_preview(rig):
    controller, camera, _, outbox, base, _ = rig
    warmup(rig)
    bright = textured_frame(base.shape, 130, 250)
    for index in range(12, 120):
        feed(rig, index / 10, bright, opened=False)
    camera.capture.assert_not_called()
    assert controller.state == "CLOSED"
    assert not outbox.head()["interior_capture_ok"]


# 미리보기와 거의 같은 고해상도 사진도 흑백 픽셀 비율이 경계를 넘으면 저장하지 않는다.
@pytest.mark.parametrize("kind", ["package", "interior"])
@pytest.mark.parametrize(
    ("preview_levels", "still_levels"),
    [((31, 150), (29, 150)), ((130, 244), (130, 246))],
    ids=["dark-ratio", "bright-ratio"],
)
def test_final_photo_exposure_rechecked(rig, kind, preview_levels, still_levels):
    controller, camera, _, outbox, base, _ = rig
    preview = textured_frame(base.shape, *preview_levels)
    still = textured_frame(base.shape, *still_levels).repeat(2, axis=0).repeat(2, axis=1)
    warmup(rig)
    for index in range(12, 42):
        feed(rig, index / 10, preview, opened=kind == "package", still_image=still)
    assert camera.capture.call_count > 0
    controller.finish()
    assert not outbox.head()[f"{kind}_capture_ok"]


# 같은 테스트 사진에서 조정한 과노출 비율이 미리보기와 최종 저장에 모두 적용된다.
def test_bright_ratio_can_be_tuned_for_real_camera(rig):
    original, camera, board, outbox, base, _ = rig
    config = original.config
    config["vision"]["max_bright_ratio"] = 0.6
    controller = CaptureController(config, camera, board, outbox, original.detector)
    bright = textured_frame(base.shape, 130, 246)
    tuned = (controller, camera, board, outbox, base, bright)
    warmup(tuned)
    present(tuned)
    assert controller.state == "CAPTURED"
    controller.finish()
    assert outbox.head()["package_capture_ok"]


# AF 또는 선명도가 불충분하면 포장 성공으로 세지 않는다.
def test_unfocused_package_not_success(rig):
    warmup(rig)
    feed(rig, 1.2, rig[5], focused=False)
    assert rig[2].color == "yellow"
    for index in range(13, 40):
        feed(rig, index / 10, rig[5], focused=False)
    assert rig[0].state == "READY"
    rig[1].capture.assert_not_called()
    assert rig[2].color == "yellow"


# 촬영·저장 실패 때는 노란색을 유지하고 같은 세션의 저장 성공에만 초록색을 표시한다.
@pytest.mark.parametrize("fail_save", [False, True], ids=["camera", "storage"])
def test_capture_failure_can_retry(rig, fail_save, monkeypatch):
    warmup(rig)
    assert rig[2].color is None
    if fail_save:
        operation = Mock(wraps=rig[3].save_capture)
        monkeypatch.setattr(rig[3], "save_capture", operation)
    else:
        operation = rig[1].capture
    operation.side_effect = OSError("capture or storage unavailable")
    present(rig)
    assert rig[0].state == "READY"
    assert rig[2].color == "yellow"
    assert [color for _, color in rig[2].history] == ["green", None, "yellow"]
    operation.side_effect = None
    for index in range(32, 65):
        feed(rig, index / 10, rig[5])
        if rig[0].state == "CAPTURED":
            break
    assert rig[0].state == "CAPTURED"
    assert rig[2].color == "green"
    rig[0].sensor(True, index / 10 + 0.21)
    assert rig[2].color is None


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
    colors = [color for _, color in rig[2].history]
    assert colors[colors.index("white") :] == ["white", None]


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


# 화질은 양호하지만 미리보기와 사진이 크게 달라지면 저장을 막고 이유를 남긴다.
def test_capture_logs_excess_movement(rig, caplog):
    controller, camera, _, outbox, _, package = rig
    warmup(rig)
    controller.rules["package_capture_motion_threshold"] = 0.1
    camera.capture.return_value = Frame(255 - package, True, 1.2)
    with caplog.at_level(logging.INFO):
        assert not controller.capture("package", Frame(package, True, 1.2), (0.1, 0.1, 0.65, 0.85))
    assert "촬영 보류: 화면 변화" in caplog.text
    controller.finish()
    assert not outbox.head()["package_capture_ok"]


# 실측 사례 수준의 미리보기·고해상도 밝기 차이에도 내부 사진을 저장한다.
def test_interior_capture_allows_mode_difference(rig):
    controller, camera, board, outbox, _, package = rig
    outbox.staged = True
    controller.initialize_baseline(0.0)
    board.opened = False
    still = np.clip(package.astype(np.int16) + 43, 0, 255).astype(np.uint8)
    camera.capture.return_value = Frame(still, True, 1.0)
    assert controller.capture("interior", Frame(package, True, 1.0))
    controller.finish()
    assert outbox.head()["interior_capture_ok"]


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


# 배경에서 검출된 예전 후보 박스에 새 물체가 겹쳐도 노란불을 잘못 켜지 않는다.
def test_yellow_waits_for_detection_from_changed_frame(rig):
    warmup(rig)
    detector = rig[0].detector
    detector.submit = Mock()
    detector.result = DetectionResult(1.1, (detector.box,), rig[4])
    feed(rig, 1.2, rig[5])
    assert rig[2].color is None
    detector.result = DetectionResult(1.2, (detector.box,), rig[5])
    feed(rig, 1.3, rig[5])
    assert rig[2].color == "yellow"


# 검출 공백과 최신 미검출에서 소등하고, 다시 포장이 잡히면 재점등한다.
@pytest.mark.parametrize("empty_result", [False, True], ids=["expired", "no-package"])
def test_yellow_tracks_current_detection(rig, empty_result):
    warmup(rig)
    controller, camera, board, _, background, package = rig
    detector = controller.detector
    detector.submit = Mock()
    detector.result = DetectionResult(1.2, (detector.box,), package)
    feed(rig, 1.2, package)
    feed(rig, 1.3, package)
    assert board.color == "yellow"

    detector.result = DetectionResult(1.4, (), background) if empty_result else None
    feed(rig, 1.4, background)
    assert board.color is None
    camera.capture.assert_not_called()

    detector.result = DetectionResult(1.5, (detector.box,), package)
    feed(rig, 1.5, package)
    assert board.color == "yellow"
    controller.camera_error(1.6)
    assert board.color is None


# 미리보기에서 1픽셀씩 흔들리고 촬영본에서 2픽셀 움직인 선명한 포장도 한 번 저장한다.
def test_package_capture_tolerates_hand_motion(rig):
    controller, camera, _, outbox, base, _ = rig
    package = base.copy()
    rows, columns = np.indices((135, 176))
    pattern = ((rows // 6 + columns // 6) % 2)[..., None]
    package[18:153, 32:208] = 100 + pattern * 72
    shifted = np.roll(package, 1, axis=1)
    still = np.roll(package, 2, axis=1)
    box = controller.detector.box.box
    crops = [crop_box(gray_frame(image), box) for image in (package, shifted, still)]
    assert controller.rules["motion_threshold"] < motion_score(*crops[:2])
    assert controller.rules["capture_motion_threshold"] < motion_score(crops[0], crops[2])

    warmup(rig)
    for index in range(12, 32):
        frame = package if index % 2 == 0 else shifted
        feed(rig, index / 10, frame, still_image=np.roll(frame, 2, axis=1))
    assert controller.state == "CAPTURED"
    camera.capture.assert_called_once()
    controller.finish()
    assert outbox.head()["package_capture_ok"]


# 한 번의 검출 결과를 여러 미리보기 프레임에서 읽어도 검출 시간이 늘지 않는다.
def test_repeated_result_does_not_trigger_capture(rig):
    warmup(rig)
    controller, camera, _, _, _, package = rig
    detector = controller.detector
    feed(rig, 1.2, package)
    detector.submit = Mock()
    detector.result = DetectionResult(1.3, (detector.box,), package)
    for index in range(13, 24):
        feed(rig, index / 10, package)
    camera.capture.assert_not_called()


# 늦게 도착한 유효한 박스로 감지 시간이 차도 현재 화면에서 포장이 사라졌으면 촬영하지 않는다.
def test_delayed_positive_cannot_capture_missing_package(rig):
    warmup(rig)
    controller, camera, _, _, background, package = rig
    detector = controller.detector
    feed(rig, 1.2, package)
    detector.submit = Mock()
    detector.result = DetectionResult(1.3, (detector.box,), package)
    feed(rig, 1.4, background)
    detector.result = DetectionResult(2.0, (detector.box,), package)
    feed(rig, 2.1, background)
    assert controller.tracker.stable_duration >= controller.rules["stable_seconds"]
    camera.capture.assert_not_called()


# 잘못된 조회 주기, 무제한 재시도 또는 유효 범위 밖의 화질 기준을 거부한다.
@pytest.mark.parametrize(
    "old,new",
    [
        ("poll_seconds = 2.5", "poll_seconds = 1.0"),
        ("close_retries = 2", "close_retries = 3"),
        ("max_bright_ratio = 0.20", "max_bright_ratio = 1.1"),
        ("min_brightness = 50.0", "min_brightness = 211.0"),
        ("package_motion_threshold = 0.09", "package_motion_threshold = true"),
        ("package_capture_motion_threshold = 0.35", "package_capture_motion_threshold = 1.1"),
        ("interior_capture_motion_threshold = 0.22", "interior_capture_motion_threshold = 1.1"),
    ],
)
def test_invalid_config_rejected(tmp_path, old, new):
    source = (Path(__file__).parents[1] / "config.example.toml").read_text(encoding="utf-8")
    assert old in source
    path = tmp_path / "bad.toml"
    path.write_text(source.replace(old, new), encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)


# 현장에 남은 예전 설정은 미리보기·촬영본의 선명도 값을 유지하며 새 노출 기준을 읽는다.
def test_existing_quality_config_remains_usable(tmp_path):
    example = (Path(__file__).parents[1] / "config.example.toml").read_text(encoding="utf-8")
    new_fields = (
        "interior_min_sharpness",
        "min_brightness",
        "max_brightness",
        "max_dark_ratio",
        "max_bright_ratio",
        "min_short_side",
        "package_motion_threshold",
        "package_capture_motion_threshold",
        "interior_capture_motion_threshold",
    )
    previous = "\n".join(line for line in example.splitlines() if not line.startswith(new_fields))
    previous = previous.replace("\nsharpness_threshold = 500.0", "\nsharpness_threshold = 65.0")
    previous = previous.replace(
        "still_sharpness_threshold = 500.0", "still_sharpness_threshold = 82.0"
    )
    path = tmp_path / "config.toml"
    path.write_text(previous, encoding="utf-8")
    vision = load_config(path)["vision"]
    assert vision["package_motion_threshold"] == 0.09
    assert vision["package_capture_motion_threshold"] == 0.35
    assert vision["interior_capture_motion_threshold"] == 0.22
    for kind in ("package", "interior"):
        assert capture_thresholds(vision, kind).min_sharpness == 65.0
        assert capture_thresholds(vision, kind, still=True).min_sharpness == 82.0
    assert capture_thresholds(vision, "package").max_bright_ratio == 0.2
