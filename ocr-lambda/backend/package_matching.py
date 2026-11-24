"""읽힌 고신뢰 포장 문구만 개별·결합 검색하고 관측 속성으로 품목을 선택한다."""

import math
import re
from itertools import product

from .image_ocr import adjacent, box_overlap, group_lines, name_height, vertical_english
from .mfds import ApiError
from .recognition import (
    BRAND_ALIASES,
    NAME_MODIFIERS,
    NOISE,
    QUANTITY,
    STRENGTH,
    RecognitionError,
    extract_candidate,
    fragment_kind,
    load_ingredients,
    name_fields,
    normalize_name,
    official_fields,
    reading_combinations,
    tight_name_pair,
)

LETTER_NAMES = dict(
    zip(
        "abcdefghijklmnopqrstuvwxyz",
        (
            "에이",
            "비",
            "씨",
            "디",
            "이",
            "에프",
            "지",
            "에이치",
            "아이",
            "제이",
            "케이",
            "엘",
            "엠",
            "엔",
            "오",
            "피",
            "큐",
            "알",
            "에스",
            "티",
            "유",
            "브이",
            "더블유",
            "엑스",
            "와이",
            "제트",
        ),
    )
)
ENGLISH_LABELS = {
    "capsule": "캡슐",
    "capsules": "캡슐",
    "softcapsule": "연질캡슐",
    "softcapsules": "연질캡슐",
    "tablet": "정",
    "tablets": "정",
    "syrup": "시럽",
    "suspension": "현탁액",
    "ointment": "연고",
    "cream": "크림",
    "gel": "겔",
    "plus": "플러스",
    "super": "슈퍼",
}


# 실제 읽힌 영문만 검증된 상표 또는 한 글자 발음으로 바꾸며 모르는 단어는 만들지 않는다.
def korean_query(text):
    text = normalize_name(text)
    if NOISE.search(text):
        return None
    if re.fullmatch(r"[a-z]+", text):
        text = ENGLISH_LABELS.get(text, BRAND_ALIASES.get(text, text))
    for word in set(re.findall(r"[a-z]+", text)):
        translated = (
            word
            if word in {"mg", "ug", "g", "ml"}
            else LETTER_NAMES.get(word)
            if len(word) == 1
            else ENGLISH_LABELS.get(word, BRAND_ALIASES.get(word))
        )
        if translated is None:
            return None
        text = re.sub(r"(?<![a-z])" + word + r"(?![a-z])", translated, text)
    return text


# 실제 글자 기준선의 이웃 또는 가까운 다음 줄만 연결하며 한 글자 표지는 방향을 강제하지 않는다.
def next_fragment(left, right):
    if tight_name_pair(left, right):
        return True
    x1, y1, x2, y2 = left["box"]
    a1, b1, a2, b2 = right["box"]
    h, k = y2 - y1, b2 - b1
    if min(h, k) <= 0 or max(h, k) > min(h, k) * 5:
        return False
    la = left["lines"][-1].get("angle", 0)
    ra = right["lines"][0].get("angle", 0)
    label = any(p.get("_letter") for p in right["lines"])
    if not label and abs((la - ra + 90) % 180 - 90) > 25:
        return False
    # 제목 외접 상자에 일부 겹친 작은 큐·에스 표지도 오른쪽 읽기 순서는 유지한다.
    horizontal = (
        a1 + a2 > x1 + x2
        and a2 > x2
        and -min(x2 - x1, a2 - a1) * 0.8 <= a1 - x2 <= max(h, k) * 0.8
        and min(y2, b2) - max(y1, b1) >= min(h, k) * 0.35
    )
    stacked = (
        -min(h, k) * 0.3 <= b1 - y2 <= max(h, k) * 0.6
        and b1 > y1
        and min(x2, a2) - max(x1, a1) >= min(x2 - x1, a2 - a1) * 0.65
    )
    return horizontal or stacked


class PackageMatcher:
    # 검색·성분 캐시와 제한은 한 포장 이벤트의 여러 배율에서 공유한다.
    def __init__(self, api, min_score, diagnostics):
        if not math.isfinite(min_score) or not 0 < min_score <= 1:
            raise ValueError("OCR_SCORE_CONFIGURATION_INVALID")
        self.api, self.min_score, self.diagnostics = api, max(0.9, min_score), diagnostics
        self.search_cache = {}
        self.ingredient_cache = {}
        self.last_cells = []

    # 점수 미달 문구와 미지원 영문을 후보에서 제외하되 실제 판독 대안은 보존한다.
    def _clean(self, lines):
        cleaned = []
        for line in lines:
            values = []
            for value in [line] + line.get("alternatives", []):
                if not self.min_score <= value["score"] <= 1:
                    continue
                if vertical_english(value):
                    continue
                text = korean_query(value["text"])
                if not text:
                    continue
                values.append(
                    {
                        **value,
                        "text": text,
                        "_letter": bool(re.fullmatch(r"[a-zA-Z]", value["text"])),
                    }
                )
            if values:
                first = {k: v for k, v in values[0].items() if k != "alternatives"}
                if len(values) > 1:
                    first["alternatives"] = [
                        {k: v for k, v in p.items() if k != "alternatives"} for p in values[1:]
                    ]
                cleaned.append(first)
        return cleaned

    # 읽힌 이름 부분만 검색어로 만들고 별도 제형·함량은 선택 근거로 남긴다.
    def _phrase(self, cells, indices, parts):
        words = [p for p in parts if fragment_kind(p["text"]) == "name"]
        text = "".join(p["text"] for p in words)
        if not text or NOISE.search(text) or not re.fullmatch(r"[가-힣a-z0-9-]+", text):
            return None
        boxes = [cells[i]["box"] for i in indices]
        cell = {
            "box": [
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            ],
            "lines": list(parts),
            "metadata": [p for i in indices for p in cells[i].get("metadata", [])],
        }
        return {
            "text": text,
            "cell": cell,
            "indices": indices,
            "required": any(cells[i].get("required", True) for i in indices),
            "label": len(indices) == 1
            and (text in NAME_MODIFIERS or len(text) < 2 or all(p.get("_letter") for p in words)),
        }

    # API 실패와 불완전한 행을 숨기지 않고 같은 읽힌 검색어는 한 번만 조회한다.
    def _search(self, text):
        if text not in self.search_cache:
            if len(self.search_cache) >= 32:
                raise RecognitionError("matching", "TOO_MANY_SEARCH_QUERIES")
            try:
                self.search_cache[text] = self.api.search(text)
            except ApiError as error:
                if error.code == "API_RESULT_LIMIT":
                    self.search_cache[text] = error
                raise RecognitionError("matching", error.code, error.retryable) from None
        rows = self.search_cache[text]
        if isinstance(rows, ApiError):
            raise RecognitionError("matching", rows.code, rows.retryable)
        unique = {}
        for row in rows:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("ITEM_NAME"), str)
                or not row["ITEM_NAME"].strip()
                or not re.fullmatch(r"\d{9}", str(row.get("ITEM_SEQ", "")))
            ):
                raise RecognitionError("matching", "API_PRODUCT_SCHEMA_INVALID")
            code = str(row["ITEM_SEQ"])
            if code in unique and unique[code] != row:
                raise RecognitionError("matching", "API_CONFLICTING_PRODUCT")
            unique[code] = row
        return list(unique.values())

    # 공식 성분으로 증명된 괄호만 해석하고 관측하지 않은 제형은 선택 근거로 만들지 않는다.
    def _fields(self, row):
        fields = official_fields(row)
        if fields is None:
            code = str(row["ITEM_SEQ"])
            if code not in self.ingredient_cache:
                try:
                    self.ingredient_cache[code] = load_ingredients(self.api, code)
                except ApiError as error:
                    raise RecognitionError("matching", error.code, error.retryable) from None
            fields = official_fields(row, self.ingredient_cache[code])
        return fields

    # 같은 포장 문구에 붙은 고신뢰 제형·함량만 추출하며 상표의 겔을 임의로 떼지 않는다.
    def _observed(self, phrase, lines, rows):
        observed = extract_candidate(phrase["cell"], self.min_score)
        raw = STRENGTH.sub("", QUANTITY.sub("", normalize_name(phrase["text"])))
        stems = {name_fields(re.sub(r"\([^()]*\)", "", r["ITEM_NAME"]))["name"] for r in rows}
        forms = set()
        if observed["form"] and raw not in stems:
            forms.add(observed["form"])
        for part in lines:
            kind = fragment_kind(part["text"])
            if kind not in {"form", "strength"}:
                continue
            same = box_overlap(phrase["cell"], part)
            if (
                same[0] / same[2] >= 0.8
                or adjacent(phrase["cell"], part)
                or adjacent(phrase["cell"], part, below=True)
            ):
                if kind == "form":
                    forms.add(normalize_name(part["text"]))
                else:
                    observed["strengths"].update(name_fields(part["text"])["strengths"])
        if len(forms) > 1 or len(observed["strengths"]) > 1:
            raise RecognitionError("validation", "CONFLICTING_ATTRIBUTES")
        observed["form"] = next(iter(forms), None)
        return observed

    # 검색 결과를 실제 읽힌 속성으로만 좁히고 이름 줄기 비교로 여러 품목을 하나로 만들지 않는다.
    def _lookup(self, phrase, lines, index):
        rows = self._search(phrase["text"])
        observed = self._observed(phrase, lines, rows)
        selected = []
        reasons = {}
        # 제형·함량을 읽지 않았다면 다건 결과는 그대로 다건이다.
        if (
            len(rows) > 1
            and not observed["form"]
            and not observed["strengths"]
            and not observed["manufacturer"]
        ):
            self.diagnostics.matching(index, len(rows), 0, {}, variant_count=1)
            return [], True
        for row in rows:
            fields = self._fields(row)
            reason = (
                "ANNOTATION_UNVERIFIED"
                if fields is None
                else "FORM_MISMATCH"
                if observed["form"] and observed["form"] != fields["form"]
                else "STRENGTH_MISMATCH"
                if observed["strengths"] and observed["strengths"] != fields["strengths"]
                else "MANUFACTURER_MISMATCH"
                if observed["manufacturer"]
                and normalize_name(observed["manufacturer"])
                != normalize_name(row.get("ENTP_NAME", ""))
                else "MATCH"
            )
            if reason == "MATCH":
                selected.append((row, fields))
            reasons[reason] = reasons.get(reason, 0) + 1
        self.diagnostics.matching(index, len(rows), len(selected), reasons, variant_count=1)
        # 전혀 다른 응답 한 건을 허가 품목이라고 확정하지 않는다.
        related = []
        for row, fields in selected:
            query = STRENGTH.sub("", QUANTITY.sub("", phrase["text"]))
            if query in normalize_name(row["ITEM_NAME"]):
                related.append((row, fields))
        return (related if len(selected) == 1 else []), len(selected) > 1

    # 개별 또는 결합 단계의 모든 독립 품목을 확인하여 먼저 나온 한 개로 숨기지 않는다.
    def _stage(self, phrases, lines):
        accepted = {}
        ambiguous = False
        limits = []
        explicit = []
        for index, phrase in enumerate(phrases):
            if phrase is None or phrase["label"]:
                continue
            try:
                matches, multiple = self._lookup(phrase, lines, index)
            except RecognitionError as error:
                if error.code != "API_RESULT_LIMIT":
                    raise
                limits.append((phrase["text"], error))
                continue
            ambiguous = ambiguous or multiple
            # 독립된 위치의 서로 다른 전체 제품명을 읽은 경우에만 복수 포장을 거절한다.
            exact = {
                str(row["ITEM_SEQ"])
                for row in self.search_cache.get(phrase["text"], [])
                if not isinstance(row, ApiError)
                and normalize_name(row["ITEM_NAME"]).split("(")[0] == phrase["text"]
            }
            if name_fields(phrase["text"])["form"] and len(exact) == 1:
                for prior, code in explicit:
                    if code != next(iter(exact)) and set(prior["indices"]).isdisjoint(
                        phrase["indices"]
                    ):
                        raise RecognitionError("matching", "AMBIGUOUS_PRODUCT")
                explicit.append((phrase, next(iter(exact))))
            for row, fields in matches:
                accepted[str(row["ITEM_SEQ"])] = (row, fields)
        if len(accepted) > 1:
            raise RecognitionError("matching", "AMBIGUOUS_PRODUCT")
        if accepted:
            row, fields = next(iter(accepted.values()))
            for phrase in phrases:
                if not phrase or not phrase["required"] or phrase["label"]:
                    continue
                rows = self.search_cache.get(phrase["text"])
                if (
                    isinstance(rows, list)
                    and rows
                    and str(row["ITEM_SEQ"]) not in {str(p["ITEM_SEQ"]) for p in rows}
                ):
                    error = RecognitionError("matching", "AMBIGUOUS_PRODUCT")
                    error.can_recheck = True
                    raise error
            for text, error in limits:
                if text not in normalize_name(row["ITEM_NAME"]):
                    raise error
            return (row, fields), ambiguous
        if limits:
            raise limits[0][1]
        return None, ambiguous

    # 새 판독에서 읽힌 순서대로 개별 문구를 조회하고 실패할 때만 최대 세 조각을 결합한다.
    def match(self, lines):
        lines = self._clean(lines)
        cells = group_lines(lines)
        self.diagnostics.candidates(cells, self.min_score)
        if not cells:
            raise RecognitionError("extraction", "NO_PRODUCT_NAME")
        if len(cells) > 25:
            raise RecognitionError("extraction", "TOO_MANY_PRODUCTS")
        cells.sort(key=name_height, reverse=True)
        self.last_cells = cells
        singles = []
        for i, cell in enumerate(cells):
            singles.extend(
                self._phrase(cells, [i], parts) for parts in reading_combinations(cell["lines"])
            )
        found, ambiguous = self._stage(singles, lines)
        labels = [p for p in singles if p and p["label"]]
        if found and any(
            next_fragment(p["cell"], q["cell"])
            and q["text"] not in normalize_name(found[0]["ITEM_NAME"])
            for p in singles
            if p and not p["label"]
            for q in labels
        ):
            found = None
        if not found:
            paths = [[i] for i in range(len(cells))]
            for _ in range(2):
                paths = [
                    path + [j]
                    for path in paths
                    for j in range(len(cells))
                    if j not in path and next_fragment(cells[path[-1]], cells[j])
                ]
                combined = []
                for path in paths:
                    for options in product(
                        *(reading_combinations(cells[i]["lines"]) for i in path)
                    ):
                        phrase = self._phrase(cells, path, [p for parts in options for p in parts])
                        if phrase:
                            combined.append(phrase)
                        if len(combined) > 64:
                            raise RecognitionError("extraction", "TOO_MANY_OCR_ALTERNATIVES")
                candidate, multiple = self._stage(combined, lines)
                ambiguous = ambiguous or multiple
                if candidate:
                    found = candidate
                    break
        if not found:
            error = RecognitionError(
                "matching", "AMBIGUOUS_PRODUCT" if ambiguous else "PRODUCT_NOT_FOUND"
            )
            error.can_recheck = True
            raise error
        row, fields = found
        if not fields["form"] or len(fields["strengths"]) > 1:
            raise RecognitionError("validation", "REQUIRED_PRODUCT_FIELDS_MISSING")
        form = fields["form"]
        code = str(row["ITEM_SEQ"])
        return {
            "medicine_id": "MFDS#" + code,
            "product_code": code,
            "name": row["ITEM_NAME"],
            "form": "CAPSULE" if "캡슐" in form else "TABLET" if form.endswith("정") else "OTHER",
            "dosage_form": form,
            "strength": next(iter(fields["strengths"]), None),
            "manufacturer": row.get("ENTP_NAME") or None,
            "ingredients": [],
            "ingredients_status": "NOT_FETCHED",
            "ingredients_error_code": None,
        }

    # 이미 관측한 가장 큰 제목과 이웃 접미 글자를 포함하는 마지막 재검출 영역을 반환한다.
    def retry_box(self, shape):
        if not self.last_cells:
            return None
        height, width = shape[:2]
        x1, y1, x2, y2 = self.last_cells[0]["box"]
        pad = max(12, round(max(x2 - x1, y2 - y1) * 0.35))
        return [max(0, x1 - pad), max(0, y1 - pad), min(width, x2 + pad), min(height, y2 + pad)]
