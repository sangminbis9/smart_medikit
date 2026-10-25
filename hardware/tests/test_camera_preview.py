"""실제 카메라와 화면을 사용하지 않고 미리보기의 저장·오류·종료를 검증한다."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from hardware import camera_preview


class PreviewTests(unittest.TestCase):
    # 카메라와 창을 모의 객체로 바꾸어 실제 장치를 열지 않는다.
    def setUp(self):
        self.enterContext(redirect_stdout(StringIO()))
        self.errors = self.enterContext(redirect_stderr(StringIO()))
        self.capture_factory = self.enterContext(patch.object(cv2, "VideoCapture"))
        self.capture = self.capture_factory.return_value
        self.capture.open.return_value = True
        self.frame = np.full((12, 16, 3), 100, dtype=np.uint8)
        self.capture.read.return_value = (True, self.frame)
        self.enterContext(patch.object(cv2, "namedWindow"))
        self.enterContext(patch.object(cv2, "imshow"))
        self.keys = self.enterContext(patch.object(cv2, "waitKey", return_value=ord("q")))
        self.visibility = self.enterContext(
            patch.object(cv2, "getWindowProperty", return_value=1)
        )
        self.destroy_window = self.enterContext(patch.object(cv2, "destroyWindow"))
        self.save = self.enterContext(patch.object(camera_preview, "save_frame"))

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
