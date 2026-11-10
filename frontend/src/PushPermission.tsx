import { useState } from 'react';

// 사용자 클릭에서 권한과 브라우저 구독을 받고 서버 저장까지 확인한다.
export default function PushPermission() {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  // 비밀키 없이 서버 공개키만 사용해 이 브라우저의 구독을 등록한다.
  async function enable() {
    if (busy) return;
    setBusy(true);
    try {
      const base = import.meta.env.VITE_ACTIVITY_API_URL;
      if (
        !base ||
        new URL(base).protocol !== 'https:' ||
        !('PushManager' in window) ||
        !('Notification' in window)
      )
        throw new Error('UNAVAILABLE');
      const permission = await Notification.requestPermission();
      if (permission !== 'granted') {
        setMessage('브라우저 설정에서 알림 권한을 허용해주세요.');
        return;
      }
      const configResponse = await fetch(`${base}/push/config`, {
        cache: 'no-store',
        signal: AbortSignal.timeout(10000),
      });
      if (!configResponse.ok) throw new Error('CONFIG_FAILED');
      const config = await configResponse.json();
      if (
        typeof config.vapid_public_key !== 'string' ||
        !/^[A-Za-z0-9_-]{87}$/.test(config.vapid_public_key)
      )
        throw new Error('INVALID_KEY');
      const raw = atob(
        config.vapid_public_key.replace(/-/g, '+').replace(/_/g, '/') + '=',
      );
      const key = Uint8Array.from(raw, function byte(character) {
        return character.charCodeAt(0);
      });
      const registration = await navigator.serviceWorker.ready;
      const subscription =
        (await registration.pushManager.getSubscription()) ||
        (await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: key,
        }));
      const response = await fetch(`${base}/push/subscriptions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        cache: 'no-store',
        signal: AbortSignal.timeout(10000),
        body: JSON.stringify({ subscription: subscription.toJSON() }),
      });
      if (!response.ok) throw new Error('SAVE_FAILED');
      setMessage('이 브라우저에서 알림을 받을 수 있어요.');
    } catch {
      setMessage(
        '알림 연결을 완료하지 못했어요. iPhone은 홈 화면에 추가한 앱에서 다시 시도해주세요.',
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <div>
      <button className="primary-button" onClick={enable} disabled={busy}>
        {busy ? '알림 연결 중…' : '이 기기에서 알림 받기'}
      </button>
      {message && <p role="status">{message}</p>}
    </div>
  );
}
