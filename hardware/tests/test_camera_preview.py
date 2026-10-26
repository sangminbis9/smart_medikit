"""실제 카메라와 화면을 사용하지 않고 미리보기의 저장·오류·종료를 검증한다."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from hardware import camera_preview
from hardware.package_detector import PackageDetection


class PreviewTests(unittest.TestCase):
    # 카메라와 창을 모의 객체로 바꾸어 실제 장치를 열지 않는다.
    def setUp(self):
        self.output = self.enterContext(redirect_stdout(StringIO()))
        self.errors = self.enterContext(redirect_stderr(StringIO()))
        self.capture_factory = self.enterContext(patch.object(cv2, "VideoCapture"))
        self.capture = self.capture_factory.return_value
        self.capture.open.return_value = True
        self.frame = np.full((12, 16, 3), 100, dtype=np.uint8)
        self.capture.read.return_value = (True, self.frame)
        self.enterContext(patch.object(cv2, "namedWindow"))
        self.show = self.enterContext(patch.object(cv2, "imshow"))
        self.keys = self.enterContext(patch.object(cv2, "waitKey", return_value=ord("q")))
        self.visibility = self.enterContext(
            patch.object(cv2, "getWindowProperty", return_value=1)
        )
        self.destroy_window = self.enterContext(patch.object(cv2, "destroyWindow"))
        self.save = self.enterContext(patch.object(camera_preview, "save_frame"))

    # 탐지와 품질 패널을 각각 검증할 수 있도록 표시 패널은 기본적으로 생략한다.
    def make_detector(self):
        detector = Mock()
        detector.make_quality_panel.return_value = None
        return detector

    # 선택한 번호와 입력 방식을 사용하고 종료 시 장치를 해제한다.
    def test_selected_camera_and_backend_are_used(self):
        self.assertEqual(camera_preview.main(["--camera", "2", "--backend", "msmf"]), 0)
        self.capture.open.assert_called_once_with(2, cv2.CAP_MSMF)
        self.capture.release.assert_called_once()
        self.save.assert_not_called()
        self.destroy_window.assert_called_once()

    # 잘못된 카메라 번호는 장치를 열기 전에 거부한다.
    def test_negative_camera_is_rejected_before_open(self):
        with self.assertRaises(SystemExit) as error:
            camera_preview.main(["--camera", "-1"])
        self.assertEqual(error.exception.code, 2)
        self.capture_factory.assert_not_called()

    # 지원하지 않는 회전 각도는 카메라를 열기 전에 거부한다.
    def test_invalid_rotation_is_rejected_before_open(self):
        with self.assertRaises(SystemExit) as error:
            camera_preview.main(["--rotate", "45"])
        self.assertEqual(error.exception.code, 2)
        self.capture_factory.assert_not_called()

    # 탐지 점수가 범위를 벗어나거나 유한하지 않으면 장치를 열지 않는다.
    def test_invalid_confidence_is_rejected_before_open(self):
        for confidence in ("0", "-0.1", "1.1", "nan", "inf"):
            with self.subTest(confidence=confidence), self.assertRaises(SystemExit) as error:
                camera_preview.main(["--conf", confidence])
            self.assertEqual(error.exception.code, 2)
        self.capture_factory.assert_not_called()

    # 기본 미리보기에서는 탐지 패키지나 모델을 준비하지 않는다.
    def test_plain_preview_does_not_load_detector(self):
        with patch.object(camera_preview, "load_detector") as loader:
            self.assertEqual(camera_preview.main([]), 0)
        loader.assert_not_called()

    # 모델 준비 실패 시 카메라를 열지 않고 이해할 수 있는 오류를 알린다.
    def test_detector_setup_failure_does_not_open_camera(self):
        with patch.object(camera_preview, "load_detector", side_effect=RuntimeError("테스트 오류")):
            self.assertEqual(camera_preview.main(["--detect"]), 1)
        self.capture_factory.assert_not_called()
        self.assertIn("탐지 모델을 준비하지 못했습니다", self.errors.getvalue())

    # 회전한 원본에서 선택 영역만 저장하고 화면의 박스·라벨은 제외한다.
    def test_detection_overlay_does_not_change_saved_image(self):
        self.frame = np.arange(18, dtype=np.uint8).reshape(2, 3, 3)
        self.capture.read.return_value = (True, self.frame)
        rotated = np.rot90(self.frame, k=-1)
        overlay = np.full(rotated.shape, 255, dtype=np.uint8)
        detector = self.make_detector()
        detector.detect.return_value = [PackageDetection(0, 0.8, (0, 1, 2, 3))]
        detector.annotate.return_value = overlay
        self.keys.side_effect = [ord("s"), ord("q")]
        with (
            patch.object(camera_preview, "load_detector", return_value=detector) as loader,
            patch.object(camera_preview, "assess_photo", wraps=camera_preview.assess_photo) as assess,
        ):
            self.assertEqual(camera_preview.main(["--detect", "--rotate", "90", "--conf", "0.4"]), 0)
        self.assertEqual(assess.call_count, 2)
        np.testing.assert_array_equal(assess.call_args_list[0].args[0], rotated[1:3, 0:2])
        loader.assert_called_once_with(0.4)
        np.testing.assert_array_equal(detector.detect.call_args_list[0].args[0], rotated)
        np.testing.assert_array_equal(detector.annotate.call_args_list[0].args[0], rotated)
        np.testing.assert_array_equal(self.show.call_args_list[0].args[1], overlay)
        self.save.assert_called_once()
        np.testing.assert_array_equal(self.save.call_args.args[0], rotated[1:3, 0:2])
        self.assertEqual(self.save.call_args.kwargs, {"prefix": "crop"})
        self.assertIn("사진 품질: 주의", self.output.getvalue())
        self.assertIn("촬영 영역이 작음", self.output.getvalue())

    # 탐지되지 않은 상태에서 저장 키를 눌러도 전체 사진으로 대체하지 않는다.
    def test_no_detection_does_not_save_full_frame(self):
        detector = self.make_detector()
        detector.detect.return_value = []
        detector.annotate.return_value = self.frame.copy()
        self.keys.side_effect = [ord("s"), ord("q")]
        self.assertEqual(camera_preview.run_preview(0, "msmf", detector=detector), 0)
        self.save.assert_not_called()
        self.assertIn("촬영 대상이 없어 저장하지 않았습니다", self.output.getvalue())
        detector.make_quality_panel.assert_called_with(None, candidate_number=None, candidate_count=0)

    # 여러 후보 선택 중에는 재촬영·재추론 없이 고정된 프레임의 선택 영역을 저장한다.
    def test_multiple_detections_select_from_frozen_frame(self):
        self.frame = np.arange(12 * 16 * 3, dtype=np.uint8).reshape(12, 16, 3)
        self.capture.read.side_effect = [(True, self.frame), (True, np.zeros_like(self.frame))]
        detector = self.make_detector()
        detections = [
            PackageDetection(0, 0.9, (1, 1, 6, 7)),
            PackageDetection(1, 0.7, (8, 3, 16, 12)),
        ]
        detector.detect.return_value = detections
        detector.annotate.return_value = np.full_like(self.frame, 255)
        self.keys.side_effect = [ord("s"), 9, 13, ord("q")]
        with patch.object(camera_preview, "assess_photo", wraps=camera_preview.assess_photo) as assess:
            self.assertEqual(camera_preview.run_preview(0, "msmf", detector=detector), 0)
        self.assertEqual(assess.call_count, 4)
        self.assertEqual(self.capture.read.call_count, 2)
        self.assertEqual(detector.detect.call_count, 2)
        selections = [call for call in detector.annotate.call_args_list if call.kwargs]
        self.assertEqual([call.kwargs["selected_index"] for call in selections], [0, 1])
        for call in selections:
            self.assertIs(call.args[0], self.frame)
            self.assertIs(call.args[1], detections)
        self.save.assert_called_once()
        np.testing.assert_array_equal(self.save.call_args.args[0], self.frame[3:12, 8:16])
        self.assertFalse(np.shares_memory(self.save.call_args.args[0], self.frame))
        selection_panels = detector.make_quality_panel.call_args_list[1:3]
        self.assertEqual([call.kwargs["candidate_number"] for call in selection_panels], [1, 2])
        selected_quality = selection_panels[1].args[0]
        self.assertEqual((selected_quality.width, selected_quality.height), (8, 9))
        expected = camera_preview.assess_photo(self.frame[3:12, 8:16])
        self.assertEqual(selected_quality, expected)

    # 선택 중 취소하면 파일을 저장하지 않고 새 실시간 프레임으로 돌아간다.
    def test_selection_cancel_resumes_live_preview(self):
        detector = self.make_detector()
        detector.detect.return_value = [
            PackageDetection(0, 0.8, (0, 0, 4, 4)),
            PackageDetection(1, 0.8, (5, 5, 9, 9)),
        ]
        detector.annotate.return_value = self.frame.copy()
        self.keys.side_effect = [ord("s"), 27, ord("q")]
        self.assertEqual(camera_preview.run_preview(0, "msmf", detector=detector), 0)
        self.save.assert_not_called()
        self.assertEqual(self.capture.read.call_count, 2)
        self.assertIn("저장을 취소", self.output.getvalue())

    # 선택 화면의 종료 키와 창 닫기는 저장이나 다음 프레임 읽기 없이 자원을 정리한다.
    def test_selection_quit_and_window_close_release_camera(self):
        detector = self.make_detector()
        detector.detect.return_value = [
            PackageDetection(0, 0.8, (0, 0, 4, 4)),
            PackageDetection(1, 0.8, (5, 5, 9, 9)),
        ]
        detector.annotate.return_value = self.frame.copy()
        for key, visibility in ((ord("q"), [1]), (-1, [1, 0])):
            with self.subTest(key=key):
                self.capture.reset_mock()
                self.destroy_window.reset_mock()
                self.keys.side_effect = [ord("s"), key]
                self.visibility.side_effect = visibility
                self.assertEqual(camera_preview.run_preview(0, "msmf", detector=detector), 0)
                self.capture.read.assert_called_once()
                self.capture.release.assert_called_once()
                self.destroy_window.assert_called_once()
                self.save.assert_not_called()

    # 축소된 미리보기의 좌표가 아니라 원본 해상도의 좌표로 영역을 저장한다.
    def test_crop_retains_original_resolution_and_reports_clipped_padding(self):
        self.frame = np.arange(800 * 1200 * 3, dtype=np.uint8).reshape(800, 1200, 3)
        self.capture.read.return_value = (True, self.frame)
        detector = self.make_detector()
        detector.detect.return_value = [PackageDetection(0, 0.8, (100, 50, 1200, 800), True)]
        detector.annotate.return_value = np.full_like(self.frame, 255)
        self.keys.side_effect = [ord("s"), ord("q")]
        self.assertEqual(camera_preview.run_preview(0, "msmf", detector=detector), 0)
        self.assertEqual(self.show.call_args_list[0].args[1].shape, (640, 960, 3))
        np.testing.assert_array_equal(self.save.call_args.args[0], self.frame[50:800, 100:1200])
        self.assertIn("여백 일부를 확보하지 못했습니다", self.output.getvalue())
        self.assertIn("화면 경계로 여백 부족", self.output.getvalue())

    # 탐지 오류 뒤 오래된 표시나 사진을 남기지 않고 카메라를 해제한다.
    def test_detection_failure_releases_camera_without_save(self):
        detector = self.make_detector()
        detector.detect.side_effect = RuntimeError("테스트 추론 실패")
        self.assertEqual(camera_preview.run_preview(0, "msmf", detector=detector), 1)
        self.show.assert_not_called()
        self.save.assert_not_called()
        self.capture.release.assert_called_once()
        self.destroy_window.assert_called_once()

    # 품질 계산이 실패하면 이전 결과를 보여주거나 저장하지 않고 장치를 해제한다.
    def test_quality_failure_releases_camera_without_save(self):
        detector = self.make_detector()
        detector.detect.return_value = [PackageDetection(0, 0.8, (0, 0, 10, 10))]
        with patch.object(camera_preview, "assess_photo", side_effect=ValueError("테스트 검사 실패")):
            self.assertEqual(camera_preview.run_preview(0, "msmf", detector=detector), 1)
        self.show.assert_not_called()
        self.save.assert_not_called()
        self.capture.release.assert_called_once()
        self.destroy_window.assert_called_once()
        self.assertIn("품질 검사 실패", self.errors.getvalue())

    # 패널을 영상 아래에 표시해도 저장되는 픽셀은 원본의 선택 영역뿐이다.
    def test_quality_panel_is_not_saved_in_photo(self):
        detector = self.make_detector()
        detector.detect.return_value = [PackageDetection(0, 0.8, (2, 3, 14, 11))]
        detector.annotate.return_value = self.frame.copy()
        panel = np.full((220, 800, 3), 250, dtype=np.uint8)
        detector.make_quality_panel.return_value = panel
        self.keys.side_effect = [ord("s"), ord("q")]
        self.assertEqual(camera_preview.run_preview(0, "msmf", detector=detector), 0)
        displayed = self.show.call_args_list[0].args[1]
        self.assertEqual(displayed.shape, (232, 800, 3))
        np.testing.assert_array_equal(displayed[12:], panel)
        np.testing.assert_array_equal(self.save.call_args.args[0], self.frame[3:11, 2:14])
        self.assertTrue(np.all(self.frame == 100))

    # 큰 영상과 품질 패널을 함께 표시해도 전체 창 높이를 제한하고 패널 글자는 축소하지 않는다.
    def test_quality_panel_remains_readable_with_large_portrait_frame(self):
        frame = np.full((1200, 800, 3), 100, dtype=np.uint8)
        panel = np.full((220, 800, 3), 250, dtype=np.uint8)
        camera_preview.show_preview(frame, panel)
        displayed = self.show.call_args.args[1]
        self.assertEqual(displayed.shape, (720, 800, 3))
        np.testing.assert_array_equal(displayed[500:], panel)
        self.assertTrue(np.all(frame == 100))
        self.assertTrue(np.all(panel == 250))

    # 비대칭 영상의 모든 픽셀이 지정한 방향으로 표시되고 그대로 저장되는지 확인한다.
    def test_rotation_preserves_pixels_and_matches_saved_frame(self):
        self.frame = np.arange(18, dtype=np.uint8).reshape(2, 3, 3)
        self.capture.read.return_value = (True, self.frame)
        for angle in (0, 90, 180, 270):
            with self.subTest(angle=angle):
                self.keys.side_effect = [ord("s"), ord("q")]
                self.show.reset_mock()
                self.save.reset_mock()
                self.assertEqual(camera_preview.main(["--rotate", str(angle)]), 0)
                expected = np.rot90(self.frame, k=-(angle // 90))
                np.testing.assert_array_equal(self.show.call_args_list[0].args[1], expected)
                np.testing.assert_array_equal(self.save.call_args.args[0], expected)

    # 회전 키를 네 번 누르면 원래 방향으로 돌아오며 매번 표시한 방향으로 저장한다.
    def test_rotation_key_cycles_back_to_original(self):
        self.frame = np.arange(18, dtype=np.uint8).reshape(2, 3, 3)
        self.capture.read.return_value = (True, self.frame)
        self.keys.side_effect = [ord("r"), ord("s"), ord("R"), ord("s")] * 2 + [ord("q")]
        self.assertEqual(camera_preview.run_preview(0, "msmf"), 0)
        self.assertEqual(self.save.call_count, 4)
        for index, saved_call in enumerate(self.save.call_args_list):
            expected = np.rot90(self.frame, k=-(index + 1))
            np.testing.assert_array_equal(saved_call.args[0], expected)
            np.testing.assert_array_equal(self.show.call_args_list[index * 2 + 1].args[1], expected)

    # 큰 세로 영상은 화면에서만 비율을 유지해 줄이고 사진은 전체 해상도로 저장한다.
    def test_large_portrait_preview_keeps_full_resolution_for_save(self):
        self.frame = np.arange(800 * 1200 * 3, dtype=np.uint8).reshape(800, 1200, 3)
        self.capture.read.return_value = (True, self.frame)
        self.keys.side_effect = [ord("s"), ord("q")]
        self.assertEqual(camera_preview.run_preview(0, "msmf", 90), 0)
        displayed = self.show.call_args_list[0].args[1]
        saved = self.save.call_args.args[0]
        self.assertEqual(displayed.shape, (720, 480, 3))
        self.assertEqual(saved.shape, (1200, 800, 3))
        np.testing.assert_array_equal(saved, np.rot90(self.frame, k=-1))

    # 카메라를 열지 못하면 읽기나 저장 없이 오류로 종료한다.
    def test_open_failure_releases_device(self):
        self.capture.open.return_value = False
        self.assertEqual(camera_preview.run_preview(0, "dshow"), 1)
        self.capture.read.assert_not_called()
        self.save.assert_not_called()
        self.capture.release.assert_called_once()
        self.destroy_window.assert_not_called()

    # 새 프레임을 받지 못하면 이전 프레임을 다시 저장하지 않는다.
    def test_disconnect_does_not_save_stale_frame(self):
        self.capture.read.side_effect = [(True, self.frame), (False, None)]
        self.keys.return_value = ord("s")
        self.assertEqual(camera_preview.run_preview(0, "dshow"), 1)
        self.assertEqual(self.save.call_count, 1)
        self.assertIs(self.save.call_args.args[0], self.frame)
        self.assertEqual(self.save.call_args.args[1], camera_preview.CAPTURE_DIRECTORY)
        self.capture.release.assert_called_once()

    # 내용이 없는 프레임은 표시하거나 저장하지 않는다.
    def test_empty_frame_is_rejected(self):
        self.capture.read.return_value = (True, np.empty((0, 0, 3), dtype=np.uint8))
        self.assertEqual(camera_preview.run_preview(0, "dshow"), 1)
        self.keys.assert_not_called()
        self.save.assert_not_called()

    # 저장에 실패해도 미리보기를 유지하고 성공 문구 대신 오류를 알린다.
    def test_save_failure_allows_normal_exit(self):
        self.keys.side_effect = [ord("s"), ord("q")]
        self.save.side_effect = OSError("테스트 저장 실패")
        self.assertEqual(camera_preview.run_preview(0, "dshow"), 0)
        self.assertIn("사진 저장 실패", self.errors.getvalue())
        self.assertEqual(self.capture.read.call_count, 2)
        self.capture.release.assert_called_once()

    # 창의 닫기 버튼으로 종료할 때 카메라를 해제한다.
    def test_closed_window_exits(self):
        self.keys.return_value = -1
        self.visibility.return_value = 0
        self.assertEqual(camera_preview.run_preview(0, "dshow"), 0)
        self.capture.release.assert_called_once()
        self.save.assert_not_called()

    # 읽기 중 예외가 발생해도 카메라와 창을 정리한다.
    def test_read_exception_cleans_up(self):
        self.capture.read.side_effect = cv2.error("테스트 읽기 오류")
        self.assertEqual(camera_preview.run_preview(0, "dshow"), 1)
        self.capture.release.assert_called_once()
        self.destroy_window.assert_called_once()

    # 터미널에서 중단해도 정상 종료하고 카메라를 해제한다.
    def test_keyboard_interrupt_cleans_up(self):
        self.capture.read.side_effect = KeyboardInterrupt
        self.assertEqual(camera_preview.run_preview(0, "dshow"), 0)
        self.capture.release.assert_called_once()
        self.destroy_window.assert_called_once()


class FrameSaveTests(unittest.TestCase):
    # 잘라낸 사진의 JPEG 크기와 파일명이 전체 프레임과 구분되는지 확인한다.
    def test_cropped_jpeg_has_selected_dimensions(self):
        frame = np.full((40, 60, 3), 100, dtype=np.uint8)
        cropped = PackageDetection(0, 0.8, (5, 10, 55, 35)).crop(frame)
        with TemporaryDirectory() as temporary_directory:
            path = camera_preview.save_frame(cropped, Path(temporary_directory), prefix="crop")
            self.assertTrue(path.name.startswith("crop_"))
            decoded = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
            self.assertEqual(decoded.shape, (25, 50, 3))
            self.assertLessEqual(np.abs(decoded.astype(int) - cropped.astype(int)).max(), 2)

    # 한국어 폴더에 저장한 합성 이미지가 정상적으로 다시 읽히는지 확인한다.
    def test_jpeg_round_trip_and_unique_names(self):
        frame = np.full((12, 16, 3), 100, dtype=np.uint8)
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory) / "한글 테스트"
            first = camera_preview.save_frame(frame, directory)
            second = camera_preview.save_frame(frame, directory)
            self.assertNotEqual(first, second)
            self.assertTrue(second.exists())
            decoded = cv2.imdecode(
                np.frombuffer(first.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR
            )
            self.assertIsNotNone(decoded)
            self.assertEqual(decoded.shape, frame.shape)
            self.assertLessEqual(np.abs(decoded.astype(int) - frame.astype(int)).max(), 2)

    # JPEG 변환 실패를 저장 성공으로 처리하지 않는다.
    def test_encoding_failure_does_not_create_file(self):
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory) / "captures"
            with patch.object(cv2, "imencode", return_value=(False, None)):
                with self.assertRaises(OSError):
                    camera_preview.save_frame(np.zeros((2, 2, 3), dtype=np.uint8), directory)
            self.assertFalse(directory.exists())


if __name__ == "__main__":
    unittest.main()
