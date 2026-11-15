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
  buildReportItems,
  reportRange,
  validReportRange,
  type ReportItem,
} from './medicationReport';
import type { Medkit } from './useMedkit';
import type { MedicationPlansStore } from './useMedicationPlans';
import MedicationAccess from './MedicationAccess';
import './medicationReport.css';

type ReportSource = {
  activity: Activity;
  state: MedicationState;
  packages: Package[];
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
  const baseUrl = activityUrl();
  const { status: medicationStatus, readSnapshot } = medications;

  useEffect(
    function loadReportSources() {
      setStatus('loading');
      setError('');
      if (kit.demo || !kit.online || medicationStatus !== 'ready') return;
      if (!baseUrl) {
        setStatus('error');
        setError('보관함 반출 기록 API 연결을 확인해주세요.');
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
          const [response, snapshot] = await Promise.all([
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
          const packages = kit.status === 'ready' ? kit.data.packages : [];
          setSource({ activity, state: snapshot.state, packages });
          setStatus('ready');
        } catch {
          if (!active) return;
          setStatus('error');
          setError(
            '약품 목록을 모두 불러오지 못했어요. 복약 화면에서 연결과 잠금을 확인하거나 새로고침해주세요.',
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
      baseUrl,
      medicationStatus,
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
        onRefresh={refreshSources}
      >
        <div className="page-heading">
          <div>
            <h1>진료용 보고서</h1>
            <p>처방과 반출 약품을 골라 진료 시 보여주세요.</p>
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

// 처방 카드·반출 약품 선택과 선택 시점의 읽기 전용 보고서를 전환한다.
function ReportSelection({
  source,
  ready,
  onRefresh,
  children,
}: {
  source: ReportSource | null;
  ready: boolean;
  onRefresh: () => void;
  children: ReactNode;
}) {
  const [preset, setPreset] = useState<1 | 3 | 6 | 'custom'>(1);
  const [range, setRange] = useState(
    // 첫 진입은 한국 날짜의 최근 한 달을 사용한다.
    function initialRange() {
      return reportRange(1);
    },
  );
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [preview, setPreview] = useState(false);
  const [previewRange, setPreviewRange] = useState(range);
  const [previewItems, setPreviewItems] = useState<ReportItem[]>([]);
  const valid = validReportRange(range.start, range.end);
  const items = useMemo(
    // 날짜별 복용 회차를 만들지 않고 등록 처방과 반출 약품만 계산한다.
    function reportItems() {
      return source && ready && valid
        ? buildReportItems(
            source.activity,
            source.state,
            source.packages,
            range,
          )
        : [];
    },
    [source, ready, valid, range],
  );
  const selected = items.filter(
    // 카드 제외는 보고서 메모리에만 적용하고 원본은 변경하지 않는다.
    function included(item) {
      return !itemIsExcluded(item, excluded);
    },
  );
  const showingReport = preview && ready;
  const displayed = showingReport ? previewItems : items;

  // 기간 변경은 이전 카드 제외 선택을 초기화한다.
  function choosePeriod(months: 1 | 3 | 6 | 'custom') {
    setPreset(months);
    setPreview(false);
    setExcluded(new Set());
    if (months !== 'custom') setRange(reportRange(months, dayKey()));
  }
  // 직접 입력한 시작일을 적용하고 선택을 초기화한다.
  function changeStart(event: ChangeEvent<HTMLInputElement>) {
    setPreview(false);
    setRange({ ...range, start: event.target.value });
    setExcluded(new Set());
  }
  // 직접 입력한 종료일을 적용하고 선택을 초기화한다.
  function changeEnd(event: ChangeEvent<HTMLInputElement>) {
    setPreview(false);
    setRange({ ...range, end: event.target.value });
    setExcluded(new Set());
  }
  // 선택한 카드와 조회 기간을 고정해 날짜 상세 없는 보고서로 보여준다.
  function showReport() {
    if (!ready || !valid || !selected.length) return;
    setPreviewRange({ ...range });
    setPreviewItems(selected);
    setPreview(true);
    window.scrollTo(0, 0);
  }
  // 기간과 제외한 카드를 유지하고 양쪽 출처를 다시 읽는다.
  function editSelection() {
    setPreview(false);
    onRefresh();
    window.scrollTo(0, 0);
  }

  const content = (['prescription', 'cabinet'] as const).map(
    // 두 화면에서 같은 분류·카드 내용을 재사용하고 보고서의 빈 분류는 숨긴다.
    function category(kind) {
      const entries = displayed.filter(
        // 등록 처방과 반출 약품을 서로 합치지 않는다.
        function sameKind(item) {
          return item.kind === kind;
        },
      );
      if (showingReport && !entries.length) return null;
      const title =
        kind === 'prescription' ? '복용 중인 처방약' : '약통에서 반출된 약품';
      return (
        <section className="report-category" key={kind} aria-label={title}>
          <h3 className="report-category-title">{title}</h3>
          {entries.length === 0 && (
            <p className="report-caption">표시할 항목이 없어요.</p>
          )}
          {entries.map(
            // 처방 하나 또는 같은 약품의 반복 반출 전체를 한 선택 단위로 제공한다.
            function card(item) {
              if (showingReport)
                return (
                  <article
                    className="report-card"
                    key={item.id}
                    aria-label={item.name}
                  >
                    <ReportItemContent item={item} />
                  </article>
                );
              // 해당 처방 카드 또는 반출 약품만 보고서 포함 대상에서 제외한다.
              function toggle() {
                setExcluded(
                  // 기존 제외 목록을 변경하지 않고 새 선택 상태로 교체한다.
                  function change(previous) {
                    const next = new Set(previous);
                    const removingExclusion = itemIsExcluded(item, previous);
                    for (const id of item.selectionIds) {
                      if (removingExclusion) next.delete(id);
                      else next.add(id);
                    }
                    return next;
                  },
                );
              }
              return (
                <label className="report-card report-record" key={item.id}>
                  <input
                    type="checkbox"
                    checked={!itemIsExcluded(item, excluded)}
                    onChange={toggle}
                    aria-label={item.name}
                  />
                  <ReportItemContent item={item} />
                </label>
              );
            },
          )}
        </section>
      );
    },
  );

  return (
    <>
      {showingReport ? (
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
      {showingReport ? (
        <section className="report-document" aria-label="진료용 약품 보고서">
          <h2>진료용 약품 보고서</h2>
          <p className="report-caption">
            조회 기간 {previewRange.start} ~ {previewRange.end}
          </p>
          <p className="report-caption">
            사용자 등록 처방과 보관함 반출 이력 참고자료이며 실제 복용을 확인한
            자료는 아닙니다. 일수는 등록한 처방 기간 기준입니다.
          </p>
          {content}
        </section>
      ) : (
        <>
          <section className="report-period" aria-label="조회 기간">
            <h2>조회 기간</h2>
            <div className="report-presets">
              {([1, 3, 6, 'custom'] as const).map(
                // 동일한 크기의 기간 버튼으로 기존 조회 방식을 유지한다.
                function period(months) {
                  // 선택한 기간으로 목록을 갱신한다.
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
                  <input
                    type="date"
                    value={range.start}
                    onChange={changeStart}
                  />
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
            aria-label="보고서 항목 선택"
            aria-busy={!ready}
          >
            <div className="report-list-heading">
              <h2>보여줄 항목 선택</h2>
              <span>
                {selected.length} / {items.length}개 선택
              </span>
            </div>
            <p className="report-caption">
              제외할 처방이나 약품의 체크를 해제하세요. 원본 기록은 바뀌지
              않아요.
            </p>
            {ready && valid && items.length === 0 && (
              <p className="report-empty">
                선택한 기간에 표시할 항목이 없어요.
              </p>
            )}
            {content}
          </section>
          <div className="report-footer">
            <span>{selected.length}개 선택</span>
            <button
              type="button"
              className="primary-button"
              disabled={!ready || !valid || !selected.length}
              onClick={showReport}
            >
              선택 완료 · 보고서 보기
            </button>
          </div>
        </>
      )}
    </>
  );
}

// 선택과 보고서가 같은 처방명·등록 기간·포함 약 이름을 표시한다.
function ReportItemContent({ item }: { item: ReportItem }) {
  return (
    <span className="report-card-content">
      <strong>
        {item.name}
        {item.duration && <span> · {item.duration}</span>}
      </strong>
      {item.kind === 'prescription' && (
        <small>
          포함 약:{' '}
          {item.medicines.length
            ? item.medicines.join(', ')
            : '개별 약 정보 없음'}
        </small>
      )}
    </span>
  );
}

// 제품·포장 표시 묶음이 달라져도 같은 원본 포장의 제외 여부를 유지한다.
function itemIsExcluded(
  item: ReportItem,
  excluded: ReadonlySet<string>,
): boolean {
  return item.selectionIds.some(
    // 하나라도 제외한 원본을 합쳐진 카드에 자동으로 다시 포함하지 않는다.
    function hasExcluded(id) {
      return excluded.has(id);
    },
  );
}
