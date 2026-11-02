# 기존 DynamoDB 제품정보 조회 API

`read_api.py`는 서울(`ap-northeast-2`)의 **`inha-capstone-02-medkit-data`**를 읽는 별도 Lambda 진입점이다. 기존 OCR Lambda·Pi·테이블·`pk`/`sk`·저장 구조를 변경하지 않는다. 웹의 기존 `useMedkit.ts`가 호출하는 `GET /inventory`, `GET /alerts` 응답으로 변환한다.

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

- `/inventory`: `{box, packages: [], doses: [], inventory_status: "UNAVAILABLE", catalog_medicines, catalog_complete, synced_at}`. `catalog_medicines`는 **제품 캐시**이며 포장·실제 보유·사용 가능한 재고가 아니다. 날짜·수량·좌표·OTC 여부를 추측하지 않는다. 제품을 보유 상태로 전환하는 API는 없다.
- `/alerts`: `{alerts: [], alerts_status: "UNAVAILABLE"}`. 알림 저장 계층이 없으므로 주의정보가 없다고 확정하지 않는다.
- 식약처 허가 출처 `MFDS_PERMIT_DETAIL`의 효능·용법·주의사항만 선택적으로 반환한다. 오래된 테이블 항목에 허가정보가 없으면 `null`이다. `care_assessment`는 자동 생성하지 않는다.
- 한 요청에서 최대 100개 평가 항목 × 2페이지를 읽는다. `LastEvaluatedKey`가 남으면 `catalog_complete=false`다. 이는 일관된 재고 스냅샷이 아니다. 테이블이 커지면 기존 조회 인덱스/접근 패턴을 팀과 합의해야 하며 무한 Scan으로 확대하지 않는다.
- 필드 허용 목록으로 인식 이벤트·원본 OCR·사진 참조·민감 원문을 제외한다. 응답 `Cache-Control: no-store`; 서버/브라우저 로그에 데이터 본문을 남기지 않는다.

## 배포 시 필요한 변경과 승인 범위

학교 계정의 `SafePowerUser`가 `iam:CreateRole`을 명시적으로 거부하여 전용 역할 방식은 사용할 수 없었다. 학교 제공 `SafeRole-inha-capstone-02`는 Lambda 신뢰가 있지만 `SafeBaseRolePolicy` 내용 조회도 `iam:GetRolePolicy` 권한 부족으로 거부되어 읽기 전용 역할이라고 보증할 수 없다. 사용자가 이 차이를 확인하고 학교 역할 재사용을 별도 승인했다.

학교의 `username`·`group` 소유 태그는 자동 태깅 정책이 관리한다. 수동으로 이 태그를 덮어쓰지 않는다. 최초 함수 생성 직후 `ControlOnlyOwnResources`로 상태 조회가 거부되면 자동 태그 반영을 확인한 뒤 같은 명령을 다시 실행한다. 이미 생성한 함수와 API는 `ManagedBy` 태그로 식별하며 다른 작업자의 동명 리소스는 덮어쓰지 않는다.

`deploy_read_api.py --account <승인된 계정 ID> --app <Amplify 앱 ID> --apply-public-demo`는 전용 역할 방식이다. 학교 제공 역할을 쓰기로 **별도 승인한 경우에만** `--use-school-role`을 추가한다. 이 모드는 IAM 정책을 변경하지 않고 기존 역할을 새 Lambda에 연결하므로, 코드/API는 읽기 전용이어도 실행 역할 권한은 읽기 전용으로 제한되지 않는다. 두 파일을 같은 디렉터리에 놓고 boto3가 있는 승인된 AWS 세션에서 실행한다. 로컬 장기 키를 만들 필요는 없다.

AGENTS.md 5절의 **“IAM 권한 변경, 서비스 공개 설정은 팀 승인 없이 실행하지 않는다”**에 따라 다음 변경은 승인 후 진행한다. 새 DynamoDB 테이블·인덱스·OCR 변경은 필요 없다.

1. 별도 소형 Lambda에 이 파일을 배포한다. Python 런타임의 boto3를 사용하며 핸들러는 ZIP 루트에 넣은 경우 `read_api.lambda_handler`. 권장 초기 설정: 메모리 128 MB, 제한 시간 10초, 예약 동시 실행 최대 2. 실제 계정 할당량을 먼저 확인한다.
2. 전용 역할 방식의 권장 권한은 기본 Lambda 로그 권한과 **해당 테이블 ARN의 `dynamodb:Scan`만**이다. 학교 역할 모드는 위의 예외를 따른다. 쓰기·삭제·S3·식약처 키·Bedrock 권한은 코드에 필요 없다. 원문 응답 로그는 사용하지 않는다.
3. 별도 API Gateway HTTP API에 `GET /inventory`, `GET /alerts`만 연결한다. payload v2, 기본 스테이지, 최소 요청 제한(예: 초당 1건/버스트 3건)을 설정한다. 기존 `yakyeon-api`와 그 서버는 변경하지 않는다.
4. 기본값은 비공개다. 기존 접근 제어를 연결할 경우 Lambda authorizer의 서버 검증 결과에 `{medkit_read:true, box_id:<일치하는 ID>}`를 제공한다. 클라이언트 헤더나 `box_id`만으로는 통과하지 않는다.
5. **무로그인 시연 공개를 선택해 승인한 경우에만** `MEDKIT_DEMO_PUBLIC_READ=true`를 설정한다. 이 경우 URL을 아는 제3자도 인식된 제품명·성분·허가정보를 조회할 수 있다. CORS나 고정 `box_id`는 인증이 아니다. 개인별 재고나 건강정보를 연결하기 전 접근 정책을 다시 정해야 한다.
6. 서버 환경변수 `MEDKIT_BOX_ID`를 팀이 정한 단일 보관함 ID로 설정한다. 이 값은 조회 범위 선택자이며 새로운 저장 키나 데이터 레코드를 생성하지 않는다. `MEDKIT_TABLE`은 생략하거나 위 테이블명과 같아야 한다.
7. API CORS는 Amplify의 정확한 HTTPS origin, 메서드 GET/OPTIONS, `Accept`, credentials 허용으로 제한한다. 와일드카드 origin을 쓰지 않는다. authorizer를 쓰면 쿠키의 SameSite·Secure와 API Gateway 전달 정책도 검증한다.
8. Amplify `main`에 공개 설정 `VITE_API_URL=<새 API 주소>`, `VITE_BOX_ID=<동일 ID>`를 입력하고 재빌드한다. 브라우저에서 제품 캐시 조회와 **보유 여부 미확인** 표시를 검증한다. 실제 보유 매칭에는 별도로 Package·실제 유효기간·안전정보·검토된 효능 데이터가 필요하다.

## 로컬 검증

저장소 루트에서 `python -m pytest backend/tests -q` 또는 `python -m unittest discover -s backend/tests`를 실행한다. Python과 pytest가 필요하며 단위 검증은 AWS·boto3·실제 의료정보를 사용하지 않는다. DynamoDB API는 가상 테이블로 대체해 필드 제외, 페이지 제한, 권한, 미확인 재고, 조회 오류를 검사한다.

2026-11-02: Python 단위 테스트 9개와 구문 검사 통과. 프론트엔드의 합성 API 화면 검증 결과는 [프론트엔드 안내](../frontend/README.md)에 기록했다.

참고: [DynamoDB Scan과 페이지 처리](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Scan.html), [Lambda와 API Gateway](https://docs.aws.amazon.com/lambda/latest/dg/services-apigateway.html).
