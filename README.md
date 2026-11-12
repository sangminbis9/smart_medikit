# smart_medikit — 타이레놀 팀

**Raspberry Pi + React(TypeScript)·Tailwind CSS 반응형 웹/PWA + AWS 기반 스마트 약품보관함** 캡스톤 프로젝트.

기존 투명 보관함에 카메라·조명·개폐 센서를 부착하여 일반의약품 포장을 **카메라에 보여주는 것만으로 신규 등록**하고, 입출고·예상 재고·DUR 주의정보·3D 보관함을 관리한다. 처방받아 복용 중인 약은 **복약 탭에서 처방 묶음과 포함된 약을 직접 등록**하며, 약통 재고·3D 위치와 별도로 관리한다.

기존 Figma 디자인을 기준으로 PC·Android·iPhone을 지원한다. Three.js를 React에 직접 연동하고, 웹은 **GitHub 연동 AWS Amplify Hosting**에서 HTTPS로 배포한다. [`frontend/`](frontend/README.md)에 웹/PWA와 32개 증상의 케어 가이드가 있다. [`backend/`](backend/README.md)는 DynamoDB 제품 캐시와 보유 포장(`PACKAGE`)을 연결하며 제품 캐시만으로 보유 재고를 만들지 않는다. `hardware/pi`의 단계별 촬영과 AWS OCR·입출고·이력·푸시가 연결되어 있으며, 합성 데이터로 S3부터 웹까지 검증했다. 실물 Pi 촬영은 별도 검증이 필요하다.

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
| [`docs/PIPELINE_AUDIT.md`](docs/PIPELINE_AUDIT.md) | 종단 결함 수정·배포·합성 검증 및 남은 실기기 검증 범위 |

## 개발 영역

[`ocr-lambda/`](ocr-lambda/README.md)는 저장소 OCR 구현과 운영 OCR에 적용한 Pi 호환 어댑터를 포함한다. 운영 OCR은 저장소 전체 구현과 다른 기존 Python ZIP이며 제품 저장 로직을 보존한 채 Pi 이벤트 ID와 결과를 연결했다. 실물 Pi부터의 전체 흐름은 아직 검증하지 않았다.

[`Raspberry_Pi/`](Raspberry_Pi/README.md)는 초기 Pi 구현을 보존한다. 현재 설치 대상은 단계별 OCR·내부 비교와 AWS 설정이 추가된 `hardware/pi/`이며 두 폴더는 자동 동기화되지 않는다.

[`hardware/pi/`](hardware/pi/README.md)는 원본을 보존하고 복사한 뒤 단계별 OCR 전송·내부 비교를 추가한 **현재 Pi 설치 대상**이다. 배포된 AWS 연결값, `check-aws` 점검, 설치·GPIO·단기 로그인 절차는 해당 README를 따른다. 기존 DroidCam 실행에는 연결하지 않았으며 `Raspberry_Pi/`와 변경이 자동 동기화되지 않는다. 실제 Pi에서 전체 촬영 경로를 검증하는 작업은 남아 있다.

PC 웹캠 실시간 객체 탐지는 `Raspberry_Pi`에서 `python -m medikit_pi --config config.example.toml webcam --camera 0`으로 실행한다. 준비된 YOLOE 모델을 사용하며 자동 촬영·저장·AWS 전송은 하지 않는다. 환경 설치와 카메라 선택은 [Pi/PC 실행 안내](Raspberry_Pi/README.md)를 참고한다.

`frontend/`에 웹 소스가 있고, [`hardware/`](hardware/README.md)에 DroidCam/OpenCV 기반 PC 촬영 실험이 있다. 실기기 설치는 `hardware/pi/`를 사용한다. AWS·웹은 배포되었지만 실제 Pi의 카메라·GPIO·AWS 로그인과 실물 인식 정확도는 미검증이다.

```text
hardware/    # PC 카메라 탐지·품질 검사·수동/자동 저장 실험
Raspberry_Pi/ # Raspberry Pi 자동 촬영·YOLOE·센서·LED·오프라인 큐
ocr-lambda/  # 공통 OCR·제품 대조·DynamoDB 인식 이벤트 (첨부 Lambda 소스)
backend/     # 기존 DynamoDB 제품 캐시 조회 API·배포·검증
frontend/    # React·TypeScript·Tailwind CSS·PWA·Three.js 반응형 웹
```

- 기능이 독립될 때만 파일을 나누고, 설정값 하나를 위해 파일을 새로 만들지 않는다.
- 소스코드가 생기면 각 영역 README에 **검증된 실제 실행 명령만** 추가한다. 존재하지 않는 명령을 임의로 만들어 적지 않는다.
- 제품/처방 사진은 반드시 **합성 또는 개인정보가 없는 테스트 데이터**를 사용한다.

## 웹/PWA와 AWS 역할

- **Pi → S3 → 기존 Python ZIP OCR Lambda(PaddleOCR+OpenCV) → DynamoDB**로 식별하고 별도 Lambda가 촬영 단계·입출고를 결합한다. API Gateway와 EventBridge 유지보수·Web Push를 사용한다. WebSocket은 선택적 연동이며 미설정 시 재조회한다. 예상 재고·복약·DUR·유효기간 정책은 `plan.md`를 따른다.
- 수동 복용약은 묶음별 공통 일정과 약마다 다른 복용법을 기록한다. 사용자가 승인한 **비밀번호 없는 공개 시연용 AWS 단일 목록**으로, 전용 `inha-capstone-02-medkit-medications` 테이블과 `smart-medikit-medication-api`를 사용한다. 공개 모드에서는 누구나 약 이름·일정·복용 기록·메모를 조회·수정·삭제할 수 있으므로 **합성 시연 데이터만 입력하고 개인정보는 입력하지 않는다.** 기존 재고 API와 분리하며 브라우저 영구 저장은 사용하지 않는다. 서버 공개 플래그의 기본값은 비공개이고 기존 비밀번호 경로를 유지한다. 메모리 시연은 AWS 공개 목록과 별개다. 배포·검증 상태는 [프론트엔드 안내](frontend/README.md)와 [백엔드 안내](backend/README.md)를 따른다.
- 기존 웹 처방전 보완 OCR·약봉투 재고 연결은 과거 설계로 보존하며, 수동 복용약 등록의 필수 흐름으로 사용하지 않는다.
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
