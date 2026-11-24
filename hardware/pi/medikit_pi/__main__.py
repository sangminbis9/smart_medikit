"""Pi 실행, PC 웹캠 탐지, 영상 재생 검증, 모델 준비 명령을 제공한다."""

import argparse
import json
import logging
import os
import threading
import time
from pathlib import Path

from .config import load_config

LOGGER = logging.getLogger(__name__)


class DemoLogFormatter(logging.Formatter):
    # 새 작업 묶음이 시작될 때만 앞에 빈 줄을 넣는다.
    def format(self, record):
        message = super().format(record)
        return f"\n{message}" if getattr(record, "section_start", False) else message


# 카메라 상세 진단은 숨기되 경고와 오류 및 사용자의 진단 설정은 보존한다.
def configure_camera_logging():
    os.environ.setdefault("LIBCAMERA_LOG_LEVELS", "*:WARN")
    level = os.environ.get("MEDIKIT_CAMERA_LOG_LEVEL", "WARNING").upper()
    logging.getLogger("picamera2").setLevel(
        {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING,
         "ERROR": logging.ERROR}.get(level, logging.WARNING)
    )


# 한 자원의 종료 실패가 조명과 카메라 등 나머지 자원 정리를 막지 않게 한다.
def safely_close(resource):
    if resource is not None:
        try:
            resource.close()
        except Exception as error:
            LOGGER.warning("자원 종료 오류: %s", type(error).__name__)


# 세션 저장 오류가 있어도 하드웨어 종료는 계속 수행한다.
def safely_finish(controller):
    if controller is not None:
        try:
            controller.finish()
        except Exception as error:
            LOGGER.warning("세션 종료 오류: %s", type(error).__name__)


# 카메라 정지 실패를 격리하고 다음 시작에서 장치를 다시 열게 한다.
def safely_stop(camera):
    try:
        camera.stop()
    except Exception as error:
        LOGGER.warning("카메라 정지 오류: %s", type(error).__name__)
        safely_close(camera)


# 하위 명령별로 필요한 입력만 받는다.
def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="스마트 약품보관함 자동 촬영")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Pi 카메라·GPIO로 실행")
    run.add_argument(
        "--local-test",
        action="store_true",
        help="새 실험 폴더에서 실제 개폐·촬영을 시험하고 AWS 전송은 끔",
    )
    commands.add_parser("prepare-model", help="사전 학습 모델에 포장 설명을 저장")
    check = commands.add_parser("check-config", help="설정 형식 검사")
    check.add_argument(
        "--hardware", action="store_true", help="GPIO 번호·Pi 모델명·모델 파일도 검사"
    )
    commands.add_parser("check-aws", help="업로드 없이 단기 인증·S3·상태 API 점검")
    camera_check = commands.add_parser(
        "camera-check", help="Pi 카메라에서 포장 탐지와 미리보기 화질을 측정"
    )
    camera_check.add_argument(
        "--samples", type=int, default=20, help="검사할 미리보기 프레임 수 (기본 20)"
    )
    webcam = commands.add_parser("webcam", help="PC 웹캠에서 실시간 포장 후보만 표시")
    webcam.add_argument("--camera", type=int, default=0, help="웹캠 번호")
    webcam.add_argument(
        "--backend",
        choices=("auto", "dshow", "msmf"),
        default="dshow" if os.name == "nt" else "auto",
        help="Windows 기본 dshow, 연결 실패 시 msmf 사용",
    )
    webcam.add_argument("--confidence", type=float, help="탐지 신뢰도 기준(0 초과 1 이하)")
    webcam.add_argument("--interval", type=float, help="추론 요청 최소 간격(초)")
    video = commands.add_parser("video", help="영상 시각과 개폐 기록으로 로컬 검증")
    video.add_argument("--input", required=True, type=Path)
    video.add_argument("--lid-events", required=True, type=Path)
    video.add_argument(
        "--data-dir", required=True, type=Path, help="실기기 큐와 분리한 테스트 저장소"
    )
    video.add_argument("--show", action="store_true", help="미리보기 창 표시")
    video.add_argument("--report", type=Path, help="촬영 상태 변화와 처리시간 보고서")
    args = parser.parse_args(argv)
    if args.command == "camera-check" and args.samples < 1:
        parser.error("--samples는 1 이상이어야 합니다.")
    if args.command == "webcam":
        if args.camera < 0:
            parser.error("웹캠 번호는 0 이상이어야 합니다.")
        if args.confidence is not None and not 0 < args.confidence <= 1:
            parser.error("신뢰도는 0 초과 1 이하이어야 합니다.")
        if args.interval is not None and not 0 < args.interval < float("inf"):
            parser.error("추론 간격은 양의 유한한 값이어야 합니다.")
    return args


# 촬영과 별도로 전송하며 AWS 결과를 로그에 남긴다.
def sync_loop(worker, stop):
    previous_message = None
    while not stop.is_set():
        try:
            status = worker.step(time.monotonic())
            if status:
                messages = {
                    "COMPLETED": "서버 처리 완료.",
                    "REVIEW_REQUIRED": "서버 처리 확인 필요. 웹에서 확인하세요.",
                    "FAILED": "서버 처리 실패. 자동 재시도합니다.",
                    "ERROR": "AWS 전송 또는 상태 확인 실패. 자동 재시도합니다.",
                }
                message = messages.get(status)
                if message and (message != previous_message or status in {"UPLOADED", "COMPLETED", "REVIEW_REQUIRED"}):
                    (LOGGER.warning if status in {"FAILED", "ERROR"} else LOGGER.info)(
                        message,
                        extra={"section_start": status in {"UPLOADED", "COMPLETED", "REVIEW_REQUIRED", "FAILED", "ERROR"}},
                    )
                previous_message = message
        except Exception as error:
            LOGGER.warning("전송 작업 오류: %s", type(error).__name__)
        stop.wait(0.1)


# 시간순 개폐 기록을 검증하고 입력 영상의 시간축에 연결한다.
def load_lid_events(path):
    events = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(events, list) or not events:
        raise ValueError("개폐 기록은 비어 있지 않은 배열이어야 합니다.")
    previous = -1.0
    for event in events:
        timestamp = event.get("at")
        if (
            type(timestamp) not in (int, float)
            or not 0 <= timestamp < float("inf")
            or timestamp <= previous
            or type(event.get("open")) is not bool
        ):
            raise ValueError("개폐 기록은 증가하는 at(초)와 open(불리언)이 필요합니다.")
        previous = timestamp
    return events


# 실제 영상과 실제 YOLOE 검출을 사용하되 AF·센서는 명시적으로 모의 처리한다.
def run_video(config, args):
    import cv2

    from .camera import VideoCamera
    from .controller import CaptureController
    from .detection import SynchronousDetector, YOLOEDetector
    from .gpio import MockBoard
    from .outbox import Outbox

    settings = config["detector"]
    events = load_lid_events(args.lid_events)
    if args.data_dir.resolve() == config["data_dir"]:
        raise ValueError("영상 테스트 저장소는 실기기 저장소와 달라야 합니다.")
    detector = camera = board = outbox = controller = None
    index, frames, transitions = 0, 0, []
    started = time.monotonic()
    LOGGER.info("영상 검증: AF 성공을 모의하며 AWS 전송은 실행하지 않습니다.")
    try:
        detector = SynchronousDetector(
            YOLOEDetector(
                settings["model_path"],
                settings["image_size"],
                settings["confidence"],
                settings["threads"],
                settings["prompts"],
            ),
            settings["interval_seconds"],
            settings["max_age_seconds"],
        )
        camera = VideoCamera(args.input, tuple(config["camera"]["preview_size"]))
        board = MockBoard()
        outbox = Outbox(args.data_dir)
        controller = CaptureController(config, camera, board, outbox, detector)
        camera.start()
        while (frame := camera.read()) is not None:
            now = frame.timestamp
            while index < len(events) and events[index]["at"] <= now:
                board.opened = events[index]["open"]
                index += 1
            before = controller.state
            controller.sensor(board.is_open(), now)
            controller.frame(frame, now)
            frames += 1
            if before != controller.state:
                transitions.append({"at": now, "state": controller.state})
                LOGGER.info("영상 %.2f초: %s", now, controller.state)
            if detector.error:
                raise RuntimeError("YOLOE 추론 실패")
            if args.show:
                cv2.imshow("medikit video", frame.image)
                if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                    break
        # EOF를 실제 닫힘으로 꾸미지 않고 확보한 사진만 불완전 세션으로 보존한다.
        controller.finish()
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                json.dumps(
                    {
                        "frames": frames,
                        "elapsed_seconds": time.monotonic() - started,
                        "autofocus_simulated": True,
                        "aws_enabled": False,
                        "transitions": transitions,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
    finally:
        safely_finish(controller)
        for resource in (camera, detector, board, outbox):
            safely_close(resource)
        if args.show:
            cv2.destroyAllWindows()
    return 0


# 실기기 오류를 제한 시간마다 복구하며 센서와 로컬 큐를 계속 유지한다.
def run_hardware(config):
    # libcamera가 처음 로드되기 전에 로그 기준을 적용한다.
    configure_camera_logging()
    from .camera import PiCamera
    from .controller import CaptureController
    from .detection import AsyncDetector, YOLOEDetector
    from .gpio import Board
    from .outbox import Outbox, SyncWorker
    from .stages import StageWorker, stage_loop
    from .transport import S3Transport

    settings = config["detector"]
    board = camera = outbox = detector = controller = None
    stopped = threading.Event()
    sender = None
    stage_threads = []
    try:
        board = Board(config["gpio"])
        staged = config["aws"].get("staged_capture", False)
        outbox = Outbox(
            config["data_dir"],
            staged=staged,
            ocr_prefix=config["aws"].get("ocr_input_prefix", config["aws"].get("prefix", "ocr-input")),
        )
        camera = PiCamera(config["camera"])
        detector = AsyncDetector(
            YOLOEDetector(
                settings["model_path"],
                settings["image_size"],
                settings["confidence"],
                settings["threads"],
                settings["prompts"],
            ),
            settings["interval_seconds"],
            settings["max_age_seconds"],
        )
        controller = CaptureController(config, camera, board, outbox, detector, clock=time.monotonic)
        if staged:
            controller.initialize_baseline(time.monotonic())
            workers = [StageWorker(outbox, "MOVEMENT_REQUESTED", detector=detector.detector, geometry=config.get('cabinet'))]
            if config["aws"]["enabled"]:
                workers.extend(
                    [
                        StageWorker(outbox, stage, S3Transport(config["aws"]))
                        for stage in ("OCR_REQUESTED", "MOVEMENT_REQUESTED")
                    ]
                )
            for stage_worker in workers:
                thread = threading.Thread(
                    target=stage_loop, args=(stage_worker, stopped), daemon=True
                )
                thread.start()
                stage_threads.append(thread)
        if config["aws"]["enabled"]:
            worker = SyncWorker(
                outbox, S3Transport(config["aws"]), poll_seconds=config["aws"]["poll_seconds"]
            )
            sender = threading.Thread(
                target=sync_loop, args=(worker, stopped), name="aws-sync", daemon=True
            )
            sender.start()
        else:
            LOGGER.info("AWS 미연결: 촬영을 로컬 큐에 보관합니다.")
        running, retry_at, last_detector_error = False, 0.0, None
        while True:
            now = time.monotonic()
            controller.sensor(board.is_open(), now)
            if controller.state == "CLOSED" and running:
                safely_stop(camera)
                running = False
            # 초록 점멸 중에는 다음 프레임을 기다리지 않고 센서와 소등 시각을 확인한다.
            if controller.state == "CLOSED" or controller.green_until is not None:
                time.sleep(0.02)
                continue
            if now < retry_at:
                controller.camera_error(now)
                time.sleep(0.02)
                continue
            try:
                if not running:
                    camera.start()
                    running = True
                frame = camera.read()
            except Exception as error:
                LOGGER.warning("카메라 연결 오류: %s", type(error).__name__)
                controller.camera_error(time.monotonic())
                safely_stop(camera)
                running = False
                retry_at = time.monotonic() + config["camera"]["restart_seconds"]
                continue
            # 카메라 대기 중 변경된 센서를 다시 읽어 닫힌 상태의 포장 촬영을 막는다.
            now = time.monotonic()
            controller.sensor(board.is_open(), now)
            controller.frame(frame, now)
            if detector.error:
                if detector.error != last_detector_error:
                    LOGGER.warning("약품 포장을 감지하지 못했습니다. 모델과 실행 환경을 확인해 주세요. (오류 종류: %s)", detector.error)
                last_detector_error = detector.error
            else:
                last_detector_error = None
    except KeyboardInterrupt:
        LOGGER.info("촬영을 중지하고 로컬 큐를 보존합니다.")
    finally:
        stopped.set()
        safely_finish(controller)
        safely_close(board)
        if sender:
            sender.join(timeout=20)
        for thread in stage_threads:
            thread.join(timeout=20)
        for resource in (detector, camera):
            safely_close(resource)
        if (
            outbox
            and (sender is None or not sender.is_alive())
            and not any(thread.is_alive() for thread in stage_threads)
        ):
            safely_close(outbox)
    return 0


# 실행 모드를 선택하고 민감 응답을 노출하지 않는 오류 요약을 출력한다.
def main(argv=None):
    args = parse_args(argv)
    logging.addLevelName(logging.INFO, "안내")
    logging.addLevelName(logging.WARNING, "확인 필요")
    logging.addLevelName(logging.ERROR, "오류")
    handler = logging.StreamHandler()
    handler.setFormatter(DemoLogFormatter("%(asctime)s [%(levelname)s] %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    try:
        try:
            config = load_config(args.config.resolve())
        except FileNotFoundError:
            LOGGER.error(
                "설정 파일을 찾을 수 없습니다: %s. hardware/pi에서 config.example.toml을 "
                "실행에 사용할 설정 파일로 복사하고 [gpio]의 lid_pin에 센서의 실제 BCM 번호를 입력하세요.",
                args.config,
            )
            return 1

        if args.command == "run" or (args.command == "check-config" and args.hardware):
            from .gpio import validate_pins

            try:
                validate_pins(config["gpio"])
            except ValueError as error:
                # 핀 검사에서 직접 작성한 안내만 표시해 인증 오류 원문은 노출하지 않는다.
                LOGGER.error("Pi 설정 파일 %s의 [gpio]를 확인하세요. %s", args.config, error)
                return 1
        if args.command == "check-config":
            if args.hardware:
                LOGGER.info("실기기 설정 점검: Pi 모델명")
                if not config.get("pi_model") or config["pi_model"] == "unconfirmed":
                    raise ValueError("pi_model에 실제 Pi 모델명을 입력하세요.")
                LOGGER.info("실기기 설정 점검: 준비된 모델 파일")
                if not config["detector"]["model_path"].is_file():
                    raise ValueError("prepare-model로 모델을 먼저 준비하세요.")
            LOGGER.info("설정 형식 확인 완료. GPIO 배선·모델·AWS 연결은 실행 시 별도 확인합니다.")
            return 0
        if args.command == "check-aws":
            from .transport import S3Transport

            S3Transport(config["aws"]).check_connection()
            LOGGER.info("AWS 읽기 점검 완료. 사진 업로드·OCR·입출고 판정은 실물 시험이 필요합니다.")
            return 0
        if args.command == "prepare-model":
            from .detection import prepare_model

            settings = config["detector"]
            prepare_model(settings["source_model"], settings["model_path"], settings["prompts"])
            LOGGER.info("포장 후보 설명을 저장한 모델 준비 완료")
            return 0
        if args.command == "webcam":
            from .webcam import run_webcam

            return run_webcam(config, args)
        if args.command == "camera-check":
            from .webcam import run_camera_check

            configure_camera_logging()
            return run_camera_check(config, args.samples)
        if args.command == "run" and args.local_test:
            import tempfile

            # 촬영 연습용 큐가 다음 운영 실행에서 전송되지 않도록 별도 폴더를 만든다.
            config["aws"]["enabled"] = False
            parent = config["data_dir"].parent
            parent.mkdir(parents=True, exist_ok=True)
            config["data_dir"] = Path(tempfile.mkdtemp(prefix="pi-camera-test-", dir=parent))
            LOGGER.info(
                "로컬 촬영 시험 · AWS 전송 꺼짐 · 사진과 큐 저장 위치: %s", config["data_dir"]
            )
        return run_video(config, args) if args.command == "video" else run_hardware(config)
    except Exception as error:
        LOGGER.error("실행 실패(%s). 설정·모델·장치 연결을 확인하세요.", type(error).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
