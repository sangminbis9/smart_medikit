"""YOLOE로 포장 후보를 탐지하고 저장용 원본과 분리된 미리보기를 만든다."""

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np

if __package__:
    from .image_quality import PhotoQuality, WARNING_MESSAGES
else:
    from image_quality import PhotoQuality, WARNING_MESSAGES

if TYPE_CHECKING:
    from hardware.auto_capture import AutoCaptureGate


MODEL_DIRECTORY = Path(__file__).resolve().parents[1] / "local_data" / "models" / "yoloe"
MODEL_FILENAME = "yoloe-26n-seg.pt"
BOX_PADDING_RATIO = 0.10
PACKAGE_CLASSES = (
    ("medicine box", "약품 상자"),
    ("prescription medicine bag", "처방약 봉투"),
    ("medicine sachet", "소형 약품 포장"),
    ("pill blister pack", "알약 블리스터 포장"),
    ("medicine bottle", "약품 병"),
    ("ointment tube", "연고 튜브"),
    ("medical patch package", "파스 포장"),
    ("paper document", "문서 후보"),
)
KOREAN_FONT_PATHS = (
    Path("C:/Windows/Fonts/malgun.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf"),
)


@dataclass(frozen=True)
class PackageDetection:
    class_id: int
    confidence: float
    # 오른쪽과 아래쪽 끝점을 제외하는 원본 좌표로 표시와 자르기를 일치시킨다.
    bounds: tuple[int, int, int, int]
    padding_clipped: bool = False

    # 유효한 원본 영역을 표시나 크기 변경 없이 독립된 BGR 배열로 잘라낸다.
    def crop(self, frame: np.ndarray) -> np.ndarray:
        if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3 or not frame.size:
            raise ValueError("자르기에는 비어 있지 않은 BGR 프레임이 필요합니다.")
        if (
            not isinstance(self.bounds, tuple)
            or len(self.bounds) != 4
            or not all(isinstance(value, (int, np.integer)) and not isinstance(value, bool) for value in self.bounds)
        ):
            raise ValueError("자르기 좌표는 정수 네 개여야 합니다.")
        left, top, right, bottom = self.bounds
        height, width = frame.shape[:2]
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            raise ValueError("자르기 영역이 비어 있거나 원본 프레임 범위를 벗어났습니다.")
        return frame[top:bottom, left:right].copy()


class PackageDetector:
    # 모델과 탐지 문구를 한 번 준비하고 생성 파일을 Git 제외 경로에 보관한다.
    def __init__(self, confidence: float = 0.25):
        if not math.isfinite(confidence) or not 0.0 < confidence <= 1.0:
            raise ValueError("탐지 점수 기준은 0 초과 1 이하의 유한한 값이어야 합니다.")

        self.confidence = confidence
        MODEL_DIRECTORY.mkdir(parents=True, exist_ok=True)
        os.environ["YOLO_CONFIG_DIR"] = str(MODEL_DIRECTORY / "config")
        previous_directory = Path.cwd()
        try:
            # 텍스트 인코더의 상대 경로 다운로드도 모델 폴더 안에서만 수행한다.
            os.chdir(MODEL_DIRECTORY)
            try:
                from PIL import Image, ImageDraw, ImageFont
                from ultralytics import YOLOE
            except ImportError as error:
                raise RuntimeError(
                    "YOLOE 실행 패키지가 없습니다. hardware/requirements.txt로 패키지를 설치하세요."
                ) from error

            self.model = YOLOE(str(MODEL_DIRECTORY / MODEL_FILENAME))
            self.model.set_classes([english for english, _ in PACKAGE_CLASSES])
        finally:
            # 초기화가 실패해도 호출자의 작업 폴더를 바꾸지 않는다.
            os.chdir(previous_directory)

        self._image = Image
        self._image_draw = ImageDraw
        self.font = None
        for font_path in KOREAN_FONT_PATHS:
            if font_path.is_file():
                try:
                    self.font = ImageFont.truetype(str(font_path), size=18)
                except OSError:
                    continue
                break
        self.use_korean_labels = self.font is not None
        if self.font is None:
            self.font = ImageFont.load_default(size=16)
            print("한국어 표시 글꼴이 없어 탐지 박스에는 영어 범주명을 표시합니다.")

    # 현재 원본을 한 번 탐지하고 각 방향에 10% 여백을 더한 자르기 좌표를 반환한다.
    def detect(self, frame: np.ndarray) -> list[PackageDetection]:
        if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3 or not frame.size:
            raise ValueError("탐지에는 비어 있지 않은 BGR 프레임이 필요합니다.")
        # 추론 내부 처리나 화면 주석이 수동 저장용 프레임을 변경하지 못하게 한다.
        results = self.model.predict(
            source=frame.copy(),
            imgsz=640,
            conf=self.confidence,
            device="cpu",
            verbose=False,
            save=False,
            max_det=20,
        )
        if not results or results[0].boxes is None or len(results[0].boxes) == 0:
            return []

        height, width = frame.shape[:2]
        detections = []
        for box in results[0].boxes:
            class_id = int(box.cls[0])
            if not 0 <= class_id < len(PACKAGE_CLASSES):
                raise RuntimeError("모델이 설정된 탐지 범위를 벗어난 범주를 반환했습니다.")
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            if not all(math.isfinite(value) for value in (x1, y1, x2, y2)) or x2 <= x1 or y2 <= y1:
                continue

            # 끝점을 너비·높이까지 허용해야 마지막 행과 열도 저장 영역에 포함된다.
            padding_x = (x2 - x1) * BOX_PADDING_RATIO
            padding_y = (y2 - y1) * BOX_PADDING_RATIO
            x1, x2 = math.floor(x1 - padding_x), math.ceil(x2 + padding_x)
            y1, y2 = math.floor(y1 - padding_y), math.ceil(y2 + padding_y)
            padding_clipped = x1 < 0 or y1 < 0 or x2 > width or y2 > height
            x1, x2 = max(0, min(width, x1)), max(0, min(width, x2))
            y1, y2 = max(0, min(height, y1)), max(0, min(height, y2))
            if x2 <= x1 or y2 <= y1:
                continue
            detections.append(PackageDetection(class_id, float(box.conf[0]), (x1, y1, x2, y2), padding_clipped))
        return detections

    # 원본 사진과 분리된 고정 크기 패널에 실험용 품질 수치와 주의 사유를 표시한다.
    def make_quality_panel(
        self,
        report: PhotoQuality | None,
        *,
        candidate_number: int | None = None,
        candidate_count: int = 0,
        auto_capture: "AutoCaptureGate | None" = None,
    ) -> np.ndarray:
        image = self._image.new("RGB", (800, 260 if auto_capture is not None else 220), (25, 29, 36))
        draw = self._image_draw.Draw(image)
        text_color = (230, 234, 240)
        title_color = text_color
        rows = []
        if report is None:
            # 대상이 없거나 선택하지 않았을 때는 검사 결과를 양호로 오해하게 표시하지 않는다.
            if self.use_korean_labels:
                title = "품질 검사: 대상 없음" if candidate_count == 0 else f"품질 검사: 후보 {candidate_count}개"
                rows = [
                    "포장을 카메라에 보여주세요." if candidate_count == 0 else "S로 화면 고정 → Tab으로 대상 선택 → Enter로 저장",
                    "대상이 하나이거나 선택한 대상이 있을 때 품질 수치를 표시합니다.",
                ]
            else:
                title = "Quality: no target" if candidate_count == 0 else f"Quality: {candidate_count} candidates"
                rows = [
                    "Show a package to the camera." if candidate_count == 0 else "S: freeze | Tab: select target | Enter: save",
                    "Quality details appear for a single or selected target.",
                ]
            if auto_capture is not None and candidate_count > 1:
                rows = (
                    ["자동 촬영은 후보가 하나일 때만 진행합니다.", "수동 선택: S → Tab으로 대상 선택 → Enter로 저장"]
                    if self.use_korean_labels
                    else ["Automatic capture needs exactly one target.", "Manual selection: S | Tab: next target | Enter: save"]
                )
        else:
            title_color = (255, 190, 80) if report.warnings else (95, 220, 160)
            if self.use_korean_labels:
                candidate = f"후보 #{candidate_number}" if candidate_number is not None else "선택 영역"
                title = f"{candidate} | 품질: {'주의' if report.warnings else '양호'} (실험 기준)"
                rows = [
                    f"크기 {report.width}×{report.height} px | 선명도 {report.sharpness:.1f}",
                    f"평균 밝기 {report.mean_brightness:.1f}/255 | 어두운 부분 {report.dark_ratio:.1%} | 밝은 부분 {report.bright_ratio:.1%}",
                ]
                reasons = [WARNING_MESSAGES.get(reason, reason) for reason in report.warnings]
                if not reasons:
                    rows.append("현재 실험 기준에 해당하는 주의 항목이 없습니다.")
            else:
                candidate = f"Candidate #{candidate_number}" if candidate_number is not None else "Selected area"
                title = f"{candidate} | Experimental: {'CAUTION' if report.warnings else 'OK'}"
                rows = [
                    f"Size {report.width}x{report.height} px | Sharpness {report.sharpness:.1f}",
                    f"Mean brightness {report.mean_brightness:.1f}/255 | Dark {report.dark_ratio:.1%} | Bright {report.bright_ratio:.1%}",
                ]
                english_reasons = {
                    "blur": "Possible blur",
                    "dark": "Possibly too dark",
                    "bright": "Possible overexposure/glare",
                    "small": "Small capture area",
                    "edge": "Margin clipped at frame edge",
                }
                reasons = [english_reasons.get(reason, reason) for reason in report.warnings]
                if not reasons:
                    rows.append("No warnings under the current experimental thresholds.")
            # 최대 다섯 가지 주의 항목을 두 개씩 나눠 좁은 화면에서도 모두 읽게 한다.
            rows.extend(" | ".join(reasons[index:index + 2]) for index in range(0, len(reasons), 2))

        draw.text((12, 12), title, font=self.font, fill=title_color, anchor="lt")
        for index, row in enumerate(rows):
            draw.text((12, 44 + index * 24), row, font=self.font, fill=text_color, anchor="lt")
        footer = (
            "수동 저장 가능 | 안정성·OCR 성공은 별도 검증"
            if self.use_korean_labels
            else "Manual save allowed | Stability and OCR success are not verified"
        )
        if report is None and candidate_count == 0:
            footer = "대상이 없어 저장하지 않습니다." if self.use_korean_labels else "No target: nothing will be saved."
        if auto_capture is not None:
            # 품질 판정과 자동 저장 대기·중복 방지 상태를 구분해 표시한다.
            if self.use_korean_labels:
                messages = {
                    "waiting": "자동: 촬영할 대상 대기",
                    "multiple": "자동: 포장을 하나만 보여주세요.",
                    "quality": "자동: 품질 개선 대기 · 위의 주의 사유를 확인하세요.",
                    "holding": f"자동: 양호 유지 {auto_capture.held_seconds:.1f} / {auto_capture.hold_seconds:.1f}초",
                    "ready": "자동: 저장 준비 중",
                    "locked": "자동: 저장 완료 · 포장을 화면 밖으로 치워주세요.",
                    "absent": f"자동: 다음 촬영 준비 {auto_capture.absent_seconds:.1f} / {auto_capture.rearm_seconds:.1f}초 (대상 없음)",
                }
                footer = "S: 수동 저장 | 저장 완료는 약품 식별 성공이 아님"
            else:
                messages = {
                    "waiting": "Auto: waiting for a target",
                    "multiple": "Auto: show only one package.",
                    "quality": "Auto: waiting for better quality; check warnings above.",
                    "holding": f"Auto: quality held {auto_capture.held_seconds:.1f} / {auto_capture.hold_seconds:.1f}s",
                    "ready": "Auto: ready to save",
                    "locked": "Auto: saved; remove the package from view.",
                    "absent": f"Auto: rearming {auto_capture.absent_seconds:.1f} / {auto_capture.rearm_seconds:.1f}s (no target)",
                }
                footer = "S: manual save | Saving does not confirm medicine identity"
            draw.text((12, 190), messages[auto_capture.status], font=self.font, fill=text_color, anchor="lt")
            draw.text((12, 222), footer, font=self.font, fill=(170, 180, 195), anchor="lt")
        else:
            draw.text((12, 190), footer, font=self.font, fill=(170, 180, 195), anchor="lt")
        return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)

    # 탐지 결과를 다시 추론하지 않고 표시용 복사본에 번호와 선택 상태를 그린다.
    def annotate(
        self,
        frame: np.ndarray,
        detections: list[PackageDetection] | None = None,
        selected_index: int | None = None,
    ) -> np.ndarray:
        if detections is None:
            detections = self.detect(frame)
        if not detections:
            return frame.copy()

        height, width = frame.shape[:2]
        image = self._image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        draw = self._image_draw.Draw(image)
        for index, detection in enumerate(detections):
            x1, y1, x2, y2 = detection.bounds
            name = PACKAGE_CLASSES[detection.class_id][1 if self.use_korean_labels else 0]
            selected = index == selected_index
            label = f"#{index + 1} {name} {detection.confidence:.2f}"
            if selected:
                label = f"{'선택' if self.use_korean_labels else 'Selected'} {label}"
            color = (255, 200, 0) if selected else (0, 220, 120)
            draw.rectangle((x1, y1, x2 - 1, y2 - 1), outline=color, width=3 if selected else 2)
            text_box = draw.textbbox((0, 0), label, font=self.font, anchor="lt")
            label_width, label_height = text_box[2] + 8, text_box[3] + 8
            left = min(x1, max(0, width - label_width))
            top = max(0, y1 - label_height)
            draw.rectangle(
                (left, top, min(width - 1, left + label_width), min(height - 1, top + label_height)),
                fill=color,
            )
            draw.text((left + 4, top + 4), label, font=self.font, fill=(0, 0, 0), anchor="lt")

        return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
