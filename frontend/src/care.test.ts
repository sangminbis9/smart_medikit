import { describe, expect, it } from 'vitest';
import {
  careCategories,
  careGuides,
  careNotice,
  findCareGuides,
} from './careGuides';
import { matchCareMedicines } from './careMatching';
import { emptyData, packageSchema, snapshotSchema, type Package } from './data';

const now = new Date('2026-11-02T12:00:00+09:00');

// 실제 허가 약품과 혼동되지 않는 합성 검토 자료를 만든다.
function reviewedPackage(): Package {
  return packageSchema.parse({
    package_id: 'synthetic-package',
    box_id: 'test-box',
    kind: 'OTC',
    state: 'STORED',
    name: '합성 검증 제품',
    expiry: { verified: true, actual_expiry: '2027-01-01' },
    safety_status: 'CHECKED',
    medicines: [
      {
        medicine_id: 'synthetic-medicine',
        product_code: '000000001',
        name: '합성 검증 제품',
        ingredients: ['합성 성분'],
        product_info_status: 'SUCCEEDED',
        product_info: {
          source: 'MFDS_PERMIT_DETAIL',
          indications: '시험용 두통 효능',
          dosage: '합성 사용법',
          precautions: '합성 주의사항',
        },
        care_assessment: {
          product_code: '000000001',
          review_status: 'APPROVED',
          scope: 'CARE_GUIDE_32_V1',
          source_url: 'https://nedrug.mfds.go.kr/test',
          reviewed_at: '2026-11-01T00:00:00Z',
          valid_until: '2026-12-01T00:00:00Z',
          indications: '시험용 두통 효능',
          ingredient_names: ['합성 성분'],
          matches: [
            {
              symptom_id: 'headache',
              medicine_categories: ['PAIN_RELIEF'],
              evidence: '시험용 두통 효능',
              conditions: ['합성 검토 조건'],
            },
          ],
        },
      },
    ],
  });
}

// 케어 판단에 필요한 합성 보관 데이터만 전달한다.
function result(items: Package[], fresh = true) {
  return matchCareMedicines(
    { ...emptyData(), inventory_status: 'CONFIRMED', packages: items },
    'headache',
    'PAIN_RELIEF',
    fresh,
    now,
  );
}

// 32개 증상의 누락과 탐색·콘텐츠 구조를 검증한다.
describe('케어 콘텐츠', function content() {
  // 카테고리별 수와 필수 안전·출처 데이터를 확인한다.
  it('8개 카테고리의 32개 증상 모두 실제 내용이 있다', function completeness() {
    expect(careGuides).toHaveLength(32);
    expect(
      new Set(
        careGuides.map(
          // 중복 주소를 검사한다.
          function id(item) {
            return item.id;
          },
        ),
      ).size,
    ).toBe(32);
    const counts = careCategories.map(
      // 카테고리별 누락을 확인한다.
      function count(category) {
        return careGuides.filter(
          // 선택한 분류의 증상만 센다.
          function belongs(guide) {
            return guide.category === category;
          },
        ).length;
      },
    );
    expect(counts).toEqual([5, 6, 6, 3, 2, 2, 4, 4]);
    for (const guide of careGuides) {
      expect(guide.name).not.toMatch(/경미한|가벼운|일시적인/);
      expect(guide.notice).toBe(careNotice);
      expect(guide.steps.length).toBeGreaterThanOrEqual(3);
      expect(guide.steps.length).toBeLessThanOrEqual(5);
      expect(guide.steps[0].medicineCategories).toEqual([]);
      expect(guide.warningSigns.length).toBeGreaterThan(0);
      expect(guide.sources.length).toBeGreaterThan(0);
      for (const step of guide.steps)
        expect(step.description.length).toBeGreaterThan(20);
    }
  });
  // 검색과 분류를 함께 적용하고 공백 검색도 지원한다.
  it('증상과 카테고리 검색이 동작한다', function search() {
    expect(findCareGuides('두통')[0].id).toBe('headache');
    expect(findCareGuides('기침')).toHaveLength(2);
    expect(findCareGuides('기침', '소화기')).toEqual([]);
    expect(findCareGuides('  ')).toHaveLength(32);
    expect(findCareGuides('없는증상')).toEqual([]);
  });
});

// 확인되지 않은 자료가 보유·미보유 확정으로 바뀌지 않는지 검증한다.
describe('제품 연결 안전 경계', function matching() {
  // 전체 검토 근거가 일치할 때만 보관 정보를 확정한다.
  it('검토된 원문·성분·품목 코드가 일치해야 보유', function approved() {
    expect(result([reviewedPackage()]).status).toBe('보유');
  });
  // 이름이나 문자열 효능은 승인 근거가 아니다.
  it('제품명과 키워드만으로 연결하지 않는다', function keywords() {
    const item = reviewedPackage();
    item.name = '두통 진통제';
    item.medicines[0].indications = ['두통'];
    item.medicines[0].care_assessment = null;
    expect(result([item])).toMatchObject({
      status: '확인 필요',
      matches: [],
      unknown: [item],
    });
  });
  // 만료·오염·불완전 근거를 자동 승인하지 않는다.
  it('오래된 검토·다른 코드·원문·성분·출처를 거부한다', function mismatches() {
    const variants = [
      reviewedPackage(),
      reviewedPackage(),
      reviewedPackage(),
      reviewedPackage(),
      reviewedPackage(),
      reviewedPackage(),
    ];
    variants[0].medicines[0].care_assessment!.valid_until =
      '2026-10-01T00:00:00Z';
    variants[1].medicines[0].care_assessment!.product_code = '000000002';
    variants[2].medicines[0].product_info!.indications = '변경된 원문';
    variants[3].medicines[0].ingredients = ['다른 성분'];
    variants[4].medicines[0].care_assessment!.source_url =
      'https://mfds.go.kr.evil.test';
    variants[5].medicines[0].product_info!.dosage = null;
    for (const item of variants) {
      expect(result([item]).status).toBe('확인 필요');
      expect(result([item]).matches).toEqual([]);
    }
  });
  // 근거가 있어도 실제 만료일과 안전정보가 없으면 보유 확정을 보류한다.
  it('임시 관리 기준일과 안전정보 미조회는 확인 필요', function unknownExpiry() {
    const item = reviewedPackage();
    item.expiry = {
      verified: false,
      actual_expiry: null,
      temporary_management_date: '2027-01-01',
    };
    expect(result([item]).matches[0].status).toBe('확인 필요');
    const pending = reviewedPackage();
    pending.safety_status = 'SAFETY_CHECK_PENDING';
    expect(result([pending]).status).toBe('확인 필요');
    expect(
      matchCareMedicines(
        {
          ...emptyData(),
          inventory_status: 'CONFIRMED',
          packages: [reviewedPackage()],
          alerts_status: 'UNAVAILABLE',
        },
        'headache',
        'PAIN_RELIEF',
        true,
        now,
      ).status,
    ).toBe('확인 필요');
  });
  // 만료나 경고는 정보 확인 여부와 관계없이 보존한다.
  it('실제 만료와 DUR·동일성분 경고는 사용 주의', function warnings() {
    const item = reviewedPackage();
    item.expiry.actual_expiry = '2026-11-01';
    expect(result([item]).status).toBe('사용 주의');
    for (const type of [
      'DUR_CONTRAINDICATION',
      'DUPLICATE_INGREDIENT',
    ] as const) {
      const data = {
        ...emptyData(),
        packages: [reviewedPackage()],
        alerts: [
          {
            alert_id: 'synthetic-alert',
            type,
            title: '합성 주의정보',
            body: '',
            scope: 'STORED' as const,
            package_ids: ['synthetic-package'],
            occurred_at: '2026-11-02T00:00:00Z',
            ack_state: 'ACKNOWLEDGED' as const,
            source_event_id: null,
          },
        ],
      };
      const matched = matchCareMedicines(
        data,
        'headache',
        'PAIN_RELIEF',
        true,
        now,
      );
      expect(matched.status).toBe('사용 주의');
      expect(matched.warningTone).toBe(
        type === 'DUR_CONTRAINDICATION' ? 'red' : 'amber',
      );
      expect(matched.matches[0].warningTone).toBe(matched.warningTone);
    }
  });
  // 보관 중이 아닌 포장과 처방약을 자가관리 후보에서 제외한다.
  it('반출·등록·소진·폐기·처방 포장을 제외한다', function states() {
    for (const state of [
      'OUT',
      'REGISTERED',
      'DEPLETED',
      'DISPOSED',
    ] as const) {
      const item = reviewedPackage();
      item.state = state;
      expect(result([item]).matches).toEqual([]);
    }
    const item = reviewedPackage();
    item.kind = 'PRESCRIPTION';
    expect(result([item]).matches).toEqual([]);
  });
  // 빈 최신 재고와 미연결·오류·오프라인을 구분한다.
  it('미연결은 확인 필요이며 확인된 빈 재고만 미보유', function empty() {
    expect(result([], false).status).toBe('확인 필요');
    expect(result([]).status).toBe('미보유');
    expect(result([reviewedPackage()], false).matches[0].status).toBe(
      '확인 필요',
    );
    expect(snapshotSchema.parse({ packages: [] }).inventory_status).toBe(
      'UNAVAILABLE',
    );
  });
  // 성공한 조회의 미완성 재고를 연결 실패나 확정 보유·미보유로 오해하지 않는다.
  it('조회 성공과 재고 미확인을 구분한다', function connectedInventory() {
    for (const inventoryStatus of ['UNAVAILABLE', 'PARTIAL'] as const) {
      for (const packages of [[], [reviewedPackage()]]) {
        const data = {
          ...emptyData(),
          inventory_status: inventoryStatus,
          packages,
        };
        const connected = matchCareMedicines(
          data,
          'headache',
          'PAIN_RELIEF',
          true,
          now,
        );
        expect(connected.status).toBe('확인 필요');
        expect(connected.reason).toContain(
          inventoryStatus === 'UNAVAILABLE'
            ? '제품 조회는 연결되어 있어요'
            : '조회는 완료됐지만 일부 보관 정보',
        );
        for (const match of connected.matches)
          expect(match.status).toBe('확인 필요');
        expect(
          matchCareMedicines(data, 'headache', 'PAIN_RELIEF', false, now)
            .reason,
        ).toBe('최신 보관함 정보를 확인할 수 없어요.');
      }
    }
  });
  // 관련 없는 제품도 검토가 완료되어야 미보유 판정에 사용할 수 있다.
  it('승인된 비관련 제품만 있으면 미보유', function noMatch() {
    const item = reviewedPackage();
    item.medicines[0].care_assessment!.matches = [];
    expect(result([item]).status).toBe('미보유');
  });
  // 선택 확장 필드가 깨져도 기존 포장을 읽고 안전하게 미확인 처리한다.
  it('잘못된 선택 허가정보는 기본 조회를 깨지 않는다', function malformed() {
    const item = packageSchema.parse({
      ...reviewedPackage(),
      medicines: [
        {
          medicine_id: 'test',
          name: '합성',
          care_assessment: { review_status: 'APPROVED' },
          product_info: { dosage: 42 },
        },
      ],
    });
    expect(result([item]).status).toBe('확인 필요');
    expect(item.medicines[0].product_info).toBeNull();
  });
});
