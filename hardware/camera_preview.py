"""PC에 연결된 카메라 영상을 확인하고 테스트용 프레임을 수동 저장한다."""

import argparse
from datetime import datetime
import os
from pathlib import Path
import sys
from uuid import uuid4
from typing import TYPE_CHECKING

import cv2
import numpy as np

if __package__:
    from .image_quality import PhotoQuality, WARNING_MESSAGES, assess_photo
else:
    from image_quality import PhotoQuality, WARNING_MESSAGES, assess_photo

if TYPE_CHECKING:
    from hardware.package_detector import PackageDetection, PackageDetector


WINDOW_NAME = "smart_medikit"
CAPTURE_DIRECTORY = Path(__file__).resolve().parents[1] / "local_data" / "camera_captures"
BACKENDS = {"auto": cv2.CAP_ANY, "dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF}


# 실행할 카메라 번호, 영상 입력 방식과 수동 회전 각도를 읽는다.
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="DroidCam 등 PC 카메라의 실시간 영상을 확인한다.",
        epilog="영상 창에서 R: 시계 방향 90도 회전, S: 저장, Q 또는 Esc: 종료.",
    )
    parser.add_argument("--camera", type=int, default=0, help="카메라 번호 (기본값: 0)")
    parser.add_argument(
        "--backend",
        choices=BACKENDS,
        default="dshow" if os.name == "nt" else "auto",
        help="영상 입력 방식 (Windows 기본값: dshow, 그 외: auto)",
    )
    parser.add_argument(
        "--rotate",
        type=int,
        choices=(0, 90, 180, 270),
        default=0,
        help="입력 영상에 적용할 시계 방향 회전 각도 (기본값: 0)",
    )
    parser.add_argument(
        "--detect", action="store_true", help="YOLOE-26n 포장 후보를 표시하고 S로 박스 영역을 저장한다"
    )
    parser.add_argument(
        "--conf", type=float, default=0.25, help="탐지 점수의 최소값 (0 초과 1 이하, 기본값: 0.25)"
    )
    args = parser.parse_args(argv)
    if args.camera < 0:
        parser.error("카메라 번호는 0 이상의 정수여야 합니다.")
    if not 0 < args.conf <= 1:
        parser.error("--conf는 0 초과 1 이하의 수여야 합니다.")
    return args


# 탐지 옵션을 선택했을 때만 추가 패키지와 모델을 불러온다.
def load_detector(confidence: float) -> "PackageDetector":
    if __package__:
        from .package_detector import PackageDetector
    else:
        from package_detector import PackageDetector

    return PackageDetector(confidence=confidence)


# 전체 프레임 또는 잘라낸 영역을 한국어 경로에도 JPEG 파일로 저장한다.
def save_frame(frame: np.ndarray, directory: Path, prefix: str = "frame") -> Path:
    encoded_ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not encoded_ok:
        raise OSError("프레임을 JPEG 이미지로 변환하지 못했습니다.")

    # 테스트 사진은 Git에서 제외된 로컬 폴더에만 보관한다.
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    destination = directory / f"{prefix}_{timestamp}_{uuid4().hex[:8]}.jpg"
    with destination.open("xb") as image_file:
        image_file.write(encoded.tobytes())
    return destination


# 원본 해상도는 유지하고 표시 영상 아래에 읽기 쉬운 크기의 품질 패널을 배치한다.
def show_preview(frame: np.ndarray, quality_panel: np.ndarray | None = None) -> None:
    height, width = frame.shape[:2]
    panel_height = 0 if quality_panel is None else quality_panel.shape[0]
    scale = min(960 / width, (720 - panel_height) / height, 1.0)
    if scale < 1.0:
        frame = cv2.resize(
            frame,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    if quality_panel is not None:
        # 패널을 사진 밖에 붙여 글자를 가리지 않고 저장용 배열과도 분리한다.
        height, width = frame.shape[:2]
        panel_width = quality_panel.shape[1]
        canvas_width = max(width, panel_width)
        canvas = np.full((height + panel_height, canvas_width, 3), 24, dtype=np.uint8)
        frame_left = (canvas_width - width) // 2
        panel_left = (canvas_width - panel_width) // 2
        canvas[:height, frame_left:frame_left + width] = frame
        canvas[height:, panel_left:panel_left + panel_width] = quality_panel
        frame = canvas
    cv2.imshow(WINDOW_NAME, frame)


# 선택한 사진의 화질 수치와 주의 사유를 터미널에 출력한다.
def print_quality(report: PhotoQuality) -> None:
    status = "주의" if report.warnings else "양호"
    print(
        f"사진 품질: {status} (실험 기준) | {report.width}×{report.height} px | "
        f"선명도 {report.sharpness:.1f} | 평균 밝기 {report.mean_brightness:.1f}/255 | "
        f"어두운 영역 {report.dark_ratio:.1%} | 밝은 영역 {report.bright_ratio:.1%}"
    )
    if report.warnings:
        print("주의 사유: " + ", ".join(WARNING_MESSAGES[warning] for warning in report.warnings))
    print("수동 저장은 가능합니다. 이 결과는 안정성 검사나 OCR 성공 판정이 아닙니다.")


# 촬영한 한 프레임을 고정해 후보를 선택하고 선택 번호와 종료 요청 여부를 반환한다.
def select_detection(
    frame: np.ndarray,
    detections: list["PackageDetection"],
    detector: "PackageDetector",
    qualities: list[PhotoQuality],
) -> tuple[int | None, bool]:
    selected_index = 0
    redraw = True
    print("화면을 멈췄습니다. Tab: 다음 박스, Enter: 선택 영역 저장, Esc: 취소, Q: 종료")
    while True:
        if redraw:
            show_preview(
                detector.annotate(frame, detections, selected_index=selected_index),
                detector.make_quality_panel(
                    qualities[selected_index],
                    candidate_number=selected_index + 1,
                    candidate_count=len(detections),
                ),
            )
            print(f"선택한 박스: #{selected_index + 1} / {len(detections)}")
            redraw = False
        key = cv2.waitKey(20) & 0xFF
        if key in (ord("q"), ord("Q")):
            return None, True
        if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
            return None, True
        if key == 27:
            print("저장을 취소하고 실시간 영상으로 돌아갑니다.")
            return None, False
        if key in (10, 13):
            return selected_index, False
        if key == 9:
            selected_index = (selected_index + 1) % len(detections)
            redraw = True


# 선택한 카메라의 미리보기를 표시하고 수동 회전, 저장 및 종료를 처리한다.
def run_preview(
    camera_index: int,
    backend: str,
    rotation: int = 0,
    detector: "PackageDetector | None" = None,
) -> int:
    capture = cv2.VideoCapture()
    window_created = False
    rotation_codes = {
        90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }
    try:
        if not capture.open(camera_index, BACKENDS[backend]):
            print(
                f"카메라 {camera_index}번을 열지 못했습니다. DroidCam 연결을 확인하고 "
                "--camera 1 또는 --backend msmf로 다시 실행해 보세요.",
                file=sys.stderr,
            )
            return 1

        # 창을 늘려도 글자 비율이 변형되지 않도록 표시 영상 크기에 맞춘다.
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_AUTOSIZE)
        window_created = True
        print(f"카메라 {camera_index}번 / 입력 방식: {backend}")
        print("영상 창을 선택한 뒤 R: 시계 방향 90도 회전, S: 저장, Q 또는 Esc: 종료")
        print(f"수동 회전: {rotation}도. 표시 방향 그대로 저장하며 휴대폰 방향은 자동 감지하지 않습니다.")
        print("개인정보가 없는 테스트용 포장만 사용하세요. 자동 촬영·약품 식별 기능은 없습니다.")
        if detector is not None:
            print("YOLOE-26n: 포장 후보를 표시합니다. 탐지 점수는 약품 식별 정확도가 아닙니다.")
            print("S: 10% 여백을 포함한 박스 영역 저장. 후보가 여러 개면 Tab으로 고른 뒤 Enter를 누르세요.")
            print("저장 사진에는 박스·라벨을 넣지 않습니다. 탐지 중에는 영상 갱신이 느려질 수 있습니다.")
            print("화면 아래에 사진 품질을 표시합니다. 실험 기준의 주의 표시이며 S 저장을 차단하지 않습니다.")

        while True:
            received, frame = capture.read()
            # 연결 실패 시 이전 프레임을 현재 영상으로 저장하지 않는다.
            if not received or frame is None or frame.size == 0:
                print(
                    "새 영상을 받지 못해 종료합니다. DroidCam 연결 후 다시 실행하세요.",
                    file=sys.stderr,
                )
                return 1

            # 탐지 좌표와 저장 이미지가 일치하도록 회전을 먼저 적용한다.
            if rotation:
                frame = cv2.rotate(frame, rotation_codes[rotation])

            # 같은 프레임의 탐지 결과만 표시하며 저장용 이미지에는 표시를 넣지 않는다.
            preview_frame = frame
            detections = []
            qualities = []
            quality_panel = None
            if detector is not None:
                try:
                    detections = detector.detect(frame)
                    # 박스·라벨을 그리기 전 저장할 원본 영역으로만 품질을 계산한다.
                    qualities = [
                        assess_photo(detection.crop(frame), padding_clipped=detection.padding_clipped)
                        for detection in detections
                    ]
                    preview_frame = detector.annotate(frame, detections)
                    quality_panel = detector.make_quality_panel(
                        qualities[0] if len(qualities) == 1 else None,
                        candidate_number=1 if len(qualities) == 1 else None,
                        candidate_count=len(detections),
                    )
                except Exception as error:
                    print(f"탐지·품질 검사 실패로 종료합니다: {error}", file=sys.stderr)
                    return 1

            show_preview(preview_frame, quality_panel)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), ord("Q"), 27):
                return 0
            if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                return 0

            if key in (ord("r"), ord("R")):
                rotation = (rotation + 90) % 360
                print(f"수동 회전: {rotation}도. 같은 설정으로 시작하려면 --rotate {rotation}")
                continue

            if key in (ord("s"), ord("S")):
                selected_index = 0
                if detector is not None:
                    if not detections:
                        print("촬영 대상이 없어 저장하지 않았습니다. 포장을 다시 보여주세요.")
                        continue
                    # 여러 후보 중 임의 대상을 저장하지 않고 같은 프레임에서 사용자가 선택한다.
                    if len(detections) > 1:
                        try:
                            selected_index, should_quit = select_detection(frame, detections, detector, qualities)
                        except Exception as error:
                            print(f"대상 선택 화면 처리 실패로 종료합니다: {error}", file=sys.stderr)
                            return 1
                        if should_quit:
                            return 0
                        if selected_index is None:
                            continue
                try:
                    if detector is None:
                        saved_path = save_frame(frame, CAPTURE_DIRECTORY)
                    else:
                        selected = detections[selected_index]
                        print_quality(qualities[selected_index])
                        # 표시용 박스나 라벨이 섞이지 않도록 원본 배열에서만 자른다.
                        cropped = selected.crop(frame)
                        saved_path = save_frame(cropped, CAPTURE_DIRECTORY, prefix="crop")
                        if selected.padding_clipped:
                            print("화면 가장자리 때문에 10% 여백 일부를 확보하지 못했습니다.")
                except (OSError, ValueError, cv2.error) as error:
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
    detector = None
    if args.detect:
        print("YOLOE-26n을 준비합니다. 첫 실행은 모델과 텍스트 인코더 다운로드가 필요합니다.")
        try:
            detector = load_detector(args.conf)
        except KeyboardInterrupt:
            print("모델 준비를 중단했습니다.")
            return 0
        except Exception as error:
            print(
                f"탐지 모델을 준비하지 못했습니다: {error}\n"
                "같은 가상환경에서 hardware/requirements.txt 설치와 인터넷 연결을 확인하세요.",
                file=sys.stderr,
            )
            return 1
    return run_preview(args.camera, args.backend, args.rotate, detector)


if __name__ == "__main__":
    raise SystemExit(main())
