"""센서와 영상 증거로 개폐 세션당 촬영을 제한한다."""

import logging

import cv2

from .vision import (
    CandidateTracker,
    crop_box,
    gray_frame,
    motion_score,
    select_candidate,
    sharpness,
)

LOGGER = logging.getLogger(__name__)


class Debouncer:
    # 동일 센서 값이 지정 시간 유지될 때만 개폐 상태를 바꾼다.
    def __init__(self, seconds: float):
        self.seconds = seconds
        self.value = False
        self.pending = False
        self.since = 0.0

    # 초기 열림도 안정화 시간을 거쳐 한 번만 전달한다.
    def update(self, raw: bool, now: float) -> bool:
        if raw != self.pending:
            self.pending, self.since = raw, now
        if now - self.since >= self.seconds:
            self.value = self.pending
        return self.value


class CaptureController:
    # 하드웨어와 저장소를 주입받아 실제 시간과 영상 재생 시간을 함께 지원한다.
    def __init__(self, config, camera, board, outbox, detector):
        self.config = config
        self.rules = config["vision"]
        self.camera, self.board, self.outbox, self.detector = camera, board, outbox, detector
        self.debounce = Debouncer(config["gpio"]["debounce_seconds"])
        self.state = "CLOSED"
        self.session_id = None
        self.previous = None
        self.background = None
        self.stable_since = None
        self.ready_at = 0.0
        self.background_at = float("-inf")
        self.retry_at = 0.0
        self.close_started = 0.0
        self.close_attempts = 0
        self.was_open = False
        self.tracker = CandidateTracker(
            self.rules["stable_seconds"],
            self.rules["min_iou"],
            config["detector"]["max_age_seconds"],
        )

    # 센서 처리와 촬영을 분리하여 카메라 오류 중에도 닫힘을 기록한다.
    def sensor(self, raw_open: bool, now: float):
        opened = self.debounce.update(raw_open, now)
        if opened == self.was_open:
            return
        self.was_open = opened
        if opened:
            if self.session_id is not None:
                self.finish()
            self.session_id = self.outbox.begin_session(self.config["box_id"])
            self.state = "WARMUP"
            self.previous = self.background = None
            self.background_at = float("-inf")
            self.stable_since = None
            self.detector.reset()
            self.tracker.reset()
            self.board.status(None)
            self.board.lamp(True)
        elif self.session_id is not None:
            self.state = "CLOSING"
            self.close_started = now
            self.close_attempts = 0
            self.previous = None
            self.stable_since = None
            self.detector.reset()
            self.board.lamp(True)

    # 새 프레임의 안정성과 최신 탐지 증거가 함께 충족될 때만 촬영한다.
    def frame(self, frame, now: float):
        if self.state == "CLOSED":
            return
        gray = gray_frame(frame.image, self.rules["analysis_width"])
        previous = self.previous
        stable = (
            previous is not None and motion_score(previous, gray) <= self.rules["motion_threshold"]
        )
        self.previous = gray
        if stable:
            if self.stable_since is None:
                self.stable_since = now
        else:
            self.stable_since = None
            self.tracker.reset()
        if self.state == "WARMUP":
            if (
                self.stable_since is not None
                and now - self.stable_since >= self.rules["warmup_seconds"]
                and frame.autofocus_ok
            ):
                self.background = gray.copy()
                self.ready_at = now
                self.state = "READY"
                self.board.status("white")
                LOGGER.info("촬영 준비: 미리 제시한 포장은 치웠다가 다시 보여주세요.")
        elif self.state == "READY":
            self.detector.submit(frame.image, now)
            result = self.detector.latest(now)
            if (
                result is None
                or self.stable_since is None
                or result.captured_at < max(self.ready_at, self.stable_since)
            ):
                self.tracker.reset()
                return
            # 준비 전에 제시한 포장을 치우면 빈 장면을 새 배경으로 확보한다.
            if (
                not result.detections
                and result.source_frame is not None
                and result.captured_at > self.background_at
                and result.captured_at - self.stable_since >= self.rules["warmup_seconds"]
            ):
                # 과거 빈 검출을 현재 포장 영상에 적용하면 새 포장이 배경에 흡수된다.
                self.background = gray_frame(
                    result.source_frame, self.rules["analysis_width"]
                ).copy()
                self.background_at = result.captured_at
            candidate = select_candidate(
                result.detections,
                self.background,
                gray,
                minimum_change=self.rules["minimum_change"],
                min_area=self.rules["min_area"],
                max_area=self.rules["max_area"],
                pixel_threshold=self.rules["pixel_threshold"],
            )
            # 작은 포장의 흔들림은 전체 화면 평균에서 희석되므로 후보 영역도 검사한다.
            if candidate is not None and previous is not None:
                stable = (
                    stable
                    and motion_score(
                        crop_box(previous, candidate.box), crop_box(gray, candidate.box)
                    )
                    <= self.rules["motion_threshold"]
                )
            ready = self.tracker.update(candidate, result.captured_at, stable)
            if (
                ready
                and candidate is not None
                and now >= self.retry_at
                and frame.autofocus_ok
                and sharpness(frame.image, candidate.box) >= self.rules["sharpness_threshold"]
            ):
                if self.capture("package", frame, candidate.box):
                    self.state = "CAPTURED"
                    self.board.status("blue")
                    self.detector.reset()
                else:
                    self.retry_at = now + self.rules["package_retry_seconds"]
                    self.tracker.reset()
        elif self.state == "CLOSING":
            settled = (
                self.stable_since is not None
                and now - self.stable_since >= self.rules["close_settle_seconds"]
            )
            if (
                settled
                and frame.autofocus_ok
                and sharpness(frame.image) >= self.rules["sharpness_threshold"]
            ):
                if self.capture("interior", frame):
                    self.finish()
                else:
                    self.retry_interior(now)
            elif now - self.close_started >= self.rules["close_attempt_seconds"]:
                self.retry_interior(now)

    # 고해상도 사진도 초점·선명도·움직임과 현재 센서 상태를 다시 검사한다.
    def capture(self, kind, preview, box=None) -> bool:
        try:
            expected_open = kind == "package"
            if self.board.is_open() != expected_open:
                return False
            photo = self.camera.capture()
            if self.board.is_open() != expected_open or not photo.autofocus_ok:
                return False
            if sharpness(photo.image, box) < self.rules["still_sharpness_threshold"]:
                return False
            before = gray_frame(preview.image, self.rules["analysis_width"])
            after = gray_frame(photo.image, before.shape[1])
            # 세로 영상 축소에서 생긴 한 픽셀 반올림 차이도 동일 좌표로 비교한다.
            if after.shape != before.shape:
                after = cv2.resize(after, (before.shape[1], before.shape[0]))
            if motion_score(before, after) > self.rules["capture_motion_threshold"]:
                return False
            if (
                box is not None
                and motion_score(crop_box(before, box), crop_box(after, box))
                > self.rules["capture_motion_threshold"]
            ):
                return False
            ok, encoded = cv2.imencode(
                ".jpg",
                photo.image,
                [cv2.IMWRITE_JPEG_QUALITY, self.config["camera"]["jpeg_quality"]],
            )
            if not ok:
                raise OSError("JPEG 변환 실패")
            self.outbox.save_capture(self.session_id, kind, encoded.tobytes())
            LOGGER.info("로컬 %s 촬영 저장 완료", kind)
            return True
        except Exception as error:
            # 사진·서버 응답 원문 대신 오류 종류만 기록한다.
            LOGGER.warning("촬영 실패: %s", type(error).__name__)
            self.board.status("red")
            return False

    # 내부 촬영 실패를 최초 시도와 최대 두 번의 추가 시도로 제한한다.
    def retry_interior(self, now):
        self.close_attempts += 1
        self.board.status("red")
        if self.close_attempts > self.rules["close_retries"]:
            self.finish()
        else:
            self.close_started = now
            self.stable_since = None
            self.previous = None

    # 카메라 단절 중에도 내부 촬영을 무한 대기하지 않도록 종료한다.
    def camera_error(self, now):
        self.previous = None
        self.stable_since = None
        self.tracker.reset()
        self.detector.reset()
        self.board.status("red")
        if (
            self.state == "CLOSING"
            and now - self.close_started >= self.rules["close_attempt_seconds"]
        ):
            self.retry_interior(now)

    # 성공한 포장 사진을 보존하고 내부 실패 사실과 함께 큐를 완결한다.
    def finish(self):
        if self.session_id is not None:
            self.outbox.finish_session(self.session_id)
            self.session_id = None
        self.state = "CLOSED"
        self.board.lamp(False)
        self.detector.reset()

    # 서버 종료 결과는 로컬 촬영 안내를 가리지 않는 닫힌 상태에 표시한다.
    def server_status(self, status):
        if self.state == "CLOSED":
            color = {
                "COMPLETED": "green",
                "REVIEW_REQUIRED": "yellow",
                "FAILED": "red",
                "ERROR": "red",
            }.get(status)
            if color:
                self.board.status(color)
