"""Pi 실기기 실행, 영상 재생 검증, 모델 준비 명령을 제공한다."""

import argparse
import json
import logging
import queue
import threading
import time
from pathlib import Path

from .config import load_config

LOGGER = logging.getLogger(__name__)


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
    commands.add_parser("run", help="Pi 카메라·GPIO로 실행")
    commands.add_parser("prepare-model", help="사전 학습 모델에 포장 설명을 저장")
    commands.add_parser("check-config", help="설정 형식 검사")
    video = commands.add_parser("video", help="영상 시각과 개폐 기록으로 로컬 검증")
    video.add_argument("--input", required=True, type=Path)
    video.add_argument("--lid-events", required=True, type=Path)
    video.add_argument(
        "--data-dir", required=True, type=Path, help="실기기 큐와 분리한 테스트 저장소"
    )
    video.add_argument("--show", action="store_true", help="미리보기 창 표시")
    video.add_argument("--report", type=Path, help="촬영 상태 변화와 처리시간 보고서")
    return parser.parse_args(argv)


# 비동기 전송 결과를 메인 루프에 전달하고 네트워크 대기를 촬영과 분리한다.
def sync_loop(worker, stop, results):
    while not stop.is_set():
        try:
            status = worker.step(time.monotonic())
            if status:
                results.put(status)
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
    from .camera import PiCamera
    from .controller import CaptureController
    from .detection import AsyncDetector, YOLOEDetector
    from .gpio import Board
    from .outbox import Outbox, SyncWorker
    from .transport import S3Transport

    settings = config["detector"]
    board = camera = outbox = detector = controller = None
    stopped = threading.Event()
    results = queue.SimpleQueue()
    sender = None
    try:
        board = Board(config["gpio"])
        outbox = Outbox(config["data_dir"])
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
        controller = CaptureController(config, camera, board, outbox, detector)
        if config["aws"]["enabled"]:
            worker = SyncWorker(
                outbox, S3Transport(config["aws"]), poll_seconds=config["aws"]["poll_seconds"]
            )
            sender = threading.Thread(
                target=sync_loop, args=(worker, stopped, results), name="aws-sync", daemon=True
            )
            sender.start()
        else:
            LOGGER.info("AWS 미연결: 촬영을 로컬 큐에 보관합니다.")
        running, retry_at = False, 0.0
        while True:
            now = time.monotonic()
            controller.sensor(board.is_open(), now)
            while not results.empty():
                controller.server_status(results.get())
            if controller.state == "CLOSED":
                if running:
                    safely_stop(camera)
                    running = False
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
                board.status("red")
                LOGGER.warning("후보 검출 오류: %s", detector.error)
    except KeyboardInterrupt:
        LOGGER.info("촬영을 중지하고 로컬 큐를 보존합니다.")
    finally:
        stopped.set()
        if sender:
            sender.join(timeout=20)
        safely_finish(controller)
        for resource in (detector, camera, board):
            safely_close(resource)
        if outbox and (sender is None or not sender.is_alive()):
            safely_close(outbox)
    return 0


# 실행 모드를 선택하고 민감 응답을 노출하지 않는 오류 요약을 출력한다.
def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = load_config(args.config.resolve())
        if args.command == "check-config":
            LOGGER.info("설정 형식 확인 완료. GPIO 배선·모델·AWS 연결은 실행 시 별도 확인합니다.")
            return 0
        if args.command == "prepare-model":
            from .detection import prepare_model

            settings = config["detector"]
            prepare_model(settings["source_model"], settings["model_path"], settings["prompts"])
            LOGGER.info("포장 후보 설명을 저장한 모델 준비 완료")
            return 0
        return run_video(config, args) if args.command == "video" else run_hardware(config)
    except Exception as error:
        LOGGER.error("실행 실패(%s). 설정·모델·장치 연결을 확인하세요.", type(error).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
