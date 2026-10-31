"""기존 pk/sk를 유지하며 성공 제품은 원자 저장하고 실패 이벤트만 별도 기록한다."""

import hashlib
import json
import re
import time
from decimal import Decimal

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError


# 저수준 DynamoDB 클라이언트에 필요한 속성 타입을 직렬화한다.
def attributes(values):
    serializer = TypeSerializer()
    return {key: serializer.serialize(value) for key, value in values.items()}


# S3 버전 또는 ETag를 이용하여 같은 이벤트 ID의 다른 입력을 구분한다.
def source_fingerprint(source):
    return hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()


# 저장 직전에도 완료 상태·필수 필드·제품 참조를 재검증한다.
def validate_result(result):
    if (
        result.get("processing_status") != "COMPLETED"
        or any(
            result.get(field) != "SUCCEEDED"
            for field in (
                "recognition_status",
                "ocr_status",
                "extraction_status",
                "matching_status",
            )
        )
        or not result.get("items")
        or not result.get("medicines")
        or len(result["items"]) > 25
        or len(result["medicines"]) > 25
    ):
        raise ValueError("SUCCESS_RESULT_REQUIRED")
    codes = set()
    for medicine in result["medicines"]:
        code = medicine.get("product_code")
        if (
            not isinstance(code, str)
            or not re.fullmatch(r"\d{9}", code)
            or code in codes
            or medicine.get("medicine_id") != "MFDS#" + code
            or not isinstance(medicine.get("name"), str)
            or not medicine["name"].strip()
            or medicine.get("form") not in {"TABLET", "CAPSULE", "OTHER"}
            or not medicine.get("dosage_form")
            or medicine.get("ingredients_status") not in {"SUCCEEDED", "NO_DATA", "UNAVAILABLE"}
            or not isinstance(medicine.get("ingredients"), list)
        ):
            raise ValueError("REQUIRED_PRODUCT_FIELDS_MISSING")
        codes.add(code)
    item_codes = set()
    for item in result["items"]:
        score = item.get("name_score")
        if (
            item.get("recognition_status") != "SUCCEEDED"
            or item.get("product_code") not in codes
            or not isinstance(score, Decimal)
            or not score.is_finite()
            or not 0 < score <= 1
        ):
            raise ValueError("SUCCESS_RESULT_REQUIRED")
        item_codes.add(item["product_code"])
    if item_codes != codes:
        raise ValueError("SUCCESS_RESULT_REQUIRED")


class DynamoStore:
    # 지정한 기존 메인 테이블만 사용한다.
    def __init__(self, client, table_name="inha-capstone-02-medkit-data"):
        if table_name != "inha-capstone-02-medkit-data":
            raise ValueError("MAIN_TABLE_REQUIRED")
        self.client, self.table_name = client, table_name

    # 완료 기록을 강한 일관성으로 조회하고 입력 충돌을 거부한다.
    def completed(self, event_id, source):
        raw = self.client.get_item(
            TableName=self.table_name,
            Key=attributes({"pk": "RECOGNITION#" + event_id, "sk": "META"}),
            ConsistentRead=True,
        ).get("Item", {})
        existing = {key: TypeDeserializer().deserialize(value) for key, value in raw.items()}
        if existing.get("processing_status") != "COMPLETED":
            return False
        previous = existing.get("source_fingerprint")
        if previous is None and existing.get("source_image"):
            previous = source_fingerprint(existing["source_image"])
        if previous != source_fingerprint(source):
            raise ValueError("EVENT_SOURCE_CONFLICT")
        return True

    # 제품 갱신과 완료 이벤트를 한 트랜잭션으로 저장하고 중복 완료를 차단한다.
    def finish(self, event_id, source, result, checked_at):
        validate_result(result)
        writes = []
        for medicine in result["medicines"]:
            fields = {
                key: medicine[key]
                for key in (
                    "medicine_id",
                    "product_code",
                    "name",
                    "strength",
                    "form",
                    "dosage_form",
                    "manufacturer",
                    "ingredients_status",
                    "ingredients_error_code",
                )
            }
            fields.update(entity_type="Medicine", ingredients_checked_at=checked_at)
            names, values, expressions = {}, {}, []
            for index, (name, value) in enumerate(fields.items()):
                alias, token = "#m" + str(index), ":m" + str(index)
                names[alias], values[token] = name, value
                expressions.append(alias + "=" + token)
            names["#ingredients"] = "ingredients"
            if medicine["ingredients_status"] == "SUCCEEDED":
                expressions.extend(
                    ["#ingredients=:ingredients", "ingredients_last_success_at=:checked"]
                )
                values.update({":ingredients": medicine["ingredients"], ":checked": checked_at})
            else:
                # 선택 API 장애가 과거에 검증된 정상 성분을 빈 목록으로 덮어쓰지 않는다.
                expressions.append("#ingredients=if_not_exists(#ingredients,:empty)")
                values[":empty"] = []
            writes.append(
                {
                    "Update": {
                        "TableName": self.table_name,
                        "Key": attributes(
                            {"pk": "MEDICINE#" + medicine["product_code"], "sk": "META"}
                        ),
                        "UpdateExpression": "SET " + ", ".join(expressions),
                        "ExpressionAttributeNames": names,
                        "ExpressionAttributeValues": attributes(values),
                    }
                }
            )
        item = {
            "pk": "RECOGNITION#" + event_id,
            "sk": "META",
            "entity_type": "RecognitionEvent",
            "event_id": event_id,
            "source_image": source,
            "source_fingerprint": source_fingerprint(source),
            "processed_at": checked_at,
            **result,
        }
        writes.append(
            {
                "Put": {
                    "TableName": self.table_name,
                    "Item": attributes(item),
                    # 실패 기록과 구버전 미완료 기록은 같은 키에서 재처리하고 성공 기록은 불변으로 둔다.
                    "ConditionExpression": "attribute_not_exists(pk) OR #state IN (:failed,:review,:pending) OR (#state=:processing AND lease_until<:now)",
                    "ExpressionAttributeNames": {"#state": "processing_status"},
                    "ExpressionAttributeValues": attributes(
                        {
                            ":failed": "FAILED",
                            ":review": "REVIEW_REQUIRED",
                            ":pending": "PENDING",
                            ":processing": "PROCESSING",
                            ":now": int(time.time()),
                        }
                    ),
                }
            }
        )
        try:
            self.client.transact_write_items(TransactItems=writes)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "TransactionCanceledException" and self.completed(
                event_id, source
            ):
                return False
            raise

    # 실패 단계·고정 코드만 이벤트에 기록하고 이미 완료된 성공은 덮어쓰지 않는다.
    def fail(self, event_id, source, stage, code, retryable, checked_at):
        item = {
            "pk": "RECOGNITION#" + event_id,
            "sk": "META",
            "entity_type": "RecognitionEvent",
            "event_id": event_id,
            "source_image": source,
            "source_fingerprint": source_fingerprint(source),
            "processing_status": "FAILED",
            "failure_stage": stage,
            "failure_code": code,
            "retryable": retryable,
            "processed_at": checked_at,
        }
        try:
            self.client.put_item(
                TableName=self.table_name,
                Item=attributes(item),
                ConditionExpression="attribute_not_exists(pk) OR #state IN (:failed,:review,:pending) OR (#state=:processing AND lease_until<:now)",
                ExpressionAttributeNames={"#state": "processing_status"},
                ExpressionAttributeValues=attributes(
                    {
                        ":failed": "FAILED",
                        ":review": "REVIEW_REQUIRED",
                        ":pending": "PENDING",
                        ":processing": "PROCESSING",
                        ":now": int(time.time()),
                    }
                ),
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
