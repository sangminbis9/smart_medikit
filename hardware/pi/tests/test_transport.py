"""AWS 전송의 명시적 계약·인증·완료 객체 순서를 검증한다."""

import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from botocore.exceptions import ClientError
from medikit_pi.outbox import Outbox, SyncWorker
from medikit_pi.transport import S3Transport, _NoRedirect


class FakeCredentials:
    # 테스트용 단기 자격증명만 반환한다.
    def get_frozen_credentials(self):
        return SimpleNamespace(token="synthetic-token")


class FakeSession:
    # 네트워크 요청 없이 자격증명 공급자를 재현한다.
    def get_credentials(self):
        return FakeCredentials()


class FakeS3:
    # 업로드 순서와 본문을 기록한다.
    def __init__(self):
        self.objects = []
        self.fail_key = None
        self.requests = []
        self.saved_metadata = {}

    # 특정 이미지에서 실패시켜 완료 객체 미전송을 확인한다.
    def put_object(self, **request):
        if self.fail_key == request["Key"]:
            raise ConnectionError("테스트 업로드 실패")
        self.requests.append(request)
        if request.get("IfNoneMatch") == "*" and request["Key"] in self.saved_metadata:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        if "Metadata" in request:
            self.saved_metadata[request["Key"]] = request["Metadata"]
        body = request["Body"]
        self.objects.append((request["Key"], body.read() if hasattr(body, "read") else body))

    # 조건부 재전송의 기존 이미지 메타데이터를 반환한다.
    def head_object(self, **request):
        return {"Metadata": self.saved_metadata[request["Key"]]}


# 단계 전송·재시작·완료 조회에서 촬영 시각과 동일한 객체 키를 보존한다.
def test_staged_key_and_restart(tmp_path):
    session = FakeSession()
    s3 = FakeS3()
    session.client = lambda *args, **kwargs: s3
    token = tmp_path / "token"
    token.write_text("synthetic-token")
    config = {
        "contract_confirmed": True, "bucket": "synthetic", "region": "ap-northeast-2",
        "prefix": "medkit-sessions", "ocr_input_prefix": "ocr-input",
        "manifest_name": "closed-ready.json", "status_url_template": "https://example.test/{event_id}",
        "auth_mode": "bearer", "bearer_token_file": str(token),
    }
    box = Outbox(tmp_path / "queue", staged=True)
    sid = box.begin_session("box")
    box.save_capture(sid, "package", b"package", captured_at=datetime(2026, 11, 19, 14, 7, 41, tzinfo=timezone.utc))
    expected = f"ocr-input/20261119_230741_{sid}_package.jpg"
    event = box.stage_head("OCR_REQUESTED")
    assert event["package_image_key"] == expected
    box.save_capture(sid, "interior", b"interior")
    box.finish_session(sid)
    box.close()
    box = Outbox(tmp_path / "queue", staged=True, ocr_prefix="changed-prefix")
    try:
        transport = S3Transport(config, session=session, opener=FakeOpener({"processing_status": "COMPLETED"}))
        transport.upload_stage(box.stage_head("OCR_REQUESTED"), box.root / sid)
        transport.upload_stage(box.stage_head("OCR_REQUESTED"), box.root / sid)
        transport.upload_stage(box.stage_head("MOVEMENT_REQUESTED"), box.root / sid)
        for stage in ("OCR_REQUESTED", "MOVEMENT_REQUESTED"):
            box.complete_stage(sid, stage)
        worker = SyncWorker(box, transport)
        assert worker.step(0) == "UPLOADED"
        assert worker.step(3) == "COMPLETED"
        image_requests = [request for request in s3.requests if request["Key"] == expected]
        assert len(image_requests) == 2
        assert image_requests[0]["Metadata"]["event-id"] == event["event_id"]
        assert image_requests[0]["Metadata"]["session-id"] == sid
        assert len(image_requests[0]["Metadata"]["sha256"]) == 64
        assert image_requests[0]["IfNoneMatch"] == "*"
        for name in ("ocr-ready.json", "movement-ready.json", "closed-ready.json"):
            body = next(body for key, body in s3.objects if key == f"medkit-sessions/{sid}/{name}")
            assert json.loads(body)["package_image_key"] == expected
        assert any(key == f"medkit-sessions/{sid}/interior.jpg" for key, _ in s3.objects)
    finally:
        box.close()


# 업그레이드 전 생성된 미완료 세션은 재시작 후에도 예전 키를 쓴다.
def test_existing_queue_preserves_legacy_key(tmp_path):
    box = Outbox(tmp_path, staged=True)
    sid = box.begin_session("box")
    with box._db:
        box._db.execute("UPDATE sessions SET key_scheme=0 WHERE session_id=?", (sid,))
    box.save_capture(sid, "package", b"legacy")
    event = box.stage_head("OCR_REQUESTED")
    box.close()
    box = Outbox(tmp_path, staged=True)
    try:
        assert box.stage_head("OCR_REQUESTED") == event
        assert "package_image_key" not in event
        config = {
            "contract_confirmed": True, "bucket": "synthetic", "region": "ap-northeast-2",
            "prefix": "medkit-sessions", "ocr_input_prefix": "ocr-input",
            "manifest_name": "closed-ready.json", "status_url_template": "https://example.test/{event_id}",
            "auth_mode": "bearer", "bearer_token_file": "unused",
        }
        session = FakeSession()
        s3 = FakeS3()
        session.client = lambda *args, **kwargs: s3
        S3Transport(config, session=session).upload_stage(event, box.root / sid)
        assert s3.objects[0][0] == f"ocr-input/{sid}/package.jpg"
    finally:
        box.close()


# 이전 스키마의 미완료 세션은 열 번호와 기존 단계 기록을 그대로 유지한다.
def test_old_database_migration(tmp_path):
    connection = sqlite3.connect(tmp_path / "queue.sqlite3")
    connection.execute("CREATE TABLE sessions (sequence INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT UNIQUE NOT NULL, event_id TEXT UNIQUE NOT NULL, box_id TEXT NOT NULL, occurred_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'CAPTURING', uploaded INTEGER NOT NULL DEFAULT 0, payload TEXT)")
    connection.execute("CREATE TABLE capture_stages (session_id TEXT, stage TEXT, payload TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(session_id,stage))")
    old_event = {"session_id": "legacy-id", "event_id": "event-id", "box_id": "box", "sequence": 9, "stage": "OCR_REQUESTED", "captured_at": "2026-11-19T14:07:41+00:00"}
    connection.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?)", (9, "legacy-id", "event-id", "box", "2026-11-19T14:00:00+00:00", "READY", 0, json.dumps({"session_id": "legacy-id"})))
    connection.execute("INSERT INTO capture_stages VALUES(?,?,?,?)", ("legacy-id", "OCR_REQUESTED", json.dumps(old_event), 0))
    connection.commit()
    connection.close()
    box = Outbox(tmp_path, staged=True)
    try:
        row = box._session("legacy-id")
        assert (row["sequence"], row["event_id"], row["state"], row["key_scheme"]) == (9, "event-id", "READY", 0)
        assert box.stage_head("OCR_REQUESTED") == old_event
    finally:
        box.close()


# 사진 저장 후 단계 기록 전에 중단되어도 원래 키와 촬영 시각을 복원한다.
def test_recovery_uses_persisted_key_and_time(tmp_path):
    box = Outbox(tmp_path, staged=True)
    sid = box.begin_session("box")
    captured = datetime(2026, 11, 19, 14, 7, 41, tzinfo=timezone.utc)
    box.save_capture(sid, "package", b"photo", captured_at=captured)
    original = box.stage_head("OCR_REQUESTED")
    with box._db:
        box._db.execute("DELETE FROM capture_stages WHERE session_id=?", (sid,))
    box.close()
    box = Outbox(tmp_path, staged=True)
    try:
        assert box.stage_head("OCR_REQUESTED") == original
    finally:
        box.close()


class FakeOpener:
    # 서버 응답과 인증 요청을 기록한다.
    def __init__(self, payload):
        self.payload = payload
        self.request = None

    # HTTP 대신 메모리 JSON 응답을 반환한다.
    def open(self, request, timeout):
        self.request = request
        return io.BytesIO(json.dumps(self.payload).encode())


class TransportTests(unittest.TestCase):
    # 합의된 가상 배포 정보와 민감정보 없는 파일을 준비한다.
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.token_file = self.directory / "token.txt"
        self.token_file.write_text("synthetic-token", encoding="utf-8")
        self.config = {
            "contract_confirmed": True,
            "bucket": "test-private-bucket",
            "region": "ap-northeast-2",
            "prefix": "test-sessions",
            "manifest_name": "ready.json",
            "status_url_template": "https://example.test/events/{event_id}",
            "auth_mode": "bearer",
            "bearer_token_file": str(self.token_file),
        }
        self.event = {
            "event_id": "event-1",
            "session_id": "session-1",
            "sequence": 1,
            "box_id": "test-box",
            "occurred_at": "2026-10-26T00:00:00+00:00",
            "package_capture_ok": True,
            "interior_capture_ok": True,
            "package_image_key": "sessions/session-1/package.jpg",
            "interior_image_key": "sessions/session-1/interior.jpg",
        }
        for kind in ("package", "interior"):
            (self.directory / f"{kind}.jpg").write_bytes(b"synthetic-jpeg")

    # 임시 인증 파일과 촬영 이미지를 삭제한다.
    def tearDown(self):
        self.temporary.cleanup()

    # 서버 계약을 확인하지 않으면 SDK와 네트워크에 접근하지 않는다.
    def test_unconfirmed_contract_is_rejected(self):
        self.config["contract_confirmed"] = False
        with self.assertRaises(ValueError):
            S3Transport(self.config)

    # 미지정 경로나 비보안 URL을 서버 API로 추측하지 않는다.
    def test_invalid_api_configuration_is_rejected(self):
        for url in (
            "https://example.test/events",
            "http://example.test/{event_id}",
            "https://user:secret@example.test/{event_id}",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                S3Transport({**self.config, "status_url_template": url}, session=FakeSession())

    # 사진이 모두 업로드된 뒤에만 완료 객체가 마지막으로 기록된다.
    def test_manifest_is_last_and_uses_configured_keys(self):
        transport = S3Transport(self.config, session=FakeSession())
        transport._s3 = FakeS3()
        transport.upload(self.event, self.directory)
        self.assertEqual(
            [item[0] for item in transport._s3.objects],
            [
                "test-sessions/session-1/package.jpg",
                "test-sessions/session-1/interior.jpg",
                "test-sessions/session-1/ready.json",
            ],
        )
        metadata = json.loads(transport._s3.objects[-1][1])
        self.assertEqual(metadata["package_image_key"], "test-sessions/session-1/package.jpg")
        self.assertEqual(metadata["event_id"], self.event["event_id"])

    # 이미지 전송이 실패하면 Lambda를 깨우는 완료 객체를 보내지 않는다.
    def test_partial_upload_does_not_publish_manifest(self):
        transport = S3Transport(self.config, session=FakeSession())
        transport._s3 = FakeS3()
        transport._s3.fail_key = "test-sessions/session-1/interior.jpg"
        with self.assertRaises(ConnectionError):
            transport.upload(self.event, self.directory)
        self.assertEqual(len(transport._s3.objects), 1)

    # 세션 토큰 없는 장기 키는 사진 전송에 사용하지 않는다.
    def test_long_lived_aws_credentials_are_rejected(self):
        transport = S3Transport(self.config, session=FakeSession())
        transport._s3 = FakeS3()
        credentials = FakeCredentials()

        # 세션 토큰 없는 공급자를 테스트용으로 구성한다.
        def frozen_without_token():
            return SimpleNamespace(token=None)

        credentials.get_frozen_credentials = frozen_without_token

        # 준비한 공급자를 반환한다.
        def get_credentials():
            return credentials

        transport.session.get_credentials = get_credentials
        with self.assertRaises(RuntimeError):
            transport.upload(self.event, self.directory)
        self.assertFalse(transport._s3.objects)

    # 인증된 상태 요청은 합의된 필드만 읽고 토큰 변경도 반영한다.
    def test_bearer_status_reads_rotated_external_token(self):
        opener = FakeOpener({"processing_status": "REVIEW_REQUIRED"})
        transport = S3Transport(self.config, session=FakeSession(), opener=opener)
        self.assertEqual(transport.status("event-1"), "REVIEW_REQUIRED")
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer synthetic-token")
        self.token_file.write_text("rotated-synthetic-token", encoding="utf-8")
        transport.status("event-1")
        self.assertEqual(
            opener.request.get_header("Authorization"), "Bearer rotated-synthetic-token"
        )

    # 계약 필드가 없는 응답을 완료 처리로 간주하지 않는다.
    def test_missing_processing_status_is_rejected(self):
        transport = S3Transport(
            self.config, session=FakeSession(), opener=FakeOpener({"status": "COMPLETED"})
        )
        with self.assertRaises(ValueError):
            transport.status("event-1")

    # 다른 주소로 인증 헤더가 넘어가는 리다이렉트를 금지한다.
    def test_redirect_is_rejected(self):
        with self.assertRaises(ValueError):
            _NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.test/redirect")


if __name__ == "__main__":
    unittest.main()
