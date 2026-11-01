"""식약처 공식 API 응답을 품목코드로 검증하고 성분·허가정보를 정규화한다."""

import json
import random
import re
import socket
import ssl
import time
import unicodedata
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen
from xml.etree import ElementTree


class ApiError(Exception):
    # 민감한 응답과 인증키 없이 고정 오류 코드만 전달한다.
    def __init__(self, code, retryable=False):
        super().__init__(code)
        self.code, self.retryable = code, retryable


INFO_FIELDS = ("indications", "dosage", "precautions", "storage_method", "shelf_life")


class DocumentText(HTMLParser):
    # CDATA 안의 표시용 HTML을 실행하지 않고 문단별 평문으로 읽는다.
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    # 위험한 태그는 버려서 정상 문서처럼 만들지 않고 응답 오류로 분리한다.
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "iframe", "object", "embed", "img"}:
            raise ApiError("API_DOCUMENT_INVALID")
        if tag in {"p", "div", "br", "tr", "li", "table"}:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append("\t")

    # 문단의 끝을 남겨 표·목록의 값이 붙지 않게 한다.
    def handle_endtag(self, tag):
        if tag in {"p", "div", "tr", "li", "table"}:
            self.parts.append("\n")

    # 허가 문구의 숫자·부호·텍스트를 요약하지 않고 유지한다.
    def handle_data(self, data):
        self.parts.append(data)


# XML의 제목·본문·꼬리 문구를 원래 순서대로 읽고 과도한 중첩은 거부한다.
def xml_document_parts(node, depth=0):
    if depth > 40:
        raise ApiError("API_DOCUMENT_INVALID")
    if node.attrib.get("title"):
        yield node.attrib["title"]
    if node.text:
        yield node.text
    for child in node:
        yield from xml_document_parts(child, depth + 1)
        if child.tail:
            yield child.tail


# 숫자와 단위를 추측하지 않고 양의 유한 함량만 사용한다.
def amount_text(value):
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", text):
        return None
    try:
        return text.replace(",", "") if Decimal(text.replace(",", "")) > 0 else None
    except InvalidOperation:
        return None


# 제품별 단위 문맥 없는 총중량을 성분 함량의 기준으로 사용하지 않는다.
def valid_basis(value):
    return isinstance(value, str) and bool(
        re.search(r"\d\s*(?:정|캡슐|포|밀리리터|밀리그램|그램|m[lL]|[mµμu]?g|g)", value)
    )


# 성분별 빠진 함량·단위·기준을 원문 없는 필드 경로로 반환한다.
def ingredient_gaps(rows):
    gaps = []
    for index, row in enumerate(rows):
        for field, valid in (
            ("amount", amount_text(row.get("amount")) is not None),
            ("unit", valid_unit(row.get("unit"))),
            ("basis", valid_basis(row.get("basis"))),
        ):
            if not valid:
                gaps.append(f"ingredients[{index}].{field}")
    return gaps


# 코드값이나 빈 문자열을 표시 가능한 함량 단위로 오판하지 않는다.
def valid_unit(value):
    return isinstance(value, str) and value.strip().lower() in {
        "밀리그램",
        "마이크로그램",
        "그램",
        "밀리리터",
        "리터",
        "단위",
        "국제단위",
        "mg",
        "g",
        "ug",
        "µg",
        "μg",
        "ng",
        "ml",
        "l",
        "iu",
        "i.u.",
        "u",
        "%",
        "meq",
        "mmol",
    }


# 같은 단위의 한글·기호 표기만 대응하고 서로 다른 배율을 자동 변환하지 않는다.
def unit_key(value):
    key = str(value).strip().lower()
    return {
        "밀리그램": "mg",
        "그램": "g",
        "마이크로그램": "ug",
        "µg": "ug",
        "μg": "ug",
        "밀리리터": "ml",
        "리터": "l",
        "국제단위": "iu",
        "i.u.": "iu",
    }.get(key, key)


# 허가 문서의 구조와 제목을 보존해 평문으로 변환하며 불완전한 문서는 잘라 저장하지 않는다.
def document_text(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value.encode("utf-8")) > 180_000:
        raise ApiError("API_DOCUMENT_INVALID")
    value = value.strip()
    if re.search(r"<!DOCTYPE|<!ENTITY|<script\b|<iframe\b", value, re.I):
        raise ApiError("API_DOCUMENT_INVALID")
    if value.startswith("&lt;"):
        value = unescape(value)
    if re.search(r"<!DOCTYPE|<!ENTITY", value, re.I):
        raise ApiError("API_DOCUMENT_INVALID")
    if value.startswith("<"):
        try:
            root = ElementTree.fromstring(value)
        except ElementTree.ParseError:
            raise ApiError("API_DOCUMENT_INVALID") from None
        value = "\n".join(xml_document_parts(root))
    if re.search(r"<[/!]?[A-Za-z]", value):
        parser = DocumentText()
        parser.feed(value)
        parser.close()
        value = "".join(parser.parts)
    return value.strip() or None


# 표시 이름이 정확히 대응하는 명시적 성분 행만 보완 자료로 해석한다.
def material_rows(value):
    if not isinstance(value, str) or len(value) > 150_000:
        return []
    groups = re.split(r"총량\s*[:：]", value)
    rows = []
    for group in groups[1:]:
        chunks = re.split(r"성분명\s*[:：]", group)
        basis = chunks[0].strip(" ;|\r\n")
        if not valid_basis(basis):
            continue
        for chunk in chunks[1:]:
            # 명시적 구분자를 요구하여 다른 성분·첨가제의 숫자를 가져오지 않는다.
            pairs = re.findall(r"(?:^|[|;])\s*([^|;:：]+)\s*[:：]\s*([^|;]*)", chunk)
            fields = {key.strip(): val.strip() for key, val in pairs}
            name = re.split(r"[|;]", chunk, maxsplit=1)[0].strip()
            amount, unit = amount_text(fields.get("분량")), fields.get("단위")
            if name and amount and unit:
                rows.append({"name": name, "amount": amount, "unit": unit, "basis": basis})
    return rows


# 성분 비교는 공백만 정규화하며 염·수화물·대소문자 이외의 명칭 차이를 추정하지 않는다.
def ingredient_key(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).lower()


# API 정보 객체와 누락 필드의 일관성을 저장 전에 확인한다.
def validate_product_info(info):
    if not isinstance(info, dict) or any(
        info.get(field) is not None and not isinstance(info[field], str) for field in INFO_FIELDS
    ):
        raise ApiError("API_PRODUCT_INFO_SCHEMA_INVALID")
    return [field for field in INFO_FIELDS if not (info.get(field) or "").strip()]


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
        max_attempts=3,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        if not service_key or not re.fullmatch(r"\d{2}", version):
            raise ApiError("API_CONFIGURATION_ERROR")
        self.service_key = service_key
        self.transport = transport or self._download
        self.page_size = page_size
        self.max_pages = max_pages
        self.version = version
        self.timeout = timeout
        if not 1 <= max_attempts <= 3 or not 1 <= timeout <= 30:
            raise ApiError("API_CONFIGURATION_ERROR")
        self.max_attempts, self.sleep, self.clock = max_attempts, sleep, clock
        self.remaining_ms, self.diagnostics = None, None
        self.request_count = 0
        self.page_cache = {}
        self.detail_cache = {}
        self.ingredient_observations = {}

    # 이벤트별 시간 예산·안전한 진단을 연결하고 요청 횟수를 초기화한다.
    def begin_event(self, context=None, diagnostics=None):
        self.remaining_ms = getattr(context, "get_remaining_time_in_millis", None)
        self.diagnostics = diagnostics
        self.request_count = 0

    # DB 저장 시간을 남겨 외부 API 때문에 Lambda가 강제 종료되지 않게 한다.
    def request_timeout(self):
        available = self.remaining_ms() / 1000 - 15 if self.remaining_ms else self.timeout
        if available < 1:
            raise ApiError("API_TIME_BUDGET_EXHAUSTED", True)
        return min(self.timeout, available)

    # 제한 시간·응답 크기를 지켜 HTTPS로 공식 API만 읽는다.
    def _download(self, url):
        try:
            with urlopen(url, timeout=self.request_timeout()) as response:
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
        except TimeoutError:
            raise ApiError("API_TIMEOUT", True) from None
        except ssl.SSLError:
            raise ApiError("API_TLS_ERROR") from None
        except URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                code, retryable = "API_TIMEOUT", True
            elif isinstance(reason, socket.gaierror):
                code, retryable = "API_DNS_ERROR", True
            elif isinstance(reason, ssl.SSLError):
                code, retryable = "API_TLS_ERROR", False
            else:
                code, retryable = "API_CONNECTION_ERROR", True
            raise ApiError(code, retryable) from None
        except (ConnectionError, OSError):
            raise ApiError("API_CONNECTION_ERROR", True) from None

    # 같은 응답을 호출 안에서 재사용하고 일시 장애만 제한적으로 재시도한다.
    def _page(self, operation, params, page):
        cache_key = (operation, tuple(sorted(params.items())), page)
        if cache_key in self.page_cache:
            return self.page_cache[cache_key]
        for attempt in range(1, self.max_attempts + 1):
            self.request_timeout()
            if self.request_count >= 64:
                raise ApiError("API_REQUEST_LIMIT")
            self.request_count += 1
            started = self.clock()
            code = "SUCCEEDED"
            try:
                result = self._page_once(operation, params, page)
                # 큰 허가문서의 중복 캐시로 기존 Lambda 메모리를 압박하지 않는다.
                if len(self.page_cache) < 16 and len(json.dumps(result).encode()) <= 65_536:
                    self.page_cache[cache_key] = result
                return result
            except ApiError as exc:
                code = exc.code
                if not exc.retryable or attempt == self.max_attempts:
                    raise
                delay = min(2, 0.4 * 2 ** (attempt - 1)) + random.uniform(0, 0.2)
                if self.remaining_ms and self.remaining_ms() / 1000 < 16 + delay:
                    raise ApiError("API_TIME_BUDGET_EXHAUSTED", True) from None
                self.sleep(delay)
            finally:
                if self.diagnostics is not None:
                    self.diagnostics.api_request(operation, attempt, self.clock() - started, code)

    # JSON/XML 게이트웨이 오류와 공식 items.item/배열 응답을 검증한다.
    def _page_once(self, operation, params, page):
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
                code in {"01", "04", "05", "22", "23"},
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
        stem = re.split(
            r"\d+(?:\.\d+)?\s*(?:mg|ug|g|ml|%)",
            re.sub(r"\s+", "", unicodedata.normalize("NFKC", name).lower()),
            maxsplit=1,
        )[0]
        return self._items("getDrugPrdtPrmsnInq", {"item_name": stem or name})

    # 동일 이벤트에서 상세 응답을 재사용하되 다른 품목의 자료는 허용하지 않는다.
    def details(self, code):
        if not re.fullmatch(r"\d{9}", code):
            raise ApiError("API_PRODUCT_CODE_MISMATCH")
        if code not in self.detail_cache:
            details = self._items("getDrugPrdtPrmsnDtlInq", {"item_seq": code})
            if len(details) != 1 or str(details[0].get("ITEM_SEQ", "")) != code:
                raise ApiError("API_PRODUCT_CODE_MISMATCH")
            self.detail_cache[code] = details[0]
        return self.detail_cache[code]

    # 공식 품목코드를 재검증하고 같은 이름의 상세 원료정보로 누락 함량을 보완한다.
    def ingredients(self, code):
        detail = self.details(code)
        fallback = material_rows(detail.get("MATERIAL_NAME"))
        # 주성분 상세의 품목 필터는 공식 명세상 첫 글자가 대문자다.
        rows = self._items("getDrugPrdtMcpnDtlInq", {"Item_seq": code})
        observation = {
            "primary_row_count": len(rows),
            "primary_amount_missing": sum(amount_text(row.get("QNT")) is None for row in rows),
            "material_row_count": len(fallback),
            "component_text_present": sum(bool(row.get("CPNT_CTNT_CONT")) for row in rows),
            "amounts_recovered": 0,
        }
        self.ingredient_observations[code] = observation
        result = []
        for row in rows:
            if str(row.get("ITEM_SEQ", "")) != code or not row.get("MTRAL_NM"):
                raise ApiError("API_INGREDIENT_SCHEMA_MISMATCH")
            entry = {
                "name": str(row["MTRAL_NM"]),
                "amount": amount_text(row.get("QNT")),
                "unit": str(row["INGD_UNIT_CD"]).strip() if row.get("INGD_UNIT_CD") else None,
                "basis": detail.get("TOTAL_CONTENT")
                if valid_basis(detail.get("TOTAL_CONTENT"))
                else None,
                "source": "MFDS_MCPN",
            }
            matches = [
                v for v in fallback if ingredient_key(v["name"]) == ingredient_key(entry["name"])
            ]
            same_name_count = sum(
                ingredient_key(str(r.get("MTRAL_NM", ""))) == ingredient_key(entry["name"])
                for r in rows
            )
            if len(matches) > 1 or same_name_count > 1:
                # 여러 구성 기준의 성분에 품목 전체 총량을 공통 분모로 붙이지 않는다.
                entry["basis"] = None
            # 여러 총량·제형에 걸친 동명 성분은 한 행으로 합치지 않는다.
            if len(matches) == 1 and same_name_count == 1:
                other = matches[0]
                if entry["amount"] and Decimal(entry["amount"]) != Decimal(other["amount"]):
                    raise ApiError("API_INGREDIENT_CONFLICT")
                if entry["unit"] and unit_key(entry["unit"]) != unit_key(other["unit"]):
                    raise ApiError("API_INGREDIENT_CONFLICT")
                # 기준 문맥도 함께 가져와 총중량과 유효성분 함량을 구분한다.
                observation["amounts_recovered"] += int(entry["amount"] is None)
                entry.update(
                    amount=entry["amount"] or other["amount"],
                    unit=entry["unit"] or other["unit"],
                    basis=other["basis"],
                    source="MFDS_MCPN_AND_MATERIAL",
                )
            result.append(entry)
        return result

    # 품목 상세의 공식 허가 문구를 그대로 보존하고 사용기간을 실제 만료일로 계산하지 않는다.
    def product_info(self, code):
        detail = self.details(code)
        info = {}
        errors = {}
        for target, sources in {
            "indications": ("EE_DOC_DATA",),
            "dosage": ("UD_DOC_DATA",),
            "precautions": ("NB_DOC_DATA", "PN_DOC_DATA"),
            "storage_method": ("STORAGE_METHOD",),
            "shelf_life": ("VALID_TERM",),
        }.items():
            try:
                parts = [document_text(detail.get(field)) for field in sources]
                info[target] = "\n\n".join(dict.fromkeys(part for part in parts if part)) or None
            except ApiError as exc:
                info[target] = None
                errors[target] = exc.code
        info.update(
            source="MFDS_PERMIT_DETAIL",
            source_updated_at=detail.get("CHANGE_DATE") or None,
            field_errors=errors,
        )
        return info
