"""한 장의 포장 이미지에서 화질 지표와 실험용 주의 사유를 계산한다."""

from dataclasses import dataclass
import math

import cv2
import numpy as np


@dataclass(frozen=True)
class QualityThresholds:
    # 초기 비교용 값이며 실제 카메라·포장·OCR 결과로 보정한 합격 기준이 아니다.
    min_sharpness: float = 500.0
    min_brightness: float = 50.0
    max_brightness: float = 210.0
    max_dark_ratio: float = 0.30
    max_bright_ratio: float = 0.20
    min_short_side: int = 160

    # 잘못된 실험 기준이 정상 판정에 사용되지 않도록 범위를 확인한다.
    def __post_init__(self) -> None:
        values = (
            self.min_sharpness,
            self.min_brightness,
            self.max_brightness,
            self.max_dark_ratio,
            self.max_bright_ratio,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float, np.integer, np.floating))
            or not math.isfinite(value)
            for value in values
        ):
            raise ValueError("화질 기준은 유한한 수여야 합니다.")
        if self.min_sharpness < 0:
            raise ValueError("선명도 기준은 0 이상이어야 합니다.")
        if not 0 <= self.min_brightness < self.max_brightness <= 255:
            raise ValueError("밝기 기준은 0~255 범위이고 최솟값이 최댓값보다 작아야 합니다.")
        if not 0 <= self.max_dark_ratio <= 1 or not 0 <= self.max_bright_ratio <= 1:
            raise ValueError("밝고 어두운 영역 비율 기준은 0~1 범위여야 합니다.")
        if (
            isinstance(self.min_short_side, bool)
            or not isinstance(self.min_short_side, (int, np.integer))
            or self.min_short_side < 1
        ):
            raise ValueError("최소 짧은 변은 1 이상의 정수여야 합니다.")


DEFAULT_THRESHOLDS = QualityThresholds()
WARNING_MESSAGES = {
    "blur": "흐림 의심",
    "dark": "어두움 의심",
    "bright": "과노출·반사 의심",
    "small": "촬영 영역이 작음",
    "edge": "화면 경계로 여백 부족",
}


@dataclass(frozen=True)
class PhotoQuality:
    width: int
    height: int
    analysis_width: int
    analysis_height: int
    sharpness: float
    mean_brightness: float
    dark_ratio: float
    bright_ratio: float
    warnings: tuple[str, ...]


# 원본을 보존하면서 제한된 크기의 측정 영상으로 화질 수치와 주의 사유를 반환한다.
def assess_photo(
    frame: np.ndarray,
    *,
    padding_clipped: bool = False,
    thresholds: QualityThresholds = DEFAULT_THRESHOLDS,
) -> PhotoQuality:
    if (
        not isinstance(frame, np.ndarray)
        or frame.ndim != 3
        or frame.shape[2] != 3
        or not frame.size
        or frame.dtype != np.uint8
    ):
        raise ValueError("화질 검사에는 비어 있지 않은 uint8 BGR 프레임이 필요합니다.")
    if not isinstance(thresholds, QualityThresholds):
        raise ValueError("화질 기준은 QualityThresholds로 지정해야 합니다.")

    height, width = frame.shape[:2]
    scale = min(640 / max(width, height), 1.0)
    analysis_width = max(1, round(width * scale))
    analysis_height = max(1, round(height * scale))
    # 측정 비용을 제한하되 작은 입력을 확대해 해상도가 충분한 것처럼 평가하지 않는다.
    analysis = frame
    if scale < 1.0:
        analysis = cv2.resize(frame, (analysis_width, analysis_height), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(analysis, cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    mean_brightness = float(gray.mean())
    dark_ratio = float(np.mean(gray <= 30))
    bright_ratio = float(np.mean(gray >= 245))

    warnings = []
    # 수치는 포장 무늬·노이즈·워터마크에도 영향을 받으므로 식별 성공으로 해석하지 않는다.
    if sharpness < thresholds.min_sharpness:
        warnings.append("blur")
    if mean_brightness < thresholds.min_brightness or dark_ratio > thresholds.max_dark_ratio:
        warnings.append("dark")
    # 흰 포장도 밝은 영역에 포함되므로 빛 반사를 확정하지 않고 의심 사유로만 알린다.
    if mean_brightness > thresholds.max_brightness or bright_ratio > thresholds.max_bright_ratio:
        warnings.append("bright")
    if min(width, height) < thresholds.min_short_side:
        warnings.append("small")
    if padding_clipped:
        warnings.append("edge")

    # 한 프레임의 결과이므로 시간에 따른 안정성이나 포장 전체 포함 여부는 판정하지 않는다.
    return PhotoQuality(
        width=width,
        height=height,
        analysis_width=analysis_width,
        analysis_height=analysis_height,
        sharpness=sharpness,
        mean_brightness=mean_brightness,
        dark_ratio=dark_ratio,
        bright_ratio=bright_ratio,
        warnings=tuple(warnings),
    )
