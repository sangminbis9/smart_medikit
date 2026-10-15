import { useState } from 'react';
import { Link } from 'react-router-dom';
import { timeLabel, type Alert } from './data';
import type { Medkit } from './useMedkit';
import { Badge, EmptyState, Note } from './ui';

// 주의정보의 종류와 검사 범위를 분리해 표시한다.
export default function Alerts({ kit }: { kit: Medkit }) {
  const [filter, setFilter] = useState('전체');
  const [selected, setSelected] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  // 알림 필터는 서버의 확인 상태를 변경하지 않는다.
  const alerts = kit.data.alerts.filter(function matches(alert) {
    return (
      filter === '전체' ||
      (filter === '주의정보' ? isRisk(alert) : !isRisk(alert))
    );
  });
  // 목록 필터 변경으로 선택이 사라지면 첫 번째 항목을 표시한다.
  const current =
    alerts.find(
      /* 선택된 알림을 현재 필터 결과에서 찾는다. */ function matches(alert) {
        return alert.alert_id === selected;
      },
    ) || alerts[0];
  // 내용 확인은 복약 확인과 별개이며 시연 메모리만 바꾼다.
  function acknowledge() {
    if (!kit.demo || !current) return;
    // 같은 논리 알림 ID만 확인하고 다른 반출 이벤트는 유지한다.
    const next = kit.data.alerts.map(function update(alert) {
      return alert.alert_id === current.alert_id
        ? { ...alert, ack_state: 'ACKNOWLEDGED' as const }
        : alert;
    });
    kit.changeDemo({ ...kit.data, alerts: next });
    setNotice('이 알림의 시연 확인 상태만 변경했어요. 서버에 저장되지 않아요.');
  }
  return (
    <div className="standard-page">
      <div className="page-heading">
        <div>
          <h1>알림</h1>
          <p>보관 약품에서 확인할 내용을 모았어요.</p>
        </div>
        <Link className="soft-button" to="/device">
          알림·설치 안내
        </Link>
      </div>
      <div className="chips section-gap">
        {['전체', '주의정보', '등록·기한 확인'].map(
          /* 알림 분류 필터를 표시한다. */ function chip(value) {
            // 선택한 분류의 알림 목록을 표시한다.
            function choose() {
              setFilter(value);
            }
            return (
              <button
                key={value}
                className={`chip ${filter === value ? 'active' : ''}`}
                onClick={choose}
                aria-pressed={filter === value}
              >
                {value}
              </button>
            );
          },
        )}
      </div>
      {!alerts.length ? (
        <EmptyState title="표시할 알림이 없어요">
          서버에서 받은 주의정보와 확인 요청이 여기에 표시돼요. 알림이 없다고
          약품의 안전성이 확인된 것은 아니에요.
        </EmptyState>
      ) : (
        <div className="alerts-grid">
          <div className="notice-list">
            {alerts.map(
              /* 알림의 종류와 확인 상태를 카드로 표시한다. */ function card(
                alert,
              ) {
                const tone =
                  alert.type === 'DUR_CONTRAINDICATION' ? 'red' : 'amber';
                // 목록에서 선택해도 확인 완료로 처리하지 않는다.
                function select() {
                  setSelected(alert.alert_id);
                }
                return (
                  <button
                    key={alert.alert_id}
                    className={`notice-card ${current?.alert_id === alert.alert_id ? `selected-${tone}` : ''}`}
                    onClick={select}
                    aria-pressed={current?.alert_id === alert.alert_id}
                  >
                    <span className="flex items-center justify-between gap-3">
                      <Badge tone={tone}>{category(alert)}</Badge>
                      <small>
                        {kit.demo ? '시연 예시' : timeLabel(alert.occurred_at)}
                      </small>
                    </span>
                    <strong>{alert.title}</strong>
                    <p>{alert.body}</p>
                    {alert.ack_state === 'ACKNOWLEDGED' && (
                      <Badge tone="green">내용 확인됨</Badge>
                    )}
                  </button>
                );
              },
            )}
          </div>
          {current && (
            <section className="notice-detail">
              <Badge
                tone={current.type === 'DUR_CONTRAINDICATION' ? 'red' : 'amber'}
              >
                {category(current)}
                {kit.demo ? ' / 예시' : ''}
              </Badge>
              <h2>{current.title}</h2>
              <p>{current.body}</p>
              {isRisk(current) && (
                <>
                  <p className="caption">
                    검사 범위:{' '}
                    {current.scope === 'DAILY_WITHDRAWAL'
                      ? '한국 날짜 기준 당일 반출 이력'
                      : '현재 보관 중인 약품'}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {current.package_ids.map(
                      /* 관련 포장으로 이동하는 링크를 표시한다. */ function tag(
                        id,
                      ) {
                        // 연결된 포장이 없으면 ID 대신 미확인 문구를 표시한다.
                        const item = kit.data.packages.find(
                          // 현재 목록에서 알림과 연결된 포장을 찾는다.
                          function linked(item) {
                            return item.package_id === id;
                          },
                        );
                        return (
                          <Badge key={id}>{item?.name || '약품 미확인'}</Badge>
                        );
                      },
                    )}
                  </div>
                  <Note
                    tone={
                      current.type === 'DUR_CONTRAINDICATION' ? 'red' : 'amber'
                    }
                  >
                    <strong>복용 여부와는 구분해요</strong>
                    <p>
                      실제 함께 복용했는지는 미확인 상태입니다. 복용 판단은 의사
                      또는 약사와 확인하세요.
                    </p>
                  </Note>
                  <h3>정보 근거</h3>
                  <p className="caption">
                    {kit.demo
                      ? '화면의 약품과 조합은 시연용 예시입니다. 실제 DUR 조회 결과가 아닙니다.'
                      : '서버가 제공한 잠재적 주의정보이며 의료적 안전성을 보장하지 않습니다.'}
                  </p>
                </>
              )}
              <div className="flex flex-col gap-3">
                <Link
                  className="secondary-button"
                  to={
                    current.package_ids[0]
                      ? `/packages/${current.package_ids[0]}`
                      : '/'
                  }
                >
                  보관 약품 보기
                </Link>
                <button
                  className="primary-button"
                  onClick={acknowledge}
                  disabled={!kit.demo || current.ack_state === 'ACKNOWLEDGED'}
                >
                  {current.ack_state === 'ACKNOWLEDGED'
                    ? '내용 확인됨'
                    : '내용 확인 · 시연'}
                </button>
              </div>
              {!kit.demo && (
                <p className="caption">
                  내용 확인 저장은 서버 연동 후 사용할 수 있어요.
                </p>
              )}
              {notice && (
                <p role="status" className="inline-status">
                  {notice}
                </p>
              )}
            </section>
          )}
        </div>
      )}
    </div>
  );
}

// 중복성분과 병용금기만 주의정보로 분류한다.
function isRisk(alert: Alert) {
  return (
    alert.type === 'DUPLICATE_INGREDIENT' ||
    alert.type === 'DUR_CONTRAINDICATION'
  );
}

// 주의 종류를 색상 외의 문구로도 구분한다.
function category(alert: Alert) {
  return {
    DUPLICATE_INGREDIENT: '동일성분 주의',
    DUR_CONTRAINDICATION: 'DUR 병용금기',
    REGISTRATION: '등록 확인',
    EXPIRY: '유효기간 확인',
    REVIEW: '인식 확인',
  }[alert.type];
}
