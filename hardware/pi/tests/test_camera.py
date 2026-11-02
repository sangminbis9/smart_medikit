import sys
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from medikit_pi.camera import CameraError, PiCamera, VideoCamera


class FakeRequest:
    # 영상 버퍼와 자동초점 메타데이터를 모의한다.
    def __init__(self, focused=2):
        self.image = np.full((8, 12, 3), 90, dtype=np.uint8)
        self.focused = focused
        self.released = False

    # 같은 촬영 요청의 초점 상태를 반환한다.
    def get_metadata(self):
        return {"AfState": self.focused}

    # 해제 이후 재사용 여부를 검증할 영상 버퍼를 반환한다.
    def make_array(self, name):
        assert name == "main"
        return self.image

    # 원본 버퍼를 덮어 복사본 소유권을 검사한다.
    def release(self):
        self.released = True
        self.image[:] = 0


class FakePicamera:
    # 설정과 비동기 작업 요청을 기록한다.
    def __init__(self, index):
        self.camera_controls = {"AfMode": True}
        self.request = FakeRequest()
        self.started = False
        self.closed = False
        self.cancelled = False
        self.timeout = False
        self.requests = []

    # 탐색 모드 설정을 그대로 보관한다.
    def create_preview_configuration(self, **kwargs):
        self.preview = kwargs
        return kwargs

    # 정지영상 모드 설정을 그대로 보관한다.
    def create_still_configuration(self, **kwargs):
        self.still = kwargs
        return kwargs

    # 활성 구성을 기록한다.
    def configure(self, config):
        self.config = config

    # 카메라 시작을 기록한다.
    def start(self, show_preview):
        self.started = True

    # 프레임 요청에서 최신 영상 요구 여부를 기록한다.
    def capture_request(self, **kwargs):
        self.capture_args = kwargs
        return "request"

    # 고해상도 전환 인자를 기록한다.
    def switch_mode_and_drop_frames(self, config, frames, **kwargs):
        self.still_args = {**kwargs, "frames": frames}
        return "switch"

    # 모드 복귀 전에 촬영 버퍼가 반환되었는지 확인한다.
    def switch_mode(self, config, **kwargs):
        assert self.request.released
        self.config = config
        return "switch"

    # 제한시간과 요청 완료 또는 오류를 모의한다.
    def wait(self, job, timeout):
        assert 0 < timeout <= 0.1
        if self.timeout:
            raise TimeoutError()
        if job == "switch":
            return None
        if self.requests:
            self.request = self.requests.pop(0)
        return self.request

    # 중지 요청을 기록한다.
    def stop(self):
        self.started = False

    # 미완료 작업 취소를 기록한다.
    def cancel_all_and_flush(self):
        self.cancelled = True

    # 장치 해제를 기록한다.
    def close(self):
        self.closed = True


# Linux 카메라 의존성 대신 모의 모듈을 주입한다.
@pytest.fixture
def fake_pi(monkeypatch):
    controls = SimpleNamespace(
        AfModeEnum=SimpleNamespace(Continuous=2),
        AfStateEnum=SimpleNamespace(Focused=2, Failed=3),
    )
    monkeypatch.setitem(sys.modules, "libcamera", SimpleNamespace(controls=controls))
    monkeypatch.setitem(sys.modules, "picamera2", SimpleNamespace(Picamera2=FakePicamera))
    camera = PiCamera({"timeout_seconds": 0.1})
    camera.start()
    return camera


# 최신 프레임과 초점 메타데이터를 읽고 카메라 버퍼를 반환하는지 검사한다.
def test_frame_owns_pixels_and_releases_request(fake_pi):
    driver = fake_pi._camera
    frame = fake_pi.read()
    assert frame.autofocus_ok
    assert not frame.autofocus_simulated
    assert frame.image.mean() == 90
    assert driver.request.released
    assert driver.capture_args == {"wait": False, "flush": True}
    assert driver.preview["main"]["format"] == "RGB888"
    assert driver.preview["queue"] is False
    fake_pi.close()


# 고해상도 촬영 당시 초점 실패를 성공으로 덮어쓰지 않는지 검사한다.
def test_high_resolution_preserves_focus_failure(fake_pi):
    driver = fake_pi._camera
    driver.request.focused = 3
    frame = fake_pi.capture()
    assert not frame.autofocus_ok
    assert driver.still["main"]["size"] == (4608, 2592)
    assert driver.still_args == {"wait": False, "frames": 3}
    assert driver.config == driver.preview
    fake_pi.close()


# 모드 전환 뒤 탐색 중 프레임은 버리고 초점이 맞은 사진을 반환한다.
def test_still_waits_for_focus_and_releases_scanning_frame(fake_pi):
    driver = fake_pi._camera
    scanning, focused = FakeRequest(1), FakeRequest(2)
    driver.requests = [scanning, focused]
    frame = fake_pi.capture()
    assert scanning.released and focused.released
    assert frame.autofocus_ok
    assert driver.config == driver.preview
    fake_pi.close()


# 제한시간 초과 시 작업을 취소하고 다음 시작에서 장치를 새로 여는지 검사한다.
def test_timeout_disposes_camera_and_can_restart(fake_pi):
    driver = fake_pi._camera
    driver.timeout = True
    with pytest.raises(CameraError):
        fake_pi.read()
    assert driver.cancelled and driver.closed
    fake_pi.start()
    assert fake_pi._camera is not driver
    fake_pi.close()


# 영상 촬영은 재생 프레임을 추가로 소비하지 않고 원본 해상도를 유지한다.
def test_video_capture_keeps_current_original_frame(tmp_path):
    path = tmp_path / "synthetic.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (80, 48))
    assert writer.isOpened()
    for level in (40, 120, 200):
        writer.write(np.full((48, 80, 3), level, dtype=np.uint8))
    writer.release()
    camera = VideoCamera(path, preview_size=(40, 24))
    first = camera.read()
    assert first.image.shape == (24, 40, 3)
    still = camera.capture()
    assert still.image.shape == (48, 80, 3)
    assert still.autofocus_simulated
    assert camera.frame_index == 0
    second = camera.read()
    assert second.timestamp == 0.1
    assert second.image.mean() > first.image.mean()
    camera.read()
    assert camera.read() is None
    with pytest.raises(CameraError):
        camera.capture()
    camera.close()


# 가로·세로 영상 모두 탐색 경계 안에서 원본 화면비와 원본 촬영을 유지한다.
@pytest.mark.parametrize(
    ("original_size", "preview_shape"),
    [((640, 480), (216, 288, 3)), ((480, 640), (216, 162, 3))],
)
def test_video_preserves_source_aspect_ratio(tmp_path, original_size, preview_shape):
    path = tmp_path / "aspect.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, original_size)
    assert writer.isOpened()
    width, height = original_size
    writer.write(np.full((height, width, 3), 90, dtype=np.uint8))
    writer.release()
    camera = VideoCamera(path, preview_size=(384, 216))
    preview = camera.read()
    original = camera.capture()
    assert preview.image.shape == preview_shape
    assert preview.image.shape[1] / preview.image.shape[0] == width / height
    assert original.image.shape == (height, width, 3)
    camera.close()
