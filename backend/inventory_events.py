"""식별과 이동 결과를 합쳐 재고·반출 확인·알림을 원자적으로 기록한다."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from activity_api import ACTIVITY_TABLE, INVENTORY_TABLE, identifier, timestamp


# 서로 다른 순서로 도착한 결과라도 같은 세션의 식별·이동만 결합한다.
def transition(package, recognition, movement, now=None):
    if recognition.get("session_id") != movement.get("session_id"):
        raise ValueError("SESSION_MISMATCH")
    code = recognition.get("product_code")
    if not isinstance(code, str) or len(code) != 9 or not code.isdigit():
        return None
    candidate = movement.get("movement", {}).get("candidate")
    if not candidate or candidate.get("direction") not in ("IN", "OUT"):
        return None
    event_id = identifier(movement["event_id"])
    box = identifier(movement["box_id"])
    sequence = movement["sequence"]
    if isinstance(sequence, Decimal) and sequence == sequence.to_integral_value():
        sequence = int(sequence)
    if type(sequence) is not int or sequence < 1:
        raise ValueError("INVALID_SEQUENCE")
    occurred = movement["captured_at"]
    timestamp(occurred)
    if package and (package.get("box_id") != box or package.get("last_sequence", 0) >= sequence):
        return None
    direction = candidate["direction"]
    if direction == "OUT" and (not package or package.get("state") != "STORED"):
        return None
    package_id = package["package_id"] if package else f"{box}-{code}"
    identifier(package_id)
    updated = dict(package or {"pk": f"PACKAGE#{package_id}", "sk": "META", "package_id": package_id,
                              "box_id": box, "kind": "OTC", "product_codes": [code],
                              "name": recognition["name"], "first_registration_pending": True,
                              "inventory": {"stock_enabled": recognition.get("form") in ("TABLET", "CAPSULE"),
                                            "initial_qty": None, "estimated_qty": None}})
    updated.update(state="STORED" if direction == "IN" else "OUT", last_sequence=sequence,
                   movement_certainty="ESTIMATED", last_event_id=event_id)
    notification = None
    activity = None
    if direction == "OUT":
        updated.update(active_out_event_id=event_id, out_at=occurred)
        activity = {"pk": f"BOX#{box}", "sk": f"OUT#{event_id}", "eventType": "OUT", "eventId": event_id,
                    "packageId": package_id, "medicineName": updated["name"], "occurredAt": occurred,
                    "movementCertainty": "ESTIMATED", "status": "PENDING", "quantity": None, "recordedAt": None,
                    "unit": recognition.get("form") if recognition.get("form") in ("TABLET", "CAPSULE") else "OTHER",
                    "disposalDueAt": (timestamp(occurred) + timedelta(hours=24)).isoformat()}
        notification = {"kind": "OUT_CONFIRMATION", "event_id": event_id, "medicine_name": updated["name"]}
    else:
        updated.pop("active_out_event_id", None)
        updated.pop("out_at", None)
        updated.pop("disposal_certainty", None)
        if package is None:
            notification = {"kind": "INITIAL_REGISTRATION", "package_id": package_id}
    if notification:
        notification.update(pk=f"BOX#{box}", sk=f"NOTICE#{event_id}", notification_id=event_id,
                            delivery_status="PENDING")
    return updated, activity, notification


# 재반입이나 이후 반출의 식별자가 달라지면 이전 폐기 예약을 무효화한다.
def disposal_due(package, event, now):
    return (package.get("state") == "OUT"
            and package.get("active_out_event_id") == event.get("eventId")
            and package.get("out_at") == event.get("occurredAt")
            and now >= timestamp(event["occurredAt"]) + timedelta(hours=24))


# 포장 변경과 이력·알림을 한 번의 조건부 트랜잭션에 포함한다.
def persist_transition(store, previous, result):
    updated, activity, notification = result
    put = {"TableName": INVENTORY_TABLE, "Item": store.encode(updated)}
    if previous is None:
        put["ConditionExpression"] = "attribute_not_exists(pk)"
    else:
        put.update(ConditionExpression="#box = :box AND #seq = :seq",
                   ExpressionAttributeNames={"#box": "box_id", "#seq": "last_sequence"},
                   ExpressionAttributeValues=store.encode({":box": previous["box_id"], ":seq": previous.get("last_sequence", 0)}))
        if "last_sequence" not in previous:
            put["ConditionExpression"] = "#box = :box AND attribute_not_exists(#seq)"
            del put["ExpressionAttributeValues"][":seq"]
    writes = [{"Put": put}]
    for item in (activity, notification):
        if item:
            writes.append({"Put": {"TableName": ACTIVITY_TABLE, "Item": store.encode(item),
                                    "ConditionExpression": "attribute_not_exists(pk)"}})
    store.client.transact_write_items(TransactItems=writes)


# 24시간 경과를 실제 폐기 확인과 구분해 조건부로 저장한다.
def expire_outbound(store, box, now=None):
    now = now or datetime.now(timezone.utc)
    for event in store.events(box):
        package = store.package(box, event["packageId"])
        if not disposal_due(package, event, now):
            continue
        try:
            store.inventory.update_item(Key={"pk": package["pk"], "sk": "META"},
                UpdateExpression="SET #state=:disposed, disposal_certainty=:estimated",
                ConditionExpression="#state=:out AND active_out_event_id=:id AND out_at=:at",
                ExpressionAttributeNames={"#state": "state"},
                ExpressionAttributeValues={":disposed": "DISPOSED", ":estimated": "ESTIMATED", ":out": "OUT",
                                           ":id": event["eventId"], ":at": event["occurredAt"]})
        except Exception as error:
            if getattr(error, "response", {}).get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
