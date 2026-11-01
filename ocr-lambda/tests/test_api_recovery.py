"""공식 API 장애 복구와 중복 이벤트의 정보 보완을 AWS 없이 검증한다."""

import copy
import io
import json
import socket
import ssl
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import URLError

import pytest
from botocore.exceptions import ClientError
from test_pipeline import FakeDynamo, decode, dependencies, event, storage_case
from test_recognition import api_for, line, product

from backend.diagnostics import Diagnostics
from backend.image_ocr import group_lines
from backend.lambda_function import process_records
from backend.mfds import ApiError, MfdsClient
from backend.recognition import enrich_medicines, identify, needs_enrichment_retry
from backend.storage import DynamoStore, attributes, source_fingerprint


# API가 정상적으로 빈 검색 결과를 반환하는 합성 JSON을 만든다.
def empty_response():
    return json.dumps(
        {"header": {"resultCode": "00"}, "body": {"totalCount": 0, "items": []}}
    ).encode()


# 시간 초과·서버 일시 장애만 재시도하고 성공 응답을 같은 호출에서 재사용한다.
@pytest.mark.parametrize("code", ["API_TIMEOUT", "API_HTTP_429", "API_HTTP_503", "API_01"])
def test_transient_api_retry_and_cache(code):
    transport = Mock(side_effect=[ApiError(code, True), empty_response()])
    sleep = Mock()
    api = MfdsClient("synthetic-key", transport=transport, sleep=sleep)
    assert api.search("가상약") == []
    assert api.search("가상약") == []
    assert transport.call_count == 2 and sleep.call_count == 1


# 인증·스키마 오류는 동일 요청을 반복하지 않는다.
@pytest.mark.parametrize("code", ["API_30", "API_INVALID_RESPONSE", "API_TLS_ERROR"])
def test_permanent_api_error_not_retried(code):
    transport = Mock(side_effect=ApiError(code))
    api = MfdsClient("synthetic-key", transport=transport, sleep=Mock())
    with pytest.raises(ApiError, match=code):
        api.search("가상약")
    assert transport.call_count == 1
    api.sleep.assert_not_called()


# 시도·이벤트 요청 상한과 남은 Lambda 시간을 모두 지킨다.
def test_api_attempt_and_time_budgets():
    transport = Mock(side_effect=ApiError("API_TIMEOUT", True))
    api = MfdsClient("synthetic-key", transport=transport, sleep=Mock())
    with pytest.raises(ApiError, match="API_TIMEOUT"):
        api.search("가상약")
    assert transport.call_count == 3
    api.begin_event(SimpleNamespace(get_remaining_time_in_millis=Mock(return_value=15000)))
    with pytest.raises(ApiError, match="API_TIME_BUDGET_EXHAUSTED"):
        api.search("새약")
    assert transport.call_count == 3
    api.begin_event()
    api.request_count = 64
    with pytest.raises(ApiError, match="API_REQUEST_LIMIT"):
        api.search("새약")
    assert transport.call_count == 3


# URL의 민감한 내용은 버리고 원인 종류만 안전한 오류 코드로 바꾼다.
@pytest.mark.parametrize(
    "error,code",
    [
        (URLError(socket.gaierror("secret-value")), "API_DNS_ERROR"),
        (URLError(TimeoutError("secret-value")), "API_TIMEOUT"),
        (URLError(ssl.SSLError("secret-value")), "API_TLS_ERROR"),
        (URLError(ConnectionRefusedError("secret-value")), "API_CONNECTION_ERROR"),
        (TimeoutError("secret-value"), "API_TIMEOUT"),
    ],
)
def test_transport_errors_are_classified(monkeypatch, error, code):
    monkeypatch.setattr("backend.mfds.urlopen", Mock(side_effect=error))
    with pytest.raises(ApiError, match=code) as caught:
        MfdsClient("synthetic-key")._download("https://apis.data.go.kr/?serviceKey=secret-value")
    assert "secret-value" not in str(caught.value)


# API 진단에는 인증키·검색어·주소·응답을 넣을 수 없다.
def test_api_diagnostics_are_numeric_and_allowlisted(caplog):
    api = MfdsClient("synthetic-key", transport=Mock(return_value=empty_response()))
    api.begin_event(diagnostics=Diagnostics("safe-event"))
    api.search("가상환자정보")
    Diagnostics("safe-event").api_request("private-url", 1, 0.1, "PRIVATE_SECRET")
    assert "synthetic-key" not in caplog.text and "가상환자정보" not in caplog.text
    assert "private-url" not in caplog.text and "PRIVATE_SECRET" not in caplog.text
    assert "api_request" in caplog.text and "elapsed_ms" in caplog.text


# 최초 식별은 성공하되 성분 API만 일시 실패한 이벤트 스냅샷을 만든다.
def incomplete_result():
    api = api_for(product())
    api.ingredients.side_effect = ApiError("API_TIMEOUT", True)
    return identify(group_lines([line("지르텍정")]), api)


# 재처리는 OCR·검색·이미 완전한 허가정보를 반복하지 않고 성분만 복구한다.
def test_completed_event_resumes_missing_enrichment():
    deps = dependencies()
    previous = incomplete_result()
    deps["store"].load_completed.return_value = previous
    assert process_records(event(), **deps) == {"processed": 1, "skipped": 0}
    deps["ocr"].assert_not_called()
    deps["api"].search.assert_not_called()
    deps["api"].product_info.assert_not_called()
    deps["api"].ingredients.assert_called_once()
    result = deps["store"].finish.call_args.args[2]
    assert result["medicines"][0]["enrichment_status"] == "SUCCEEDED"
    assert previous["medicines"][0]["enrichment_status"] == "INCOMPLETE"
    assert deps["store"].finish.call_args.kwargs["previous"] == previous
    deps["store"].fail.assert_not_called()
    assert deps["s3"].get_object.return_value["Body"].closed


# 정보 복구는 두 번까지만 시도하며 완료된 식별을 실패로 덮어쓰지 않는다.
@pytest.mark.parametrize("attempts", [0, 1, 2])
def test_enrichment_resume_bound(attempts):
    deps = dependencies()
    previous = {**incomplete_result(), "enrichment_attempts": attempts}
    deps["store"].load_completed.return_value = previous
    deps["api"].ingredients.side_effect = ApiError("API_TIMEOUT", True)
    if attempts == 0:
        with pytest.raises(RuntimeError, match="RECOGNITION_BATCH_FAILED"):
            process_records(event(), **deps)
    else:
        process_records(event(), **deps)
    assert deps["api"].ingredients.call_count == (0 if attempts == 2 else 1)
    deps["store"].fail.assert_not_called()
    deps["ocr"].assert_not_called()


# 실제 정보 누락과 영구 오류를 자동 재시도 상태로 승격하지 않는다.
@pytest.mark.parametrize(
    "status,error",
    [("PARTIAL", "API_INGREDIENT_FIELDS_MISSING"), ("NO_DATA", None), ("UNAVAILABLE", "API_30")],
)
def test_nontransient_enrichment_not_replayed(status, error):
    result = incomplete_result()
    result["medicines"][0].update(
        ingredients_status=status, ingredients_error_code=error, ingredients_retryable=False
    )
    assert not needs_enrichment_retry(result)


# 제품과 정보 보완 이벤트를 한 트랜잭션으로 갱신하고 식별 근거는 수정하지 않는다.
def test_enrichment_refresh_is_atomic_and_versioned():
    _, _, _, source = storage_case()
    client = Mock()
    store = DynamoStore(client)
    previous = incomplete_result()
    refreshed = copy.deepcopy(previous)
    enrich_medicines(refreshed["medicines"], api_for(product()), missing_only=True)
    assert store.finish("event", source, refreshed, "now", previous=previous)
    writes = client.transact_write_items.call_args.kwargs["TransactItems"]
    assert len(writes) == 2 and all("Update" in write for write in writes)
    marker = writes[-1]["Update"]
    values = decode(marker["ExpressionAttributeValues"])
    assert values[":revision"] == 0 and values[":next"] == 1
    assert values[":source"] == source_fingerprint(source)
    assert "#state=:completed" in marker["ConditionExpression"]
    assert "items=" not in marker["UpdateExpression"]
    assert "processing_status=" not in marker["UpdateExpression"]


# 동시에 진행한 정보 보완 중 최신 버전만 남기고 재등록하지 않는다.
def test_enrichment_concurrent_refresh_is_idempotent():
    _, _, _, source = storage_case()
    previous = incomplete_result()
    result = copy.deepcopy(previous)
    enrich_medicines(result["medicines"], api_for(product()), missing_only=True)
    client = Mock()
    client.transact_write_items.side_effect = ClientError(
        {"Error": {"Code": "TransactionCanceledException"}}, "TransactWriteItems"
    )
    client.get_item.return_value = {
        "Item": attributes(
            {**result, "source_fingerprint": source_fingerprint(source), "enrichment_attempts": 1}
        )
    }
    assert not DynamoStore(client).finish("event", source, result, "now", previous=previous)
    assert client.transact_write_items.call_count == 1


# 저장 단계에서 식별 결과를 다른 품목으로 바꾼 정보 보완을 거부한다.
def test_enrichment_cannot_change_identified_product():
    _, _, _, source = storage_case()
    previous = incomplete_result()
    result = copy.deepcopy(previous)
    result["medicines"][0]["name"] = "다른약품정"
    client = Mock()
    with pytest.raises(ValueError, match="ENRICHMENT_IDENTITY_CHANGED"):
        DynamoStore(client).finish("event", source, result, "now", previous=previous)
    client.transact_write_items.assert_not_called()


# 정보 보완 트랜잭션의 버전 조건만 모사하여 실제 이벤트 재호출 흐름을 검증한다.
class RecoveryDynamo(FakeDynamo):
    # 최초 저장은 기존 대역을 쓰고 완료 이벤트 갱신은 버전 일치를 요구한다.
    def transact_write_items(self, **request):
        writes = request["TransactItems"]
        if "Put" in writes[-1]:
            return super().transact_write_items(**request)
        marker = writes[-1]["Update"]
        key = decode(marker["Key"])["pk"]
        old = decode(self.items[key])
        values = decode(marker["ExpressionAttributeValues"])
        if old.get("enrichment_attempts", 0) != values[":revision"]:
            raise ClientError(
                {"Error": {"Code": "TransactionCanceledException"}}, "TransactWriteItems"
            )
        self.transactions.append(request)
        old.update(
            medicines=values[":medicines"],
            enrichment_attempts=values[":next"],
            enrichment_checked_at=values[":checked"],
        )
        self.items[key] = attributes(old)


# 최초 저장 후 같은 S3 이벤트를 재전달하면 OCR은 한 번만 하고 정보만 완성한다.
def test_real_store_replay_recovers_without_reregistering():
    deps = dependencies({"event-id": "recovery-event"})
    client = RecoveryDynamo()
    deps["store"] = DynamoStore(client)
    success = deps["api"].ingredients.return_value
    deps["api"].ingredients.side_effect = [ApiError("API_TIMEOUT", True), success]
    with pytest.raises(RuntimeError, match="RECOGNITION_BATCH_FAILED"):
        process_records(event(), **deps)
    saved = decode(client.items["RECOGNITION#recovery-event"])
    assert saved["processing_status"] == "COMPLETED"
    assert saved["medicines"][0]["enrichment_status"] == "INCOMPLETE"
    for _ in range(2):
        deps["s3"].get_object.return_value["Body"] = io.BytesIO(b"image")
        process_records(event(), **deps)
    latest = decode(client.items["RECOGNITION#recovery-event"])
    assert latest["medicines"][0]["enrichment_status"] == "SUCCEEDED"
    assert latest["enrichment_attempts"] == 1
    assert latest["items"] == saved["items"] and latest["processed_at"] == saved["processed_at"]
    assert len(client.transactions) == 2
    assert deps["ocr"].call_count == 1 and deps["api"].ingredients.call_count == 2
    assert not client.fail_puts


# 장애가 계속되어도 최초 저장과 두 번의 보완까지만 수행하고 재등록하지 않는다.
def test_real_store_repeated_outage_stops_after_two_refreshes():
    deps = dependencies({"event-id": "recovery-event"})
    client = RecoveryDynamo()
    deps["store"] = DynamoStore(client)
    deps["api"].ingredients.side_effect = ApiError("API_TIMEOUT", True)
    for attempt in range(4):
        deps["s3"].get_object.return_value["Body"] = io.BytesIO(b"image")
        if attempt < 2:
            with pytest.raises(RuntimeError, match="RECOGNITION_BATCH_FAILED"):
                process_records(event(), **deps)
        else:
            process_records(event(), **deps)
    saved = decode(client.items["RECOGNITION#recovery-event"])
    assert saved["enrichment_attempts"] == 2
    assert saved["medicines"][0]["enrichment_status"] == "INCOMPLETE"
    assert len(client.transactions) == 3 and not client.fail_puts
    assert deps["ocr"].call_count == 1 and deps["api"].ingredients.call_count == 3
