import { useRef, useState, type FormEvent } from 'react';
import { Icon } from './ui';
import './medicationPlans.css';

// 공통 비밀번호로 보호된 복용약 목록의 연결·잠금 상태를 안내한다.
export default function MedicationAccess({
  status,
  error,
  busy,
  onUnlock,
  onReload,
}: {
  status: 'unconfigured' | 'locked' | 'loading' | 'ready' | 'error';
  error: string;
  busy: boolean;
  onUnlock: (password: string) => Promise<boolean>;
  onReload: () => Promise<void>;
}) {
  const [password, setPassword] = useState('');
  const [pending, setPending] = useState(false);
  const [localError, setLocalError] = useState('');
  const pendingRef = useRef(false);
  const disabled = busy || pending;

  // 비밀번호는 화면 메모리에서만 입력받는다.
  function changePassword(event: React.ChangeEvent<HTMLInputElement>) {
    setPassword(event.target.value);
  }
  // 서버 인증이 끝날 때까지 중복 제출을 막고 비밀번호를 보관하지 않는다.
  async function unlock(event: FormEvent) {
    event.preventDefault();
    if (disabled || pendingRef.current || !password) return;
    pendingRef.current = true;
    setPending(true);
    setLocalError('');
    try {
      if (!(await onUnlock(password)))
        setLocalError(
          '목록을 열지 못했어요. 비밀번호와 연결 상태를 확인해 주세요.',
        );
    } catch {
      setLocalError('서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.');
    } finally {
      setPassword('');
      pendingRef.current = false;
      setPending(false);
    }
  }
  // 서버 조회를 다시 시도하고 오류를 일반 문구로 알린다.
  async function retry() {
    if (disabled || pendingRef.current) return;
    pendingRef.current = true;
    setPending(true);
    setLocalError('');
    try {
      await onReload();
    } catch {
      setLocalError(
        '목록을 다시 가져오지 못했어요. 연결 상태를 확인해 주세요.',
      );
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  }

  return (
    <section
      className="medication-plans medication-access"
      aria-labelledby="medication-access-title"
      aria-busy={disabled || status === 'loading'}
    >
      <div className="medication-access-heading">
        <span className="medication-plan-icon">
          <Icon name="pill" />
        </span>
        <div>
          <div className="medication-eyebrow">처방 묶음과 복용 일정</div>
          <h2 id="medication-access-title">복용 중인 약</h2>
        </div>
      </div>
      {status === 'unconfigured' ? (
        <div className="medication-access-message">
          <strong>복용약 저장 API 연결을 준비 중이에요</strong>
          <p>
            연결이 완료되면 공통 비밀번호로 목록을 열고, 여러 기기에서 같은
            복용약을 관리할 수 있어요.
          </p>
        </div>
      ) : status === 'locked' ? (
        <form className="medication-access-form" onSubmit={unlock}>
          <p>팀 공통 비밀번호로 복용약 목록을 열어요.</p>
          <p className="medication-hint">
            비밀번호를 아는 팀원은 같은 목록과 기록을 조회하고 수정할 수 있어요.
          </p>
          <label className="medication-field">
            공통 비밀번호
            <input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={changePassword}
              disabled={disabled}
            />
          </label>
          {(error || localError) && (
            <p className="medication-form-error" role="alert">
              {error || localError}
            </p>
          )}
          <button
            type="submit"
            className="primary-button"
            disabled={disabled || !password}
          >
            {disabled ? '확인 중…' : '잠금 해제'}
          </button>
        </form>
      ) : status === 'loading' ? (
        <p className="medication-access-message" role="status">
          복용약 목록을 불러오고 있어요…
        </p>
      ) : status === 'error' ? (
        <div className="medication-access-message">
          <p className="medication-form-error" role="alert">
            {error || localError || '복용약 목록을 가져오지 못했어요.'}
          </p>
          <button
            type="button"
            className="secondary-button"
            onClick={retry}
            disabled={disabled}
          >
            {disabled ? '다시 연결 중…' : '다시 시도'}
          </button>
        </div>
      ) : null}
    </section>
  );
}
