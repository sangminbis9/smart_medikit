"""장치를 열지 않고 개폐 모의 입력 옵션과 실행 경로를 검증한다."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from hardware import camera_preview


class LidCliTests(unittest.TestCase):
    # 안내 출력을 감추고 카메라와 모델의 실제 준비를 차단한다.
    def setUp(self):
        self.enterContext(redirect_stdout(StringIO()))
        self.enterContext(redirect_stderr(StringIO()))
        self.loader = self.enterContext(patch.object(camera_preview, "load_detector"))
        self.preview = self.enterContext(patch.object(camera_preview, "run_preview", return_value=0))
        self.capture = self.enterContext(patch.object(camera_preview.cv2, "VideoCapture"))
        self.simulation = ModuleType("hardware.lid_capture")
        self.simulation.run_lid_simulation = Mock(return_value=0)
        self.enterContext(patch.dict("sys.modules", {"hardware.lid_capture": self.simulation}))

    # 일반 미리보기의 기본 옵션에는 탐지와 모의 센서를 켜지 않는다.
    def test_simulation_is_opt_in(self):
        args = camera_preview.parse_args([])
        self.assertFalse(args.simulate_lid)
        self.assertFalse(args.detect)
        self.assertFalse(args.auto_capture)

    # 모의 센서 하나만 지정해도 기존 포장 자동 촬영에 필요한 옵션을 켠다.
    def test_simulation_enables_detection_and_auto_capture(self):
        args = camera_preview.parse_args(["--simulate-lid", "--rotate", "90"])
        self.assertTrue(args.detect)
        self.assertTrue(args.auto_capture)
        self.assertEqual(args.min_sharpness, 500)
        self.assertEqual(args.hold_seconds, 2)
        self.assertEqual(args.interior_min_sharpness, 0)
        self.assertEqual(args.interior_hold_seconds, 1)
        self.assertEqual(args.interior_timeout, 15)

    # 조정한 내부 조건과 포장 기준을 같은 탐지기와 함께 모의 실행기로 전달한다.
    def test_simulation_dispatches_custom_options(self):
        self.simulation.run_lid_simulation.return_value = 1
        result = camera_preview.main([
            "--simulate-lid", "--camera", "1", "--backend", "msmf", "--rotate", "90",
            "--min-sharpness", "700", "--hold-seconds", "3",
            "--interior-min-sharpness", "100", "--interior-hold-seconds", "2",
            "--interior-timeout", "20",
        ])
        self.assertEqual(result, 1)
        self.loader.assert_called_once_with(0.25)
        self.simulation.run_lid_simulation.assert_called_once()
        args, detector = self.simulation.run_lid_simulation.call_args.args
        self.assertIs(detector, self.loader.return_value)
        self.assertEqual((args.camera, args.backend, args.rotate), (1, "msmf", 90))
        self.assertEqual((args.min_sharpness, args.hold_seconds), (700, 3))
        self.assertEqual(
            (args.interior_min_sharpness, args.interior_hold_seconds, args.interior_timeout),
            (100, 2, 20),
        )
        self.preview.assert_not_called()
        self.capture.assert_not_called()

    # 잘못된 내부 시간과 선명도는 모델을 준비하거나 장치를 열기 전에 거부한다.
    def test_invalid_interior_options_fail_before_loading(self):
        cases = [
            (option, value)
            for option in ("--interior-hold-seconds", "--interior-timeout")
            for value in ("0", "-1", "nan", "inf", "-inf")
        ] + [("--interior-min-sharpness", value) for value in ("-1", "nan", "inf", "-inf")]
        for option, value in cases:
            with self.subTest(option=option, value=value), self.assertRaises(SystemExit) as error:
                camera_preview.main(["--simulate-lid", f"{option}={value}"])
            self.assertEqual(error.exception.code, 2)
        self.loader.assert_not_called()
        self.simulation.run_lid_simulation.assert_not_called()
        self.capture.assert_not_called()

    # 안정화 시간보다 짧거나 같은 제한 시간은 촬영할 수 없으므로 거부한다.
    def test_timeout_must_exceed_hold_duration(self):
        for timeout in ("1", "0.5"):
            with self.subTest(timeout=timeout), self.assertRaises(SystemExit):
                camera_preview.parse_args(["--simulate-lid", "--interior-timeout", timeout])
        args = camera_preview.parse_args(["--simulate-lid", "--interior-timeout", "1.1"])
        self.assertEqual(args.interior_timeout, 1.1)

    # 기존 자동 촬영은 새 실행기로 넘어가지 않고 같은 기준으로 미리보기를 연다.
    def test_existing_auto_mode_uses_existing_runner(self):
        self.assertEqual(camera_preview.main(["--auto-capture", "--rotate", "90"]), 0)
        self.preview.assert_called_once()
        self.assertEqual(self.preview.call_args.args[2], 90)
        self.assertEqual(self.preview.call_args.args[-1].min_sharpness, 500)
        self.simulation.run_lid_simulation.assert_not_called()

    # 탐지 모델 준비가 실패하면 모의 세션이나 카메라를 시작하지 않는다.
    def test_model_failure_stops_before_simulation(self):
        self.loader.side_effect = RuntimeError("모의 모델 준비 실패")
        self.assertEqual(camera_preview.main(["--simulate-lid", "--rotate", "90"]), 1)
        self.simulation.run_lid_simulation.assert_not_called()
        self.preview.assert_not_called()
        self.capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
