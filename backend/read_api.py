"""기존 DynamoDB 제품 캐시를 읽고 보관 재고와 구분해 웹 조회 계약으로 반환한다."""

import json
import os
import re
from datetime import datetime, timezone


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


# 허용한 제품 필드만 내보내며 OCR 원문·S3 참조·인식 이벤트를 제외한다.
def medicine_view(item):
    code = item.get("product_code")
    if not isinstance(code, str) or not re.fullmatch(r"\d{9}", code):
        return None
    if item.get("pk") != f"MEDICINE#{code}" or item.get("sk") != "META":
        return None
    ingredients = []
    if item.get("ingredients_status") == "SUCCEEDED":
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
            info[field] = clean_text(raw_info.get(field))
    form = item.get("form")
    return {
        "medicine_id": code,
        "product_code": code,
        "name": clean_text(item.get("name")) or "약품 정보 미확인",
        "form": form if form in {"TABLET", "CAPSULE", "OTHER"} else "UNKNOWN",
        "ingredients": ingredients,
        "indications": [info["indications"]] if info and info["indications"] else [],
        "product_info": info,
        "product_info_status": clean_text(item.get("product_info_status")),
        # 공식 원문만으로 증상 태깅이나 전문가 검토 승인을 만들어 내지 않는다.
        "care_assessment": None,
    }


# 기존 키만 조회하고 읽기량을 제한하며 중간 페이지를 전체 결과로 표시하지 않는다.
def read_catalog(table):
    fields = ["pk", "sk", "product_code", "name", "form", "ingredients",
              "ingredients_status", "product_info", "product_info_status"]
    names = {f"#f{index}": name for index, name in enumerate(fields)}
    query = {
        "Limit": 100,
        "ProjectionExpression": ", ".join(names),
        "ExpressionAttributeNames": names,
        "FilterExpression": "begins_with(#f0, :prefix) AND #f1 = :meta",
        "ExpressionAttributeValues": {":prefix": "MEDICINE#", ":meta": "META"},
    }
    medicines = []
    seen = set()
    complete = False
    for _ in range(2):
        response = table.scan(**query)
        for item in response.get("Items", []):
            medicine = medicine_view(item)
            if medicine and medicine["medicine_id"] not in seen:
                seen.add(medicine["medicine_id"])
                medicines.append(medicine)
        cursor = response.get("LastEvaluatedKey")
        if not cursor:
            complete = True
            break
        query["ExclusiveStartKey"] = cursor
    return medicines, complete


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


# 제품 조회만 제공하며 등록·입출고·재고·복약 상태를 생성하거나 변경하지 않는다.
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
        medicines, complete = read_catalog(get_table())
    except Exception:
        return respond(503, {"code": "CATALOG_UNAVAILABLE"})
    return respond(200, {
        "box": {"box_id": box_id, "dimensions": None, "device_status": "UNKNOWN"},
        "packages": [], "doses": [],
        "inventory_status": "UNAVAILABLE",
        "catalog_medicines": medicines, "catalog_complete": complete,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    })
