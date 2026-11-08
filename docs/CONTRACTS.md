# 구성요소 간 API·이벤트 계약 (초안)

> **상태:** 구현 전에 합의할 인터페이스 제안. `plan.md`에 정해진 동작 규칙이 우선한다. 이 파일의 경로와 필드는 실제 구현에 맞춰 세 영역 담당자가 함께 확정한다.

> **2026-11-08 경계 변경:** 처방받아 복용 중인 약은 복약 탭의 수동 처방 묶음으로 관리한다(10.3절). 아래 `package_id` 기반 처방 포장·OCR·회분 계약은 기존 데이터와 코드의 호환 설명이며 수동 복용약 등록에 적용하지 않는다. 일반의약품의 Pi 촬영·OCR·재고 계약은 유지한다.

## 1. 공통 규칙

- 한 번의 뚜껑 개폐 = 하나의 `session_id`, 약품 촬영 성공 최대 한 번.
- `event_id`는 **서버 멱등성 키**, `sequence`는 단일 Raspberry Pi의 순번이다.
- 이벤트 시각은 시간대 정보가 있는 ISO 8601로 저장한다. 당일 위험 조합 검사와 수동 복약 달력의 날짜는 `Asia/Seoul` 기준이다. 수동 일정의 날짜·시각 입력 형식은 10.3절을 따른다.
- 입출고 상태(`STORED`, `OUT`)와 인식 처리 상태(`PROCESSING`, `REVIEW_REQUIRED`)는 별도 필드다.
- 촬영 성공/약품 식별 성공/실제 복용 완료는 서로 다른 사실이다.
- 수동 처방 묶음의 등록·일정 생성·복용 확인은 물리적 포장의 등록·입출고·재고 차감과 별개이며 `GET /inventory`에 합치지 않는다.

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

### 2.1 Pi 구현 상태와 배포 전 확정 항목 (2026-10-26)

`Raspberry_Pi/`는 위 세션 필드와 처리 상태를 사용한 로컬 영속 큐를 구현한다. `sequence`는 세션 시작 때 할당하며 재부팅·재전송해도 `event_id`와 함께 유지한다. 촬영 실패 이미지의 키는 `null`이다. 중단된 열린 세션은 확보한 사진만 보존한 채 불완전 세션으로 복구한다. 내부 사진 실패를 받은 AWS가 기존 위치 보존 및 `POSITION_UNCERTAIN`을 적용해야 하며, Pi는 재고·입출고를 확정하지 않는다.

서버 구현은 아직 없으므로 기본 `aws.enabled=false`다. 구현된 S3 전송 어댑터는 다음 배포 계약이 실제로 확인된 경우에만 `contract_confirmed=true`로 활성화한다. 아래는 **어댑터가 지원하는 연결 조건**이며 배포된 서버를 뜻하지 않는다.

- 명시한 `prefix/<session_id>/package.jpg`, `interior.jpg`를 먼저 업로드하고, 설정한 `manifest_name` JSON을 마지막에 올린다. JSON의 이미지 키는 실제 prefix로 치환한다. Lambda는 완료 객체만 트리거하고 이미지별 트리거를 함께 사용하지 않는다.
- 상태 조회의 **전체 HTTPS URL 템플릿**, 인증 방식(SigV4 `execute-api` 또는 외부 파일의 bearer 토큰), 응답 `processing_status`와 기존 enum을 확정한다. 미구현 `POST /capture-events`나 업로드 URL 발급 API를 자동 호출하지 않는다.
- 서버는 `event_id` 멱등성, `sequence` 순서, `FAILED` 이벤트의 동일 ID 재처리와 중복 완료 객체를 처리한다. `PENDING`/`PROCESSING`은 계속 조회하고 `FAILED`/통신 오류는 큐 선두를 보존한다. `COMPLETED`/`REVIEW_REQUIRED` 확인 후에만 임시 사진을 삭제하고 다음 세션을 전송한다.
- S3·API 권한은 외부에서 갱신되는 단기 AWS 세션 자격증명으로 제한한다. 이 구현은 IAM·버킷 공개 설정·Lambda·API를 생성하거나 변경하지 않는다.
- YOLOE-26n의 결과는 Pi 내부 포장 **후보**이며 이벤트에 약품명·복용·재고 확정값을 추가하지 않는다. 한글 OCR과 최종 식별은 기존 AWS 책임을 유지한다. 포장 사진이 없으면 내부 전후 비교와 확인 요청 판단도 AWS에서 수행한다.

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

## 4. 웹앱 조회 및 변경 이벤트

**검토할 엔드포인트 (아직 배포되지 않음)**

| 경로 | 역할 |
|---|---|
| `POST /capture-events` | Pi 촬영 메타데이터 등록 및 제한된 S3 업로드 경로 발급 |
| `GET /inventory` | 단일 보관함 약품 목록·수량·회분 조회 |
| `GET /packages/{package_id}` | 개별 포장과 의약품 정보 조회 |
| `GET /capture-events/{event_id}` | Pi 처리 상태 조회 |
| `POST /reviews/{review_id}/resolve` | 후보 포장 및 반입·반출·변경 없음 확정 |
| `POST /packages/{package_id}/adjust` | 잔여량·입출고 기록 정정, 수정 이력 보존 |
| `POST /doses/{dose_id}/confirm` | 복용 확인·미복용·확인 보류 |
| `POST /disposals/{package_id}` | 소진·폐기 확인 |
| `GET /alerts` | 경고·만료·미확인 작업 조회 |
| `POST /alerts/{alert_id}/ack` | 하나의 기기에서 최초 확인 시 전체 동기화 |
| `GET /push/vapid-public-key` | 브라우저 구독에 사용하는 VAPID 공개키 조회 |
| `POST /push/subscriptions` | 브라우저 Web Push 구독 등록·갱신 |
| `DELETE /push/subscriptions/{subscription_id}` | 해당 브라우저 구독 해제 |
| `POST /packages/{package_id}/prescription-supplements` | 보완 OCR 작업 생성·제한된 임시 S3 업로드 경로 발급 |
| `GET /prescription-supplements/{supplement_id}` | OCR 처리 상태·필요한 처방 필드 조회 |
| `POST /prescription-supplements/{supplement_id}/confirm` | 사용자가 검토한 처방 정보 반영 |
| `WS /realtime` | 상태 변경 사실·버전을 모든 활성 웹앱에 전달 |

기존 재고·포장·복약·정정·Pi 이벤트 계약은 유지한다. 네이티브 푸시 토큰 경로는 Web Push 구독 API로 대체하고 보완 OCR 작업 경로를 추가한다. 위 경로·필드는 **구현 제안**이며 실제 요청/응답·접근 제어·오류 코드는 구현 전 합의한다.

WebSocket은 **상태 변경 사실과 버전 정보**를 알리고, 웹앱은 필요하면 조회 API로 최신 상태를 다시 불러온다. HTTPS API와 `wss`를 사용하며 재연결·백그라운드 복귀·알림 클릭 후 진입 시 반드시 재조회한다. 중지된 브라우저/PWA에서 연결이 유지된다고 가정하지 않는다.

## 5. 상태를 바꾸는 작업의 일관성

- `event_id`가 이미 완료되었으면 재처리해도 잔여 수량을 다시 차감하지 않는다.
- 처방약 봉투 1개에 여러 의약품이 있어도 **물리 포장 ID는 하나**다.
- 약봉투 반출은 실제 복용을 입증하지 않으며 복용 기록에 추정 여부를 남긴다.
- 동일 포장 60분 이내 재반출은 추가 복용 확인 전까지 추가 회분/정수를 차감하지 않는다.
- 인식 실패는 관련 후보만 보류하고, 영향 없는 포장은 정상 처리한다.
- 정제·캡슐 외 일반의약품에는 잔여량·퍼센트·저재고 알림을 생성하지 않는다.
- 실제 만료일과 임시 관리 기준일은 다른 필드다. 둘을 같은 만료일로 보내지 않는다.
- 서버 정정은 수정 이력을 남기고 잔여 수량·복용 회차·보관 상태를 함께 검토한다.
- 여러 기기·브라우저 탭의 확인/정정은 서버에서 일관성을 검증하고 같은 확인 요청의 최초 확정 응답 하나만 반영한다. 오프라인 화면에서 정정을 서버 반영 완료로 표시하지 않는다.
- 푸시 수신·알림 클릭만으로 내용 확인이나 실제 복용을 확정하지 않는다. 정해진 확인 API의 응답으로만 확정한다.

## 6. 권한과 민감정보

- 공개 GitHub 저장소에 실제 처방전, 실사용자의 의료정보, API 키·토큰·IAM 키를 올리지 않는다.
- 시제품 앱은 회원가입이 없어도 **AWS 쓰기 권한을 무제한 공개하지 않는다**. 사용자가 별도 승인한 10.3절의 수동 복용약 공개 시연은 지정한 단일 목록의 조회·쓰기에 한정하며, 기존 재고·S3 업로드·기기 통신 접근 정책은 유지한다.
- 이미지 원본은 식별용 참조 이미지를 제외하고 처리·동기화 완료 후 삭제한다. 관련 접근 권한을 확인한다.
- 웹 번들·Amplify 프론트엔드 빌드 변수에는 공개 API/`wss` URL·단일 `box_id`·VAPID 공개키 등 공개 가능한 값만 둔다. IAM·VAPID 비밀키와 다른 브라우저의 구독 정보는 노출하지 않는다.
- API·S3 업로드 CORS는 승인 출처·메서드·헤더로 제한하고 WebSocket 출처·접근 제어는 별도 검토한다. HTTPS·CORS·`box_id`·공개키는 인증 수단이 아니다. 로그인 없는 시연 정책이 쓰기·업로드·구독 API의 무제한 공개를 뜻하지 않는다.

## 7. Web Push 구독·발송 계약 (초안)

- 프론트엔드는 사용자 버튼 동작으로 알림 권한을 요청한다. HTTPS·Service Worker·Push API/Notifications API 지원 및 사용자 허용 후 VAPID 공개키와 `userVisibleOnly: true`로 구독한다. 거부 상태에서 자동 반복 요청하지 않는다.
- `POST /push/subscriptions`는 단일 `box_id`와 브라우저 `PushSubscription`의 `endpoint`, `keys.p256dh`, `keys.auth`, 선택적 `expirationTime`을 전달한다. 서버는 구독 식별자·상태를 반환하고 동일 endpoint 재등록은 갱신으로 처리한다. 실제 JSON 구조와 크기 제한은 구현 전 확정한다.
- DynamoDB의 논리 엔터티 **`WebPushSubscription`**는 `subscription_id`, `box_id`, `endpoint`, `keys.p256dh`, `keys.auth`, `expiration_time`, `status`, `created_at`, `last_seen_at`을 관리한다. 브라우저의 `expirationTime`을 서버 `expiration_time`으로 변환하는 기준과 `null`/누락 처리를 합의한다.
- 같은 물리 기기의 브라우저·프로필·PWA별 구독을 구분하며 구독 수를 사용자 수로 간주하지 않는다. 웹 재접속 시 구독을 확인·갱신하고 구독 해제는 브라우저와 서버 양쪽에 반영한다. 구독 소유·해제 권한은 구현 전 확정한다.
- 서버는 endpoint가 HTTPS의 허용 푸시 서비스 목적지인지 검증한다. endpoint·암호화 정보는 접근 제한된 DynamoDB에 저장하고 공개 조회·로그에 포함하지 않는다. VAPID 비밀키는 서버에서만 관리한다.
- **Lambda**가 구독별 페이로드를 암호화하고 VAPID로 발신 서버를 인증해 브라우저 푸시 서비스로 전송한다. 복약·만료·재시도 예약은 기존 **EventBridge Scheduler + Lambda**를 유지한다. SNS/네이티브 토큰 연동을 추가하지 않는다.
- Service Worker의 `push`는 알림을 표시하고 `notificationclick`은 같은 출처의 기존 창 또는 새 창으로 연결한다. 링크 진입 후 서버 상태를 재조회한다. 브라우저 타이머로 복약·60분 재알림을 예약하지 않는다.
- 서버의 논리 알림 ID·구독별 발송 기록 및 수신 측 중복 표시 억제를 설계한다. 같은 이벤트 중복 발송은 억제하되 서로 다른 반출 이벤트에서 반복되는 같은 위험 조합은 합치지 않는다. 불확실한 전송 결과의 재시도로 중복 도착할 가능성을 고려한다.
- 푸시 서비스가 `404/410` 등으로 구독 무효를 알리면 정리하고 `429/5xx`·일시 통신 오류는 재시도한다. 한 구독 실패로 다른 발송을 중단하지 않는다. TTL·재시도 간격·비밀키 교체는 구현 전 확정한다.
- 정확히 60분 후 재알림 1회는 **서버 예약·논리 알림 생성 기준**이다. 응답한 확인 요청의 예약은 취소하고 발송 직전에도 상태를 확인한다. 실제 도착 시각·수신/읽음은 푸시 서비스의 수락만으로 보장하지 않는다.
- iPhone은 **iOS 16.4 이상 + 홈 화면 PWA + 사용자 동작에 따른 권한 허용·구독**이 필요하다. 일반 Safari 탭에는 설치 안내를 제공한다. 미지원·거부·미설치 상태에서도 서버 알림 이력·확인 요청을 유지한다. 공식 근거와 지원 표는 [`plan.md` 7.5절](../plan.md#75-iphone-푸시-조건과-환경별-제약)을 따른다.

## 8. 웹 처방전 보완 OCR 계약 (과거 초안·호환 참고)

이 절은 과거 처방 포장 흐름의 계약으로 보존한다. 새 수동 복용약 등록에서는 촬영·업로드·`package_id`를 요구하지 않으며, 이 작업에서 기존 OCR·Pi 기능을 삭제하거나 배포 설정을 바꾸지 않는다.

- 보관함 카메라로 생성한 기존 `package_id`에 보완 작업을 연결한다. 웹에서 촬영/파일을 선택하고 필요한 영역만 남겨 개인정보를 제거한 뒤 업로드한다. 카메라 권한 거부·미지원·카메라 부재 시 파일 선택을 제공한다.
- 생성 API는 `supplement_id`와 제한된 임시 S3 업로드 경로를 발급한다. 지정 객체 범위·짧은 유효기간·이미지 형식/크기를 검증하고 업로드 완료 이벤트를 기존 ECR Lambda/PaddleOCR에 연결한다. 임시 경로 트리거는 Pi 촬영 트리거와 분리한다.
- 논리 엔터티 **`PrescriptionSupplement`**는 `supplement_id`, `package_id`, `temporary_image_key`, `processing_status`, `extracted_fields`, `confirmed_at`을 관리한다. 필요한 처방 필드만 포함하며 OCR 원문·개인식별정보는 보존하지 않는다. 처리 상태·오류·재요청 멱등성 및 취소 방식은 구현 전 확정한다.
- 조회 API는 처리 상태와 약품명·복용법·기간·횟수 등 필요한 필드를 반환한다. 누락 항목만 재촬영/수동 입력하고 사용자가 검토한 처방 정보를 확인 API로 반영한다. 시작일·종료일·알림시간 제안/확인 정책은 유지한다.
- 중복 업로드·S3 이벤트·확인 재요청으로 처방 정보를 중복 반영하지 않는다. 보완 업로드를 Pi의 `sequence`에 섞거나 신규 반입/반출·재고 차감으로 처리하지 않는다.
- 성공·실패·취소 후 임시 원본을 삭제하고 중단 작업도 정리한다. 삭제 기한·정리 작업·취소·오류 응답은 구현 전에 합의한다. 브라우저 임시 이미지·객체 URL은 해제하고 원본·OCR 원문·민감 응답은 오프라인 캐시·로그에 저장하지 않는다. 사진첩 원본 자동 삭제를 보장하지 않는다.

## 9. 웹 배포·캐시와 API 경계

- **GitHub 연동 Amplify Hosting**은 React 정적 웹/PWA의 HTTPS 배포를 담당하며 기존 AWS 백엔드를 대체하지 않는다. 배포 대상은 `main`, 앱 루트는 `frontend`, 빌드 명령은 `npm run build`, 산출물은 앱 루트 기준 `dist`다. 루트 `amplify.yml`의 설치·검증 절차를 함께 사용한다.
- SPA 상세 경로와 알림 링크 새로고침을 지원하되 Manifest·Service Worker·실제 정적 자산 경로를 HTML로 치환하지 않는다. Service Worker 범위·캐시 버전·갱신 후 API 호환성을 검증한다.
- 정적 화면 자산만 캐시하고 민감 API 응답·처방 이미지는 오프라인 저장하지 않는다. 서버 상태 재조회 및 오프라인 안내를 적용하며 Pi의 촬영·오프라인 큐 계약은 변경하지 않는다.

## 10. 초기 프론트엔드 조회 어댑터 v0.1

`frontend/src/data.ts`에 **프론트엔드가 읽는 초기 응답 스키마**를 구현했다. 이는 배포된 AWS 계약이나 쓰기 API 확정을 뜻하지 않는다. 백엔드 담당자는 실제 제공 가능 필드와 접근 제어를 확인하고 연동 전에 합의한다. 기존 Pi 이벤트·멱등성·상태 전이 정책을 변경하지 않는다.

- `VITE_API_URL`에 `/inventory`, `/alerts`를 붙이고 `box_id` query를 전달한다. `credentials: include`, `cache: no-store`를 사용하며 12초 후 요청을 종료한다. 웹 변수에 토큰을 넣지 않는다. 쿠키 인증을 쓸 경우 서버의 HTTPS·SameSite·CORS·접근 제어 설정을 별도 검토한다.
- `GET /inventory` 응답: `{ box, packages, doses, synced_at }`. `box`: `{ box_id, dimensions: { width, depth, height } | null, device_status: ONLINE | OFFLINE | UNKNOWN }`. 치수와 `PositionSnapshot` 좌표의 단위는 이 어댑터에서 **mm**, X=가로/Y=세로/Z=바닥 기준 추정 높이다. 정확한 영상 보정은 Pi/AWS가 담당한다.
- `packages`는 조회용 결합 결과이며 새 저장 엔터티가 아니다. 각 항목은 기존 `Package`의 `package_id`, `box_id`, `state`, `size_class`, `base_color`와 `kind: OTC | PRESCRIPTION`, `name`, `medicines`, `inventory`, `remaining_doses`, `expiry`, `position`, `safety_status`, `note`를 포함한다. `medicines`는 `Medicine`/`PackageMedicine`, `inventory`는 `Inventory`, `remaining_doses`는 `PrescriptionPlan`, `expiry`는 `ExpiryInfo`, `position`은 `PositionSnapshot`의 조회 결합이다.
- `medicines`: `{ medicine_id, name, form: TABLET | CAPSULE | OTHER | UNKNOWN, ingredients: string[], indications: string[] }[]`. 효능 검색어는 제공된 제품정보에서만 가져온다. 비정제·미확인 제형은 `stock_enabled=true`가 잘못 들어와도 수량을 표시하지 않는다.
- `inventory`: `{ stock_enabled, initial_qty: number | null, estimated_qty: number | null } | null`; `expiry`: `{ actual_expiry: YYYY-MM-DD | null, temporary_management_date: YYYY-MM-DD | null, verified }`; `position`: `{ x, y, z_estimated, certainty: OBSERVED | ESTIMATED | UNCERTAIN } | null`. 화면은 확인 여부와 수량의 추정 성격을 구분한다.
- `doses`: `{ dose_id, package_id, scheduled_at, result }[]`. 표시용 `result`는 `SCHEDULED | ESTIMATED | CONFIRMED | NOT_TAKEN | DEFERRED | UNCONFIRMED`를 사용하며 `DoseEvent`에서 변환한다. 실제 회차 조회 범위·추가 조회·정정 요청은 후속 합의 대상이다. 프론트엔드는 받은 일정만 표시하고 예약·자동 차감을 수행하지 않는다.
- `GET /alerts` 응답: `{ alerts: [...] }`. 각 항목은 `alert_id`, `type`, `title`, `body`, `scope`, `package_ids`, `occurred_at`, `ack_state`, `source_event_id`를 포함한다. `type`: `DUPLICATE_INGREDIENT | DUR_CONTRAINDICATION | REGISTRATION | EXPIRY | REVIEW`; `scope`: `STORED | DAILY_WITHDRAWAL | null`; `ack_state`: `PENDING | ACKNOWLEDGED`. 안전정보의 보관 검사와 당일 반출 검사를 합치지 않는다.
- 빈 배열/없는 선택 필드는 빈 목록 또는 `null`/미확인으로 표시한다. 잘못된 제형·날짜·수량 타입, 중복 포장 ID, 다른 보관함의 포장은 조회 오류로 처리하며 이전 메모리 스냅샷을 최신 성공으로 표시하지 않는다. 시연 샘플을 오류 대체 데이터로 사용하지 않는다.
- WebSocket 메시지는 본문을 상태에 직접 쓰지 않고 재조회 신호로 사용한다. 재연결 간격은 1초에서 최대 30초까지 늘리고 숨겨진 탭에서는 연결을 닫는다. 화면 복귀·온라인 복구·푸시 클릭은 재조회한다.
- 기존 재고 API의 실제 쓰기·사진 업로드·Web Push 구독은 실행하지 않는다. 기존 포장 일정의 시연 편집은 메모리에만 반영된다. 수동 복용약은 10.3절의 전용 AWS 저장 API로 분리하며 기존 재고 쓰기 계약을 확장하지 않는다.
- Service Worker는 정적 산출물만 캐시한다. `push`의 `notification_id`를 알림 tag로 사용하고 일반 문구만 표시한다. 알림 클릭은 같은 출처 `/alerts`를 열고 상태를 재조회할 뿐 확인/복용 API를 호출하지 않는다. 실제 서버 발송·구독은 아직 미연동이다.

### 10.1 증상별 케어 조회 확장 (2026-11-02)

`/care`, `/care/:symptomId`는 8개 카테고리·32개 증상의 일반 건강정보를 표시한다. `careGuides.ts`의 버전·출처 확인일·원문 링크와 증상별 대처/위험 신호를 사용한다. 의료 전문가 검토 완료나 개인별 사용 적합성을 뜻하지 않는다. 기존 `useMedkit.ts` 조회·취소·오프라인·복귀 재조회 경로를 재사용하며 쓰기·OCR·Pi 이벤트 계약은 바꾸지 않는다.

- `GET /inventory`의 선택 응답 확장: `inventory_status: CONFIRMED | PARTIAL | UNAVAILABLE`(없으면 `UNAVAILABLE`), `catalog_medicines: Medicine[]`(없으면 `[]`), `catalog_complete: boolean`(없으면 `false`). 기존 필드는 유지한다. 케어의 보유·미보유 확정에는 최신 성공 응답과 `inventory_status=CONFIRMED`가 필요하다. 시연·오프라인·조회 실패·미완성 재고는 확인 필요다.
- `catalog_medicines`는 기존 `MEDICINE#<ITEM_SEQ>/META` 제품 캐시다. 포장 상태·실제 보유·유효기간을 의미하지 않으므로 `packages`로 변환하지 않는다. 홈의 **등록된 약품** 카드에서 제품명·성분·효능 검색과 사용정보 열람을 제공한다. 케어 첫 화면은 증상 선택만 제공하며, 상세에서는 아래 효능 검색 규칙으로 관련 제품만 표시한다. 이미 포장에 연결된 제품은 중복 표시하지 않는다. `GET /alerts`에 `alerts_status=UNAVAILABLE`이 있으면 알림 미연결을 안내한다.
- `Medicine` 선택 필드: `product_code`(9자리 품목기준코드), `product_info: {source:"MFDS_PERMIT_DETAIL"|"DYNAMODB_PRODUCT_INFO", indications, dosage, precautions}`(각 문구 `string|null`), `product_info_status`, `care_assessment`. 없거나 잘못된 선택 허가·검토 구조는 `null`로 처리하고 기존 목록은 유지한다. 원문을 HTML로 실행하지 않고 텍스트로 표시한다. 사진 근거가 없으면 기본 아이콘을 사용한다.
- `care_assessment`는 **향후 서버의 검토 결과를 받기 위한 선택 계약**이다. 현재 테이블에 존재한다고 가정하거나 자동 생성하지 않는다. `{product_code, review_status:"APPROVED", scope:"CARE_GUIDE_32_V1", source_url, reviewed_at, valid_until, indications, ingredient_names, matches:[{symptom_id, medicine_categories, evidence, conditions}]}` 형태다. 시간은 시간대 포함 ISO 8601, `conditions`는 비어 있지 않은 적용 조건 목록이다. `scope`는 이번 32개 증상 전체를 검토했다는 뜻이며, 부분 검토 결과를 이 값으로 보내면 안 된다. 제품별 `APPROVED`를 발행할 사람 검토 절차와 유효기간 정책은 별도 팀 합의 대상이다.
- 프론트엔드는 출처 HTTPS 허용 호스트(`nedrug.mfds.go.kr`, `data.mfds.go.kr`, `apis.data.go.kr`), 품목 코드·성분 집합·효능 원문 일치, 허가 사용법/주의사항 존재, 검토 유효기간, 원문 내 직접 근거, 정확한 증상 ID·역할을 검사한다. 이 검토 기반 **보유 상태 판단**을 제품명·효능 키워드 부분 일치로 대신하지 않는다. 별도의 **효능 정보 검색**은 선택한 증상 표현을 저장된 효능 원문에서 찾으며 검토 승인을 생성하지 않는다. 조건을 노출하되 개인별 적합성을 자동 판단하지 않는다.
- 현재 케어 화면에서는 다음 보유 상태 판정을 표시하지 않는다. 향후 검토 기반 보유 판정을 사용할 때는 `STORED` 일반의약품만 연결한다. 처방·반출·등록·소진·폐기 포장은 제외한다. 실제 유효기간/안전정보가 부족하면 **확인 필요**, 만료 또는 확인된 DUR/동일성분/기한 경고는 **사용 주의**, 모든 조회 근거가 있는 관련 제품은 **보유**다. 주의사항 확인 버튼을 눌렀다고 위험 근거를 지우지 않는다. 검토가 불완전한 보관 품목이 남으면 미보유로 단정하지 않는다.
- [별도 읽기 API](../backend/README.md)는 기존 테이블을 조회해 `inventory_status=UNAVAILABLE`, `packages=[]`, `catalog_medicines`를 반환한다. 2026-11-02 사용자 승인 후 학교 제공 역할을 재사용해 공개 시연 조회를 배포했다. 새 테이블·저장 구조·OCR·Pi는 변경하지 않았다. 기본 코드는 접근 거부이며 허가된 authorizer 문맥 또는 명시적인 `MEDKIT_DEMO_PUBLIC_READ=true` 배포만 허용한다. 실제 역할의 권한 범위와 주소·현재 데이터 한계는 백엔드 안내를 따른다.

### 10.2 보유 포장 조회 연결 (2026-11-07)

- 기존 테이블의 `MEDICINE#<품목기준코드>/META`와 별도로 **실물 포장마다** `PACKAGE#<package_id>/META`를 저장한다. 새 테이블·인덱스를 만들지 않는다. 필수 필드는 `package_id`, `box_id`, `kind`, `state`, `product_codes`이며, `package_id`는 영문·숫자·`_`·`-` 1~100자다. `kind`와 `state`는 기존 enum을 유지한다. `product_codes`는 연결할 9자리 코드의 문자열 배열이다.
- 제품 마스터만 추가하거나 OCR 성공 이벤트만 저장한 것을 보유 확정으로 바꾸지 않는다. 이번 구현은 명시적인 포장 레코드의 조회이며 Pi/OCR의 자동 입출고 판정·재고 쓰기는 별도다.
- 선택 필드 `name`, `inventory`, `remaining_doses`, `expiry`, `position`, `size_class`, `base_color`, `safety_status`는 10절의 응답 필드와 같다. 날짜·숫자를 검증하고 누락값은 `null`로 남긴다. 제품 연결이 빠진 경우 미확인 의약품을 남겨 일부 성분만으로 포장 전체가 검토됐다고 표시하지 않는다. 자유 메모·환자 식별정보·사진 참조는 반환하지 않는다.
- 사용자의 전체 보관 약품 조회 요청에 따라 `demo_public` 항목 필터와 `OTC` 전용 제한을 제거한다. 설정된 `box_id`의 일반약·처방약 포장을 모두 반환하며, 기존 `demo_public` 값은 읽거나 판단하지 않는다. API 접근 정책과 다른 보관함 제외는 유지한다. 무로그인 배포에서는 반환되는 제품·포장 정보가 공개되므로 환자 식별정보·원문·메모는 응답에 포함하지 않는다.
- 한 요청에서 최대 100개 평가 항목 × 2페이지를 강한 일관성으로 읽고 제품·포장을 결합한다. 전체 조회가 끝나고 제외/잘못된 대상 포장이 없으면 `inventory_status=CONFIRMED`, 나머지는 `PARTIAL`이다. `CONFIRMED`는 **DB 포장 목록 조회 완성 여부**이며 실물·기한·효능·안전성 보증이 아니다. 빈 포장 목록도 완전한 조회이면 `CONFIRMED`다. 10.1의 제품 전용 API는 구버전 계약으로 유지한다.
- 홈에는 포장 자료를 표시한다. 케어는 증상 선택 후 효능 문구 검색 결과만 정보로 표시하며, 단계별 약품 유형과 보유·사용 주의 판정은 표시하지 않는다. `care_assessment`를 생성하지 않는다.
- 홈은 제품만 등록된 경우에도 3D 보관함을 항상 표시하고 제품 목록을 함께 제공한다. 포장·추정 좌표가 없으면 3D 안의 약품을 임의 생성하지 않고 위치 정보 미확인을 안내한다. 케어 첫 화면에는 전체 약품을 나열하지 않는다. 상세의 효능 원문 검색 결과는 일반 건강정보보다 먼저 표시하며, **등록된 (증상) 관련 약품**에서 제품을 하나의 목록으로 표시하고 보관 여부를 추정하지 않는다. 검색은 `STORED` 포장의 일반약·처방약과 미연결 제품에서 `product_info_status=SUCCEEDED`인 효능 문구만 검색한다. 동일 품목코드는 한 번 표시하고 포장 상태·처방 여부·기한은 별도로 표시한다. 반출·소진·폐기 포장에 연결된 제품을 카탈로그로 되살리지 않는다.
- 효능 검색은 32개 증상에 대응하는 명시적 표현·동의어와 띄어쓰기/괄호 정규화를 사용한다. 이름·성분·주의사항으로 효능을 추측하지 않고, 부정·제외 문장은 근거에서 제외한다. 감기 등 적용 조건을 놓치지 않도록 효능 문구 전체와 제품 사용정보를 제공한다. 이 정보는 의료 전문가 검토를 받지 않았으며 개인별 진단·치료·복용 판단을 제공하지 않는다. 일치 없음·효능 누락·부분 조회·이전 조회를 구분하며 검색 결과를 복용 권장이나 `care_assessment` 승인으로 표시하지 않는다. 표현 사전에 없는 문구는 검색되지 않을 수 있다.
- 읽기 API는 중첩 `product_info` 외에 현재 테이블의 최상위 `indications`, `dosage`, `precautions`도 지원한다. 최상위 형식은 `product_info_status=SUCCEEDED`일 때만 사용하며 출처를 `DYNAMODB_PRODUCT_INFO`로 구분한다. 기존 OCR의 `document_text` 변환기를 배포 ZIP에 포함해 호환용 평문을 반환하고 `product_document.py`가 XML에 명시된 표와 셀 병합을 별도의 선택 필드로 보존한다. 잘린 XML·엔터티·과도한 문서는 미확인으로 두고 기존 항목을 수정하지 않는다.
- 홈과 케어의 약품 카드에는 저장된 제품 용법·용량을 최대 세 줄로 미리 보여주고 제품 사용정보에서 전체 내용을 확인하게 한다. 이름 뒤 괄호 내용과 HTML 문자표현 정리는 화면 표시에만 적용하며 원본 제품명·검색·저장 데이터는 바꾸지 않는다. 원문 XML에 표 구조가 있으면 `product_info.documents`에서 각 필드별 텍스트와 표 블록을 순서대로 전달한다. 표는 `rows[].cells[]`의 `text`, `header`, `rowspan`, `colspan`을 사용하며 HTML을 실행하지 않는다. `documents_incomplete`는 형식을 안전하게 복원하지 못한 필드 이름이다. 구버전·평문 전용·손상·미지원 표나 계산식은 원래 배열을 추정하지 않고 식약처 원문 확인을 안내한다. 제품 사용정보 상세는 용법·용량, 주의사항, 효능·효과, 성분 순이며 없는 내용을 만들어 내지 않는다. 처방약의 제품 허가정보는 개인별 처방 지시와 구분한다.
- WebSocket이 연결되지 않은 온라인 화면은 보이는 동안 30초 간격으로 재조회한다. 숨긴 탭·오프라인·시연은 폴링하지 않으며, 화면 복귀·재연결·알림 클릭의 즉시 재조회도 유지한다. 성공한 조회 이후 백그라운드 갱신은 로딩 배너로 화면을 이동시키지 않고 같은 포장·치수는 3D 장면을 다시 생성하지 않는다. 조회 실패·부분 조회는 별도로 알린다. 브라우저 캐시나 장기 AWS 키를 사용하지 않는다.
- DynamoDB 콘솔 입력 예시는 [백엔드 안내](../backend/README.md#dynamodb에-보유-약품-추가하기)를 따른다.

### 10.3 수동 처방 묶음과 AWS 공유 복약 기록 (2026-11-08)

`/schedule`의 **복용 중인 약**은 `frontend/src/medicationPlans.ts`의 별도 모델과 `useMedicationPlans.ts`의 전용 API 연결을 사용한다. `backend/medication_api.py`가 같은 상태를 검증·저장하며 `deploy_medication_api.py`가 전용 자원의 사전 점검·배포를 담당한다. 기존 `Dose.package_id` 응답과 공개 재고·Pi·OCR·Service Worker 계약은 바꾸지 않는다.

| 모델 | 주요 필드와 의미 |
|---|---|
| `MedicationPlan` | `id`, `title`, `timing`, `notes`, `medicines`, `endedOn`. 사용자 처방 묶음이며 `package_id` 없음 |
| `MedicationItem` | `id`, `name`, `strength`, `dose`, `timing`. 함량과 복용량은 사용자 입력 문자열, `timing=null`이면 공통 복용법 적용 |
| `MedicationTiming` | `mode: regular \| as_needed`, `schedule: MedicationSchedule \| null`. 정기 복용도 일정 없이 목록만 등록 가능 |
| `MedicationSchedule` | `startDate`, `endDate`(빈 문자열이면 미정), `weekdays`(0=일~6=토), `times`(`HH:mm` 여러 개) |
| `MedicationRecord` | `occurrenceId`, `result: all \| some \| none \| deferred`, `takenMedicineIds`, `recordedAt`. 사용자가 확인한 회차의 결과 |
| `MedicationState` | `version: 1`, `plans`, `records`. 서버·클라이언트가 구조를 검증하는 팀 공유 상태 |

- 묶음 이름과 정기/필요 시 복용 방식은 필수다. 일정은 선택이며 사용할 때 실제 날짜·요일·시간을 검증한다. 종료일 역전·중복 시간·중복 식별자는 허용하지 않는다. 개별 약 정보 없이 묶음만 등록할 수 있으며 추가한 약은 이름을 입력한다. 이름·함량·1회 복용량·안내를 처방 지시로 자동 보완하지 않는다.
- 사용자가 입력한 공통 일정 또는 약별 예외에 따라 선택한 한국 날짜의 회차를 계산한다. 정기 회차에는 그 시간에 예정된 약만 포함하며 필요 시 복용약은 제외한다. 개별 약이 없을 때만 묶음 자체의 일정으로 회차를 만든다. 종료 후 미래 회차는 생성하지 않는다. 화면 회차 계산은 서버 예약이나 푸시 발송이 아니다.
- `all`은 해당 회차에 예정된 약 전체의 사용자 확인, `some`은 일부 복용이다. 개별 약이 있으면 복용한 약 ID를 기록하고, 개별 약 정보가 없으면 일부 복용의 세부 약품을 추정하지 않는다. 등록·시간 경과·약통 반출로 `all`을 생성하지 않는다. 약 정보·복용법·일정을 바꾸면 수정 안내 후 기존 확인 기록을 초기화하며 제목·메모 수정은 기록을 유지한다. 종료일까지의 기록은 보존한다.
- 사용자가 후속 승인한 **비밀번호 없는 공개 시연** 범위다. 공개 모드에서는 누구나 같은 목록의 약 이름·일정·복용 기록·메모를 조회·수정·삭제할 수 있다. **합성 시연 데이터만 입력하며 개인정보는 입력하지 않는다.** 공개 범위와 개인정보 관련 안내는 문서에 유지하며 사용자의 요청에 따라 복약 화면에는 표시하지 않는다. 계정별 소유권·개인 신원·변경자 감사 기능은 제공하지 않는다. 기본 비공개 모드에서는 기존 공통 비밀번호와 2시간 메모리 토큰을 사용한다.
- `localStorage`·IndexedDB·Service Worker 캐시·로그에 복용약·비밀번호·접근 토큰을 남기지 않는다. 공개 웹 설정은 잠금 화면 없이 즉시 서버 목록을 읽으며, 새로고침 후에도 자동 조회한다. 비공개 모드의 토큰은 메모리에만 두므로 새로고침·직접 잠금·시연 전환 때 인증을 잃는다. 어느 모드든 새로고침은 DynamoDB 데이터를 지우지 않으며 저장 실패 시 로컬 저장으로 대체하지 않는다. 앱 메모리 시연은 API를 호출하지 않는 별도 모드다.
- 저장 중 중복 조작을 막고 실패 시 입력창을 유지한다. 손상되거나 지원하지 않는 버전의 서버 상태를 빈 목록으로 감추지 않는다. 조회 가능한 화면이 보일 때 30초마다, 화면 복귀·수동 새로고침 때 최신 목록을 조회한다. 편집 시작 당시 묶음과 현재 내용, 서버 revision을 확인해 다른 기기의 변경을 덮어쓰지 않는다. 복용 확인창도 열었을 때의 확인 기록과 최신 기록을 비교하므로 자동 재조회 이후 오래 열린 창에서 다른 기기의 확인을 덮어쓰지 않는다. 묶음 삭제 시 연결된 복용 기록도 삭제한다.
- 개인 복용약·기록·메모는 기존 공개 재고 API에 포함하지 않는다. 이 등록은 재고·3D·성분/DUR 검사·자동 차감·복약 알림을 생성하지 않는다.

#### 전용 API·DynamoDB 계약

| 경로 | 요청 | 성공 응답과 접근 |
|---|---|---|
| `POST /session` | `{ password }` | 비공개 모드에서 `{ token, expires_at }`. 비밀번호 검증 후 2시간 세션 발급 |
| `GET /medications` | 공개 모드는 인증 없이, 비공개 모드는 Bearer 토큰 | `{ revision, state }`. 저장 범위는 서버의 단일 보관함 설정 |
| `PUT /medications` | 같은 모드별 인증, `{ expected_revision, state }` | 조건부 저장 후 `{ revision, state }`. 현재 버전과 다르면 `409` |

- 전용 Lambda·HTTP API 이름은 `smart-medikit-medication-api`, DynamoDB 테이블은 **`inha-capstone-02-medkit-medications`**다. 기존 공개 `inha-capstone-02-medkit-data`를 사용하지 않는다. 서버의 **`MEDKIT_MEDICATION_PUBLIC_DEMO`가 정확히 문자열 `true`일 때만** 공개 GET/PUT을 허용한다. 기본값은 `false`이며 기존 비밀번호 검증을 유지한다. 프론트엔드는 `VITE_MEDICATION_API_URL`과 공개 화면용 `VITE_MEDICATION_PUBLIC_DEMO=true`를 설정한다. 웹 플래그만으로 서버 인증을 생략할 수 없고 비밀번호·서명키·AWS 자격증명은 웹 설정에 넣지 않는다.
- 키는 `pk=BOX#<서버에 설정한 box_id>`, `sk=MEDICATION_STATE`다. 항목에는 `revision`, `state`, `updated_at`을 저장한다. 클라이언트가 제출한 `box_id`로 저장 범위를 바꾸지 않으며, 항목이 없을 때만 `{revision:0,state:{version:1,plans:[],records:[]}}`로 시작한다. 손상 항목은 `503`으로 알려 보존한다.
- `PUT`은 먼저 이전 상태를 읽고 입력·허용 전이를 검증한 뒤 `revision` 조건부 쓰기로 원자 갱신한다. 최초 쓰기는 키가 없을 때만 허용한다. 저장 성공 응답에 새 revision을 반환하며 동시 변경 충돌을 `409`로 구분한다. 타임아웃만으로 저장 실패·성공을 단정하지 않고 재조회한다.
- 본문과 정규화된 상태는 각각 **300KiB(307,200바이트)** 이하로 제한한다. 최대 묶음 100개, 묶음당 약 30개, 일정당 시각 12개, 전체 확인 기록 2,000개다. 날짜·시간·식별자 중복·알 수 없는 필드·기록의 회차/약 연결을 서버에서 다시 검증한다.
- 비공개 모드의 비밀번호는 서버의 PBKDF2-SHA256 솔트 해시로 검증한다. 토큰은 HMAC 서명, 만료, audience, 서버 `box_id`를 검사한다. 공개 모드는 GET/PUT의 세션 검증만 생략하며 입력·상태 전이·크기 제한·revision 검사는 유지한다. 정확한 HTTPS origin과 `Authorization`/`Content-Type` CORS를 적용하지만 CORS를 인증이나 개인정보 보호 수단으로 취급하지 않는다. 응답은 `Cache-Control: no-store`다.
- 비공개 모드의 인증 누락·잘못된 비밀번호·만료는 `401`, 허용하지 않은 origin은 `403`, 충돌은 `409`, 크기 초과는 `413`, 잘못된 상태는 `422`, API 요청 제한은 `429`, 설정/저장소 문제는 `503`으로 구분한다. 비밀번호·토큰·복용약·AWS 예외 원문을 오류 응답이나 로그로 출력하지 않는다.
- 배포 도구는 기존 학교 역할 `SafeRole-inha-capstone-02`를 재사용하며 IAM 정책을 수정하지 않는다. 테이블 생성 시 `dynamodb:TagResource` 권한을 요구하는 `ManagedBy` 태그는 전달하지 않는다. 테이블은 학교 자동 태그 **`username=inha-capstone-02`, `group=inha-capstone` 모두 일치**와 정확한 계정·리전·테이블 ARN·키 구조·인덱스·과금 설정으로 확인한다. 기존 `ManagedBy`가 있으면 그 값도 일치해야 한다. Lambda·API·로그 그룹은 계속 `ManagedBy=smart-medikit-medication-api`를 요구하며 다른 소유자의 동명 자원은 사용하지 않는다. 요청 기반 과금·삭제 보호 테이블, 7일 로그 보존, 경로별 초당 2회/순간 5회 요청 제한을 설정한다. 계정 여유가 있을 때만 Lambda 동시 실행 2개를 예약한다. 기존 역할의 전체 권한이 이 테이블로 제한된다는 의미는 아니다.
- 공개 모드 전환은 배포 도구의 명시적 `--public-demo`로 요청하며, 옵션을 생략하면 기존 배포의 접근 모드를 보존한다. 새 비공개 배포의 비밀번호는 숨김 터미널 입력으로 받고 파일·명령 인수에 저장하지 않는다. 비밀번호 교체는 서명키도 바꿔 기존 토큰을 만료시킨다. 기존 비밀번호 배포 기록과 새 공개 시연의 배포·검증 결과를 구분해 기록한다.

## 11. 공통 제품 OCR Lambda 저장 계약 (2026-10-31)

`ocr-lambda/`는 첨부 ZIP의 제품 식별 계층을 가져온 구현이다. 이 절은 해당 코드의 입출력과 저장 계약을 설명하며, 위 Pi 세션·웹 API 초안의 배포 완료를 뜻하지 않는다.

- 입력은 허용 버킷·prefix의 S3 ObjectCreated 이미지다. 객체 버전 또는 ETag를 고정해 읽고, 문서 종류 메타데이터는 사용하지 않는다. 전체 이미지 OCR 결과의 위치·크기·거리로 제품명과 부가정보를 구분한다. 한 이미지에 여러 제품 후보가 있으면 모두 검증해야 제품 저장을 실행한다.
- `event-id`/`event_id` S3 메타데이터가 있으면 그 값을 멱등 키로 쓴다. 없으면 기존 ZIP의 S3 이벤트 해시를 유지한다. 호출자는 같은 논리 이벤트에 안정된 ID를 제공해야 하며 성공 ID를 다른 이미지에 재사용하면 충돌이다. ID·객체 키에 개인정보를 넣지 않는다.
- 기존 테이블 `inha-capstone-02-medkit-data`, 소문자 `pk`/`sk`, `MEDICINE#<ITEM_SEQ>` + `META`, `RECOGNITION#<event_id>` + `META`를 유지한다. 이는 ZIP 코드에서 확인한 규칙이며 실제 AWS 테이블은 조회하지 않았다.
- **사용자의 후속 지시에 따라 실패 이력은 유지한다.** 필수 OCR·추출·매칭·검증·저장 단계가 실패하면 제품을 쓰지 않고 `RecognitionEvent`만 `FAILED`로 기록한다. `failure_stage`, `failure_code`, `retryable`, `processed_at`, S3 참조를 포함하며 원문·환자정보·미확정 제품 후보는 저장하지 않는다. 실패 저장 자체가 불가능하면 안전한 로그와 Lambda 예외로 남긴다.
- 성공은 `processing_status=COMPLETED`, `recognition_status=SUCCEEDED`와 단계별 `ocr_status`/`extraction_status`/`matching_status`를 기록한다. 선택적 성분 API 실패는 `ingredients_status=UNAVAILABLE`이며 OCR 실패로 집계하지 않는다. `NO_DATA`도 안전성 확인 완료를 뜻하지 않는다. 과거 정상 성분은 장애 시 지우지 않고 최근 조회 상태·마지막 성공 시각을 함께 제공한다.
- 모든 제품과 완료 이벤트는 한 `TransactWriteItems`로 저장한다. 완료 이벤트 조건이 중복 제품 갱신을 막고, 실패 기록은 완료된 성공을 덮어쓸 수 없다. 실패 이벤트 및 구버전 미완료/임대 만료 이벤트는 같은 키에서 재처리 가능하다. 사전 `PROCESSING` 쓰기는 없으므로 동시 호출의 중복 OCR 계산은 가능하다.
- 포장 수량은 `RecognitionEvent.items[].package_quantity={value, unit}` 또는 `null`로 기록한다. 이미지 관측값이며 실제 재고·잔여 회분을 뜻하지 않는다. 제품의 `dosage_form`과 `manufacturer`는 추가 필드이며 기존 `form` 값은 `TABLET | CAPSULE | OTHER`를 유지한다. 확인되지 않은 함량·업체·포장 수량은 만들어 내지 않는다.
- 이 Lambda는 입출고·재고·Package·웹 `kind`를 생성하지 않는다. 같은 품목의 여러 OCR 영역은 제품 캐시 하나로 합치지만 같은 물리 포장이라고 단정하지 않는다. `REVIEW_REQUIRED` 사용자 확인은 **제품 식별 후 물리적 포장/동작이 모호한 계층**에서 별도로 처리한다.
- Pi의 완료 manifest·`sequence` 처리 및 `GET /capture-events`와 연결되지 않았다. 여기의 제품 인식 `COMPLETED`를 Pi 세션 완료로 직접 반환하면 안 된다. 현재 Pi 호출부와 웹 조회 어댑터를 변경하지 않으며 AWS 기본 비활성화를 유지한다.
- S3 사진은 기존 보존 정책에 맡기고 이 코드에서 삭제하지 않는다. 웹 보완 OCR의 개인정보 제거·임시 원본 삭제 계약은 별도 경로에 유지한다. 런타임 진입점·설정·검증 범위는 [OCR Lambda 안내](../ocr-lambda/README.md)를 따른다.

### 11.1 약봉투 구성 약품 행 확장

- 문서 종류 분기 없이 열 제목 또는 반복되는 약품명·분류 위치를 제품 목록 근거로 사용한다. 성분·외형·복약 설명과 영수증·머리말은 제품명과 분리한다. 모든 선택된 제품 행이 검증되어야 기존 성공 트랜잭션을 실행하며 잘린 이름은 `TRUNCATED_PRODUCT_NAME` 실패 이벤트로 기록한다.
- 기존 키와 API 필드는 유지한다. 같은 제품의 반복 행은 `items`에 남기고 `medicines`는 품목코드별 하나다. 복용량·횟수·일수와 봉투 잔여 회분은 아직 계산하지 않으며 `prescription_status=NOT_EVALUATED`다. 업로드 전 개인정보 제거와 사용자 확인 계약은 그대로 적용한다.

### 11.2 OCR 진단 로그

`OCR_DIAGNOSTICS=1`(기본)에서 원문 없는 영역 좌표·점수·후보 개수·첫 매칭 거부 조건별 개수를 CloudWatch에 기록한다. 원본 이벤트 ID는 로그에서 SHA-256 `event_ref`로 대체하고 `attempt_id`로 재시도를 구분한다. 이미지·OCR 원문·제품명·품목코드·성분·API 응답·S3 경로는 진단에 넣지 않는다. DB 이벤트 ID·성공 트랜잭션·실패 기록 계약은 유지하며 진단 필드는 DB에 추가하지 않는다. 상세 한도와 코드 의미는 OCR README의 진단 절을 따른다. 이 변경은 인식 임계값이나 매칭 조건을 완화하지 않는다.

### 11.3 괄호 성분명 검증 보완

목록의 성분 필드만으로 공식 제품명의 괄호를 검증할 수 없을 때, 이름 본문·업체가 먼저 일치하는 후보에 한해 같은 품목코드의 공식 성분 상세로 확인한 뒤 제형·함량까지 최종 대조한다. 공식 성분과 일치하는 괄호에 들어 있는 제형·농도도 검증된 필드로 사용하며 임의로 추정하지 않는다. 이 조회가 식별 근거에 필요한 경우 장애는 `matching` 실패이며 약품을 저장하지 않는다. 식별 완료 뒤의 선택적 성분 보완 장애와 구분한다. 성공 근거는 재사용하고 로그에는 조회 횟수·고정 코드만 추가한다. 키·입출고·재고 계약은 변경하지 않는다.

### 11.4 상표 영역 재검출

최초 전체 이미지와 후속 전체/전경/큰 글자 영역의 검출·인식 호출은 최대 4회다. 같은 영역의 고신뢰 함량·제형·수량 충돌은 `failure_stage=ocr`, `failure_code=OCR_TEXT_CONFLICT`로 실패한다. 이름 철자의 고신뢰 대안은 같은 물리 영역으로 묶어 공식 품목 대조 후 유일한 품목만 채택하며 둘 이상이면 `AMBIGUOUS_PRODUCT`다. 큰 상자의 줄 분리·좌표 대조 조건은 [OCR Lambda 안내](../ocr-lambda/README.md)를 따른다. 최종 식별 임계값 0.9를 유지한다. 진단 `ocr_regions`의 숫자 `pass_index`와 `source_box` 및 모든 좌표는 원본 기준이다.

### 11.5 문자 가설과 필수 제품 후보

약품 행·명시적 제형/함량은 필수 제품 후보이며 모두 검증해야 성공 트랜잭션을 실행한다. 상대적으로 큰 이름 영역도 우선 필수 대상으로 다루되 아래 11.7의 공식 무결과·인접 문맥 조건을 만족하는 경우만 제외한다. 그 밖의 작은 문자 가설은 낮은 신뢰도 또는 품목 무결과만으로 다른 검증된 제품을 실패시키지 않는다. 작은 가설도 고신뢰 근거와 유일한 공식 품목이 확인되면 저장 대상에 포함한다. 모호한 실제 품목·API 장애·검증되지 않은 동일 이름의 성분 괄호는 무시하지 않으며 빈 성공 결과는 금지한다. 사진 전체의 제품 검출 완전성이나 재고 확정을 뜻하지 않는다.

세로로 긴 저신뢰 필수 조각은 기존 인식기로 회전 없이 최대 8개까지 다시 읽고 같은 문자열을 0.9 이상으로 판독한 경우에만 점수를 갱신한다. 내부 재사용 인터페이스가 없으면 `OCR_RUNTIME_INCOMPATIBLE`로 실패한다. 판독 대안·검색 상한 초과도 안전한 고정 코드로 실패한다. 진단에는 후보 근거·대안 개수·제외 사유·회전 보정 전후 점수만 추가하고 원문은 기록하지 않는다. 기존 DynamoDB 키·성공 원자 저장·실패 이벤트 저장·멱등성·Pi/웹 API 필드는 유지한다.


### 11.6 공식 성분 함량과 허가정보 보완 (2026-11-01)

- 기존 `Medicine`에 `product_info` 객체를 추가한다. `indications`(효능), `dosage`(용법), `precautions`(일반·전문 주의사항), `storage_method`(저장방법), `shelf_life`(사용기간), 공식 `source`·`source_updated_at`·`field_errors`를 저장한다. 식약처 허가 상세의 동일 품목코드 응답만 사용한다. XML/CDATA는 제목·문단·표 순서를 유지한 평문이며 화면에서 HTML로 실행하지 않는다.
- `ingredients[]`의 `amount`, `unit`, `basis`를 검증하고 `source`를 보존한다. `QNT` 누락은 동일 품목 `MATERIAL_NAME`의 명시적 성분명·함량·단위·기준이 일대일 대응할 때만 보완한다. 중복 기준·염 형태 차이·상충 값·미지원 형식은 추정하지 않는다. 원료 목록의 첨가제를 주성분으로 추가하지 않는다.
- 성분 상태에 `PARTIAL`을 추가한다. `ingredients_missing_fields`는 성분 인덱스별 누락 필드 경로다. `product_info_status`는 `SUCCEEDED | PARTIAL | NO_DATA | UNAVAILABLE`이며 `product_info_missing_fields`와 `product_info_error_code`를 제공한다. 두 조회가 모두 완전한 경우에만 `enrichment_status=SUCCEEDED`, 그 외는 `INCOMPLETE`다. **기존 `COMPLETED`는 식별 결과 저장 완료를 뜻하며 정보 보완 완료·DUR 검사 완료가 아니다.**
- 성분·허가정보별로 새 완전한 결과를 갱신한다. 불완전한 결과는 해당 캐시가 없는 경우에만 저장하고 기존 값을 보존한다. 조회 시각 `ingredients_checked_at`/`product_info_checked_at`, 마지막 완전 성공 시각 `ingredients_last_success_at`/`product_info_last_success_at`으로 최신 조회 상태와 기존 캐시를 구분한다. 이벤트의 `medicines`는 이번 조회 스냅샷이다.
- 사용기간은 공식 문구이며 제조일·실제 인쇄 만료일·개봉일이 없는 상태에서 포장별 만료일로 환산하지 않는다. 성분별 함량을 복합제의 단일 `strength`나 개인별 복용량으로 합치지 않는다.
- 제품 검증과 추가 정보 조회는 분리한다. 추가 정보 장애/누락은 제품 식별을 OCR 실패로 바꾸지 않는다. 미검증 제품은 저장하지 않으며 실패 이벤트 기록, 성공 원자 저장, 멱등 키는 유지한다. 저장 크기 한도 초과는 문서를 잘라 완료시키지 않고 실패 처리한다.
- `api_enrichment` 로그에는 품목·성분·허가 문구 원문 대신 주성분 행 수, 원본 함량 누락 수, 명시적 상세 행 수, 보완 수, 고정 상태·누락 필드만 기록한다.
- 완전한 완료 이벤트는 재조회하지 않는다. 일시적인 정보 조회 장애로 미완료인 이벤트는 아래 11.8에 따라 동일 이벤트 재전달 시 정보만 복구한다. OCR·식별 결과를 다시 계산하거나 영구 누락/보완 한도 초과 후 갱신하려면 새 `event_id`를 사용한다. 자동 전체 백필·재고 변경·원격 데이터 삭제는 수행하지 않는다.
- 이 추가 필드는 OCR 내부 저장 계약이다. 아직 연결되지 않은 Pi 세션·프론트엔드 스냅샷 API의 필드를 임의 변경하지 않는다. 웹 연동 시 허가정보와 정보 조회 상태를 전달하는 별도 매핑 검증이 필요하다.


### 11.7 일반 포장 영역·이름 결합 보완 (2026-11-01)

- 균일 배경의 전체 전경을 첫 재검출 ROI로 사용하고, 겹친 작은 제형 경계를 근거로 이름을 다시 읽는다. 임계값은 0.9다. 같은 모델을 사용하며 횟수·입력 크기·판독 대안 상한은 [OCR Lambda 안내](../ocr-lambda/README.md)의 일반 포장 절을 따른다.
- 사전에 없는 인접 이름도 독립/결합 가설을 공식 품목에 대조한다. 다른 독립 약품이나 미검증 별도 품목을 결합으로 숨기지 않는다. 순수 영문 문구는 품목 대조 가설이며 공식 품목이 맞으면 포함한다. 공식 무결과 제목은 행·제형·함량 근거가 없고 유일한 검증 제품에 밀착한 더 작은 문구일 때만 `VERIFIED_PRODUCT_CONTEXT`로 제외한다. 이는 전체 사진의 제품 검출 완전성 보증이 아니다.
- `RecognitionEvent.items[].package_size`는 선택적 관측 포장 용량 `{value: Decimal, unit: "ml" | "g"}` 또는 `null`이다. 단독 용량을 주성분 함량·재고·잔여량으로 변환하지 않는다. 기존 `package_quantity`와 구분하며 `Medicine` 함량은 공식 품목/성분으로 검증한다. 액·연고의 상위 `form=OTHER`를 유지한다.
- 기존 PK/SK·테이블·상태·실패 이벤트·원자 저장·멱등성은 유지한다. 이 필드는 OCR 내부 이벤트 정보이며 아직 연결하지 않은 Pi/웹 API·재고 필드를 변경하지 않는다. 연구 선택과 실제/Mock 검증 범위는 OCR 안내에 기록한다.


### 11.8 실제 API 검증 및 정보 복구 (2026-11-01)

- 입력은 JPG/JPEG·PNG만 유지한다. WebP 지원은 추가하지 않는다. OCR 대체 판독·결합 가설도 최초 후보와 동일한 제품명 형식 검사를 거친 뒤 검색한다. 기호가 섞인 무효 검색어는 공식 API에 전달하지 않으며, 유효한 이름들의 모호성·API 실패는 계속 실패 조건이다.
- API 요청은 기본 8초 소켓 제한, 최대 3회 시도, 지수 지연과 jitter를 사용한다. 인증·TLS·형식 오류는 자동 반복하지 않는다. 일시 네트워크/서버 오류는 제한적으로 재시도한다. 이벤트당 최대 64회 요청이며 실제 Lambda context가 있으면 저장용 15초를 남기도록 요청 시간과 다음 시도를 제한한다. 이는 OCR 실행 시간 또는 전체 응답 시간 보장이 아니다.
- 호출 내부의 정상 페이지를 최대 16개·각 64KiB까지만 재사용한다. 실패 응답은 캐시하지 않는다. 전체 약품 카탈로그의 영구 동기화나 새 AWS 자원은 추가하지 않는다.
- `RecognitionEvent.medicines[]`의 `ingredients_retryable`, `product_info_retryable`는 각 정보 조회의 일시 장애 여부다. 기존 이벤트의 `API_TIMEOUT` 등 알려진 통신 오류도 보완 대상으로 읽는다. `PARTIAL`, `NO_DATA`, 영구 오류는 자동 반복하지 않는다.
- 최초 식별 성공 뒤 일시적인 정보 조회 장애가 있으면 성공 트랜잭션을 보존하고 Lambda 호출을 오류로 종료하여 기존 S3 비동기 재시도를 요청한다. **함수 오류가 곧 제품 인식 실패라는 뜻은 아니다.** 재전달은 저장된 품목코드로 미완료 정보만 조회하며 OCR·제품 검색·완전한 정보 조회는 생략한다.
- 보완 결과는 해당 `Medicine` 갱신과 완료 이벤트 `medicines` 갱신을 하나의 트랜잭션으로 저장한다. `enrichment_attempts`(없으면 0)와 `enrichment_checked_at`을 기록하며 최대 두 번 보완한다. 버전·입력 fingerprint·완료 상태 조건으로 동시 보완의 중복 갱신을 막는다. `items`, 식별 품목코드/이름/제형/함량/업체, 최초 `processed_at`, 인식 성공 상태는 변경하지 않는다.
- 보완 후에도 정보가 부족하면 `INCOMPLETE`를 유지한다. 두 번의 보완이 끝나면 추가 자동 재시도를 요청하지 않는다. 완전한 중복 이벤트는 계속 건너뛴다. 기존 실패 이벤트 저장과 테이블/PK/SK, 입출고·재고 계약은 유지한다.
- `api_request` 진단은 허용된 작업 종류(search/permit_detail/ingredients), 시도 번호, 소요 밀리초, 고정 오류 코드만 기록한다. URL·키·검색어·응답 원문은 남기지 않는다. `API_TIMEOUT`, `API_DNS_ERROR`, `API_TLS_ERROR`, `API_CONNECTION_ERROR`, `API_TIME_BUDGET_EXHAUSTED`를 구분한다.
- 자동 재전달은 기존 Lambda 비동기 재시도 설정에 의존한다. 이미 종료된 과거 이벤트는 ZIP 교체만으로 자동 재실행되지 않는다. 같은 S3 이벤트·입력 버전/ETag를 재전달하거나 새 촬영 이벤트를 사용해야 한다. 이번 작업은 Lambda 설정·IAM·원격 데이터 쓰기를 실행하지 않는다.
