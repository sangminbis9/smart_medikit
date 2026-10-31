"""진단에 원문·제품·식별정보가 섞이지 않고 실패 원인이 숫자와 코드로 남는지 확인한다."""

import json

import pytest
from test_pipeline import dependencies, event
from test_recognition import api_for, line, product

from backend.diagnostics import Diagnostics, event_reference
from backend.image_ocr import group_lines
from backend.lambda_function import process_records
from backend.recognition import RecognitionError, identify


# 수집된 앱 진단 JSON만 읽는다.
def diagnostic_logs(caplog):
    return [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "backend.diagnostics"
    ]


# 각 매칭 거부 분기를 같은 대조 로직에서 관측하는지 검증한다.
@pytest.mark.parametrize(
    "observed,official,reason",
    [
        ("가상정", product("가상정(비공개성분)"), "ANNOTATION_UNVERIFIED"),
        ("가상정", product("다른정"), "NAME_MISMATCH"),
        ("가상정10mg", product("가상정20mg"), "STRENGTH_MISMATCH"),
        ("가상연질캡슐", product("가상경질캡슐"), "FORM_MISMATCH"),
    ],
)
def test_matching_rejection_details(observed, official, reason, caplog):
    with pytest.raises(RecognitionError):
        identify(
            group_lines([line(observed)]),
            api_for(official),
            diagnostics=Diagnostics("private-person-id"),
        )
    summary = diagnostic_logs(caplog)[-1]
    assert summary["kind"] == "product_matching"
    assert summary["api_row_count"] == 1
    assert summary["accepted_product_count"] == 0
    assert summary["rejections"] == {reason: 1}
    assert observed not in caplog.text and official["ITEM_NAME"] not in caplog.text
    assert "private-person-id" not in caplog.text


# 업체명 원문 없이 업체 불일치 개수만 기록한다.
def test_manufacturer_rejection(caplog):
    cells = group_lines([line("가상정"), line("제조사: 비공개업체", (10, 44, 150, 65))])
    with pytest.raises(RecognitionError):
        identify(cells, api_for(product("가상정")), diagnostics=Diagnostics("test"))
    assert diagnostic_logs(caplog)[-1]["rejections"] == {"MANUFACTURER_MISMATCH": 1}
    assert "비공개업체" not in caplog.text


# 검색 무결과와 매칭 조건 탈락을 별도로 구분한다.
def test_zero_results(caplog):
    with pytest.raises(RecognitionError):
        identify(group_lines([line("가상정")]), api_for(), diagnostics=Diagnostics("test"))
    assert diagnostic_logs(caplog)[-1]["api_row_count"] == 0
    assert diagnostic_logs(caplog)[-1]["rejections"] == {}


# 다중 품목 모호성은 실제 이름·품목코드 없이 통과한 품목 수로 확인한다.
def test_ambiguous_results(caplog):
    with pytest.raises(RecognitionError):
        identify(
            group_lines([line("가상정")]),
            api_for(product("가상정"), product("가상정", "900000002")),
            diagnostics=Diagnostics("test"),
        )
    assert diagnostic_logs(caplog)[-1]["accepted_product_count"] == 2
    assert "900000002" not in caplog.text


# 낮은 신뢰도 실패 이전에 모든 후보 좌표·점수와 판정값을 기록한다.
def test_low_confidence_has_all_candidates(caplog):
    cells = group_lines([line("가상정", score=0.899999), line("다른정", (400, 10, 550, 40))])
    with pytest.raises(RecognitionError, match="LOW_OCR_CONFIDENCE"):
        identify(cells, api_for(), diagnostics=Diagnostics("test"))
    summary = diagnostic_logs(caplog)[0]
    assert summary["candidate_count"] == 2
    assert summary["candidates"][0]["parts"][0]["passes_threshold"] is False
    assert summary["candidates"][1]["parts"][0]["passes_threshold"] is True
    assert summary["candidates"][0]["box"] == [10, 10, 150, 40]
    assert "가상정" not in caplog.text


# 원문·추가 필드·잘못된 좌표가 로그에 복사되지 않고 비정상 실수도 JSON으로 안전하게 표현된다.
def test_region_allowlist_and_limits(caplog):
    private = line("가상환자 전화번호 비밀키", box=("PRIVATE", 1, 20, 30), score=float("nan"))
    private["secret"] = "NEVER_LOG"
    Diagnostics("PatientAlice123").regions([private] * 200, (100, 300, 3))
    summary = diagnostic_logs(caplog)[0]
    assert len(summary["regions"]) == 120 and summary["omitted_regions"] == 80
    assert summary["regions"][0] == {"box": [None, 1, 20, 30], "score": None}
    assert summary["event_ref"] == event_reference("PatientAlice123")
    for raw in ("가상환자", "PRIVATE", "NEVER_LOG", "PatientAlice123", "NaN"):
        assert raw not in caplog.text


# 실제 Lambda 처리 경로에서도 실패 원문·환자 식별자를 출력하지 않는다.
def test_process_logs_are_private_and_do_not_change_failure_storage(caplog):
    deps = dependencies({"event-id": "PatientAlice123"})
    deps["ocr"].return_value = group_lines([line("AliceSecret정", score=0.5)])
    with pytest.raises(RuntimeError):
        process_records(event(), **deps)
    kinds = [entry["kind"] for entry in diagnostic_logs(caplog)]
    assert kinds == ["pipeline_started", "ocr_candidates"]
    assert "PatientAlice123" not in caplog.text and "AliceSecret" not in caplog.text
    deps["store"].finish.assert_not_called()
    assert deps["store"].fail.call_args.args[3] == "LOW_OCR_CONFIDENCE"


# 진단을 꺼도 인식·저장 결과는 같고 진단 로그만 사라진다.
def test_disabled_diagnostics(caplog):
    deps = dependencies()
    assert process_records(event(), **deps, diagnostics_enabled=False)["processed"] == 1
    assert not diagnostic_logs(caplog)


# 호출 간 로그 식별자와 출력 한도는 분리된다.
def test_attempt_isolation_and_log_budget(caplog):
    first, second = Diagnostics("same-event"), Diagnostics("same-event")
    for _ in range(40):
        first.started(0.9)
    second.started(0.9)
    entries = diagnostic_logs(caplog)
    assert len(entries) == 33
    assert entries[0]["event_ref"] == entries[-1]["event_ref"]
    assert entries[0]["attempt_id"] != entries[-1]["attempt_id"]


# 괄호 검증용 상세 조회 성공도 원문 없이 횟수와 판정만 진단한다.
def test_annotation_detail_diagnostic_is_private(caplog):
    api = api_for(product("가상정(비공개성분)"))
    api.ingredients.return_value = [{"name": "비공개성분"}]
    result = identify(group_lines([line("가상정")]), api, diagnostics=Diagnostics("test"))
    assert result["processing_status"] == "COMPLETED"
    summary = diagnostic_logs(caplog)[-1]
    assert summary["annotation_detail_checks"] == 1
    assert summary["rejections"] == {"MATCH": 1}
    assert "비공개성분" not in caplog.text
