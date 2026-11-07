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
  await expect(page.locator('.care-match-reason')).toHaveText(
    '최신 보관함 정보를 확인할 수 없어요.',
  );
});

// 새 포장·반출 상태가 자동 재조회로 홈과 케어에 함께 반영되는지 확인한다.
test('보유 포장 추가와 반출이 홈·케어에 반영된다', async function inventory({
  page,
}) {
  const item = syntheticPackage();
  item.medicines[0].care_assessment = null;
  item.expiry = {
    actual_expiry: null,
    temporary_management_date: null,
    verified: false,
  };
  item.safety_status = 'SAFETY_CHECK_PENDING';
  let packages: (typeof item)[] = [];
  let inventoryReads = 0;
  // 검증용 시간을 앞당겨 실제 네트워크를 호출하지 않고 30초 갱신을 검사한다.
  await page.clock.install();
  // 백엔드의 포장·제품 결합 응답을 동일 계약의 합성 데이터로 제공한다.
  await page.route('**/api/**', async function respond(route) {
    const inventory = route.request().url().includes('/inventory');
    if (inventory) inventoryReads++;
    await route.fulfill({
      json: inventory
        ? {
            box: { box_id: 'test-box' },
            packages,
            inventory_status: 'CONFIRMED',
            catalog_medicines: [item.medicines[0]],
            catalog_complete: true,
          }
        : { alerts: [], alerts_status: 'UNAVAILABLE' },
    });
  });
  await page.goto('/');
  await expect(page.locator('.package-card')).toHaveCount(0);
  await expect
    .poll(
      // 첫 조회 완료 뒤 추가 항목의 반영 여부를 검사한다.
      function reads() {
        return inventoryReads;
      },
    )
    .toBeGreaterThan(0);
  packages = [item];
  await page.clock.fastForward(31000);
  await expect(page.locator('.package-card')).toContainText(
    '검증 전용 합성 제품',
  );
  await expect(page.locator('.package-card')).toContainText('보관 중');
  await page
    .getByRole('link', { name: '케어 가이드', exact: true })
    .filter({ visible: true })
    .click();
  const stored = page.getByRole('region', {
    name: '증상 관련 약품',
    exact: true,
  });
  await expect(stored).toHaveCount(0);
  await page.getByRole('link', { name: '두통', exact: true }).click();
  await expect(stored).toContainText('검증 전용 합성 제품');
  await expect(page.locator('.care-medicine-heading')).toContainText(
    '확인 필요',
  );
  await expect(page.locator('.care-product')).toHaveCount(0);
  await expect(
    page.getByRole('region', { name: '등록된 약품', exact: true }),
  ).toHaveCount(0);
  await stored.getByRole('link', { name: /검증 전용 합성 제품/ }).click();
  await expect(page.getByText('합성 사용법', { exact: true })).toBeVisible();
  await page.goBack();
  item.state = 'OUT';
  await page.clock.fastForward(31000);
  await expect(stored.locator('li')).toHaveCount(0);
  await expect(stored).toContainText(
    '등록된 효능 정보에서 이 증상과 일치하는 약품을 찾지 못했어요',
  );
  await page
    .getByRole('link', { name: '홈', exact: true })
    .filter({ visible: true })
    .click();
  await expect(page.locator('.package-card')).toContainText('반출 중');
});

// 공개 플래그 없이 받은 일반약·처방약 전체를 검색하고 케어 보관 목록에 표시한다.
test('보관함의 일반약과 처방약을 모두 검색한다', async function allMedicines({
  page,
}) {
  const otc = syntheticPackage();
  const prescription = syntheticPackage();
  prescription.package_id = 'synthetic-prescription';
  prescription.kind = 'PRESCRIPTION';
  prescription.name = '검증용 처방약 봉투';
  prescription.medicines[0].name = '처방 검색용 합성약';
  prescription.medicines[0].medicine_id = 'prescription-product';
  prescription.medicines[0].product_code = '000000002';
  prescription.medicines[0].ingredients = ['처방 검색용 성분'];
  prescription.medicines[0].care_assessment = null;
  // 두 종류의 보관 약품이 동일한 조회 응답으로 들어오는 상황을 재현한다.
  await page.route('**/api/**', async function respond(route) {
    await route.fulfill({
      json: route.request().url().includes('/inventory')
        ? {
            box: { box_id: 'test-box' },
            packages: [otc, prescription],
            inventory_status: 'CONFIRMED',
          }
        : { alerts: [], alerts_status: 'UNAVAILABLE' },
    });
  });
  await page.goto('/');
  await expect(page.locator('.package-card')).toHaveCount(2);
  const search = page
    .getByRole('searchbox', { name: '보관 약품 검색' })
    .filter({ visible: true });
  await search.fill('처방 검색용 성분');
  await expect(page.locator('.package-card')).toHaveCount(1);
  await expect(page.locator('.package-card')).toContainText(
    '검증용 처방약 봉투',
  );
  await search.fill('검증 전용 합성 제품');
  await expect(page.locator('.package-card')).toHaveCount(1);
  await expect(page.locator('.package-card')).toContainText(
    '검증 전용 합성 제품',
  );
  await search.fill('');
  await page
    .getByRole('link', { name: '케어 가이드', exact: true })
    .filter({ visible: true })
    .click();
  const stored = page.getByRole('region', {
    name: '증상 관련 약품',
    exact: true,
  });
  await expect(stored).toHaveCount(0);
  await page.getByRole('link', { name: '두통', exact: true }).click();
  await expect(stored).toContainText('검증용 처방약 봉투');
  await expect(stored.locator('li')).toHaveCount(2);
  await expect(page.locator('.care-product')).not.toContainText(
    '검증용 처방약 봉투',
  );
});

// 포장이 없는 제품 4개도 홈·케어에서 바로 보고 검색하며 조회 실패를 구분한다.
test('제품만 등록해도 홈·케어에서 표시하고 검색한다', async function productOnly({
  page,
}) {
  await page.clock.install();
  // 실제 제품·환자 정보 대신 네 종류의 합성 등록 제품을 만든다.
  const medicines = Array.from({ length: 4 }, function product(_, index) {
    const medicine = syntheticPackage().medicines[0];
    medicine.medicine_id = `synthetic-${index}`;
    medicine.product_code = `00000000${index + 1}`;
    medicine.name = `등록 제품 ${index + 1}`;
    medicine.ingredients = [`시험 성분 ${index + 1}`];
    medicine.care_assessment = null;
    medicine.product_info!.source = 'DYNAMODB_PRODUCT_INFO';
    medicine.product_info!.indications =
      index < 2 ? '감기로 인한 두통의 완화' : '알레르기성 비염의 완화';
    return medicine;
  });
  let fail = false;
  // 실제 장애와 동일한 빈 포장·완전한 제품 목록 응답을 재현한다.
  await page.route('**/api/**', async function respond(route) {
    await route.fulfill({
      status: fail ? 503 : 200,
      json: route.request().url().includes('/inventory')
        ? {
            box: { box_id: 'test-box' },
            packages: [],
            inventory_status: 'CONFIRMED',
            catalog_medicines: medicines,
            catalog_complete: true,
          }
        : { alerts: [], alerts_status: 'UNAVAILABLE' },
    });
  });
  await page.goto('/');
  const catalog = page.getByRole('region', {
    name: '등록된 약품',
    exact: true,
  });
  await expect(catalog.locator('li')).toHaveCount(4);
  await expect(page.locator('.scene canvas')).toBeVisible();
  await expect(page.getByText('포장 위치 정보가 아직 없어요')).toBeVisible();
  await expect(page.locator('.package-card')).toHaveCount(0);
  await expect(page.getByText('보관 중 0', { exact: true })).toHaveCount(0);
  await expect(
    page.getByText('아직 보관된 약품이 없어요', { exact: true }),
  ).toHaveCount(0);
  const search = page
    .getByRole('searchbox', { name: '보관 약품 검색' })
    .filter({ visible: true });
  await search.fill('시험 성분 2');
  await expect(catalog.locator('li')).toHaveCount(1);
  await expect(catalog).toContainText('등록 제품 2');
  await search.fill('없는 제품');
  await expect(catalog.locator('li')).toHaveCount(0);
  await expect(catalog).toContainText('일치하는 등록 약품이 없어요');
  await search.fill('등록 제품 1');
  await catalog
    .getByRole('button', { name: '등록 제품 1 · 사용법 상세' })
    .click();
  await expect(page.getByRole('dialog')).toContainText('합성 사용법');
  await page.getByRole('button', { name: '닫기', exact: true }).click();
  await search.fill('');
  await page
    .getByRole('link', { name: '케어 가이드', exact: true })
    .filter({ visible: true })
    .click();
  await expect(catalog).toHaveCount(0);
  await page.getByRole('link', { name: '두통', exact: true }).click();
  const related = page.getByRole('region', {
    name: '증상 관련 약품',
    exact: true,
  });
  await expect(related.locator('li')).toHaveCount(2);
  await expect(related).toContainText('감기로 인한 두통의 완화');
  await expect(related).not.toContainText('등록 제품 3');
  await related
    .getByRole('button', { name: '등록 제품 1 · 사용법 상세' })
    .click();
  await expect(page.getByRole('dialog')).toContainText('합성 사용법');
  await page.getByRole('button', { name: '닫기', exact: true }).click();
  await expect(page.locator('.care-medicine-heading')).toContainText(
    '확인 필요',
  );
  await expect(page.locator('.care-product')).toHaveCount(0);
  await page
    .getByRole('link', { name: '홈', exact: true })
    .filter({ visible: true })
    .click();
  fail = true;
  await page.clock.fastForward(31000);
  await expect(catalog).toContainText('이전 조회 결과예요');
  await expect(catalog.locator('li')).toHaveCount(4);
});

// DynamoDB 제품 캐시를 실제 포장이나 보유 제품으로 잘못 표현하지 않는지 확인한다.
test('제품 캐시만 있으면 보유 여부와 사용정보 부족을 알린다', async function catalog({
  page,
}) {
  const medicine = syntheticPackage().medicines[0];
  medicine.product_info = null;
  medicine.care_assessment = null;
  let inventoryStatus: 'UNAVAILABLE' | 'PARTIAL' = 'UNAVAILABLE';
  // 현재 읽기 전용 Lambda와 같은 미확인 응답 계약을 제공한다.
  await page.route('**/api/**', async function respond(route) {
    await route.fulfill({
      json: route.request().url().includes('/inventory')
        ? {
            box: { box_id: 'test-box' },
            packages: [],
            inventory_status: inventoryStatus,
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
  await expect(page.locator('.care-match-reason')).toContainText(
    '제품 조회는 연결되어 있어요',
  );
  await expect(page.locator('.care-side-note .care-data-notice')).toContainText(
    '실제 보관 여부·유효기간 정보가 없어',
  );
  await expect(
    page.getByText('최신 보관함 정보를 확인할 수 없어요.', {
      exact: true,
    }),
  ).toHaveCount(0);
  const related = page.getByRole('region', {
    name: '증상 관련 약품',
    exact: true,
  });
  await expect(related.locator('li')).toHaveCount(0);
  await expect(related).toContainText('효능 정보를 확인할 수 없는 제품 1개');
  inventoryStatus = 'PARTIAL';
  await page.reload();
  await expect(page.locator('.care-match-reason')).toContainText(
    '조회는 완료됐지만 일부 보관 정보',
  );
  await expect(page.locator('.care-medicine-heading')).toContainText(
    '확인 필요',
  );
  await page.goto('/alerts');
  await expect(page.getByText(/알림 이력이 아직 연결되지/)).toBeVisible();
});
