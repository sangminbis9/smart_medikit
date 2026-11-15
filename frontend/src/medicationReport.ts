import { dayKey, type Package } from './data';
import type { Activity } from './activity';
import type { MedicationState } from './medicationPlans';

export type ReportItem = {
  id: string;
  selectionIds: string[];
  kind: 'prescription' | 'cabinet';
  name: string;
  duration: string;
  medicines: string[];
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

// 등록한 시작일과 종료일을 모두 포함하며 복용 회차나 실제 복용일을 추정하지 않는다.
export function prescriptionDuration(start: string, end: string): string {
  if (!end) return '상시 복용 중';
  const days =
    (Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) /
      86400000 +
    1;
  return `${days}일분`;
}

// 직접 등록한 처방 카드와 조회 기간의 반출 약품 목록을 읽기 전용으로 만든다.
export function buildReportItems(
  activity: Activity,
  state: MedicationState,
  packages: Package[],
  period: { start: string; end: string },
): ReportItem[] {
  const items: ReportItem[] = [];
  if (!validReportRange(period.start, period.end)) return items;
  for (const plan of state.plans) {
    if (plan.endedOn && plan.endedOn < period.start) continue;
    const timings = plan.medicines.length
      ? plan.medicines.map(
          // 약별 예외 일정이 있으면 조회 범위 판단에 우선 적용한다.
          function timing(medicine) {
            return medicine.timing ?? plan.timing;
          },
        )
      : [plan.timing];
    const overlaps = timings.some(
      // 일정이 없는 등록도 보존하고 등록 기간이 겹치는 처방만 포함한다.
      function inRange(timing) {
        const schedule = timing.schedule;
        return (
          !schedule ||
          (schedule.startDate <= period.end &&
            (!schedule.endDate || schedule.endDate >= period.start) &&
            (!plan.endedOn || schedule.startDate <= plan.endedOn))
        );
      },
    );
    if (!overlaps) continue;
    const schedule = plan.timing.schedule;
    items.push({
      id: `plan:${plan.id}`,
      selectionIds: [`plan:${plan.id}`],
      kind: 'prescription',
      name: plan.title,
      // 일수는 등록 기간 그대로 유지하되 명시적 종료는 상시 표시보다 우선한다.
      duration: plan.endedOn
        ? schedule?.endDate
          ? `${prescriptionDuration(schedule.startDate, schedule.endDate)} · 복용 종료`
          : '복용 종료'
        : prescriptionDuration(
            schedule?.startDate ?? '',
            schedule?.endDate ?? '',
          ),
      medicines: plan.medicines.map(
        // 확인 여부와 무관하게 사용자가 처방에 등록한 약 이름 전체를 보여준다.
        function medicine(item) {
          return [item.name, item.strength].filter(Boolean).join(' ');
        },
      ),
    });
  }
  const packageMap = new Map<string, Package>();
  for (const pack of packages) packageMap.set(pack.package_id, pack);
  const cabinetItems = new Map<string, ReportItem>();
  const events = [...activity.history, ...activity.pending].sort(
    // 같은 약품의 이름은 가장 최근 반출 기록의 표기를 사용한다.
    function latest(left, right) {
      return right.occurredAt.localeCompare(left.occurredAt);
    },
  );
  for (const event of events) {
    const date = dayKey(event.occurredAt);
    if (date < period.start || date > period.end) continue;
    const pack = packageMap.get(event.packageId);
    // 여러 약이 든 포장이나 이름만 같은 약을 임의로 같은 제품으로 합치지 않는다.
    const code =
      pack?.medicines.length === 1 ? pack.medicines[0].product_code : null;
    const id = code
      ? `cabinet:product:${code}`
      : `cabinet:package:${event.packageId}`;
    const selectionId = `cabinet:package:${event.packageId}`;
    const existing = cabinetItems.get(id);
    if (existing) {
      if (!existing.selectionIds.includes(selectionId))
        existing.selectionIds.push(selectionId);
      continue;
    }
    // 제품 매핑이 없어져도 포함된 포장 ID로 제외 선택을 유지한다.
    const item: ReportItem = {
      id,
      selectionIds: [selectionId],
      kind: 'cabinet',
      name: event.medicineName,
      duration: '',
      medicines: [],
    };
    cabinetItems.set(id, item);
    // 확인·미복용·대기는 반출 목록으로만 사용하며 복용 사실로 변환하지 않는다.
    items.push(item);
  }
  return items;
}
