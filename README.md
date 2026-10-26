# smart_medikit — 타이레놀 팀

**Raspberry Pi + React(TypeScript)·Tailwind CSS 반응형 웹/PWA + AWS 기반 스마트 약품보관함** 캡스톤 프로젝트.

기존 투명 보관함에 카메라·조명·개폐 센서를 부착하여 일반의약품과 처방약 포장을 **카메라에 보여주는 것만으로 신규 등록**하고, 입출고·복약 기록·예상 재고·DUR 주의정보·3D 보관함을 통합 관리한다.

기존 Figma 디자인을 기준으로 PC·Android·iPhone을 지원한다. Three.js를 React에 직접 연동하고, 웹은 **GitHub 연동 AWS Amplify Hosting**에서 HTTPS로 배포하도록 준비했다. [`frontend/`](frontend/README.md)에 실행 가능한 초기 웹/PWA가 있으며 API·약품 DB·보관함 치수가 없어도 빈 화면과 선택형 시연 모드로 실행된다. 실제 Pi/AWS 백엔드와 배포 환경은 아직 연결하지 않았다.

**빠른 실행:** `cd frontend` → `npm ci` → `npm run dev` (Node.js 22.12 이상). 사용자가 채울 공개 설정, Amplify 연결 절차와 현재 구현 범위는 [프론트엔드 안내](frontend/README.md)에 정리했다.

## 먼저 읽을 문서

| 파일 | 목적 |
|---|---|
| [`AGENTS.md`](AGENTS.md) | **Codex와 Claude 공통 개발 규칙**. 주석, 함수 설명, 파일 분리, 보안, 검증 |
| [`CLAUDE.md`](CLAUDE.md) | Claude Code가 공통 규칙을 가져오는 연결 파일 |
| [`plan.md`](plan.md) | 프로젝트의 **상세 확정 요구사항 및 개발 계획** |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 3인 역할 분리, 구현 경계, 데이터 흐름 |
| [`docs/CONTRACTS.md`](docs/CONTRACTS.md) | 컴포넌트 간 이벤트·API·상태 계약 **초안** |
| [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) | `main` 직접 작업·커밋·push 및 검증 방법 |

## 개발 영역

[`Raspberry_Pi/`](Raspberry_Pi/README.md)는 YOLOE-26n 후보 감지, Picamera2 자동 촬영, GPIO 및 영속 순차 큐를 구현한다. 요청에 따라 Pi 제어 소스는 이 폴더에 두며, 기존 `hardware/`의 PC 카메라 수동 미리보기 실험은 유지한다. AWS는 배포 계약 미확정으로 기본 비활성화되어 있다.

PC 웹캠 실시간 객체 탐지는 `Raspberry_Pi`에서 `python -m medikit_pi --config config.example.toml webcam --camera 0`으로 실행한다. 준비된 YOLOE 모델을 사용하며 자동 촬영·저장·AWS 전송은 하지 않는다. 환경 설치와 카메라 선택은 [Pi/PC 실행 안내](Raspberry_Pi/README.md)를 참고한다.

`frontend/`에 웹 소스가 있고, [`hardware/`](hardware/README.md)에 DroidCam/OpenCV 기반 **PC 미리보기·수동 프레임 저장 실험**이 있다. Pi 자동 촬영은 `Raspberry_Pi/`에 구현했으며 실기기 검증과 AWS 서버 배포·연결은 아직 수행하지 않았다. 나머지 디렉터리는 **실제 소스코드를 처음 추가할 때에만** 생성한다. 빈 폴더나 `.gitkeep` 파일을 만들 필요는 없다.

```text
hardware/    # PC 카메라 수동 미리보기 실험
Raspberry_Pi/ # Raspberry Pi 자동 촬영·YOLOE·센서·LED·오프라인 큐
backend/     # AWS Lambda·PaddleOCR·DynamoDB·S3·API 및 이벤트 처리
frontend/    # React·TypeScript·Tailwind CSS·PWA·Three.js 반응형 웹
```

- 기능이 독립될 때만 파일을 나누고, 설정값 하나를 위해 파일을 새로 만들지 않는다.
- 소스코드가 생기면 각 영역 README에 **검증된 실제 실행 명령만** 추가한다. 존재하지 않는 명령을 임의로 만들어 적지 않는다.
- 제품/처방 사진은 반드시 **합성 또는 개인정보가 없는 테스트 데이터**를 사용한다.

## 웹/PWA와 AWS 역할

- **Pi → S3 → ECR Lambda(PaddleOCR+OpenCV) → DynamoDB**의 촬영·식별·상태 처리와 API Gateway·WebSocket·EventBridge Scheduler를 유지한다. 예상 재고·복약·DUR·유효기간 정책은 `plan.md`를 따른다.
- 웹 보완 사진은 촬영 또는 파일 선택 후 개인정보를 제거하고 임시 S3 경로로 전송해 기존 Lambda/PaddleOCR로 처리한다. 사용자 확인 후 필요한 처방 필드만 반영하고 임시 원본을 삭제한다.
- **Lambda → Web Push(VAPID) → Service Worker**로 알림을 보내고 DynamoDB에 브라우저별 구독을 저장한다. 알림 허용·구독 완료 대상 모두에 발송하며 권한 거부·미지원 환경에서도 웹 알림 이력은 조회할 수 있다.
- iPhone 푸시는 **iOS 16.4 이상에서 홈 화면에 추가한 PWA를 열고 사용자 동작으로 알림을 허용·구독**해야 한다. 일반 Safari 탭은 기본 웹 기능을 제공하고 홈 화면 설치를 안내한다. 환경별 제약과 공식 근거는 [`plan.md` 7절](plan.md#7-react-반응형-웹pwa-및-다중-기기)을 참고한다.
- 기본 기능은 URL 접속으로 사용한다. 단일 보관함·무로그인은 시연 범위이며 HTTPS/CORS가 접근 제어를 대신하지 않는다. IAM·VAPID 비밀키를 웹 번들에 포함하지 않는다.

## Codex / Claude Code 이용법

저장소 루트에서 에이전트를 실행하면 공통 지침을 읽도록 구성되어 있다.

- **Codex:** 루트 `AGENTS.md` 적용.
- **Claude Code:** 루트 `CLAUDE.md`의 `@AGENTS.md` 가져오기로 동일 규칙 적용.
- 반복적인 변경 검토에는 각 에이전트의 `medikit-review` 프로젝트 스킬을 사용한다.
- 작업 요청 시 **담당 영역, 입력/출력, 예외 상황, 검증 조건**을 함께 명시한다.

요청 예시:

> `AGENTS.md`와 `plan.md`의 관련 섹션, `docs/CONTRACTS.md`를 읽고 hardware에서 약품 촬영 이벤트 로컬 큐를 구현해줘. 함수 앞에는 짧은 한글 주석을 쓰고, 변경 파일을 최소화해줘. 오프라인 재시도 테스트까지 해줘.

## 협업 원칙

기본 작업·커밋·push 대상은 **`main`**이다. 별도 작업 브랜치·PR은 사용자가 명시적으로 요청한 경우에만 사용한다. Amplify Hosting도 `main`을 배포 대상으로 연결한다.

서로 다른 에이전트를 써도 **저장소의 계약 문서와 상태 enum은 동일**해야 한다. 백엔드 변경으로 앱/Pi가 깨질 수 있으면 인터페이스 변경과 검증을 같은 변경 묶음에서 다룬다.

개발에 착수하기 전 확정되지 않은 값(인식 임계값, 모델 메모리, 식약처 API 응답 필드)은 **실험을 통해 정하고 문서에 반영**한다.
