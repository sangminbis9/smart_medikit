import { z } from 'zod';

export type MedicationSchedule = {
  startDate: string;
  endDate: string;
  weekdays: number[];
  times: string[];
};

export type MedicationTiming = {
  mode: 'regular' | 'as_needed';
  schedule: MedicationSchedule | null;
};

export type MedicationItem = {
  id: string;
  name: string;
  strength: string;
  dose: string;
  timing: MedicationTiming | null;
};

export type MedicationPlan = {
  id: string;
  title: string;
  timing: MedicationTiming;
  notes: string;
  medicines: MedicationItem[];
  endedOn: string | null;
};

export type MedicationRecord = {
  occurrenceId: string;
  result: 'all' | 'some' | 'none' | 'deferred';
  takenMedicineIds: string[];
  recordedAt: string;
};

export type MedicationState = {
  version: 1;
  plans: MedicationPlan[];
  records: MedicationRecord[];
};

export type MedicationOccurrence = {
  id: string;
  planId: string;
  title: string;
  date: string;
  time: string;
  medicines: MedicationItem[];
  record: MedicationRecord | null;
};

const scheduleSchema = z.strictObject({
  startDate: z.string(),
  endDate: z.string(),
  weekdays: z.array(z.number().int()),
  times: z.array(z.string()),
});
const timingSchema = z.strictObject({
  mode: z.enum(['regular', 'as_needed']),
  schedule: scheduleSchema.nullable(),
});
const medicineSchema = z.strictObject({
  id: z.string().trim().min(1),
  name: z
    .string()
    .trim()
    .min(1, '추가한 약의 이름을 입력하거나 행을 삭제해 주세요.'),
  strength: z.string().trim(),
  dose: z.string().trim(),
  timing: timingSchema.nullable(),
});
const planSchema = z.strictObject({
  id: z.string().trim().min(1),
  title: z.string().trim().min(1, '처방 묶음 이름을 입력해 주세요.'),
  timing: timingSchema,
  notes: z.string().trim(),
  medicines: z.array(medicineSchema),
  endedOn: z.string().nullable(),
});
const stateSchema = z.strictObject({
  version: z.literal(1),
  plans: z.array(planSchema),
  records: z.array(
    z.strictObject({
      occurrenceId: z.string().min(1),
      result: z.enum(['all', 'some', 'none', 'deferred']),
      takenMedicineIds: z.array(z.string().min(1)),
      recordedAt: z.iso.datetime({ offset: true }),
    }),
  ),
});

// 날짜 문자열이 실제 그레고리력 날짜인지 시간대 변환 없이 검증한다.
function isValidDay(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const year = Number(value.slice(0, 4));
  const month = Number(value.slice(5, 7));
  const day = Number(value.slice(8, 10));
  if (year < 1 || month < 1 || month > 12) return false;
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return day >= 1 && day <= days[month - 1];
}

// 공통 일정과 약별 일정에 같은 날짜·요일·시간 규칙을 적용한다.
function validateTiming(timing: MedicationTiming): string | null {
  const schedule = timing.schedule;
  if (timing.mode === 'as_needed') {
    return schedule
      ? '필요할 때 복용하는 약에는 정기 일정을 지정할 수 없어요.'
      : null;
  }
  // 일정 없이 목록에만 등록하는 경우는 자동으로 회차를 만들지 않는다.
  if (!schedule) return null;
  if (!isValidDay(schedule.startDate))
    return '일정 시작일을 올바르게 입력해 주세요.';
  if (schedule.endDate && !isValidDay(schedule.endDate))
    return '일정 종료일을 올바르게 입력해 주세요.';
  if (schedule.endDate && schedule.endDate < schedule.startDate)
    return '일정 종료일은 시작일보다 빠를 수 없어요.';
  if (!schedule.weekdays.length) return '복용 요일을 한 개 이상 선택해 주세요.';
  for (const day of schedule.weekdays) {
    if (day < 0 || day > 6) return '복용 요일을 다시 선택해 주세요.';
  }
  if (new Set(schedule.weekdays).size !== schedule.weekdays.length)
    return '같은 복용 요일을 중복해서 지정할 수 없어요.';
  if (!schedule.times.length) return '복용 시간을 한 개 이상 입력해 주세요.';
  for (const time of schedule.times) {
    if (!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(time))
      return '복용 시간을 00:00부터 23:59 사이로 입력해 주세요.';
  }
  if (new Set(schedule.times).size !== schedule.times.length)
    return '같은 복용 시간을 중복해서 지정할 수 없어요.';
  return null;
}

// 아직 등록한 약과 복용 기록이 없는 독립된 상태를 만든다.
export function emptyMedicationState(): MedicationState {
  return { version: 1, plans: [], records: [] };
}

// 복용량이나 일정을 추측하지 않는 새 처방 묶음 초안을 만든다.
export function createMedicationPlan(): MedicationPlan {
  return {
    id: crypto.randomUUID(),
    title: '',
    timing: { mode: 'regular', schedule: null },
    notes: '',
    medicines: [],
    endedOn: null,
  };
}

// 입력한 처방 묶음과 개별 약의 저장 가능 여부를 한국어로 설명한다.
export function validateMedicationPlan(plan: MedicationPlan): string | null {
  const parsed = planSchema.safeParse(plan);
  if (!parsed.success) {
    const issue = parsed.error.issues[0];
    return issue.code === 'too_small'
      ? issue.message
      : '처방 묶음의 입력값 형식을 확인해 주세요.';
  }
  const value = parsed.data;
  const timingError = validateTiming(value.timing);
  if (timingError) return timingError;
  if (value.endedOn !== null && !isValidDay(value.endedOn))
    return '복용 종료 날짜를 올바르게 입력해 주세요.';
  const medicineIds = new Set<string>();
  for (const medicine of value.medicines) {
    if (medicineIds.has(medicine.id)) return '같은 약 항목이 중복되어 있어요.';
    medicineIds.add(medicine.id);
    if (medicine.timing) {
      const error = validateTiming(medicine.timing);
      if (error) return `${medicine.name}: ${error}`;
    }
  }
  return null;
}

// 다른 묶음의 기록까지 지우지 않도록 묶음 식별자를 구분자로부터 분리한다.
function occurrencePrefix(planId: string): string {
  return `medication:${encodeURIComponent(planId)}:`;
}

// 한국 날짜와 시각을 그대로 담은 안정적인 복용 회차 식별자를 만든다.
function occurrenceId(planId: string, day: string, time: string): string {
  return `${occurrencePrefix(planId)}${day}T${time}+09:00`;
}

// 한국 날짜의 일정 조건을 확인하고 해당하는 복용 시간만 반환한다.
function timesOnDay(timing: MedicationTiming, day: string): string[] {
  const schedule = timing.schedule;
  if (timing.mode !== 'regular' || !schedule) return [];
  if (day < schedule.startDate || (schedule.endDate && day > schedule.endDate))
    return [];
  // 입력은 이미 한국 날짜이므로 브라우저의 현지 시간대로 다시 변환하지 않는다.
  const weekday = new Date(`${day}T00:00:00Z`).getUTCDay();
  return schedule.weekdays.includes(weekday) ? schedule.times : [];
}

// 약별 일정 차이를 반영해 한국 날짜 하루의 묶음별 회차를 생성한다.
export function occurrencesOnDay(
  state: MedicationState,
  day: string,
): MedicationOccurrence[] {
  if (!isValidDay(day)) return [];
  const occurrences: MedicationOccurrence[] = [];
  const records = new Map<string, MedicationRecord>();
  for (const record of state.records) records.set(record.occurrenceId, record);
  for (const plan of state.plans) {
    // 종료한 날의 기록은 유지하고 그 다음 날부터 일정을 만들지 않는다.
    if (plan.endedOn && day > plan.endedOn) continue;
    const medicinesByTime = new Map<string, MedicationItem[]>();
    if (plan.medicines.length === 0) {
      for (const time of timesOnDay(plan.timing, day))
        medicinesByTime.set(time, []);
    } else {
      // 등록된 약이 있으면 실제로 해당하는 약이 없는 공통 회차는 생성하지 않는다.
      for (const medicine of plan.medicines) {
        const timing = medicine.timing ?? plan.timing;
        for (const time of timesOnDay(timing, day)) {
          const medicines = medicinesByTime.get(time) ?? [];
          medicines.push(medicine);
          medicinesByTime.set(time, medicines);
        }
      }
    }
    for (const [time, medicines] of medicinesByTime) {
      const id = occurrenceId(plan.id, day, time);
      occurrences.push({
        id,
        planId: plan.id,
        title: plan.title,
        date: day,
        time,
        medicines,
        record: records.get(id) ?? null,
      });
    }
  }
  // 같은 시각의 여러 처방 묶음도 화면에서 일정한 순서를 유지한다.
  return occurrences.sort(function byTimeAndTitle(left, right) {
    return (
      left.time.localeCompare(right.time) ||
      left.title.localeCompare(right.title, 'ko') ||
      left.planId.localeCompare(right.planId)
    );
  });
}

// 회차에 속한 약만 복용 확인에 사용하고 일부 복용의 모호함을 제한한다.
function validateTakenMedicines(
  occurrence: MedicationOccurrence,
  result: MedicationRecord['result'],
  takenMedicineIds: string[],
): string | null {
  const medicineIds = new Set<string>();
  for (const medicine of occurrence.medicines) medicineIds.add(medicine.id);
  const takenIds = new Set(takenMedicineIds);
  if (takenIds.size !== takenMedicineIds.length)
    return '선택한 약이 중복되어 있어요.';
  for (const id of takenIds) {
    if (!medicineIds.has(id))
      return '이 복용 회차에 없는 약이 선택되어 있어요.';
  }
  if (result === 'some' && medicineIds.size > 0) {
    if (takenIds.size === 0) return '실제로 복용한 약을 선택해 주세요.';
    if (takenIds.size === medicineIds.size)
      return '예정된 약을 모두 복용했다면 모두 복용을 선택해 주세요.';
  }
  if (result === 'all' && takenIds.size !== medicineIds.size)
    return '모두 복용 기록과 선택된 약이 일치하지 않아요.';
  if ((result === 'none' || result === 'deferred') && takenIds.size > 0)
    return '미복용·확인 보류 기록에는 복용한 약을 지정할 수 없어요.';
  return null;
}

// 저장된 회차 식별자에서 날짜를 읽되 잘못된 형식을 자동 복구하지 않는다.
function occurrenceDay(id: string): string | null {
  const match =
    /^medication:[^:]+:(\d{4}-\d{2}-\d{2})T(?:[01]\d|2[0-3]):[0-5]\d\+09:00$/.exec(
      id,
    );
  return match && isValidDay(match[1]) ? match[1] : null;
}

// 저장 문자열 전체를 검증하고 손상되거나 연결이 깨진 기록은 오류로 알린다.
export function parseMedicationState(raw: string | null): MedicationState {
  if (raw === null) return emptyMedicationState();
  let state: MedicationState;
  try {
    state = stateSchema.parse(JSON.parse(raw));
  } catch {
    throw new Error(
      '저장된 복용약 데이터를 읽을 수 없어요. 원본을 보존한 채 다시 확인해 주세요.',
    );
  }
  const planIds = new Set<string>();
  for (const plan of state.plans) {
    if (planIds.has(plan.id) || validateMedicationPlan(plan))
      throw new Error('저장된 처방 묶음의 정보가 올바르지 않아요.');
    planIds.add(plan.id);
  }
  const recordIds = new Set<string>();
  for (const record of state.records) {
    const day = occurrenceDay(record.occurrenceId);
    // 기록은 현재 구성에서 실제로 존재하는 회차와 연결되어야 한다.
    const occurrences = day ? occurrencesOnDay(state, day) : [];
    // 해당 날짜의 회차 중 저장된 식별자와 일치하는 것을 찾는다.
    const occurrence = occurrences.find(function matchingId(candidate) {
      return candidate.id === record.occurrenceId;
    });
    if (
      recordIds.has(record.occurrenceId) ||
      !occurrence ||
      !isValidDay(record.recordedAt.slice(0, 10)) ||
      validateTakenMedicines(occurrence, record.result, record.takenMedicineIds)
    )
      throw new Error('저장된 복용 확인 기록이 일정과 일치하지 않아요.');
    recordIds.add(record.occurrenceId);
  }
  return state;
}

// 기존 확인이 다른 약이나 일정에 적용되지 않도록 의미 있는 변경을 구분한다.
function regimenSignature(plan: MedicationPlan): string {
  return JSON.stringify([plan.timing, plan.medicines]);
}

// 새 묶음을 추가하거나 수정하고 바뀐 약·일정의 이전 확인 기록을 무효화한다.
export function saveMedicationPlan(
  state: MedicationState,
  plan: MedicationPlan,
): MedicationState {
  const error = validateMedicationPlan(plan);
  if (error) throw new Error(error);
  const saved = planSchema.parse(plan);
  // 같은 식별자의 묶음만 수정 대상으로 찾는다.
  const previous = state.plans.find(function matchingPlan(candidate) {
    return candidate.id === saved.id;
  });
  const changed =
    previous && regimenSignature(previous) !== regimenSignature(saved);
  // 다른 묶음은 기존 순서를 유지하며 수정된 묶음만 교체한다.
  const plans = state.plans.map(function replacePlan(candidate) {
    return candidate.id === saved.id ? saved : candidate;
  });
  if (!previous) plans.push(saved);
  // 종료일만 변경할 때는 종료일까지의 기록을 유지한다.
  const records = state.records.filter(function keepValidRecord(record) {
    if (!record.occurrenceId.startsWith(occurrencePrefix(saved.id)))
      return true;
    if (changed) return false;
    const day = occurrenceDay(record.occurrenceId);
    return !saved.endedOn || (day !== null && day <= saved.endedOn);
  });
  return { version: 1, plans, records };
}

// 삭제한 묶음과 연결된 복용 기록만 함께 제거한다.
export function removeMedicationPlan(
  state: MedicationState,
  id: string,
): MedicationState {
  // 삭제 대상과 다른 묶음을 보존한다.
  const plans = state.plans.filter(function keepPlan(plan) {
    return plan.id !== id;
  });
  // 식별자 접두어가 완전히 일치하는 묶음의 기록만 제거한다.
  const records = state.records.filter(function keepRecord(record) {
    return !record.occurrenceId.startsWith(occurrencePrefix(id));
  });
  return { version: 1, plans, records };
}

// 현재 회차에 대한 사용자 확인만 반영하며 재고와 자동 복용 추정은 변경하지 않는다.
export function recordMedicationOccurrence(
  state: MedicationState,
  occurrence: MedicationOccurrence,
  result: MedicationRecord['result'],
  takenMedicineIds: string[],
): MedicationState {
  // 확인 창을 연 뒤 변경된 회차에 이전 선택을 적용하지 않는다.
  const current = occurrencesOnDay(state, occurrence.date).find(
    function matchingOccurrence(candidate) {
      return candidate.id === occurrence.id;
    },
  );
  if (
    !current ||
    current.planId !== occurrence.planId ||
    current.time !== occurrence.time ||
    JSON.stringify(current.medicines) !== JSON.stringify(occurrence.medicines)
  )
    throw new Error(
      '복용 일정이 변경되었어요. 최신 일정을 다시 열어 확인해 주세요.',
    );
  // 자동 재조회로 버전이 갱신돼도 다른 기기의 최신 확인을 오래된 창으로 덮어쓰지 않는다.
  if (JSON.stringify(current.record) !== JSON.stringify(occurrence.record))
    throw new Error(
      '복용 확인 기록이 변경되었어요. 최신 기록을 다시 열어 확인해 주세요.',
    );
  let ids: string[] = [];
  if (result === 'all') {
    for (const medicine of current.medicines) ids.push(medicine.id);
  } else if (result === 'some') ids = [...takenMedicineIds];
  const error = validateTakenMedicines(current, result, ids);
  if (error) throw new Error(error);
  const record: MedicationRecord = {
    occurrenceId: current.id,
    result,
    takenMedicineIds: ids,
    recordedAt: new Date().toISOString(),
  };
  // 같은 회차를 다시 확인하면 중복 기록 대신 최신 사용자 확인으로 바꾼다.
  const records = state.records.filter(function otherOccurrence(candidate) {
    return candidate.occurrenceId !== current.id;
  });
  records.push(record);
  return { ...state, records };
}
