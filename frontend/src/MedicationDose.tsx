import { useRef, useState, type FormEvent } from 'react';
import type { MedicationOccurrence, MedicationRecord } from './medicationPlans';
import { Badge, Dialog } from './ui';

const resultLabels: Record<MedicationRecord['result'], string> = {
  all: '모두 복용 확인',
  some: '일부 복용 확인',
  none: '미복용',
  deferred: '확인 보류',
};

// 수동 등록된 일정과 사용자가 직접 남긴 복용 확인을 표시한다.
export default function MedicationDose({
  occurrence,
  onRecord,
}: {
  occurrence: MedicationOccurrence;
  onRecord: (
    occurrence: MedicationOccurrence,
    result: MedicationRecord['result'],
    ids: string[],
  ) => Promise<boolean>;
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<MedicationOccurrence | null>(null);
  const active = editing ?? occurrence;
  const [result, setResult] = useState<MedicationRecord['result']>('all');
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const pendingRef = useRef(false);
  // 저장된 확인 내용을 폼에 가져오며 열기만으로 복용 기록을 만들지 않는다.
  function edit() {
    setEditing(structuredClone(occurrence));
    setResult(occurrence.record?.result || 'all');
    setSelected(occurrence.record?.takenMedicineIds || []);
    setError('');
    setOpen(true);
  }
  // 저장하지 않고 확인창을 닫는다.
  function close() {
    if (!pendingRef.current) setOpen(false);
  }
  // 실제로 복용한 약 선택을 검사한 뒤 한 회차만 저장한다.
  async function save(event: FormEvent) {
    event.preventDefault();
    if (pendingRef.current) return;
    if (
      result === 'some' &&
      active.medicines.length > 0 &&
      (selected.length === 0 || selected.length === active.medicines.length)
    ) {
      setError(
        '실제로 복용한 약만 선택해주세요. 모두 먹었다면 모두 복용을 선택해주세요.',
      );
      return;
    }
    pendingRef.current = true;
    setPending(true);
    try {
      if (await onRecord(active, result, selected)) setOpen(false);
      else
        setError(
          '기록을 저장하지 못했어요. 목록의 저장 상태를 확인하고, 변경된 일정은 창을 다시 열어 확인해주세요.',
        );
    } catch {
      setError('서버에 연결하지 못했어요. 입력 내용은 유지돼요.');
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  }
  return (
    <>
      <button
        className="dose-row manual-dose-row"
        onClick={edit}
        aria-label={`${occurrence.title} ${occurrence.time} 복용 확인`}
      >
        <span className="dose-time">
          <strong>{occurrence.time}</strong>
          <small>직접 등록</small>
        </span>
        <span className="dose-record">
          <Badge
            tone={occurrence.record?.result === 'all' ? 'green' : 'neutral'}
          >
            {occurrence.record
              ? resultLabels[occurrence.record.result]
              : '예정'}
          </Badge>
          <strong>{occurrence.title}</strong>
          <small>
            {occurrence.medicines.length
              ? occurrence.medicines
                  .map(
                    // 해당 회차에 포함된 약과 입력된 복용량만 표시한다.
                    function label(medicine) {
                      return [medicine.name, medicine.strength, medicine.dose]
                        .filter(Boolean)
                        .join(' · ');
                    },
                  )
                  .join(' / ')
              : '개별 약 정보 없이 등록한 묶음'}
          </small>
          {occurrence.record?.result === 'some' &&
            occurrence.medicines.length === 0 && (
              <small>일부 복용 · 개별 약 기록 없음</small>
            )}
        </span>
      </button>
      {open && (
        <Dialog title="복용 여부 확인" onClose={close} closeDisabled={pending}>
          <form onSubmit={save}>
            <fieldset className="medication-confirm-fields" disabled={pending}>
              <p>
                <strong>{active.title}</strong> · {active.date} {active.time}
              </p>
              <p className="caption">
                이 시간에 예정된 약의 복용 여부를 직접 기록해주세요.
              </p>
              <fieldset className="radio-list">
                <legend>확인 결과</legend>
                {(['all', 'some', 'none', 'deferred'] as const).map(
                  // 묶음 전체와 일부 복용을 구분하는 선택지를 제공한다.
                  function option(value) {
                    // 선택한 확인 상태를 임시 입력에만 반영한다.
                    function change() {
                      setResult(value);
                      setError('');
                    }
                    return (
                      <label key={value}>
                        <input
                          type="radio"
                          name="manual-dose-result"
                          checked={result === value}
                          onChange={change}
                          disabled={
                            value === 'some' && active.medicines.length === 1
                          }
                        />
                        {
                          {
                            all: '예정된 약 모두 복용',
                            some: '일부 약만 복용',
                            none: '복용 안 함',
                            deferred: '확인 보류',
                          }[value]
                        }
                      </label>
                    );
                  },
                )}
              </fieldset>
              {result === 'some' &&
                (active.medicines.length ? (
                  <fieldset className="radio-list">
                    <legend>실제로 복용한 약</legend>
                    {active.medicines.map(
                      // 이번 회차에 포함된 약만 선택할 수 있게 한다.
                      function medicine(item) {
                        // 선택 여부만 바꾸고 원래 처방의 용량을 변경하지 않는다.
                        function toggle() {
                          setSelected(
                            // 기존 선택에서 약 하나를 추가하거나 제거한다.
                            function update(ids) {
                              return ids.includes(item.id)
                                ? ids.filter(
                                    // 선택 해제한 약을 제외한다.
                                    function keep(id) {
                                      return id !== item.id;
                                    },
                                  )
                                : [...ids, item.id];
                            },
                          );
                        }
                        return (
                          <label key={item.id}>
                            <input
                              type="checkbox"
                              checked={selected.includes(item.id)}
                              onChange={toggle}
                            />
                            {item.name} {item.strength} {item.dose}
                          </label>
                        );
                      },
                    )}
                  </fieldset>
                ) : (
                  <p className="caption">
                    개별 약 정보가 없어 일부 복용 여부만 기록해요.
                  </p>
                ))}
              {error && (
                <p className="form-error" role="alert">
                  {error}
                </p>
              )}
              <button className="primary-button" disabled={pending}>
                {pending ? '저장 중…' : '복용 기록 저장'}
              </button>
            </fieldset>
          </form>
        </Dialog>
      )}
    </>
  );
}
