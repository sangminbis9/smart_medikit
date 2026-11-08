"""승인된 공통 비밀번호 복용약 API를 별도 AWS 자원에 배포한다."""

import argparse
import base64
import getpass
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit
import warnings
import zipfile


NAME = "smart-medikit-medication-api"
TABLE = "inha-capstone-02-medkit-medications"
ROLE_NAME = "SafeRole-inha-capstone-02"
HANDLER = "medication_api.lambda_handler"
LOG_GROUP = f"/aws/lambda/{NAME}"
TAGS = {"ManagedBy": NAME}
TABLE_OWNER_ACCOUNT = "842544789367"
TABLE_OWNER_TAGS = {"username": "inha-capstone-02", "group": "inha-capstone"}
KEY_SCHEMA = [
    {"AttributeName": "pk", "KeyType": "HASH"},
    {"AttributeName": "sk", "KeyType": "RANGE"},
]
ROUTES = {
    "POST /session": "MedicationSession",
    "GET /medications": "MedicationRead",
    "PUT /medications": "MedicationWrite",
}
PASSWORD_ITERATIONS = 200000


# URL에 경로·인증정보·와일드카드가 들어간 배포 설정을 거부한다.
def validate_options(account, region, box_id, origin):
    if not re.fullmatch(r"[0-9]{12}", account):
        raise RuntimeError("AWS 계정 ID는 숫자 12자리여야 합니다.")
    if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-[0-9]+", region):
        raise RuntimeError("AWS 리전 형식이 올바르지 않습니다.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", box_id):
        raise RuntimeError("보관함 ID는 영문·숫자·밑줄·하이픈 1~100자여야 합니다.")
    try:
        parsed = urlsplit(origin)
        valid_port = parsed.port is None or 1 <= parsed.port <= 65535
    except ValueError:
        raise RuntimeError("허용 출처 URL 형식이 올바르지 않습니다.") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or "*" in origin
        or any(character.isspace() for character in origin)
        or not valid_port
    ):
        raise RuntimeError("허용 출처는 끝의 / 없이 정확한 HTTPS origin으로 지정하세요.")


# 이번 기능이 소유한 자원만 갱신하고 학교 자동 태그는 보존한다.
def require_owned(tags):
    if tags.get("ManagedBy") != NAME:
        raise RuntimeError("동명 자원의 소유 태그가 달라 배포를 중단합니다.")


# 테이블은 학교 자동 소유 태그를 필수로 확인하고 기존 관리 태그가 있으면 함께 대조한다.
def require_table_owned(tags):
    if any(tags.get(key) != value for key, value in TABLE_OWNER_TAGS.items()):
        raise RuntimeError("복용약 테이블의 학교 소유 태그가 일치하지 않아 중단합니다.")
    if "ManagedBy" in tags and tags["ManagedBy"] != NAME:
        raise RuntimeError("복용약 테이블의 기존 관리 태그가 달라 중단합니다.")


# 정확한 계정·리전·테이블 ARN과 저장 키 계약을 확인한다.
def require_table_metadata(table, region, account):
    expected_arn = f"arn:aws:dynamodb:{region}:{account}:table/{TABLE}"
    if table.get("TableArn") != expected_arn or table.get("TableName") != TABLE:
        raise RuntimeError("복용약 테이블의 ARN이나 이름이 승인한 범위와 다릅니다.")
    if table.get("KeySchema") != KEY_SCHEMA:
        raise RuntimeError("복용약 테이블의 키 구조가 계약과 다릅니다.")
    attributes = {item["AttributeName"]: item["AttributeType"] for item in table.get("AttributeDefinitions", [])}
    if attributes != {"pk": "S", "sk": "S"} or table.get("GlobalSecondaryIndexes") or table.get("LocalSecondaryIndexes"):
        raise RuntimeError("복용약 테이블의 속성 또는 인덱스가 계약과 다릅니다.")
    if table.get("BillingModeSummary", {}).get("BillingMode") != "PAY_PER_REQUEST":
        raise RuntimeError("복용약 테이블은 요청 기반 과금 설정이어야 합니다.")
    if table.get("TableStatus") not in {"ACTIVE", "CREATING", "UPDATING"}:
        raise RuntimeError("복용약 테이블이 배포 가능한 상태가 아닙니다.")


# 페이지가 나뉜 HTTP API와 로그 메타데이터를 끝까지 읽는다.
def collect_pages(client, operation, result_key, **parameters):
    items = []
    while True:
        response = getattr(client, operation)(**parameters)
        items.extend(response.get(result_key, []))
        token = response.get("NextToken") or response.get("nextToken")
        if not token:
            return items
        token_name = "nextToken" if "nextToken" in response else "NextToken"
        parameters[token_name] = token


# 학교에서 이미 제공한 역할의 계정과 Lambda 신뢰만 확인한다.
def find_school_role(iam, account):
    for page in iam.get_paginator("list_roles").paginate():
        for role in page["Roles"]:
            if role["RoleName"] != ROLE_NAME:
                continue
            if role["Arn"] != f"arn:aws:iam::{account}:role/{ROLE_NAME}":
                raise RuntimeError("학교 제공 역할의 계정이나 경로가 다릅니다.")
            for statement in role.get("AssumeRolePolicyDocument", {}).get("Statement", []):
                services = statement.get("Principal", {}).get("Service", [])
                if isinstance(services, str):
                    services = [services]
                if statement.get("Effect") == "Allow" and "lambda.amazonaws.com" in services:
                    return role
            raise RuntimeError("학교 제공 역할에서 Lambda 실행 신뢰를 확인할 수 없습니다.")
    raise RuntimeError("학교 제공 실행 역할을 찾지 못했습니다.")


# 기존 테이블의 소유권·키·과금 설정을 확인하며 항목은 읽지 않는다.
def inspect_table(dynamodb, account=TABLE_OWNER_ACCOUNT):
    exists = False
    for page in dynamodb.get_paginator("list_tables").paginate():
        if TABLE in page.get("TableNames", []):
            exists = True
            break
    if not exists:
        return None
    table = dynamodb.describe_table(TableName=TABLE)["Table"]
    require_table_metadata(table, dynamodb.meta.region_name, account)
    tags = collect_pages(dynamodb, "list_tags_of_resource", "Tags", ResourceArn=table["TableArn"])
    require_table_owned({tag["Key"]: tag["Value"] for tag in tags})
    return table


# 기존 함수의 소유권·진입점·데이터 범위를 확인하고 환경변수를 외부에 출력하지 않는다.
def inspect_function(lambdas, account, box_id):
    exists = False
    for page in lambdas.get_paginator("list_functions").paginate():
        if any(function["FunctionName"] == NAME for function in page.get("Functions", [])):
            exists = True
            break
    if not exists:
        return None
    function = lambdas.get_function(FunctionName=NAME)
    require_owned(function.get("Tags", {}))
    config = function["Configuration"]
    if config["Handler"] != HANDLER or config["Role"] != f"arn:aws:iam::{account}:role/{ROLE_NAME}":
        raise RuntimeError("기존 복용약 함수의 진입점이나 실행 역할이 다릅니다.")
    variables = config.get("Environment", {}).get("Variables", {})
    if config.get("Environment", {}).get("Error"):
        raise RuntimeError("기존 복용약 함수의 환경변수를 읽을 수 없습니다.")
    if variables.get("MEDKIT_MEDICATION_TABLE", TABLE) != TABLE:
        raise RuntimeError("기존 함수의 복용약 테이블이 다릅니다.")
    if variables.get("MEDKIT_BOX_ID", box_id) != box_id:
        raise RuntimeError("기존 함수의 보관함 ID를 다른 저장 범위로 변경할 수 없습니다.")
    return function


# 기존 HTTP API의 통합·경로·스테이지가 이번 기능에 속하는지 확인한다.
def inspect_api(gateway, function_arn):
    matches = []
    for api in collect_pages(gateway, "get_apis", "Items"):
        if api.get("Name") == NAME:
            matches.append(api)
    if len(matches) > 1:
        raise RuntimeError("동명 복용약 API가 여러 개여서 자동 선택하지 않습니다.")
    if not matches:
        return None
    api = matches[0]
    require_owned(api.get("Tags", {}))
    if api.get("ProtocolType") != "HTTP":
        raise RuntimeError("기존 복용약 API의 프로토콜이 다릅니다.")
    api_id = api["ApiId"]
    integrations = collect_pages(gateway, "get_integrations", "Items", ApiId=api_id)
    if len(integrations) > 1:
        raise RuntimeError("기존 복용약 API에 여러 통합이 있어 중단합니다.")
    integration_id = None
    if integrations:
        integration = integrations[0]
        if (
            integration.get("IntegrationUri") != function_arn
            or integration.get("IntegrationType") != "AWS_PROXY"
            or integration.get("PayloadFormatVersion") != "2.0"
        ):
            raise RuntimeError("기존 복용약 API의 Lambda 통합이 다릅니다.")
        integration_id = integration["IntegrationId"]
    routes = collect_pages(gateway, "get_routes", "Items", ApiId=api_id)
    for route in routes:
        if (
            route["RouteKey"] not in ROUTES
            or not integration_id
            or route.get("Target") != f"integrations/{integration_id}"
            or route.get("AuthorizationType", "NONE") != "NONE"
        ):
            raise RuntimeError("기존 복용약 API 경로나 인증 구성이 예상과 다릅니다.")
    stages = collect_pages(gateway, "get_stages", "Items", ApiId=api_id)
    if any(stage["StageName"] != "$default" for stage in stages):
        raise RuntimeError("기존 복용약 API에 별도 스테이지가 있어 중단합니다.")
    return {"api": api, "integration_id": integration_id, "routes": routes, "stages": stages}


# 기존 로그 그룹의 소유권만 확인하고 민감 로그 내용은 읽지 않는다.
def inspect_logs(logs):
    groups = collect_pages(logs, "describe_log_groups", "logGroups", logGroupNamePrefix=LOG_GROUP)
    for group in groups:
        if group["logGroupName"] == LOG_GROUP:
            require_owned(logs.list_tags_log_group(logGroupName=LOG_GROUP).get("tags", {}))
            return True
    return False


# 바이너리를 환경변수에 넣을 수 있는 패딩 없는 URL-safe 문자열로 바꾼다.
def encode_secret(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


# 새 비밀번호를 화면에 노출하지 않고 받아 솔트 해시와 세션 서명키를 생성한다.
def prompt_credentials():
    if not sys.stdin.isatty():
        raise RuntimeError("비밀번호 입력은 직접 조작하는 TTY 터미널에서 실행하세요.")
    # 숨김 입력을 지원하지 않는 터미널에서 평문 입력으로 대체하지 않는다.
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("공통 복용약 비밀번호 (12자 이상, 화면에 표시되지 않음): ")
            confirmation = getpass.getpass("공통 복용약 비밀번호 다시 입력: ")
    except getpass.GetPassWarning:
        raise RuntimeError("숨김 입력을 지원하는 터미널에서 비밀번호를 설정하세요.") from None
    if password != confirmation:
        raise RuntimeError("입력한 두 비밀번호가 다릅니다.")
    if not 12 <= len(password) <= 256:
        raise RuntimeError("공통 비밀번호는 12~256자로 입력하세요.")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    password_hash = f"pbkdf2_sha256${PASSWORD_ITERATIONS}${encode_secret(salt)}${encode_secret(digest)}"
    return password_hash, encode_secret(os.urandom(32))


# 접근 모드는 명시적 공개 선택 때만 바꾸고 재배포에서는 기존 설정과 인증정보를 유지한다.
def build_environment(function, box_id, origin, rotate_password, public_demo=False):
    variables = dict((function or {}).get("Configuration", {}).get("Environment", {}).get("Variables", {}))
    if public_demo:
        variables["MEDKIT_MEDICATION_PUBLIC_DEMO"] = "true"
    else:
        variables.setdefault("MEDKIT_MEDICATION_PUBLIC_DEMO", "false")
    is_public = variables["MEDKIT_MEDICATION_PUBLIC_DEMO"] == "true"
    if is_public and rotate_password:
        raise RuntimeError("공개 시연에서는 비밀번호 교체 옵션을 함께 사용할 수 없습니다.")
    if not is_public:
        password_hash = variables.get("MEDKIT_PASSWORD_HASH")
        signing_secret = variables.get("MEDKIT_SESSION_SECRET")
        if rotate_password or not password_hash or not signing_secret:
            password_hash, signing_secret = prompt_credentials()
        elif not re.fullmatch(r"pbkdf2_sha256\$200000\$[A-Za-z0-9_-]{22}\$[A-Za-z0-9_-]{43}", password_hash) or not re.fullmatch(r"[A-Za-z0-9_-]{43,}", signing_secret):
            raise RuntimeError("기존 인증 설정이 예상 형식과 다릅니다. 확인 후 비밀번호 교체 옵션을 사용하세요.")
        variables.update({"MEDKIT_PASSWORD_HASH": password_hash, "MEDKIT_SESSION_SECRET": signing_secret})
    variables.update({
        "MEDKIT_MEDICATION_TABLE": TABLE,
        "MEDKIT_BOX_ID": box_id,
        "MEDKIT_ALLOWED_ORIGIN": origin,
    })
    # 공개 재고 API의 인증 생략 설정을 새 함수에 상속하지 않는다.
    variables.pop("MEDKIT_DEMO_PUBLIC_READ", None)
    return {"Variables": variables}


# API 소스만 메모리 ZIP으로 묶고 비밀번호나 배포 환경을 파일에 남기지 않는다.
def source_archive():
    source = Path(__file__).with_name("medication_api.py")
    if not source.is_file():
        raise RuntimeError("같은 디렉터리에 medication_api.py가 필요합니다.")
    compile(source.read_text(encoding="utf-8"), "medication_api.py", "exec")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(source, "medication_api.py")
    return buffer.getvalue()


# 기존 항목을 건드리지 않고 전용 테이블이 없을 때만 생성한다.
def ensure_table(dynamodb, table, account=TABLE_OWNER_ACCOUNT):
    if table is None:
        try:
            # TagResource 권한을 요구하지 않고 학교 자동 태깅을 기다린다.
            dynamodb.create_table(
                TableName=TABLE,
                KeySchema=KEY_SCHEMA,
                AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
                DeletionProtectionEnabled=True,
            )
        except Exception as error:
            if getattr(error, "response", {}).get("Error", {}).get("Code") != "ResourceInUseException":
                raise
            # 이전 시도가 생성까지 성공했다면 기존 항목을 건드리지 않고 소유권만 재검증한다.
    deadline = time.monotonic() + 60
    reason = "state"
    while True:
        try:
            current = dynamodb.describe_table(TableName=TABLE)["Table"]
            require_table_metadata(current, dynamodb.meta.region_name, account)
            raw_tags = collect_pages(dynamodb, "list_tags_of_resource", "Tags", ResourceArn=current["TableArn"])
            tags = {tag["Key"]: tag["Value"] for tag in raw_tags}
            # 명백히 다른 소유자의 값은 태그 지연으로 간주하지 않는다.
            for key, expected in {**TABLE_OWNER_TAGS, "ManagedBy": NAME}.items():
                if key in tags and tags[key] != expected:
                    raise RuntimeError("복용약 테이블의 소유 태그가 달라 중단합니다.")
            if all(key in tags for key in TABLE_OWNER_TAGS):
                require_table_owned(tags)
                if current["TableStatus"] == "ACTIVE":
                    return current
                reason = "state"
            else:
                reason = "tags"
        except Exception as error:
            code = getattr(error, "response", {}).get("Error", {}).get("Code")
            if code not in {"AccessDenied", "AccessDeniedException", "ResourceNotFoundException"}:
                raise
            reason = "access" if code in {"AccessDenied", "AccessDeniedException"} else "state"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if reason == "access":
                raise RuntimeError("테이블 생성 후 60초 안에 소유권 조회 권한을 확인하지 못했습니다. 학교 자동 태그 반영과 계정의 조회 권한을 확인하세요.")
            if reason == "tags":
                raise RuntimeError("학교 자동 소유 태그를 60초 안에 확인하지 못했습니다. 태그 반영을 확인한 뒤 다시 실행하세요.")
            raise RuntimeError("복용약 테이블이 60초 안에 ACTIVE 상태로 확인되지 않았습니다. 상태를 확인한 뒤 다시 실행하세요.")
        time.sleep(min(2, remaining))


# 학교 역할을 재사용해 새 함수를 만들거나 확인한 revision의 함수만 갱신한다.
def ensure_function(lambdas, function, role_arn, environment, archive):
    if function is None:
        lambdas.create_function(
            FunctionName=NAME,
            Role=role_arn,
            Runtime="python3.12",
            Handler=HANDLER,
            Timeout=10,
            MemorySize=256,
            Environment=environment,
            Code={"ZipFile": archive},
            Tags=TAGS,
        )
        lambdas.get_waiter("function_active_v2").wait(FunctionName=NAME)
    else:
        lambdas.update_function_code(
            FunctionName=NAME,
            ZipFile=archive,
            RevisionId=function["Configuration"]["RevisionId"],
        )
        lambdas.get_waiter("function_updated_v2").wait(FunctionName=NAME)
        current = lambdas.get_function_configuration(FunctionName=NAME)
        # 코드 갱신 완료 뒤 revision은 달라질 수 있지만 다른 실행자의 설정 변경은 덮어쓰지 않는다.
        for field in ("Role", "Runtime", "Handler", "Timeout", "MemorySize", "Environment"):
            if current.get(field) != function["Configuration"].get(field):
                raise RuntimeError("코드 갱신 중 Lambda 설정이 변경되어 중단합니다. 최신 설정을 확인한 뒤 다시 실행하세요.")
        lambdas.update_function_configuration(
            FunctionName=NAME,
            RevisionId=current["RevisionId"],
            Role=role_arn,
            Runtime="python3.12",
            Handler=HANDLER,
            Timeout=10,
            MemorySize=256,
            Environment=environment,
        )
        lambdas.get_waiter("function_updated_v2").wait(FunctionName=NAME)
    available = lambdas.get_account_settings()["AccountLimit"].get("UnreservedConcurrentExecutions", 0)
    if available >= 102:
        lambdas.put_function_concurrency(FunctionName=NAME, ReservedConcurrentExecutions=2)
    else:
        print("CONCURRENCY: 계정 할당량으로 예약을 추가하지 않았습니다. API 요청 제한을 적용합니다.", flush=True)
    return lambdas.get_function_configuration(FunctionName=NAME)["FunctionArn"]


# 인증 없는 조회·쓰기가 거부되는지 실제 함수에서 확인하며 데이터는 변경하지 않는다.
def verify_deny(lambdas, origin):
    for method in ("GET", "PUT"):
        event = {
            "version": "2.0",
            "rawPath": "/medications",
            "headers": {"origin": origin, "content-type": "application/json"},
            "requestContext": {"http": {"method": method}},
            "body": "{}" if method == "PUT" else None,
        }
        result = lambdas.invoke(FunctionName=NAME, Payload=json.dumps(event).encode("utf-8"))
        response = json.load(result["Payload"])
        if result.get("FunctionError") or response.get("statusCode") != 401:
            raise RuntimeError("인증 없는 복용약 요청 거부 검증에 실패했습니다. API 연결을 중단합니다.")


# 서명키를 메모리에서만 사용해 배포 점검에 필요한 짧은 잔여시간의 토큰을 만든다.
def verification_token(environment):
    variables = environment["Variables"]
    secret = variables["MEDKIT_SESSION_SECRET"]
    signing_key = base64.urlsafe_b64decode(secret + "=" * (-len(secret) % 4))
    now = int(time.time())
    # 기존 2시간 세션 계약을 유지하되 배포 토큰은 2분 뒤 만료되게 한다.
    claims = {
        "v": 1,
        "aud": "smart-medikit-medications",
        "box_id": variables["MEDKIT_BOX_ID"],
        "iat": now - (7200 - 120),
        "exp": now + 120,
        "nonce": encode_secret(os.urandom(12)),
    }
    payload = encode_secret(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signature = encode_secret(hmac.new(signing_key, payload.encode("ascii"), hashlib.sha256).digest())
    return f"{payload}.{signature}"


# 인증된 함수를 호출하되 응답 본문·비밀번호·토큰을 출력하지 않는다.
def invoke_medications(lambdas, origin, token, method, body=None):
    event = {
        "version": "2.0",
        "rawPath": "/medications",
        "headers": {
            "origin": origin,
            "content-type": "application/json",
        },
        "requestContext": {"http": {"method": method}},
        "body": None if body is None else json.dumps(body, separators=(",", ":")),
    }
    if token:
        event["headers"]["authorization"] = f"Bearer {token}"
    result = lambdas.invoke(FunctionName=NAME, Payload=json.dumps(event).encode("utf-8"))
    response = json.load(result["Payload"])
    if result.get("FunctionError") or response.get("statusCode") != 200:
        raise RuntimeError("인증된 복용약 저장소 검증에 실패했습니다. 데이터 원문은 출력하지 않으며 API 연결을 중단합니다.")
    snapshot = json.loads(response.get("body", "null"))
    if (
        not isinstance(snapshot, dict)
        or set(snapshot) != {"revision", "state"}
        or type(snapshot["revision"]) is not int
        or snapshot["revision"] < 0
        or not isinstance(snapshot["state"], dict)
    ):
        raise RuntimeError("인증된 복용약 응답의 구조가 다릅니다. API 연결을 중단합니다.")
    return snapshot


# 실제 테이블 접근을 검증하고 처음부터 비어 있던 항목만 조건부로 생성한다.
def verify_storage(lambdas, origin, environment):
    token = verification_token(environment)
    snapshot = invoke_medications(lambdas, origin, token, "GET")
    print("AUTHENTICATED_GET_STATUS=200", flush=True)
    if snapshot["revision"] > 0:
        # 기존 항목은 비어 있어도 권한 점검을 이유로 다시 쓰거나 수정하지 않는다.
        return
    empty_state = {"version": 1, "plans": [], "records": []}
    if snapshot["state"] != empty_state or type(snapshot["state"].get("version")) is not int:
        raise RuntimeError("초기 revision의 상태가 빈 목록과 달라 쓰기 검증을 중단합니다.")
    created = invoke_medications(lambdas, origin, token, "PUT", {"expected_revision": 0, "state": empty_state})
    if created["revision"] != 1 or created["state"] != empty_state:
        raise RuntimeError("초기 빈 항목의 조건부 저장 결과가 예상과 다릅니다. API 연결을 중단합니다.")
    print("INITIAL_EMPTY_PUT_STATUS=200", flush=True)
    persisted = invoke_medications(lambdas, origin, token, "GET")
    # 검증 직후의 다른 기기 변경은 되돌리지 않고 저장 버전의 전진만 확인한다.
    if persisted["revision"] < 1 or (persisted["revision"] == 1 and persisted["state"] != empty_state):
        raise RuntimeError("초기 빈 항목을 저장한 뒤 다시 읽는 검증에 실패했습니다.")
    print("PERSISTENCE_GET_STATUS=200", flush=True)


# 공개 접근과 입력 검증만 확인하며 빈 항목 생성이나 기존 데이터 쓰기는 수행하지 않는다.
def verify_public_demo(lambdas, origin):
    invoke_medications(lambdas, origin, None, "GET")
    print("PUBLIC_GET_STATUS=200", flush=True)
    # 음수 revision은 어떤 저장 상태에서도 유효하지 않아 테이블 쓰기 전에 거부된다.
    event = {
        "version": "2.0",
        "rawPath": "/medications",
        "headers": {"origin": origin, "content-type": "application/json"},
        "requestContext": {"http": {"method": "PUT"}},
        "body": json.dumps({"expected_revision": -1, "state": {"version": 1, "plans": [], "records": []}}),
    }
    result = lambdas.invoke(FunctionName=NAME, Payload=json.dumps(event).encode("utf-8"))
    response = json.load(result["Payload"])
    body = json.loads(response.get("body", "null"))
    if result.get("FunctionError") or response.get("statusCode") != 422 or not isinstance(body, dict) or body.get("code") != "INVALID_STATE":
        raise RuntimeError("공개 시연의 저장 입력 검증을 확인하지 못했습니다. API 연결을 중단합니다.")
    print("PUBLIC_INVALID_PUT_STATUS=422", flush=True)


# 이미 있는 호출 권한은 범위를 대조하고 다른 권한을 조용히 덮어쓰지 않는다.
def ensure_permission(lambdas, account, region, api_id, route_key, statement_id):
    method, path = route_key.split(" ", 1)
    source_arn = f"arn:aws:execute-api:{region}:{account}:{api_id}/*/{method}{path}"
    revision = None
    try:
        existing = lambdas.get_policy(FunctionName=NAME)
        revision = existing.get("RevisionId")
        policy = json.loads(existing["Policy"])
    except lambdas.exceptions.ResourceNotFoundException:
        policy = {"Statement": []}
    for statement in policy.get("Statement", []):
        if statement.get("Sid") != statement_id:
            continue
        condition = statement.get("Condition", {})
        if (
            statement.get("Effect") != "Allow"
            or statement.get("Action") != "lambda:InvokeFunction"
            or statement.get("Principal", {}).get("Service") != "apigateway.amazonaws.com"
            or condition.get("ArnLike", {}).get("AWS:SourceArn") != source_arn
            or condition.get("StringEquals", {}).get("AWS:SourceAccount") != account
        ):
            raise RuntimeError("기존 Lambda 호출 권한의 범위가 다릅니다.")
        return
    parameters = {
        "FunctionName": NAME,
        "StatementId": statement_id,
        "Action": "lambda:InvokeFunction",
        "Principal": "apigateway.amazonaws.com",
        "SourceAccount": account,
        "SourceArn": source_arn,
    }
    if revision:
        parameters["RevisionId"] = revision
    lambdas.add_permission(**parameters)


# 정확한 출처와 제한된 경로를 가진 별도 API를 연결한다.
def ensure_api(gateway, lambdas, existing, account, region, origin, function_arn):
    cors = {
        "AllowOrigins": [origin],
        "AllowMethods": ["GET", "PUT", "POST", "OPTIONS"],
        "AllowHeaders": ["Content-Type", "Authorization"],
        "AllowCredentials": False,
        "MaxAge": 300,
    }
    if existing:
        api = existing["api"]
        gateway.update_api(ApiId=api["ApiId"], CorsConfiguration=cors)
        integration_id = existing["integration_id"]
        routes = {route["RouteKey"] for route in existing["routes"]}
        stages = existing["stages"]
    else:
        api = gateway.create_api(Name=NAME, ProtocolType="HTTP", CorsConfiguration=cors, Tags=TAGS)
        integration_id, routes, stages = None, set(), []
    api_id = api["ApiId"]
    if integration_id is None:
        integration_id = gateway.create_integration(
            ApiId=api_id,
            IntegrationType="AWS_PROXY",
            IntegrationUri=function_arn,
            PayloadFormatVersion="2.0",
            TimeoutInMillis=12000,
        )["IntegrationId"]
    for route_key, statement_id in ROUTES.items():
        ensure_permission(lambdas, account, region, api_id, route_key, statement_id)
        if route_key not in routes:
            # 데이터 경로의 bearer 인증은 Lambda에서 매번 검증한다.
            gateway.create_route(ApiId=api_id, RouteKey=route_key, Target=f"integrations/{integration_id}", AuthorizationType="NONE")
    throttle = {"ThrottlingBurstLimit": 5, "ThrottlingRateLimit": 2.0}
    parameters = {
        "ApiId": api_id,
        "StageName": "$default",
        "AutoDeploy": True,
        "DefaultRouteSettings": throttle,
        "RouteSettings": {route_key: dict(throttle) for route_key in ROUTES},
    }
    if stages:
        gateway.update_stage(**parameters)
    else:
        gateway.create_stage(**parameters)
    return api["ApiEndpoint"]


# 조회만 하는 사전 점검과 승인 후 적용을 분리하며 기존 재고 API는 접근하지 않는다.
def deploy(account, region, box_id, origin, apply=False, rotate_password=False, public_demo=False):
    import boto3

    validate_options(account, region, box_id, origin)
    if rotate_password and not apply:
        raise RuntimeError("비밀번호 교체에는 --apply도 필요합니다.")
    archive = source_archive()
    session = boto3.Session(region_name=region)
    if session.client("sts").get_caller_identity()["Account"] != account:
        raise RuntimeError("현재 AWS 세션의 계정이 승인한 계정과 다릅니다.")
    role = find_school_role(session.client("iam"), account)
    dynamodb = session.client("dynamodb")
    lambdas = session.client("lambda")
    gateway = session.client("apigatewayv2")
    logs = session.client("logs")
    table = inspect_table(dynamodb, account)
    function = inspect_function(lambdas, account, box_id)
    function_arn = f"arn:aws:lambda:{region}:{account}:function:{NAME}"
    api = inspect_api(gateway, function_arn)
    logs_exist = inspect_logs(logs)
    print(f"PREFLIGHT_OK: account={account}, region={region}, box={box_id}, origin={origin}", flush=True)
    print("ROLE: 학교 제공 역할을 재사용하며 IAM 정책은 수정하지 않습니다.", flush=True)
    print(f"RESOURCES: table={'existing' if table else 'new'}, lambda={'existing' if function else 'new'}, api={'existing' if api else 'new'}", flush=True)
    if not apply:
        print("READ_ONLY: 변경하지 않았습니다. 검토 후 --apply로 적용하세요.", flush=True)
        return
    # 사용자 입력을 완료하기 전에는 AWS 자원을 만들거나 갱신하지 않는다.
    environment = build_environment(function, box_id, origin, rotate_password, public_demo)
    ensure_table(dynamodb, table, account)
    if not logs_exist:
        logs.create_log_group(logGroupName=LOG_GROUP, tags=TAGS)
    logs.put_retention_policy(logGroupName=LOG_GROUP, retentionInDays=7)
    function_arn = ensure_function(lambdas, function, role["Arn"], environment, archive)
    is_public = environment["Variables"]["MEDKIT_MEDICATION_PUBLIC_DEMO"] == "true"
    if is_public:
        verify_public_demo(lambdas, origin)
    else:
        verify_deny(lambdas, origin)
        verify_storage(lambdas, origin, environment)
    endpoint = ensure_api(gateway, lambdas, api, account, region, origin, function_arn)
    print(f"MEDICATION_API_URL={endpoint}", flush=True)
    print(f"TABLE={TABLE}\nBOX_ID={box_id}", flush=True)
    if is_public:
        print("DEPLOYED: 공개 시연 조회·입력 검증 확인. 목록을 누구나 조회·수정·삭제할 수 있습니다. 배포 중 데이터 항목은 변경하지 않았습니다.", flush=True)
    else:
        print("DEPLOYED: 인증과 실제 저장소 접근 확인. 최초 빈 항목만 조건부 생성하며 기존 항목·IAM·재고 API·Amplify 설정은 변경하지 않았습니다.", flush=True)


# 비밀번호를 인수나 파일로 받지 않고 명시적 적용 옵션을 제공한다.
def main():
    parser = argparse.ArgumentParser(description="공통 비밀번호 복용약 API 배포 및 조회 전용 사전 점검")
    parser.add_argument("--account", required=True)
    parser.add_argument("--region", default="ap-northeast-2")
    parser.add_argument("--box-id", required=True)
    parser.add_argument("--origin", required=True, help="끝의 / 없는 정확한 Amplify HTTPS origin")
    parser.add_argument("--apply", action="store_true", help="확인한 전용 AWS 자원 생성·갱신을 적용")
    parser.add_argument("--rotate-password", action="store_true", help="공통 비밀번호 교체와 기존 세션 만료")
    parser.add_argument("--public-demo", action="store_true", help="누구나 복용약을 조회·수정·삭제하는 공개 시연으로 명시 전환")
    args = parser.parse_args()
    try:
        deploy(args.account, args.region, args.box_id, args.origin, args.apply, args.rotate_password, args.public_demo)
    except (RuntimeError, KeyboardInterrupt) as error:
        message = str(error) if isinstance(error, RuntimeError) else "사용자가 배포를 중단했습니다."
        print(f"중단: {message}", file=sys.stderr)
        return 1
    except Exception as error:
        # AWS 예외의 요청·응답 원문에는 환경변수가 섞일 수 있어 오류 코드만 내보낸다.
        code = getattr(error, "response", {}).get("Error", {}).get("Code", "DEPLOYMENT_FAILED")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", str(code)):
            code = "DEPLOYMENT_FAILED"
        operation = getattr(error, "operation_name", "UnknownOperation")
        if not isinstance(operation, str) or not re.fullmatch(r"[A-Za-z0-9]{1,100}", operation):
            operation = "UnknownOperation"
        print(f"중단: AWS 배포 요청 실패 ({code}, {operation}). 비밀정보를 제외한 계정 권한과 자원 상태를 확인하세요.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
