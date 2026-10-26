"""AWS 전송의 명시적 계약·인증·완료 객체 순서를 검증한다."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

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

    # 특정 이미지에서 실패시켜 완료 객체 미전송을 확인한다.
    def put_object(self, **request):
        if self.fail_key == request["Key"]:
            raise ConnectionError("테스트 업로드 실패")
        body = request["Body"]
        self.objects.append((request["Key"], body.read() if hasattr(body, "read") else body))


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
