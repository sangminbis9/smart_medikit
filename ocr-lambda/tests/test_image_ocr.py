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
    reader.engine.predict.return_value = [prediction(lines)]
    result = identify(
        reader(data, diagnostics=Diagnostics("test-image")), api_for(product(official))
    )
    assert '"kind": "ocr_regions"' in caplog.text
    assert official not in caplog.text
    assert result["medicines"][0]["name"] == official
    assert reader.engine.predict.call_args.kwargs == {"text_rec_score_thresh": 0.0}
    assert reader.engine.predict.call_args.args[0].shape == (300, 600, 3)


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
