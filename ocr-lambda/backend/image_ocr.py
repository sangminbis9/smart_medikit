"""문서 형식과 관계없이 전체 이미지에서 위치가 검증된 제품 후보를 만든다."""

import os
import re
from operator import itemgetter

from backend.recognition import candidate_name, fragment_kind, name_fields, normalize_name


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
        -min(height, other) * 0.2 <= a1 - x2 <= height * 1.2
        and min(y2, b2) - max(y1, b1) >= min(height, other) * 0.5
    )


# 검증된 상표 조각 또는 명시적 제형·함량만 결합 대상으로 삼는다.
def can_join(left, right):
    # 수량·함량의 숫자와 단위가 분리된 경우 먼저 한 부가정보로 묶는다.
    if re.fullmatch(r"\d+(?:\.\d+)?", normalize_name(left["text"])):
        return fragment_kind(left["text"] + right["text"]) in {"quantity", "strength"}
    if not candidate_name(left["text"]):
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
        "탁센400": {"이부프로펜", "이부프로펜연질캡슐"},
    }
    return normalize_name(right["text"]) in pairs.get(normalize_name(left["text"]), set())


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
                if i != index and adjacent(left, right) and can_join(left, right)
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
                if i != index and adjacent(left, right, below=True) and can_join(left, right)
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
        if candidate_name(line["text"])
    ]
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
            )
        return self.engine

    # 표·문서 종류를 요구하지 않고 이미지 전체에서 OCR을 한 번 수행한다.
    def __call__(self, data):
        image = read_image(data)
        predictions = list(self._engine().predict(image, text_rec_score_thresh=0.0))
        if len(predictions) != 1:
            raise ImageError("OCR_OUTPUT_INVALID")
        output = predictions[0]
        try:
            texts, scores, polys = output["rec_texts"], output["rec_scores"], output["rec_polys"]
            if len(texts) != len(scores) or len(texts) != len(polys) or len(texts) > 2000:
                raise ImageError("OCR_OUTPUT_INVALID")
            lines = []
            for text, score, poly in zip(texts, scores, polys):
                if text.strip():
                    lines.append(
                        {
                            "text": text,
                            "score": float(score),
                            "box": [
                                int(poly[:, 0].min()),
                                int(poly[:, 1].min()),
                                int(poly[:, 0].max()),
                                int(poly[:, 1].max()),
                            ],
                        }
                    )
        except (KeyError, TypeError, ValueError, IndexError):
            raise ImageError("OCR_OUTPUT_INVALID") from None
        if not lines:
            raise ImageError("NO_TEXT")
        return group_lines(lines)
