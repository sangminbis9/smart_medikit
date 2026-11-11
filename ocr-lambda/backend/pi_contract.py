"""기존 OCR 제품 저장을 유지하며 Pi 촬영의 식별자와 완료 결과만 연결한다."""

import re
from datetime import datetime, timezone

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer


# 비공개 포장 객체의 검증된 메타데이터를 같은 세션 ID에 연결한다.
def bind_pi_event(response, bucket, key, legacy_id):
    metadata = response.get("Metadata", {})
    event_id = metadata.get("event-id")
    if event_id is None:
        return legacy_id, None
    session = metadata.get("session-id")
    if (
        not isinstance(event_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", event_id)
        or not isinstance(session, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", session)
        or not key.endswith(f"/{session}/package.jpg")
    ):
        raise ValueError("PI_SOURCE_MISMATCH")
    return event_id, {
        "bucket": bucket,
        "key": key,
        "etag": response.get("ETag"),
        "version_id": response.get("VersionId"),
        "session_id": session,
    }


# DynamoDB의 속성값을 직렬화해 API 키나 사진 원문을 저장하지 않는다.
def attributes(values):
    serializer = TypeSerializer()
    return {key: serializer.serialize(value) for key, value in values.items()}


# 같은 ID의 다른 이미지 재사용을 거부하고 이미 완료한 촬영의 OCR 중복을 줄인다.
def pi_completed(store, event_id, source):
    raw = store.client.get_item(
        TableName=store.table_name,
        Key=attributes({"pk": f"RECOGNITION#{event_id}", "sk": "META"}),
        ConsistentRead=True,
    ).get("Item", {})
    decoder = TypeDeserializer()
    item = {key: decoder.deserialize(value) for key, value in raw.items()}
    if item.get("source") and item["source"] != source:
        raise ValueError("PI_EVENT_ID_REUSED")
    return item.get("processing_status") == "COMPLETED" and item.get("source") == source


# 공식 제품명의 명시적인 제형만 수량 관리 분류에 사용한다.
def official_form(medicine):
    if medicine.get("form") in ("TABLET", "CAPSULE", "OTHER"):
        return medicine["form"]
    name = re.sub(r"\([^)]*\)", "", medicine.get("name", "")).replace(" ", "")
    if re.search(r"캡슐(?:[\d.]|$)", name):
        return "CAPSULE"
    if re.search(r"정(?:[\d.]|$)", name):
        return "TABLET"
    return "OTHER"


# 기존 제품 보완 로직을 재사용하고 성공·실패 모두 Pi가 조회할 이벤트로 남긴다.
def save_pi_result(store, event_id, source, result, checked_at=None):
    if pi_completed(store, event_id, source):
        return
    medicines = []
    for medicine in result.get("medicines", []):
        store._save_medicine(medicine, checked_at or datetime.now(timezone.utc).isoformat())
        form = official_form(medicine)
        store.client.update_item(
            TableName=store.table_name,
            Key=attributes({"pk": f"MEDICINE#{medicine['product_code']}", "sk": "META"}),
            UpdateExpression="SET #form=if_not_exists(#form,:form)",
            ExpressionAttributeNames={"#form": "form"},
            ExpressionAttributeValues=attributes({":form": form}),
        )
        medicines.append(
            {"product_code": medicine["product_code"], "name": medicine["name"], "form": form}
        )
    values = {
        "pk": f"RECOGNITION#{event_id}",
        "sk": "META",
        "event_id": event_id,
        "source": source,
        "processing_status": result["processing_status"],
        "medicines": medicines,
        "product_codes": [m["product_code"] for m in medicines],
        "processed_at": checked_at or datetime.now(timezone.utc).isoformat(),
        "retryable": bool(result.get("retryable")),
    }
    try:
        store.client.put_item(
            TableName=store.table_name,
            Item=attributes(values),
            ConditionExpression="(attribute_not_exists(#source) OR #source=:source) AND (attribute_not_exists(#state) OR #state<>:done)",
            ExpressionAttributeNames={"#source": "source", "#state": "processing_status"},
            ExpressionAttributeValues=attributes({":source": source, ":done": "COMPLETED"}),
        )
    except Exception as error:
        if (
            getattr(error, "response", {}).get("Error", {}).get("Code")
            != "ConditionalCheckFailedException"
        ):
            raise
        if not pi_completed(store, event_id, source):
            raise


# 기존 비-Pi 입력의 저장 의미를 유지하며 Pi 결과만 새 계약으로 저장한다.
def save_result(store, source, event_id, result, checked_at, previous=None):
    if source is None:
        return store.save_result(event_id, result, checked_at, previous)
    return save_pi_result(store, event_id, source, result, checked_at)
