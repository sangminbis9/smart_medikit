"""가상 제품으로 읽기 경계·페이지 처리·권한 검사·개인정보 제외를 검증한다."""

import json
import io
import os
import unittest
import zipfile
from decimal import Decimal
from unittest.mock import Mock, call, patch

from backend import read_api
from backend.deploy_read_api import school_role, update_code


# 실제 약품·환자정보와 구분되는 합성 제품을 만든다.
def product():
    return {
        "pk": "MEDICINE#000000001", "sk": "META", "product_code": "000000001",
        "name": "검증용 합성 제품", "ingredients_status": "SUCCEEDED",
        "ingredients": [{"name": "합성 성분"}],
        "product_info": {"source": "MFDS_PERMIT_DETAIL", "indications": "합성 효능"},
        "ocr_text": "노출 금지 합성 원문", "source_image": "노출 금지 참조",
    }


# 기존 제품과 연결되는 명시적인 합성 보유 포장을 만든다.
def package():
    return {
        "pk": "PACKAGE#synthetic-package", "sk": "META", "package_id": "synthetic-package",
        "box_id": "test-box", "kind": "OTC", "state": "STORED",
        "product_codes": ["000000001"],
        "note": "노출 금지 메모", "patient_name": "노출 금지 개인 정보",
    }


class FakeTable:
    # 순서가 있는 가상 응답과 호출 내역을 보관한다.
    def __init__(self, pages):
        self.pages = iter(pages)
        self.calls = []

    # 쓰기 메서드 없이 조회 호출만 기록한다.
    def scan(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.pages)


class ReadApiTests(unittest.TestCase):
    # 원문 문단과 표의 셀 병합·기호를 정확히 보존한다.
    def test_structured_formula_and_table(self):
        item = product()
        del item["product_info"]
        item.update({"product_info_status": "SUCCEEDED", "dosage": '''<DOC title="용법용량"><SECTION>
          <ARTICLE title="합성 조건"><PARAGRAPH tagName="table"><![CDATA[<tbody>
          <tr><td rowspan="2"><p>X =</p></td><td><p>합성 분자</p></td><td rowspan="2"><p>× 조건</p></td></tr>
          <tr><td><p>합성 분모</p></td></tr></tbody>]]></PARAGRAPH>
          <PARAGRAPH tagName="p"><![CDATA[합성 표 제목]]></PARAGRAPH>
          <PARAGRAPH tagName="table"><![CDATA[<tbody><tr><th>구분</th><th colspan="2">합성 정보</th></tr>
          <tr><td>항목</td><td>&lt; 30</td><td>주의</td></tr></tbody>]]></PARAGRAPH>
          </ARTICLE></SECTION></DOC>'''})
        info = read_api.medicine_view(item)["product_info"]
        blocks = info["documents"]["dosage"]
        self.assertEqual([block["type"] for block in blocks], ["text", "table", "text", "table"])
        self.assertEqual(blocks[0]["text"], "합성 조건")
        formula = blocks[1]["rows"]
        self.assertEqual([cell["text"] for cell in formula[0]["cells"]], ["X =", "합성 분자", "× 조건"])
        self.assertEqual(formula[0]["cells"][0]["rowspan"], 2)
        self.assertEqual(formula[1]["cells"][0]["text"], "합성 분모")
        self.assertEqual(blocks[3]["rows"][0]["cells"][1]["colspan"], 2)
        self.assertEqual(blocks[3]["rows"][1]["cells"][1]["text"], "< 30")
        self.assertIn("합성 분모", info["dosage"])

    # 깨진 표·중첩 태그·위험한 코드와 과도한 병합은 평문 원문 확인 대상으로 둔다.
    def test_unsafe_or_unsupported_structures_are_not_guessed(self):
        from backend.product_document import structured_document
        for table in (
            '<tr><td rowspan="3">X</td></tr>',
            '<tr><td colspan="99">X</td></tr>',
            '<tr><td><script>alert(1)</script></td></tr>',
            '<tr><td><img src="https://example.invalid/test"></td></tr>',
            '<tr><td>기호<sup>2</sup></td></tr>',
            '<tr><td>누락</tr>',
        ):
            xml = f'<DOC><PARAGRAPH tagName="table"><![CDATA[<tbody>{table}</tbody>]]></PARAGRAPH></DOC>'
            self.assertIsNone(structured_document(xml, read_api.product_text))
        self.assertIsNone(structured_document('평문 표 데이터', read_api.product_text))
        item = product()
        item["product_info"]["dosage"] = '<DOC><PARAGRAPH tagName="table"><![CDATA[<tbody><tr><td rowspan="3">X</td></tr></tbody>]]></PARAGRAPH></DOC>'
        result = read_api.medicine_view(item)["product_info"]
        self.assertIn("dosage", result["documents_incomplete"])
        self.assertNotIn("documents", result)

    # 최상위 XML의 제목·CDATA·조건을 보존하고 기존 중첩 형식도 유지한다.
    def test_flat_product_information(self):
        item = product()
        del item["product_info"]
        item.update({
            "product_info_status": "SUCCEEDED",
            "indications": '<DOC title="효능효과"><SECTION><ARTICLE title="1. 합성 비염"/><ARTICLE title="2. 조건"><PARAGRAPH><![CDATA[감기로 인한 두통의 완화]]></PARAGRAPH></ARTICLE></SECTION></DOC>',
            "dosage": '<DOC><SECTION><PARAGRAPH><![CDATA[<p>합성 사용법</p><p>합성 조건</p>]]></PARAGRAPH></SECTION></DOC>',
            "precautions": "합성 주의사항",
        })
        info = read_api.medicine_view(item)["product_info"]
        self.assertEqual(info["source"], "DYNAMODB_PRODUCT_INFO")
        self.assertEqual(info["indications"], "효능효과\n1. 합성 비염\n2. 조건\n감기로 인한 두통의 완화")
        self.assertIn("합성 사용법", info["dosage"])
        self.assertIn("합성 조건", info["dosage"])
        table = FakeTable([{"Items": [item]}])
        result = read_api.read_inventory(table, "test-box")
        self.assertEqual(result["catalog_medicines"][0]["indications"], [info["indications"]])
        for field in ("indications", "dosage", "precautions"):
            self.assertIn(field, table.calls[0]["ExpressionAttributeNames"].values())
        item["product_info_status"] = "FAILED"
        self.assertIsNone(read_api.medicine_view(item)["product_info"])
        self.assertEqual(read_api.medicine_view(product())["product_info"]["source"], "MFDS_PERMIT_DETAIL")

    # 깨진 XML·엔터티·빈 내용으로 효능을 만들어내지 않는다.
    def test_invalid_product_information(self):
        for value in (None, {}, '<DOC><ARTICLE', '<DOC/>', '<!DOCTYPE DOC [<!ENTITY a "두통">]><DOC>&a;</DOC>', 'x' * 400_001):
            self.assertIsNone(read_api.product_text(value))
        self.assertEqual(read_api.product_text('합성 두통 효능'), '합성 두통 효능')

    # 코드만 배포할 때 역할·환경변수·API를 변경하지 않고 동시 수정도 보호한다.
    def test_code_only_update_boundary(self):
        sdk = Mock()
        session = sdk.Session.return_value
        sts, lambdas = Mock(), Mock()
        session.client.side_effect = {"sts": sts, "lambda": lambdas}.__getitem__
        sts.get_caller_identity.return_value = {"Account": "123456789012"}
        lambdas.get_function.return_value = {
            "Tags": {"ManagedBy": "smart-medikit-read-api"},
            "Configuration": {"Handler": "read_api.lambda_handler", "RevisionId": "test-revision"},
        }
        lambdas.invoke.return_value = {"Payload": io.BytesIO(json.dumps({
            "statusCode": 200, "body": json.dumps({"packages": [], "inventory_status": "CONFIRMED"}),
        }).encode())}
        with patch.dict("sys.modules", {"boto3": sdk}):
            update_code("123456789012")
        self.assertEqual(session.client.call_args_list, [call("sts"), call("lambda")])
        self.assertEqual(lambdas.update_function_code.call_args.kwargs["RevisionId"], "test-revision")
        with zipfile.ZipFile(io.BytesIO(lambdas.update_function_code.call_args.kwargs["ZipFile"])) as archive:
            self.assertEqual(set(archive.namelist()), {"read_api.py", "product_document.py", "medkit_mfds.py"})
            compile(archive.read("medkit_mfds.py"), "medkit_mfds.py", "exec")
        lambdas.update_function_configuration.assert_not_called()
        lambdas.update_function_code.reset_mock()
        lambdas.get_function.return_value["Tags"] = {}
        with patch.dict("sys.modules", {"boto3": sdk}), self.assertRaises(RuntimeError):
            update_code("123456789012")
        lambdas.update_function_code.assert_not_called()

    # 학교 역할 선택도 고정 계정과 Lambda 신뢰를 검증하고 IAM 변경을 호출하지 않는다.
    def test_school_role_boundary(self):
        from unittest.mock import Mock
        iam = Mock()
        role = {"RoleName": "SafeRole-inha-capstone-02", "Arn": "arn:aws:iam::123456789012:role/SafeRole-inha-capstone-02",
                "AssumeRolePolicyDocument": {"Statement": [{"Effect": "Allow", "Principal": {"Service": ["lambda.amazonaws.com"]}}]}}
        iam.get_paginator.return_value.paginate.return_value = [{"Roles": [role]}]
        self.assertEqual(school_role(iam, "123456789012"), role)
        self.assertEqual(len(iam.mock_calls), 2)
        with self.assertRaises(RuntimeError):
            school_role(iam, "999999999999")
        role["AssumeRolePolicyDocument"]["Statement"] = []
        with self.assertRaises(RuntimeError):
            school_role(iam, "123456789012")

    # 실행 PC의 시연 허용 설정이 테스트 권한 경계에 영향을 주지 않게 한다.
    def setUp(self):
        self.env = patch.dict(os.environ, {"MEDKIT_DEMO_PUBLIC_READ": "false"})
        self.env.start()
        self.addCleanup(self.env.stop)

    # 공개 시연은 명시적으로 승인한 설정에서만 허용한다.
    def test_public_demo_opt_in(self):
        self.assertFalse(read_api.authorized({}, "test-box"))
        with patch.dict(os.environ, {"MEDKIT_DEMO_PUBLIC_READ": "true"}):
            self.assertTrue(read_api.authorized({}, "test-box"))

    # 허용 필드만 반환하고 인식을 보유나 태깅 승인으로 승격하지 않는지 확인한다.
    def test_product_allowlist(self):
        result = read_api.medicine_view(product())
        self.assertEqual(result["ingredients"], ["합성 성분"])
        self.assertIsNone(result["care_assessment"])
        for key in ("ocr_text", "source_image", "state", "expiry", "inventory"):
            self.assertNotIn(key, result)
        self.assertIsNone(result["product_info"]["dosage"])

    # 제품 코드와 저장 키가 다르면 잘못 연결된 제품을 제외한다.
    def test_key_mismatch(self):
        item = product()
        item["pk"] = "RECOGNITION#synthetic"
        self.assertIsNone(read_api.medicine_view(item))

    # 빈 필터 페이지라도 다음 키가 있으면 계속 조회한다.
    def test_filtered_empty_page(self):
        cursor = {"pk": "RECOGNITION#synthetic", "sk": "META"}
        table = FakeTable([{"Items": [], "LastEvaluatedKey": cursor}, {"Items": [product()]}])
        result = read_api.read_inventory(table, "test-box")
        self.assertEqual(len(result["catalog_medicines"]), 1)
        self.assertTrue(result["catalog_complete"])
        self.assertEqual(table.calls[1]["ExclusiveStartKey"], cursor)
        self.assertEqual(table.calls[0]["Limit"], 100)
        self.assertTrue(table.calls[0]["ConsistentRead"])

    # 읽기 한도를 넘으면 부분 결과임을 유지한다.
    def test_read_limit(self):
        page = {"Items": [], "LastEvaluatedKey": {"pk": "next", "sk": "META"}}
        table = FakeTable([page, page, page])
        self.assertEqual(read_api.read_inventory(table, "test-box")["inventory_status"], "PARTIAL")
        self.assertEqual(len(table.calls), 2)

    # 승인된 요청도 제품 캐시를 포장·재고로 변환하지 않는지 확인한다.
    def test_catalog_does_not_create_packages(self):
        event = self.event()
        with patch.dict(os.environ, {"MEDKIT_BOX_ID": "test-box"}), patch.object(read_api, "get_table", return_value=FakeTable([{"Items": [product()]}])):
            response = read_api.lambda_handler(event, None)
        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertEqual(body["packages"], [])
        self.assertEqual(body["inventory_status"], "CONFIRMED")
        self.assertEqual(len(body["catalog_medicines"]), 1)
        self.assertEqual(response["headers"]["Cache-Control"], "no-store")

    # 포장과 제품의 스캔 순서·페이지가 달라도 품목 코드로 연결한다.
    def test_package_join_across_pages(self):
        cursor = {"pk": "cursor", "sk": "META"}
        table = FakeTable([{"Items": [package()], "LastEvaluatedKey": cursor}, {"Items": [product()]}])
        result = read_api.read_inventory(table, "test-box")
        self.assertEqual(result["inventory_status"], "CONFIRMED")
        item = result["packages"][0]
        self.assertEqual(item["name"], "검증용 합성 제품")
        self.assertEqual(item["medicines"][0]["product_code"], "000000001")
        self.assertEqual(item["state"], "STORED")
        self.assertIsNone(item["expiry"]["actual_expiry"])
        self.assertFalse(item["expiry"]["verified"])
        self.assertIsNone(item["inventory"])
        self.assertIsNone(item["position"])
        self.assertEqual(item["safety_status"], "SAFETY_CHECK_PENDING")
        self.assertNotIn("노출 금지", json.dumps(result, ensure_ascii=False))

    # 공개 플래그의 누락·거짓·참 여부와 약품 종류가 재고 조회를 제한하지 않는다.
    def test_all_packages_without_public_flag_filter(self):
        for changes in ({}, {"demo_public": False}, {"demo_public": True}, {"demo_public": "true"}):
            for kind in ("OTC", "PRESCRIPTION"):
                item = {**package(), **changes, "kind": kind}
                table = FakeTable([{"Items": [product(), item]}])
                with self.subTest(changes=changes, kind=kind), patch.dict(os.environ, {
                    "MEDKIT_BOX_ID": "test-box", "MEDKIT_DEMO_PUBLIC_READ": "true",
                }), patch.object(read_api, "get_table", return_value=table):
                    event = self.event()
                    event["requestContext"].pop("authorizer")
                    response = read_api.lambda_handler(event, None)
                    self.assertEqual(response["statusCode"], 200)
                    result = json.loads(response["body"])
                    self.assertEqual(result["packages"][0]["kind"], kind)
                    self.assertEqual(result["inventory_status"], "CONFIRMED")
                    self.assertNotIn("demo_public", table.calls[0]["ExpressionAttributeNames"].values())
                    self.assertNotIn("노출 금지", response["body"])

    # 공개 플래그를 제거해도 다른 보관함과 보관함 없는 레코드를 섞지 않는다.
    def test_box_boundary(self):
        other = {**package(), "box_id": "other-box"}
        result = read_api.read_inventory(FakeTable([{"Items": [other]}]), "test-box")
        self.assertEqual(result["packages"], [])
        self.assertEqual(result["inventory_status"], "CONFIRMED")
        unknown = {**package(), "box_id": None}
        result = read_api.read_inventory(FakeTable([{"Items": [unknown]}]), "test-box")
        self.assertEqual(result["packages"], [])
        self.assertEqual(result["inventory_status"], "PARTIAL")

    # 부적합 포장은 전체 조회를 깨지 않고 부분 상태로 남긴다.
    def test_invalid_package_is_partial(self):
        for changes in ({"state": "UNKNOWN"}, {"package_id": "other"}, {"kind": "UNKNOWN"}):
            result = read_api.read_inventory(FakeTable([{"Items": [{**package(), **changes}]}]), "test-box")
            self.assertEqual(result["inventory_status"], "PARTIAL")
            self.assertEqual(result["packages"], [])

    # 일부 제품 연결이 빠져도 나머지 약품만으로 포장 전체를 검토 완료 처리하지 않는다.
    def test_missing_product_stays_unresolved(self):
        item = {**package(), "product_codes": ["000000001", "000000002"]}
        result = read_api.read_inventory(FakeTable([{"Items": [product(), item]}]), "test-box")
        self.assertEqual(len(result["packages"][0]["medicines"]), 2)
        self.assertNotIn("care_assessment", result["packages"][0]["medicines"][1])

    # DynamoDB 숫자·실제 날짜만 변환하고 임시 기준일을 실제 유효기간으로 승격하지 않는다.
    def test_inventory_values_and_forms(self):
        medicine = {**product(), "form": "TABLET"}
        item = {**package(),
                "inventory": {"stock_enabled": True, "initial_qty": Decimal("10"), "estimated_qty": Decimal("7")},
                "expiry": {"verified": True, "actual_expiry": "2027-02-30", "temporary_management_date": "2027-03-01"},
                "position": {"x": Decimal("12.5"), "y": Decimal("3"), "z_estimated": Decimal("0"), "certainty": "ESTIMATED"}}
        result = read_api.read_inventory(FakeTable([{"Items": [medicine, item]}]), "test-box")
        view = result["packages"][0]
        self.assertEqual(view["inventory"]["estimated_qty"], 7)
        self.assertEqual(view["position"]["x"], 12.5)
        self.assertIsNone(view["expiry"]["actual_expiry"])
        self.assertFalse(view["expiry"]["verified"])
        self.assertEqual(view["expiry"]["temporary_management_date"], "2027-03-01")
        json.dumps(result)
        for form in ("OTHER", "UNKNOWN"):
            medicine["form"] = form
            view = read_api.read_inventory(FakeTable([{"Items": [medicine, item]}]), "test-box")["packages"][0]
            self.assertIsNone(view["inventory"])
        for value in (True, -1, Decimal("1.5"), Decimal("NaN"), "7"):
            self.assertIsNone(read_api.clean_number(value, integer=True))

    # 보관 상태 변경은 그대로 반영하고 실제 데이터가 사라졌을 때 가짜 항목을 남기지 않는다.
    def test_state_change_and_empty_inventory(self):
        for state in ("REGISTERED", "STORED", "OUT", "DEPLETED", "DISPOSED"):
            result = read_api.read_inventory(FakeTable([{"Items": [{**package(), "state": state}]}]), "test-box")
            self.assertEqual(result["packages"][0]["state"], state)
        result = read_api.read_inventory(FakeTable([{"Items": []}]), "test-box")
        self.assertEqual(result["packages"], [])
        self.assertEqual(result["inventory_status"], "CONFIRMED")

    # 검증된 서버 권한 문맥을 포함한 합성 요청을 만든다.
    def event(self):
        return {"rawPath": "/inventory", "queryStringParameters": {"box_id": "test-box"},
                "requestContext": {"http": {"method": "GET"}, "authorizer": {
                    "lambda": {"medkit_read": True, "box_id": "test-box"}}}}

    # 클라이언트가 보낸 보관함 ID와 헤더만으로는 조회하지 못한다.
    def test_authorization(self):
        event = self.event()
        event["requestContext"].pop("authorizer")
        event["headers"] = {"medkit_read": "true"}
        with patch.dict(os.environ, {"MEDKIT_BOX_ID": "test-box"}), patch.object(read_api, "get_table") as table:
            self.assertEqual(read_api.lambda_handler(event, None)["statusCode"], 401)
            table.assert_not_called()

    # 쓰기·다른 보관함·미설정·장애를 서로 구분해 차단한다.
    def test_failures(self):
        with patch.dict(os.environ, {"MEDKIT_BOX_ID": "test-box"}):
            event = self.event()
            event["requestContext"]["http"]["method"] = "POST"
            self.assertEqual(read_api.lambda_handler(event, None)["statusCode"], 405)
            event = self.event()
            event["queryStringParameters"]["box_id"] = "other"
            self.assertEqual(read_api.lambda_handler(event, None)["statusCode"], 403)
            with patch.object(read_api, "get_table", side_effect=RuntimeError("숨길 내용")):
                result = read_api.lambda_handler(self.event(), None)
                self.assertEqual(result["statusCode"], 503)
                self.assertNotIn("숨길 내용", result["body"])
        with patch.dict(os.environ, {"MEDKIT_BOX_ID": ""}):
            self.assertEqual(read_api.lambda_handler(self.event(), None)["statusCode"], 503)


if __name__ == "__main__":
    unittest.main()
