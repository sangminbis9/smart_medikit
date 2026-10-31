# OCR Lambda

첨부 `lambda-code.zip`의 실행 진입점과 `backend/` 모듈을 이 폴더에 가져왔다. S3 이미지 → PaddleOCR → 식약처 제품 대조 → 기존 DynamoDB 테이블 흐름을 유지한다. 이 모듈은 **제품 정보와 인식 이벤트만 저장**한다. 포장 생성·입출고·재고 차감·실제 복용·사용기한을 확정하지 않는다.

## 변경 사항

- `backend/lambda_function.py`: 이미지 종류 메타데이터를 읽지 않고 `ocr(data)` 하나로 처리한다. `event-id` 또는 `event_id` S3 메타데이터가 있으면 멱등 키로 사용하며, 없으면 기존 버킷·키·버전·ETag·sequencer 해시를 유지한다. S3 버전 또는 IfMatch로 입력을 고정하고 실패는 안전한 예외로 전파한다.
- `backend/image_ocr.py`: 전체 이미지 OCR을 수행한다. 표 검출·칸 개수·문서 종류에 따른 실패 조건을 제거했다. 글자의 수평 거리·겹침·높이와 제한된 의미 관계를 모두 검사해 조각을 결합한다. 이름과 무관한 가격·광고·로고·포장 수량은 제품명에 붙이지 않는다.
- `backend/recognition.py`: 제형 없는 상표도 검색한다. 제품명·관측 함량·세부 제형·명시된 업체명을 대조하고, 결과가 여러 품목이면 실패한다. 알려진 상표 외에는 임의 오타 교정이나 영문 전체 치환을 하지 않는다. 필수 검증이 완료된 뒤 선택적 성분 조회를 수행한다.
- `backend/storage.py`: 제품과 완료 이벤트를 단일 `TransactWriteItems`로 저장한다. **최신 사용자 지시를 반영하여 실패 정보는 DynamoDB에 유지**하되 실패 이벤트만 저장하며 미검증 제품은 저장하지 않는다. 성공 이벤트를 실패가 덮어쓰지 않도록 조건을 건다.

기본 `OCR_MIN_SCORE=0.9`는 유지했다. 기존 전체 행의 최소 점수는 옆 광고 문구 때문에 정상 제품명도 실패시켰다. 지금은 실제 이름 구성 요소의 최소 점수를 `name_score`로 보존한다. 제품 선택에 사용한 제형·함량도 0.9 이상이어야 한다. 낮은 신뢰도의 별도 포장 수량은 저장하지 않고 제품명의 점수에는 영향을 주지 않는다. 이는 정확도 보정이 완료된 확률이 아니며 실제 모델 임계값 검증은 남아 있다.

## 저장 계약과 실패 처리

테이블은 **`inha-capstone-02-medkit-data`**, 키 이름은 소문자 **`pk` / `sk`**다. ZIP의 기존 키 규칙을 확인해 유지했으며 실제 AWS 테이블 조회는 수행하지 않았다.

| 엔터티 | pk | sk | 저장 조건 |
|---|---|---|---|
| Medicine | `MEDICINE#<식약처 ITEM_SEQ>` | `META` | 모든 제품 후보의 필수 검증 성공 |
| RecognitionEvent | `RECOGNITION#<event_id>` | `META` | 완료 또는 안전한 실패 이력 |

- 필수 제품 필드: 9자리 품목기준코드, 코드에 연결된 `medicine_id`, 공식 제품명, 공식 제품명의 검증 가능한 제형, 일치한 OCR 근거. 함량은 명시적 단위가 확인될 때만 저장하며 `탁센400`의 숫자만 보고 400mg을 만들어 내지 않는다. 제조사·포장 수량·함량이 확인되지 않으면 `null`이다.
- `Medicine`은 기존 필드를 유지하고 `dosage_form`, `manufacturer`를 추가한다. `RecognitionEvent.items`의 `name_score`, `package_quantity`는 이미지에서 관측한 근거다. 포장 수량은 실제 잔여량이 아니며 동일 제품의 다른 포장에 적용하지 않는다.
- 내부 성공 단계는 `ocr_status`, `extraction_status`, `matching_status`로 구분한다. 선택적 성분 조회는 `ingredients_status=SUCCEEDED | NO_DATA | UNAVAILABLE`로 구분하며 제품 대조 성공을 OCR 실패로 바꾸지 않는다. 기존 성분 캐시는 장애 시 지우지 않으며 최근 조회 상태와 `ingredients_last_success_at`을 함께 해석해야 한다. 안전성 검사 완료를 뜻하지 않는다.
- 필수 단계 실패는 `processing_status=FAILED`, `failure_stage`, `failure_code`, `retryable`, 처리 시각, 재처리용 S3 참조를 이벤트에 저장한다. 제품 후보·OCR 원문·환자 정보를 실패 레코드나 CloudWatch 로그에 넣지 않는다. 오류 로그는 원본 이벤트 ID 대신 SHA-256 `event_ref`, `stage`, `error_code`를 포함한다. 추가 진단 로그 범위는 아래 진단 절을 따른다. S3 객체 키와 event_id에도 개인정보를 사용하지 않아야 한다.
- 신규 `PENDING`/`PROCESSING`/`REVIEW_REQUIRED` 레코드는 만들지 않는다. 실패 레코드는 같은 ID에서 성공으로 교체 가능하다. 기존 ZIP이 남긴 `FAILED`/`REVIEW_REQUIRED`/`PENDING`과 임대가 만료된 `PROCESSING`도 재처리할 수 있다. 기존에 잘못 저장된 제품을 자동 삭제하거나 정정하지는 않는다.
- 성공 이벤트의 조건부 기록이 제품 갱신과 같은 트랜잭션에 있으므로 중복 완료가 제품을 다시 갱신하지 않는다. 응답 유실 뒤 재시도에서도 완료 기록을 읽어 건너뛴다. 같은 성공 ID의 다른 S3 입력은 `EVENT_SOURCE_CONFLICT`다. 메타데이터 ID가 없으면 재업로드가 다른 S3 이벤트로 취급될 수 있으므로 호출자가 안정된 ID를 제공해야 한다.
- 사전 DB 임대를 없앴기 때문에 동시 호출이 OCR/API 계산을 중복 실행할 수는 있다. 최종 저장은 조건부 트랜잭션으로 한 번만 반영한다. 제품 수를 25개로 제한해 완료 이벤트를 포함한 단일 트랜잭션 범위 안에 둔다.
- Lambda 비동기 실패는 고정 예외로 반환해 기존 재시도 정책에 맡긴다. 영구 인식 오류도 AWS 설정에 따라 제한적으로 재실행될 수 있다. `retryable=false`는 자동 재시도 가치가 낮다는 의미이며 수정된 모델로 수동 재처리하는 것은 차단하지 않는다.
- 성공/실패 이미지 모두 이 코드에서 삭제하지 않는다. 기존 S3 보존 정책을 따른다. 이 입력 경로는 웹 처방 보완용 임시 업로드 경로와 분리해야 하며 그 경로의 삭제 계약을 대체하지 않는다.

## 실행과 통합 경계

기존 자원·권한·S3 알림·배포 형식은 이 변경에서 수정하지 않았다.

- ECR 컨테이너의 공통 진입점은 `backend.lambda_function.handler`다. 컨테이너에 호환되는 PaddleOCR/PaddlePaddle/OpenCV와 한국어 PP-OCRv5 모델이 있어야 한다.
- ZIP에 있던 `lambda_function.lambda_handler`, `backend/runtime_loader.py`, `exec-wrapper.sh`도 보존했다. 이 진입점은 관리형 Python 3.12 Lambda에서 해시로 검증한 S3 실행 패키지를 `/tmp`에 준비하는 기존 배포 방식이다. 배포 대상이 ECR인지 이 ZIP 방식인지는 운영 설정에서 확인해야 한다. 새 런타임·모델을 임의로 설치하거나 원격 배포하지 않았다.
- 공통 설정: `OCR_BUCKET`, `OCR_INPUT_PREFIX`(기본 `ocr-input/`), 서버 전용 `MFDS_API_KEY`, `MFDS_API_VERSION`(기본 `08`), `OCR_MIN_SCORE`(기본 `0.9`), `MEDKIT_TABLE`(위 테이블만 허용), `OCR_MODEL_DIR`(컨테이너 기본 `/opt/models`). ZIP 진입점은 기존 `OCR_RUNTIME_BUCKET`, `OCR_RUNTIME_KEY`, `OCR_RUNTIME_SHA256`, `AWS_LAMBDA_EXEC_WRAPPER` 설정도 필요하다. 인증정보는 Git이나 프론트엔드에 넣지 않는다.
- `Raspberry_Pi`의 세션 완료 manifest와 상태 조회 API는 아직 이 이미지별 Lambda에 연결되지 않았다. `COMPLETED`는 여기서는 **제품 인식 결과 저장 완료**이며 Pi의 입출고 세션 전체 완료가 아니다. `sequence` 검사·포장/내부 이미지 통합·API 응답 변환은 기존 세션 처리 계층에서 별도로 구현해야 한다. Pi의 `aws.enabled=false`와 프론트엔드 조회 계약은 유지한다.

## 테스트

폴더 안에서 개발 의존성이 설치된 Python으로 실행한다.

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

실제 사용자 사진은 Git에 포함하지 않는다. 로컬 사진 디코딩 테스트는 다음 환경변수로 별도 활성화한다. 환경변수가 없으면 해당 4개 테스트만 skip한다.

```powershell
$env:OCR_TEST_IMAGE_DIR='사진이 있는 로컬 폴더'
python -m pytest -q
```

`tests/test_recognition.py`의 네 사례는 제공 사진에서 확인한 문구를 **합성 OCR 텍스트·좌표·점수**로 구성했다. 실제 모델 출력이나 실제 식약처 응답이 아니며 가상 품목코드를 사용한다.

| 사진 | 관찰한 핵심 내용 | 검증 구분 |
|---|---|---|
| test-1.jpg | 탁센400, 이부프로펜, 연질캡슐, 가격표 | 실제 JPEG 디코딩 + 합성 영역 결합·제품 대조 |
| test-2.jpg | 화이투벤, 큐, 플러스, 연질캡슐, 증상 문구 | 실제 JPEG 디코딩 + 세부 상표 보존·동일 브랜드 모호성 |
| test-3.jpg | 모드코S, 연질캡슐, 종근당 로고 | 실제 JPEG 디코딩 + 영문/한글 상표 대응 |
| test-4.jpg | 지르텍정, 10정, 증상 문구 | 실제 JPEG 디코딩 + 제형 결합·포장 수량 분리 |

2026-10-31 로컬 검증: **99개 테스트 통과**(실제 사진 4개 디코딩 포함), Ruff 검사·포맷 확인·Python 컴파일 확인 통과. 저장소의 기존 `Raspberry_Pi/.venv/Scripts/python.exe`를 사용했다. PaddleOCR가 설치되어 있지 않아 모델 추론을 실행하지 않았고, 실제 식약처 API·AWS도 실행하지 않았으므로 사진 인식 성공률은 산출하지 않았다.

## 실환경 검증 필요 항목

1. 네 원본 사진을 배포 모델로 추론해 텍스트·좌표·제품명 신뢰도를 확인한다. 특히 기울어진 글자, 작게 인쇄된 제형, 화이투벤의 세부 상표, 떨어진 포장 수량을 확인한다. 원문을 CloudWatch나 Git에 저장하지 않는다.
2. 식약처 08 API 권한·페이지 응답·품목기준코드·성분 괄호·ENTP_NAME 의미를 실제 허가정보와 대조한다. 허가명이 가공 규칙으로 검증되지 않으면 실패 처리한다. 임의로 후보를 고르거나 함량을 보충하지 않는다.
3. 실제 테이블의 pk/sk와 기존 항목·권한을 확인하고 동시 S3 이벤트, 트랜잭션 취소, 응답 유실, 동일 ID 재처리를 검증한다. 구버전 활성 임대가 있다면 종료/만료 후 새 코드를 사용한다.
4. S3 보존 정책·인증·로그, Lambda 메모리/시간 제한·재시도·실행 패키지 또는 컨테이너 버전을 확인한다. 현재 이미지당 최대 25개 후보와 보수적인 결합/노이즈 규칙은 모든 약봉투 양식의 완전한 검출을 보장하지 않는다. 누락된 OCR 글자는 이 코드만으로 알아낼 수 없다.
5. 제품 인식 결과를 Pi 세션 완료·재고·웹의 `kind`나 안전성 확정으로 바로 변환하지 않는다. 그 연결은 별도 계약 검증이 필요하다.

## 근거

- [종근당 모드코S 제품·모드코에스 허가명](https://www.ckdpharm.com/product/productView.do?prodCode=CKD0000525): 제한된 `S` ↔ `에스` 별칭의 근거.
- [탁센 공식 제품 설명서](https://tak-sen.com/web/pdf/guide_ibu.pdf): `탁센400`과 전체 제품명 대응의 근거.
- [식약처 의약품 제품 허가정보 API](https://www.data.go.kr/data/15095677/openapi.do): 기존 제품·주성분 조회 경로의 원천.
- [PaddleOCR 공식 출력 예시](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/quick_start.md): `rec_texts`, `rec_scores`, `rec_polys` 어댑터.
- [DynamoDB TransactWriteItems](https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_TransactWriteItems.html): 원자적 다중 항목 저장. 단기 요청 토큰만 의존하지 않고 영구 완료 이벤트 조건을 사용한다.

## 약봉투 뒷면의 여러 약품 행

- `image_ocr.py`는 `약품명/성분` 등 열 제목이 있으면 해당 열을 우선 사용한다. 제목이 없어도 약품명과 오른쪽 대괄호 분류가 두 행 이상 정렬되어 있으면 제품 목록의 근거로 사용한다. 표 선 검출이나 문서 종류 메타데이터는 필요하지 않으며 공통 `identify()`로 대조한다.
- 제품 제목과 인접한 명시적 함량을 선택하고 성분 설명·외형·효능·복약 안내·영수증 열·머리말을 제품 검색에서 제외한다. 제한된 급여 접두 표식 `(한)`/`(비)` 등과 `캅셀`/`캡슐` 표기를 정리한다. 같은 제품이 여러 행에 있으면 관측 행은 유지하고 Medicine만 중복 제거한다.
- 말줄임표로 잘린 이름은 `TRUNCATED_PRODUCT_NAME`으로 실패하며 추정 복원하지 않는다. 하나의 선택된 제품 행이 낮은 신뢰도이거나 매칭에 실패하면 제품은 저장하지 않고 기존 실패 이벤트만 기록한다.
- `1일 3회 5일` 또는 별도 숫자 열은 포장 수량·재고·복용 일정으로 변환하지 않는다. 현재 확장 범위는 **구성 약품 식별**이며 복용량·기간의 구조화와 사용자 확인은 후속 구현 대상이다. `prescription_status=NOT_EVALUATED`를 유지한다.
- 새 예시에는 개인식별 영역이 있으므로 첨부 원본을 Git이나 테스트 데이터로 복사하지 않았다. `test_bag_rows.py`는 가상 약품·가상 인적 문구로 두 배치와 오류 조건을 재현한다. 이 필터는 업로드 전 개인정보 제거를 대체하지 않는다.
- 제공한 약봉투 원본에 대한 PaddleOCR 추론은 실행하지 않았다. 작은 글자·기울기·잘린 제목·다단 배치·선택 범위 밖의 광고 및 제형이 빠진 행의 검출 누락은 실제 모델로 검증해야 한다. 일부 글자 미검출만으로 전체 약품 목록의 완전성을 보증하지 않는다.

## 개인정보 없는 OCR 진단 로그

`OCR_DIAGNOSTICS=1`이 기본값이며 `0`으로 끌 수 있다. CloudWatch 진단은 WARNING 수준으로 기록한다. 이 수준은 진단 레코드가 인식 실패라는 뜻이 아니다. DB 저장 필드·인식 임계값·매칭 판정은 변경하지 않는다.

- `diagnostic_version=ocr-diag-v1`: 배포한 진단 코드가 실행됐는지 확인한다.
- `event_ref`: DynamoDB의 `event_id` 문자열을 UTF-8로 SHA-256 해시한 값이다. 호출자가 ID에 개인 이름을 넣어도 로그에 복사되지 않는다. 같은 이벤트의 재시도는 같은 값이며, `attempt_id`는 호출마다 다르다. 기존 오류 로그도 같은 `event_ref`로 연결한다.
- `pipeline_started`: OCR 초기화 전의 진단 실행 표시와 임계값이다. 이미 완료된 이벤트는 중복 방지 때문에 OCR과 진단을 건너뛴다.
- `ocr_regions`: 실제 PaddleReader가 검출한 영역의 `[x1,y1,x2,y2]`와 점수, 이미지 크기다. 좌표는 모델에 입력된 이미지의 픽셀 기준이다. 원문·이미지·텍스트 해시·파일명은 기록하지 않는다. 최대 120개, 초과 개수는 `omitted_regions`다.
- `ocr_candidates`: 이름 후보별 `candidate_index`, 결합 영역, 최대 24개 구성 조각의 좌표·점수·역할(`name`, `form`, `strength` 등), `passes_threshold`를 기록한다. 모든 후보를 첫 실패 이전에 기록하므로 무관한 영역이 후보가 됐는지 좌표로 확인할 수 있다. 점수 표시는 소수점 넷째 자리로 반올림하되 판정은 원래 값으로 계산한다. 별도 부가정보는 최대 8개다.
- `product_matching`: 후보별 API 반환 행 수, 검증 통과한 고유 품목 수, 첫 거부 조건별 개수다. API 반환이 0이면 검색 무결과이며 반환이 있는데 통과가 0이면 아래 검증 조건에서 탈락했다. `MATCH`는 통과 행 수다. 이름·품목코드·함량 값·제조사·API 응답·인증키는 기록하지 않는다.

| 거부 코드 | 의미 |
|---|---|
| ANNOTATION_UNVERIFIED | 공식 제품명 괄호를 성분 필드로 검증하지 못함 |
| NAME_MISMATCH | 정규화·허용 별칭 적용 후 이름 불일치 |
| STRENGTH_MISMATCH | 관측 함량과 공식 제품명 함량 불일치 |
| FORM_MISMATCH | 관측 세부 제형 불일치 |
| MANUFACTURER_MISMATCH | 명시된 업체명 불일치 |
| SCHEMA_INVALID | API 행의 필수 이름·품목코드 형식 누락/오류 |

호출당 진단 JSON을 최대 32건으로 제한한다. API 자체 오류는 기존 안전한 오류 코드로 기록하며 그 경우 제품 대조 요약이 없을 수 있다. 제3자 라이브러리의 DEBUG 로그나 OCR 원문 출력은 활성화하지 않는다. 해시와 좌표는 익명화의 보증이 아니라 데이터 최소화 수단이므로 기존 CloudWatch 접근 제한·보존 정책을 유지한다.

검증: 99개 테스트 통과. 원문·가상 환자 ID·제품명·품목코드·임의 추가 필드의 로그 미포함, NaN 처리, 출력 상한, 진단 비활성화, 매칭 거부 분기, 낮은 신뢰도, 기존 저장 정책을 확인했다. 실제 Lambda에서 발생하는 네 사진의 실패 원인은 새 로그를 수집한 뒤 판별해야 한다.
