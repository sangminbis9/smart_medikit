import { displayMedicineName, type Medicine } from './data';
import { Badge } from './ui';

// HTML 엔터티만 문자로 해석하고 제품 문구를 HTML로 실행하지 않는다.
function decodeProductEntities(value: string) {
  return value.replace(
    /&(?:#[xX][0-9a-fA-F]+|#[0-9]+|[A-Za-z][A-Za-z0-9]+);/g,
    function decode(entity) {
      if (typeof document !== 'undefined') {
        // 문자표현 하나만 전달하므로 원문 태그가 DOM에 삽입되지 않는다.
        const field = document.createElement('textarea');
        field.innerHTML = entity;
        return field.value;
      }
      const common: Record<string, string> = {
        '&nbsp;': ' ',
        '&amp;': '&',
        '&lt;': '<',
        '&gt;': '>',
        '&quot;': '"',
        '&apos;': "'",
      };
      if (common[entity]) return common[entity];
      const numeric = /^&#(x[0-9a-f]+|[0-9]+);$/i.exec(entity);
      if (!numeric) return entity;
      const number =
        numeric[1][0].toLowerCase() === 'x'
          ? parseInt(numeric[1].slice(1), 16)
          : Number(numeric[1]);
      return number > 0 &&
        number <= 0x10ffff &&
        !(number >= 0xd800 && number <= 0xdfff)
        ? String.fromCodePoint(number)
        : entity;
    },
  );
}

// XML 변환 과정에서 생긴 빈 줄과 들여쓰기만 표시할 때 정리한다.
export function formatProductText(value: string | null | undefined) {
  if (!value) return '';
  // 중복 인코딩된 문자표현까지 최대 두 번만 해석한다.
  const decoded = decodeProductEntities(decodeProductEntities(value)).replace(
    /\u00a0/g,
    ' ',
  );
  return decoded
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .map(
      // 줄 안의 내용과 표 구분 탭은 보존한다.
      function trimIndent(line) {
        return line.replace(/^[ \u00a0]+|[ \u00a0]+$/g, '');
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

// 제목과 중복되는 첫 줄만 제거하고 용량·계산식·표의 순서는 유지한다.
export function formatDosageText(value: string | null | undefined) {
  return formatProductText(value).replace(
    /^(?:용법\s*[·ㆍ]?\s*용량|용법\s*및\s*용량)\s*\n/,
    '',
  );
}

// 저장된 제품 문구와 출처를 표시하고 없는 사용법이나 용량은 만들지 않는다.
export default function MedicineUsage({ medicine }: { medicine: Medicine }) {
  const info = medicine.product_info;
  const complete =
    medicine.product_info_status === 'SUCCEEDED' &&
    info?.indications?.trim() &&
    info.dosage?.trim() &&
    info.precautions?.trim();
  // 평문으로 전달된 표·계산식은 원래의 행과 열을 복원할 수 없다.
  const hasStructuredDosage = /다음\s*표|표를\s*참조|계산식|\bCLcr\b/i.test(
    info?.dosage || '',
  );
  return (
    <article className="medicine-usage">
      <h3>{displayMedicineName(medicine.name)}</h3>
      {info?.source === 'DYNAMODB_PRODUCT_INFO' && (
        <p>보관함에 등록된 제품 정보</p>
      )}
      {!complete && <Badge tone="amber">의약품 사용정보 확인 필요</Badge>}
      <dl className="detail-fields medicine-usage-fields">
        <div className="usage-dosage">
          <dt>용법·용량</dt>
          <dd>
            {formatDosageText(info?.dosage) || '허가된 용법·용량 확인 필요'}
            {hasStructuredDosage && (
              <span className="dosage-source-note">
                표와 계산식은 배열이 달라질 수 있어요.{' '}
                {medicine.product_code ? (
                  <a
                    href={`https://nedrug.mfds.go.kr/pbp/CCBBB01/getItemDetail?itemSeq=${medicine.product_code}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    식약처 원문에서 확인 ↗
                  </a>
                ) : (
                  '식약처 원문을 확인해주세요.'
                )}
              </span>
            )}
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
