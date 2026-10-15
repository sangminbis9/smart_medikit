import { useCallback, useEffect, useRef, useState } from 'react';
import { emptyData, parseData, type Data } from './data';
import { makeDemo } from './demo';
import { settings } from './settings';

// 캐시 없이 조회하고 오류 본문이나 민감 응답을 로그에 남기지 않는다.
export async function getJson(
  path: string,
  signal: AbortSignal,
): Promise<unknown> {
  const url = new URL(`${settings.apiUrl}${path}`);
  url.searchParams.set('box_id', settings.boxId);
  const response = await fetch(url, {
    signal,
    credentials: 'include',
    cache: 'no-store',
    headers: { Accept: 'application/json' },
  });
  if (!response.ok)
    throw new Error(`서버 조회에 실패했어요. (${response.status})`);
  return response.json();
}

// 서버 조회와 시연 데이터를 분리하고 늦게 도착한 이전 응답을 무시한다.
export function useMedkit() {
  const [demo, setDemo] = useState(false);
  const [data, setData] = useState<Data>(emptyData);
  const [online, setOnline] = useState(navigator.onLine);
  const [status, setStatus] = useState<'empty' | 'loading' | 'ready' | 'error'>(
    'empty',
  );
  const [error, setError] = useState('');
  const [live, setLive] = useState(false);
  const request = useRef<AbortController | null>(null);

  // 각 조회마다 이전 요청을 취소하고 데이터 전체를 한 번에 교체한다.
  const refresh = useCallback(
    // 최신 조회만 유지하고 실패 시 재시도 안내를 표시한다.
    async function refresh() {
      request.current?.abort();
      if (demo || !navigator.onLine) return;
      if (settings.issues.length) {
        setError(settings.issues.join(' '));
        setStatus('error');
        return;
      }
      if (!settings.apiUrl) {
        setStatus('empty');
        return;
      }
      const controller = new AbortController();
      request.current = controller;
      setStatus('loading');
      const timeout = window.setTimeout(abortRequest, 12000);
      // 응답이 멈춘 요청은 종료하고 재시도할 수 있게 한다.
      function abortRequest() {
        controller.abort();
      }
      try {
        const [inventory, alerts] = await Promise.all([
          getJson('/inventory', controller.signal),
          getJson('/alerts', controller.signal),
        ]);
        if (request.current !== controller || controller.signal.aborted) return;
        setData(parseData(inventory, alerts, settings.boxId));
        setError('');
        setStatus('ready');
      } catch {
        if (request.current !== controller) return;
        setError(
          '최신 정보를 가져오지 못했어요. 주소와 서버 연결을 확인한 후 다시 시도해주세요.',
        );
        setStatus('error');
      } finally {
        window.clearTimeout(timeout);
      }
    },
    [demo],
  );

  // 화면 복귀·네트워크 복구·푸시 클릭 때 최신 상태를 다시 조회한다.
  useEffect(
    // 연결·화면 상태 이벤트와 조회 취소를 함께 관리한다.
    function observeConnection() {
      void refresh();
      // 오프라인에서는 이전 메모리 스냅샷만 표시한다.
      function connectionChanged() {
        setOnline(navigator.onLine);
        void refresh();
      }
      // 백그라운드에서 돌아오면 서버 누락분을 보정한다.
      function visibilityChanged() {
        if (!document.hidden) void refresh();
      }
      // 알림 클릭은 복용이나 내용 확인을 확정하지 않는다.
      function notificationOpened(event: MessageEvent) {
        if (event.data?.type === 'REFRESH') void refresh();
      }
      window.addEventListener('online', connectionChanged);
      window.addEventListener('offline', connectionChanged);
      document.addEventListener('visibilitychange', visibilityChanged);
      navigator.serviceWorker?.addEventListener('message', notificationOpened);
      // 떠난 화면의 요청과 이벤트를 해제한다.
      return function cleanup() {
        request.current?.abort();
        request.current = null;
        window.removeEventListener('online', connectionChanged);
        window.removeEventListener('offline', connectionChanged);
        document.removeEventListener('visibilitychange', visibilityChanged);
        navigator.serviceWorker?.removeEventListener(
          'message',
          notificationOpened,
        );
      };
    },
    [refresh],
  );

  // WebSocket은 변경 신호로만 사용하고 재연결 때 전체 상태를 조회한다.
  useEffect(
    // 서버 연결을 재시도하고 재접속 시 최신 데이터를 조회한다.
    function observeRealtime() {
      if (demo || !online || !settings.wsUrl || settings.issues.length) return;
      let socket: WebSocket | null = null;
      let timer = 0;
      let delay = 1000;
      let stopped = false;
      // 숨겨진 탭에서는 연결을 유지하지 않는다.
      function connect() {
        if (stopped || document.hidden) return;
        socket = new WebSocket(settings.wsUrl);
        // 연결 성공은 장치 온라인 상태와 구분한다.
        socket.onopen = function opened() {
          delay = 1000;
          setLive(true);
          void refresh();
        };
        // 서버 데이터는 메시지 대신 검증된 조회 응답으로만 반영한다.
        socket.onmessage = function changed() {
          void refresh();
        };
        // 오류 연결은 닫힘 처리에서 점진적으로 재시도한다.
        socket.onerror = function failed() {
          socket?.close();
        };
        // 서버나 네트워크 부담을 줄이도록 재시도 간격을 늘린다.
        socket.onclose = function closed() {
          setLive(false);
          if (!stopped && !document.hidden) {
            timer = window.setTimeout(connect, delay);
            delay = Math.min(delay * 2, 30000);
          }
        };
      }
      // 탭이 보이면 새 연결을 시작한다.
      function visibilityChanged() {
        window.clearTimeout(timer);
        if (document.hidden) socket?.close();
        else if (!socket || socket.readyState >= WebSocket.CLOSING) connect();
      }
      connect();
      document.addEventListener('visibilitychange', visibilityChanged);
      // 연결과 예약 작업을 함께 해제한다.
      return function cleanup() {
        stopped = true;
        window.clearTimeout(timer);
        socket?.close();
        setLive(false);
        document.removeEventListener('visibilitychange', visibilityChanged);
      };
    },
    [demo, online, refresh],
  );

  // 시연 전환 시 실제 응답과 시연 수정값을 모두 버린다.
  function toggleDemo() {
    request.current?.abort();
    request.current = null;
    setData(demo ? emptyData() : makeDemo());
    setDemo(!demo);
    setError('');
    setStatus('empty');
  }

  // 초기 단계의 편집은 시연 메모리에만 적용하며 API 쓰기를 흉내 내지 않는다.
  function changeDemo(next: Data) {
    if (demo) setData(next);
  }

  return {
    data,
    demo,
    online,
    status,
    error,
    live,
    refresh,
    toggleDemo,
    changeDemo,
  };
}

export type Medkit = ReturnType<typeof useMedkit>;
