"""순서·지연·복구·배포 호환과 수량 연결의 회귀를 검사한다."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock

import pytest
from activity_api import ApiError, confirm_initial
from capture_events import apply_late_position, list_reviews, reconcile, resolve_review
from deploy_pipeline_fixes import patch_ocr
from inventory_events import transition
from read_api import read_inventory


# 동일 세션의 세 단계와 완료 인식을 가진 합성 저장소를 준비한다.
def store_fixture():
    store = Mock()
    store.encode.side_effect = dict
    store.records.return_value = []
    record = {
        "sequence": 2,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "SESSION_CLOSED": {
            "package_capture_ok": True,
            "interior_capture_ok": True,
            "session_id": "s2",
            "event_id": "e2",
            "sequence": 2,
            "box_id": "test",
            "occurred_at": "2026-11-11T00:00:00Z",
        },
        "MOVEMENT_REQUESTED": {
            "session_id": "s2",
            "event_id": "e2",
            "sequence": Decimal(2),
            "box_id": "test",
            "captured_at": "2026-11-11T00:00:01Z",
            "movement": {"candidate": {"direction": "IN"}},
        },
    }
    store.activity.get_item.return_value = {"Item": record}
    medicine = {"product_code": "000000001", "name": "합성 시험정", "form": "TABLET"}
    store.inventory.get_item.return_value = {
        "Item": {"processing_status": "COMPLETED", "medicines": [medicine]}
    }
    store.inventory.scan.return_value = {"Items": []}
    return store, record, medicine


# 배포 OCR의 참조 코드 형식도 제품 캐시와 결합해 포장을 생성한다.
def test_product_code_only_recognition():
    store, _, medicine = store_fixture()
    store.inventory.get_item.side_effect = [
        {"Item": {"processing_status": "COMPLETED", "product_codes": ["000000001"]}},
        {"Item": medicine},
    ]
    assert reconcile(store, "test", "e2") == "COMPLETED"
    writes = store.client.transact_write_items.call_args.kwargs["TransactItems"]
    assert writes[0]["Put"]["Item"]["product_codes"] == ["000000001"]
    assert writes[-1]["Update"]["Key"]["sk"] == "CAPTURE#e2"


# 늦게 끝난 선행 촬영을 건너뛰지 않되 확인 대기는 다른 촬영을 막지 않는다.
def test_earlier_capture_holds_until_review():
    store, _, _ = store_fixture()
    earlier = {"sequence": 1, "processing_status": "PROCESSING"}
    store.records.return_value = [earlier]
    assert reconcile(store, "test", "e2") == "PROCESSING"
    store.client.transact_write_items.assert_not_called()
    earlier["processing_status"] = "REVIEW_REQUIRED"
    assert reconcile(store, "test", "e2") == "COMPLETED"


# 오프라인 촬영 날짜가 오래돼도 방금 도착한 단계는 기다린다.
def test_timeout_uses_receipt_not_capture_time():
    store, record, _ = store_fixture()
    record.pop("MOVEMENT_REQUESTED")
    record["SESSION_CLOSED"]["occurred_at"] = "2020-01-01T00:00:00Z"
    assert reconcile(store, "test", "e2") == "PROCESSING"
    record["received_at"] = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
    assert reconcile(store, "test", "e2") == "REVIEW_REQUIRED"
    assert (
        store.activity.update_item.call_args.kwargs["ExpressionAttributeValues"][":reason"]
        == "STAGE_TIMEOUT"
    )


# 위 포장 반출로 아래 후보가 보인 경우 인식 제품의 보관 상태를 결합한다.
def test_occlusion_resolves_to_existing_package_out():
    store, record, medicine = store_fixture()
    record["MOVEMENT_REQUESTED"]["movement"]["candidate"]["direction"] = "STATE"
    first = {
        **record["MOVEMENT_REQUESTED"],
        "sequence": 1,
        "event_id": "e1",
        "movement": {"candidate": {"direction": "IN"}},
    }
    package = transition(None, dict(medicine, session_id="s2"), first)[0]
    store.inventory.scan.return_value = {"Items": [package]}
    assert reconcile(store, "test", "e2") == "COMPLETED"
    writes = store.client.transact_write_items.call_args.kwargs["TransactItems"]
    assert writes[0]["Put"]["Item"]["state"] == "OUT"
    assert writes[1]["Put"]["Item"]["status"] == "PENDING"


# 확인된 닫힘과 일치하는 포장 OCR을 가진 새 촬영으로 바꾼다.
def inventory_state_fixture():
    store, record, medicine = store_fixture()
    record["SESSION_CLOSED"].update(
        movement_policy="INVENTORY_STATE",
        closed_at="2026-11-11T00:00:05Z",
        package_image_key="ocr-input/s2/package.jpg",
    )
    raw = store.inventory.get_item.return_value["Item"]
    raw["source"] = {"key": "ocr-input/s2/package.jpg", "session_id": "s2"}
    return store, record, medicine, raw


# 늦은 단독 후보는 같은 반입에만 적용하고 중복 포장이면 적용하지 않는다.
def test_late_single_position_requires_current_unique_package():
    store, record, _, _ = inventory_state_fixture()
    record['processing_status'] = 'COMPLETED'
    record['MOVEMENT_REQUESTED']['movement'] = {
        'reason': 'NO_SINGLE_CHANGE',
        'single_visible': {'candidate': {'box': [.1, .1, .4, .4]},
                           'geometry': {'position': {'x': 40, 'y': 50, 'z_estimated': 0,
                                                     'certainty': 'ESTIMATED'},
                                        'size_class': 'SMALL'}},
    }
    package = {'pk': 'PACKAGE#p1', 'package_id': 'p1', 'state': 'STORED',
               'last_event_id': 'e2', 'last_sequence': 2}
    store.inventory.scan.return_value = {'Items': [package]}
    apply_late_position(store, 'test', 'e2', record)
    call = store.inventory.update_item.call_args.kwargs
    assert call['ExpressionAttributeValues'][':pos']['certainty'] == 'UNCERTAIN'
    assert call['ExpressionAttributeValues'][':seq'] == 2
    store.inventory.update_item.reset_mock()
    store.inventory.scan.return_value = {'Items': [package, {'state': 'STORED'}]}
    apply_late_position(store, 'test', 'e2', record)
    store.inventory.update_item.assert_not_called()


# 같은 이름의 다른 품목이나 폐기 이력은 신규 반입을 막지 않는다.
def test_inventory_state_adds_new_product_without_movement():
    store, record, medicine, _ = inventory_state_fixture()
    record.pop("MOVEMENT_REQUESTED")
    store.inventory.scan.return_value = {"Items": [
        {"name": medicine["name"], "product_codes": ["000000002"], "state": "STORED"},
        {"product_codes": [medicine["product_code"]], "state": "DISPOSED",
         "package_id": "retired", "last_sequence": 1, "inventory": {"estimated_qty": 0}},
    ]}
    assert reconcile(store, "test", "e2") == "COMPLETED"
    writes = store.client.transact_write_items.call_args.kwargs["TransactItems"]
    package = writes[0]["Put"]["Item"]
    assert package["state"] == "STORED" and package["package_id"] != "retired"
    assert package["inventory"]["estimated_qty"] is None
    assert writes[1]["Put"]["Item"]["kind"] == "INITIAL_REGISTRATION"


# 내부 후보가 반입이어도 보관 중인 품목은 반출하고 중복 응답은 전환하지 않는다.
def test_inventory_state_out_and_redelivery():
    store, record, medicine, _ = inventory_state_fixture()
    first = {**record["MOVEMENT_REQUESTED"], "sequence": 1, "event_id": "e1"}
    package = transition(None, dict(medicine, session_id="s2"), first)[0]
    package["inventory"]["estimated_qty"] = 8
    store.inventory.scan.return_value = {"Items": [package]}
    assert reconcile(store, "test", "e2") == "COMPLETED"
    writes = store.client.transact_write_items.call_args.kwargs["TransactItems"]
    assert writes[0]["Put"]["Item"]["state"] == "OUT"
    assert writes[0]["Put"]["Item"]["inventory"]["estimated_qty"] == 8
    assert writes[1]["Put"]["Item"]["status"] == "PENDING"
    assert writes[1]["Put"]["Item"]["occurredAt"] == record["SESSION_CLOSED"]["closed_at"]
    assert writes[2]["Put"]["Item"]["kind"] == "OUT_CONFIRMATION"
    assert writes[-1]["Update"]["ExpressionAttributeValues"][":review"] == "REVIEW_REQUIRED"
    record.update(processing_status="COMPLETED")
    assert reconcile(store, "test", "e2") == "COMPLETED"
    assert store.client.transact_write_items.call_count == 1


# 반출 중인 포장은 기존 수량과 만료일을 보존하고 같은 포장으로 되돌린다.
def test_inventory_state_return_preserves_stock():
    store, record, medicine, _ = inventory_state_fixture()
    record["MOVEMENT_REQUESTED"]["movement"] = {"candidate": None, "reason": "NO_SINGLE_CHANGE"}
    package = {
        "pk": "PACKAGE#p1", "sk": "META", "package_id": "p1", "name": medicine["name"],
        "product_codes": [medicine["product_code"]], "state": "OUT", "box_id": "test",
        "last_sequence": 1, "active_out_event_id": "e1", "inventory": {"estimated_qty": 8},
        "expiry": {"actual_expiry": "2027-01-01", "verified": True},
    }
    store.inventory.scan.return_value = {"Items": [package]}
    assert reconcile(store, "test", "e2") == "COMPLETED"
    writes = store.client.transact_write_items.call_args.kwargs["TransactItems"]
    updated = writes[0]["Put"]["Item"]
    assert updated["state"] == "STORED" and updated["package_id"] == "p1"
    assert updated["inventory"] == package["inventory"] and updated["expiry"] == package["expiry"]
    assert "active_out_event_id" not in updated and len(writes) == 2
    assert "state" in writes[0]["Put"]["ExpressionAttributeNames"].values()


# OCR 지연과 사진 불일치를 구분하고 확인 대기를 뒤늦게 자동 전환하지 않는다.
def test_inventory_state_waits_for_ocr_and_keeps_review():
    store, record, _, raw = inventory_state_fixture()
    closed = record.pop("SESSION_CLOSED")
    assert reconcile(store, "test", "e2") == "PROCESSING"
    record["SESSION_CLOSED"] = closed
    result = store.inventory.get_item.return_value
    store.inventory.get_item.return_value = {}
    assert reconcile(store, "test", "e2") == "PROCESSING"
    store.inventory.get_item.return_value = result
    raw["source"]["session_id"] = "other-session"
    assert reconcile(store, "test", "e2") == "REVIEW_REQUIRED"
    assert store.activity.update_item.call_args.kwargs["ExpressionAttributeValues"][":reason"] == "RECOGNITION_UNCERTAIN"
    raw["source"]["session_id"] = "s2"
    record["processing_status"] = "REVIEW_REQUIRED"
    assert reconcile(store, "test", "e2") == "REVIEW_REQUIRED"
    store.client.transact_write_items.assert_not_called()


# 같은 품목의 활성 포장이 둘이면 어느 포장도 자동 반출하지 않는다.
def test_inventory_state_multiple_active_packages_require_review():
    store, _, medicine, _ = inventory_state_fixture()
    stored = {"name": medicine["name"], "product_codes": [medicine["product_code"]], "state": "STORED"}
    store.inventory.scan.return_value = {"Items": [dict(stored, package_id="a"), dict(stored, package_id="b")]}
    assert reconcile(store, "test", "e2") == "REVIEW_REQUIRED"
    assert store.activity.update_item.call_args.kwargs["ExpressionAttributeValues"][":reason"] == "MULTIPLE_PACKAGES"
    store.client.transact_write_items.assert_not_called()


# 종료 사진이 없는 실패도 변경 없음으로 닫고 동일 응답을 재요청할 수 있다.
def test_review_none_is_idempotent():
    store, record, _ = store_fixture()
    record.pop("SESSION_CLOSED")
    record["processing_status"] = "REVIEW_REQUIRED"
    body = {"direction": "NONE", "productCode": None}
    assert resolve_review(store, "test", "e2", body)["processing_status"] == "COMPLETED"
    record.update(processing_status="COMPLETED", user_decision=body)
    assert resolve_review(store, "test", "e2", body)["processing_status"] == "COMPLETED"
    assert store.client.transact_write_items.call_count == 1
    with pytest.raises(ApiError):
        resolve_review(store, "test", "e2", {"direction": "OUT", "productCode": "000000001"})


# NEW 수량 미확인은 나중에 보완하고 알려진 재고를 덮어쓰지 않는다.
def test_initial_quantity_backfill():
    store = Mock()
    store.form.return_value = "TABLET"
    store.package.return_value = {
        "first_registration_pending": False,
        "inventory": {"stock_enabled": True, "estimated_qty": None, "initial_qty": None},
    }
    confirm_initial(
        store, {"box": "test"}, "p1", {"condition": "NEW", "actualExpiry": None, "remainingQty": 10}
    )
    values = store.initial.call_args.args[2]
    assert values[":inventory"]["initial_qty"] == values[":inventory"]["estimated_qty"] == 10
    assert values[":old_inventory"]["estimated_qty"] is None


# 인식 이력이 앞 페이지를 채워도 세 번째 페이지의 제품과 포장을 끝까지 찾는다.
def test_inventory_reads_beyond_two_pages():
    table = Mock()
    table.scan.side_effect = [
        {"Items": [], "LastEvaluatedKey": {"pk": "a"}},
        {"Items": [], "LastEvaluatedKey": {"pk": "b"}},
        {"Items": []},
    ]
    assert read_inventory(table, "test")["inventory_status"] == "CONFIRMED"
    assert table.scan.call_count == 3


# 모르는 운영 OCR 버전은 자동 치환하지 않는다.
def test_ocr_patch_rejects_unknown_version():
    with pytest.raises(ValueError, match="UNREVIEWED_OCR_VERSION"):
        patch_ocr("unknown source")


# 사진·원문을 제외한 촬영 확인 항목만 웹에 노출한다.
def test_review_view_has_no_photo():
    store, record, _ = store_fixture()
    record.update(sk="CAPTURE#e2", processing_status="REVIEW_REQUIRED", secret_photo="never-return")
    store.records.return_value = [record]
    assert set(list_reviews(store, "test")[0]) == {"eventId", "occurredAt", "reason"}


# 실제 저장할 3D 좌표가 mm와 Decimal 계약을 따르는지 검사한다.
def test_geometry_reaches_inventory():
    store, record, medicine = store_fixture()
    movement = record["MOVEMENT_REQUESTED"]
    movement["movement"].update(
        geometry={
            "position": {"x": 152.5, "y": 82.5, "z_estimated": 0, "certainty": "UNCERTAIN"},
            "size_class": "MEDIUM",
        }
    )
    movement["movement"]["candidate"]["box"] = [0.4, 0.4, 0.6, 0.6]
    package = transition(None, dict(medicine, session_id="s2"), movement)[0]
    assert package["position"]["x"] == Decimal("152.5")
    assert package["position"]["certainty"] == "UNCERTAIN"
    assert package["size_class"] == "MEDIUM"


# Pi 전용 matcher 의존성도 실제 배포 대상 모듈 목록에 포함한다.
def test_package_search_module_is_in_deployment_bundle():
    from deploy_pipeline_fixes import PACKAGE_OCR_MODULES

    assert 'package_matching' in PACKAGE_OCR_MODULES
