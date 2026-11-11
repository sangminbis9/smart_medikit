"""반입·반출·24시간 추정의 순서와 재반입 취소를 검증한다."""

from datetime import datetime, timezone, timedelta
from inventory_events import transition, disposal_due


# 서로 연결된 합성 식별·이동 결과를 만든다.
def inputs(direction='IN', sequence=1):
    recognition = dict(session_id='s1', product_code='123456789', name='합성 시험약', form='TABLET')
    movement = dict(session_id='s1', event_id=f'e{sequence}', box_id='test', sequence=sequence,
                    captured_at='2026-11-10T00:00:00Z', movement={'candidate': {'direction': direction}})
    return recognition, movement


# 최초 반입은 수량을 만들지 않고 최초 확인 알림을 한 번 생성한다.
def test_initial_and_duplicate():
    recognition, movement = inputs()
    package, activity, notice = transition(None, recognition, movement)
    assert package['state'] == 'STORED' and package['first_registration_pending']
    assert package['inventory']['estimated_qty'] is None and activity is None
    assert notice['kind'] == 'INITIAL_REGISTRATION'
    assert transition(package, recognition, movement) is None


# 반출은 미확인 복용 이력만 만들고 수량은 확인 전 차감하지 않는다.
def test_out_and_return_cancel_disposal():
    package = transition(None, *inputs())[0]
    package, event, notice = transition(package, *inputs('OUT', 2))
    assert event['status'] == 'PENDING' and event['quantity'] is None
    assert notice['kind'] == 'OUT_CONFIRMATION'
    now = datetime(2026, 11, 11, tzinfo=timezone.utc)
    assert not disposal_due(package, event, now - timedelta(seconds=1))
    assert disposal_due(package, event, now)
    returned, _, notice = transition(package, *inputs('IN', 3))
    assert not disposal_due(returned, event, now)
    assert notice is None


# 식별되지 않은 약품과 보관 이력이 없는 반출은 재고를 만들지 않는다.
def test_unknown_and_unmatched_out():
    recognition, movement = inputs('OUT')
    assert transition(None, recognition, movement) is None
    recognition['product_code'] = None
    assert transition(None, recognition, movement) is None


# 과거 포장을 다른 보관함으로 옮긴 뒤 새 등록도 고유 키를 얻는다.
def test_new_registration_does_not_reuse_archived_key():
    recognition, first = inputs()
    original = transition(None, recognition, first)[0]
    replacement = transition(None, recognition, {**first, 'event_id': 'different'})[0]
    assert original['package_id'] != replacement['package_id']
    same = transition(None, recognition, first)[0]
    assert same['package_id'] == original['package_id']
