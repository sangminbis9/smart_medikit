import { useState, type FormEvent } from 'react';
import { dayKey, timeLabel, type Dose } from './data';
import type { Medkit } from './useMedkit';
import { Badge, Dialog, EmptyState, Icon, Note } from './ui';

const labels: Record<Dose['result'], string> = {
  SCHEDULED: '예정',
  ESTIMATED: '복용 추정',
  CONFIRMED: '사용자 확인',
  NOT_TAKEN: '미복용',
  DEFERRED: '확인 보류',
  UNCONFIRMED: '복용 미확인',
};

// 한국 날짜 기준 달력과 확인 근거가 다른 복약 기록을 보여준다.
export default function Schedule({ kit }: { kit: Medkit }) {
  const [day, setDay] = useState(dayKey);
  const [month, setMonth] = useState(day.slice(0, 7));
  const [editing, setEditing] = useState<Dose | null>(null);
  const [result, setResult] = useState<Dose['result']>('CONFIRMED');
  const [notice, setNotice] = useState('');
  // 선택한 날짜의 일정만 추린다.
  const doses = kit.data.doses.filter(function onDay(dose) {
    return dayKey(dose.scheduled_at) === day;
  });
  // 원본 시간대가 달라도 실제 시각 순서로 정렬한다.
  doses.sort(function byTime(a, b) {
    return Date.parse(a.scheduled_at) - Date.parse(b.scheduled_at);
  });
  let confirmed = 0;
  let estimated = 0;
  for (const dose of doses) {
    if (dose.result === 'CONFIRMED') confirmed++;
    if (dose.result === 'ESTIMATED') estimated++;
  }
  const [year, monthNumber] = month.split('-').map(Number);
  const start = new Date(Date.UTC(year, monthNumber - 1, 1));
  const offset = (start.getUTCDay() + 6) % 7;
  const days: string[] = [];
  const weeks = Math.ceil(
    (offset + new Date(Date.UTC(year, monthNumber, 0)).getUTCDate()) / 7,
  );
  for (let i = 0; i < weeks * 7; i++)
    days.push(
      new Date(Date.UTC(year, monthNumber - 1, i - offset + 1))
        .toISOString()
        .slice(0, 10),
    );

  // 월 이동은 복약 데이터를 만들거나 바꾸지 않는다.
  function moveMonth(amount: number) {
    setMonth(
      new Date(Date.UTC(year, monthNumber - 1 + amount, 1))
        .toISOString()
        .slice(0, 7),
    );
  }
  // 이전 달을 연다.
  function previous() {
    moveMonth(-1);
  }
  // 다음 달을 연다.
  function next() {
    moveMonth(1);
  }
  // 오늘 날짜로 달력과 선택을 돌린다.
  function today() {
    const value = dayKey();
    setDay(value);
    setMonth(value.slice(0, 7));
  }
  // 복약 확인창을 닫는다.
  function close() {
    setEditing(null);
  }
  // 확인 결과는 시연 화면에서만 바꾸며 실제 재고 차감을 실행하지 않는다.
  function save(event: FormEvent) {
    event.preventDefault();
    if (!editing || !kit.demo) return;
    // 시연에서도 동일 회차의 표시 상태만 교체한다.
    const next = kit.data.doses.map(function update(dose) {
      return dose.dose_id === editing.dose_id ? { ...dose, result } : dose;
    });
    kit.changeDemo({ ...kit.data, doses: next });
    setNotice(
      '시연 기록만 변경했어요. 새로고침하면 초기화되며 서버나 재고에는 반영되지 않아요.',
    );
    close();
  }

  return (
    <div className="standard-page">
      <div className="page-heading">
        <div>
          <h1>복약</h1>
          <p>일정과 기록을 함께 확인해요.</p>
        </div>
        <button className="soft-button" onClick={today}>
          오늘
        </button>
      </div>
      <div className="schedule-grid">
        <div className="calendar-column">
          <section className="calendar" aria-label="복약 달력">
            <div className="flex items-center justify-between">
              <h2>
                {year}년 {monthNumber}월
              </h2>
              <div className="flex">
                <button
                  className="icon-button"
                  aria-label="이전 달"
                  onClick={previous}
                >
                  <Icon name="previous" />
                </button>
                <button
                  className="icon-button"
                  aria-label="다음 달"
                  onClick={next}
                >
                  <Icon name="next" />
                </button>
              </div>
            </div>
            <div className="calendar-grid">
              {/* 월요일부터 일요일까지 고정된 열 제목을 표시한다. */}
              {['월', '화', '수', '목', '금', '토', '일'].map(
                // 달력 상단에 한국어 요일을 표시한다.
                function weekday(value) {
                  return (
                    <span className="weekday" key={value}>
                      {value}
                    </span>
                  );
                },
              )}
              {days.map(
                /* 날짜별 선택 버튼과 오늘 표시를 만든다. */ function date(
                  value,
                ) {
                  // 날짜 선택은 달력과 기록 목록에 함께 반영한다.
                  function select() {
                    setDay(value);
                    if (!value.startsWith(month)) setMonth(value.slice(0, 7));
                  }
                  return (
                    <button
                      key={value}
                      className={`calendar-day ${value === day ? 'selected' : ''} ${value.startsWith(month) ? '' : 'outside'}`}
                      onClick={select}
                      aria-label={value}
                      aria-pressed={value === day}
                      aria-current={value === dayKey() ? 'date' : undefined}
                    >
                      {Number(value.slice(-2))}
                    </button>
                  );
                },
              )}
            </div>
          </section>
          <div className="schedule-summary">
            <h2>선택한 날 {doses.length}회 일정</h2>
            <p>
              사용자 확인 {confirmed} / 복용 추정 {estimated} / 그 외{' '}
              {doses.length - confirmed - estimated}
            </p>
            <div className="evidence-bars">
              {/* 수량이 아닌 각 회차의 확인 근거를 표현한다. */}
              {doses.map(
                /* 일정별 확인 상태를 요약 막대로 표시한다. */ function bar(
                  dose,
                ) {
                  return (
                    <span
                      key={dose.dose_id}
                      className={dose.result.toLowerCase()}
                    />
                  );
                },
              )}
            </div>
            <small>자동 기록은 실제 복용과 구분해요.</small>
          </div>
          {kit.demo && (
            <p className="caption">날짜와 기록은 시연 데이터입니다.</p>
          )}
        </div>
        <section className="timeline" aria-label="선택한 날의 복약 일정">
          <div className="flex items-center gap-3">
            <h2>복약 일정</h2>
            <Badge tone="green">
              {Number(day.slice(5, 7))}월 {Number(day.slice(8))}일
            </Badge>
          </div>
          {doses.length ? (
            doses.map(
              /* 일정과 확인 결과를 시간 순서로 표시한다. */ function row(
                dose,
              ) {
                // 연결된 포장을 찾아 처방약 표시를 통일한다.
                const item = kit.data.packages.find(function linked(item) {
                  return item.package_id === dose.package_id;
                });
                // 확인창의 기본 선택을 준비한다.
                function edit() {
                  setResult('CONFIRMED');
                  setEditing(dose);
                }
                return (
                  <button
                    key={dose.dose_id}
                    className="dose-row"
                    onClick={edit}
                  >
                    <span className="dose-time">
                      <strong>{timeLabel(dose.scheduled_at)}</strong>
                      <small>
                        {day === dayKey()
                          ? '오늘'
                          : `${Number(day.slice(5, 7))}/${Number(day.slice(8))}`}
                      </small>
                    </span>
                    <span className="dose-record">
                      <Badge
                        tone={
                          dose.result === 'CONFIRMED'
                            ? 'green'
                            : dose.result === 'ESTIMATED' ||
                                dose.result === 'UNCONFIRMED'
                              ? 'amber'
                              : 'neutral'
                        }
                      >
                        {labels[dose.result]}
                      </Badge>
                      <strong>
                        {item?.kind === 'PRESCRIPTION'
                          ? '처방 받은 약봉투'
                          : item?.name || '약품 정보 미확인'}
                      </strong>
                      <small>
                        {dose.result === 'ESTIMATED'
                          ? '반출로 생성된 기록 · 실제 복용 미확인'
                          : dose.result === 'CONFIRMED'
                            ? '사용자가 복용 여부를 확인한 기록이에요.'
                            : '등록된 일정과 확인 상태를 살펴보세요.'}
                      </small>
                    </span>
                  </button>
                );
              },
            )
          ) : (
            <EmptyState title="등록된 복약 일정이 없어요">
              보관함에서 인식한 처방 정보와 복용 시작일을 확인하면 일정이
              표시돼요.
            </EmptyState>
          )}
          <p className="caption">반출 기록은 실제 복용을 보장하지 않아요.</p>
          {notice && (
            <p className="inline-status" role="status">
              {notice}
            </p>
          )}
        </section>
      </div>
      {editing && (
        <Dialog title="복용 여부 확인" onClose={close}>
          <form onSubmit={save}>
            <Note>
              반출은 실제 복용을 뜻하지 않아요. 이 단계에서는 시연 기록만 변경할
              수 있어요.
            </Note>
            <fieldset className="radio-list">
              <legend>확인 결과</legend>
              {(['CONFIRMED', 'NOT_TAKEN', 'DEFERRED'] as const).map(
                // 사용자가 선택할 확인 결과를 표시한다.
                function option(value) {
                  // 선택한 확인 상태를 폼에만 반영한다.
                  function change() {
                    setResult(value);
                  }
                  return (
                    <label key={value}>
                      <input
                        type="radio"
                        name="dose-result"
                        checked={result === value}
                        onChange={change}
                      />
                      {labels[value]}
                    </label>
                  );
                },
              )}
            </fieldset>
            {!kit.demo && (
              <p className="caption">
                실제 기록 변경은 서버의 확인·접근 제어 연동 후 제공됩니다.
              </p>
            )}
            <button className="primary-button" disabled={!kit.demo}>
              시연 기록에 반영
            </button>
          </form>
        </Dialog>
      )}
    </div>
  );
}
