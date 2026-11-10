# DynamoDB 제품·보유 포장 조회와 수동 복용약 API

## 촬영 단계·반출 이력 추가 (2026-11-10, AWS·웹 배포)

`capture_events.py`는 Pi의 단계별 완료 객체와 기존 OCR 결과를 결합하며 `inventory_events.py`는 재고·반출 확인·알림을 조건부 트랜잭션으로 기록한다. `activity_api.py`는 복용 Y/N 및 최초 포장 정보 입력, `notifications.py`는 Web Push 구독·전송 및 24시간 경과 재검사를 담당한다. 세부 계약은 `docs/CONTRACTS.md` 2.2절을 따른다.

`python backend/deploy_activity_api.py`는 계획만 출력한다. `--apply`는 전용 DynamoDB, Lambda 3개, HTTP API, S3의 새 prefix 알림, EventBridge Scheduler와 Amplify 변수를 설정한다. 기존 OCR 트리거·IAM 정책·기존 항목은 보존한다. VAPID 비밀키는 최초 서버 배포에서만 생성하고 이후에는 재사용한다. 배포 스크립트나 로그에 값을 출력하지 않는다. 학교 역할은 `ListRoles`에서 Lambda·Scheduler 신뢰를 확인하고 재사용했다. IAM 정책은 변경하지 않았다.

합성 검증: Pi 전체 테스트 80개, 신규 백엔드 및 기존 조회 API 28개, 배포 회귀 8개, PC·모바일 최초 정보/Y/N 브라우저 테스트 2개 통과. 웹 빌드·타입 검사·lint 통과. Linux Python 3.12용 의존성 다운로드 확인. 실제 Pi 판정 정확도·촬영 전체 경로·24시간 경과·푸시 도착은 아직 검증하지 않았다.

배포 오류 수정(2026-11-10): 최초 실행은 DynamoDB 생성 요청의 수동 소유 태그 때문에 `dynamodb:TagResource`가 거부되어 중단됐다. 테이블 생성에서 태그 전달을 제거하고 학교 자동 태그·ARN·키를 확인하도록 수정했다. Lambda·API에는 `ManagedBy`만 전달하며 미생성 Lambda는 목록으로 확인한다. 기존 조회 Lambda의 필수 `medkit_mfds.py`도 배포 압축에 포함한다. 배포 회귀 6개를 포함한 백엔드 검사 34개가 통과했다.

실제 배포 검증(2026-11-10): 이력 테이블과 Lambda 3개, HTTP API `https://yqcfg2houe.execute-api.ap-northeast-2.amazonaws.com`, S3 단계 트리거, `smart-medikit-activity-maintenance` 예약을 생성했다. 기존 `ocr-input/` OCR 트리거를 보존하고 `medkit-sessions/`의 JSON만 추가 수신한다. 실제 HTTPS에서 `/activity`·`/push/config`는 `200` 및 `no-store`, 빈 확인 입력은 `422`, 무인증 Pi 조회는 `403`, SigV4 Pi 조회는 `200/PROCESSING`을 확인했다. 기존 재고 Lambda 조회 `200`, 유지보수 Lambda 직접 실행 `ok=true`, 예약 `ENABLED/rate(1 minute)`을 확인했다. 실제 촬영 이벤트나 사용자 복용 기록을 이 검증에서 생성하지 않았다.

Amplify `main`에 `VITE_ACTIVITY_API_URL`을 반영했다. 자동 빌드와 동시 실행된 수동 배포 요청의 `LimitExceededException`은 기존 빌드를 취소하지 않고 후속 실행을 기다리도록 수정했다. 커밋 `0f947e8`의 작업 **58번 SUCCEED**, 사용자 Chrome의 앱 업데이트 적용 후 **복약 하단 보관함 복용 기록·확인 대기 0건·기기 알림 받기 버튼**을 확인했다. 브라우저 알림 권한은 아직 허용하지 않았으며 실기기 Pi 설정·단기 자격증명 공급자·GPIO 배선 검증은 별도다.

`read_api.py`는 서울(`ap-northeast-2`)의 **`inha-capstone-02-medkit-data`**를 읽는 별도 Lambda 진입점이다. 기존 OCR Lambda·Pi·테이블·`pk`/`sk`·저장 구조를 변경하지 않는다. 웹의 기존 `useMedkit.ts`가 호출하는 `GET /inventory`, `GET /alerts` 응답으로 변환한다.

수동 등록한 복용약은 아래 [별도 수동 복용약 API](#별도-수동-복용약-api-2026-11-08)의 전용 테이블에 저장한다. 사용자는 후속으로 비밀번호 없는 공개 시연을 선택했으며 기본 비공개 경로도 유지한다. 기존 공개 재고 API의 데이터·접근 정책은 바꾸지 않는다.

## 확인한 실제 환경 (2026-11-02)

- 로그인된 AWS 콘솔에서 테이블과 `MEDICINE#<품목기준코드>/META`, `RECOGNITION#<이벤트>/META`를 확인했다. 표시된 8개 항목은 제품 4개와 인식 이벤트 4개였다. 재고·포장별 사용기한 항목은 없었다.
- 유일한 HTTP API `yakyeon-api`는 외부 HTTP 서버로 프록시하며 `/inventory`, `/alerts`는 인증 없는 요청에 `401`을 반환했다. 이를 이 프로젝트 조회 API라고 추정해 수정하지 않았다.
- Amplify 앱 `smart-medikit-web`의 `main`에 `VITE_API_URL`, `VITE_BOX_ID`가 없었다. 장기 키나 인증 토큰을 수집하지 않았다.

## 실제 배포 (2026-11-02)

- 사용자의 공개 시연·학교 역할 재사용 승인 후 Lambda `smart-medikit-read-api`와 별도 HTTP API를 배포했다. Python 3.12, 128 MB, 제한 시간 10초, 예약 동시 실행 2, API 경로별 초당 1건/버스트 3건이다. 함수 로그 보존은 7일이다.
- API 주소: `https://afqta7t4o0.execute-api.ap-northeast-2.amazonaws.com`, 단일 `box_id`: `inha-capstone-02-medkit`. 기존 테이블에 새 보관함 레코드를 쓰지 않는다.
- Amplify `main`의 공개 변수 `VITE_API_URL`, `VITE_BOX_ID`와 `/care`, `/care/<*>` SPA 규칙을 설정했다. 기존 변수·경로는 보존했다. CORS는 `https://main.d3t4nvt3phq6xc.amplifyapp.com`만 허용한다.
- Lambda 실제 조회와 HTTPS `/inventory`의 `200`, `Cache-Control: no-store`, CORS를 확인했다. `/alerts`는 `200`/`UNAVAILABLE`, 잘못된 보관함은 `403`, 없는 POST 경로는 `404`였다. 최종 강한 일관성 Scan도 전체 항목 0건이었으며 초기 콘솔의 4개 제품과 달랐다. 이 배포는 DynamoDB 항목을 쓰거나 지우지 않는다. 빈 조회도 보유 재고로 확정하지 않는다.
- **실행 역할은 학교 제공 `SafeRole-inha-capstone-02`를 그대로 사용한다.** IAM 정책은 변경하지 않았다. API와 코드는 조회만 수행하지만 역할 자체의 권한은 읽기 전용으로 한정되지 않으므로, 전용 최소 권한 역할로 교체하는 것은 후속 관리자 작업이다.

## 응답의 의미

- `/inventory`: `{box, packages, doses: [], inventory_status, catalog_medicines, catalog_complete, synced_at}`. `MEDICINE#<코드>/META` 제품 정보와 `PACKAGE#<포장 ID>/META` 보유 포장을 `product_codes`로 연결한다. 제품 정보만 추가하면 포장이 생기지 않는다. 날짜·수량·좌표·OTC 여부를 추측하지 않으며 쓰기 API는 없다.
- `inventory_status=CONFIRMED`는 해당 보관함의 포장 레코드를 끝까지 읽었다는 뜻이다. 실물 재고·유효기간·안전성 검증 완료가 아니다. 빈 포장 목록도 전체 조회가 성공하면 `CONFIRMED`다. 페이지 한도 또는 잘못된 포장이 있으면 `PARTIAL`이다. 구버전 제품 전용 API의 `UNAVAILABLE`도 프론트엔드에서 지원한다.
- `product_info.documents`는 원문 XML의 표를 텍스트·표 블록으로 전달하며, 표 셀의 행·열 병합을 유지한다. 미지원·잘못된 구조는 `documents_incomplete`로 표시하고 원문 확인이 필요하다. 기존 평문 필드와 기존 OCR Lambda는 유지한다.
- `/alerts`: `{alerts: [], alerts_status: "UNAVAILABLE"}`. 알림 저장 계층이 없으므로 주의정보가 없다고 확정하지 않는다.
- 식약처 허가 출처 `MFDS_PERMIT_DETAIL`의 중첩 `product_info`와, `product_info_status=SUCCEEDED`인 기존 최상위 `indications`·`dosage`·`precautions`를 읽는다. 최상위 형식은 출처를 `DYNAMODB_PRODUCT_INFO`로 구분하며 `ocr-lambda/backend/mfds.py`의 기존 XML 변환기를 `medkit_mfds.py`로 ZIP에 포함해 재사용한다. 제목 속성·CDATA·문단·표를 평문으로 반환한다. 오래된 테이블 항목에 허가정보가 없으면 `null`이다. `care_assessment`는 자동 생성하지 않는다.
- 한 요청에서 최대 100개 평가 항목 × 2페이지를 `ConsistentRead=true`로 읽는다. `LastEvaluatedKey`가 남으면 `catalog_complete=false`, `inventory_status=PARTIAL`이다. 여러 페이지를 읽는 동안의 변경까지 원자적으로 보장하지는 않는다. 테이블이 커지면 기존 조회 인덱스/접근 패턴을 팀과 합의해야 하며 무한 Scan으로 확대하지 않는다.
- 필드 허용 목록으로 인식 이벤트·원본 OCR·사진 참조·민감 원문을 제외한다. 응답 `Cache-Control: no-store`; 서버/브라우저 로그에 데이터 본문을 남기지 않는다.

## DynamoDB에 보유 약품 추가하기

같은 테이블 `inha-capstone-02-medkit-data`에 **실물 포장마다** 아래 항목을 추가한다. 기존 `MEDICINE#<품목기준코드>/META`와 `product_codes`로 연결한다. 같은 제품 두 포장은 서로 다른 ID를 쓴다. 아래는 DynamoDB 콘솔의 **DynamoDB JSON** 형식이며, 합성 코드 `000000001`과 포장 ID를 실제 값으로 바꾼다.

```json
{
  "pk": { "S": "PACKAGE#my-package-001" },
  "sk": { "S": "META" },
  "package_id": { "S": "my-package-001" },
  "box_id": { "S": "inha-capstone-02-medkit" },
  "kind": { "S": "OTC" },
  "state": { "S": "STORED" },
  "product_codes": { "L": [{ "S": "000000001" }] }
}
```

- `package_id`는 영문·숫자·`_`·`-` 1~100자이며 `pk` 접미사와 같아야 한다. `state`는 `REGISTERED`, `STORED`, `OUT`, `DEPLETED`, `DISPOSED` 중 하나다. 케어 효능 검색에는 `STORED` 포장만 포함한다.
- **`demo_public`을 지정할 필요가 없다.** 사용자 요청으로 항목별 공개 플래그와 일반약 제한을 제거했다. 같은 보관함의 `OTC`·`PRESCRIPTION` 포장을 모두 조회하며 기존 플래그가 `false`여도 포함한다. 무로그인 시연에서는 제품명·보관 상태·입력한 수량/회분·기한·추정 위치가 API 주소를 아는 사람에게 공개된다. 환자 식별정보·처방전 원문·자유 메모는 응답에서 제외한다.
- 제품 레코드가 있으면 이름·성분·허가정보를 함께 표시한다. 연결이 빠지면 `name`(선택 문자열) 또는 미확인 이름으로 포장을 표시하고 효능을 추정하지 않는다. 이름만으로 실제 제품을 확정하지 않는다.
- `MEDICINE` 제품만 저장해도 홈의 **등록된 약품**에서 검색·열람할 수 있다. 케어 상세는 선택 증상과 저장된 효능이 일치하는 제품만 보여준다. 효능 누락·실패인 제품은 케어 검색에서 제외하며 전체 제품은 홈에서 확인한다. 보유 포장과 연결되지 않았으면 보관 상태·수량·유효기간은 미확인으로 표시하며 재고를 자동 생성하지 않는다.
- `expiry`(Map)의 `actual_expiry`(String, `YYYY-MM-DD`)와 `verified`(Bool)는 실물 기한을 확인한 경우에만 입력한다. `temporary_management_date`는 별도 임시 날짜다.
- `inventory`(Map)의 `stock_enabled`(Bool), `initial_qty`·`estimated_qty`(Number)는 정제·캡슐 일반약의 예상 수량이다. 시럽·연고 등에는 반환하지 않는다. 누락된 날짜·수량·위치는 `null`, 안전정보는 기본 `SAFETY_CHECK_PENDING`이다.
- 웹은 새로고침·화면 복귀 때 조회하며 WebSocket이 없으면 보이는 화면에서 **30초 간격**으로 다시 조회한다. 항목 추가·수정에는 재배포가 필요 없다.
- 홈과 케어 첫 화면·증상 상세의 **보관 중인 약품**에 같은 포장을 표시한다. 증상별 약품 역할의 보유 판정에는 별도 검토된 효능·기한·안전정보가 필요하다. 읽기 API는 승인된 효능 매칭을 생성하지 않는다.

실제 촬영에서 `PACKAGE`를 쓰는 입출고 처리기는 이번 조회 연결의 범위가 아니다. 기존 OCR의 `MEDICINE`·`RECOGNITION` 저장과 Pi 이벤트는 유지한다. 조회 확인용 합성 항목은 `test_only=true`로 표시하고 검증 후 별도 검증용 `box_id`로 옮겨 실제 보관 목록에서 제외한다.

실제 연결 검증(2026-11-07): MFA 로그인 후 비어 있던 테이블을 확인하고, 사용자 승인으로 `f18d827`의 조회 Lambda 코드만 갱신했다. Amplify `main` 작업 28번도 같은 커밋으로 성공했다. 합성 포장 `integration-check-20261107-f18d827` 1건을 조건부 추가한 뒤 실제 HTTPS 홈·케어 첫 화면·증상 상세에서 30초 조회로 표시되는 것을 확인했다. PC·390px 모바일에서 화면 넘침·페이지 예외·콘솔 오류가 없었고, 유효기간은 미확인으로 유지됐다. 이후 해당 항목만 `box_id=integration-archive-20261107`, `demo_public=false`로 옮겼으며 열린 홈·케어에서 자동 제외되는 것까지 확인했다. 검증 항목은 별도 테스트 보관함에 남아 있고, 실제 보관함 목록은 0건이다. 실제 제품 연결과 수량·기한 정규화는 합성 단위 테스트로 검증했다.

## 배포 시 필요한 변경과 승인 범위

기존 함수만 갱신할 때는 MFA 인증한 CloudShell에서 같은 커밋의 두 Python 파일을 준비한 뒤 `python backend/deploy_read_api.py --account 842544789367 --update-code-only`를 실행한다. 이 모드는 소유 태그·계정·진입점과 `RevisionId`를 검사하고 **코드만** 교체하며 IAM·역할·환경변수·API·Amplify 설정을 바꾸지 않는다.

학교 계정의 `SafePowerUser`가 `iam:CreateRole`을 명시적으로 거부하여 전용 역할 방식은 사용할 수 없었다. 학교 제공 `SafeRole-inha-capstone-02`는 Lambda 신뢰가 있지만 `SafeBaseRolePolicy` 내용 조회도 `iam:GetRolePolicy` 권한 부족으로 거부되어 읽기 전용 역할이라고 보증할 수 없다. 사용자가 이 차이를 확인하고 학교 역할 재사용을 별도 승인했다.

학교의 `username`·`group` 소유 태그는 자동 태깅 정책이 관리한다. 수동으로 이 태그를 덮어쓰지 않는다. 최초 함수 생성 직후 `ControlOnlyOwnResources`로 상태 조회가 거부되면 자동 태그 반영을 확인한 뒤 같은 명령을 다시 실행한다. 이미 생성한 함수와 API는 `ManagedBy` 태그로 식별하며 다른 작업자의 동명 리소스는 덮어쓰지 않는다.

`deploy_read_api.py --account <승인된 계정 ID> --app <Amplify 앱 ID> --apply-public-demo`는 전용 역할 방식이다. 학교 제공 역할을 쓰기로 **별도 승인한 경우에만** `--use-school-role`을 추가한다. 이 모드는 IAM 정책을 변경하지 않고 기존 역할을 새 Lambda에 연결하므로, 코드/API는 읽기 전용이어도 실행 역할 권한은 읽기 전용으로 제한되지 않는다. 두 파일을 같은 디렉터리에 놓고 boto3가 있는 승인된 AWS 세션에서 실행한다. 로컬 장기 키를 만들 필요는 없다.

AGENTS.md 5절의 **“IAM 권한 변경, 서비스 공개 설정은 팀 승인 없이 실행하지 않는다”**에 따라 다음 변경은 승인 후 진행한다. 새 DynamoDB 테이블·인덱스·OCR 변경은 필요 없다.

1. 별도 소형 Lambda에 이 파일을 배포한다. Python 런타임의 boto3를 사용하며 핸들러는 ZIP 루트에 넣은 경우 `read_api.lambda_handler`. 권장 초기 설정: 메모리 128 MB, 제한 시간 10초, 예약 동시 실행 최대 2. 실제 계정 할당량을 먼저 확인한다.
2. 전용 역할 방식의 권장 권한은 기본 Lambda 로그 권한과 **해당 테이블 ARN의 `dynamodb:Scan`만**이다. 학교 역할 모드는 위의 예외를 따른다. 쓰기·삭제·S3·식약처 키·Bedrock 권한은 코드에 필요 없다. 원문 응답 로그는 사용하지 않는다.
3. 별도 API Gateway HTTP API에 `GET /inventory`, `GET /alerts`만 연결한다. payload v2, 기본 스테이지, 최소 요청 제한(예: 초당 1건/버스트 3건)을 설정한다. 기존 `yakyeon-api`와 그 서버는 변경하지 않는다.
4. 기본값은 비공개다. 기존 접근 제어를 연결할 경우 Lambda authorizer의 서버 검증 결과에 `{medkit_read:true, box_id:<일치하는 ID>}`를 제공한다. 클라이언트 헤더나 `box_id`만으로는 통과하지 않는다.
5. **무로그인 시연 공개를 선택해 승인한 경우에만** 서버의 `MEDKIT_DEMO_PUBLIC_READ=true`를 설정한다. 이는 제거한 항목별 `demo_public`과 다른 API 접근 설정이다. URL을 아는 제3자도 제품명·성분·허가정보와 보관함의 일반약·처방약 포장 정보를 조회할 수 있다. CORS나 고정 `box_id`는 인증이 아니다. 개인별 건강정보를 연결하기 전 접근 정책을 다시 정해야 한다.
6. 서버 환경변수 `MEDKIT_BOX_ID`를 팀이 정한 단일 보관함 ID로 설정한다. 이 값은 조회 범위 선택자이며 새로운 저장 키나 데이터 레코드를 생성하지 않는다. `MEDKIT_TABLE`은 생략하거나 위 테이블명과 같아야 한다.
7. API CORS는 Amplify의 정확한 HTTPS origin, 메서드 GET/OPTIONS, `Accept`, credentials 허용으로 제한한다. 와일드카드 origin을 쓰지 않는다. authorizer를 쓰면 쿠키의 SameSite·Secure와 API Gateway 전달 정책도 검증한다.
8. Amplify `main`에 공개 설정 `VITE_API_URL=<새 API 주소>`, `VITE_BOX_ID=<동일 ID>`를 입력하고 재빌드한다. 브라우저에서 제품 캐시 조회와 **보유 여부 미확인** 표시를 검증한다. 실제 보유 매칭에는 별도로 Package·실제 유효기간·안전정보·검토된 효능 데이터가 필요하다.

## 별도 수동 복용약 API (2026-11-08)

`medication_api.py`와 `deploy_medication_api.py`는 처방 묶음·여러 약·공통/약별 일정·전체/일부 복용 확인을 **AWS 단일 공유 목록**으로 저장한다. 비밀번호 모드의 배포·초기 빈 상태 저장·HTTPS 인증 검증 후, 사용자 최종 승인으로 **공개 시연 API 배포·무인증 조회·빈 상태의 조건부 저장과 실제 웹 자동 조회를 확인**했다. 아래 과거 비밀번호 모드 기록과 현재 공개 시연 검증을 구분한다.

| 구분 | 구성 |
|---|---|
| 전용 Lambda·HTTP API | `smart-medikit-medication-api`, Python 3.12, 256MB, 제한 시간 10초 |
| 전용 DynamoDB | `inha-capstone-02-medkit-medications`, 요청 기반 과금·삭제 보호 |
| 공유 항목 | `pk=BOX#<서버 box_id>`, `sk=MEDICATION_STATE`, `revision`, `state`, `updated_at` |
| 접근 모드 | 기본 비공개: 공통 비밀번호·2시간 Bearer 토큰. 서버 `MEDKIT_MEDICATION_PUBLIC_DEMO=true`인 공개 시연만 무인증 GET/PUT |
| 경로 | `POST /session`, `GET/PUT /medications`, `POST /medicine-products/search`, `POST /medicine-products/link`, `POST /medicine-products/auto-link`, `POST /medicine-products/suggestion`, `GET /medicine-products/links`, `GET /medicine-products/{productCode}` |
| 충돌 방지 | `PUT`의 `expected_revision`과 DynamoDB 조건부 쓰기, 충돌 시 `409` |
| 제한 | 요청/상태 각각 300KiB, 묶음 100개·묶음당 약 30개·일정당 시각 12개·전체 기록 2,000개 |

수동 복용약의 제품 정보는 식약처 제품 허가정보 API에서 상세·성분을 조회해 전용 테이블의 별도 품목 캐시에 저장한다. 약 이름 검색은 POST 본문만 사용하며 약품명·API 키·응답 원문을 로그에 남기지 않는다. 조회 데이터는 기존 재고 테이블이나 OCR을 수정하지 않고 개인 처방량·복용 기록을 변경하지 않는다. 제품 연결 API와 캐시·동시 변경 조건은 [CONTRACTS.md 10.3절](../docs/CONTRACTS.md#103-수동-처방-묶음과-aws-공유-복약-기록-2026-11-08)을 따른다. 이 기능은 서버 측 `MFDS_API_KEY`와 Lambda 제품 API 경로의 배포가 완료된 환경에서만 작동한다.

자동 연결은 이름·제형 및 입력 함량이 유일한 품목에 맞을 때에만 수행한다. 같은 이름 후보가 여럿이거나 근거가 부족한 함량·성분은 보류한다. 자동 연결이 실제 약의 일치나 의료적 안전성을 보증하지 않으며, 허가정보의 용법은 개인 처방량을 대체하지 않는다. 상세 조회는 저장된 확인 시각과 캐시를 반환하고, 24시간이 지난 제품을 다시 연결할 때 식약처에 재조회한다. 복용약 API의 식약처 외부 요청은 보관함별 UTC 날짜 기준 하루 최대 100회이며, OCR과 같은 키를 쓰면 실제 식약처 제공 한도는 공유된다. 공개 시연에서는 누구나 제품 검색·연결도 요청할 수 있다.

자동 연결 실제 검증: 기존 약 4개를 대상으로 3개가 연결되고 1개는 불명확해 보류됐다. 연결된 3개는 각 효능·성분·주의사항을 전용 테이블 캐시에서 재조회했으며 복약 상태와 revision은 변경되지 않았다. 약품명·응답 원문은 검증 로그에 기록하지 않았다.

자동 연결이 보류된 경우 유사 품목을 별도 조회해 효능·용법·주의사항을 보여줄 수 있다. 이 후보는 실제 처방약과 같은 제품으로 확정하거나 기존 연결 목록에 넣지 않는다. 식약처 성분 자료의 수치가 서로 충돌하면 동일 품목의 주성분명과 원료명이 일대일로 일치하는 경우에만 이름을 표시하고 함량·단위·기준은 확인 필요로 둔다. 후보의 공개 제품 캐시는 기존 확정 제품 캐시와 분리한다.

동일 성분의 허가 상세 원문에 기준량과 `성분정보`가 같은 행으로 명시돼 있으면 해당 문맥을 별도로 표시한다. 원문과 주성분 상세 API의 다른 수치를 같은 기준이라고 추정하거나 환산하지 않으며, 기존 충돌 상태와 확정 제품 연결은 변경하지 않는다.

유사 품목 실제 검증: 미연결 1개에 대해 API가 `POSSIBLE`과 성분 `CONFLICT`를 반환했고 효능·용법·주의사항 존재를 확인했다. 기존 복약 상태와 확정 연결 목록은 전후 동일했다. 제품의 실제 일치 여부나 충돌한 성분 수치는 확인되지 않았다.

성분명 보완 검증: 같은 후보의 식약처 주성분 상세와 허가 상세에서 일치한 성분명 1개를 확인하고 함량·단위·기준은 비워 반환했다. 처음 조회는 `503`으로 실패했고 원인은 확인하지 못했으며, 재시도와 캐시 재조회는 성공했다. 기존 복약 상태와 확정 연결 목록은 그대로였다.

허가 원문 추가 검증: 같은 미확인 후보의 `ingredientStatements`에서 기준량과 성분정보 원문 1건을 재조회했고 복약 상태와 확정 연결 목록이 보존됐다. 별도 주성분 API의 수치가 다른 이유는 확인하지 못했으며 해당 수치를 환산하거나 원문 성분량과 동일시하지 않는다.

공개 시연에서는 **누구나 같은 목록의 약 이름·일정·복용 기록·메모를 조회·수정·삭제**할 수 있다. **합성 시연 데이터만 입력하고 개인정보는 입력하지 않는다.** 비공개 모드는 공통 비밀번호를 아는 사람만 접근하며 어느 모드든 개인별 소유권·변경자 식별은 제공하지 않는다. 복용약 입력은 재고·3D·OCR·자동 차감·DUR 검사·푸시 예약을 생성하지 않는다. 필드와 오류의 상세 계약은 [CONTRACTS.md 10.3절](../docs/CONTRACTS.md#103-수동-처방-묶음과-aws-공유-복약-기록-2026-11-08)을 따른다.

### 복용약 API 사전 점검과 배포

같은 커밋의 두 Python 파일과 boto3가 있는, MFA를 완료한 학교 AWS 세션에서 저장소 루트 기준으로 실행한다. 아래 첫 명령은 계정·학교 역할·동명 자원 소유 태그·키·기존 연결을 **조회만** 한다.

제품정보 기능 배포에는 기존 `ocr-lambda/backend/mfds.py`도 필요하다. CloudShell에 별도 파일로 올릴 때는 배포 스크립트 옆에 `medkit_mfds.py` 이름으로 둔다. `--mfds-key-from-function inha-capstone-02-ocr` 옵션은 지정된 학교 계정·서울 리전의 OCR 함수에 설정된 식약처 키를 서버 메모리에서 복용약 함수로 재사용하며 기존에 다른 키가 있다면 중단한다. 키를 명령 인수·파일·출력에 넣지 않는다.

```sh
python backend/deploy_medication_api.py --account 842544789367 --region ap-northeast-2 --box-id inha-capstone-02-medkit --origin https://main.d3t4nvt3phq6xc.amplifyapp.com
```

승인된 공개 시연으로 전환할 때는 `--apply --public-demo`를 명시한다. `--public-demo`를 생략하면 기존 배포의 모드를 보존하며 새 배포의 기본값은 비공개다.

```sh
python backend/deploy_medication_api.py --account 842544789367 --region ap-northeast-2 --box-id inha-capstone-02-medkit --origin https://main.d3t4nvt3phq6xc.amplifyapp.com --apply --public-demo
```

- 서버의 `MEDKIT_MEDICATION_PUBLIC_DEMO`가 **정확히 문자열 `true`일 때만** GET/PUT의 세션 검사를 생략한다. 미설정·`false`를 비롯한 다른 값은 기존 비밀번호 경로다. 공개 모드에서도 입력·상태 전이·크기 제한·revision 충돌 검사는 유지하며 공개 재고 API의 `MEDKIT_DEMO_PUBLIC_READ`와 별개의 설정이다.
- **비공개 모드의 최초 공통 비밀번호는 사람이 터미널에서 직접 입력**한다. `getpass`의 숨김 입력으로 12~256자 비밀번호를 두 번 받으며, 숨김 입력을 지원하지 않으면 중단한다. 비밀번호를 채팅·명령 인수·스크립트·파일·빌드 변수에 넣지 않는다. 기존 설정이 있으면 유지하며 명시적으로 `--apply --rotate-password`를 사용할 때만 비밀번호와 서명키를 함께 교체해 기존 토큰을 만료시킨다.
- 학교 제공 `SafeRole-inha-capstone-02`를 재사용하고 **IAM 정책은 변경하지 않는다**. 앞선 학교 정책 조회 제한 때문에 역할 전체가 전용 테이블 최소 권한이라고 보증하지 않는다. 배포 도구는 역할 계정·Lambda 신뢰와 대상 자원 설정을 확인하지만 정책 전체의 권한 범위를 확정하는 도구는 아니다.
- 테이블 생성에는 `ManagedBy` 태그를 전달하지 않는다. 학교 자동 태그 **`username=inha-capstone-02`와 `group=inha-capstone` 두 값 모두**, 정확한 계정·리전·테이블 ARN·키 구조·인덱스·과금 설정을 확인한다. 기존 `ManagedBy` 태그가 있으면 그 값도 대조한다. Lambda·API·로그 그룹은 계속 `ManagedBy=smart-medikit-medication-api`를 요구하며 역할·연결 경로가 다르면 중단한다. 기존 재고 테이블·API·OCR·Pi·Amplify 설정은 수정하지 않는다. 함수 로그 보존은 7일, API 경로별 초당 2회/버스트 5회 제한을 적용하고 계정 여유가 있을 때만 Lambda 동시 실행 2개를 예약한다.
- 배포 도구는 선택한 모드의 접근 정책과 저장소 조회를 확인한다. **항목이 없는 `revision=0`인 경우에만** 빈 상태를 조건부로 최초 저장하고 다시 읽는다. 기존 항목은 비어 있어도 검증을 이유로 수정하지 않는다. 비밀번호 모드 최초 배포의 실제 AWS 저장·재조회 결과는 아래에 기록했다.
- 비공개 배포의 검증용 토큰은 메모리에서만 만들고 2분 뒤 만료되도록 한다. 토큰·비밀번호·복용약 응답 본문·AWS 예외 원문을 로그나 터미널에 출력하지 않는다. 공개 모드는 토큰 없이 GET/PUT을 사용하며 응답은 계속 `Cache-Control: no-store`다. CORS는 지정한 HTTPS origin으로 제한하지만 공개 데이터의 접근 보호 수단은 아니다.
- 성공 시 출력된 공개 `MEDICATION_API_URL`을 Amplify `main`의 **`VITE_MEDICATION_API_URL`**에 입력한다. 공개 화면에는 **`VITE_MEDICATION_PUBLIC_DEMO=true`**도 설정하고 재빌드한다. 웹 플래그만 바꿔도 서버 인증은 유지되므로 서버 공개 전환을 함께 확인한다. 기존 `VITE_API_URL`은 재고용으로 유지하고 비밀번호·접근 토큰·서명키는 `VITE_` 변수에 넣지 않는다.
- 공개 화면은 잠금 없이 즉시 자동 조회하고 새로고침 후에도 AWS 목록을 불러온다. 비공개 화면은 메모리 토큰을 사용하므로 새로고침 후 다시 잠금을 해제한다. 두 모드 모두 브라우저 영구 저장·실패 시 로컬 저장 대체는 제공하지 않는다. 조회 가능한 화면에서 30초마다·화면 복귀 시 재조회하며 쓰기 실패·충돌은 저장 완료로 표시하지 않는다.

### 복용약 API 검증 범위

저장소 루트의 `python -m unittest backend.test_medication_api -v`는 합성 입력과 가상 DynamoDB로 인증 누락·잘못된/변조된/만료된 토큰, 날짜·일정·약 구성, 전체/일부 복용 기록, revision 충돌, 저장 실패·정보 노출 방지 등을 검사한다. 실제 환자 정보나 AWS를 호출하지 않는다.

2026-11-08 비밀번호 모드 검증(공개 전환 이전): **로컬 Python 3.14에서 API 합성 단위 테스트 25개와 배포 도구 테스트 11개 통과**, 배포 파일을 포함한 Python 3.12 문법 호환 검사도 통과했다. 배포 검사는 `python -m unittest backend.test_deploy_medication_api -v`로 실행하며 학교 자동 태그·ARN·키 구조·생성 요청과 코드 활성화 완료 후 최신 `RevisionId` 사용·동시 환경 변경 거부를 가상 AWS에서 확인한다. 프론트엔드 타입 검사·린트·빌드·단위 테스트 55개, PC·모바일의 AWS API 모의 응답 E2E 12개, 기존 화면 회귀 검사 13개도 통과했고 회귀 검사 1개는 제외됐다. 화면 검사 범위는 [프론트엔드 검증 기록](../frontend/README.md#파일과-검증)을 따른다.

### 복용약 API 비밀번호 모드 실제 배포·연결 검증 (2026-11-08)

- 전용 DynamoDB **`inha-capstone-02-medkit-medications`**, Lambda·HTTP API **`smart-medikit-medication-api`**를 서울 리전에 배포했다. API 기본 주소는 `https://wda2durbx5.execute-api.ap-northeast-2.amazonaws.com`이며 당시 데이터 조회·쓰기는 Bearer 인증을 요구했다.
- 실제 Lambda → DynamoDB에서 `AUTHENTICATED_GET_STATUS=200`, `INITIAL_EMPTY_PUT_STATUS=200`, `PERSISTENCE_GET_STATUS=200`을 확인했다. 새 공유 항목의 빈 상태만 조건부 저장했으며 기존 재고 테이블·항목은 변경하지 않았다.
- 실제 HTTPS에서 무인증 `GET /medications`와 `PUT /medications`는 **401**, 사전 요청 `OPTIONS`는 **204**, 짧은 메모리 검증 토큰의 인증된 `GET /medications`는 **200**을 확인했다. 허용 origin이 정확히 일치했고 OPTIONS를 제외한 응답의 `Cache-Control: no-store`도 검증했다. 토큰·응답 데이터 원문은 로그에 남기지 않았다.
- Amplify `main`의 기존 환경변수를 보존하고 **`VITE_MEDICATION_API_URL`**에 위 주소를 추가한 뒤 재조회해 일치를 확인했다. 비밀번호·서명키·토큰은 프론트엔드 변수에 넣지 않았다. 사용자가 터미널에 직접 설정한 공통 비밀번호는 유지했으며 IAM 정책은 변경하지 않았다.
- 최초 테이블 생성의 `dynamodb:TagResource` 거부는 테이블에 수동 관리 태그를 전달하지 않고 학교 자동 소유 태그를 확인하는 방식으로 해결했다. Lambda 생성 후 자동 태그 지연으로 일시적인 `GetFunction` 거부가 발생할 때는 태그 반영 후 같은 배포 명령을 다시 실행해 기존 비밀번호를 유지했다. 코드 활성화 완료 후 최신 `RevisionId`를 사용하되 다른 실행자가 환경을 바꿨으면 중단한다.

공개 시연 전환 로컬 검증(2026-11-08): **API·배포 도구 단위 테스트 총 47개**와 Python 3.12 문법 호환 검사가 통과했다. 프론트엔드 타입 검사·린트·빌드·단위 테스트 **55개**, PC·모바일의 **공개 모드 API 모의 응답 E2E 6개**도 통과했다. 비밀번호 모드 회귀 E2E는 최초 실행에서 11개 통과·개발 서버 HMR로 1개 실패했으며, 실패 항목을 코드 변경 없이 단독 재실행해 통과하여 **총 12개 항목을 확인**했다.

공개 시연 실제 AWS 배포·검증(2026-11-08): 사용자 최종 승인 후 기존 `smart-medikit-medication-api` 코드와 `MEDKIT_MEDICATION_PUBLIC_DEMO=true`를 배포했다. Lambda 무인증 조회는 `200`, 음수 revision을 보낸 쓰기는 `422`였다. 실제 HTTPS에서도 무인증 조회 `200`, 같은 입력 검증 쓰기 `422`, CORS 사전 요청 `204`와 정확한 허용 origin을 확인했으며 사전 요청을 제외한 응답의 `no-store`를 확인했다. 이어서 실제 HTTPS에서 `{version: 1, plans: [], records: []}`인 빈 상태를 읽고 같은 상태를 `expected_revision` 조건으로 저장해 `PUT 200`·재조회 `GET 200`과 내용 일치를 확인했다. 처방 묶음·복용 기록은 0개로 유지되고 revision만 `1→2`로 증가했다. 기존 Amplify `main` 환경변수를 보존하면서 `VITE_MEDICATION_PUBLIC_DEMO=true`와 기존 `VITE_MEDICATION_API_URL`을 확인했고 IAM은 변경하지 않았다.

배포 웹 검증(2026-11-08): 커밋 `7e7b15c5c178d587738b39f54db3ef3315d15bda`의 **Amplify 작업 44가 `SUCCEED`** 상태로 완료됐다. 실제 사용자 Chrome의 `/schedule`에서 기존 PWA 업데이트 버튼을 눌러 적용한 뒤 비밀번호·잠금 없이 AWS 목록을 자동 조회했고, 복용 중인 약 0개·등록 버튼·공개 목록 안내를 확인했다. **실제 약 묶음의 화면 저장·삭제는 실행하지 않았다.** 해당 화면 동작은 공개 모의 API E2E 6개와 비공개 모드 12개 항목으로 검증했으며, 실제 API의 빈 상태 조건부 저장 검증과 구분한다.

## 기존 조회 API 로컬 검증

저장소 루트에서 `python -m pytest backend/tests -q` 또는 `python -m unittest discover -s backend/tests`를 실행한다. Python과 pytest가 필요하며 단위 검증은 AWS·boto3·실제 의료정보를 사용하지 않는다. DynamoDB API는 가상 테이블로 대체해 필드 제외, 페이지 제한, 권한, 미확인 재고, 조회 오류를 검사한다.

2026-11-02: Python 단위 테스트 9개와 구문 검사 통과. 프론트엔드의 합성 API 화면 검증 결과는 [프론트엔드 안내](../frontend/README.md)에 기록했다.

2026-11-07: Python 단위 테스트 16개와 구문 검사 통과. 포장·제품의 페이지 간 연결, 다른 보관함·비공개·처방약 제외, 누락 정보·잘못된 날짜·수량, 빈 재고·부분 조회 및 기존 함수 코드만 교체하는 경로를 검증했다. 실제 환자 정보는 사용하지 않았다.

전체 약품 조회 변경 검증(2026-11-07): `demo_public` 및 일반약 제한 제거 후 Python 단위 테스트 17개와 구문 검사 통과. 플래그 누락·거짓·참과 일반약·처방약의 모든 조합을 공개 API 진입점에서 검증했다. PC·모바일 합성 API E2E는 기존 6개와 새 전체 약품 검색 2개가 통과했고, 이름·성분 검색 및 두 케어 보관 목록의 처방약 표시를 확인했다. 타입·린트·테스트 파일 포맷과 diff 검사도 통과했다. 프론트 제품 코드는 변경하지 않았다.

참고: [DynamoDB Scan과 페이지 처리](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Scan.html), [Lambda와 API Gateway](https://docs.aws.amazon.com/lambda/latest/dg/services-apigateway.html).
