import { useState } from 'react';
import { displayMedicineName, findCatalogMedicines } from './data';
import type { Medkit } from './useMedkit';
import MedicineUsage, { formatDosageText } from './MedicineUsage';
import { Dialog, Note } from './ui';

// 포장 정보가 없는 등록 제품도 홈·케어에서 검색하고 실제 허가정보를 열람하게 한다.
export default function CatalogMedicines({
  kit,
  query = '',
}: {
  kit: Medkit;
  query?: string;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const allMedicines = findCatalogMedicines(kit.data);
  const medicines = findCatalogMedicines(kit.data, query);
  // 재조회 뒤 사라지거나 포장과 연결된 제품의 오래된 상세를 남기지 않는다.
  const selectedMedicine = allMedicines.find(function selected(medicine) {
    return medicine.medicine_id === selectedId;
  });
  // 상세 창을 닫으면 열었던 카드로 초점을 돌린다.
  function closeMedicine() {
    setSelectedId(null);
  }
  if (kit.demo || !allMedicines.length) return null;
  return (
    <section className="catalog-medicines" aria-label="등록된 약품">
      <div className="catalog-heading">
        <h2>
          등록된 약품 <span>{medicines.length}개</span>
        </h2>
      </div>
      {!kit.data.catalog_complete && <p>제품 목록 일부만 조회되었어요.</p>}
      {(!kit.online || kit.status !== 'ready') && (
        <p role="status">이전 조회 결과예요. 최신 정보 확인이 필요해요.</p>
      )}
      {!medicines.length && (
        <p>일치하는 등록 약품이 없어요. 다른 이름이나 성분으로 검색해주세요.</p>
      )}
      <ul className="catalog-list">
        {medicines.map(
          // 제품 카드는 보유 수량을 만들지 않고 확인 가능한 제품 정보만 표시한다.
          function productCard(medicine) {
            // 선택한 제품의 최신 사용정보를 연다.
            function openMedicine() {
              setSelectedId(medicine.medicine_id);
            }
            return (
              <li key={medicine.medicine_id}>
                <button
                  className="catalog-card"
                  onClick={openMedicine}
                  aria-label={`${displayMedicineName(medicine.name)} · 사용법 상세`}
                >
                  <strong>{displayMedicineName(medicine.name)}</strong>
                  <span>
                    {medicine.ingredients.join(', ') || '성분 정보 확인 필요'}
                  </span>
                  <span className="product-dosage">
                    <b>용법·용량</b>
                    <span className="dosage-preview">
                      {formatDosageText(medicine.product_info?.dosage) ||
                        '용법·용량 정보 확인 필요'}
                    </span>
                  </span>
                  <span className="catalog-link">
                    제품 사용정보에서 전체 내용 확인 →
                  </span>
                </button>
              </li>
            );
          },
        )}
      </ul>
      {selectedMedicine && (
        <Dialog
          title="제품 사용정보"
          onClose={closeMedicine}
          className="product-info-dialog"
        >
          <Note>제품 정보이며 개인별 사용 권장이 아니에요.</Note>
          <MedicineUsage medicine={selectedMedicine} />
        </Dialog>
      )}
    </section>
  );
}
