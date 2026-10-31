"""실제 사진의 문구를 참고한 합성 OCR 영역으로 추출·제품 대조를 검증한다."""

from unittest.mock import Mock

import pytest

from backend.image_ocr import group_lines, merge_lines
from backend.recognition import ApiError, RecognitionError, identify


# 사진 자체의 모델 추론과 구분되는 합성 OCR 텍스트·좌표를 만든다.
def line(text, box=(10, 10, 150, 40), score=0.98):
    return {"text": text, "box": list(box), "score": score}


# 테스트용 가상 품목코드와 최소 공식 응답 필드를 만든다.
def product(name="지르텍정", code="900000001", **extra):
    return {"ITEM_SEQ": code, "ITEM_NAME": name, "ENTP_NAME": "테스트제약", **extra}


# 외부 API를 호출하지 않는 성공 응답을 준비한다.
def api_for(*products):
    api = Mock()
    api.search.return_value = list(products)
    api.ingredients.return_value = [
        {"name": "테스트성분", "amount": "10", "unit": "mg", "basis": "1정"}
    ]
    return api


# 네 실제 사진에 보이는 핵심 상표·제형과 무관한 문구를 합성 영역으로 표현한다.
PHOTO_CASES = [
    (
        "test-1.jpg",
        [
            line("탁센400"),
            line("이부프로펜", (10, 43, 150, 65)),
            line("연질캡슐", (10, 69, 150, 89)),
            line("판매가격 3000원", (200, 10, 350, 35), 0.2),
        ],
        "탁센400이부프로펜연질캡슐",
    ),
    (
        "test-2.jpg",
        [
            line("화이투벤"),
            line("큐", (155, 10, 180, 40)),
            line("플러스", (185, 10, 240, 40)),
            line("연질캡슐", (10, 43, 150, 65)),
            line("몸살감기 코감기 목감기", (10, 180, 280, 205), 0.3),
        ],
        "화이투벤큐플러스연질캡슐",
    ),
    (
        "test-3.jpg",
        [
            line("모드"),
            line("코S", (155, 10, 210, 40)),
            line("연질캡슐", (10, 43, 150, 65)),
            line("종근당", (10, 220, 100, 240), 0.2),
        ],
        "모드코에스연질캡슐",
    ),
    (
        "test-4.jpg",
        [
            line("지르텍"),
            line("정", (155, 10, 175, 40)),
            line("10정", (10, 44, 70, 64)),
            line("콧물 코막힘 재채기 가려움증", (10, 200, 300, 225), 0.1),
        ],
        "지르텍정",
    ),
]


# 문서 메타데이터·표 구조 없이 네 사진 유형의 동일한 추출 경로를 검증한다.
@pytest.mark.parametrize("filename,lines,official", PHOTO_CASES)
def test_four_photo_transcripts(filename, lines, official):
    result = identify(group_lines(lines), api_for(product(official)))
    assert result["processing_status"] == "COMPLETED"
    assert result["medicines"][0]["name"] == official
    assert float(result["items"][0]["name_score"]) == 0.98
    if filename == "test-4.jpg":
        assert result["items"][0]["package_quantity"] == {"value": 10, "unit": "정"}


# 제형 없는 상표도 실제 검색 후보가 되는지 검증한다.
@pytest.mark.parametrize(
    "brand,official",
    [
        ("지르텍", "지르텍정"),
        ("모드코S", "모드코에스연질캡슐"),
        ("화이투벤", "화이투벤큐플러스연질캡슐"),
        ("탁센400", "탁센400이부프로펜연질캡슐"),
    ],
)
def test_brand_only_is_search_candidate(brand, official):
    api = api_for(product(official))
    assert identify(group_lines([line(brand)]), api)["medicines"]
    api.search.assert_called_once()


# 제형 단독·포장 수량·가격표는 제품 검색 후보로 보내지 않는다.
@pytest.mark.parametrize("text", ["연질캡슐", "정", "시럽", "10정", "10mg", "판매가격 3000원"])
def test_non_name_is_not_candidate(text):
    api = api_for()
    with pytest.raises(RecognitionError, match="NO_PRODUCT_NAME"):
        identify(group_lines([line(text)]), api)
    api.search.assert_not_called()


# 수평 거리·크기가 다른 텍스트와 광고 문구는 같은 높이여도 합치지 않는다.
def test_merge_rejects_far_small_and_unrelated_regions():
    for other in [
        line("정", (500, 10, 520, 40)),
        line("정", (155, 10, 160, 14)),
        line("최고의선택", (155, 10, 230, 40), 0.2),
        line("10정", (155, 10, 180, 40), 0.2),
    ]:
        assert len(merge_lines([line("지르텍"), other])) == 2


# 여러 약품 사이의 제형 조각은 임의의 한 제품에 붙이지 않는다.
def test_ambiguous_fragment_owner_is_not_joined():
    lines = [
        line("지르텍", (10, 10, 100, 40)),
        line("다른약", (10, 45, 100, 75)),
        line("정", (105, 25, 120, 60)),
    ]
    assert len(merge_lines(lines)) == 3


# 포장 수량 10정은 함량 10mg과 분리하고 복용량 문구는 제외한다.
def test_strength_quantity_and_dosage_are_distinct():
    lines = [
        line("테스트정"),
        line("10mg", (155, 10, 220, 40)),
        line("10정", (10, 44, 65, 65)),
        line("1회 1정", (10, 180, 100, 200)),
    ]
    result = identify(group_lines(lines), api_for(product("테스트정10밀리그램")))
    assert result["medicines"][0]["strength"] == "10mg"
    assert result["items"][0]["package_quantity"]["value"] == 10


# 이름의 낮은 신뢰도를 다른 고득점 영역으로 상쇄하지 않는다.
@pytest.mark.parametrize("score", [0.899, float("nan"), float("inf"), -1, 1.1])
def test_low_or_invalid_name_confidence_fails(score):
    api = api_for(product())
    with pytest.raises(RecognitionError, match="LOW_OCR_CONFIDENCE"):
        identify(group_lines([line("지르텍정", score=score)]), api)
    api.search.assert_not_called()


# 제품 판단에 사용한 별도 제형·함량이 불확실하면 저장 가능한 결과를 만들지 않는다.
def test_low_attribute_confidence_fails_without_polluting_name_score():
    with pytest.raises(RecognitionError, match="LOW_OCR_CONFIDENCE"):
        identify(
            group_lines([line("지르텍"), line("정", (155, 10, 175, 40), 0.5)]), api_for(product())
        )


# 이름과 무관한 수량의 낮은 신뢰도는 제품명을 실패시키지 않고 수량만 미확인으로 둔다.
def test_low_quantity_is_ignored():
    result = identify(
        group_lines([line("지르텍정"), line("10정", (10, 44, 70, 64), 0.5)]), api_for(product())
    )
    assert result["items"][0]["package_quantity"] is None
    assert float(result["items"][0]["name_score"]) == 0.98


# 같은 브랜드의 두 품목·함량·제형 후보를 임의 선택하지 않는다.
def test_multiple_products_fail_closed():
    api = api_for(product("화이투벤큐연질캡슐"), product("화이투벤큐플러스연질캡슐", "900000002"))
    with pytest.raises(RecognitionError, match="AMBIGUOUS_PRODUCT"):
        identify(group_lines([line("화이투벤")]), api)
    api.ingredients.assert_not_called()


# 관측한 함량·세부 제형·업체가 다르면 이름이 같아도 대조를 거부한다.
@pytest.mark.parametrize(
    "lines,official",
    [
        ([line("테스트정10mg")], product("테스트정20mg")),
        ([line("테스트연질캡슐")], product("테스트경질캡슐")),
        ([line("테스트정"), line("제조사: 다른제약", (10, 44, 150, 65))], product("테스트정")),
    ],
)
def test_conflicting_evidence_fails(lines, official):
    with pytest.raises(RecognitionError, match="PRODUCT_NOT_FOUND"):
        identify(group_lines(lines), api_for(official))


# 검증된 별칭을 사용하더라도 비슷한 상표까지 자동 확장하지 않는다.
def test_alias_does_not_match_different_brand():
    with pytest.raises(RecognitionError, match="PRODUCT_NOT_FOUND"):
        identify(group_lines([line("모드코S")]), api_for(product("모드콜에스연질캡슐")))


# 공식 성분 근거가 있는 괄호만 분리하며 임의의 제품 변형은 제거하지 않는다.
def test_ingredient_annotation_requires_primary_evidence():
    api = api_for(product("지르텍정(세티리진염산염)", ITEM_INGR_NAME="세티리진염산염"))
    assert identify(group_lines([line("지르텍정")]), api)["medicines"]
    with pytest.raises(RecognitionError, match="PRODUCT_NOT_FOUND"):
        identify(group_lines([line("지르텍정")]), api_for(product("지르텍정(소아용)")))


# 선택적 성분 API 장애는 제품 대조 성공과 별도로 표시한다.
@pytest.mark.parametrize("error", [ApiError("API_TIMEOUT", True), RuntimeError("민감한 원문")])
def test_optional_ingredient_failure_is_not_ocr_failure(error):
    api = api_for(product())
    api.ingredients.side_effect = error
    result = identify(group_lines([line("지르텍정")]), api)
    assert result["processing_status"] == "COMPLETED"
    assert result["medicines"][0]["ingredients_status"] == "UNAVAILABLE"
    assert "민감한 원문" not in str(result)


# 복수 약품 중 하나라도 매칭에 실패하면 부분 성공 결과를 반환하지 않는다.
def test_multi_product_all_or_nothing():
    cells = group_lines([line("지르텍정"), line("미확인약정", (400, 10, 550, 40))])
    api = api_for(product())
    with pytest.raises(RecognitionError, match="PRODUCT_NOT_FOUND"):
        identify(cells, api)
    api.ingredients.assert_not_called()


# 동일 품목 반복 영역은 이벤트 근거만 남기고 Medicine 갱신은 하나로 합친다.
def test_duplicate_product_regions_are_deduplicated():
    cells = group_lines([line("지르텍정"), line("지르텍정", (400, 10, 550, 40))])
    result = identify(cells, api_for(product()))
    assert len(result["items"]) == 2
    assert len(result["medicines"]) == 1


# 잘린 API 응답·필수 코드 또는 공식 제형 누락은 성공으로 만들지 않는다.
@pytest.mark.parametrize(
    "row,code",
    [
        ({"ITEM_NAME": "지르텍정"}, "API_PRODUCT_SCHEMA_INVALID"),
        (product("지르텍"), "REQUIRED_PRODUCT_FIELDS_MISSING"),
    ],
)
def test_missing_required_product_fields(row, code):
    with pytest.raises(RecognitionError, match=code):
        identify(group_lines([line("지르텍")]), api_for(row))


# 세 조각으로 나뉜 영문 상표와 함량 없는 제형을 연결한다.
def test_three_piece_brand_and_form():
    lines = [
        line("모드", (10, 10, 90, 40)),
        line("코", (95, 10, 120, 40)),
        line("S", (125, 10, 145, 40)),
        line("연질캡슐", (10, 44, 145, 65)),
    ]
    result = identify(group_lines(lines), api_for(product("모드코에스연질캡슐")))
    assert result["medicines"][0]["name"] == "모드코에스연질캡슐"


# 별도로 인식한 수량 숫자와 단위도 함량으로 혼동하지 않는다.
def test_split_package_quantity():
    lines = [line("지르텍정"), line("10", (10, 44, 30, 64)), line("정", (33, 44, 50, 64))]
    result = identify(group_lines(lines), api_for(product()))
    assert result["items"][0]["package_quantity"] == {"value": 10, "unit": "정"}
    assert result["medicines"][0]["strength"] is None


# 두 제품이 모두 확인된 한 이미지에서 각각의 품목을 보존한다.
def test_multiple_distinct_products_success():
    cells = group_lines([line("지르텍정"), line("모드코S", (400, 10, 550, 40))])
    api = api_for(product(), product("모드코에스연질캡슐", "900000002"))
    result = identify(cells, api)
    assert {medicine["product_code"] for medicine in result["medicines"]} == {
        "900000001",
        "900000002",
    }
