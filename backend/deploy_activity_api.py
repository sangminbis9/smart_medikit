"""승인된 반출 이력·촬영 단계·Web Push API를 학교 역할로 배포한다."""

import argparse
import base64
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ACCOUNT = '842544789367'
REGION = 'ap-northeast-2'
BOX = 'inha-capstone-02-medkit'
ORIGIN = 'https://main.d3t4nvt3phq6xc.amplifyapp.com'
TABLE = 'inha-capstone-02-medkit-activity'
BUCKET = 'inha-capstone-02-s3'
PREFIX = 'medkit-sessions/'
ROLE = f'arn:aws:iam::{ACCOUNT}:role/SafeRole-inha-capstone-02'
TAGS = {'username': 'inha-capstone-02', 'group': 'inha-capstone'}
FUNCTIONS = {'smart-medikit-activity-api': 'activity_api.lambda_handler',
             'smart-medikit-capture-events': 'capture_events.lambda_handler',
             'smart-medikit-notifications': 'notifications.lambda_handler'}


# 지정한 소스와 Linux Lambda 의존성만 압축하며 로컬 환경 파일을 포함하지 않는다.
def package_code():
    with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as wheels:
        # 순수 Python 의존성의 공식 배포본을 플랫폼 독립 wheel로 준비한다.
        subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--quiet', '--no-deps', '--wheel-dir', wheels, 'http-ece==1.2.1'], check=True)
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--quiet', '--target', folder,
                        '--find-links', wheels,
                        '--platform', 'manylinux2014_x86_64', '--python-version', '3.12', '--only-binary=:all:',
                        '-r', str(Path(__file__).with_name('requirements-activity.txt'))], check=True)
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
            for path in Path(folder).rglob('*'):
                if path.is_file() and '__pycache__' not in path.parts:
                    output.write(path, path.relative_to(folder))
            for name in ('activity_api.py', 'capture_events.py', 'inventory_events.py', 'notifications.py'):
                output.write(Path(__file__).with_name(name), name)
        return archive.getvalue()


# 기존 VAPID 비밀키는 보존하고 최초 배포 시 서버 안에서만 생성한다.
def vapid_keys(client):
    try:
        values = client.get_function_configuration(FunctionName='smart-medikit-notifications').get('Environment', {}).get('Variables', {})
        if values.get('MEDKIT_VAPID_PRIVATE_KEY') and values.get('MEDKIT_VAPID_PUBLIC_KEY'):
            return values['MEDKIT_VAPID_PRIVATE_KEY'], values['MEDKIT_VAPID_PUBLIC_KEY']
    except client.exceptions.ResourceNotFoundException:
        pass
    pem = subprocess.run(['openssl', 'ecparam', '-name', 'prime256v1', '-genkey', '-noout'], check=True, capture_output=True).stdout
    private = subprocess.run(['openssl', 'ec', '-outform', 'DER'], input=pem, check=True, capture_output=True).stdout
    public_der = subprocess.run(['openssl', 'ec', '-pubout', '-outform', 'DER', '-conv_form', 'uncompressed'], input=pem, check=True, capture_output=True).stdout
    public = public_der[-65:]
    if len(public) != 65 or public[0] != 4:
        raise RuntimeError('VAPID 공개키 생성 실패')
    return base64.urlsafe_b64encode(private).decode().rstrip('='), base64.urlsafe_b64encode(public).decode().rstrip('=')


# 이미 존재하는 동일 명칭의 Lambda 호출 권한은 중복 추가하지 않는다.
def permission(client, name, sid, principal, source):
    try:
        client.add_permission(FunctionName=name, StatementId=sid, Action='lambda:InvokeFunction', Principal=principal, SourceArn=source, SourceAccount=ACCOUNT)
    except client.exceptions.ResourceConflictException:
        pass


# 실제 계정과 학교 역할만 사용하며 기존 IAM 정책이나 데이터를 바꾸지 않는다.
def deploy():
    import boto3
    session = boto3.Session(region_name=REGION)
    if session.client('sts').get_caller_identity()['Account'] != ACCOUNT:
        raise RuntimeError('예정된 학교 계정이 아닙니다.')
    dynamo, client = session.client('dynamodb'), session.client('lambda')
    try:
        description = dynamo.describe_table(TableName=TABLE)['Table']
        if description['KeySchema'] != [{'AttributeName': 'pk', 'KeyType': 'HASH'}, {'AttributeName': 'sk', 'KeyType': 'RANGE'}]:
            raise RuntimeError('기존 이력 테이블의 키 형식이 다릅니다.')
    except dynamo.exceptions.ResourceNotFoundException:
        dynamo.create_table(TableName=TABLE, KeySchema=[{'AttributeName': 'pk', 'KeyType': 'HASH'}, {'AttributeName': 'sk', 'KeyType': 'RANGE'}],
            AttributeDefinitions=[{'AttributeName': name, 'AttributeType': 'S'} for name in ('pk', 'sk')], BillingMode='PAY_PER_REQUEST', Tags=[{'Key': key, 'Value': value} for key, value in TAGS.items()])
        dynamo.get_waiter('table_exists').wait(TableName=TABLE)
    code = package_code()
    private, public = vapid_keys(client)
    common = {'MEDKIT_BOX_ID': BOX, 'MEDKIT_ALLOWED_ORIGIN': ORIGIN, 'MEDKIT_ACTIVITY_TABLE': TABLE,
              'MEDKIT_INVENTORY_TABLE': 'inha-capstone-02-medkit-data', 'MEDKIT_ACTIVITY_PUBLIC_DEMO': 'true',
              'MEDKIT_ALLOW_ESTIMATED_MOVEMENT': 'true', 'MEDKIT_VAPID_PUBLIC_KEY': public,
              'MEDKIT_CAPTURE_BUCKET': BUCKET, 'MEDKIT_CAPTURE_PREFIX': PREFIX,
              'MEDKIT_NOTIFICATION_FUNCTION': 'smart-medikit-notifications'}
    for name, handler in FUNCTIONS.items():
        values = dict(common)
        if name == 'smart-medikit-notifications':
            values.update(MEDKIT_VAPID_PRIVATE_KEY=private, MEDKIT_VAPID_SUBJECT=ORIGIN)
        try:
            previous = client.get_function_configuration(FunctionName=name)
            if previous['Role'] != ROLE:
                raise RuntimeError('기존 함수의 역할이 예상과 다릅니다.')
            client.update_function_code(FunctionName=name, ZipFile=code)
            client.get_waiter('function_updated_v2').wait(FunctionName=name)
            client.update_function_configuration(FunctionName=name, Handler=handler, Environment={'Variables': {**previous.get('Environment', {}).get('Variables', {}), **values}}, Timeout=60, MemorySize=256)
            client.get_waiter('function_updated_v2').wait(FunctionName=name)
        except client.exceptions.ResourceNotFoundException:
            client.create_function(FunctionName=name, Runtime='python3.12', Role=ROLE, Handler=handler,
                Code={'ZipFile': code}, Environment={'Variables': values}, Timeout=60, MemorySize=256, Tags=TAGS)
            client.get_waiter('function_active_v2').wait(FunctionName=name)
    api = session.client('apigatewayv2')
    existing = [item for page in api.get_paginator('get_apis').paginate() for item in page.get('Items', []) if item['Name'] == 'smart-medikit-activity-api']
    if len(existing) > 1:
        raise RuntimeError('동일 명칭 API가 여러 개입니다.')
    gateway = existing[0] if existing else api.create_api(Name='smart-medikit-activity-api', ProtocolType='HTTP', Tags=TAGS,
        CorsConfiguration={'AllowOrigins': [ORIGIN], 'AllowMethods': ['GET', 'POST', 'OPTIONS'], 'AllowHeaders': ['content-type']})
    api_id = gateway['ApiId']
    routes = api.get_routes(ApiId=api_id).get('Items', [])
    for name in ('smart-medikit-activity-api', 'smart-medikit-capture-events'):
        arn = f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:{name}'
        integration = api.create_integration(ApiId=api_id, IntegrationType='AWS_PROXY', IntegrationUri=arn, PayloadFormatVersion='2.0')['IntegrationId']
        keys = ['GET /capture-events/{event_id}'] if 'capture' in name else ['GET /activity', 'POST /activity/{eventId}/confirm', 'POST /packages/{packageId}/initial', 'GET /push/config', 'POST /push/subscriptions', 'POST /push/subscriptions/remove']
        for key in keys:
            options = dict(ApiId=api_id, RouteKey=key, Target=f'integrations/{integration}', AuthorizationType='AWS_IAM' if 'capture' in name else 'NONE')
            prior = next((item for item in routes if item['RouteKey'] == key), None)
            if prior:
                api.update_route(RouteId=prior['RouteId'], **options)
            else:
                api.create_route(**options)
        permission(client, name, 'activity-gateway', 'apigateway.amazonaws.com', f'arn:aws:execute-api:{REGION}:{ACCOUNT}:{api_id}/*')
    if not any(item['StageName'] == '$default' for item in api.get_stages(ApiId=api_id).get('Items', [])):
        api.create_stage(ApiId=api_id, StageName='$default', AutoDeploy=True, DefaultRouteSettings={'ThrottlingBurstLimit': 10, 'ThrottlingRateLimit': 5.0})
    s3 = session.client('s3')
    config = s3.get_bucket_notification_configuration(Bucket=BUCKET)
    config.pop('ResponseMetadata', None)
    notifications = config.setdefault('LambdaFunctionConfigurations', [])
    arn = f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:smart-medikit-capture-events'
    permission(client, 'smart-medikit-capture-events', 'activity-s3', 's3.amazonaws.com', f'arn:aws:s3:::{BUCKET}')
    entry = {'Id': 'medkit-capture-stages', 'LambdaFunctionArn': arn, 'Events': ['s3:ObjectCreated:*'],
             'Filter': {'Key': {'FilterRules': [{'Name': 'prefix', 'Value': PREFIX}, {'Name': 'suffix', 'Value': '.json'}]}}}
    config['LambdaFunctionConfigurations'] = [item for item in notifications if item.get('Id') != entry['Id']] + [entry]
    s3.put_bucket_notification_configuration(Bucket=BUCKET, NotificationConfiguration=config)
    scheduler = session.client('scheduler')
    schedule = dict(Name='smart-medikit-activity-maintenance', ScheduleExpression='rate(1 minute)', FlexibleTimeWindow={'Mode': 'OFF'},
                    Target={'Arn': f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:smart-medikit-notifications', 'RoleArn': ROLE, 'Input': '{}'}, State='ENABLED')
    try:
        scheduler.get_schedule(Name=schedule['Name'])
        scheduler.update_schedule(**schedule)
    except scheduler.exceptions.ResourceNotFoundException:
        scheduler.create_schedule(**schedule)
    # 읽기 API도 신규 확인 필드를 반환하도록 소스만 갱신한다.
    read_code = io.BytesIO()
    with zipfile.ZipFile(read_code, 'w', zipfile.ZIP_DEFLATED) as output:
        for name in ('read_api.py', 'product_document.py'):
            output.write(Path(__file__).with_name(name), name)
    client.update_function_code(FunctionName='smart-medikit-read-api', ZipFile=read_code.getvalue())
    amplify = session.client('amplify')
    branch = amplify.get_branch(appId='d3t4nvt3phq6xc', branchName='main')['branch']
    amplify.update_branch(appId='d3t4nvt3phq6xc', branchName='main', environmentVariables={**branch.get('environmentVariables', {}), 'VITE_ACTIVITY_API_URL': gateway['ApiEndpoint']})
    amplify.start_job(appId='d3t4nvt3phq6xc', branchName='main', jobType='RELEASE')
    print(json.dumps({'activity_api': gateway['ApiEndpoint'], 'capture_prefix': PREFIX, 'box_id': BOX}))


# 기본 실행은 계획만 출력하며 명시적인 적용 인자가 있어야 변경한다.
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not args.apply:
        print(json.dumps({'table': TABLE, 'functions': FUNCTIONS, 'capture_prefix': PREFIX, 'schedule': 'rate(1 minute)', 'origin': ORIGIN}))
        return
    deploy()


if __name__ == '__main__':
    main()
