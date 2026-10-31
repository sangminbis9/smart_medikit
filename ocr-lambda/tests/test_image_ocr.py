"""실제 이미지 디코딩과 Mock PaddleOCR 출력 어댑터를 구분해 검증한다."""

import os
from pathlib import Path
from unittest.mock import Mock

import cv2
import numpy as np
import pytest
from test_recognition import PHOTO_CASES, api_for, product

from backend.diagnostics import Diagnostics
from backend.image_ocr import ImageError, PaddleReader, read_image
from backend.recognition import identify


# 사진 문구 기반 합성 영역을 PaddleOCR 응답 형식으로 변환한다.
def prediction(lines):
    polygons = []
    for line in lines:
        x1, y1, x2, y2 = line["box"]
        polygons.append(np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]]))
    return {
        "rec_texts": [line["text"] for line in lines],
        "rec_scores": [line["score"] for line in lines],
        "rec_polys": polygons,
    }


# 표가 없는 실제 JPEG 바이트를 해석하고 모델 출력만 대체해 공통 경로를 검증한다.
@pytest.mark.parametrize("filename,lines,official", PHOTO_CASES)
def test_no_table_image_with_mock_paddle_output(filename, lines, official, caplog):
    data = cv2.imencode(".jpg", np.full((300, 600, 3), 255, dtype=np.uint8))[1].tobytes()
    reader = PaddleReader()
    reader.engine = Mock()
    reader.engine.predict.side_effect = [[prediction(lines)]] + [[prediction([])]] * 3
    result = identify(
        reader(data, diagnostics=Diagnostics("test-image")), api_for(product(official))
    )
    assert '"kind": "ocr_regions"' in caplog.text
    assert official not in caplog.text
    assert result["medicines"][0]["name"] == official
    assert reader.engine.predict.call_args_list[0].kwargs == {"text_rec_score_thresh": 0.0}
    assert reader.engine.predict.call_args_list[0].args[0].shape == (300, 600, 3)
    assert reader.engine.predict.call_count <= 4


# 모델 출력 길이 불일치와 빈 텍스트를 명확한 오류로 구분한다.
@pytest.mark.parametrize(
    "output,code",
    [
        ({"rec_texts": [], "rec_scores": [], "rec_polys": []}, "NO_TEXT"),
        ({"rec_texts": ["지르텍정"], "rec_scores": [], "rec_polys": []}, "OCR_OUTPUT_INVALID"),
        ({}, "OCR_OUTPUT_INVALID"),
    ],
)
def test_invalid_model_output(output, code):
    data = cv2.imencode(".png", np.full((30, 30, 3), 255, dtype=np.uint8))[1].tobytes()
    reader = PaddleReader()
    reader.engine = Mock()
    reader.engine.predict.return_value = [output]
    with pytest.raises(ImageError, match=code):
        reader(data)


# 제공된 실제 사진은 로컬에서만 읽고 Git·로그에 원본이나 모델 성공률을 남기지 않는다.
@pytest.mark.parametrize("filename", [case[0] for case in PHOTO_CASES])
def test_private_photo_decode_only(filename):
    directory = os.environ.get("OCR_TEST_IMAGE_DIR")
    if not directory:
        pytest.skip("실제 사진 디코딩은 OCR_TEST_IMAGE_DIR을 지정할 때만 실행")
    image = read_image((Path(directory) / filename).read_bytes())
    assert image.shape[0] > 0 and image.shape[1] > 0


# 이미지로 위장한 잘못된 파일은 OCR 모델 호출 전에 거부한다.
@pytest.mark.parametrize("data", [b"plain text", b"\xff\xd8\xffbroken"])
def test_invalid_image_bytes(data):
    with pytest.raises(ImageError, match="INVALID_IMAGE"):
        read_image(data)


# 명시적으로 지정한 로컬 모델만 초기화하고 기본 테스트에서는 실제 추론을 생략한다.
@pytest.fixture(scope="module")
def live_reader(tmp_path_factory):
    root = os.environ.get("OCR_LIVE_MODEL_DIR")
    if not root:
        pytest.skip("실제 모델 검증은 OCR_LIVE_MODEL_DIR과 OCR_TEST_IMAGE_DIR을 지정할 때 실행")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("OCR_MODEL_DIR", root)
        patch.setenv("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        patch.setenv("PADDLE_PDX_CACHE_HOME", str(tmp_path_factory.mktemp("ocr-cache")))
        reader = PaddleReader()
        reader._engine()
        yield reader


# 네 실제 사진의 모델 추론을 실행하되 식약처와 AWS는 합성 응답으로 분리한다.
@pytest.mark.parametrize("filename,unused,official", PHOTO_CASES)
def test_private_photo_live_ocr_mock_catalog(
    filename, unused, official, live_reader, record_property
):
    directory = os.environ.get("OCR_TEST_IMAGE_DIR")
    if not directory:
        pytest.fail("실제 사진 검증에는 OCR_TEST_IMAGE_DIR이 필요함")
    cells = live_reader((Path(directory) / filename).read_bytes())
    # 다른 사진의 제품도 포함해 실제 이름이 다르면 성공할 수 없도록 대조한다.
    products = [product(case[2], f"90000000{index + 1}") for index, case in enumerate(PHOTO_CASES)]
    result = identify(cells, api_for(*products))
    assert len(result["medicines"]) == 1
    assert result["medicines"][0]["name"] == official
    assert len(result["items"]) == 1
    score = float(result["items"][0]["name_score"])
    assert score >= 0.9
    record_property("name_score", score)
