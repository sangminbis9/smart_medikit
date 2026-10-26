"""합성 영상으로 신규 후보·흔들림·선명도·연속 검출을 검증한다."""

import unittest

import cv2
import numpy as np
from medikit_pi.detection import Detection
from medikit_pi.vision import (
    CandidateTracker,
    change_fraction,
    crop_box,
    gray_frame,
    iou,
    motion_score,
    select_candidate,
    sharpness,
)


class VisionTests(unittest.TestCase):
    # 비교 영상의 화면 비율과 주변부 시야가 보존되는지 확인한다.
    def test_grayscale_preserves_full_frame(self):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        frame[:, 600:] = 255
        gray = gray_frame(frame, width=320)
        self.assertEqual(gray.shape, (240, 320))
        self.assertEqual(int(gray[20, -1]), 255)

    # 정적 배경의 기존 포장은 신규 후보로 선택하지 않는지 확인한다.
    def test_static_package_rejected_and_edge_package_detected(self):
        background = np.zeros((120, 160), dtype=np.uint8)
        background[20:60, 50:100] = 200
        current = background.copy()
        current[70:110, 2:32] = 180
        old = Detection((50 / 160, 20 / 120, 100 / 160, 60 / 120), 0.99, "box")
        new = Detection((2 / 160, 70 / 120, 32 / 160, 110 / 120), 0.7, "blister pack")
        self.assertEqual(select_candidate([old, new], background, current), new)
        self.assertEqual(change_fraction(background, current, old.box), 0)

    # 배경 잡음과 너무 작거나 화면을 덮는 후보를 제외하는지 확인한다.
    def test_noise_and_implausible_area_rejected(self):
        background = np.zeros((100, 100), dtype=np.uint8)
        noise = np.full((100, 100), 10, dtype=np.uint8)
        candidate = Detection((0.1, 0.1, 0.5, 0.5), 0.9, "box")
        self.assertIsNone(select_candidate([candidate], background, noise))
        bright = noise * 20
        whole = Detection((0, 0, 1, 1), 0.99, "box")
        tiny = Detection((0.5, 0.5, 0.51, 0.51), 0.99, "box")
        self.assertIsNone(select_candidate([whole, tiny], background, bright))

    # 영상 차이와 흐림이 서로 독립된 품질 지표인지 확인한다.
    def test_motion_and_sharpness(self):
        checker = ((np.indices((120, 160)).sum(axis=0) // 4) % 2 * 255).astype(np.uint8)
        blurry = cv2.GaussianBlur(checker, (15, 15), 5)
        self.assertEqual(motion_score(checker, checker), 0)
        self.assertGreater(motion_score(checker, 255 - checker), 0.9)
        self.assertGreater(sharpness(checker), sharpness(blurry) * 10)
        self.assertEqual(sharpness(checker, (0.2, 0.2, 0.1, 0.1)), 0)
        self.assertEqual(motion_score(None, checker), 1)

    # 유효하지 않은 후보 좌표를 안전하게 제외하는지 확인한다.
    def test_invalid_coordinates(self):
        frame = np.zeros((12, 12), dtype=np.uint8)
        self.assertEqual(crop_box(frame, (float("nan"), 0, 1, 1)).size, 0)
        candidate = Detection((float("nan"), 0, 1, 1), 0.9, "box")
        self.assertIsNone(select_candidate([candidate], frame, frame))
        self.assertEqual(iou((0, 0, 0.5, 0.5), (0.5, 0.5, 1, 1)), 0)

    # 같은 검출 결과를 여러 프레임에서 읽어도 안정화 시간이 누적되지 않는지 확인한다.
    def test_stability_requires_fresh_detections(self):
        tracker = CandidateTracker(stable_seconds=1, max_gap_seconds=0.6)
        candidate = Detection((0.1, 0.1, 0.5, 0.5), 0.8, "box")
        self.assertFalse(tracker.update(candidate, 0, True))
        self.assertFalse(tracker.update(candidate, 0, True))
        self.assertFalse(tracker.update(candidate, 0.5, True))
        self.assertTrue(tracker.update(candidate, 1, True))

    # 흔들림·검출 공백·다른 후보로 바뀌면 안정화 시간을 다시 시작하는지 확인한다.
    def test_motion_missing_and_replacement_reset_stability(self):
        tracker = CandidateTracker(stable_seconds=1, max_gap_seconds=0.6)
        candidate = Detection((0.1, 0.1, 0.4, 0.4), 0.8, "box")
        replacement = Detection((0.6, 0.6, 0.9, 0.9), 0.8, "box")
        tracker.update(candidate, 0, True)
        tracker.update(candidate, 0.5, False)
        self.assertFalse(tracker.update(candidate, 1, True))
        self.assertFalse(tracker.update(candidate, 2, True))
        tracker.update(candidate, 2.5, True)
        self.assertFalse(tracker.update(replacement, 3, True))
        tracker.update(None, 3.5, True)
        self.assertFalse(tracker.update(replacement, 4, True))


if __name__ == "__main__":
    unittest.main()
