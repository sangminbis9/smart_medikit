"""센서와 영상 증거로 개폐 세션당 촬영을 제한한다."""

import logging
import time
from datetime import datetime, timedelta, timezone

import cv2

from .image_quality import WARNING_MESSAGES
from .vision import (
    CandidateTracker,
    assess_capture_quality,
    capture_thresholds,
    crop_box,
    gray_frame,
    motion_score,
    select_candidate,
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
    def __init__(self, config, camera, board, outbox, detector, *, clock=None):
        self.config = config
        self.rules = config["vision"]
        self.quality_thresholds = {
            (kind, still): capture_thresholds(self.rules, kind, still=still)
            for kind in ("package", "interior")
            for still in (False, True)
        }
        self.quality_log = {}
        self.camera, self.board, self.outbox, self.detector = camera, board, outbox, detector
        self.debounce = Debouncer(config["gpio"]["debounce_seconds"])
        self.clock = clock
        self.pulse_seconds = config["gpio"]["ring_pulse_seconds"]
        self.green_until = None
        self.candidate_signalled = False
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
        self.closed_at = None
        self.was_open = False
        self.tracker = CandidateTracker(
            self.rules["stable_seconds"],
            self.rules["min_iou"],
            config["detector"]["max_age_seconds"],
        )

    # 실기기는 표시 시각을, 영상 재생은 프레임 시각을 기준으로 소등을 예약한다.
    def pulse_green(self, now):
        self.board.set_ring("green")
        self.green_until = (self.clock() if self.clock else now) + self.pulse_seconds

    # 같은 품질 지표를 미리보기와 저장할 사진에서 측정하고 원본 없이 현장 보정 수치를 남긴다.
    def quality_ok(self, kind, frame, box=None, *, still=False):
        key = (kind, still)
        report = assess_capture_quality(
            frame.image, box, self.quality_thresholds[key], self.rules["min_area"]
        )
        previous = self.quality_log.get(key)
        # 촬영본은 매번, 미리보기는 사유 변경 또는 계속되는 실패만 간격을 두어 기록한다.
        if (
            still
            or previous is None
            or (
                (previous[0] != report.warnings and frame.timestamp - previous[1] >= 1)
                or (report.blocking_warnings and frame.timestamp - previous[1] >= 5)
            )
        ):
            LOGGER.info(
                "%s %s 품질: %s · 선명도 %.1f · 평균 밝기 %.1f · 어두운 영역 %.1f%% · "
                "밝은 영역 %.1f%% · %d×%d px",
                "포장" if kind == "package" else "내부",
                "촬영본" if still else "미리보기",
                ", ".join(WARNING_MESSAGES[w] for w in report.warnings) or "양호",
                report.sharpness,
                report.mean_brightness,
                report.dark_ratio * 100,
                report.bright_ratio * 100,
                report.width,
                report.height,
            )
            self.quality_log[key] = (report.warnings, frame.timestamp)
        return not report.blocking_warnings

    # 시작 시 닫힌 빈 작업 세션으로 첫 비교 기준을 확보한다.
    def initialize_baseline(self, now):
        if not self.outbox.staged or self.board.is_open():
            return
        LOGGER.info("약품 보관함 내부 촬영을 시작합니다.")
        self.session_id = self.outbox.begin_session(self.config["box_id"])
        self.state = "CLOSING"
        self.close_started = now
        self.board.set_ring("white")

    # 센서 처리와 촬영을 분리하여 카메라 오류 중에도 닫힘을 기록한다.
    def sensor(self, raw_open: bool, now: float):
        if self.green_until is not None and now >= self.green_until:
            self.green_until = None
            self.board.set_ring(None)
        opened = self.debounce.update(raw_open, now)
        if opened == self.was_open:
            return
        self.was_open = opened
        self.green_until = None
        if opened:
            LOGGER.info("뚜껑이 열렸습니다. 촬영을 시작합니다.", extra={"section_start": True})
            if self.session_id is not None:
                self.finish()
            self.session_id = self.outbox.begin_session(self.config["box_id"])
            self.state = "OPENING"
            self.quality_log.clear()
            self.candidate_signalled = False
            self.previous = self.background = None
            self.background_at = float("-inf")
            self.stable_since = None
            self.detector.reset()
            self.tracker.reset()
            self.board.set_ring(None)
        elif self.session_id is not None:
            LOGGER.info("뚜껑이 닫혔습니다. 보관함 내부 촬영을 시작합니다.", extra={"section_start": True})
            self.closed_at = datetime.now(timezone.utc).isoformat()
            self.state = "CLOSING"
            self.close_started = now
            self.close_attempts = 0
            self.previous = None
            self.stable_since = None
            self.detector.reset()
            self.board.set_ring("white")

    # 새 프레임의 안정성과 최신 탐지 증거가 함께 충족될 때만 촬영한다.
    def frame(self, frame, now: float, *, preview=None):
        # 탐색 단계 이외에는 추론을 새로 요청하지 않고 현재 카메라 영상만 보여준다.
        if preview is not None and self.state not in {"CLOSED", "READY"}:
            preview.publish(frame.image)
        if self.state == "OPENING":
            # 열린 세션의 첫 프레임이 실제로 도착해야 시작 성공을 알린다.
            if not self.board.is_open():
                return
            self.state = "WARMUP"
            self.pulse_green(now)
            return
        if self.state in {"CLOSED", "CAPTURED"} or self.green_until is not None:
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
                LOGGER.info("촬영 준비 완료. 약품 포장을 보여주세요.")
        elif self.state == "READY":
            self.detector.submit(frame.image, now)
            result = self.detector.latest(now)
            if preview is not None:
                # 비동기 검출 박스를 새 프레임에 덧씌우면 이미 움직인 포장의 위치가 틀어진다.
                if (
                    result is not None
                    and result.captured_at >= self.ready_at
                    and result.detections
                    and result.source_frame is not None
                ):
                    preview.publish(result.source_frame, result.detections)
                else:
                    preview.publish(frame.image)
            if result is None or result.captured_at < self.ready_at:
                self.tracker.reset()
                return
            # 준비 전에 제시한 포장을 치우면 빈 장면을 새 배경으로 확보한다.
            if (
                not result.detections
                and result.source_frame is not None
                and self.stable_since is not None
                and result.captured_at > self.background_at
                and result.captured_at - self.stable_since >= self.rules["warmup_seconds"]
            ):
                # 과거 빈 검출을 현재 포장 영상에 적용하면 새 포장이 배경에 흡수된다.
                self.background = gray_frame(
                    result.source_frame, self.rules["analysis_width"]
                ).copy()
                self.background_at = result.captured_at
            # 첫 감지는 결과가 나온 프레임으로 판단해 늦은 검출과 새 움직임을 섞지 않는다.
            detected_gray = gray
            if not self.candidate_signalled and result.detections and result.source_frame is not None:
                detected_gray = gray_frame(result.source_frame, self.rules["analysis_width"])
            candidate = select_candidate(
                result.detections,
                self.background,
                detected_gray,
                minimum_change=self.rules["minimum_change"],
                max_area=self.rules["max_area"],
                pixel_threshold=self.rules["pixel_threshold"],
            )
            if candidate is not None and not self.candidate_signalled:
                # 밝혀진 장면에서 다시 안정화한 뒤 포장을 저장한다.
                self.board.set_ring("yellow")
                self.candidate_signalled = True
                self.previous = self.stable_since = None
                self.tracker.reset()
                self.detector.reset()
                return
            if self.stable_since is None or result.captured_at < self.stable_since:
                self.tracker.reset()
                return
            # 작은 포장의 흔들림은 전체 화면 평균에서 희석되므로 후보 영역도 검사한다.
            if candidate is not None and previous is not None:
                stable = (
                    stable
                    and motion_score(
                        crop_box(previous, candidate.box), crop_box(gray, candidate.box)
                    )
                    <= self.rules["motion_threshold"]
                )
            if candidate is not None and not self.quality_ok("package", frame, candidate.box):
                # 품질 실패 전에 얻은 비동기 검출을 다음 안정 시간에 합산하지 않는다.
                self.stable_since = None
                self.tracker.reset()
                return
            ready = self.tracker.update(candidate, result.captured_at, stable)
            if ready and candidate is not None and now >= self.retry_at and frame.autofocus_ok:
                LOGGER.info(
                    "포장 감지: 신뢰도 %.0f%% · 안정 %.1f초",
                    candidate.confidence * 100,
                    self.tracker.stable_duration,
                )
                if self.capture("package", frame, candidate.box):
                    self.state = "CAPTURED"
                    self.pulse_green(now)
                    self.detector.reset()
                else:
                    self.retry_at = now + self.rules["package_retry_seconds"]
                    self.tracker.reset()
        elif self.state == "CLOSING":
            if not self.quality_ok("interior", frame):
                self.stable_since = None
            settled = (
                self.stable_since is not None
                and now - self.stable_since >= self.rules["close_settle_seconds"]
            )
            if settled and frame.autofocus_ok:
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
            captured_at = None
            if kind == "package" and self.outbox.staged:
                # 프레임을 받은 단조 시각을 실제 시각으로 환산해 저장 시각과 구분한다.
                age = time.monotonic() - photo.timestamp
                captured_at = datetime.now(timezone.utc) - timedelta(seconds=age)
            if self.board.is_open() != expected_open or not photo.autofocus_ok:
                return False
            if not self.quality_ok(kind, photo, box, still=True):
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
            self.outbox.save_capture(self.session_id, kind, encoded.tobytes(), captured_at=captured_at)
            if kind == "package":
                LOGGER.info("약품 포장 감지 및 촬영 성공.")
            else:
                LOGGER.info("보관함 내부 촬영 성공.")
            return True
        except Exception as error:
            # 사진·서버 응답 원문 대신 오류 종류만 기록한다.
            LOGGER.warning("촬영 또는 저장 실패. 카메라와 저장 공간을 확인하세요. (%s)", type(error).__name__)
            return False

    # 내부 촬영 실패를 최초 시도와 최대 두 번의 추가 시도로 제한한다.
    def retry_interior(self, now):
        self.close_attempts += 1
        LOGGER.warning("내부 촬영 조건을 만족하지 못했습니다. (시도 %d)", self.close_attempts)
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
        if (
            self.state == "CLOSING"
            and now - self.close_started >= self.rules["close_attempt_seconds"]
        ):
            self.retry_interior(now)

    # 성공한 포장 사진을 보존하고 내부 실패 사실과 함께 큐를 완결한다.
    def finish(self):
        if self.session_id is not None:
            self.outbox.finish_session(self.session_id, closed_at=self.closed_at)
            self.session_id = None
        self.closed_at = None
        self.state = "CLOSED"
        self.green_until = None
        self.board.set_ring(None)
        self.detector.reset()
