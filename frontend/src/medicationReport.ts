import { dayKey, type Package } from './data';
import type { Activity } from './activity';
import { occurrencesOnDay, type MedicationState } from './medicationPlans';
import type { ProductLink, ProductDetail } from './medicationProducts';

export type ReportRecord = {
  id: string;
  groupKey: string;
  name: string;
  strength: string;
  ingredients: string[];
  date: string;
  dateKind: '반출 날짜' | '일정 날짜';
  time?: string;
  certainty: 'confirmed' | 'scheduled';
  amount: string | null;
  incomplete: boolean;
};
export type ReportGroup = {
  key: string;
  name: string;
  strength: string;
  ingredients: string[];
  start: string;
  end: string;
  count: number;
  confirmedCount: number;
  scheduledCount: number;
  records: ReportRecord[];
};
export type ReportProducts = {
  links: ProductLink[];
  details: Record<string, ProductDetail>;
};

// 직접 입력한 날짜가 실제 달력 날짜인지 검증한다.
function validDay(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return (
    Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value
  );
}

// 빈 날짜·존재하지 않는 날짜·역전된 조회 기간을 막는다.
export function validReportRange(start: string, end: string): boolean {
  return validDay(start) && validDay(end) && start <= end;
}

// 달 단위로 이동하되 해당 날짜가 없으면 대상 월의 말일을 사용한다.
export function reportRange(
  months: 1 | 3 | 6,
  today = dayKey(),
): { start: string; end: string } {
  const date = new Date(`${today}T00:00:00Z`);
  const target = new Date(
    Date.UTC(date.getUTCFullYear(), date.getUTCMonth() - months, 1),
  );
  const last = new Date(
    Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0),
  ).getUTCDate();
  target.setUTCDate(Math.min(date.getUTCDate(), last));
  return { start: target.toISOString().slice(0, 10), end: today };
}

// 확인 기록을 날짜 역순으로 정렬하며 원본 데이터는 변경하지 않는다.
function newestFirst(left: ReportRecord, right: ReportRecord): number {
  return (
    right.date.localeCompare(left.date) ||
    (right.time ?? '').localeCompare(left.time ?? '') ||
    left.name.localeCompare(right.name, 'ko') ||
    left.id.localeCompare(right.id)
  );
}

// 기존 확인과 지난 일정의 추정을 구분하고 날짜를 실제 섭취 시각으로 해석하지 않는다.
export function buildReportRecords(
  activity: Activity,
  state: MedicationState,
  packages: Package[],
  products: ReportProducts = { links: [], details: {} },
  period?: { start: string; end: string; now: Date },
): ReportRecord[] {
  const result: ReportRecord[] = [];
  const packageMap = new Map<string, Package>();
  for (const item of packages) packageMap.set(item.package_id, item);
  for (const event of activity.history) {
    if (event.status !== 'TAKEN') continue;
    const pack = packageMap.get(event.packageId);
    // 여러 약이 담긴 포장의 수량이나 이름을 개별 약에 임의 배분하지 않는다.
    const medicine = pack?.medicines.length === 1 ? pack.medicines[0] : null;
    const code = medicine?.product_code;
    result.push({
      id: `activity:${event.eventId}`,
      groupKey: code ? `product:${code}:` : `package:${event.packageId}`,
      name: event.medicineName,
      strength: '',
      ingredients: code ? (medicine?.ingredients ?? []) : [],
      date: dayKey(event.occurredAt),
      dateKind: '반출 날짜',
      certainty: 'confirmed',
      amount:
        event.quantity === null
          ? null
          : `${event.quantity}${event.unit === 'CAPSULE' ? '캡슐' : '정'}`,
      incomplete: false,
    });
  }

  // 확인 기록이 있는 날짜만 복원해 긴 빈 기간의 일정을 전부 생성하지 않는다.
  const dates = new Set<string>();
  for (const record of state.records) {
    if (record.result !== 'all' && record.result !== 'some') continue;
    const match = /:(\d{4}-\d{2}-\d{2})T\d{2}:\d{2}\+09:00$/.exec(
      record.occurrenceId,
    );
    if (
      match &&
      (!period || (match[1] >= period.start && match[1] <= period.end))
    )
      dates.add(match[1]);
  }
  // 조회 기간과 실제 등록 일정이 겹치는 날짜만 추가하고 미래 날짜는 순회하지 않는다.
  if (period && validReportRange(period.start, period.end)) {
    const today = dayKey(period.now);
    let first: string | null = null;
    let last: string | null = null;
    for (const plan of state.plans) {
      const timings = plan.medicines.length
        ? plan.medicines.map(
            // 약별 예외가 있으면 공통 일정보다 우선한다.
            function timing(medicine) {
              return medicine.timing ?? plan.timing;
            },
          )
        : [plan.timing];
      for (const timing of timings) {
        if (timing.mode !== 'regular' || !timing.schedule) continue;
        const schedule = timing.schedule;
        const start =
          period.start > schedule.startDate ? period.start : schedule.startDate;
        let end = period.end < today ? period.end : today;
        if (schedule.endDate && schedule.endDate < end) end = schedule.endDate;
        if (plan.endedOn && plan.endedOn < end) end = plan.endedOn;
        if (start > end) continue;
        if (first === null || start < first) first = start;
        if (last === null || end > last) last = end;
      }
    }
    if (first !== null && last !== null) {
      const cursor = new Date(`${first}T00:00:00Z`);
      for (
        let day = first;
        day <= last;
        day = cursor.toISOString().slice(0, 10)
      ) {
        dates.add(day);
        cursor.setUTCDate(cursor.getUTCDate() + 1);
      }
    }
  }
  const links = new Map<string, ProductLink>();
  for (const link of products.links)
    links.set(JSON.stringify([link.planId, link.medicineId]), link);
  for (const date of dates) {
    for (const occurrence of occurrencesOnDay(state, date)) {
      const record = occurrence.record;
      if (record && record.result !== 'all' && record.result !== 'some')
        continue;
      // 확인이 없는 회차만 지난 예정 시각에 근거한 추정으로 표시하며 서버에는 저장하지 않는다.
      if (
        !record &&
        (!period ||
          !validReportRange(period.start, period.end) ||
          date < period.start ||
          date > period.end ||
          Date.parse(`${date}T${occurrence.time}:00+09:00`) >
            period.now.getTime())
      )
        continue;
      const certainty = record ? 'confirmed' : 'scheduled';
      if (occurrence.medicines.length === 0) {
        result.push({
          id: `manual:${occurrence.id}:unknown`,
          groupKey: `plan:${occurrence.planId}`,
          name: occurrence.title || '이름 없는 처방 묶음',
          strength: '',
          ingredients: [],
          date,
          dateKind: '일정 날짜',
          time: occurrence.time,
          certainty,
          amount: null,
          incomplete: true,
        });
      }
      for (const medicine of occurrence.medicines) {
        if (record && !record.takenMedicineIds.includes(medicine.id)) continue;
        const link = links.get(
          JSON.stringify([occurrence.planId, medicine.id]),
        );
        const detail = link ? products.details[link.productCode] : null;
        result.push({
          id: `manual:${occurrence.id}:${medicine.id}`,
          // 이름만으로 묶지 않으며 입력된 함량이 다르면 같은 품목 연결도 분리한다.
          groupKey: link
            ? `product:${link.productCode}:${medicine.strength.trim()}`
            : JSON.stringify(['manual', occurrence.planId, medicine.id]),
          name: medicine.name,
          strength: medicine.strength,
          ingredients:
            detail?.productCode === link?.productCode
              ? (detail?.ingredients ?? []).map(
                  // 확정 품목의 성분명만 사용하고 일반 용법을 개인 복용량으로 바꾸지 않는다.
                  function ingredient(item) {
                    return item.name;
                  },
                )
              : [],
          date,
          dateKind: '일정 날짜',
          time: occurrence.time,
          certainty,
          amount: medicine.dose || null,
          incomplete: false,
        });
      }
    }
  }
  return result.sort(newestFirst);
}

// 선택된 건을 동일 약 식별자로 묶어 기간·확인/추정 횟수·날짜별 내역을 계산한다.
export function groupReportRecords(records: ReportRecord[]): ReportGroup[] {
  const groups = new Map<string, ReportGroup>();
  for (const record of [...records].sort(newestFirst)) {
    let group = groups.get(record.groupKey);
    if (!group) {
      group = {
        key: record.groupKey,
        name: record.name,
        strength: record.strength,
        ingredients: [...record.ingredients],
        start: record.date,
        end: record.date,
        count: 0,
        confirmedCount: 0,
        scheduledCount: 0,
        records: [],
      };
      groups.set(record.groupKey, group);
    }
    group.count++;
    if (record.certainty === 'scheduled') group.scheduledCount++;
    else group.confirmedCount++;
    // 같은 품목의 최신 건에 성분이 없어도 선택된 다른 건의 확인 자료를 유지한다.
    if (!group.ingredients.length && record.ingredients.length)
      group.ingredients = [...record.ingredients];
    group.start = record.date < group.start ? record.date : group.start;
    group.end = record.date > group.end ? record.date : group.end;
    group.records.push(record);
  }
  return [...groups.values()];
}
