import { describe, expect, it } from 'vitest';
import type { Activity, OutboundEvent } from './activity';
import { packageSchema } from './data';
import type { MedicationPlan, MedicationState } from './medicationPlans';
import {
  buildReportItems,
  prescriptionDuration,
  reportRange,
  validReportRange,
} from './medicationReport';

// 복용 확인 없이 직접 등록한 다섯 날짜의 처방을 준비한다.
function plan(overrides: Partial<MedicationPlan> = {}): MedicationPlan {
  return {
    id: 'cold',
    title: '감기 처방약',
    notes: '',
    endedOn: null,
    timing: {
      mode: 'regular',
      schedule: {
        startDate: '2026-11-12',
        endDate: '2026-11-16',
        weekdays: [1, 3, 5],
        times: ['09:00', '18:00'],
      },
    },
    medicines: [
      { id: 'a', name: 'A정', strength: '250mg', dose: '1정', timing: null },
      { id: 'b', name: 'B캡슐', strength: '', dose: '1캡슐', timing: null },
    ],
    ...overrides,
  };
}

// 실제 환자 자료 없이 날짜·포장·확인 여부가 다른 반출 기록을 준비한다.
function outbound(overrides: Partial<OutboundEvent> = {}): OutboundEvent {
  return {
    eventId: 'e1',
    packageId: 'p1',
    medicineName: '합성 일반약',
    occurredAt: '2026-11-12T00:00:00Z',
    recordedAt: '2026-11-13T00:00:00Z',
    status: 'TAKEN',
    quantity: 1,
    unit: 'TABLET',
    ...overrides,
  };
}

const period = { start: '2026-11-12', end: '2026-11-12' };
// 양쪽 출처의 원본 상태를 별도로 보존할 수 있게 합성 상태를 만든다.
function state(plans: MedicationPlan[]): MedicationState {
  return { version: 1, plans, records: [] };
}

describe('조회 기간과 등록 처방 일수', function dates() {
  // 월 말과 윤년을 포함한 달 단위 기간을 유지한다.
  it('최근 1·3·6개월과 직접 날짜를 검증한다', function ranges() {
    expect(reportRange(1, '2026-03-31')).toEqual({
      start: '2026-02-28',
      end: '2026-03-31',
    });
    expect(reportRange(3, '2026-11-15').start).toBe('2026-08-15');
    expect(reportRange(6, '2026-11-15').start).toBe('2026-05-15');
    expect(validReportRange('2026-02-30', '2026-03-01')).toBe(false);
    expect(validReportRange('2026-11-15', '2026-11-12')).toBe(false);
    expect(validReportRange('', '2026-11-12')).toBe(false);
    expect(validReportRange('2026-11-12', '2026-11-12')).toBe(true);
  });
  // 하루의 시간·요일·횟수와 관계없이 등록한 날짜 양 끝을 포함한다.
  it('같은 날·월말·윤년도 시작일부터 종료일까지 포함한다', function inclusive() {
    expect(prescriptionDuration('2026-11-12', '2026-11-16')).toBe('5일분');
    expect(prescriptionDuration('2026-11-12', '2026-11-12')).toBe('1일분');
    expect(prescriptionDuration('2024-02-28', '2024-03-01')).toBe('3일분');
    expect(prescriptionDuration('2026-12-31', '2027-01-01')).toBe('2일분');
  });
  // 종료일이 없으면 복용일이나 회차를 추정하지 않는다.
  it('종료일 없음은 상시 복용 중으로 표시한다', function ongoing() {
    expect(prescriptionDuration('2026-11-12', '')).toBe('상시 복용 중');
    expect(prescriptionDuration('', '')).toBe('상시 복용 중');
  });
});

describe('진료용 카드 목록', function cards() {
  // 과거 확인 건이 없어도 처방 하나와 포함 약 전체를 한 카드에 보존한다.
  it('처방을 한 장으로 표시하고 조회 기간이 하루여도 전체 5일분을 유지한다', function prescription() {
    const original = state([plan()]);
    const copy = structuredClone(original);
    const rows = buildReportItems(
      { history: [], pending: [], reviews: [] },
      original,
      [],
      period,
    );
    expect(rows).toEqual([
      {
        id: 'plan:cold',
        selectionIds: ['plan:cold'],
        kind: 'prescription',
        name: '감기 처방약',
        duration: '5일분',
        medicines: ['A정 250mg', 'B캡슐'],
      },
    ]);
    expect(original).toEqual(copy);
    expect(rows[0]).not.toHaveProperty('records');
  });
  // 같은 이름·품목이어도 서로 다른 등록 처방은 별도 선택 단위다.
  it('동명 처방을 ID로 분리하고 약 없는 처방도 표시한다', function identity() {
    const rows = buildReportItems(
      { history: [], pending: [], reviews: [] },
      state([plan(), plan({ id: 'cold2', medicines: [] })]),
      [],
      period,
    );
    expect(rows).toHaveLength(2);
    expect(
      rows.map(
        // 카드 식별자는 처방 이름에 의존하지 않는다.
        function key(item) {
          return item.id;
        },
      ),
    ).toEqual(['plan:cold', 'plan:cold2']);
    expect(rows[1].medicines).toEqual([]);
  });
  // 시작·종료일과 조회 기간이 한 날이라도 겹쳐야 하며 종료 처리는 존중한다.
  it('범위 밖 처방을 제외하고 무일정·필요 시·상시 처방은 추정 없이 포함한다', function overlap() {
    const future = plan({ id: 'future' });
    if (future.timing.schedule) future.timing.schedule.startDate = '2026-11-13';
    const ongoing = plan({ id: 'ongoing' });
    if (ongoing.timing.schedule) ongoing.timing.schedule.endDate = '';
    const rows = buildReportItems(
      { history: [], pending: [], reviews: [] },
      state([
        plan(),
        future,
        plan({ id: 'ended', endedOn: '2026-11-11' }),
        ongoing,
        plan({
          id: 'unscheduled',
          timing: { mode: 'regular', schedule: null },
        }),
        plan({ id: 'needed', timing: { mode: 'as_needed', schedule: null } }),
      ]),
      [],
      period,
    );
    expect(
      rows.map(
        // 표시 대상은 실제 복용 확인이나 예정 시각으로 결정하지 않는다.
        function key(item) {
          return item.id;
        },
      ),
    ).toEqual(['plan:cold', 'plan:ongoing', 'plan:unscheduled', 'plan:needed']);
    expect(rows[1].duration).toBe('상시 복용 중');
  });
  // 명시적 종료 처리는 종료일이 비어 있는 처방의 상시 표시보다 우선한다.
  it('조회 기간 안에 종료한 처방을 현재 상시 복용 중으로 표시하지 않는다', function ended() {
    const ongoing = plan({ id: 'ended-ongoing', endedOn: '2026-11-14' });
    if (ongoing.timing.schedule) ongoing.timing.schedule.endDate = '';
    const rows = buildReportItems(
      { history: [], pending: [], reviews: [] },
      state([ongoing, plan({ id: 'ended-course', endedOn: '2026-11-14' })]),
      [],
      { start: '2026-11-01', end: '2026-11-15' },
    );
    expect(rows[0].duration).toBe('복용 종료');
    expect(rows[1].duration).toBe('5일분 · 복용 종료');
  });
  // 공통 일정이 범위 밖이어도 약별 예외 일정이 겹치면 처방은 빠지지 않는다.
  it('포함 약의 예외 일정도 조회 범위에 반영한다', function override() {
    const input = plan();
    input.medicines[0].timing = {
      mode: 'regular',
      schedule: {
        startDate: '2026-11-01',
        endDate: '2026-11-03',
        weekdays: [0],
        times: ['09:00'],
      },
    };
    expect(
      buildReportItems(
        { history: [], pending: [], reviews: [] },
        state([input]),
        [],
        {
          start: '2026-11-01',
          end: '2026-11-03',
        },
      ),
    ).toHaveLength(1);
  });
  // 반출 확인 시각 대신 KST 반출 날짜로 필터하고 미확인도 복용으로 단정하지 않는다.
  it('반출 이력만 약품별로 합치고 조회 기간 밖 기록을 제외한다', function cabinet() {
    const activity: Activity = {
      reviews: [],
      history: [
        outbound(),
        outbound({ eventId: 'e2', occurredAt: '2026-11-11T15:00:00Z' }),
        outbound({
          eventId: 'old',
          packageId: 'old',
          occurredAt: '2026-11-11T14:59:00Z',
        }),
        outbound({
          eventId: 'not',
          packageId: 'not',
          medicineName: '미복용 반출약',
          status: 'NOT_TAKEN',
          quantity: null,
        }),
      ],
      pending: [
        outbound({
          eventId: 'pending',
          packageId: 'p2',
          medicineName: '미확인 반출약',
          status: 'PENDING',
          quantity: null,
          recordedAt: null,
        }),
      ],
    };
    const copy = structuredClone(activity);
    const rows = buildReportItems(activity, state([]), [], period);
    expect(
      rows.map(
        // 반복 반출은 약품 한 항목이며 미복용 여부를 복용 사실로 변환하지 않는다.
        function name(item) {
          return item.name;
        },
      ),
    ).toEqual(['합성 일반약', '미복용 반출약', '미확인 반출약']);
    expect(
      rows.every(
        // 모든 목록은 날짜 상세·복용 횟수 없이 반출 약품으로만 표시한다.
        function source(item) {
          return item.kind === 'cabinet' && item.duration === '';
        },
      ),
    ).toBe(true);
    expect(activity).toEqual(copy);
  });
  // 포장이 달라도 확정된 단일 제품 코드가 같을 때에만 합친다.
  it('같은 이름의 다른 제품은 분리하고 같은 제품의 반복 포장은 합친다', function products() {
    const pack = packageSchema.parse({
      package_id: 'p1',
      box_id: 'box',
      name: '합성 일반약',
      kind: 'OTC',
      state: 'STORED',
      medicines: [
        {
          medicine_id: 'm',
          name: '합성 일반약',
          product_code: '123456789',
          form: 'TABLET',
        },
      ],
    });
    const other = { ...pack, package_id: 'p2' };
    const activity: Activity = {
      reviews: [],
      pending: [],
      history: [
        outbound(),
        outbound({ eventId: 'e2', packageId: 'p2' }),
        outbound({ eventId: 'e3', packageId: 'p3' }),
      ],
    };
    const rows = buildReportItems(activity, state([]), [pack, other], period);
    expect(rows).toHaveLength(2);
    expect(rows[0].selectionIds).toEqual([
      'cabinet:package:p1',
      'cabinet:package:p2',
    ]);
    expect(rows[0].id).not.toBe(rows[1].id);
  });
});
