import { expect, test } from '@playwright/test';

// 처음 접속한 사용자에게 가상의 의료 데이터나 연결 성공을 표시하지 않는지 확인한다.
test('빈 시작 화면 → 시연 → 검색 → 상세 수정 → 종료', async function flow({
  page,
}) {
  const errors: string[] = [];
  // 브라우저의 예외만 수집하며 응답 본문은 남기지 않는다.
  page.on('pageerror', function error(error) {
    errors.push(error.message);
  });
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: '우리집 약품함' }),
  ).toBeVisible();
  await expect(page.getByText('첫 약품을 기다리고 있어요')).toBeVisible();
  await expect(page.getByText('치수 미설정')).toBeVisible();
  await page
    .getByRole('button', { name: '시연 데이터 보기', exact: true })
    .last()
    .click();
  await expect(
    page.getByText('시연 데이터 · 실제 약품·기록이 아니에요'),
  ).toBeVisible();
  await page.getByRole('button', { name: '두통', exact: true }).click();
  await expect(page.locator('.package-card')).toHaveCount(1);
  await page.getByRole('link', { name: '타이레놀정 500mg 상세 보기' }).click();
  await expect(
    page.getByRole('heading', { name: '약품 상세', exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: '수량 및 정보 수정' }).click();
  await page.getByRole('spinbutton').fill('5');
  await page.getByLabel('수정 사유').fill('가상 포장 수량 확인');
  await page.getByRole('button', { name: '시연 정보에 반영' }).click();
  await expect(page.getByText('예상 잔여 5정')).toBeVisible();
  await expect(page.getByRole('status')).toContainText('서버 저장');
  await page.getByRole('button', { name: '시연 종료', exact: true }).click();
  await page.getByRole('link', { name: '홈으로 돌아가기' }).click();
  await expect(page.getByText('첫 약품을 기다리고 있어요')).toBeVisible();
  expect(errors).toEqual([]);
});

// 필터 없는 카드 선택·달력·주의정보가 같은 시연 상태를 사용하는지 확인한다.
test('3D 선택과 복약·알림 탐색', async function navigation({ page }) {
  await page.goto('/');
  await page
    .getByRole('button', { name: '시연 데이터 보기', exact: true })
    .last()
    .click();
  await page.getByRole('button', { name: '다음 약품', exact: true }).click();
  await expect(
    page.getByRole('button', { name: '일반의약품 B 3D에서 선택' }),
  ).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: '다음 약품', exact: true }).click();
  // 자동 스크롤의 130ms 선택 처리 이후에도 명시적 선택이 유지되어야 한다.
  await page.waitForTimeout(220);
  await expect(
    page.getByRole('button', { name: '처방 받은 약봉투 3D에서 선택' }),
  ).toHaveAttribute('aria-pressed', 'true');
  await page
    .getByRole('link', { name: '복약', exact: true })
    .filter({ visible: true })
    .click();
  await expect(page.locator('.dose-row')).toHaveCount(3);
  await page.locator('.dose-row').nth(1).click();
  await page.getByRole('radio', { name: '미복용', exact: true }).check();
  await page.getByRole('button', { name: '시연 기록에 반영' }).click();
  await expect(page.locator('.dose-row').nth(1)).toContainText('미복용');
  await page
    .getByRole('link', { name: /^알림/ })
    .filter({ visible: true })
    .last()
    .click();
  await expect(
    page.getByRole('heading', { name: '동일 성분 중복 가능성' }),
  ).toBeVisible();
  await expect(page.locator('.notice-detail')).toContainText(
    '현재 보관 중인 약품',
  );
  await page.getByRole('button', { name: '내용 확인 · 시연' }).click();
  await expect(
    page.getByRole('button', { name: '내용 확인됨', exact: true }),
  ).toBeDisabled();
  await expect(page.locator('.notice-card')).toHaveCount(3);
});

// 작은 화면에서도 문서 폭이 넘치지 않고 직접 링크가 동작하는지 확인한다.
test('반응형·직접 링크·오프라인 앱 셸', async function responsive({
  page,
  context,
}) {
  const errors: string[] = [];
  // Service Worker 설치 실패도 브라우저 오류로 수집한다.
  context.on('console', function diagnostic(message) {
    if (message.type() === 'error') errors.push(message.text());
  });
  await page.goto('/device');
  await expect(
    page.getByRole('heading', { name: '보관함 연결' }),
  ).toBeVisible();
  await expect(page.getByText(/일반 Safari 탭에서는/)).toBeVisible();
  // 설치된 앱 셸만 오프라인에서 사용하고 서버 성공 상태를 표시하지 않는다.
  // 워커 상태를 출력하는 검증으로 설치 실패와 지연을 구별한다.
  await expect
    .poll(
      // 등록된 워커가 활성화될 때까지 상태를 확인한다.
      async function workerState() {
        // 등록 상태만 조회하고 의료 데이터는 수집하지 않는다.
        return page.evaluate(async function registrations() {
          const registrations =
            await navigator.serviceWorker.getRegistrations();
          // 등록된 워커의 수명주기 상태를 검사한다.
          return registrations.map(function state(registration) {
            return (
              registration.active?.state ||
              registration.installing?.state ||
              registration.waiting?.state ||
              'none'
            );
          });
        });
      },
      { timeout: 15000 },
    )
    .toContain('activated');
  await page.reload();
  await context.setOffline(true);
  await page.reload();
  await expect(page.getByText(/오프라인이에요/)).toBeVisible();
  await context.setOffline(false);
  // 레이아웃 폭은 실제 viewport 안에 있어야 한다.
  const overflow = await page.evaluate(function width() {
    return document.documentElement.scrollWidth > window.innerWidth;
  });
  expect(overflow).toBe(false);
  expect(errors).toEqual([]);
});

// 반응형 전환으로 검색·선택이 초기화되거나 가로 스크롤이 생기지 않는지 확인한다.
test('좁은 휴대폰·태블릿·PC 전환에서도 선택 유지', async function breakpoints({
  page,
}) {
  await page.goto('/');
  await page
    .getByRole('button', { name: '시연 데이터 보기', exact: true })
    .last()
    .click();
  await page.getByRole('button', { name: '두통', exact: true }).click();
  for (const width of [320, 768, 1199, 1200]) {
    await page.setViewportSize({ width, height: 844 });
    await expect(page.locator('.package-card')).toHaveCount(1);
    await expect(
      page.getByRole('button', { name: '타이레놀정 500mg 3D에서 선택' }),
    ).toHaveAttribute('aria-pressed', 'true');
    // 화면의 실제 레이아웃 폭만 확인한다.
    expect(
      // 문서가 화면보다 넓은지 확인한다.
      await page.evaluate(function overflow() {
        return document.documentElement.scrollWidth > window.innerWidth;
      }),
    ).toBe(false);
  }
});

// 높이가 낮은 PC에서도 홈의 마지막 안내문까지 휠로 볼 수 있는지 확인한다.
test('낮은 PC 홈 화면 세로 스크롤', async function desktopScroll({
  page,
}, testInfo) {
  test.skip(testInfo.project.name !== 'desktop');
  await page.setViewportSize({ width: 1440, height: 620 });
  await page.goto('/');
  await page
    .getByRole('button', { name: '시연 데이터 보기', exact: true })
    .last()
    .click();

  const home = page.locator('.main-home');
  const caption = page.locator('.scene-caption');
  await expect(caption).toBeAttached();
  // 잘린 영역에 실제 사용자 스크롤이 적용되는지 확인한다.
  const layout = await home.evaluate(function measure(element) {
    return {
      visible: element.clientHeight,
      total: element.scrollHeight,
      overflow: getComputedStyle(element).overflowY,
    };
  });
  expect(layout.overflow).toBe('auto');
  expect(layout.total).toBeGreaterThan(layout.visible);
  await page.getByRole('heading', { name: '우리집 약품함' }).hover();
  await page.mouse.wheel(0, 1000);
  await expect
    .poll(
      // 실제 휠 입력으로 홈 영역이 내려갔는지 확인한다.
      async function scrollPosition() {
        return home.evaluate(function position(element) {
          return element.scrollTop;
        });
      },
    )
    .toBeGreaterThan(0);
  await expect(caption).toBeInViewport();
});
