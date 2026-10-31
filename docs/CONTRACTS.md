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
- 시제품 앱은 회원가입이 없어도 **AWS 쓰기 권한을 무제한 공개하지 않는다**. 공개 가능 범위를 최소화하고 쓰기 API·S3 업로드·기기 통신은 별도 제어를 검토한다.
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

## 8. 웹 처방전 보완 OCR 계약 (초안)

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
- 현재 실제 API 쓰기·사진 업로드·Web Push 구독은 실행하지 않는다. 시연 편집은 메모리에만 반영되고 서버 성공 문구를 표시하지 않는다. 구독 소유권·서버 쓰기 계약이 합의되면 기존 경로와 연결한다.
- Service Worker는 정적 산출물만 캐시한다. `push`의 `notification_id`를 알림 tag로 사용하고 일반 문구만 표시한다. 알림 클릭은 같은 출처 `/alerts`를 열고 상태를 재조회할 뿐 확인/복용 API를 호출하지 않는다. 실제 서버 발송·구독은 아직 미연동이다.

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
