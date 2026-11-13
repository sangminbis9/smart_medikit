import {
  useEffect,
  useMemo,
  useState,
  type ChangeEvent,
  type ReactNode,
} from 'react';
import { Link } from 'react-router-dom';
import { activitySchema, activityUrl, type Activity } from './activity';
import { dayKey, type Package } from './data';
import type { MedicationState } from './medicationPlans';
import {
  buildReportRecords,
  groupReportCategories,
  type ReportCategory,
  reportRange,
  validReportRange,
  type ReportProducts,
  type ReportRecord,
} from './medicationReport';
import type { Medkit } from './useMedkit';
import type { MedicationPlansStore } from './useMedicationPlans';
import MedicationAccess from './MedicationAccess';
import './medicationReport.css';

type ReportSource = {
  activity: Activity;
  state: MedicationState;
  packages: Package[];
  products: ReportProducts;
};

// 서버 기록은 읽기만 하며 기존 인증·시연 경계를 그대로 적용한다.
export default function MedicationReport({
  kit,
  medications,
}: {
  kit: Medkit;
  medications: MedicationPlansStore;
}) {
  const [attempt, setAttempt] = useState(0);
  const [selectionVersion, setSelectionVersion] = useState(0);
  const [source, setSource] = useState<ReportSource | null>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>(
    'loading',
  );
  const [error, setError] = useState('');
  const [productWarning, setProductWarning] = useState(false);
  const baseUrl = activityUrl();
  const {
    status: medicationStatus,
    products: productSource,
    readSnapshot,
  } = medications;

  useEffect(
    function loadReportSources() {
      setStatus('loading');
      setError('');
      if (kit.demo || !kit.online || medicationStatus !== 'ready') return;
      if (!baseUrl) {
        setStatus('error');
        setError('보관함 복용 기록 API 연결을 확인해주세요.');
        return;
      }
      const controller = new AbortController();
      let active = true;
      const timeout = window.setTimeout(
        // 보관함 응답이 멈추면 제한 시간 뒤 요청을 끝낸다.
        function abort() {
          controller.abort();
        },
        12000,
      );
      // 두 출처의 최신 기록을 모두 읽은 뒤에만 보고서 선택을 활성화한다.
      async function load() {
        try {
          const [response, before] = await Promise.all([
            fetch(`${baseUrl}/activity`, {
              signal: controller.signal,
              cache: 'no-store',
              headers: { Accept: 'application/json' },
            }),
            readSnapshot(),
          ]);
          if (!response.ok) throw new Error('SOURCE_UNAVAILABLE');
          const activity = activitySchema.parse(await response.json());
          if (!active) return;
          const products: ReportProducts = { links: [], details: {} };
          let missingProducts = false;
          if (before.state.plans.length) {
            try {
              products.links = await productSource.links();
              const codes = new Set<string>();
              for (const link of products.links) codes.add(link.productCode);
              // 선택적인 제품 정보만 실패하면 확인된 복용 기록은 유지한다.
              for (const code of codes) {
                if (!active) return;
                try {
                  products.details[code] = await productSource.detail(code);
                } catch {
                  missingProducts = true;
                }
              }
            } catch {
              missingProducts = true;
            }
          }
          if (!active) return;
          const after = await readSnapshot();
          // 제품 조회 전후 처방이 바뀌면 과거 약과 새 성분을 잘못 결합하지 않는다.
          if (
            before.revision !== after.revision ||
            JSON.stringify(before.state) !== JSON.stringify(after.state)
          )
            throw new Error('RECORD_CHANGED');
          if (!active) return;
          const packages = kit.status === 'ready' ? kit.data.packages : [];
          setSource({ activity, state: before.state, packages, products });
          setProductWarning(
            missingProducts ||
              kit.status !== 'ready' ||
              kit.data.inventory_status !== 'CONFIRMED',
          );
          setStatus('ready');
        } catch (cause) {
          if (!active) return;
          setStatus('error');
          setError(
            cause instanceof Error && cause.message === 'RECORD_CHANGED'
              ? '조회 중 복용 기록이 변경됐어요. 새로고침 후 다시 선택해주세요.'
              : '복용 기록을 모두 불러오지 못했어요. 복약 화면에서 연결과 잠금을 확인하거나 새로고침해주세요.',
          );
        } finally {
          window.clearTimeout(timeout);
        }
      }
      void load();
      // 화면 이동·재시도 이후 도착한 과거 응답을 적용하지 않는다.
      return function cleanup() {
        active = false;
        controller.abort();
        window.clearTimeout(timeout);
      };
    },
    [
      kit.demo,
      kit.online,
      kit.status,
      kit.data.packages,
      kit.data.inventory_status,
      baseUrl,
      medicationStatus,
      productSource,
      readSnapshot,
      attempt,
    ],
  );

  const ready =
    !kit.demo &&
    kit.online &&
    medications.status === 'ready' &&
    status === 'ready';
  // 선택 수정은 양쪽 최신 기록을 재조회하되 기존 체크는 유지한다.
  function refreshSources() {
    setStatus('loading');
    setAttempt(function next(previous) {
      return previous + 1;
    });
  }
  // 명시적 새로고침에서는 기간과 체크도 초기화한다.
  function retry() {
    setSelectionVersion(function next(previous) {
      return previous + 1;
    });
    refreshSources();
    void kit.refresh();
  }
  return (
    <div className="standard-page report-page">
      <ReportSelection
        key={`${kit.demo}:${selectionVersion}`}
        source={source}
        ready={ready}
        productWarning={productWarning}
        onRefresh={refreshSources}
      >
        <div className="page-heading">
          <div>
            <h1>진료용 보고서</h1>
            <p>보여줄 기록을 고르고 약별로 정리해요.</p>
          </div>
          <button type="button" className="soft-button" onClick={retry}>
            새로고침
          </button>
        </div>
        {kit.demo && (
          <p className="report-notice" role="status">
            진료용 보고서는 시연 모드를 종료한 뒤 AWS에 저장된 기록으로
            만들어요.
          </p>
        )}
        {!kit.online && (
          <p className="report-notice" role="alert">
            인터넷 연결을 확인한 뒤 기록을 다시 불러와주세요.
          </p>
        )}
        {medications.status !== 'ready' && !kit.demo && (
          <MedicationAccess
            status={medications.status}
            error={medications.error}
            busy={medications.busy}
            onUnlock={medications.unlock}
            onReload={medications.reload}
            publicAccess={medications.publicAccess}
          />
        )}
        {status === 'error' && !kit.demo && kit.online && (
          <p className="report-notice" role="alert">
            {error}
          </p>
        )}
        {!ready && (
          <p className="caption" role="status">
            두 종류의 기록을 모두 불러온 뒤 보고서를 볼 수 있어요.
          </p>
        )}
      </ReportSelection>
    </div>
  );
}

// 날짜별 건 선택과 선택한 약별 읽기 전용 보고서를 같은 화면에서 전환한다.
function ReportSelection({
  source,
  ready,
  productWarning,
  onRefresh,
  children,
}: {
  source: ReportSource | null;
  ready: boolean;
  productWarning: boolean;
  onRefresh: () => void;
  children: ReactNode;
}) {
  const [preset, setPreset] = useState<1 | 3 | 6 | 'custom'>(1);
  const [range, setRange] = useState(
    // 첫 진입은 한국 날짜의 최근 1개월을 사용한다.
    function initialRange() {
      return reportRange(1);
    },
  );
  const [now, setNow] = useState(Date.now);
  useEffect(function observeScheduleClock() {
    // 예정 시각이 지나거나 화면으로 복귀하면 서버 쓰기 없이 추정 목록만 갱신한다.
    function refreshClock() {
      if (!document.hidden) setNow(Date.now());
    }
    const timer = window.setInterval(refreshClock, 30000);
    document.addEventListener('visibilitychange', refreshClock);
    // 화면을 떠나면 타이머와 이벤트를 정리한다.
    return function cleanup() {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', refreshClock);
    };
  }, []);
  const records = useMemo(
    // 표시 기간의 일정만 생성하며 확인 기록과 추정을 같은 회차에 중복 추가하지 않는다.
    function recordsForPeriod() {
      return source
        ? buildReportRecords(
            source.activity,
            source.state,
            source.packages,
            source.products,
            { ...range, now: new Date(now) },
          )
        : [];
    },
    [source, range, now],
  );
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [preview, setPreview] = useState(false);
  const [previewRange, setPreviewRange] = useState(range);
  const [previewRecords, setPreviewRecords] = useState<ReportRecord[]>([]);
  const valid = validReportRange(range.start, range.end);
  const visible =
    ready && valid
      ? records.filter(
          // 기간 양 끝의 기록도 포함한다.
          function inRange(item) {
            return item.date >= range.start && item.date <= range.end;
          },
        )
      : [];
  const selected = visible.filter(
    // 체크 해제는 이 화면 메모리에서만 적용한다.
    function included(item) {
      return !excluded.has(item.id);
    },
  );
  // 날짜를 먼저 나눈 뒤 각 날짜 안에서 기존 처방·약품 분류를 재사용한다.
  const recordsByDate = new Map<string, ReportRecord[]>();
  for (const item of visible) {
    const dayRecords = recordsByDate.get(item.date);
    if (dayRecords) dayRecords.push(item);
    else recordsByDate.set(item.date, [item]);
  }
  const categories = groupReportCategories(previewRecords);
  const groupCount = categories.reduce(
    // 처방 카테고리 수가 아니라 실제 표시하는 약별 항목 수를 집계한다.
    function count(total, category) {
      return total + category.groups.length;
    },
    0,
  );
  // 기간을 바꾸면 새 기간의 모든 기록을 기본 선택한다.
  function choosePeriod(months: 1 | 3 | 6 | 'custom') {
    setPreset(months);
    setPreview(false);
    setExcluded(new Set());
    if (months !== 'custom') setRange(reportRange(months, dayKey()));
  }
  // 직접 설정한 시작일을 적용한다.
  function changeStart(event: ChangeEvent<HTMLInputElement>) {
    setPreview(false);
    setRange({ ...range, start: event.target.value });
    setExcluded(new Set());
  }
  // 직접 설정한 종료일을 적용한다.
  function changeEnd(event: ChangeEvent<HTMLInputElement>) {
    setPreview(false);
    setRange({ ...range, end: event.target.value });
    setExcluded(new Set());
  }
  // 원본 저장 없이 선택된 기록만 진료용 표시로 전환한다.
  function showReport() {
    if (ready && valid && selected.length) {
      // 선택 완료 이후의 백그라운드 변경을 검토한 보고서에 자동 포함하지 않는다.
      setPreviewRange({ ...range });
      setPreviewRecords(selected);
      setPreview(true);
      window.scrollTo(0, 0);
    }
  }
  // 날짜와 체크 상태를 유지한 채 기록 선택으로 돌아간다.
  function editSelection() {
    setPreview(false);
    onRefresh();
    window.scrollTo(0, 0);
  }

  // 보고서에서는 선택을 수정하고 선택 화면에서는 복약 화면으로 돌아간다.
  const header = (
    <>
      {preview && ready ? (
        <button
          type="button"
          className="report-back"
          aria-label="기록 선택으로 돌아가기"
          title="기록 선택으로 돌아가기"
          onClick={editSelection}
        >
          <span aria-hidden="true">←</span>
        </button>
      ) : (
        <Link
          to="/schedule"
          className="report-back"
          aria-label="복약으로 돌아가기"
          title="복약으로 돌아가기"
        >
          <span aria-hidden="true">←</span>
        </Link>
      )}
      {children}
    </>
  );

  if (preview && ready)
    return (
      <>
        {header}
        <section className="report-document" aria-label="진료용 복용 이력">
          <div className="report-document-heading">
            <div>
              <h2>진료용 복용 이력</h2>
              <p>
                조회 기간 {previewRange.start} ~ {previewRange.end}
              </p>
            </div>
          </div>
          <p className="report-totals">
            약별 요약 {groupCount}개 · 선택한 기록 {previewRecords.length}건
          </p>
          <p className="report-caption">
            선택 완료 시점의 사용자 확인·일정 기반 추정 기록입니다. 보관함
            기록은 반출 날짜, 수동 처방약은 일정 날짜 기준이며 추정 항목은 실제
            복용 확인이 아닙니다.
          </p>
          {productWarning && (
            <p className="report-caption">
              일부 제품·성분 정보는 확인되지 않아 표시하지 않았어요.
            </p>
          )}
          <div className="report-groups">
            {categories.map(
              // 수동 처방만 카테고리 제목을 붙이고 개별 보관함 약은 그대로 표시한다.
              function category(category) {
                return (
                  <ReportCategorySection
                    key={category.key}
                    category={category}
                    heading="h3"
                  >
                    {category.groups.map(
                      // 동일 약의 확인 횟수와 선택된 날짜별 내역을 함께 보여준다.
                      function group(item) {
                        const Heading = category.title === null ? 'h3' : 'h4';
                        return (
                          <article
                            className="report-drug"
                            key={item.key}
                            aria-label={[item.name, item.strength]
                              .filter(Boolean)
                              .join(' ')}
                          >
                            <div className="report-drug-heading">
                              <Heading>
                                {item.name}{' '}
                                {item.strength && <span>{item.strength}</span>}
                              </Heading>
                              <strong>
                                {item.scheduledCount ? '기록' : '확인'}{' '}
                                {item.count}회
                              </strong>
                            </div>
                            {item.scheduledCount > 0 && (
                              <p className="report-caption">
                                사용자 확인 {item.confirmedCount}회 / 일정 기반
                                추정 {item.scheduledCount}회
                              </p>
                            )}
                            {item.ingredients.length > 0 && (
                              <p className="report-ingredients">
                                {item.ingredients.join(', ')}
                              </p>
                            )}
                            <p className="report-drug-period">
                              기록 기간 {item.start} ~ {item.end}
                            </p>
                            <details>
                              <summary>날짜별 내역</summary>
                              <ul className="report-detail-list">
                                {item.records.map(
                                  // 확인 시각 대신 반출·일정 날짜와 저장된 양만 표시한다.
                                  function detail(record) {
                                    return (
                                      <li key={record.id}>
                                        <span>
                                          <span>{record.dateKind}</span>{' '}
                                          <time dateTime={record.date}>
                                            {record.date}
                                          </time>
                                          {record.time && (
                                            <span> · {record.time} 예정</span>
                                          )}
                                          {record.certainty === 'scheduled' && (
                                            <span className="report-estimate">
                                              일정 기반 추정
                                            </span>
                                          )}
                                        </span>
                                        <span>
                                          {record.incomplete
                                            ? '개별 약 정보 없음'
                                            : record.amount
                                              ? `${record.dateKind === '일정 날짜' ? '입력 복용량' : '확인 수량'} ${record.amount}`
                                              : '수량 정보 없음'}
                                        </span>
                                      </li>
                                    );
                                  },
                                )}
                              </ul>
                            </details>
                          </article>
                        );
                      },
                    )}
                  </ReportCategorySection>
                );
              },
            )}
          </div>
        </section>
      </>
    );

  return (
    <>
      {header}
      <section className="report-period" aria-label="조회 기간">
        <h2>조회 기간</h2>
        <div className="report-presets">
          {([1, 3, 6, 'custom'] as const).map(
            // 기간 버튼을 화면 상단에 같은 크기로 제공한다.
            function period(months) {
              // 현재 기간을 바꾸고 새 기간의 선택을 초기화한다.
              function choose() {
                choosePeriod(months);
              }
              return (
                <button
                  key={months}
                  type="button"
                  aria-pressed={preset === months}
                  onClick={choose}
                >
                  {months === 'custom' ? '직접 설정' : `최근 ${months}개월`}
                </button>
              );
            },
          )}
        </div>
        {preset === 'custom' ? (
          <div className="report-date-inputs">
            <label>
              시작일
              <input type="date" value={range.start} onChange={changeStart} />
            </label>
            <label>
              종료일
              <input type="date" value={range.end} onChange={changeEnd} />
            </label>
          </div>
        ) : (
          <p className="report-caption">
            {range.start} ~ {range.end}
          </p>
        )}
        {!valid && (
          <p className="form-error" role="alert">
            조회 기간의 시작일과 종료일을 올바르게 입력해주세요.
          </p>
        )}
      </section>
      <section
        className="report-records"
        aria-label="복용 기록 선택"
        aria-busy={!ready}
      >
        <div className="report-list-heading">
          <h2>보여줄 기록 선택</h2>
          <span>
            {selected.length} / {visible.length}건 선택
          </span>
        </div>
        <p className="report-caption">
          제외할 기록의 체크를 해제하세요. 원본 복용 기록은 바뀌지 않아요.
        </p>
        {ready && valid && visible.length === 0 && (
          <p className="report-empty">선택한 기간에 복용 확인 기록이 없어요.</p>
        )}
        {[...recordsByDate]
          .sort(
            // 날짜 최상위 구분은 기존 기록 목록과 같은 최신순으로 보여준다.
            function newestDate([left], [right]) {
              return right.localeCompare(left);
            },
          )
          .map(
            // 같은 날짜의 처방 묶음과 개별 약을 하나의 날짜 영역에 표시한다.
            function day([date, dayRecords]) {
              return (
                <section
                  className="report-selection-day"
                  key={date}
                  aria-label={`${date} 기록`}
                >
                  <h3 className="report-selection-date">
                    <time dateTime={date}>{date}</time>
                  </h3>
                  {groupReportCategories(dayRecords).map(
                    // 같은 날짜의 수동 처방만 묶고 보관함 약은 개별 표시한다.
                    function category(category) {
                      return (
                        <ReportCategorySection
                          key={category.key}
                          category={category}
                          heading="h4"
                        >
                          {category.groups.map(
                            // 날짜 안의 개별 약 아래 건별 체크 항목을 표시한다.
                            function drug(group) {
                              const Heading =
                                category.title === null ? 'h4' : 'h5';
                              return (
                                <div
                                  className="report-selection-drug"
                                  key={group.key}
                                >
                                  <Heading>
                                    {group.records[0].incomplete
                                      ? '개별 약 정보 없음'
                                      : `${group.name} ${group.strength}`.trim()}
                                  </Heading>
                                  <ul>
                                    {group.records.map(
                                      // 기존 복용 기록 한 건의 포함 여부만 변경한다.
                                      function record(item) {
                                        // 체크 상태는 원본과 분리된 화면 메모리에만 보존한다.
                                        function toggle() {
                                          setExcluded(
                                            function change(previous) {
                                              const next = new Set(previous);
                                              if (next.has(item.id))
                                                next.delete(item.id);
                                              else next.add(item.id);
                                              return next;
                                            },
                                          );
                                        }
                                        return (
                                          <li key={item.id}>
                                            <label className="report-record">
                                              <input
                                                type="checkbox"
                                                checked={!excluded.has(item.id)}
                                                onChange={toggle}
                                                aria-label={`${item.date} ${item.planTitle ? `${item.planTitle} ` : ''}${item.name} ${item.strength} ${item.dateKind}${item.time ? ` ${item.time} 예정` : ''} ${item.certainty === 'scheduled' ? '일정 기반 추정' : '사용자 확인'}${item.amount ? ` ${item.amount}` : ''}`}
                                              />
                                              <span>
                                                <strong>
                                                  <time dateTime={item.date}>
                                                    {item.date}
                                                  </time>
                                                  {item.time &&
                                                    ` · ${item.time} 예정`}
                                                </strong>
                                                <small>
                                                  {item.dateKind}
                                                  {item.incomplete
                                                    ? ' · 개별 약 정보 없음'
                                                    : item.amount
                                                      ? ` · ${item.dateKind === '일정 날짜' ? '입력 복용량' : '확인 수량'} ${item.amount}`
                                                      : ' · 수량 정보 없음'}
                                                </small>
                                                {item.certainty ===
                                                  'scheduled' && (
                                                  <span className="report-estimate">
                                                    일정 기반 추정
                                                  </span>
                                                )}
                                              </span>
                                            </label>
                                          </li>
                                        );
                                      },
                                    )}
                                  </ul>
                                </div>
                              );
                            },
                          )}
                        </ReportCategorySection>
                      );
                    },
                  )}
                </section>
              );
            },
          )}
      </section>
      <div className="report-footer">
        <span>{selected.length}건 선택</span>
        <button
          type="button"
          className="primary-button"
          disabled={!ready || !valid || selected.length === 0}
          onClick={showReport}
        >
          선택 완료 · 보고서 보기
        </button>
      </div>
    </>
  );
}

// 처방만 제목 있는 영역으로 감싸고 보관함 약에는 카테고리 자체를 만들지 않는다.
function ReportCategorySection({
  category,
  children,
  heading: Heading,
}: {
  category: ReportCategory;
  children: ReactNode;
  heading: 'h3' | 'h4';
}) {
  if (category.title === null) return <>{children}</>;
  return (
    <section
      className="report-category"
      aria-label={`${category.title} 처방 묶음`}
    >
      <Heading className="report-category-title">{category.title}</Heading>
      {children}
    </section>
  );
}
