import { useState, type FormEvent } from 'react';
import { z } from 'zod';
import { displayMedicineName, type Medicine } from './data';

export const captureReviewSchema = z.object({
  eventId: z.string().regex(/^[A-Za-z0-9_-]{1,100}$/),
  occurredAt: z.iso.datetime({ offset: true }),
  reason: z.string(),
});
type Review = z.infer<typeof captureReviewSchema>;
const reasons: Record<string, string> = {
  STAGE_TIMEOUT: '촬영 단계 전송을 완료하지 못했어요.',
  OCR_TIMEOUT: '약품 인식이 지연되거나 완료되지 않았어요.',
  RECOGNITION_UNCERTAIN: '약품을 정확히 구분하지 못했어요.',
  PRODUCT_UNRESOLVED: '연결할 제품을 확인해 주세요.',
  MOVEMENT_UNCERTAIN: '사진만으로 반입·반출을 구분하지 못했어요.',
  PACKAGE_IMAGE_MISSING: '약품 포장 사진이 없어요.',
  STALE_OR_CONFLICTING_MOVEMENT: '이후 촬영 기록과 상태가 달라요.',
};

// 자동 처리하지 못한 촬영만 사용자가 제품과 실제 동작을 함께 정정한다.
export default function CaptureReviews({
  reviews,
  medicines,
  baseUrl,
  onSaved,
}: {
  reviews: Review[];
  medicines: Medicine[];
  baseUrl: string;
  onSaved: () => Promise<void>;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  // 서버 확인 전에는 대기 항목과 입력값을 지우지 않는다.
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    const data = new FormData(event.currentTarget);
    const id = String(data.get('eventId'));
    const direction = String(data.get('direction'));
    const productCode =
      direction === 'NONE' ? null : String(data.get('productCode') || '');
    if (direction !== 'NONE' && !productCode) {
      setError('제품을 선택해 주세요.');
      return;
    }
    setSaving(true);
    setError('');
    try {
      const response = await fetch(
        `${baseUrl}/activity/${encodeURIComponent(id)}/confirm`,
        {
          method: 'POST',
          cache: 'no-store',
          signal: AbortSignal.timeout(12000),
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ review: { direction, productCode } }),
        },
      );
      if (!response.ok) throw new Error('REVIEW_FAILED');
      const result = await response.json();
      if (result.eventId !== id || result.processing_status !== 'COMPLETED')
        throw new Error('INVALID_RESPONSE');
      await onSaved();
    } catch {
      setError(
        '저장 결과를 확인하지 못했어요. 새로고침 후 다시 확인해주세요. 이후 기록과 충돌하면 변경 없음으로 닫고 다시 촬영해주세요.',
      );
    } finally {
      setSaving(false);
    }
  }
  if (!reviews.length) return null;
  return (
    <div className="capture-reviews">
      <h3>
        촬영 확인 대기 <span>{reviews.length}</span>
      </h3>
      <p>
        확인이 필요한 촬영만 표시돼요. 목록에 제품이 없으면 포장을 다시
        촬영해주세요.
      </p>
      {reviews.map(
        // 촬영마다 제품·동작을 독립적으로 확인할 입력 폼을 만든다.
        function renderReview(review) {
          return (
            <form
              className="outbound-card grid gap-3"
              key={review.eventId}
              onSubmit={submit}
            >
              <input type="hidden" name="eventId" value={review.eventId} />
              <strong>
                {reasons[review.reason] || '촬영 결과를 확인해 주세요.'}
              </strong>
              <time dateTime={review.occurredAt}>
                {new Date(review.occurredAt).toLocaleString('ko-KR', {
                  timeZone: 'Asia/Seoul',
                })}
              </time>
              <label>
                촬영한 약품{' '}
                <select name="productCode" disabled={saving} defaultValue="">
                  <option value="">제품 선택</option>
                  {medicines.map(
                    // 서버에 등록된 제품만 정정 후보로 표시한다.
                    function product(medicine) {
                      return (
                        <option
                          key={medicine.medicine_id}
                          value={medicine.product_code || medicine.medicine_id}
                        >
                          {displayMedicineName(medicine.name)}
                        </option>
                      );
                    },
                  )}
                </select>
              </label>
              <label>
                실제 동작{' '}
                <select name="direction" disabled={saving} defaultValue="NONE">
                  <option value="NONE">변경 없음</option>
                  <option value="IN">반입</option>
                  <option value="OUT">반출</option>
                </select>
              </label>
              <button className="secondary-button" disabled={saving}>
                {saving ? '저장 중…' : '촬영 결과 확인'}
              </button>
            </form>
          );
        },
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
