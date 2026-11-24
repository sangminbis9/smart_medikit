"""합성 입력으로 Pi 사진의 측정 크기, 노출 경계값, 포장 여백을 검증한다."""

from dataclasses import replace

import numpy as np
import pytest
from medikit_pi.image_quality import DEFAULT_THRESHOLDS, assess_photo, padded_bounds


# 원본 픽셀로 크기를 판정하고, 화질 측정 시에만 화면 비율을 유지해 줄인다.
@pytest.mark.parametrize(
    ("height", "width", "analysis_size", "small"),
    [(200, 2000, (640, 64), False), (2000, 200, (64, 640), False), (100, 120, (120, 100), True)],
)
def test_quality_preserves_source_and_limits_analysis_size(height, width, analysis_size, small):
    frame = np.full((height, width, 3), 160, dtype=np.uint8)
    frame[::2, ::2] = 80
    original = frame.copy()

    report = assess_photo(frame)

    assert (report.width, report.height) == (width, height)
    assert (report.analysis_width, report.analysis_height) == analysis_size
    assert ("small" in report.warnings) == small
    np.testing.assert_array_equal(frame, original)


# 측정값과 기준이 같을 때는 허용하며, 보정한 기준은 경고에만 반영된다.
def test_quality_limits_are_inclusive_and_do_not_affect_measurements():
    frame = np.full((200, 200, 3), 128, dtype=np.uint8)
    frame[:60] = 30
    frame[60:100] = 245
    measured = assess_photo(frame)
    assert measured.dark_ratio == pytest.approx(0.30)
    assert measured.bright_ratio == pytest.approx(0.20)
    limits = replace(
        DEFAULT_THRESHOLDS,
        min_sharpness=measured.sharpness,
        min_brightness=measured.mean_brightness,
        min_short_side=200,
    )
    assert assess_photo(frame, thresholds=limits).warnings == ()
    upper_limit = replace(limits, min_brightness=0, max_brightness=measured.mean_brightness)
    assert assess_photo(frame, thresholds=upper_limit).warnings == ()

    strict = replace(limits, min_sharpness=measured.sharpness + 1, min_short_side=201)
    report = assess_photo(frame, thresholds=strict)
    assert report.sharpness == measured.sharpness
    assert report.warnings == ("blur", "small")
    assert report.blocking_warnings == ("blur",)


# 소수 좌표의 10% 여백을 바깥쪽 정수로 반올림하고 화면 경계에서만 자른다.
@pytest.mark.parametrize(
    ("box", "bounds", "clipped"),
    [
        ((30.25, 90.25, 120.25, 180.25), (21, 81, 130, 190), False),
        ((1, 1, 158, 238), (0, 0, 160, 240), True),
    ],
)
def test_padded_bounds_rounds_outwards(box, bounds, clipped):
    assert padded_bounds(box, 160, 240) == (bounds, clipped)


# 카메라가 반환한 영상의 크기, 채널 수 또는 픽셀 자료형이 잘못되면 판정을 거부한다.
@pytest.mark.parametrize(
    ("shape", "dtype"),
    [
        ((0, 10, 3), np.uint8),
        ((10, 10), np.uint8),
        ((10, 10, 4), np.uint8),
        ((10, 10, 3), np.uint16),
    ],
)
def test_invalid_camera_frames_are_rejected(shape, dtype):
    with pytest.raises(ValueError):
        assess_photo(np.zeros(shape, dtype=dtype))


# 유한하지 않은 수치, 범위를 벗어난 노출 비율, 정수가 아닌 최소 크기 기준을 거부한다.
@pytest.mark.parametrize(
    "changes",
    [
        {"min_sharpness": float("nan")},
        {"max_brightness": 256},
        {"max_dark_ratio": -0.1},
        {"min_short_side": True},
    ],
)
def test_invalid_quality_limits_are_rejected(changes):
    with pytest.raises(ValueError):
        replace(DEFAULT_THRESHOLDS, **changes)
