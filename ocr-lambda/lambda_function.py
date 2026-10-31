"""AWS 관리형 Python 3.12 ZIP Lambda의 진입점. 로컬 서버를 시작하지 않는다."""

import importlib
import os
import sys

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError

from backend.runtime_loader import OcrRuntime, RuntimeSetupError

_runtime = OcrRuntime()


# S3 실행 ZIP을 검증·준비한 뒤 기존 OCR/제품·성분/DB 처리를 실행한다.
def lambda_handler(event, context):
    if "/tmp/medikit-ocr/lib" not in os.environ.get("LD_LIBRARY_PATH", "").split(":"):
        raise RuntimeSetupError("OCR_RUNTIME_LIBRARY_PATH_REQUIRED")
    try:
        s3 = boto3.client(
            "s3",
            region_name=os.environ.get("AWS_REGION", "ap-northeast-2"),
            config=Config(
                connect_timeout=3,
                read_timeout=30,
                retries={"mode": "standard", "total_max_attempts": 2},
            ),
        )
    except BotoCoreError:
        raise RuntimeSetupError("OCR_RUNTIME_DOWNLOAD_FAILED") from None
    root = _runtime.prepare(
        s3,
        os.environ.get("OCR_RUNTIME_BUCKET"),
        os.environ.get("OCR_RUNTIME_KEY"),
        os.environ.get("OCR_RUNTIME_SHA256"),
    )
    packages = str(root / "python")
    if packages not in sys.path:
        sys.path.insert(0, packages)
        importlib.invalidate_caches()
    os.environ["OCR_MODEL_DIR"] = str(root / "models")
    os.environ["XDG_CACHE_HOME"] = "/tmp/.cache"
    os.environ["PADDLE_PDX_CACHE_HOME"] = "/tmp/.paddlex"
    os.environ["OPENCV_IO_MAX_IMAGE_PIXELS"] = "20000000"
    from backend.lambda_function import handler

    return handler(event, context)
