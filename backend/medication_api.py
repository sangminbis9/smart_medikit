"""승인된 접근 모드에 따라 팀 복용약을 별도 DynamoDB 항목에 조건부 저장한다."""

import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from urllib.parse import quote, urlsplit

TABLE_NAME = "inha-capstone-02-medkit-medications"
TOKEN_AUDIENCE = "smart-medikit-medications"
TOKEN_SECONDS = 7200
MAX_BYTES = 300 * 1024
MAX_PLANS = 100
MAX_MEDICINES = 30
MAX_TIMES = 12
MAX_RECORDS = 2000
MAX_REVISION = 9007199254740990
_table = None


class ApiError(Exception):
    # 입력 원문 대신 정해진 코드와 설명만 오류 응답에 전달한다.
    def __init__(self, status, code, message):
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message


# 패딩 없는 URL 안전 형식으로 해시와 세션 데이터를 인코딩한다.
def encode_urlsafe(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


# 정규화된 URL 안전 문자만 허용해 모호한 서명 입력을 차단한다.
def decode_urlsafe(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("INVALID_BASE64URL")
    decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if encode_urlsafe(decoded) != value:
        raise ValueError("NON_CANONICAL_BASE64URL")
    return decoded


# 공개 시연은 정확한 명시 설정만 허용하고 기본 모드는 비밀번호 인증을 요구한다.
def configuration():
    try:
        table_name = os.environ["MEDKIT_MEDICATION_TABLE"]
        box_id = os.environ["MEDKIT_BOX_ID"]
        origin = os.environ["MEDKIT_ALLOWED_ORIGIN"]
        parsed_origin = urlsplit(origin)
        valid_port = parsed_origin.port is None or 1 <= parsed_origin.port <= 65535
        if (
            table_name != TABLE_NAME
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", box_id)
            or parsed_origin.scheme != "https"
            or not parsed_origin.hostname
            or parsed_origin.username is not None
            or parsed_origin.password is not None
            or parsed_origin.path
            or parsed_origin.query
            or parsed_origin.fragment
            or "*" in origin
            or any(character.isspace() for character in origin)
            or not valid_port
        ):
            raise ValueError("INVALID_CONFIGURATION")
        config = {
            "table_name": table_name,
            "box_id": box_id,
            "origin": origin,
            "public_demo": os.environ.get("MEDKIT_MEDICATION_PUBLIC_DEMO") == "true",
        }
        # 공개 모드에도 테이블·보관함·출처 설정은 필수이며 비밀번호만 요구하지 않는다.
        if config["public_demo"]:
            return config
        password_hash = os.environ["MEDKIT_PASSWORD_HASH"]
        secret = decode_urlsafe(os.environ["MEDKIT_SESSION_SECRET"])
        parts = password_hash.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            raise ValueError("INVALID_PASSWORD_HASH")
        iterations = int(parts[1])
        salt, digest = decode_urlsafe(parts[2]), decode_urlsafe(parts[3])
        if len(secret) < 32 or not 200000 <= iterations <= 1000000 or len(salt) < 16 or len(digest) != 32:
            raise ValueError("INVALID_CONFIGURATION")
        config.update({
            "secret": secret,
            "iterations": iterations,
            "salt": salt,
            "digest": digest,
        })
        return config
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ApiError(503, "SERVICE_NOT_CONFIGURED", "복용약 저장 서비스 설정을 확인해 주세요.") from None


# AWS 실행 역할로 전용 테이블만 연결하며 기존 공개 재고 테이블은 사용하지 않는다.
def get_table():
    global _table
    if _table is None:
        import boto3
        from botocore.config import Config

        resource = boto3.resource(
            "dynamodb",
            region_name=os.environ.get("AWS_REGION", "ap-northeast-2"),
            config=Config(connect_timeout=2, read_timeout=3, retries={"max_attempts": 1}),
        )
        _table = resource.Table(TABLE_NAME)
    return _table


# 응답과 인증값을 캐시하거나 내부 예외를 응답에 노출하지 않는다.
def respond(status, body, origin=None):
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Vary": "Origin",
    }
    if origin:
        headers.update({
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "GET, PUT, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Authorization, Content-Type",
        })
    return {
        "statusCode": status,
        "headers": headers,
        "body": "" if status == 204 else json.dumps(body, ensure_ascii=False, separators=(",", ":")),
    }


# JSON 객체의 중복 필드를 거부해 서로 다른 해석으로 저장되는 것을 막는다.
def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


# JSON 표준에 없는 NaN과 Infinity를 입력으로 받지 않는다.
def reject_constant(value):
    raise ValueError("INVALID_JSON_NUMBER")


# 본문 크기와 형식을 먼저 검사하고 JSON 이외의 전송을 차단한다.
def request_body(event, headers):
    if headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise ApiError(415, "JSON_REQUIRED", "JSON 형식의 요청이 필요해요.")
    raw = event.get("body")
    if not isinstance(raw, str):
        raise ApiError(400, "INVALID_JSON", "요청 본문을 읽을 수 없어요.")
    try:
        if event.get("isBase64Encoded") is True:
            if len(raw) > ((MAX_BYTES + 2) // 3) * 4:
                raise ApiError(413, "PAYLOAD_TOO_LARGE", "저장할 데이터는 300KB 이하여야 해요.")
            raw_bytes = base64.b64decode(raw, validate=True)
        else:
            raw_bytes = raw.encode("utf-8")
        if len(raw_bytes) > MAX_BYTES:
            raise ApiError(413, "PAYLOAD_TOO_LARGE", "저장할 데이터는 300KB 이하여야 해요.")
        return json.loads(raw_bytes.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ApiError(400, "INVALID_JSON", "요청 본문을 읽을 수 없어요.") from None


# 계약에 없는 필드나 필수 필드 누락을 조용히 무시하지 않는다.
def exact_object(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ApiError(422, "INVALID_STATE", f"{label}의 입력 형식을 확인해 주세요.")


# 문자 필드를 정리하되 약 이름과 복용량의 내용을 추정하거나 변환하지 않는다.
def text_field(value, label, limit, required=False):
    if not isinstance(value, str):
        raise ApiError(422, "INVALID_STATE", f"{label}은 문자로 입력해 주세요.")
    value = value.strip()
    if (required and not value) or len(value) > limit:
        raise ApiError(422, "INVALID_STATE", f"{label}의 길이나 누락 여부를 확인해 주세요.")
    # 단독 서로게이트 문자는 JSON으로 읽혀도 정상 UTF-8 저장값이 될 수 없다.
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ApiError(422, "INVALID_STATE", f"{label}의 문자 형식을 확인해 주세요.") from None
    return value


# 불리언이나 소수로 revision·요일·버전을 우회하지 못하게 정수를 검사한다.
def integer(value, label, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ApiError(422, "INVALID_STATE", f"{label}은 올바른 정수여야 해요.")
    if (isinstance(value, float) and not math.isfinite(value)) or (isinstance(value, Decimal) and not value.is_finite()):
        raise ApiError(422, "INVALID_STATE", f"{label}의 범위를 확인해 주세요.")
    # 지나치게 큰 정수도 부동소수점으로 바꾸지 않고 입력 오류로 처리한다.
    if not minimum <= value <= maximum or value != int(value):
        raise ApiError(422, "INVALID_STATE", f"{label}의 범위를 확인해 주세요.")
    return int(value)


# 존재하는 그레고리력 날짜만 허용하며 연월일을 시간대로 변환하지 않는다.
def valid_day(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ApiError(422, "INVALID_STATE", f"{label}을 올바르게 입력해 주세요.")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ApiError(422, "INVALID_STATE", f"{label}은 실제 날짜여야 해요.") from None
    return value


# 입력한 공통·개별 일정의 기간, 중복 요일과 시각을 동일하게 검증한다.
def validate_timing(value):
    exact_object(value, ("mode", "schedule"), "복용 방식")
    if value["mode"] not in ("regular", "as_needed"):
        raise ApiError(422, "INVALID_STATE", "복용 방식을 다시 선택해 주세요.")
    schedule = value["schedule"]
    if schedule is None:
        return {"mode": value["mode"], "schedule": None}
    if value["mode"] != "regular":
        raise ApiError(422, "INVALID_STATE", "필요시 복용약에는 정기 일정을 지정할 수 없어요.")
    exact_object(schedule, ("startDate", "endDate", "weekdays", "times"), "복용 일정")
    start = valid_day(schedule["startDate"], "일정 시작일")
    end = schedule["endDate"]
    if end != "":
        end = valid_day(end, "일정 종료일")
    if end and end < start:
        raise ApiError(422, "INVALID_STATE", "일정 종료일은 시작일보다 빠를 수 없어요.")
    if not isinstance(schedule["weekdays"], list) or not 1 <= len(schedule["weekdays"]) <= 7:
        raise ApiError(422, "INVALID_STATE", "복용 요일을 한 개 이상 선택해 주세요.")
    weekdays = []
    for day in schedule["weekdays"]:
        weekdays.append(integer(day, "복용 요일", 0, 6))
    if len(set(weekdays)) != len(weekdays):
        raise ApiError(422, "INVALID_STATE", "복용 요일이 중복되어 있어요.")
    times = schedule["times"]
    if not isinstance(times, list) or not 1 <= len(times) <= MAX_TIMES:
        raise ApiError(422, "LIMIT_EXCEEDED", "복용 시간은 1개부터 12개까지 입력할 수 있어요.")
    for slot in times:
        if not isinstance(slot, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", slot):
            raise ApiError(422, "INVALID_STATE", "복용 시간을 00:00부터 23:59 사이로 입력해 주세요.")
    if len(set(times)) != len(times):
        raise ApiError(422, "INVALID_STATE", "복용 시간이 중복되어 있어요.")
    return {"mode": "regular", "schedule": {"startDate": start, "endDate": end, "weekdays": weekdays, "times": list(times)}}


# 수동 입력한 묶음과 약 목록을 검증하며 보관함 포장으로 변환하지 않는다.
def validate_plan(value):
    exact_object(value, ("id", "title", "timing", "notes", "medicines", "endedOn"), "처방 묶음")
    if not isinstance(value["medicines"], list) or len(value["medicines"]) > MAX_MEDICINES:
        raise ApiError(422, "LIMIT_EXCEEDED", "한 묶음에는 약 정보를 최대 30개 등록할 수 있어요.")
    medicines = []
    ids = set()
    for medicine in value["medicines"]:
        exact_object(medicine, ("id", "name", "strength", "dose", "timing"), "개별 약")
        medicine_id = text_field(medicine["id"], "약 식별자", 200, True)
        if medicine_id in ids:
            raise ApiError(422, "INVALID_STATE", "같은 약 항목이 중복되어 있어요.")
        ids.add(medicine_id)
        medicines.append({
            "id": medicine_id,
            "name": text_field(medicine["name"], "약 이름", 200, True),
            "strength": text_field(medicine["strength"], "함량·제형", 100),
            "dose": text_field(medicine["dose"], "1회 복용량", 200),
            "timing": validate_timing(medicine["timing"]) if medicine["timing"] is not None else None,
        })
    ended = value["endedOn"]
    if ended is not None:
        ended = valid_day(ended, "복용 종료 날짜")
    return {
        "id": text_field(value["id"], "묶음 식별자", 200, True),
        "title": text_field(value["title"], "처방 묶음 이름", 100, True),
        "timing": validate_timing(value["timing"]),
        "notes": text_field(value["notes"], "복용 안내·메모", 2000),
        "medicines": medicines,
        "endedOn": ended,
    }


# 프론트엔드 encodeURIComponent와 동일한 방식으로 묶음 식별자를 구분한다.
def encoded_plan_id(plan_id):
    return quote(plan_id, safe="~()*!.'-_")


# 한국 날짜와 입력된 시각에 실제로 해당하는 일정인지 확인한다.
def timing_matches(timing, day, slot):
    schedule = timing["schedule"]
    if timing["mode"] != "regular" or schedule is None:
        return False
    if day < schedule["startDate"] or (schedule["endDate"] and day > schedule["endDate"]):
        return False
    weekday = (date.fromisoformat(day).weekday() + 1) % 7
    return weekday in schedule["weekdays"] and slot in schedule["times"]


# 회차가 존재하는지 검증하고 그 시각에 예정된 약 식별자만 반환한다.
def occurrence_medicines(plan, day, slot):
    if plan["endedOn"] and day > plan["endedOn"]:
        raise ApiError(422, "INVALID_RECORD", "종료한 날짜 이후의 복용 확인 기록이 있어요.")
    ids = set()
    if not plan["medicines"]:
        exists = timing_matches(plan["timing"], day, slot)
    else:
        for medicine in plan["medicines"]:
            if timing_matches(medicine["timing"] or plan["timing"], day, slot):
                ids.add(medicine["id"])
        exists = bool(ids)
    if not exists:
        raise ApiError(422, "INVALID_RECORD", "등록된 일정에 없는 복용 확인 기록이 있어요.")
    return ids


# 기록 시각은 시간대가 명시된 실제 날짜여야 하며 실제 복용 시각으로 추정하지 않는다.
def valid_timestamp(value):
    if not isinstance(value, str) or len(value) > 64 or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9](?::[0-5][0-9](?:\.[0-9]+)?)?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])", value
    ):
        raise ApiError(422, "INVALID_RECORD", "복용 확인 기록 시각의 형식을 확인해 주세요.")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ApiError(422, "INVALID_RECORD", "복용 확인 기록 시각의 날짜를 확인해 주세요.") from None
    return value


# 전체 상태를 검증해 고아 회차나 다른 약에 대한 확인이 저장되지 않게 한다.
def validate_state(value):
    exact_object(value, ("version", "plans", "records"), "복용약 데이터")
    integer(value["version"], "데이터 버전", 1, 1)
    if not isinstance(value["plans"], list) or len(value["plans"]) > MAX_PLANS:
        raise ApiError(422, "LIMIT_EXCEEDED", "처방 묶음은 최대 100개 등록할 수 있어요.")
    if not isinstance(value["records"], list) or len(value["records"]) > MAX_RECORDS:
        raise ApiError(422, "LIMIT_EXCEEDED", "복용 확인 기록은 최대 2000개까지 저장할 수 있어요.")
    plans = []
    by_id = {}
    for raw_plan in value["plans"]:
        plan = validate_plan(raw_plan)
        plan_id = encoded_plan_id(plan["id"])
        if plan_id in by_id:
            raise ApiError(422, "INVALID_STATE", "같은 처방 묶음이 중복되어 있어요.")
        by_id[plan_id] = plan
        plans.append(plan)
    records = []
    seen = set()
    for record in value["records"]:
        exact_object(record, ("occurrenceId", "result", "takenMedicineIds", "recordedAt"), "복용 확인 기록")
        occurrence_id = text_field(record["occurrenceId"], "회차 식별자", 2200, True)
        match = re.fullmatch(r"medication:([^:]+):([0-9]{4}-[0-9]{2}-[0-9]{2})T((?:[01][0-9]|2[0-3]):[0-5][0-9])\+09:00", occurrence_id)
        if not match or match[1] not in by_id or occurrence_id in seen:
            raise ApiError(422, "INVALID_RECORD", "복용 확인 기록이 묶음과 일치하지 않거나 중복되어 있어요.")
        seen.add(occurrence_id)
        day = valid_day(match[2], "복용 회차 날짜")
        medicine_ids = occurrence_medicines(by_id[match[1]], day, match[3])
        result = record["result"]
        if result not in ("all", "some", "none", "deferred"):
            raise ApiError(422, "INVALID_RECORD", "복용 확인 결과를 다시 선택해 주세요.")
        if not isinstance(record["takenMedicineIds"], list) or len(record["takenMedicineIds"]) > MAX_MEDICINES:
            raise ApiError(422, "INVALID_RECORD", "복용한 약 목록의 형식을 확인해 주세요.")
        taken = []
        for item in record["takenMedicineIds"]:
            taken.append(text_field(item, "복용한 약 식별자", 200, True))
        taken_set = set(taken)
        if len(taken_set) != len(taken) or not taken_set <= medicine_ids:
            raise ApiError(422, "INVALID_RECORD", "이 회차에 없는 약이나 중복된 약이 선택되어 있어요.")
        if (
            (result == "all" and taken_set != medicine_ids)
            or (result == "some" and medicine_ids and not 0 < len(taken_set) < len(medicine_ids))
            or (result in ("none", "deferred") and taken_set)
        ):
            raise ApiError(422, "INVALID_RECORD", "복용 확인 결과와 선택한 약이 일치하지 않아요.")
        records.append({"occurrenceId": occurrence_id, "result": result, "takenMedicineIds": taken, "recordedAt": valid_timestamp(record["recordedAt"])})
    return {"version": 1, "plans": plans, "records": records}


# 서버 기록 시각을 UTC ISO 문자열로 통일한다.
def recorded_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# 약·일정 수정에서 확인 기록을 초기화하고 바뀐 확인 시각은 서버가 지정한다.
def prepare_transition(previous, next_state):
    previous_plans = {plan["id"]: plan for plan in previous["plans"]}
    changed = set()
    for plan in next_state["plans"]:
        old = previous_plans.get(plan["id"])
        if old and (old["timing"] != plan["timing"] or old["medicines"] != plan["medicines"]):
            changed.add(encoded_plan_id(plan["id"]))
    previous_records = {record["occurrenceId"]: record for record in previous["records"]}
    now = recorded_now()
    for record in next_state["records"]:
        plan_id = record["occurrenceId"].split(":", 2)[1]
        if plan_id in changed:
            raise ApiError(422, "RECORDS_REQUIRE_RESET", "약 구성이나 일정을 바꿀 때는 이 묶음의 기존 확인 기록을 초기화해 주세요.")
        if previous_records.get(record["occurrenceId"]) != record:
            record["recordedAt"] = now
    return next_state


# 없는 항목만 빈 상태로 표시하고 손상된 서버 데이터를 새 빈 상태로 덮어쓰지 않는다.
def read_snapshot(table, box_id):
    response = table.get_item(Key={"pk": f"BOX#{box_id}", "sk": "MEDICATION_STATE"}, ConsistentRead=True)
    item = response.get("Item")
    if item is None:
        return {"revision": 0, "state": {"version": 1, "plans": [], "records": []}}
    try:
        revision = integer(item["revision"], "저장 버전", 1, MAX_REVISION)
        state = validate_state(item["state"])
    except (ApiError, KeyError, TypeError):
        raise ApiError(503, "STORED_DATA_INVALID", "저장된 복용약 데이터 확인이 필요해요. 기존 데이터는 변경하지 않았어요.") from None
    return {"revision": revision, "state": state}


# 현재 버전과 일치할 때에만 공유 스냅샷을 원자적으로 갱신한다.
def put_snapshot(table, box_id, body):
    exact_object(body, ("expected_revision", "state"), "저장 요청")
    expected = integer(body["expected_revision"], "저장 버전", 0, MAX_REVISION - 1)
    state = validate_state(body["state"])
    previous = read_snapshot(table, box_id)
    if previous["revision"] != expected:
        raise ApiError(409, "REVISION_CONFLICT", "다른 기기에서 복용약이 변경되었어요. 최신 내용을 불러와 확인해 주세요.")
    state = prepare_transition(previous["state"], state)
    # 요청 크기뿐 아니라 정규화·기록 시각 변경 후 실제 저장 크기도 제한한다.
    if len(json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_BYTES:
        raise ApiError(413, "PAYLOAD_TOO_LARGE", "저장할 데이터는 300KB 이하여야 해요.")
    item = {"pk": f"BOX#{box_id}", "sk": "MEDICATION_STATE", "revision": expected + 1, "state": state, "updated_at": recorded_now()}
    request = {"Item": item}
    if expected == 0:
        request["ConditionExpression"] = "attribute_not_exists(pk)"
    else:
        request.update({"ConditionExpression": "#revision = :expected", "ExpressionAttributeNames": {"#revision": "revision"}, "ExpressionAttributeValues": {":expected": expected}})
    try:
        table.put_item(**request)
    except Exception as error:
        if getattr(error, "response", {}).get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise ApiError(409, "REVISION_CONFLICT", "다른 기기에서 먼저 저장했어요. 최신 내용을 불러와 확인해 주세요.") from None
        raise
    return {"revision": expected + 1, "state": state}


# 공통 비밀번호 검증 후 개인 신원 없이 만료되는 팀 접근 토큰만 발급한다.
def create_session(config, body):
    exact_object(body, ("password",), "접근 비밀번호")
    password = body["password"]
    if not isinstance(password, str) or not 1 <= len(password) <= 256:
        raise ApiError(401, "INVALID_PASSWORD", "공통 비밀번호가 올바르지 않아요.")
    try:
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), config["salt"], config["iterations"])
    except UnicodeError:
        raise ApiError(401, "INVALID_PASSWORD", "공통 비밀번호가 올바르지 않아요.") from None
    if not hmac.compare_digest(digest, config["digest"]):
        raise ApiError(401, "INVALID_PASSWORD", "공통 비밀번호가 올바르지 않아요.")
    now = int(time.time())
    claims = {"v": 1, "aud": TOKEN_AUDIENCE, "box_id": config["box_id"], "iat": now, "exp": now + TOKEN_SECONDS, "nonce": secrets.token_urlsafe(12)}
    payload = encode_urlsafe(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signature = encode_urlsafe(hmac.new(config["secret"], payload.encode("ascii"), hashlib.sha256).digest())
    expires = datetime.fromtimestamp(now + TOKEN_SECONDS, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {"token": f"{payload}.{signature}", "expires_at": expires}


# 서명·만료·대상 보관함을 검증하며 URL이나 클라이언트 box_id는 권한으로 쓰지 않는다.
def require_session(config, headers):
    authorization = headers.get("authorization", "")
    try:
        if not authorization.startswith("Bearer ") or len(authorization) > 2048:
            raise ValueError("INVALID_TOKEN")
        payload, signature = authorization[7:].split(".")
        expected = hmac.new(config["secret"], payload.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(decode_urlsafe(signature), expected):
            raise ValueError("INVALID_SIGNATURE")
        claims = json.loads(decode_urlsafe(payload).decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant)
        if not isinstance(claims, dict) or set(claims) != {"v", "aud", "box_id", "iat", "exp", "nonce"}:
            raise ValueError("INVALID_CLAIMS")
        now = int(time.time())
        if (
            type(claims["v"]) is not int or claims["v"] != 1
            or claims["aud"] != TOKEN_AUDIENCE
            or claims["box_id"] != config["box_id"]
            or type(claims["iat"]) is not int
            or type(claims["exp"]) is not int
            or not 0 < claims["iat"] <= now + 30
            or claims["exp"] - claims["iat"] != TOKEN_SECONDS
            or claims["exp"] <= now
            or not isinstance(claims["nonce"], str)
            or not 8 <= len(claims["nonce"]) <= 64
        ):
            raise ValueError("INVALID_CLAIMS")
    except (ValueError, UnicodeError, TypeError, KeyError):
        raise ApiError(401, "SESSION_REQUIRED", "접근 시간이 만료되었거나 인증이 필요해요. 공통 비밀번호를 다시 입력해 주세요.") from None


# 별도 복용약 경로에만 접근 모드를 적용하고 입력 검증과 조건부 저장은 공통으로 유지한다.
def lambda_handler(event, context):
    allowed_origin = None
    try:
        config = configuration()
        headers = {}
        for name, value in (event.get("headers") or {}).items():
            if isinstance(name, str) and isinstance(value, str):
                headers[name.lower()] = value
        origin = headers.get("origin")
        if origin is not None and origin != config["origin"]:
            raise ApiError(403, "ORIGIN_NOT_ALLOWED", "허용된 웹사이트에서 다시 접근해 주세요.")
        if origin == config["origin"]:
            allowed_origin = origin
        method = ((event.get("requestContext") or {}).get("http") or {}).get("method")
        path = event.get("rawPath")
        if path not in ("/session", "/medications"):
            raise ApiError(404, "NOT_FOUND", "지원하지 않는 요청 경로예요.")
        if method == "OPTIONS":
            return respond(204, None, allowed_origin)
        if path == "/session":
            if config["public_demo"]:
                raise ApiError(404, "SESSION_NOT_AVAILABLE", "공개 시연에서는 비밀번호 세션을 사용하지 않아요.")
            if method != "POST":
                raise ApiError(405, "METHOD_NOT_ALLOWED", "지원하지 않는 요청 방식이에요.")
            return respond(200, create_session(config, request_body(event, headers)), allowed_origin)
        if method not in ("GET", "PUT"):
            raise ApiError(405, "METHOD_NOT_ALLOWED", "지원하지 않는 요청 방식이에요.")
        # 공개 시연은 누구나 읽고 수정할 수 있으며 출처 제한은 인증을 대신하지 않는다.
        if not config["public_demo"]:
            require_session(config, headers)
        if method == "GET":
            snapshot = read_snapshot(get_table(), config["box_id"])
        else:
            snapshot = put_snapshot(get_table(), config["box_id"], request_body(event, headers))
        return respond(200, snapshot, allowed_origin)
    except ApiError as error:
        return respond(error.status, {"code": error.code, "message": error.message}, allowed_origin)
    except Exception:
        # 비밀번호·토큰·의료정보·AWS 예외 원문을 응답이나 로그에 남기지 않는다.
        return respond(503, {"code": "MEDICATIONS_UNAVAILABLE", "message": "복용약 저장소에 연결하지 못했어요. 잠시 후 다시 시도해 주세요."}, allowed_origin)
