"""일반 ZIP Lambda에서 검증된 S3 OCR 실행 패키지를 /tmp에 준비한다."""

import hashlib
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from botocore.exceptions import BotoCoreError, ClientError

MAX_DOWNLOAD_BYTES = 1_500_000_000
MAX_UNPACKED_BYTES = 3_000_000_000


class RuntimeSetupError(RuntimeError):
    pass


class OcrRuntime:
    # 사용자 사진과 다른 고정 경로에 실행 패키지 하나만 준비한다.
    def __init__(self, root=Path("/tmp/medikit-ocr")):
        self.root = Path(root)
        self._identity = None

    # SHA-256·경로·크기를 검증한 패키지만 실행 경로로 옮긴다.
    def prepare(self, s3, bucket, key, digest):
        if (
            not bucket
            or not key
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise RuntimeSetupError("OCR_RUNTIME_CONFIGURATION_REQUIRED")
        identity = (bucket, key, digest)
        if self._identity == identity:
            return self.root
        if self._identity is not None:
            raise RuntimeSetupError("OCR_RUNTIME_RESTART_REQUIRED")
        self.root.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Lambda 강제 종료 뒤에도 /tmp가 남으므로 이 로더의 중단 파일만 정리한다.
            for abandoned in self.root.parent.glob("medikit-stage-*"):
                if abandoned.is_dir() and not abandoned.is_symlink():
                    shutil.rmtree(abandoned)
            if self.root.exists():
                shutil.rmtree(self.root)
            with tempfile.TemporaryDirectory(
                prefix="medikit-stage-", dir=self.root.parent
            ) as staging:
                downloaded = Path(staging) / "runtime.zip"
                self._download(s3, bucket, key, digest, downloaded)
                unpacked = Path(staging) / "contents"
                unpacked.mkdir()
                self._extract(downloaded, unpacked)
                # 중단된 추출 결과 대신 검증이 완료된 디렉터리만 사용한다.
                if self.root.exists():
                    shutil.rmtree(self.root)
                os.replace(unpacked, self.root)
            self._identity = identity
            return self.root
        except OSError:
            raise RuntimeSetupError("OCR_RUNTIME_STORAGE_FAILED") from None

    # 전체를 메모리에 올리지 않고 S3 본문을 읽으며 해시를 검증한다.
    def _download(self, s3, bucket, key, digest, destination):
        try:
            response = s3.get_object(Bucket=bucket, Key=key)
            body = response["Body"]
            try:
                if response.get("ContentLength", 0) > MAX_DOWNLOAD_BYTES:
                    raise RuntimeSetupError("OCR_RUNTIME_TOO_LARGE")
                checksum, size = hashlib.sha256(), 0
                with destination.open("wb") as output:
                    while chunk := body.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_DOWNLOAD_BYTES:
                            raise RuntimeSetupError("OCR_RUNTIME_TOO_LARGE")
                        checksum.update(chunk)
                        output.write(chunk)
                if checksum.hexdigest() != digest:
                    raise RuntimeSetupError("OCR_RUNTIME_HASH_MISMATCH")
            finally:
                body.close()
        except (BotoCoreError, ClientError):
            raise RuntimeSetupError("OCR_RUNTIME_DOWNLOAD_FAILED") from None

    # 실행 ZIP의 경로 탈출·링크·과도한 확장 크기를 거부한다.
    def _extract(self, downloaded, destination):
        try:
            with zipfile.ZipFile(downloaded) as zipped:
                members = zipped.infolist()
                if (
                    len(members) > 60_000
                    or sum(item.file_size for item in members) > MAX_UNPACKED_BYTES
                ):
                    raise RuntimeSetupError("OCR_RUNTIME_TOO_LARGE")
                for member in members:
                    path = PurePosixPath(member.filename)
                    if (
                        not path.parts
                        or path.is_absolute()
                        or ".." in path.parts
                        or "\\" in member.filename
                        or path.parts[0] not in {"python", "models", "lib", "licenses"}
                        or stat.S_ISLNK(member.external_attr >> 16)
                    ):
                        raise RuntimeSetupError("OCR_RUNTIME_ARCHIVE_INVALID")
                zipped.extractall(destination)
            required = [
                "python/paddleocr/__init__.py",
                "models/det/inference.yml",
                "models/rec/inference.yml",
                "lib/libgomp.so.1",
            ]
            if not all((destination / name).is_file() for name in required):
                raise RuntimeSetupError("OCR_RUNTIME_ARCHIVE_INVALID")
        except (zipfile.BadZipFile, RuntimeError) as exc:
            if isinstance(exc, RuntimeSetupError):
                raise
            raise RuntimeSetupError("OCR_RUNTIME_ARCHIVE_INVALID") from None
