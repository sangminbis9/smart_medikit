"""S3 처리·실패 이력·원자 저장·중복 방지를 AWS 없이 검증한다."""

import copy
import io
import json
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import pytest
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError
from test_recognition import api_for, line, product

from backend.image_ocr import ImageError, group_lines
from backend.lambda_function import process_records
from backend.recognition import ApiError, MfdsClient, identify
from backend.storage import DynamoStore


# DynamoDB 속성 맵을 테스트 단언에 사용할 일반 값으로 변환한다.
def decode(values):
    return {key: TypeDeserializer().deserialize(value) for key, value in values.items()}


# 모든 테스트에 동일한 가상 S3 생성 이벤트를 제공한다.
def event():
    return {
        "Records": [
            {
                "eventSource": "aws:s3",
                "eventName": "ObjectCreated:Put",
                "s3": {
                    "bucket": {"name": "test-bucket"},
                    "object": {"key": "ocr-input/test.jpg", "eTag": "abc", "sequencer": "001"},
                },
            }
        ]
    }


# 메타데이터 유무와 무관하게 동일한 OCR 호출을 만드는 입력을 준비한다.
def dependencies(metadata=None):
    s3, store = Mock(), Mock()
    s3.get_object.return_value = {
        "Body": io.BytesIO(b"image"),
        "Metadata": metadata or {},
        "ContentLength": 5,
    }
    store.completed.return_value = False
    store.finish.return_value = True
    return {
        "s3": s3,
        "store": store,
        "api": api_for(product()),
        "ocr": Mock(return_value=group_lines([line("지르텍정")])),
        "bucket": "test-bucket",
    }


# 기존 문서 종류 메타데이터를 주더라도 공통 인식 함수의 인자는 동일하다.
@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"document-type": "OTC"},
        {"document-type": "PRESCRIPTION"},
        {"document-type": "AUTO"},
        {"document-type": "unknown"},
    ],
)
def test_single_pipeline_ignores_document_metadata(metadata):
    deps = dependencies(metadata)
    assert process_records(event(), **deps) == {"processed": 1, "skipped": 0}
    deps["ocr"].assert_called_once_with(b"image")
    deps["store"].fail.assert_not_called()


# 실패는 이벤트 이력에만 기록하고 성공 약품 저장 함수는 호출하지 않는다.
@pytest.mark.parametrize(
    "failure,expected_stage,expected_code",
    [
        ("image", "ocr", "INVALID_IMAGE"),
        ("ocr", "ocr", "OCR_SYSTEM_ERROR"),
        ("name", "extraction", "NO_PRODUCT_NAME"),
        ("confidence", "extraction", "LOW_OCR_CONFIDENCE"),
        ("matching", "matching", "PRODUCT_NOT_FOUND"),
        ("api", "matching", "API_TIMEOUT"),
        ("schema", "matching", "API_PRODUCT_SCHEMA_INVALID"),
    ],
)
def test_failure_saves_only_safe_event(failure, expected_stage, expected_code, caplog):
    deps = dependencies({"event-id": "test-event-01"})
    if failure == "image":
        deps["ocr"].side_effect = ImageError("INVALID_IMAGE")
    elif failure == "ocr":
        deps["ocr"].side_effect = RuntimeError("환자이름 개인정보 OCR원문 API비밀키")
    elif failure == "name":
        deps["ocr"].return_value = []
    elif failure == "confidence":
        deps["ocr"].return_value = group_lines([line("지르텍정", score=0.5)])
    elif failure == "matching":
        deps["api"].search.return_value = []
    elif failure == "api":
        deps["api"].search.side_effect = ApiError("API_TIMEOUT", True)
    else:
        deps["api"].search.return_value = [{"ITEM_NAME": "지르텍정"}]
    with pytest.raises(RuntimeError, match="RECOGNITION_BATCH_FAILED"):
        process_records(event(), **deps)
    deps["store"].finish.assert_not_called()
    args = deps["store"].fail.call_args.args
    assert args[0] == "test-event-01"
    assert args[2:4] == (expected_stage, expected_code)
    assert "환자이름" not in caplog.text and "지르텍" not in caplog.text
    assert "test-event-01" in caplog.text and expected_code in caplog.text


# 선택적 API 장애에도 완전한 필수 필드를 가진 성공 결과는 저장한다.
def test_optional_api_failure_stores_success(caplog):
    deps = dependencies()
    deps["api"].ingredients.side_effect = ApiError("API_TIMEOUT", True)
    process_records(event(), **deps)
    result = deps["store"].finish.call_args.args[2]
    assert result["processing_status"] == "COMPLETED"
    assert result["medicines"][0]["ingredients_status"] == "UNAVAILABLE"
    deps["store"].fail.assert_not_called()
    assert '"stage": "ingredients"' in caplog.text


# 중복 성공은 본문·OCR·제품 저장을 생략하고 S3 스트림을 닫는다.
def test_duplicate_skips_recognition_and_closes_body():
    deps = dependencies()
    deps["store"].completed.return_value = True
    assert process_records(event(), **deps) == {"processed": 0, "skipped": 1}
    deps["ocr"].assert_not_called()
    deps["store"].finish.assert_not_called()
    assert deps["s3"].get_object.return_value["Body"].closed


# 허용되지 않은 버킷·경로·파일은 저장 함수를 호출하지 않는다.
def test_unrelated_s3_object_is_ignored():
    incoming = event()
    incoming["Records"][0]["s3"]["object"]["key"] = "private/manifest.json"
    deps = dependencies()
    assert process_records(incoming, **deps)["skipped"] == 1
    deps["s3"].get_object.assert_not_called()
    deps["store"].fail.assert_not_called()


# 조건부 저장의 동시 실행과 실패를 재현하는 최소 DynamoDB 테스트 대역이다.
class FakeDynamo:
    # 성공 전 취소·성공 후 응답 유실을 각각 주입할 수 있게 준비한다.
    def __init__(self):
        self.items = {}
        self.transactions = []
        self.fail_puts = []
        self.cancel_next = False
        self.lose_ack = False

    # 강한 일관성 조회 요청을 확인하고 현재 이벤트를 반환한다.
    def get_item(self, **request):
        assert request["ConsistentRead"] is True
        return {"Item": self.items.get(decode(request["Key"])["pk"], {})}

    # 완료된 이벤트에 대한 실패 저장을 조건 실패로 거부한다.
    def put_item(self, **request):
        self.fail_puts.append(request)
        item = decode(request["Item"])
        old = decode(self.items.get(item["pk"], {}))
        if old.get("processing_status") == "COMPLETED":
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        self.items[item["pk"]] = request["Item"]

    # 트랜잭션 전체 성공·전체 취소·응답 유실만 재현하고 AWS 실행으로 주장하지 않는다.
    def transact_write_items(self, **request):
        self.transactions.append(request)
        writes = request["TransactItems"]
        marker = decode(writes[-1]["Put"]["Item"])
        old = decode(self.items.get(marker["pk"], {}))
        if self.cancel_next or old.get("processing_status") == "COMPLETED":
            self.cancel_next = False
            raise ClientError(
                {"Error": {"Code": "TransactionCanceledException"}}, "TransactWriteItems"
            )
        for write in writes[:-1]:
            key = decode(write["Update"]["Key"])
            self.items[key["pk"]] = write["Update"]["Key"]
        self.items[marker["pk"]] = writes[-1]["Put"]["Item"]
        if self.lose_ack:
            self.lose_ack = False
            raise TimeoutError("민감한 SDK 오류 원문")


# 중복·재시도에 사용할 저장소와 필수 필드가 검증된 성공 결과를 만든다.
def storage_case():
    client = FakeDynamo()
    store = DynamoStore(client)
    result = identify(group_lines([line("지르텍정")]), api_for(product()))
    source = {
        "bucket": "test-bucket",
        "key": "ocr-input/test.jpg",
        "version_id": None,
        "etag": "abc",
    }
    return client, store, result, source


# 제품 키·이벤트 키와 필수 필드를 하나의 트랜잭션에 포함한다.
def test_atomic_success_uses_existing_keys_and_fields():
    client, store, result, source = storage_case()
    assert store.finish("event-01", source, result, "2026-10-31T00:00:00+00:00")
    assert len(client.transactions) == 1 and not client.fail_puts
    writes = client.transactions[0]["TransactItems"]
    assert len(writes) == 2
    update = writes[0]["Update"]
    assert decode(update["Key"]) == {"pk": "MEDICINE#900000001", "sk": "META"}
    values = decode(update["ExpressionAttributeValues"])
    fields = {
        name: values[alias.replace("#", ":")]
        for alias, name in update["ExpressionAttributeNames"].items()
        if alias.startswith("#m")
    }
    assert fields["name"] == "지르텍정" and fields["form"] == "TABLET"
    assert fields["medicine_id"] == "MFDS#900000001"
    marker = decode(writes[1]["Put"]["Item"])
    assert marker["pk"] == "RECOGNITION#event-01" and marker["processing_status"] == "COMPLETED"
    assert "attribute_not_exists(pk)" in writes[1]["Put"]["ConditionExpression"]
    assert all(
        next(iter(write.values()))["TableName"] == "inha-capstone-02-medkit-data"
        for write in writes
    )


# 동시 중복 완료가 발생해도 조건부 이벤트 기록이 두 번째 제품 갱신을 취소한다.
def test_repeated_event_commits_only_once():
    client, store, result, source = storage_case()
    assert store.finish("event-01", source, result, "first")
    before = copy.deepcopy(client.items)
    assert not store.finish("event-01", source, result, "later")
    assert client.items == before
    assert store.completed("event-01", source)


# 실패 이벤트는 같은 키에서 재처리할 수 있고 성공 뒤 실패는 덮어쓰지 못한다.
def test_failure_retry_then_late_failure_preserves_success():
    client, store, result, source = storage_case()
    assert store.fail("event-01", source, "ocr", "NO_TEXT", False, "first")
    assert set(client.items) == {"RECOGNITION#event-01"}
    assert not store.completed("event-01", source)
    assert store.finish("event-01", source, result, "retry")
    before = copy.deepcopy(client.items)
    assert not store.fail("event-01", source, "ocr", "NO_TEXT", False, "late")
    assert client.items == before


# 성공 이벤트의 ID를 다른 원본에 재사용하면 충돌로 거부한다.
def test_same_event_different_source_is_rejected():
    _, store, result, source = storage_case()
    store.finish("event-01", source, result, "first")
    with pytest.raises(ValueError, match="EVENT_SOURCE_CONFLICT"):
        store.completed("event-01", {**source, "etag": "different"})


# 필수 검증이 빠진 결과를 저장 함수에 직접 전달해도 쓰기를 거부한다.
@pytest.mark.parametrize("mutation", ["status", "code", "name", "reference"])
def test_storage_revalidates_before_any_write(mutation):
    client, store, result, source = storage_case()
    if mutation == "status":
        result["processing_status"] = "FAILED"
    elif mutation == "code":
        result["medicines"][0]["product_code"] = ""
    elif mutation == "name":
        result["medicines"][0]["name"] = ""
    else:
        result["items"][0]["product_code"] = "900000002"
    with pytest.raises(ValueError):
        store.finish("event-01", source, result, "now")
    assert not client.transactions and not client.fail_puts


# 원자 저장 취소 뒤에는 실패 이벤트만 남기고 같은 ID 재시도에서 완료한다.
def test_transaction_failure_does_not_leave_partial_medicines():
    client, store, _, _ = storage_case()
    client.cancel_next = True
    deps = dependencies({"event-id": "event-01"})
    deps["store"] = store
    with pytest.raises(RuntimeError, match="RECOGNITION_BATCH_FAILED"):
        process_records(event(), **deps)
    assert set(client.items) == {"RECOGNITION#event-01"}
    assert decode(client.items["RECOGNITION#event-01"])["processing_status"] == "FAILED"
    deps["s3"].get_object.return_value["Body"] = io.BytesIO(b"image")
    assert process_records(event(), **deps)["processed"] == 1


# 커밋 후 응답만 유실된 경우 실패 기록이 성공을 덮지 않으며 재실행은 건너뛴다.
def test_lost_commit_ack_is_idempotent():
    client, store, _, _ = storage_case()
    client.lose_ack = True
    deps = dependencies({"event-id": "event-01"})
    deps["store"] = store
    with pytest.raises(RuntimeError):
        process_records(event(), **deps)
    assert decode(client.items["RECOGNITION#event-01"])["processing_status"] == "COMPLETED"
    deps["s3"].get_object.return_value["Body"] = io.BytesIO(b"image")
    assert process_records(event(), **deps)["skipped"] == 1
    assert len(client.transactions) == 1


# 선택 성분 API 장애 시 기존 성분을 보존하는 갱신식을 사용한다.
def test_ingredient_outage_preserves_previous_ingredients():
    client, store, result, source = storage_case()
    result["medicines"][0].update(
        ingredients_status="UNAVAILABLE", ingredients=[], ingredients_error_code="API_TIMEOUT"
    )
    store.finish("event-01", source, result, "now")
    update = client.transactions[0]["TransactItems"][0]["Update"]
    assert "if_not_exists(#ingredients,:empty)" in update["UpdateExpression"]
    assert ":ingredients" not in update["ExpressionAttributeValues"]


# 잘린 공식 API 페이지를 유일한 후보라고 판단하지 않는다.
def test_mfds_pagination_limit_fails():
    payload = json.dumps(
        {"header": {"resultCode": "00"}, "body": {"items": [product()], "totalCount": 2}}
    )
    api = MfdsClient("test-key", transport=Mock(return_value=payload), page_size=1, max_pages=1)
    with pytest.raises(ApiError, match="API_RESULT_LIMIT"):
        api.search("지르텍")


# 정규화한 검색어와 페이지를 보내며 인증 오류 원문은 노출하지 않는다.
def test_mfds_search_and_xml_error_are_safe():
    transport = Mock(
        return_value=b"<error><returnReasonCode>30</returnReasonCode><message>secret</message></error>"
    )
    api = MfdsClient("test-key", transport=transport)
    with pytest.raises(ApiError, match="^API_30$"):
        api.search("모드코에스")
    query = parse_qs(urlparse(transport.call_args.args[0]).query)
    assert query["item_name"] == ["모드코에스"]


# 실패 저장 자체가 실패해도 민감한 예외 대신 재시도 가능한 호출 실패를 반환한다.
def test_failure_storage_error_is_safe(caplog):
    deps = dependencies()
    deps["ocr"].side_effect = ImageError("NO_TEXT")
    deps["store"].fail.side_effect = RuntimeError("secret patient text")
    with pytest.raises(RuntimeError, match="^RECOGNITION_BATCH_FAILED$"):
        process_records(event(), **deps)
    assert "FAILURE_SAVE_FAILED" in caplog.text
    assert "secret patient text" not in caplog.text
