# 진료용 복용 이력 보고서 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 기존 AWS 복용 확인 기록에서 기간과 포함 기록을 선택해 휴대폰용 약별 요약 보고서를 보여준다.

**Architecture:** 기존 React 라우터에 `/schedule/report`를 추가한다. 보관함 조회 검증은 기존 화면과 공유하고 수동 처방약은 앱의 기존 `useMedicationPlans`를 재사용한다. 보고서의 선택·요약은 읽기 전용이며 메모리에만 유지한다.

**Tech Stack:** 기존 React·TypeScript·Zod·CSS, Vitest, Playwright. 추가 의존성이나 AWS 리소스 없음.

**Spec:** `docs/superpowers/specs/2026-11-12-medication-history-report-design.md`

## Global Constraints

- 단일 사용자이며 기존 Y/N 확인·수동 기록 저장·재고 차감은 변경하지 않는다.
- 상단 기간은 최근 1개월 / 최근 3개월 / 최근 6개월 / 직접 설정, 기본 최근 1개월이다.
- 보관함 날짜는 ‘반출 날짜’, 수동 처방은 ‘일정 날짜’이며 실제 섭취 시각이라는 표현을 쓰지 않는다.
- 체크 해제는 보고서에만 적용한다. 로그·URL·영구 저장소·Service Worker 캐시에 복용 기록을 추가하지 않는다.
- 기존 비공개 접근과 시연 데이터 경계를 유지한다. 실제 환자 데이터를 테스트에 사용하지 않는다.
- 모든 새 함수·콜백 바로 위에 짧은 한국어 설명을 작성한다. 기존 스타일을 유지한다.
- 별도 브랜치·워크트리를 만들지 않고 현재 `main`에서 요청 범위만 변경한다.

## Review Focus

- KST 날짜 경계와 3월 말일의 전월 계산: 기록 누락·날짜 이동이 없어야 한다(작업 1).
- 일부 복용, 개별 약 정보 없는 묶음: 선택하지 않은 약·성분을 복용했다고 만들지 않는다(작업 1).
- 이름이 같지만 품목·함량이 다른 약: 같은 그룹으로 잘못 합치지 않는다(작업 1).
- 한 출처의 실패·인증 잠금·부분 재고: 불완전한 자료를 완전한 정상 보고서로 숨기지 않는다(작업 2).
- 기간 변경·재조회·선택 수정: 선택이 잘못된 다른 기록에 적용되거나 쓰기 요청이 발생하지 않는다(작업 2·3).

### 작업 1: 보고서 데이터 변환과 기존 조회 검증 재사용

**Files:**
- Create: `frontend/src/activity.ts`, `frontend/src/medicationReport.ts`, `frontend/src/medicationReport.test.ts`
- Modify: `frontend/src/OutboundHistory.tsx`의 기존 스키마·주소 검증 가져오기 부분

**Interfaces:**
- `activity.ts`: 기존 `activitySchema`, `eventSchema`, `activityUrl()` 및 `Activity`, `OutboundEvent` 타입을 의미 변경 없이 옮긴다.
- `medicationReport.ts`: `ReportRecord`, `ReportGroup`, `reportRange(months: 1 | 3 | 6, today: string)`, `buildReportRecords(...)`, `groupReportRecords(records: ReportRecord[])`를 제공한다.
- `ReportRecord`는 건별 안정 ID, 약별 그룹 키, 약 이름·함량·성분·양, 한국 날짜, 날짜 근거와 출처를 갖는다. 원본 확인 시각은 실제 섭취 시각으로 사용하지 않는다.
- 기존 `MedicationState`, `occurrencesOnDay`, `Package`, 명시적 `ProductLink`·`ProductDetail`을 소비하며 후보 제품은 사용하지 않는다.

- [x] `reportRange`의 1·3·6개월·전월 말일·연도 경계, `TAKEN`만 포함, 일부 복용 약 선택, 미복용/보류/미확인 제외, 약 정보 없는 묶음, KST 경계, 함량별 분리, 선택 제외 후 기간·횟수 재계산 테스트를 작성한다.
- [x] `npm test -- src/medicationReport.test.ts`를 실행해 새 기능 부재로 실패함을 확인한다.
- [x] 조회 검증을 의미 변경 없이 추출하고, 저장된 확인 회차가 있는 날짜만 기존 일정 생성기로 복원한다. 기록 이름만으로 성분·품목을 추정하지 않는다. 요약 횟수는 선택된 확인 기록 수다.
- [x] 같은 테스트를 통과시키고 기존 단위 테스트·타입 검사를 실행한다.

### 작업 2: 기존 웹 안의 기록 선택·요약 화면

**Files:**
- Create: `frontend/src/MedicationReportPage.tsx`, `frontend/src/medicationReport.css`
- Modify: `frontend/src/App.tsx`, `frontend/src/Schedule.tsx`, `frontend/src/useMedicationPlans.ts`, `frontend/src/sw.ts`
- Test: `frontend/e2e/report.spec.ts`, `frontend/playwright.config.ts`

**Interfaces:**
- `useMedicationPlans.readSnapshot()`은 기존 인증으로 최신 스냅샷만 읽고 원본 상태를 변경하지 않는다. 제품 조회 전후 revision과 상태가 다르면 보고서 생성을 보류한다.
- `MedicationReport({ kit, medications })`는 기존 `Medkit`·`MedicationPlansStore`를 소비하고 기록을 쓰지 않는다.
- 경로는 `/schedule/report`, 복약 탭에서 ‘진료용 보고서’ 링크로 진입한다.
- 작업 1의 타입·변환기를 사용한다. 조회는 `GET /activity`, 기존 수동 처방 조회와 명시적 품목 연결 조회를 재사용하고 `no-store`를 유지한다.

- [x] 합성 AWS 응답으로 진입·상단 네 기간·기간 직접 입력·체크 해제·약별 요약·상세 펼침·선택 수정·빈 선택·한 출처 실패를 검증하는 E2E를 작성한다. 모든 API 요청의 메서드를 기록해 쓰기 요청 0건을 단언한다.
- [x] 관련 E2E를 실행해 보고서 진입점 부재로 실패함을 확인한다.
- [x] 기존 화면 스타일을 재사용해 날짜별 체크 목록과 약별 요약을 구현한다. 오류·잠금·오프라인과 조회 중 상태를 표시하고 읽기 실패를 빈 기록으로 감추지 않는다. 기존 화면 복귀·메모리 시연 전환으로 실제 데이터가 잘못 섞이지 않도록 한다.
- [x] 같은 E2E를 데스크톱·모바일에서 통과시키고 화면 너비 초과가 없는지 검증한다. 새로고침과 기간 변경의 선택 초기화, 돌아가기의 선택 유지, 오래된 응답의 덮어쓰기 방지를 검사한다.

### 작업 3: 문서 정합성과 회귀 검증

**Files:**
- Modify: `plan.md`, `docs/CONTRACTS.md`, `frontend/README.md`의 보고서 관련 설명만

**Interfaces:**
- 새 보고서 API나 저장 모델은 없다. 기존 조회 위에 추가한 읽기 전용 기능과 기존 기록 삭제·변경 시 과거 데이터 복원 불가를 명시한다.

- [x] 문서를 현재 구현 범위로 갱신하고 `npm run typecheck`, `npm run lint`, `npm test`, `npm run build`를 실행한다.
- [x] 관련 보고서 E2E와 기존 activity·수동 복약 E2E를 실행한다. 실제 AWS 자원 변경·배포·실제 환자 자료는 사용하지 않는다.
- [x] 차이를 검토해 원본 저장 흐름·인증·재고·Service Worker가 불필요하게 변경되지 않았는지 확인하고 실행한 검증 결과와 배포 미실시를 구분해 보고한다.
- [x] 저장소 정책에 따라 검증한 요청 범위만 `main`에 커밋한다.
- [ ] 사용자에게 push·Amplify 반영 여부를 별도로 확인한다. 실제 배포는 이번 로컬 검증에 포함하지 않는다.

## 실행·검증 결과 (2026-11-12)

- 기존 시스템 Node 22.1 대신 프로젝트 요구 버전을 충족하는 임시 Node 22.23.5를 사용했다. 기존 lockfile의 의존성만 설치했고 package.json/lockfile은 변경하지 않았다.
- 신규 기능 부재의 단위/E2E 실패를 확인한 뒤 구현했다. 최종 단위 테스트 **77개 통과**(기존 61 + 보고서 16), 보고서 E2E **18개 통과**(데스크톱·모바일), 타입 검사·린트·변경 소스 포맷·빌드·`git diff --check` 통과.
- 기존 보관함 activity E2E **4개 통과**, 기본 홈/케어/복약/반응형/오프라인 앱 셸 E2E **13개 통과·1개 조건부 제외**.
- 기존 수동 복약 공개 모드 E2E는 **16개 통과·12개 조건부 제외·2개 실패**, 비공개 모드는 **10개 통과·18개 조건부 제외·2개 실패**다. 실패 항목은 `완전 공개 시연 모드 / 비밀번호 없이 등록·새로고침·다른 페이지 공유·삭제`와 `공통 비밀번호 모드 / AWS 처방 묶음 등록부터 다른 페이지 공유와 삭제까지`의 데스크톱·모바일이다. 두 시나리오 모두 기존 콘솔 404 단언 실패이며, 변경 전 `c361483`의 별도 임시 복사본에서도 동일하게 재현했다. 요청과 무관한 기존 테스트/제품 연결 동작은 변경하지 않았다.
- 독립 코드 검토의 P2 다섯 건을 실패 테스트로 재현해 보완했다: 두 출처 재조회 완료 대기, 오프라인 기간 변경 시 과거 미리보기 종료, 선택 수정 시 보관함도 재조회, 제품 연결 전후 스냅샷 일관성, 같은 그룹의 확인된 성분 누락 방지.
- 합성 화면을 실제 Chrome으로 캡처해 모바일 선택·요약을 확인했고 320px 너비 가로 넘침이 없음을 확인했다. 실제 iPhone·Pi·실제 환자 자료·운영 AWS·Amplify 배포는 이 검증에 포함하지 않았다.
- 빌드는 성공했으나 기존 의존성의 Rollup 주석 및 500kB 청크 경고는 남아 있다. 의존성 교체나 무관한 번들 재구성은 하지 않았다.
