"""실제로 읽힌 고신뢰 문구의 개별·결합 검색과 관측 제형 선택을 검증한다."""

from unittest.mock import Mock

import numpy as np
import pytest
from test_candidate_selection import catalog_api
from test_image_ocr import prediction
from test_package_retry import package_photo, sloped_line
from test_recognition import line, product

from backend.diagnostics import Diagnostics
from backend.image_ocr import PaddleReader
from backend.pi_package import PackageReader
from backend.recognition import RecognitionError


# 모델만 대체하고 실제 포장 검색·재검출 흐름을 실행한다.
def run_package(lines, api, later=None):
    reader = PackageReader()
    if later is None:
        reader._predict = Mock(return_value=lines)
    else:
        reader._predict = Mock(side_effect=[lines, later, later, later])
    result = reader.match_package(
        package_photo(), api, 0.9, Diagnostics("synthetic", enabled=False)
    )
    return reader, result


# 고신뢰 제품명이 있어도 저신뢰 잡음 때문에 API 이전에 중단하지 않는다.
def test_low_confidence_noise_is_not_a_required_product():
    api = catalog_api(product("새빛정"))
    _, result = run_package(
        [line("M10", (0, 0, 150, 50), 0.4), line("새빛", (10, 60, 150, 100), 0.98)], api
    )
    assert result["name"] == "새빛정"
    assert [c.args[0] for c in api.search.call_args_list] == ["새빛"]


# 첫 단어가 여러 결과면 다음 읽힌 단어로 조회하고 유일해지면 결합 검색은 하지 않는다.
def test_other_word_is_searched_before_combination():
    api = catalog_api(product("새빛하늘정"), product("새빛바다정", "900000002"))
    _, result = run_package([line("새빛", (10, 10, 100, 40)), line("하늘", (10, 42, 95, 69))], api)
    assert result["name"] == "새빛하늘정"
    assert [c.args[0] for c in api.search.call_args_list] == ["새빛", "하늘"]


# 개별 문구가 모두 여러 결과일 때만 읽힌 두 문구를 결합한다.
def test_combination_follows_both_individual_queries():
    api = catalog_api(
        product("새빛하늘정"),
        product("새빛바다정", "900000002"),
        product("아침하늘정", "900000003"),
    )
    _, result = run_package([line("새빛", (10, 10, 100, 40)), line("하늘", (10, 42, 95, 69))], api)
    assert result["name"] == "새빛하늘정"
    assert [c.args[0] for c in api.search.call_args_list] == ["새빛", "하늘", "새빛하늘"]


# 읽힌 별도 제형으로만 API의 여러 결과를 좁힌다.
def test_only_observed_form_narrows_catalog():
    api = catalog_api(product("새빛정"), product("새빛캡슐", "900000002"))
    _, result = run_package(
        [line("새빛", (10, 10, 100, 40)), line("캡슐", (103, 10, 140, 40))], api
    )
    assert result["name"] == "새빛캡슐"
    assert [c.args[0] for c in api.search.call_args_list] == ["새빛"]


# 제형을 읽지 않았다면 이름 줄기가 같은 시럽 하나만 남겨 확정하지 않는다.
def test_no_form_does_not_pick_syrup_from_several_rows():
    api = catalog_api(product("모드코시럽"), product("모드코에스연질캡슐", "900000002"))
    with pytest.raises(RecognitionError, match="AMBIGUOUS_PRODUCT"):
        run_package([line("모드코")], api)
    assert api.search.call_count == 1


# 읽힌 이름으로 API 결과가 실제 한 개면 읽히지 않은 제형을 요구하지 않는다.
def test_one_api_row_can_be_registered_without_observed_form():
    api = catalog_api(product("새빛시럽"))
    _, result = run_package([line("새빛")], api)
    assert result["name"] == "새빛시럽"


# 상표에 들어간 겔을 제형으로 떼지 않고 읽힌 전체 문구를 조회한다.
def test_brand_ending_gel_is_not_rewritten_as_a_form():
    api = catalog_api(product("포타겔현탁액"))
    _, result = run_package([line("포타겔")], api)
    assert result["name"] == "포타겔현탁액"
    assert api.search.call_args.args[0] == "포타겔"


# API 결과에 올바른 이름이 있어도 OCR에서 읽히지 않은 철자를 검색하지 않는다.
def test_catalog_does_not_invent_unread_spelling():
    api = catalog_api(product("훼스탈플러스정"), product("훼스탈슈퍼자임정", "900000002"))
    with pytest.raises(RecognitionError):
        run_package([line("훼스탈", (10, 10, 100, 40)), line("규퍼자임", (15, 41, 95, 69))], api)
    queries = [c.args[0] for c in api.search.call_args_list]
    assert "규퍼자임" in queries and "훼스탈규퍼자임" in queries
    assert not any("슈퍼자임" in value for value in queries)


# 세 조각의 실제 기울기와 가까운 배치로 전체 제품명을 조회한다.
def test_three_slanted_fragments_can_be_combined():
    api = catalog_api(
        product("화이투벤큐플러스연질캡슐"),
        product("화이투벤큐연질캡슐", "900000002"),
        product("아침큐플러스정", "900000003"),
    )
    title = sloped_line("화이투벤", (35, 115), -16, width=220, height=75)
    _, result = run_package(
        [
            title,
            line("큐", (249, 54, 280, 94)),
            sloped_line("플러스", (277, 73), -13, width=34, height=22),
        ],
        api,
    )
    assert result["name"] == "화이투벤큐플러스연질캡슐"
    assert "화이투벤큐플러스" in [c.args[0] for c in api.search.call_args_list]


# 저신뢰 제형을 사용해 여러 후보를 임의로 하나로 좁히지 않는다.
def test_low_confidence_form_is_not_used_for_selection():
    api = catalog_api(product("새빛정"), product("새빛캡슐", "900000002"))
    with pytest.raises(RecognitionError, match="AMBIGUOUS_PRODUCT"):
        run_package([line("새빛"), line("캡슐", (155, 10, 190, 40), 0.89)], api)


# 다건 검색이면 재검출하고 단일 S를 에스로 바꾼 읽힌 결합 문구로 조회한다.
def test_ambiguous_brand_rechecks_and_combines_s():
    api = catalog_api(product("모드코시럽"), product("모드코에스연질캡슐", "900000002"))
    initial = [line("모드코", (10, 10, 140, 50))]
    later = initial + [line("S", (142, 8, 162, 55), 0.97)]
    reader, result = run_package(initial, api, later)
    assert result["name"] == "모드코에스연질캡슐"
    assert reader._predict.call_count == 2
    assert "모드코에스" in [c.args[0] for c in api.search.call_args_list]
    assert not any(c.args[0] == "에스" for c in api.search.call_args_list)


# 세로로 긴 S 하나는 긴 세로 영문 문구와 구분하여 보존한다.
def test_tall_single_s_is_kept_but_vertical_long_word_is_ignored():
    reader = PaddleReader()
    reader.engine = Mock()
    values = [
        sloped_line("S", (160, 10), 90, width=80, height=20),
        sloped_line("COMPANY", (220, 10), 90, width=150, height=20),
    ]
    result = prediction(values)
    result["rec_polys"] = [np.array(v["quad"]) for v in values]
    reader.engine.predict.return_value = [result]
    lines = reader._predict(np.zeros((300, 400, 3), np.uint8), [0, 0, 400, 300], package=True)
    assert [value["text"] for value in lines] == ["S"]


# 기존 별칭이 붙이던 성분도 읽히지 않았다면 검색어에 추가하지 않는다.
def test_korean_brand_query_does_not_append_unread_ingredient():
    api = catalog_api(product("탁센400이부프로펜연질캡슐"))
    _, result = run_package([line("탁센400")], api)
    assert result["name"] == "탁센400이부프로펜연질캡슐"
    assert [call.args[0] for call in api.search.call_args_list] == ["탁센400"]


# 기존 환경값이 낮아도 새 포장 검색의 점수 하한은 0.9다.
def test_package_search_never_uses_a_score_below_point_nine():
    api = catalog_api(product("새빛정"))
    reader = PackageReader()
    reader._predict = Mock(return_value=[line("새빛", score=0.89)])
    with pytest.raises(RecognitionError):
        reader.match_package(package_photo(), api, 0.8, Diagnostics("test", enabled=False))
    api.search.assert_not_called()


# 알려진 상표라도 세로로 놓인 긴 영어는 번역·검색에 사용하지 않는다.
def test_vertical_known_english_is_not_queried():
    api = catalog_api(product("타이레놀정"))
    with pytest.raises(RecognitionError):
        run_package([sloped_line("TYLENOL", (80, 10), 90, width=160)], api)
    api.search.assert_not_called()


# 검증된 가로 영문 상표만 한글 표기로 조회한다.
def test_horizontal_known_english_is_queried_in_korean():
    api = catalog_api(product("타이레놀정"))
    _, result = run_package([line("TYLENOL")], api)
    assert result["name"] == "타이레놀정"
    api.search.assert_called_once_with("타이레놀")


# 마지막 재검출은 제목을 포함하는 국소 영역에서 실제 글자를 다시 읽는다.
def test_final_retry_crops_the_observed_title():
    import cv2

    reader = PackageReader()
    reader._predict = Mock(return_value=[line("미확인상표", (400, 400, 600, 450))])
    api = catalog_api()
    data = cv2.imencode(".jpg", np.full((1000, 1000, 3), 255, np.uint8))[1].tobytes()
    with pytest.raises(RecognitionError):
        reader.match_package(data, api, 0.9, Diagnostics("test", enabled=False))
    box = reader._predict.call_args.args[1]
    assert box != [0, 0, 1000, 1000]
    assert box[0] <= 400 and box[1] <= 400 and box[2] >= 600 and box[3] >= 450


# 서로 다른 두 개의 제품명 근거를 한쪽 부분 이름의 단일 결과로 덮지 않는다.
def test_unique_suffix_cannot_override_a_different_required_brand():
    api = catalog_api(
        product("새빛바다정"),
        product("새빛아침정", "900000002"),
        product("아침하늘정", "900000003"),
    )
    with pytest.raises(RecognitionError):
        run_package([line("새빛", (10, 10, 100, 40)), line("하늘", (10, 42, 95, 69))], api)


# 실제 읽힌 가로 영문 제형도 한글 제형으로 바꾸어 다건 결과 선택에 사용한다.
def test_read_english_capsules_narrows_the_results():
    api = catalog_api(product("새빛정"), product("새빛캡슐", "900000002"))
    _, result = run_package(
        [line("새빛", (10, 10, 100, 40)), line("CAPSULES", (103, 10, 180, 40))], api
    )
    assert result["name"] == "새빛캡슐"
    assert [call.args[0] for call in api.search.call_args_list] == ["새빛"]


# 다른 단어의 조회로 넘어가도 먼저 관측한 캡슐과 정의 충돌을 지우지 않는다.
def test_other_query_cannot_erase_observed_form_conflict():
    api = catalog_api(product("새빛하늘정"))
    with pytest.raises(RecognitionError):
        run_package(
            [
                line("새빛", (10, 10, 100, 40)),
                line("캡슐", (104, 10, 150, 40)),
                line("하늘", (10, 90, 100, 120)),
            ],
            api,
        )


# 결합에 쓰이지 않은 독립 필수 문구의 후보도 같은 품목을 지지해야 한다.
def test_combination_keeps_other_required_product_evidence():
    api = catalog_api(
        product("새빛하늘정"),
        product("새빛바다정", "900000002"),
        product("아침하늘정", "900000003"),
        product("파란정", "900000004"),
        product("파란캡슐", "900000005"),
    )
    with pytest.raises(RecognitionError):
        run_package(
            [
                line("새빛", (10, 10, 100, 40)),
                line("하늘", (10, 42, 100, 72)),
                line("파란", (230, 180, 330, 210)),
            ],
            api,
        )


# 명시된 제조사는 검색어가 아니라 반드시 지켜야 하는 관측 속성이다.
def test_explicit_manufacturer_conflict_is_not_discarded():
    api = catalog_api(product("새빛정", ENTP_NAME="나래제약"))
    with pytest.raises(RecognitionError):
        run_package(
            [line("새빛", (10, 10, 100, 40)), line("제조사:가람제약", (10, 43, 150, 63))], api
        )
    assert not any("제조사" in c.args[0] for c in api.search.call_args_list)


# 동일 제품명의 여러 업체 중에서도 실제 읽은 업체만 사용한다.
def test_explicit_manufacturer_can_narrow_multiple_products():
    api = catalog_api(
        product("새빛정", ENTP_NAME="가람제약"),
        product("새빛정", "900000002", ENTP_NAME="나래제약"),
    )
    _, result = run_package(
        [line("새빛", (10, 10, 100, 40)), line("제조사:가람제약", (10, 43, 150, 63))], api
    )
    assert result["product_code"] == "900000001"


# 성분 괄호를 검증할 수 없는 행을 탈락시켜 가짜 유일 후보를 만들지 않는다.
def test_unknown_annotation_cannot_create_false_uniqueness():
    api = catalog_api(product("새빛캡슐(미확인성분)"), product("새빛하늘캡슐", "900000002"))
    with pytest.raises(RecognitionError):
        run_package([line("새빛", (10, 10, 100, 40)), line("캡슐", (104, 10, 150, 40))], api)


# 두 조각이 맞아도 바로 옆의 읽힌 플러스를 무시하고 기본 품목으로 등록하지 않는다.
def test_pair_success_does_not_ignore_a_remaining_modifier():
    api = catalog_api(product("새빛큐정"), product("새빛바다정", "900000002"))
    with pytest.raises(RecognitionError):
        run_package(
            [
                line("새빛", (10, 10, 100, 40)),
                line("큐", (102, 10, 130, 40)),
                line("플러스", (132, 10, 195, 40)),
            ],
            api,
        )
    assert "새빛큐플러스" in [c.args[0] for c in api.search.call_args_list]


# 독립된 G는 숫자에 붙은 g 단위가 아니라 지라는 표지로 결합한다.
def test_single_g_is_transliterated_not_treated_as_a_unit():
    api = catalog_api(product("새빛시럽"), product("새빛지캡슐", "900000002"))
    _, result = run_package([line("새빛", (10, 10, 100, 40)), line("G", (102, 10, 130, 40))], api)
    assert result["name"] == "새빛지캡슐"
    assert "새빛지" in [c.args[0] for c in api.search.call_args_list]


# 읽힌 함량의 소수점과 농도 기호도 버리지 않고 조회한다.
@pytest.mark.parametrize("text", ["새빛정0.5mg", "새빛크림0.1%", "새빛액10mg/ml"])
def test_observed_strength_punctuation_is_searchable(text):
    api = catalog_api(product(text))
    _, result = run_package([line(text)], api)
    assert result["name"] == text
    assert api.search.call_args.args[0] == text


# 잘린 넓은 검색은 성공으로 쓰지 않되 더 구체적인 읽힌 조합의 조회는 허용한다.
def test_result_limit_does_not_skip_a_more_specific_combination():
    from backend.mfds import ApiError

    api = catalog_api()

    # 넓은 검색은 잘린 응답을, 실제 읽힌 결합어는 유일 품목을 반환한다.
    def search(text):
        if text == "새빛":
            raise ApiError("API_RESULT_LIMIT")
        if text == "하늘":
            return [product("새빛하늘정"), product("아침하늘정", "900000002")]
        if text == "새빛하늘":
            return [product("새빛하늘정")]
        return []

    api.search.side_effect = search
    _, result = run_package([line("새빛", (10, 10, 100, 40)), line("하늘", (10, 42, 100, 72))], api)
    assert result["name"] == "새빛하늘정"
    assert [c.args[0] for c in api.search.call_args_list] == ["새빛", "하늘", "새빛하늘"]


# 기존에 사전에 있던 이름 조각도 개별 원문 조회보다 먼저 결합하지 않는다.
def test_known_name_fragments_are_queried_individually_first():
    api = catalog_api(
        product("탁센400이부프로펜연질캡슐"), product("이부프로펜연질캡슐", "900000002")
    )
    _, result = run_package(
        [line("탁센400", (10, 10, 120, 40)), line("이부프로펜연질캡슐", (121, 10, 300, 40))], api
    )
    assert result["name"] == "탁센400이부프로펜연질캡슐"
    assert api.search.call_args_list[0].args[0] == "탁센400"


# 제품명에 붙어 읽힌 G도 숫자 단위가 아니면 한글 표지로 바꾼다.
def test_attached_g_is_a_read_letter_not_a_unit():
    api = catalog_api(product("새빛지캡슐"))
    _, result = run_package([line("새빛G")], api)
    assert result["name"] == "새빛지캡슐"
    api.search.assert_called_once_with("새빛지")
