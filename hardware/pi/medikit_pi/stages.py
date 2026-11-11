"""포장 OCR 전송과 닫힘 후 이동 판단을 독립 작업자로 실행한다."""

import json

import cv2
import numpy as np

from .movement import compare_interior, locate_candidate


class StageWorker:
    # 단계별 작업자에 영속 큐와 별도 전송 연결을 주입한다.
    def __init__(self, outbox, stage, transport=None, detector=None, geometry=None):
        self.outbox, self.stage, self.transport, self.detector = outbox, stage, transport, detector
        self.geometry = geometry
        with outbox._mutex, outbox._db:
            outbox._db.execute("CREATE TABLE IF NOT EXISTS movement_results(session_id TEXT PRIMARY KEY, result TEXT NOT NULL)")
            outbox._db.execute("CREATE TABLE IF NOT EXISTS interior_baseline(box_id TEXT PRIMARY KEY, sequence INTEGER, image BLOB)")

    # OCR 상태 조회 없이 각 단계의 촬영 준비 여부만 확인한다.
    def step(self):
        if self.detector is not None:
            with self.outbox._mutex:
                row = self.outbox._db.execute("SELECT c.payload FROM capture_stages c JOIN sessions s ON s.session_id=c.session_id LEFT JOIN movement_results m ON m.session_id=c.session_id WHERE c.stage='MOVEMENT_REQUESTED' AND m.session_id IS NULL ORDER BY s.sequence LIMIT 1").fetchone()
            event = json.loads(row[0]) if row else None
        else:
            event = self.outbox.stage_head(self.stage)
        if event is None:
            return
        directory = self.outbox.root / event["session_id"]
        if self.stage == "MOVEMENT_REQUESTED":
            if self.detector is not None:
                self.movement(event, directory)
                return
            with self.outbox._mutex:
                saved = self.outbox._db.execute("SELECT result FROM movement_results WHERE session_id=?", (event["session_id"],)).fetchone()
            if not saved:
                return
            event["movement"] = json.loads(saved[0])
        if self.transport is None:
            return
        self.transport.upload_stage(event, directory)
        self.outbox.complete_stage(event["session_id"], self.stage)

    # 직전 내부 영상과 비교한 결과·새 기준 영상을 하나의 트랜잭션으로 보존한다.
    def movement(self, event, directory):
        store = self.outbox
        with store._mutex:
            saved = store._db.execute("SELECT result FROM movement_results WHERE session_id=?", (event["session_id"],)).fetchone()
            previous = store._db.execute("SELECT sequence,image FROM interior_baseline WHERE box_id=?", (event["box_id"],)).fetchone()
        if saved:
            return json.loads(saved[0])
        raw = (directory / "interior.jpg").read_bytes()
        after = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        before = None
        # 촬영 실패나 누락 세션을 건너뛴 사진을 바로 전 상태로 사용하지 않는다.
        if previous and previous[0] == event["sequence"] - 1:
            before = cv2.imdecode(np.frombuffer(previous[1], np.uint8), cv2.IMREAD_COLOR)
        first = self.detector.predict(before) if before is not None else []
        second = self.detector.predict(after) if after is not None else []
        result = compare_interior(before, after, first, second, automatic_in=True)
        if result.get('candidate'):
            result['geometry'] = locate_candidate(result['candidate'], self.geometry)
        result["observed_at"] = event["captured_at"]
        with store._mutex, store._db:
            store._db.execute("INSERT INTO movement_results VALUES(?,?)", (event["session_id"], json.dumps(result)))
            if after is not None:
                store._db.execute("INSERT OR REPLACE INTO interior_baseline VALUES(?,?,?)", (event["box_id"], event["sequence"], raw))
        return result


# 네트워크 실패는 해당 단계만 재시도하고 촬영·다른 단계는 계속 진행한다.
def stage_loop(worker, stop):
    failures = 0
    while not stop.is_set():
        try:
            worker.step()
            failures = 0
        except Exception:
            failures = min(failures + 1, 6)
        stop.wait(min(60, 2 ** failures) if failures else 0.1)
