"""위치별 OCR 근거와 식약처 제품 정보를 대조하고 검증된 제품만 반환한다."""

import math
import re
import unicodedata
from decimal import Decimal
from itertools import product as combinations

from backend.mfds import ApiError, MfdsClient, ingredient_gaps, validate_product_info

FORMS = (
    "연질캡슐",
    "경질캡슐",
    "서방정",
    "장용정",
    "구강붕해정",
    "필름코팅정",
    "캡슐",
    "시럽",
    "현탁액",
    "점안액",
    "내복액",
    "크림",
    "연고",
    "과립",
    "정",
    "겔",
    "액",
)
FORM_PATTERN = "(?:" + "|".join(FORMS) + ")"
STRENGTH = re.compile(r"\d+(?:\.\d+)?(?:mg|ug|g|ml|%)(?:/\d*(?:ml|g))?")
QUANTITY = re.compile(r"(?<!\d)(\d+)(연질캡슐|캡슐|정|매|포)(?![가-힣a-z])")
NOISE = re.compile(
    r"일반의약품|전문의약품|판매|가격|\d원|복용|\d일|\d회|식후|감기|콧물|코막힘|재채기|기침|"
    r"알레르기|두통|치통|근육통|생리통|관절|인후|가려움|해열|진통|효과|증상|완화|함유|증량|"
    r"제조|업체|수입|성명|환자|병원|약국|전화|처방|주소|사용|주의|보관|유통|제약|"
    r"녹십자|종근당|유한양행|알피바이오|celltrion|madein|since|neosol|빠른|강력한|부드러운|"
    r"처음부터|뚝떨어|^외용살균소독제$",
    re.I,
)
# 종근당 공식 제품 페이지 CKD0000525에서 확인한 상표와 허가명 표기 대응이다.
BRAND_ALIASES = {"모드코s": "모드코에스"}
# 탁센 공식 설명서의 전체 제품명만 대응하며 다른 탁센 제품으로 확장하지 않는다.
BRAND_ALIASES["탁센400"] = "탁센400이부프로펜"


class RecognitionError(Exception):
    # 실패 단계와 고정 오류 코드만 전달하고 OCR 원문은 보존하지 않는다.
    def __init__(self, stage, code, retryable=False):
        super().__init__(code)
        self.stage, self.code, self.retryable = stage, code, retryable


# 함량과 제형은 유지하면서 공백·표기 단위만 정규화한다.
def normalize_name(value):
    value = unicodedata.normalize("NFKC", value).lower()
    for old, new in [
        ("마이크로그램", "ug"),
        ("밀리그램", "mg"),
        ("밀리그람", "mg"),
        ("밀리리터", "ml"),
        ("그램", "g"),
        ("캅셀", "캡슐"),
        ("µg", "ug"),
        ("μg", "ug"),
    ]:
        value = value.replace(old, new)
    return re.sub(r"[\sㆍ·®™]", "", value)


# 제형·함량·포장 수량을 구분하여 제품명에서 별도 필드로 추출한다.
def name_fields(text):
    text = re.sub(r"^(약품명|제품명)\s*[:：]?\s*", "", text.strip())
    normalized = normalize_name(text)
    quantities = {(int(match[0]), match[1]) for match in QUANTITY.findall(normalized)}
    normalized = QUANTITY.sub("", normalized)
    strengths = set(STRENGTH.findall(normalized))
    normalized = STRENGTH.sub("", normalized)
    form_match = re.search(FORM_PATTERN + r"$", normalized)
    form = form_match.group() if form_match else None
    stem = normalized[: form_match.start()] if form_match else normalized
    return {"name": stem, "form": form, "strengths": strengths, "quantities": quantities}


# 이름 없는 제형과 부가정보를 먼저 구분하여 광범위한 제품 검색을 막는다.
def fragment_kind(text):
    value = normalize_name(text)
    if re.fullmatch(FORM_PATTERN, value):
        return "form"
    if re.fullmatch(r"\d+(?:\.\d+)?(?:ml|g)", value):
        return "package_size"
    if STRENGTH.fullmatch(value):
        return "strength"
    if QUANTITY.fullmatch(value):
        return "quantity"
    if re.match(r"^(제조사|업체명)[:：]", value):
        return "manufacturer"
    return "name"


# 제형이 없는 상표도 후보로 허용하되 안내·가격·성분 설명은 제외한다.
def candidate_name(text):
    if fragment_kind(text) != "name":
        return None
    fields = name_fields(text)
    name = fields["name"]
    if (
        len(name) < 2
        or len(name) > 60
        or NOISE.search(name)
        or not re.fullmatch(r"[가-힣a-z][가-힣a-z0-9-]+", name)
        or name in {"약품", "약품명", "제품명", "약명", "연질", "캡슐"}
    ):
        return None
    return name


# 실제 제품명 구성 요소만 점수를 계산하고 사용한 제형·함량도 별도로 검증한다.
def extract_candidate(cell, min_score):
    parts = cell["lines"]
    name_parts = [line for line in parts if fragment_kind(line["text"]) == "name"]
    if not name_parts:
        raise RecognitionError("extraction", "NO_PRODUCT_NAME")
    for part in parts:
        score = part["score"]
        if not math.isfinite(score) or not min_score <= score <= 1:
            stage = "extraction" if part in name_parts else "validation"
            raise RecognitionError(stage, "LOW_OCR_CONFIDENCE")
    fields = name_fields("".join(part["text"] for part in parts))
    if len(fields["strengths"]) > 1 or len(fields["quantities"]) > 1:
        raise RecognitionError("validation", "CONFLICTING_ATTRIBUTES")
    fields["score"] = Decimal(str(min(part["score"] for part in name_parts)))
    fields["manufacturer"] = None
    fields["package_size"] = None
    for part in cell.get("metadata", []):
        # 이름 판별과 무관한 부가정보의 낮은 점수는 이름 점수를 낮추지 않는다.
        if not math.isfinite(part["score"]) or not min_score <= part["score"] <= 1:
            continue
        if fragment_kind(part["text"]) == "package_size":
            match = re.fullmatch(r"(\d+(?:\.\d+)?)(ml|g)", normalize_name(part["text"]))
            size = {"value": Decimal(match[1]), "unit": match[2]}
            if fields["package_size"] and fields["package_size"] != size:
                raise RecognitionError("validation", "CONFLICTING_ATTRIBUTES")
            fields["package_size"] = size
        elif fragment_kind(part["text"]) == "quantity":
            fields["quantities"].update(name_fields(part["text"])["quantities"])
        else:
            maker = re.split(r"[:：]", part["text"], maxsplit=1)[1]
            if fields["manufacturer"] and normalize_name(maker) != normalize_name(
                fields["manufacturer"]
            ):
                raise RecognitionError("validation", "CONFLICTING_ATTRIBUTES")
            fields["manufacturer"] = maker
    if len(fields["quantities"]) > 1:
        raise RecognitionError("validation", "CONFLICTING_ATTRIBUTES")
    return fields


# 승인된 별칭만 변환하며 임의의 철자 유사도나 전체 영문 치환은 사용하지 않는다.
def canonical_brand(name):
    return BRAND_ALIASES.get(name, name)


# 공식 성분 필드와 일치한 괄호만 이름에서 분리한다.
def official_fields(product, details=()):
    name = product["ITEM_NAME"].strip()
    annotation = re.fullmatch(r"(.+)\(([^()]+)\)", name)
    annotation_fields = None
    if annotation:
        ingredients = product.get("ITEM_INGR_NAME") or product.get("MAIN_ITEM_INGR") or ""
        ingredient_names = {
            normalize_name(part)
            for part in re.split(r"[;,|]", ingredients) + [row["name"] for row in details]
        }
        annotation_fields = name_fields(annotation[2])
        if normalize_name(annotation[2]) not in ingredient_names and not (
            annotation_fields["name"] in ingredient_names
            and (annotation_fields["form"] or annotation_fields["strengths"])
        ):
            return None
        name = annotation[1]
    fields = name_fields(name)
    if annotation_fields:
        # 공식 괄호에서 성분이 검증된 경우에만 명시적 제형·농도를 보존한다.
        if (
            fields["form"]
            and annotation_fields["form"]
            and fields["form"] != annotation_fields["form"]
        ):
            return None
        if (
            fields["strengths"]
            and annotation_fields["strengths"]
            and fields["strengths"] != annotation_fields["strengths"]
        ):
            return None
        fields["form"] = fields["form"] or annotation_fields["form"]
        fields["strengths"] = fields["strengths"] or annotation_fields["strengths"]
    return fields


# 이름·관측 함량·세부 제형·명시된 업체명을 함께 대조한다.
def product_match_reason(observed, product, details=()):
    official = official_fields(product, details)
    if official is None:
        return "ANNOTATION_UNVERIFIED"
    name, target = canonical_brand(observed["name"]), canonical_brand(official["name"])
    # 브랜드만 관측한 경우 계열 전체를 검토하며 두 품목 이상이면 상위 로직에서 실패한다.
    if name != target and not (name == "화이투벤" and target.startswith(name)):
        return "NAME_MISMATCH"
    if observed["strengths"] and observed["strengths"] != official["strengths"]:
        return "STRENGTH_MISMATCH"
    if observed["form"] and observed["form"] != official["form"]:
        return "FORM_MISMATCH"
    if observed["manufacturer"]:
        maker = normalize_name(observed["manufacturer"]).replace("(주)", "").replace("주식회사", "")
        target_maker = (
            normalize_name(product.get("ENTP_NAME", "")).replace("(주)", "").replace("주식회사", "")
        )
        if maker != target_maker:
            return "MANUFACTURER_MISMATCH"
    return "MATCH"


# 기존 불리언 대조 인터페이스와 동일한 판단 기준을 유지한다.
def matches_product(observed, product):
    return product_match_reason(observed, product) == "MATCH"


# 공식 성분 상세의 이름 필드를 검증하고 원문 없는 오류로 변환한다.
def load_ingredients(api, code):
    ingredients = api.ingredients(code)
    if not isinstance(ingredients, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("name"), str) or not row["name"].strip()
        for row in ingredients
    ):
        raise ApiError("API_INGREDIENT_SCHEMA_MISMATCH")
    return ingredients


# 같은 영역의 판독 대안만 제한적으로 조합하고 다른 약품 영역은 섞지 않는다.
def reading_combinations(parts):
    choices = [[part] + part.get("alternatives", []) for part in parts]
    if math.prod(len(choice) for choice in choices) > 8:
        raise RecognitionError("extraction", "TOO_MANY_OCR_ALTERNATIVES")
    return list(combinations(*choices))


# 상표 사전 대신 같은 줄의 간격·높이로 두 이름 조각의 연결 가설을 제한한다.
def tight_name_pair(left, right):
    if left.get("evidence") == "row" or right.get("evidence") == "row":
        return False
    fields = name_fields("".join(part["text"] for part in left["lines"]))
    if fields["form"] or fields["strengths"] or fields["quantities"]:
        return False
    x1, y1, x2, y2 = left["box"]
    a1, b1, a2, b2 = right["box"]
    h, k = y2 - y1, b2 - b1
    return (
        min(h, k) > 0
        and max(h, k) <= min(h, k) * 4
        and a1 > x1
        and a2 > x2
        and -min(x2 - x1, a2 - a1) * 0.2 <= a1 - x2 <= min(h, k) * 0.5
        and min(y2, b2) - max(y1, b1) >= min(h, k) * 0.7
    )


# 독립 후보와 인접 결합을 모두 공식 품목에 대조하며 원래 영역 번호를 보존한다.
def candidate_observations(cells, index, min_score):
    cell = cells[index]
    options = [([index], cell)]
    for other_index, other in enumerate(cells):
        if other_index == index:
            continue
        indices = (
            [other_index, index]
            if tight_name_pair(other, cell)
            else [index, other_index]
            if tight_name_pair(cell, other)
            else None
        )
        if indices is None or any(
            part["score"] < min_score for i in indices for part in cells[i]["lines"]
        ):
            continue
        left, right = [cells[i] for i in indices]
        options.append(
            (
                indices,
                {
                    "lines": left["lines"] + right["lines"],
                    "metadata": left.get("metadata", []) + right.get("metadata", []),
                    "box": [
                        min(left["box"][0], right["box"][0]),
                        min(left["box"][1], right["box"][1]),
                        max(left["box"][2], right["box"][2]),
                        max(left["box"][3], right["box"][3]),
                    ],
                },
            )
        )
    observations = []
    for indices, option in options:
        for parts in reading_combinations(option["lines"]):
            observed = extract_candidate({**option, "lines": list(parts)}, min_score)
            observed["_indices"], observed["_box"] = indices, option["box"]
            observations.append(observed)
            if len(observations) > 8:
                raise RecognitionError("extraction", "TOO_MANY_OCR_ALTERNATIVES")
    return observations


# 유일하게 식별된 큰 제목에 밀착한 작은 부가 문구만 같은 포장의 문맥으로 분류한다.
def nearby_context(cell, owner):
    fields = name_fields("".join(part["text"] for part in cell["lines"]))
    if cell.get("evidence") == "row" or fields["form"] or fields["strengths"]:
        return False
    x1, y1, x2, y2 = cell["box"]
    a1, b1, a2, b2 = owner["box"]
    height = max(min(p["box"][3] - p["box"][1], p["box"][2] - p["box"][0]) for p in cell["lines"])
    other = max(min(p["box"][3] - p["box"][1], p["box"][2] - p["box"][0]) for p in owner["lines"])
    if height >= other * 0.85:
        return False
    horizontal = min(x2, a2) - max(x1, a1)
    vertical = min(y2, b2) - max(y1, b1)
    return (
        horizontal >= min(x2 - x1, a2 - a1) * 0.5 and max(b1 - y2, y1 - b2, 0) <= other * 0.75
    ) or (vertical >= min(y2 - y1, b2 - b1) * 0.7 and max(a1 - x2, x1 - a2, 0) <= other * 0.5)


# 한 판독 대안을 공식 품목과 대조하며 검색·성분 결과를 이벤트 안에서 재사용한다.
def lookup_candidate(observed, api, ingredient_cache, search_cache, index, diagnostics):
    try:
        query = canonical_brand(observed["name"])
        if query not in search_cache:
            if len(search_cache) >= 32:
                raise RecognitionError("matching", "TOO_MANY_SEARCH_QUERIES")
            search_cache[query] = api.search(query)
        rows = search_cache[query]
    except ApiError as exc:
        raise RecognitionError("matching", exc.code, exc.retryable) from None
    candidates, reasons, detail_checks, plausible = {}, {}, 0, False
    for product in rows:
        # 불완전한 응답 행을 버리고 남은 하나를 유일 후보로 오판하지 않는다.
        if (
            not isinstance(product, dict)
            or not isinstance(product.get("ITEM_NAME"), str)
            or not product["ITEM_NAME"].strip()
            or not re.fullmatch(r"\d{9}", str(product.get("ITEM_SEQ", "")))
        ):
            if diagnostics is not None:
                diagnostics.matching(
                    index, len(rows), len(candidates), {**reasons, "SCHEMA_INVALID": 1}
                )
            raise RecognitionError("matching", "API_PRODUCT_SCHEMA_INVALID")
        reason = product_match_reason(observed, product)
        code = str(product["ITEM_SEQ"])
        if reason == "ANNOTATION_UNVERIFIED":
            annotation = re.fullmatch(r"(.+)\(([^()]+)\)", product["ITEM_NAME"].strip())
            # 이름 본문·업체가 맞으면 성분 괄호를 확인하고 제형·함량까지 다시 대조한다.
            bare = {**product, "ITEM_NAME": annotation[1]} if annotation else product
            base_observed = {**observed, "form": None, "strengths": set()}
            if annotation and product_match_reason(base_observed, bare) == "MATCH":
                plausible = True
                detail_checks += 1
                try:
                    if code not in ingredient_cache:
                        ingredient_cache[code] = load_ingredients(api, code)
                except Exception as exc:
                    if diagnostics is not None:
                        diagnostics.matching(
                            index,
                            len(rows),
                            len(candidates),
                            {**reasons, "ANNOTATION_LOOKUP_FAILED": 1},
                            detail_checks,
                        )
                    error = exc.code if isinstance(exc, ApiError) else "ANNOTATION_LOOKUP_FAILED"
                    retryable = exc.retryable if isinstance(exc, ApiError) else True
                    raise RecognitionError("matching", error, retryable) from None
                reason = product_match_reason(observed, product, ingredient_cache[code])
        reasons[reason] = reasons.get(reason, 0) + 1
        if reason in {"MATCH", "STRENGTH_MISMATCH", "FORM_MISMATCH", "MANUFACTURER_MISMATCH"}:
            plausible = True
        if reason == "MATCH":
            code = str(product["ITEM_SEQ"])
            if code in candidates and candidates[code] != product:
                raise RecognitionError("matching", "API_CONFLICTING_PRODUCT")
            candidates[code] = product
    return candidates, plausible, reasons, detail_checks, len(rows)


# 문자 가설을 제품으로 검증하고 모든 필수 제품 후보가 통과한 뒤 성분을 조회한다.
def identify(cells, api, min_score=0.9, diagnostics=None):
    if not math.isfinite(min_score) or not 0 < min_score <= 1:
        raise ValueError("OCR_SCORE_CONFIGURATION_INVALID")
    if diagnostics is not None:
        diagnostics.candidates(cells, min_score)
    if not cells:
        raise RecognitionError("extraction", "NO_PRODUCT_NAME")
    if len(cells) > 25:
        raise RecognitionError("extraction", "TOO_MANY_PRODUCTS")
    medicines, items, ingredient_cache, ignored, search_cache = {}, [], {}, [], {}
    consumed, deferred, owners = set(), [], []
    for index, cell in enumerate(cells):
        if index in consumed:
            continue
        required = cell.get("required", True)
        try:
            observations = candidate_observations(cells, index, min_score)
        except RecognitionError as exc:
            # 제목·제형·약품 행 근거 없는 저신뢰 문구는 등록 후보로 승격하지 않는다.
            if not required and exc.code == "LOW_OCR_CONFIDENCE":
                ignored.append({"candidate_index": index, "reason": "LOW_LEXICAL_CONFIDENCE"})
                continue
            raise
        candidates, witnesses, reasons = {}, {}, {}
        plausible, unresolved, detail_checks, row_count = False, False, 0, 0
        for observed in observations:
            matched, possible, counts, checks, returned = lookup_candidate(
                observed, api, ingredient_cache, search_cache, index, diagnostics
            )
            plausible = plausible or possible
            unresolved = unresolved or (possible and not matched)
            detail_checks += checks
            row_count += returned
            for reason, count in counts.items():
                reasons[reason] = reasons.get(reason, 0) + count
            for code, product in matched.items():
                if code in candidates and candidates[code] != product:
                    raise RecognitionError("matching", "API_CONFLICTING_PRODUCT")
                candidates[code] = product
                if code not in witnesses or observed["score"] > witnesses[code]["score"]:
                    witnesses[code] = observed
        if diagnostics is not None:
            diagnostics.matching(
                index,
                row_count,
                len(candidates),
                reasons,
                detail_checks,
                variant_count=len(observations),
            )
        if unresolved:
            raise RecognitionError("matching", "PRODUCT_NOT_FOUND")
        # 무결과인 주변 문구와 식약처에서 실제로 충돌하는 제품 후보를 구분한다.
        if not candidates and not required and not plausible:
            ignored.append({"candidate_index": index, "reason": "NO_CATALOG_MATCH"})
            continue
        if len(candidates) != 1:
            if not candidates and not plausible and cell.get("evidence") == "title":
                deferred.append((index, cell))
                continue
            raise RecognitionError(
                "matching", "AMBIGUOUS_PRODUCT" if candidates else "PRODUCT_NOT_FOUND"
            )
        code, product = next(iter(candidates.items()))
        observed = witnesses[code]
        # 결합한 조각이 독립된 다른 약품과도 일치하면 결합으로 숨기지 않는다.
        for used_index in observed["_indices"]:
            if used_index == index:
                continue
            for parts in reading_combinations(cells[used_index]["lines"]):
                independent = extract_candidate(
                    {**cells[used_index], "lines": list(parts)}, min_score
                )
                separate, possible, _, _, _ = lookup_candidate(
                    independent, api, ingredient_cache, search_cache, used_index, diagnostics
                )
                if any(other_code != code for other_code in separate):
                    raise RecognitionError("matching", "AMBIGUOUS_PRODUCT")
                # 별도 약품의 근거가 있지만 상세 검증이 끝나지 않았다면 결합으로 숨기지 않는다.
                if possible and not separate:
                    raise RecognitionError("matching", "PRODUCT_NOT_FOUND")
        consumed.update(observed["_indices"])
        owners.append(
            {
                "box": observed["_box"],
                "lines": [part for i in observed["_indices"] for part in cells[i]["lines"]],
            }
        )
        fields = official_fields(product, ingredient_cache.get(code, ()))
        if not fields["form"] or len(fields["strengths"]) > 1:
            raise RecognitionError("validation", "REQUIRED_PRODUCT_FIELDS_MISSING")
        form = fields["form"]
        medicines[code] = {
            "medicine_id": "MFDS#" + code,
            "product_code": code,
            "name": product["ITEM_NAME"],
            "form": "CAPSULE" if "캡슐" in form else "TABLET" if form.endswith("정") else "OTHER",
            "dosage_form": form,
            "strength": next(iter(fields["strengths"]), None),
            "manufacturer": product.get("ENTP_NAME") or None,
            "ingredients": [],
            "ingredients_status": "NOT_FETCHED",
            "ingredients_error_code": None,
        }
        quantity = next(iter(observed["quantities"]), None)
        items.append(
            {
                "cell_index": index,
                "box": observed["_box"],
                "recognition_status": "SUCCEEDED",
                "product_code": code,
                "name_score": observed["score"],
                # 포장에 인쇄된 수량은 재고·실제 잔여량이 아니므로 이벤트에만 보관한다.
                "package_quantity": {"value": quantity[0], "unit": quantity[1]}
                if quantity
                else None,
                "package_size": observed["package_size"],
            }
        )
    for index, cell in deferred:
        if index in consumed:
            continue
        if sum(nearby_context(cell, owner) for owner in owners) != 1:
            raise RecognitionError("matching", "PRODUCT_NOT_FOUND")
        ignored.append({"candidate_index": index, "reason": "VERIFIED_PRODUCT_CONTEXT"})
    if diagnostics is not None:
        diagnostics.selection([item["cell_index"] for item in items], ignored)
    if not items:
        raise RecognitionError("matching", "PRODUCT_NOT_FOUND")
    for index, (code, medicine) in enumerate(medicines.items()):
        try:
            if code not in ingredient_cache:
                ingredient_cache[code] = load_ingredients(api, code)
            ingredients = ingredient_cache[code]
            medicine["ingredients"] = [
                {key: row.get(key) for key in ("name", "amount", "unit", "basis", "source")}
                for row in ingredients
            ]
            gaps = ingredient_gaps(ingredients)
            medicine["ingredients_missing_fields"] = gaps
            medicine["ingredients_status"] = (
                "PARTIAL" if gaps else "SUCCEEDED" if ingredients else "NO_DATA"
            )
            medicine["ingredients_error_code"] = "API_INGREDIENT_FIELDS_MISSING" if gaps else None
        except Exception as exc:
            medicine["ingredients_status"] = "UNAVAILABLE"
            medicine["ingredients_missing_fields"] = []
            medicine["ingredients_error_code"] = (
                exc.code if isinstance(exc, ApiError) else "INGREDIENT_SYSTEM_ERROR"
            )
        # 제품 식별과 공식 정보의 완전성을 분리하여 외부 조회 장애가 OCR 실패가 되지 않게 한다.
        try:
            info = api.product_info(code)
            missing = validate_product_info(info)
            medicine["product_info"] = info
            medicine["product_info_missing_fields"] = missing
            medicine["product_info_status"] = (
                "NO_DATA" if len(missing) == 5 else "PARTIAL" if missing else "SUCCEEDED"
            )
            medicine["product_info_error_code"] = (
                "API_PRODUCT_INFO_FIELDS_MISSING" if missing else None
            )
        except Exception as exc:
            medicine["product_info"] = {}
            medicine["product_info_missing_fields"] = []
            medicine["product_info_status"] = "UNAVAILABLE"
            medicine["product_info_error_code"] = (
                exc.code if isinstance(exc, ApiError) else "PRODUCT_INFO_SYSTEM_ERROR"
            )
        medicine["enrichment_status"] = (
            "SUCCEEDED"
            if medicine["ingredients_status"] == medicine["product_info_status"] == "SUCCEEDED"
            else "INCOMPLETE"
        )
        if diagnostics is not None:
            observations = (
                api.ingredient_observations.get(code, {}) if isinstance(api, MfdsClient) else {}
            )
            diagnostics.enrichment(index, medicine, observations)
    return {
        "processing_status": "COMPLETED",
        "recognition_status": "SUCCEEDED",
        "ocr_status": "SUCCEEDED",
        "extraction_status": "SUCCEEDED",
        "matching_status": "SUCCEEDED",
        "prescription_status": "NOT_EVALUATED",
        "items": items,
        "medicines": list(medicines.values()),
        "retryable": False,
    }
