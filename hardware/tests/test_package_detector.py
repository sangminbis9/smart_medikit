"""모델 다운로드나 실제 사진 없이 탐지 초기화와 표시용 프레임 분리를 검증한다."""

from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from hardware import package_detector


class PackageDetectorTests(unittest.TestCase):
    # 실제 모델을 가짜 모델로 바꾸고 생성 경로를 테스트 임시 폴더로 제한한다.
    def setUp(self):
        temporary_directory = self.enterContext(TemporaryDirectory())
        self.model_directory = Path(temporary_directory) / "models" / "yoloe"
        self.enterContext(patch.object(package_detector, "MODEL_DIRECTORY", self.model_directory))
        self.enterContext(patch.object(package_detector, "KOREAN_FONT_PATHS", ()))
        self.enterContext(patch.dict(os.environ))
        self.enterContext(redirect_stdout(StringIO()))
        self.model = Mock()
        self.model.predict.return_value = [SimpleNamespace(boxes=[])]
        self.factory = Mock(return_value=self.model)
        fake_ultralytics = ModuleType("ultralytics")
        fake_ultralytics.YOLOE = self.factory
        self.enterContext(patch.dict("sys.modules", {"ultralytics": fake_ultralytics}))

    # 초기화 중의 캐시 위치와 탐지 문구를 확인하고 원래 작업 폴더 복원을 검증한다.
    def test_initialization_sets_prompts_and_restores_directory(self):
        original_directory = Path.cwd()

        # 모델 생성 당시의 작업 폴더와 설정 경로를 기록한다.
        def capture_model_context(model_path):
            self.assertEqual(Path.cwd(), self.model_directory)
            self.assertEqual(os.environ["YOLO_CONFIG_DIR"], str(self.model_directory / "config"))
            self.assertEqual(model_path, str(self.model_directory / "yoloe-26n-seg.pt"))
            return self.model

        self.factory.side_effect = capture_model_context
        detector = package_detector.PackageDetector()
        self.assertEqual(Path.cwd(), original_directory)
        self.model.set_classes.assert_called_once_with(
            [english for english, _ in package_detector.PACKAGE_CLASSES]
        )
        self.assertEqual(len(package_detector.PACKAGE_CLASSES), 8)
        self.assertFalse(detector.use_korean_labels)

    # 텍스트 인코더 준비가 실패해도 원래 작업 폴더가 유지되는지 확인한다.
    def test_prompt_failure_restores_directory(self):
        original_directory = Path.cwd()
        self.model.set_classes.side_effect = RuntimeError("테스트 모델 준비 실패")
        with self.assertRaisesRegex(RuntimeError, "테스트 모델 준비 실패"):
            package_detector.PackageDetector()
        self.assertEqual(Path.cwd(), original_directory)

    # 패키지가 없으면 설치 안내를 제공하고 작업 폴더를 복원한다.
    def test_missing_dependency_has_installation_message(self):
        original_directory = Path.cwd()
        with patch.dict("sys.modules", {"ultralytics": None}):
            with self.assertRaisesRegex(RuntimeError, "hardware/requirements.txt"):
                package_detector.PackageDetector()
        self.assertEqual(Path.cwd(), original_directory)

    # 잘못된 점수 기준으로 모델을 불러오지 않는다.
    def test_invalid_confidence_does_not_load_model(self):
        for confidence in (0.0, -0.1, 1.1, float("nan"), float("inf")):
            with self.subTest(confidence=confidence), self.assertRaises(ValueError):
                package_detector.PackageDetector(confidence)
        self.factory.assert_not_called()

    # 탐지가 없을 때는 영상 내용이 같아도 저장용 원본과 분리된 배열을 반환한다.
    def test_empty_results_preserve_input_and_return_separate_frame(self):
        detector = package_detector.PackageDetector(confidence=0.4)
        frame = np.arange(60, dtype=np.uint8).reshape(4, 5, 3)
        original = frame.copy()
        annotated = detector.annotate(frame)
        np.testing.assert_array_equal(frame, original)
        np.testing.assert_array_equal(annotated, original)
        self.assertFalse(np.shares_memory(annotated, frame))
        arguments = self.model.predict.call_args.kwargs
        np.testing.assert_array_equal(arguments.pop("source"), original)
        self.assertEqual(
            arguments,
            {"imgsz": 640, "conf": 0.4, "device": "cpu", "verbose": False, "save": False, "max_det": 20},
        )

    # 추론 함수가 입력 배열을 바꾸어도 저장용 프레임에는 영향이 없도록 한다.
    def test_inference_cannot_mutate_capture_frame(self):
        detector = package_detector.PackageDetector()
        frame = np.full((30, 20, 3), 17, dtype=np.uint8)

        # 추론 내부에서 배열이 바뀌는 상황을 재현한다.
        def mutate_model_input(**arguments):
            arguments["source"][:] = 255
            return []

        self.model.predict.side_effect = mutate_model_input
        annotated = detector.annotate(frame)
        self.assertTrue(np.all(frame == 17))
        np.testing.assert_array_equal(annotated, frame)

    # 각 방향 10%를 확장한 박스만 표시하고 원본 픽셀과 전체 크기는 보존한다.
    def test_portrait_boxes_are_drawn_only_on_preview(self):
        detector = package_detector.PackageDetector()
        frame = np.full((240, 160, 3), 17, dtype=np.uint8)
        original = frame.copy()
        self.model.predict.return_value = [
            SimpleNamespace(
                boxes=[
                    SimpleNamespace(
                        cls=np.array([0]),
                        conf=np.array([0.8]),
                        xyxy=np.array([[30, 90, 120, 180]]),
                    )
                ]
            )
        ]
        annotated = detector.annotate(frame)
        self.assertEqual(annotated.shape, frame.shape)
        np.testing.assert_array_equal(frame, original)
        np.testing.assert_array_equal(annotated[188, 128], [120, 220, 0])
        np.testing.assert_array_equal(annotated[189, 129], original[189, 129])
        np.testing.assert_array_equal(annotated[81, 21], [120, 220, 0])
        np.testing.assert_array_equal(annotated[180, 120], original[180, 120])
        np.testing.assert_array_equal(annotated[220, 150], original[220, 150])

    # 소수점 박스는 바깥쪽으로 반올림하고 여백이 화면 밖으로 나가면 경계에 제한한다.
    def test_padding_rounds_outwards_and_stays_in_frame(self):
        detector = package_detector.PackageDetector()
        frame = np.full((240, 160, 3), 17, dtype=np.uint8)
        cases = (
            ([30.25, 90.25, 120.25, 180.25], (21, 81, 130, 190), False),
            ([1, 1, 158, 238], (0, 0, 160, 240), True),
        )
        for coordinates, expected, padding_clipped in cases:
            with self.subTest(coordinates=coordinates):
                self.model.predict.return_value = [
                    SimpleNamespace(boxes=[SimpleNamespace(
                        cls=np.array([0]), conf=np.array([0.8]), xyxy=np.array([coordinates])
                    )])
                ]
                detections = detector.detect(frame)
                self.assertEqual(len(detections), 1)
                self.assertEqual(detections[0].bounds, expected)
                self.assertEqual(detections[0].padding_clipped, padding_clipped)
                self.assertEqual(detections[0].class_id, 0)
                self.assertAlmostEqual(detections[0].confidence, 0.8)
                annotated = detector.annotate(frame, detections)
                x1, y1, x2, y2 = expected
                np.testing.assert_array_equal(annotated[y2 - 1, x2 - 1], [120, 220, 0])
                np.testing.assert_array_equal(annotated[(y1 + y2) // 2, x1], [120, 220, 0])
                self.assertEqual(annotated.shape, frame.shape)
                self.assertTrue(np.all(frame == 17))

    # 잘못된 박스를 여백 확장으로 유효하게 보이도록 만들지 않는다.
    def test_invalid_boxes_are_not_expanded_or_drawn(self):
        detector = package_detector.PackageDetector()
        frame = np.full((240, 160, 3), 17, dtype=np.uint8)
        for coordinates in (
            [40, 80, 40, 120], [80, 80, 40, 120], [20, 80, 40, 80],
            [float("nan"), 0, 50, 50], [0, 0, float("inf"), 50],
            [-80, 10, -30, 50], [200, 10, 240, 50],
        ):
            with self.subTest(coordinates=coordinates):
                self.model.predict.return_value = [
                    SimpleNamespace(boxes=[SimpleNamespace(
                        cls=np.array([0]), conf=np.array([0.8]), xyxy=np.array([coordinates])
                    )])
                ]
                np.testing.assert_array_equal(detector.annotate(frame), frame)

    # 표시와 저장이 같은 탐지 좌표를 사용하고 원본 추론을 중복 실행하지 않는지 확인한다.
    def test_detected_crop_and_preview_share_one_inference(self):
        detector = package_detector.PackageDetector()
        frame = np.arange(240 * 160 * 3, dtype=np.uint8).reshape(240, 160, 3)
        original = frame.copy()
        self.model.predict.return_value = [SimpleNamespace(boxes=[SimpleNamespace(
            cls=np.array([4]), conf=np.array([0.82]), xyxy=np.array([[30, 90, 120, 180]])
        )])]

        detections = detector.detect(frame)
        detector.annotate(frame, detections, selected_index=0)
        cropped = detections[0].crop(frame)
        self.model.predict.assert_called_once()
        self.assertEqual(cropped.shape, (108, 108, 3))
        np.testing.assert_array_equal(cropped, original[81:189, 21:129])
        np.testing.assert_array_equal(frame, original)
        self.assertFalse(np.shares_memory(cropped, frame))

    # 전달한 빈 탐지 목록은 재추론 없이 독립된 미리보기로 반환한다.
    def test_supplied_empty_detections_do_not_infer(self):
        detector = package_detector.PackageDetector()
        frame = np.full((20, 30, 3), 17, dtype=np.uint8)
        annotated = detector.annotate(frame, [])
        self.model.predict.assert_not_called()
        np.testing.assert_array_equal(annotated, frame)
        self.assertFalse(np.shares_memory(annotated, frame))

    # 품질 패널은 모델 추론 없이 독립된 고정 크기 BGR 이미지를 생성한다.
    def test_quality_panel_is_separate_bgr_image_without_inference(self):
        detector = package_detector.PackageDetector()
        report = package_detector.PhotoQuality(800, 600, 640, 480, 220.0, 125.0, 0.02, 0.03, ())
        with patch.object(detector._image_draw.ImageDraw, "text") as draw_text:
            panel = detector.make_quality_panel(report, candidate_number=1, candidate_count=1)
        messages = [call.args[1] for call in draw_text.call_args_list]
        self.assertEqual(panel.shape, (220, 800, 3))
        self.assertEqual(panel.dtype, np.uint8)
        self.assertIn("Candidate #1 | Experimental: OK", messages)
        self.assertTrue(any("Sharpness 220.0" in message for message in messages))
        self.assertTrue(any("Dark 2.0%" in message and "Bright 3.0%" in message for message in messages))
        self.assertTrue(any("not verified" in message for message in messages))
        second_panel = detector.make_quality_panel(report, candidate_number=1)
        self.assertFalse(np.shares_memory(panel, second_panel))
        self.assertTrue(np.any(second_panel != panel))
        self.model.predict.assert_not_called()

    # 주의 항목이 있으면 정상으로 표시하지 않고 모든 사유를 패널 안에 배치한다.
    def test_quality_panel_displays_all_warnings_without_clipping(self):
        detector = package_detector.PackageDetector()
        report = package_detector.PhotoQuality(
            80, 60, 80, 60, 0.0, 120.0, 0.4, 0.3, ("blur", "dark", "bright", "small", "edge")
        )
        with patch.object(detector._image_draw.ImageDraw, "text") as draw_text:
            detector.make_quality_panel(report, candidate_number=2, candidate_count=3)
        messages = [call.args[1] for call in draw_text.call_args_list]
        self.assertIn("Candidate #2 | Experimental: CAUTION", messages)
        joined_messages = "\n".join(messages)
        for reason in ("blur", "dark", "overexposure/glare", "Small capture area", "Margin clipped"):
            self.assertIn(reason, joined_messages)
        for call in draw_text.call_args_list:
            x, y = call.args[0]
            bounds = detector.font.getbbox(call.args[1], anchor="lt")
            self.assertLessEqual(x + bounds[2], 800)
            self.assertLessEqual(y + bounds[3], 220)
        self.model.predict.assert_not_called()

    # 대상이 없거나 선택 전인 경우 품질 양호로 표시하지 않고 다음 조작을 알린다.
    def test_quality_panel_distinguishes_no_target_and_unselected_candidates(self):
        detector = package_detector.PackageDetector()
        for count in (0, 3):
            with self.subTest(count=count), patch.object(detector._image_draw.ImageDraw, "text") as draw_text:
                detector.make_quality_panel(None, candidate_count=count)
            messages = [call.args[1] for call in draw_text.call_args_list]
            expected_title = "Quality: no target" if count == 0 else "Quality: 3 candidates"
            self.assertIn(expected_title, messages)
            self.assertFalse(any("Experimental: OK" in message for message in messages))
            if count:
                self.assertTrue(any("Tab: select target" in message for message in messages))
            else:
                self.assertIn("No target: nothing will be saved.", messages)
                self.assertFalse(any("Manual save allowed" in message for message in messages))
        self.model.predict.assert_not_called()

    # 한국어 글꼴 모드에서는 판정·측정값·주의 사유를 한국어로 안내한다.
    def test_quality_panel_uses_korean_messages_when_supported(self):
        detector = package_detector.PackageDetector()
        detector.use_korean_labels = True
        report = package_detector.PhotoQuality(80, 60, 80, 60, 0.0, 20.0, 0.7, 0.0, ("blur", "dark", "edge"))
        with patch.object(detector._image_draw.ImageDraw, "text") as draw_text:
            detector.make_quality_panel(report, candidate_number=2)
        messages = [call.args[1] for call in draw_text.call_args_list]
        self.assertIn("후보 #2 | 품질: 주의 (실험 기준)", messages)
        joined_messages = "\n".join(messages)
        for reason in report.warnings:
            self.assertIn(package_detector.WARNING_MESSAGES[reason], joined_messages)
        self.assertIn("안정성·OCR 성공은 별도 검증", joined_messages)
        self.model.predict.assert_not_called()

    # 자동 촬영의 진행과 잠금 상태를 품질 결과와 별도로 표시하고 입력 상태를 바꾸지 않는다.
    def test_auto_panel_displays_progress_and_lock_without_changing_state(self):
        detector = package_detector.PackageDetector()
        report = package_detector.PhotoQuality(800, 600, 640, 480, 220.0, 125.0, 0.02, 0.03, ())
        expected = {
            "waiting": "Auto: waiting for a target",
            "multiple": "Auto: show only one package.",
            "quality": "Auto: waiting for better quality; check warnings above.",
            "holding": "Auto: quality held 1.2 / 3.5s",
            "ready": "Auto: ready to save",
            "locked": "Auto: saved; remove the package from view.",
            "absent": "Auto: rearming 0.7 / 2.2s (no target)",
        }
        for status, expected_message in expected.items():
            gate = SimpleNamespace(
                status=status, held_seconds=1.2, hold_seconds=3.5,
                absent_seconds=0.7, rearm_seconds=2.2,
            )
            before = vars(gate).copy()
            with self.subTest(status=status), patch.object(detector._image_draw.ImageDraw, "text") as draw_text:
                panel = detector.make_quality_panel(report, candidate_number=1, candidate_count=1, auto_capture=gate)
            messages = [call.args[1] for call in draw_text.call_args_list]
            self.assertEqual(panel.shape, (260, 800, 3))
            self.assertEqual(panel.dtype, np.uint8)
            self.assertIn("Candidate #1 | Experimental: OK", messages)
            self.assertIn(expected_message, messages)
            self.assertTrue(any("does not confirm medicine identity" in message for message in messages))
            self.assertEqual(vars(gate), before)
            for call in draw_text.call_args_list:
                x, y = call.args[0]
                bounds = detector.font.getbbox(call.args[1], anchor="lt")
                self.assertLessEqual(x + bounds[2], 800)
                self.assertLessEqual(y + bounds[3], 260)
        self.model.predict.assert_not_called()

    # 자동 모드의 다중 후보와 미감지 상태에서 양호로 오해하거나 자동 선택을 안내하지 않는다.
    def test_auto_panel_explains_ambiguous_and_missing_targets(self):
        detector = package_detector.PackageDetector()
        for count, status in ((0, "absent"), (3, "multiple")):
            gate = SimpleNamespace(
                status=status, held_seconds=0.0, hold_seconds=2.0,
                absent_seconds=0.5, rearm_seconds=1.5,
            )
            with self.subTest(count=count), patch.object(detector._image_draw.ImageDraw, "text") as draw_text:
                detector.make_quality_panel(None, candidate_count=count, auto_capture=gate)
            messages = [call.args[1] for call in draw_text.call_args_list]
            self.assertFalse(any("Experimental: OK" in message for message in messages))
            if count:
                self.assertIn("Automatic capture needs exactly one target.", messages)
                self.assertIn("Manual selection: S | Tab: next target | Enter: save", messages)
            else:
                self.assertIn("Quality: no target", messages)
                self.assertIn("Auto: rearming 0.5 / 1.5s (no target)", messages)
        self.model.predict.assert_not_called()

    # 한국어 자동 상태와 모든 품질 경고를 함께 표시하되 기존 수동 모드의 크기를 유지한다.
    def test_korean_auto_panel_keeps_warnings_and_manual_panel_unchanged(self):
        detector = package_detector.PackageDetector()
        detector.use_korean_labels = True
        report = package_detector.PhotoQuality(
            80, 60, 80, 60, 0.0, 120.0, 0.4, 0.3, ("blur", "dark", "bright", "small", "edge")
        )
        gate = SimpleNamespace(
            status="locked", held_seconds=0.0, hold_seconds=2.0,
            absent_seconds=0.0, rearm_seconds=1.5,
        )
        with patch.object(detector._image_draw.ImageDraw, "text") as draw_text:
            auto_panel = detector.make_quality_panel(report, candidate_number=1, auto_capture=gate)
        joined_messages = "\n".join(call.args[1] for call in draw_text.call_args_list)
        self.assertIn("자동: 저장 완료 · 포장을 화면 밖으로 치워주세요.", joined_messages)
        self.assertIn("저장 완료는 약품 식별 성공이 아님", joined_messages)
        for reason in report.warnings:
            self.assertIn(package_detector.WARNING_MESSAGES[reason], joined_messages)
        with patch.object(detector._image_draw.ImageDraw, "text") as draw_text:
            manual_panel = detector.make_quality_panel(report, candidate_number=1)
        self.assertEqual(auto_panel.shape, (260, 800, 3))
        self.assertEqual(manual_panel.shape, (220, 800, 3))
        self.assertFalse(any("자동:" in call.args[1] for call in draw_text.call_args_list))
        self.assertEqual(report.warnings, ("blur", "dark", "bright", "small", "edge"))
        self.model.predict.assert_not_called()

    # 후보 번호와 선택 강조를 표시하되 선택하지 않은 박스는 기존 색을 유지한다.
    def test_selection_label_and_outline_do_not_touch_original(self):
        detector = package_detector.PackageDetector()
        frame = np.full((240, 400, 3), 17, dtype=np.uint8)
        detections = [
            package_detector.PackageDetection(0, 0.8, (20, 90, 120, 200)),
            package_detector.PackageDetection(4, 0.7, (240, 90, 340, 200)),
        ]
        with patch.object(detector._image_draw.ImageDraw, "text") as draw_text:
            annotated = detector.annotate(frame, detections, selected_index=1)
        np.testing.assert_array_equal(annotated[199, 119], [120, 220, 0])
        np.testing.assert_array_equal(annotated[199, 339], [0, 200, 255])
        self.assertTrue(np.all(frame == 17))
        self.model.predict.assert_not_called()
        self.assertEqual(
            [call.args[1] for call in draw_text.call_args_list],
            ["#1 medicine box 0.80", "Selected #2 medicine bottle 0.70"],
        )

    # 모델 범주가 설정 목록을 벗어나면 잘못된 후보로 저장하지 않는다.
    def test_invalid_model_class_is_rejected(self):
        detector = package_detector.PackageDetector()
        frame = np.full((20, 30, 3), 17, dtype=np.uint8)
        self.model.predict.return_value = [SimpleNamespace(boxes=[SimpleNamespace(
            cls=np.array([len(package_detector.PACKAGE_CLASSES)]),
            conf=np.array([0.8]), xyxy=np.array([[1, 1, 10, 10]]),
        )])]
        with self.assertRaisesRegex(RuntimeError, "탐지 범위"):
            detector.detect(frame)

    # 빈 영상이나 색상 채널이 없는 영상은 모델 실행 전에 거부한다.
    def test_invalid_frames_are_rejected_before_inference(self):
        detector = package_detector.PackageDetector()
        for frame in (None, np.empty((0, 4, 3)), np.zeros((4, 4)), np.zeros((4, 4, 4))):
            with self.subTest(frame_type=type(frame)), self.assertRaises(ValueError):
                detector.detect(frame)
        self.model.predict.assert_not_called()


class PackageDetectionTests(unittest.TestCase):
    # 원본 해상도의 모든 행과 열을 포함하고 저장용 배열 변경이 원본에 전파되지 않는다.
    def test_full_frame_crop_keeps_bottom_right_pixel_and_is_independent(self):
        frame = np.arange(60, dtype=np.uint8).reshape(4, 5, 3)
        detection = package_detector.PackageDetection(0, 0.8, (0, 0, 5, 4), True)
        cropped = detection.crop(frame)
        self.assertEqual(cropped.shape, frame.shape)
        np.testing.assert_array_equal(cropped, frame)
        np.testing.assert_array_equal(cropped[-1, -1], frame[-1, -1])
        self.assertFalse(np.shares_memory(cropped, frame))
        cropped[:] = 255
        self.assertFalse(np.any(frame == 255))

    # 잘못된 좌표를 조용히 보정하거나 다른 영역을 저장하지 않도록 거부한다.
    def test_empty_outside_and_non_integer_bounds_are_rejected(self):
        frame = np.zeros((4, 5, 3), dtype=np.uint8)
        for bounds in (
            (1, 1, 1, 2), (2, 1, 1, 2), (0, 2, 3, 2), (-1, 0, 3, 3),
            (0, -1, 3, 3), (0, 0, 6, 4), (0, 0, 5, 5),
            (0.5, 0, 3, 3), (True, 0, 3, 3), (0, 0, 3),
        ):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                package_detector.PackageDetection(0, 0.8, bounds).crop(frame)

    # BGR 저장 이미지가 될 수 없는 입력을 자르기 전에 거부한다.
    def test_invalid_frame_is_rejected(self):
        detection = package_detector.PackageDetection(0, 0.8, (0, 0, 3, 3))
        for frame in (None, np.empty((0, 4, 3)), np.zeros((4, 4)), np.zeros((4, 4, 4))):
            with self.subTest(frame_type=type(frame)), self.assertRaises(ValueError):
                detection.crop(frame)


if __name__ == "__main__":
    unittest.main()
