import { useEffect, useRef, type ReactNode } from 'react';

const icons = {
  home: 'd68f5',
  homeActive: '5f8de',
  calendar: '1a879',
  calendarActive: '86154',
  bell: 'caeab',
  bellActive: '7c8eb',
  search: '80914',
  next: '2294a',
  previous: '42707',
  up: '1ea58',
  down: 'e2dad',
  chevron: '21bbe',
  memo: 'f8ea4',
  plus: 'dff95',
  rotate: '51e67',
  back: '91bfd',
  pill: '792ca',
  info: 'd6ee5',
  check: '495f7',
  edit: '154a5',
  offline: '98ef2',
} as const;

// Figma에서 내려받은 원본 아이콘을 원래 크기로 표시한다.
export function Icon({ name }: { name: keyof typeof icons }) {
  const mobile = {
    homeActive: 'e33f1',
    memo: 'f8ea4',
    search: '80914',
    rotate: 'ff3f1',
  };
  const desktop = { memo: '23ff6', search: '1f666' };
  const small = name in mobile ? mobile[name as keyof typeof mobile] : null;
  const large =
    name in desktop ? desktop[name as keyof typeof desktop] : icons[name];
  return (
    <picture className="icon">
      {small && (
        <source media="(max-width: 767px)" srcSet={`/assets/${small}.svg`} />
      )}
      <img src={`/assets/${large}.svg`} alt="" aria-hidden="true" />
    </picture>
  );
}

// 색상과 함께 근거 상태를 글자로 표시한다.
export function Badge({
  children,
  tone = 'neutral',
}: {
  children: ReactNode;
  tone?: 'neutral' | 'green' | 'amber' | 'red';
}) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

// 정보가 없는 이유와 가능한 다음 행동을 같은 자리에서 안내한다.
export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-mark">
        <Icon name="pill" />
      </span>
      <h2>{title}</h2>
      <p>{children}</p>
      {action}
    </div>
  );
}

// 실제 버튼·폼을 담는 모달에 초점 잠금과 Escape 닫기를 제공한다.
export function Dialog({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  // 대화상자를 열고 닫힐 때 이전 키보드 초점으로 복귀한다.
  useEffect(function openDialog() {
    const dialog = ref.current;
    const previous = document.activeElement as HTMLElement | null;
    dialog?.showModal();
    // 닫은 뒤 원래 조작 위치로 키보드 초점을 돌려준다.
    return function closeDialog() {
      dialog?.close();
      previous?.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className="dialog"
      onCancel={onClose}
      aria-labelledby="dialog-title"
    >
      <div className="dialog-heading">
        <h2 id="dialog-title">{title}</h2>
        <button type="button" className="text-button" onClick={onClose}>
          닫기
        </button>
      </div>
      {children}
    </dialog>
  );
}

// 필요한 정보와 주의 문구를 반복 가능한 형식으로 표시한다.
export function Note({
  children,
  tone = 'amber',
}: {
  children: ReactNode;
  tone?: 'amber' | 'neutral' | 'red';
}) {
  return (
    <div className={`note note-${tone}`}>
      <Icon name="info" />
      <div>{children}</div>
    </div>
  );
}
