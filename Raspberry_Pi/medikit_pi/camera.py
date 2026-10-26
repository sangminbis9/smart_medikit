"""Picamera2 및 녹화 영상의 동일한 프레임 인터페이스."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

LOGGER = logging.getLogger(__name__)


class CameraError(RuntimeError):
    """재시작 또는 사용자 확인이 필요한 카메라 오류."""


@dataclass(frozen=True)
class Frame:
    image: np.ndarray
    autofocus_ok: bool
    timestamp: float
    autofocus_simulated: bool = False


class PiCamera:
    # 실제 장치 접근을 시작 시점까지 늦추고 촬영 설정을 보관한다.
    def __init__(self, config: dict):
        self.config = config
        self.preview_size = tuple(config.get("preview_size", (768, 432)))
        self.still_size = tuple(config.get("still_size", (4608, 2592)))
        self.timeout = float(config.get("timeout_seconds", 3.0))
        self._camera = None
        self._controls = None
        self._preview_config = None
        self._still_config = None
        self._running = False

    # 저해상도 탐색 모드와 연속 자동초점을 시작한다.
    def start(self) -> None:
        if self._running:
            return
        try:
            if self._camera is None:
                from libcamera import controls
                from picamera2 import Picamera2

                self._controls = controls
                self._camera = Picamera2(self.config.get("camera_index", 0))
                if "AfMode" not in self._camera.camera_controls:
                    raise CameraError("자동초점 카메라가 필요합니다.")
                common = {"AfMode": controls.AfModeEnum.Continuous}
                # RGB888은 Picamera2 배열에서 OpenCV의 BGR 순서다.
                self._preview_config = self._camera.create_preview_configuration(
                    main={"size": self.preview_size, "format": "RGB888"},
                    raw=None,
                    controls={**common, "FrameRate": self.config.get("fps", 15)},
                    buffer_count=4,
                    queue=False,
                )
                self._still_config = self._camera.create_still_configuration(
                    main={"size": self.still_size, "format": "RGB888"},
                    raw=None,
                    controls=common,
                    buffer_count=2,
                    queue=False,
                )
                self._camera.configure(self._preview_config)
            self._camera.start(show_preview=False)
            self._running = True
        except Exception as exc:
            self._dispose()
            raise CameraError("카메라를 시작하지 못했습니다.") from exc

    # 현재 요청의 영상과 자동초점 상태를 함께 복사하고 버퍼를 반환한다.
    def _frame(self, request) -> Frame:
        try:
            metadata = request.get_metadata()
            image = request.make_array("main").copy()
            focused = metadata.get("AfState") == self._controls.AfStateEnum.Focused
            return Frame(image, focused, time.monotonic())
        finally:
            request.release()

    # 비동기 카메라 작업을 제한시간 안에 완료하고 오류 시 장치를 해제한다.
    def _wait_frame(self, job) -> Frame:
        try:
            return self._frame(self._camera.wait(job, timeout=self.timeout))
        except Exception as exc:
            self._dispose()
            raise CameraError("카메라 프레임을 받지 못했습니다.") from exc

    # 이전에 대기 중이던 프레임을 피하여 새 탐색 영상을 읽는다.
    def read(self) -> Frame:
        if not self._running:
            raise CameraError("카메라가 시작되지 않았습니다.")
        try:
            job = self._camera.capture_request(wait=False, flush=True)
        except Exception as exc:
            self._dispose()
            raise CameraError("카메라 프레임 요청에 실패했습니다.") from exc
        return self._wait_frame(job)

    # 고해상도로 잠시 전환한 뒤 원래 탐색 모드로 복귀한다.
    def capture(self) -> Frame:
        if not self._running:
            raise CameraError("카메라가 시작되지 않았습니다.")
        deadline = time.monotonic() + self.timeout
        try:
            job = self._camera.switch_mode_and_drop_frames(
                self._still_config,
                self.config.get("still_delay_frames", 3),
                wait=False,
            )
            self._wait_until(job, deadline)
            while True:
                job = self._camera.capture_request(wait=False, flush=True)
                request = self._wait_until(job, deadline)
                try:
                    state = request.get_metadata().get("AfState")
                except Exception:
                    request.release()
                    raise
                # 고해상도 전환 뒤 렌즈 탐색이 끝난 프레임만 화질 검사로 넘긴다.
                if state in (
                    self._controls.AfStateEnum.Focused,
                    self._controls.AfStateEnum.Failed,
                ):
                    frame = self._frame(request)
                    break
                request.release()
            # 고해상도 요청을 먼저 반환하여 모드 전환 시 동시 버퍼 점유를 줄인다.
            self._wait_until(self._camera.switch_mode(self._preview_config, wait=False), deadline)
            return frame
        except Exception as exc:
            self._dispose()
            raise CameraError("고해상도 촬영 또는 초점 확인에 실패했습니다.") from exc

    # 모드 전환과 초점 탐색 전체가 하나의 제한시간을 공유하도록 한다.
    def _wait_until(self, job, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("고해상도 촬영 제한시간 초과")
        return self._camera.wait(job, timeout=min(self.timeout, remaining))

    # 제한시간 동안 초점을 다시 맞춘 뒤 연속 자동초점으로 복귀한다.
    def refocus(self) -> bool:
        if not self._running:
            raise CameraError("카메라가 시작되지 않았습니다.")
        try:
            job = self._camera.autofocus_cycle(wait=False)
            focused = bool(self._camera.wait(job, timeout=self.timeout))
            self._camera.set_controls({"AfMode": self._controls.AfModeEnum.Continuous})
            return focused
        except Exception as exc:
            self._dispose()
            raise CameraError("자동초점을 완료하지 못했습니다.") from exc

    # 촬영하지 않는 동안 센서 스트리밍을 중지한다.
    def stop(self) -> None:
        if self._camera is not None and self._running:
            try:
                self._camera.stop()
            except Exception as exc:
                self._dispose()
                raise CameraError("카메라 중지에 실패했습니다.") from exc
            finally:
                self._running = False

    # 미완료 작업을 취소하고 장치를 닫아 다음 시작에서 다시 연다.
    def _dispose(self) -> None:
        camera, self._camera = self._camera, None
        self._running = False
        if camera is None:
            return
        try:
            camera.cancel_all_and_flush()
        except Exception:
            LOGGER.warning("카메라 대기 작업 취소에 실패했습니다.")
        finally:
            try:
                camera.close()
            except Exception:
                LOGGER.warning("카메라 자원 해제에 실패했습니다.")

    # 프로그램 종료 시 카메라 자원을 모두 해제한다.
    def close(self) -> None:
        self._dispose()


class VideoCamera:
    # 녹화 파일을 열고 프레임 번호로 재현 가능한 영상 시각을 계산한다.
    def __init__(self, path: str | Path, preview_size=(768, 432)):
        self._capture = cv2.VideoCapture(str(path))
        if not self._capture.isOpened():
            self._capture.release()
            raise CameraError("테스트 영상을 열 수 없습니다.")
        self.fps = float(self._capture.get(cv2.CAP_PROP_FPS))
        if not np.isfinite(self.fps) or self.fps <= 0:
            self._capture.release()
            raise CameraError("영상의 FPS를 확인할 수 없습니다.")
        self.preview_size = tuple(preview_size)
        self.frame_index = -1
        self.timestamp = 0.0
        self._current = None

    # 영상은 세션 사이에도 순서대로 재생하므로 위치를 초기화하지 않는다.
    def start(self) -> None:
        pass

    # 녹화 영상의 재생 위치를 유지한다.
    def stop(self) -> None:
        pass

    # 다음 영상 프레임을 읽고 탐색 해상도로 축소한다.
    def read(self) -> Frame | None:
        ok, image = self._capture.read()
        if not ok:
            self._current = None
            return None
        self.frame_index += 1
        self.timestamp = self.frame_index / self.fps
        self._current = image
        height, width = image.shape[:2]
        # 원본 화면비를 유지해야 탐색 좌표와 고해상도 화질 비교가 일치한다.
        scale = min(1.0, self.preview_size[0] / width, self.preview_size[1] / height)
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        preview = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        # 녹화에는 AfState가 없으므로 실제 자동초점 검증과 구분한다.
        return Frame(preview, True, self.timestamp, autofocus_simulated=True)

    # 추가 프레임 소비 없이 현재 영상 원본 해상도의 이미지를 반환한다.
    def capture(self) -> Frame:
        if self._current is None:
            raise CameraError("촬영할 테스트 영상 프레임이 없습니다.")
        return Frame(self._current.copy(), True, self.timestamp, autofocus_simulated=True)

    # 녹화 영상에는 렌즈가 없으므로 모의 자동초점 성공을 반환한다.
    def refocus(self) -> bool:
        return True

    # 영상 파일 핸들과 보관 중인 이미지를 해제한다.
    def close(self) -> None:
        self._capture.release()
        self._current = None
