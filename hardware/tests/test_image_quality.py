"""개인 사진이나 카메라 없이 합성 이미지로 화질 측정과 실험 기준을 검증한다."""

from dataclasses import replace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from hardware.image_quality import DEFAULT_THRESHOLDS, QualityThresholds, assess_photo


class PhotoQualityTests(unittest.TestCase):
    # 밝기와 크기가 일정한 합성 글자·윤곽 이미지를 준비한다.
    def setUp(self):
        self.frame = np.full((240, 400, 3), 160, dtype=np.uint8)
        cv2.putText(self.frame, "MEDICINE", (15, 85), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (40, 40, 40), 2)
        cv2.putText(self.frame, "10 mg", (15, 155), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (40, 40, 40), 2)
        cv2.rectangle(self.frame, (15, 180), (380, 220), (80, 80, 80), 2)

    # 동일 이미지가 흐려지면 선명도 수치가 감소하고 흐림 의심이 생기는지 확인한다.
    def test_blur_reduces_sharpness_and_adds_warning(self):
        clear = assess_photo(self.frame)
        blurred = assess_photo(cv2.GaussianBlur(self.frame, (31, 31), 5))
        self.assertGreater(clear.sharpness, blurred.sharpness * 4)
        self.assertNotIn("blur", clear.warnings)
        self.assertIn("blur", blurred.warnings)
        self.assertEqual(clear.warnings, ())

    # 전체 평균이 정상 범위여도 넓은 암부가 있으면 어두움 주의를 표시한다.
    def test_dark_fraction_detects_locally_underexposed_image(self):
        frame = np.full((200, 200, 3), 160, dtype=np.uint8)
        frame[:80] = 0
        result = assess_photo(frame)
        self.assertAlmostEqual(result.dark_ratio, 0.4)
        self.assertGreater(result.mean_brightness, DEFAULT_THRESHOLDS.min_brightness)
        self.assertIn("dark", result.warnings)
        self.assertNotIn("bright", result.warnings)

    # 전체 평균이 정상 범위여도 밝게 포화된 영역을 과노출·반사 의심으로 표시한다.
    def test_bright_fraction_detects_local_highlights(self):
        frame = np.full((200, 200, 3), 100, dtype=np.uint8)
        frame[:50] = 255
        result = assess_photo(frame)
        self.assertAlmostEqual(result.bright_ratio, 0.25)
        self.assertLess(result.mean_brightness, DEFAULT_THRESHOLDS.max_brightness)
        self.assertIn("bright", result.warnings)
        self.assertNotIn("dark", result.warnings)

    # 극단 픽셀이 없더라도 평균 밝기가 범위를 벗어나면 노출 주의를 표시한다.
    def test_mean_brightness_detects_dim_and_bright_images(self):
        for value, expected in ((40, "dark"), (225, "bright")):
            with self.subTest(value=value):
                result = assess_photo(np.full((200, 200, 3), value, dtype=np.uint8))
                self.assertEqual(result.mean_brightness, value)
                self.assertEqual(result.dark_ratio, 0)
                self.assertEqual(result.bright_ratio, 0)
                self.assertIn(expected, result.warnings)

    # 작거나 화면 끝에 걸린 영역을 별도 사유로 표시하고 원본 크기를 유지한다.
    def test_small_crop_and_clipped_padding_have_distinct_warnings(self):
        result = assess_photo(self.frame[:100, :120], padding_clipped=True)
        self.assertEqual((result.width, result.height), (120, 100))
        self.assertIn("small", result.warnings)
        self.assertIn("edge", result.warnings)
        self.assertNotIn("edge", assess_photo(self.frame).warnings)

    # 최소 크기 기준은 축소된 측정 영상이 아닌 실제 저장할 원본 영역에 적용한다.
    def test_original_dimensions_determine_size_warning(self):
        frame = np.full((200, 2000, 3), 160, dtype=np.uint8)
        result = assess_photo(frame)
        self.assertEqual((result.width, result.height), (2000, 200))
        self.assertEqual((result.analysis_width, result.analysis_height), (640, 64))
        self.assertNotIn("small", result.warnings)

    # 작은 영상은 확대하지 않고 원본과 복사된 측정 결과를 분리한다.
    def test_small_frame_is_not_upscaled_or_modified(self):
        original = self.frame.copy()
        with patch.object(cv2, "resize", wraps=cv2.resize) as resize:
            result = assess_photo(self.frame)
        resize.assert_not_called()
        self.assertEqual((result.analysis_width, result.analysis_height), (400, 240))
        np.testing.assert_array_equal(self.frame, original)

    # 큰 가로·세로 영상 모두 비율을 유지해 긴 변 640 이내에서 측정한다.
    def test_large_frames_keep_aspect_ratio_and_original_pixels(self):
        for shape, expected in (((800, 1200, 3), (640, 427)), ((1200, 800, 3), (427, 640))):
            with self.subTest(shape=shape):
                frame = np.full(shape, 160, dtype=np.uint8)
                original = frame.copy()
                result = assess_photo(frame)
                self.assertEqual((result.analysis_width, result.analysis_height), expected)
                self.assertEqual((result.width, result.height), (shape[1], shape[0]))
                np.testing.assert_array_equal(frame, original)

    # 사용자가 바꾼 실험 기준이 판정에 반영되며 계산된 수치는 바뀌지 않는다.
    def test_custom_thresholds_change_warnings_only(self):
        baseline = assess_photo(self.frame)
        strict = replace(DEFAULT_THRESHOLDS, min_sharpness=baseline.sharpness + 1, min_short_side=300)
        result = assess_photo(self.frame, thresholds=strict)
        self.assertEqual(result.sharpness, baseline.sharpness)
        self.assertIn("blur", result.warnings)
        self.assertIn("small", result.warnings)
        self.assertEqual(baseline.warnings, ())

    # 경계값과 같은 측정치는 기준 미달이나 초과로 처리하지 않는다.
    def test_threshold_boundaries_are_inclusive(self):
        frame = np.full((160, 160, 3), 50, dtype=np.uint8)
        result = assess_photo(frame, thresholds=replace(DEFAULT_THRESHOLDS, min_sharpness=0))
        self.assertEqual(result.warnings, ())

    # 잘못된 이미지 자료형과 빈 배열은 검사 결과를 만들지 않고 거부한다.
    def test_invalid_inputs_are_rejected(self):
        inputs = (
            None,
            [],
            np.empty((0, 10, 3), dtype=np.uint8),
            np.empty((10, 0, 3), dtype=np.uint8),
            np.zeros((10, 10), dtype=np.uint8),
            np.zeros((10, 10, 4), dtype=np.uint8),
            np.zeros((10, 10, 3), dtype=np.float32),
            np.zeros((10, 10, 3), dtype=np.uint16),
        )
        for frame in inputs:
            with self.subTest(shape=getattr(frame, "shape", None)), self.assertRaises(ValueError):
                assess_photo(frame)

    # 실험 기준 객체 대신 다른 값이 전달되면 명확한 오류를 반환한다.
    def test_invalid_threshold_object_is_rejected(self):
        with self.assertRaises(ValueError):
            assess_photo(self.frame, thresholds=None)

    # 비정상 수치·범위·크기 기준은 측정 전에 설정 단계에서 거부한다.
    def test_invalid_threshold_values_are_rejected(self):
        changes = (
            {"min_sharpness": -1},
            {"min_sharpness": float("nan")},
            {"max_brightness": float("inf")},
            {"min_brightness": -1},
            {"max_brightness": 256},
            {"min_brightness": 210},
            {"max_dark_ratio": -0.1},
            {"max_bright_ratio": 1.1},
            {"min_short_side": 0},
            {"min_short_side": 1.5},
            {"min_short_side": True},
            {"min_sharpness": "100"},
            {"max_dark_ratio": True},
        )
        for values in changes:
            with self.subTest(values=values), self.assertRaises(ValueError):
                QualityThresholds(**values)


if __name__ == "__main__":
    unittest.main()
