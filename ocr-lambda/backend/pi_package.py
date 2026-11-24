"""Pi 포장 사진을 판독하고 확정된 품목만 운영 허가정보 보완에 넘긴다."""

import logging
import math
import re

from .diagnostics import Diagnostics
from .image_ocr import (
    ImageError,
    PaddleReader,
    foreground_region,
    read_image,
    reconcile_regions,
)
from .mfds import MfdsClient
from .package_matching import PackageMatcher
from .recognition import RecognitionError, normalize_name

LOGGER = logging.getLogger(__name__)
RECHECK_ERRORS = {"NO_PRODUCT_NAME", "LOW_OCR_CONFIDENCE", "PRODUCT_NOT_FOUND", "API_RESULT_LIMIT"}


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

    # 동일 이미지의 원본·두 배·네 배·기울기 보정을 순차 검증하고 저장 시간을 남긴다.
    def match_package(self, data, api, min_score, diagnostics, context=None):
        image = read_image(data)
        height, width = image.shape[:2]
        full_box = [0, 0, width, height]
        crop_box = foreground_region(image) or full_box
        lines = []
        matcher = PackageMatcher(api, min_score, diagnostics)
        error = RecognitionError("extraction", "NO_PRODUCT_NAME")
        for pass_index, scale in enumerate((1, 2, 4, 2)):
            if context is not None and context.get_remaining_time_in_millis() <= 30000:
                raise error
            box = full_box if pass_index == 0 else crop_box
            if pass_index == 2 and max(box[2] - box[0], box[3] - box[1]) * 2 >= 2048:
                # 두 배에서 이미 상한에 도달했다면 동일 입력의 네 배 추론은 건너뛴다.
                continue
            options = {"package": True}
            if pass_index:
                options["scale_factor"] = scale
            if pass_index == 3:
                angled = [part for part in lines if 2 <= abs(part.get("angle", 0)) <= 60]
                angle = max(angled, key=package_text_area)["angle"] if angled else 0
                local_box = matcher.retry_box(image.shape)
                if local_box:
                    box = local_box
                    options["scale_factor"] = 4
                # 제목이 있으면 이웃 글자까지 국소 재검출하고, 없으면 검출 확장률을 바꾼다.
                options.update(deskew_angle=angle, refined=not bool(angle or local_box))
            fresh = self._predict(image, box, **options)
            diagnostics.regions(fresh, image.shape, pass_index=pass_index, source_box=box)
            for part in fresh:
                # 의약외품을 같은 상표의 의약품으로 잘못 등록하는 것보다 확인 대기를 우선한다.
                if part["score"] >= 0.6 and re.search(r"의[약익]외품", normalize_name(part["text"])):
                    raise RecognitionError("validation", "UNSUPPORTED_PRODUCT_TYPE")
            # 저신뢰 문구는 이름·제형 결합과 검색에 사용하지 않는다.
            strong = [part for part in fresh if part["score"] >= min_score]
            lines = reconcile_regions(lines, strong)
            try:
                return matcher.match(lines)
            except RecognitionError as caught:
                error = caught
                if error.code not in RECHECK_ERRORS and not getattr(error, "can_recheck", False):
                    raise
        raise error

    # 식약처 대조와 운영 허가정보 저장을 연결하며 저신뢰·복수 품목은 등록하지 않는다.
    def identify(self, data, legacy_api, enrich, min_score, event_id, context=None):
        diagnostics = Diagnostics(event_id)
        diagnostics.started(min_score)
        api = MfdsClient(legacy_api.service_key, version=legacy_api.version)
        api.reserve_seconds = 30
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


# 대각선 보정은 넓은 제목의 실제 글자 방향을 우선한다.
def package_text_area(part):
    quad = part.get("quad")
    if quad:
        return math.dist(quad[0], quad[1]) * math.dist(quad[0], quad[3])
    x1, y1, x2, y2 = part["box"]
    return (x2 - x1) * (y2 - y1)
