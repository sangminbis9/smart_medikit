import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { CareGuide } from './careGuides';
import { findIndicationMedicines } from './careMatching';
import { expiryLabel } from './data';
import type { Medkit } from './useMedkit';
import MedicineUsage, { formatProductText } from './MedicineUsage';
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
  // 제품 마스터만 있는 항목은 실제 보유 포장과 분리한다.
  const stored = matches.filter(function isStored(match) {
    return match.packages.length > 0;
  });
  const unconfirmed = matches.filter(function isUnconfirmed(match) {
    return match.packages.length === 0;
  });
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
      aria-label="효능 원문 검색 결과"
    >
      <div className="catalog-heading">
        <h2>
          보유중인 {guide.name} 관련 약품 <span>{stored.length}개</span>
        </h2>
        <Badge>문구 검색</Badge>
      </div>
      <p>
        등록된 효능·효과 문구에서 선택한 증상 표현을 찾은 결과예요. 검색어
        일치만으로 이 증상이나 사용자에게 적합한 제품인지 판단할 수 없어요.
      </p>
      <Note>
        복용·사용하기 전에 효능의 적용 조건, 용법·주의사항과 처방 여부를
        확인하고 의사 또는 약사와 상담하세요. 처방약은 처방에 따라 사용하세요.
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
        <p>
          등록된 효능 문구에서 일치하는 표현을 찾지 못했어요. 검색 결과가
          없더라도 관련 제품이 없다는 뜻은 아니에요.
        </p>
      )}
      {stored.length === 0 && (
        <p>보관 중으로 등록된 관련 포장을 확인하지 못했어요.</p>
      )}
      {[
        { label: '', items: stored },
        { label: '보관 정보 미확인 제품', items: unconfirmed },
      ].map(
        // 보관 여부가 미확인인 제품은 제목과 목록을 분리한다.
        function group(group) {
          if (!group.items.length) return null;
          return (
            <div key={group.label || 'stored'}>
              {group.label && (
                <h3 className="care-unconfirmed-heading">
                  {group.label} <span>{group.items.length}개</span>
                </h3>
              )}
              <ul className="catalog-list">
                {group.items.map(
                  // 효능 근거와 실제 포장 상태를 구분해 표시한다.
                  function product(match) {
                    const { medicine, packages } = match;
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
                            <b>등록된 효능·효과 원문</b>
                            {formatProductText(
                              medicine.product_info?.indications,
                            )}
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
            </div>
          );
        },
      )}
      {selected && (
        <Dialog
          title="제품 사용정보"
          onClose={close}
          className="product-info-dialog"
        >
          <MedicineUsage medicine={selected.medicine} />
        </Dialog>
      )}
    </section>
  );
}
