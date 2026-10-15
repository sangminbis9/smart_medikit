import { afterEach, expect, test, vi } from 'vitest';
import { getJson } from './useMedkit';

// 검증용 주소만 사용하며 실제 서버나 장치에 접속하지 않는다.
vi.mock('./settings', function settings() {
  return { settings: { apiUrl: 'https://example.test/prod', boxId: 'box 01' } };
});

// 테스트의 네트워크 대역을 다음 검증에 남기지 않는다.
afterEach(function cleanup() {
  vi.unstubAllGlobals();
});

// 보관함 ID를 인코딩하고 의료 응답의 브라우저 캐시를 금지한다.
test('조회 요청은 보관함 ID·취소 신호·접근 쿠키를 전달한다', async function request() {
  const fetchMock = vi.fn().mockResolvedValue(new Response('{"packages":[]}'));
  vi.stubGlobal('fetch', fetchMock);
  const controller = new AbortController();
  await expect(getJson('/inventory', controller.signal)).resolves.toEqual({
    packages: [],
  });
  const [url, options] = fetchMock.mock.calls[0];
  expect(url.href).toBe('https://example.test/prod/inventory?box_id=box+01');
  expect(options).toMatchObject({
    signal: controller.signal,
    credentials: 'include',
    cache: 'no-store',
  });
});

// 서버 오류 본문을 사용자 메시지나 로그에 노출하지 않는다.
test('실패한 조회는 상태만 반환하고 응답 본문을 숨긴다', async function denied() {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValue(new Response('비공개 오류 본문', { status: 403 })),
  );
  await expect(
    getJson('/alerts', new AbortController().signal),
  ).rejects.toThrow('서버 조회에 실패했어요. (403)');
});

// HTML 오류 페이지를 빈 정상 데이터로 처리하지 않는다.
test('JSON이 아닌 응답은 실패 처리한다', async function malformed() {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(new Response('<html>unavailable</html>')),
  );
  await expect(
    getJson('/inventory', new AbortController().signal),
  ).rejects.toThrow();
});
