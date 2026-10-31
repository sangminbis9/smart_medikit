"""제품명 근거와 주변 문자 가설의 차이 및 재검출 중복의 실로그 회귀를 검증한다."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from test_pipeline import dependencies, event
from test_recognition import api_for, line, product

from backend.diagnostics import Diagnostics
from backend.image_ocr import (
    ImageError,
    PaddleReader,
    choose_reading,
    group_lines,
    reconcile_regions,
)
from backend.lambda_function import process_records
from backend.recognition import ApiError, RecognitionError, identify, lookup_candidate


# 식약처 응답을 이름별로 달리해 무관한 문구가 항상 성공하는 Mock을 방지한다.
def catalog_api(*products):
    api = api_for(*products)

    # 관측 이름이 들어 있는 가상 품목만 검색 결과로 돌려준다.
    def search(name):
        return [item for item in products if name in item["ITEM_NAME"].lower()]

    api.search.side_effect = search
    return api


# 실제 화이투벤 좌표처럼 작은 문구가 겹쳐도 같은 상표 영역은 한 번만 남긴다.
def test_duplicate_title_with_overlapping_small_text():
    old = [line("화이투벤", (40, 52, 261, 187), 0.9115)]
    passes = [
        [
            line("화이투벤", (40, 58, 258, 184), 0.8845),
            line("처음부터", (49, 44, 222, 109), 0.9007),
            line("큐", (252, 55, 279, 93), 0.9996),
            line("플러스", (278, 66, 310, 86), 0.9968),
        ],
        [
            line("화이투벤", (38, 55, 261, 187), 0.9177),
            line("처음부터", (51, 78, 112, 108), 0.9905),
            line("큐", (252, 55, 278, 94), 0.9997),
        ],
    ]
    for refined in passes:
        old = reconcile_regions(old, refined)
    assert sum(item["text"] == "화이투벤" for item in old) == 1
    cells = group_lines(old)
    assert len(cells) == 1
    result = identify(cells, catalog_api(product("화이투벤큐플러스연질캡슐")))
    assert len(result["items"]) == 1
    assert float(result["items"][0]["name_score"]) == 0.9177


# 같은 영역의 고신뢰 이름 대안 둘이 실제 품목이면 주변 상자가 있어도 확정하지 않는다.
def test_duplicate_conflict_with_extra_overlap():
    lines = reconcile_regions(
        [line("가상알파", (10, 10, 180, 100), 0.97)],
        [line("가상베타", (11, 11, 179, 99), 0.98), line("가격표", (100, 50, 130, 65))],
    )
    with pytest.raises(RecognitionError, match="AMBIGUOUS_PRODUCT"):
        identify(
            group_lines(lines),
            catalog_api(product("가상알파정"), product("가상베타정", "900000002")),
        )


# 공간이 다른 같은 이름의 포장은 중복 판독으로 지우지 않는다.
def test_same_name_in_separate_locations_is_preserved():
    a, b = line("지르텍정", (10, 10, 100, 40)), line("지르텍정", (200, 100, 290, 130))
    assert len(reconcile_regions([a, b], [a, b])) == 2


# 실로그의 이름·주변 영역 배치에서 합성 잡음의 검색 실패는 제품 성공을 취소하지 않는다.
@pytest.mark.parametrize(
    "title,extras,official",
    [
        (
            line("탁센400이부프로펜", (42, 146, 225, 207), 0.9983),
            [
                line("고함량으로", (86, 117, 139, 135), 0.9851),
                line("KSWEI", (86, 386, 118, 396), 0.9409),
                line("연질캡술", (134, 200, 166, 212), 0.8733),
            ],
            "탁센400이부프로펜연질캡슐",
        ),
        (
            line("지르텍정", (207, 95, 306, 132), 0.9949),
            [
                line("app", (251, 4, 265, 26), 0.9968),
                line("일반의악품", (263, 53, 301, 66), 0.8476),
            ],
            "지르텍정",
        ),
    ],
)
def test_surrounding_text_does_not_cancel_verified_product(title, extras, official, caplog):
    api = catalog_api(product(official))
    cells = group_lines([title] + extras)
    result = identify(cells, api, diagnostics=Diagnostics("synthetic-event"))
    assert len(result["medicines"]) == 1
    assert result["medicines"][0]["name"] == official
    summary = next(
        json.loads(r.message)
        for r in caplog.records
        if json.loads(r.message)["kind"] == "candidate_selection"
    )
    assert len(summary["ignored"]) == len(extras)
    assert all(part["text"] not in caplog.text for part in [title] + extras)


# 작은 상표라도 높은 신뢰도와 유일한 공식 품목 근거가 있으면 두 번째 제품으로 검증한다.
def test_small_brand_without_form_is_not_discarded():
    cells = group_lines(
        [line("가상알파정", (10, 10, 180, 80)), line("가상베타", (250, 150, 320, 165))]
    )
    result = identify(
        cells, catalog_api(product("가상알파정"), product("가상베타캡슐", "900000002"))
    )
    assert len(result["medicines"]) == 2


# 작은 제형 명시 제품과 큰 미등록 상표는 첫 제품이 성공해도 전체 저장을 막는다.
@pytest.mark.parametrize(
    "other,code",
    [
        (line("가상베타정", (250, 150, 320, 165), 0.7), "LOW_OCR_CONFIDENCE"),
        (line("미등록상표", (250, 150, 420, 215)), "PRODUCT_NOT_FOUND"),
    ],
)
def test_required_second_product_blocks_partial_save(other, code):
    deps = dependencies()
    deps.update(api=catalog_api(product("가상알파정")))
    deps["ocr"].return_value = group_lines([line("가상알파정", (10, 10, 180, 80)), other])
    with pytest.raises(RuntimeError, match="RECOGNITION_BATCH_FAILED"):
        process_records(event(), **deps)
    deps["store"].finish.assert_not_called()
    assert deps["store"].fail.call_args.args[3] == code


# 주변 문자 가설이라도 실제 품목 둘이 일치하면 임의로 제외하거나 하나를 선택하지 않는다.
def test_ambiguous_small_brand_still_fails():
    cells = group_lines(
        [line("가상알파정", (10, 10, 180, 80)), line("가상베타", (250, 150, 320, 165))]
    )
    with pytest.raises(RecognitionError, match="AMBIGUOUS_PRODUCT"):
        identify(
            cells,
            catalog_api(
                product("가상알파정"),
                product("가상베타정", "900000002"),
                product("가상베타캡슐", "900000003"),
            ),
        )


# 조회 장애는 제품이 없다는 근거가 아니므로 작은 문자 가설도 장애를 숨기지 않는다.
def test_api_error_on_lexical_hypothesis_fails():
    cells = group_lines(
        [line("가상알파정", (10, 10, 180, 80)), line("가상베타", (250, 150, 320, 165))]
    )
    api = api_for()
    api.search.side_effect = [[product("가상알파정")], ApiError("API_TIMEOUT", True)]
    with pytest.raises(RecognitionError, match="API_TIMEOUT"):
        identify(cells, api)


# 주변 문자만 남아도 빈 성공 결과를 저장할 수 없다.
def test_lexical_only_no_match_is_failure():
    cells = group_lines([line("가상문구")])
    cells[0].update(required=False, evidence="lexical")
    with pytest.raises(RecognitionError, match="PRODUCT_NOT_FOUND"):
        identify(cells, api_for())


# 이름이 맞지만 성분 괄호를 검증하지 못한 작은 제품은 단순 잡음으로 버리지 않는다.
def test_unverified_small_product_blocks_success():
    cells = group_lines(
        [line("가상알파정", (10, 10, 180, 80)), line("가상베타", (250, 150, 320, 165))]
    )
    api = catalog_api(product("가상알파정"), product("가상베타정(가상성분)", "900000002"))
    api.ingredients.return_value = [{"name": "다른성분"}]
    with pytest.raises(RecognitionError, match="PRODUCT_NOT_FOUND"):
        identify(cells, api)


# 더 높은 점수의 오타도 공식 품목 근거가 없으면 낮은 점수의 검증된 판독을 덮지 않는다.
def test_catalog_resolves_name_alternatives_without_score_guessing():
    lines = reconcile_regions(
        [line("화이투벤", (40, 52, 261, 187), 0.9177)], [line("가상오독", (38, 55, 261, 187), 0.99)]
    )
    lines += [line("큐", (252, 55, 278, 94), 0.9997), line("플러스", (278, 66, 310, 86), 0.9968)]
    result = identify(group_lines(lines), catalog_api(product("화이투벤큐플러스연질캡슐")))
    assert float(result["items"][0]["name_score"]) == 0.9177


# 대안의 조회 장애도 판독 유일성을 확인하지 못한 것이므로 성공으로 넘기지 않는다.
def test_alternative_api_error_is_not_ignored():
    lines = reconcile_regions([line("가상알파", score=0.97)], [line("가상베타", score=0.98)])
    api = api_for()
    api.search.side_effect = [[product("가상베타정")], ApiError("API_TIMEOUT", True)]
    with pytest.raises(RecognitionError, match="API_TIMEOUT"):
        identify(group_lines(lines), api)


# 한 영역의 판독 대안이 과도하게 늘면 추측과 API 호출 폭증을 막는다.
def test_name_alternatives_are_bounded():
    region = line("가상알파")
    for name in ["가상베타", "가상감마"]:
        region = choose_reading(region, line(name))
    with pytest.raises(ImageError, match="TOO_MANY_OCR_ALTERNATIVES"):
        choose_reading(region, line("가상델타"))


# 실제 로그의 세로로 긴 S는 회전 없이 같은 모델에 전달하고 그 관측 점수만 반영한다.
@pytest.mark.parametrize(
    "text,score,accepted", [("S", 0.9991, True), ("S", 0.8, False), ("5", 0.9999, False)]
)
def test_upright_retry_requires_same_text_and_threshold(text, score, accepted, caplog):
    cells = group_lines(
        [
            line("모드", (51, 61, 104, 90), 1.0),
            line("코", (103, 55, 154, 120), 0.9992),
            line("S", (146, 58, 173, 103), 0.7716),
        ]
    )
    recognizer = Mock(return_value=[{"rec_text": text, "rec_score": score}])
    reader = PaddleReader()
    reader.engine = SimpleNamespace(
        paddlex_pipeline=SimpleNamespace(_pipeline=SimpleNamespace(text_rec_model=recognizer))
    )
    reader._retry_upright(np.zeros((205, 340, 3), np.uint8), cells, Diagnostics("synthetic"))
    assert recognizer.call_count == 1
    assert recognizer.call_args.args[0][0].shape == (45, 27, 3)
    assert cells[0]["lines"][2]["score"] == (score if accepted else 0.7716)
    entry = json.loads(caplog.records[0].message)
    assert entry["regions"][0]["accepted"] is accepted
    if accepted:
        assert identify(cells, catalog_api(product("모드코에스연질캡슐")))["medicines"]
    else:
        with pytest.raises(RecognitionError, match="LOW_OCR_CONFIDENCE"):
            identify(cells, api_for())


# 내부 재사용 인터페이스가 다른 런타임에서는 낮은 점수를 강제로 통과시키지 않는다.
def test_upright_runtime_compatibility_failure():
    reader = PaddleReader()
    reader.engine = SimpleNamespace()
    cells = group_lines([line("가상약품", (10, 10, 30, 60), 0.7)])
    with pytest.raises(ImageError, match="OCR_RUNTIME_INCOMPATIBLE"):
        reader._retry_upright(np.zeros((100, 100, 3), np.uint8), cells, None)


# 판독 대안이 늘어도 이벤트의 고유 검색어 상한을 넘겨 API를 호출하지 않는다.
def test_search_budget_blocks_additional_query():
    api = api_for()
    cache = {f"query{index}": [] for index in range(32)}
    with pytest.raises(RecognitionError, match="TOO_MANY_SEARCH_QUERIES"):
        lookup_candidate({"name": "새가상약품"}, api, {}, cache, 0, None)
    api.search.assert_not_called()
