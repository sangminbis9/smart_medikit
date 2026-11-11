# 종단 파이프라인 점검 — 2026-11-11

## 판정과 범위

**현재 상태로 Pi만 연결하면 처음부터 끝까지 정상 동작한다고 판단할 수 없다.** 배포 OCR의 이벤트 ID 불일치가 우선 차단 요인이며, 저장소에도 단계 도착 순서·반출 판별·장애 복구 문제가 남아 있다.

점검 기준은 `main`의 `c7711fc2cd026adcf19db390f02fc37d3a0bf06e`이다. `hardware/pi`의 포장 촬영 → OCR 조기 전송 → 뚜껑 닫힘 → 내부 비교 → S3 단계 결합 → 재고·반출·알림 → 웹 표시를 추적했다. AWS는 읽기 전용으로 설정과 배포 소스를 확인했다. 이번 변경은 재현 테스트와 점검 문서이며 운영 코드 수정·AWS 배포·실제 재고 변경은 하지 않았다.

합성 이미지와 모의 저장소 테스트는 실제 카메라 정확도, DynamoDB 동시성, 실제 사진 OCR 성공률을 증명하지 않는다. 실기기 촬영부터 시작한 전체 성공 사례는 아직 없다.

## 우선 수정할 문제

### PA-00 · P1 · 배포 OCR과 Pi의 이벤트 ID가 다르다

- Pi [`transport.py`](../hardware/pi/medikit_pi/transport.py)는 포장 S3 객체의 `Metadata.event-id`에 세션의 이벤트 ID를 넣는다.
- [`capture_events.py`](../backend/capture_events.py)의 `reconcile`은 `RECOGNITION#{Pi event_id}`를 조회한다.
- **실제 배포** `inha-capstone-02-ocr`의 `backend/lambda_function.py:59`는 `hashlib.sha256(repr(identity).encode()).hexdigest()`로 별도 ID를 만들며, `event-id`·`explicit_id`·`metadata.get` 처리 코드가 없다. 결과는 그 별도 ID의 `RECOGNITION`에 저장된다.
- 저장소의 [`ocr-lambda/backend/lambda_function.py`](../ocr-lambda/backend/lambda_function.py)는 메타데이터 ID를 사용하는 코드가 있지만 배포본과 다르다.
- 영향: 촬영 이미지의 OCR이 성공해도 Pi 상태 조회에서 결과를 찾지 못하고 `PROCESSING`에 남는다. 웹 제품 캐시가 갱신되는 것만으로 해당 포장의 반입이 완료됐다고 볼 수 없다.
- 수정 방향: 운영 OCR의 기존 기능을 보존하면서 ID 계약을 통합하고, 실제 S3 포장 객체 → 같은 ID의 인식 결과 → 포장·알림 생성까지 검증한다. 파일 해시가 다른 전체 패키지를 검토 없이 덮어쓰면 안 된다.

### PA-01 · P1 · 종료 단계가 먼저 처리되면 재고 반영 없이 세션이 끝난다

- [`capture_events.py`](../backend/capture_events.py)의 `reconcile`은 `SESSION_CLOSED`가 있고 `MOVEMENT_REQUESTED`가 아직 없으면 `REVIEW_REQUIRED`를 반환한다.
- [`outbox.py`](../hardware/pi/medikit_pi/outbox.py)의 `SyncWorker`는 이 상태를 종료로 보고 사진 정리 후 다음 세션으로 넘어간다.
- 뒤늦게 이동 단계가 도착해도 `receive_s3`는 종료 단계 수신 시에만 `reconcile`을 호출하므로 자동 재결합되지 않는다.
- S3 업로드 성공은 Lambda 처리 완료를 뜻하지 않는다. [AWS 공식 문서](https://docs.aws.amazon.com/AmazonS3/latest/userguide/notification-content-structure.html)도 이벤트 알림의 도착 순서를 보장하지 않는다고 설명한다. 서로 다른 단계 객체에 하나의 S3 `sequencer` 비교를 적용하는 것으로 해결되지 않는다.
- 재현: `test_late_movement_must_not_terminate_capture`, `test_late_movement_must_reconcile_after_prior_poll`.
- 수정 방향: 단계 미도착과 실제 판단 불가를 구분하고, 필요한 단계·OCR 결과가 도착할 때 재결합한다. 유실·장기 지연에 대한 별도 마감·복구 경로가 필요하다.

### PA-02 · P1 · 위 약을 꺼내 아래 약이 보이면 반입으로 오판할 수 있다

- [`movement.py`](../hardware/pi/medikit_pi/movement.py)의 `compare_interior`는 `automatic_in`이고 새 검출 후보가 하나면, 사라진 후보가 함께 있는지와 관계없이 먼저 `IN`으로 반환한다.
- 큰 위 포장을 꺼내 작은 아래 포장이 드러나는 합성 장면에서 `IN`이 재현된다. 검출 상자 IoU만으로 기존 포장과 새 포장을 구분하는 데 한계가 있다.
- 영향: 꺼낸 약을 계속 `STORED`로 저장하고 반출 복용 확인·푸시·24시간 폐기 처리가 시작되지 않을 수 있다.
- 재현: `test_revealed_lower_package_must_not_be_inbound`.
- 수정 방향: 사라진 대상·가림 해제와 신규 상단 배치를 구분하고, 촬영한 제품의 기존 상태와 결합한다. 사용자 확인 없는 반입은 유지하되, 단순히 `removed`가 있으면 전부 확인 요청으로 돌리는 변경은 요구사항을 충족하지 않는다. 실제 쌓인 포장 영상이 필요하다.

### PA-03 · P1 · OCR 결과가 없으면 큐 선두가 무기한 멈춘다

- `reconcile`에는 인식 결과 미생성에 대한 경과 시간 제한이나 복구 상태가 없다. 수년 전 종료 시각을 넣어도 `PROCESSING`이다.
- Pi는 종료 객체를 이미 올린 후에는 같은 ID의 상태만 조회한다. 최종 상태 조회가 끝나지 않아 다음 세션의 최종 처리가 막힌다. 로컬 촬영과 조기 단계 업로드가 계속되는 것과 최종 재고 반영이 진행되는 것은 다르다.
- PA-00이 현재 이 현상을 일으킬 수 있고, 향후 OCR 비동기 실행·실패 저장 실패도 같은 결과를 만들 수 있다.
- 재현: `test_missing_ocr_must_eventually_leave_processing`, `test_ocr_wait_keeps_same_session`.
- 수정 방향: 서버 처리 제한 시간·오류 상태·같은 ID 재처리를 구현하고, 관련 없는 다음 세션의 처리와 실패 세션 복구를 함께 설계한다.

### PA-04 · P2 · 누락된 포장 하나가 유지보수 알림 전송 전체를 막는다

- [`notifications.py`](../backend/notifications.py)의 예약 진입점은 `expire_outbound` 이후 `deliver`를 순차 호출한다.
- [`inventory_events.py`](../backend/inventory_events.py)의 `expire_outbound`는 각 포장 조회를 예외 격리 없이 실행한다. 과거 반출 이력의 포장이 삭제되거나 다른 보관함으로 이동돼 404가 나면 새 알림의 `deliver`까지 실행되지 않는다.
- 재현: `test_orphaned_outbound_must_not_block_notification`.
- 수정 방향: 개별 반출 오류를 격리하고 푸시 전송을 독립적으로 진행한다. 합성 시험 포장을 정리할 때 이력의 참조 관계도 점검해야 한다.

## 기능 완성에 필요한 연결

| ID | 위치 / 관찰 | 사용자에게 보이는 결과 | 필요한 작업 |
|---|---|---|---|
| PA-05 · P2 | `inventory_events.transition`은 `candidate.box`를 좌표로 저장하지 않고 `position`·`size_class`를 생성하지 않는다. `Cabinet.tsx`는 두 필드가 없으면 포장을 건너뛴다. | 자동 등록 약은 목록에 생겨도 3D 안에는 안 보일 수 있다. | 실제 카메라 보정·추정 위치/크기 변환과 저장 연결. 임의 좌표를 정확한 위치처럼 만들지 않는다. |
| PA-06 · P2 | 신규 포장의 `initial_qty`·`estimated_qty`는 항상 `None`. NEW 확인은 이를 유지하고 최초 확인을 끝낸다. | 새 정제 포장에서 Y와 복용 수량을 기록해도 초기 수량이 없으면 잔여 수량을 계산할 수 없다. USED 수량 입력 경로는 존재한다. | 포장 수량의 신뢰 가능한 초기값 또는 사용자의 초기 수량 보완 경로. OCR 숫자를 무조건 알약 수로 쓰지 않는다. |
| PA-07 · P2 | `read_api.read_inventory`는 `Limit=100` Scan을 최대 두 번만 수행한다. 같은 테이블의 인식 이력이 계속 늘어난다. | 조회가 `PARTIAL`이 되고 실제 보유 제품·포장이 결과 밖으로 밀릴 수 있다. | 제품·포장 전체를 안정적으로 조회할 키/인덱스·페이지 처리. 현재 누락이 발생했다는 측정 결과는 아니며 코드의 확정된 제한이다. |
| PA-08 · P2 | 서버에서 `REVIEW_REQUIRED`로 끝난 촬영에 대해 후보 포장·방향을 정정하는 웹 연결이 없다. 계약의 `/reviews/{review_id}/resolve`는 초안에 남아 있다. | 인식·방향 판단에 실패한 세션을 사용자가 앱에서 복구할 수 없다. | 서버 확인 요청 이력·조회·정정과 해당 웹 화면 연결. 반출 후 Y/N은 이 기능을 대신하지 않는다. |

PA-05·06의 현재 누락은 `test_new_package_has_no_geometry_or_quantity`에서 실제 전이와 조회 응답을 연결해 확인했다. 이 테스트의 통과는 기능 완성이 아니라 누락 상태를 확인했다는 뜻이다.

## 정상 구간과 검증 결과

| 구간 | 확인 결과 | 검증 수준 |
|---|---|---|
| 포장 촬영 후 OCR 단계 전송 / 닫힘 후 독립 내부 판단 | 관련 Pi 테스트 통과 | 로컬 모의 카메라·전송, 실기기 아님 |
| 이벤트 ID·순번 영속화 / 재전송 | 기존 Pi 테스트 통과 | 로컬 큐·모의 네트워크 |
| 정상 IN → 최초 등록 → OUT → Y/N → 재반입·24시간 조건 | 계약 결합 테스트 통과 | 실제 판단 함수 + 모의 DynamoDB. 실제 트랜잭션 동시성은 이번 테스트 대상 아님 |
| 최초 등록 팝업 / USED 정보 / N 대기 해제 / Y 수량 입력 | PC·모바일 크기 E2E 통과 | Chromium + 합성 API 응답, 실제 iPhone 아님 |
| 실제 신규 등록·반출 푸시와 클릭 이동 | 앞선 작업에서 사용자 수신·클릭 확인 완료 | 합성 서버 이벤트에서 시작. 촬영 → OCR 구간은 포함하지 않음 |
| 24시간 미반입 | 경계 시각과 재반입 무효화 테스트 통과 | 실제 24시간 경과 관측은 미실시 |

이번 점검 실행 결과:

- `hardware/pi`: **91 passed, 5 xfailed, 3 subtests passed**. 추가 경계 테스트 8개 중 정상/현상 확인 3개, 미수정 결함 재현 5개다.
- `backend`: **130 passed, 58 subtests passed**.
- `ocr-lambda`: **217 passed, 14 skipped**. 실제 사진·외부 API 등을 요구하는 생략 테스트를 성공으로 세지 않는다.
- `frontend` 단위 테스트: **61 passed**.
- `MEDKIT_E2E_ACTIVITY=1` 브라우저 테스트: **2 passed** (desktop/mobile).
- 새 Python 테스트 Ruff 검사·포맷 통과. 운영 소스 변경이 없어 프론트 배포 빌드는 이번 점검에서 재실행하지 않았다.

`xfail`은 알려진 실패를 성공으로 숨기는 표시가 아니다. 각 항목에 PA 번호, `strict=True`, `raises=AssertionError`를 지정해 예상한 검증 실패만 허용하며, 수정 후 XPASS가 나면 테스트 표시를 제거하도록 한다. 새 파일은 서버 함수를 가져오므로 기존 Pi 테스트 의존성 외 `boto3` 등 서버 테스트 환경이 필요하다.

재현 명령(저장소 루트에서 적절한 Python 환경 활성화 후):

```powershell
cd hardware/pi
python -m pytest tests/test_pipeline_contracts.py -q -rx
# 미수정 기대 동작을 일반 실패로 표시해 상세 원인을 확인한다.
python -m pytest tests/test_pipeline_contracts.py --runxfail -q
```

## AWS 읽기 전용 확인

- 서울 리전, `inha-capstone-02-s3`: `ocr-input/` 생성 → `inha-capstone-02-ocr`, `medkit-sessions/*.json` 생성 → `smart-medikit-capture-events` 트리거가 설정돼 있다.
- 상태 조회 `GET /capture-events/{event_id}`는 `AWS_IAM`. 공개 시연용 activity/initial/push 경로는 배포 승인 범위의 `NONE` 설정이다.
- `smart-medikit-activity-maintenance`는 `ENABLED`, `rate(1 minute)`.
- capture/activity/notifications의 `MEDKIT_BOX_ID=inha-capstone-02-medkit`, `MEDKIT_ALLOW_ESTIMATED_MOVEMENT=true`, 버킷·prefix가 Pi 설정과 맞는다.
- OCR은 현재 ECR Image가 아니라 Python 3.12 ZIP, handler `lambda_function.lambda_handler`, timeout 300초, 최종 수정 `2026-11-09T07:14:23Z`, Active/Successful이다. 상태가 Active인 것은 전체 파이프라인 성공을 뜻하지 않는다.
- 배포 capture의 `capture_events.py`와 `inventory_events.py` SHA-256이 로컬과 일치했다. PA-01과 재고 연결 누락은 저장소에만 있는 문제가 아니다.

배포 OCR의 소스 비교 증거(비밀값·다운로드 서명 URL은 기록하지 않음):

| 파일 | 배포 SHA-256 | 로컬 SHA-256 |
|---|---|---|
| `backend/lambda_function.py` | `84fc7cd0cc7848293a9d7223583bec82b5fda275044b136fb626bd5ff4e0a7db` | `e5647003ae6f980af5026a15094b2a15f3fd504eafde2ffb035faf7bbd047de5` |
| `backend/storage.py` | `b2a006032fa9a52603832016701f688f667a278848b31f56dff35ed387106c78` | `7983dc1b7b7f0a1a372bfde45e75ec2bfa4ea4bca762713baf977affa92a3644` |

## 다음 통합 완료 기준

1. PA-00의 배포 ID 계약, PA-01/03의 순서·지연 복구, PA-02의 방향 오판을 먼저 해결한다.
2. 알림 실패 격리와 최초 수량·3D·실패 정정을 연결한다.
3. 합성 포장으로 실제 S3 촬영 업로드부터 같은 ID의 OCR → PACKAGE → NOTICE → 홈·복약 화면까지 추적한다. 중복 업로드와 단계 역순도 검증한다.
4. 실제 Pi에서 카메라 방향·초점·조명, 개폐 센서 극성·채터링, 모델·처리 지연, 최초 빈 내부 기준 사진, 쌓인 포장 반출, 오프라인·재부팅 복구를 확인한다.
5. Pi의 단기 AWS 인증 발급과 만료 후 재로그인을 실제 학교 계정에서 확인한다. 이전 CloudShell `check-aws` 성공은 Pi에서 `aws login --remote` 권한이 있다는 증거가 아니다.
6. 동일 약 재반입 후 이전 24시간 폐기 조건이 무효화되는지 실제 저장소에서 확인하고, 실제 iPhone PWA와 Android 푸시는 별도 검증한다.

현재는 반출·재반입 시에도 약 포장을 카메라에 보여줘야 한다. 내부 사진만으로 기존 약품 ID를 특정하는 기능은 아직 없다. 설치 대상은 `hardware/pi`이며 `Raspberry_Pi` 원본 실행으로 대체하면 단계별 전송 흐름이 적용되지 않는다.
