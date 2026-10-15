import { Link } from 'react-router-dom';
import { settings } from './settings';
import { timeLabel } from './data';
import type { Medkit } from './useMedkit';
import { Badge, Icon, Note } from './ui';

// 장치 연결과 웹 푸시 지원 조건을 성공으로 오인하지 않도록 표시한다.
export default function Device({ kit }: { kit: Medkit }) {
  const supported =
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window;
  const standalone = window.matchMedia('(display-mode: standalone)').matches;
  const dimensions = kit.data.box.dimensions || settings.dimensions;
  const device = kit.data.box.device_status;
  const status = kit.demo
    ? '시연 모드'
    : !kit.online
      ? '인터넷 연결 끊김'
      : device === 'ONLINE'
        ? '보관함 온라인'
        : device === 'OFFLINE'
          ? '보관함 연결 끊김'
          : '보관함 미연결';
  return (
    <div className="standard-page device-page">
      <div className="detail-heading">
        <Link className="icon-button" to="/" aria-label="홈으로 돌아가기">
          <Icon name="back" />
        </Link>
        <h1>보관함 연결</h1>
      </div>
      <div className="device-grid">
        <section className="device-panel">
          <div className="device-symbol">
            <Icon
              name={device === 'ONLINE' && !kit.demo ? 'check' : 'offline'}
            />
          </div>
          <h2>우리집 약품함</h2>
          <Badge tone={device === 'ONLINE' && !kit.demo ? 'green' : 'amber'}>
            {status}
          </Badge>
          <p>
            마지막 동기화{' '}
            {kit.data.synced_at && !kit.demo
              ? timeLabel(kit.data.synced_at)
              : '아직 없음'}
          </p>
          <button
            className="primary-button"
            onClick={kit.refresh}
            disabled={
              kit.demo ||
              !kit.online ||
              !settings.apiUrl ||
              kit.status === 'loading'
            }
          >
            {kit.status === 'loading' ? '확인 중…' : '연결 다시 확인'}
          </button>
        </section>
        <div className="flex flex-col gap-4">
          <Note tone="neutral">
            약품은 보관함 카메라에서 자동 등록됩니다. 아직 장치·서버가 없어도
            화면을 둘러볼 수 있어요.
          </Note>
          <dl className="detail-fields">
            <div>
              <dt>데이터 연결</dt>
              <dd>
                {kit.demo
                  ? '시연 데이터 · 서버 저장 없음'
                  : settings.apiUrl
                    ? kit.status === 'ready'
                      ? '조회 서버 연결됨'
                      : '조회 서버 확인 필요'
                    : '서버 주소 미설정'}
              </dd>
            </div>
            <div>
              <dt>실시간 갱신</dt>
              <dd>{kit.live ? '실시간 연결됨' : '실시간 연결 대기'}</dd>
            </div>
            <div>
              <dt>보관함 내부 치수 · 가로 × 세로 × 높이</dt>
              <dd>
                {dimensions
                  ? `${dimensions.width} × ${dimensions.depth} × ${dimensions.height} mm${kit.demo ? ' · 시연 값' : ''}`
                  : '미설정 · 3D는 예시 비율'}
              </dd>
            </div>
          </dl>
          <button className="secondary-button" onClick={kit.toggleDemo}>
            {kit.demo ? '시연 종료 · 실제 연결 화면으로' : '시연 데이터 보기'}
          </button>
          <p className="caption">
            시연 중 수정한 내용은 새로고침하거나 시연을 종료하면 초기화돼요.
          </p>
        </div>
      </div>
      <section className="install-panel">
        <h2>홈 화면에서 더 편하게</h2>
        <p>
          설치 없이도 기본 화면을 사용할 수 있어요.{' '}
          {standalone
            ? '현재 설치된 웹앱으로 실행 중이에요.'
            : '자주 사용한다면 홈 화면에 추가해보세요.'}
        </p>
        <div className="install-grid">
          <div>
            <h3>iPhone · iPad</h3>
            <p>Safari의 공유 메뉴 → 홈 화면에 추가 → 추가한 MEDKIT 열기</p>
          </div>
          <div>
            <h3>Android · PC</h3>
            <p>
              브라우저 메뉴에서 ‘앱 설치’ 또는 ‘홈 화면에 추가’를 선택하세요.
              지원 브라우저에서 설치 메뉴가 표시됩니다.
            </p>
          </div>
        </div>
        <Note>
          iPhone 푸시는 iOS 16.4 이상에서 홈 화면에 추가한 PWA를 열고, 직접 알림
          권한을 허용하고 구독해야 합니다. 일반 Safari 탭에서는 웹 푸시를 받을
          수 없어요.
        </Note>
      </section>
      <section className="install-panel">
        <h2>웹 푸시 알림</h2>
        <p>
          이 브라우저:{' '}
          {supported
            ? '푸시 API 지원'
            : '푸시 API 미지원 또는 실행 조건 미충족'}
          {supported
            ? ` / 권한 ${Notification.permission === 'granted' ? '허용됨' : Notification.permission === 'denied' ? '차단됨' : '아직 요청하지 않음'}`
            : ''}
        </p>
        <Badge>알림 전송 기능 준비 중</Badge>
        <p className="caption">
          권한 허용만으로 알림 설정이 완료되지는 않아요. 현재는 알림 탭을
          이용해주세요. 알림 서비스가 준비되면 이곳에서 직접 알림을 켤 수
          있어요.
        </p>
        <Link className="secondary-button" to="/alerts">
          웹 알림 목록 보기
        </Link>
      </section>
    </div>
  );
}
