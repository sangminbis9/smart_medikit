"""PC 카메라에서 포장 품질을 확인하고 수동 또는 조건부 자동으로 저장한다."""

import argparse
from datetime import datetime
import math
import os
from pathlib import Path
import sys
import time
from uuid import uuid4
from typing import TYPE_CHECKING

import cv2
import numpy as np

if __package__:
    from .auto_capture import AutoCaptureGate
    from .image_quality import DEFAULT_THRESHOLDS, PhotoQuality, QualityThresholds, WARNING_MESSAGES, assess_photo
else:
    from auto_capture import AutoCaptureGate
    from image_quality import DEFAULT_THRESHOLDS, PhotoQuality, QualityThresholds, WARNING_MESSAGES, assess_photo

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
    parser.add_argument(
        "--min-sharpness",
        type=float,
        default=DEFAULT_THRESHOLDS.min_sharpness,
        help="품질 검사의 선명도 최소값 (기본값: %(default)g, --detect/--auto-capture에 적용)",
    )
    parser.add_argument(
        "--auto-capture", action="store_true", help="품질 양호가 유지되면 한 번 자동 저장한다 (--detect 포함)"
    )
    parser.add_argument(
        "--hold-seconds", type=float, default=2.0, help="자동 저장 전 양호 유지 시간 (기본값: 2초)"
    )
    parser.add_argument(
        "--rearm-seconds", type=float, default=1.5, help="다음 자동 촬영을 허용할 연속 미감지 시간 (기본값: 1.5초)"
    )
    args = parser.parse_args(argv)
    if args.camera < 0:
        parser.error("카메라 번호는 0 이상의 정수여야 합니다.")
    if not 0 < args.conf <= 1:
        parser.error("--conf는 0 초과 1 이하의 수여야 합니다.")
    if not math.isfinite(args.min_sharpness) or args.min_sharpness < 0:
        parser.error("--min-sharpness는 0 이상의 유한한 수여야 합니다.")
    for option, value in (("--hold-seconds", args.hold_seconds), ("--rearm-seconds", args.rearm_seconds)):
        if not math.isfinite(value) or value <= 0:
            parser.error(f"{option}는 0보다 큰 유한한 수여야 합니다.")
    args.detect = args.detect or args.auto_capture
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


# 카메라의 품질 판정과 수동·자동 저장을 연결하고 회전 및 종료를 처리한다.
def run_preview(
    camera_index: int,
    backend: str,
    rotation: int = 0,
    detector: "PackageDetector | None" = None,
    auto_capture: AutoCaptureGate | None = None,
    thresholds: QualityThresholds = DEFAULT_THRESHOLDS,
) -> int:
    if auto_capture is not None and detector is None:
        raise ValueError("자동 촬영에는 포장 탐지기가 필요합니다.")
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
        print("개인정보가 없는 테스트용 포장만 사용하세요. 최종 약품 식별이나 서버 전송은 하지 않습니다.")
        if detector is not None:
            print("YOLOE-26n: 포장 후보를 표시합니다. 탐지 점수는 약품 식별 정확도가 아닙니다.")
            print(f"선명도 기준: {thresholds.min_sharpness:g} 미만이면 흐림 의심으로 표시합니다.")
            print("S: 10% 여백을 포함한 박스 영역 저장. 후보가 여러 개면 Tab으로 고른 뒤 Enter를 누르세요.")
            print("저장 사진에는 박스·라벨을 넣지 않습니다. 탐지 중에는 영상 갱신이 느려질 수 있습니다.")
            print("화면 아래에 사진 품질을 표시합니다. 실험 기준의 주의 표시이며 S 저장을 차단하지 않습니다.")
        if auto_capture is not None:
            print(
                f"자동 촬영: 후보 하나의 품질이 {auto_capture.hold_seconds:g}초 양호하면 한 번 저장합니다. "
                f"모든 후보가 {auto_capture.rearm_seconds:g}초 감지되지 않아야 다음 촬영을 허용합니다."
            )
            print("각 조건은 최소 3개 프레임에서 확인합니다. S 수동 저장도 성공하면 자동 중복 저장을 막습니다.")

        while True:
            received, frame = capture.read()
            # 연결 실패 시 이전 프레임을 현재 영상으로 저장하지 않는다.
            if not received or frame is None or frame.size == 0:
                print(
                    "새 영상을 받지 못해 종료합니다. DroidCam 연결 후 다시 실행하세요.",
                    file=sys.stderr,
                )
                return 1

            # 처리하지 못한 시간이나 수동 선택에 머문 시간을 양호한 관측으로 세지 않는다.
            observed_at = time.monotonic() if auto_capture is not None else 0.0

            # 탐지 좌표와 저장 이미지가 일치하도록 회전을 먼저 적용한다.
            if rotation:
                frame = cv2.rotate(frame, rotation_codes[rotation])

            # 같은 프레임의 탐지 결과만 표시하며 저장용 이미지에는 표시를 넣지 않는다.
            preview_frame = frame
            detections = []
            qualities = []
            quality_panel = None
            auto_ready = False
            if detector is not None:
                try:
                    detections = detector.detect(frame)
                    # 박스·라벨을 그리기 전 저장할 원본 영역으로만 품질을 계산한다.
                    qualities = [
                        assess_photo(
                            detection.crop(frame), padding_clipped=detection.padding_clipped, thresholds=thresholds
                        )
                        for detection in detections
                    ]
                    if auto_capture is not None:
                        auto_ready = auto_capture.update(detections, qualities, observed_at)
                    preview_frame = detector.annotate(frame, detections)
                    panel_options = {} if auto_capture is None else {"auto_capture": auto_capture}
                    quality_panel = detector.make_quality_panel(
                        qualities[0] if len(qualities) == 1 else None,
                        candidate_number=1 if len(qualities) == 1 else None,
                        candidate_count=len(detections),
                        **panel_options,
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
                if auto_capture is not None:
                    # 회전 전 좌표의 유지 시간은 버리되 이미 저장한 대상의 잠금은 보존한다.
                    auto_capture.reset_pending()
                print(f"수동 회전: {rotation}도. 같은 설정으로 시작하려면 --rotate {rotation}")
                continue

            manual_save = key in (ord("s"), ord("S"))
            if manual_save or auto_ready:
                if auto_capture is not None and manual_save:
                    # 고정 화면에서 선택하는 동안 자동 촬영의 관측 시간이 흐르지 않게 한다.
                    auto_capture.reset_pending()
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
                        saved_path = save_frame(
                            cropped, CAPTURE_DIRECTORY, prefix="crop" if manual_save else "auto_crop"
                        )
                        if selected.padding_clipped:
                            print("화면 가장자리 때문에 10% 여백 일부를 확보하지 못했습니다.")
                except (OSError, ValueError, cv2.error) as error:
                    if auto_capture is not None:
                        # 저장 실패를 성공으로 잠그지 않고 새 양호 구간을 확인한 뒤 재시도한다.
                        auto_capture.reset_pending()
                    print(f"사진 저장 실패: {error}", file=sys.stderr)
                else:
                    if auto_capture is not None:
                        auto_capture.mark_saved()
                    message = "테스트 프레임 저장 완료" if manual_save else "자동 촬영 저장 완료"
                    print(f"{message}: {saved_path}")

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
    auto_capture = AutoCaptureGate(args.hold_seconds, args.rearm_seconds) if args.auto_capture else None
    thresholds = QualityThresholds(min_sharpness=args.min_sharpness)
    return run_preview(args.camera, args.backend, args.rotate, detector, auto_capture, thresholds)


if __name__ == "__main__":
    raise SystemExit(main())
