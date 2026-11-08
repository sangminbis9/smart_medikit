import { describe, expect, it } from 'vitest';
import {
  createMedicationPlan,
  emptyMedicationState,
  occurrencesOnDay,
  parseMedicationState,
  recordMedicationOccurrence,
  removeMedicationPlan,
  saveMedicationPlan,
  validateMedicationPlan,
  type MedicationItem,
  type MedicationPlan,
  type MedicationSchedule,
  type MedicationState,
} from './medicationPlans';

// 실제 처방정보 없이 일정 계산만 확인하는 합성 일정을 만든다.
function schedule(
  overrides: Partial<MedicationSchedule> = {},
): MedicationSchedule {
  return {
    startDate: '2026-11-01',
    endDate: '2026-11-30',
    weekdays: [0, 1, 2, 3, 4, 5, 6],
    times: ['08:00', '20:00'],
    ...overrides,
  };
}

// 개인정보가 없는 합성 약 항목을 만든다.
function medicine(
  id: string,
  overrides: Partial<MedicationItem> = {},
): MedicationItem {
  return {
    id,
    name: `합성 약 ${id}`,
    strength: '',
    dose: '',
    timing: null,
    ...overrides,
  };
}

// 각 검증이 다른 검증의 입력을 바꾸지 않도록 새 묶음을 만든다.
function plan(overrides: Partial<MedicationPlan> = {}): MedicationPlan {
  return {
    id: 'synthetic-plan',
    title: '합성 처방 묶음',
    timing: { mode: 'regular', schedule: schedule() },
    notes: '',
    medicines: [],
    endedOn: null,
    ...overrides,
  };
}

// 합성 묶음을 빈 상태에 저장한다.
function stateWith(value: MedicationPlan): MedicationState {
  return saveMedicationPlan(emptyMedicationState(), value);
}

// 누락된 정보와 잘못된 달력 입력으로 복용 회차가 만들어지는 것을 막는다.
describe('수동 복용약 입력 검증', function validation() {
  // 초안은 임의의 약이나 복용량을 포함하지 않는다.
  it('일정 없는 새 초안은 제목을 채운 뒤 목록에만 등록할 수 있다', function draft() {
    const draft = createMedicationPlan();
    expect(draft.title).toBe('');
    expect(draft.medicines).toEqual([]);
    expect(draft.timing).toEqual({ mode: 'regular', schedule: null });
    expect(validateMedicationPlan(draft)).toContain('이름');
    draft.title = '목록만 기록';
    expect(validateMedicationPlan(draft)).toBeNull();
    expect(occurrencesOnDay(stateWith(draft), '2026-11-08')).toEqual([]);
  });

  // 윤년과 세기 경계를 실제 달력 규칙으로 확인한다.
  it('존재하지 않는 날짜와 역전된 기간을 거부한다', function dates() {
    const invalidDays = [
      '2026-02-29',
      '2100-02-29',
      '2026-04-31',
      '2026-13-01',
      '0000-01-01',
    ];
    for (const day of invalidDays) {
      const value = plan({
        timing: {
          mode: 'regular',
          schedule: schedule({ startDate: day, endDate: '' }),
        },
      });
      expect(validateMedicationPlan(value)).toContain('시작일');
    }
    const leap = plan({
      timing: {
        mode: 'regular',
        schedule: schedule({ startDate: '2000-02-29', endDate: '' }),
      },
    });
    expect(validateMedicationPlan(leap)).toBeNull();
    const reversed = plan({
      timing: {
        mode: 'regular',
        schedule: schedule({ endDate: '2026-10-31' }),
      },
    });
    expect(validateMedicationPlan(reversed)).toContain('빠를 수');
    expect(validateMedicationPlan(plan({ endedOn: '2026-02-30' }))).toContain(
      '종료 날짜',
    );
  });

  // 같은 회차가 중복 생성되거나 시간 범위를 벗어나지 않아야 한다.
  it('빈 값과 중복 요일·시간 및 잘못된 시간을 거부한다', function slots() {
    const invalidSchedules = [
      schedule({ weekdays: [] }),
      schedule({ weekdays: [1, 1] }),
      schedule({ weekdays: [7] }),
      schedule({ times: [] }),
      schedule({ times: ['08:00', '08:00'] }),
      schedule({ times: ['24:00'] }),
      schedule({ times: ['08:60'] }),
      schedule({ times: ['8:00'] }),
    ];
    for (const value of invalidSchedules) {
      expect(
        validateMedicationPlan(
          plan({ timing: { mode: 'regular', schedule: value } }),
        ),
      ).not.toBeNull();
    }
  });

  // 이름을 모르는 경우는 약 행을 만들지 않으며 빈 행은 저장하지 않는다.
  it('빈 약 이름과 중복 항목 및 필요시 약의 정기 일정을 거부한다', function medicines() {
    expect(
      validateMedicationPlan(
        plan({ medicines: [medicine('a', { name: '  ' })] }),
      ),
    ).toContain('이름');
    expect(
      validateMedicationPlan(
        plan({ medicines: [medicine('a'), medicine('a')] }),
      ),
    ).toContain('중복');
    expect(
      validateMedicationPlan(
        plan({ timing: { mode: 'as_needed', schedule: schedule() } }),
      ),
    ).toContain('정기 일정');
    expect(
      validateMedicationPlan(
        plan({
          medicines: [
            medicine('a', {
              timing: {
                mode: 'regular',
                schedule: schedule({ times: ['99:00'] }),
              },
            }),
          ],
        }),
      ),
    ).toContain('합성 약 a');
  });
});

// 공통 처방과 개별 약의 일정 차이가 실제 표시 회차에 반영되는지 확인한다.
describe('수동 복용약 회차 생성', function occurrences() {
  // 브라우저 시간대에 관계없이 한국 날짜의 요일과 양 끝 날짜를 유지한다.
  it('시작일·종료일을 포함하고 요일을 적용한다', function boundaries() {
    const value = plan({
      timing: {
        mode: 'regular',
        schedule: schedule({
          startDate: '2026-11-08',
          endDate: '2026-11-15',
          weekdays: [0],
          times: ['00:00', '23:59'],
        }),
      },
    });
    const state = stateWith(value);
    expect(occurrencesOnDay(state, '2026-11-07')).toEqual([]);
    expect(occurrencesOnDay(state, '2026-11-08')).toHaveLength(2);
    expect(occurrencesOnDay(state, '2026-11-09')).toEqual([]);
    expect(occurrencesOnDay(state, '2026-11-15')).toHaveLength(2);
    expect(occurrencesOnDay(state, '2026-11-22')).toEqual([]);
    expect(occurrencesOnDay(state, '2026-02-30')).toEqual([]);
    expect(occurrencesOnDay(state, '2026-11-08')[0].id).toContain(
      '2026-11-08T00:00+09:00',
    );
  });

  // 약마다 독립적인 기간과 시간을 적용하고 필요시 약은 정기회차에서 제외한다.
  it('공통 약·별도 일정 약·필요시 약을 해당 회차에만 묶는다', function overrides() {
    const value = plan({
      medicines: [
        medicine('common'),
        medicine('evening', {
          timing: {
            mode: 'regular',
            schedule: schedule({
              startDate: '2026-11-08',
              endDate: '2026-11-08',
              times: ['20:00', '22:00'],
            }),
          },
        }),
        medicine('as-needed', {
          timing: { mode: 'as_needed', schedule: null },
        }),
      ],
    });
    const state = stateWith(value);
    const day = occurrencesOnDay(state, '2026-11-08');
    expect(day).toHaveLength(3);
    expect(day[0].time).toBe('08:00');
    expect(day[0].medicines).toEqual([value.medicines[0]]);
    expect(day[1].medicines).toEqual([value.medicines[0], value.medicines[1]]);
    expect(day[2].medicines).toEqual([value.medicines[1]]);
    expect(occurrencesOnDay(state, '2026-11-09')).toHaveLength(2);
  });

  // 구성 약이 모두 다른 일정이면 공통 시간대에 내용 없는 회차를 만들지 않는다.
  it('실제로 해당하는 약이 없는 공통 회차를 생성하지 않는다', function noGhostSlots() {
    const value = plan({
      medicines: [
        medicine('override', {
          timing: { mode: 'regular', schedule: schedule({ times: ['12:00'] }) },
        }),
      ],
    });
    const day = occurrencesOnDay(stateWith(value), '2026-11-08');
    expect(day).toHaveLength(1);
    expect(day[0].time).toBe('12:00');
    value.medicines[0].timing = { mode: 'as_needed', schedule: null };
    expect(occurrencesOnDay(stateWith(value), '2026-11-08')).toEqual([]);
  });

  // 개별 약을 모르는 처방 묶음은 구성 약을 추정하지 않고 묶음 일정만 만든다.
  it('약 목록 없는 묶음과 공통 일정 없는 개별 약 일정도 지원한다', function unidentified() {
    const unknown = occurrencesOnDay(stateWith(plan()), '2026-11-08');
    expect(unknown).toHaveLength(2);
    expect(unknown[0].medicines).toEqual([]);
    const value = plan({
      timing: { mode: 'regular', schedule: null },
      medicines: [
        medicine('a', {
          timing: { mode: 'regular', schedule: schedule({ times: ['10:00'] }) },
        }),
      ],
    });
    expect(occurrencesOnDay(stateWith(value), '2026-11-08')).toHaveLength(1);
  });
});

// 사용자 확인을 재고나 다른 약의 기록으로 오인하지 않도록 변경 경계를 검증한다.
describe('복용 확인과 상태 복원', function records() {
  // 한 회차를 다시 확인해도 중복 기록이 생기지 않고 원본은 바뀌지 않는다.
  it('전체·일부·미복용 확인을 회차별로 교체한다', function confirm() {
    const state = stateWith(
      plan({ medicines: [medicine('a'), medicine('b')] }),
    );
    const occurrence = occurrencesOnDay(state, '2026-11-08')[0];
    const all = recordMedicationOccurrence(state, occurrence, 'all', []);
    expect(state.records).toEqual([]);
    expect(all.records[0].takenMedicineIds).toEqual(['a', 'b']);
    const some = recordMedicationOccurrence(
      all,
      occurrencesOnDay(all, '2026-11-08')[0],
      'some',
      ['a'],
    );
    expect(some.records).toHaveLength(1);
    expect(some.records[0].takenMedicineIds).toEqual(['a']);
    const none = recordMedicationOccurrence(
      some,
      occurrencesOnDay(some, '2026-11-08')[0],
      'none',
      ['a'],
    );
    expect(none.records[0].takenMedicineIds).toEqual([]);
    expect(occurrencesOnDay(some, '2026-11-08')[0].record?.result).toBe('some');
    expect(parseMedicationState(JSON.stringify(some))).toEqual(some);
  });

  // 자동 재조회가 최신 상태를 반영해도 먼저 열었던 확인창은 다른 기기의 기록을 덮어쓰지 않는다.
  it('약 구성이 같아도 확인 기록이 바뀐 오래된 창을 거부한다', function staleRecord() {
    const state = stateWith(
      plan({ medicines: [medicine('a'), medicine('b')] }),
    );
    const stale = occurrencesOnDay(state, '2026-11-08')[0];
    const latest = recordMedicationOccurrence(state, stale, 'none', []);
    const preserved = structuredClone(latest);
    // 미확인 상태에서 열린 창은 나중에 기록된 미복용을 모두 복용으로 바꿀 수 없다.
    expect(function staleUnconfirmed() {
      recordMedicationOccurrence(latest, stale, 'all', []);
    }).toThrow('복용 확인 기록이 변경');
    expect(latest).toEqual(preserved);

    const current = occurrencesOnDay(latest, '2026-11-08')[0];
    const changed = recordMedicationOccurrence(latest, current, 'some', ['a']);
    // 기존 확인을 편집하던 중 상태가 다시 바뀐 경우에도 재확인을 요구한다.
    expect(function staleExistingRecord() {
      recordMedicationOccurrence(changed, current, 'all', []);
    }).toThrow('복용 확인 기록이 변경');
    expect(changed.records[0].result).toBe('some');
  });

  // 같은 회차의 기록 변경만 충돌로 보고 다른 시간대의 확인은 방해하지 않는다.
  it('다른 회차 변경은 허용하고 같은 내용의 새 확인 시각도 대조한다', function recordScope() {
    const state = stateWith(plan());
    const [morning, evening] = occurrencesOnDay(state, '2026-11-08');
    const eveningRecorded = recordMedicationOccurrence(
      state,
      evening,
      'all',
      [],
    );
    const recorded = recordMedicationOccurrence(
      eveningRecorded,
      morning,
      'none',
      [],
    );
    expect(recorded.records).toHaveLength(2);
    const prior = occurrencesOnDay(recorded, '2026-11-08')[0];
    const newer = structuredClone(recorded);
    const record = newer.records.find(
      // 아침 회차의 확인 시각만 다른 기기의 새 확인으로 교체한다.
      function sameOccurrence(item) {
        return item.occurrenceId === prior.id;
      },
    )!;
    record.recordedAt =
      prior.record!.recordedAt === '2026-11-08T01:00:00Z'
        ? '2026-11-08T01:01:00Z'
        : '2026-11-08T01:00:00Z';
    // 결과가 같아도 확인 이후의 새 기록은 오래된 입력으로 덮어쓰지 않는다.
    expect(function staleTimestamp() {
      recordMedicationOccurrence(newer, prior, 'all', []);
    }).toThrow('복용 확인 기록이 변경');
    const refreshed = occurrencesOnDay(newer, '2026-11-08')[0];
    const corrected = recordMedicationOccurrence(newer, refreshed, 'all', []);
    expect(occurrencesOnDay(corrected, '2026-11-08')[0].record?.result).toBe(
      'all',
    );
  });

  // 개별 약이 알려진 경우 전체와 일부를 구분하고 무관한 약을 차단한다.
  it('일부 복용의 누락·전체 선택·중복·외부 약을 거부한다', function partialErrors() {
    const state = stateWith(
      plan({ medicines: [medicine('a'), medicine('b')] }),
    );
    const occurrence = occurrencesOnDay(state, '2026-11-08')[0];
    const invalidSelections = [[], ['a', 'b'], ['a', 'a'], ['unknown']];
    for (const ids of invalidSelections) {
      // 잘못된 선택으로 저장 함수가 실제 오류를 내는지 확인한다.
      expect(function invalidSelection() {
        recordMedicationOccurrence(state, occurrence, 'some', ids);
      }).toThrow();
    }
    const unknown = stateWith(plan());
    const recorded = recordMedicationOccurrence(
      unknown,
      occurrencesOnDay(unknown, '2026-11-08')[0],
      'some',
      [],
    );
    expect(recorded.records[0]).toMatchObject({
      result: 'some',
      takenMedicineIds: [],
    });
  });

  // 일정 종료는 기존 확인 이력을 지우거나 종료일 당일을 누락하지 않는다.
  it('종료일의 과거 기록을 보존하고 이후 회차와 기록을 차단한다', function ending() {
    const value = plan();
    let state = stateWith(value);
    state = recordMedicationOccurrence(
      state,
      occurrencesOnDay(state, '2026-11-08')[0],
      'all',
      [],
    );
    state = recordMedicationOccurrence(
      state,
      occurrencesOnDay(state, '2026-11-09')[0],
      'all',
      [],
    );
    const ended = saveMedicationPlan(state, {
      ...value,
      endedOn: '2026-11-08',
    });
    expect(ended.records).toHaveLength(1);
    expect(occurrencesOnDay(ended, '2026-11-08')).toHaveLength(2);
    expect(occurrencesOnDay(ended, '2026-11-09')).toEqual([]);
    expect(parseMedicationState(JSON.stringify(ended))).toEqual(ended);
  });

  // 이름·복용량·시간 수정으로 이전 확인이 새 처방 구성에 붙지 않아야 한다.
  it('제목·메모 수정은 보존하고 약·일정 수정은 확인 기록을 무효화한다', function editing() {
    const value = plan({ medicines: [medicine('a'), medicine('b')] });
    let state = stateWith(value);
    const oldOccurrence = occurrencesOnDay(state, '2026-11-08')[0];
    state = recordMedicationOccurrence(state, oldOccurrence, 'all', []);
    const renamed = saveMedicationPlan(state, {
      ...value,
      title: '새 별칭',
      notes: '추가 메모',
    });
    expect(renamed.records).toHaveLength(1);
    const changed = saveMedicationPlan(state, {
      ...value,
      medicines: [medicine('a', { dose: '사용자 수정 문구' }), medicine('b')],
    });
    expect(changed.records).toEqual([]);
    // 이전 확인 창의 내용은 수정된 약 구성에 적용하지 않는다.
    expect(function staleConfirmation() {
      recordMedicationOccurrence(changed, oldOccurrence, 'all', []);
    }).toThrow('변경');
    const rescheduled = saveMedicationPlan(state, {
      ...value,
      timing: { mode: 'regular', schedule: schedule({ times: ['09:00'] }) },
    });
    expect(rescheduled.records).toEqual([]);
  });

  // 비슷한 식별자의 다른 묶음과 기록은 삭제 대상에 포함하지 않는다.
  it('묶음 삭제는 연결된 기록만 지운다', function deletion() {
    const first = plan({ id: 'synthetic:a' });
    const second = plan({ id: 'synthetic:a:other' });
    let state = saveMedicationPlan(stateWith(first), second);
    for (const occurrence of occurrencesOnDay(state, '2026-11-08')) {
      state = recordMedicationOccurrence(state, occurrence, 'all', []);
    }
    const removed = removeMedicationPlan(state, first.id);
    expect(removed.plans).toEqual([second]);
    expect(removed.records).toHaveLength(2);
    expect(parseMedicationState(JSON.stringify(removed))).toEqual(removed);
  });

  // 손상 데이터를 빈 목록으로 덮어쓰지 않도록 읽기 실패를 명시한다.
  it('잘못된 저장 구조·중복·고아 기록을 거부한다', function corrupted() {
    expect(parseMedicationState(null)).toEqual(emptyMedicationState());
    const state = stateWith(plan());
    const recorded = recordMedicationOccurrence(
      state,
      occurrencesOnDay(state, '2026-11-08')[0],
      'all',
      [],
    );
    const invalidStates = [
      '',
      '{}',
      JSON.stringify({ ...state, version: 2 }),
      JSON.stringify({ ...state, plans: [...state.plans, ...state.plans] }),
      JSON.stringify({
        ...recorded,
        records: [...recorded.records, ...recorded.records],
      }),
      JSON.stringify({ ...recorded, plans: [] }),
      JSON.stringify({
        ...recorded,
        records: [{ ...recorded.records[0], takenMedicineIds: ['unrelated'] }],
      }),
    ];
    for (const raw of invalidStates) {
      // 잘못된 저장값을 정상 상태로 취급하지 않는지 확인한다.
      expect(function corruptedState() {
        parseMedicationState(raw);
      }).toThrow();
    }
  });
});
