import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from 'react';
import { useLocation } from 'react-router-dom';
import { z } from 'zod';
import { Badge, Dialog } from './ui';
import './outboundHistory.css';

const eventId = z.string().regex(/^[A-Za-z0-9_-]{1,128}$/);
const eventSchema = z.object({
  eventId,
  packageId: z.string().min(1).max(128),
  medicineName: z.string().trim().min(1).max(200),
  occurredAt: z.iso.datetime({ offset: true }),
  eventType: z.literal('OUT').optional(),
  movementCertainty: z.enum(['CONFIRMED', 'ESTIMATED']).optional(),
  status: z.enum(['PENDING', 'TAKEN', 'NOT_TAKEN']),
  quantity: z.number().int().positive().nullable(),
  unit: z.enum(['TABLET', 'CAPSULE', 'OTHER']),
  recordedAt: z.iso.datetime({ offset: true }).nullable(),
});
const activitySchema = z
  .object({
    pending: z.array(eventSchema).max(1000),
    history: z.array(eventSchema).max(1000),
  })
  .superRefine(function validateActivity(data, context) {
    const ids = new Set<string>();
    for (const [group, events] of [
      ['pending', data.pending],
      ['history', data.history],
    ] as const) {
      for (const item of events) {
        // 상태와 수량이 충돌하는 응답은 빈 기록이나 확인된 사실로 표시하지 않는다.
        if (
          ids.has(item.eventId) ||
          (group === 'pending' &&
            (item.status !== 'PENDING' ||
              item.recordedAt !== null ||
              item.quantity !== null)) ||
          (group === 'history' &&
            (item.status === 'PENDING' || item.recordedAt === null)) ||
          (item.status === 'NOT_TAKEN' && item.quantity !== null) ||
          (item.status === 'TAKEN' &&
            (item.unit === 'OTHER') !== (item.quantity === null))
        ) {
          context.addIssue({
            code: 'custom',
            message: '반출 기록의 상태를 확인할 수 없습니다.',
          });
        }
        ids.add(item.eventId);
      }
    }
  });
type Activity = z.infer<typeof activitySchema>;
type OutboundEvent = z.infer<typeof eventSchema>;

// 기록 API 주소는 HTTPS 또는 로컬 개발 주소만 사용한다.
function activityUrl(): string {
  const value =
    import.meta.env.VITE_ACTIVITY_API_URL?.trim().replace(/\/$/, '') || '';
  if (!value) return '';
  try {
    const parsed = new URL(value);
    if (
      (parsed.protocol !== 'https:' &&
        !(
          parsed.protocol === 'http:' &&
          ['localhost', '127.0.0.1'].includes(parsed.hostname)
        )) ||
      parsed.username ||
      parsed.password ||
      parsed.search ||
      parsed.hash
    )
      return '';
    return value;
  } catch {
    return '';
  }
}

// 반출 시각과 확인 시각은 한국 시간으로 구분해 표시한다.
function formatTime(value: string): string {
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
}

// 촬영 기록의 반출 확실성을 사용자의 복용 확인과 분리한다.
function movementLabel(item: OutboundEvent): string {
  if (item.movementCertainty === 'CONFIRMED') return '반출 확인';
  if (item.movementCertainty === 'ESTIMATED') return '반출 추정';
  return '반출 확인 필요';
}

// 보관함 반출 확인과 이력을 처방 일정과 별도로 서버에서 읽는다.
export default function OutboundHistory({
  enabled,
  onInventoryChange,
}: {
  enabled: boolean;
  onInventoryChange: () => void | Promise<void>;
}) {
  const [data, setData] = useState<Activity | null>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'unavailable'>(
    'loading',
  );
  const [error, setError] = useState('');
  const [editing, setEditing] = useState<OutboundEvent | null>(null);
  const [quantity, setQuantity] = useState('');
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState('');
  const request = useRef<AbortController | null>(null);
  const savingRef = useRef(false);
  const sectionRef = useRef<HTMLElement>(null);
  const scrolledId = useRef('');
  const location = useLocation();
  const selected = new URLSearchParams(location.search).get('out_event') || '';
  const selectedId = eventId.safeParse(selected).success ? selected : '';
  const baseUrl = activityUrl();

  // 이전 요청을 취소하고 정상적인 서버 응답만 목록으로 교체한다.
  const reload = useCallback(
    async function reloadActivity() {
      request.current?.abort();
      if (!enabled || !baseUrl) {
        setStatus('unavailable');
        setData(null);
        return;
      }
      const controller = new AbortController();
      request.current = controller;
      const timeout = window.setTimeout(function abort() {
        controller.abort();
      }, 12000);
      try {
        const response = await fetch(`${baseUrl}/activity`, {
          signal: controller.signal,
          cache: 'no-store',
          headers: { Accept: 'application/json' },
        });
        if (!response.ok) throw new Error('ACTIVITY_UNAVAILABLE');
        const next = activitySchema.parse(await response.json());
        if (request.current !== controller || controller.signal.aborted) return;
        setData(next);
        setError('');
        setStatus('ready');
      } catch {
        if (request.current !== controller) return;
        setStatus('unavailable');
        setError('반출 기록을 가져오지 못했어요. 다시 시도해주세요.');
      } finally {
        window.clearTimeout(timeout);
      }
    },
    [baseUrl, enabled],
  );

  // 보이는 화면에서는 주기적으로 갱신하고 알림 클릭이나 복귀 시 즉시 재조회한다.
  useEffect(
    function observeActivity() {
      void reload();
      // 다시 온라인이 되거나 화면이 보이면 서버 상태를 확인한다.
      function refresh() {
        if (!document.hidden) void reload();
      }
      // 알림 클릭은 확인이나 복용으로 처리하지 않는다.
      function notificationOpened(event: MessageEvent) {
        if (event.data?.type === 'REFRESH') refresh();
      }
      window.addEventListener('online', refresh);
      document.addEventListener('visibilitychange', refresh);
      navigator.serviceWorker?.addEventListener('message', notificationOpened);
      const timer = window.setInterval(refresh, 30000);
      return function cleanup() {
        request.current?.abort();
        request.current = null;
        window.clearInterval(timer);
        window.removeEventListener('online', refresh);
        document.removeEventListener('visibilitychange', refresh);
        navigator.serviceWorker?.removeEventListener(
          'message',
          notificationOpened,
        );
      };
    },
    [reload],
  );

  // 알림의 반출 식별자에 해당하는 영역을 열고 최신 서버 기록만 보여준다.
  useEffect(
    function scrollToNotification() {
      if (
        status !== 'ready' ||
        !selectedId ||
        scrolledId.current === selectedId
      )
        return;
      scrolledId.current = selectedId;
      sectionRef.current?.scrollIntoView({
        behavior: 'smooth',
        block: 'start',
      });
    },
    [selectedId, status],
  );

  // 복용 수량을 입력할 대화상자를 연다.
  function begin(item: OutboundEvent) {
    setEditing(item);
    setQuantity('');
    setFormError('');
  }
  // 저장 중에는 미완료 입력을 닫지 않는다.
  function close() {
    if (!savingRef.current) setEditing(null);
  }

  // 서버가 확인한 최초 응답만 대기 목록에서 제거하고 이력에 반영한다.
  async function confirm(
    item: OutboundEvent,
    result: 'TAKEN' | 'NOT_TAKEN',
    count: number | null,
  ) {
    if (savingRef.current || !baseUrl) return;
    savingRef.current = true;
    setSaving(true);
    setFormError('');
    setError('');
    const controller = new AbortController();
    const timeout = window.setTimeout(function abort() {
      controller.abort();
    }, 12000);
    try {
      const response = await fetch(
        `${baseUrl}/activity/${encodeURIComponent(item.eventId)}/confirm`,
        {
          method: 'POST',
          cache: 'no-store',
          signal: controller.signal,
          headers: {
            Accept: 'application/json',
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ result, quantity: count }),
        },
      );
      if (!response.ok) throw new Error('CONFIRM_FAILED');
      const confirmed = z
        .object({ event: eventSchema })
        .parse(await response.json()).event;
      if (
        confirmed.eventId !== item.eventId ||
        confirmed.status !== result ||
        confirmed.quantity !== count ||
        confirmed.recordedAt === null
      )
        throw new Error('INVALID_CONFIRMATION');
      setData(function apply(previous) {
        if (!previous) return previous;
        return {
          pending: previous.pending.filter(function keep(entry) {
            return entry.eventId !== item.eventId;
          }),
          history: [
            confirmed,
            ...previous.history.filter(function keep(entry) {
              return entry.eventId !== item.eventId;
            }),
          ],
        };
      });
      setEditing(null);
      void onInventoryChange();
      void reload();
    } catch {
      // 시간 초과는 저장 실패를 뜻하지 않으므로 서버 상태를 다시 확인한다.
      const message =
        '저장 결과를 확인하지 못했어요. 최신 기록을 확인한 뒤 다시 시도해주세요.';
      setFormError(message);
      setError(message);
      void reload();
    } finally {
      window.clearTimeout(timeout);
      savingRef.current = false;
      setSaving(false);
    }
  }
  // 입력한 실제 수량만 서버에 제출한다.
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!editing) return;
    const amount = editing.unit === 'OTHER' ? null : Number(quantity);
    if (
      amount !== null &&
      (!quantity || !Number.isSafeInteger(amount) || amount <= 0)
    ) {
      setFormError('복용한 수량을 1 이상의 정수로 입력해주세요.');
      return;
    }
    void confirm(editing, 'TAKEN', amount);
  }

  return (
    <section
      ref={sectionRef}
      id="outbound-history"
      className="outbound-history"
      aria-labelledby="outbound-history-title"
    >
      <div className="outbound-heading">
        <div>
          <h2 id="outbound-history-title">보관함 복용 기록</h2>
          <p>약을 꺼낸 뒤 실제 복용·사용 여부를 확인해주세요.</p>
        </div>
        <button
          type="button"
          className="text-button"
          onClick={reload}
          disabled={!enabled || !baseUrl || saving}
        >
          새로고침
        </button>
      </div>
      {status === 'loading' && (
        <p role="status">반출 기록을 불러오고 있어요…</p>
      )}
      {status === 'unavailable' && (
        <p role={error ? 'alert' : 'status'}>
          {error || '보관함 반출 기록 연결을 준비하고 있어요.'}
        </p>
      )}
      {status === 'ready' && data && (
        <>
          {selectedId &&
            ![...data.pending, ...data.history].some(function same(item) {
              return item.eventId === selectedId;
            }) && (
              <p role="status">
                해당 반출 기록을 찾지 못했어요. 최신 목록을 확인해주세요.
              </p>
            )}
          <h3>
            확인 대기 <span>{data.pending.length}</span>
          </h3>
          {data.pending.length === 0 ? (
            <p className="outbound-empty">확인할 반출 기록이 없어요.</p>
          ) : (
            <ul className="outbound-list">
              {data.pending.map(function pending(item) {
                // 클릭만으로 반출이나 복용을 확정하지 않는다.
                function yes() {
                  begin(item);
                }
                // 미복용도 서버의 확인 응답을 받은 뒤 이력으로 남긴다.
                function no() {
                  void confirm(item, 'NOT_TAKEN', null);
                }
                return (
                  <li
                    key={item.eventId}
                    className={`outbound-card ${selectedId === item.eventId ? 'outbound-selected' : ''}`}
                  >
                    <div>
                      <Badge tone="amber">복용 확인 필요</Badge>
                      <strong>{item.medicineName}</strong>
                      <p>
                        {movementLabel(item)} · {formatTime(item.occurredAt)}
                      </p>
                    </div>
                    <div className="outbound-actions">
                      <button
                        type="button"
                        className="primary-button"
                        onClick={yes}
                        disabled={saving}
                      >
                        Y · 복용/사용
                      </button>
                      <button
                        type="button"
                        className="secondary-button"
                        onClick={no}
                        disabled={saving}
                      >
                        N · 복용 안 함
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
          <h3>확인한 기록</h3>
          {data.history.length === 0 ? (
            <p className="outbound-empty">확인한 복용 기록이 없어요.</p>
          ) : (
            <ul className="outbound-list">
              {data.history.map(function history(item) {
                return (
                  <li
                    key={item.eventId}
                    className={`outbound-card ${selectedId === item.eventId ? 'outbound-selected' : ''}`}
                  >
                    <div>
                      <Badge
                        tone={item.status === 'TAKEN' ? 'green' : 'neutral'}
                      >
                        {item.status === 'TAKEN' ? '복용/사용 확인' : '미복용'}
                      </Badge>
                      <strong>{item.medicineName}</strong>
                      <p>
                        {movementLabel(item)} · {formatTime(item.occurredAt)}
                      </p>
                      {item.status === 'TAKEN' && item.quantity !== null && (
                        <p>
                          확인 수량 {item.quantity}
                          {item.unit === 'CAPSULE' ? '캡슐' : '정'}
                        </p>
                      )}
                      {item.recordedAt && (
                        <small>확인 {formatTime(item.recordedAt)}</small>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
      {error && status === 'ready' && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {editing && (
        <Dialog title="복용·사용 확인" onClose={close} closeDisabled={saving}>
          <form onSubmit={submit} className="outbound-confirm">
            <p>
              <strong>{editing.medicineName}</strong> ·{' '}
              {formatTime(editing.occurredAt)}
            </p>
            <p>실제로 복용하거나 사용하셨나요?</p>
            {editing.unit !== 'OTHER' && (
              <label>
                복용한 수량 ({editing.unit === 'CAPSULE' ? '캡슐' : '정'})
                <input
                  type="number"
                  inputMode="numeric"
                  min="1"
                  step="1"
                  value={quantity}
                  onChange={function change(event) {
                    setQuantity(event.target.value);
                    setFormError('');
                  }}
                  required
                />
              </label>
            )}
            {formError && (
              <p className="form-error" role="alert">
                {formError}
              </p>
            )}
            <button className="primary-button" disabled={saving}>
              {saving ? '저장 중…' : '복용/사용 기록 저장'}
            </button>
          </form>
        </Dialog>
      )}
    </section>
  );
}
