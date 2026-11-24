# 포장 OCR 재판독 개선 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 저장 계약을 유지하고 제품명 추출·각도/배율 재판독·공식 품목 대조의 실패 회복을 개선한다.
**Architecture:** 기존 PaddleReader에서 각도 정보를 보존하고, recognition에서 위치 기반 조각 결합·잡음 배제·검증된 영문명 대응을 수행한다. PackageReader는 같은 모델을 순차 재사용하며 성공한 단일 품목만 기존 정보 보완으로 넘긴다.
**Tech Stack:** Python 3.12, PaddleOCR PP-OCRv5, OpenCV, boto3, pytest.
**Spec:** `docs/superpowers/specs/2026-11-24-package-ocr-retry-design.md`

## Global Constraints
- `hardware/pi`, S3 저장 키·메타데이터·단계 JSON, DB 키·멱등성·저장 형식은 변경하지 않는다.
- 운영 배포·IAM·환경변수 변경은 하지 않는다. 기존 OCR 점수 0.9를 낮추지 않는다.
- OCR은 최대 4회, 확대는 2배·4배(입력 긴 변 2048 이하), 저장 여유 30초, API 실제 호출 상한 64·고유 검색어 상한 32를 유지한다.
- 세로 영어 제외, 증상/효능/단독 수식어 검색 제외, 인접 문자 결합, 검증된 영문명 대응, 실패 시 다른 추출 전략을 포함한다.
- 함수 바로 위에 한국어 설명을 작성하고 운영 원문 로그·사진·키를 추가하지 않는다.

## Review Focus
- 다른 품목이 함께 보이거나 대안 제형이 충돌하면 유일해 보이는 첫 품목으로 확정하지 않는다.
- 높이·너비만으로 대각선 영어를 세로 영어로 오판하거나 먼 문구를 결합하지 않는다.
- 낮은 신뢰도·누락 제형·의약외품에서 같은 상표의 다른 의약품으로 성공하지 않는다.
- 확대 한도·남은 시간·API 오류가 재시도 루프에서 사라지지 않는다.
- 영문 변환은 검증된 대응 또는 공식 영문명과 일치해야 하며 미지원 영어를 임의 번역하지 않는다.

### Task 1: 각도·확대 OCR와 후보 기하
**Files:** Modify `ocr-lambda/backend/image_ocr.py`, `ocr-lambda/backend/recognition.py`; Create `ocr-lambda/tests/test_package_retry.py`.
**Interfaces:** 기존 `_predict(..., refined=False, package=False)`와 `group_lines`, `candidate_observations`의 기본 호출을 보존한다. 포장 판독의 내부 line에 각도·사각 좌표를 추가하고 재판독 옵션은 선택 인자로 확장한다.
- [x] Step 1: 세로 영어 제외/대각선 유지, 기울어진 인접·원거리 분리, 위아래 상표 조각 결합, 배율·픽셀 상한 테스트를 작성한다.
- [x] Step 2: `../.venv/bin/python -m pytest tests/test_package_retry.py -q` → 새 동작 부재의 FAIL을 확인한다.
- [x] Step 3: 좌표 기반 필터·인접 관계와 제한된 2배/4배 판독을 구현한다. 기존 문서 OCR 동작은 보존한다.
- [x] Step 4: 위 테스트와 전체 OCR 테스트 → PASS, 기존 실패 없음.

### Task 2: 제품 후보·API 검증과 단계별 재판독
**Files:** Modify `ocr-lambda/backend/pi_package.py`, `ocr-lambda/backend/recognition.py`, `ocr-lambda/backend/mfds.py`; extend `ocr-lambda/tests/test_package_retry.py`.
**Interfaces:** `match_package(data, api, min_score, diagnostics, context=None)`와 `identify` 반환형을 보존한다. Task 1 각도/배율 옵션을 소비하고 기존 `match_products(..., single_package=True)`를 재사용한다.
- [x] Step 1: API 무결과→확대 성공, 결과 상한→다른 검색 전략, 시간/횟수 상한, 증상 검색 차단, 영문 대응, 제형 충돌·의약외품 차단 테스트를 먼저 작성한다.
- [x] Step 2: 새 테스트 → 요구한 동작 부재의 FAIL을 확인한다.
- [x] Step 3: 단일 검증 품목에서만 종료하는 재판독과 구체 후보 전환을 구현한다. 일반 API 장애·모호성은 보존한다.
- [x] Step 4: `../.venv/bin/python -m pytest -q` → 전체 PASS; 저장/메타데이터 회귀도 PASS.

### Task 3: 검증·적용 묶음 제공
**Files:** Modify `ocr-lambda/README.md`; package outside Git under `/Users/suyoung/Documents/medkit-lambda-update-20261124`.
**Interfaces:** 기존 `backend/deploy_pipeline_fixes.py --ocr-only`가 사용하는 파일 배치를 유지한다. ZIP에 저장/전송 코드 변경을 포함하지 않는다.
- [x] Step 1: 전체 OCR 테스트, `backend/test_pipeline_fixes.py`, ruff, `git diff --check`를 실행하고 결과를 읽는다.
- [x] Step 2: 수정 전/후 저장·전송 관련 파일 해시와 ZIP 내용을 대조한다.
- [ ] Step 3: 전체 변경을 별도 리뷰하고 주요 지적은 실패 테스트→수정→전체 재검증한다.
- [ ] Step 4: 검증된 변경 소스와 적용 설명을 ZIP으로 제공한다. 실모델·운영 배포 미검증 항목은 명시한다.

## 실행 기록
- 승인: 사용자가 위 설계대로 수정하라고 명시적으로 응답했다. 구현은 현재 세션에서 직접 수행한다.
- 기준: GitHub `b05449f2c56942c0469962ee5b746786bf4cc023`; 변경 전 전체 OCR 테스트 `233 passed, 14 skipped`.
- Ruling: 사용자 제공 AGENTS.md가 별도 브랜치를 금지하고 main 작업을 지정하므로 별도 worktree를 생성하지 않는다.
- Ruling: 공식 API 목록 명세에 영문 전용 검색 인자가 없으므로 미지원 인자를 만들어 보내지 않는다. 검증된 영문명 대응과 기존 공식 품목 대조로 한정한다.
