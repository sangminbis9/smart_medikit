import type { Medicine } from './data';
import { Badge } from './ui';

// XML 변환 과정에서 생긴 빈 줄과 들여쓰기만 표시할 때 정리한다.
export function formatProductText(value: string | null | undefined) {
  if (!value) return '';
  return value
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .map(
      // 줄 안의 내용과 표 구분 탭은 보존한다.
      function trimIndent(line) {
        return line.replace(/^ +| +$/g, '');
      },
    )
    .filter(
      // 빈 줄로만 구성된 XML 서식은 화면에서 제외한다.
      function hasContent(line) {
        return line.trim().length > 0;
      },
    )
    .join('\n');
}

// 저장된 제품 문구와 출처를 표시하고 없는 사용법이나 용량은 만들지 않는다.
export default function MedicineUsage({ medicine }: { medicine: Medicine }) {
  const info = medicine.product_info;
  const complete =
    medicine.product_info_status === 'SUCCEEDED' &&
    info?.indications?.trim() &&
    info.dosage?.trim() &&
    info.precautions?.trim();
  return (
    <article className="medicine-usage">
      <h3>{medicine.name}</h3>
      {info?.source === 'DYNAMODB_PRODUCT_INFO' && (
        <p>보관함에 등록된 제품 정보</p>
      )}
      {!complete && <Badge tone="amber">의약품 사용정보 확인 필요</Badge>}
      <dl className="detail-fields medicine-usage-fields">
        <div className="usage-dosage">
          <dt>용법·용량</dt>
          <dd>
            {formatProductText(info?.dosage) || '허가된 용법·용량 확인 필요'}
          </dd>
        </div>
        <div className="usage-precautions">
          <dt>사용 시 주의사항</dt>
          <dd>
            {formatProductText(info?.precautions) ||
              '사용 시 주의사항 확인 필요'}
          </dd>
        </div>
        <div className="usage-indications">
          <dt>주요 효능·효과</dt>
          <dd>
            {formatProductText(info?.indications) || '효능·효과 확인 필요'}
          </dd>
        </div>
        <div className="usage-ingredients">
          <dt>주요 성분</dt>
          <dd>{medicine.ingredients.join(', ') || '성분 정보 확인 필요'}</dd>
        </div>
      </dl>
      {medicine.product_code && (
        <a
          className="text-button"
          href={`https://nedrug.mfds.go.kr/pbp/CCBBB01/getItemDetail?itemSeq=${medicine.product_code}`}
          target="_blank"
          rel="noreferrer"
        >
          의약품안전나라 품목정보 확인 ↗
        </a>
      )}
    </article>
  );
}
