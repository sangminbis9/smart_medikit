"""도착 순서와 무관하게 촬영 단계를 결합하고 실패 확인·복구를 유지한다."""

import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import unquote_plus

from activity_api import ACTIVITY_TABLE, ApiError, exact_body, get_store, identifier, timestamp
from inventory_events import persist_transition, transition

WAIT_SECONDS = 900
TERMINAL = ("COMPLETED", "REVIEW_REQUIRED")


# 기존 단계 레코드와 새 레코드에서 동일한 세션 순번을 읽는다.
def sequence_of(item):
    return item.get("sequence") or next(
        (
            item[s].get("sequence", 0)
            for s in ("SESSION_CLOSED", "MOVEMENT_REQUESTED", "OCR_REQUESTED")
            if item.get(s)
        ),
        0,
    )


# 오프라인 촬영 시간 대신 서버의 최초 수신 시각으로 처리 제한 시간을 계산한다.
def expired(item, now=None):
    source = item.get("received_at") or (item.get("SESSION_CLOSED") or {}).get("occurred_at")
    if not source:
        return False
    return ((now or datetime.now(timezone.utc)) - timestamp(source)).total_seconds() >= WAIT_SECONDS


# 종료 판단을 영속 저장해 Pi가 다음 세션으로 넘어가도 웹에서 복구할 수 있게 한다.
def review(store, box, event_id, reason):
    store.activity.update_item(
        Key={"pk": f"BOX#{box}", "sk": f"CAPTURE#{event_id}"},
        UpdateExpression="SET processing_status=:status, review_reason=:reason",
        ConditionExpression="attribute_not_exists(processing_status) OR processing_status<>:done",
        ExpressionAttributeValues={
            ":status": "REVIEW_REQUIRED",
            ":reason": reason,
            ":done": "COMPLETED",
        },
    )
    return "REVIEW_REQUIRED"


# 두 OCR 저장 형식의 단일 제품 참조를 동일한 식별 입력으로 변환한다.
def recognized_medicines(store, raw):
    medicines = raw.get("medicines")
    if isinstance(medicines, list) and medicines:
        return medicines
    result = []
    for code in raw.get("product_codes", []):
        if not isinstance(code, str) or not re.fullmatch(r"\d{9}", code):
            continue
        medicine = store.inventory.get_item(
            Key={"pk": f"MEDICINE#{code}", "sk": "META"}, ConsistentRead=True
        ).get("Item")
        if medicine:
            result.append(medicine)
    return result


# 같은 제품의 기존 포장만 끝까지 읽어 사용자 중복 등록을 막는다.
def product_packages(store, box, code=None):
    from boto3.dynamodb.conditions import Attr

    packages, start = [], None
    while True:
        expression = Attr("box_id").eq(box) & Attr("pk").begins_with("PACKAGE#")
        if code:
            expression &= Attr("product_codes").contains(code)
        request = {"FilterExpression": expression, "ConsistentRead": True}
        if start:
            request["ExclusiveStartKey"] = start
        response = store.inventory.scan(**request)
        packages.extend(response.get("Items", []))
        start = response.get("LastEvaluatedKey")
        if not start:
            return packages


# 재고·이력과 촬영 종료를 같은 트랜잭션에 넣어 지연 재시도도 중복 처리하지 않는다.
def completion_write(store, box, event_id, decision=None):
    values = {":done": "COMPLETED", ":at": datetime.now(timezone.utc).isoformat()}
    update = "SET processing_status=:done, completed_at=:at"
    if decision:
        values[":decision"] = decision
        update += ", user_decision=:decision"
    return {
        "TableName": ACTIVITY_TABLE,
        "Key": store.encode({"pk": f"BOX#{box}", "sk": f"CAPTURE#{event_id}"}),
        "UpdateExpression": update,
        "ConditionExpression": "attribute_not_exists(completed_at)",
        "ExpressionAttributeValues": store.encode(values),
    }


# 전송 실패 알림은 이미 저장되어 있으므로 예약 전송으로 복구한다.
def wake_notifications():
    if os.environ.get("MEDKIT_NOTIFICATION_FUNCTION"):
        import boto3

        try:
            boto3.client("lambda").invoke(
                FunctionName=os.environ["MEDKIT_NOTIFICATION_FUNCTION"],
                InvocationType="Event",
                Payload=b"{}",
            )
        except Exception:
            print("NOTIFICATION_WAKE_RETRY_SCHEDULED")


# 같은 세션의 단계와 OCR 결과를 결합하고 선행 세션의 처리 순서를 보존한다.
def reconcile(store, box, event_id, decision=None):
    key = {"pk": f"BOX#{box}", "sk": f"CAPTURE#{event_id}"}
    item = store.activity.get_item(Key=key, ConsistentRead=True).get("Item", {})
    if item.get("processing_status") == "COMPLETED":
        return "COMPLETED"
    if decision and decision["direction"] == "NONE":
        store.client.transact_write_items(
            TransactItems=[{"Update": completion_write(store, box, event_id, decision)}]
        )
        return "COMPLETED"
    closed, movement = item.get("SESSION_CLOSED"), item.get("MOVEMENT_REQUESTED")
    if not closed or (
        (closed.get("package_capture_ok") or closed.get("interior_capture_ok")) and not movement
    ):
        return review(store, box, event_id, "STAGE_TIMEOUT") if expired(item) else "PROCESSING"
    if not closed.get("package_capture_ok") and not decision:
        if (
            closed.get("interior_capture_ok")
            and not movement.get("movement", {}).get("candidate")
            and movement.get("movement", {}).get("reason")
            in ("IMAGE_UNAVAILABLE_OR_MISMATCH", "NO_SINGLE_CHANGE")
        ):
            # 포장 촬영 없는 빈 기준 사진은 기존 계약대로 재고를 바꾸지 않는다.
            store.client.transact_write_items(
                TransactItems=[{"Update": completion_write(store, box, event_id)}]
            )
            return "COMPLETED"
        return review(store, box, event_id, "PACKAGE_IMAGE_MISSING")
    if not decision and not (movement or {}).get("movement", {}).get("candidate"):
        return review(store, box, event_id, "MOVEMENT_UNCERTAIN")
    raw = store.inventory.get_item(
        Key={"pk": f"RECOGNITION#{event_id}", "sk": "META"}, ConsistentRead=True
    ).get("Item", {})
    if decision:
        medicine = store.inventory.get_item(
            Key={"pk": f"MEDICINE#{decision['productCode']}", "sk": "META"}, ConsistentRead=True
        ).get("Item")
        medicines = [medicine] if medicine else []
    else:
        if raw.get("processing_status") != "COMPLETED":
            if raw.get("processing_status") == "REVIEW_REQUIRED":
                return review(store, box, event_id, "RECOGNITION_UNCERTAIN")
            return review(store, box, event_id, "OCR_TIMEOUT") if expired(item) else "PROCESSING"
        medicines = recognized_medicines(store, raw)
    if len(medicines) != 1:
        return review(store, box, event_id, "PRODUCT_UNRESOLVED")
    recognition = dict(medicines[0], session_id=closed["session_id"])
    all_packages = product_packages(store, box)
    packages = [
        p for p in all_packages if recognition["product_code"] in p.get("product_codes", [])
    ]
    if len(packages) > 1:
        return review(store, box, event_id, "MULTIPLE_PACKAGES")
    previous = packages[0] if packages else None
    if previous and previous.get("last_event_id") == event_id:
        store.client.transact_write_items(
            TransactItems=[{"Update": completion_write(store, box, event_id)}]
        )
        return "COMPLETED"
    seq = sequence_of(item)
    for earlier in store.records(box, "CAPTURE#"):
        if 0 < sequence_of(earlier) < seq and earlier.get("processing_status") not in TERMINAL:
            return "PROCESSING"
    movement = dict(movement or closed)
    if decision:
        movement.update(
            captured_at=movement.get("captured_at", closed["occurred_at"]),
            movement={"candidate": {"direction": decision["direction"]}},
        )
    candidate = dict(movement["movement"]["candidate"])
    # 가림 변화는 인식한 제품의 현재 상태와 결합하며 새 반입은 확인 없이 유지한다.
    if candidate["direction"] == "STATE":
        candidate["direction"] = "OUT" if previous and previous.get("state") == "STORED" else "IN"
    movement["movement"] = {**movement["movement"], "candidate": candidate}
    if candidate["direction"] == "IN":
        from inventory_events import stack_geometry

        movement["movement"] = stack_geometry(movement["movement"], all_packages, previous)
    result = transition(previous, recognition, movement)
    if result is None:
        return review(store, box, event_id, "STALE_OR_CONFLICTING_MOVEMENT")
    try:
        persist_transition(
            store, previous, result, completion_write(store, box, event_id, decision)
        )
    except Exception as error:
        if (
            getattr(error, "response", {}).get("Error", {}).get("Code")
            == "TransactionCanceledException"
        ):
            current = store.activity.get_item(Key=key, ConsistentRead=True).get("Item", {})
            if current.get("processing_status") == "COMPLETED":
                return "COMPLETED"
        raise
    wake_notifications()
    return "COMPLETED"


# 미완료 촬영만 순번대로 재결합하며 개별 장애는 다음 항목과 푸시에서 격리한다.
def maintain_captures(store, box):
    records = store.records(box, "CAPTURE#")
    for item in sorted(records, key=sequence_of):
        if item.get("processing_status") == "COMPLETED":
            continue
        try:
            reconcile(store, box, item["sk"].removeprefix("CAPTURE#"))
        except Exception:
            print("CAPTURE_MAINTENANCE_ITEM_RETRY")


# 사전에 지정된 비공개 버킷의 단계 JSON만 받아 원본과 순번을 보존한다.
def receive_s3(event, store, s3, box, bucket, prefix):
    for record in event.get("Records", []):
        if record.get("eventSource") != "aws:s3":
            continue
        obj = record.get("s3", {})
        key = unquote_plus(obj.get("object", {}).get("key", ""))
        if obj.get("bucket", {}).get("name") != bucket or not key.startswith(prefix):
            raise ValueError("UNEXPECTED_SOURCE")
        stages = {
            "ocr-ready.json": "OCR_REQUESTED",
            "movement-ready.json": "MOVEMENT_REQUESTED",
            "closed-ready.json": "SESSION_CLOSED",
        }
        stage = stages.get(key.rsplit("/", 1)[-1])
        if not stage:
            continue
        response = s3.get_object(Bucket=bucket, Key=key)
        try:
            data = response["Body"].read(32769)
        finally:
            response["Body"].close()
        if len(data) > 32768:
            raise ValueError("PAYLOAD_TOO_LARGE")
        payload = json.loads(data, parse_float=Decimal)
        event_id, session = identifier(payload["event_id"]), identifier(payload["session_id"])
        if (
            payload.get("box_id") != box
            or payload.get("stage") != stage
            or key != f"{prefix}{session}/{key.rsplit('/', 1)[-1]}"
        ):
            raise ValueError("EVENT_MISMATCH")
        if type(payload.get("sequence")) is not int or payload["sequence"] < 1:
            raise ValueError("INVALID_SEQUENCE")
        store.activity.update_item(
            Key={"pk": f"BOX#{box}", "sk": f"CAPTURE#{event_id}"},
            UpdateExpression="SET #stage=if_not_exists(#stage,:payload), session_id=:session, #seq=:seq, received_at=if_not_exists(received_at,:at)",
            ConditionExpression="(attribute_not_exists(session_id) OR session_id=:session) AND (attribute_not_exists(#seq) OR #seq=:seq) AND (attribute_not_exists(#stage) OR #stage=:payload)",
            ExpressionAttributeNames={"#stage": stage, "#seq": "sequence"},
            ExpressionAttributeValues={
                ":payload": payload,
                ":session": session,
                ":seq": payload["sequence"],
                ":at": datetime.now(timezone.utc).isoformat(),
            },
        )
        reconcile(store, box, event_id)


# S3 또는 IAM 인증된 Pi의 상태 조회만 받아 기존 접근 제한을 유지한다.
def lambda_handler(event, context):
    import boto3

    box = identifier(os.environ["MEDKIT_BOX_ID"])
    store = get_store()
    if "Records" in event:
        receive_s3(
            event,
            store,
            boto3.client("s3"),
            box,
            os.environ["MEDKIT_CAPTURE_BUCKET"],
            os.environ["MEDKIT_CAPTURE_PREFIX"].rstrip("/") + "/",
        )
        return {"ok": True}
    iam = event.get("requestContext", {}).get("authorizer", {}).get("iam", {})
    if not iam.get("userArn"):
        return {"statusCode": 403, "body": "{}"}
    event_id = identifier(event.get("pathParameters", {}).get("event_id"))
    status = reconcile(store, box, event_id)
    return {
        "statusCode": 200,
        "headers": {"Cache-Control": "no-store", "Content-Type": "application/json"},
        "body": json.dumps({"processing_status": status}),
    }


# 사용자 확인 대기인 촬영만 원문·사진 없이 복약 화면에 전달한다.
def list_reviews(store, box):
    result = []
    for item in store.records(box, "CAPTURE#"):
        if item.get("processing_status") != "REVIEW_REQUIRED":
            continue
        stage = (
            item.get("SESSION_CLOSED")
            or item.get("MOVEMENT_REQUESTED")
            or item.get("OCR_REQUESTED")
            or {}
        )
        result.append(
            {
                "eventId": item["sk"].removeprefix("CAPTURE#"),
                "occurredAt": stage.get("captured_at")
                or stage.get("occurred_at")
                or item.get("received_at"),
                "reason": item.get("review_reason", "MOVEMENT_UNCERTAIN"),
            }
        )
    return result


# 확인 대상 제품과 동작을 검증하고 최초 촬영 결과를 조건부로 확정한다.
def resolve_review(store, box, event_id, body):
    event_id = identifier(event_id)
    exact_body(body, ("productCode", "direction"))
    if body["direction"] not in ("IN", "OUT", "NONE"):
        raise ApiError(422, "INVALID_DIRECTION", "반입·반출·변경 없음을 선택해 주세요.")
    if body["direction"] != "NONE" and (
        not isinstance(body["productCode"], str) or not re.fullmatch(r"\d{9}", body["productCode"])
    ):
        raise ApiError(422, "INVALID_PRODUCT", "등록된 제품을 선택해 주세요.")
    item = store.activity.get_item(
        Key={"pk": f"BOX#{box}", "sk": f"CAPTURE#{event_id}"}, ConsistentRead=True
    ).get("Item", {})
    if item.get("processing_status") != "REVIEW_REQUIRED":
        if item.get("processing_status") == "COMPLETED" and item.get("user_decision") == body:
            return {"eventId": event_id, "processing_status": "COMPLETED"}
        raise ApiError(409, "REVIEW_CHANGED", "촬영 상태가 변경됐어요. 새로고침해 주세요.")
    if body["direction"] != "NONE" and not item.get("SESSION_CLOSED"):
        raise ApiError(
            409, "SESSION_INCOMPLETE", "종료 사진이 없어요. 변경 없음으로 닫고 다시 촬영해 주세요."
        )
    status = reconcile(store, box, event_id, body)
    if status != "COMPLETED":
        raise ApiError(
            409,
            "REVIEW_NOT_APPLIED",
            "이후 기록과 충돌하거나 제품을 찾지 못했어요. 확인 후 다시 시도해 주세요.",
        )
    return {"eventId": event_id, "processing_status": status}
