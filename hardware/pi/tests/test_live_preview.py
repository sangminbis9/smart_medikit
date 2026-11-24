"""촬영에 영향을 주지 않는 로컬 영상 표시와 대기·종료 동작을 검증한다."""

import socket
import threading
from http.client import HTTPConnection

import cv2
import numpy as np
import pytest
from medikit_pi import live_preview
from medikit_pi.detection import Detection
from medikit_pi.live_preview import LivePreview


# 실제 브라우저와 같은 응답에서 다음 JPEG 부분을 디코딩한다.
def read_frame(response):
    assert response.readline() == b"--frame\r\n"
    assert response.readline() == b"Content-Type: image/jpeg\r\n"
    length = int(response.readline().split(b":")[1])
    assert response.readline() == b"\r\n"
    jpeg = response.read(length)
    assert response.readline() == b"\r\n"
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)


# 실제 루프백에서 페이지를 연 뒤 대기→상자 표시→대기와 새 접속을 확인한다.
def test_stream_follows_publish_and_clear():
    preview = LivePreview(0)
    preview.start()
    conn = HTTPConnection("127.0.0.1", preview.port, timeout=4)
    try:
        conn.request("GET", "/")
        page = conn.getresponse()
        assert page.status == 200
        assert page.getheader("Cache-Control") == "no-store"
        assert page.getheader("Access-Control-Allow-Origin") is None
        assert b"/stream.mjpg" in page.read()
        conn.request("GET", "/stream.mjpg")
        with conn.getresponse() as stream:
            assert stream.getheader("Content-Type").startswith("multipart/x-mixed-replace;")
            assert stream.getheader("Cache-Control") == "no-store"
            assert stream.getheader("Cross-Origin-Resource-Policy") == "same-origin"
            idle = read_frame(stream)
            frame = np.full((80, 160, 3), 40, np.uint8)
            preview.publish(
                frame,
                [
                    Detection((float("nan"), 0.1, 0.5, 0.5), 0.75, "bad"),
                    Detection((0.25, 0.25, 0.75, 0.75), 0.84, "box"),
                ],
            )
            marked = read_frame(stream)
            assert marked.shape == frame.shape
            assert marked[20, 60, 1] > 150
            assert np.max(marked[:15]) < 65
            assert np.all(frame == 40)
            preview.clear()
            np.testing.assert_array_equal(read_frame(stream), idle)
        conn.request("GET", "/stream.mjpg")
        with conn.getresponse() as reconnected:
            np.testing.assert_array_equal(read_frame(reconnected), idle)
        conn.request("GET", "/config.toml")
        assert conn.getresponse().status == 404
        conn.request("GET", "/stream.mjpg", headers={"Host": "external.invalid"})
        assert conn.getresponse().status == 403
    finally:
        conn.close()
        preview.close()


# 시청자가 없으면 압축하지 않고 같은 검출 원본을 다시 게시해도 JPEG를 재사용한다.
def test_publish_does_not_encode_and_viewers_share_result(monkeypatch):
    preview = LivePreview()
    calls = []

    # 호출 수로 같은 프레임의 중복 인코딩 여부를 확인한다.
    def encode(frame, detections):
        calls.append(frame)
        return b"jpeg"

    monkeypatch.setattr(live_preview, "_jpeg", encode)
    frame = np.zeros((20, 20, 3), np.uint8)
    preview.publish(frame)
    assert not calls
    first = preview.next_jpeg(-1)
    preview.publish(frame)
    assert preview.next_jpeg(-1) == first
    assert len(calls) == 1
    preview.close()
    assert preview.next_jpeg(-1) is None


# 인코딩 중 카메라를 껐다 켠 경우에도 이전 세션 사진을 송출하지 않는다.
def test_clear_discards_in_flight_encoding(monkeypatch):
    preview = LivePreview()
    started, resume = threading.Event(), threading.Event()
    old_frame = np.zeros((10, 10, 3), np.uint8)
    new_frame = np.ones_like(old_frame)

    # 이전 프레임의 압축만 지연해 폐기와 새 세션 게시 사이의 경합을 재현한다.
    def slow_encode(frame, detections):
        if frame is old_frame:
            started.set()
            assert resume.wait(3)
            return b"old"
        assert frame is new_frame
        return b"new"

    received = []

    # 압축을 요청한 접속자가 받은 결과를 보존한다.
    def read():
        received.append(preview.next_jpeg(-1))

    monkeypatch.setattr(live_preview, "_jpeg", slow_encode)
    preview.publish(old_frame)
    reader = threading.Thread(target=read)
    reader.start()
    try:
        assert started.wait(3)
        preview.clear()
        preview.publish(new_frame)
    finally:
        resume.set()
        reader.join(3)
    assert received == [(3, b"new")]
    preview.close()


# 서버가 포트를 연 직후 스레드 시작이 실패해도 종료 처리가 대기하지 않아야 한다.
def test_failed_server_thread_start_closes_socket(monkeypatch):
    preview = LivePreview(0)

    # 자원이 부족해 운영체제가 스레드를 시작할 수 없는 상황을 재현한다.
    def fail_start(thread):
        raise RuntimeError("시험용 스레드 오류")

    # 시작하지 않은 서버에서 shutdown 호출로 테스트가 멈추는 것을 방지한다.
    def unexpected_shutdown(server):
        pytest.fail("시작하지 않은 서버에서 종료를 기다렸습니다.")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    monkeypatch.setattr(live_preview.ThreadingHTTPServer, "shutdown", unexpected_shutdown)
    with pytest.raises(RuntimeError, match="시험용"):
        preview.start()
    preview.close()
    with socket.socket() as check:
        check.bind(("127.0.0.1", preview.port))
