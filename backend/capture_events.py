"""신뢰된 S3 단계 객체와 Pi 상태 조회를 기존 OCR 결과에 연결한다."""

import json
import os
from decimal import Decimal
from urllib.parse import unquote_plus
from activity_api import get_store, identifier
from inventory_events import transition, persist_transition


# 같은 이벤트의 종료·이동·식별 결과를 모아 한 번만 재고에 반영한다.
def reconcile(store, box, event_id):
    key = {"pk": f"BOX#{box}", "sk": f"CAPTURE#{event_id}"}
    item = store.activity.get_item(Key=key, ConsistentRead=True).get("Item", {})
    closed = item.get("SESSION_CLOSED")
    movement = item.get("MOVEMENT_REQUESTED")
    if not closed:
        return "PROCESSING"
    if not closed.get("package_capture_ok"):
        return "COMPLETED" if closed.get("interior_capture_ok") else "REVIEW_REQUIRED"
    if not movement or not movement.get("movement", {}).get("candidate"):
        return "REVIEW_REQUIRED"
    raw = store.inventory.get_item(Key={"pk": f"RECOGNITION#{event_id}", "sk": "META"}, ConsistentRead=True).get("Item", {})
    if raw.get("processing_status") != "COMPLETED":
        return "REVIEW_REQUIRED" if raw.get("processing_status") in ("FAILED", "REVIEW_REQUIRED") else "PROCESSING"
    medicines = raw.get("medicines", [])
    if len(medicines) != 1:
        return "REVIEW_REQUIRED"
    recognition = dict(medicines[0], session_id=closed["session_id"])
    # 기존 코드에서 만든 같은 제품 포장이 있으면 중복 등록 대신 그 포장을 연결한다.
    from boto3.dynamodb.conditions import Attr
    packages, start = [], None
    while True:
        request = {"FilterExpression": Attr('box_id').eq(box) & Attr('product_codes').contains(recognition['product_code']) & Attr('pk').begins_with('PACKAGE#'), "ConsistentRead": True}
        if start:
            request['ExclusiveStartKey'] = start
        response = store.inventory.scan(**request)
        packages.extend(response.get('Items', []))
        start = response.get('LastEvaluatedKey')
        if not start:
            break
    if len(packages) > 1:
        return "REVIEW_REQUIRED"
    previous = packages[0] if packages else None
    if previous and previous.get('last_event_id') == event_id:
        return "COMPLETED"
    result = transition(previous, recognition, movement)
    if result is None:
        return "REVIEW_REQUIRED"
    persist_transition(store, previous, result)
    if os.environ.get('MEDKIT_NOTIFICATION_FUNCTION'):
        import boto3
        try:
            boto3.client('lambda').invoke(FunctionName=os.environ['MEDKIT_NOTIFICATION_FUNCTION'], InvocationType='Event', Payload=b'{}')
        except Exception:
            # 알림은 이미 영속 저장되어 예약 작업에서 재시도한다.
            pass
    return "COMPLETED"


# 사전에 지정된 비공개 버킷의 단계 JSON만 받아 저장한다.
def receive_s3(event, store, s3, box, bucket, prefix):
    for record in event.get('Records', []):
        if record.get('eventSource') != 'aws:s3':
            continue
        obj = record.get('s3', {})
        key = unquote_plus(obj.get('object', {}).get('key', ''))
        if obj.get('bucket', {}).get('name') != bucket or not key.startswith(prefix):
            raise ValueError('UNEXPECTED_SOURCE')
        suffix = key.rsplit('/', 1)[-1]
        stages = {'ocr-ready.json': 'OCR_REQUESTED', 'movement-ready.json': 'MOVEMENT_REQUESTED', 'closed-ready.json': 'SESSION_CLOSED'}
        if suffix not in stages:
            continue
        response = s3.get_object(Bucket=bucket, Key=key)
        try:
            data = response['Body'].read(32769)
        finally:
            response['Body'].close()
        if len(data) > 32768:
            raise ValueError('PAYLOAD_TOO_LARGE')
        payload = json.loads(data, parse_float=Decimal)
        event_id = identifier(payload['event_id'])
        session = identifier(payload['session_id'])
        stage = stages[suffix]
        if payload.get('box_id') != box or payload.get('stage') != stage or key != f'{prefix}{session}/{suffix}':
            raise ValueError('EVENT_MISMATCH')
        if type(payload.get('sequence')) is not int or payload['sequence'] < 1:
            raise ValueError('INVALID_SEQUENCE')
        # 같은 단계의 재전송은 내용을 바꾸지 않고 이벤트 식별자 충돌을 거부한다.
        store.activity.update_item(Key={'pk': f'BOX#{box}', 'sk': f'CAPTURE#{event_id}'},
            UpdateExpression='SET #stage = if_not_exists(#stage, :payload), session_id=:session',
            ConditionExpression='(attribute_not_exists(session_id) OR session_id=:session) AND (attribute_not_exists(#stage) OR #stage=:payload)',
            ExpressionAttributeNames={'#stage': stage}, ExpressionAttributeValues={':payload': payload, ':session': session})
        if stage == 'SESSION_CLOSED':
            reconcile(store, box, event_id)


# S3 또는 IAM 인증된 Pi 상태 조회만 처리하며 공개 쓰기 API와 분리한다.
def lambda_handler(event, context):
    import boto3
    box = identifier(os.environ['MEDKIT_BOX_ID'])
    store = get_store()
    if 'Records' in event:
        receive_s3(event, store, boto3.client('s3'), box, os.environ['MEDKIT_CAPTURE_BUCKET'], os.environ['MEDKIT_CAPTURE_PREFIX'].rstrip('/') + '/')
        return {'ok': True}
    iam = event.get('requestContext', {}).get('authorizer', {}).get('iam', {})
    if not iam.get('userArn'):
        return {'statusCode': 403, 'body': '{}'}
    event_id = identifier(event.get('pathParameters', {}).get('event_id'))
    status = reconcile(store, box, event_id)
    return {'statusCode': 200, 'headers': {'Cache-Control': 'no-store', 'Content-Type': 'application/json'}, 'body': json.dumps({'processing_status': status})}
