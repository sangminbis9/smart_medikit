"""터미널 개폐 입력으로 포장·내부 촬영을 같은 로컬 세션에 보관한다."""

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from queue import Empty, Queue
import sys
from threading import Thread
import time
from uuid import uuid4

import cv2
import numpy as np
from PIL import Image, ImageDraw

if __package__:
    from . import camera_preview as preview
    from .auto_capture import AutoCaptureGate
    from .image_quality import QualityThresholds, WARNING_MESSAGES, assess_photo
    from .interior_capture import InteriorCaptureGate
else:
    import camera_preview as preview
    from auto_capture import AutoCaptureGate
    from image_quality import QualityThresholds, WARNING_MESSAGES, assess_photo
    from interior_capture import InteriorCaptureGate


# 로컬 촬영 기록에 사용할 시간대가 명시된 시각을 만든다.
def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class CaptureSession:
    # 세션마다 독립 폴더를 만들고 아직 확보하지 않은 사진을 명시한다.
    def __init__(self, directory: Path, args) -> None:
        session_id = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex}"
        self.directory = directory / session_id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.data = {
            "format_version": 1, "local_only": True, "session_id": session_id,
            "status": "active", "started_at": timestamp(), "closed_at": None,
            "finished_at": None, "reason": None, "package": None, "interior": None,
            "settings": {
                "rotation": args.rotate,
                "package_min_sharpness": args.min_sharpness,
                "package_hold_seconds": args.hold_seconds,
                "interior_min_sharpness": args.interior_min_sharpness,
                "interior_hold_seconds": args.interior_hold_seconds,
                "interior_timeout": args.interior_timeout,
            },
        }
        self.persist()

    # 임시 파일을 교체해 중간에 잘린 JSON이 기존 기록을 덮어쓰지 않게 한다.
    def persist(self) -> None:
        temporary = self.directory / "session.json.tmp"
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.directory / "session.json")

    # 표시가 없는 사진과 같은 프레임의 검사 결과를 보관한다.
    def save(self, kind: str, frame: np.ndarray, report) -> Path:
        if kind not in ("package", "interior") or self.data[kind] is not None:
            raise ValueError("사진 종류가 잘못되었거나 이미 저장한 사진입니다.")
        path = preview.save_frame(frame, self.directory / kind, prefix=kind)
        self.data[kind] = {
            "path": path.relative_to(self.directory).as_posix(),
            "captured_at": timestamp(), "quality": asdict(report),
        }
        self.persist()
        return path

    # 확보한 사진을 보존하고 로컬 촬영의 완료 여부만 기록한다.
    def finish(self, reason: str) -> None:
        complete = reason == "captured" and all(self.data[kind] is not None for kind in ("package", "interior"))
        self.data.update(status="complete" if complete else "incomplete", reason=reason, finished_at=timestamp())
        self.persist()


class LidCaptureController:
    # 실제 장치와 분리한 개폐 상태 및 두 종류의 촬영 조건을 준비한다.
    def __init__(self, args, detector, directory: Path) -> None:
        self.args = args
        self.detector = detector
        self.directory = directory
        self.thresholds = QualityThresholds(min_sharpness=args.min_sharpness)
        self.package_gate = AutoCaptureGate(args.hold_seconds, args.rearm_seconds)
        self.interior_gate = InteriorCaptureGate(args.interior_hold_seconds, args.interior_min_sharpness)
        self.session: CaptureSession | None = None
        self.phase = "idle"
        self.lid_open = False
        self.running = True
        self.revision = 0
        self.closed_at: float | None = None

    # 논리 개폐 입력을 처리하며 중복 신호로 세션을 반복 생성하지 않는다.
    def handle_command(self, command: str, now: float) -> None:
        command = command.strip().lower()
        if command == "q":
            self.running = False
            self.finish("interrupted")
        elif command == "1" and not self.lid_open:
            # 다시 연 장면을 이전 세션의 내부 사진으로 연결하지 않는다.
            self.finish("reopened_before_interior")
            self.session = CaptureSession(self.directory, self.args)
            self.package_gate = AutoCaptureGate(self.args.hold_seconds, self.args.rearm_seconds)
            self.phase = "package"
            self.lid_open = True
            self.revision += 1
            print(f"열림: 카메라 준비 후 포장을 보여주세요. 세션 {self.session.data['session_id']}")
        elif command == "0" and self.lid_open:
            self.lid_open = False
            self.revision += 1
            if self.session is not None:
                self.session.data["closed_at"] = timestamp()
                self.session.persist()
                self.phase = "interior"
                self.closed_at = now
                self.interior_gate.reset_pending()
                print("닫힘: 전체 화면의 움직임·밝기 안정 대기. 내부 조명은 직접 켜 주세요(제어 미연결).")
                if self.session.data["package"] is None:
                    print("포장 사진이 없습니다. 내부 사진만 확보하고 누락을 기록합니다.")
        elif command not in ("0", "1", ""):
            print("PowerShell 입력: 1 + Enter = 열림, 0 + Enter = 닫힘, q + Enter = 종료")

    # 중단 또는 완료된 세션을 기록한 뒤 다음 열림을 기다린다.
    def finish(self, reason: str) -> None:
        if self.session is not None:
            self.session.finish(reason)
            status = "완료" if self.session.data["status"] == "complete" else "미완료"
            print(f"로컬 촬영 {status}: {self.session.directory}")
            self.session = None
        self.phase = "idle"
        self.closed_at = None
        self.revision += 1

    # 현재 단계의 원본만 검사하고 저장 후보와 표시용 영상을 분리한다.
    def analyze(self, frame: np.ndarray, now: float):
        if self.phase == "package":
            detections = self.detector.detect(frame)
            qualities = [
                assess_photo(item.crop(frame), padding_clipped=item.padding_clipped, thresholds=self.thresholds)
                for item in detections
            ]
            ready = self.package_gate.update(detections, qualities, now)
            report = qualities[0] if len(qualities) == 1 else None
            candidate = detections[0].crop(frame) if ready else None
            return self.detector.annotate(frame, detections), candidate, report
        if self.phase == "interior":
            # 빈 보관함도 유효하므로 대상 탐지나 박스 자르기를 요구하지 않는다.
            ready = self.interior_gate.update(frame, now)
            return frame, frame if ready else None, self.interior_gate.report
        return frame, None, None

    # 저장 직전 상태가 바뀌지 않았을 때만 사진을 확정하고 다음 단계로 이동한다.
    def save_candidate(self, frame: np.ndarray, report, revision: int) -> bool:
        if revision != self.revision or self.session is None or self.phase not in ("package", "interior"):
            return False
        kind = self.phase
        path = self.session.save(kind, frame, report)
        print(f"{'포장' if kind == 'package' else '내부 전체'} 사진 저장: {path}")
        if kind == "package":
            # 개폐 한 번에 포장 한 장만 허용하며 대상이 사라져도 재무장하지 않는다.
            self.phase = "wait_close"
            self.revision += 1
            print("포장 촬영 완료. 이제 닫힘을 시험하려면 PowerShell에 0 입력 후 Enter를 누르세요.")
        else:
            self.finish("captured")
        return True

    # 제한 시간 안에 내부 촬영 조건이 성립하지 않으면 누락을 남기고 대기한다.
    def check_timeout(self, now: float) -> None:
        if self.phase == "interior" and self.closed_at is not None:
            if now - self.closed_at >= self.args.interior_timeout:
                print("내부 촬영 시간 초과: 사진을 억지로 저장하지 않고 미완료로 보관합니다.")
                self.finish("interior_timeout")


# 영상 처리와 별도로 터미널 줄 입력을 받아 종료도 같은 경로로 전달한다.
def read_terminal_commands(commands: Queue, stream) -> None:
    try:
        for line in stream:
            command = line.strip().lower()
            commands.put(command)
            if command == "q":
                return
    except (OSError, ValueError):
        print("터미널 입력이 끊겨 종료합니다.", file=sys.stderr)
    finally:
        # EOF를 닫힘으로 해석해 열린 장면을 내부 사진으로 저장하지 않는다.
        commands.put("q")


# 대기 중 입력을 순서대로 적용해 추론 중 도착한 신호도 저장 전에 반영한다.
def apply_commands(controller: LidCaptureController, commands: Queue) -> None:
    while controller.running:
        try:
            command = commands.get_nowait()
        except Empty:
            return
        controller.handle_command(command, time.monotonic())


# 세션 단계와 검사 수치를 사진 밖의 안내 패널로 표시한다.
def make_session_panel(controller: LidCaptureController, report) -> np.ndarray:
    image = Image.new("RGB", (800, 200), (25, 29, 36))
    draw = ImageDraw.Draw(image)
    korean = controller.detector.use_korean_labels
    gate = controller.interior_gate if controller.phase == "interior" else controller.package_gate
    if korean:
        titles = {"package": "열림 · 포장 자동 촬영 대기", "wait_close": "포장 저장 완료 · 닫힘 대기", "interior": "닫힘 · 내부 전체 촬영 대기"}
        lines = [titles.get(controller.phase, "대기"), "PowerShell에서 1/0/q 입력 후 Enter | S·R 수동 조작은 사용하지 않음"]
        if controller.phase == "package":
            lines.append(f"포장 하나만 제시 | 조건 유지 {gate.held_seconds:.1f} / {gate.hold_seconds:.1f}초")
        elif controller.phase == "interior":
            lines.append(f"전체 화면 안정 {gate.held_seconds:.1f} / {gate.hold_seconds:.1f}초 | 선명도 기준 {controller.args.interior_min_sharpness:g}")
        else:
            lines.append("포장을 계속 보여줘도 추가 저장하지 않습니다. 0 입력으로 닫힘을 시험하세요.")
        if report is not None:
            lines.append(f"선명도 {report.sharpness:.1f} | 평균 밝기 {report.mean_brightness:.1f} | {report.width}×{report.height} px")
            reasons = ", ".join(WARNING_MESSAGES[item] for item in report.warnings)
            lines.append("주의: " + (reasons or "현재 검사 항목 없음 (OCR 성공 보장 아님)"))
    else:
        lines = [f"Lid capture: {controller.phase}", "Terminal: 1/0/q then Enter | S/R disabled"]
        lines.append(f"Held: {gate.held_seconds:.1f} / {gate.hold_seconds:.1f}s | one package per opening")
        if report is not None:
            lines.append(f"Sharpness: {report.sharpness:.1f} | Brightness: {report.mean_brightness:.1f}")
            lines.append(f"Warnings: {', '.join(report.warnings) or 'none'} | OCR success is not guaranteed")
    for index, line in enumerate(lines):
        draw.text((12, 12 + 34 * index), line, font=controller.detector.font, fill=(230, 234, 240))
    return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)


# 개폐 상태에 맞춰 카메라를 열고 닫으며 두 장의 로컬 촬영을 실행한다.
def run_lid_simulation(args, detector, commands: Queue | None = None, directory: Path | None = None) -> int:
    controller = LidCaptureController(args, detector, directory or preview.CAPTURE_DIRECTORY / "sessions")
    if commands is None:
        commands = Queue()
        Thread(target=read_terminal_commands, args=(commands, sys.stdin), daemon=True).start()
    capture = None
    window_created = False
    rotation_codes = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
    print("개폐 시험 준비 완료. 카메라를 닫은 상태로 기다립니다.")
    print("PowerShell에서 1 + Enter: 열림 / 0 + Enter: 닫힘 / q + Enter: 종료")
    print("개인정보 없는 테스트 포장만 사용하세요. 실제 센서·LED·AWS는 연결하지 않았습니다.")
    print(f"포장 선명도 기준 {args.min_sharpness:g}, 내부 기준 {args.interior_min_sharpness:g} (0이면 초점 차단 없음).")
    try:
        while controller.running:
            apply_commands(controller, commands)
            controller.check_timeout(time.monotonic())
            if not controller.running or controller.phase == "idle":
                if capture is not None:
                    capture.release()
                    capture = None
                if window_created:
                    cv2.destroyWindow(preview.WINDOW_NAME)
                    window_created = False
                if not controller.running:
                    break
                try:
                    command = commands.get(timeout=0.1)
                except Empty:
                    continue
                controller.handle_command(command, time.monotonic())
                continue

            if capture is None:
                capture = cv2.VideoCapture()
                if not capture.open(args.camera, preview.BACKENDS[args.backend]):
                    raise OSError(f"카메라 {args.camera}번을 열지 못했습니다. DroidCam 연결과 --camera/--backend를 확인하세요.")
                cv2.namedWindow(preview.WINDOW_NAME, cv2.WINDOW_AUTOSIZE)
                window_created = True
                print("카메라 연결 완료. 새 프레임부터 촬영 조건을 검사합니다.")

            revision = controller.revision
            received, frame = capture.read()
            observed_at = time.monotonic()
            if not received or frame is None or frame.size == 0:
                raise OSError("새 영상을 받지 못했습니다. DroidCam 연결을 확인하세요.")
            if args.rotate:
                frame = cv2.rotate(frame, rotation_codes[args.rotate])
            apply_commands(controller, commands)
            if revision != controller.revision or not controller.running:
                continue
            display, candidate, report = controller.analyze(frame, observed_at)
            # 느린 탐지 중 개폐·종료가 입력되었다면 이전 단계의 결과를 버린다.
            apply_commands(controller, commands)
            controller.check_timeout(time.monotonic())
            if revision != controller.revision or not controller.running:
                continue
            preview.show_preview(display, make_session_panel(controller, report))
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), ord("Q"), 27) or cv2.getWindowProperty(preview.WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                controller.handle_command("q", time.monotonic())
                break
            apply_commands(controller, commands)
            if candidate is not None:
                controller.save_candidate(candidate, report, revision)
    except KeyboardInterrupt:
        print("개폐 시험을 중단합니다. 확보한 사진은 보존합니다.")
        return 0
    except Exception as error:
        print(f"개폐 촬영 오류로 중단합니다: {error}", file=sys.stderr)
        try:
            controller.finish("error")
        except OSError as record_error:
            print(f"세션 기록도 갱신하지 못했습니다. 기존 사진과 기록을 확인하세요: {record_error}", file=sys.stderr)
        return 1
    finally:
        if capture is not None:
            capture.release()
        if window_created:
            try:
                cv2.destroyWindow(preview.WINDOW_NAME)
            except cv2.error:
                pass
        if controller.session is not None:
            try:
                controller.finish("interrupted")
            except OSError as error:
                print(f"중단 기록 실패: {error}. 이미 저장한 사진은 그대로 남아 있습니다.", file=sys.stderr)
    return 0
