import type { Medicine } from './data';
import { Badge } from './ui';

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
      <dl className="detail-fields">
        <div>
          <dt>주요 효능·효과</dt>
          <dd>{info?.indications || '효능·효과 확인 필요'}</dd>
        </div>
        <div>
          <dt>용법·용량</dt>
          <dd>{info?.dosage || '허가된 용법·용량 확인 필요'}</dd>
        </div>
        <div>
          <dt>사용 시 주의사항</dt>
          <dd>{info?.precautions || '사용 시 주의사항 확인 필요'}</dd>
        </div>
        <div>
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
