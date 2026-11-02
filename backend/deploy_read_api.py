"""팀이 승인한 공개 시연 조회 API만 생성하고 기존 OCR·테이블·API를 보존한다."""

import argparse
import io
import json
from pathlib import Path
import time
import zipfile

REGION = "ap-northeast-2"
NAME = "smart-medikit-read-api"
TABLE = "inha-capstone-02-medkit-data"
BOX = "inha-capstone-02-medkit"
TAGS = {"ManagedBy": NAME}


# 다른 작업자가 만든 동명 리소스에 덮어쓰기를 시도하지 않는다.
def require_owned(tags):
    if tags.get("ManagedBy") != NAME:
        raise RuntimeError("동명 리소스의 소유 태그가 달라 배포를 중단합니다.")


# 별도 승인을 받은 학교 제공 역할만 읽어서 사용하며 정책을 수정하지 않는다.
def school_role(iam, account):
    role = iam.get_role(RoleName="SafeRole-inha-capstone-02")["Role"]
    if role["Arn"] != f"arn:aws:iam::{account}:role/SafeRole-inha-capstone-02":
        raise RuntimeError("학교 제공 역할의 계정이나 이름이 다릅니다.")
    trusted = False
    for statement in role["AssumeRolePolicyDocument"].get("Statement", []):
        services = statement.get("Principal", {}).get("Service", [])
        if isinstance(services, str):
            services = [services]
        if statement.get("Effect") == "Allow" and "lambda.amazonaws.com" in services:
            trusted = True
    if not trusted:
        raise RuntimeError("학교 역할이 Lambda 실행을 신뢰하지 않습니다.")
    return role


# 승인된 계정·앱을 검증한 뒤 선택한 실행 역할 방식으로 별도 조회 API를 배포한다.
def deploy(account, app_id, use_school_role=False):
    import boto3
    from botocore.exceptions import ClientError

    session = boto3.Session(region_name=REGION)
    if session.client("sts").get_caller_identity()["Account"] != account:
        raise RuntimeError("승인된 AWS 계정과 현재 계정이 다릅니다.")
    amplify = session.client("amplify")
    app = amplify.get_app(appId=app_id)["app"]
    if app["name"] != "smart-medikit-web":
        raise RuntimeError("승인된 Amplify 앱 이름과 다릅니다.")
    origin = f"https://main.{app['defaultDomain']}"
    # 키 구조만 확인하며 기존 테이블과 레코드는 수정하지 않는다.
    table = session.client("dynamodb").describe_table(TableName=TABLE)["Table"]
    if table["KeySchema"] != [{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}]:
        raise RuntimeError("확인한 DynamoDB 키 계약과 다릅니다.")

    iam = session.client("iam")
    role_name = f"{NAME}-role"
    if use_school_role:
        role = school_role(iam, account)
    else:
        try:
            role = iam.get_role(RoleName=role_name)["Role"]
            require_owned({tag["Key"]: tag["Value"] for tag in role.get("Tags", [])})
        except iam.exceptions.NoSuchEntityException:
            role = iam.create_role(RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]}),
                Tags=[{"Key": "ManagedBy", "Value": NAME}])["Role"]
    log_group = f"/aws/lambda/{NAME}"
    logs = session.client("logs")
    try:
        logs.create_log_group(logGroupName=log_group, tags=TAGS)
    except logs.exceptions.ResourceAlreadyExistsException:
        require_owned(logs.list_tags_log_group(logGroupName=log_group).get("tags", {}))
    logs.put_retention_policy(logGroupName=log_group, retentionInDays=7)
    if use_school_role:
        print("ROLE_READY: existing school role; IAM permissions not narrowed by this deployment", flush=True)
    else:
        iam.put_role_policy(RoleName=role_name, PolicyName="ReadMedkitCatalog", PolicyDocument=json.dumps({
            "Version": "2012-10-17", "Statement": [
                {"Effect": "Allow", "Action": "dynamodb:Scan", "Resource": table["TableArn"]},
                {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"], "Resource": f"arn:aws:logs:{REGION}:{account}:log-group:{log_group}:*"},
            ]}))
        print("ROLE_READY: Scan only on the approved table", flush=True)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(Path(__file__).with_name("read_api.py"), "read_api.py")
    lambdas = session.client("lambda")
    environment = {"Variables": {"MEDKIT_BOX_ID": BOX, "MEDKIT_DEMO_PUBLIC_READ": "true"}}
    try:
        function = lambdas.get_function(FunctionName=NAME)
        require_owned(function.get("Tags", {}))
        lambdas.update_function_code(FunctionName=NAME, ZipFile=buffer.getvalue())
        lambdas.get_waiter("function_updated_v2").wait(FunctionName=NAME)
        lambdas.update_function_configuration(FunctionName=NAME, Role=role["Arn"], Runtime="python3.12", Handler="read_api.lambda_handler", Timeout=10, MemorySize=128, Environment=environment)
        lambdas.get_waiter("function_updated_v2").wait(FunctionName=NAME)
    except lambdas.exceptions.ResourceNotFoundException:
        # IAM 전파 지연만 한정 재시도하며 권한 거부를 우회하지 않는다.
        for attempt in range(6):
            try:
                lambdas.create_function(FunctionName=NAME, Runtime="python3.12", Role=role["Arn"], Handler="read_api.lambda_handler", Code={"ZipFile": buffer.getvalue()}, Timeout=10, MemorySize=128, Environment=environment, Tags=TAGS)
                break
            except ClientError as error:
                if error.response["Error"]["Code"] != "InvalidParameterValueException" or attempt == 5:
                    raise
                time.sleep(5)
        lambdas.get_waiter("function_active_v2").wait(FunctionName=NAME)
    function_arn = lambdas.get_function_configuration(FunctionName=NAME)["FunctionArn"]
    # 낮은 학교 계정 할당량에서는 AWS의 미예약 100개 유지 규칙을 침해하지 않는다.
    available = lambdas.get_account_settings()["AccountLimit"].get("UnreservedConcurrentExecutions", 0)
    if available >= 102:
        lambdas.put_function_concurrency(FunctionName=NAME, ReservedConcurrentExecutions=2)
        print("CONCURRENCY: reserved 2", flush=True)
    else:
        print("CONCURRENCY: account limit prevents reservation; API throttle enabled", flush=True)
    verification = lambdas.invoke(FunctionName=NAME, Payload=json.dumps({"rawPath": "/inventory", "queryStringParameters": {"box_id": BOX}, "requestContext": {"http": {"method": "GET"}}}).encode())
    response = json.load(verification["Payload"])
    if verification.get("FunctionError") or response.get("statusCode") != 200:
        raise RuntimeError("Lambda 실제 테이블 조회 검증 실패: 웹 연결 전 중단합니다.")
    body = json.loads(response["body"])
    print(f"LAMBDA_VERIFIED: catalog_count={len(body['catalog_medicines'])}, inventory_status={body['inventory_status']}", flush=True)

    gateway = session.client("apigatewayv2")
    apis = []
    for page in gateway.get_paginator("get_apis").paginate():
        apis.extend(page.get("Items", []))
    matching = [api for api in apis if api["Name"] == NAME]
    if len(matching) > 1:
        raise RuntimeError("동명 API가 여럿이므로 자동 선택하지 않습니다.")
    cors = {"AllowOrigins": [origin], "AllowMethods": ["GET", "OPTIONS"], "AllowHeaders": ["Accept"], "AllowCredentials": True, "MaxAge": 300}
    if matching:
        api = matching[0]
        require_owned(api.get("Tags", {}))
        gateway.update_api(ApiId=api["ApiId"], CorsConfiguration=cors)
    else:
        api = gateway.create_api(Name=NAME, ProtocolType="HTTP", CorsConfiguration=cors, Tags=TAGS)
    api_id = api["ApiId"]
    integrations = gateway.get_integrations(ApiId=api_id).get("Items", [])
    if integrations:
        if len(integrations) != 1 or integrations[0].get("IntegrationUri") != function_arn:
            raise RuntimeError("기존 API 통합이 승인한 Lambda와 다릅니다.")
        integration_id = integrations[0]["IntegrationId"]
    else:
        integration_id = gateway.create_integration(ApiId=api_id, IntegrationType="AWS_PROXY", IntegrationUri=function_arn, PayloadFormatVersion="2.0", TimeoutInMillis=12000)["IntegrationId"]
    routes = {route["RouteKey"] for route in gateway.get_routes(ApiId=api_id).get("Items", [])}
    if routes - {"GET /inventory", "GET /alerts"}:
        raise RuntimeError("승인 범위 외 API 경로가 있어 중단합니다.")
    for path in ("inventory", "alerts"):
        if f"GET /{path}" not in routes:
            gateway.create_route(ApiId=api_id, RouteKey=f"GET /{path}", Target=f"integrations/{integration_id}", AuthorizationType="NONE")
        try:
            lambdas.add_permission(FunctionName=NAME, StatementId=f"ApiRead{path}", Action="lambda:InvokeFunction", Principal="apigateway.amazonaws.com", SourceAccount=account, SourceArn=f"arn:aws:execute-api:{REGION}:{account}:{api_id}/*/GET/{path}")
        except lambdas.exceptions.ResourceConflictException:
            pass
    stage_settings = {"ThrottlingBurstLimit": 3, "ThrottlingRateLimit": 1.0}
    stages = gateway.get_stages(ApiId=api_id).get("Items", [])
    if stages:
        gateway.update_stage(ApiId=api_id, StageName="$default", AutoDeploy=True, DefaultRouteSettings=stage_settings)
    else:
        gateway.create_stage(ApiId=api_id, StageName="$default", AutoDeploy=True, DefaultRouteSettings=stage_settings)
    endpoint = api["ApiEndpoint"]

    # 기존 앱·브랜치 변수와 SPA 규칙을 보존하고 새 조회 주소와 가이드 경로만 추가한다.
    branch = amplify.get_branch(appId=app_id, branchName="main")["branch"]
    variables = branch.get("environmentVariables", {})
    variables.update({"VITE_API_URL": endpoint, "VITE_BOX_ID": BOX})
    amplify.update_branch(appId=app_id, branchName="main", environmentVariables=variables)
    rules = app.get("customRules", [])
    sources = {rule["source"] for rule in rules}
    for source in ("/care", "/care/<*>"):
        if source not in sources:
            rules.append({"source": source, "target": "/index.html", "status": "200"})
    amplify.update_app(appId=app_id, customRules=rules)
    print(f"API_URL={endpoint}\nBOX_ID={BOX}\nAMPLIFY_CONFIGURED: rebuild main to activate", flush=True)


# 계정·앱과 공개 시연 승인 플래그를 명시해야 배포를 시작한다.
def main():
    parser = argparse.ArgumentParser(description="승인된 공개 시연 조회 API 배포")
    parser.add_argument("--account", required=True)
    parser.add_argument("--app", required=True)
    parser.add_argument("--apply-public-demo", action="store_true")
    parser.add_argument("--use-school-role", action="store_true", help="학교 역할의 넓은 권한 가능성을 별도 승인한 경우에만 사용")
    args = parser.parse_args()
    if not args.apply_public_demo:
        parser.error("팀 승인 후 --apply-public-demo를 지정하세요.")
    deploy(args.account, args.app, args.use_school_role)


if __name__ == "__main__":
    main()
