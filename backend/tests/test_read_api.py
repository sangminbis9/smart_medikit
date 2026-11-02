"""가상 제품으로 읽기 경계·페이지 처리·권한 검사·개인정보 제외를 검증한다."""

import json
import os
import unittest
from unittest.mock import patch

from backend import read_api
from backend.deploy_read_api import school_role


# 실제 약품·환자정보와 구분되는 합성 제품을 만든다.
def product():
    return {
        "pk": "MEDICINE#000000001", "sk": "META", "product_code": "000000001",
        "name": "검증용 합성 제품", "ingredients_status": "SUCCEEDED",
        "ingredients": [{"name": "합성 성분"}],
        "product_info": {"source": "MFDS_PERMIT_DETAIL", "indications": "합성 효능"},
        "ocr_text": "노출 금지 합성 원문", "source_image": "노출 금지 참조",
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
        medicines, complete = read_api.read_catalog(table)
        self.assertEqual(len(medicines), 1)
        self.assertTrue(complete)
        self.assertEqual(table.calls[1]["ExclusiveStartKey"], cursor)
        self.assertEqual(table.calls[0]["Limit"], 100)

    # 읽기 한도를 넘으면 부분 결과임을 유지한다.
    def test_read_limit(self):
        page = {"Items": [], "LastEvaluatedKey": {"pk": "next", "sk": "META"}}
        table = FakeTable([page, page, page])
        self.assertEqual(read_api.read_catalog(table), ([], False))
        self.assertEqual(len(table.calls), 2)

    # 승인된 요청도 제품 캐시를 포장·재고로 변환하지 않는지 확인한다.
    def test_inventory_unknown(self):
        event = self.event()
        with patch.dict(os.environ, {"MEDKIT_BOX_ID": "test-box"}), patch.object(read_api, "get_table", return_value=FakeTable([{"Items": [product()]}])):
            response = read_api.lambda_handler(event, None)
        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertEqual(body["packages"], [])
        self.assertEqual(body["inventory_status"], "UNAVAILABLE")
        self.assertEqual(len(body["catalog_medicines"]), 1)
        self.assertEqual(response["headers"]["Cache-Control"], "no-store")

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
