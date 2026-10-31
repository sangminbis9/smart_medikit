"""실제 로그 좌표와 수동 전사 문구로 영역 재검출 회귀를 검증하며 모델 추론과 구분한다."""

import io
import json
from unittest.mock import Mock

import cv2
import numpy as np
import pytest
from test_image_ocr import prediction
from test_pipeline import dependencies, event
from test_recognition import api_for, line, product

from backend.diagnostics import Diagnostics
from backend.image_ocr import (
    ImageError,
    PaddleReader,
    group_lines,
    product_regions,
    reconcile_regions,
)
from backend.lambda_function import process_records
from backend.recognition import RecognitionError, identify

# 로그에 기록되지 않은 OCR 원문은 추정하지 않고 실패 형태를 합성한다.
CASES = [
    (
        (205, 340),
        [
            line("모드코S코감기", (38, 45, 183, 125), 0.995),
            line("코감기", (50, 89, 102, 117), 0.9987),
        ],
        [
            line("모드", (50, 59, 105, 87)),
            line("코", (107, 55, 151, 112)),
            line("S", (151, 51, 176, 105)),
            line("코감기", (50, 92, 104, 111)),
        ],
        "모드코에스연질캡슐",
    ),
    (
        (265, 398),
        [line("잘못합쳐진상표", (40, 52, 261, 187), 0.9115)],
        [
            line("화이투벤", (53, 74, 242, 162)),
            line("처음부터", (48, 57, 173, 72)),
            line("큐", (249, 61, 276, 96)),
            line("플러스", (280, 73, 313, 90)),
        ],
        "화이투벤큐플러스연질캡슐",
    ),
    (
        (446, 283),
        [
            line("탁센", (37, 143, 139, 211), 0.8953),
            line("400", (119, 144, 190, 179), 0.999),
            line("이부프로펜", (123, 165, 228, 201), 0.9983),
        ],
        [
            line("탁센", (37, 143, 139, 211), 0.97),
            line("400", (119, 144, 190, 179), 0.999),
            line("이부프로펜", (123, 165, 228, 201), 0.9983),
        ],
        "탁센400이부프로펜연질캡슐",
    ),
]


# 재검출 결과를 모델 입력의 확대 좌표로 표현한다.
def scaled_prediction(lines, width, height):
    scale = min(3.0, 960 / max(height, width))
    sx, sy = round(width * scale) / width, round(height * scale) / height
    result = prediction(lines)
    result["rec_polys"] = [poly * [sx, sy] for poly in result["rec_polys"]]
    return result


# 이미지 디코딩부터 재검출·식약처 대조·저장 경계까지 모델과 외부 서비스만 대체한다.
@pytest.mark.parametrize("shape,initial,refined,official", CASES)
def test_three_packaging_regressions(shape, initial, refined, official, caplog):
    height, width = shape
    data = cv2.imencode(".jpg", np.full((height, width, 3), 255, np.uint8))[1].tobytes()
    reader = PaddleReader()
    reader.engine = Mock()
    reader.engine.predict.side_effect = [
        [prediction(initial)],
        [scaled_prediction(refined, width, height)],
        [prediction([])],
        [prediction([])],
    ]
    deps = dependencies({"event-id": "synthetic-region-test"})
    deps["s3"].get_object.return_value.update(Body=io.BytesIO(data), ContentLength=len(data))
    deps.update(ocr=reader, api=api_for(product(official)))
    assert process_records(event(), **deps) == {"processed": 1, "skipped": 0}
    deps["store"].fail.assert_not_called()
    result = deps["store"].finish.call_args.args[2]
    assert result["medicines"][0]["name"] == official
    assert float(result["items"][0]["name_score"]) >= 0.9
    assert 2 <= reader.engine.predict.call_count <= 4
    assert all(
        max(call.args[0].shape[:2]) <= 960 for call in reader.engine.predict.call_args_list[1:]
    )
    entries = [json.loads(record.message) for record in caplog.records]
    retry = next(row for row in entries if row.get("pass_index") == 1)
    assert retry["source_box"] == [0, 0, width, height]
    assert retry["regions"][0]["box"] == refined[0]["box"]
    assert all(part["text"] not in caplog.text for part in initial + refined)


# 0.8953은 반복 판독만으로 신뢰도가 올라가지 않으며 기존 기준에서 계속 실패한다.
def test_same_low_confidence_is_not_promoted():
    original = [line("탁센", (37, 143, 139, 211), 0.8953), line("400", (119, 144, 190, 179), 0.999)]
    lines = reconcile_regions(original, original)
    with pytest.raises(RecognitionError, match="LOW_OCR_CONFIDENCE"):
        identify(group_lines(lines), api_for(product("탁센400이부프로펜연질캡슐")))


# 같은 영역의 다른 고신뢰 이름·함량·제형·수량을 점수 순으로 임의 선택하지 않는다.
@pytest.mark.parametrize(
    "before,after", [("모드코S", "모드코프S"), ("10mg", "20mg"), ("정", "캡슐"), ("10정", "20정")]
)
def test_conflicting_high_confidence_regions_fail(before, after):
    with pytest.raises(ImageError, match="OCR_TEXT_CONFLICT"):
        reconcile_regions([line(before, score=0.95)], [line(after, score=0.99)])


# 재검출에서 누락한 다른 제품은 유지하여 한 제품만 조용히 등록하지 않는다.
def test_refinement_keeps_other_products_and_deduplicates():
    first = line("지르텍정", (10, 10, 100, 40))
    second = line("모드코S", (200, 100, 290, 140))
    actual = reconcile_regions([first, second], [first])
    assert actual == [first, second]
    assert len(group_lines(actual)) == 2


# 큰 상표 상자에서도 한 줄만 남은 재검출로 전체 근거를 삭제하지 않는다.
def test_single_child_does_not_erase_large_region():
    original = line("모드코S", (10, 10, 180, 130))
    child = line("S", (150, 30, 175, 65))
    assert original in reconcile_regions([original], [child])


# 수량과 근거 없는 숫자는 상표의 400 조각처럼 취급하지 않는다.
@pytest.mark.parametrize("fragment", ["10정", "10", "400정", "200"])
def test_only_verified_numeric_brand_fragment_joins(fragment):
    cells = group_lines([line("탁센", (37, 143, 139, 211)), line(fragment, (119, 144, 190, 179))])
    assert cells[0]["lines"][0]["text"] == "탁센"
    assert len(cells[0]["lines"]) == 1


# 떨어진 상표와 숫자는 같은 제품명으로 결합하지 않는다.
def test_distant_brand_suffix_is_not_joined():
    cells = group_lines([line("탁센", (10, 10, 100, 60)), line("400", (250, 10, 300, 60))])
    assert len(cells[0]["lines"]) == 1


# 국소 재검출의 오프셋·확대 배율을 되돌리고 잘린 가장자리 문구는 제외한다.
def test_crop_coordinates_and_clipped_text():
    reader = PaddleReader()
    reader.engine = Mock()
    reader.engine.predict.return_value = [
        prediction(
            [
                line("모드코S", (30, 30, 210, 90)),
                line("잘린문구", (0, 3, 60, 30)),
            ]
        )
    ]
    image = np.full((205, 340, 3), 255, np.uint8)
    lines = reader._predict(image, [40, 20, 140, 100], refined=True)
    assert lines == [line("모드코S", (50, 30, 110, 50))]
    assert reader.engine.predict.call_args.args[0].shape == (240, 300, 3)


# 상표 재검출 영역은 경계 안에 있고 겹치는 큰 상자를 반복 실행하지 않는다.
def test_region_budget_and_bounds():
    lines = [
        line("상표", (-5, -4, 100, 80)),
        line("겹침", (0, 0, 99, 79)),
        line("다른상표", (180, 100, 300, 170)),
        line("추가상표", (10, 180, 100, 204)),
    ]
    boxes = product_regions(lines, (205, 340, 3))
    assert len(boxes) == 2
    assert all(0 <= x1 < x2 <= 340 and 0 <= y1 < y2 <= 205 for x1, y1, x2, y2 in boxes)


# 판독 충돌은 실패 이벤트만 남기고 성공 제품 저장을 호출하지 않는다.
def test_conflict_records_failure_only():
    deps = dependencies()
    deps["ocr"].side_effect = ImageError("OCR_TEXT_CONFLICT")
    with pytest.raises(RuntimeError, match="RECOGNITION_BATCH_FAILED"):
        process_records(event(), **deps)
    deps["store"].finish.assert_not_called()
    assert deps["store"].fail.call_args.args[2:4] == ("ocr", "OCR_TEXT_CONFLICT")


# 비정상 모델 좌표와 점수를 진단 로그에 넣기 전에 거부한다.
@pytest.mark.parametrize(
    "score,poly",
    [(float("nan"), [[0, 0]] * 4), (0.95, [[0, 0], [1, float("inf")]]), (1.1, [[0, 0]] * 4)],
)
def test_invalid_model_numbers(score, poly):
    reader = PaddleReader()
    reader.engine = Mock()
    reader.engine.predict.return_value = [
        {"rec_texts": ["지르텍"], "rec_scores": [score], "rec_polys": [np.array(poly)]}
    ]
    with pytest.raises(ImageError, match="OCR_OUTPUT_INVALID"):
        reader._predict(np.zeros((30, 30, 3), np.uint8), [0, 0, 30, 30])


# 추가 진단에도 원문과 임의 필드를 복사하지 않는다.
def test_retry_diagnostics_are_numeric_only(caplog):
    Diagnostics("가상환자ID").regions(
        [line("가상환자이름")], (205, 340, 3), pass_index=2, source_box=[10, 20, 180, 120]
    )
    assert "가상환자" not in caplog.text
    record = json.loads(caplog.records[0].message)
    assert record["pass_index"] == 2 and record["source_box"] == [10, 20, 180, 120]
