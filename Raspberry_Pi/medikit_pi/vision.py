"""OpenCV 영상 차이·화질 검사와 포장 후보의 연속성을 평가한다."""

from __future__ import annotations

import math

import cv2
import numpy as np

from .detection import Detection


# 전체 화면을 유지하면서 비교용 회색조 영상을 작게 만든다.
def gray_frame(frame: np.ndarray, width: int = 320) -> np.ndarray:
    if frame.size == 0 or width < 1:
        raise ValueError("비어 있는 영상 또는 잘못된 비교 해상도입니다.")
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    if gray.shape[1] > width:
        height = max(1, round(gray.shape[0] * width / gray.shape[1]))
        gray = cv2.resize(gray, (width, height), interpolation=cv2.INTER_AREA)
    return gray


# 연속 영상의 평균 밝기 차이를 0~1 범위로 계산한다.
def motion_score(previous: np.ndarray | None, current: np.ndarray) -> float:
    if previous is None or previous.shape != current.shape:
        return 1.0
    return float(cv2.absdiff(previous, current).mean() / 255.0)


# 정규화된 후보 좌표를 영상 경계 안의 잘라내기 영역으로 바꾼다.
def crop_box(frame: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray:
    if len(box) != 4 or any(not math.isfinite(value) for value in box):
        return frame[:0, :0]
    height, width = frame.shape[:2]
    x1 = max(0, min(width, math.floor(box[0] * width)))
    y1 = max(0, min(height, math.floor(box[1] * height)))
    x2 = max(0, min(width, math.ceil(box[2] * width)))
    y2 = max(0, min(height, math.ceil(box[3] * height)))
    return frame[y1:y2, x1:x2]


# 고주파 경계 성분으로 전체 영상 또는 포장 영역의 선명도를 측정한다.
def sharpness(frame: np.ndarray, box: tuple[float, float, float, float] | None = None) -> float:
    region = crop_box(frame, box) if box is not None else frame
    if region.size == 0:
        return 0.0
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY) if region.ndim == 3 else region
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


# 준비 시점 배경에 비해 후보 영역에서 바뀐 픽셀 비율을 계산한다.
def change_fraction(
    background: np.ndarray,
    current: np.ndarray,
    box: tuple[float, float, float, float],
    pixel_threshold: int = 25,
) -> float:
    if background.shape != current.shape:
        return 0.0
    before = crop_box(background, box)
    after = crop_box(current, box)
    if before.size == 0:
        return 0.0
    return float(np.count_nonzero(cv2.absdiff(before, after) > pixel_threshold) / before.size)


# 후보 사각형 두 개의 겹침 비율로 같은 포장의 연속성을 평가한다.
def iou(
    first: tuple[float, float, float, float], second: tuple[float, float, float, float]
) -> float:
    overlap_width = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    overlap_height = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
    overlap = overlap_width * overlap_height
    first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    union = first_area + second_area - overlap
    return overlap / union if union > 0 else 0.0


# 화면 어디에 있든 배경과 다르고 크기 기준을 만족하는 최고 신뢰도 후보를 선택한다.
def select_candidate(
    detections: tuple[Detection, ...] | list[Detection],
    background: np.ndarray,
    current: np.ndarray,
    minimum_change: float = 0.08,
    min_area: float = 0.01,
    max_area: float = 0.9,
    pixel_threshold: int = 25,
) -> Detection | None:
    best = None
    for detection in detections:
        x1, y1, x2, y2 = detection.box
        if not all(math.isfinite(value) and 0 <= value <= 1 for value in detection.box):
            continue
        area = max(0, x2 - x1) * max(0, y2 - y1)
        if not min_area <= area <= max_area:
            continue
        # 기준 배경 속 정적 포장을 신규 제시로 처리하지 않는다.
        if change_fraction(background, current, detection.box, pixel_threshold) < minimum_change:
            continue
        if best is None or detection.confidence > best.confidence:
            best = detection
    return best


class CandidateTracker:
    # 후보 연속 검출에 필요한 시간과 겹침 기준을 설정한다.
    def __init__(
        self,
        stable_seconds: float = 1.0,
        min_iou: float = 0.6,
        max_gap_seconds: float = 1.0,
    ) -> None:
        if stable_seconds <= 0 or not 0 < min_iou <= 1 or max_gap_seconds <= 0:
            raise ValueError("포장 안정화 시간·겹침·검출 간격 설정을 확인하세요.")
        self.stable_seconds = stable_seconds
        self.min_iou = min_iou
        self.max_gap_seconds = max_gap_seconds
        self.reset()

    # 새 검출과 연속 영상 안정성이 모두 유지될 때만 촬영 조건을 만족시킨다.
    def update(self, candidate: Detection | None, detected_at: float, frame_stable: bool) -> bool:
        if candidate is None or not frame_stable:
            self.reset()
            return False
        if self._last_at is not None and detected_at <= self._last_at:
            return False
        continues = (
            self._candidate is not None
            and self._last_at is not None
            and detected_at - self._last_at <= self.max_gap_seconds
            and iou(candidate.box, self._candidate.box) >= self.min_iou
        )
        if not continues:
            self._started_at = detected_at
        self._candidate = candidate
        self._last_at = detected_at
        return detected_at - self._started_at >= self.stable_seconds

    # 흔들림이나 후보 소실 이후에는 안정화 시간을 처음부터 측정한다.
    def reset(self) -> None:
        self._candidate: Detection | None = None
        self._last_at: float | None = None
        self._started_at = 0.0
