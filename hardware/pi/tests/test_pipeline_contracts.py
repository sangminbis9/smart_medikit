"""Pi와 서버 경계의 정상 경로 및 아직 수정하지 않은 종단 장애를 재현한다."""

import io
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest

# 서버를 별도 프로세스로 배포하는 대신 동일 저장소의 계약 함수를 직접 연결한다.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
sys.path.append(str(Path(__file__).resolve().parents[3]))

from activity_api import ApiError, confirm_activity  # noqa: E402
from capture_events import receive_s3, reconcile  # noqa: E402
from inventory_events import disposal_due, transition  # noqa: E402
from medikit_pi.detection import Detection  # noqa: E402
from medikit_pi.movement import compare_interior  # noqa: E402
from medikit_pi.outbox import Outbox, SyncWorker  # noqa: E402
from notifications import lambda_handler as maintain  # noqa: E402
from read_api import package_view  # noqa: E402


# OCR이 실제로 저장하는 단일 제품과 Pi 이동 단계의 필드 형태를 재현한다.
def evidence(direction="IN", sequence=1):
    recognition = dict(
        session_id="audit-session", product_code="000000001", name="합성 점검 약품", form="TABLET"
    )
    movement = dict(
        session_id="audit-session",
        event_id=f"audit-{sequence}",
        box_id="audit-box",
        sequence=Decimal(sequence),
        captured_at="2026-11-11T00:00:00Z",
        movement={"candidate": {"direction": direction, "box": [0.1, 0.1, 0.3, 0.3]}},
    )
    return recognition, movement


# 단계 수신 저장과 OCR 조회만 모의하며 실제 처리 판단 함수는 교체하지 않는다.
def capture_store(closed=True, movement=True, recognized=True):
    recognition, observed = evidence()
    record = {}
    if closed:
        record["SESSION_CLOSED"] = dict(
            session_id="audit-session",
            package_capture_ok=True,
            interior_capture_ok=True,
            occurred_at="2020-01-01T00:00:00Z",
        )
    if movement:
        record["MOVEMENT_REQUESTED"] = observed
    store = Mock()
    store.activity.get_item.return_value = {"Item": record}
    store.inventory.get_item.return_value = (
        {"Item": {"processing_status": "COMPLETED", "medicines": [recognition]}}
        if recognized
        else {}
    )
    store.inventory.scan.return_value = {"Items": []}
    store.encode.side_effect = dict
    return store, record


# 신규 등록부터 반출·재반입까지 같은 제품을 유지하고 알림·웹 응답을 연결한다.
def test_happy_path_contracts(monkeypatch):
    monkeypatch.delenv("MEDKIT_NOTIFICATION_FUNCTION", raising=False)
    store, _ = capture_store()
    assert reconcile(store, "audit-box", "audit-1") == "COMPLETED"
    writes = store.client.transact_write_items.call_args.kwargs["TransactItems"]
    package = writes[0]["Put"]["Item"]
    assert writes[1]["Put"]["Item"]["kind"] == "INITIAL_REGISTRATION"
    assert package["state"] == "STORED" and package["first_registration_pending"]
    medicine = {"medicine_id": "000000001", "form": "TABLET", "name": package["name"]}
    view = package_view(package, {"000000001": medicine}, "audit-box")
    assert view["first_registration_pending"] and len(view["medicines"]) == 1
    # 수량이 알려진 포장으로 Y/N 입력 계약과 24시간 경계를 확인한다.
    package["inventory"]["estimated_qty"] = 10
    outgoing, event, notice = transition(package, *evidence("OUT", 2))
    assert event["status"] == "PENDING" and notice["kind"] == "OUT_CONFIRMATION"
    store.event.return_value = event
    store.package.return_value = outgoing
    store.form.return_value = "TABLET"
    confirmed = confirm_activity(
        store,
        {"box": "audit-box", "allow_estimated": True},
        "audit-2",
        {"result": "TAKEN", "quantity": 2},
    )
    assert confirmed["event"]["status"] == "TAKEN"
    assert store.confirm.call_args.args[-1] == {"estimated_qty": 10}
    confirm_activity(
        store,
        {"box": "audit-box", "allow_estimated": True},
        "audit-2",
        {"result": "NOT_TAKEN", "quantity": None},
    )
    assert store.confirm.call_args.args[-1] is None
    deadline = datetime(2026, 11, 12, tzinfo=timezone.utc)
    assert not disposal_due(outgoing, event, deadline - timedelta(seconds=1))
    assert disposal_due(outgoing, event, deadline)
    returned, _, notice = transition(outgoing, *evidence("IN", 3))
    assert not disposal_due(returned, event, deadline) and notice is None


# 처리 완료가 늦으면 Pi는 종료 객체를 다시 업로드하지 않고 같은 ID를 조회한다.
def test_ocr_wait_keeps_same_session(tmp_path):
    box = Outbox(tmp_path)
    try:
        sid = box.begin_session("audit-box")
        box.save_capture(sid, "package", b"synthetic-photo")
        event = box.finish_session(sid)
        transport = Mock()
        transport.status.return_value = "PROCESSING"
        worker = SyncWorker(box, transport)
        assert worker.step(0) == "UPLOADED"
        assert worker.step(3) == "PROCESSING"
        assert worker.step(86403) == "PROCESSING"
        assert box.head()["event_id"] == event["event_id"]
        transport.upload.assert_called_once()
        assert (box.root / sid / "package.jpg").exists()
    finally:
        box.close()


# OCR의 인식 성공만으로 3D 좌표나 포장 수량이 생기지 않는 현재 경계를 기록한다.
def test_new_package_has_no_geometry_or_quantity():
    package, _, _ = transition(None, *evidence())
    view = package_view(
        package, {"000000001": {"form": "TABLET", "name": "합성 약품"}}, "audit-box"
    )
    assert view["position"] is None and view["size_class"] is None
    assert view["inventory"]["estimated_qty"] is None


# 미수정 결함은 정상 통과로 세지 않으며 수정되면 XPASS로 점검 갱신을 요구한다.
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="PA-01: 종료 객체가 이동 단계보다 먼저 처리되면 조기 종료",
)
def test_late_movement_must_not_terminate_capture():
    store, _ = capture_store(movement=False)
    assert reconcile(store, "audit-box", "audit-1") == "PROCESSING"


# 첫 조회가 종료된 뒤 늦은 이동 단계가 오더라도 현재 수신기는 재고를 재결합하지 않는다.
@pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="PA-01: 늦은 MOVEMENT_REQUESTED 수신 시 재결합 없음"
)
def test_late_movement_must_reconcile_after_prior_poll(monkeypatch):
    monkeypatch.delenv("MEDKIT_NOTIFICATION_FUNCTION", raising=False)
    store, record = capture_store(movement=False)
    assert reconcile(store, "audit-box", "audit-1") == "REVIEW_REQUIRED"
    _, movement = evidence()
    movement.update(stage="MOVEMENT_REQUESTED", sequence=1)
    s3 = Mock()
    s3.get_object.return_value = {"Body": io.BytesIO(json.dumps(movement).encode())}

    # DynamoDB에 저장된 늦은 단계가 다음 조회에서 보이는 상황을 재현한다.
    def save_stage(**request):
        record[request["ExpressionAttributeNames"]["#stage"]] = request[
            "ExpressionAttributeValues"
        ][":payload"]

    store.activity.update_item.side_effect = save_stage
    receive_s3(
        {
            "Records": [
                {
                    "eventSource": "aws:s3",
                    "s3": {
                        "bucket": {"name": "audit-bucket"},
                        "object": {"key": "medkit-sessions/audit-session/movement-ready.json"},
                    },
                }
            ]
        },
        store,
        s3,
        "audit-box",
        "audit-bucket",
        "medkit-sessions/",
    )
    assert store.client.transact_write_items.call_count == 1


# 맨 위 큰 포장을 꺼내 아래의 작은 포장이 드러난 장면을 재현한다.
@pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="PA-02: 반출로 드러난 후보 하나를 새 반입으로 오분류"
)
def test_revealed_lower_package_must_not_be_inbound():
    before = np.zeros((100, 100, 3), np.uint8)
    after = before.copy()
    before[20:80, 20:80] = 100
    after[30:50, 30:50] = 100
    result = compare_interior(
        before,
        after,
        [Detection((0.2, 0.2, 0.8, 0.8), 0.9, "box")],
        [Detection((0.3, 0.3, 0.5, 0.5), 0.9, "box")],
        automatic_in=True,
    )
    assert not result["candidate"] or result["candidate"]["direction"] != "IN"


# OCR 이벤트 자체가 끝내 저장되지 않으면 오래된 세션도 무한 처리 중으로 남는다.
@pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="PA-03: OCR 결과 누락에 대한 종료·복구 경로 없음"
)
def test_missing_ocr_must_eventually_leave_processing():
    store, _ = capture_store(recognized=False)
    assert reconcile(store, "audit-box", "audit-1") != "PROCESSING"


# 기존 반출의 포장이 사라져도 다른 새 알림의 전송을 막아서는 안 된다.
@pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="PA-04: 폐기 점검 오류가 전체 알림 전송을 중단"
)
def test_orphaned_outbound_must_not_block_notification(monkeypatch):
    store = Mock()
    store.events.return_value = [{"packageId": "missing-package"}]
    store.package.side_effect = ApiError(404, "PACKAGE_NOT_FOUND", "합성 누락")
    monkeypatch.setattr("activity_api.configuration", Mock(return_value={"box": "audit-box"}))
    monkeypatch.setattr("activity_api.get_store", Mock(return_value=store))
    send = Mock()
    monkeypatch.setattr("notifications.deliver", send)
    try:
        maintain({}, None)
    except ApiError:
        pass
    send.assert_called_once()
