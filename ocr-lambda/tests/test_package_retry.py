"""포장 OCR의 배율·기울기·추출 재시도와 기존 저장 계약의 경계를 검사한다."""

import inspect
import math
from unittest.mock import Mock

import numpy as np
import pytest
from test_image_ocr import prediction
from test_recognition import line

from backend.image_ocr import PaddleReader, group_lines
from backend.recognition import candidate_observations


# 읽는 방향의 사각형을 만들어 축 정렬 상자와 실제 기울기를 함께 표현한다.
def sloped_line(text, origin, angle, width=80, height=20):
    radians = math.radians(angle)
    along = np.array([math.cos(radians), math.sin(radians)])
    normal = np.array([-along[1], along[0]])
    p = np.array(origin, dtype=float)
    polygon = np.array([p, p + along * width, p + along * width + normal * height,
                        p + normal * height])
    box = [int(polygon[:, 0].min()), int(polygon[:, 1].min()),
           int(math.ceil(polygon[:, 0].max())), int(math.ceil(polygon[:, 1].max()))]
    return {**line(text, box), "angle": angle, "quad": polygon.tolist()}


# 포장 OCR은 실제 사각 좌표에서 읽기 기울기를 보존한다.
def test_package_predict_preserves_text_angle():
    item = sloped_line("새빛하늘정", (100, 50), 30)
    output = prediction([item])
    output["rec_polys"] = [np.array(item["quad"])]
    reader = PaddleReader()
    reader.engine = Mock()
    reader.engine.predict.return_value = [output]
    found = reader._predict(np.zeros((300, 400, 3), np.uint8), [0, 0, 400, 300], package=True)
    assert found[0].get("angle") == pytest.approx(30)
    assert np.allclose(found[0].get("quad"), item["quad"])


# 세로 영어는 제외하되 기울어진 가로 영어와 한글은 남긴다.
def test_package_predict_ignores_vertical_latin_only():
    items = [sloped_line("VERTICAL", (150, 10), 90),
             sloped_line("TYLENOL", (30, 150), 30), line("새빛정", (10, 10, 90, 30))]
    output = prediction(items)
    output["rec_polys"][:2] = [np.array(item["quad"]) for item in items[:2]]
    reader = PaddleReader()
    reader.engine = Mock()
    reader.engine.predict.return_value = [output]
    found = reader._predict(np.zeros((300, 400, 3), np.uint8), [0, 0, 400, 300], package=True)
    assert [row["text"] for row in found] == ["TYLENOL", "새빛정"]


# 두 배 확대 후에도 제품 좌표를 원본 이미지 기준으로 돌려준다.
def test_package_scale_restores_original_coordinates():
    reader = PaddleReader()
    assert "scale_factor" in inspect.signature(reader._predict).parameters
    reader.engine = Mock()
    reader.engine.predict.return_value = [prediction([line("새빛정", (20, 40, 100, 80))])]
    found = reader._predict(np.zeros((100, 200, 3), np.uint8), [0, 0, 200, 100],
                            package=True, scale_factor=2)
    assert reader.engine.predict.call_args.args[0].shape[:2] == (200, 400)
    assert found[0]["box"] == [10, 20, 50, 40]


# 네 배 확대도 모델 입력 긴 변 상한을 넘기지 않는다.
def test_package_scale_caps_pixels():
    reader = PaddleReader()
    assert "scale_factor" in inspect.signature(reader._predict).parameters
    reader.engine = Mock()
    reader.engine.predict.return_value = [prediction([])]
    reader._predict(np.zeros((900, 1800, 3), np.uint8), [0, 0, 1800, 900],
                    package=True, scale_factor=4)
    assert max(reader.engine.predict.call_args.args[0].shape[:2]) <= 2048


# 같은 대각선 기준선에 붙은 처음 보는 상표 조각도 결합 가설을 만든다.
def test_slanted_neighbor_names_are_combined():
    cells = group_lines([sloped_line("새빛", (30, 30), 35),
                         sloped_line("하늘", (101, 80), 35)])
    observations = candidate_observations(cells, 0, 0.9)
    assert "새빛하늘" in {value["name"] for value in observations}


# 위아래로 인쇄한 제품명은 가까운 같은 열에서만 결합한다.
def test_stacked_brand_names_are_combined():
    cells = group_lines([line("새빛", (10, 10, 110, 35)),
                         line("하늘", (10, 40, 105, 63))])
    observations = candidate_observations(cells, 0, 0.9)
    assert "새빛하늘" in {value["name"] for value in observations}


# 다른 기울기의 근처 문구를 같은 제품명으로 연결하지 않는다.
def test_different_slopes_do_not_join():
    cells = group_lines([sloped_line("새빛", (30, 30), 35),
                         sloped_line("하늘", (101, 80), -35)])
    assert "새빛하늘" not in {v["name"] for v in candidate_observations(cells, 0, 0.9)}

# 메모리의 합성 JPEG만 사용하고 실사용 사진·모델·외부 API와 분리한다.
def package_photo():
    import cv2
    return cv2.imencode('.jpg', np.full((240, 400, 3), 255, np.uint8))[1].tobytes()


# 포장 판독을 고정 응답으로 구동하되 실제 추출·대조·재시도 코드는 실행한다.
def match_readings(readings, api, context=None):
    from backend.diagnostics import Diagnostics
    from backend.pi_package import PackageReader
    reader = PackageReader()
    reader._predict = Mock(side_effect=readings)
    result = reader.match_package(package_photo(), api, 0.9,
                                  Diagnostics('synthetic', enabled=False), context)
    return reader, result


# 원본 실패 후 두 배에서도 실패하면 네 배 판독의 단일 제품으로 확정한다.
def test_package_retries_two_and_four_times():
    from test_recognition import api_for, product
    api = api_for(product('새빛정'))
    reader, result = match_readings([[], [], [line('새빛정')]], api)
    assert result['name'] == '새빛정'
    assert [call.kwargs.get('scale_factor', 1) for call in reader._predict.call_args_list] == [1, 2, 4]


# 두 배 판독이 통과하면 네 배와 회전 판독은 실행하지 않는다.
def test_package_stops_after_first_verified_product():
    from test_recognition import api_for, product
    reader, _ = match_readings([[], [line('새빛정')]], api_for(product('새빛정')))
    assert reader._predict.call_count == 2
    assert reader._predict.call_args.kwargs.get('scale_factor') == 2


# 결과가 너무 넓은 검색도 확대 재판독에서 구체 제품명이 확인되면 다시 대조한다.
def test_result_limit_retries_a_more_specific_name():
    from test_recognition import api_for, product

    from backend.mfds import ApiError
    api = api_for()
    api.search.side_effect = [ApiError('API_RESULT_LIMIT'), [product('새빛하늘정')]]
    # 첫 문구는 높은 점수라도 유효한 단일 품목 근거가 되지 못한다.
    _, result = match_readings([[line('새빛')], [line('새빛하늘정')]], api)
    assert result['name'] == '새빛하늘정'


# 저장 여유 시간이 없으면 모델을 다시 호출하지 않는다.
def test_package_respects_remaining_time():
    from test_recognition import api_for

    from backend.recognition import RecognitionError
    context = Mock()
    context.get_remaining_time_in_millis.return_value = 30000
    with pytest.raises(RecognitionError, match='NO_PRODUCT_NAME'):
        match_readings([[]], api_for(), context)


# 무결과 사진의 판독은 원본·두 배·네 배·기울기 보정 총 네 번으로 제한한다.
def test_package_limits_ocr_passes():
    from test_recognition import api_for

    from backend.diagnostics import Diagnostics
    from backend.pi_package import PackageReader
    from backend.recognition import RecognitionError
    reader = PackageReader()
    reader._predict = Mock(return_value=[])
    with pytest.raises(RecognitionError, match='NO_PRODUCT_NAME'):
        reader.match_package(package_photo(), api_for(), 0.9, Diagnostics('test', enabled=False))
    assert reader._predict.call_count == 4


# 증상·효능과 단독 수식어를 검색어로 보내지 않는다.
@pytest.mark.parametrize('text', ['소화불량', '속쓰림', '체함', '비동물성성분', '플러스', '슈퍼'])
def test_non_product_words_are_not_queried(text):
    from test_recognition import api_for

    from backend.recognition import RecognitionError, match_products
    api = api_for()
    with pytest.raises(RecognitionError, match='NO_PRODUCT_NAME'):
        match_products(group_lines([line(text)]), api, single_package=True)
    api.search.assert_not_called()


# 제품명 일부인 플러스는 단독 검색만 막고 가까운 상표와의 결합은 허용한다.
def test_modifier_is_kept_in_compound_name():
    cells = group_lines([line('새빛', (10, 10, 80, 35)), line('플러스', (82, 10, 160, 35))])
    observations = candidate_observations(cells, 0, 0.9)
    assert '새빛플러스' in {value['name'] for value in observations}
    assert all(value['name'] != '플러스' for value in candidate_observations(cells, 1, 0.9))


# 검증된 영문 상표는 한글 조회를 거쳐 함량이 맞는 품목만 선택한다.
@pytest.mark.parametrize('english,korean', [('TYLENOL 500mg', '타이레놀정500밀리그람'),
                                           ('ZYRTEC', '지르텍정')])
def test_verified_english_brand_is_searched_in_korean(english, korean):
    from test_recognition import api_for, product

    from backend.recognition import match_products
    api = api_for(product(korean))
    result, _, _ = match_products(group_lines([line(english)]), api, single_package=True)
    assert result[0]['name'] == korean
    assert api.search.call_args.args[0] in {'타이레놀', '지르텍'}


# 의약외품 표시는 저신뢰 오독이어도 다른 의약품 제형으로 확정하지 않는다.
@pytest.mark.parametrize('marker', ['의약외품', '의익외품'])
def test_quasi_drug_is_not_registered_as_gel(marker):
    from test_recognition import api_for, product

    from backend.recognition import RecognitionError
    api = api_for(product('마데카솔겔'))
    with pytest.raises(RecognitionError, match='UNSUPPORTED_PRODUCT_TYPE'):
        match_readings([[line('마데카솔'), line(marker, (10, 80, 90, 100), 0.78)]], api)
    api.search.assert_not_called()


# 제형 근거 없는 외용 상표를 같은 이름의 겔로 확정하지 않는다.
def test_topical_product_requires_visible_form():
    from test_recognition import api_for, product

    from backend.recognition import RecognitionError, match_products
    with pytest.raises(RecognitionError, match='PRODUCT_NOT_FOUND'):
        match_products(group_lines([line('마데카솔')]), api_for(product('마데카솔겔')),
                       single_package=True)


# API 연결 장애는 새로운 OCR로 가리지 않고 원래의 재시도 가능 오류를 보존한다.
def test_api_connection_error_is_not_an_ocr_retry():
    from test_recognition import api_for

    from backend.mfds import ApiError
    from backend.recognition import RecognitionError
    api = api_for()
    api.search.side_effect = ApiError('API_CONNECTION_ERROR', True)
    with pytest.raises(RecognitionError, match='API_CONNECTION_ERROR') as error:
        match_readings([[line('새빛정')]], api)
    assert error.value.retryable

# 기울기 보정으로 읽은 상자도 역변환하여 원본 위치·각도를 유지한다.
def test_deskew_restores_original_geometry():
    import cv2
    reader = PaddleReader()
    assert 'deskew_angle' in inspect.signature(reader._predict).parameters
    original = sloped_line('새빛정', (100, 90), 30)
    matrix = cv2.getRotationMatrix2D((200, 150), 30, 1)
    c, s = abs(matrix[0, 0]), abs(matrix[0, 1])
    w, h = math.ceil(300 * s + 400 * c), math.ceil(300 * c + 400 * s)
    matrix[0, 2] += w / 2 - 200
    matrix[1, 2] += h / 2 - 150
    rotated = cv2.transform(np.array([original['quad']], np.float64), matrix)[0]
    output = prediction([original])
    output['rec_polys'] = [rotated * 2]
    reader.engine = Mock()
    reader.engine.predict.return_value = [output]
    found = reader._predict(np.zeros((300, 400, 3), np.uint8), [0, 0, 400, 300],
                            package=True, scale_factor=2, deskew_angle=30)
    assert np.allclose(found[0]['quad'], original['quad'])
    assert found[0]['angle'] == pytest.approx(30)


# 같은 위치의 판독을 통합해도 대각선 결합에 필요한 사각 좌표가 사라지지 않는다.
def test_reconciled_reading_keeps_geometry():
    from backend.image_ocr import reconcile_regions
    old = sloped_line('새빛', (100, 90), 30)
    new = {**old, 'score': 0.99}
    assert reconcile_regions([old], [new])[0].get('quad') == new['quad']

# 여러 배율에서 같은 무결과 이름이 반복돼도 외부 검색은 이벤트당 한 번만 한다.
def test_search_cache_is_shared_across_ocr_passes():
    from test_recognition import api_for

    from backend.diagnostics import Diagnostics
    from backend.pi_package import PackageReader
    from backend.recognition import RecognitionError
    api = api_for()
    reader = PackageReader()
    reader._predict = Mock(return_value=[line('미확인상표')])
    with pytest.raises(RecognitionError, match='PRODUCT_NOT_FOUND'):
        reader.match_package(package_photo(), api, 0.9, Diagnostics('test', enabled=False))
    assert reader._predict.call_count == 4
    api.search.assert_called_once_with('미확인상표')


# 새 포장 경로의 외부 API에도 저장용 삼십 초를 남긴다.
def test_package_api_reserves_thirty_seconds():
    from backend.mfds import ApiError, MfdsClient
    api = MfdsClient('synthetic-key')
    api.reserve_seconds = 30
    context = Mock()
    context.get_remaining_time_in_millis.return_value = 30000
    api.begin_event(context)
    with pytest.raises(ApiError, match='API_TIME_BUDGET_EXHAUSTED'):
        api.request_timeout()


# 기울기 보정의 네 번째 판독은 앞에서 관측한 제목 각도로 실행한다.
def test_fourth_pass_uses_observed_angle():
    from test_recognition import api_for

    from backend.diagnostics import Diagnostics
    from backend.pi_package import PackageReader
    from backend.recognition import RecognitionError
    reader = PackageReader()
    reader._predict = Mock(return_value=[sloped_line('미확인상표', (70, 70), 30)])
    with pytest.raises(RecognitionError, match='PRODUCT_NOT_FOUND'):
        reader.match_package(package_photo(), api_for(), 0.9, Diagnostics('test', enabled=False))
    assert reader._predict.call_args.kwargs['deskew_angle'] == 30


# 유효 품목과 다른 제형도 가능하다는 근거가 공존하면 자동 확정하지 않는다.
def test_conflicting_form_remains_unresolved():
    from test_recognition import api_for, product

    from backend.image_ocr import ImageError
    with pytest.raises(ImageError, match='OCR_TEXT_CONFLICT'):
        match_readings([[line('새빛정', score=0.85), line('캡슐', (160, 10, 200, 40))],
                        [line('새빛정'), line('정', (160, 10, 200, 40))]], api_for(product('새빛정')))

# 결과 상한으로 검증하지 못한 다른 이름 가설을 무시하고 새 이름을 확정하지 않는다.
def test_result_limit_does_not_hide_unrelated_reading():
    from test_recognition import api_for, product

    from backend.mfds import ApiError
    from backend.recognition import RecognitionError
    api = api_for()
    api.search.side_effect = [ApiError('API_RESULT_LIMIT'), [product('새빛정')]]
    with pytest.raises(RecognitionError, match='API_RESULT_LIMIT'):
        match_readings([[line('가람')], [line('새빛정')], [], []], api)


# 괄호·라벨·띄어쓰기가 포함된 의약외품 표시도 다른 약품으로 확정하지 않는다.
@pytest.mark.parametrize('marker', ['[의약외품]', '제품구분: 의약외품', '의 약 외 품'])
def test_embedded_quasi_drug_label_blocks_registration(marker):
    from test_recognition import api_for, product

    from backend.recognition import RecognitionError
    api = api_for(product('마데카솔겔'))
    with pytest.raises(RecognitionError, match='UNSUPPORTED_PRODUCT_TYPE'):
        match_readings([[line('마데카솔겔'), line(marker, (10, 80, 190, 105))]], api)
    api.search.assert_not_called()


# 기존 제형 조각 결합도 다른 각도의 문구를 같은 제품명으로 만들지 않는다.
def test_form_fragment_with_different_angle_is_not_joined():
    cells = group_lines([sloped_line('새빛', (30, 30), 0),
                         sloped_line('정', (115, 30), 40, width=20)])
    assert all('새빛정' != ''.join(p['text'] for p in cell['lines']) for cell in cells)


# 조금 낮은 점수라도 기준을 통과하고 제형까지 검증한 대안을 근거로 사용한다.
def test_compatible_visible_form_is_not_lost_to_higher_bare_score():
    from test_recognition import api_for, product
    reader, result = match_readings([[line('마데카솔', score=0.99)],
                                     [line('마데카솔겔', score=0.98)]],
                                    api_for(product('마데카솔겔')))
    assert result['name'] == '마데카솔겔'
    assert reader._predict.call_count == 2


# 결합에 사용한 수식어의 독립 확인에서도 단독 수식어 검색은 금지한다.
def test_independent_modifier_is_never_queried():
    from test_recognition import api_for, product

    from backend.recognition import match_products
    api = api_for(product('새빛플러스정'))
    result, _, _ = match_products(group_lines([line('새빛', (10, 10, 80, 35)),
                                              line('플러스', (82, 10, 160, 35))]),
                                  api, single_package=True)
    assert result[0]['name'] == '새빛플러스정'
    assert '플러스' not in [call.args[0] for call in api.search.call_args_list]


# 한 글자도 독립 검색하지 않고 같은 기울기의 이웃과 결합할 때는 보존한다.
@pytest.mark.parametrize('first,second', [('새', '빛'), ('새빛', '나')])
def test_single_korean_character_can_join_slanted_name(first, second):
    cells = group_lines([sloped_line(first, (30, 30), 35),
                         sloped_line(second, (101, 80), 35)])
    names = {v['name'] for index in range(len(cells))
             for v in candidate_observations(cells, index, 0.9)}
    assert first + second in names
    assert not any(len(value) == 1 for value in names)


# 이미 픽셀 상한에 도달한 두 배와 동일한 네 배 입력은 다시 추론하지 않는다.
def test_capped_four_times_pass_is_skipped():
    import cv2
    from test_recognition import api_for

    from backend.diagnostics import Diagnostics
    from backend.pi_package import PackageReader
    from backend.recognition import RecognitionError
    reader = PackageReader()
    reader._predict = Mock(return_value=[])
    photo = cv2.imencode('.jpg', np.full((1200, 1600, 3), 255, np.uint8))[1].tobytes()
    with pytest.raises(RecognitionError, match='NO_PRODUCT_NAME'):
        reader.match_package(photo, api_for(), 0.9, Diagnostics('test', enabled=False))
    assert reader._predict.call_count == 3
    assert 4 not in [call.kwargs.get('scale_factor') for call in reader._predict.call_args_list]


# 첫 판독도 저장 여유가 없는 시점에는 시작하지 않는다.
def test_initial_ocr_is_not_started_with_one_second_left():
    from test_recognition import api_for

    from backend.diagnostics import Diagnostics
    from backend.pi_package import PackageReader
    from backend.recognition import RecognitionError
    context = Mock()
    context.get_remaining_time_in_millis.return_value = 1000
    reader = PackageReader()
    reader._predict = Mock(return_value=[])
    with pytest.raises(RecognitionError):
        reader.match_package(package_photo(), api_for(), 0.9,
                             Diagnostics('test', enabled=False), context)
    reader._predict.assert_not_called()


# 큰 원본도 중간 회전 버퍼를 모델 입력 상한 크기로 직접 생성한다.
def test_rotation_canvas_is_bounded_before_allocation(monkeypatch):
    import cv2
    original = cv2.warpAffine
    spy = Mock(wraps=original)
    monkeypatch.setattr(cv2, 'warpAffine', spy)
    reader = PaddleReader()
    reader.engine = Mock()
    reader.engine.predict.return_value = [prediction([])]
    reader._predict(np.zeros((3000, 4000, 3), np.uint8), [0, 0, 4000, 3000],
                    package=True, scale_factor=2, deskew_angle=45)
    assert max(spy.call_args.args[2]) <= 2048
