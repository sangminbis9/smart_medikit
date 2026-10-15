import { useState, type FormEvent } from 'react';
import { Link, useParams } from 'react-router-dom';
import { expiryLabel, hasStock, stockLabel, type Package } from './data';
import { stateLabels } from './Home';
import type { Medkit } from './useMedkit';
import { Badge, Dialog, EmptyState, Icon, Note } from './ui';

// 직접 링크로 열린 포장도 현재 목록에서 안전하게 조회한다.
export default function PackageDetail({ kit }: { kit: Medkit }) {
  const { id } = useParams();
  // 포장 ID가 같은 객체만 상세 화면에 연결한다.
  const item = kit.data.packages.find(function matches(item) {
    return item.package_id === id;
  });
  const [editing, setEditing] = useState(false);
  const [notice, setNotice] = useState('');
  // 편집창을 연다.
  function edit() {
    setEditing(true);
  }
  // 편집창을 닫는다.
  function close() {
    setEditing(false);
  }
  // 실제 기록 대신 시연 포장 하나만 교체한다.
  function save(next: Package, reason: string) {
    if (!kit.demo) return;
    // 수량 수정은 다른 포장이나 복약 기록에 임의로 적용하지 않는다.
    const packages = kit.data.packages.map(function replace(current) {
      return current.package_id === next.package_id ? next : current;
    });
    kit.changeDemo({ ...kit.data, packages });
    setNotice(
      `시연 정보만 변경했어요. 사유: ${reason} · 서버 저장과 연관 기록 정정은 수행되지 않아요.`,
    );
    close();
  }
  return (
    <div className="standard-page detail-page">
      <div className="detail-heading">
        <Link className="icon-button" to="/" aria-label="홈으로 돌아가기">
          <Icon name="back" />
        </Link>
        <h1>약품 상세</h1>
      </div>
      <p className="section-gap muted">포장에 연결된 정보를 확인해요.</p>
      {!item ? (
        <EmptyState
          title={
            kit.status === 'loading'
              ? '약품 정보를 불러오고 있어요'
              : '이 포장을 찾을 수 없어요'
          }
          action={
            <Link className="secondary-button" to="/">
              보관 목록으로
            </Link>
          }
        >
          아직 데이터가 연결되지 않았거나 현재 목록에 없는 포장이에요.
        </EmptyState>
      ) : (
        <div className="detail-content">
          <div className="package-identity">
            <span>
              <Icon name="pill" />
            </span>
            <div>
              <h2>
                {item.kind === 'PRESCRIPTION' ? '처방 받은 약봉투' : item.name}
              </h2>
              <p>
                {item.kind === 'PRESCRIPTION' ? '처방의약품' : '일반의약품'} /{' '}
                {stateLabels[item.state]}
              </p>
            </div>
          </div>
          {stockLabel(item) && (
            <div className="quantity-panel">
              <div className="flex justify-between gap-3">
                <h2>
                  {item.kind === 'PRESCRIPTION'
                    ? '봉투 단위 예상 잔여 회분'
                    : '예상 잔여량'}
                </h2>
                <Badge tone="amber">추정</Badge>
              </div>
              <strong>{stockLabel(item)}</strong>
              <p>
                {item.kind === 'PRESCRIPTION'
                  ? '봉투 전체의 예상 회분이며 개별 알약 수가 아니에요.'
                  : item.inventory?.initial_qty == null
                    ? '초기 수량은 미확인 상태예요.'
                    : `초기 ${item.inventory.initial_qty} · 포장 기준 추정값`}
              </p>
            </div>
          )}
          <dl className="detail-fields">
            <div>
              <dt>
                {item.expiry.verified && item.expiry.actual_expiry
                  ? '유효기간 · 확인된 실제 만료일'
                  : '유효기간 확인 상태'}
              </dt>
              <dd>{expiryLabel(item)}</dd>
            </div>
            <div>
              <dt>보관 위치 · 추정 배치</dt>
              <dd>
                {item.position
                  ? `X ${item.position.x} / Y ${item.position.y} / 추정 Z ${item.position.z_estimated} mm`
                  : '위치 미확인'}
              </dd>
            </div>
            <div>
              <dt>성분 정보</dt>
              <dd>{ingredients(item) || '성분 미확인'}</dd>
            </div>
            <div>
              <dt>안전정보 조회</dt>
              <dd>
                {item.safety_status === 'SAFETY_CHECK_PENDING'
                  ? '안전정보 확인 대기'
                  : '서버 조회 완료 · 안전성 보증 아님'}
              </dd>
            </div>
          </dl>
          <Note>
            {hasStock(item)
              ? '잔여량은 사용 기록에 따른 예상값이에요. 실제 남은 수량과 다르면 수정해주세요.'
              : item.kind === 'PRESCRIPTION'
                ? '약봉투 반출만으로 실제 복용을 확정하지 않아요.'
                : '이 제형은 잔여량을 표시하거나 차감하지 않아요. 포장 상태와 유효기간을 확인해주세요.'}
          </Note>
          <button className="secondary-button" onClick={edit}>
            수량 및 정보 수정
          </button>
          {item.kind === 'PRESCRIPTION' && (
            <Note tone="neutral">
              처방 정보가 부족할 때 기존 포장에 보완 사진을 연결할 수 있도록
              준비 중이에요. 사진 추가 기능은 추후 이곳에서 제공해요.
            </Note>
          )}
          {notice && (
            <p role="status" className="inline-status">
              {notice}
            </p>
          )}
          {editing && (
            <Dialog title="포장 정보 수정" onClose={close}>
              <EditPackage item={item} demo={kit.demo} onSave={save} />
            </Dialog>
          )}
        </div>
      )}
    </div>
  );
}

// 여러 의약품이 연결된 약봉투의 성분을 중복 없이 표시한다.
function ingredients(item: Package) {
  const names = new Set<string>();
  for (const medicine of item.medicines)
    for (const ingredient of medicine.ingredients) names.add(ingredient);
  return [...names].join(', ');
}

// 미확인 값을 보존하고 지원 제형에만 수량 입력을 제공한다.
function EditPackage({
  item,
  demo,
  onSave,
}: {
  item: Package;
  demo: boolean;
  onSave: (next: Package, reason: string) => void;
}) {
  const [error, setError] = useState('');
  const countable = hasStock(item) || item.kind === 'PRESCRIPTION';
  // 날짜와 수량을 검사한 뒤 사용자가 검토한 값만 전달한다.
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!demo) return;
    const values = new FormData(event.currentTarget);
    const amount = String(values.get('amount') || '').trim();
    const qty = amount === '' ? null : Number(amount);
    const expiry = String(values.get('expiry') || '');
    const reason = String(values.get('reason') || '').trim();
    const memo = String(values.get('memo') || '').trim();
    if (
      countable &&
      qty !== null &&
      (!Number.isSafeInteger(qty) || qty < 0 || qty > 100000)
    ) {
      setError('수량은 0 이상 100,000 이하 정수로 입력해주세요.');
      return;
    }
    if (
      expiry &&
      (!/^\d{4}-\d{2}-\d{2}$/.test(expiry) ||
        !Number.isFinite(Date.parse(expiry)) ||
        new Date(expiry).toISOString().slice(0, 10) !== expiry)
    ) {
      setError('실제 포장에 표시된 날짜를 확인해주세요.');
      return;
    }
    if (!reason) {
      setError('수정 사유를 입력해주세요.');
      return;
    }
    onSave(
      {
        ...item,
        inventory:
          hasStock(item) && item.inventory
            ? { ...item.inventory, estimated_qty: qty }
            : item.inventory,
        remaining_doses:
          item.kind === 'PRESCRIPTION' ? qty : item.remaining_doses,
        expiry: {
          ...item.expiry,
          actual_expiry: expiry || null,
          verified: Boolean(expiry),
        },
        note: memo || null,
      },
      reason,
    );
  }
  return (
    <form className="edit-form" onSubmit={submit}>
      <Note>
        시연 모드에서만 수정할 수 있어요. 실제 수량·복약·입출고 이력 정정은 AWS
        연동 후 서버에서 함께 처리합니다.
      </Note>
      {countable && (
        <label>
          {item.kind === 'PRESCRIPTION' ? '예상 잔여 회분' : '예상 잔여 수량'}
          <input
            type="number"
            name="amount"
            min="0"
            max="100000"
            step="1"
            inputMode="numeric"
            defaultValue={
              (item.kind === 'PRESCRIPTION'
                ? item.remaining_doses
                : item.inventory?.estimated_qty) ?? ''
            }
            placeholder="미확인"
            disabled={!demo}
          />
        </label>
      )}
      <label>
        포장에 표시된 실제 만료일
        <input
          type="date"
          name="expiry"
          defaultValue={
            item.expiry.verified ? item.expiry.actual_expiry || '' : ''
          }
          disabled={!demo}
        />
        <small>
          비워 두면 미확인입니다. 임시 관리 기준일을 입력하지 마세요.
        </small>
      </label>
      <label>
        메모
        <textarea
          name="memo"
          maxLength={200}
          defaultValue={item.note || ''}
          disabled={!demo}
        />
      </label>
      <label>
        수정 사유
        <input
          name="reason"
          maxLength={120}
          required
          disabled={!demo}
          placeholder="예: 실제 포장 수량 확인"
        />
      </label>
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      <button className="primary-button" disabled={!demo}>
        시연 정보에 반영
      </button>
    </form>
  );
}
