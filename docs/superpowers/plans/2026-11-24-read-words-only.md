# 읽힌 문구 단계별 검색 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 0.9 이상 읽힌 문구만 개별/결합 검색하여 후처리 오식별을 방지하고 전체 Lambda ZIP을 제공한다.
**Architecture:** Pi 전용 package_matching 모듈에서 검색과 관측 속성 기반 후보 선택을 수행하고 PackageReader가 재검출한다. 기존 문서 matcher와 저장 계약은 유지한다.
**Tech Stack:** 기존 Python 3.12/PaddleOCR/OpenCV/표준 라이브러리, 추가 서비스 없음.
**Spec:** docs/superpowers/specs/2026-11-24-read-words-only-design.md

## Global Constraints
- 검색/결합은 점수 0.9 이상, 실제 읽힌 문구와 명시된 영문 표기 변환만 사용. API 후보에서 읽히지 않은 이름을 만들어 검색하지 않는다.
- 개별 문구 → 다른 문구 → 인접 결합 → 재검출. 여러 API 품목은 관측 제형·함량만으로 좁힌다.
- 저장·S3·Pi·DB 불변. 운영 자동 배포/추가 S3 업로드/DB 정정 없음.
- OCR 최대 4회, 긴 변/회전 2048px, 새 API 클라이언트 64회·고유 검색어 32개·시작 전 저장 여유 30초.
- 기존 legacy_api 보완의 전체 예산 보장은 별도. 함수 앞 한국어 주석, 민감 원문·키·사진 Git 금지.

## Review Focus
- API 다건을 이름 줄기 비교만으로 한 건으로 만들지 않는가.
- 읽히지 않은 제품명·제형·오타 교정을 검색어에 주입하지 않는가.
- 결합 순서·부가 문구·다른 포장이 단일 등록을 잘못 만들지 않는가.
- 단일 영문자와 긴 세로 영어의 구분·제형에 포함된 상표 글자가 잘못 해석되지 않는가.
- 오류/예산·새 모듈 배포 패키징·기존 저장 호환이 유지되는가.

### Task 1: 읽힌 문구 검색과 선택
**Files:** Create `ocr-lambda/backend/package_matching.py`, `ocr-lambda/tests/test_package_search.py`; Modify `ocr-lambda/backend/pi_package.py`.
**Interfaces:** `PackageMatcher(api,min_score,diagnostics).match(lines)` → 검증 단일 medicine dict; 이벤트 동안 검색/성분 캐시 공유. 기존 match_package/identify 반환 경계 유지.
- [x] 개별→후속→결합 검색, 낮은 문구 제외, 제형 관측 유무, 포타겔 줄기 분해 방지, 추정 오타 검색 금지, 복수 독립 품목/불완전 API 오류 테스트 작성·RED 확인.
- [x] Pi 전용 matcher 구현·연결. 카탈로그 단일 결과 또는 관측 속성으로 유일해진 결과만 등록한다.
- [x] 새 테스트·기존 OCR 전체 GREEN. 의도적으로 바뀐 Pi 저신뢰 실패 코드는 근거를 기록한다.

### Task 2: 영어·재검출과 패키징 연결
**Files:** Modify `ocr-lambda/backend/image_ocr.py`, `ocr-lambda/backend/pi_package.py`, `backend/deploy_pipeline_fixes.py`; extend tests.
**Interfaces:** 긴 세로 영어 제외/모든 단일 영문자의 일반 처리; PackageReader의 동일 모델·최대 4회 재사용. 배포 도구의 medikit_package_ocr에 새 모듈 포함.
- [x] 단일 영문자/영문 한글 변환, 긴 영어의 세로 제외, 모드코 다건→재검출→S 포함 검색, 공간 결합·예산 및 새 모듈 포함 테스트 RED.
- [x] 필터·마지막 국소 재검출·배포 모듈 연결 구현. 기존 전달 코드/원문 로그는 보존.
- [x] 관련·전체 OCR/배포 패치 GREEN 및 실제 원본/모델의 읽기 전용 재판독 검증. 결과에 따라 읽히지 않은 제품은 성공시키지 않는다.

### Task 3: 검증·리뷰·전체 ZIP
**Files:** Modify `ocr-lambda/README.md`; 전달 ZIP은 저장소 밖 `/Users/suyoung/Documents/lambda-code-upload-20261125-v2.zip`.
- [x] OCR 전체·배포 패치·Pi 전송·ruff·diff 검증. 저장/전송 바이트 동일 확인.
- [x] fresh-context 최종 리뷰, 중요 지적 1회 TDD 수정·전체 검증.
- [x] 이전 실제 운영 ZIP의 나머지를 보존하여 전체 ZIP 생성·원본/모듈/실행 wrapper 권한/무결성 검증. 다운로드 폴더에 저장.
- [x] 저장소 AGENTS 지침대로 main commit/push, 실제 배포는 하지 않는다.

## 실행 기록
- 기준 main `393ee26` (원격 팀 Pi 변경 반영). OCR baseline `276 passed, 14 skipped`.
- Ruling: 사용자 AGENTS에 따라 별도 worktree/브랜치 없이 main에서 작업한다. 격리 브랜치는 없지만 팀 변경은 보존한다.
- Ruling: 사용자 후속 지시로 API 이름을 이용한 자동 오타 교정을 하지 않는다. 실제로 슈퍼자임을 읽지 못하면 재검출 후에도 확인 대기일 수 있다.
- Ruling: 가로 긴 영문은 검증된 상표 대응만 지원한다. 미등록 상표의 범용 발음 변환은 정확도를 보장할 수 없어 임의 생성하지 않는다.

## 최종 검증 기록 (2026-11-25)
- 팀 원격 변경 70cdfe0까지 보존하여 main에 rebase했다. 기존 저장/전송 소스 변경은 없다.
- 최종 리뷰의 9개 중요 지적은 독립 회귀 재현 후 수정했다. 실제 판독 점검에서 추가 확인한 숫자 단독 검색 및 관측 속성 누락도 RED→GREEN으로 수정했다.
- 최종 OCR 312 passed/14 skipped, 배포 패치 17 passed, Pi 전송/큐 24 passed 및 3 subtests, Ruff/diff 검사 통과. 생략된 실제 모델 선택 테스트는 통과로 세지 않는다.
- 13장 실제 원본과 기존 운영 모델·공식 API를 읽기 전용으로 실행했다. 이후 최종 후처리 변경은 같은 입력 영역/옵션의 모델 출력과 실제 API 응답을 고정하여 다시 검증했다. 최종 6건 단일 품목 확정, 7건 확인 대기. 이 결과는 운영 S3/DB 저장 시험이나 지연 지표가 아니다.
- 남은 확인 대기: 의약외품 002, 품목 모호성 004/005/006/011/012, 판독 제형 충돌 013. 미지원 제형/공식 괄호 검증 불가 결과를 버려 유일성을 만들거나 읽히지 않은 문구를 정답으로 주입하지 않았다.
- 전체 ZIP은 기존 실제 운영 ZIP에서 OCR 모듈만 교체/추가하며 진입점·저장 코드·실행 wrapper 권한은 보존한다. 업로드·배포는 실행하지 않는다.
