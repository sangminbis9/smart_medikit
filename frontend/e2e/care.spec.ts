import { expect, test } from '@playwright/test';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { careGuides } from '../src/careGuides';

// PC·휴대폰에서 검색·상세·위험 신호·목록 복귀와 렌더링 오류를 검증한다.
test('케어 검색 → 상세 → 목록 복귀와 32개 가이드', async function care({
  page,
}, testInfo) {
  test.setTimeout(90_000);
  const errors: string[] = [];
  // 앱 예외만 수집하며 의료 응답 본문은 수집하지 않는다.
  page.on('pageerror', function error(error) {
    errors.push(error.message);
  });
  await page.goto('/');
  await page
    .getByRole('link', { name: '케어 가이드', exact: true })
    .filter({ visible: true })
    .click();
  await expect(page).toHaveURL(/\/care$/);
  await expect(page).toHaveTitle(/MEDKIT/);
  await expect(page.locator('.care-category-card')).toHaveCount(8);
  await expect(page.locator('.care-symptom-link')).toHaveCount(32);
  await page.screenshot({
    path: join(tmpdir(), `medikit-care-${testInfo.project.name}.png`),
  });
  await page.getByRole('searchbox', { name: '증상 검색' }).fill('기침');
  await expect(page.locator('.care-symptom-link')).toHaveCount(2);
  await page.getByRole('link', { name: '마른기침', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: '마른기침', exact: true }),
  ).toBeVisible();
  await expect(page.locator('.care-medicine-heading')).toHaveCount(0);
  await expect(page.locator('.care-health-notice')).toContainText(
    '의료 전문가의 검토를 받지 않은',
  );
  await expect(page.locator('.care-product')).toHaveCount(0);
  await page.screenshot({
    path: join(tmpdir(), `medikit-care-detail-${testInfo.project.name}.png`),
  });
  await page.getByRole('link', { name: '증상 목록으로' }).click();
  await expect(page.getByRole('searchbox', { name: '증상 검색' })).toHaveValue(
    '기침',
  );
  await page.getByRole('searchbox', { name: '증상 검색' }).fill('없는증상');
  await expect(
    page.getByRole('heading', { name: '검색한 증상을 찾지 못했어요' }),
  ).toBeVisible();
  await page.getByRole('button', { name: '전체 증상 보기' }).click();
  for (const guide of careGuides) {
    await page.getByRole('link', { name: guide.name, exact: true }).click();
    await expect(
      page.getByRole('heading', { name: guide.name, exact: true }),
    ).toBeVisible();
    await expect(page.locator('.care-step')).toHaveCount(guide.steps.length);
    await expect(page.locator('.care-health-notice')).toHaveText(guide.notice);
    await expect(page.locator('.care-warnings li')).toHaveCount(
      guide.warningSigns.length,
    );
    await expect(page.locator('.care-sources a')).toHaveCount(
      guide.sources.length,
    );
    await page.getByRole('link', { name: '증상 목록으로' }).click();
  }
  await expect(page.locator('vite-error-overlay')).toHaveCount(0);
  expect(errors).toEqual([]);
});

// 좁은 휴대폰부터 PC까지 가로 넘침과 네 메뉴 접근성을 확인한다.
test('케어 반응형·잘못된 ID·오프라인 직접 링크', async function responsive({
  page,
  context,
}) {
  await page.goto('/care/cuts');
  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(
      page.getByRole('heading', { name: '베인 상처 및 찰과상', exact: true }),
    ).toBeVisible();
    // 실제 요소의 폭이 화면 경계를 넘지 않는지 확인한다.
    expect(
      await page.evaluate(function overflow() {
        return document.documentElement.scrollWidth > innerWidth;
      }),
    ).toBe(false);
    await expect(
      page
        .getByRole('link', { name: '케어 가이드', exact: true })
        .filter({ visible: true }),
    ).toBeVisible();
  }
  // 활성 워커가 이 페이지를 제어한 뒤 오프라인 직접 링크를 검증한다.
  await page.evaluate(async function ready() {
    await navigator.serviceWorker.ready;
  });
  await page.reload();
  await context.setOffline(true);
  await page.goto('/care/burns');
  await expect(
    page.getByRole('heading', { name: '화상', exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/오프라인이에요/)).toBeVisible();
  await context.setOffline(false);
  await page.goto('/care/not-a-symptom');
  await expect(
    page.getByRole('heading', { name: '이 증상 가이드를 찾을 수 없어요' }),
  ).toBeVisible();
});
