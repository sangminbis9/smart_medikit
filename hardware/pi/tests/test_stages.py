"""OCR 지연·오프라인과 관계없는 내부 판단과 영속 단계 복구를 검사한다."""

from unittest.mock import Mock
import cv2
import numpy as np
from medikit_pi.outbox import Outbox
from medikit_pi.stages import StageWorker
from medikit_pi.detection import Detection


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
    finally:
        restored.close()
