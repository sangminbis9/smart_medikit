"""문서 형식과 관계없이 전체 이미지에서 위치가 검증된 제품 후보를 만든다."""

import math
import os
import re
from operator import itemgetter

from backend.recognition import (
    candidate_name,
    fragment_kind,
    name_fields,
    normalize_name,
    reading_combinations,
)


class ImageError(Exception):
    # 이미지 오류를 원문 없는 사유 코드로 전달한다.
    def __init__(self, code):
        super().__init__(code)
        self.code = code


# 실제 포맷과 픽셀 수를 검증하고 이미지를 해석한다.
def read_image(data):
    import cv2
    import numpy as np

    if not data.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")):
        raise ImageError("INVALID_IMAGE")
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape[0] * image.shape[1] > 20_000_000:
        raise ImageError("INVALID_IMAGE")
    return image


# 높이·수평 거리·겹침을 함께 검사하여 가까운 조각만 연결한다.
def adjacent(left, right, *, below=False):
    x1, y1, x2, y2 = left["box"]
    a1, b1, a2, b2 = right["box"]
    height, other = max(1, y2 - y1), max(1, b2 - b1)
    if max(height, other) / min(height, other) > 4:
        return False
    if below:
        overlap = min(x2, a2) - max(x1, a1)
        return -other * 0.2 <= b1 - y2 <= height * 1.2 and overlap >= min(x2 - x1, a2 - a1) * 0.5
    return (
        # 기울어진 글자의 외접 사각형은 겹칠 수 있지만 중심 순서는 유지해야 한다.
        -min(x2 - x1, a2 - a1) * 0.35 <= a1 - x2 <= height * 1.2
        and (a1 + a2) > (x1 + x2)
        and min(y2, b2) - max(y1, b1) >= min(height, other) * 0.5
    )


# 검증된 상표 조각 또는 명시적 제형·함량만 결합 대상으로 삼는다.
def can_join(left, right):
    # 수량·함량의 숫자와 단위가 분리된 경우 먼저 한 부가정보로 묶는다.
    if re.fullmatch(r"\d+(?:\.\d+)?", normalize_name(left["text"])):
        return fragment_kind(left["text"] + right["text"]) in {"quantity", "strength"}
    left_texts = [
        "".join(part["text"] for part in parts)
        for parts in reading_combinations(left.get("parts", [left]))
    ]
    if not any(candidate_name(text) for text in left_texts):
        return False
    if fragment_kind(right["text"]) in {"form", "strength"}:
        return True
    # 자유로운 단어 연결 대신 사진에서 분리될 수 있는 제품명 구성만 허용한다.
    pairs = {
        "모드": {"코", "코s", "코에스"},
        "모드코": {"s", "에스"},
        "화이투벤": {"큐", "큐플러스"},
        "화이투벤큐": {"플러스", "코프", "노즈"},
        "화이투벤큐플러스": {"코프", "노즈"},
        "탁센": {"400"},
        "탁센400": {"이부프로펜", "이부프로펜연질캡슐"},
    }
    return any(
        normalize_name(right["text"]) in pairs.get(normalize_name(text), set())
        for text in left_texts
    )


# 결합한 구성 요소를 보존하여 이름과 부가정보 점수를 분리한다.
def combine(left, right):
    return {
        "text": left["text"].strip() + right["text"].strip(),
        "score": min(left["score"], right["score"]),
        "box": [
            min(left["box"][0], right["box"][0]),
            min(left["box"][1], right["box"][1]),
            max(left["box"][2], right["box"][2]),
            max(left["box"][3], right["box"][3]),
        ],
        "parts": left.get("parts", [left]) + right.get("parts", [right]),
    }


# 상표 구성 조각은 마지막 글자와의 기울어진 배치도 확인한다.
def fragment_adjacent(left, right, *, below=False):
    if adjacent(left, right, below=below):
        return True
    tail = left.get("parts", [left])[-1]
    if can_join(left, right) and adjacent(tail, right, below=below):
        return True
    if not below or fragment_kind(right["text"]) != "name" or not can_join(left, right):
        return False
    x1, y1, x2, y2 = tail["box"]
    a1, b1, a2, b2 = right["box"]
    height, other = y2 - y1, b2 - b1
    return (
        max(height, other) <= min(height, other) * 4
        and (b1 + b2) - (y1 + y2) >= min(height, other) * 0.5
        and -other * 0.55 <= b1 - y2 <= height * 1.2
        and min(x2, a2) - max(x1, a1) >= min(x2 - x1, a2 - a1) * 0.5
    )


# 같은 줄에서도 공간·의미 조건을 모두 만족한 유일한 연결만 수행한다.
def merge_lines(lines):
    rows = sorted((dict(line) for line in lines), key=itemgetter("box"))
    changed = True
    while changed:
        changed = False
        for index, right in enumerate(rows):
            owners = [
                i
                for i, left in enumerate(rows)
                if i != index and fragment_adjacent(left, right) and can_join(left, right)
            ]
            if len(owners) == 1:
                owner = owners[0]
                rows[owner] = combine(rows[owner], right)
                rows.pop(index)
                changed = True
                break
    return rows


# 함량 유무와 무관하게 인접한 제형·함량을 연결하고 포장 수량은 분리한다.
def join_fragments(lines):
    rows = merge_lines(lines)
    changed = True
    while changed:
        changed = False
        for index, right in enumerate(rows):
            owners = [
                i
                for i, left in enumerate(rows)
                if i != index
                and fragment_adjacent(left, right, below=True)
                and can_join(left, right)
            ]
            if len(owners) == 1:
                owner = owners[0]
                rows[owner] = combine(rows[owner], right)
                rows.pop(index)
                changed = True
                break
    return rows


# 약품 목록의 제품 제목과 인접 함량만 남기고 설명·개인정보는 검색에서 제외한다.
def product_row_titles(lines):
    titles = []
    for line in lines:
        text = line["text"].strip()
        if re.search(r"[.…]", text) and re.search(r"정|캡슐|캅셀|액|시럽", text):
            # 소수점 함량은 잘림 기호와 구분한다.
            if re.search(r"\.{2,}|…|\.$", text):
                raise ImageError("TRUNCATED_PRODUCT_NAME")
        text = re.sub(r"^\((?:한|비|급여|비급여)\)\s*", "", text)
        if name_fields(text)["form"] and candidate_name(text):
            titles.append({**line, "text": text})
    strengths = [
        line
        for line in lines
        if fragment_kind(line["text"]) == "strength"
        and any(adjacent(title, line) for title in titles)
    ]
    return titles + strengths


# 열 제목 또는 반복되는 약품명·분류 쌍을 근거로 제품 목록의 검색 범위를 좁힌다.
def select_product_rows(lines):
    headers = [
        line
        for line in lines
        if normalize_name(line["text"])
        in {"약품명/성분", "약품명및성분", "약명/성분", "약품명", "제품명"}
    ]
    selected = []
    for header in headers:
        x1, top, x2, bottom = header["box"]
        height = max(1, bottom - top)
        boundaries = [
            line["box"][0]
            for line in lines
            if line["box"][0] > x2
            and abs(line["box"][1] - top) < height
            and re.search(r"복약|투약|횟수|일수", line["text"])
        ]
        right = min(boundaries) if boundaries else x2 + height * 5
        selected.extend(
            line
            for line in lines
            if line["box"][1] > bottom
            and x1 - height <= line["box"][0] < right
            and line["box"][2] <= right + height
        )
    if headers:
        # 제목 아래에 제품이 없으면 개인정보 영역으로 검색 범위를 다시 넓히지 않는다.
        return product_row_titles(selected)
    pairs = []
    for label in lines:
        if not name_fields(label["text"])["form"] and not re.search(r"\.{2,}|…", label["text"]):
            continue
        if any(
            description["box"][0] >= label["box"][2]
            and abs(description["box"][1] - label["box"][1])
            <= max(3, label["box"][3] - label["box"][1])
            and re.match(r"^\[(?!비\]|급여)", description["text"].strip())
            for description in lines
        ):
            pairs.append(label)
    if len(pairs) >= 2:
        lefts = [line["box"][0] for line in pairs]
        heights = [line["box"][3] - line["box"][1] for line in pairs]
        if max(lefts) - min(lefts) <= max(heights) * 2:
            top = min(line["box"][1] for line in pairs)
            bottom = max(line["box"][1] for line in pairs)
            selected = [
                line
                for line in lines
                if min(lefts) - max(heights) <= line["box"][0] <= max(lefts) + max(heights)
                and top <= line["box"][1] <= bottom
            ]
            return product_row_titles(selected)
    return None


# 전체 OCR 영역에서 제품명 후보를 만들고 유일하게 가까운 부가정보만 연결한다.
def group_lines(lines):
    product_rows = select_product_rows(lines)
    rows = join_fragments(product_rows if product_rows is not None else lines)
    candidates = [
        {"box": line["box"], "lines": line.get("parts", [line]), "metadata": []}
        for line in rows
        if any(
            candidate_name("".join(part["text"] for part in parts))
            for parts in reading_combinations(line.get("parts", [line]))
        )
    ]
    # 문자처럼 보인다는 이유만으로 주변 문구까지 필수 약품으로 간주하지 않는다.
    tallest = max((name_height(cell) for cell in candidates), default=0)
    for cell in candidates:
        fields = name_fields("".join(part["text"] for part in cell["lines"]))
        evidence = (
            "row"
            if product_rows is not None
            else "form"
            if fields["form"] or fields["strengths"]
            else "title"
            if name_height(cell) >= tallest * 0.6
            else "lexical"
        )
        cell["evidence"] = evidence
        cell["required"] = evidence != "lexical"
    for line in rows:
        if fragment_kind(line["text"]) not in {"quantity", "manufacturer"}:
            continue
        owners = [
            candidate
            for candidate in candidates
            if adjacent(candidate, line)
            or adjacent(candidate, line, below=True)
            or adjacent(line, candidate, below=True)
        ]
        if len(owners) == 1:
            owners[0]["metadata"].append(line)
    if len(candidates) > 25:
        raise ImageError("TOO_MANY_PRODUCTS")
    return candidates


# 결합 상자 대신 이름을 읽은 글자 조각의 높이를 제품 제목 근거로 사용한다.
def name_height(cell):
    return max(
        (
            part["box"][3] - part["box"][1]
            for part in cell["lines"]
            if fragment_kind(part["text"]) == "name"
        ),
        default=0,
    )


# 두 영역의 교집합과 각 면적을 계산한다.
def box_overlap(left, right):
    x1, y1, x2, y2 = left["box"]
    a1, b1, a2, b2 = right["box"]
    intersection = max(0, min(x2, a2) - max(x1, a1)) * max(0, min(y2, b2) - max(y1, b1))
    return intersection, max(1, (x2 - x1) * (y2 - y1)), max(1, (a2 - a1) * (b2 - b1))


# 재검출 결과를 같은 위치의 판독 대안으로 통합하고 다른 제품 영역은 보존한다.
def reconcile_regions(original, refined):
    retained, updated = [], [dict(line) for line in refined]
    for old in original:
        overlaps, equivalents = [], []
        for index, new in enumerate(updated):
            intersection, old_area, new_area = box_overlap(old, new)
            if intersection / min(old_area, new_area) >= 0.7:
                overlaps.append((index, new))
            if intersection / max(old_area, new_area) >= 0.6:
                equivalents.append((intersection / (old_area + new_area - intersection), index))
        # 주변 작은 상자의 겹침 여부와 무관하게 같은 위치의 판독부터 하나로 통합한다.
        if equivalents:
            _, index = max(equivalents)
            updated[index] = choose_reading(old, updated[index])
            continue
        if not overlaps:
            retained.append(old)
            continue
        children = [new for _, new in overlaps]
        old_height = old["box"][3] - old["box"][1]
        # 큰 검출 상자 안에서 둘 이상의 겹치지 않는 작은 줄이 확인되면 줄 분리를 우선한다.
        split = (
            len(children) >= 2
            and all(new["box"][3] - new["box"][1] <= old_height * 0.8 for new in children)
            and all(box_overlap(old, new)[0] / box_overlap(old, new)[2] >= 0.8 for new in children)
            and all(
                box_overlap(a, b)[0] / min(box_overlap(a, b)[1:]) < 0.25
                for i, a in enumerate(children)
                for b in children[i + 1 :]
            )
        )
        if split:
            continue
        retained.append(old)
    return sorted(retained + updated, key=itemgetter("box"))


# 같은 물리 영역의 점수를 부풀리지 않고 확신 높은 판독 충돌을 보존한다.
def choose_reading(old, new):
    same = normalize_name(old["text"]) == normalize_name(new["text"])
    conflict = (
        not same
        and min(old["score"], new["score"]) >= 0.9
        and (
            candidate_name(old["text"])
            or candidate_name(new["text"])
            or fragment_kind(old["text"]) != "name"
            or fragment_kind(new["text"]) != "name"
        )
    )
    if conflict:
        a, b = name_fields(old["text"]), name_fields(new["text"])
        # 이름 철자 대안은 공식 품목으로 판정하되 함량·제형·수량 충돌은 숨기지 않는다.
        if (
            fragment_kind(old["text"]) != "name"
            or fragment_kind(new["text"]) != "name"
            or any(a[key] != b[key] for key in ("form", "strengths", "quantities"))
        ):
            raise ImageError("OCR_TEXT_CONFLICT")
    selected = new if new["score"] > old["score"] else old
    variants = {}
    readings = [old, new] if conflict else [selected]
    readings += old.get("alternatives", []) + new.get("alternatives", [])
    for line in readings:
        key = normalize_name(line["text"])
        if key not in variants or line["score"] > variants[key]["score"]:
            variants[key] = {field: line[field] for field in ("text", "score", "box")}
    if len(variants) > 3:
        raise ImageError("TOO_MANY_OCR_ALTERNATIVES")
    selected = normalize_name(max(variants.values(), key=itemgetter("score"))["text"])
    result = variants[selected]
    if len(variants) > 1:
        result = {
            **result,
            "alternatives": [value for key, value in variants.items() if key != selected],
        }
    return result


# 큰 상표 영역을 여백과 함께 최대 두 개 선택하고 동일 영역의 중복 재검출을 막는다.
def product_regions(lines, shape):
    height, width = shape[:2]
    proposals = []
    ranked = sorted(lines, key=region_area, reverse=True)
    for line in ranked:
        x1, y1, x2, y2 = line["box"]
        if y2 - y1 < max(24, height * 0.1) or x2 - x1 < 30:
            continue
        pad = max(8, int((y2 - y1) * 0.3))
        box = [max(0, x1 - pad), max(0, y1 - pad), min(width, x2 + pad), min(height, y2 + pad)]
        region = {"box": box}
        if any(
            box_overlap(region, other)[0] / min(box_overlap(region, other)[1:]) > 0.5
            for other in proposals
        ):
            continue
        proposals.append(region)
        if len(proposals) == 2:
            break
    return [region["box"] for region in proposals]


# 영역 면적으로 큰 검출 상자를 먼저 재검사한다.
def region_area(line):
    x1, y1, x2, y2 = line["box"]
    return (x2 - x1) * (y2 - y1)


class PaddleReader:
    # 모델은 컨테이너에 미리 포함하고 워밍 실행 동안 한 번만 초기화한다.
    def __init__(self):
        self.engine = None

    # 공식 한국어 PP-OCRv5 모델의 로컬 경로만 사용한다.
    def _engine(self):
        if self.engine is None:
            from paddleocr import PaddleOCR

            root = os.environ.get("OCR_MODEL_DIR", "/opt/models")
            if not all(
                os.path.isfile(os.path.join(root, name, "inference.yml")) for name in ("det", "rec")
            ):
                raise RuntimeError("OCR_MODELS_NOT_FOUND")
            self.engine = PaddleOCR(
                device="cpu",
                ocr_version="PP-OCRv5",
                lang="korean",
                text_detection_model_name="PP-OCRv5_mobile_det",
                text_detection_model_dir=root + "/det",
                text_recognition_model_name="korean_PP-OCRv5_mobile_rec",
                text_recognition_model_dir=root + "/rec",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                enable_mkldnn=False,
                text_recognition_batch_size=1,
            )
        return self.engine

    # 모델 출력을 검증하고 확대·잘라내기 전 원본 좌표로 돌려놓는다.
    def _predict(self, image, source_box, *, refined=False):
        import cv2
        import numpy as np

        x1, y1, x2, y2 = source_box
        crop = image[y1:y2, x1:x2]
        options = {"text_rec_score_thresh": 0.0}
        if refined:
            # 검출 상자 확장을 줄이고 최대 960픽셀에서 순차 추론하여 메모리를 제한한다.
            scale = min(3.0, 960 / max(crop.shape[:2]))
            crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            options.update(
                text_det_limit_side_len=960, text_det_limit_type="max", text_det_unclip_ratio=1.1
            )
        scale_x, scale_y = crop.shape[1] / (x2 - x1), crop.shape[0] / (y2 - y1)
        predictions = list(self._engine().predict(crop, **options))
        if len(predictions) != 1:
            raise ImageError("OCR_OUTPUT_INVALID")
        output = predictions[0]
        try:
            texts, scores, polys = output["rec_texts"], output["rec_scores"], output["rec_polys"]
            if len(texts) != len(scores) or len(texts) != len(polys) or len(texts) > 2000:
                raise ImageError("OCR_OUTPUT_INVALID")
            lines = []
            for text, score, poly in zip(texts, scores, polys):
                poly = np.asarray(poly, dtype=float)
                score = float(score)
                if (
                    not isinstance(text, str)
                    or not math.isfinite(score)
                    or not 0 <= score <= 1
                    or poly.shape != (4, 2)
                    or not np.isfinite(poly).all()
                ):
                    raise ImageError("OCR_OUTPUT_INVALID")
                if text.strip():
                    box = [
                        max(x1, int(poly[:, 0].min() / scale_x + x1)),
                        max(y1, int(poly[:, 1].min() / scale_y + y1)),
                        min(x2, int(round(poly[:, 0].max() / scale_x + x1))),
                        min(y2, int(round(poly[:, 1].max() / scale_y + y1))),
                    ]
                    if box[2] <= box[0] or box[3] <= box[1]:
                        raise ImageError("OCR_OUTPUT_INVALID")
                    # 잘라낸 가장자리의 불완전한 글자는 기존 전체 이미지 판독을 대체하지 않는다.
                    if refined and (
                        (x1 > 0 and box[0] - x1 < 3)
                        or (y1 > 0 and box[1] - y1 < 3)
                        or (x2 < image.shape[1] and x2 - box[2] < 3)
                        or (y2 < image.shape[0] and y2 - box[3] < 3)
                    ):
                        continue
                    lines.append(
                        {
                            "text": text,
                            "score": score,
                            "box": box,
                        }
                    )
        except (KeyError, TypeError, ValueError, IndexError):
            raise ImageError("OCR_OUTPUT_INVALID") from None
        return lines

    # 세로로 긴 저신뢰 제목 조각은 자동 90도 회전을 거치지 않고 기존 인식기로 다시 읽는다.
    def _retry_upright(self, image, cells, diagnostics):
        import cv2

        targets, crops, seen = [], [], set()
        for index, cell in enumerate(cells):
            if not cell.get("required", True):
                continue
            for part_index, part in enumerate(cell["lines"]):
                x1, y1, x2, y2 = part["box"]
                key = tuple(part["box"])
                if key in seen or not 0 <= part["score"] < 0.9 or (y2 - y1) / max(1, x2 - x1) < 1.5:
                    continue
                if len(targets) == 8:
                    break
                seen.add(key)
                crop = image[y1:y2, x1:x2]
                if max(crop.shape[:2]) > 960:
                    scale = 960 / max(crop.shape[:2])
                    crop = cv2.resize(crop, None, fx=scale, fy=scale)
                crops.append(crop)
                targets.append((index, part_index, part))
        if not targets:
            return cells
        # PaddleX의 기존 인식기를 재사용하며 두 번째 모델을 메모리에 올리지 않는다.
        pipeline = getattr(self._engine(), "paddlex_pipeline", None)
        pipeline = getattr(pipeline, "_pipeline", pipeline)
        recognizer = getattr(pipeline, "text_rec_model", None)
        if not callable(recognizer):
            raise ImageError("OCR_RUNTIME_INCOMPATIBLE")
        results = list(recognizer(crops))
        if len(results) != len(targets):
            raise ImageError("OCR_OUTPUT_INVALID")
        summaries = []
        for (index, part_index, part), result in zip(targets, results):
            try:
                text, score = result["rec_text"], float(result["rec_score"])
                if not isinstance(text, str) or not math.isfinite(score) or not 0 <= score <= 1:
                    raise ImageError("OCR_OUTPUT_INVALID")
            except (KeyError, TypeError, ValueError):
                raise ImageError("OCR_OUTPUT_INVALID") from None
            accepted = normalize_name(text) == normalize_name(part["text"]) and score >= 0.9
            summaries.append(
                {
                    "candidate_index": index,
                    "part_index": part_index,
                    "box": part["box"],
                    "score_before": part["score"],
                    "score_after": score,
                    "accepted": accepted,
                }
            )
            if accepted:
                part["score"] = score
        if diagnostics is not None:
            diagnostics.upright(summaries)
        return cells

    # 전체 OCR 뒤 저해상도·큰 상표 영역을 같은 모델로 최대 세 번 더 검출한다.
    def __call__(self, data, diagnostics=None):
        image = read_image(data)
        height, width = image.shape[:2]
        full_box = [0, 0, width, height]
        lines = self._predict(image, full_box)
        if diagnostics is not None:
            diagnostics.regions(lines, image.shape)
        regions = product_regions(lines, image.shape)
        # 약봉투의 열 제한은 최종 후보 추출에서도 유지하며 임의의 문서 종류를 요구하지 않는다.
        if max(height, width) < 960 or regions:
            for index, box in enumerate([full_box] + regions, start=1):
                refined = self._predict(image, box, refined=True)
                if diagnostics is not None:
                    diagnostics.regions(refined, image.shape, pass_index=index, source_box=box)
                lines = reconcile_regions(lines, refined)
        if not lines:
            raise ImageError("NO_TEXT")
        return self._retry_upright(image, group_lines(lines), diagnostics)
