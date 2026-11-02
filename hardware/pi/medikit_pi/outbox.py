"""촬영 세션과 순차 전송 상태를 전원 중단에도 보존한다."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


# 파일 교체와 삭제 결과를 디렉터리에 반영한다.
def _sync_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class Outbox:
    # 동일 데이터 폴더의 중복 실행을 차단하고 중단된 세션을 복구한다.
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._mutex = threading.RLock()
        self._lock_file = (self.root / "instance.lock").open("a+b")
        self._closed = False
        try:
            if os.name == "nt":
                import msvcrt

                self._lock_file.seek(0)
                if not self._lock_file.read(1):
                    self._lock_file.write(b"0")
                    self._lock_file.flush()
                self._lock_file.seek(0)
                msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock_file.close()
            raise RuntimeError("같은 데이터 폴더를 사용하는 프로세스가 실행 중입니다.") from None
        try:
            self._db = sqlite3.connect(self.root / "queue.sqlite3", check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS sessions ("
                "sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
                "session_id TEXT UNIQUE NOT NULL, event_id TEXT UNIQUE NOT NULL, "
                "box_id TEXT NOT NULL, occurred_at TEXT NOT NULL, "
                "state TEXT NOT NULL DEFAULT 'CAPTURING', "
                "uploaded INTEGER NOT NULL DEFAULT 0, payload TEXT)"
            )
            self._db.commit()
            for row in self._db.execute(
                "SELECT session_id FROM sessions WHERE state='CAPTURING'"
            ).fetchall():
                self.finish_session(row["session_id"])
            for row in self._db.execute(
                "SELECT session_id FROM sessions WHERE state IN ('COMPLETED','REVIEW_REQUIRED')"
            ).fetchall():
                self._cleanup(row["session_id"])
        except BaseException:
            if hasattr(self, "_db"):
                self._db.close()
            self._lock_file.close()
            raise

    # 세션 시작 시 영속적인 순번과 멱등성 식별자를 할당한다.
    def begin_session(self, box_id: str) -> str:
        if not box_id or not isinstance(box_id, str):
            raise ValueError("box_id가 필요합니다.")
        session_id = str(uuid.uuid4())
        with self._mutex, self._db:
            self._db.execute(
                "INSERT INTO sessions(session_id,event_id,box_id,occurred_at) VALUES(?,?,?,?)",
                (session_id, str(uuid.uuid4()), box_id, datetime.now(timezone.utc).isoformat()),
            )
        return session_id

    # 성공한 사진만 원자적으로 저장하며 같은 종류의 성공 사진은 덮어쓰지 않는다.
    def save_capture(self, session_id: str, kind: str, jpeg: bytes) -> None:
        if kind not in {"package", "interior"} or not jpeg:
            raise ValueError("촬영 종류와 비어 있지 않은 JPEG가 필요합니다.")
        with self._mutex:
            row = self._session(session_id)
            if row["state"] != "CAPTURING":
                raise ValueError("종료한 촬영 세션입니다.")
            directory = self.root / session_id
            directory.mkdir(mode=0o700, exist_ok=True)
            target = directory / f"{kind}.jpg"
            if target.exists():
                raise ValueError("이 세션에는 해당 사진이 이미 저장되었습니다.")
            temporary = directory / f"{kind}.tmp"
            with temporary.open("wb") as image_file:
                image_file.write(jpeg)
                image_file.flush()
                os.fsync(image_file.fileno())
            os.replace(temporary, target)
            _sync_directory(directory)
            _sync_directory(self.root)

    # 촬영 실패도 사진 존재 여부로 기록하고 전송 가능한 세션으로 확정한다.
    def finish_session(self, session_id: str) -> dict:
        with self._mutex, self._db:
            row = self._session(session_id)
            if row["payload"]:
                return json.loads(row["payload"])
            event = {
                key: row[key]
                for key in ("box_id", "session_id", "event_id", "sequence", "occurred_at")
            }
            for kind in ("package", "interior"):
                captured = (self.root / session_id / f"{kind}.jpg").is_file()
                event[f"{kind}_capture_ok"] = captured
                event[f"{kind}_image_key"] = (
                    f"sessions/{session_id}/{kind}.jpg" if captured else None
                )
            self._db.execute(
                "UPDATE sessions SET state='READY',payload=? WHERE session_id=?",
                (json.dumps(event), session_id),
            )
            return event

    # 가장 오래된 미완료 세션만 반환하여 촬영 중인 앞 세션도 추월하지 않는다.
    def head(self) -> dict | None:
        with self._mutex:
            row = self._db.execute(
                "SELECT * FROM sessions WHERE state NOT IN ('COMPLETED','REVIEW_REQUIRED') ORDER BY sequence LIMIT 1"
            ).fetchone()
            if row is None or row["state"] == "CAPTURING":
                return None
            event = json.loads(row["payload"])
            event["_uploaded"] = bool(row["uploaded"])
            event["_directory"] = self.root / row["session_id"]
            return event

    # 업로드가 끝나도 서버 종료 상태를 받기 전까지 사진을 보존한다.
    def mark_uploaded(self, event_id: str) -> None:
        with self._mutex, self._db:
            self._db.execute("UPDATE sessions SET uploaded=1 WHERE event_id=?", (event_id,))

    # 서버 실패 시 같은 식별자의 업로드를 재시도할 수 있도록 표시한다.
    def retry_upload(self, event_id: str) -> None:
        with self._mutex, self._db:
            self._db.execute("UPDATE sessions SET uploaded=0 WHERE event_id=?", (event_id,))

    # 서버 완료를 먼저 영속화한 뒤 로컬 사진을 삭제한다.
    def complete(self, event_id: str, status: str) -> None:
        if status not in {"COMPLETED", "REVIEW_REQUIRED"}:
            raise ValueError("사진 삭제에는 서버의 정상 종료 상태가 필요합니다.")
        with self._mutex:
            row = self._db.execute(
                "SELECT session_id,state FROM sessions WHERE event_id=?", (event_id,)
            ).fetchone()
            if row is None or row["state"] == "CAPTURING":
                raise ValueError("전송 가능한 이벤트가 아닙니다.")
            with self._db:
                self._db.execute("UPDATE sessions SET state=? WHERE event_id=?", (status, event_id))
            self._cleanup(row["session_id"])

    # 생성된 식별자에 대응하는 세션만 파일 경로로 사용한다.
    def _session(self, session_id: str) -> sqlite3.Row:
        row = self._db.execute(
            "SELECT * FROM sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        if row is None:
            raise ValueError("등록되지 않은 촬영 세션입니다.")
        return row

    # 완료된 세션의 알려진 임시 파일만 제거하며 임의 경로는 순회하지 않는다.
    def _cleanup(self, session_id: str) -> None:
        directory = self.root / session_id
        if directory.exists():
            for name in ("package.jpg", "interior.jpg", "package.tmp", "interior.tmp"):
                (directory / name).unlink(missing_ok=True)
            directory.rmdir()
            _sync_directory(self.root)

    # 작업 스레드 종료 후 데이터베이스와 프로세스 잠금을 해제한다.
    def close(self) -> None:
        with self._mutex:
            if not self._closed:
                self._db.close()
                self._lock_file.close()
                self._closed = True


class SyncWorker:
    # 촬영 루프와 독립적으로 호출할 순차 전송기를 구성한다.
    def __init__(self, outbox: Outbox, transport, poll_seconds: float = 2.5):
        if not 2.0 <= poll_seconds <= 3.0:
            raise ValueError("서버 상태 조회 간격은 2~3초여야 합니다.")
        self.outbox = outbox
        self.transport = transport
        self.poll_seconds = poll_seconds
        self.next_attempt = 0.0
        self.failures = 0
        self.last_error: str | None = None
        self._mutex = threading.Lock()

    # 준비된 한 세션을 전송하거나 조회하며 오류는 지수 백오프로 보존한다.
    def step(self, now: float) -> str | None:
        with self._mutex:
            if now < self.next_attempt:
                return None
            event = self.outbox.head()
            if event is None:
                return None
            uploaded = event.pop("_uploaded")
            directory = event.pop("_directory")
            self.next_attempt = now + self.poll_seconds
            try:
                if not uploaded:
                    self.transport.upload(event, directory)
                    self.outbox.mark_uploaded(event["event_id"])
                    self.last_error = None
                    return "UPLOADED"
                status = self.transport.status(event["event_id"])
                if status not in {
                    "PENDING",
                    "PROCESSING",
                    "COMPLETED",
                    "REVIEW_REQUIRED",
                    "FAILED",
                }:
                    raise ValueError("알 수 없는 서버 처리 상태입니다.")
                if status in {"COMPLETED", "REVIEW_REQUIRED"}:
                    self.outbox.complete(event["event_id"], status)
                    self.failures = 0
                    self.last_error = None
                    self.next_attempt = now
                elif status == "FAILED":
                    self.outbox.retry_upload(event["event_id"])
                    self._backoff(now)
                else:
                    self.failures = 0
                    self.last_error = None
                return status
            except Exception as error:
                # 응답 본문과 인증값이 들어갈 수 있는 예외 문자열은 보관하지 않는다.
                self.last_error = type(error).__name__
                self._backoff(now)
                return "ERROR"

    # 연속 장애 시 재시도 간격을 최대 60초까지 늘린다.
    def _backoff(self, now: float) -> None:
        self.failures = min(self.failures + 1, 6)
        self.next_attempt = now + min(60.0, self.poll_seconds * 2**self.failures)
