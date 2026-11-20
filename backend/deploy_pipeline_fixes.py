"""기존 배포 패키지·역할·환경변수를 유지하며 파이프라인 코드만 갱신한다."""

import argparse
import hashlib
import io
import urllib.request
import zipfile
from pathlib import Path

ACCOUNT = "842544789367"
REGION = "ap-northeast-2"
OCR_HASH = "84fc7cd0cc7848293a9d7223583bec82b5fda275044b136fb626bd5ff4e0a7db"
OCR_RECOGNITION_HASH = "bf9e33c045da0afd3e08fbfaeadc66cc2ce8c445a004e7090becfe891ff47aa7"
ROOT = Path(__file__).resolve().parents[1]


# 검토한 배포 OCR만 수정하고 알 수 없는 버전은 덮어쓰지 않는다.
def patch_ocr(source):
    if "from backend.pi_contract import" in source:
        return source
    if hashlib.sha256(source.encode()).hexdigest() != OCR_HASH:
        raise ValueError("UNREVIEWED_OCR_VERSION")
    source = source.replace(
        "from backend.storage import DynamoStore",
        "from backend.storage import DynamoStore\nfrom backend.pi_contract import bind_pi_event, pi_completed, save_result",
    )
    source = source.replace(
        "        try:\n            previous = store.failure_snapshot(event_id)",
        """        pi_source = None
        try:
            # 이미지 버전을 고정한 메타데이터로 Pi 세션의 식별자를 먼저 연결한다.
            head_request = {"Bucket": bucket, "Key": key}
            if obj.get("versionId"):
                head_request["VersionId"] = obj["versionId"]
            else:
                head_request["IfMatch"] = '"' + obj["eTag"].strip('"') + '"'
            head = s3.head_object(**head_request)
            event_id, pi_source = bind_pi_event(head, bucket, key, event_id)
            if pi_source and pi_completed(store, event_id, pi_source):
                processed += 1
                continue
            previous = store.failure_snapshot(event_id)""",
    )
    source = source.replace("store.save_result(", "save_result(store, pi_source, ")
    compile(source, "<deployed-ocr-patch>", "exec")
    return source


# Pi 메타데이터가 검증된 포장 사진만 새 판독기로 연결한다.
def patch_package_entrypoint(source):
    if "from medikit_package_ocr.pi_package import PackageReader" in source:
        return source
    original = "            result = identify(ocr(data), api, min_score)"
    if source.count(original) != 1 or "pi_source = None" not in source:
        raise ValueError("UNREVIEWED_OCR_VERSION")
    source = source.replace(original, """            if pi_source:
                result = _package_reader.identify(data, api, identify, min_score, event_id, context)
            else:
                result = identify(ocr(data), api, min_score)""")
    source = source.replace(
        "from backend.pi_contract import bind_pi_event, pi_completed, save_result",
        "from backend.pi_contract import bind_pi_event, pi_completed, save_result\n"
        "from medikit_package_ocr.pi_package import PackageReader",
    )
    source = source.replace(
        "_reader = PaddleReader()", "_reader = PaddleReader()\n_package_reader = PackageReader(_reader)"
    )
    if source.count("_package_reader = PackageReader(_reader)") != 1 or (
        "from medikit_package_ocr.pi_package import PackageReader" not in source
    ):
        raise ValueError("UNREVIEWED_OCR_VERSION")
    compile(source, "<deployed-package-entrypoint>", "exec")
    return source


# 확인된 품목코드는 운영 함수에서 다시 검색하지 않고 기존 상세정보 보완만 적용한다.
def patch_verified_enrichment(source):
    if "*, verified_product=None):" in source:
        return source
    if hashlib.sha256(source.encode()).hexdigest() != OCR_RECOGNITION_HASH:
        raise ValueError("UNREVIEWED_OCR_RECOGNITION_VERSION")
    source = source.replace(
        "def identify(cells, api, min_score=0.9):",
        "def identify(cells, api, min_score=0.9, *, verified_product=None):",
    )
    source = source.replace(
        "            item, p = resolve_product(cell, api, min_score, search_results)",
        """            if verified_product is None:
                item, p = resolve_product(cell, api, min_score, search_results)
            else:
                p = verified_product
                item.update(recognition_status="SUCCEEDED", product_code=str(p["ITEM_SEQ"]))""",
    )
    compile(source, "<deployed-verified-enrichment>", "exec")
    return source


# 원래 ZIP의 의존성과 나머지 파일을 보존하고 지정된 소스만 치환한다.
def replace_files(original, replacements):
    result = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(original)) as source,
        zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as output,
    ):
        for name in source.namelist():
            if name not in replacements:
                output.writestr(name, source.read(name))
        for name, content in replacements.items():
            output.writestr(name, content)
    return result.getvalue()


# 계정과 변경 전 리비전을 확인하고 비밀 설정·IAM·공개 범위를 그대로 유지한다.
def deploy(apply=False, activity_only=False, ocr_only=False):
    import boto3

    session = boto3.Session(region_name=REGION)
    if session.client("sts").get_caller_identity()["Account"] != ACCOUNT:
        raise ValueError("WRONG_ACCOUNT")
    client = session.client("lambda")
    names = [
        "smart-medikit-capture-events",
        "smart-medikit-activity-api",
        "smart-medikit-notifications",
    ]
    if ocr_only:
        names = ["inha-capstone-02-ocr"]
    elif not activity_only:
        names = ["inha-capstone-02-ocr", *names, "smart-medikit-read-api"]
    prepared = []
    for name in names:
        function = client.get_function(FunctionName=name)
        config = function["Configuration"]
        if config["PackageType"] != "Zip" or not config["Role"].startswith(
            f"arn:aws:iam::{ACCOUNT}:role/"
        ):
            raise ValueError("UNEXPECTED_DEPLOYMENT")
        with urllib.request.urlopen(function["Code"]["Location"], timeout=30) as response:
            original = response.read()
        if name == "inha-capstone-02-ocr":
            with zipfile.ZipFile(io.BytesIO(original)) as archive:
                source = archive.read("backend/lambda_function.py").decode("utf-8")
                if ocr_only:
                    recognition = archive.read("backend/recognition.py").decode("utf-8")
            source = patch_ocr(source)
            replacements = {
                "backend/lambda_function.py": source.encode("utf-8"),
                "backend/pi_contract.py": (ROOT / "ocr-lambda/backend/pi_contract.py").read_bytes(),
            }
            if ocr_only:
                replacements["backend/lambda_function.py"] = patch_package_entrypoint(source).encode("utf-8")
                replacements["backend/recognition.py"] = patch_verified_enrichment(recognition).encode("utf-8")
                for module in ("diagnostics", "image_ocr", "mfds", "pi_package", "recognition"):
                    replacements[f"medikit_package_ocr/{module}.py"] = (
                        ROOT / "ocr-lambda" / "backend" / f"{module}.py"
                    ).read_bytes()
        elif name == "smart-medikit-read-api":
            replacements = {
                p: (ROOT / "backend" / p).read_bytes()
                for p in ("read_api.py", "product_document.py")
            }
            replacements["medkit_mfds.py"] = (ROOT / "ocr-lambda/backend/mfds.py").read_bytes()
        else:
            replacements = {
                p: (ROOT / "backend" / p).read_bytes()
                for p in (
                    "capture_events.py",
                    "activity_api.py",
                    "inventory_events.py",
                    "notifications.py",
                )
            }
        code = replace_files(original, replacements)
        prepared.append((name, config, original, code))
        print("PREPARED", name, hashlib.sha256(code).hexdigest(), flush=True)
    if not apply:
        return
    backup = Path.home() / "medkit-pipeline-backup-20261111"
    backup.mkdir(exist_ok=True, mode=0o700)
    for name, config, original, code in prepared:
        path = backup / f"{name}-{config['RevisionId']}.zip"
        if not path.exists():
            path.write_bytes(original)
            path.chmod(0o600)
        client.update_function_code(
            FunctionName=name, ZipFile=code, RevisionId=config["RevisionId"]
        )
        client.get_waiter("function_updated_v2").wait(FunctionName=name)
        after = client.get_function_configuration(FunctionName=name)
        if after.get("Environment") != config.get("Environment") or after["Role"] != config["Role"]:
            raise ValueError("DEPLOYMENT_CONFIGURATION_CHANGED")
        print("UPDATED", name, after["LastUpdateStatus"], flush=True)


# 기본은 배포 차이만 검증하고 명시적인 적용 시에만 기존 함수를 갱신한다.
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--activity-only", action="store_true", help="촬영·복약·알림 함수만 준비한다.")
    group.add_argument("--ocr-only", action="store_true", help="운영 OCR에 Pi 포장 판독 개선만 준비한다.")
    args = parser.parse_args()
    deploy(args.apply, args.activity_only, args.ocr_only)


if __name__ == "__main__":
    main()
