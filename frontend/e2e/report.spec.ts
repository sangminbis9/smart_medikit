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

// 건별 제외 후 요약과 복귀가 원본 저장 없이 동작하는지 검증한다.
test('복약에서 기록을 선택해 약별 요약과 날짜 내역을 본다', async function report({
  page,
}) {
  const writes = await prepare(page);
  await page.goto('/schedule');
  const reportLink = page.getByRole('link', {
    name: '진료용 보고서',
    exact: true,
  });
  const linkStyle = await reportLink.evaluate(
    // 글자 링크가 아닌 큰 배경색 버튼인지 실제 렌더링으로 검사한다.
    function style(element) {
      const css = getComputedStyle(element);
      return {
        background: css.backgroundColor,
        radius: parseFloat(css.borderRadius),
        font: parseFloat(css.fontSize),
        height: element.getBoundingClientRect().height,
      };
    },
  );
  expect(linkStyle.background).not.toBe('rgba(0, 0, 0, 0)');
  expect(linkStyle.radius).toBeGreaterThanOrEqual(12);
  expect(linkStyle.font).toBeGreaterThanOrEqual(16);
  expect(linkStyle.height).toBeGreaterThanOrEqual(44);
  await reportLink.click();
  await expect(page).toHaveURL(/\/schedule\/report$/);
  const backStyle = await page
    .getByRole('link', { name: '복약으로 돌아가기' })
    .evaluate(
      // 돌아가기 링크에도 같은 버튼의 시각·터치 크기가 적용됐는지 검사한다.
      function style(element) {
        const css = getComputedStyle(element);
        return {
          background: css.backgroundColor,
          radius: parseFloat(css.borderRadius),
          font: parseFloat(css.fontSize),
          height: element.getBoundingClientRect().height,
        };
      },
    );
  expect(backStyle.background).not.toBe('rgba(0, 0, 0, 0)');
  expect(backStyle.radius).toBeGreaterThanOrEqual(12);
  expect(backStyle.font).toBeGreaterThanOrEqual(16);
  expect(backStyle.height).toBeGreaterThanOrEqual(44);
  const list = page.getByRole('region', { name: '복용 기록 선택' });
  await expect(list.getByRole('checkbox')).toHaveCount(3);
  await expect(
    page.getByRole('button', { name: '최근 1개월', exact: true }),
  ).toHaveAttribute('aria-pressed', 'true');
  await expect(list.getByText('미복용 시험약')).toHaveCount(0);
  await expect(list.getByText('제외된 처방약')).toHaveCount(0);
  // 기존 복약 탭의 자동 품목 연결은 별도 동작이며 보고서 선택 시점부터 검사한다.
  writes.length = 0;
  const excluded = list.getByRole('checkbox', {
    name: /2026-11-11.*합성 일반약/,
  });
  await excluded.uncheck();
  await page.getByRole('button', { name: '선택 완료 · 보고서 보기' }).click();
  await expect(
    page.getByRole('heading', { name: '진료용 복용 이력', exact: true }),
  ).toBeVisible();
  const group = page.getByRole('article', { name: '합성 일반약', exact: true });
  await expect(group.getByText('확인 1회', { exact: true })).toBeVisible();
  await expect(group.getByText('합성성분', { exact: true })).toBeVisible();
  await group.getByText('날짜별 내역', { exact: true }).click();
  await expect(group.getByText('반출 날짜', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('article', { name: '합성 처방약 250mg' }),
  ).toContainText('일정 날짜');
  await page.getByRole('button', { name: '선택 수정' }).click();
  await expect(excluded).not.toBeChecked();
  // 보고서 선택은 서버의 Y/N을 수정하지 않는다.
  await expect
    .poll(function noWrites() {
      return writes;
    })
    .toEqual([]);
  expect(
    await page.evaluate(function fits() {
      return document.documentElement.scrollWidth <= window.innerWidth;
    }),
  ).toBe(true);
});

// 기간 확대·직접 입력과 빈 선택 상태를 확인한다.
test('1·3·6개월과 직접 기간을 적용하고 선택을 초기화한다', async function periods({
  page,
}) {
  const writes = await prepare(page);
  await page.goto('/schedule/report');
  const list = page.getByRole('region', { name: '복용 기록 선택' });
  await expect(list.getByRole('checkbox')).toHaveCount(3);
  await page.getByRole('button', { name: '최근 3개월', exact: true }).click();
  await expect(list.getByRole('checkbox')).toHaveCount(3);
  await page.getByRole('button', { name: '최근 6개월', exact: true }).click();
  await expect(list.getByRole('checkbox')).toHaveCount(4);
  await page.getByRole('button', { name: '직접 설정', exact: true }).click();
  await page.getByLabel('시작일', { exact: true }).fill('2026-11-10');
  await page.getByLabel('종료일', { exact: true }).fill('2026-11-10');
  await expect(list.getByRole('checkbox')).toHaveCount(2);
  await list.getByRole('checkbox').first().uncheck();
  await list.getByRole('checkbox').last().uncheck();
  await expect(
    page.getByRole('button', { name: '선택 완료 · 보고서 보기' }),
  ).toBeDisabled();
  await page.getByRole('button', { name: '최근 1개월', exact: true }).click();
  await expect(list.getByRole('checkbox', { checked: true })).toHaveCount(3);
  await page.getByRole('button', { name: '직접 설정', exact: true }).click();
  await page.getByLabel('시작일', { exact: true }).fill('2026-12-01');
  await expect(page.getByRole('alert')).toContainText('기간');
  await expect(
    page.getByRole('button', { name: '선택 완료 · 보고서 보기' }),
  ).toBeDisabled();
  await page.getByLabel('시작일', { exact: true }).fill('2026-07-01');
  await page.getByLabel('종료일', { exact: true }).fill('2026-07-31');
  await expect(
    page.getByText('선택한 기간에 복용 확인 기록이 없어요.'),
  ).toBeVisible();
  expect(writes).toEqual([]);
});

// 어느 한 출처가 실패하면 정상 전체 보고서로 숨기지 않는다.
for (const source of ['activity', 'medications'] as const) {
  // 실패 출처와 관계없이 보고서 생성 차단을 확인한다.
  test(`${source} 조회 실패는 빈 이력이 아닌 오류로 표시한다`, async function failure({
    page,
  }) {
    await prepare(page, source);
    await page.goto('/schedule/report');
    await expect(
      page.getByRole('button', { name: '선택 완료 · 보고서 보기' }),
    ).toBeDisabled();
    await expect(
      page.getByText('두 종류의 기록을 모두 불러온 뒤 보고서를 볼 수 있어요.'),
    ).toBeVisible();
  });
}

// 다른 기기의 새 기록이 이미 검토한 보고서에 자동으로 들어가면 안 된다.
test('보고서 생성 후의 서버 변경은 선택 수정에서 다시 검토한다', async function snapshot({
  page,
}) {
  await prepare(page);
  await page.goto('/schedule/report');
  await expect(
    page.getByRole('region', { name: '복용 기록 선택' }).getByRole('checkbox'),
  ).toHaveCount(3);
  await page.getByRole('button', { name: '선택 완료 · 보고서 보기' }).click();
  await page.route(
    '**/medication-api/medications',
    async function changed(route) {
      await route.fulfill({
        json: { revision: 2, state: { version: 1, plans: [], records: [] } },
      });
    },
  );
  await page.clock.runFor(31000);
  await expect(
    page.getByRole('heading', { name: '진료용 복용 이력', exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole('article', { name: '합성 처방약 250mg' }),
  ).toBeVisible();
  await page.getByRole('button', { name: '선택 수정' }).click();
  await expect(
    page.getByRole('region', { name: '복용 기록 선택' }).getByRole('checkbox'),
  ).toHaveCount(2);
});

// 명시적 새로고침이 느린 수동 출처를 기다리지 않으면 오래된 기록이 확정된다.
test('새로고침은 두 출처의 새 응답이 끝날 때까지 생성을 차단한다', async function refreshBarrier({
  page,
}) {
  await prepare(page);
  await page.goto('/schedule/report');
  await expect(
    page.getByRole('region', { name: '복용 기록 선택' }).getByRole('checkbox'),
  ).toHaveCount(3);
  let release = function noop() {};
  const held = new Promise<void>(function wait(resolve) {
    release = resolve;
  });
  await page.route(
    '**/medication-api/medications',
    async function delayed(route) {
      await held;
      await route.fulfill({
        json: { revision: 2, state: { version: 1, plans: [], records: [] } },
      });
    },
  );
  await page.getByRole('button', { name: '새로고침', exact: true }).click();
  // 다른 합성 API가 응답한 뒤에도 수동 출처는 의도적으로 대기시킨다.
  await page.waitForTimeout(300);
  const disabled = await page
    .getByRole('button', { name: '선택 완료 · 보고서 보기' })
    .isDisabled();
  release();
  expect(disabled).toBe(true);
  await expect(
    page.getByRole('region', { name: '복용 기록 선택' }).getByRole('checkbox'),
  ).toHaveCount(2);
});

// 비정상 연결 중 바뀐 날짜를 이전 보고서 기록에 붙이지 않는다.
test('오프라인에서 직접 기간을 바꾸면 이전 보고서를 끝낸다', async function offlineRange({
  page,
  context,
}) {
  await prepare(page);
  await page.goto('/schedule/report');
  await expect(
    page.getByRole('region', { name: '복용 기록 선택' }).getByRole('checkbox'),
  ).toHaveCount(3);
  await page.getByRole('button', { name: '직접 설정', exact: true }).click();
  await page.getByRole('button', { name: '선택 완료 · 보고서 보기' }).click();
  await context.setOffline(true);
  await page.getByLabel('시작일', { exact: true }).fill('2026-07-01');
  await page.getByLabel('종료일', { exact: true }).fill('2026-07-31');
  await context.setOffline(false);
  await expect(
    page.getByText('선택한 기간에 복용 확인 기록이 없어요.'),
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: '진료용 복용 이력', exact: true }),
  ).toHaveCount(0);
});

// 선택 수정은 수동 약뿐 아니라 보관함 이력도 다시 읽어야 한다.
test('선택 수정에서 바뀐 보관함 기록을 다시 조회한다', async function activityRefresh({
  page,
}) {
  await prepare(page);
  await page.goto('/schedule/report');
  await expect(
    page.getByRole('region', { name: '복용 기록 선택' }).getByRole('checkbox'),
  ).toHaveCount(3);
  await page.getByRole('button', { name: '선택 완료 · 보고서 보기' }).click();
  await page.route(
    'https://activity.test/activity',
    async function changed(route) {
      await route.fulfill({ json: { pending: [], history: [], reviews: [] } });
    },
  );
  await page.getByRole('button', { name: '선택 수정' }).click();
  await expect(
    page.getByRole('region', { name: '복용 기록 선택' }).getByRole('checkbox'),
  ).toHaveCount(1);
});

// 두 조회 사이의 약 변경을 검출해 이전 기록에 새 약 성분을 붙이지 않는다.
test('제품 연결 조회 중 수동 약이 변경되면 보고서를 보류한다', async function productRace({
  page,
}) {
  await prepare(page);
  await page.route(
    '**/medication-api/medicine-products/links',
    async function links(route) {
      await page.route(
        '**/medication-api/medications',
        async function changed(next) {
          await next.fulfill({
            json: {
              revision: 2,
              state: { version: 1, plans: [], records: [] },
            },
          });
        },
      );
      await route.fulfill({
        json: {
          links: [
            {
              planId: 'plan1',
              medicineId: 'm1',
              productCode: '987654321',
              productName: '변경된 시험약',
            },
          ],
        },
      });
    },
  );
  await page.route(
    '**/medication-api/medicine-products/987654321',
    async function detail(route) {
      await route.fulfill({
        json: {
          productCode: '987654321',
          productName: '변경된 시험약',
          manufacturer: '',
          ingredients: [{ name: '다른 시험성분' }],
          indications: '',
          dosage: '',
          precautions: '',
          source: 'MFDS_PERMIT_DETAIL',
          checkedAt: '2026-11-12T00:00:00Z',
        },
      });
    },
  );
  await page.goto('/schedule/report');
  await expect(
    page.getByText(
      '조회 중 복용 기록이 변경됐어요. 새로고침 후 다시 선택해주세요.',
    ),
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: '선택 완료 · 보고서 보기' }),
  ).toBeDisabled();
});

// 수동 일정만 등록한 경우도 이미 지난 회차를 추정 항목으로 선택할 수 있어야 한다.
test('확인 기록 없는 수동 일정이 회차별 체크와 추정 요약으로 나타난다', async function registeredSchedule({
  page,
}) {
  const state: MedicationState = {
    version: 1,
    plans: [
      {
        id: 'scheduled-plan',
        title: '감기약',
        notes: '',
        endedOn: null,
        timing: {
          mode: 'regular',
          schedule: {
            startDate: '2026-11-10',
            endDate: '',
            weekdays: [0, 1, 2, 3, 4, 5, 6],
            times: ['09:00'],
          },
        },
        medicines: [
          {
            id: 'scheduled-medicine',
            name: '합성 일정약',
            strength: '',
            dose: '1정',
            timing: null,
          },
        ],
      },
    ],
    records: [],
  };
  const writes = await prepare(page, 'none', state);
  await page.goto('/schedule/report');
  const list = page.getByRole('region', { name: '복용 기록 선택' });
  await expect(list.getByRole('checkbox')).toHaveCount(5);
  await expect(list.getByText('일정 기반 추정', { exact: true })).toHaveCount(
    3,
  );
  const excluded = list.getByRole('checkbox', {
    name: /2026-11-11.*합성 일정약/,
  });
  await excluded.uncheck();
  await page.getByRole('button', { name: '선택 완료 · 보고서 보기' }).click();
  const group = page.getByRole('article', { name: '합성 일정약', exact: true });
  await expect(group.getByText('기록 2회', { exact: true })).toBeVisible();
  await expect(
    group.getByText('사용자 확인 0회 / 일정 기반 추정 2회', { exact: true }),
  ).toBeVisible();
  await group.getByText('날짜별 내역', { exact: true }).click();
  await expect(group).toContainText('09:00 예정');
  await page.getByRole('button', { name: '선택 수정' }).click();
  await expect(excluded).not.toBeChecked();
  await page.getByRole('button', { name: '직접 설정', exact: true }).click();
  await page.getByLabel('시작일', { exact: true }).fill('2026-11-12');
  await page.getByLabel('종료일', { exact: true }).fill('2026-11-12');
  await expect(list.getByRole('checkbox')).toHaveCount(1);
  await expect(list.getByRole('checkbox').first()).toBeChecked();
  expect(writes).toEqual([]);
  expect(state.records).toEqual([]);
});

// 열어 둔 선택 화면도 예정 시각을 지나면 새 회차를 표시해야 한다.
test('오전 9시가 되면 서버 기록 없이 오늘의 추정 회차가 나타난다', async function clockBoundary({
  page,
}) {
  const state: MedicationState = {
    version: 1,
    plans: [
      {
        id: 'clock-plan',
        title: '감기약',
        notes: '',
        endedOn: null,
        timing: {
          mode: 'regular',
          schedule: {
            startDate: '2026-11-10',
            endDate: '',
            weekdays: [0, 1, 2, 3, 4, 5, 6],
            times: ['09:00', '18:00'],
          },
        },
        medicines: [],
      },
    ],
    records: [],
  };
  const writes = await prepare(page, 'none', state);
  await page.clock.setSystemTime(new Date('2026-11-11T23:59:45Z'));
  await page.goto('/schedule/report');
  const list = page.getByRole('region', { name: '복용 기록 선택' });
  await expect(list.getByRole('checkbox')).toHaveCount(6);
  await page.clock.runFor(30000);
  await expect(list.getByRole('checkbox')).toHaveCount(7);
  await expect(
    list.getByRole('checkbox', {
      name: /2026-11-12.*감기약.*09:00 예정.*일정 기반 추정/,
    }),
  ).toBeChecked();
  await expect(
    list.getByRole('checkbox', {
      name: /2026-11-11.*감기약.*18:00 예정.*일정 기반 추정/,
    }),
  ).toHaveCount(1);
  expect(writes).toEqual([]);
});
