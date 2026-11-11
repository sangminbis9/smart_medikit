"""운영 OCR의 Pi 연결이 성공 기록·멱등성과 비-Pi 호환을 유지하는지 검사한다."""

from unittest.mock import Mock

import pytest
from backend.pi_contract import (
    bind_pi_event,
    official_form,
    pi_completed,
    save_pi_result,
    save_result,
)
from boto3.dynamodb.types import TypeDeserializer


# 합성 DynamoDB 응답으로 저장한 인식 결과를 다음 조회에서 재사용한다.
def bridge_store():
    store = Mock()
    store.table_name = "synthetic-table"
    saved = {}

    # 저장된 완료 이벤트만 반환해 중복 OCR 조회를 재현한다.
    def get_item(**request):
        return {"Item": saved.get("event", {})}

    # 직렬화된 실제 요청을 보관하여 중복 저장 여부를 검사한다.
    def put_item(**request):
        saved["event"] = request["Item"]

    store.client.get_item.side_effect = get_item
    store.client.put_item.side_effect = put_item
    return store, saved


# 메타데이터가 없는 기존 OCR과 세션이 일치하는 Pi 입력을 구분한다.
def test_source_binding():
    assert bind_pi_event({}, "bucket", "ocr-input/old.jpg", "legacy") == ("legacy", None)
    metadata = {"Metadata": {"event-id": "event-1", "session-id": "session-1"}, "ETag": "digest"}
    event, source = bind_pi_event(metadata, "bucket", "ocr-input/session-1/package.jpg", "legacy")
    assert event == "event-1" and source["session_id"] == "session-1"
    with pytest.raises(ValueError):
        bind_pi_event(metadata, "bucket", "ocr-input/other/package.jpg", "legacy")


# 완료를 남겨 Pi 결합을 가능하게 하고 중복 전송에서 제품·이력을 다시 쓰지 않는다.
def test_success_is_persisted_once():
    store, saved = bridge_store()
    result = {
        "processing_status": "COMPLETED",
        "medicines": [
            {"product_code": "000000001", "name": "합성시험정500밀리그램", "ingredients": []}
        ],
    }
    source = {"bucket": "bucket", "key": "ocr-input/session-1/package.jpg", "etag": "digest"}
    save_pi_result(store, "event-1", source, result)
    decoder = TypeDeserializer()
    item = {key: decoder.deserialize(value) for key, value in saved["event"].items()}
    assert item["processing_status"] == "COMPLETED"
    assert item["medicines"][0]["form"] == "TABLET"
    assert "ingredients" not in item["medicines"][0]
    save_pi_result(store, "event-1", source, result)
    store._save_medicine.assert_called_once()
    store.client.put_item.assert_called_once()
    with pytest.raises(ValueError):
        pi_completed(store, "event-1", {**source, "etag": "different"})


# 비-Pi 경로는 기존 저장 메서드와 실패 스냅샷 인자를 그대로 사용한다.
def test_legacy_save_unchanged():
    store = Mock()
    result = {"processing_status": "COMPLETED"}
    save_result(store, None, "legacy", result, "time", {"prior": True})
    store.save_result.assert_called_once_with("legacy", result, "time", {"prior": True})


# 공식 제품명에 명시되지 않은 제형을 숫자나 성분명으로 추정하지 않는다.
@pytest.mark.parametrize(
    ("name", "form"),
    [
        ("시험캡슐100밀리그램", "CAPSULE"),
        ("시험정(성분명)", "TABLET"),
        ("안정액", "OTHER"),
        ("시험연고", "OTHER"),
    ],
)
def test_form_classification(name, form):
    assert official_form({"name": name}) == form
