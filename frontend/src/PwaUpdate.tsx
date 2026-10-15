import { useRegisterSW } from 'virtual:pwa-register/react';
import { useState } from 'react';

// 업데이트를 알리고 사용자가 선택한 시점에 앱을 새로고침한다.
export default function PwaUpdate() {
  const [failed, setFailed] = useState(false);
  // 설치 실패는 일반 웹 사용과 구분해 안내한다.
  function registrationFailed() {
    setFailed(true);
  }
  const {
    needRefresh: [needed, setNeeded],
    updateServiceWorker,
  } = useRegisterSW({ onRegisterError: registrationFailed });
  // 새 버전을 적용하면 메모리의 시연 수정은 초기화된다.
  function update() {
    if (failed) window.location.reload();
    else void updateServiceWorker(true);
  }
  // 현재 화면을 유지하고 다음 방문에 갱신한다.
  function dismiss() {
    setNeeded(false);
    setFailed(false);
  }
  if (!needed && !failed) return null;
  return (
    <aside className="update-toast" role="status">
      <p>
        {failed
          ? '오프라인 화면 준비에 실패했어요. 인터넷 연결 상태에서 새로고침해주세요.'
          : '새 버전이 준비됐어요. 새로고침하면 시연 수정은 초기화돼요.'}
      </p>
      <div className="flex gap-3">
        <button className="secondary-button" onClick={update}>
          업데이트
        </button>
        <button className="text-button" onClick={dismiss}>
          나중에
        </button>
      </div>
    </aside>
  );
}
