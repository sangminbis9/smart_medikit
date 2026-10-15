import { z } from 'zod';

const count = z.number().int().nonnegative().nullable().default(null);
const text = z.string().nullable().default(null);
const date = z.iso.date().nullable().default(null);

export const dimensionsSchema = z.object({
  width: z.number().positive(),
  depth: z.number().positive(),
  height: z.number().positive(),
});

const medicineSchema = z.object({
  medicine_id: z.string().min(1),
  name: z.string().min(1),
  form: z.enum(['TABLET', 'CAPSULE', 'OTHER', 'UNKNOWN']).default('UNKNOWN'),
  ingredients: z.array(z.string()).default([]),
  indications: z.array(z.string()).default([]),
});

export const packageSchema = z.object({
  package_id: z.string().min(1),
  box_id: z.string().min(1),
  kind: z.enum(['OTC', 'PRESCRIPTION']),
  state: z.enum(['REGISTERED', 'STORED', 'OUT', 'DEPLETED', 'DISPOSED']),
  name: z.string().default('약품 정보 미확인'),
  medicines: z.array(medicineSchema).default([]),
  size_class: z.enum(['SMALL', 'MEDIUM', 'LARGE']).nullable().default(null),
  base_color: z
    .string()
    .regex(/^#[0-9a-fA-F]{6}$/)
    .default('#d9e4d5'),
  inventory: z
    .object({
      stock_enabled: z.boolean(),
      initial_qty: count,
      estimated_qty: count,
    })
    .nullable()
    .default(null),
  remaining_doses: count,
  expiry: z
    .object({
      actual_expiry: date,
      temporary_management_date: date,
      verified: z.boolean().default(false),
    })
    .default({
      actual_expiry: null,
      temporary_management_date: null,
      verified: false,
    }),
  position: z
    .object({
      x: z.number().nonnegative(),
      y: z.number().nonnegative(),
      z_estimated: z.number().nonnegative(),
      certainty: z.enum(['OBSERVED', 'ESTIMATED', 'UNCERTAIN']),
    })
    .nullable()
    .default(null),
  safety_status: z
    .enum(['CHECKED', 'SAFETY_CHECK_PENDING'])
    .default('SAFETY_CHECK_PENDING'),
  note: text,
});

export const doseSchema = z.object({
  dose_id: z.string().min(1),
  package_id: z.string().min(1),
  scheduled_at: z.iso.datetime({ offset: true }),
  result: z.enum([
    'SCHEDULED',
    'ESTIMATED',
    'CONFIRMED',
    'NOT_TAKEN',
    'DEFERRED',
    'UNCONFIRMED',
  ]),
});

export const alertSchema = z.object({
  alert_id: z.string().min(1),
  type: z.enum([
    'DUPLICATE_INGREDIENT',
    'DUR_CONTRAINDICATION',
    'REGISTRATION',
    'EXPIRY',
    'REVIEW',
  ]),
  title: z.string().min(1),
  body: z.string(),
  scope: z.enum(['STORED', 'DAILY_WITHDRAWAL']).nullable().default(null),
  package_ids: z.array(z.string()).default([]),
  occurred_at: z.iso.datetime({ offset: true }),
  ack_state: z.enum(['PENDING', 'ACKNOWLEDGED']).default('PENDING'),
  source_event_id: text,
});

export const snapshotSchema = z.object({
  box: z
    .object({
      box_id: text,
      dimensions: dimensionsSchema.nullable().default(null),
      device_status: z
        .enum(['ONLINE', 'OFFLINE', 'UNKNOWN'])
        .default('UNKNOWN'),
    })
    .default({ box_id: null, dimensions: null, device_status: 'UNKNOWN' }),
  packages: z.array(packageSchema).default([]),
  doses: z.array(doseSchema).default([]),
  synced_at: z.iso.datetime({ offset: true }).nullable().default(null),
});

export type Package = z.infer<typeof packageSchema>;
export type Dose = z.infer<typeof doseSchema>;
export type Alert = z.infer<typeof alertSchema>;
export type Snapshot = z.infer<typeof snapshotSchema>;
export type Dimensions = z.infer<typeof dimensionsSchema>;
export type Data = Snapshot & { alerts: Alert[] };

// 입력되지 않은 정보는 수량·날짜를 추측하지 않고 빈 상태로 시작한다.
export function emptyData(): Data {
  return { ...snapshotSchema.parse({}), alerts: [] };
}

// 정제·캡슐이라는 근거와 재고 사용 여부가 모두 확인될 때만 수량을 표시한다.
export function hasStock(item: Package): boolean {
  return (
    item.kind === 'OTC' &&
    item.inventory?.stock_enabled === true &&
    item.medicines.length > 0 &&
    item.medicines.every(isCountable)
  );
}

// 정제와 캡슐 외 제형은 잔여량 관리에서 제외한다.
function isCountable(medicine: Package['medicines'][number]): boolean {
  return medicine.form === 'TABLET' || medicine.form === 'CAPSULE';
}

// 처방약의 회분과 일반약의 예상 정수를 서로 다른 문구로 표시한다.
export function stockLabel(item: Package): string | null {
  if (item.kind === 'PRESCRIPTION')
    return item.remaining_doses === null
      ? '예상 회분 미확인'
      : `예상 잔여 ${item.remaining_doses}회분`;
  if (!hasStock(item)) return null;
  const qty = item.inventory?.estimated_qty;
  const unit = item.medicines[0]?.form === 'CAPSULE' ? '캡슐' : '정';
  return qty == null ? '초기 수량 미확인' : `예상 잔여 ${qty}${unit}`;
}

// 실제 만료일과 임시 관리 기준일의 근거를 구분한다.
export function expiryLabel(item: Package, today = dayKey()): string {
  const expiry = item.expiry;
  if (expiry.verified && expiry.actual_expiry)
    return `${expiry.actual_expiry.replaceAll('-', '.')} ${expiry.actual_expiry < today ? '만료' : '까지'}`;
  if (expiry.temporary_management_date)
    return `임시 기준 ${expiry.temporary_management_date.replaceAll('-', '.')}`;
  return '유효기간 미확인';
}

// 모든 달력과 당일 표시를 한국 날짜로 통일한다.
export function dayKey(value: Date | string = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date(value));
}

// 원본 시간의 시간대와 관계없이 한국 시각을 표시한다.
export function timeLabel(value: string): string {
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value));
}

// 실제 제품정보에 포함된 이름·성분·효능 문자열만 검색하고 복용을 추천하지 않는다.
export function findPackages(items: Package[], query: string): Package[] {
  const words = query.trim().toLocaleLowerCase('ko-KR').split(/\s+/);
  // 폐기와 소진 기록은 활성 목록에서 숨긴다.
  return items.filter(function matches(item) {
    if (item.state === 'DISPOSED' || item.state === 'DEPLETED') return false;
    const parts = [
      item.name,
      item.kind === 'PRESCRIPTION' ? '처방 받은 약봉투' : '',
    ];
    for (const med of item.medicines)
      parts.push(med.name, ...med.ingredients, ...med.indications);
    const name = parts.join(' ').toLocaleLowerCase('ko-KR');
    // 검색어가 여러 개면 모두 일치하는 포장만 남긴다.
    return words.every(function contains(word) {
      return name.includes(word);
    });
  });
}

// 현재 보관 약품 관계만 3D에 사용하고 병용금기의 빨간 표시를 우선한다.
export function riskFor(
  id: string,
  selected: string | null,
  alerts: Alert[],
  items: Package[],
): 'amber' | 'red' | null {
  if (!selected) return null;
  const stored = new Set<string>();
  for (const item of items)
    if (item.state === 'STORED') stored.add(item.package_id);
  if (!stored.has(id) || !stored.has(selected)) return null;
  let risk: 'amber' | 'red' | null = null;
  for (const alert of alerts) {
    if (
      alert.scope !== 'STORED' ||
      !alert.package_ids.includes(id) ||
      !alert.package_ids.includes(selected)
    )
      continue;
    if (alert.type === 'DUR_CONTRAINDICATION') return 'red';
    if (alert.type === 'DUPLICATE_INGREDIENT') risk = 'amber';
  }
  return risk;
}

// 외부 응답의 중복 ID와 보관함 혼입을 거부한다.
export function parseData(
  inventory: unknown,
  alerts: unknown,
  boxId: string,
): Data {
  const snapshot = snapshotSchema.parse(inventory);
  const notices = z
    .object({ alerts: z.array(alertSchema).default([]) })
    .parse(alerts);
  if (snapshot.box.box_id && snapshot.box.box_id !== boxId)
    throw new Error('보관함 정보가 일치하지 않습니다.');
  const ids = new Set<string>();
  for (const item of snapshot.packages) {
    if (ids.has(item.package_id) || item.box_id !== boxId)
      throw new Error('포장 정보를 확인해주세요.');
    ids.add(item.package_id);
  }
  return { ...snapshot, ...notices };
}
