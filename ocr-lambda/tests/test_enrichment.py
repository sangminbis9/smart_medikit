"""공식 필드 명세와 합성 API 응답으로 함량 보완·허가정보·저장 경계를 검증한다."""

import json
import os
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import pytest
from test_pipeline import decode, storage_case
from test_recognition import api_for, line, product

from backend.diagnostics import Diagnostics
from backend.image_ocr import group_lines
from backend.mfds import ApiError, MfdsClient, document_text, ingredient_gaps
from backend.recognition import identify

# 실제 CSV에서 누락된 여섯 함량을 공식 공개 페이지와 대조한 재현용 값이다.
HWAITUVEN_AMOUNTS = [
    ("아세트아미노펜", "200.000"),
    ("구아이페네신", "27.800"),
    ("슈도에페드린염산염", "15.000"),
    ("DL-메틸에페드린염산염", "12.500"),
    ("덱스트로메토르판브롬화수소산염수화물", "8.000"),
    ("트리프롤리딘염산염수화물", "0.660"),
]


# 외부 API 캡처가 아닌 합성 상세 응답을 만든다.
def detail_row(code="900000001"):
    material = "총량 : 1캡슐(1,130 밀리그램) 중 | " + " | ".join(
        f"성분명 : {name} | 분량 : {amount} | 단위 : 밀리그램 | 규격 : 합성"
        for name, amount in HWAITUVEN_AMOUNTS
    )
    return {
        "ITEM_SEQ": code,
        "ITEM_NAME": "화이투벤큐플러스연질캡슐",
        "MATERIAL_NAME": material,
        "TOTAL_CONTENT": "11,130",
        "EE_DOC_DATA": '<DOC><SECTION title="효능"><ARTICLE><PARAGRAPH><![CDATA[합성 효능]]></PARAGRAPH></ARTICLE></SECTION></DOC>',
        "UD_DOC_DATA": '<DOC><SECTION title="용법"><PARAGRAPH><![CDATA[합성 용법]]></PARAGRAPH></SECTION></DOC>',
        "NB_DOC_DATA": '<DOC><SECTION title="주의"><PARAGRAPH><![CDATA[합성 주의]]></PARAGRAPH></SECTION></DOC>',
        "STORAGE_METHOD": "합성 저장방법",
        "VALID_TERM": "제조일로부터 36개월",
        "CHANGE_DATE": "20261101",
    }


# 요청 경로·품목 필터와 상세 응답 재사용을 검증할 가상 전송 계층을 만든다.
def client_for(detail=None, rows=None):
    detail = detail if detail is not None else detail_row()
    rows = (
        rows
        if rows is not None
        else [
            {"ITEM_SEQ": "900000001", "MTRAL_NM": name, "QNT": None, "INGD_UNIT_CD": "밀리그램"}
            for name, _ in HWAITUVEN_AMOUNTS
        ]
    )

    # API와 같은 JSON 봉투만 사용하며 인증키·응답 원문은 출력하지 않는다.
    def transport(url):
        parts = urlparse(url)
        query = parse_qs(parts.query)
        if "Mcpn" in parts.path:
            assert query["Item_seq"] == ["900000001"]
            items = rows
        elif "PrmsnInq" in parts.path:
            assert query["item_name"] == ["화이투벤큐플러스"]
            items = [detail]
        else:
            assert query["item_seq"] == ["900000001"]
            items = [detail]
        return json.dumps(
            {"header": {"resultCode": "00"}, "body": {"totalCount": len(items), "items": items}}
        ).encode()

    mock = Mock(side_effect=transport)
    return MfdsClient("synthetic-key", transport=mock), mock


# QNT가 모두 비어도 동일 품목·성분명의 명시적 상세 함량만 보완한다.
def test_hwaituven_missing_qnt_recovered_from_material():
    api, transport = client_for()
    rows = api.ingredients("900000001")
    assert [(row["name"], row["amount"]) for row in rows] == HWAITUVEN_AMOUNTS
    assert not ingredient_gaps(rows)
    assert all(row["basis"] == "1캡슐(1,130 밀리그램) 중" for row in rows)
    assert all(row["source"] == "MFDS_MCPN_AND_MATERIAL" for row in rows)
    info = api.product_info("900000001")
    assert info["indications"] == "효능\n합성 효능"
    assert info["shelf_life"] == "제조일로부터 36개월"
    assert transport.call_count == 2


# 응답에 근거가 없으면 사진·브랜드 숫자·전체 캡슐 무게로 함량을 만들어내지 않는다.
def test_missing_material_remains_partial_not_success():
    detail = detail_row()
    detail["MATERIAL_NAME"] = "알 수 없는 형식"
    api, _ = client_for(detail)
    ingredients = api.ingredients("900000001")
    stub = api_for(product("화이투벤큐플러스연질캡슐"))
    stub.ingredients.return_value = ingredients
    result = identify(group_lines([line("화이투벤큐플러스연질캡슐")]), stub)
    medicine = result["medicines"][0]
    assert medicine["ingredients_status"] == "PARTIAL"
    assert medicine["enrichment_status"] == "INCOMPLETE"
    assert len(medicine["ingredients_missing_fields"]) == 12
    assert all(row["amount"] is row["basis"] is None for row in ingredients)


# 다른 품목의 원료나 허가정보가 섞이면 상세 응답을 거부한다.
@pytest.mark.parametrize("method", ["ingredients", "product_info"])
def test_other_product_details_rejected(method):
    api, _ = client_for(detail_row("900000002"))
    with pytest.raises(ApiError, match="API_PRODUCT_CODE_MISMATCH"):
        getattr(api, method)("900000001")


# QNT와 상세 함량이 충돌하면 편한 값을 임의로 고르지 않는다.
def test_conflicting_ingredient_amount_rejected():
    api, _ = client_for(
        rows=[
            {
                "ITEM_SEQ": "900000001",
                "MTRAL_NM": "아세트아미노펜",
                "QNT": "500",
                "INGD_UNIT_CD": "밀리그램",
            }
        ]
    )
    with pytest.raises(ApiError, match="API_INGREDIENT_CONFLICT"):
        api.ingredients("900000001")


# 같은 단위의 기호·한글 표기는 충돌로 오판하지 않는다.
def test_equivalent_units_keep_numeric_amount():
    api, _ = client_for(
        rows=[
            {
                "ITEM_SEQ": "900000001",
                "MTRAL_NM": "아세트아미노펜",
                "QNT": "200",
                "INGD_UNIT_CD": "mg",
            }
        ]
    )
    assert api.ingredients("900000001")[0]["amount"] == "200"


# 여러 총량에 중복된 성분이나 염 형태가 다른 원료는 대체하지 않는다.
@pytest.mark.parametrize("mutation", ["duplicate", "different_salt"])
def test_ambiguous_or_different_material_not_used(mutation):
    detail = detail_row()
    if mutation == "duplicate":
        detail["MATERIAL_NAME"] += " ; " + detail["MATERIAL_NAME"]
    else:
        detail["MATERIAL_NAME"] = detail["MATERIAL_NAME"].replace("아세트아미노펜", "다른원료")
    api, _ = client_for(detail)
    assert api.ingredients("900000001")[0]["amount"] is None


# 주성분 응답에 없는 첨가제는 원료 상세에 있더라도 주성분으로 추가하지 않는다.
def test_material_does_not_add_excipients():
    detail = detail_row()
    detail["MATERIAL_NAME"] += " | 성분명 : 합성첨가제 | 분량 : 50 | 단위 : 밀리그램"
    api, _ = client_for(detail)
    assert len(api.ingredients("900000001")) == 6


# 여러 구성 기준의 동명 성분에 단일 총량을 잘못 붙여 완전 성공으로 만들지 않는다.
def test_duplicate_ingredient_keeps_basis_unresolved():
    detail = detail_row()
    detail["TOTAL_CONTENT"] = "1캡슐 중"
    rows = [
        {"ITEM_SEQ": "900000001", "MTRAL_NM": "아세트아미노펜", "QNT": "200", "INGD_UNIT_CD": "mg"}
    ] * 2
    api, _ = client_for(detail, rows)
    ingredients = api.ingredients("900000001")
    assert all(row["basis"] is None for row in ingredients)
    assert ingredient_gaps(ingredients) == ["ingredients[0].basis", "ingredients[1].basis"]


# 잘못된 수치·단위는 완전한 성분 정보로 인정하지 않는다.
@pytest.mark.parametrize(
    "amount,unit", [("NaN", "mg"), ("-1", "mg"), ("0", "mg"), ("10", "1001"), (None, "mg")]
)
def test_invalid_ingredient_fields_are_incomplete(amount, unit):
    assert ingredient_gaps([{"amount": amount, "unit": unit, "basis": "1정"}])


# XML 제목·CDATA 표시 태그·표 값의 순서를 유지하고 실행 태그는 남기지 않는다.
def test_document_preserves_titles_and_cdata_html():
    text = '<DOC><SECTION title="첫 제목"><P><![CDATA[<b>첫째</b><br/>둘째]]></P><P>셋째</P></SECTION><SECTION title="다음 제목"><P>넷째</P></SECTION></DOC>'
    assert document_text(text) == "첫 제목\n첫째\n둘째\n셋째\n다음 제목\n넷째"


# 잘린 XML·외부 엔터티·지나치게 큰 문서는 완전한 공식 정보로 저장하지 않는다.
@pytest.mark.parametrize(
    "value",
    [
        "<DOC><P>잘림",
        '<!DOCTYPE DOC [<!ENTITY x "text">]><DOC>&x;</DOC>',
        "&lt;!DOCTYPE DOC&gt;&lt;DOC/&gt;",
        "<DOC><![CDATA[<script>alert(1)</script>]]></DOC>",
        "가" * 60_001,
    ],
    ids=["truncated", "entity", "escaped_entity", "script", "oversize"],
)
def test_invalid_document_is_safe(value):
    with pytest.raises(ApiError, match="^API_DOCUMENT_INVALID$"):
        document_text(value)


# 한 문서 누락이 다른 공식 정보를 버리거나 조회 성공으로 숨겨지지 않는다.
def test_missing_or_invalid_info_field_is_partial():
    detail = detail_row()
    detail["UD_DOC_DATA"] = "<DOC>잘림"
    api, _ = client_for(detail)
    stub = api_for(product())
    stub.product_info.return_value = api.product_info("900000001")
    medicine = identify(group_lines([line("지르텍정")]), stub)["medicines"][0]
    assert medicine["product_info_status"] == "PARTIAL"
    assert medicine["product_info_missing_fields"] == ["dosage"]
    assert medicine["product_info"]["storage_method"] == "합성 저장방법"
    assert medicine["enrichment_status"] == "INCOMPLETE"


# 허가정보와 성분 조회가 모두 완료되어야 정보 보완 완료를 표시한다.
def test_success_info_is_in_atomic_medicine_and_event_writes():
    client, store, result, source = storage_case()
    assert result["medicines"][0]["enrichment_status"] == "SUCCEEDED"
    store.finish("event", source, result, "now")
    writes = client.transactions[0]["TransactItems"]
    values = decode(writes[0]["Update"]["ExpressionAttributeValues"])
    assert values[":info"]["shelf_life"] == "제조일로부터 36개월"
    marker = decode(writes[-1]["Put"]["Item"])
    assert marker["medicines"][0]["product_info"] == values[":info"]
    assert "expiry_date" not in values[":info"]


# 불완전한 조회는 과거 정상 허가정보 캐시를 덮어쓰지 않는 식을 생성한다.
def test_partial_preserves_existing_complete_cache():
    client, store, result, source = storage_case()
    med = result["medicines"][0]
    med.update(
        product_info_status="PARTIAL",
        product_info={"indications": "새 일부정보"},
        enrichment_status="INCOMPLETE",
    )
    store.finish("event", source, result, "now")
    expression = client.transactions[0]["TransactItems"][0]["Update"]["UpdateExpression"]
    assert "#info=if_not_exists(#info,:info)" in expression
    assert "product_info_last_success_at" not in expression


# 호출 실패는 OCR 성공과 분리하고 민감한 예외 원문을 결과에 포함하지 않는다.
def test_info_api_outage_does_not_become_ocr_failure():
    stub = api_for(product())
    stub.product_info.side_effect = RuntimeError("환자 원문 비밀키")
    result = identify(group_lines([line("지르텍정")]), stub)
    assert result["processing_status"] == "COMPLETED"
    assert result["medicines"][0]["product_info_status"] == "UNAVAILABLE"
    assert "환자" not in str(result)


# 상태만 성공으로 바꾼 불완전한 성분 결과는 쓰기 전에 거부한다.
def test_storage_rejects_false_ingredient_success():
    client, store, result, source = storage_case()
    result["medicines"][0]["ingredients"][0]["amount"] = None
    with pytest.raises(ValueError, match="INCOMPLETE_INGREDIENT_SUCCESS"):
        store.finish("event", source, result, "now")
    assert not client.transactions


# 긴 허가 문서는 의미를 잘라 저장하지 않고 DynamoDB 호출 전에 크기를 검사한다.
def test_oversize_event_rejected_before_write():
    client, store, result, source = storage_case()
    result["medicines"][0]["product_info"]["precautions"] = "가" * 150_000
    with pytest.raises(ValueError, match="PRODUCT_INFO_STORAGE_LIMIT"):
        store.finish("event", source, result, "now")
    assert not client.transactions


# 실제 클라이언트의 세 API 경로를 거쳐 여섯 함량·허가정보를 한 저장 요청에 연결한다.
def test_full_api_enrichment_to_transaction(caplog):
    api, transport = client_for()
    result = identify(
        group_lines([line("화이투벤큐플러스연질캡슐")]),
        api,
        diagnostics=Diagnostics("synthetic-event"),
    )
    medicine = result["medicines"][0]
    assert medicine["enrichment_status"] == "SUCCEEDED"
    assert medicine["product_info_missing_fields"] == medicine["ingredients_missing_fields"] == []
    assert transport.call_count == 3
    client, store, _, source = storage_case()
    store.finish("enriched", source, result, "now")
    update = client.transactions[0]["TransactItems"][0]["Update"]
    values = decode(update["ExpressionAttributeValues"])
    assert [(row["name"], row["amount"]) for row in values[":ingredients"]] == HWAITUVEN_AMOUNTS
    assert values[":info"]["dosage"] == "용법\n합성 용법"
    entry = next(
        json.loads(record.message)
        for record in caplog.records
        if '"kind": "api_enrichment"' in record.message
    )
    assert entry["primary_amount_missing"] == entry["amounts_recovered"] == 6
    assert entry["material_row_count"] == 6
    assert "아세트아미노펜" not in caplog.text and "synthetic-key" not in caplog.text


# 서버 환경에 키와 실행 의사를 명시했을 때만 네 실제 품목의 API 완전성을 검사한다.
@pytest.mark.parametrize("code", ["201602703", "200610765", "201203128", "202300071"])
def test_live_mfds_information_opt_in(code):
    if os.environ.get("MFDS_LIVE_TEST") != "1":
        pytest.skip("실제 API는 MFDS_LIVE_TEST=1과 서버 전용 MFDS_API_KEY가 있을 때만 호출")
    key = os.environ.get("MFDS_API_KEY")
    if not key:
        pytest.fail("MFDS_API_KEY_REQUIRED", pytrace=False)
    api = MfdsClient(key)
    rows = api.ingredients(code)
    # 실패 시 응답 객체나 인증 URL을 pytest 단언에 노출하지 않는다.
    if not rows or ingredient_gaps(rows):
        pytest.fail("LIVE_INGREDIENT_INFORMATION_INCOMPLETE", pytrace=False)
    info = api.product_info(code)
    from backend.mfds import validate_product_info

    if validate_product_info(info):
        pytest.fail("LIVE_PRODUCT_INFORMATION_INCOMPLETE", pytrace=False)
