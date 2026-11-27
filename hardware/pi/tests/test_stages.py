"""OCR 지연·오프라인과 관계없는 내부 판단과 영속 단계 복구를 검사한다."""

import logging
from unittest.mock import Mock

import cv2
import numpy as np
from medikit_pi.detection import Detection
from medikit_pi.outbox import Outbox, SyncWorker
from medikit_pi.stages import StageWorker, stage_loop


# 합성 사진을 실제 저장 경로에 전달한다.
def photo():
    return cv2.imencode('.jpg', np.zeros((100, 100, 3), np.uint8))[1].tobytes()


# 뚜껑이 열린 세션에서도 포장 촬영 단계가 즉시 전송 가능하다.
def test_ocr_queued_before_close(tmp_path):
    with_box = Outbox(tmp_path, staged=True)
    try:
        session = with_box.begin_session('test-box')
        with_box.save_capture(session, 'package', photo())
        assert with_box.head() is None
        assert with_box.stage_head('OCR_REQUESTED')['session_id'] == session
        assert with_box.stage_head('MOVEMENT_REQUESTED') is None
    finally:
        with_box.close()


# 닫힘이 확인되면 OCR 전송 뒤 내부 분석보다 먼저 서버 처리를 시작한다.
def test_inventory_state_close_does_not_wait_for_movement(tmp_path):
    box = Outbox(tmp_path, staged=True)
    try:
        session = box.begin_session("test-box")
        box.save_capture(session, "package", photo())
        box.save_capture(session, "interior", photo())
        closed_at = "2026-11-20T01:00:00+00:00"
        box.finish_session(session, closed_at=closed_at)
        transport = Mock()
        transport.status.return_value = "COMPLETED"
        worker = SyncWorker(box, transport)
        assert worker.step(1) is None
        box.complete_stage(session, "OCR_REQUESTED")
        assert worker.step(2) == "UPLOADED"
        payload, directory = transport.upload.call_args.args
        assert payload["movement_policy"] == "INVENTORY_STATE"
        assert payload["closed_at"] == closed_at
        assert payload["stage"] == "SESSION_CLOSED"
        assert worker.step(5) == "COMPLETED"
        assert (directory / "interior.jpg").exists()
        box.complete_stage(session, "MOVEMENT_REQUESTED")
        assert not directory.exists()
    finally:
        box.close()


# OCR 전송이 끝나지 않아도 두 번째 내부 영상의 자동 반입 판단을 저장한다.
def test_movement_does_not_wait_for_ocr(tmp_path):
    box = Outbox(tmp_path, staged=True)
    detector = Mock()
    worker = StageWorker(box, 'MOVEMENT_REQUESTED', detector=detector)
    try:
        first = box.begin_session('test-box')
        box.save_capture(first, 'interior', photo())
        box.finish_session(first)
        detector.predict.return_value = []
        worker.step()
        second = box.begin_session('test-box')
        box.save_capture(second, 'package', photo())
        box.save_capture(second, 'interior', photo())
        box.finish_session(second)
        detector.predict.side_effect = [[], [Detection((.1, .1, .4, .4), .9, 'box')]]
        worker.step()
        assert box.stage_head('OCR_REQUESTED')['session_id'] == second
        import json
        result = json.loads(box._db.execute('SELECT result FROM movement_results WHERE session_id=?', (second,)).fetchone()[0])
        assert result['candidate']['direction'] == 'IN'
        assert result['processing_status'] == 'COMPLETED'
    finally:
        box.close()


# 전후에 같은 단독 후보가 보여 변화가 없을 때 위치 후보만 별도로 기록한다.
def test_single_visible_candidate_is_preserved(tmp_path):
    box = Outbox(tmp_path, staged=True)
    detector = Mock()
    worker = StageWorker(box, 'MOVEMENT_REQUESTED', detector=detector)
    try:
        candidate = Detection((.1, .1, .4, .4), .9, 'box')
        detector.predict.return_value = [candidate]
        first = box.begin_session('test-box')
        box.save_capture(first, 'interior', photo())
        box.finish_session(first)
        worker.step()
        second = box.begin_session('test-box')
        box.save_capture(second, 'interior', photo())
        box.finish_session(second)
        worker.step()
        import json
        result = json.loads(box._db.execute('SELECT result FROM movement_results WHERE session_id=?', (second,)).fetchone()[0])
        assert result['reason'] == 'NO_SINGLE_CHANGE'
        assert result['single_visible']['candidate']['box'] == [.1, .1, .4, .4]
    finally:
        box.close()


# 배경 검출이 반출로 나오더라도 현재 포장 대조 위치를 함께 전송한다.
def test_package_match_is_saved_when_interior_suggests_out(tmp_path, monkeypatch):
    box = Outbox(tmp_path, staged=True)
    detector = Mock()
    worker = StageWorker(box, 'MOVEMENT_REQUESTED', detector=detector)
    bounds = (.31, .28, .59, .55)
    monkeypatch.setattr('medikit_pi.stages.match_package_interior', Mock(return_value=bounds))
    try:
        first = box.begin_session('test-box')
        box.save_capture(first, 'interior', photo())
        box.finish_session(first)
        detector.predict.return_value = [Detection((.09, .11, .9, .91), .6, 'box')]
        worker.step()
        second = box.begin_session('test-box')
        box.save_capture(second, 'package', photo())
        box.save_capture(second, 'interior', photo())
        box.finish_session(second)
        detector.predict.side_effect = [detector.predict.return_value, []]
        worker.step()
        transport = Mock()
        sync = StageWorker(box, 'MOVEMENT_REQUESTED', transport=transport)
        sync.step()
        sync.step()
        result = transport.upload_stage.call_args.args[0]['movement']
        assert result['candidate']['direction'] == 'OUT'
        assert result['package_match']['candidate']['box'] == list(bounds)
    finally:
        box.close()


# 파일 저장 뒤 큐 기록이 빠진 장애를 재시작 시 복구한다.
def test_recover_missing_stage(tmp_path):
    box = Outbox(tmp_path, staged=True)
    session = box.begin_session('test-box')
    box.save_capture(session, 'package', photo())
    with box._db:
        box._db.execute('DELETE FROM capture_stages')
    box.close()
    restored = Outbox(tmp_path, staged=True)
    try:
        assert restored.stage_head('OCR_REQUESTED')['session_id'] == session
        assert "movement_policy" not in restored.head()
    finally:
        restored.close()


# 단계 실패는 재시도 안내를 한 번만 표시하고 예외에 포함된 정보를 노출하지 않는다.
def test_stage_failure_log_is_safe_and_not_repeated(caplog):
    worker = Mock()
    worker.step.side_effect = RuntimeError("synthetic-secret")
    stop = Mock()
    stop.is_set.side_effect = [False, False, True]
    with caplog.at_level(logging.WARNING):
        stage_loop(worker, stop)
    assert caplog.text.count("AWS 전송 또는 내부 분석 실패. 자동 재시도합니다") == 1
    assert "synthetic-secret" not in caplog.text


# AWS 전송이 성공한 뒤에만 포장 단계 성공 메시지를 남긴다.
def test_upload_success_log_requires_success(tmp_path, caplog):
    box = Outbox(tmp_path, staged=True)
    transport = Mock()
    try:
        session = box.begin_session("test-box")
        box.save_capture(session, "package", photo())
        worker = StageWorker(box, "OCR_REQUESTED", transport=transport)
        transport.upload_stage.side_effect = ConnectionError()
        with caplog.at_level(logging.INFO):
            try:
                worker.step()
            except ConnectionError:
                pass
            assert "AWS 전송 성공" not in caplog.text
            transport.upload_stage.side_effect = None
            worker.step()
        assert "AWS 전송 성공: 약품 포장" in caplog.text
    finally:
        box.close()
