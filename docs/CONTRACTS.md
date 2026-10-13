# 구성요소 간 API·이벤트 계약 (초안)

> **상태:** 구현 전에 합의할 인터페이스 제안. `plan.md`에 정해진 동작 규칙이 우선한다. 이 파일의 경로와 필드는 실제 구현에 맞춰 세 영역 담당자가 함께 확정한다.

## 1. 공통 규칙

- 한 번의 뚜껑 개폐 = 하나의 `session_id`, 약품 촬영 성공 최대 한 번.
- `event_id`는 **서버 멱등성 키**, `sequence`는 단일 Raspberry Pi의 순번이다.
- 시각은 시간대 정보가 있는 ISO 8601로 저장한다. **당일 위험 조합 검사만 `Asia/Seoul` 날짜 기준**이다.
- 입출고 상태(`STORED`, `OUT`)와 인식 처리 상태(`PROCESSING`, `REVIEW_REQUIRED`)는 별도 필드다.
- 촬영 성공/약품 식별 성공/실제 복용 완료는 서로 다른 사실이다.

## 2. Raspberry Pi → AWS 촬영 세션

한 세션에는 열린 상태의 `package.jpg`와 닫힌 상태의 `interior.jpg`가 있을 수 있다. 내부 촬영에 실패했을 때에도 이벤트는 생성한다.

**세션 메타데이터 예시 (테스트용 가상 값)**

```json
{
  "box_id": "prototype-01",
  "session_id": "session-demo-001",
  "event_id": "event-demo-001",
  "sequence": 1,
  "occurred_at": "2026-10-14T12:00:00+09:00",
  "package_capture_ok": true,
  "interior_capture_ok": true,
  "package_image_key": "sessions/session-demo-001/package.jpg",
  "interior_image_key": "sessions/session-demo-001/interior.jpg"
}
```

**S3 트리거 설계 주의:** 포장 이미지와 내부 이미지가 따로 올라갈 때 Lambda를 매 업로드마다 실행하면 같은 세션을 중복 처리할 수 있다. 실제 구현에서는 이미지 업로드 완료를 확인한 뒤 **세션 완료를 나타내는 단일 S3 객체**로 한 번만 트리거하거나, 동등한 방식으로 세션 완결성과 멱등성을 보장한다. 구체적인 키·업로드 방식은 구현 전 합의한다.

**순서:** Pi는 한 세션을 업로드하고 `GET /capture-events/{event_id}`에서 처리가 끝났는지 2~3초 간격으로 조회한 뒤 다음 세션을 업로드한다. 중간에 촬영한 세션은 로컬 큐에 보관한다.

## 3. 이벤트 처리 상태

| `processing_status` | 의미 | Pi 동작 |
|---|---|---|
| `PENDING` | AWS에 등록, 처리 대기 | 계속 조회 |
| `PROCESSING` | OCR/영상 분석 중 | 계속 조회 |
| `COMPLETED` | 결과 저장 완료 | 다음 세션 전송 |
| `REVIEW_REQUIRED` | 후보 포장/동작 사용자 확인 필요 | 다음 세션 전송 가능, **관련 포장 갱신만 보류** |
| `FAILED` | 시스템 처리 오류 | 유실 없이 로컬 큐 보존·재시도 정책 적용 |

**약품 보관 상태 예시:** `REGISTERED`, `STORED`, `OUT`, `DEPLETED`, `DISPOSED`. `REVIEW_REQUIRED`는 약품의 위치 상태가 아니다.

별도 품질 상태: `POSITION_UNCERTAIN`, `SAFETY_CHECK_PENDING`, `EXPIRY_UNVERIFIED`.

## 4. 앱 조회 및 변경 이벤트

**검토할 엔드포인트 (아직 배포되지 않음)**

| 경로 | 역할 |
|---|---|
| `GET /inventory` | 단일 보관함 약품 목록·수량·회분 조회 |
| `GET /packages/{package_id}` | 개별 포장과 의약품 정보 조회 |
| `GET /capture-events/{event_id}` | Pi 처리 상태 조회 |
| `POST /reviews/{review_id}/resolve` | 후보 포장 및 반입·반출·변경 없음 확정 |
| `POST /packages/{package_id}/adjust` | 잔여량·입출고 기록 정정, 수정 이력 보존 |
| `POST /doses/{dose_id}/confirm` | 복용 확인·미복용·확인 보류 |
| `POST /alerts/{alert_id}/ack` | 하나의 기기에서 최초 확인 시 전체 동기화 |
| `POST /devices/push-token` | 스마트폰별 푸시 대상 등록 |
| `WS /realtime` | 상태 변경 시 모든 실행 중인 앱의 화면 갱신 |

WebSocket은 **상태 변경 사실과 버전 정보**를 알리고, 앱은 필요하면 조회 API로 최신 상태를 다시 불러온다. 재연결 시 반드시 재조회하여 이벤트 유실에 대응한다.

## 5. 상태를 바꾸는 작업의 일관성

- `event_id`가 이미 완료되었으면 재처리해도 잔여 수량을 다시 차감하지 않는다.
- 처방약 봉투 1개에 여러 의약품이 있어도 **물리 포장 ID는 하나**다.
- 약봉투 반출은 실제 복용을 입증하지 않으며 복용 기록에 추정 여부를 남긴다.
- 동일 포장 60분 이내 재반출은 추가 복용 확인 전까지 추가 회분/정수를 차감하지 않는다.
- 인식 실패는 관련 후보만 보류하고, 영향 없는 포장은 정상 처리한다.
- 정제·캡슐 외 일반의약품에는 잔여량·퍼센트·저재고 알림을 생성하지 않는다.
- 실제 만료일과 임시 관리 기준일은 다른 필드다. 둘을 같은 만료일로 보내지 않는다.
- 서버 정정은 수정 이력을 남기고 잔여 수량·복용 회차·보관 상태를 함께 검토한다.

## 6. 권한과 민감정보

- 공개 GitHub 저장소에 실제 처방전, 실사용자의 의료정보, API 키·토큰·IAM 키를 올리지 않는다.
- 시제품 앱은 회원가입이 없어도 **AWS 쓰기 권한을 무제한 공개하지 않는다**. 공개 가능 범위를 최소화하고 쓰기 API·S3 업로드·기기 통신은 별도 제어를 검토한다.
- 이미지 원본은 식별용 참조 이미지를 제외하고 처리·동기화 완료 후 삭제한다. 관련 접근 권한을 확인한다.
