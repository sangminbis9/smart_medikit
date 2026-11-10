"""브라우저 구독을 비공개로 보관하고 영속 알림을 Web Push로 전달한다."""

import base64
import hashlib
import json
import os
import re
import time
from urllib.parse import urlsplit
from activity_api import ApiError


# 허용 푸시 서비스와 암호화 키 형식을 검사해 임의 서버 전송을 차단한다.
def subscription(value):
    if not isinstance(value, dict) or not isinstance(value.get('endpoint'), str):
        raise ApiError(422, 'INVALID_SUBSCRIPTION', '알림 구독을 확인해 주세요.')
    parsed = urlsplit(value['endpoint'])
    host = parsed.hostname or ''
    if (len(value['endpoint']) > 2048 or parsed.scheme != 'https' or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.fragment
            or not (host in ('fcm.googleapis.com', 'updates.push.services.mozilla.com', 'web.push.apple.com')
                    or host.endswith('.notify.windows.com'))):
        raise ApiError(422, 'INVALID_ENDPOINT', '지원하지 않는 알림 서비스입니다.')
    keys = value.get('keys', {})
    for name, size in (('p256dh', 65), ('auth', 16)):
        encoded = keys.get(name)
        if not isinstance(encoded, str) or not re.fullmatch(r'[A-Za-z0-9_-]+={0,2}', encoded):
            raise ApiError(422, 'INVALID_KEY', '알림 구독을 확인해 주세요.')
        try:
            decoded = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4))
        except ValueError:
            raise ApiError(422, 'INVALID_KEY', '알림 구독을 확인해 주세요.') from None
        if len(decoded) != size or (name == 'p256dh' and decoded[0] != 4):
            raise ApiError(422, 'INVALID_KEY', '알림 구독을 확인해 주세요.')
    return {'endpoint': value['endpoint'], 'keys': {name: keys[name] for name in ('p256dh', 'auth')}}


# 구독 소유자가 알고 있는 암호화 정보를 조건으로 등록·해제를 처리한다.
def manage_subscription(store, box, body, remove=False):
    value = subscription(body.get('subscription'))
    sid = hashlib.sha256(value['endpoint'].encode()).hexdigest()
    key = {'pk': f'BOX#{box}', 'sk': f'SUB#{sid}'}
    try:
        if remove:
            store.activity.delete_item(Key=key, ConditionExpression='#value=:value',
                ExpressionAttributeNames={'#value': 'subscription'}, ExpressionAttributeValues={':value': value})
        else:
            store.activity.put_item(Item={**key, 'subscription': value, 'created_at': int(time.time())},
                ConditionExpression='attribute_not_exists(pk) OR #value=:value',
                ExpressionAttributeNames={'#value': 'subscription'}, ExpressionAttributeValues={':value': value})
    except Exception as error:
        if getattr(error, 'response', {}).get('Error', {}).get('Code') == 'ConditionalCheckFailedException':
            raise ApiError(409, 'SUBSCRIPTION_CHANGED', '알림 구독을 다시 확인해 주세요.') from None
        raise
    return {'removed': True} if remove else {'subscription_id': sid}


# 보관함 내부 알림 또는 구독만 페이지별로 조회한다.
def rows(table, box, prefix):
    start = None
    while True:
        query = dict(KeyConditionExpression='pk=:pk AND begins_with(sk,:prefix)',
                     ExpressionAttributeValues={':pk': f'BOX#{box}', ':prefix': prefix}, ConsistentRead=True)
        if start:
            query['ExclusiveStartKey'] = start
        response = table.query(**query)
        yield from response.get('Items', [])
        start = response.get('LastEvaluatedKey')
        if not start:
            break


# 리다이렉트 없이 허용한 푸시 목적지에만 암호화 페이로드를 전송한다.
def send_webpush(value, payload):
    import requests
    from pywebpush import webpush

    class NoRedirectSession(requests.Session):
        # 푸시 서버의 리다이렉트를 따라 구독 정보를 다른 곳에 보내지 않는다.
        def request(self, method, url, **kwargs):
            kwargs['allow_redirects'] = False
            return super().request(method, url, **kwargs)

    with NoRedirectSession() as session:
        webpush(subscription(value), data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=os.environ['MEDKIT_VAPID_PRIVATE_KEY'],
                vapid_claims={'sub': os.environ['MEDKIT_VAPID_SUBJECT']},
                ttl=3600, timeout=5, requests_session=session)


# 구독별 발송 성공을 저장해 재시도 시 이미 성공한 대상은 건너뛴다.
def deliver(store, box, send=send_webpush):
    subscriptions = list(rows(store.activity, box, 'SUB#'))
    for notice in rows(store.activity, box, 'NOTICE#'):
        if notice.get('delivery_status') == 'SENT':
            continue
        complete = True
        for saved in subscriptions:
            delivery_key = {'pk': f'BOX#{box}', 'sk': f"DELIVERY#{notice['notification_id']}#{saved['sk'][4:]}"}
            now = int(time.time())
            try:
                store.activity.update_item(Key=delivery_key,
                    UpdateExpression='SET lease_until=:lease, #status=:sending',
                    ConditionExpression='attribute_not_exists(pk) OR (#status<>:sent AND lease_until<:now)',
                    ExpressionAttributeNames={'#status': 'status'},
                    ExpressionAttributeValues={':lease': now + 120, ':sending': 'SENDING', ':sent': 'SENT', ':now': now})
            except Exception as error:
                if getattr(error, 'response', {}).get('Error', {}).get('Code') == 'ConditionalCheckFailedException':
                    prior = store.activity.get_item(Key=delivery_key, ConsistentRead=True).get('Item', {})
                    if prior.get('status') != 'SENT':
                        complete = False
                    continue
                raise
            payload = {key: notice[key] for key in ('notification_id', 'kind', 'medicine_name', 'event_id', 'package_id') if key in notice}
            try:
                send(saved['subscription'], payload)
                store.activity.update_item(Key=delivery_key, UpdateExpression='SET #status=:sent, sent_at=:at',
                    ExpressionAttributeNames={'#status': 'status'}, ExpressionAttributeValues={':sent': 'SENT', ':at': int(time.time())})
            except Exception as error:
                status = getattr(getattr(error, 'response', None), 'status_code', None)
                # 구독 주소·암호화 키·응답 본문 없이 전송 실패 유형만 기록한다.
                print(json.dumps({'event': 'WEB_PUSH_FAILED', 'error_type': type(error).__name__,
                                  'http_status': status if isinstance(status, int) else None}))
                if status in (404, 410):
                    store.activity.delete_item(Key={'pk': saved['pk'], 'sk': saved['sk']})
                else:
                    complete = False
        if complete:
            store.activity.update_item(Key={'pk': notice['pk'], 'sk': notice['sk']}, UpdateExpression='SET delivery_status=:sent', ExpressionAttributeValues={':sent': 'SENT'})


# EventBridge 예약으로 폐기 추정과 실패한 알림 전송을 재검사한다.
def lambda_handler(event, context):
    from activity_api import configuration, get_store
    from inventory_events import expire_outbound
    config, store = configuration(), get_store()
    expire_outbound(store, config['box'])
    deliver(store, config['box'])
    return {'ok': True}
