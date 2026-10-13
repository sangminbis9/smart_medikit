# smart_medikit — 타이레놀 팀

**Raspberry Pi + Flutter(Android/iOS) + AWS 기반 스마트 약품보관함** 캡스톤 프로젝트.

기존 투명 보관함에 카메라·조명·개폐 센서를 부착하여 일반의약품과 처방약 포장을 **카메라에 보여주는 것만으로 신규 등록**하고, 입출고·복약 기록·예상 재고·DUR 주의정보·3D 보관함을 통합 관리한다.

## 먼저 읽을 문서

| 파일 | 목적 |
|---|---|
| [`AGENTS.md`](AGENTS.md) | **Codex와 Claude 공통 개발 규칙**. 주석, 함수 설명, 파일 분리, 보안, 검증 |
| [`CLAUDE.md`](CLAUDE.md) | Claude Code가 공통 규칙을 가져오는 연결 파일 |
| [`plan.md`](plan.md) | 프로젝트의 **상세 확정 요구사항 및 개발 계획** |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 3인 역할 분리, 구현 경계, 데이터 흐름 |
| [`docs/CONTRACTS.md`](docs/CONTRACTS.md) | 컴포넌트 간 이벤트·API·상태 계약 **초안** |
| [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) | 브랜치, PR, 리뷰 및 검증 방법 |

## 개발 영역

아래 디렉터리는 **실제 소스코드를 처음 추가할 때에만** 생성한다. 빈 폴더나 `.gitkeep` 파일을 만들 필요는 없다.

```text
hardware/    # Raspberry Pi 촬영·센서·LED·오프라인 큐 (Python/OpenCV)
backend/     # AWS Lambda·PaddleOCR·DynamoDB·S3·API 및 이벤트 처리
mobile/      # Flutter Android/iOS 앱·알림·Three.js 3D View
```

- 기능이 독립될 때만 파일을 나누고, 설정값 하나를 위해 파일을 새로 만들지 않는다.
- 소스코드가 생기면 각 영역 README에 **검증된 실제 실행 명령만** 추가한다. 존재하지 않는 명령을 임의로 만들어 적지 않는다.
- 제품/처방 사진은 반드시 **합성 또는 개인정보가 없는 테스트 데이터**를 사용한다.

## Codex / Claude Code 이용법

저장소 루트에서 에이전트를 실행하면 공통 지침을 읽도록 구성되어 있다.

- **Codex:** 루트 `AGENTS.md` 적용.
- **Claude Code:** 루트 `CLAUDE.md`의 `@AGENTS.md` 가져오기로 동일 규칙 적용.
- 반복적인 변경 검토에는 각 에이전트의 `medikit-review` 프로젝트 스킬을 사용한다.
- 작업 요청 시 **담당 영역, 입력/출력, 예외 상황, 검증 조건**을 함께 명시한다.

요청 예시:

> `AGENTS.md`와 `plan.md`의 관련 섹션, `docs/CONTRACTS.md`를 읽고 hardware에서 약품 촬영 이벤트 로컬 큐를 구현해줘. 함수 앞에는 짧은 한글 주석을 쓰고, 변경 파일을 최소화해줘. 오프라인 재시도 테스트까지 해줘.

## 협업 원칙

서로 다른 에이전트를 써도 **저장소의 계약 문서와 상태 enum은 동일**해야 한다. 백엔드 변경으로 앱/Pi가 깨질 수 있으면 PR에서 인터페이스 변경과 검증을 같이 다룬다.

개발에 착수하기 전 확정되지 않은 값(인식 임계값, 모델 메모리, 식약처 API 응답 필드)은 **실험을 통해 정하고 문서에 반영**한다.
