/// <reference lib="webworker" />
import { clientsClaim } from 'workbox-core';
import {
  cleanupOutdatedCaches,
  createHandlerBoundToURL,
  precacheAndRoute,
} from 'workbox-precaching';
import { NavigationRoute, registerRoute } from 'workbox-routing';

declare const self: ServiceWorkerGlobalScope & {
  __WB_MANIFEST: Array<{ url: string; revision: string | null }>;
};

// 빌드 산출물만 사전 캐시하고 API·사진·OCR 응답은 캐시하지 않는다.
precacheAndRoute(self.__WB_MANIFEST);
cleanupOutdatedCaches();
// 사용자 승인으로 활성화된 새 버전이 열린 창의 갱신까지 이어지게 한다.
clientsClaim();
// 실제 UI 경로에만 앱 셸을 제공하고 API나 자산 404를 HTML로 바꾸지 않는다.
registerRoute(
  new NavigationRoute(createHandlerBoundToURL('/index.html'), {
    allowlist: [
      /^\/$/,
      /^\/(schedule|alerts|device)\/?$/,
      /^\/packages\/[^/.]+\/?$/,
    ],
  }),
);

// 사용자 갱신 동작을 받은 뒤에만 새 Service Worker를 활성화한다.
self.addEventListener('message', function update(event) {
  if (event.data?.type === 'SKIP_WAITING') void self.skipWaiting();
});

// 잠금 화면에는 민감한 약품명 대신 일반 안내를 표시한다.
self.addEventListener('push', function showPush(event) {
  let id: string | undefined;
  try {
    const data: unknown = event.data?.json();
    if (
      data &&
      typeof data === 'object' &&
      'notification_id' in data &&
      typeof data.notification_id === 'string' &&
      data.notification_id.length > 0 &&
      data.notification_id.length <= 160
    )
      id = data.notification_id;
  } catch {
    /* 잘못된 본문도 민감정보 없이 일반 알림으로 안내한다. */
  }
  event.waitUntil(
    self.registration.showNotification('MEDKIT · 확인할 내용이 있어요', {
      body: '앱을 열어 최신 알림과 확인 요청을 확인해주세요.',
      icon: '/icon-192.png',
      badge: '/icon-192.png',
      tag: id,
      data: { path: '/alerts' },
    }),
  );
});

// 같은 출처의 알림 화면을 열고 조회만 요청하며 자동 확인을 수행하지 않는다.
self.addEventListener('notificationclick', function openAlert(event) {
  event.notification.close();
  // 기존 창이 있으면 재사용하고 없으면 앱을 연다.
  async function openWindow() {
    const windows = await self.clients.matchAll({
      type: 'window',
      includeUncontrolled: true,
    });
    for (const client of windows) {
      if (new URL(client.url).origin !== self.location.origin) continue;
      await client.navigate('/alerts');
      client.postMessage({ type: 'REFRESH' });
      await client.focus();
      return;
    }
    await self.clients.openWindow('/alerts');
  }
  event.waitUntil(openWindow());
});
