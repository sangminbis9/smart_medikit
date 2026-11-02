import { dayKey, type Data, type Package } from './data';
import type { MedicineCategory } from './careGuides';

export type CareStatus = '보유' | '미보유' | '확인 필요' | '사용 주의';
export type CareMatch = {
  item: Package;
  status: CareStatus;
  reasons: string[];
  conditions: string[];
  warningTone: 'amber' | 'red';
};
type Medicine = Package['medicines'][number];

// 공식 출처와 제품·성분·원문·검토 유효기간이 모두 일치해야 승인 근거를 사용한다.
export function verifiedAssessment(medicine: Medicine, now = new Date()) {
  const assessment = medicine.care_assessment;
  const info = medicine.product_info;
  if (
    !assessment ||
    !info ||
    medicine.product_info_status !== 'SUCCEEDED' ||
    !medicine.product_code ||
    assessment.product_code !== medicine.product_code ||
    !info.indications?.trim() ||
    !info.dosage?.trim() ||
    !info.precautions?.trim() ||
    assessment.indications !== info.indications ||
    Date.parse(assessment.reviewed_at) > now.getTime() ||
    Date.parse(assessment.valid_until) <= now.getTime()
  )
    return null;
  let source: URL;
  try {
    source = new URL(assessment.source_url);
  } catch {
    return null;
  }
  if (
    source.protocol !== 'https:' ||
    source.username ||
    source.password ||
    !['nedrug.mfds.go.kr', 'data.mfds.go.kr', 'apis.data.go.kr'].includes(
      source.hostname,
    )
  )
    return null;
  const actual = [...new Set(medicine.ingredients)].sort();
  const reviewed = [...new Set(assessment.ingredient_names)].sort();
  if (!actual.length || JSON.stringify(actual) !== JSON.stringify(reviewed))
    return null;
  // 원문의 직접 근거는 일관성 검사이며 의미 해석은 서버의 검토 결과에 맡긴다.
  for (const match of assessment.matches)
    if (!info.indications.includes(match.evidence)) return null;
  return assessment;
}

// 제품명이나 성분 키워드로 추정하지 않고 검토된 증상·역할 연결만 반환한다.
export function matchCareMedicines(
  data: Data,
  symptomId: string,
  category: MedicineCategory,
  fresh: boolean,
  now = new Date(),
): {
  status: CareStatus;
  matches: CareMatch[];
  unknown: Package[];
  reason: string;
  warningTone: 'amber' | 'red';
} {
  const matches: CareMatch[] = [];
  const unknown: Package[] = [];
  for (const item of data.packages) {
    // 반출·등록·소진·폐기와 처방약은 자가관리 보유 제품으로 연결하지 않는다.
    if (item.state !== 'STORED' || item.kind !== 'OTC') continue;
    let unresolved = item.medicines.length === 0;
    let related = false;
    const conditions = new Set<string>();
    for (const medicine of item.medicines) {
      const assessment = verifiedAssessment(medicine, now);
      if (!assessment) {
        unresolved = true;
        continue;
      }
      for (const match of assessment.matches) {
        if (
          match.symptom_id !== symptomId ||
          !match.medicine_categories.includes(category)
        )
          continue;
        related = true;
        for (const condition of match.conditions) conditions.add(condition);
      }
    }
    if (!related) {
      if (unresolved) unknown.push(item);
      continue;
    }
    const reasons: string[] = [];
    let caution = false;
    let warningTone: 'amber' | 'red' = 'amber';
    if (!fresh) reasons.push('최신 보관 상태 확인 필요');
    if (unresolved) reasons.push('일부 의약품의 허가정보 확인 필요');
    if (!item.expiry.verified || !item.expiry.actual_expiry)
      reasons.push('실제 유효기간 확인 필요');
    else if (item.expiry.actual_expiry < dayKey(now)) {
      caution = true;
      reasons.push('확인된 유효기간이 지났어요');
    }
    if (item.safety_status !== 'CHECKED') reasons.push('안전정보 확인 필요');
    if (item.inventory?.stock_enabled && item.inventory.estimated_qty === 0)
      reasons.push('예상 잔여량이 0이에요. 실제 포장을 확인하세요');
    for (const alert of data.alerts) {
      // 확인 버튼을 눌렀어도 기존 주의정보를 안전하다는 뜻으로 해제하지 않는다.
      if (!alert.package_ids.includes(item.package_id)) continue;
      // 기존 보관함과 동일하게 DUR만 빨강, 동일성분 중복은 주황으로 구분한다.
      if (alert.type === 'DUR_CONTRAINDICATION') warningTone = 'red';
      if (
        alert.type === 'DUR_CONTRAINDICATION' ||
        alert.type === 'DUPLICATE_INGREDIENT' ||
        alert.type === 'EXPIRY'
      ) {
        caution = true;
        reasons.push(alert.title);
      }
      if (alert.type === 'REVIEW') reasons.push('보관함 확인 요청이 있어요');
    }
    matches.push({
      item,
      status: caution ? '사용 주의' : reasons.length ? '확인 필요' : '보유',
      reasons,
      conditions: [...conditions],
      warningTone,
    });
  }
  let status: CareStatus = '미보유';
  if (!fresh || unknown.length) status = '확인 필요';
  for (const match of matches) {
    if (match.status === '사용 주의') {
      status = '사용 주의';
      break;
    }
    if (match.status === '보유') status = '보유';
    else if (status !== '보유') status = '확인 필요';
  }
  const reason = !fresh
    ? '최신 보관함 정보를 확인할 수 없어요.'
    : unknown.length
      ? '효능 근거가 부족한 보관 품목이 있어 보유 여부를 확정할 수 없어요.'
      : !matches.length
        ? '조회된 보관 목록에서 관련 제품이 확인되지 않았어요.'
        : '허가 효능과 연결된 보관 제품 정보이며 개인별 사용 권장이 아니에요.';
  const warningTone = matches.some(
    // 하나라도 DUR 경고가 있으면 요약에도 가장 높은 주의 색상을 유지한다.
    function hasDur(match) {
      return match.warningTone === 'red';
    },
  )
    ? 'red'
    : 'amber';
  return { status, matches, unknown, reason, warningTone };
}
