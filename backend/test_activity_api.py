"""복용 확인·수량·초기 정보 입력을 합성 저장소로 검증한다."""

from unittest.mock import Mock
import pytest
from activity_api import confirm_activity, confirm_initial, list_activity, ApiError, DynamoStore


# 물리 반출 기록과 제품 제형이 있는 합성 저장소를 만든다.
def store():
    value = Mock()
    value.event.return_value = dict(pk='BOX#test', sk='OUT#e1', eventId='e1', packageId='p1',
        eventType='OUT', movementCertainty='ESTIMATED', medicineName='합성 시험약', unit='TABLET',
        occurredAt='2026-11-10T00:00:00Z', status='PENDING', quantity=None, recordedAt=None)
    value.package.return_value = {'first_registration_pending': True, 'inventory': {'stock_enabled': True, 'estimated_qty': 10}}
    value.form.return_value = 'TABLET'
    return value


# Y 응답은 입력한 수량만 전달하고 N 응답은 재고 차감 없이 이력에 남긴다.
def test_yes_and_no():
    config = dict(box='test', allow_estimated=True)
    for result, count in [('TAKEN', 2), ('NOT_TAKEN', None)]:
        value = store()
        response = confirm_activity(value, config, 'e1', {'result': result, 'quantity': count})
        assert response['event']['status'] == result
        assert value.confirm.call_args.args[-1] == ({'estimated_qty': 10} if count else None)


# 중복 확인·잘못된 수량은 차감 트랜잭션을 실행하지 않는다.
def test_reject_repeat_and_excess():
    config = dict(box='test', allow_estimated=True)
    value = store()
    with pytest.raises(ApiError):
        confirm_activity(value, config, 'e1', {'result': 'TAKEN', 'quantity': 11})
    value.confirm.assert_not_called()
    value.event.return_value.update(status='NOT_TAKEN', recordedAt='2026-11-10T01:00:00Z')
    with pytest.raises(ApiError):
        confirm_activity(value, config, 'e1', {'result': 'TAKEN', 'quantity': 1})
    value.confirm.assert_not_called()


# 사용자의 폐기 응답은 복용 수량 없이 저장하고 같은 반출에만 재고 상태를 바꾼다.
def test_disposal_is_atomic_and_scoped_to_active_out():
    config = dict(box='test', allow_estimated=True)
    value = store()
    value.confirm.side_effect = lambda *args: DynamoStore.confirm(value, *args)
    value.encode.side_effect = dict
    response = confirm_activity(value, config, 'e1', {'result': 'DISPOSED', 'quantity': None})
    assert response['event']['status'] == 'DISPOSED'
    assert response['event']['quantity'] is None
    writes = value.client.transact_write_items.call_args.kwargs['TransactItems']
    event, pack = writes[0]['Update'], writes[1]['Delete']
    assert event['ExpressionAttributeValues'][':result'] == 'DISPOSED'
    assert '#status = :pending' in event['ConditionExpression']
    assert 'active_out_event_id=:event' in pack['ConditionExpression']
    assert pack['Key']['pk'] == 'PACKAGE#p1'
    assert ':out' in pack['ExpressionAttributeValues']
    value.package.assert_not_called()
    with pytest.raises(ApiError):
        confirm_activity(value, config, 'e1', {'result': 'DISPOSED', 'quantity': 1})
    value.client.transact_write_items.assert_called_once()


# 새것 확인은 재고와 유통기한을 덮어쓰지 않는다.
def test_new_preserves_stock():
    value = store()
    confirm_initial(value, {'box': 'test'}, 'p1', {'condition': 'NEW', 'actualExpiry': None, 'remainingQty': None})
    assert '#inventory' not in value.initial.call_args.args[1]
    assert '#expiry' not in value.initial.call_args.args[1]


# 새 약의 허가기간은 최초 반입일을 기준으로 한 임시 날짜로만 저장한다.
def test_new_shelf_life_is_temporary():
    value = store()
    value.package.return_value.update(product_codes=['198700405'], first_stored_at='2026-01-31T00:00:00Z')
    value.inventory.get_item.return_value = {'Item': {'shelf_life': '제조일로부터 1 개월'}}
    confirm_initial(value, {'box': 'test'}, 'p1', {'condition': 'NEW', 'actualExpiry': None, 'remainingQty': None})
    assert value.initial.call_args.args[2][':expiry'] == {'temporary_management_date': '2026-02-28'}


# 비정제 약품의 잔여 알약 수 입력을 거부한다.
def test_other_form_has_no_quantity():
    value = store()
    value.form.return_value = 'OTHER'
    with pytest.raises(ApiError):
        confirm_initial(value, {'box': 'test'}, 'p1', {'condition': 'USED', 'actualExpiry': '2027-01-01', 'remainingQty': 2})


# 동일 품목, 다른 제품의 동일 성분, 삭제된 포장, 한국 날짜 경계를 한 조회에서 검증한다.
def test_activity_warns_for_same_day_confirmed_intakes():
    value = store()
    value.records.return_value = []
    template = value.event.return_value

    # 같은 보관함의 합성 반출을 상태와 발생 시간에 맞춰 만든다.
    def event(event_id, code, name, occurred, status='TAKEN', package_id=None):
        return dict(template, eventId=event_id, sk=f'OUT#{event_id}', medicineName=name,
                    packageId=package_id or f'p-{event_id}', productCode=code, occurredAt=occurred,
                    status=status, quantity=1 if status == 'TAKEN' else None,
                    recordedAt='2026-11-11T01:00:00Z' if status != 'PENDING' else None)

    value.events.return_value = [
        event('current', '100000001', '현재 약', '2026-11-11T01:00:00Z', 'PENDING'),
        event('same', '100000001', '이전에 복용한 같은 약', '2026-11-10T15:00:00Z'),
        event('shared', None, '폐기한 약의 복용 기록', '2026-11-10T16:00:00Z',
              package_id='test-100000002-123456abcdef'),
        event('different', '100000003', '다른 성분의 약', '2026-11-10T17:00:00Z'),
        event('yesterday', '100000001', '어제 복용한 약', '2026-11-10T14:59:59Z'),
        event('not-taken', '100000001', '복용하지 않은 약', '2026-11-10T18:00:00Z', 'NOT_TAKEN'),
        event('disposed', '100000001', '복용하지 않고 버린 약', '2026-11-10T19:00:00Z', 'DISPOSED'),
        event('pending', '100000001', '응답하지 않은 약', '2026-11-10T20:00:00Z', 'PENDING'),
        event('later', '100000001', '나중에 반출한 약', '2026-11-11T02:00:00Z'),
        event('unverified', '100000004', '성분 미확인 약', '2026-11-10T21:00:00Z'),
    ]
    value.medicine.side_effect = {
        '100000001': {'ingredients_status': 'SUCCEEDED', 'ingredients': [{'name': '성분 A'}]},
        '100000002': {'ingredients_status': 'SUCCEEDED', 'ingredients': [{'name': '성분a'}]},
        '100000003': {'ingredients_status': 'SUCCEEDED', 'ingredients': [{'name': '성분 A 염'}]},
        '100000004': {'ingredients_status': 'PARTIAL', 'ingredients': [{'name': '성분 A'}]},
    }.__getitem__

    result = list_activity(value, dict(box='test', allow_estimated=True))
    assert result['pending'][0]['duplicateWarnings'] == [
        {'medicineName': '폐기한 약의 복용 기록', 'ingredients': ['성분 A']},
        {'medicineName': '이전에 복용한 같은 약', 'ingredients': ['성분 A']},
    ]
    assert all('duplicateWarnings' not in item for item in result['history'])
    value.package.assert_not_called()
    assert value.medicine.call_count == 4


# 예전 포장의 품목코드를 조회하고 성분이 없어도 동일 품목 복용 기록을 알린다.
def test_activity_warns_for_same_product_without_ingredients():
    value = store()
    value.records.return_value = []
    value.package.return_value = {'product_codes': ['100000001']}
    value.medicine.return_value = None
    current = value.event.return_value
    previous = dict(current, eventId='old', sk='OUT#old', packageId='legacy-package',
                    status='TAKEN', quantity=1, recordedAt='2026-11-10T00:00:00Z')
    value.events.return_value = [dict(current, productCode='100000001'), previous]
    result = list_activity(value, dict(box='test', allow_estimated=True))
    assert result['pending'][0]['duplicateWarnings'] == [
        {'medicineName': current['medicineName'], 'ingredients': []},
    ]
    value.package.assert_called_once_with('test', 'legacy-package')
