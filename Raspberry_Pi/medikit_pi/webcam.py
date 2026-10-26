"""PC 웹캠의 현재 영상과 YOLOE 후보 검출을 저장 없이 표시한다."""

import logging
import time

import cv2
import numpy as np

from .detection import AsyncDetector, YOLOEDetector

LOGGER = logging.getLogger(__name__)
WINDOW_NAME = "smart_medikit - YOLOE"


# 최신 영상과 검출의 원본 영상을 나란히 표시하여 지연된 상자를 오버레이하지 않는다.
def render_preview(frame, result):
    height, width = frame.shape[:2]
    detected = np.zeros_like(frame)
    if result is not None and result.source_frame is not None:
        detected = result.source_frame.copy()
        result_height, result_width = detected.shape[:2]
        for candidate in result.detections:
            x1, y1, x2, y2 = candidate.box
            left, top = max(0, int(x1 * result_width)), max(0, int(y1 * result_height))
            right, bottom = (
                min(result_width - 1, int(x2 * result_width)),
                min(result_height - 1, int(y2 * result_height)),
            )
            cv2.rectangle(detected, (left, top), (right, bottom), (0, 210, 255), 2)
            # 모델이 반환한 영문 분류명과 점수이며 제품명이나 식별 확정값이 아니다.
            label = f"{candidate.label} {candidate.confidence:.2f}"
            cv2.putText(
                detected,
                label,
                (left, max(18, top - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 210, 255),
                1,
                cv2.LINE_AA,
            )
        if detected.shape != frame.shape:
            detected = cv2.resize(detected, (width, height))
    return np.hstack((frame, detected))


# 카메라와 추론 작업자만 실행하며 촬영 상태 머신·GPIO·AWS에는 연결하지 않는다.
def run_webcam(config, args):
    settings = config["detector"]
    capture, worker, window_created = None, None, False
    try:
        confidence = args.confidence if args.confidence is not None else settings["confidence"]
        interval = args.interval if args.interval is not None else settings["interval_seconds"]
        model = YOLOEDetector(
            settings["model_path"],
            settings["image_size"],
            confidence,
            settings["threads"],
            settings["prompts"],
        )
        capture = cv2.VideoCapture()
        backends = {"auto": cv2.CAP_ANY, "dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF}
        if not capture.open(args.camera, backends[args.backend]):
            LOGGER.error(
                "카메라 %s번을 열 수 없습니다. --camera 1 또는 --backend msmf와 카메라 권한을 확인하세요.",
                args.camera,
            )
            return 1
        width, height = config["camera"]["preview_size"]
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        worker = AsyncDetector(model, interval, settings["max_age_seconds"])
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
        window_created = True
        LOGGER.info(
            "왼쪽: 현재 영상 / 오른쪽: 검출한 원본 영상과 후보 분류·점수. 검은 영역은 결과 대기 또는 지연입니다."
        )
        LOGGER.info(
            "Q·Esc·창 닫기로 종료합니다. 포장 후보 탐지이며 약품명 식별은 아닙니다. 사진 저장·자동 촬영·AWS 전송은 없습니다."
        )
        while True:
            received, frame = capture.read()
            if not received or frame is None or frame.size == 0:
                LOGGER.error("새 카메라 영상을 받지 못했습니다. 연결 후 다시 실행하세요.")
                return 1
            # 장치가 요청 해상도를 무시해도 추론용 프레임 복사와 표시 비용을 제한한다.
            scale = min(1.0, width / frame.shape[1], height / frame.shape[0])
            if scale < 1:
                frame = cv2.resize(
                    frame,
                    (max(1, round(frame.shape[1] * scale)), max(1, round(frame.shape[0] * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            now = time.monotonic()
            worker.submit(frame, now)
            result = worker.latest(now)
            if worker.error:
                LOGGER.error("후보 검출 실패(%s). 모델과 실행 환경을 확인하세요.", worker.error)
                return 1
            cv2.imshow(WINDOW_NAME, render_preview(frame, result))
            if cv2.waitKey(1) & 0xFF in (ord("q"), ord("Q"), 27):
                return 0
            if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                return 0
    except FileNotFoundError:
        LOGGER.error("준비된 모델이 없습니다. 같은 설정으로 prepare-model을 먼저 실행하세요.")
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        if capture is not None:
            capture.release()
        if worker is not None:
            worker.close()
        if window_created:
            try:
                cv2.destroyWindow(WINDOW_NAME)
            except cv2.error:
                # 사용자가 먼저 닫은 창은 다시 해제할 필요가 없다.
                pass
