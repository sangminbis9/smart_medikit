import { useId, useRef, useState, type FormEvent } from 'react';
import { dayKey } from './data';
import {
  validateMedicationPlan,
  type MedicationItem,
  type MedicationPlan,
  type MedicationSchedule,
  type MedicationTiming,
} from './medicationPlans';
import { Dialog, Icon } from './ui';

const weekdayLabels = ['일', '월', '화', '수', '목', '금', '토'];
const weekdayOrder = [1, 2, 3, 4, 5, 6, 0];

// 일정 사용을 선택한 뒤 사용자가 시각을 직접 입력하게 준비한다.
function emptySchedule(): MedicationSchedule {
  return {
    startDate: dayKey(),
    endDate: '',
    weekdays: [...weekdayOrder],
    times: [''],
  };
}

// 묶음과 개별 약에 같은 복용 방식·일정 입력을 제공한다.
function TimingFields({
  value,
  onChange,
  label,
}: {
  value: MedicationTiming;
  onChange: (value: MedicationTiming) => void;
  label: string;
}) {
  const fieldId = useId();
  const schedule = value.schedule;

  // 복용 방식이 바뀌면 정기 일정 사용 여부를 새로 선택하게 한다.
  function changeMode(event: React.ChangeEvent<HTMLSelectElement>) {
    onChange({
      mode: event.target.value as MedicationTiming['mode'],
      schedule: null,
    });
  }
  // 일정 입력을 켜거나 목록만 관리하도록 바꾼다.
  function toggleSchedule(event: React.ChangeEvent<HTMLInputElement>) {
    onChange({
      ...value,
      schedule: event.target.checked ? emptySchedule() : null,
    });
  }
  // 입력한 일정의 일부 항목만 교체한다.
  function updateSchedule(patch: Partial<MedicationSchedule>) {
    if (schedule) onChange({ ...value, schedule: { ...schedule, ...patch } });
  }
  // 사용자가 모든 요일을 한 번에 선택한다.
  function selectEveryDay() {
    updateSchedule({ weekdays: [...weekdayOrder] });
  }
  // 입력할 시각을 추가하며 임의 복용 시각을 채우지 않는다.
  function addTime() {
    if (schedule) updateSchedule({ times: [...schedule.times, ''] });
  }
  // 달력에 일정을 표시하기 시작할 날짜를 바꾼다.
  function changeStart(event: React.ChangeEvent<HTMLInputElement>) {
    updateSchedule({ startDate: event.target.value });
  }
  // 사용자가 정한 마지막 일정 날짜를 바꾼다.
  function changeEnd(event: React.ChangeEvent<HTMLInputElement>) {
    updateSchedule({ endDate: event.target.value });
  }

  return (
    <fieldset className="medication-timing">
      <legend>{label}</legend>
      <label className="medication-field" htmlFor={`${fieldId}-mode`}>
        복용 방식 <span className="medication-required">필수</span>
        <select id={`${fieldId}-mode`} value={value.mode} onChange={changeMode}>
          <option value="regular">정기적으로</option>
          <option value="as_needed">필요할 때</option>
        </select>
      </label>
      {value.mode === 'regular' ? (
        <label className="medication-checkbox">
          <input
            type="checkbox"
            checked={schedule !== null}
            onChange={toggleSchedule}
          />
          달력에 일정 등록
        </label>
      ) : (
        <p className="medication-hint">
          필요할 때 복용하는 약으로 기록하며, 정해진 시간의 일정을 만들지
          않아요.
        </p>
      )}
      {value.mode === 'regular' && schedule && (
        <div className="medication-schedule-fields">
          <fieldset className="medication-days">
            <legend>복용 요일</legend>
            <div className="medication-weekdays">
              {weekdayOrder.map(
                // 선택된 요일을 각 버튼의 눌림 상태로 표시한다.
                function weekday(day) {
                  // 현재 요일만 선택하거나 해제한다.
                  function toggleDay() {
                    if (!schedule) return;
                    const selected = new Set(schedule.weekdays);
                    if (selected.has(day)) selected.delete(day);
                    else selected.add(day);
                    updateSchedule({ weekdays: [...selected] });
                  }
                  return (
                    <button
                      type="button"
                      key={day}
                      aria-label={`${weekdayLabels[day]}요일`}
                      aria-pressed={schedule.weekdays.includes(day)}
                      onClick={toggleDay}
                    >
                      {weekdayLabels[day]}
                    </button>
                  );
                },
              )}
              <button
                type="button"
                className="medication-everyday"
                onClick={selectEveryDay}
              >
                매일
              </button>
            </div>
          </fieldset>
          <fieldset className="medication-times">
            <legend>복용 시간</legend>
            <div className="medication-time-list">
              {schedule.times.map(
                // 복용 시간 입력과 해당 시간 삭제 버튼을 함께 표시한다.
                function timeField(time, index) {
                  // 해당 시각만 사용자의 입력으로 교체한다.
                  function changeTime(
                    event: React.ChangeEvent<HTMLInputElement>,
                  ) {
                    if (!schedule) return;
                    const times = [...schedule.times];
                    times[index] = event.target.value;
                    updateSchedule({ times });
                  }
                  // 선택한 시간 입력을 지우고 최소 한 칸은 유지한다.
                  function removeTime() {
                    if (!schedule) return;
                    const times = [...schedule.times];
                    times.splice(index, 1);
                    updateSchedule({ times });
                  }
                  return (
                    <div className="medication-time" key={index}>
                      <label>
                        <span className="medication-hint">
                          시간 {index + 1}
                        </span>
                        <input
                          type="time"
                          required
                          value={time}
                          onChange={changeTime}
                        />
                      </label>
                      <button
                        type="button"
                        className="text-button"
                        aria-label={`시간 ${index + 1} 삭제`}
                        disabled={schedule.times.length === 1}
                        onClick={removeTime}
                      >
                        삭제
                      </button>
                    </div>
                  );
                },
              )}
              <button
                type="button"
                className="soft-button"
                onClick={addTime}
                disabled={schedule.times.length >= 12}
              >
                + 시간 추가
              </button>
            </div>
          </fieldset>
          <div className="medication-field-pair">
            <label className="medication-field">
              일정 시작일 <span className="medication-required">필수</span>
              <input
                type="date"
                required
                value={schedule.startDate}
                onChange={changeStart}
              />
            </label>
            <label className="medication-field">
              종료일 <span className="medication-optional">선택</span>
              <input
                type="date"
                value={schedule.endDate}
                min={schedule.startDate || undefined}
                onChange={changeEnd}
              />
              <small>미정이면 비워두세요.</small>
            </label>
          </div>
          <p className="medication-hint">
            일정은 한국 시간 기준이에요. 알림 발송은 포함하지 않아요.
          </p>
        </div>
      )}
    </fieldset>
  );
}

// 처방 묶음과 포함 약을 편집하고 검증을 통과한 입력만 전달한다.
export function MedicationPlanForm({
  initial,
  onSave,
  onClose,
  storageLabel,
  saveError = '',
}: {
  initial: MedicationPlan;
  onSave: (
    value: MedicationPlan,
    original?: MedicationPlan,
  ) => Promise<boolean>;
  onClose: () => void;
  storageLabel: string;
  saveError?: string;
}) {
  // 입력 중인 복사본을 만들어 저장 전 원래 목록을 바꾸지 않는다.
  const [draft, setDraft] = useState(function initialDraft() {
    return structuredClone(initial);
  });
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const pendingRef = useRef(false);

  // 묶음의 표시 이름을 입력한다.
  function changeTitle(event: React.ChangeEvent<HTMLInputElement>) {
    setDraft({ ...draft, title: event.target.value });
  }
  // 처방받은 안내를 그대로 메모한다.
  function changeNotes(event: React.ChangeEvent<HTMLTextAreaElement>) {
    setDraft({ ...draft, notes: event.target.value });
  }
  // 여러 약이 공유하는 일정을 바꾼다.
  function changeTiming(timing: MedicationTiming) {
    setDraft({ ...draft, timing });
  }
  // 약 정보 한 행을 빈 입력으로 추가한다.
  function addMedicine() {
    const medicine: MedicationItem = {
      id: crypto.randomUUID(),
      name: '',
      strength: '',
      dose: '',
      timing: null,
    };
    setDraft({ ...draft, medicines: [...draft.medicines, medicine] });
  }
  // 검증과 저장이 성공한 경우에만 편집창을 닫는다.
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pendingRef.current) return;
    const next = structuredClone(draft);
    next.title = next.title.trim();
    next.notes = next.notes.trim();
    for (const medicine of next.medicines) {
      medicine.name = medicine.name.trim();
      medicine.strength = medicine.strength.trim();
      medicine.dose = medicine.dose.trim();
    }
    const validation = validateMedicationPlan(next);
    if (validation) {
      setError(validation);
      return;
    }
    pendingRef.current = true;
    setPending(true);
    setError('');
    try {
      // 서버가 저장을 확인하기 전에는 입력창과 기존 목록을 유지한다.
      if (await onSave(next, initial)) onClose();
      else
        setError(
          '저장하지 못했어요. 입력 내용은 유지돼요. 다른 기기에서 변경했다면 닫고 최신 내용을 다시 열어주세요.',
        );
    } catch {
      setError(
        '저장 응답을 확인하지 못했어요. 연결 상태를 확인한 뒤 다시 시도해 주세요.',
      );
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  }

  return (
    <Dialog
      title={initial.title ? '복용약 수정' : '복용약 등록'}
      className="medication-dialog"
      onClose={onClose}
      closeDisabled={pending}
    >
      <form onSubmit={submit} aria-busy={pending}>
        <fieldset
          className="medication-form medication-form-body"
          disabled={pending}
        >
          <p className="medication-form-intro">
            한 번에 처방받은 여러 약을 하나로 묶어 관리해요. 복용법은 처방받은
            내용대로 입력해 주세요.
          </p>
          <section
            className="medication-form-section"
            aria-labelledby="medication-bundle-heading"
          >
            <div className="medication-section-title">
              <span>1</span>
              <h3 id="medication-bundle-heading">처방 묶음</h3>
            </div>
            <label className="medication-field">
              묶음 이름 <span className="medication-required">필수</span>
              <input
                required
                autoFocus
                maxLength={100}
                value={draft.title}
                onChange={changeTitle}
                placeholder="예: 감기 처방약"
              />
            </label>
            <TimingFields
              value={draft.timing}
              onChange={changeTiming}
              label="공통 복용법"
            />
          </section>
          <section
            className="medication-form-section"
            aria-labelledby="medication-items-heading"
          >
            <div className="medication-section-title">
              <span>2</span>
              <h3 id="medication-items-heading">포함된 약</h3>
              <small>선택</small>
            </div>
            <p className="medication-hint">
              약 이름을 모르면 묶음만 먼저 등록해도 돼요. 개별 약 정보는 나중에
              추가할 수 있어요.
            </p>
            <div className="medication-item-editors">
              {draft.medicines.map(
                // 약마다 복용량과 공통 일정의 적용 여부를 입력한다.
                function medicineFields(medicine, index) {
                  // 현재 약의 입력값만 교체한다.
                  function updateMedicine(patch: Partial<MedicationItem>) {
                    const medicines = [...draft.medicines];
                    medicines[index] = { ...medicine, ...patch };
                    setDraft({ ...draft, medicines });
                  }
                  // 현재 약 정보 행을 삭제한다.
                  function removeMedicine() {
                    const medicines = [...draft.medicines];
                    medicines.splice(index, 1);
                    setDraft({ ...draft, medicines });
                  }
                  // 처방전에 적힌 약 이름을 입력한다.
                  function changeName(
                    event: React.ChangeEvent<HTMLInputElement>,
                  ) {
                    updateMedicine({ name: event.target.value });
                  }
                  // 같은 이름의 제품을 구분하는 함량과 제형을 입력한다.
                  function changeStrength(
                    event: React.ChangeEvent<HTMLInputElement>,
                  ) {
                    updateMedicine({ strength: event.target.value });
                  }
                  // 처방받은 1회 복용량을 임의 변환 없이 입력한다.
                  function changeDose(
                    event: React.ChangeEvent<HTMLInputElement>,
                  ) {
                    updateMedicine({ dose: event.target.value });
                  }
                  // 공통 일정을 복사해 해당 약의 예외 일정을 편집한다.
                  function toggleOverride(
                    event: React.ChangeEvent<HTMLInputElement>,
                  ) {
                    updateMedicine({
                      timing: event.target.checked
                        ? structuredClone(draft.timing)
                        : null,
                    });
                  }
                  // 해당 약의 복용 방식과 일정을 바꾼다.
                  function changeItemTiming(timing: MedicationTiming) {
                    updateMedicine({ timing });
                  }
                  return (
                    <fieldset
                      className="medication-item-editor"
                      key={medicine.id}
                    >
                      <legend>약 {index + 1}</legend>
                      <button
                        type="button"
                        className="text-button medication-remove"
                        onClick={removeMedicine}
                        aria-label={`약 ${index + 1} 삭제`}
                      >
                        삭제
                      </button>
                      <label className="medication-field">
                        약 이름{' '}
                        <span className="medication-required">필수</span>
                        <input
                          required
                          maxLength={200}
                          value={medicine.name}
                          onChange={changeName}
                          placeholder="처방전·포장에 적힌 제품명"
                        />
                      </label>
                      <div className="medication-field-pair">
                        <label className="medication-field">
                          함량·제형{' '}
                          <span className="medication-optional">선택</span>
                          <input
                            maxLength={100}
                            value={medicine.strength}
                            onChange={changeStrength}
                            placeholder="포장에 적힌 함량·제형"
                          />
                        </label>
                        <label className="medication-field">
                          1회 복용량{' '}
                          <span className="medication-optional">선택</span>
                          <input
                            maxLength={200}
                            value={medicine.dose}
                            onChange={changeDose}
                            placeholder="처방받은 수량·단위"
                          />
                        </label>
                      </div>
                      <label className="medication-checkbox">
                        <input
                          type="checkbox"
                          checked={medicine.timing !== null}
                          onChange={toggleOverride}
                        />
                        이 약만 다르게 설정
                      </label>
                      {medicine.timing && (
                        <TimingFields
                          value={medicine.timing}
                          onChange={changeItemTiming}
                          label={`약 ${index + 1}의 복용법`}
                        />
                      )}
                    </fieldset>
                  );
                },
              )}
            </div>
            <button
              type="button"
              className="secondary-button medication-add-item"
              onClick={addMedicine}
              disabled={draft.medicines.length >= 30}
            >
              <Icon name="plus" />약 추가
            </button>
          </section>
          <section
            className="medication-form-section"
            aria-labelledby="medication-notes-heading"
          >
            <div className="medication-section-title">
              <span>3</span>
              <h3 id="medication-notes-heading">복용 안내·메모</h3>
              <small>선택</small>
            </div>
            <label className="medication-field">
              처방받은 안내
              <textarea
                maxLength={2000}
                rows={3}
                value={draft.notes}
                onChange={changeNotes}
                placeholder="식전·식후 등 처방받은 안내를 입력해 주세요."
              />
            </label>
          </section>
          <p className="medication-hint">처방받은 내용을 직접 입력해 주세요.</p>
          <p className="medication-hint">{storageLabel}</p>
          {initial.title && (
            <p className="medication-hint">
              약 구성이나 일정을 바꾸면 이 묶음의 복용 확인 기록이 초기화돼요.
            </p>
          )}
          {(saveError || error) && (
            <p className="medication-form-error" role="alert">
              {saveError || error}
            </p>
          )}
          <div className="medication-form-actions">
            <button
              type="button"
              className="secondary-button"
              onClick={onClose}
            >
              취소
            </button>
            <button type="submit" className="primary-button">
              {pending
                ? '저장 중…'
                : initial.title
                  ? '변경사항 저장'
                  : '복용약 등록'}
            </button>
          </div>
        </fieldset>
      </form>
    </Dialog>
  );
}
