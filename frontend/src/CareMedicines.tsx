import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { CareGuide } from './careGuides';
import { findIndicationMedicines } from './careMatching';
import { expiryLabel } from './data';
import type { Medkit } from './useMedkit';
import MedicineUsage from './MedicineUsage';
import { Badge, Dialog, Note } from './ui';

// 선택 증상과 효능 원문이 일치하는 제품만 보여주고 사용정보를 연결한다.
export default function CareMedicines({
  kit,
  guide,
}: {
  kit: Medkit;
  guide: CareGuide;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { matches, unavailableCount } = findIndicationMedicines(
    kit.data,
    guide.id,
  );
  // 증상 변경·재조회로 결과에서 빠진 제품은 상세 창에서도 제외한다.
  const selected = matches.find(function selectedProduct(match) {
    return match.medicine.medicine_id === selectedId;
  });
  // 상세 창을 닫고 선택한 카드로 초점을 돌린다.
  function close() {
    setSelectedId(null);
  }
  return (
    <section
      className="catalog-medicines care-related"
      aria-label="증상 관련 약품"
    >
      <div className="catalog-heading">
        <h2>
          {guide.name} 관련 약품 <span>{matches.length}개</span>
        </h2>
        <Badge>효능 정보 검색</Badge>
      </div>
      <p>
        등록된 효능·효과에 이 증상이 나오는 제품이에요. 일치한 원문의 적용
        조건과 사용법을 함께 확인해주세요.
      </p>
      <Note>
        검색 결과는 개인별 복용 권장이 아니에요. 보관 상태·유효기간과 처방
        여부는 별도로 확인해주세요.
      </Note>
      {kit.demo && <Badge tone="amber">시연 정보 · 실제 보유 아님</Badge>}
      {(!kit.online || kit.status !== 'ready') && (
        <p role="status">이전 조회 결과예요. 최신 정보 확인이 필요해요.</p>
      )}
      {(!kit.data.catalog_complete ||
        kit.data.inventory_status !== 'CONFIRMED') && (
        <p>일부 정보만 조회되었어요. 검색 결과가 완전하지 않을 수 있어요.</p>
      )}
      {unavailableCount > 0 && (
        <p>
          효능 정보를 확인할 수 없는 제품 {unavailableCount}개는 검색에서
          제외했어요.
        </p>
      )}
      {!matches.length && (
        <p>등록된 효능 정보에서 이 증상과 일치하는 약품을 찾지 못했어요.</p>
      )}
      <ul className="catalog-list">
        {matches.map(
          // 효능 근거와 실제 포장 상태를 구분해 표시한다.
          function product(match) {
            const { medicine, packages, evidence } = match;
            // 선택한 제품의 저장된 사용정보를 연다.
            function open() {
              setSelectedId(medicine.medicine_id);
            }
            return (
              <li
                className="care-related-card"
                key={medicine.product_code || medicine.medicine_id}
              >
                <button
                  className="catalog-card"
                  onClick={open}
                  aria-label={`${medicine.name} · 사용법 상세`}
                >
                  <strong>{medicine.name}</strong>
                  <span className="indication-evidence">
                    <b>일치한 효능·효과</b>
                    {evidence}
                  </span>
                  {!packages.length && (
                    <Badge tone="amber">보관 정보 미확인</Badge>
                  )}
                  <span className="catalog-link">
                    효능·용법·주의사항 확인 →
                  </span>
                </button>
                {packages.map(
                  // 처방약·미확인 기한을 숨기지 않고 포장 상세로 연결한다.
                  function stored(item) {
                    return (
                      <Link
                        className="care-related-package"
                        to={`/packages/${encodeURIComponent(item.package_id)}`}
                        key={item.package_id}
                      >
                        {item.name} ·{' '}
                        {item.kind === 'PRESCRIPTION'
                          ? '처방약 봉투'
                          : '보관 포장'}{' '}
                        · {expiryLabel(item)} →
                      </Link>
                    );
                  },
                )}
              </li>
            );
          },
        )}
      </ul>
      {selected && (
        <Dialog title="제품 사용정보" onClose={close}>
          <MedicineUsage medicine={selected.medicine} />
        </Dialog>
      )}
    </section>
  );
}
