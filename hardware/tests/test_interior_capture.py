"""카메라 없이 합성 영상으로 내부 전체 촬영의 안정화 조건을 검증한다."""

import unittest

import numpy as np

from hardware.interior_capture import InteriorCaptureGate


class InteriorCaptureTests(unittest.TestCase):
    # 무늬가 없는 빈 보관함을 대신하는 합성 영상을 준비한다.
    def setUp(self):
        self.frame = np.full((200, 300, 3), 120, dtype=np.uint8)
        self.gate = InteriorCaptureGate()

    # 빈 내부는 객체나 무늬가 없어도 노출과 시간 조건으로 촬영할 수 있다.
    def test_empty_scene_becomes_ready_after_stable_hold(self):
        self.assertFalse(self.gate.update(self.frame, 0))
        self.assertFalse(self.gate.update(self.frame, 0.5))
        self.assertTrue(self.gate.update(self.frame, 1))
        self.assertEqual(self.gate.status, "ready")
        self.assertEqual(self.gate.report.sharpness, 0)
        self.assertEqual(self.gate.report.warnings, ())
        self.assertEqual(self.gate.motion_score, 0)

    # 시간 조건만 만족하는 두 관측으로 촬영하지 않는다.
    def test_requires_at_least_three_observations(self):
        self.assertFalse(self.gate.update(self.frame, 0))
        self.assertFalse(self.gate.update(self.frame, 1))
        self.assertTrue(self.gate.update(self.frame, 1.1))

    # 밝기 변화가 크면 이전 안정화 시간을 버리고 새 영상에서 시간을 센다.
    def test_large_change_restarts_hold(self):
        changed = self.frame + 20
        self.gate.update(self.frame, 0)
        self.gate.update(self.frame, 0.5)
        self.assertFalse(self.gate.update(changed, 1))
        self.assertEqual(self.gate.status, "moving")
        self.assertEqual(self.gate.motion_score, 20)
        self.assertEqual(self.gate.held_seconds, 0)
        self.assertFalse(self.gate.update(changed, 1.5))
        self.assertTrue(self.gate.update(changed, 2))

    # 조금씩 계속 바뀌는 영상은 최초 안정 영상과도 비교한다.
    def test_accumulated_drift_is_compared_to_anchor(self):
        self.gate.update(self.frame, 0)
        self.gate.update(self.frame + 2, 0.5)
        self.assertFalse(self.gate.update(self.frame + 4, 1))
        self.assertEqual(self.gate.motion_score, 4)
        self.assertEqual(self.gate.status, "moving")

    # 최초 영상과 비슷해도 직전 영상에서 크게 움직였으면 다시 안정화한다.
    def test_change_is_also_compared_to_previous_frame(self):
        self.gate.update(self.frame, 0)
        self.gate.update(self.frame + 3, 0.5)
        self.assertFalse(self.gate.update(self.frame - 3, 1))
        self.assertEqual(self.gate.motion_score, 6)
        self.assertEqual(self.gate.status, "moving")

    # 움직임 기준값과 같은 변화는 허용한다.
    def test_motion_threshold_is_inclusive(self):
        self.gate.update(self.frame, 0)
        self.gate.update(self.frame + 3, 0.5)
        self.assertTrue(self.gate.update(self.frame + 3, 1))

    # 노출이 나쁜 프레임은 차단하고 이후 정상 영상부터 시간을 다시 센다.
    def test_bad_exposure_restarts_observation(self):
        for brightness in (0, 255):
            with self.subTest(brightness=brightness):
                self.gate = InteriorCaptureGate()
                self.gate.update(self.frame, 0)
                self.gate.update(self.frame, 0.5)
                bad = np.full_like(self.frame, brightness)
                self.assertFalse(self.gate.update(bad, 1))
                self.assertEqual(self.gate.status, "quality")
                self.assertEqual(self.gate.held_seconds, 0)
                self.assertIsNone(self.gate.motion_score)
                self.assertFalse(self.gate.update(self.frame, 1.5))
                self.assertFalse(self.gate.update(self.frame, 2))
                self.assertTrue(self.gate.update(self.frame, 2.5))

    # 선명도 문턱을 선택하면 무늬 없는 영상의 자동 촬영도 차단한다.
    def test_configured_sharpness_threshold_blocks_blur(self):
        gate = InteriorCaptureGate(min_sharpness=500)
        for now in (0, 0.5, 1):
            self.assertFalse(gate.update(self.frame, now))
        self.assertEqual(gate.status, "quality")
        self.assertIn("blur", gate.report.warnings)

    # 작은 전체 입력은 경고를 남기되 크기만으로 촬영하지 못하게 하지는 않는다.
    def test_small_scene_warning_does_not_block(self):
        small = self.frame[:100, :100]
        self.gate.update(small, 0)
        self.gate.update(small, 0.5)
        self.assertTrue(self.gate.update(small, 1))
        self.assertEqual(self.gate.report.warnings, ("small",))

    # 오래 끊기거나 중복·역행한 시각은 이전 유지 시간으로 계산하지 않는다.
    def test_timestamp_discontinuity_restarts_hold(self):
        for timestamp in (0.5, 0.1, 3):
            with self.subTest(timestamp=timestamp):
                self.gate = InteriorCaptureGate()
                self.gate.update(self.frame, 0)
                self.gate.update(self.frame, 0.5)
                self.assertFalse(self.gate.update(self.frame, timestamp))
                self.assertEqual(self.gate.held_seconds, 0)
                self.assertFalse(self.gate.update(self.frame, timestamp + 0.5))
                self.assertTrue(self.gate.update(self.frame, timestamp + 1))

    # 축소 결과가 비슷해도 원본 해상도가 바뀌면 안정 구간을 다시 시작한다.
    def test_resolution_change_restarts_hold(self):
        self.gate.update(self.frame, 0)
        self.gate.update(self.frame, 0.5)
        larger = np.full((400, 600, 3), 120, dtype=np.uint8)
        self.assertFalse(self.gate.update(larger, 1))
        self.assertEqual(self.gate.held_seconds, 0)
        self.assertFalse(self.gate.update(larger, 1.5))
        self.assertTrue(self.gate.update(larger, 2))
        self.assertEqual((self.gate.report.width, self.gate.report.height), (600, 400))

    # 화질·움직임 측정은 원본 영상의 픽셀을 변경하지 않는다.
    def test_input_is_preserved(self):
        original = self.frame.copy()
        for now in (0, 0.5, 1):
            self.gate.update(self.frame, now)
        np.testing.assert_array_equal(self.frame, original)

    # 재시작은 이전 화질과 움직임 수치까지 버린다.
    def test_explicit_reset_clears_pending_result(self):
        for now in (0, 0.5, 1):
            self.gate.update(self.frame, now)
        self.gate.reset_pending()
        self.assertEqual(self.gate.status, "waiting")
        self.assertEqual(self.gate.held_seconds, 0)
        self.assertIsNone(self.gate.report)
        self.assertIsNone(self.gate.motion_score)
        self.assertFalse(self.gate.update(self.frame, 3))

    # 잘못된 입력 뒤에는 이전 양호 결과를 재사용하지 않는다.
    def test_invalid_frames_reset_and_raise(self):
        invalid_frames = (
            None,
            np.zeros((0, 3, 3), dtype=np.uint8),
            np.zeros((20, 20), dtype=np.uint8),
            np.zeros((20, 20, 4), dtype=np.uint8),
            np.zeros((20, 20, 3), dtype=np.float32),
        )
        for frame in invalid_frames:
            with self.subTest(shape=getattr(frame, "shape", None)):
                self.gate.update(self.frame, 0)
                with self.assertRaises(ValueError):
                    self.gate.update(frame, 1)
                self.assertEqual(self.gate.status, "waiting")
                self.assertIsNone(self.gate.report)

    # 유한한 시각만 허용하고 오류가 나면 시간 누적을 버린다.
    def test_invalid_timestamps_reset_and_raise(self):
        for timestamp in (True, "1", None, float("nan"), float("inf")):
            with self.subTest(timestamp=timestamp):
                self.gate.update(self.frame, 0)
                with self.assertRaises(ValueError):
                    self.gate.update(self.frame, timestamp)
                self.assertEqual(self.gate.status, "waiting")

    # 비정상 시간·움직임·선명도 설정을 객체 생성 시 거부한다.
    def test_invalid_configuration_is_rejected(self):
        for name, values in (
            ("hold_seconds", (0, -1, True, "1", float("nan"), float("inf"))),
            ("max_frame_gap", (0, -1, True, "1", float("nan"), float("inf"))),
            ("max_motion", (-1, 256, True, "1", float("nan"), float("inf"))),
            ("min_sharpness", (-1, True, "1", float("nan"), float("inf"))),
        ):
            for value in values:
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError):
                        InteriorCaptureGate(**{name: value})


if __name__ == "__main__":
    unittest.main()
