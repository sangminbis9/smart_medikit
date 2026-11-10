"""기존 DynamoDB의 제품 정보와 명시적인 보유 포장을 연결해 읽기 전용으로 반환한다."""

import json
import math
import os
import re
from datetime import date, datetime, timezone
from decimal import Decimal

# 구조화 모듈은 단독 Lambda ZIP과 저장소 테스트에서 같은 코드를 사용한다.
try:
    from product_document import structured_document
except ModuleNotFoundError:
    from backend.product_document import structured_document


# 배포 ZIP과 저장소 실행 모두 기존 OCR의 문서 변환기를 재사용한다.
try:
    from medkit_mfds import ApiError, document_text
except ModuleNotFoundError:
    from importlib import import_module

    _mfds = import_module("ocr-lambda.backend.mfds")
    ApiError, document_text = _mfds.ApiError, _mfds.document_text


TABLE_NAME = "inha-capstone-02-medkit-data"
_table = None


# AWS 실행 역할로 기존 테이블을 연결하며 장기 키를 받거나 저장하지 않는다.
def get_table():
    global _table
    if os.environ.get("MEDKIT_TABLE", TABLE_NAME) != TABLE_NAME:
        raise ValueError("UNEXPECTED_TABLE")
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


# 공백뿐인 필드와 예상하지 않은 자료형은 미확인 값으로 남긴다.
def clean_text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


# 기존 OCR 변환 규칙으로 제목·본문·표 순서를 유지하고 잘못된 문서는 미확인으로 둔다.
def product_text(value):
    try:
        return document_text(value)
    except ApiError:
        return None


# 실제 달력 날짜만 사용하며 누락·잘못된 날짜를 추정하지 않는다.
def clean_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


# DynamoDB Decimal을 JSON 숫자로 바꾸고 음수·비정상 수량을 차단한다.
def clean_number(value, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 9007199254740991:
        return None
    if integer:
        return int(number) if number.is_integer() else None
    return number


# 허용한 제품 필드만 내보내며 OCR 원문·S3 참조·인식 이벤트를 제외한다.
def medicine_view(item):
    code = item.get("product_code")
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{9}", code):
        return None
    if item.get("pk") != f"MEDICINE#{code}" or item.get("sk") != "META":
        return None
    ingredients = []
    if item.get("ingredients_status") == "SUCCEEDED" and isinstance(item.get("ingredients"), list):
        for ingredient in item.get("ingredients", []):
            if isinstance(ingredient, dict):
                name = clean_text(ingredient.get("name"))
                if name and name not in ingredients:
                    ingredients.append(name)
    raw_info = item.get("product_info")
    info = None
    if isinstance(raw_info, dict) and raw_info.get("source") == "MFDS_PERMIT_DETAIL":
        info = {"source": "MFDS_PERMIT_DETAIL"}
        for field in ("indications", "dosage", "precautions"):
            info[field] = product_text(raw_info.get(field))
        source_fields = raw_info
    elif item.get("product_info_status") == "SUCCEEDED":
        # 현재 OCR 파이프라인은 사용정보를 최상위 필드에 저장한다.
        fields = {field: product_text(item.get(field)) for field in ("indications", "dosage", "precautions")}
        if any(fields.values()):
            # 저장된 정보에 없는 공식 출처·전문가 승인을 만들어 붙이지 않는다.
            info = {"source": "DYNAMODB_PRODUCT_INFO", **fields}
            source_fields = item
    if info:
        # 공개 필드에만 구조를 더하고 기존 평문 계약은 유지한다.
        documents = {field: structured_document(source_fields.get(field), product_text)
                     for field in ("indications", "dosage", "precautions")}
        if any(documents.values()):
            info["documents"] = documents
        # 표·계산식 구조를 읽지 못한 필드는 완전한 원문 배열로 오인하지 않게 표시한다.
        incomplete = []
        for field, blocks in documents.items():
            value = source_fields.get(field)
            if isinstance(value, str) and not blocks and re.search(
                r'tagName\s*=\s*["\']table["\']|(?:<|&(?:amp;){0,2}lt;)\s*(?:table|math|sup|sub|img)\b', value, re.I
            ):
                incomplete.append(field)
        if incomplete:
            info["documents_incomplete"] = incomplete
    form = item.get("form")
    return {
        "medicine_id": code,
        "product_code": code,
        "name": clean_text(item.get("name")) or "약품 정보 미확인",
        "form": form if form in ("TABLET", "CAPSULE", "OTHER") else "UNKNOWN",
        "ingredients": ingredients,
        "indications": [info["indications"]] if info and info["indications"] else [],
        "product_info": info,
        "product_info_status": clean_text(item.get("product_info_status")),
        # 공식 원문만으로 증상 태깅이나 전문가 검토 승인을 만들어 내지 않는다.
        "care_assessment": None,
    }


# 명시적으로 저장된 포장만 연결하고 제품 정보에서 보유 상태나 수량을 만들지 않는다.
def package_view(item, medicines, box_id):
    package_id = item.get("package_id")
    if (not isinstance(package_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", package_id)
            or item.get("pk") != f"PACKAGE#{package_id}" or item.get("sk") != "META"
            or item.get("box_id") != box_id
            or item.get("kind") not in ("OTC", "PRESCRIPTION")
            or item.get("state") not in ("REGISTERED", "STORED", "OUT", "DEPLETED", "DISPOSED")):
        return None
    codes = item.get("product_codes")
    if not isinstance(codes, list):
        codes = []
    linked = []
    seen = set()
    for code in codes:
        if isinstance(code, str) and re.fullmatch(r"[0-9]{9}", code) and code not in seen:
            seen.add(code)
            if code in medicines:
                linked.append(medicines[code])
    # 일부 제품이 없으면 알 수 있는 성분만으로 포장 전체를 판정하지 않도록 미확인 항목을 남긴다.
    if len(linked) != len(codes):
        linked.append({"medicine_id": f"unresolved-{package_id}", "name": "연결된 제품 정보 확인 필요"})
    expiry = item.get("expiry") if isinstance(item.get("expiry"), dict) else {}
    actual_expiry = clean_date(expiry.get("actual_expiry"))
    inventory = None
    raw_inventory = item.get("inventory")
    # 정제·캡슐 일반약의 명시적인 예상 수량만 내보낸다.
    if (item["kind"] == "OTC" and linked and all(m.get("form") in {"TABLET", "CAPSULE"} for m in linked)
            and isinstance(raw_inventory, dict) and raw_inventory.get("stock_enabled") is True):
        inventory = {"stock_enabled": True,
                     "initial_qty": clean_number(raw_inventory.get("initial_qty"), integer=True),
                     "estimated_qty": clean_number(raw_inventory.get("estimated_qty"), integer=True)}
    position = None
    raw_position = item.get("position")
    if isinstance(raw_position, dict) and raw_position.get("certainty") in ("OBSERVED", "ESTIMATED", "UNCERTAIN"):
        coordinates = {field: clean_number(raw_position.get(field)) for field in ("x", "y", "z_estimated")}
        if all(value is not None for value in coordinates.values()):
            position = {**coordinates, "certainty": raw_position["certainty"]}
    color = item.get("base_color")
    return {
        "package_id": package_id, "box_id": box_id, "kind": item["kind"], "state": item["state"],
        "first_registration_pending": item.get("first_registration_pending") is True,
        "disposal_certainty": "ESTIMATED" if item.get("disposal_certainty") == "ESTIMATED" else None,
        "name": clean_text(item.get("name")) or (linked[0]["name"] if linked else "약품 정보 미확인"),
        "medicines": linked, "inventory": inventory,
        "remaining_doses": clean_number(item.get("remaining_doses"), integer=True) if item["kind"] == "PRESCRIPTION" else None,
        "expiry": {"actual_expiry": actual_expiry,
                   "temporary_management_date": clean_date(expiry.get("temporary_management_date")),
                   "verified": expiry.get("verified") is True and actual_expiry is not None},
        "position": position,
        "size_class": item.get("size_class") if item.get("size_class") in ("SMALL", "MEDIUM", "LARGE") else None,
        "base_color": color if isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color) else "#d9e4d5",
        "safety_status": "CHECKED" if item.get("safety_status") == "CHECKED" else "SAFETY_CHECK_PENDING",
        # 자유 메모·환자 식별정보·사진·원문은 공개 조회에 포함하지 않는다.
        "note": None,
    }


# 같은 보관함의 모든 제품·포장을 조회하고 누락·부분 페이지를 구분한다.
def read_inventory(table, box_id):
    fields = ["pk", "sk", "product_code", "name", "form", "ingredients",
              "ingredients_status", "product_info", "product_info_status", "indications", "dosage", "precautions",
              "package_id", "box_id", "kind", "state", "product_codes", "expiry", "inventory",
              "remaining_doses", "position", "size_class", "base_color", "safety_status", "first_registration_pending", "disposal_certainty"]
    names = {f"#f{index}": name for index, name in enumerate(fields)}
    query = {
        "Limit": 100,
        "ConsistentRead": True,
        "ProjectionExpression": ", ".join(names),
        "ExpressionAttributeNames": names,
        "FilterExpression": "(begins_with(#f0, :medicine) OR begins_with(#f0, :package)) AND #f1 = :meta",
        "ExpressionAttributeValues": {":medicine": "MEDICINE#", ":package": "PACKAGE#", ":meta": "META"},
    }
    medicines = {}
    package_items = []
    complete = False
    for _ in range(2):
        response = table.scan(**query)
        for item in response.get("Items", []):
            medicine = medicine_view(item)
            if medicine:
                medicines[medicine["medicine_id"]] = medicine
            elif isinstance(item.get("pk"), str) and item["pk"].startswith("PACKAGE#") and item.get("sk") == "META":
                package_items.append(item)
        cursor = response.get("LastEvaluatedKey")
        if not cursor:
            complete = True
            break
        query["ExclusiveStartKey"] = cursor
    packages = []
    inventory_complete = complete
    for item in package_items:
        if item.get("box_id") != box_id:
            if not clean_text(item.get("box_id")):
                inventory_complete = False
            continue
        # 같은 보관함의 일반약·처방약을 공개 플래그와 무관하게 모두 연결한다.
        package = package_view(item, medicines, box_id)
        if package is None:
            inventory_complete = False
        else:
            packages.append(package)
    return {
        "packages": packages,
        "inventory_status": "CONFIRMED" if inventory_complete else "PARTIAL",
        "catalog_medicines": list(medicines.values()), "catalog_complete": complete,
    }


# 민감 응답은 캐시하지 않고 오류에도 내부 내용이나 예외 원문을 포함하지 않는다.
def respond(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"},
        "body": json.dumps(body, ensure_ascii=False),
    }


# API Gateway의 검증된 authorizer 문맥만 신뢰하고 클라이언트 헤더는 권한으로 사용하지 않는다.
def authorized(event, box_id):
    # 공개 시연 조회는 팀이 노출 범위를 승인한 배포에서만 명시적으로 켠다.
    if os.environ.get("MEDKIT_DEMO_PUBLIC_READ") == "true":
        return True
    authorizer = (event.get("requestContext") or {}).get("authorizer") or {}
    context = authorizer.get("lambda") or {}
    return context.get("medkit_read") is True and context.get("box_id") == box_id


# 저장된 제품·포장을 읽으며 등록·입출고·재고·복약 상태를 생성하거나 변경하지 않는다.
def lambda_handler(event, context):
    box_id = os.environ.get("MEDKIT_BOX_ID", "").strip()
    if not box_id:
        return respond(503, {"code": "BOX_NOT_CONFIGURED"})
    if not authorized(event, box_id):
        return respond(401, {"code": "READ_ACCESS_REQUIRED"})
    if (event.get("queryStringParameters") or {}).get("box_id") != box_id:
        return respond(403, {"code": "BOX_MISMATCH"})
    method = ((event.get("requestContext") or {}).get("http") or {}).get("method")
    if method != "GET":
        return respond(405, {"code": "READ_ONLY"})
    path = event.get("rawPath", "")
    if path == "/alerts":
        # 현재 저장 계약에 알림 이력이 없다는 사실을 명시한다.
        return respond(200, {"alerts": [], "alerts_status": "UNAVAILABLE"})
    if path != "/inventory":
        return respond(404, {"code": "NOT_FOUND"})
    try:
        inventory = read_inventory(get_table(), box_id)
    except Exception:
        return respond(503, {"code": "CATALOG_UNAVAILABLE"})
    return respond(200, {
        "box": {"box_id": box_id, "dimensions": None, "device_status": "UNKNOWN"},
        **inventory, "doses": [],
        "synced_at": datetime.now(timezone.utc).isoformat(),
    })
