"""포장 후보의 품질 유지 시간을 확인하고 한 번 제시한 대상의 중복 저장을 막는다."""

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hardware.image_quality import PhotoQuality
    from hardware.package_detector import PackageDetection


class AutoCaptureGate:
    # 연속 관측 기준과 저장 후 재무장 조건을 설정한다.
    def __init__(
        self,
        hold_seconds: float = 2.0,
        rearm_seconds: float = 1.5,
        max_frame_gap: float = 2.0,
        min_iou: float = 0.6,
        min_observations: int = 3,
    ) -> None:
        for value in (hold_seconds, rearm_seconds, max_frame_gap, min_iou):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError("자동 촬영 시간과 겹침 기준은 유한한 양수여야 합니다.")
        if min_iou > 1:
            raise ValueError("포장 겹침 기준은 1 이하여야 합니다.")
        if isinstance(min_observations, bool) or not isinstance(min_observations, int) or min_observations < 3:
            raise ValueError("자동 촬영에는 최소 3회의 연속 관측이 필요합니다.")

        self.hold_seconds = float(hold_seconds)
        self.rearm_seconds = float(rearm_seconds)
        self.max_frame_gap = float(max_frame_gap)
        self.min_iou = float(min_iou)
        self.min_observations = min_observations
        self._locked = False
        self.reset_pending()

    # 새 프레임에서 같은 위치의 단일 양호 후보가 충분히 유지됐는지 판정한다.
    def update(
        self,
        detections: list["PackageDetection"],
        qualities: list["PhotoQuality"],
        now: float,
    ) -> bool:
        if len(detections) != len(qualities):
            self.reset_pending()
            raise ValueError("촬영 후보와 품질 결과의 개수가 일치해야 합니다.")
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
            self.reset_pending()
            raise ValueError("관측 시각은 유한한 단조 시각이어야 합니다.")

        # 관측이 끊긴 시간이나 같은 프레임의 재처리를 안정적인 유지 시간으로 세지 않는다.
        if self._last_at is not None and (now <= self._last_at or now - self._last_at > self.max_frame_gap):
            self.reset_pending()
        self._last_at = now

        if self._locked:
            # 저장 후에는 라벨·위치·품질이 변해도 후보가 남아 있는 동안 재촬영하지 않는다.
            if detections:
                self._clear_pending()
                self.status = "locked"
                return False
            if self._absence_started_at is None:
                self._absence_started_at = now
            self._absence_observations += 1
            self.absent_seconds = now - self._absence_started_at
            self.status = "absent"
            if self.absent_seconds >= self.rearm_seconds and self._absence_observations >= self.min_observations:
                self._locked = False
                self._clear_pending()
                self.status = "waiting"
            return False

        if len(detections) != 1 or qualities[0].warnings:
            self._clear_pending()
            self.status = "multiple" if len(detections) > 1 else "quality" if detections else "waiting"
            return False

        bounds = detections[0].bounds
        continues = (
            self._anchor is not None
            and self._previous is not None
            and self._overlap(bounds, self._anchor) >= self.min_iou
            and self._overlap(bounds, self._previous) >= self.min_iou
        )
        if not continues:
            # 처음 위치도 함께 비교해 조금씩 계속 움직이는 후보의 시간 누적을 막는다.
            self._anchor = bounds
            self._hold_started_at = now
            self._hold_observations = 0
        self._previous = bounds
        self._hold_observations += 1
        self.held_seconds = now - self._hold_started_at
        ready = self.held_seconds >= self.hold_seconds and self._hold_observations >= self.min_observations
        self.status = "ready" if ready else "holding"
        return ready

    # 파일 저장이 실제 성공한 뒤에만 다음 제시까지 자동 저장을 잠근다.
    def mark_saved(self) -> None:
        self._locked = True
        self.reset_pending()

    # 회전·수동 선택·저장 실패 후 시간 누적만 버리고 기존 촬영 잠금은 보존한다.
    def reset_pending(self) -> None:
        self._clear_pending()
        self._last_at: float | None = None
        self.status = "locked" if self._locked else "waiting"

    # 연속 후보와 비감지 구간의 관측 수·표시 시간을 함께 비운다.
    def _clear_pending(self) -> None:
        self._anchor: tuple[int, int, int, int] | None = None
        self._previous: tuple[int, int, int, int] | None = None
        self._hold_started_at = 0.0
        self._hold_observations = 0
        self._absence_started_at: float | None = None
        self._absence_observations = 0
        self.held_seconds = 0.0
        self.absent_seconds = 0.0

    # 오른쪽·아래 끝점을 제외하는 사각형의 교집합 대 합집합 비율을 구한다.
    @staticmethod
    def _overlap(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> float:
        overlap_width = max(0, min(first[2], second[2]) - max(first[0], second[0]))
        overlap_height = max(0, min(first[3], second[3]) - max(first[1], second[1]))
        overlap = overlap_width * overlap_height
        first_area = max(0, first[2] - first[0]) * max(0, first[3] - first[1])
        second_area = max(0, second[2] - second[0]) * max(0, second[3] - second[1])
        union = first_area + second_area - overlap
        return overlap / union if union > 0 else 0.0
