"""루프백 HTTP로 Pi 미리보기와 같은 프레임의 포장 검출 상자를 전송한다."""

import logging
import threading
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

LOGGER = logging.getLogger(__name__)
DEFAULT_PORT = 8765
KOREAN_LABELS = {
    "medicine box": "약품 상자",
    "prescription medicine bag": "처방약 봉투",
    "medicine sachet": "소형 약품 포장",
    "pill blister pack": "알약 블리스터 포장",
    "medicine bottle": "약품 병",
    "ointment tube": "연고 튜브",
    "medical patch package": "파스 포장",
    "paper document": "문서 후보",
}
PAGE = """<!doctype html>
<html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>보관함 카메라</title>
<style>
body{margin:0;background:#171a1d;color:#f1f3f5;font:16px system-ui,sans-serif}
main{max-width:960px;margin:5vh auto;padding:0 16px}h1{font-size:1.4rem}
img{width:100%;display:block;aspect-ratio:16/9;object-fit:contain;background:#101316}
</style>
<main><h1>보관함 카메라</h1><img src="/stream.mjpg" alt="실시간 카메라 영상">
<p>초록 박스 옆에 감지된 포장 종류가 표시됩니다. Ⅱ 표시는 카메라가 영상을 기다리는 상태입니다.</p></main></html>""".encode()


# 한글을 지원하는 시스템 글꼴을 우선하고, 없으면 영문 범주명을 그릴 글꼴을 고른다.
@lru_cache(maxsize=1)
def _label_font():
    for name in (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf",
        "C:/Windows/Fonts/malgun.ttf",
    ):
        if Path(name).is_file():
            try:
                return ImageFont.truetype(name, 16), True
            except OSError:
                continue
    try:
        return ImageFont.truetype("DejaVuSans.ttf", 16), False
    except OSError:
        return ImageFont.load_default(), False


# 포장 종류별 글자 이미지를 재사용해 프레임마다 같은 글자를 다시 그리지 않는다.
@lru_cache(maxsize=64)
def _label_tile(label):
    font, korean = _label_font()
    text = KOREAN_LABELS.get(label, label) if korean else label
    left, top, right, bottom = font.getbbox(text)
    tile = Image.new("L", (right - left + 8, bottom - top + 6), 28)
    ImageDraw.Draw(tile).text((4 - left, 3 - top), text, font=font, fill=255)
    return np.asarray(tile)


# 표시용 복사본에 포장 박스와 분류명을 그리며 영상이 없을 때는 대기 기호를 보낸다.
def _jpeg(frame, detections):
    if frame is None:
        image = np.full((180, 320, 3), 24, np.uint8)
        cv2.rectangle(image, (145, 72), (154, 108), (154, 154, 154), -1)
        cv2.rectangle(image, (166, 72), (175, 108), (154, 154, 154), -1)
    else:
        image = frame.copy() if detections else frame
        height, width = image.shape[:2]
        for detection in detections:
            if len(detection.box) != 4:
                continue
            x1, y1, x2, y2 = detection.box
            # 비정상 좌표의 후보 한 개 때문에 정상 영상과 다른 상자가 끊기지 않게 한다.
            if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
                continue
            left, right = round(x1 * (width - 1)), round(x2 * (width - 1))
            top, bottom = round(y1 * (height - 1)), round(y2 * (height - 1))
            cv2.rectangle(image, (left, top), (right, bottom), (40, 230, 65), 2)
            tile = _label_tile(detection.label)[:height, :width]
            h, w = tile.shape
            x = min(left, width - w)
            # 화면 상단에서는 박스 안에 표시하고 오른쪽 끝에서도 글씨가 보이게 한다.
            y = top - h if top >= h else min(top + 2, height - h)
            image[y : y + h, x : x + w] = tile[:, :, None]
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        raise ValueError("실시간 미리보기 JPEG 변환 실패")
    return encoded.tobytes()


class _PreviewHandler(BaseHTTPRequestHandler):
    timeout = 4

    # 접근 경로나 접속자 주소를 로그에 남기지 않는다.
    def log_message(self, *args):
        pass

    # 화면과 JPEG가 브라우저 캐시에 남거나 다른 사이트에 포함되지 않게 한다.
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    # 루프백으로 요청한 전용 페이지와 영상 스트림만 제공한다.
    def do_GET(self):
        if self.headers.get("Host", "").partition(":")[0].lower() not in {"localhost", "127.0.0.1"}:
            self.send_error(403)
        elif self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)
        elif self.path == "/stream.mjpg":
            self.stream()
        else:
            self.send_error(404)

    # 느린 브라우저에는 중간 프레임을 생략하고 연결이 끊기면 슬롯을 회수한다.
    def stream(self):
        preview = self.server.preview
        if not preview.slots.acquire(blocking=False):
            self.send_error(503)
            return
        try:
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            version = -1
            self.wfile.write(b"--frame\r\n")
            while True:
                part = preview.next_jpeg(version)
                if part is None:
                    return
                version, jpeg = part
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii"))
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n--frame\r\n")
        except OSError:
            pass
        finally:
            preview.slots.release()


class LivePreview:
    # 카메라 원본과 전송용 JPEG를 각각 최대 한 프레임만 보관한다.
    def __init__(self, port=DEFAULT_PORT):
        self.port = port
        self.slots = threading.BoundedSemaphore(4)
        self._changed = threading.Condition()
        self._encode_lock = threading.Lock()
        self._frame = None
        self._detections = ()
        self._version = self._generation = 0
        self._cached = None
        self._closed = False
        self._server = self._thread = None

    # Pi의 루프백에만 바인딩해 PuTTY의 로컬 포트 전달로 접속할 수 있게 한다.
    def start(self):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", self.port), _PreviewHandler)
        except OSError:
            LOGGER.error(
                "미리보기 포트 %d를 열 수 없습니다. 같은 포트를 쓰는 프로그램을 확인하세요.",
                self.port,
            )
            raise
        self.port = server.server_port
        server.preview = self
        thread = threading.Thread(target=server.serve_forever, args=(0.1,), daemon=True)
        try:
            thread.start()
        except BaseException:
            server.server_close()
            raise
        self._server, self._thread = server, thread
        LOGGER.info(
            "카메라 미리보기: PuTTY 포트 전달 후 http://127.0.0.1:%d/ 에 접속하세요.", self.port
        )

    # 호출자가 넘긴 읽기 전용 프레임 참조만 교체해 촬영 루프에서 복사·압축을 피한다.
    def publish(self, frame, detections=()):
        detections = tuple(detections)
        with self._changed:
            if self._closed or (frame is self._frame and detections == self._detections):
                return
            self._frame, self._detections = frame, detections
            self._version += 1
            self._changed.notify_all()

    # 대기로 전환하면 원본과 이미 압축한 사진을 버리고 연결 중인 화면을 비운다.
    def clear(self):
        with self._changed:
            if self._frame is not None:
                self._frame, self._detections, self._cached = None, (), None
                self._generation += 1
                self._version += 1
                self._changed.notify_all()

    # 접속자가 읽을 때만 한 번 압축하고, 대기 중에도 간헐적으로 연결 종료를 확인한다.
    def next_jpeg(self, previous):
        with self._changed:
            self._changed.wait_for(lambda: self._closed or self._version != previous, timeout=2)
        with self._encode_lock:
            while True:
                with self._changed:
                    if self._closed:
                        return None
                    version, generation = self._version, self._generation
                    if self._cached is not None and self._cached[0] == version:
                        return self._cached
                    frame, detections = self._frame, self._detections
                jpeg = _jpeg(frame, detections)
                with self._changed:
                    if self._closed:
                        return None
                    # 압축 도중 카메라가 대기로 바뀌면 이전 사진을 다시 표시하지 않는다.
                    if generation == self._generation:
                        self._cached = version, jpeg
                        return self._cached

    # 기다리는 브라우저를 깨우고 HTTP 서버와 소켓을 종료한다.
    def close(self):
        with self._changed:
            self._closed = True
            self._frame, self._detections, self._cached = None, (), None
            self._changed.notify_all()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join()
            self._server = self._thread = None
