import { dimensionsSchema } from './data';

// 공개 주소만 허용하며 잘못된 설정은 빈 데이터로 감추지 않는다.
export function readSettings(env: Record<string, string | undefined>) {
  const apiUrl = env.VITE_API_URL?.trim().replace(/\/$/, '') || '';
  const wsUrl = env.VITE_WS_URL?.trim() || '';
  const boxId = env.VITE_BOX_ID?.trim() || '';
  const issues: string[] = [];
  for (const [url, protocol, label] of [
    [apiUrl, 'https:', 'API'],
    [wsUrl, 'wss:', 'WebSocket'],
  ]) {
    if (!url) continue;
    try {
      const parsed = new URL(url);
      const local = ['localhost', '127.0.0.1'].includes(parsed.hostname);
      if (
        (parsed.protocol !== protocol &&
          !(
            local && parsed.protocol === (protocol === 'wss:' ? 'ws:' : 'http:')
          )) ||
        parsed.username ||
        parsed.password ||
        parsed.search ||
        parsed.hash
      )
        throw new Error();
    } catch {
      issues.push(
        `${label} 주소를 확인해주세요. HTTPS/WSS 주소만 사용할 수 있습니다.`,
      );
    }
  }
  if (apiUrl && !boxId) issues.push('API를 연결하려면 보관함 ID가 필요합니다.');
  if (wsUrl && !apiUrl)
    issues.push('WebSocket을 연결하려면 API 주소도 필요합니다.');
  const values = [env.VITE_BOX_WIDTH, env.VITE_BOX_DEPTH, env.VITE_BOX_HEIGHT];
  const result = dimensionsSchema.safeParse({
    width: Number(values[0]),
    depth: Number(values[1]),
    height: Number(values[2]),
  });
  if (values.some(isFilled) && !result.success)
    issues.push('보관함 가로·세로·높이는 모두 0보다 큰 mm 값이어야 합니다.');
  return {
    apiUrl,
    wsUrl,
    boxId,
    dimensions: result.success ? result.data : null,
    issues,
  };
}

// 일부 치수만 입력했는지 확인한다.
function isFilled(value: string | undefined): boolean {
  return Boolean(value?.trim());
}

export const settings = readSettings(import.meta.env);
