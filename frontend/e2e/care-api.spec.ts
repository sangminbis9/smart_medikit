import { expect, test } from '@playwright/test';
import { packageSchema } from '../src/data';

// 실제 약품을 흉내 내지 않는 합성 승인 자료로 화면의 안전 상태를 검증한다.
function syntheticPackage() {
  return packageSchema.parse({
    package_id: 'synthetic-package',
    box_id: 'test-box',
    kind: 'OTC',
    state: 'STORED',
    name: '검증 전용 합성 제품',
    expiry: { verified: true, actual_expiry: '2099-01-01' },
    safety_status: 'CHECKED',
    medicines: [
      {
        medicine_id: 'synthetic',
        name: '검증 전용 합성 제품',
        product_code: '000000001',
        ingredients: ['합성 성분'],
        product_info_status: 'SUCCEEDED',
        product_info: {
          source: 'MFDS_PERMIT_DETAIL',
          indications: '합성 두통 효능',
          dosage: '합성 사용법',
          precautions: '합성 주의사항',
        },
        care_assessment: {
          product_code: '000000001',
          review_status: 'APPROVED',
          scope: 'CARE_GUIDE_32_V1',
          source_url: 'https://nedrug.mfds.go.kr/test',
          reviewed_at: '2026-01-01T00:00:00Z',
          valid_until: '2099-01-01T00:00:00Z',
          indications: '합성 두통 효능',
          ingredient_names: ['합성 성분'],
          matches: [
            {
              symptom_id: 'headache',
              medicine_categories: ['PAIN_RELIEF'],
              evidence: '합성 두통 효능',
              conditions: ['합성 검토 조건'],
            },
          ],
        },
      },
    ],
  });
}

// 최신 재고·미확인·주의·빈 목록·조회 실패가 UI에서도 서로 구분되는지 확인한다.
test('API 상태와 사용정보를 안전하게 표시한다', async function safety({
  page,
}) {
  let item = syntheticPackage();
  let packages = [item];
  let fail = false;
  let alertType: 'DUPLICATE_INGREDIENT' | 'DUR_CONTRAINDICATION' | null = null;
  // 응답 데이터는 합성 자료로 한정하고 실제 AWS로 요청하지 않는다.
  await page.route('**/api/**', async function respond(route) {
    const url = new URL(route.request().url());
    expect(url.searchParams.get('box_id')).toBe('test-box');
    const alerts = alertType
      ? [
          {
            alert_id: 'synthetic-alert',
            type: alertType,
            title: '합성 주의정보',
            body: '검증용',
            package_ids: [item.package_id],
            occurred_at: '2026-01-01T00:00:00Z',
          },
        ]
      : [];
    await route.fulfill({
      status: fail ? 503 : 200,
      json: url.pathname.endsWith('inventory')
        ? {
            box: { box_id: 'test-box' },
            packages,
            inventory_status: 'CONFIRMED',
          }
        : { alerts, alerts_status: 'CONFIRMED' },
      headers: { 'Cache-Control': 'no-store' },
    });
  });
  await page.goto('/care/headache');
  await expect(page.locator('.care-product .badge')).toHaveText('보유');
  await page
    .getByRole('link', { name: '사용법 상세 · 검증 전용 합성 제품' })
    .click();
  await expect(page.getByText('합성 사용법', { exact: true })).toBeVisible();
  await expect(page.getByText('합성 주의사항', { exact: true })).toBeVisible();
  item.expiry.verified = false;
  await page.goto('/care/headache');
  await expect(page.locator('.care-product .badge')).toHaveText('확인 필요');
  for (const type of [
    'DUPLICATE_INGREDIENT',
    'DUR_CONTRAINDICATION',
  ] as const) {
    alertType = type;
    await page.reload();
    await expect(page.locator('.care-product .badge')).toHaveText('사용 주의');
    await expect(page.locator('.care-product .badge')).toHaveClass(
      type === 'DUR_CONTRAINDICATION' ? /badge-red/ : /badge-amber/,
    );
  }
  alertType = null;
  packages = [];
  await page.reload();
  await expect(page.locator('.care-medicine-heading')).toContainText('미보유');
  item = syntheticPackage();
  packages = [item];
  await page.reload();
  await expect(page.locator('.care-product .badge')).toHaveText('보유');
  fail = true;
  // 화면 복귀 재조회가 실패하면 메모리에 남은 제품도 확정 보유로 표시하지 않는다.
  await page.evaluate(function returnToPage() {
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(page.getByRole('alert')).toContainText(
    '최신 정보를 가져오지 못했어요',
  );
  await expect(page.locator('.care-product .badge')).toHaveText('확인 필요');
});

// DynamoDB 제품 캐시를 실제 포장이나 보유 제품으로 잘못 표현하지 않는지 확인한다.
test('제품 캐시만 있으면 보유 여부와 사용정보 부족을 알린다', async function catalog({
  page,
}) {
  const medicine = syntheticPackage().medicines[0];
  medicine.product_info = null;
  medicine.care_assessment = null;
  // 현재 읽기 전용 Lambda와 같은 미확인 응답 계약을 제공한다.
  await page.route('**/api/**', async function respond(route) {
    await route.fulfill({
      json: route.request().url().includes('/inventory')
        ? {
            box: { box_id: 'test-box' },
            packages: [],
            inventory_status: 'UNAVAILABLE',
            catalog_medicines: [medicine],
            catalog_complete: true,
          }
        : { alerts: [], alerts_status: 'UNAVAILABLE' },
    });
  });
  await page.goto('/care/headache');
  await expect(page.locator('.care-product')).toHaveCount(0);
  await expect(page.locator('.care-medicine-heading')).toContainText(
    '확인 필요',
  );
  await page.getByText('인식된 제품 정보 1개 · 보유 여부 미확인').click();
  await page
    .getByRole('button', { name: '검증 전용 합성 제품 · 사용법 상세' })
    .click();
  await expect(page.getByRole('dialog')).toContainText(
    '의약품 사용정보 확인 필요',
  );
  await page.getByRole('button', { name: '닫기', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.goto('/alerts');
  await expect(page.getByText(/알림 이력이 아직 연결되지/)).toBeVisible();
});
