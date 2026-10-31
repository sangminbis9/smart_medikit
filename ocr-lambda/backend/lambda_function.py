"""S3 이미지의 공통 OCR을 실행하고 성공 제품 또는 안전한 실패 이벤트를 기록한다."""

import hashlib
import json
import logging
import math
import os
import re
from datetime import datetime, timezone
from urllib.parse import unquote_plus

from backend.diagnostics import Diagnostics, event_reference
from backend.image_ocr import ImageError, PaddleReader
from backend.recognition import MfdsClient, RecognitionError, identify
from backend.storage import DynamoStore

LOGGER = logging.getLogger(__name__)
_reader = PaddleReader()


# 예외·OCR 원문·객체 경로 없이 이벤트와 실패 단계·고정 코드만 기록한다.
def log_failure(event_id, stage, code):
    LOGGER.warning(
        json.dumps({"event_ref": event_reference(event_id), "stage": stage, "error_code": code})
    )


# 허용된 S3 입력만 처리하며 제품 저장 실패가 부분 등록으로 이어지지 않게 한다.
def process_records(
    event,
    *,
    s3,
    store,
    api,
    ocr,
    bucket,
    prefix="ocr-input/",
    context=None,
    min_score=0.9,
    diagnostics_enabled=True,
):
    if not math.isfinite(min_score) or not 0 < min_score <= 1:
        raise ValueError("OCR_SCORE_CONFIGURATION_INVALID")
    if (
        not isinstance(event, dict)
        or not isinstance(event.get("Records"), list)
        or not event["Records"]
    ):
        raise ValueError("S3_RECORDS_REQUIRED")
    processed, skipped, failed = 0, 0, False
    for record in event["Records"]:
        if (
            not isinstance(record, dict)
            or record.get("eventSource") != "aws:s3"
            or not record.get("eventName", "").startswith("ObjectCreated:")
        ):
            skipped += 1
            continue
        info = record.get("s3", {})
        obj = info.get("object", {})
        key = unquote_plus(obj.get("key", ""))
        if (
            info.get("bucket", {}).get("name") != bucket
            or not key.startswith(prefix)
            or not key.lower().endswith((".jpg", ".jpeg", ".png"))
        ):
            skipped += 1
            continue
        # 기존 S3 기반 이벤트 ID 산식은 메타데이터가 없는 과거 입력과의 호환을 위해 유지한다.
        identity = [bucket, key, obj.get("versionId"), obj.get("eTag"), obj.get("sequencer")]
        event_id = hashlib.sha256(repr(identity).encode()).hexdigest()
        source = {
            "bucket": bucket,
            "key": key,
            "version_id": obj.get("versionId"),
            "etag": obj.get("eTag"),
        }
        stage = "input"
        try:
            if not obj.get("versionId") and not obj.get("eTag"):
                raise RecognitionError(stage, "S3_OBJECT_IDENTITY_REQUIRED")
            request = {"Bucket": bucket, "Key": key}
            if obj.get("versionId"):
                request["VersionId"] = obj["versionId"]
            else:
                request["IfMatch"] = '"' + obj["eTag"].strip('"') + '"'
            response = s3.get_object(**request)
            body = response["Body"]
            try:
                metadata = response.get("Metadata", {})
                explicit_id = metadata.get("event-id", metadata.get("event_id"))
                if explicit_id is not None:
                    if not isinstance(explicit_id, str) or not re.fullmatch(
                        r"[A-Za-z0-9_-]{1,128}", explicit_id
                    ):
                        raise RecognitionError(stage, "EVENT_ID_INVALID")
                    event_id = explicit_id
                stage = "deduplication"
                if store.completed(event_id, source):
                    skipped += 1
                    continue
                stage = "input"
                if response.get("ContentLength", 0) > 10_000_000:
                    raise ImageError("IMAGE_TOO_LARGE")
                data = body.read(10_000_001)
                if len(data) > 10_000_000:
                    raise ImageError("IMAGE_TOO_LARGE")
            finally:
                body.close()
            stage = "ocr"
            diagnostics = Diagnostics(event_id, enabled=diagnostics_enabled)
            diagnostics.started(min_score)
            cells = (
                ocr(data, diagnostics=diagnostics) if isinstance(ocr, PaddleReader) else ocr(data)
            )
            stage = "extraction"
            result = identify(cells, api, min_score, diagnostics=diagnostics)
            for medicine in result["medicines"]:
                if medicine["ingredients_error_code"]:
                    log_failure(event_id, "ingredients", medicine["ingredients_error_code"])
            stage = "storage"
            saved = store.finish(event_id, source, result, datetime.now(timezone.utc).isoformat())
            processed += int(saved)
            skipped += int(not saved)
        except Exception as exc:
            failed = True
            if isinstance(exc, RecognitionError):
                stage, code, retryable = exc.stage, exc.code, exc.retryable
            elif isinstance(exc, ImageError):
                code, retryable = exc.code, False
            elif isinstance(exc, ValueError) and str(exc) == "EVENT_SOURCE_CONFLICT":
                code, retryable = "EVENT_SOURCE_CONFLICT", False
            else:
                code, retryable = stage.upper() + "_SYSTEM_ERROR", True
            log_failure(event_id, stage, code)
            try:
                store.fail(
                    event_id, source, stage, code, retryable, datetime.now(timezone.utc).isoformat()
                )
            except Exception:
                log_failure(event_id, "failure_storage", "FAILURE_SAVE_FAILED")
    if failed:
        # S3 비동기 호출에서 HTTP 응답 본문만으로 실패를 숨기지 않는다.
        raise RuntimeError("RECOGNITION_BATCH_FAILED") from None
    return {"processed": processed, "skipped": skipped}


# 서버 환경변수만 읽어 기존 S3·DynamoDB 자원으로 공통 파이프라인을 실행한다.
def handler(event, context):
    import boto3

    bucket = os.environ.get("OCR_BUCKET")
    prefix = os.environ.get("OCR_INPUT_PREFIX", "ocr-input/")
    if not bucket or not prefix:
        raise ValueError("OCR_INPUT_CONFIGURATION_REQUIRED")
    service_key = os.environ.get("MFDS_API_KEY")
    if not service_key or not service_key.strip():
        raise ValueError("MFDS_API_KEY_REQUIRED")
    if any(character.isspace() for character in service_key):
        raise ValueError("MFDS_API_KEY_INVALID")
    region = os.environ.get("AWS_REGION", "ap-northeast-2")
    return process_records(
        event,
        s3=boto3.client("s3", region_name=region),
        store=DynamoStore(
            boto3.client("dynamodb", region_name=region),
            os.environ.get("MEDKIT_TABLE", "inha-capstone-02-medkit-data"),
        ),
        api=MfdsClient(service_key, version=os.environ.get("MFDS_API_VERSION", "08")),
        ocr=_reader,
        bucket=bucket,
        prefix=prefix,
        context=context,
        min_score=float(os.environ.get("OCR_MIN_SCORE", "0.9")),
        diagnostics_enabled=os.environ.get("OCR_DIAGNOSTICS", "1") == "1",
    )
