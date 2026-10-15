import { lazy, Suspense, useEffect, useMemo, useRef } from 'react';
import { Link } from 'react-router-dom';
import { expiryLabel, findPackages, stockLabel, type Package } from './data';
import { settings } from './settings';
import type { Medkit } from './useMedkit';
import { Badge, EmptyState, Icon } from './ui';

// 3D 코드는 홈 진입 시에만 내려받는다.
const Cabinet = lazy(function loadCabinet() {
  return import('./Cabinet');
});

export const stateLabels = {
  REGISTERED: '등록 대기',
  STORED: '보관 중',
  OUT: '반출 중',
  DEPLETED: '소진',
  DISPOSED: '폐기',
};

type Props = {
  kit: Medkit;
  query: string;
  setQuery: (value: string) => void;
  selected: string | null;
  setSelected: (id: string | null) => void;
};

// 고정된 3D와 독립 스크롤 목록에서 같은 포장 선택을 공유한다.
export default function Home({
  kit,
  query,
  setQuery,
  selected,
  setSelected,
}: Props) {
  const { data, demo, toggleDemo } = kit;
  const list = useRef<HTMLDivElement>(null);
  const scrollTarget = useRef<number | null>(null);
  const filter = query === '' ? '전체' : query;
  // 검색 결과를 계산하되 화면 크기 변경으로 상태를 초기화하지 않는다.
  const items = useMemo(
    // 현재 검색어와 일치하는 포장만 반환한다.
    function filtered() {
      return findPackages(data.packages, query);
    },
    [data.packages, query],
  );
  // 현재 선택이 검색 결과에서 사라질 때만 첫 결과로 이동한다.
  const active =
    items.find(
      /* 기존 선택을 검색 결과 안에서 찾는다. */ function matches(item) {
        return item.package_id === selected;
      },
    ) || items[0];
  const selectedId = active?.package_id || null;
  const index = active ? items.indexOf(active) : -1;
  let stored = 0;
  let review = 0;
  for (const item of data.packages) {
    if (item.state === 'STORED') stored++;
    if (!item.expiry.verified && item.state !== 'DISPOSED') review++;
  }

  useEffect(
    // 목록 스크롤에 따라 가장 가까운 카드와 3D 선택을 맞춘다.
    function observeCards() {
      const root = list.current;
      if (!root || !items.length) return;
      let timer = 0;
      // 스크롤이 멈추면 가장 위에 보이는 카드를 3D에서 선택한다.
      function pickVisible() {
        // 명시적 선택으로 이동한 위치에서는 그 선택을 다시 덮어쓰지 않는다.
        if (root!.scrollTop === scrollTarget.current) return;
        scrollTarget.current = null;
        const top = root!.getBoundingClientRect().top;
        const cards = root!.querySelectorAll<HTMLElement>('[data-package]');
        let nearest: HTMLElement | null = null;
        let distance = Infinity;
        for (const card of cards) {
          const gap = Math.abs(card.getBoundingClientRect().top - top - 8);
          if (gap < distance) {
            distance = gap;
            nearest = card;
          }
        }
        if (nearest?.dataset.package) setSelected(nearest.dataset.package);
      }
      // 빠른 스크롤 중 선택이 계속 바뀌지 않게 묶는다.
      function onScroll() {
        window.clearTimeout(timer);
        timer = window.setTimeout(pickVisible, 130);
      }
      root.addEventListener('scroll', onScroll, { passive: true });
      // 화면을 떠날 때 지연 선택과 스크롤 핸들러를 해제한다.
      return function cleanup() {
        root.removeEventListener('scroll', onScroll);
        window.clearTimeout(timer);
      };
    },
    [items, setSelected],
  );

  // 3D나 이전·다음 버튼에서 선택한 카드를 목록 안으로 가져온다.
  function select(id: string) {
    setSelected(id);
    const cards = list.current?.querySelectorAll<HTMLElement>('[data-package]');
    for (const card of cards || [])
      if (card.dataset.package === id)
        card.scrollIntoView({ block: 'nearest', behavior: 'instant' });
    scrollTarget.current = list.current?.scrollTop ?? null;
  }
  // 검색어와 바로가기 선택을 함께 변경한다.
  function shortcut(value: string) {
    setQuery(value === '전체' ? '' : value);
  }
  // 수동 검색 시 바로가기 강조를 해제한다.
  function search(value: string) {
    setQuery(value);
  }
  // 한 단계 앞의 검색 결과를 선택한다.
  function previous() {
    if (index > 0) select(items[index - 1].package_id);
  }
  // 한 단계 뒤의 검색 결과를 선택한다.
  function next() {
    if (index + 1 < items.length) select(items[index + 1].package_id);
  }
  // 검색 필터를 해제해 전체 목록으로 돌아간다.
  function clear() {
    shortcut('전체');
  }

  return (
    <div className="home-page">
      <div className="page-heading home-heading">
        <div>
          <h1>우리집 약품함</h1>
          <p>보고 있는 약을, 보관함에서도 바로 찾아보세요.</p>
        </div>
        <div className="heading-badges">
          <Badge tone="green">보관 중 {stored}</Badge>
          <Badge tone="amber">확인할 정보 {review}</Badge>
        </div>
        <Link className="mobile-connection" to="/device">
          <Badge tone={demo ? 'amber' : 'neutral'}>
            {demo ? '시연 중' : '연결 정보'}
          </Badge>
        </Link>
      </div>
      <div className="mobile-search">
        <Search value={query} onChange={search} />
      </div>
      <div className="home-filters">
        <div className="chips">
          {/* 효능 검색은 보유 약품을 찾는 기능이며 복용 추천이 아니다. */}
          {['전체', '두통', '복통'].map(
            /* 검색 바로가기 버튼을 표시한다. */ function chip(label) {
              // 버튼에 해당하는 검색 조건을 적용한다.
              function choose() {
                shortcut(label);
              }
              return (
                <button
                  key={label}
                  className={`chip ${filter === label && (label === '전체' ? !query : query === label) ? 'active' : ''}`}
                  onClick={choose}
                  aria-pressed={
                    filter === label &&
                    (label === '전체' ? !query : query === label)
                  }
                >
                  {label}
                </button>
              );
            },
          )}
        </div>
        <span>약 이름 또는 효능·효과로 검색</span>
      </div>
      <div className="home-grid">
        <div className="scene-column">
          <Suspense
            fallback={
              <div className="cabinet scene-loading" role="status">
                3D 보관함을 준비하고 있어요…
              </div>
            }
          >
            <Cabinet
              items={items}
              alerts={data.alerts}
              selected={selectedId}
              dimensions={data.box.dimensions || settings.dimensions}
              demo={demo}
              onSelect={select}
            />
          </Suspense>
          {active ? (
            <Link
              className="focus-summary"
              to={`/packages/${active.package_id}`}
            >
              <div className="flex items-center justify-between gap-3">
                <h2>
                  {String(index + 1).padStart(2, '0')} {active.name}
                </h2>
                <span>선택된 약품</span>
              </div>
              <p>
                {active.position ? '위치 추정' : '위치 미확인'} ·{' '}
                {active.note || '포장 정보를 확인해주세요.'}
              </p>
            </Link>
          ) : (
            <div className="focus-summary">
              <h2>
                {query ? '검색 결과가 없어요' : '아직 보관된 약품이 없어요'}
              </h2>
              <p>보관함 카메라로 등록한 약품이 여기에 표시돼요.</p>
            </div>
          )}
          <p className="scene-caption">
            가려진 약품의 위치는 실제 배치와 다를 수 있어요.
          </p>
        </div>
        <section className="inventory" aria-label="보관 약품 목록">
          <div className="inventory-heading">
            <div className="flex items-center justify-between gap-3">
              <h2>
                보관 약품 <span>{items.length}</span>
              </h2>
              <div className="stepper">
                <span>
                  {index < 0 ? '00' : String(index + 1).padStart(2, '0')} /{' '}
                  {String(items.length).padStart(2, '0')}
                </span>
                <button
                  className="icon-button"
                  aria-label="이전 약품"
                  onClick={previous}
                  disabled={index <= 0}
                >
                  <Icon name="up" />
                </button>
                <button
                  className="icon-button"
                  aria-label="다음 약품"
                  onClick={next}
                  disabled={index < 0 || index >= items.length - 1}
                >
                  <Icon name="down" />
                </button>
              </div>
            </div>
            <p>카드를 스크롤하면 3D에서도 같은 약을 강조해요.</p>
          </div>
          <div
            className="card-list"
            ref={list}
            tabIndex={0}
            aria-label="약품 카드 스크롤 영역"
          >
            {items.length ? (
              items.map(
                /* 포장별 요약 카드와 상세 링크를 표시한다. */ function card(
                  item,
                  order,
                ) {
                  // 카드 본문을 선택하면 3D만 바꾸고 상세 링크는 별도로 연다.
                  function focus() {
                    select(item.package_id);
                  }
                  return (
                    <PackageCard
                      key={item.package_id}
                      item={item}
                      order={order + 1}
                      selected={item.package_id === selectedId}
                      onSelect={focus}
                    />
                  );
                },
              )
            ) : (
              <EmptyState
                title={
                  query ? '일치하는 약품이 없어요' : '첫 약품을 기다리고 있어요'
                }
                action={
                  query ? (
                    <button className="secondary-button" onClick={clear}>
                      검색 초기화
                    </button>
                  ) : (
                    <button className="secondary-button" onClick={toggleDemo}>
                      {demo ? '빈 보관함으로 돌아가기' : '시연 데이터 보기'}
                    </button>
                  )
                }
              >
                {query
                  ? '약 이름이나 다른 검색어로 다시 찾아보세요.'
                  : '약 포장을 보관함 카메라에 보여주면 자동 등록돼요. 지금은 시연 데이터로 먼저 둘러볼 수 있어요.'}
              </EmptyState>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

// 동일한 입력 컴포넌트를 PC 헤더와 모바일 홈에서 사용한다.
export function Search({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  // 입력한 검색어를 상위 화면에 전달한다.
  function change(event: React.ChangeEvent<HTMLInputElement>) {
    onChange(event.target.value);
  }
  // 검색어를 한 번에 지운다.
  function clear() {
    onChange('');
  }
  return (
    <div className="search">
      <Icon name="search" />
      <input
        type="search"
        value={value}
        onChange={change}
        placeholder="약 이름, 증상으로 보관 약품 검색"
        aria-label="보관 약품 검색"
      />
      {value && (
        <button
          onClick={clear}
          className="text-button"
          aria-label="검색어 지우기"
        >
          지우기
        </button>
      )}
    </div>
  );
}

// 포장 단위 재고·근거·메모와 상세 접근을 함께 제공한다.
function PackageCard({
  item,
  order,
  selected,
  onSelect,
}: {
  item: Package;
  order: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const stock = stockLabel(item);
  return (
    <article
      className={`package-card ${selected ? 'selected' : ''}`}
      data-package={item.package_id}
    >
      <div className="package-top">
        <button
          className="package-select"
          onClick={onSelect}
          aria-label={`${item.name} 3D에서 선택`}
          aria-pressed={selected}
        >
          <span className="ordinal">{String(order).padStart(2, '0')}</span>
          <span className="package-label">
            <strong>{item.name}</strong>
            <small>
              {stateLabels[item.state]} /{' '}
              {item.kind === 'PRESCRIPTION' ? '처방약 봉투' : '일반의약품'}
            </small>
          </span>
        </button>
        <Link
          className="detail-link"
          to={`/packages/${item.package_id}`}
          aria-label={`${item.name} 상세 보기`}
        >
          <Icon name="chevron" />
        </Link>
      </div>
      <div className="flex flex-wrap gap-2">
        {stock && <Badge tone="amber">{stock}</Badge>}
        <Badge>{expiryLabel(item)}</Badge>
      </div>
      <div className="card-note">
        <Icon name="memo" />
        <p>{item.note || '확인 필요  포장에 적힌 정보를 확인해주세요.'}</p>
      </div>
    </article>
  );
}
