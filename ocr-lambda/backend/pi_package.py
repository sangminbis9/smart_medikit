"""Pi 포장 사진을 판독하고 확정된 품목만 운영 허가정보 보완에 넘긴다."""

import logging

from .diagnostics import Diagnostics
from .image_ocr import ImageError, PaddleReader, group_lines, read_image
from .mfds import MfdsClient
from .recognition import RecognitionError, match_products

LOGGER = logging.getLogger(__name__)
RECHECK_ERRORS = {"NO_PRODUCT_NAME", "LOW_OCR_CONFIDENCE", "PRODUCT_NOT_FOUND"}


class PackageReader(PaddleReader):
    # 운영의 기존 입력과 Pi 입력이 하나의 모델을 공유하게 한다.
    def __init__(self, legacy_reader=None):
        super().__init__()
        self.legacy_reader = legacy_reader

    # 같은 Lambda 컨테이너에서 입력 종류가 바뀌어도 모델을 두 번 적재하지 않는다.
    def _engine(self):
        if self.legacy_reader is None:
            return super()._engine()
        if self.legacy_reader.engine is None:
            self.legacy_reader.engine = super()._engine()
        return self.legacy_reader.engine

    # 동일 이미지에서 확인된 단일 품목만 반환하며 문자 누락 때 한 번 재검출한다.
    def match_package(self, data, api, min_score, diagnostics, context=None):
        image = read_image(data)
        height, width = image.shape[:2]
        lines = self._predict(image, [0, 0, width, height], package=True)
        diagnostics.regions(lines, image.shape)
        try:
            matches, _, _ = match_products(group_lines(lines), api, min_score, diagnostics)
        except RecognitionError as error:
            if error.code not in RECHECK_ERRORS or (
                context is not None and context.get_remaining_time_in_millis() < 45000
            ):
                raise
            cells = self.refine_package_cells(image, lines, diagnostics)
            matches, _, _ = match_products(cells, api, min_score, diagnostics)
        if len(matches) != 1:
            raise RecognitionError("matching", "AMBIGUOUS_PRODUCT")
        return matches[0]

    # 식약처 대조와 운영 허가정보 저장을 연결하며 저신뢰·복수 품목은 등록하지 않는다.
    def identify(self, data, legacy_api, enrich, min_score, event_id, context=None):
        diagnostics = Diagnostics(event_id)
        diagnostics.started(min_score)
        api = MfdsClient(legacy_api.service_key, version=legacy_api.version)
        api.begin_event(context, diagnostics)
        try:
            medicine = self.match_package(data, api, min_score, diagnostics, context)
        except (ImageError, RecognitionError) as error:
            stage = error.stage if isinstance(error, RecognitionError) else "ocr"
            retryable = isinstance(error, RecognitionError) and error.retryable
            LOGGER.warning("Pi OCR %s %s %s", diagnostics.event_ref, stage, error.code)
            return {
                "processing_status": "FAILED" if retryable else "REVIEW_REQUIRED",
                "failure_stage": stage,
                "failure_code": error.code,
                "retryable": retryable,
                "medicines": [],
            }
        product = {
            "ITEM_SEQ": medicine["product_code"],
            "ITEM_NAME": medicine["name"],
            "ENTP_NAME": medicine["manufacturer"],
        }
        result = enrich([{"lines": []}], legacy_api, min_score, verified_product=product)
        saved = result["medicines"]
        if len(saved) != 1 or saved[0]["product_code"] != medicine["product_code"]:
            raise RuntimeError("VERIFIED_PRODUCT_ENRICHMENT_MISMATCH")
        saved[0]["form"] = medicine["form"]
        # 공식 품목이 확정됐으므로 선택적 성분·허가정보 장애는 해당 필드의 상태로 보존한다.
        result.update(processing_status="COMPLETED", retryable=False)
        return result
