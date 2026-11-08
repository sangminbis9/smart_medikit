import { expect, test, type BrowserContext, type Page } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import {
  emptyMedicationState,
  type MedicationState,
} from '../src/medicationPlans';

const fakePassword = 'synthetic-e2e-password-only';
const fakeToken = 'synthetic-e2e-memory-token';
const fixedTime = new Date('2026-11-08T13:00:00Z');
const publicMode = process.env.MEDKIT_E2E_MEDICATION_PUBLIC === '1';

type MockApi = {
  revision: number;
  state: MedicationState;
  rejectWrites: boolean;
  rejectReads: boolean;
  corruptReads: boolean;
  putRequests: number;
  acceptedWrites: number;
  requests: number;
  sessionRequests: number;
  authorizationHeaders: number;
};

// 모든 테스트 페이지가 같은 합성 서버 데이터와 버전을 사용하게 한다.
async function mockMedicationApi(context: BrowserContext): Promise<MockApi> {
  const server: MockApi = {
    revision: 0,
    state: emptyMedicationState(),
    rejectWrites: false,
    rejectReads: false,
    corruptReads: false,
    putRequests: 0,
    acceptedWrites: 0,
    requests: 0,
    sessionRequests: 0,
    authorizationHeaders: 0,
  };
  // 실제 AWS 대신 인증과 조건부 저장 응답만 합성한다.
  await context.route('**/medication-api/**', async function respond(route) {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    server.requests++;
    if (request.headers().authorization) server.authorizationHeaders++;
    if (path.endsWith('/session') && request.method() === 'POST') {
      server.sessionRequests++;
      if (request.postDataJSON().password !== fakePassword)
        await route.fulfill({
          status: 401,
          json: { error: 'INVALID_PASSWORD' },
        });
      else
        await route.fulfill({
          json: { token: fakeToken, expires_at: '2099-01-01T00:00:00Z' },
        });
      return;
    }
    if (
      !publicMode &&
      request.headers().authorization !== `Bearer ${fakeToken}`
    ) {
      await route.fulfill({ status: 401, json: { error: 'UNAUTHORIZED' } });
      return;
    }
    if (path.endsWith('/medications') && request.method() === 'GET') {
      if (server.rejectReads) {
        await route.fulfill({
          status: 503,
          json: { error: 'SYNTHETIC_UNAVAILABLE' },
        });
        return;
      }
      await route.fulfill({
        json: server.corruptReads
          ? {
              revision: server.revision,
              state: { version: 1, plans: 'broken', records: [] },
            }
          : { revision: server.revision, state: server.state },
      });
      return;
    }
    if (path.endsWith('/medications') && request.method() === 'PUT') {
      server.putRequests++;
      const body = request.postDataJSON() as {
        expected_revision: number;
        state: MedicationState;
      };
      if (server.rejectWrites)
        await route.fulfill({
          status: 500,
          json: { error: 'SYNTHETIC_FAILURE' },
        });
      else if (body.expected_revision !== server.revision)
        await route.fulfill({
          status: 409,
          json: { error: 'REVISION_CONFLICT' },
        });
      else {
        server.state = structuredClone(body.state);
        server.revision++;
        server.acceptedWrites++;
        await route.fulfill({
          json: { revision: server.revision, state: server.state },
        });
      }
      return;
    }
    await route.fulfill({ status: 404, json: { error: 'NOT_FOUND' } });
  });
  return server;
}

// 새 페이지와 새로고침 후에는 메모리 토큰을 재사용하지 않고 다시 인증한다.
async function unlock(page: Page): Promise<void> {
  await expect(page.getByLabel('공통 비밀번호', { exact: true })).toBeVisible();
  await page.getByLabel('공통 비밀번호', { exact: true }).fill(fakePassword);
  await page.getByRole('button', { name: '잠금 해제', exact: true }).click();
  await expect(
    page.getByRole('button', { name: '복용약 등록', exact: true }),
  ).toBeVisible();
}

// 합성 기준일로 복약 화면을 열고 보호된 목록에 접근한다.
async function openSchedule(page: Page): Promise<void> {
  await page.clock.setFixedTime(fixedTime);
  await page.goto('/schedule');
  await unlock(page);
}

// 충돌 검증에 필요한 단일 합성 처방을 실제 등록 UI로 만든다.
async function registerSimplePlan(page: Page, title: string): Promise<void> {
  await page.getByRole('button', { name: '복용약 등록', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('묶음 이름', { exact: false }).fill(title);
  await dialog.getByLabel('달력에 일정 등록').check();
  await dialog.getByLabel('시간 1', { exact: true }).fill('08:00');
  await dialog.getByRole('button', { name: '약 추가', exact: true }).click();
  await dialog.getByLabel('약 이름', { exact: false }).fill('합성 약 A');
  await dialog.getByLabel('1회 복용량', { exact: false }).fill('초기 입력');
  await dialog
    .getByRole('button', { name: '복용약 등록', exact: true })
    .click();
  await expect(dialog).toHaveCount(0);
}

// 비밀번호 보호 설정에서는 기존 인증·공유·오류 시나리오를 유지한다.
test.describe('공통 비밀번호 모드', function privateModeTests() {
  test.skip(publicMode, '공개 시연 모드에서는 별도 공개 시나리오를 실행한다.');

  // 처방 등록·복용 확인·다른 페이지 공유·수정·종료·삭제를 검증한다.
  test('AWS 처방 묶음 등록부터 다른 페이지 공유와 삭제까지', async function medicationFlow({
    page,
    context,
  }, testInfo) {
    const server = await mockMedicationApi(context);
    const errors: string[] = [];
    // 사용자 입력이나 응답 본문 대신 실행 오류만 확인한다.
    page.on('pageerror', function runtimeError(error) {
      errors.push(error.message);
    });
    // 정상 흐름의 콘솔 오류도 검사한다.
    page.on('console', function consoleError(message) {
      if (message.type() === 'error') errors.push(message.text());
    });
    await openSchedule(page);
    await expect(page).toHaveTitle(/MEDKIT|메디킷|약품/i);
    await expect(page.locator('vite-error-overlay')).toHaveCount(0);
    await page
      .getByRole('button', { name: '복용약 등록', exact: true })
      .click();
    const dialog = page.getByRole('dialog');
    await dialog
      .getByLabel('묶음 이름', { exact: false })
      .fill('합성 감기 처방약');
    const common = dialog.getByRole('group', {
      name: '공통 복용법',
      exact: true,
    });
    await common.getByLabel('달력에 일정 등록').check();
    await common.getByLabel('시간 1', { exact: true }).fill('08:00');
    await common
      .getByRole('button', { name: '+ 시간 추가', exact: true })
      .click();
    await common.getByLabel('시간 2', { exact: true }).fill('20:00');
    for (const [index, name] of [
      '합성 약 A',
      '합성 약 B',
      '합성 약 C',
      '합성 필요 시 약',
    ].entries()) {
      await dialog
        .getByRole('button', { name: '약 추가', exact: true })
        .click();
      const medicine = dialog.getByRole('group', {
        name: `약 ${index + 1}`,
        exact: true,
      });
      await medicine.getByLabel('약 이름', { exact: false }).fill(name);
      await medicine
        .getByLabel('1회 복용량', { exact: false })
        .fill('처방 안내대로');
      if (index === 2) {
        await medicine.getByLabel('이 약만 다르게 설정').check();
        await medicine
          .getByRole('button', { name: '시간 1 삭제', exact: true })
          .click();
      }
      if (index === 3) {
        await medicine.getByLabel('이 약만 다르게 설정').check();
        await medicine
          .getByLabel('복용 방식', { exact: false })
          .selectOption('as_needed');
      }
    }
    await dialog
      .getByLabel('처방받은 안내', { exact: true })
      .fill('합성 시험 자료');
    const evidenceDir = resolve('..', 'local_data', 'manual-medication-qa');
    await mkdir(evidenceDir, { recursive: true });
    // 긴 등록창의 첫 화면과 가로폭을 기록한다.
    await dialog.evaluate(function top(element) {
      element.scrollTop = 0;
    });
    await page.screenshot({
      path: resolve(evidenceDir, `${testInfo.project.name}-form.png`),
    });
    // 좁은 화면에서도 등록창이 가로로 넘치지 않는다.
    expect(
      await dialog.evaluate(function width(element) {
        return element.scrollWidth <= element.clientWidth;
      }),
    ).toBe(true);
    await dialog
      .getByRole('button', { name: '복용약 등록', exact: true })
      .click();
    await expect(dialog).toHaveCount(0);
    const card = page.getByRole('article', {
      name: '합성 감기 처방약',
      exact: true,
    });
    await expect(card).toBeVisible();
    expect(server.state.plans[0].medicines).toHaveLength(4);
    await expect(page.locator('.manual-dose-row')).toHaveCount(2);
    const morning = page.getByRole('button', {
      name: '합성 감기 처방약 08:00 복용 확인',
      exact: true,
    });
    const evening = page.getByRole('button', {
      name: '합성 감기 처방약 20:00 복용 확인',
      exact: true,
    });
    await expect(morning).toContainText('합성 약 A');
    await expect(morning).toContainText('합성 약 B');
    await expect(morning).not.toContainText('합성 약 C');
    await expect(morning).not.toContainText('합성 필요 시 약');
    await expect(evening).toContainText('합성 약 C');
    await expect(evening).not.toContainText('합성 필요 시 약');
    await morning.click();
    await dialog
      .getByRole('radio', { name: '일부 약만 복용', exact: true })
      .check();
    await dialog.getByRole('checkbox', { name: /합성 약 A/ }).check();
    await dialog.getByRole('button', { name: '복용 기록 저장' }).click();
    await expect(morning).toContainText('일부 복용 확인');
    await evening.click();
    await dialog
      .getByRole('radio', { name: '예정된 약 모두 복용', exact: true })
      .check();
    await dialog.getByRole('button', { name: '복용 기록 저장' }).click();
    await expect(evening).toContainText('모두 복용 확인');
    await page.reload();
    await expect(page.locator('.medication-plan-card')).toHaveCount(0);
    await unlock(page);
    await expect(morning).toContainText('일부 복용 확인');
    await expect(evening).toContainText('모두 복용 확인');
    await morning.click();
    await expect(
      dialog.getByRole('checkbox', { name: /합성 약 A/ }),
    ).toBeChecked();
    await expect(
      dialog.getByRole('checkbox', { name: /합성 약 B/ }),
    ).not.toBeChecked();
    await dialog.getByRole('button', { name: '닫기', exact: true }).click();
    const other = await context.newPage();
    await openSchedule(other);
    await expect(
      other.getByRole('article', { name: '합성 감기 처방약', exact: true }),
    ).toBeVisible();
    await expect(
      other.getByRole('button', {
        name: '합성 감기 처방약 08:00 복용 확인',
        exact: true,
      }),
    ).toContainText('일부 복용 확인');
    await other.close();
    // 제목만 바꾸면 기존 복용 확인 기록은 유지된다.
    await card.getByRole('button', { name: '수정', exact: true }).click();
    await dialog
      .getByLabel('묶음 이름', { exact: false })
      .fill('합성 처방 묶음');
    await dialog
      .getByRole('button', { name: '변경사항 저장', exact: true })
      .click();
    const editedCard = page.getByRole('article', {
      name: '합성 처방 묶음',
      exact: true,
    });
    await expect(
      page.getByRole('button', {
        name: '합성 처방 묶음 08:00 복용 확인',
        exact: true,
      }),
    ).toContainText('일부 복용 확인');
    await editedCard.locator('summary').click();
    await page.screenshot({
      path: resolve(evidenceDir, `${testInfo.project.name}-registered.png`),
    });
    // 수동 복용약은 공개 재고 목록과 브라우저 저장소에 복제하지 않는다.
    await page
      .getByRole('link', { name: '홈', exact: true })
      .filter({ visible: true })
      .click();
    await expect(page.getByText('합성 처방 묶음', { exact: true })).toHaveCount(
      0,
    );
    const stored = await page.evaluate(function persisted() {
      return JSON.stringify({
        local: { ...localStorage },
        session: { ...sessionStorage },
      });
    });
    expect(stored).not.toContain('합성 처방 묶음');
    expect(stored).not.toContain(fakePassword);
    expect(stored).not.toContain(fakeToken);
    await page
      .getByRole('link', { name: '복약', exact: true })
      .filter({ visible: true })
      .click();
    await editedCard
      .getByRole('button', { name: '복용 종료', exact: true })
      .click();
    await page.getByRole('button', { name: '2026-11-09', exact: true }).click();
    await expect(page.locator('.manual-dose-row')).toHaveCount(0);
    await page.locator('.medication-ended > summary').click();
    await editedCard
      .getByRole('button', { name: '다시 복용 중으로', exact: true })
      .click();
    await expect(page.locator('.manual-dose-row')).toHaveCount(2);
    await editedCard.getByRole('button', { name: '삭제', exact: true }).click();
    await dialog.getByRole('button', { name: '삭제하기', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await page.reload();
    await unlock(page);
    await expect(page.locator('.medication-plan-card')).toHaveCount(0);
    await expect(page.locator('.manual-dose-row')).toHaveCount(0);
    expect(server.state.plans).toHaveLength(0);
    expect(server.state.records).toHaveLength(0);
    // 페이지 전체도 모바일 가로폭을 넘지 않는다.
    expect(
      await page.evaluate(function width() {
        return document.documentElement.scrollWidth <= window.innerWidth;
      }),
    ).toBe(true);
    expect(errors).toEqual([]);
  });

  // 다른 기기에서 편집한 약을 오래된 편집창과 확인창이 덮어쓰지 않는다.
  test('다른 기기 변경과 오래된 편집·복용 확인 충돌', async function concurrentPages({
    page,
    context,
  }) {
    const server = await mockMedicationApi(context);
    await openSchedule(page);
    await registerSimplePlan(page, '합성 공동 편집');
    const other = await context.newPage();
    await openSchedule(other);
    const dialog = page.getByRole('dialog');
    const otherDialog = other.getByRole('dialog');
    await page
      .getByRole('article', { name: '합성 공동 편집', exact: true })
      .getByRole('button', { name: '수정', exact: true })
      .click();
    await other
      .getByRole('article', { name: '합성 공동 편집', exact: true })
      .getByRole('button', { name: '수정', exact: true })
      .click();
    await otherDialog
      .getByLabel('1회 복용량', { exact: false })
      .fill('다른 기기의 최신 입력');
    await otherDialog
      .getByRole('button', { name: '변경사항 저장', exact: true })
      .click();
    await expect(otherDialog).toHaveCount(0);
    await dialog
      .getByRole('button', { name: '변경사항 저장', exact: true })
      .click();
    await expect(dialog.getByRole('alert')).toContainText(
      /다른 기기|저장하지 못/,
    );
    await expect(dialog.getByLabel('1회 복용량', { exact: false })).toHaveValue(
      '초기 입력',
    );
    expect(server.state.plans[0].medicines[0].dose).toBe(
      '다른 기기의 최신 입력',
    );
    await dialog.getByRole('button', { name: '닫기', exact: true }).click();
    await page
      .getByRole('button', { name: '복용약 새로고침', exact: true })
      .click();
    await expect(page.locator('.manual-dose-row')).toContainText(
      '다른 기기의 최신 입력',
    );
    await page.locator('.manual-dose-row').click();
    await other
      .getByRole('article', { name: '합성 공동 편집', exact: true })
      .getByRole('button', { name: '수정', exact: true })
      .click();
    await otherDialog
      .getByLabel('1회 복용량', { exact: false })
      .fill('다시 바뀐 입력');
    await otherDialog
      .getByRole('button', { name: '변경사항 저장', exact: true })
      .click();
    await expect(otherDialog).toHaveCount(0);
    await dialog
      .getByRole('button', { name: '복용 기록 저장', exact: true })
      .click();
    await expect(dialog.getByRole('alert')).toContainText(
      /다른 기기|기록을 저장하지 못/,
    );
    expect(server.state.records).toHaveLength(0);
    await other.close();
  });

  // 강제로 만든 서버 버전 충돌도 최신 데이터 보존과 초안 유지로 처리한다.
  test('409 응답은 최신 서버 목록을 보존', async function revisionConflict({
    page,
    context,
  }) {
    const server = await mockMedicationApi(context);
    await openSchedule(page);
    await registerSimplePlan(page, '합성 버전 충돌');
    const dialog = page.getByRole('dialog');
    await page
      .getByRole('article', { name: '합성 버전 충돌', exact: true })
      .getByRole('button', { name: '수정', exact: true })
      .click();
    await dialog
      .getByLabel('묶음 이름', { exact: false })
      .fill('저장되면 안 되는 초안');
    // 외부 기기의 저장 직후 상태를 UI 이벤트 없이 합성한다.
    server.state.plans[0].title = '다른 기기가 저장한 제목';
    server.revision++;
    const accepted = server.acceptedWrites;
    await dialog
      .getByRole('button', { name: '변경사항 저장', exact: true })
      .click();
    await expect(dialog.getByRole('alert')).toContainText('다른 기기');
    await expect(dialog.getByLabel('묶음 이름', { exact: false })).toHaveValue(
      '저장되면 안 되는 초안',
    );
    expect(server.acceptedWrites).toBe(accepted);
    expect(server.state.plans[0].title).toBe('다른 기기가 저장한 제목');
    await dialog.getByRole('button', { name: '닫기', exact: true }).click();
    await expect(
      page.getByRole('article', {
        name: '다른 기기가 저장한 제목',
        exact: true,
      }),
    ).toBeVisible();
  });

  // 개별 약 이름 없는 묶음과 필요 시 복용을 허용하며 시연은 서버와 분리한다.
  test('묶음만 등록·필요 시 복용·시연 분리', async function unknownPlan({
    page,
    context,
  }) {
    const server = await mockMedicationApi(context);
    await openSchedule(page);
    await page
      .getByRole('button', { name: '복용약 등록', exact: true })
      .click();
    const dialog = page.getByRole('dialog');
    await dialog
      .getByLabel('묶음 이름', { exact: false })
      .fill('합성 이름 모르는 묶음');
    await dialog
      .getByLabel('복용 방식', { exact: false })
      .selectOption('as_needed');
    await dialog
      .getByRole('button', { name: '복용약 등록', exact: true })
      .click();
    await expect(dialog).toHaveCount(0);
    await expect(page.locator('.manual-dose-row')).toHaveCount(0);
    await page.reload();
    await unlock(page);
    await expect(
      page.getByRole('article', { name: '합성 이름 모르는 묶음', exact: true }),
    ).toBeVisible();
    const savedState = structuredClone(server.state);
    const writes = server.putRequests;
    await page
      .getByRole('link', { name: '홈', exact: true })
      .filter({ visible: true })
      .click();
    await page
      .getByRole('button', { name: '시연 데이터 보기', exact: true })
      .filter({ visible: true })
      .last()
      .click();
    await page
      .getByRole('link', { name: '복약', exact: true })
      .filter({ visible: true })
      .click();
    await expect(page.locator('.medication-plan-card')).toHaveCount(0);
    await page
      .getByRole('button', { name: '복용약 등록', exact: true })
      .click();
    await dialog
      .getByLabel('묶음 이름', { exact: false })
      .fill('합성 시연 전용');
    await dialog
      .getByRole('button', { name: '복용약 등록', exact: true })
      .click();
    await expect(
      page.getByRole('article', { name: '합성 시연 전용', exact: true }),
    ).toBeVisible();
    expect(server.state).toEqual(savedState);
    expect(server.putRequests).toBe(writes);
    await page.getByRole('button', { name: '시연 종료', exact: true }).click();
    await unlock(page);
    await expect(
      page.getByRole('article', { name: '합성 이름 모르는 묶음', exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole('article', { name: '합성 시연 전용', exact: true }),
    ).toHaveCount(0);
  });

  // 서버 오류로 저장이 실패하면 팝업과 입력을 유지하며 재시도할 수 있다.
  test('서버 500 저장 실패는 입력 유지', async function serverFailure({
    page,
    context,
  }) {
    const server = await mockMedicationApi(context);
    await openSchedule(page);
    server.rejectWrites = true;
    await page
      .getByRole('button', { name: '복용약 등록', exact: true })
      .click();
    const dialog = page.getByRole('dialog');
    await dialog
      .getByLabel('묶음 이름', { exact: false })
      .fill('합성 저장 실패');
    await dialog
      .getByRole('button', { name: '복용약 등록', exact: true })
      .click();
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel('묶음 이름', { exact: false })).toHaveValue(
      '합성 저장 실패',
    );
    await expect(dialog.getByRole('alert')).toBeVisible();
    await expect(
      dialog.getByRole('button', { name: '복용약 등록', exact: true }),
    ).toBeEnabled();
    await expect(page.locator('.medication-plan-card')).toHaveCount(0);
    expect(server.state.plans).toHaveLength(0);
    server.rejectWrites = false;
    await dialog
      .getByRole('button', { name: '복용약 등록', exact: true })
      .click();
    await expect(dialog).toHaveCount(0);
    await expect(
      page.getByRole('article', { name: '합성 저장 실패', exact: true }),
    ).toBeVisible();
  });

  // 401과 손상된 조회 응답은 잠금 해제나 빈 목록 쓰기를 허용하지 않는다.
  test('잘못된 비밀번호와 손상 응답 거부', async function rejectedResponse({
    page,
    context,
  }) {
    const server = await mockMedicationApi(context);
    await page.goto('/schedule');
    await page
      .getByLabel('공통 비밀번호', { exact: true })
      .fill('wrong-synthetic-password');
    await page.getByRole('button', { name: '잠금 해제', exact: true }).click();
    await expect(page.getByRole('alert')).toContainText('비밀번호');
    await expect(
      page.getByRole('button', { name: '복용약 등록', exact: true }),
    ).toHaveCount(0);
    await expect(page.getByLabel('공통 비밀번호', { exact: true })).toHaveValue(
      '',
    );
    server.corruptReads = true;
    await page.getByLabel('공통 비밀번호', { exact: true }).fill(fakePassword);
    await page.getByRole('button', { name: '잠금 해제', exact: true }).click();
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(
      page.getByRole('button', { name: '복용약 등록', exact: true }),
    ).toHaveCount(0);
    expect(server.putRequests).toBe(0);
    server.corruptReads = false;
    await unlock(page);
    await expect(page.locator('.medication-plan-card')).toHaveCount(0);
  });
});

// 공개 시연 설정은 비밀번호·접근 토큰 없이 같은 서버 상태를 사용한다.
test.describe('완전 공개 시연 모드', function publicModeTests() {
  test.skip(!publicMode, '비밀번호 모드에서는 기존 보호 시나리오를 실행한다.');

  // 공개 목록이 로드될 때까지 기다리며 인증 입력이 나타나지 않는지 확인한다.
  async function openPublicSchedule(page: Page): Promise<void> {
    await page.clock.setFixedTime(fixedTime);
    await page.goto('/schedule');
    await expect(
      page.getByRole('button', { name: '복용약 등록', exact: true }),
    ).toBeVisible();
    await expect(page.getByLabel('공통 비밀번호', { exact: true })).toHaveCount(
      0,
    );
    await expect(
      page.getByRole('button', { name: '복용약 잠그기', exact: true }),
    ).toHaveCount(0);
  }

  // 누구나 인증 없이 등록·공유·삭제하며 새로고침 후에도 서버 목록을 다시 읽는다.
  test('비밀번호 없이 등록·새로고침·다른 페이지 공유·삭제', async function publicFlow({
    page,
    context,
  }, testInfo) {
    const server = await mockMedicationApi(context);
    const errors: string[] = [];
    // 공개 정상 흐름에서도 렌더링 오류를 허용하지 않는다.
    page.on('pageerror', function runtimeError(error) {
      errors.push(error.message);
    });
    // API 실패를 의도하지 않은 흐름의 콘솔 오류도 검사한다.
    page.on('console', function consoleError(message) {
      if (message.type() === 'error') errors.push(message.text());
    });
    await openPublicSchedule(page);
    await expect(page).toHaveTitle(/MEDKIT|메디킷|약품/i);
    await expect(page.locator('vite-error-overlay')).toHaveCount(0);
    await expect(page.getByText(/공개 시연/).first()).toBeVisible();
    await registerSimplePlan(page, '합성 공개 처방');
    const card = page.getByRole('article', {
      name: '합성 공개 처방',
      exact: true,
    });
    await expect(card).toBeVisible();
    await page.reload();
    await expect(card).toBeVisible();
    await expect(page.getByLabel('공통 비밀번호', { exact: true })).toHaveCount(
      0,
    );
    const other = await context.newPage();
    await openPublicSchedule(other);
    const otherCard = other.getByRole('article', {
      name: '합성 공개 처방',
      exact: true,
    });
    await expect(otherCard).toBeVisible();
    const evidenceDir = resolve('..', 'local_data', 'manual-medication-qa');
    await mkdir(evidenceDir, { recursive: true });
    await page.screenshot({
      path: resolve(evidenceDir, `${testInfo.project.name}-public.png`),
    });
    await otherCard.getByRole('button', { name: '삭제', exact: true }).click();
    await other
      .getByRole('dialog')
      .getByRole('button', { name: '삭제하기', exact: true })
      .click();
    await expect(other.getByRole('dialog')).toHaveCount(0);
    await page
      .getByRole('button', { name: '복용약 새로고침', exact: true })
      .click();
    await expect(card).toHaveCount(0);
    await expect(page.locator('.manual-dose-row')).toHaveCount(0);
    expect(server.state.plans).toHaveLength(0);
    expect(server.sessionRequests).toBe(0);
    expect(server.authorizationHeaders).toBe(0);
    // 공개 모드에서도 화면 밖으로 내용이 밀려나지 않는다.
    expect(
      await page.evaluate(function width() {
        return document.documentElement.scrollWidth <= window.innerWidth;
      }),
    ).toBe(true);
    expect(errors).toEqual([]);
    await other.close();
  });

  // 공개 접근이라도 실패를 성공으로 표시하거나 최신 서버 상태를 덮어쓰지 않는다.
  test('초기 조회 재시도·저장 실패 입력 유지·409 보존', async function publicFailures({
    page,
    context,
  }) {
    const server = await mockMedicationApi(context);
    server.rejectReads = true;
    await page.clock.setFixedTime(fixedTime);
    await page.goto('/schedule');
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(
      page.getByRole('button', { name: '복용약 등록', exact: true }),
    ).toHaveCount(0);
    server.rejectReads = false;
    await page.getByRole('button', { name: '다시 시도', exact: true }).click();
    await expect(
      page.getByRole('button', { name: '복용약 등록', exact: true }),
    ).toBeVisible();
    server.rejectWrites = true;
    await page
      .getByRole('button', { name: '복용약 등록', exact: true })
      .click();
    const dialog = page.getByRole('dialog');
    await dialog
      .getByLabel('묶음 이름', { exact: false })
      .fill('합성 공개 실패');
    await dialog
      .getByRole('button', { name: '복용약 등록', exact: true })
      .click();
    await expect(dialog.getByRole('alert')).toBeVisible();
    await expect(dialog.getByLabel('묶음 이름', { exact: false })).toHaveValue(
      '합성 공개 실패',
    );
    expect(server.state.plans).toHaveLength(0);
    server.rejectWrites = false;
    await dialog
      .getByRole('button', { name: '복용약 등록', exact: true })
      .click();
    await expect(dialog).toHaveCount(0);
    await page
      .getByRole('article', { name: '합성 공개 실패', exact: true })
      .getByRole('button', { name: '수정', exact: true })
      .click();
    await dialog
      .getByLabel('묶음 이름', { exact: false })
      .fill('오래된 공개 초안');
    // 다른 방문자가 먼저 저장한 최신 revision을 합성한다.
    server.state.plans[0].title = '다른 방문자의 최신 제목';
    server.revision++;
    const accepted = server.acceptedWrites;
    await dialog
      .getByRole('button', { name: '변경사항 저장', exact: true })
      .click();
    await expect(dialog.getByRole('alert')).toContainText('다른 기기');
    await expect(dialog.getByLabel('묶음 이름', { exact: false })).toHaveValue(
      '오래된 공개 초안',
    );
    expect(server.state.plans[0].title).toBe('다른 방문자의 최신 제목');
    expect(server.acceptedWrites).toBe(accepted);
    await dialog.getByRole('button', { name: '닫기', exact: true }).click();
    await expect(
      page.getByRole('article', {
        name: '다른 방문자의 최신 제목',
        exact: true,
      }),
    ).toBeVisible();
    expect(server.sessionRequests).toBe(0);
    expect(server.authorizationHeaders).toBe(0);
  });

  // 공개 AWS 목록과 앱 내부 합성 시연 목록 사이에 조회·쓰기·인증이 섞이지 않는다.
  test('공개 목록과 메모리 시연 분리', async function publicDemoIsolation({
    page,
    context,
  }) {
    const server = await mockMedicationApi(context);
    await openPublicSchedule(page);
    await registerSimplePlan(page, '합성 공개 서버 목록');
    const saved = structuredClone(server.state);
    await page
      .getByRole('link', { name: '홈', exact: true })
      .filter({ visible: true })
      .click();
    await page
      .getByRole('button', { name: '시연 데이터 보기', exact: true })
      .filter({ visible: true })
      .last()
      .click();
    await page
      .getByRole('link', { name: '복약', exact: true })
      .filter({ visible: true })
      .click();
    await expect(page.locator('.medication-plan-card')).toHaveCount(0);
    const requests = server.requests;
    await registerSimplePlan(page, '합성 메모리 전용');
    await expect(
      page.getByRole('article', { name: '합성 메모리 전용', exact: true }),
    ).toBeVisible();
    expect(server.state).toEqual(saved);
    expect(server.requests).toBe(requests);
    await page.getByRole('button', { name: '시연 종료', exact: true }).click();
    await expect(
      page.getByRole('article', { name: '합성 공개 서버 목록', exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole('article', { name: '합성 메모리 전용', exact: true }),
    ).toHaveCount(0);
    await expect(page.getByLabel('공통 비밀번호', { exact: true })).toHaveCount(
      0,
    );
    expect(server.sessionRequests).toBe(0);
    expect(server.authorizationHeaders).toBe(0);
  });
});
