import { useEffect, useState } from 'react';
import type { Medicine } from './data';
import type { MedicationItem } from './medicationPlans';
import type {
  MedicationProducts,
  ProductDetail,
  ProductSuggestion,
} from './medicationProducts';
import MedicineUsage from './MedicineUsage';
import { Dialog } from './ui';

// 식약처 제품을 기존 사용정보의 표시 형식으로 변환한다.
function asMedicine(product: ProductDetail): Medicine {
  return {
    medicine_id: product.productCode,
    product_code: product.productCode,
    name: product.productName,
    form: 'UNKNOWN',
    ingredients: product.ingredients
      .map(function ingredient(item) {
        return item.name.trim();
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

// 자동으로 식별한 제품과 확인이 필요한 상태를 구분해 표시한다.
export function MedicationProductDialog({
  planId,
  medicine,
  products,
  onClose,
  stale,
  matchState,
  suggestion,
  onRetry,
}: {
  planId: string;
  medicine: MedicationItem;
  products: MedicationProducts;
  onClose: () => void;
  stale: boolean;
  matchState: 'checking' | 'matched' | 'possible' | 'review' | 'unavailable';
  suggestion: ProductSuggestion | null;
  onRetry: () => void;
}) {
  const [product, setProduct] = useState<ProductDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const hasSuggestionIngredients = suggestion?.ingredients.some(
    // 양쪽 자료에서 확인된 이름의 유무를 검사한다.
    function hasName(item) {
      return item.name.trim().length > 0;
    },
  );

  // 연결 성공 후 제품 상세를 조회하며 열린 창의 약이 바뀌었는지도 확인한다.
  useEffect(
    function loadProduct() {
      if (stale || matchState !== 'matched') return;
      let active = true;
      async function load() {
        try {
          if (!products.isCurrent(planId, medicine.id, medicine))
            throw new Error('MEDICINE_CHANGED');
          const links = await products.links();
          const linked = links.find(function same(item) {
            return item.planId === planId && item.medicineId === medicine.id;
          });
          if (!linked) throw new Error('LINK_NOT_FOUND');
          const detail = await products.detail(linked.productCode);
          if (!products.isCurrent(planId, medicine.id, medicine))
            throw new Error('MEDICINE_CHANGED');
          if (active) {
            setProduct(detail);
            setError('');
          }
        } catch {
          if (active)
            setError(
              '연결된 제품 정보를 불러오지 못했어요. 다시 조회해 주세요.',
            );
        } finally {
          if (active) setLoading(false);
        }
      }
      void load();
      return function cancel() {
        active = false;
      };
    },
    [products, planId, medicine, stale, matchState],
  );

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
          {matchState === 'checking' && (
            <p role="status">입력한 약의 제품 정보를 확인하고 있어요…</p>
          )}
          {matchState === 'matched' && loading && (
            <p role="status">연결된 제품을 불러오고 있어요…</p>
          )}
          {matchState === 'review' && (
            <p role="alert">
              정확한 품목을 확인하지 못했어요. 약 이름과 함량·제형을 확인해
              주세요.
            </p>
          )}
          {matchState === 'unavailable' && (
            <p role="alert">
              식약처 제품 정보를 불러오지 못했어요. 잠시 후 다시 조회해 주세요.
            </p>
          )}
          {matchState === 'possible' && suggestion && (
            <>
              <div className="medication-product-warning" role="alert">
                <strong>비슷한 식약처 제품 · 실제 처방약과 일치 미확인</strong>
                <p>
                  입력한 약과 유사한 제품의 정보예요. 제품명·제조사·함량을
                  처방전이나 포장과 비교해 주세요.
                </p>
              </div>
              <p className="medication-hint">
                제조사: {suggestion.manufacturer || '확인 필요'} · 출처:
                식품의약품안전처 · 확인:{' '}
                {new Date(suggestion.checkedAt).toLocaleString('ko-KR')}
              </p>
              <MedicineUsage
                medicine={asMedicine(suggestion)}
                ingredientStatements={suggestion.ingredientStatements}
                ingredientsNotice={
                  suggestion.ingredientsStatus === 'CONFLICT'
                    ? suggestion.ingredientStatements.length > 0
                      ? '별도 주성분 자료의 수치는 달라 확인 필요'
                      : hasSuggestionIngredients
                        ? '함량 자료 확인 필요'
                        : '자료 충돌로 확인 필요'
                    : suggestion.ingredientsStatus === 'NO_DATA'
                      ? '성분 정보 확인 필요'
                      : undefined
                }
              />
            </>
          )}
          {matchState === 'matched' && error && (
            <p className="medication-form-error" role="alert">
              {error}
            </p>
          )}
          {matchState === 'matched' && product && !error && (
            <>
              <p className="medication-hint">
                입력한 약명으로 자동 연결된 제품입니다. 제품명·제조사·함량을
                처방전과 확인해 주세요.
              </p>
              <p className="medication-hint">
                제조사: {product.manufacturer || '확인 필요'} · 출처:
                식품의약품안전처 · 확인:{' '}
                {new Date(product.checkedAt).toLocaleString('ko-KR')}
              </p>
              <MedicineUsage medicine={asMedicine(product)} />
            </>
          )}
          {(matchState === 'review' ||
            matchState === 'unavailable' ||
            error) && (
            <button
              type="button"
              className="secondary-button"
              onClick={onRetry}
            >
              다시 조회
            </button>
          )}
        </>
      )}
    </Dialog>
  );
}
