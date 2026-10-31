"""위치별 OCR 근거와 식약처 제품 정보를 대조하고 검증된 제품만 반환한다."""

import json
import math
import re
import unicodedata
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen
from xml.etree import ElementTree

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
    r"처음부터|뚝떨어",
    re.I,
)
# 종근당 공식 제품 페이지 CKD0000525에서 확인한 상표와 허가명 표기 대응이다.
BRAND_ALIASES = {"모드코s": "모드코에스"}
# 탁센 공식 설명서의 전체 제품명만 대응하며 다른 탁센 제품으로 확장하지 않는다.
BRAND_ALIASES["탁센400"] = "탁센400이부프로펜"


class ApiError(Exception):
    # 인증키·응답 원문 없이 호출 실패 사유와 재시도 가능 여부를 보존한다.
    def __init__(self, code, retryable=False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


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
    for part in cell.get("metadata", []):
        # 이름 판별과 무관한 부가정보의 낮은 점수는 이름 점수를 낮추지 않는다.
        if not math.isfinite(part["score"]) or not min_score <= part["score"] <= 1:
            continue
        if fragment_kind(part["text"]) == "quantity":
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
    if annotation:
        ingredients = product.get("ITEM_INGR_NAME") or product.get("MAIN_ITEM_INGR") or ""
        if normalize_name(annotation[2]) not in {
            normalize_name(part)
            for part in re.split(r"[;,|]", ingredients) + [row["name"] for row in details]
        }:
            return None
        name = annotation[1]
    return name_fields(name)


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


# 모든 후보의 필수 정보를 검증한 후 선택적인 성분 정보를 별도로 조회한다.
def identify(cells, api, min_score=0.9, diagnostics=None):
    if not math.isfinite(min_score) or not 0 < min_score <= 1:
        raise ValueError("OCR_SCORE_CONFIGURATION_INVALID")
    if diagnostics is not None:
        diagnostics.candidates(cells, min_score)
    if not cells:
        raise RecognitionError("extraction", "NO_PRODUCT_NAME")
    if len(cells) > 25:
        raise RecognitionError("extraction", "TOO_MANY_PRODUCTS")
    medicines, items, ingredient_cache = {}, [], {}
    for index, cell in enumerate(cells):
        observed = extract_candidate(cell, min_score)
        try:
            rows = api.search(canonical_brand(observed["name"]))
        except ApiError as exc:
            raise RecognitionError("matching", exc.code, exc.retryable) from None
        candidates, reasons, detail_checks = {}, {}, 0
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
                # 이름 본문·함량·제형이 먼저 일치할 때만 같은 품목의 공식 성분으로 괄호를 검증한다.
                bare = {**product, "ITEM_NAME": annotation[1]} if annotation else product
                if annotation and product_match_reason(observed, bare) == "MATCH":
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
                        error = (
                            exc.code if isinstance(exc, ApiError) else "ANNOTATION_LOOKUP_FAILED"
                        )
                        retryable = exc.retryable if isinstance(exc, ApiError) else True
                        raise RecognitionError("matching", error, retryable) from None
                    reason = product_match_reason(observed, product, ingredient_cache[code])
            reasons[reason] = reasons.get(reason, 0) + 1
            if reason == "MATCH":
                code = str(product["ITEM_SEQ"])
                if code in candidates and candidates[code] != product:
                    raise RecognitionError("matching", "API_CONFLICTING_PRODUCT")
                candidates[code] = product
        if diagnostics is not None:
            diagnostics.matching(index, len(rows), len(candidates), reasons, detail_checks)
        if len(candidates) != 1:
            raise RecognitionError(
                "matching", "AMBIGUOUS_PRODUCT" if candidates else "PRODUCT_NOT_FOUND"
            )
        code, product = next(iter(candidates.items()))
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
                "box": cell["box"],
                "recognition_status": "SUCCEEDED",
                "product_code": code,
                "name_score": observed["score"],
                # 포장에 인쇄된 수량은 재고·실제 잔여량이 아니므로 이벤트에만 보관한다.
                "package_quantity": {"value": quantity[0], "unit": quantity[1]}
                if quantity
                else None,
            }
        )
    for code, medicine in medicines.items():
        try:
            if code not in ingredient_cache:
                ingredient_cache[code] = load_ingredients(api, code)
            ingredients = ingredient_cache[code]
            medicine["ingredients"] = [
                {key: row.get(key) for key in ("name", "amount", "unit", "basis")}
                for row in ingredients
            ]
            medicine["ingredients_status"] = "SUCCEEDED" if ingredients else "NO_DATA"
        except Exception as exc:
            medicine["ingredients_status"] = "UNAVAILABLE"
            medicine["ingredients_error_code"] = (
                exc.code if isinstance(exc, ApiError) else "INGREDIENT_SYSTEM_ERROR"
            )
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


class MfdsClient:
    # 공식 포털의 08 오퍼레이션을 사용하며 인증키는 실행 환경에서만 받는다.
    def __init__(
        self,
        service_key,
        transport=None,
        page_size=100,
        max_pages=5,
        version="08",
        timeout=8,
    ):
        if not service_key or not re.fullmatch(r"\d{2}", version):
            raise ApiError("API_CONFIGURATION_ERROR")
        self.service_key = service_key
        self.transport = transport or self._download
        self.page_size = page_size
        self.max_pages = max_pages
        self.version = version
        self.timeout = timeout

    # 제한 시간·응답 크기를 지켜 HTTPS로 공식 API만 읽는다.
    def _download(self, url):
        try:
            with urlopen(url, timeout=self.timeout) as response:
                if urlparse(response.url).hostname != "apis.data.go.kr":
                    raise ApiError("API_UNEXPECTED_REDIRECT")
                data = response.read(4_000_001)
                if len(data) > 4_000_000:
                    raise ApiError("API_RESPONSE_TOO_LARGE")
                return data
        except HTTPError as exc:
            raise ApiError(
                "API_HTTP_" + str(exc.code), exc.code == 429 or exc.code >= 500
            ) from None
        except (URLError, TimeoutError):
            raise ApiError("API_TIMEOUT", True) from None

    # JSON/XML 게이트웨이 오류와 공식 items.item/배열 응답을 검증한다.
    def _page(self, operation, params, page):
        query = urlencode(
            {
                "serviceKey": self.service_key,
                "type": "json",
                "pageNo": page,
                "numOfRows": self.page_size,
                **params,
            }
        )
        raw = self.transport(
            f"https://apis.data.go.kr/1471000/DrugPrdtPrmsnInfoService{self.version}/{operation}{self.version}?{query}"
        )
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            # 인증 오류가 XML로 반환되더라도 원문이나 키를 예외에 포함하지 않는다.
            try:
                xml = ElementTree.fromstring(raw)
                code = xml.findtext(".//returnReasonCode") or xml.findtext(".//resultCode")
            except ElementTree.ParseError:
                code = None
            if not code or not re.fullmatch(r"\d{2,4}", code):
                code = None
            raise ApiError(
                "API_" + code if code else "API_INVALID_RESPONSE",
                code in {"05", "22", "23"},
            ) from None
        if not isinstance(data, dict):
            raise ApiError("API_INVALID_RESPONSE")
        data = data.get("response", data)
        if not isinstance(data, dict) or not isinstance(data.get("header"), dict):
            raise ApiError("API_INVALID_RESPONSE")
        code = str(data.get("header", {}).get("resultCode", ""))
        if code not in {"00", "0000"}:
            raise ApiError(
                "API_" + code if code.isdigit() else "API_INVALID_RESPONSE",
                code in {"01", "04", "05", "22", "23"},
            )
        body = data.get("body")
        if not isinstance(body, dict):
            raise ApiError("API_INVALID_RESPONSE")
        items = body.get("items", [])
        if isinstance(items, dict) and "item" in items:
            items = items["item"]
        if items in (None, ""):
            items = []
        if isinstance(items, dict):
            items = [items] if items else []
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ApiError("API_INVALID_RESPONSE")
        try:
            total = int(body["totalCount"]) if "totalCount" in body else None
            if total is not None and total < 0:
                raise ValueError
        except (TypeError, ValueError):
            raise ApiError("API_INVALID_RESPONSE") from None
        return items, total

    # 결과를 잘라서 유일한 후보라고 오판하지 않도록 페이지 한도 초과를 실패로 둔다.
    def _items(self, operation, params):
        rows = []
        for page in range(1, self.max_pages + 1):
            items, total = self._page(operation, params, page)
            rows.extend(items)
            if total is not None and len(rows) > total:
                raise ApiError("API_INCOMPLETE_RESPONSE")
            if (total is not None and len(rows) == total) or (
                total is None and len(items) < self.page_size
            ):
                return rows
            if not items:
                raise ApiError("API_INCOMPLETE_RESPONSE")
        raise ApiError("API_RESULT_LIMIT")

    # OCR 제품명으로 후보를 조회하고 최종 대조는 도메인 로직에 맡긴다.
    def search(self, name):
        # API의 단위 표기 차이로 검색이 누락되지 않게 줄기로 조회하고 전체 후보를 정밀 대조한다.
        stem = re.split(r"\d+(?:\.\d+)?\s*(?:mg|ug|g|ml|%)", normalize_name(name), maxsplit=1)[0]
        return self._items("getDrugPrdtPrmsnInq", {"item_name": stem or name})

    # 공식 품목코드를 재검증하고 주성분 상세만 성분 배열로 변환한다.
    def ingredients(self, code):
        details = self._items("getDrugPrdtPrmsnDtlInq", {"item_seq": code})
        if len(details) != 1 or str(details[0].get("ITEM_SEQ", "")) != code:
            raise ApiError("API_PRODUCT_CODE_MISMATCH")
        # 주성분 상세의 품목 필터는 공식 명세상 첫 글자가 대문자다.
        rows = self._items("getDrugPrdtMcpnDtlInq", {"Item_seq": code})
        result = []
        for row in rows:
            if str(row.get("ITEM_SEQ", "")) != code or not row.get("MTRAL_NM"):
                raise ApiError("API_INGREDIENT_SCHEMA_MISMATCH")
            result.append(
                {
                    "name": str(row["MTRAL_NM"]),
                    "amount": str(row["QNT"]) if row.get("QNT") not in (None, "") else None,
                    "unit": str(row["INGD_UNIT_CD"]) if row.get("INGD_UNIT_CD") else None,
                    "basis": details[0].get("TOTAL_CONTENT") or None,
                }
            )
        return result
