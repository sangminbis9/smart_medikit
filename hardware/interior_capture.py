"""내부 전체 영상의 노출과 움직임을 확인해 로컬 촬영 시점을 고른다."""

import math

import cv2
import numpy as np

if __package__:
    from .image_quality import PhotoQuality, QualityThresholds, assess_photo
else:
    from image_quality import PhotoQuality, QualityThresholds, assess_photo


class InteriorCaptureGate:
    # 빈 보관함도 허용하도록 선명도 차단을 기본 비활성화하고 안정화 기준을 설정한다.
    def __init__(
        self,
        hold_seconds: float = 1.0,
        min_sharpness: float = 0.0,
        max_motion: float = 3.0,
        max_frame_gap: float = 2.0,
    ) -> None:
        for value in (hold_seconds, max_motion, max_frame_gap):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float, np.integer, np.floating))
                or not math.isfinite(value)
            ):
                raise ValueError("내부 촬영의 시간과 움직임 기준은 유한한 수여야 합니다.")
        if hold_seconds <= 0 or max_frame_gap <= 0:
            raise ValueError("내부 촬영의 유지 시간과 최대 관측 간격은 0보다 커야 합니다.")
        if not 0 <= max_motion <= 255:
            raise ValueError("내부 촬영의 움직임 기준은 0~255 범위여야 합니다.")
        self.thresholds = QualityThresholds(min_sharpness=min_sharpness)
        self.hold_seconds = float(hold_seconds)
        self.max_motion = float(max_motion)
        self.max_frame_gap = float(max_frame_gap)
        self.reset_pending()

    # 같은 전체 화면이 최소 세 번 관측되고 노출·움직임 조건을 유지했는지 확인한다.
    def update(self, frame: np.ndarray, now: float) -> bool:
        if (
            isinstance(now, bool)
            or not isinstance(now, (int, float, np.integer, np.floating))
            or not math.isfinite(now)
        ):
            self.reset_pending()
            raise ValueError("관측 시각은 유한한 단조 시각이어야 합니다.")
        try:
            report = assess_photo(frame, thresholds=self.thresholds)
        except (ValueError, cv2.error):
            self.reset_pending()
            raise

        # 오래 관측하지 못한 시간이나 해상도가 다른 영상을 안정 구간에 포함하지 않는다.
        if self._last_at is not None and (
            now <= self._last_at
            or now - self._last_at > self.max_frame_gap
            or frame.shape != self._frame_shape
        ):
            self.reset_pending()
        self.report = report
        self._last_at = float(now)
        self._frame_shape = frame.shape

        # 내부 전체 촬영에서는 크기 경고만 허용하고 노출 및 선택한 선명도 기준은 적용한다.
        if any(warning != "small" for warning in report.warnings):
            self._anchor = None
            self._previous = None
            self._observations = 0
            self.held_seconds = 0.0
            self.motion_score = None
            self.status = "quality"
            return False

        height, width = frame.shape[:2]
        scale = min(160 / max(height, width), 1.0)
        reduced = cv2.resize(
            frame,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
        gray = cv2.GaussianBlur(cv2.cvtColor(reduced, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        if self._anchor is None:
            self._start_hold(gray, float(now))
            self.status = "holding"
            return False

        # 처음 영상도 비교해 인접 프레임 사이에서는 작게 보이는 누적 움직임을 확인한다.
        self.motion_score = max(
            float(cv2.absdiff(gray, self._anchor).mean()),
            float(cv2.absdiff(gray, self._previous).mean()),
        )
        if self.motion_score > self.max_motion:
            self._start_hold(gray, float(now))
            self.status = "moving"
            return False

        self._previous = gray
        self._observations += 1
        self.held_seconds = float(now) - self._hold_started_at
        ready = self._observations >= 3 and self.held_seconds >= self.hold_seconds
        self.status = "ready" if ready else "holding"
        return ready

    # 새 닫힘이나 저장 실패 후 이전 영상·화질·유지 시간을 모두 버린다.
    def reset_pending(self) -> None:
        self.report: PhotoQuality | None = None
        self.held_seconds = 0.0
        self.motion_score: float | None = None
        self.status = "waiting"
        self._anchor: np.ndarray | None = None
        self._previous: np.ndarray | None = None
        self._frame_shape: tuple[int, ...] | None = None
        self._last_at: float | None = None
        self._hold_started_at = 0.0
        self._observations = 0

    # 현재 축소 영상을 새 안정 구간의 첫 관측으로 설정한다.
    def _start_hold(self, gray: np.ndarray, now: float) -> None:
        self._anchor = gray
        self._previous = gray
        self._hold_started_at = now
        self._observations = 1
        self.held_seconds = 0.0
