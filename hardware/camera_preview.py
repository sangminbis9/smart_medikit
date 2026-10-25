"""PC에 연결된 카메라 영상을 확인하고 테스트용 프레임을 수동 저장한다."""

import argparse
from datetime import datetime
import os
from pathlib import Path
import sys
from uuid import uuid4

import cv2
import numpy as np


WINDOW_NAME = "smart_medikit"
CAPTURE_DIRECTORY = Path(__file__).resolve().parents[1] / "local_data" / "camera_captures"
BACKENDS = {"auto": cv2.CAP_ANY, "dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF}


# 실행할 카메라 번호와 영상 입력 방식을 읽고 잘못된 번호를 거부한다.
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="DroidCam 등 PC 카메라의 실시간 영상을 확인한다.",
        epilog="영상 창에서 S: 한 장 저장, Q 또는 Esc: 종료. 저장은 약품 식별이 아닙니다.",
    )
    parser.add_argument("--camera", type=int, default=0, help="카메라 번호 (기본값: 0)")
    parser.add_argument(
        "--backend",
        choices=BACKENDS,
        default="dshow" if os.name == "nt" else "auto",
        help="영상 입력 방식 (Windows 기본값: dshow, 그 외: auto)",
    )
    args = parser.parse_args(argv)
    if args.camera < 0:
        parser.error("카메라 번호는 0 이상의 정수여야 합니다.")
    return args


# 현재 원본 프레임을 한국어 경로에서도 저장할 수 있는 방식으로 JPEG 파일에 쓴다.
def save_frame(frame: np.ndarray, directory: Path) -> Path:
    encoded_ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not encoded_ok:
        raise OSError("프레임을 JPEG 이미지로 변환하지 못했습니다.")

    # 테스트 사진은 Git에서 제외된 로컬 폴더에만 보관한다.
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    destination = directory / f"frame_{timestamp}_{uuid4().hex[:8]}.jpg"
    with destination.open("xb") as image_file:
        image_file.write(encoded.tobytes())
    return destination


# 선택한 카메라의 미리보기를 표시하고 수동 저장 및 종료 요청을 처리한다.
def run_preview(camera_index: int, backend: str) -> int:
    capture = cv2.VideoCapture()
    window_created = False
    try:
        if not capture.open(camera_index, BACKENDS[backend]):
            print(
                f"카메라 {camera_index}번을 열지 못했습니다. DroidCam 연결을 확인하고 "
                "--camera 1 또는 --backend msmf로 다시 실행해 보세요.",
                file=sys.stderr,
            )
            return 1

        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
        window_created = True
        print(f"카메라 {camera_index}번 / 입력 방식: {backend}")
        print("영상 창을 선택한 뒤 S: 한 장 저장, Q 또는 Esc: 종료")
        print("개인정보가 없는 테스트용 포장만 사용하세요. 자동 촬영·약품 식별 기능은 없습니다.")

        while True:
            received, frame = capture.read()
            # 연결 실패 시 이전 프레임을 현재 영상으로 저장하지 않는다.
            if not received or frame is None or frame.size == 0:
                print(
                    "새 영상을 받지 못해 종료합니다. DroidCam 연결 후 다시 실행하세요.",
                    file=sys.stderr,
                )
                return 1

            cv2.imshow(WINDOW_NAME, frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), ord("Q"), 27):
                return 0
            if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                return 0

            if key in (ord("s"), ord("S")):
                try:
                    saved_path = save_frame(frame, CAPTURE_DIRECTORY)
                except (OSError, cv2.error) as error:
                    print(f"사진 저장 실패: {error}", file=sys.stderr)
                else:
                    print(f"테스트 프레임 저장 완료: {saved_path}")

    except KeyboardInterrupt:
        print("미리보기를 종료합니다.")
        return 0
    except cv2.error as error:
        print(f"영상 처리 오류: {error}", file=sys.stderr)
        return 1
    finally:
        capture.release()
        if window_created:
            try:
                cv2.destroyWindow(WINDOW_NAME)
            except cv2.error:
                # 사용자가 이미 창을 닫았다면 추가로 닫을 창이 없다.
                pass


# 명령행 설정으로 미리보기를 실행하고 종료 상태를 반환한다.
def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run_preview(args.camera, args.backend)


if __name__ == "__main__":
    raise SystemExit(main())
