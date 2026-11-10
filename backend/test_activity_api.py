"""복용 확인·수량·초기 정보 입력을 합성 저장소로 검증한다."""

from unittest.mock import Mock
import pytest
from activity_api import confirm_activity, confirm_initial, ApiError


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


# 새것 확인은 재고와 유통기한을 덮어쓰지 않는다.
def test_new_preserves_stock():
    value = store()
    confirm_initial(value, {'box': 'test'}, 'p1', {'condition': 'NEW', 'actualExpiry': None, 'remainingQty': None})
    assert '#inventory' not in value.initial.call_args.args[1]
    assert '#expiry' not in value.initial.call_args.args[1]


# 비정제 약품의 잔여 알약 수 입력을 거부한다.
def test_other_form_has_no_quantity():
    value = store()
    value.form.return_value = 'OTHER'
    with pytest.raises(ApiError):
        confirm_initial(value, {'box': 'test'}, 'p1', {'condition': 'USED', 'actualExpiry': '2027-01-01', 'remainingQty': 2})
