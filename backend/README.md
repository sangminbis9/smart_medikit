# 기존 DynamoDB 제품정보 조회 API

`read_api.py`는 서울(`ap-northeast-2`)의 **`inha-capstone-02-medkit-data`**를 읽는 별도 Lambda 진입점이다. 기존 OCR Lambda·Pi·테이블·`pk`/`sk`·저장 구조를 변경하지 않는다. 웹의 기존 `useMedkit.ts`가 호출하는 `GET /inventory`, `GET /alerts` 응답으로 변환한다. 배포 또는 실제 API 연결 완료를 뜻하지 않는다.

## 확인한 실제 환경 (2026-11-02)

- 로그인된 AWS 콘솔에서 테이블과 `MEDICINE#<품목기준코드>/META`, `RECOGNITION#<이벤트>/META`를 확인했다. 표시된 8개 항목은 제품 4개와 인식 이벤트 4개였다. 재고·포장별 사용기한 항목은 없었다.
- 유일한 HTTP API `yakyeon-api`는 외부 HTTP 서버로 프록시하며 `/inventory`, `/alerts`는 인증 없는 요청에 `401`을 반환했다. 이를 이 프로젝트 조회 API라고 추정해 수정하지 않았다.
- Amplify 앱 `smart-medikit-web`의 `main`에 `VITE_API_URL`, `VITE_BOX_ID`가 없었다. 장기 키나 인증 토큰을 수집하지 않았다.

## 응답의 의미

- `/inventory`: `{box, packages: [], doses: [], inventory_status: "UNAVAILABLE", catalog_medicines, catalog_complete, synced_at}`. `catalog_medicines`는 **제품 캐시**이며 포장·실제 보유·사용 가능한 재고가 아니다. 날짜·수량·좌표·OTC 여부를 추측하지 않는다. 제품을 보유 상태로 전환하는 API는 없다.
- `/alerts`: `{alerts: [], alerts_status: "UNAVAILABLE"}`. 알림 저장 계층이 없으므로 주의정보가 없다고 확정하지 않는다.
- 식약처 허가 출처 `MFDS_PERMIT_DETAIL`의 효능·용법·주의사항만 선택적으로 반환한다. 오래된 테이블 항목에 허가정보가 없으면 `null`이다. `care_assessment`는 자동 생성하지 않는다.
- 한 요청에서 최대 100개 평가 항목 × 2페이지를 읽는다. `LastEvaluatedKey`가 남으면 `catalog_complete=false`다. 이는 일관된 재고 스냅샷이 아니다. 테이블이 커지면 기존 조회 인덱스/접근 패턴을 팀과 합의해야 하며 무한 Scan으로 확대하지 않는다.
- 필드 허용 목록으로 인식 이벤트·원본 OCR·사진 참조·민감 원문을 제외한다. 응답 `Cache-Control: no-store`; 서버/브라우저 로그에 데이터 본문을 남기지 않는다.

## 배포 시 필요한 변경과 승인 범위

AGENTS.md 5절의 **“IAM 권한 변경, 서비스 공개 설정은 팀 승인 없이 실행하지 않는다”**에 따라 다음 변경은 승인 후 진행한다. 새 DynamoDB 테이블·인덱스·OCR 변경은 필요 없다.

1. 별도 소형 Lambda에 이 파일을 배포한다. Python 런타임의 boto3를 사용하며 핸들러는 ZIP 루트에 넣은 경우 `read_api.lambda_handler`. 권장 초기 설정: 메모리 128 MB, 제한 시간 10초, 예약 동시 실행 최대 2. 실제 계정 할당량을 먼저 확인한다.
2. 실행 역할에는 기본 Lambda 로그 권한과 **해당 테이블 ARN의 `dynamodb:Scan`만** 부여한다. 쓰기·삭제·S3·식약처 키·Bedrock 권한은 필요 없다. 원문 응답 로그는 사용하지 않는다.
3. 별도 API Gateway HTTP API에 `GET /inventory`, `GET /alerts`만 연결한다. payload v2, 기본 스테이지, 최소 요청 제한(예: 초당 1건/버스트 3건)을 설정한다. 기존 `yakyeon-api`와 그 서버는 변경하지 않는다.
4. 기본값은 비공개다. 기존 접근 제어를 연결할 경우 Lambda authorizer의 서버 검증 결과에 `{medkit_read:true, box_id:<일치하는 ID>}`를 제공한다. 클라이언트 헤더나 `box_id`만으로는 통과하지 않는다.
5. **무로그인 시연 공개를 선택해 승인한 경우에만** `MEDKIT_DEMO_PUBLIC_READ=true`를 설정한다. 이 경우 URL을 아는 제3자도 인식된 제품명·성분·허가정보를 조회할 수 있다. CORS나 고정 `box_id`는 인증이 아니다. 개인별 재고나 건강정보를 연결하기 전 접근 정책을 다시 정해야 한다.
6. 서버 환경변수 `MEDKIT_BOX_ID`를 팀이 정한 단일 보관함 ID로 설정한다. 이 값은 조회 범위 선택자이며 새로운 저장 키나 데이터 레코드를 생성하지 않는다. `MEDKIT_TABLE`은 생략하거나 위 테이블명과 같아야 한다.
7. API CORS는 Amplify의 정확한 HTTPS origin, 메서드 GET/OPTIONS, `Accept`, credentials 허용으로 제한한다. 와일드카드 origin을 쓰지 않는다. authorizer를 쓰면 쿠키의 SameSite·Secure와 API Gateway 전달 정책도 검증한다.
8. Amplify `main`에 공개 설정 `VITE_API_URL=<새 API 주소>`, `VITE_BOX_ID=<동일 ID>`를 입력하고 재빌드한다. 브라우저에서 제품 캐시 조회와 **보유 여부 미확인** 표시를 검증한다. 실제 보유 매칭에는 별도로 Package·실제 유효기간·안전정보·검토된 효능 데이터가 필요하다.

## 로컬 검증

저장소 루트에서 `python -m pytest backend/tests -q` 또는 `python -m unittest discover -s backend/tests`를 실행한다. Python과 pytest가 필요하며 단위 검증은 AWS·boto3·실제 의료정보를 사용하지 않는다. DynamoDB API는 가상 테이블로 대체해 필드 제외, 페이지 제한, 권한, 미확인 재고, 조회 오류를 검사한다.

참고: [DynamoDB Scan과 페이지 처리](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Scan.html), [Lambda와 API Gateway](https://docs.aws.amazon.com/lambda/latest/dg/services-apigateway.html).
