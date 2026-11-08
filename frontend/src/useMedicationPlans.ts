import { useCallback, useEffect, useRef, useState } from 'react';
import { dayKey } from './data';
import {
  emptyMedicationState,
  parseMedicationState,
  recordMedicationOccurrence,
  removeMedicationPlan,
  saveMedicationPlan,
  type MedicationOccurrence,
  type MedicationPlan,
  type MedicationRecord,
  type MedicationState,
} from './medicationPlans';

type Snapshot = { revision: number; state: MedicationState };
export type MedicationAccessStatus =
  'unconfigured' | 'locked' | 'loading' | 'ready' | 'error';

// 공개 API 주소에 인증정보나 잘못된 프로토콜이 섞이지 않게 한다.
function configuredApi(): string {
  const value =
    import.meta.env.VITE_MEDICATION_API_URL?.trim().replace(/\/$/, '') || '';
  if (!value) return '';
  try {
    const url = new URL(value);
    if (url.username || url.password || url.search || url.hash) return '';
    if (
      url.protocol !== 'https:' &&
      !(
        url.protocol === 'http:' &&
        ['localhost', '127.0.0.1'].includes(url.hostname)
      )
    )
      return '';
    return value;
  } catch {
    return '';
  }
}
const apiUrl = configuredApi();

class ApiError extends Error {
  status: number;
  // 요청 본문 없이 오류 상태와 사용자 안내만 보관한다.
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// 인증된 별도 API에만 요청하며 비밀번호·토큰·복용약을 캐시나 로그에 남기지 않는다.
async function request(
  path: string,
  method: string,
  token: string,
  body?: unknown,
): Promise<unknown> {
  const controller = new AbortController();
  // 응답이 늦은 요청을 종료해 입력창이 무한히 기다리지 않게 한다.
  const timer = window.setTimeout(function abort() {
    controller.abort();
  }, 15000);
  try {
    const response = await fetch(`${apiUrl}${path}`, {
      method,
      cache: 'no-store',
      credentials: 'omit',
      signal: controller.signal,
      headers: {
        ...(body ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!response.ok) {
      const messages: Record<number, string> = {
        401:
          path === '/session'
            ? '공통 비밀번호를 확인해주세요.'
            : '잠금 해제 시간이 만료됐어요. 입력을 확인한 뒤 잠그고 다시 열어주세요.',
        403: '이 복용약 목록에 접근할 수 없어요.',
        409: '다른 기기에서 목록을 변경했어요. 최신 내용을 다시 열어 수정해주세요.',
        413: '저장할 데이터가 너무 커요. 오래된 묶음이나 기록을 정리해주세요.',
        422: '입력 내용이나 저장 한도를 확인해주세요.',
        429: '요청이 많아요. 잠시 후 다시 시도해주세요.',
      };
      throw new ApiError(
        response.status,
        messages[response.status] ||
          'AWS 저장소에 연결하지 못했어요. 잠시 후 다시 시도해주세요.',
      );
    }
    return await response.json();
  } catch (cause) {
    if (cause instanceof ApiError) throw cause;
    throw new Error(
      '연결이 끊겼거나 응답이 늦어요. 저장 성공을 확인하지 못했으니 다시 불러와주세요.',
      { cause },
    );
  } finally {
    window.clearTimeout(timer);
  }
}

// 서버의 버전과 복용약 구조를 검증하며 손상된 응답을 빈 목록으로 감추지 않는다.
function parseSnapshot(value: unknown): Snapshot {
  if (
    !value ||
    typeof value !== 'object' ||
    !('revision' in value) ||
    !('state' in value) ||
    !Number.isSafeInteger(value.revision) ||
    Number(value.revision) < 0
  )
    throw new Error('복용약 응답 형식을 확인할 수 없어요.');
  return {
    revision: Number(value.revision),
    state: parseMedicationState(JSON.stringify(value.state)),
  };
}

// 실제 복용약은 AWS 공유 목록으로, 시연 입력은 별도 메모리로 관리한다.
export function useMedicationPlans(demo: boolean) {
  const [snapshot, setSnapshot] = useState<Snapshot>({
    revision: 0,
    state: emptyMedicationState(),
  });
  const [demoState, setDemoState] = useState(emptyMedicationState);
  const [status, setStatus] = useState<MedicationAccessStatus>(
    apiUrl ? 'locked' : 'unconfigured',
  );
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const current = useRef(snapshot);
  const currentDemo = useRef(demoState);
  const token = useRef('');
  const generation = useRef(0);
  const saving = useRef(false);

  // 서버가 확인한 응답만 화면과 다음 변경의 기준으로 반영한다.
  const accept = useCallback(function accept(next: Snapshot) {
    current.current = next;
    setSnapshot(next);
  }, []);
  // 화면 복귀나 새로고침 버튼으로 최신 공유 목록을 읽는다.
  const reload = useCallback(
    async function reload() {
      if (!token.current || saving.current) return;
      const session = generation.current;
      const revision = current.current.revision;
      try {
        const next = parseSnapshot(
          await request('/medications', 'GET', token.current),
        );
        if (
          session !== generation.current ||
          saving.current ||
          current.current.revision !== revision
        )
          return;
        accept(next);
        setStatus('ready');
        setError('');
      } catch (cause) {
        if (session === generation.current)
          setError(
            cause instanceof Error
              ? cause.message
              : '목록을 불러오지 못했어요.',
          );
      }
    },
    [accept],
  );

  // 인증된 화면이 보일 때만 최신 목록을 조회한다.
  useEffect(
    function watchVisibility() {
      if (demo || status !== 'ready') return;
      // 숨겨진 화면에서는 조회하지 않는다.
      function refresh() {
        if (document.visibilityState === 'visible') void reload();
      }
      const interval = window.setInterval(refresh, 30000);
      window.addEventListener('focus', refresh);
      document.addEventListener('visibilitychange', refresh);
      // 잠금·시연 전환 때 조회 예약을 해제한다.
      return function cleanup() {
        window.clearInterval(interval);
        window.removeEventListener('focus', refresh);
        document.removeEventListener('visibilitychange', refresh);
      };
    },
    [demo, status, reload],
  );

  // 비밀번호를 서버에서 검증하고 짧은 세션 토큰은 메모리에만 둔다.
  async function unlock(password: string): Promise<boolean> {
    if (!apiUrl || saving.current) return false;
    saving.current = true;
    setBusy(true);
    setError('');
    const session = ++generation.current;
    try {
      const auth = await request('/session', 'POST', '', { password });
      if (
        !auth ||
        typeof auth !== 'object' ||
        !('token' in auth) ||
        typeof auth.token !== 'string'
      )
        throw new Error('잠금 해제 응답을 확인할 수 없어요.');
      const next = parseSnapshot(
        await request('/medications', 'GET', auth.token),
      );
      if (session !== generation.current) return false;
      token.current = auth.token;
      accept(next);
      setStatus('ready');
      return true;
    } catch (cause) {
      if (session === generation.current) {
        token.current = '';
        setStatus('locked');
        setError(
          cause instanceof Error ? cause.message : '잠금을 해제하지 못했어요.',
        );
      }
      return false;
    } finally {
      saving.current = false;
      setBusy(false);
    }
  }
  // 화면의 목록과 세션을 지우고 AWS에 저장한 데이터는 유지한다.
  function lock() {
    generation.current++;
    token.current = '';
    accept({ revision: 0, state: emptyMedicationState() });
    setError('');
    setStatus(apiUrl ? 'locked' : 'unconfigured');
  }

  // 버전이 일치하는 서버 쓰기의 성공 응답을 받은 뒤에만 변경을 확정한다.
  async function change(
    transform: (state: MedicationState) => MedicationState,
  ): Promise<boolean> {
    if (saving.current) return false;
    saving.current = true;
    setBusy(true);
    setError('');
    const session = generation.current;
    try {
      if (demo) {
        const next = transform(currentDemo.current);
        currentDemo.current = next;
        setDemoState(next);
        return true;
      }
      if (!token.current)
        throw new Error('공통 비밀번호로 목록을 먼저 열어주세요.');
      const nextState = transform(current.current.state);
      const next = parseSnapshot(
        await request('/medications', 'PUT', token.current, {
          expected_revision: current.current.revision,
          state: nextState,
        }),
      );
      if (session !== generation.current) return false;
      accept(next);
      return true;
    } catch (cause) {
      if (!demo && cause instanceof ApiError && cause.status === 409) {
        try {
          const next = parseSnapshot(
            await request('/medications', 'GET', token.current),
          );
          if (session === generation.current) accept(next);
        } catch {
          /* 충돌 후 재조회 실패를 저장 성공으로 바꾸지 않는다. */
        }
      }
      if (session === generation.current)
        setError(cause instanceof Error ? cause.message : '저장하지 못했어요.');
      return false;
    } finally {
      saving.current = false;
      setBusy(false);
    }
  }

  // 편집창을 연 당시의 묶음을 대조해 다른 기기의 변경을 보존한다.
  function save(plan: MedicationPlan, original?: MedicationPlan) {
    // 최신 묶음과 다른 초안은 다시 열도록 안내한다.
    return change(function update(state) {
      if (original) {
        // 같은 식별자의 현재 묶음을 찾는다.
        const latest = state.plans.find(function match(item) {
          return item.id === original.id;
        });
        if (
          (original.title &&
            JSON.stringify(latest) !== JSON.stringify(original)) ||
          (!original.title && latest)
        )
          throw new Error(
            '다른 기기에서 이 복용약이 변경되었어요. 편집창을 닫고 최신 내용을 다시 열어주세요.',
          );
      }
      return saveMedicationPlan(state, plan);
    });
  }
  // 삭제한 묶음과 연결된 확인 기록만 제거한다.
  function remove(id: string) {
    return change(
      // 다른 묶음의 기록은 유지한다.
      function removePlan(state) {
        return removeMedicationPlan(state, id);
      },
    );
  }
  // 복용 종료와 재개는 재고나 실제 복용 여부를 바꾸지 않는다.
  function toggleEnded(id: string) {
    return change(
      // 한국 날짜 기준으로 종료 상태만 교체한다.
      function updateEnd(state) {
        // 대상 묶음이 아직 존재하는지 확인한다.
        const plan = state.plans.find(function match(item) {
          return item.id === id;
        });
        if (!plan) throw new Error('목록이 변경되었어요. 다시 불러와주세요.');
        return saveMedicationPlan(state, {
          ...plan,
          endedOn: plan.endedOn ? null : dayKey(),
        });
      },
    );
  }
  // 확인창을 열었을 때의 약 구성에 대해서만 복용 확인을 기록한다.
  function record(
    occurrence: MedicationOccurrence,
    result: MedicationRecord['result'],
    medicineIds: string[],
  ) {
    return change(
      // 현재 일정과 원래 확인창의 내용을 다시 비교한다.
      function confirm(state) {
        return recordMedicationOccurrence(
          state,
          occurrence,
          result,
          medicineIds,
        );
      },
    );
  }
  // 시연 전환 시 실제 인증 세션을 잠그고 합성 입력만 초기화한다.
  function resetDemo() {
    const next = emptyMedicationState();
    currentDemo.current = next;
    setDemoState(next);
    lock();
  }

  return {
    state: demo ? demoState : snapshot.state,
    status: demo ? ('ready' as const) : status,
    error,
    busy,
    storageLabel: demo
      ? '시연 기록 · 새로고침하면 초기화돼요'
      : 'AWS에 저장 · 공통 비밀번호로 같은 목록을 사용해요',
    save,
    remove,
    toggleEnded,
    record,
    reload,
    resetDemo,
    unlock,
    lock,
  };
}
export type MedicationPlansStore = ReturnType<typeof useMedicationPlans>;
