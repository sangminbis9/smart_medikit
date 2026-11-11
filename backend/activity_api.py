"""확인된 보관함 반출의 사용자 응답과 신규 포장 정보를 조건부 저장한다."""

import json
import os
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from urllib.parse import urlsplit

ACTIVITY_TABLE = "inha-capstone-02-medkit-activity"
INVENTORY_TABLE = "inha-capstone-02-medkit-data"
ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")
MAX_BODY = 8192
MAX_PAGES = 100


class ApiError(Exception):
    # 민감 원문 없이 고정 오류만 전달한다.
    def __init__(self, status, code, message):
        super().__init__(code)
        self.status, self.code, self.message = status, code, message


# 배포 범위와 공개 시연의 명시적 활성화만 허용한다.
def configuration():
    box = os.environ.get("MEDKIT_BOX_ID", "")
    origin = os.environ.get("MEDKIT_ALLOWED_ORIGIN", "")
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError:
        raise ApiError(503, "NOT_CONFIGURED", "보관함 연결 설정을 확인해 주세요.") from None
    if (os.environ.get("MEDKIT_ACTIVITY_PUBLIC_DEMO") != "true"
            or os.environ.get("MEDKIT_ACTIVITY_TABLE") != ACTIVITY_TABLE
            or os.environ.get("MEDKIT_INVENTORY_TABLE") != INVENTORY_TABLE
            or not ID_PATTERN.fullmatch(box)
            or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or "*" in origin
            or (port is not None and not 1 <= port <= 65535)):
        raise ApiError(503, "NOT_CONFIGURED", "보관함 연결 설정을 확인해 주세요.")
    return {"box": box, "origin": origin,
            "allow_estimated": os.environ.get("MEDKIT_ALLOW_ESTIMATED_MOVEMENT") == "true"}


# 식별자를 공개 경로와 DynamoDB 키에 동일하게 사용한다.
def identifier(value):
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise ApiError(422, "INVALID_ID", "요청 항목을 확인해 주세요.")
    return value


# 날짜는 실제 달력 날짜만 받아 임의 만료일을 만들지 않는다.
def actual_date(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ApiError(422, "INVALID_DATE", "유통기한을 확인해 주세요.")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ApiError(422, "INVALID_DATE", "유통기한을 확인해 주세요.") from None


# 시간대가 있는 실제 시각만 이력 정렬에 사용한다.
def timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("INVALID_TIME")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("INVALID_TIMEZONE")
    return parsed.astimezone(timezone.utc)


# 정수 수량만 허용하고 불리언과 비정상 크기를 제외한다.
def quantity(value, allow_zero=False):
    if type(value) is not int or not (0 if allow_zero else 1) <= value <= 1000000:
        raise ApiError(422, "INVALID_QUANTITY", "알약 수를 확인해 주세요.")
    return value


# 요청 필드 누락과 예상하지 않은 속성을 거부한다.
def exact_body(body, keys):
    if not isinstance(body, dict) or set(body) != set(keys):
        raise ApiError(422, "INVALID_BODY", "입력한 정보를 확인해 주세요.")


# 입력 길이를 제한하고 JSON 객체만 처리한다.
def parse_body(event):
    body = event.get("body")
    if event.get("isBase64Encoded") or not isinstance(body, str) or len(body.encode("utf-8")) > MAX_BODY:
        raise ApiError(422, "INVALID_BODY", "입력한 정보를 확인해 주세요.")
    try:
        return json.loads(body)
    except (ValueError, UnicodeError):
        raise ApiError(422, "INVALID_BODY", "입력한 정보를 확인해 주세요.") from None


# 저장된 반출 기록을 검사해 손상·미확인 방향을 정상 기록처럼 표시하지 않는다.
def event_view(item, box, allow_estimated=False):
    if not isinstance(item, dict) or item.get("pk") != f"BOX#{box}" or item.get("eventType") != "OUT":
        raise ValueError("INVALID_EVENT")
    event_id, package_id = item.get("eventId"), item.get("packageId")
    if (not isinstance(event_id, str) or not ID_PATTERN.fullmatch(event_id)
            or item.get("sk") != f"OUT#{event_id}"
            or not isinstance(package_id, str) or not ID_PATTERN.fullmatch(package_id)):
        raise ValueError("INVALID_EVENT_ID")
    certainty = item.get("movementCertainty")
    if certainty not in ("CONFIRMED", "ESTIMATED"):
        raise ValueError("INVALID_CERTAINTY")
    name, unit, status = item.get("medicineName"), item.get("unit"), item.get("status")
    if (not isinstance(name, str) or not name.strip() or len(name) > 200
            or unit not in ("TABLET", "CAPSULE", "OTHER")
            or status not in ("PENDING", "TAKEN", "NOT_TAKEN")):
        raise ValueError("INVALID_EVENT_FIELDS")
    timestamp(item.get("occurredAt"))
    recorded = item.get("recordedAt")
    count = item.get("quantity")
    if status == "PENDING":
        if recorded is not None or count is not None:
            raise ValueError("INVALID_PENDING")
    else:
        timestamp(recorded)
        if status == "NOT_TAKEN" and count is not None:
            raise ValueError("INVALID_NOT_TAKEN")
        if status == "TAKEN" and unit == "OTHER" and count is not None:
            raise ValueError("INVALID_OTHER_QUANTITY")
        if status == "TAKEN" and unit != "OTHER":
            if isinstance(count, Decimal) and count == count.to_integral_value():
                count = int(count)
            if type(count) is not int or not 1 <= count <= 1000000:
                raise ValueError("INVALID_QUANTITY")
    if certainty == "ESTIMATED" and not allow_estimated:
        return None
    return {"eventId": event_id, "packageId": package_id, "medicineName": name.strip(),
            "occurredAt": item["occurredAt"], "eventType": "OUT", "movementCertainty": certainty,
            "status": status, "quantity": count, "unit": unit, "recordedAt": recorded}


# 범위가 정해진 두 테이블에만 접근한다.
class DynamoStore:
    # 단기 실행 역할을 사용하고 클라이언트 비밀키를 받지 않는다.
    def __init__(self):
        import boto3
        from boto3.dynamodb.types import TypeSerializer
        from botocore.config import Config
        resource = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "ap-northeast-2"),
                                  config=Config(connect_timeout=2, read_timeout=3, retries={"max_attempts": 1}))
        self.activity = resource.Table(ACTIVITY_TABLE)
        self.inventory = resource.Table(INVENTORY_TABLE)
        self.client = boto3.client("dynamodb", region_name=os.environ.get("AWS_REGION", "ap-northeast-2"),
                                   config=Config(connect_timeout=2, read_timeout=3, retries={"max_attempts": 1}))
        self.serializer = TypeSerializer()

    # 강한 일관성 읽기로 같은 반출의 최초 응답 여부를 확인한다.
    def event(self, box, event_id):
        return self.activity.get_item(Key={"pk": f"BOX#{box}", "sk": f"OUT#{event_id}"}, ConsistentRead=True).get("Item")

    # 포장과 제품을 현재 보관함의 실제 저장 항목에서 읽는다.
    def package(self, box, package_id):
        item = self.inventory.get_item(Key={"pk": f"PACKAGE#{package_id}", "sk": "META"}, ConsistentRead=True).get("Item")
        if not isinstance(item, dict) or item.get("box_id") != box:
            raise ApiError(404, "PACKAGE_NOT_FOUND", "해당 약품을 찾지 못했어요.")
        return item

    # 단일 제품의 공식 제형만 수량 관리 근거로 사용한다.
    def form(self, package):
        codes = package.get("product_codes")
        if not isinstance(codes, list) or len(codes) != 1 or not isinstance(codes[0], str) or not re.fullmatch(r"\d{9}", codes[0]):
            return "OTHER"
        medicine = self.inventory.get_item(Key={"pk": f"MEDICINE#{codes[0]}", "sk": "META"}, ConsistentRead=True).get("Item")
        if package.get("kind") != "OTC" or not isinstance(medicine, dict):
            return "OTHER"
        return medicine.get("form") if medicine.get("form") in ("TABLET", "CAPSULE") else "OTHER"

    # 보관함의 반출 이력을 끝까지 읽고 한도 초과 시 불완전함을 알린다.
    def events(self, box):
        # 별도 보존한 합성 검증 이력은 삭제하지 않고 실제 복용 목록에서 제외한다.
        return [item for item in self.records(box, 'OUT#') if item.get('archived') is not True]

    # 촬영 확인과 반출 이력을 같은 보관함 키에서 페이지 끝까지 조회한다.
    def records(self, box, prefix):
        items, start = [], None
        for _ in range(MAX_PAGES):
            query = {"KeyConditionExpression": "#pk = :pk AND begins_with(#sk, :prefix)",
                     "ExpressionAttributeNames": {"#pk": "pk", "#sk": "sk"},
                     "ExpressionAttributeValues": {":pk": f"BOX#{box}", ":prefix": prefix},
                     "ConsistentRead": True, "Limit": 100}
            if start:
                query["ExclusiveStartKey"] = start
            response = self.activity.query(**query)
            items.extend(response.get("Items", []))
            start = response.get("LastEvaluatedKey")
            if not start:
                return items
        raise ApiError(503, "ACTIVITY_INCOMPLETE", "복용 기록을 모두 불러오지 못했어요.")

    # DynamoDB 트랜잭션의 원시 값으로 직렬화한다.
    def encode(self, values):
        return {key: self.serializer.serialize(value) for key, value in values.items()}

    # 최초 확인과 입력값을 한 포장에 조건부로 반영한다.
    def initial(self, package_id, names, values, update, condition):
        try:
            self.inventory.update_item(Key={"pk": f"PACKAGE#{package_id}", "sk": "META"},
                                       UpdateExpression=update, ConditionExpression=condition,
                                       ExpressionAttributeNames=names, ExpressionAttributeValues=values)
        except Exception as error:
            if getattr(error, "response", {}).get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise ApiError(409, "PACKAGE_CHANGED", "약품 정보가 변경됐어요. 다시 확인해 주세요.") from None
            raise

    # 확인 기록과 알약 차감을 하나의 조건부 트랜잭션으로 저장한다.
    def confirm(self, box, event_id, result, count, recorded, package_id, inventory):
        event_update = {"TableName": ACTIVITY_TABLE,
                        "Key": self.encode({"pk": f"BOX#{box}", "sk": f"OUT#{event_id}"}),
                        "UpdateExpression": "SET #status = :result, #quantity = :quantity, #recorded = :recorded",
                        "ConditionExpression": "#status = :pending AND #kind = :kind AND #package = :package",
                        "ExpressionAttributeNames": {"#status": "status", "#quantity": "quantity", "#recorded": "recordedAt", "#kind": "eventType", "#package": "packageId"},
                        "ExpressionAttributeValues": self.encode({":result": result, ":quantity": count, ":recorded": recorded, ":pending": "PENDING", ":kind": "OUT", ":package": package_id})}
        changes = [{"Update": event_update}]
        if inventory is not None:
            prior = inventory["estimated_qty"]
            package_update = {"TableName": INVENTORY_TABLE,
                              "Key": self.encode({"pk": f"PACKAGE#{package_id}", "sk": "META"}),
                              "UpdateExpression": "SET #inventory.#estimated = :next",
                              "ConditionExpression": "#box = :box AND #inventory.#enabled = :true AND #inventory.#estimated = :prior",
                              "ExpressionAttributeNames": {"#box": "box_id", "#inventory": "inventory", "#estimated": "estimated_qty", "#enabled": "stock_enabled"},
                              "ExpressionAttributeValues": self.encode({":box": box, ":true": True, ":prior": prior, ":next": prior - count})}
            changes.append({"Update": package_update})
        try:
            self.client.transact_write_items(TransactItems=changes)
        except Exception as error:
            if getattr(error, "response", {}).get("Error", {}).get("Code") in ("TransactionCanceledException", "ConditionalCheckFailedException"):
                raise ApiError(409, "EVENT_CHANGED", "이미 확인했거나 약품 정보가 변경됐어요. 다시 확인해 주세요.") from None
            raise


_store = None


# Lambda 실행 환경에서 테이블 연결을 재사용한다.
def get_store():
    global _store
    if _store is None:
        _store = DynamoStore()
    return _store


# 손상되거나 일부만 읽힌 이력을 정상 빈 목록으로 숨기지 않는다.
def list_activity(store, config):
    from capture_events import list_reviews
    try:
        events = [event_view(item, config["box"], config["allow_estimated"]) for item in store.events(config["box"])]
        events = [item for item in events if item is not None]
        events.sort(key=lambda item: timestamp(item["occurredAt"]), reverse=True)
    except (ValueError, TypeError, KeyError):
        raise ApiError(503, "ACTIVITY_INVALID", "복용 기록을 확인하지 못했어요.") from None
    return {"pending": [item for item in events if item["status"] == "PENDING"],
            "history": [item for item in events if item["status"] != "PENDING"],
            "reviews": list_reviews(store, config['box'])}


# 외부에서 방향이 확인된 반출만 사용자 확인으로 확정한다.
def confirm_activity(store, config, event_id, body):
    event_id = identifier(event_id)
    exact_body(body, ("result", "quantity"))
    result = body["result"]
    if result not in ("TAKEN", "NOT_TAKEN"):
        raise ApiError(422, "INVALID_RESULT", "복용 여부를 확인해 주세요.")
    item = store.event(config["box"], event_id)
    if item is None:
        raise ApiError(404, "EVENT_NOT_FOUND", "해당 복용 확인을 찾지 못했어요.")
    try:
        event = event_view(item, config["box"], config["allow_estimated"])
    except (ValueError, TypeError, KeyError):
        raise ApiError(503, "ACTIVITY_INVALID", "복용 기록을 확인하지 못했어요.") from None
    if event is None or event["status"] != "PENDING":
        raise ApiError(409, "EVENT_CHANGED", "이미 확인했거나 확인할 수 없는 기록이에요.")
    count = body["quantity"]
    if result == "NOT_TAKEN" or event["unit"] == "OTHER":
        if count is not None:
            raise ApiError(422, "INVALID_QUANTITY", "알약 수를 확인해 주세요.")
    else:
        count = quantity(count)
    inventory = None
    if result == "TAKEN" and event["unit"] != "OTHER":
        package = store.package(config["box"], event["packageId"])
        if store.form(package) != event["unit"]:
            raise ApiError(409, "FORM_CHANGED", "약품의 제형을 다시 확인해 주세요.")
        stock = package.get("inventory")
        if isinstance(stock, dict) and stock.get("stock_enabled") is True:
            prior = stock.get("estimated_qty")
            if isinstance(prior, Decimal) and prior == prior.to_integral_value():
                prior = int(prior)
            if prior is not None:
                if type(prior) is not int or prior < 0 or prior > 1000000:
                    raise ApiError(503, "INVENTORY_INVALID", "보관 수량을 확인해 주세요.")
                if count > prior:
                    raise ApiError(422, "INSUFFICIENT_QUANTITY", "남은 수량보다 많이 입력했어요.")
                inventory = {"estimated_qty": prior}
    recorded = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    store.confirm(config["box"], event_id, result, count, recorded, event["packageId"], inventory)
    return {"event": {**event, "status": result, "quantity": count, "recordedAt": recorded}}


# 최초 등록의 새것·사용 중 정보를 한 번만 확인하고 실제로 입력한 값만 반영한다.
def confirm_initial(store, config, package_id, body):
    package_id = identifier(package_id)
    exact_body(body, ("condition", "actualExpiry", "remainingQty"))
    condition = body["condition"]
    if condition not in ("NEW", "USED"):
        raise ApiError(422, "INVALID_CONDITION", "약품 상태를 선택해 주세요.")
    expiry = actual_date(body["actualExpiry"])
    remaining = body["remainingQty"]
    if condition == "NEW" and expiry is not None:
        raise ApiError(422, "INVALID_INITIAL", "약품 정보를 다시 확인해 주세요.")
    if remaining is not None:
        remaining = quantity(remaining, allow_zero=True)
    package = store.package(config["box"], package_id)
    missing_quantity = isinstance(package.get('inventory'), dict) and package['inventory'].get('estimated_qty') is None
    if package.get("first_registration_pending") is not True and not (missing_quantity and remaining is not None):
        raise ApiError(409, "ALREADY_CONFIRMED", "이미 초기 정보를 확인했어요.")
    if condition == 'NEW' and remaining is not None and not missing_quantity:
        raise ApiError(422, 'QUANTITY_ALREADY_KNOWN', '기존 수량은 새것 확인으로 변경할 수 없어요.')
    if remaining is not None and (store.form(package) == "OTHER" or not isinstance(package.get("inventory"), dict)
                                  or package["inventory"].get("stock_enabled") is not True):
        raise ApiError(422, "QUANTITY_NOT_AVAILABLE", "이 약품의 수량은 입력할 수 없어요.")
    names = {"#box": "box_id", "#pending": "first_registration_pending", "#condition": "initial_condition"}
    values = {":box": config["box"], ":true": True, ":false": False, ":condition": condition}
    update = "SET #pending = :false, #condition = :condition"
    condition_expr = "#box = :box AND #pending = :true"
    if package.get('first_registration_pending') is not True:
        condition_expr = '#box = :box'
        del values[':true']
    if expiry is not None:
        old = package.get("expiry")
        if old is not None and not isinstance(old, dict):
            raise ApiError(503, "EXPIRY_INVALID", "유통기한 정보를 확인해 주세요.")
        names["#expiry"] = "expiry"
        values[":expiry"] = {**(old or {}), "actual_expiry": expiry, "verified": True}
        update += ", #expiry = :expiry"
        if "expiry" not in package:
            condition_expr += " AND attribute_not_exists(#expiry)"
        else:
            values[":old_expiry"] = old
            condition_expr += " AND #expiry = :old_expiry"
    if remaining is not None:
        old = package["inventory"]
        names["#inventory"] = "inventory"
        values[":inventory"] = {**old, "estimated_qty": remaining}
        if condition == 'NEW':
            values[':inventory']['initial_qty'] = remaining
        values[":old_inventory"] = old
        update += ", #inventory = :inventory"
        condition_expr += " AND #inventory = :old_inventory"
    store.initial(package_id, names, values, update, condition_expr)
    return {"packageId": package_id, "firstRegistrationPending": False}


# 정상 응답도 캐시하지 않아 반출·복용 정보를 브라우저에 남기지 않는다.
def response(status, body, origin):
    headers = {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}
    if origin:
        headers.update({"Access-Control-Allow-Origin": origin, "Vary": "Origin", "Access-Control-Allow-Methods": "GET,POST,OPTIONS", "Access-Control-Allow-Headers": "Content-Type"})
    return {"statusCode": status, "headers": headers, "body": "" if body is None else json.dumps(body, ensure_ascii=False)}


# 웹 API는 외부에서 이미 확인된 이벤트만 읽고 직접 입출고를 추론하지 않는다.
def lambda_handler(event, context):
    origin = None
    try:
        config = configuration()
        headers = {str(key).lower(): value for key, value in (event.get("headers") or {}).items()}
        supplied_origin = headers.get("origin")
        if supplied_origin is not None and supplied_origin != config["origin"]:
            raise ApiError(403, "ORIGIN_NOT_ALLOWED", "웹사이트 주소를 확인해 주세요.")
        if supplied_origin == config["origin"]:
            origin = supplied_origin
        method = ((event.get("requestContext") or {}).get("http") or {}).get("method")
        path = event.get("rawPath") or ""
        if path in ("/push/config", "/push/subscriptions", "/push/subscriptions/remove"):
            from notifications import manage_subscription
            if method == "OPTIONS":
                return response(204, None, origin)
            if path == "/push/config" and method == "GET":
                key = os.environ.get("MEDKIT_VAPID_PUBLIC_KEY")
                if not key:
                    raise ApiError(503, "PUSH_NOT_CONFIGURED", "알림 연결을 준비 중입니다.")
                return response(200, {"vapid_public_key": key}, origin)
            if path != "/push/config" and method == "POST":
                body = parse_body(event)
                exact_body(body, ("subscription",))
                return response(200, manage_subscription(get_store(), config["box"], body, path.endswith('/remove')), origin)
            raise ApiError(405, "METHOD_NOT_ALLOWED", "지원하지 않는 요청이에요.")
        confirm_match = re.fullmatch(r"/activity/([A-Za-z0-9_-]{1,100})/confirm", path)
        initial_match = re.fullmatch(r"/packages/([A-Za-z0-9_-]{1,100})/initial", path)
        if path != "/activity" and not confirm_match and not initial_match:
            raise ApiError(404, "NOT_FOUND", "지원하지 않는 요청이에요.")
        if method == "OPTIONS":
            return response(204, None, origin)
        store = get_store()
        if method == "GET" and path == "/activity":
            result = list_activity(store, config)
        elif method == "POST" and confirm_match:
            body = parse_body(event)
            if isinstance(body, dict) and 'review' in body:
                from capture_events import resolve_review
                exact_body(body, ('review',))
                result = resolve_review(store, config['box'], confirm_match.group(1), body['review'])
            else:
                result = confirm_activity(store, config, confirm_match.group(1), body)
        elif method == "POST" and initial_match:
            result = confirm_initial(store, config, initial_match.group(1), parse_body(event))
        else:
            raise ApiError(405, "METHOD_NOT_ALLOWED", "지원하지 않는 요청이에요.")
        return response(200, result, origin)
    except ApiError as error:
        return response(error.status, {"code": error.code, "message": error.message}, origin)
    except Exception:
        # 입력·복용 정보와 AWS 응답 원문을 로그에 남기지 않는다.
        return response(503, {"code": "ACTIVITY_UNAVAILABLE", "message": "복용 기록에 연결하지 못했어요."}, origin)
