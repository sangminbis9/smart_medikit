"""사전 학습 YOLOE 포장 후보 검출과 최신 프레임 추론을 담당한다."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class Detection:
    box: tuple[float, float, float, float]
    confidence: float
    label: str


@dataclass(frozen=True)
class DetectionResult:
    captured_at: float
    detections: tuple[Detection, ...]
    source_frame: np.ndarray | None = field(default=None, compare=False, repr=False)


class Detector(Protocol):
    # BGR 영상에서 정규화 좌표를 가진 포장 후보를 반환한다.
    def predict(self, frame: np.ndarray) -> list[Detection]: ...


# 사전 학습 모델에 포장 설명을 한 번 적용하여 오프라인 추론용으로 저장한다.
def prepare_model(source: str, output: Path, prompts: list[str]) -> Path:
    if not prompts or any(not isinstance(item, str) or not item.strip() for item in prompts):
        raise ValueError("포장 후보 설명을 하나 이상 설정해야 합니다.")
    if len(set(prompts)) != len(prompts):
        raise ValueError("포장 후보 설명은 중복될 수 없습니다.")
    output = Path(output).resolve()
    if output.suffix != ".pt":
        raise ValueError("준비 모델의 확장자는 .pt여야 합니다.")
    if Path(source).exists() and Path(source).resolve() == output:
        raise ValueError("원본 모델과 준비 모델의 경로를 분리해야 합니다.")
    from ultralytics import YOLOE

    model = YOLOE(source)
    model.to("cpu")
    # 포장 형태 설명만 사용하며 한글 약품명 식별은 AWS OCR의 책임이다.
    model.set_classes(prompts)
    output.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(output))
    return output


class YOLOEDetector:
    # 준비된 로컬 모델과 CPU 추론 예산을 적용한다.
    def __init__(
        self,
        model_path: str | Path,
        image_size: int = 320,
        confidence: float = 0.25,
        threads: int = 2,
        prompts: list[str] | None = None,
    ) -> None:
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError("준비된 모델이 없습니다. prepare-model을 먼저 실행하세요.")
        if image_size < 32 or image_size % 32 or not 0 < confidence <= 1 or threads < 1:
            raise ValueError("YOLO 입력 크기·신뢰도·CPU 스레드 설정을 확인하세요.")
        import torch
        from ultralytics import YOLOE

        torch.set_num_threads(threads)
        self.model = YOLOE(str(path.resolve()))
        self.model.to("cpu")
        # 텍스트 인코더를 실행 중 다시 내려받지 않도록 준비 임베딩을 요구한다.
        if getattr(self.model.model, "pe", None) is None:
            raise ValueError("포장 설명이 준비되지 않은 모델입니다. prepare-model을 실행하세요.")
        names = self.model.names
        labels = list(names.values()) if isinstance(names, dict) else list(names)
        if prompts is not None and labels != prompts:
            raise ValueError("설정의 포장 설명과 모델이 다릅니다. prepare-model을 다시 실행하세요.")
        self.image_size = image_size
        self.confidence = confidence

    # 전체 화면을 CPU로 추론하고 결과를 카메라 해상도와 무관한 좌표로 반환한다.
    def predict(self, frame: np.ndarray) -> list[Detection]:
        result = self.model.predict(
            source=frame,
            imgsz=self.image_size,
            conf=self.confidence,
            device="cpu",
            max_det=12,
            retina_masks=False,
            verbose=False,
            save=False,
        )[0]
        detections = []
        if result.boxes is None:
            return detections
        boxes = result.boxes.xyxyn.cpu().numpy()
        scores = result.boxes.conf.cpu().numpy()
        classes = result.boxes.cls.cpu().numpy()
        for box, score, class_id in zip(boxes, scores, classes, strict=True):
            coordinates = tuple(float(value) for value in np.clip(box, 0, 1))
            detections.append(Detection(coordinates, float(score), result.names[int(class_id)]))
        return detections


class AsyncDetector:
    # 대기 프레임을 하나로 제한한 추론 작업자를 시작한다.
    def __init__(
        self,
        detector: Detector,
        interval_seconds: float = 0.4,
        max_age_seconds: float = 1.0,
    ) -> None:
        if interval_seconds <= 0 or max_age_seconds <= 0:
            raise ValueError("추론 주기와 결과 유효시간은 양수여야 합니다.")
        self.detector = detector
        self.interval_seconds = interval_seconds
        self.max_age_seconds = max_age_seconds
        self._condition = threading.Condition()
        self._pending: tuple[int, float, np.ndarray] | None = None
        self._result: DetectionResult | None = None
        self._last_submitted = float("-inf")
        self._generation = 0
        self._closed = False
        self._error: str | None = None
        self._thread = threading.Thread(target=self._run, name="yoloe-inference", daemon=True)
        self._thread.start()

    # 설정 주기마다 최신 영상 하나를 복사하여 오래된 대기 영상을 교체한다.
    def submit(self, frame: np.ndarray, now: float) -> None:
        with self._condition:
            if self._closed or now - self._last_submitted < self.interval_seconds:
                return
            self._last_submitted = now
            self._pending = (self._generation, now, frame.copy())
            self._condition.notify()

    # 촬영 시각 기준으로 오래된 추론 결과를 제외한다.
    def latest(self, now: float) -> DetectionResult | None:
        with self._condition:
            result = self._result
            if result is None or not 0 <= now - result.captured_at <= self.max_age_seconds:
                return None
            return result

    # 새 세션 또는 카메라 재시작 시 이전 추론의 결과를 무효화한다.
    def reset(self) -> None:
        with self._condition:
            self._generation += 1
            self._pending = None
            self._result = None
            self._error = None
            self._last_submitted = float("-inf")

    # 모델 오류 종류만 전달하고 영상이나 모델 응답은 노출하지 않는다.
    @property
    def error(self) -> str | None:
        with self._condition:
            return self._error

    # 추론 중인 프레임이 끝난 뒤 작업자를 정리한다.
    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify_all()
        self._thread.join(timeout=5)

    # GPIO와 OpenCV 루프를 막지 않고 최신 포장 후보를 계산한다.
    def _run(self) -> None:
        while True:
            with self._condition:
                while self._pending is None and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                generation, captured_at, frame = self._pending
                self._pending = None
            try:
                detections = tuple(self.detector.predict(frame))
                failure = None
            except Exception as error:  # noqa: BLE001 - 모델 오류가 촬영 루프를 종료하지 않도록 격리한다.
                detections = ()
                failure = type(error).__name__
            with self._condition:
                # 늦게 완료된 이전 세션의 검출로 새 세션을 촬영하지 않는다.
                if generation == self._generation and not self._closed:
                    self._error = failure
                    self._result = DetectionResult(
                        captured_at, detections, frame if failure is None else None
                    )


class SynchronousDetector:
    # 파일 영상의 시간축을 유지하는 결정적 추론 스케줄러를 만든다.
    def __init__(
        self,
        detector: Detector,
        interval_seconds: float = 0.4,
        max_age_seconds: float = 1.0,
    ) -> None:
        if interval_seconds <= 0 or max_age_seconds <= 0:
            raise ValueError("추론 주기와 결과 유효시간은 양수여야 합니다.")
        self.detector = detector
        self.interval_seconds = interval_seconds
        self.max_age_seconds = max_age_seconds
        self.reset()

    # 영상 시각 기준 주기에 도달한 프레임만 추론한다.
    def submit(self, frame: np.ndarray, now: float) -> None:
        if now - self._last_submitted < self.interval_seconds:
            return
        self._last_submitted = now
        try:
            self._result = DetectionResult(now, tuple(self.detector.predict(frame)), frame.copy())
            self.error = None
        except Exception as error:  # noqa: BLE001 - 영상 검증에서 모델 오류 종류만 전달한다.
            self._result = None
            self.error = type(error).__name__

    # 영상 시각에서 유효한 최신 검출만 반환한다.
    def latest(self, now: float) -> DetectionResult | None:
        if self._result is None or not 0 <= now - self._result.captured_at <= self.max_age_seconds:
            return None
        return self._result

    # 새 세션의 프레임이 이전 검출을 재사용하지 않도록 초기화한다.
    def reset(self) -> None:
        self._last_submitted = float("-inf")
        self._result: DetectionResult | None = None
        self.error: str | None = None

    # 파일 영상 모드의 추론 결과를 해제한다.
    def close(self) -> None:
        self.reset()
