import { test, expect } from '@playwright/test';
import { packageSchema } from '../src/data';

// 합성 재고로 신규 확인과 실제 수량 입력의 서버 왕복을 검증한다.
test('신규 약 정보와 반출 미복용 이력을 저장한다', async function flow({
  page,
}) {
  const item = packageSchema.parse({
    package_id: 'p1',
    box_id: 'test-box',
    kind: 'OTC',
    state: 'STORED',
    name: '합성 시험약',
    first_registration_pending: true,
    inventory: { stock_enabled: true, initial_qty: null, estimated_qty: null },
    medicines: [
      {
        medicine_id: 'm1',
        name: '합성 시험약',
        form: 'TABLET',
        ingredients: [],
      },
    ],
  });
  let confirmed = false;
  // 목록은 저장된 합성 상태만 반영한다.
  await page.route('**/api/**', async function inventory(route) {
    await route.fulfill({
      json: route.request().url().includes('/inventory')
        ? {
            box: { box_id: 'test-box' },
            packages: [item],
            inventory_status: 'CONFIRMED',
            catalog_complete: true,
          }
        : { alerts: [] },
    });
  });
  const event = {
    eventId: 'e1',
    packageId: 'p1',
    medicineName: '합성 시험약',
    occurredAt: '2026-11-10T00:00:00Z',
    status: 'PENDING',
    quantity: null as number | null,
    unit: 'TABLET',
    recordedAt: null as string | null,
    movementCertainty: 'ESTIMATED',
  };
  // API에 전송된 본문을 검사한 뒤에만 성공 응답을 돌려준다.
  await page.route('https://activity.test/**', async function activity(route) {
    const path = new URL(route.request().url()).pathname;
    if (path === '/packages/p1/initial') {
      expect(route.request().postDataJSON()).toEqual({
        condition: 'USED',
        actualExpiry: '2027-12-31',
        remainingQty: 8,
      });
      item.first_registration_pending = false;
      await route.fulfill({
        json: { packageId: 'p1', firstRegistrationPending: false },
      });
    } else if (path.endsWith('/confirm')) {
      const result = event.eventId === 'e1' ? 'NOT_TAKEN' : 'TAKEN';
      const count = event.eventId === 'e1' ? null : 2;
      expect(route.request().postDataJSON()).toEqual({
        result,
        quantity: count,
      });
      confirmed = true;
      event.status = result;
      event.quantity = count;
      event.recordedAt = '2026-11-10T01:00:00Z';
      await route.fulfill({ json: { event } });
    } else {
      await route.fulfill({
        json: {
          pending: confirmed ? [] : [event],
          history: confirmed ? [event] : [],
        },
      });
    }
  });
  await page.goto('/?new_package=p1');
  await expect(
    page.getByText('처음 등록된 약입니다', { exact: true }),
  ).toBeVisible();
  await page.getByLabel('약품 상태').selectOption('USED');
  await page.getByLabel('유통기한', { exact: true }).fill('2027-12-31');
  await page.getByLabel('잔여 수량').fill('8');
  await page.getByRole('button', { name: '기본 정보 확인' }).click();
  await expect(
    page.getByText('처음 등록된 약입니다', { exact: true }),
  ).toHaveCount(0);
  await page.goto('/schedule?out_event=e1');
  await expect(page.getByText('복용 확인 필요', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: /N.*복용/ }).click();
  await expect(page.getByText('확인할 반출 기록이 없어요.')).toBeVisible();
  await expect(
    page.locator('#outbound-history').getByText('미복용', { exact: true }),
  ).toBeVisible();
  confirmed = false;
  event.eventId = 'e2';
  event.status = 'PENDING';
  event.recordedAt = null;
  await page.goto('/schedule?out_event=e2');
  await page.reload();
  await page.getByRole('button', { name: 'Y · 복용/사용' }).click();
  await page.getByRole('spinbutton').fill('2');
  await page.getByRole('dialog').getByRole('button', { name: /저장/ }).click();
  await expect(page.getByText('확인할 반출 기록이 없어요.')).toBeVisible();
});

// 새 약 수량 보완과 촬영 확인의 실패·재시도·목록 갱신을 함께 검증한다.
test('새 약 수량과 촬영 결과를 서버에 저장한다', async function recovery({
  page,
}) {
  const medicine = {
    medicine_id: '000000001',
    product_code: '000000001',
    name: '합성 회귀 시험정',
    form: 'TABLET',
    ingredients: [],
  };
  const item = packageSchema.parse({
    package_id: 'p2',
    box_id: 'test-box',
    kind: 'OTC',
    state: 'STORED',
    name: medicine.name,
    first_registration_pending: false,
    inventory: { stock_enabled: true, initial_qty: null, estimated_qty: null },
    medicines: [medicine],
  });
  let reviewOpen = true;
  let rejectOnce = true;
  // 실제 입력을 저장한 뒤 재조회 응답에 반영한다.
  await page.route('**/api/**', async function inventory(route) {
    await route.fulfill({
      json: route.request().url().includes('/inventory')
        ? {
            box: {
              box_id: 'test-box',
              dimensions: { width: 305, depth: 165, height: 190 },
            },
            packages: [item],
            catalog_medicines: [medicine],
            catalog_complete: true,
            inventory_status: 'CONFIRMED',
          }
        : { alerts: [] },
    });
  });
  // 첫 정정의 충돌 응답을 재현해 입력이 사라지지 않는지 검사한다.
  await page.route('https://activity.test/**', async function activity(route) {
    const path = new URL(route.request().url()).pathname;
    if (path === '/packages/p2/initial') {
      expect(route.request().postDataJSON()).toEqual({
        condition: 'NEW',
        actualExpiry: null,
        remainingQty: 10,
      });
      item.inventory = {
        stock_enabled: true,
        initial_qty: 10,
        estimated_qty: 10,
      };
      await route.fulfill({
        json: { packageId: 'p2', firstRegistrationPending: false },
      });
    } else if (path === '/activity/capture-1/confirm') {
      expect(route.request().postDataJSON()).toEqual({
        review: { productCode: '000000001', direction: 'OUT' },
      });
      if (rejectOnce) {
        rejectOnce = false;
        await route.fulfill({ status: 409, json: {} });
      } else {
        reviewOpen = false;
        await route.fulfill({
          json: { eventId: 'capture-1', processing_status: 'COMPLETED' },
        });
      }
    } else {
      await route.fulfill({
        json: {
          pending: [],
          history: [],
          reviews: reviewOpen
            ? [
                {
                  eventId: 'capture-1',
                  occurredAt: '2026-11-11T00:00:00Z',
                  reason: 'OCR_TIMEOUT',
                },
              ]
            : [],
        },
      });
    }
  });
  await page.goto('/?new_package=p2');
  await page.getByLabel('포장에 표시된 전체 수량', { exact: false }).fill('10');
  await page.getByRole('button', { name: '기본 정보 확인' }).click();
  await expect(
    page.getByLabel('포장에 표시된 전체 수량', { exact: false }),
  ).toHaveCount(0);
  await page.goto('/schedule');
  await page.getByLabel('촬영한 약품').selectOption('000000001');
  await page.getByLabel('실제 동작').selectOption('OUT');
  await page.getByRole('button', { name: '촬영 결과 확인' }).click();
  await expect(
    page.getByText('저장 결과를 확인하지 못했어요.', { exact: false }),
  ).toBeVisible();
  await expect(page.getByLabel('촬영한 약품')).toHaveValue('000000001');
  await page.getByRole('button', { name: '촬영 결과 확인' }).click();
  await expect(page.getByLabel('촬영한 약품')).toHaveCount(0);
});
