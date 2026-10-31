"""약봉투 예시의 배치를 합성 텍스트로 재현하며 개인정보 원본은 사용하지 않는다."""

import pytest
from test_recognition import api_for, line, product

from backend.image_ocr import ImageError, group_lines
from backend.recognition import RecognitionError, identify


# 표 선 없이 약품명과 우측 분류가 반복되는 약봉투 행을 만든다.
def compact_rows(truncated=False):
    names = [
        "가상알파정",
        "가상베타정",
        "가상감마정500mg",
        "가상델타캡슐",
        "가상엡실론정",
        "가상제타정",
        "가상에타정",
    ]
    lines = []
    for index, name in enumerate(names):
        y = 10 + index * 60
        lines.extend(
            [
                line(name, (70, y, 210, y + 16)),
                line("백색의 원형 필름코팅정제", (70, y + 20, 210, y + 30), 0.4),
                line("[항히스타민] 알레르기 치료", (230, y, 410, y + 16), 0.3),
                line("1일 3회 5일", (430, y, 520, y + 16), 0.3),
            ]
        )
    if truncated:
        lines[8]["text"] = "가상감마정500밀리…"
    return names, lines


# 열 제목·성분·안내·숫자·가상 식별정보가 있는 두 번째 예시의 구조를 만든다.
def detailed_rows():
    names = [
        "가상점안액",
        "가상항생캡슐",
        "가상항생캡슐",
        "가상알파정10mg",
        "가상베타정1mg",
        "가상점이액",
    ]
    lines = [
        line("가상환자", (70, 5, 130, 25)),
        line("가상약국", (300, 5, 390, 25)),
        line("약품명 / 성분", (70, 60, 220, 75)),
        line("복약안내", (250, 60, 350, 75)),
    ]
    for index, name in enumerate(names):
        y = 90 + index * 70
        lines.extend(
            [
                line("(한) " + name, (70, y, 220, y + 18)),
                line("가상성분염산염 500mg", (70, y + 20, 220, y + 33), 0.5),
                line("[비] 약: 제조사 안내", (70, y + 35, 220, y + 45), 0.4),
                line("위장관계약", (260, y, 350, y + 18), 0.2),
                line("1", (380, y, 390, y + 18)),
                line("6", (410, y, 420, y + 18)),
                line("3", (440, y, 450, y + 18)),
                line("가상영수증정보", (500, y, 590, y + 18)),
            ]
        )
    lines.append(line("아세트아미노펜과 교차 복용하세요", (50, 550, 350, 570), 0.2))
    return names, lines


# 두 예시의 합성 배치에서 약품 행만 검색하고 성분·개인정보·광고는 보내지 않는다.
@pytest.mark.parametrize("factory", [compact_rows, detailed_rows])
def test_bag_rows_use_common_pipeline(factory):
    names, lines = factory()
    unique = list(dict.fromkeys(names))
    api = api_for(*(product(name, str(900000001 + index)) for index, name in enumerate(unique)))
    result = identify(group_lines(lines), api)
    assert len(result["items"]) == len(names)
    assert len(result["medicines"]) == len(unique)
    queries = [call.args[0] for call in api.search.call_args_list]
    assert not any("환자" in query or "성분" in query or "영수증" in query for query in queries)
    assert all(item["package_quantity"] is None for item in result["items"])
    assert result["prescription_status"] == "NOT_EVALUATED"


# 말줄임표가 있는 행은 누락시키거나 임의의 품목으로 대체하지 않는다.
def test_truncated_bag_row_fails_whole_image():
    _, lines = compact_rows(truncated=True)
    with pytest.raises(ImageError, match="TRUNCATED_PRODUCT_NAME"):
        group_lines(lines)


# 낮은 신뢰도의 이름은 주변 정상 행이 있더라도 전체 확정을 보류한다.
def test_one_low_confidence_bag_name_fails():
    names, lines = compact_rows()
    lines[4]["score"] = 0.5
    api = api_for(*(product(name, str(900000001 + i)) for i, name in enumerate(names)))
    with pytest.raises(RecognitionError, match="LOW_OCR_CONFIDENCE"):
        identify(group_lines(lines), api)


# 빈 약품 열에서는 머리말의 이름을 제품 검색으로 보내지 않는다.
def test_empty_named_column_does_not_search_personal_header():
    api = api_for()
    with pytest.raises(RecognitionError, match="NO_PRODUCT_NAME"):
        identify(
            group_lines(
                [line("가상환자", (10, 0, 150, 15)), line("약품명/성분", (10, 40, 150, 55))]
            ),
            api,
        )
    api.search.assert_not_called()


# 소수점 함량과 과거 제형 표기를 잘림으로 오인하지 않는다.
def test_decimal_strength_and_capsule_spelling():
    lines = [line("약품명/성분", (10, 0, 150, 15)), line("가상캅셀0.5mg", (10, 30, 150, 45))]
    result = identify(group_lines(lines), api_for(product("가상캡슐0.5밀리그램")))
    assert result["medicines"][0]["strength"] == "0.5mg"
