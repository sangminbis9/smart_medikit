"""OCR 진단에 필요한 숫자·고정 코드만 제한된 크기로 기록한다."""

import hashlib
import json
import logging
import math
import uuid

from backend.recognition import fragment_kind

LOGGER = logging.getLogger(__name__)
REJECTIONS = frozenset(
    {
        "ANNOTATION_UNVERIFIED",
        "NAME_MISMATCH",
        "STRENGTH_MISMATCH",
        "FORM_MISMATCH",
        "MANUFACTURER_MISMATCH",
        "SCHEMA_INVALID",
        "MATCH",
    }
)


# 호출자가 개인정보를 넣은 이벤트 ID도 원문 대신 연결용 해시로 기록한다.
def event_reference(event_id):
    return hashlib.sha256(event_id.encode("utf-8")).hexdigest()


# 숫자가 아닌 값·비정상 실수는 로그에 복사하지 않는다.
def safe_number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 100_000_000:
        return None
    return round(value, 4)


# 텍스트를 제외한 좌표·점수만 새 객체에 복사한다.
def region_summary(region):
    box = region.get("box")
    return {
        "box": [safe_number(value) for value in box]
        if isinstance(box, (list, tuple)) and len(box) == 4
        else None,
        "score": safe_number(region.get("score")),
    }


class Diagnostics:
    # 호출마다 독립적인 진단 ID와 로그 상한을 준비한다.
    def __init__(self, event_id, enabled=True):
        self.enabled = enabled
        self.event_ref = event_reference(event_id)
        self.attempt_id = uuid.uuid4().hex
        self.remaining = 32

    # 내부에서 만든 숫자·고정 코드 페이로드만 JSON으로 기록한다.
    def _write(self, kind, payload):
        if not self.enabled or self.remaining <= 0:
            return
        self.remaining -= 1
        LOGGER.warning(
            json.dumps(
                {
                    "diagnostic_version": "ocr-diag-v1",
                    "event_ref": self.event_ref,
                    "attempt_id": self.attempt_id,
                    "kind": kind,
                    **payload,
                },
                allow_nan=False,
            )
        )

    # 모델 초기화 전에도 새 진단 코드 실행 여부를 확인할 수 있게 한다.
    def started(self, threshold):
        self._write("pipeline_started", {"threshold": safe_number(threshold)})

    # 검출 영역은 최대 120개만 기록하고 잘린 개수를 명시한다.
    def regions(self, lines, shape):
        self._write(
            "ocr_regions",
            {
                "image_height": safe_number(shape[0]),
                "image_width": safe_number(shape[1]),
                "region_count": len(lines),
                "omitted_regions": max(0, len(lines) - 120),
                "regions": [region_summary(line) for line in lines[:120]],
            },
        )

    # 첫 후보에서 실패해도 다른 후보의 영역과 점수를 확인할 수 있게 먼저 기록한다.
    def candidates(self, cells, threshold):
        summaries = []
        for index, cell in enumerate(cells[:25]):
            parts = cell.get("lines", [])
            summaries_parts = []
            for part in parts[:24]:
                score = part.get("score")
                summaries_parts.append(
                    {
                        **region_summary(part),
                        "role": fragment_kind(part["text"]),
                        "passes_threshold": safe_number(score) is not None
                        and threshold <= score <= 1,
                    }
                )
            summaries.append(
                {
                    "candidate_index": index,
                    "box": region_summary(cell)["box"],
                    "part_count": len(parts),
                    "omitted_parts": max(0, len(parts) - 24),
                    "parts": summaries_parts,
                    "metadata": [region_summary(part) for part in cell.get("metadata", [])[:8]],
                }
            )
        self._write(
            "ocr_candidates",
            {
                "candidate_count": len(cells),
                "threshold": safe_number(threshold),
                "omitted_candidates": max(0, len(cells) - 25),
                "candidates": summaries,
            },
        )

    # 품목 이름·코드·API 응답 대신 첫 불일치 사유별 개수만 기록한다.
    def matching(self, index, returned, accepted, reasons):
        self._write(
            "product_matching",
            {
                "candidate_index": index,
                "api_row_count": returned,
                "accepted_product_count": accepted,
                "rejections": {key: value for key, value in reasons.items() if key in REJECTIONS},
            },
        )
