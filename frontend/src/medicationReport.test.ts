import { describe, expect, it } from 'vitest';
import { packageSchema } from './data';
import type { MedicationState } from './medicationPlans';
import type { Activity, OutboundEvent } from './activity';
import {
  buildReportRecords,
  groupReportRecords,
  reportRange,
  validReportRange,
} from './medicationReport';

// 확인 날짜와 반출 날짜가 다른 합성 기록을 만든다.
function outbound(overrides: Partial<OutboundEvent> = {}): OutboundEvent {
  return {
    eventId: 'e1',
    packageId: 'p1',
    medicineName: '합성 시험약',
    occurredAt: '2026-11-09T15:30:00Z',
    recordedAt: '2026-11-12T00:00:00Z',
    status: 'TAKEN',
    quantity: 2,
    unit: 'TABLET',
    movementCertainty: 'ESTIMATED',
    ...overrides,
  };
}

// 일부 복용한 두 약과 일정 날짜보다 늦은 확인 시각을 준비한다.
function manual(): MedicationState {
  return {
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
            startDate: '2026-01-01',
            endDate: '',
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
            name: '다른 시험약',
            strength: '500mg',
            dose: '2정',
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
}

// 원본 응답의 읽기 전용 보고서 변환 입력을 준비한다.
function activity(history: OutboundEvent[]): Activity {
  return { pending: [], history, reviews: [] };
}

describe('보고서 기간', function ranges() {
  // 월 이동을 일수 계산으로 대체하면 말일과 연도 경계가 잘못된다.
  it.each([
    [1, '2026-03-31', '2026-02-28'],
    [1, '2024-03-31', '2024-02-29'],
    [3, '2026-01-31', '2025-10-31'],
    [6, '2026-11-12', '2026-05-12'],
  ] as const)(
    '최근 %i개월의 말일과 연도 경계를 계산한다',
    function range(months, today, start) {
      expect(reportRange(months, today)).toEqual({ start, end: today });
    },
  );
  // 비어 있거나 역전된 기간을 정상 조회로 처리하지 않는다.
  it('실제 날짜와 양 끝 순서를 검증한다', function validity() {
    expect(validReportRange('2026-11-10', '2026-11-10')).toBe(true);
    expect(validReportRange('', '2026-11-10')).toBe(false);
    expect(validReportRange('2026-02-30', '2026-11-10')).toBe(false);
    expect(validReportRange('2026-11-11', '2026-11-10')).toBe(false);
  });
});

describe('확인된 복용 기록 변환', function records() {
  // 미복용과 확인 대기를 보고서에 넣으면 복용하지 않은 약이 노출된다.
  it('TAKEN만 포함하고 KST 반출 날짜를 사용한다', function taken() {
    const input = activity([
      outbound(),
      outbound({ eventId: 'e2', status: 'NOT_TAKEN', quantity: null }),
    ]);
    input.pending.push(
      outbound({
        eventId: 'e3',
        status: 'PENDING',
        quantity: null,
        recordedAt: null,
      }),
    );
    const result = buildReportRecords(
      input,
      { version: 1, plans: [], records: [] },
      [],
    );
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({
      id: 'activity:e1',
      date: '2026-11-10',
      dateKind: '반출 날짜',
      amount: '2정',
    });
  });
  // 일부 복용을 회차 전체 약으로 확대하지 않는다.
  it('수동 일부 복용은 선택한 약과 일정 날짜만 사용한다', function some() {
    const result = buildReportRecords(activity([]), manual(), []);
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({
      name: '합성 처방약',
      strength: '250mg',
      date: '2026-11-10',
      dateKind: '일정 날짜',
      amount: '1정',
    });
  });
  // 확인 기록 없는 일정과 명시적 미복용·보류는 복용 증거가 아니다.
  it.each(['none', 'deferred'] as const)(
    '%s는 제외한다',
    function excluded(result) {
      const state = manual();
      state.records[0].result = result;
      state.records[0].takenMedicineIds = [];
      expect(buildReportRecords(activity([]), state, [])).toEqual([]);
    },
  );
  // 모두 복용은 예정 회차의 약별 선택 가능한 기록으로 나눈다.
  it('모두 복용을 약별 건으로 제공하고 원본은 변경하지 않는다', function all() {
    const state = manual();
    state.records[0].result = 'all';
    state.records[0].takenMedicineIds = ['m1', 'm2'];
    const before = structuredClone(state);
    const result = buildReportRecords(activity([]), state, []);
    expect(result).toHaveLength(2);
    expect(
      new Set(
        result.map(
          // 약마다 다른 선택 식별자를 확인한다.
          function id(item) {
            return item.id;
          },
        ),
      ).size,
    ).toBe(2);
    expect(state).toEqual(before);
  });
  // 개별 정보가 없는 처방을 임의의 약품으로 바꾸지 않는다.
  it('이름 없는 묶음은 개별 약 정보 없음으로 남긴다', function unknown() {
    const state = manual();
    state.plans[0].medicines = [];
    state.plans[0].title = '';
    state.records[0].takenMedicineIds = [];
    const result = buildReportRecords(activity([]), state, []);
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({
      name: '이름 없는 처방 묶음',
      amount: null,
      incomplete: true,
      ingredients: [],
    });
  });
  // 이름이 같다는 이유만으로 재고의 성분을 복사하지 않는다.
  it('포장 ID가 명확히 연결된 제품에서만 성분을 가져온다', function ingredients() {
    const item = packageSchema.parse({
      package_id: 'different',
      box_id: 'test-box',
      kind: 'OTC',
      state: 'STORED',
      medicines: [
        {
          medicine_id: 'm1',
          name: '합성 시험약',
          product_code: '123456789',
          ingredients: ['합성성분'],
        },
      ],
    });
    const empty = { version: 1 as const, plans: [], records: [] };
    expect(
      buildReportRecords(activity([outbound()]), empty, [item])[0].ingredients,
    ).toEqual([]);
    item.package_id = 'p1';
    expect(
      buildReportRecords(activity([outbound()]), empty, [item])[0].ingredients,
    ).toEqual(['합성성분']);
  });
  // 수동 약도 확정된 품목 연결을 통한 성분만 허용한다.
  it('수동 약의 명시적 제품 연결과 성분을 재사용한다', function linked() {
    const result = buildReportRecords(activity([]), manual(), [], {
      links: [
        {
          planId: 'plan1',
          medicineId: 'm1',
          productCode: '123456789',
          productName: '합성 품목',
        },
      ],
      details: {
        '123456789': {
          productCode: '123456789',
          productName: '합성 품목',
          manufacturer: '',
          ingredients: [{ name: '확인된 합성성분' }],
          indications: '',
          dosage: '',
          precautions: '',
          source: 'MFDS_PERMIT_DETAIL',
          checkedAt: '2026-11-10T00:00:00Z',
        },
      },
    });
    expect(result[0].ingredients).toEqual(['확인된 합성성분']);
  });
});

describe('선택된 기록의 약별 요약', function grouping() {
  // 최신 건에 성분이 없어도 같은 품목의 확인된 과거 성분을 숨기지 않는다.
  it('같은 품목의 선택 기록에 있는 성분 정보를 요약에 남긴다', function knownIngredients() {
    const rows = buildReportRecords(
      activity([outbound()]),
      { version: 1, plans: [], records: [] },
      [],
    );
    const base = rows[0];
    const groups = groupReportRecords([
      {
        ...base,
        groupKey: 'product:123456789:',
        date: '2026-11-11',
        ingredients: [],
      },
      {
        ...base,
        id: 'manual:older',
        groupKey: 'product:123456789:',
        date: '2026-11-10',
        ingredients: ['확인된 합성성분'],
      },
    ]);
    expect(groups[0].count).toBe(2);
    expect(groups[0].ingredients).toEqual(['확인된 합성성분']);
  });
  // 선택 제외가 횟수뿐 아니라 기록 기간에도 반영되어야 한다.
  it('선택된 확인 기록만으로 기간과 횟수를 다시 계산한다', function summarize() {
    const state = manual();
    state.records.push({
      ...state.records[0],
      occurrenceId: 'medication:plan1:2026-11-11T09:00+09:00',
    });
    const records = buildReportRecords(activity([]), state, []);
    const groups = groupReportRecords(records);
    expect(groups).toHaveLength(1);
    expect(groups[0]).toMatchObject({
      count: 2,
      start: '2026-11-10',
      end: '2026-11-11',
    });
    expect(groupReportRecords(records.slice(0, 1))[0]).toMatchObject({
      count: 1,
      start: '2026-11-11',
      end: '2026-11-11',
    });
    expect(groupReportRecords([])).toEqual([]);
  });
  // 이름만 같은 별도 약과 서로 다른 함량을 합치지 않는다.
  it('이름이 같아도 서로 다른 함량과 식별자는 분리한다', function distinct() {
    const state = manual();
    state.plans[0].medicines[1].name = '합성 처방약';
    state.records[0].result = 'all';
    state.records[0].takenMedicineIds = ['m1', 'm2'];
    const records = buildReportRecords(
      activity([outbound({ medicineName: '합성 처방약' })]),
      state,
      [],
    );
    expect(groupReportRecords(records)).toHaveLength(3);
  });
});
