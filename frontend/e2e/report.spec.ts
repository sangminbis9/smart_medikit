import { readFile } from 'node:fs/promises';
import { expect, test, type Page } from '@playwright/test';
import type { MedicationState } from '../src/medicationPlans';

// 실제 AWS가 아닌 합성 자료로 보고서의 조회·선택과 쓰기 요청 부재를 확인한다.
async function prepare(
  page: Page,
  failed: 'activity' | 'medications' | 'none' = 'none',
  manualState?: MedicationState,
) {
  await page.clock.install({ time: new Date('2026-11-12T03:00:00Z') });
  const writes: string[] = [];
  const state: MedicationState = {
    version: 1,
    plans: [
      {
        id: 'plan1',
        title: '합성 처방',
        notes: '',
        endedOn: null,
        timing: {
          mode: 'regular',
          schedule: {
            startDate: '2026-11-10',
            endDate: '2026-11-10',
            weekdays: [0, 1, 2, 3, 4, 5, 6],
            times: ['09:00'],
          },
        },
        medicines: [
          {
            id: 'm1',
            name: '합성 처방약',
            strength: '250mg',
            dose: '1정',
            timing: null,
          },
          {
            id: 'm2',
            name: '제외된 처방약',
            strength: '500mg',
            dose: '1정',
            timing: null,
          },
        ],
      },
    ],
    records: [
      {
        occurrenceId: 'medication:plan1:2026-11-10T09:00+09:00',
        result: 'some',
        takenMedicineIds: ['m1'],
        recordedAt: '2026-11-12T00:00:00Z',
      },
    ],
  };
  // 보고서 조작 중 쓰기 메서드는 어떤 API에도 전송하지 않아야 한다.
  page.on('request', function recordWrite(request) {
    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(request.method()))
      writes.push(request.url());
  });
  // 기존 재고의 포장 식별자와 제품 정보를 제공한다.
  await page.route('**/api/**', async function inventory(route) {
    await route.fulfill({
      json: route.request().url().includes('/inventory')
        ? {
            box: { box_id: 'test-box' },
            inventory_status: 'CONFIRMED',
            catalog_complete: true,
            packages: [
              {
                package_id: 'p1',
                box_id: 'test-box',
                name: '합성 일반약',
                kind: 'OTC',
                state: 'STORED',
                medicines: [
                  {
                    medicine_id: 'm1',
                    name: '합성 일반약',
                    product_code: '123456789',
                    ingredients: ['합성성분'],
                    form: 'TABLET',
                  },
                ],
              },
            ],
          }
        : { alerts: [] },
    });
  });
  // 반출 날짜와 확인 날짜가 다른 정상·미복용·대기 기록을 제공한다.
  await page.route('https://activity.test/**', async function activity(route) {
    if (failed === 'activity') {
      await route.fulfill({ status: 503, json: {} });
      return;
    }
    const base = {
      packageId: 'p1',
      medicineName: '합성 일반약',
      quantity: 2,
      unit: 'TABLET',
      recordedAt: '2026-11-12T00:00:00Z',
      movementCertainty: 'ESTIMATED',
    };
    await route.fulfill({
      json: {
        pending: [
          {
            ...base,
            eventId: 'pending',
            status: 'PENDING',
            quantity: null,
            recordedAt: null,
            occurredAt: '2026-11-12T00:00:00Z',
          },
        ],
        history: [
          {
            ...base,
            eventId: 'e1',
            status: 'TAKEN',
            occurredAt: '2026-11-10T00:00:00Z',
          },
          {
            ...base,
            eventId: 'e2',
            status: 'TAKEN',
            occurredAt: '2026-11-11T00:00:00Z',
          },
          {
            ...base,
            eventId: 'old',
            packageId: 'p-old',
            medicineName: '과거 반출약',
            status: 'TAKEN',
            occurredAt: '2026-06-01T00:00:00Z',
          },
          {
            ...base,
            eventId: 'no',
            status: 'NOT_TAKEN',
            quantity: null,
            medicineName: '미복용 시험약',
            occurredAt: '2026-11-10T00:00:00Z',
          },
        ],
        reviews: [],
      },
    });
  });
  // 수동 처방약의 기존 공개 시연 조회만 허용한다.
  await page.route('**/medication-api/**', async function medications(route) {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/medications')) {
      await route.fulfill(
        failed === 'medications'
          ? { status: 503, json: {} }
          : { json: { revision: 1, state: manualState ?? state } },
      );
    } else if (path.endsWith('/links'))
      await route.fulfill({ json: { links: [] } });
    else await route.fulfill({ status: 404, json: {} });
  });
  return writes;
}

// 날짜별 회차가 아니라 처방 카드와 반출 약품을 선택하는 전체 흐름을 검증한다.
test('처방 카드와 반출 약품을 기본 선택하고 제외·복귀·간단 보고서를 제공한다', async function report({
  page,
}) {
  const writes = await prepare(page);
  await page.goto('/schedule');
  await expect(
    page.getByRole('button', { name: '복용약 새로고침', exact: true }),
  ).toHaveCount(0);
  await page.getByRole('link', { name: '진료용 보고서' }).click();
  await expect(
    page.getByRole('button', { name: '새로고침', exact: true }),
  ).toHaveCount(0);
  const selection = page.getByRole('region', { name: '보고서 항목 선택' });
  const prescriptions = selection.getByRole('region', {
    name: '복용 중인 처방약',
    exact: true,
  });
  const cabinet = selection.getByRole('region', {
    name: '일반 의약품 복용 기록',
    exact: true,
  });
  await expect(selection.getByRole('checkbox')).toHaveCount(2);
  const prescription = prescriptions.getByRole('checkbox', {
    name: /합성 처방/,
  });
  await expect(prescription).toBeChecked();
  await expect(prescriptions).toContainText('1일분');
  await expect(prescriptions).toContainText(
    '합성 처방약 250mg, 제외된 처방약 500mg',
  );
  await expect(
    cabinet.getByRole('checkbox', { name: '합성 일반약', exact: true }),
  ).toBeChecked();
  await expect(selection.getByRole('heading', { name: /2026-/ })).toHaveCount(
    0,
  );
  await page.getByRole('button', { name: '직접 설정', exact: true }).click();
  await page.getByLabel('시작일', { exact: true }).fill('2026-11-01');
  await page.getByLabel('종료일', { exact: true }).fill('2026-11-12');
  // 기존 복약 화면의 자동 연결과 보고서 전용 선택의 쓰기를 분리해 검사한다.
  writes.length = 0;
  await prescription.uncheck();
  await page.getByRole('button', { name: '선택 완료 · 보고서 보기' }).click();
  const report = page.getByRole('region', {
    name: '진료용 약 복용 보고서',
    exact: true,
  });
  await expect(
    report.getByRole('article', { name: '합성 일반약', exact: true }),
  ).toBeVisible();
  await expect(report.getByText('합성 처방', { exact: true })).toHaveCount(0);
  await expect(report.locator('details')).toHaveCount(0);
  await expect(report).toContainText('복용 날짜(반출 기준)');
  await expect(report).toContainText('2026-11-12, 2026-11-11, 2026-11-10');
  await expect(
    page.getByRole('button', { name: '새로고침', exact: true }),
  ).toHaveCount(0);
  await expect(report).not.toContainText('일정 기반');
  await expect(report).not.toContainText('날짜별');
  await expect(report).not.toContainText('09:00');
  await expect(report).not.toContainText('확인 2회');
  await expect(
    page.getByRole('button', { name: '선택 수정', exact: true }),
  ).toHaveCount(0);
  await page.getByRole('button', { name: '기록 선택으로 돌아가기' }).click();
  await expect(page).toHaveURL(/\/schedule\/report$/);
  await expect(prescription).not.toBeChecked();
  await expect(page.getByLabel('시작일', { exact: true })).toHaveValue(
    '2026-11-01',
  );
  await expect(page.getByLabel('종료일', { exact: true })).toHaveValue(
    '2026-11-12',
  );
  await prescription.check();
  await page.getByRole('button', { name: '선택 완료 · 보고서 보기' }).click();
  await expect(
    report.getByRole('article', { name: '합성 처방', exact: true }),
  ).toContainText('1일분');
  await expect(report).toContainText('제외된 처방약 500mg');
  await page.setViewportSize({ width: 320, height: 740 });
  expect(
    await page.evaluate(
      // 두 분류와 처방 안의 긴 약 이름이 좁은 화면을 넘지 않아야 한다.
      function fits() {
        return document.documentElement.scrollWidth <= window.innerWidth;
      },
    ),
  ).toBe(true);
  await page.getByRole('button', { name: '기록 선택으로 돌아가기' }).click();
  await page.getByRole('link', { name: '복약으로 돌아가기' }).click();
  await expect(page).toHaveURL(/\/schedule$/);
  expect(writes).toEqual([]);
});

// 등록 처방의 전체 기간과 종료일 없는 표시가 조회 기간에 의해 잘리지 않아야 한다.
test('5일분·상시 복용 중과 전체 포함 약을 한 카드에 표시한다', async function durations({
  page,
}) {
  const state: MedicationState = {
    version: 1,
    records: [],
    plans: [
      {
        id: 'cold',
        title: '감기 처방약',
        notes: '',
        endedOn: null,
        timing: {
          mode: 'regular',
          schedule: {
            startDate: '2026-11-12',
            endDate: '2026-11-16',
            weekdays: [1],
            times: ['23:00'],
          },
        },
        medicines: [
          { id: 'a', name: 'A정', strength: '', dose: '1정', timing: null },
          { id: 'b', name: 'B캡슐', strength: '', dose: '1캡슐', timing: null },
        ],
      },
      {
        id: 'pressure',
        title: '고혈압 처방약',
        notes: '',
        endedOn: null,
        timing: {
          mode: 'regular',
          schedule: {
            startDate: '2026-11-01',
            endDate: '',
            weekdays: [0],
            times: ['09:00'],
          },
        },
        medicines: [
          { id: 'c', name: 'C정', strength: '5mg', dose: '1정', timing: null },
        ],
      },
    ],
  };
  const writes = await prepare(page, 'none', state);
  const productReads: string[] = [];
  // 처방 이름만 보여주기 위해 제품·성분 API를 추가 조회하지 않는다.
  page.on('request', function product(request) {
    if (request.url().includes('/medicine-products/'))
      productReads.push(request.url());
  });
  await page.goto('/schedule/report');
  const selection = page.getByRole('region', { name: '보고서 항목 선택' });
  await expect(selection.getByRole('checkbox')).toHaveCount(3);
  await page.getByRole('button', { name: '직접 설정', exact: true }).click();
  await page.getByLabel('시작일', { exact: true }).fill('2026-11-12');
  await page.getByLabel('종료일', { exact: true }).fill('2026-11-12');
  await expect(selection).toContainText('감기 처방약');
  await expect(selection).toContainText('5일분');
  await expect(selection).toContainText('상시 복용 중');
  await expect(selection).toContainText('A정, B캡슐');
  await page.getByRole('button', { name: '선택 완료 · 보고서 보기' }).click();
  const report = page.getByRole('region', {
    name: '진료용 약 복용 보고서',
    exact: true,
  });
  await expect(
    report.getByRole('article', { name: '감기 처방약', exact: true }),
  ).toContainText('5일분');
  await expect(
    report.getByRole('article', { name: '고혈압 처방약', exact: true }),
  ).toContainText('상시 복용 중');
  await expect(report).not.toContainText('2026-11-16');
  await expect(report).not.toContainText('추정');
  expect(productReads).toEqual([]);
  expect(writes).toEqual([]);
});

// 기간 버튼·빈 선택·잘못된 날짜를 기존 조회 규칙에 맞춰 검증한다.
test('기간 변경은 카드 선택을 초기화하고 빈 결과와 잘못된 기간을 구분한다', async function periods({
  page,
}) {
  await prepare(page);
  await page.goto('/schedule/report');
  const selection = page.getByRole('region', { name: '보고서 항목 선택' });
  await expect(selection.getByRole('checkbox')).toHaveCount(2);
  for (const checkbox of await selection.getByRole('checkbox').all())
    await checkbox.uncheck();
  const show = page.getByRole('button', { name: '선택 완료 · 보고서 보기' });
  await expect(show).toBeDisabled();
  await page.getByRole('button', { name: '최근 3개월', exact: true }).click();
  await expect(selection.getByRole('checkbox').first()).toBeChecked();
  await page.getByRole('button', { name: '최근 6개월', exact: true }).click();
  await expect(selection.getByRole('checkbox')).toHaveCount(3);
  await expect(
    selection.getByRole('checkbox', { name: '과거 반출약', exact: true }),
  ).toBeChecked();
  await page.getByRole('button', { name: '직접 설정', exact: true }).click();
  await page.getByLabel('시작일', { exact: true }).fill('2026-07-01');
  await page.getByLabel('종료일', { exact: true }).fill('2026-07-31');
  await expect(selection).toContainText('선택한 기간에 표시할 항목이 없어요.');
  await expect(show).toBeDisabled();
  await page.getByLabel('시작일', { exact: true }).fill('2026-08-01');
  await expect(page.getByRole('alert')).toContainText(
    '조회 기간의 시작일과 종료일',
  );
  await expect(show).toBeDisabled();
});

for (const failed of ['activity', 'medications'] as const) {
  // 한쪽 조회 실패를 빈 정상 보고서나 한쪽 자료만의 결과로 숨기지 않는다.
  test(`${failed} 조회 실패는 보고서 생성을 막는다`, async function unavailable({
    page,
  }) {
    await prepare(page, failed);
    await page.goto('/schedule/report');
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(
      page.getByRole('button', { name: '선택 완료 · 보고서 보기' }),
    ).toBeDisabled();
    await expect(
      page.getByRole('region', { name: '진료용 약 복용 보고서', exact: true }),
    ).toHaveCount(0);
    if (failed === 'activity') {
      await page.route(
        'https://activity.test/**',
        async function recovered(route) {
          await route.fulfill({
            json: { history: [], pending: [], reviews: [] },
          });
        },
      );
    } else {
      await page.route(
        '**/medication-api/medications',
        async function recovered(route) {
          await route.fulfill({
            json: {
              revision: 2,
              state: { version: 1, plans: [], records: [] },
            },
          });
        },
      );
    }
    await page.getByRole('button', { name: '다시 시도', exact: true }).click();
    await expect(page.getByRole('checkbox')).toHaveCount(1);
    await expect(
      page.getByRole('button', { name: '선택 완료 · 보고서 보기' }),
    ).toBeEnabled();
    await expect(
      page.getByRole('button', { name: '새로고침', exact: true }),
    ).toHaveCount(0);
  });
}

// 선택 완료 시점의 목록은 고정하고 수정으로 돌아올 때 양쪽 출처를 새로 읽는다.
test('보고서는 선택 시점에 고정되고 화살표 복귀에서 두 출처를 재조회한다', async function snapshot({
  page,
}) {
  const writes = await prepare(page);
  await page.goto('/schedule/report');
  await expect(page.getByRole('checkbox')).toHaveCount(2);
  await page.getByRole('button', { name: '선택 완료 · 보고서 보기' }).click();
  const report = page.getByRole('region', {
    name: '진료용 약 복용 보고서',
    exact: true,
  });
  await page.route(
    '**/medication-api/medications',
    async function changed(route) {
      await route.fulfill({
        json: { revision: 2, state: { version: 1, plans: [], records: [] } },
      });
    },
  );
  await page.route(
    'https://activity.test/**',
    async function changedActivity(route) {
      await route.fulfill({ json: { pending: [], history: [], reviews: [] } });
    },
  );
  await expect(report.getByRole('article')).toHaveCount(2);
  await page.getByRole('button', { name: '기록 선택으로 돌아가기' }).click();
  await expect(
    page.getByText('선택한 기간에 표시할 항목이 없어요.'),
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: '선택 완료 · 보고서 보기' }),
  ).toBeDisabled();
  expect(writes).toEqual([]);
});

// 선택 복귀의 두 응답이 모두 오기 전에는 이전 자료로 생성을 허용하지 않는다.
test('선택 복귀는 양쪽 응답을 기다리고 제외 선택을 유지한다', async function refresh({
  page,
}) {
  await prepare(page);
  await page.goto('/schedule/report');
  await expect(page.getByRole('checkbox')).toHaveCount(2);
  await page.getByRole('checkbox').first().uncheck();
  await page.getByRole('button', { name: '선택 완료 · 보고서 보기' }).click();
  let releaseActivity!: () => void;
  let releaseManual!: () => void;
  const activityWait = new Promise<void>(
    // 반출 이력 응답을 테스트가 허용할 때까지 보류한다.
    function wait(resolve) {
      releaseActivity = resolve;
    },
  );
  const manualWait = new Promise<void>(
    // 수동 처방 응답을 테스트가 허용할 때까지 보류한다.
    function wait(resolve) {
      releaseManual = resolve;
    },
  );
  await page.route(
    'https://activity.test/**',
    async function waitActivity(route) {
      await activityWait;
      await route.fallback();
    },
  );
  await page.route(
    '**/medication-api/medications',
    async function waitManual(route) {
      await manualWait;
      await route.fallback();
    },
  );
  await page.getByRole('button', { name: '기록 선택으로 돌아가기' }).click();
  const show = page.getByRole('button', { name: '선택 완료 · 보고서 보기' });
  await expect(show).toBeDisabled();
  releaseManual();
  await expect(show).toBeDisabled();
  releaseActivity();
  await expect(page.getByRole('checkbox')).toHaveCount(2);
  await expect(page.getByRole('checkbox').first()).not.toBeChecked();
  await expect(show).toBeEnabled();
});

// 오프라인이면 정상 보고서 대신 선택 화면과 기존 복약 이동을 유지한다.
test('오프라인에서는 생성을 막고 복약 화면으로 돌아갈 수 있다', async function offline({
  page,
  context,
}) {
  await prepare(page);
  await page.goto('/schedule/report');
  await expect(page.getByRole('checkbox')).toHaveCount(2);
  await page.getByRole('button', { name: '선택 완료 · 보고서 보기' }).click();
  await context.setOffline(true);
  await expect(
    page.getByRole('link', { name: '복약으로 돌아가기' }),
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: '선택 완료 · 보고서 보기' }),
  ).toBeDisabled();
  await expect(
    page.getByRole('region', { name: '진료용 약 복용 보고서', exact: true }),
  ).toHaveCount(0);
});

// 재고 정보가 사라졌다 돌아와도 제외한 반출 약품이 자동으로 선택되면 안 된다.
test('재고 조회 실패와 복구에도 반출 약품 제외를 유지한다', async function inventoryIdentity({
  page,
}) {
  await prepare(page);
  await page.goto('/schedule/report');
  const cabinet = page.getByRole('checkbox', {
    name: '합성 일반약',
    exact: true,
  });
  await expect(cabinet).toBeChecked();
  await cabinet.uncheck();
  await page.getByRole('button', { name: '선택 완료 · 보고서 보기' }).click();
  let inventoryAvailable = false;
  await page.route('**/api/**', async function inventoryResponse(route) {
    if (inventoryAvailable) await route.fallback();
    else await route.fulfill({ status: 503, json: {} });
  });
  const back = page.getByRole('button', { name: '기록 선택으로 돌아가기' });
  await page.clock.fastForward(31000);
  await expect(back).toBeVisible();
  await back.click();
  await expect(cabinet).not.toBeChecked();
  await cabinet.check();
  await page.getByRole('button', { name: '선택 완료 · 보고서 보기' }).click();
  inventoryAvailable = true;
  await page.clock.fastForward(31000);
  await expect(back).toBeVisible();
  await back.click();
  await expect(cabinet).toBeChecked();
});

// 선택한 보고서를 파일로 직접 내려받고 제목 오른쪽 버튼과 한글 파일명을 확인한다.
test('선택한 보고서를 한국어 PDF 파일로 다운로드한다', async function pdfDownload({
  page,
}, testInfo) {
  const writes = await prepare(page);
  await page.goto('/schedule/report');
  await expect(page.getByRole('checkbox')).toHaveCount(2);
  await page
    .getByRole('checkbox', { name: '합성 처방', exact: true })
    .uncheck();
  await page.getByRole('button', { name: '선택 완료 · 보고서 보기' }).click();
  const report = page.getByRole('region', {
    name: '진료용 약 복용 보고서',
    exact: true,
  });
  const button = report.getByRole('button', {
    name: 'PDF 다운로드',
    exact: true,
  });
  const heading = await report
    .getByRole('heading', { name: '진료용 약 복용 보고서', exact: true })
    .boundingBox();
  const bounds = await button.boundingBox();
  expect(bounds!.x).toBeGreaterThan(heading!.x);
  const waiting = page.waitForEvent('download');
  await button.click();
  const download = await waiting;
  expect(download.suggestedFilename()).toBe('진료용 약 복용 보고서.pdf');
  const path = `/private/tmp/medkit-report-pdf-${testInfo.project.name}.pdf`;
  await download.saveAs(path);
  const data = await readFile(path);
  expect(data.subarray(0, 5).toString()).toBe('%PDF-');
  expect(data.byteLength).toBeGreaterThan(5000);
  expect(writes).toEqual([]);
});

// 긴 처방 이름과 많은 항목이 한 페이지에서 잘리거나 사라지지 않아야 한다.
test('긴 보고서를 여러 페이지 PDF로 다운로드한다', async function longPdf({
  page,
}, testInfo) {
  const plans: MedicationState['plans'] = [];
  for (let index = 0; index < 24; index++)
    plans.push({
      id: `long-${index}`,
      title: `합성 장기 처방 ${index + 1} - 매우 긴 이름으로 등록한 처방 묶음을 구분하기 위한 추가 설명이 들어간 제목`,
      notes: '',
      endedOn: null,
      timing: { mode: 'regular', schedule: null },
      medicines: [
        {
          id: 'a',
          name: '매우 긴 이름의 합성 처방 약품과 추가 설명을 포함한 시험 알약',
          strength: '250mg',
          dose: '1정',
          timing: null,
        },
      ],
    });
  await prepare(page, 'none', { version: 1, records: [], plans });
  await page.goto('/schedule/report');
  await expect(page.getByRole('checkbox')).toHaveCount(25);
  await page.evaluate(
    // 실제 PDF 캔버스의 글꼴·페이지를 관찰해 제목과 본문이 분리되는지 검사한다.
    function observePdfLayout() {
      const draws: { page: number; font: string; text: string }[] = [];
      let page = 0;
      const originalRect = CanvasRenderingContext2D.prototype.fillRect;
      const originalText = CanvasRenderingContext2D.prototype.fillText;
      // 전체 캔버스 배경 초기화를 새 PDF 페이지의 시작으로 구분한다.
      CanvasRenderingContext2D.prototype.fillRect = function rect(...args) {
        if (args[2] === 1240 && args[3] === 1754) page++;
        return originalRect.apply(this, args);
      };
      // 글자 내용은 합성 테스트 메모리에만 보존하며 실제 서버에 보내지 않는다.
      CanvasRenderingContext2D.prototype.fillText = function text(...args) {
        draws.push({ page, font: this.font, text: args[0] });
        return originalText.apply(this, args);
      };
      (window as unknown as { pdfDraws: typeof draws }).pdfDraws = draws;
    },
  );
  await page.getByRole('button', { name: '선택 완료 · 보고서 보기' }).click();
  const waiting = page.waitForEvent('download');
  await page.getByRole('button', { name: 'PDF 다운로드', exact: true }).click();
  const download = await waiting;
  const path = `/private/tmp/medkit-report-pdf-long-${testInfo.project.name}.pdf`;
  await download.saveAs(path);
  const data = await readFile(path);
  expect(
    data.toString('latin1').match(/\/Type \/Page\b/g)!.length,
  ).toBeGreaterThan(1);
  const draws = await page.evaluate(
    // 페이지 번호·메타 설명을 제외한 분류/약품 제목과 본문만 비교한다.
    function pdfLayout() {
      return (
        window as unknown as {
          pdfDraws: { page: number; font: string; text: string }[];
        }
      ).pdfDraws.filter(
        // 문서의 실제 스타일로 각 줄의 역할을 판별한다.
        function relevant(row) {
          return /(?:600 (?:28|32)px|26px)/.test(row.font);
        },
      );
    },
  );
  for (let index = 0; index < draws.length - 1; index++) {
    const current = draws[index];
    const next = draws[index + 1];
    if (
      (current.font.includes('600 32px') && next.font.includes('600 28px')) ||
      (current.font.includes('600 28px') && !next.font.includes('600 28px'))
    ) {
      expect(next.page, `${current.text} 다음 줄이 같은 페이지여야 함`).toBe(
        current.page,
      );
    }
  }
});
