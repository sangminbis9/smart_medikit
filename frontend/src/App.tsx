import { useState } from 'react';
import {
  Link,
  NavLink,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from 'react-router-dom';
import { timeLabel } from './data';
import { useMedkit } from './useMedkit';
import { Badge, Icon } from './ui';
import Home, { Search } from './Home';
import Schedule from './Schedule';
import Alerts from './Alerts';
import PackageDetail from './PackageDetail';
import Device from './Device';
import PwaUpdate from './PwaUpdate';

const destinations = [
  { path: '/', label: '홈', icon: 'home', active: 'homeActive' },
  {
    path: '/schedule',
    label: '복약',
    icon: 'calendar',
    active: 'calendarActive',
  },
  { path: '/alerts', label: '알림', icon: 'bell', active: 'bellActive' },
] as const;

// 화면 이동과 반응형 재배치에도 검색과 선택을 유지한다.
export default function App() {
  const kit = useMedkit();
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const navigate = useNavigate();
  const location = useLocation();
  let unread = 0;
  for (const alert of kit.data.alerts)
    if (alert.ack_state === 'PENDING') unread++;
  const title = location.pathname.startsWith('/schedule')
    ? '복약'
    : location.pathname.startsWith('/alerts')
      ? '알림'
      : location.pathname.startsWith('/device')
        ? '보관함 연결'
        : location.pathname.startsWith('/packages')
          ? '약품 상세'
          : '홈';
  const date = new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    month: 'long',
    day: 'numeric',
    weekday: 'long',
  }).format(new Date());
  const connection = kit.demo
    ? '시연 중'
    : !kit.online
      ? '오프라인'
      : kit.status === 'ready'
        ? '조회 연결됨'
        : '연결 준비';

  // 어디서 검색하더라도 홈의 같은 검색 상태를 사용한다.
  function search(value: string) {
    setQuery(value);
    if (location.pathname !== '/') navigate('/');
  }
  // 시연 진입과 종료 시 오래된 검색·선택을 지운다.
  function toggleDemo() {
    setQuery('');
    setSelected(null);
    kit.toggleDemo();
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        본문으로 건너뛰기
      </a>
      <aside className="sidebar">
        <Link className="brand" to="/" aria-label="MEDKIT 홈">
          <span>
            <Icon name="plus" />
          </span>
          MEDKIT
        </Link>
        <p className="sidebar-label">우리집 약품함</p>
        <Navigation unread={unread} />
        <div className="sidebar-bottom">
          <Link className="connection-card" to="/device">
            <strong>{connection}</strong>
            <span>
              마지막 동기화
              <br />
              {kit.data.synced_at && !kit.demo
                ? timeLabel(kit.data.synced_at)
                : '아직 없음'}
            </span>
          </Link>
          <button className="demo-switch" onClick={toggleDemo}>
            {kit.demo ? '시연 종료 · 서버 저장 없음' : '시연 데이터 보기'}
          </button>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <span className="topbar-title">{title}</span>
          <Search value={query} onChange={search} />
          <div className="topbar-right">
            <time>{date}</time>
            <Link to="/device">
              <Badge tone={kit.demo ? 'amber' : 'green'}>{connection}</Badge>
            </Link>
          </div>
        </header>
        <div className="tablet-navigation">
          <Navigation unread={unread} />
        </div>
        <main
          id="main"
          className={`main-content ${location.pathname === '/' ? 'main-home' : ''}`}
        >
          {kit.demo && (
            <div className="mode-banner">
              <span>시연 데이터 · 실제 약품·기록이 아니에요</span>
              <button onClick={toggleDemo}>시연 종료</button>
            </div>
          )}
          {!kit.online && (
            <div className="connection-banner" role="status">
              오프라인이에요.{' '}
              {kit.demo
                ? '시연 화면만 볼 수 있어요.'
                : '현재 화면의 이전 정보만 표시하며 최신 상태는 확인할 수 없어요.'}
            </div>
          )}
          {kit.error && !kit.demo && (
            <div className="connection-banner" role="alert">
              <span>{kit.error}</span>
              <button className="text-button" onClick={kit.refresh}>
                다시 시도
              </button>
            </div>
          )}
          {kit.status === 'loading' && !kit.demo && (
            <div className="loading-strip" role="status">
              최신 정보를 가져오고 있어요…
            </div>
          )}
          <Routes>
            <Route
              path="/"
              element={
                <Home
                  kit={{ ...kit, toggleDemo }}
                  query={query}
                  setQuery={setQuery}
                  selected={selected}
                  setSelected={setSelected}
                />
              }
            />
            <Route path="/schedule" element={<Schedule kit={kit} />} />
            <Route path="/alerts" element={<Alerts kit={kit} />} />
            <Route path="/packages/:id" element={<PackageDetail kit={kit} />} />
            <Route
              path="/device"
              element={<Device kit={{ ...kit, toggleDemo }} />}
            />
            <Route
              path="*"
              element={
                <div className="standard-page">
                  <h1>페이지를 찾을 수 없어요</h1>
                  <Link className="secondary-button" to="/">
                    홈으로 돌아가기
                  </Link>
                </div>
              }
            />
          </Routes>
        </main>
      </div>
      <div className="bottom-navigation">
        <Navigation unread={unread} />
      </div>
      <PwaUpdate />
    </div>
  );
}

// 데스크톱·태블릿·모바일에서 같은 목적지와 선택 상태를 공유한다.
function Navigation({ unread }: { unread: number }) {
  return (
    <nav aria-label="주 메뉴">
      {destinations.map(
        /* 공통 메뉴의 링크와 선택 아이콘을 만든다. */ function destination(
          item,
        ) {
          // 현재 경로의 내비게이션만 강조한다.
          function style({ isActive }: { isActive: boolean }) {
            return `nav-item ${isActive ? 'active' : ''}`;
          }
          // Figma의 선택 상태 아이콘을 그대로 사용한다.
          function content({ isActive }: { isActive: boolean }) {
            return (
              <>
                <span className="nav-icon">
                  <Icon name={isActive ? item.active : item.icon} />
                </span>
                <span>{item.label}</span>
                {item.path === '/alerts' && unread > 0 && (
                  <small className="nav-count">{unread}</small>
                )}
              </>
            );
          }
          return (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === '/'}
              className={style}
            >
              {content}
            </NavLink>
          );
        },
      )}
    </nav>
  );
}
