import { useEffect, useRef, useState } from 'react';
import type { Medicine } from './data';
import type { MedicationItem } from './medicationPlans';
import type {
  MedicationProducts,
  ProductCandidate,
  ProductDetail,
} from './medicationProducts';
import MedicineUsage from './MedicineUsage';
import { Dialog } from './ui';

// 사용자가 이름과 제조사를 확인한 후보만 명시적으로 선택한다.
export function MedicationProductSearch({
  name,
  products,
  selected,
  onSelect,
}: {
  name: string;
  products: MedicationProducts;
  selected: ProductCandidate | null;
  onSelect: (candidate: ProductCandidate) => void | Promise<void>;
}) {
  const [query, setQuery] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<ProductCandidate[] | null>(null);
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const pendingRef = useRef(false);
  const term = query ?? name;

  // 약 이름만 전송하고 응답이 없거나 실패한 경우를 구분한다.
  async function search() {
    if (pendingRef.current) return;
    if (!term.trim()) {
      setError('검색할 제품명을 입력해 주세요.');
      return;
    }
    pendingRef.current = true;
    setPending(true);
    setError('');
    setCandidates(null);
    try {
      setCandidates(await products.search(term.trim()));
    } catch {
      setError('제품 정보를 조회하지 못했어요. 잠시 후 다시 시도해 주세요.');
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  }
  // 검색어를 바꾸면 이전 검색 결과는 더 이상 선택할 수 없다.
  function changeQuery(event: React.ChangeEvent<HTMLInputElement>) {
    setQuery(event.target.value);
    setCandidates(null);
    setError('');
  }
  return (
    <div className="medication-product-search">
      <p className="medication-hint">
        제품명과 제조사를 처방전이나 포장과 비교해 선택해 주세요.
      </p>
      <label className="medication-field">
        제품명 검색
        <input
          value={term}
          onChange={changeQuery}
          maxLength={200}
          disabled={pending || !products.available}
        />
      </label>
      <button
        type="button"
        className="secondary-button"
        onClick={search}
        disabled={pending || !products.available}
      >
        {pending ? '조회 중…' : '식약처 제품 검색'}
      </button>
      {!products.available && (
        <p className="medication-hint">
          시연 데이터에서는 제품 조회를 사용할 수 없어요.
        </p>
      )}
      {error && (
        <p className="medication-form-error" role="alert">
          {error}
        </p>
      )}
      {selected && (
        <p className="medication-product-selected">
          선택: {selected.productName}
          {selected.manufacturer ? ` · ${selected.manufacturer}` : ''}
        </p>
      )}
      {candidates && !candidates.length && (
        <p className="medication-hint">
          일치하는 제품을 찾지 못했어요. 제품명을 확인해 주세요.
        </p>
      )}
      {candidates && candidates.length > 0 && (
        <ul className="medication-product-candidates">
          {candidates.map(function candidate(item) {
            // 사용자가 후보를 고른 경우에만 해당 품목을 연결한다.
            async function choose() {
              if (pendingRef.current) return;
              pendingRef.current = true;
              setPending(true);
              setError('');
              try {
                await onSelect(item);
                setCandidates(null);
              } catch {
                setError(
                  '제품을 연결하지 못했어요. 목록을 새로고침한 뒤 다시 시도해 주세요.',
                );
              } finally {
                pendingRef.current = false;
                setPending(false);
              }
            }
            return (
              <li key={item.productCode}>
                <button type="button" onClick={choose} disabled={pending}>
                  <strong>{item.productName}</strong>
                  <span>
                    {item.manufacturer || '제조사 확인 필요'} · 품목코드{' '}
                    {item.productCode}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// 품목 상세를 기존 제품 사용정보의 표시 계약으로 변환한다.
function asMedicine(product: ProductDetail): Medicine {
  return {
    medicine_id: product.productCode,
    product_code: product.productCode,
    name: product.productName,
    form: 'UNKNOWN',
    ingredients: product.ingredients
      .map(function ingredient(item) {
        return item.name;
      })
      .filter(Boolean),
    indications: product.indications ? [product.indications] : [],
    product_info_status: 'SUCCEEDED',
    product_info: {
      source: 'MFDS_PERMIT_DETAIL',
      indications: product.indications || null,
      dosage: product.dosage || null,
      precautions: product.precautions || null,
    },
    care_assessment: null,
  };
}

// 저장된 약의 연결 상태와 식약처 제품 상세를 표시한다.
export function MedicationProductDialog({
  planId,
  medicine,
  products,
  onClose,
  stale,
}: {
  planId: string;
  medicine: MedicationItem;
  products: MedicationProducts;
  onClose: () => void;
  stale: boolean;
}) {
  const [product, setProduct] = useState<ProductDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  // 서버의 최신 연결만 확인하며 변경된 약에는 기존 연결을 적용하지 않는다.
  useEffect(
    function loadProduct() {
      if (stale) return;
      let active = true;
      async function load() {
        try {
          if (!products.isCurrent(planId, medicine.id, medicine))
            throw new Error(
              '복용약이 변경됐어요. 최신 목록을 다시 열어주세요.',
            );
          const links = await products.links();
          const linked = links.find(function same(item) {
            return item.planId === planId && item.medicineId === medicine.id;
          });
          const detail = linked
            ? await products.detail(linked.productCode)
            : null;
          if (!products.isCurrent(planId, medicine.id, medicine))
            throw new Error(
              '복용약이 변경됐어요. 최신 목록을 다시 열어주세요.',
            );
          if (active) setProduct(detail);
        } catch {
          if (active) setError('연결된 제품 정보를 불러오지 못했어요.');
        } finally {
          if (active) setLoading(false);
        }
      }
      void load();
      return function cancel() {
        active = false;
      };
    },
    [products, planId, medicine, stale],
  );
  // 선택한 품목을 서버에 연결한 뒤 상세 정보를 다시 읽는다.
  async function connect(candidate: ProductCandidate) {
    await products.link(planId, medicine.id, candidate.productCode, medicine);
    try {
      setProduct(await products.detail(candidate.productCode));
      setError('');
    } catch {
      setError(
        '제품은 연결됐지만 상세 정보를 불러오지 못했어요. 다시 열어 확인해 주세요.',
      );
    }
  }
  return (
    <Dialog
      title="제품 사용정보"
      onClose={onClose}
      className="product-info-dialog"
    >
      {stale ? (
        <p className="medication-form-error" role="alert">
          다른 기기에서 이 약이 변경됐어요. 최신 목록에서 다시 열어주세요.
        </p>
      ) : (
        <>
          <p className="medication-hint">
            처방받은 복용법과 아래 제품 허가정보는 다를 수 있어요. 개인 복용법은
            처방 내용을 확인해 주세요.
          </p>
          {loading && <p role="status">연결된 제품을 확인하고 있어요…</p>}
          {error && (
            <p className="medication-form-error" role="alert">
              {error}
            </p>
          )}
          {!loading && !product && !error && (
            <p>연결된 제품이 없어요. 아래에서 정확한 제품을 선택해 주세요.</p>
          )}
          {product && (
            <>
              <p className="medication-hint">
                출처: 식품의약품안전처 · 확인:{' '}
                {new Date(product.checkedAt).toLocaleString('ko-KR')}
              </p>
              <MedicineUsage medicine={asMedicine(product)} />
            </>
          )}
          <details className="medication-product-lookup">
            <summary>
              {product ? '다른 제품으로 변경' : '식약처 제품 찾기'}
            </summary>
            <MedicationProductSearch
              name={medicine.name}
              products={products}
              selected={null}
              onSelect={connect}
            />
          </details>
        </>
      )}
    </Dialog>
  );
}
