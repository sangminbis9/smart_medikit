import { useRef, useState } from 'react';
import {
  createMedicationPlan,
  type MedicationPlan,
  type MedicationTiming,
} from './medicationPlans';
import { MedicationPlanForm } from './MedicationPlanForm';
import { Badge, Dialog, Icon } from './ui';
import './medicationPlans.css';

const weekdays = ['일', '월', '화', '수', '목', '금', '토'];

// 사용자가 입력한 복용 방식과 일정만 읽기 쉬운 문장으로 표시한다.
export function medicationTimingLabel(timing: MedicationTiming): string {
  if (timing.mode === 'as_needed') return '필요할 때';
  if (!timing.schedule) return '정기 복용 · 일정 미등록';
  const dayLabels: string[] = [];
  for (const day of [1, 2, 3, 4, 5, 6, 0]) {
    if (timing.schedule.weekdays.includes(day)) dayLabels.push(weekdays[day]);
  }
  const days = dayLabels.length === 7 ? '매일' : dayLabels.join('·');
  return `${days} · ${[...timing.schedule.times].sort().join(', ')}`;
}

// 복용 중인 처방 묶음과 종료한 묶음을 재고와 구분해 관리한다.
export default function MedicationPlans({
  plans,
  onSave,
  onDelete,
  onEnd,
  storageLabel,
  busy = false,
  error = '',
}: {
  plans: MedicationPlan[];
  onSave: (plan: MedicationPlan, original?: MedicationPlan) => Promise<boolean>;
  onDelete: (id: string) => Promise<boolean>;
  onEnd: (id: string) => Promise<boolean>;
  storageLabel: string;
  busy?: boolean;
  error?: string;
}) {
  const [editing, setEditing] = useState<MedicationPlan | null>(null);
  const [deleting, setDeleting] = useState<MedicationPlan | null>(null);
  const [actionError, setActionError] = useState('');
  const [pending, setPending] = useState(false);
  const pendingRef = useRef(false);
  const disabled = busy || pending;
  const active: MedicationPlan[] = [];
  const ended: MedicationPlan[] = [];
  for (const plan of plans) {
    if (plan.endedOn) ended.push(plan);
    else active.push(plan);
  }

  // 새 처방 묶음 등록창을 연다.
  function addPlan() {
    if (disabled || pendingRef.current) return;
    setActionError('');
    setEditing(createMedicationPlan());
  }
  // 입력 중인 등록창을 닫는다.
  function closeEditor() {
    setEditing(null);
  }
  // 삭제 확인창을 닫고 이전 오류를 지운다.
  function closeDelete() {
    setDeleting(null);
    setActionError('');
  }
  // 선택한 묶음과 연결된 확인 기록의 삭제를 요청한다.
  async function confirmDelete() {
    if (!deleting || disabled || pendingRef.current) return;
    pendingRef.current = true;
    setPending(true);
    setActionError('');
    try {
      if (await onDelete(deleting.id)) closeDelete();
      else setActionError('삭제하지 못했어요. 다시 시도해 주세요.');
    } catch {
      setActionError(
        '삭제 응답을 확인하지 못했어요. 연결 상태를 확인해 주세요.',
      );
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  }
  // 묶음별 정보와 편집 동작을 카드로 표시한다.
  function planCard(plan: MedicationPlan) {
    let hasDifferentTiming = false;
    for (const medicine of plan.medicines) {
      if (medicine.timing) hasDifferentTiming = true;
    }
    // 저장된 복사본으로 편집창을 연다.
    function editPlan() {
      if (disabled || pendingRef.current) return;
      setActionError('');
      setEditing(plan);
    }
    // 종료 또는 재개를 요청하고 저장 실패를 알린다.
    async function toggleEnd() {
      if (disabled || pendingRef.current) return;
      pendingRef.current = true;
      setPending(true);
      setActionError('');
      try {
        if (!(await onEnd(plan.id)))
          setActionError('변경사항을 저장하지 못했어요. 다시 시도해 주세요.');
      } catch {
        setActionError(
          '저장 응답을 확인하지 못했어요. 연결 상태를 확인해 주세요.',
        );
      } finally {
        pendingRef.current = false;
        setPending(false);
      }
    }
    // 삭제할 대상과 영향을 확인할 창을 연다.
    function askDelete() {
      if (disabled || pendingRef.current) return;
      setActionError('');
      setDeleting(plan);
    }
    return (
      <article
        className="medication-plan-card"
        key={plan.id}
        aria-label={plan.title}
      >
        <div className="medication-plan-top">
          <span className="medication-plan-icon">
            <Icon name="pill" />
          </span>
          <Badge tone={plan.endedOn ? 'neutral' : 'green'}>
            {plan.endedOn ? '복용 종료' : '복용 중'}
          </Badge>
        </div>
        <h3>{plan.title}</h3>
        <p className="medication-plan-timing">
          {medicationTimingLabel(plan.timing)}
        </p>
        {hasDifferentTiming && (
          <p className="medication-plan-dates">
            약별로 다른 복용법은 상세보기에서 확인하세요.
          </p>
        )}
        {plan.timing.schedule && (
          <p className="medication-plan-dates">
            {plan.timing.schedule.startDate} ~{' '}
            {plan.timing.schedule.endDate || '종료일 미정'}
          </p>
        )}
        {plan.endedOn && (
          <p className="medication-plan-dates">종료 처리일 {plan.endedOn}</p>
        )}
        <details className="medication-plan-details">
          <summary>
            <span>
              {plan.medicines.length
                ? `약 정보 ${plan.medicines.length}개`
                : '개별 약 정보 추가 가능'}
            </span>
            <span>상세보기</span>
          </summary>
          {plan.medicines.length ? (
            <ul className="medication-item-list">
              {plan.medicines.map(
                // 각 약의 식별 정보와 사용자가 입력한 복용량을 표시한다.
                function item(medicine) {
                  const timing = medicine.timing ?? plan.timing;
                  return (
                    <li key={medicine.id}>
                      <strong>{medicine.name}</strong>
                      {medicine.strength && <span>{medicine.strength}</span>}
                      {medicine.dose && (
                        <span>1회 복용량: {medicine.dose}</span>
                      )}
                      <small>
                        {medicine.timing ? '개별 일정 · ' : '공통 일정 · '}
                        {medicationTimingLabel(timing)}
                      </small>
                      {medicine.timing?.schedule && (
                        <small>
                          {medicine.timing.schedule.startDate} ~{' '}
                          {medicine.timing.schedule.endDate || '종료일 미정'}
                        </small>
                      )}
                    </li>
                  );
                },
              )}
            </ul>
          ) : (
            <p className="medication-hint">
              묶음으로 등록했어요. 약 이름을 알게 되면 수정에서 추가해 주세요.
            </p>
          )}
          {plan.notes && (
            <div className="medication-plan-notes">
              <strong>복용 안내·메모</strong>
              <p>{plan.notes}</p>
            </div>
          )}
        </details>
        <div className="medication-card-actions">
          <button
            type="button"
            className="text-button"
            onClick={editPlan}
            disabled={disabled}
          >
            수정
          </button>
          <button
            type="button"
            className="text-button"
            onClick={toggleEnd}
            disabled={disabled}
          >
            {plan.endedOn ? '다시 복용 중으로' : '복용 종료'}
          </button>
          <button
            type="button"
            className="text-button medication-delete"
            onClick={askDelete}
            disabled={disabled}
          >
            삭제
          </button>
        </div>
      </article>
    );
  }

  return (
    <section
      className="medication-plans"
      aria-labelledby="medication-plans-title"
      aria-busy={disabled}
    >
      <div className="medication-plans-heading">
        <div>
          <div className="medication-eyebrow">내가 직접 등록하는 복용약</div>
          <h2 id="medication-plans-title">
            복용 중인 약 <span>{active.length}</span>
          </h2>
          <p>처방받은 여러 약을 하나의 묶음으로 관리하세요.</p>
        </div>
        <button
          type="button"
          className="primary-button"
          onClick={addPlan}
          disabled={disabled}
        >
          <Icon name="plus" />
          복용약 등록
        </button>
      </div>
      <p className="medication-storage-label">{storageLabel}</p>
      {disabled && !editing && (
        <p className="medication-hint" role="status">
          변경사항을 저장하고 있어요…
        </p>
      )}
      {active.length ? (
        <div className="medication-plan-grid">{active.map(planCard)}</div>
      ) : (
        <div className="medication-plans-empty">
          <span className="medication-plan-icon">
            <Icon name="pill" />
          </span>
          <div>
            <strong>복용 중인 처방약을 등록해 보세요</strong>
            <p>
              ‘감기 처방약’처럼 이름을 붙이고, 함께 먹는 약과 일정을 추가할 수
              있어요.
            </p>
          </div>
        </div>
      )}
      {ended.length > 0 && (
        <details className="medication-ended">
          <summary>복용 종료한 묶음 {ended.length}개</summary>
          <div className="medication-plan-grid">{ended.map(planCard)}</div>
        </details>
      )}
      {(actionError || error) && !deleting && !editing && (
        <p className="medication-form-error" role="alert">
          {error || actionError}
        </p>
      )}
      {editing && (
        <MedicationPlanForm
          initial={editing}
          onSave={onSave}
          onClose={closeEditor}
          storageLabel={storageLabel}
          saveError={error}
        />
      )}
      {deleting && (
        <Dialog
          title="복용약 삭제"
          onClose={closeDelete}
          closeDisabled={disabled}
        >
          <p className="medication-delete-description">
            <strong>{deleting.title}</strong> 묶음과 이 묶음의 복용 확인 기록을
            삭제할까요?
          </p>
          {(actionError || error) && (
            <p className="medication-form-error" role="alert">
              {error || actionError}
            </p>
          )}
          <div className="medication-form-actions">
            <button
              type="button"
              className="secondary-button"
              onClick={closeDelete}
              disabled={disabled}
            >
              취소
            </button>
            <button
              type="button"
              className="primary-button"
              onClick={confirmDelete}
              disabled={disabled}
            >
              {pending ? '삭제 중…' : '삭제하기'}
            </button>
          </div>
        </Dialog>
      )}
    </section>
  );
}
