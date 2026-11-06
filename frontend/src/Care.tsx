import { useEffect, useRef, useState, type ChangeEvent } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import {
  careCategories,
  careGuides,
  findCareGuides,
  medicineCategories,
} from './careGuides';
import {
  careInventoryNotice,
  matchCareMedicines,
  type CareStatus,
} from './careMatching';
import { expiryLabel, stockLabel, type Package } from './data';
import type { Medkit } from './useMedkit';
import { Badge, Dialog, EmptyState, Icon, Note } from './ui';
import MedicineUsage from './MedicineUsage';

// 검색과 카테고리를 주소에 남겨 상세 화면에서 돌아와도 탐색을 유지한다.
export function CareIndex() {
  const [params, setParams] = useSearchParams();
  const query = params.get('q') || '';
  const category = params.get('category') || '';
  const results = findCareGuides(query, category);
  // 입력한 검색어만 바꾸고 선택 카테고리는 유지한다.
  function search(event: ChangeEvent<HTMLInputElement>) {
    const next = new URLSearchParams(params);
    if (event.target.value) next.set('q', event.target.value);
    else next.delete('q');
    setParams(next, { replace: true });
  }
  // 카테고리 선택과 검색을 초기화한다.
  function reset() {
    setParams({});
  }
  return (
    <div className="standard-page care-page">
      <header className="care-intro">
        <div className="care-eyebrow">
          <Icon name="memo" /> 우리집 건강 안내
        </div>
        <h1>증상별 케어 가이드</h1>
        <p>
          어디가 불편하신가요?
          <br />
          먼저 할 수 있는 대처부터, 보관 중인 약품 정보까지 살펴보세요.
        </p>
        <div className="care-counts">
          <span>8개 카테고리</span>
          <span>32개 증상</span>
          <span>출처 기반 건강 정보</span>
        </div>
      </header>
      <label className="care-search">
        <Icon name="search" />
        <span className="sr-only">증상 검색</span>
        <input
          type="search"
          value={query}
          onChange={search}
          placeholder="증상으로 찾아보세요. 예: 두통, 코막힘"
        />
      </label>
      <nav className="care-filters" aria-label="증상 카테고리">
        <Link
          to={`/care${query ? `?q=${encodeURIComponent(query)}` : ''}`}
          aria-current={!category ? 'page' : undefined}
        >
          전체
        </Link>
        {careCategories.map(
          // 기존 검색을 유지한 채 카테고리만 전환한다.
          function filter(name) {
            const next = new URLSearchParams(params);
            next.set('category', name);
            return (
              <Link
                key={name}
                to={`/care?${next}`}
                aria-current={category === name ? 'page' : undefined}
              >
                {name}
              </Link>
            );
          },
        )}
      </nav>
      <p className="care-result-count" role="status">
        {query ? `“${query}” 검색 결과` : '살펴볼 수 있는 증상'}{' '}
        <strong>{results.length}개</strong>
      </p>
      {!results.length && (
        <EmptyState
          title="검색한 증상을 찾지 못했어요"
          action={
            <button className="secondary-button" onClick={reset}>
              전체 증상 보기
            </button>
          }
        >
          다른 증상명으로 검색하거나 카테고리를 바꿔보세요.
        </EmptyState>
      )}
      <div className="care-category-grid">
        {careCategories.map(
          // 증상이 없는 카테고리는 검색 결과에서 숨긴다.
          function categorySection(name, index) {
            // 해당 카테고리의 증상만 묶는다.
            const items = results.filter(function belongs(item) {
              return item.category === name;
            });
            if (!items.length) return null;
            return (
              <section className="care-category-card" key={name}>
                <div className="care-category-heading">
                  <span className="care-category-number">
                    {String(index + 1).padStart(2, '0')}
                  </span>
                  <h2>{name}</h2>
                  <small>{items.length}개</small>
                </div>
                <div className="care-symptom-list">
                  {items.map(
                    // 상세 링크에 탐색 조건을 전달한다.
                    function symptom(item) {
                      return (
                        <Link
                          key={item.id}
                          to={`/care/${item.id}${params.size ? `?${params}` : ''}`}
                          className="care-symptom-link"
                        >
                          <span>{item.name}</span>
                          <Icon name="next" />
                        </Link>
                      );
                    },
                  )}
                </div>
              </section>
            );
          },
        )}
      </div>
      <Note tone="neutral">
        일반적인 건강 정보이며 진단이나 개인별 복용 지시가 아니에요.
        어린이·임산부·기저질환자·다른 약을 복용 중인 분은 의사 또는 약사와 먼저
        상담하세요.
      </Note>
    </div>
  );
}

// 증상별 대처·위험 신호와 근거가 확인된 보관 제품을 같은 화면에서 보여준다.
export function CareDetail({ kit }: { kit: Medkit }) {
  const { symptomId } = useParams();
  const [params] = useSearchParams();
  const heading = useRef<HTMLHeadingElement>(null);
  const [selectedMedicineId, setSelectedMedicine] = useState<string | null>(
    null,
  );
  // 재조회 후에도 선택 제품의 최신 사용정보를 보여준다.
  const selectedMedicine = kit.data.catalog_medicines.find(
    function selected(item) {
      return item.medicine_id === selectedMedicineId;
    },
  );
  // 제품정보 창을 닫고 원래 확인 버튼으로 초점을 돌린다.
  function closeMedicine() {
    setSelectedMedicine(null);
  }
  // 주소의 정확한 ID만 사용하고 다른 증상으로 대체하지 않는다.
  const guide = careGuides.find(function same(item) {
    return item.id === symptomId;
  });
  useEffect(
    // 상세 이동 때 제목을 읽고 위에서부터 내용을 볼 수 있게 한다.
    function focusGuide() {
      heading.current?.focus();
    },
    [symptomId],
  );
  const back = `/care${params.size ? `?${params}` : ''}`;
  if (!guide)
    return (
      <div className="standard-page">
        <EmptyState
          title="이 증상 가이드를 찾을 수 없어요"
          action={
            <Link className="secondary-button" to="/care">
              전체 증상 보기
            </Link>
          }
        >
          증상 목록에서 다시 선택해주세요.
        </EmptyState>
      </div>
    );
  const queryReady = kit.online && kit.status === 'ready' && !kit.demo;
  const inventoryNotice = careInventoryNotice(kit.data, queryReady);
  const unknown = new Map<string, Package>();
  for (const category of guide.relatedMedicineCategories) {
    const result = matchCareMedicines(kit.data, guide.id, category, queryReady);
    for (const item of result.unknown) unknown.set(item.package_id, item);
  }
  return (
    <div className="standard-page care-page care-detail">
      <Link className="care-back" to={back}>
        <Icon name="back" /> 증상 목록으로
      </Link>
      <header className="care-detail-heading">
        <Badge tone="green">{guide.category}</Badge>
        <h1 ref={heading} tabIndex={-1}>
          {guide.name}
        </h1>
        <p>{guide.description}</p>
      </header>
      {guide.urgentNotice && (
        <div className="care-urgent">
          <strong>즉시 진료가 필요한 신호</strong>
          <p>{guide.urgentNotice}</p>
        </div>
      )}
      <div className="care-health-notice">
        <Icon name="info" />
        <p>{guide.notice}</p>
      </div>
      <div className="care-detail-grid">
        <section aria-labelledby="care-steps-title">
          <div className="care-section-heading">
            <h2 id="care-steps-title">단계별 케어 가이드</h2>
            <span>{guide.steps.length}단계</span>
          </div>
          <ol className="care-steps">
            {guide.steps.map(
              // 단계의 비약물 설명을 먼저 보여주고 필요한 유형만 연결한다.
              function step(item, index) {
                return (
                  <li
                    className={`care-step care-step-${index % 3}`}
                    key={item.title}
                  >
                    <span className="care-step-number" aria-hidden="true">
                      {String(index + 1).padStart(2, '0')}
                    </span>
                    <div className="care-step-content">
                      <h3>{item.title}</h3>
                      <p>{item.description}</p>
                      {item.medicineCategories.map(
                        // 제품명 검색과 분리한 근거 기반 상태를 표시한다.
                        function medicines(category) {
                          const result = matchCareMedicines(
                            kit.data,
                            guide.id,
                            category,
                            queryReady,
                          );
                          return (
                            <div className="care-medicines" key={category}>
                              <div className="care-medicine-heading">
                                <strong>{medicineCategories[category]}</strong>
                                <Badge
                                  tone={statusTone(
                                    result.status,
                                    result.warningTone,
                                  )}
                                >
                                  {result.status}
                                </Badge>
                              </div>
                              <p className="care-match-reason">
                                {result.reason}
                              </p>
                              {result.matches.map(
                                // 검토 조건과 포장 상태를 숨기지 않고 상세 페이지로 연결한다.
                                function product(match) {
                                  return (
                                    <article
                                      className="care-product"
                                      key={match.item.package_id}
                                    >
                                      <span className="care-product-icon">
                                        <Icon name="pill" />
                                      </span>
                                      <div>
                                        <h4>{match.item.name}</h4>
                                        <Badge
                                          tone={statusTone(
                                            match.status,
                                            match.warningTone,
                                          )}
                                        >
                                          {match.status}
                                        </Badge>
                                        <p>{expiryLabel(match.item)}</p>
                                        {stockLabel(match.item) && (
                                          <p>{stockLabel(match.item)}</p>
                                        )}
                                        {match.reasons.map(
                                          // 미확인·주의 근거를 제품 가까이에 표시한다.
                                          function reason(text) {
                                            return <p key={text}>{text}</p>;
                                          },
                                        )}
                                        <details>
                                          <summary>허가 적용 조건 확인</summary>
                                          <ul>
                                            {match.conditions.map(
                                              // 개인별 적합성은 자동 판단하지 않는다.
                                              function condition(text) {
                                                return (
                                                  <li key={text}>{text}</li>
                                                );
                                              },
                                            )}
                                          </ul>
                                        </details>
                                        <Link
                                          className="secondary-button"
                                          to={`/packages/${encodeURIComponent(match.item.package_id)}`}
                                        >
                                          사용법 상세
                                          <span className="sr-only">
                                            {' '}
                                            · {match.item.name}
                                          </span>
                                        </Link>
                                      </div>
                                    </article>
                                  );
                                },
                              )}
                            </div>
                          );
                        },
                      )}
                    </div>
                  </li>
                );
              },
            )}
          </ol>
        </section>
        <aside className="care-side-note">
          <Icon name="pill" />
          <h2>우리집 약품, 확인부터</h2>
          <p>
            보유 표시는 허가 효능과 연결된 보관 정보예요. 지금 나에게 안전한
            약이라는 뜻은 아니에요.
          </p>
          <p>
            어린이·임산부·기저질환자·다른 약을 복용 중인 분은 의사 또는 약사와
            먼저 상담하세요.
          </p>
          <Link className="text-button" to="/">
            보관함 살펴보기 →
          </Link>
          {kit.demo && <Badge tone="amber">시연 정보 · 실제 보유 아님</Badge>}
          {inventoryNotice && (
            <p className="care-data-notice">{inventoryNotice}</p>
          )}
        </aside>
      </div>
      {unknown.size > 0 && (
        <details className="care-unverified">
          <summary>관련성 확인이 필요한 보관 품목 {unknown.size}개</summary>
          <p>효능 근거가 부족해 이 증상의 관련 약품으로 연결하지 않았어요.</p>
          <ul>
            {[...unknown.values()].map(
              // 미검증 품목은 관련 제품 카드와 구분해 상세 확인만 제공한다.
              function unverified(item) {
                return (
                  <li key={item.package_id}>
                    <Link
                      to={`/packages/${encodeURIComponent(item.package_id)}`}
                    >
                      {item.name} · 정보 확인 →
                    </Link>
                  </li>
                );
              },
            )}
          </ul>
        </details>
      )}
      {!kit.demo && kit.data.catalog_medicines.length > 0 && (
        <details className="care-unverified">
          <summary>
            인식된 제품 정보 {kit.data.catalog_medicines.length}개 · 보유 여부
            미확인
          </summary>
          <p>
            제품 인식 기록만 연결되어 있어요. 실제 보관 여부·유효기간·증상
            관련성은 확인되지 않았으며 아래 목록은 이 증상에 대한 추천이
            아니에요.
          </p>
          {!kit.data.catalog_complete && <p>제품 목록 일부만 조회되었어요.</p>}
          {(!kit.online || kit.status !== 'ready') && (
            <p>이전 조회 결과예요. 최신 정보 확인이 필요해요.</p>
          )}
          <ul>
            {kit.data.catalog_medicines.map(
              // 제품 캐시는 포장 카드와 구분하고 정보 열람만 제공한다.
              function catalog(medicine) {
                // 현재 선택한 제품의 허가정보를 연다.
                function openMedicine() {
                  setSelectedMedicine(medicine.medicine_id);
                }
                return (
                  <li key={medicine.medicine_id}>
                    <button className="text-button" onClick={openMedicine}>
                      {medicine.name} · 사용법 상세
                    </button>
                  </li>
                );
              },
            )}
          </ul>
        </details>
      )}
      {selectedMedicine && (
        <Dialog title="제품 사용정보" onClose={closeMedicine}>
          <Note>
            보관함 보유 여부와 실제 유효기간은 확인되지 않았어요. 일반 의약품
            정보이며 개인별 사용 권장이 아니에요.
          </Note>
          <MedicineUsage medicine={selectedMedicine} />
        </Dialog>
      )}
      <section className="care-warnings" aria-labelledby="care-warning-title">
        <h2 id="care-warning-title">이럴 때는 진료가 필요해요</h2>
        <ul>
          {guide.warningSigns.map(
            // 증상별로 확인한 진료 필요 신호를 모두 표시한다.
            function warning(text) {
              return <li key={text}>{text}</li>;
            },
          )}
        </ul>
      </section>
      <section className="care-sources">
        <h2>안내의 근거</h2>
        <p>
          출처 확인 {guide.sourceCheckedAt} · 일반 자가관리 정보와 국내 제품
          허가정보는 별도로 확인해요.
        </p>
        <ul>
          {guide.sources.map(
            // 검토한 원문을 새 탭에서 열고 현재 탐색은 보존한다.
            function source(item) {
              return (
                <li key={item.url}>
                  <a href={item.url} target="_blank" rel="noreferrer">
                    {item.title} ↗
                  </a>
                </li>
              );
            },
          )}
        </ul>
      </section>
    </div>
  );
}

// 상태의 뜻을 한국어 배지와 일관된 색으로 표현한다.
function statusTone(status: CareStatus, warningTone: 'amber' | 'red') {
  return status === '보유'
    ? 'green'
    : status === '사용 주의'
      ? warningTone
      : status === '확인 필요'
        ? 'amber'
        : 'neutral';
}
