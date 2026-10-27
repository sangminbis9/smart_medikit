"""실제 시간·카메라 없이 관측 순서를 재생해 자동 촬영과 중복 방지를 검증한다."""

from dataclasses import replace
import unittest

from hardware.auto_capture import AutoCaptureGate
from hardware.image_quality import PhotoQuality
from hardware.package_detector import PackageDetection


class AutoCaptureTests(unittest.TestCase):
    # 같은 포장과 양호한 검사 결과를 갖는 기본 관측을 만든다.
    def setUp(self):
        self.gate = AutoCaptureGate()
        self.target = PackageDetection(0, 0.9, (100, 100, 300, 300))
        self.good = PhotoQuality(200, 200, 200, 200, 250, 140, 0.02, 0.01, ())

    # 기본 포장의 관측 결과를 지정 시각에 전달한다.
    def observe(self, now, *, target=None, quality=None):
        return self.gate.update([target or self.target], [quality or self.good], now)

    # 시간 기준에 도달하기 전에는 대기하고 충족한 최신 프레임만 저장 가능하게 한다.
    def test_good_target_becomes_ready_after_hold(self):
        for now in (0, 0.8, 1.9):
            self.assertFalse(self.observe(now))
            self.assertEqual(self.gate.status, "holding")
        self.assertTrue(self.observe(2.0))
        self.assertEqual(self.gate.status, "ready")
        self.assertEqual(self.gate.held_seconds, 2.0)

    # 두 장 사이의 시간만으로 양호가 유지됐다고 판단하지 않는다.
    def test_time_alone_without_three_observations_is_insufficient(self):
        self.assertFalse(self.observe(0))
        self.assertFalse(self.observe(2))
        self.assertTrue(self.observe(2.1))

    # 품질 주의가 한 번이라도 나오면 회복한 첫 프레임부터 시간을 다시 센다.
    def test_quality_warning_restarts_hold(self):
        self.observe(0)
        self.observe(1)
        self.assertFalse(self.observe(1.5, quality=replace(self.good, warnings=("blur",))))
        self.assertEqual(self.gate.status, "quality")
        self.assertEqual(self.gate.held_seconds, 0)
        self.assertFalse(self.observe(2))
        self.assertFalse(self.observe(3))
        self.assertTrue(self.observe(4))

    # 후보 소실이나 복수 후보가 나타난 구간을 양호 유지 시간에서 제외한다.
    def test_missing_and_multiple_candidates_restart_hold(self):
        for targets, qualities, status in (([], [], "waiting"), ([self.target] * 2, [self.good] * 2, "multiple")):
            with self.subTest(status=status):
                self.gate = AutoCaptureGate()
                self.observe(0)
                self.observe(1)
                self.assertFalse(self.gate.update(targets, qualities, 1.5))
                self.assertEqual(self.gate.status, status)
                self.assertFalse(self.observe(2))
                self.assertFalse(self.observe(3))
                self.assertTrue(self.observe(4))

    # 다른 위치의 대상으로 바뀌면 기존 대상의 유지 시간을 이어 쓰지 않는다.
    def test_target_position_change_restarts_hold(self):
        other = replace(self.target, bounds=(400, 100, 600, 300))
        self.observe(0)
        self.observe(1)
        self.assertFalse(self.observe(1.5, target=other))
        self.assertEqual(self.gate.held_seconds, 0)
        self.assertFalse(self.observe(2.5, target=other))
        self.assertTrue(self.observe(3.5, target=other))

    # 같은 위치의 후보는 범주명이 흔들려도 기존 관측을 유지한다.
    def test_label_change_does_not_reset_same_position(self):
        self.observe(0)
        self.assertFalse(self.observe(1, target=replace(self.target, class_id=4)))
        self.assertTrue(self.observe(2, target=replace(self.target, class_id=7)))

    # 인접 프레임끼리 겹쳐도 첫 위치에서 계속 멀어지면 양호 시간을 다시 센다.
    def test_cumulative_drift_is_compared_with_anchor(self):
        self.observe(0)
        self.observe(0.5, target=replace(self.target, bounds=(120, 100, 320, 300)))
        self.observe(1, target=replace(self.target, bounds=(140, 100, 340, 300)))
        self.assertFalse(self.observe(1.5, target=replace(self.target, bounds=(160, 100, 360, 300))))
        self.assertEqual(self.gate.held_seconds, 0)

    # 첫 위치와 겹치더라도 직전 위치에서 급격히 바뀐 후보는 새로 관측한다.
    def test_jump_across_anchor_is_compared_with_previous(self):
        self.observe(0)
        self.observe(1, target=replace(self.target, bounds=(60, 100, 260, 300)))
        self.assertFalse(self.observe(2, target=replace(self.target, bounds=(140, 100, 340, 300))))
        self.assertEqual(self.gate.held_seconds, 0)

    # 저장 후에는 라벨·위치·품질과 후보 수 변화가 재촬영을 허용하지 않는다.
    def test_saved_target_stays_locked_while_any_candidate_remains(self):
        self.gate.mark_saved()
        for now in (0, 1, 2, 3):
            self.assertFalse(self.observe(now))
        moved = replace(self.target, class_id=5, bounds=(400, 200, 500, 250))
        self.assertFalse(self.observe(4, target=moved, quality=replace(self.good, warnings=("small",))))
        self.assertFalse(self.gate.update([self.target, moved], [self.good, self.good], 5))
        self.assertEqual(self.gate.status, "locked")

    # 잠깐의 감지 누락은 촬영 잠금을 해제하지 않으며 복귀 시 비감지 시간을 비운다.
    def test_short_detection_flicker_does_not_rearm(self):
        self.gate.mark_saved()
        self.gate.update([], [], 0)
        self.gate.update([], [], 0.5)
        self.gate.update([], [], 1.0)
        self.assertEqual(self.gate.status, "absent")
        self.assertFalse(self.observe(1.1))
        self.assertEqual(self.gate.status, "locked")
        self.assertEqual(self.gate.absent_seconds, 0)
        self.assertFalse(self.observe(2.1))
        self.assertFalse(self.observe(3.1))

    # 충분한 비감지 후 재제시한 대상은 새 유지 시간을 거쳐 한 번 더 저장할 수 있다.
    def test_absence_rearms_then_requires_fresh_good_hold(self):
        self.gate.mark_saved()
        for now in (0, 0.75, 1.5):
            self.assertFalse(self.gate.update([], [], now))
        self.assertEqual(self.gate.status, "waiting")
        self.assertFalse(self.observe(2))
        self.assertFalse(self.observe(3))
        self.assertTrue(self.observe(4))

    # 비감지 시간도 최소 관측 수를 충족해야 잠금을 해제한다.
    def test_absence_time_alone_does_not_rearm(self):
        self.gate.mark_saved()
        self.gate.update([], [], 0)
        self.gate.update([], [], 1.5)
        self.assertEqual(self.gate.status, "absent")
        self.assertFalse(self.observe(1.6))
        self.assertEqual(self.gate.status, "locked")

    # 장시간 처리 정지나 역전·중복 시각은 양호 유지 시간을 새로 시작한다.
    def test_discontinuous_time_restarts_good_hold(self):
        for next_time in (20, 0.5, 1.0):
            with self.subTest(next_time=next_time):
                self.gate = AutoCaptureGate()
                self.observe(0)
                self.observe(1)
                self.assertFalse(self.observe(next_time))
                self.assertEqual(self.gate.held_seconds, 0)
                self.assertFalse(self.observe(next_time + 1))
                self.assertTrue(self.observe(next_time + 2))

    # 관측 중단 시간을 비감지 증거로 간주해 저장 잠금을 풀지 않는다.
    def test_discontinuous_absence_preserves_lock(self):
        for next_time in (20, 0.25, 0.5):
            with self.subTest(next_time=next_time):
                self.gate = AutoCaptureGate()
                self.gate.mark_saved()
                self.gate.update([], [], 0)
                self.gate.update([], [], 0.5)
                self.gate.update([], [], next_time)
                self.assertEqual(self.gate.status, "absent")
                self.assertEqual(self.gate.absent_seconds, 0)
                self.assertFalse(self.observe(next_time + 0.1))
                self.assertEqual(self.gate.status, "locked")

    # 회전이나 수동 선택 후 초기화해도 성공 저장 여부는 유지한다.
    def test_reset_pending_preserves_saved_lock(self):
        self.gate.mark_saved()
        self.gate.update([], [], 0)
        self.gate.update([], [], 1)
        self.gate.reset_pending()
        self.assertEqual(self.gate.status, "locked")
        self.assertEqual(self.gate.absent_seconds, 0)
        self.assertFalse(self.observe(20))
        self.assertFalse(self.observe(21))
        self.assertFalse(self.observe(22))

    # 저장 실패로 성공 표시 없이 초기화하면 새 양호 구간에서 재시도할 수 있다.
    def test_failed_save_can_retry_without_success_lock(self):
        self.observe(0)
        self.observe(1)
        self.assertTrue(self.observe(2))
        self.gate.reset_pending()
        self.assertFalse(self.observe(3))
        self.assertFalse(self.observe(4))
        self.assertTrue(self.observe(5))

    # 잘못된 설정이 시간·겹침 검사나 최소 관측 조건을 무력화하지 못하게 한다.
    def test_invalid_configuration_is_rejected(self):
        for name in ("hold_seconds", "rearm_seconds", "max_frame_gap", "min_iou"):
            for value in (0, -1, float("nan"), float("inf"), True, "2"):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    AutoCaptureGate(**{name: value})
        for value in (0, 2, 3.5, True, "3"):
            with self.subTest(min_observations=value), self.assertRaises(ValueError):
                AutoCaptureGate(min_observations=value)
        with self.assertRaises(ValueError):
            AutoCaptureGate(min_iou=1.1)

    # 실험 설정을 바꾸어도 시간과 관측 수 두 조건을 모두 적용한다.
    def test_custom_timing_and_observation_count(self):
        self.gate = AutoCaptureGate(hold_seconds=1, rearm_seconds=0.5, min_observations=4)
        for now in (0, 0.5, 1.0):
            self.assertFalse(self.observe(now))
        self.assertTrue(self.observe(1.1))
        self.gate.mark_saved()
        for now in (2, 2.2, 2.5):
            self.gate.update([], [], now)
        self.assertEqual(self.gate.status, "absent")
        self.gate.update([], [], 2.6)
        self.assertEqual(self.gate.status, "waiting")

    # 잘못된 시각이나 후보·결과 불일치는 기존 시간 누적을 버리고 명시적으로 실패한다.
    def test_invalid_observation_clears_pending(self):
        for now in (float("nan"), float("inf"), True, "2"):
            with self.subTest(now=now):
                self.observe(0)
                self.observe(1)
                with self.assertRaises(ValueError):
                    self.observe(now)
                self.assertEqual(self.gate.held_seconds, 0)
        with self.assertRaises(ValueError):
            self.gate.update([self.target], [], 2)
        self.assertEqual(self.gate.status, "waiting")


if __name__ == "__main__":
    unittest.main()
