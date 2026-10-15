# MEDKIT 웹

Figma의 모바일·데스크톱 시안을 구현한 **React + TypeScript + Tailwind CSS + Three.js** 초기 프론트엔드입니다. API·약품 DB·보관함 치수가 없어도 실행됩니다. 기본값은 빈 보관함이며, **시연 데이터 보기**를 눌러야 가상 포장이 표시됩니다.

## 실행

Node.js **22.12 이상(22 LTS 권장)**과 npm을 사용합니다. 저장소 루트에서:

```sh
cd frontend
npm ci
npm run dev
```

터미널에 표시된 로컬 주소로 접속합니다. 설정 없이도 홈·검색·상세·복약 달력·알림·설치 안내를 열 수 있습니다. `npm run dev`에서는 Service Worker를 등록하지 않으며, 설치·오프라인은 배포 빌드에서 확인합니다.

```sh
npm run build
npm run preview
```

## 사용자가 나중에 입력할 값

**빈 화면·시연 모드 배포에는 아래 값이 하나도 필요하지 않습니다.** 로컬에서는 `.env.example`을 `.env.local`로 복사하고, Amplify에서는 해당 브랜치의 환경변수에 입력 후 다시 빌드합니다.

| 값                                 | 어디에 입력          | 비어 있을 때                                                                        |
| ---------------------------------- | -------------------- | ----------------------------------------------------------------------------------- |
| 내부 가로(mm)                      | `VITE_BOX_WIDTH`     | 아래 3개가 모두 없으면 ‘치수 미설정·예시 비율’                                      |
| 내부 세로(mm)                      | `VITE_BOX_DEPTH`     | 실제 약품 좌표를 임의 생성하지 않음                                                 |
| 내부 높이(mm)                      | `VITE_BOX_HEIGHT`    | 세 치수 모두 양수여야 함. 서버 `box.dimensions`가 있으면 서버 값 우선               |
| 보관함 ID                          | `VITE_BOX_ID`        | API를 연결할 때만 필수. 서버/Pi와 같은 ID                                           |
| 조회 API 기본 주소                 | `VITE_API_URL`       | 예: `https://…execute-api…/prod`. 없으면 네트워크 조회 없이 빈 상태                 |
| WebSocket 주소                     | `VITE_WS_URL`        | `wss://…`. 없으면 화면 복귀·수동 새로고침으로 조회                                  |
| 약품 DB/API 및 DUR 데이터·인증정보 | **백엔드 담당 영역** | 웹에서 임의 약품·효능·성분·안전성을 만들지 않음. 키를 `VITE_` 변수에 넣지 않음      |
| 보정값·약품 위치·대표색·상대 크기  | **Pi/AWS 인식 결과** | 좌표·크기 미확인 포장은 목록만 제공. 사용자에게 포장 크기 수동 편집을 요구하지 않음 |

치수를 일부만 입력하거나 주소가 잘못되면 화면에 설정 오류가 표시됩니다. DB 연결 문자열·IAM 키·VAPID 비밀키·구독 `auth`는 프론트엔드에 입력하지 않습니다. 식약처/DUR 키, VAPID 키와 이미지 업로드 정책은 추후 서버에서 설정합니다.

**실제 데이터를 표시하려면 주소뿐 아니라 [조회 응답 계약](../docs/CONTRACTS.md#10-초기-프론트엔드-조회-어댑터-v01)에 맞는 서버가 필요합니다.** API 인증/접근 제어·CORS는 백엔드에서 별도 구현합니다. 이 앱이 AWS 자원이나 데이터베이스를 자동 생성하지 않습니다.

## 현재 동작하는 기능

- 홈: 빈 상태, 시연 전환, 약 이름·제공된 효능 검색, 카드 독립 스크롤과 3D 선택 연동, 포장 상세 링크.
- 3D: 마우스·터치 회전/확대, 키보드 좌우 회전·`+`/`-` 확대·`Home` 초기화, 선택 핀, 다른 포장의 반투명 처리, 주황 동일성분/빨강 병용금기 테두리. WebGL 실패 시 목록 이용 가능.
- 상세: 정제·캡슐의 예상 수량, 처방약의 예상 회분, 비정제 잔여량 미표시, 실제 만료일/임시 기준일/미확인 구분.
- 복약: 한국 날짜 기준 달력, 사용자 확인/복용 추정/예정 등의 구분. 시연에서만 확인 상태 변경.
- 알림: 분류 필터·상세·검사 범위 표시. 시연에서 논리 알림 ID 하나만 확인 처리하며 다른 이벤트를 합치지 않음.
- 데이터: 선택적 `GET /inventory`, `GET /alerts` 조회, 런타임 응답 검증, 12초 제한, 오류·오프라인 안내, 늦은 응답 취소, WebSocket 재연결/화면 복귀/알림 클릭 후 재조회.
- PWA: Manifest·앱 아이콘·정적 앱 셸 캐시·업데이트 안내. 일반 푸시 수신 처리와 알림 화면 열기 코드 포함. API·사진·OCR 원문은 캐시하지 않음.

시연 변경은 **메모리만** 사용하며 새로고침·시연 종료 시 초기화됩니다. 실제 서버 데이터·의료정보는 localStorage/IndexedDB에 저장하지 않습니다. 오프라인 재시작은 이전 재고를 복원하지 않습니다.

## 아직 서버 연동이 필요한 기능

실제 촬영·OCR·등록·입출고 판정·재고 계산·DUR 조회·복약 예약·다중 기기 확인·정정/폐기 저장은 AWS/Pi 담당 영역입니다. 프론트엔드는 이 결과를 만들어내거나 의료적 판단을 수행하지 않습니다. 실제 쓰기 API의 스키마와 접근 제어가 미확정이므로 **현재 서버 모드는 읽기 전용**입니다. 수정 버튼을 열면 이유를 안내하고 서버 저장은 비활성화합니다.

처방전 보완 업로드, 구독 등록/해제, VAPID 공개키 조회·Lambda 푸시 발송은 후속 구현입니다. 서버 없이 권한만 요청하거나 구독 완료로 표시하지 않습니다. Service Worker 수신 코드가 있어도 현재 알림 발송이 연결된 상태는 아닙니다. iPhone은 iOS 16.4 이상 홈 화면 PWA·사용자 동작 기반 허용/구독이 필요하며 일반 Safari 탭은 푸시 미지원입니다.

이 범위는 `plan.md`의 기능 삭제가 아니라 첫 프론트엔드 구현 단계입니다. 고정 정책·카메라 최초 등록·서버에서의 60분 재알림 기준은 그대로 유지합니다.

## AWS Amplify Hosting

1. 변경 내용을 GitHub의 배포할 브랜치에 반영합니다.
2. Amplify Hosting에서 GitHub 저장소 `sangminbis9/smart_medikit`과 해당 브랜치를 선택합니다. **모노레포 경로를 `frontend`**로 지정합니다. `AMPLIFY_MONOREPO_APP_ROOT=frontend`도 확인합니다.
3. 루트 [`amplify.yml`](../amplify.yml)을 사용합니다. Node 22 → `npm ci` → 타입·린트·단위 테스트 → 빌드, 산출물은 `frontend/dist`입니다. AWS 백엔드 생성 단계는 없습니다.
4. **Rewrites and redirects**의 JSON 편집기에 [`amplify-rewrites.json`](../amplify-rewrites.json)을 적용합니다. 이 JSON은 자동 로딩되지 않습니다. `/schedule`, `/alerts`, `/device`, `/packages/…`만 앱 셸로 연결하며 `/sw.js`, Manifest, 아이콘, `/assets/…`, API 경로는 제외합니다.
5. 루트 [`customHttp.yml`](../customHttp.yml)의 응답 헤더가 Hosting에 반영됐는지 확인합니다. Service Worker는 재검증하고 HTML·Manifest를 무기한 캐시하지 않습니다.
6. 빈 화면 배포라면 `VITE_` 값 없이 배포합니다. 실서버 연결 시에는 위 표를 입력하고 재빌드한 뒤 API/S3 CORS와 서버 접근 제어를 별도로 검증합니다.
7. HTTPS 주소에서 직접 경로 새로고침, Manifest, 앱 설치, 오프라인 새로고침, 새 버전 안내를 확인합니다. iPhone 실기기 및 실제 푸시 수신은 후속 검증입니다.

설정은 [AWS 모노레포 문서](https://docs.aws.amazon.com/amplify/latest/userguide/monorepo-configuration.html), [SPA rewrite 문서](https://docs.aws.amazon.com/amplify/latest/userguide/redirect-rewrite-examples.html), [모노레포 응답 헤더 문서](https://docs.aws.amazon.com/amplify/latest/userguide/custom-header-YAML-format.html)를 기준으로 작성했습니다. **현재 AWS 자원을 생성하거나 실제 배포하지 않았습니다.**

## 파일과 검증

`App.tsx`는 화면 구성, `Home`/`Schedule`/`Alerts`/`PackageDetail`/`Device`는 각 화면, `Cabinet`은 Three.js 수명주기, `data.ts`는 실제 공유 응답 계약·표시 정책, `useMedkit.ts`는 서버 연결, `settings.ts`는 공개 설정 검증을 담당합니다. 의미 없는 상수/래퍼 전용 디렉터리를 추가하지 않았습니다.

```sh
npm run typecheck
npm run lint
npm run test
npm run format:check
npm run build
npm run test:e2e
```

E2E는 설치된 **Google Chrome**의 별도 임시 프로필을 사용합니다. 실제 iPhone Safari 테스트를 대체하지 않습니다. 테스트 결과 임시 파일은 OS 임시 디렉터리의 `medikit-playwright`에 생성됩니다. 브라우저가 없다면 먼저 Chrome을 설치하거나 팀 환경에 맞게 `playwright.config.ts`의 `channel`을 조정합니다.

초기 구현 검증(2026-10-15): 타입 검사·린트·포맷·배포 빌드 통과, 단위 테스트 14개와 Chrome E2E 8개 통과. 빈 시작 → 시연 → 검색 → 상세 수정, 3D와 카드 선택 유지, 복약·알림 확인 시연, 직접 링크·오프라인 재접속을 확인했습니다. 320/390/768/1199/1200/1440px 폭에서 검증했고, Chrome에서 PWA 새 버전 안내와 업데이트 적용도 직접 확인했습니다. Figma 기준으로 홈·복약·알림의 데스크톱 화면과 모바일 홈을 비교했습니다.

남은 검증: 실제 Amplify HTTPS 환경·API 인증/CORS·AWS/Pi 통합·iPhone Safari 실기기·실제 푸시 발송. Three.js 지연 로딩 청크가 약 554KB(압축 전)여서 Vite 크기 경고가 있으며, 정적 폰트를 포함한 오프라인 캐시는 약 4.5MB입니다. Zod 의존성의 PURE 주석 처리 경고도 있으나 빌드는 완료됩니다. 저사양 기기의 3D 성능은 실제 장치에서 추가 확인해야 합니다.

시각 기준은 Figma의 `3:22`(모바일 홈), `42:804`(데스크톱 홈), `42:846`(알림), `42:888`(복약), `43:1027`(반응형)입니다. 배경 `#f5f7f4`, 본문 `#163c34`, 강조 `#206d5c`, Noto Sans KR/Manrope, 208px 사이드바, 768/1200px 전환점을 따릅니다. Figma 아이콘은 로컬 SVG를 사용하며, 정적인 3D 예시는 실제 Three.js로 구현합니다.

의도적인 차이: 가짜 OS 상태바/홈 인디케이터 제거, 빈 상태·시연 배너 추가, 실제 연결 전 상태 표시, 처방약의 ‘회분’ 표기, 동일성분 주황색 적용, 모바일에서도 날짜를 고를 수 있는 전체 달력. 이는 웹 실행 환경과 확정 정책·빈 데이터 지원을 위한 조정입니다.

새 의존성의 목적: Vite(개발/정적 빌드), React Router(직접 링크), Zod(불완전한 외부 응답 검증), Three.js(직접 3D), Tailwind(반응형 스타일), Fontsource(로컬 폰트), Vite PWA/Workbox(정적 캐시), TypeScript/ESLint/Prettier/Vitest/Playwright(검증). 버전은 lockfile로 고정합니다.
