"""촬영 큐의 재시작·중복·네트워크 장애를 검증한다."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from medikit_pi.outbox import Outbox, SyncWorker


class FakeTransport:
    # 업로드 순서와 가상의 서버 상태를 준비한다.
    def __init__(self):
        self.events = []
        self.result = "PROCESSING"
        self.fail_upload = False

    # 전송 내용을 기록하거나 네트워크 장애를 재현한다.
    def upload(self, event, directory):
        self.events.append(dict(event))
        if self.fail_upload:
            raise ConnectionError("테스트 네트워크 장애")

    # 현재 설정한 서버 상태를 반환한다.
    def status(self, event_id):
        return self.result


class OutboxTests(unittest.TestCase):
    # 매 테스트에 독립적인 임시 큐를 만든다.
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.outbox = Outbox(self.root)
        self.transport = FakeTransport()

    # 잠금과 임시 파일을 테스트 후 정리한다.
    def tearDown(self):
        self.outbox.close()
        self.temporary.cleanup()

    # 선택한 사진이 있는 완료 세션을 만든다.
    def session(self, package=True, interior=True):
        session_id = self.outbox.begin_session("test-box")
        for kind, captured in (("package", package), ("interior", interior)):
            if captured:
                self.outbox.save_capture(session_id, kind, b"synthetic-jpeg")
        return self.outbox.finish_session(session_id)

    # 재시작 시 미완료 포장 사진과 순번이 유지되는지 확인한다.
    def test_restart_recovers_open_session_and_sequence(self):
        session_id = self.outbox.begin_session("test-box")
        self.outbox.save_capture(session_id, "package", b"synthetic-jpeg")
        self.outbox.close()
        self.outbox = Outbox(self.root)
        event = self.outbox.head()
        self.assertEqual(event["session_id"], session_id)
        self.assertTrue(event["package_capture_ok"])
        self.assertFalse(event["interior_capture_ok"])
        following = self.session()
        self.assertGreater(following["sequence"], event["sequence"])

    # 같은 폴더의 이중 실행으로 세션이 복구되는 것을 차단한다.
    def test_second_process_lock_is_rejected(self):
        with self.assertRaises(RuntimeError):
            Outbox(self.root)

    # 성공 촬영은 세션 안에서 종류별 한 번만 저장한다.
    def test_successful_capture_is_immutable(self):
        session_id = self.outbox.begin_session("test-box")
        self.outbox.save_capture(session_id, "package", b"first")
        with self.assertRaises(ValueError):
            self.outbox.save_capture(session_id, "package", b"second")
        self.assertEqual((self.root / session_id / "package.jpg").read_bytes(), b"first")

    # 촬영 중인 앞 세션을 완료된 뒷 세션이 추월하지 않는다.
    def test_unfinished_head_blocks_later_session(self):
        first = self.outbox.begin_session("test-box")
        self.session()
        self.assertIsNone(self.outbox.head())
        self.outbox.finish_session(first)
        self.assertEqual(self.outbox.head()["session_id"], first)

    # 네트워크 복구 후에도 동일 이벤트를 다시 전송한다.
    def test_network_failure_retries_same_event_with_backoff(self):
        first = self.session()
        self.session()
        worker = SyncWorker(self.outbox, self.transport)
        self.transport.fail_upload = True
        self.assertEqual(worker.step(0), "ERROR")
        self.assertIsNone(worker.step(2.5))
        self.transport.fail_upload = False
        self.assertEqual(worker.step(5), "UPLOADED")
        self.assertEqual(
            [event["event_id"] for event in self.transport.events], [first["event_id"]] * 2
        )
        self.assertTrue((self.root / first["session_id"] / "package.jpg").exists())

    # 서버 처리 완료 전에는 다음 세션을 전송하거나 사진을 삭제하지 않는다.
    def test_fifo_waits_for_terminal_status_and_review_advances(self):
        first = self.session()
        second = self.session()
        worker = SyncWorker(self.outbox, self.transport)
        self.assertEqual(worker.step(0), "UPLOADED")
        self.assertEqual(worker.step(2.5), "PROCESSING")
        self.assertEqual(len(self.transport.events), 1)
        self.assertTrue((self.root / first["session_id"] / "package.jpg").exists())
        self.transport.result = "REVIEW_REQUIRED"
        self.assertEqual(worker.step(5), "REVIEW_REQUIRED")
        self.assertFalse((self.root / first["session_id"]).exists())
        self.assertEqual(worker.step(5), "UPLOADED")
        self.assertEqual(self.transport.events[-1]["event_id"], second["event_id"])

    # 서버 실패는 앞 세션을 보존하고 같은 식별자로 다시 전송한다.
    def test_server_failure_blocks_following_session(self):
        first = self.session()
        self.session()
        worker = SyncWorker(self.outbox, self.transport)
        worker.step(0)
        self.transport.result = "FAILED"
        self.assertEqual(worker.step(2.5), "FAILED")
        self.assertEqual(self.outbox.head()["event_id"], first["event_id"])
        self.assertIsNone(worker.step(5))
        self.assertEqual(worker.step(7.5), "UPLOADED")
        self.assertEqual(self.transport.events[-1]["event_id"], first["event_id"])

    # 업로드 직후 재시작하면 재업로드 대신 동일 이벤트의 상태를 조회한다.
    def test_restart_keeps_uploaded_state(self):
        first = self.session()
        worker = SyncWorker(self.outbox, self.transport)
        worker.step(0)
        self.outbox.close()
        self.outbox = Outbox(self.root)
        worker = SyncWorker(self.outbox, self.transport)
        self.transport.result = "COMPLETED"
        self.assertEqual(worker.step(0), "COMPLETED")
        self.assertEqual(len(self.transport.events), 1)
        self.assertIsNone(self.outbox.head())
        self.assertFalse((self.root / first["session_id"]).exists())

    # 원격 업로드 직후 로컬 기록이 실패해도 같은 이벤트만 재전송한다.
    def test_upload_commit_gap_replays_same_identity_after_restart(self):
        first = self.session()
        worker = SyncWorker(self.outbox, self.transport)
        with patch.object(self.outbox, "mark_uploaded", side_effect=OSError("테스트 기록 실패")):
            self.assertEqual(worker.step(0), "ERROR")
        self.outbox.close()
        self.outbox = Outbox(self.root)
        worker = SyncWorker(self.outbox, self.transport)
        self.assertEqual(worker.step(0), "UPLOADED")
        self.assertEqual(
            [event["event_id"] for event in self.transport.events], [first["event_id"]] * 2
        )
        self.assertEqual(
            [event["sequence"] for event in self.transport.events], [first["sequence"]] * 2
        )

    # 사진 저장 도중 디스크 쓰기가 실패하면 성공으로 확정하지 않는다.
    def test_failed_atomic_write_does_not_mark_capture_success(self):
        session_id = self.outbox.begin_session("test-box")
        with patch("medikit_pi.outbox.os.replace", side_effect=OSError("테스트 디스크 실패")):
            with self.assertRaises(OSError):
                self.outbox.save_capture(session_id, "package", b"synthetic-jpeg")
        event = self.outbox.finish_session(session_id)
        self.assertFalse(event["package_capture_ok"])
        self.assertIsNone(event["package_image_key"])
        self.assertEqual(self.outbox.finish_session(session_id), event)

    # 삭제 중단 이후에도 완료 상태는 유지하며 재시작 시 삭제를 끝낸다.
    def test_cleanup_failure_does_not_replay_completed_event(self):
        first = self.session()
        with patch.object(self.outbox, "_cleanup", side_effect=OSError("테스트 삭제 실패")):
            with self.assertRaises(OSError):
                self.outbox.complete(first["event_id"], "COMPLETED")
        self.assertIsNone(self.outbox.head())
        self.outbox.close()
        self.outbox = Outbox(self.root)
        self.assertFalse((self.root / first["session_id"]).exists())

    # 계약 밖 상태나 실패 상태로 사진을 삭제할 수 없다.
    def test_unknown_status_preserves_images(self):
        first = self.session()
        worker = SyncWorker(self.outbox, self.transport)
        worker.step(0)
        self.transport.result = "DONE"
        self.assertEqual(worker.step(2.5), "ERROR")
        self.assertTrue((self.root / first["session_id"] / "package.jpg").exists())
        with self.assertRaises(ValueError):
            self.outbox.complete(first["event_id"], "FAILED")


if __name__ == "__main__":
    unittest.main()
