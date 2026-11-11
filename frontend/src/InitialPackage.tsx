import { useState, type FormEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { Medkit } from './useMedkit';
import { Dialog } from './ui';
import MedicineUsage from './MedicineUsage';

// 알림과 신규 카드가 같은 제품 사용정보 및 최초 입력 화면을 연다.
export default function InitialPackage({ kit }: { kit: Medkit }) {
  const [params, setParams] = useSearchParams();
  const id = params.get('new_package');
  const item = kit.data.packages.find(function match(entry) {
    return entry.package_id === id;
  });
  const [condition, setCondition] = useState('NEW');
  const [expiry, setExpiry] = useState('');
  const [count, setCount] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  // 제품 팝업만 닫고 나머지 검색 조건은 유지한다.
  function close() {
    if (!saving) {
      const next = new URLSearchParams(params);
      next.delete('new_package');
      setParams(next);
    }
  }
  // 서버 저장 성공 뒤에만 최초 등록 표시와 최신 재고를 갱신한다.
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!item || saving || kit.demo) return;
    setSaving(true);
    setError('');
    try {
      const base = import.meta.env.VITE_ACTIVITY_API_URL;
      if (!base || new URL(base).protocol !== 'https:')
        throw new Error('NOT_CONFIGURED');
      const response = await fetch(
        `${base.replace(/\/$/, '')}/packages/${encodeURIComponent(item.package_id)}/initial`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          cache: 'no-store',
          signal: AbortSignal.timeout(12000),
          body: JSON.stringify({
            condition,
            actualExpiry: condition === 'USED' && expiry ? expiry : null,
            remainingQty:
              (condition === 'USED' || item.inventory?.estimated_qty == null) &&
              item.inventory?.stock_enabled &&
              count !== ''
                ? Number(count)
                : null,
          }),
        },
      );
      if (!response.ok) throw new Error('SAVE_FAILED');
      await kit.refresh();
    } catch {
      setError('저장 결과를 확인하지 못했어요. 다시 불러온 뒤 확인해주세요.');
    } finally {
      setSaving(false);
    }
  }
  if (!item) return null;
  return (
    <Dialog
      title="제품 사용정보"
      className="product-info-dialog"
      onClose={close}
    >
      {(item.first_registration_pending ||
        (item.inventory?.stock_enabled &&
          item.inventory.estimated_qty == null)) && (
        <form onSubmit={save} className="grid gap-3 mb-5">
          <strong>
            {item.first_registration_pending
              ? '처음 등록된 약입니다'
              : '보관 수량 입력'}
          </strong>
          <label>
            약품 상태{' '}
            <select
              value={condition}
              disabled={saving}
              onChange={function change(event) {
                setCondition(event.target.value);
              }}
            >
              <option value="NEW">새것</option>
              <option value="USED">쓰던 약</option>
            </select>
          </label>
          {condition === 'USED' && (
            <>
              <label>
                유통기한{' '}
                <input
                  type="date"
                  value={expiry}
                  disabled={saving}
                  onChange={function change(event) {
                    setExpiry(event.target.value);
                  }}
                />
              </label>
            </>
          )}
          {item.inventory?.stock_enabled &&
            (condition === 'USED' || item.inventory.estimated_qty == null) && (
              <label>
                {condition === 'NEW'
                  ? '포장에 표시된 전체 수량 (선택)'
                  : '잔여 수량'}{' '}
                <input
                  type="number"
                  min="0"
                  max="1000000"
                  step="1"
                  value={count}
                  disabled={saving}
                  onChange={
                    // 수량 보완 입력을 서버 저장 전까지 폼에 유지한다.
                    function changeCount(event) {
                      setCount(event.target.value);
                    }
                  }
                />{' '}
                알
                {condition === 'NEW' && (
                  <small>
                    수량을 입력하면 복용 확인 시 남은 수량을 계산해요. 나중에
                    입력할 수도 있어요.
                  </small>
                )}
              </label>
            )}
          {error && <p role="alert">{error}</p>}
          <button className="primary-button" disabled={saving || kit.demo}>
            {saving ? '저장 중…' : '기본 정보 확인'}
          </button>
        </form>
      )}
      {item.medicines.map(function usage(medicine) {
        return <MedicineUsage key={medicine.medicine_id} medicine={medicine} />;
      })}
    </Dialog>
  );
}
