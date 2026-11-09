"""합성 복용약과 가상 식약처 응답으로 품목 선택·캐시·동시 수정 경계를 검사한다."""

import copy
import json
import os
import unittest
from importlib import import_module
from unittest.mock import patch

from backend import medication_api as api
from backend.test_medication_api import ConditionalFailure, medicine, plan, record, state


class FakeTable:
    # 서로 다른 품목·복용약 항목을 분리하고 조건부 쓰기 경쟁을 재현한다.
    def __init__(self, snapshot):
        self.items = {("BOX#synthetic-box", "MEDICATION_STATE"): {"pk": "BOX#synthetic-box", "sk": "MEDICATION_STATE", "revision": 1, "state": copy.deepcopy(snapshot)}}
        self.writes = []
        self.before_state_put = None
        self.deny_budget = False

    # 키에 해당하는 항목만 반환해 제품과 사용자 상태가 섞이지 않게 한다.
    def get_item(self, **request):
        key = request["Key"]
        item = self.items.get((key["pk"], key["sk"]))
        return {} if item is None else {"Item": copy.deepcopy(item)}

    # 같은 항목의 저장 버전과 연결 메타데이터를 함께 비교한다.
    def put_item(self, **request):
        item = copy.deepcopy(request["Item"])
        key = (item["pk"], item["sk"])
        if key[1] == "MEDICATION_STATE" and self.before_state_put:
            callback, self.before_state_put = self.before_state_put, None
            callback(self.items[key])
        previous = self.items.get(key)
        condition = request.get("ConditionExpression")
        if condition == "attribute_not_exists(pk)" and previous is not None:
            raise ConditionalFailure()
        if condition and "#revision = :expected" in condition:
            expected = request["ExpressionAttributeValues"][":expected"]
            if previous is None or previous.get("revision") != expected:
                raise ConditionalFailure()
            if "attribute_not_exists(#links)" in condition and "product_links" in previous:
                raise ConditionalFailure()
            if "#links = :previous_links" in condition and previous.get("product_links") != request["ExpressionAttributeValues"][":previous_links"]:
                raise ConditionalFailure()
        self.items[key] = item
        self.writes.append(copy.deepcopy(request))
        return {}

    # 식약처 요청 예산을 날짜별로 원자 갱신하는 동작을 모의한다.
    def update_item(self, **request):
        if self.deny_budget:
            raise RuntimeError("synthetic permission denial")
        key = request["Key"]
        pair = (key["pk"], key["sk"])
        current = self.items.get(pair)
        values = request["ExpressionAttributeValues"]
        today = values[":today"]
        condition = request["ConditionExpression"]
        if condition == "#day = :today AND #count < :limit":
            if not current or current["day"] != today or current["count"] >= values[":limit"]:
                raise ConditionalFailure()
            current["count"] += 1
        else:
            if current and current["day"] >= today:
                raise ConditionalFailure()
            self.items[pair] = {"pk": pair[0], "sk": pair[1], "day": today, "count": 1}
        return {}


class FakeMfds:
    # 공식 API 응답 모양의 합성 품목만 반환한다.
    def __init__(self):
        self.names = []
        self.detail_calls = 0

    # 후보를 반환하되 선택은 테스트가 명시적으로 수행한다.
    def search(self, name):
        self.names.append(name)
        return [{"ITEM_SEQ": "000000001", "ITEM_NAME": "합성 제품", "ENTP_NAME": "합성 업체"}]

    # 요청한 품목코드가 같은 상세만 제공한다.
    def details(self, code):
        self.detail_calls += 1
        return {"ITEM_SEQ": code, "ITEM_NAME": "합성 제품", "ENTP_NAME": "합성 업체"}

    # 실제 의약품과 무관한 설명을 반환한다.
    def product_info(self, code):
        return {"source": "MFDS_PERMIT_DETAIL", "indications": "합성 효능", "dosage": "합성 용법", "precautions": "합성 주의"}

    # 합성 성분만 반환한다.
    def ingredients(self, code):
        return [{"name": "합성 성분", "amount": "1", "unit": "mg", "basis": None}]


class MedicationProductTests(unittest.TestCase):
    # 모든 상태와 식약처 호출을 합성 객체로 격리한다.
    def setUp(self):
        self.med = medicine("synthetic-medicine")
        self.plan = plan(medicines=[self.med])
        self.snapshot = state(self.plan, [record(self.plan, taken=[self.med["id"]])])
        self.table = FakeTable(self.snapshot)
        self.mfds = FakeMfds()
        self.client = patch.object(api, "mfds_client", return_value=self.mfds)
        self.client.start()
        self.addCleanup(self.client.stop)
        self.request = {"planId": self.plan["id"], "medicineId": self.med["id"], "expectedRevision": 1, "productCode": "000000001"}

    # 후보 조회만으로는 선택이나 저장을 만들지 않는다.
    def test_search_returns_candidates_without_choosing(self):
        result = api.search_mfds_products(self.table, "synthetic-box", {"name": "합성 약"})
        self.assertEqual(result["candidates"][0]["productCode"], "000000001")
        self.assertEqual(self.table.writes, [])
        with self.assertRaises(api.ApiError):
            api.search_mfds_products(self.table, "synthetic-box", {"name": "약"})

    # 사용자가 고른 품목만 연결하며 기존 기록·스냅샷 응답을 바꾸지 않는다.
    def test_explicit_link_preserves_records_and_original_contract(self):
        result = api.link_mfds_product(self.table, "synthetic-box", self.request)
        self.assertEqual(result["productCode"], "000000001")
        self.assertEqual(api.read_snapshot(self.table, "synthetic-box"), {"revision": 1, "state": self.snapshot})
        links = api.list_product_links(self.table, "synthetic-box")["links"]
        self.assertEqual(links, [{"planId": self.plan["id"], "medicineId": self.med["id"], "productCode": "000000001", "productName": "합성 제품"}])
        self.assertEqual(api.read_cached_product(self.table, "000000001")["ingredients"][0]["name"], "합성 성분")

    # 공식 캐시가 최신이면 같은 품목을 다시 연결할 때 API를 호출하지 않는다.
    def test_recent_cache_is_reused(self):
        api.link_mfds_product(self.table, "synthetic-box", self.request)
        self.assertEqual(self.mfds.detail_calls, 1)
        api.link_mfds_product(self.table, "synthetic-box", self.request)
        self.assertEqual(self.mfds.detail_calls, 1)

    # 오래된 캐시는 선택 시 다시 조회하고 확인 날짜를 갱신한다.
    def test_old_cache_is_refreshed_when_linking(self):
        api.link_mfds_product(self.table, "synthetic-box", self.request)
        cache = self.table.items[("PRODUCT#000000001", "MFDS")]["product"]
        cache["checkedAt"] = "2000-01-01T00:00:00Z"
        api.link_mfds_product(self.table, "synthetic-box", self.request)
        self.assertEqual(self.mfds.detail_calls, 2)
        self.assertNotEqual(self.table.items[("PRODUCT#000000001", "MFDS")]["product"]["checkedAt"], "2000-01-01T00:00:00Z")

    # 약명·함량 변경이나 약 삭제는 상태와 함께 이전 연결을 원자 삭제한다.
    def test_changed_or_deleted_medicine_removes_link(self):
        api.link_mfds_product(self.table, "synthetic-box", self.request)
        changed = copy.deepcopy(self.snapshot)
        changed["plans"][0]["medicines"][0]["strength"] = "변경"
        changed["records"] = []
        api.put_snapshot(self.table, "synthetic-box", {"expected_revision": 1, "state": changed})
        self.assertNotIn("product_links", self.table.items[("BOX#synthetic-box", "MEDICATION_STATE")])
        self.assertEqual(api.list_product_links(self.table, "synthetic-box"), {"links": []})
        self.assertEqual(api.read_snapshot(self.table, "synthetic-box")["revision"], 2)

    # 삭제된 묶음의 연결도 같은 스냅샷 저장에서 함께 제거된다.
    def test_deleted_plan_removes_link(self):
        api.link_mfds_product(self.table, "synthetic-box", self.request)
        api.put_snapshot(self.table, "synthetic-box", {"expected_revision": 1, "state": state()})
        self.assertNotIn("product_links", self.table.items[("BOX#synthetic-box", "MEDICATION_STATE")])
        self.assertEqual(api.list_product_links(self.table, "synthetic-box"), {"links": []})

    # 손상된 연결을 조용히 버리고 복용약을 덮어쓰지 않는다.
    def test_corrupt_links_do_not_get_overwritten(self):
        item = self.table.items[("BOX#synthetic-box", "MEDICATION_STATE")]
        item["product_links"] = {"corrupted": {"productCode": "000000001"}}
        with self.assertRaises(api.ApiError) as error:
            api.put_snapshot(self.table, "synthetic-box", {"expected_revision": 1, "state": self.snapshot})
        self.assertEqual(error.exception.status, 503)
        self.assertEqual(self.table.writes, [])

    # 다른 기기의 연결 수정이 더 빠르면 일반 복용약 저장도 기존 연결을 덮어쓰지 않는다.
    def test_concurrent_link_blocks_stale_snapshot_write(self):
        api.link_mfds_product(self.table, "synthetic-box", self.request)
        key = api.product_link_key(self.plan["id"], self.med["id"])
        # 같은 저장 버전에서 다른 기기의 제품 선택이 먼저 완료됐다고 가정한다.
        def changed(item):
            item["product_links"][key]["productName"] = "다른 합성 제품"
        self.table.before_state_put = changed
        with self.assertRaises(api.ApiError) as error:
            api.put_snapshot(self.table, "synthetic-box", {"expected_revision": 1, "state": self.snapshot})
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.table.items[("BOX#synthetic-box", "MEDICATION_STATE")]["product_links"][key]["productName"], "다른 합성 제품")

    # 외부 조회 중 목록이 바뀌면 선택했던 제품과 이전 기록을 새 상태에 덮어쓰지 않는다.
    def test_concurrent_snapshot_blocks_stale_link(self):
        def changed(item):
            item["revision"] = 2
        self.table.before_state_put = changed
        with self.assertRaises(api.ApiError) as error:
            api.link_mfds_product(self.table, "synthetic-box", self.request)
        self.assertEqual(error.exception.status, 409)
        stored = self.table.items[("BOX#synthetic-box", "MEDICATION_STATE")]
        self.assertEqual(stored["revision"], 2)
        self.assertEqual(stored["state"], self.snapshot)
        self.assertNotIn("product_links", stored)

    # 동일 revision에서 두 기기가 제품을 고르면 늦은 선택이 앞선 연결을 덮어쓰지 않는다.
    def test_concurrent_link_blocks_stale_link(self):
        api.link_mfds_product(self.table, "synthetic-box", self.request)
        key = api.product_link_key(self.plan["id"], self.med["id"])
        # 서버 읽기 직후 다른 기기의 확인된 제품 선택을 반영한다.
        def changed(item):
            item["product_links"][key]["productName"] = "다른 합성 제품"
        self.table.before_state_put = changed
        with self.assertRaises(api.ApiError) as error:
            api.link_mfds_product(self.table, "synthetic-box", self.request)
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.table.items[("BOX#synthetic-box", "MEDICATION_STATE")]["product_links"][key]["productName"], "다른 합성 제품")

    # 새 제품의 공식 조회가 실패해도 기존 연결과 복용 기록은 남긴다.
    def test_failed_reselection_preserves_existing_link(self):
        api.link_mfds_product(self.table, "synthetic-box", self.request)
        previous = copy.deepcopy(self.table.items[("BOX#synthetic-box", "MEDICATION_STATE")])
        other = {**self.request, "productCode": "000000002"}
        with patch.object(self.mfds, "details", side_effect=RuntimeError("synthetic upstream error")):
            with self.assertRaises(api.ApiError) as error:
                api.link_mfds_product(self.table, "synthetic-box", other)
        self.assertEqual(error.exception.status, 503)
        self.assertEqual(self.table.items[("BOX#synthetic-box", "MEDICATION_STATE")], previous)

    # 비어 있거나 지나치게 큰 원문을 성공한 제품 연결로 만들지 않는다.
    def test_mismatched_code_and_large_document_fail_without_link(self):
        with patch.object(self.mfds, "details", return_value={"ITEM_SEQ": "000000002", "ITEM_NAME": "다른 제품"}):
            with self.assertRaises(api.ApiError):
                api.link_mfds_product(self.table, "synthetic-box", self.request)
        with patch.object(self.mfds, "product_info", return_value={"source": "MFDS_PERMIT_DETAIL", "indications": "x" * (api.MAX_BYTES + 1)}):
            with self.assertRaises(api.ApiError) as error:
                api.link_mfds_product(self.table, "synthetic-box", self.request)
            self.assertEqual(error.exception.status, 413)
        self.assertNotIn("product_links", self.table.items[("BOX#synthetic-box", "MEDICATION_STATE")])

    # 조회 예산을 기록할 수 없거나 상한을 넘으면 외부 요청을 허용하지 않는다.
    def test_daily_budget_fails_closed(self):
        self.table.deny_budget = True
        with self.assertRaises(api.ApiError) as error:
            api.consume_mfds_budget(self.table, "synthetic-box")
        self.assertEqual(error.exception.status, 503)
        self.table.deny_budget = False
        with patch.object(api, "MFDS_DAILY_LIMIT", 2):
            api.consume_mfds_budget(self.table, "synthetic-box")
            api.consume_mfds_budget(self.table, "synthetic-box")
            with self.assertRaises(api.ApiError) as error:
                api.consume_mfds_budget(self.table, "synthetic-box")
            self.assertEqual(error.exception.status, 429)

    # 예산이 없으면 HTTP 전송 함수 자체를 호출하지 않는다.
    def test_budget_is_reserved_before_each_external_request(self):
        self.client.stop()
        module = import_module("ocr-lambda.backend.mfds")
        with patch.dict(os.environ, {"MFDS_API_KEY": "synthetic-key"}), patch.object(module.MfdsClient, "_download", return_value=b"synthetic") as download, patch.object(api, "MFDS_DAILY_LIMIT", 1):
            client = api.mfds_client(self.table, "synthetic-box")
            self.assertEqual(client.transport("https://apis.data.go.kr/synthetic"), b"synthetic")
            with self.assertRaises(api.ApiError) as error:
                client.transport("https://apis.data.go.kr/synthetic")
            self.assertEqual(error.exception.status, 429)
            download.assert_called_once()

    # API 키 누락은 복용 기록 저장 기능을 막지 않고 제품 조회에만 오류를 낸다.
    def test_missing_mfds_key_does_not_break_medications(self):
        self.client.stop()
        with patch.dict(os.environ, {"MFDS_API_KEY": ""}):
            with self.assertRaises(api.ApiError) as error:
                api.search_mfds_products(self.table, "synthetic-box", {"name": "합성 약"})
            self.assertEqual(error.exception.status, 503)
        self.assertEqual(api.read_snapshot(self.table, "synthetic-box")["state"], self.snapshot)

    # 공개 모드에서도 본문 검색과 제품 연결을 분리하고 응답에 민감한 예외를 포함하지 않는다.
    def test_public_http_search_and_errors(self):
        environment = {"MEDKIT_MEDICATION_TABLE": api.TABLE_NAME, "MEDKIT_BOX_ID": "synthetic-box",
                       "MEDKIT_ALLOWED_ORIGIN": "https://synthetic.example.com", "MEDKIT_MEDICATION_PUBLIC_DEMO": "true"}
        event = {"rawPath": "/medicine-products/search", "requestContext": {"http": {"method": "POST"}},
                 "headers": {"content-type": "application/json", "origin": "https://synthetic.example.com"},
                 "body": json.dumps({"name": "합성 약"}, ensure_ascii=False)}
        with patch.dict(os.environ, environment, clear=True), patch.object(api, "get_table", return_value=self.table):
            response = api.lambda_handler(event, None)
            self.assertEqual(response["statusCode"], 200)
            self.assertEqual(json.loads(response["body"])["candidates"][0]["productCode"], "000000001")
            with patch.object(self.mfds, "search", side_effect=RuntimeError("private-name-and-key")):
                response = api.lambda_handler(event, None)
                self.assertEqual(response["statusCode"], 503)
                self.assertNotIn("private-name-and-key", response["body"])
            event["headers"]["origin"] = "https://not-allowed.example.com"
            self.assertEqual(api.lambda_handler(event, None)["statusCode"], 403)

    # 공백과 구분 기호만 다른 유일 품목을 연결하되 기록과 버전은 보존한다.
    def test_auto_link_normalized_unique_name_preserves_state(self):
        self.med["name"] = "합성 제품"
        self.table = FakeTable(self.snapshot)
        request = {"planId": self.plan["id"], "medicineId": self.med["id"], "expectedRevision": 1}
        result = api.auto_link_mfds_product(self.table, "synthetic-box", request)
        self.assertEqual(result["productCode"], "000000001")
        self.assertEqual(api.read_snapshot(self.table, "synthetic-box"), {"revision": 1, "state": self.snapshot})
        self.assertEqual(self.mfds.names, ["합성"])

    # 식약처 품목명이나 성분에서 명시된 동일 수치와 단위만 함량을 확인한다.
    def test_auto_match_strength_checks_name_or_ingredient(self):
        med = {"name": "합성 캡슐", "strength": "300mg"}
        self.assertTrue(api.auto_product_matches(med, "합성캡슐300밀리그램(합성성분)"))
        self.assertFalse(api.auto_product_matches(med, "합성캡슐150밀리그램(합성성분)"))
        self.assertFalse(api.auto_product_matches(med, "합성캡슐(합성성분)"))
        self.assertTrue(api.auto_product_matches(med, "합성캡슐(합성성분)", ingredients=[{"amount": "300", "unit": "밀리그램", "basis": "1캡슐 중"}]))
        self.assertFalse(api.auto_product_matches(med, "합성캡슐(합성성분)", ingredients=[{"amount": "300", "unit": "mg", "basis": None}]))
        self.assertFalse(api.auto_product_matches(med, "합성캡슐(합성성분)", ingredients=[{"amount": "300", "unit": "mg", "basis": "100ml 중"}]))
        self.assertFalse(api.auto_product_matches(med, "합성캡슐(합성성분)", ingredients=[{"amount": "300", "unit": "ml", "basis": "1캡슐 중"}]))
        self.assertFalse(api.auto_product_matches({"name": "합성 캡슐", "strength": "1정"}, "합성캡슐"))

    # 괄호 속 함량과 제형을 무시해 서로 다른 제품을 같은 제품으로 만들지 않는다.
    def test_auto_match_keeps_form_and_parenthetical_amount(self):
        med = {"name": "합성정", "strength": ""}
        self.assertFalse(api.auto_product_matches(med, "합성정(300mg)"))
        self.assertFalse(api.auto_product_matches(med, "합성정(서방형)"))
        self.assertFalse(api.auto_product_matches(med, "합성서방정"))
        self.assertTrue(api.auto_product_matches(med, "합성정(합성성분)"))
        self.assertFalse(api.auto_product_matches({"name": "합성정(가상성분)", "strength": ""}, "합성정(다른성분)"))
        self.assertTrue(api.auto_product_matches({"name": "합성정(가상성분)", "strength": ""}, "합성정(가상성분)"))
        self.assertFalse(api.auto_product_matches({"name": "합성정(가상성분)", "strength": ""}, "합성정"))
        self.assertTrue(api.auto_product_matches({"name": "합성정(가상성분)", "strength": ""}, "합성정", ingredients=[{"name": "가상성분"}]))

    # 가장 비슷해 보여도 다른 이름과 여러 동일 후보는 연결하지 않는다.
    def test_auto_match_ambiguous_or_different_names_are_not_linked(self):
        self.med["name"] = "합성 제품"
        self.table = FakeTable(self.snapshot)
        request = {"planId": self.plan["id"], "medicineId": self.med["id"], "expectedRevision": 1}
        with patch.object(self.mfds, "search", return_value=[{"ITEM_SEQ": "000000001", "ITEM_NAME": "합성서방제품", "ENTP_NAME": "합성"}]):
            with self.assertRaises(api.ApiError) as error:
                api.auto_link_mfds_product(self.table, "synthetic-box", request)
            self.assertEqual(error.exception.code, "AUTO_MATCH_UNCERTAIN")
        with patch.object(self.mfds, "search", return_value=[{"ITEM_SEQ": "000000001", "ITEM_NAME": "합성 제품", "ENTP_NAME": "A"}, {"ITEM_SEQ": "000000002", "ITEM_NAME": "합성제품", "ENTP_NAME": "B"}]):
            with self.assertRaises(api.ApiError) as error:
                api.auto_link_mfds_product(self.table, "synthetic-box", request)
            self.assertEqual(error.exception.code, "AUTO_MATCH_UNCERTAIN")
        self.assertEqual(self.table.writes, [])

    # 상세의 다른 함량 또는 이름은 검색 결과가 일치해도 저장하지 않는다.
    def test_auto_match_rechecks_detail_before_saving(self):
        self.med["name"] = "합성 제품"
        self.med["strength"] = "300mg"
        self.table = FakeTable(self.snapshot)
        request = {"planId": self.plan["id"], "medicineId": self.med["id"], "expectedRevision": 1}
        with self.assertRaises(api.ApiError) as error:
            api.auto_link_mfds_product(self.table, "synthetic-box", request)
        self.assertEqual(error.exception.code, "AUTO_MATCH_UNCERTAIN")
        self.assertNotIn("product_links", self.table.items[("BOX#synthetic-box", "MEDICATION_STATE")])

    # 품목명에 없는 함량을 공식 성분에서 확인하면 자동 연결한다.
    def test_auto_link_confirms_strength_in_ingredient(self):
        self.med["name"] = "합성 제품"
        self.med["strength"] = "300mg"
        self.table = FakeTable(self.snapshot)
        request = {"planId": self.plan["id"], "medicineId": self.med["id"], "expectedRevision": 1}
        with patch.object(self.mfds, "ingredients", return_value=[{"name": "합성 성분", "amount": "300", "unit": "mg", "basis": "1캡슐 중"}]):
            result = api.auto_link_mfds_product(self.table, "synthetic-box", request)
        self.assertEqual(result["productCode"], "000000001")
        self.assertEqual(api.read_snapshot(self.table, "synthetic-box")["state"], self.snapshot)

    # 검색 이후 다른 기기의 기록 변경은 자동 연결을 조건부 충돌로 거부한다.
    def test_auto_link_cannot_overwrite_concurrent_snapshot(self):
        self.med["name"] = "합성 제품"
        self.table = FakeTable(self.snapshot)
        request = {"planId": self.plan["id"], "medicineId": self.med["id"], "expectedRevision": 1}
        def changed(item):
            item["revision"] = 2
        self.table.before_state_put = changed
        with self.assertRaises(api.ApiError) as error:
            api.auto_link_mfds_product(self.table, "synthetic-box", request)
        self.assertEqual(error.exception.status, 409)
        self.assertNotIn("product_links", self.table.items[("BOX#synthetic-box", "MEDICATION_STATE")])

    # HTTP 자동 연결은 공개 모드에서도 본문 검증과 동일한 저장 경계를 사용한다.
    def test_auto_link_http_route(self):
        self.med["name"] = "합성 제품"
        self.table = FakeTable(self.snapshot)
        environment = {"MEDKIT_MEDICATION_TABLE": api.TABLE_NAME, "MEDKIT_BOX_ID": "synthetic-box",
                       "MEDKIT_ALLOWED_ORIGIN": "https://synthetic.example.com", "MEDKIT_MEDICATION_PUBLIC_DEMO": "true"}
        body = {"planId": self.plan["id"], "medicineId": self.med["id"], "expectedRevision": 1}
        event = {"rawPath": "/medicine-products/auto-link", "requestContext": {"http": {"method": "POST"}},
                 "headers": {"content-type": "application/json", "origin": "https://synthetic.example.com"}, "body": json.dumps(body)}
        with patch.dict(os.environ, environment, clear=True), patch.object(api, "get_table", return_value=self.table):
            response = api.lambda_handler(event, None)
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(json.loads(response["body"])["productCode"], "000000001")

    # 다른 기기가 먼저 연결한 제품은 자동 조회가 덮어쓰지 않는다.
    def test_auto_link_returns_existing_without_external_calls(self):
        api.link_mfds_product(self.table, "synthetic-box", self.request)
        calls = self.mfds.detail_calls
        result = api.auto_link_mfds_product(self.table, "synthetic-box", {key: self.request[key] for key in ("planId", "medicineId", "expectedRevision")})
        self.assertEqual(result["productCode"], "000000001")
        self.assertEqual(self.mfds.detail_calls, calls)
        self.assertEqual(self.mfds.names, [])

    # 성분 원문 수치가 충돌해도 유사품 문구만 별도 캐시에 두며 확정 연결을 만들지 않는다.
    def test_suggestion_retains_official_text_when_ingredients_conflict(self):
        self.med["name"] = "가상복용시럽"
        self.med["strength"] = ""
        self.table = FakeTable(self.snapshot)
        request = {"planId": self.plan["id"], "medicineId": self.med["id"], "expectedRevision": 1}
        candidate = {"ITEM_SEQ": "000000001", "ITEM_NAME": "가상복용시럽플러스", "ENTP_NAME": "합성 업체"}
        conflict = import_module("ocr-lambda.backend.mfds").ApiError("API_INGREDIENT_CONFLICT")
        with patch.object(self.mfds, "search", return_value=[candidate]), patch.object(self.mfds, "details", return_value=candidate), patch.object(self.mfds, "ingredients", side_effect=conflict):
            result = api.suggest_mfds_product(self.table, "synthetic-box", request)
            self.assertEqual(result["matchStatus"], "POSSIBLE")
            self.assertEqual(result["ingredientsStatus"], "CONFLICT")
            self.assertEqual(result["ingredients"], [])
            self.assertEqual(result["indications"], "합성 효능")
            self.assertEqual(result["precautions"], "합성 주의")
            api.suggest_mfds_product(self.table, "synthetic-box", request)
        self.assertEqual(api.list_product_links(self.table, "synthetic-box"), {"links": []})
        self.assertEqual(api.read_snapshot(self.table, "synthetic-box"), {"revision": 1, "state": self.snapshot})
        self.assertIn(("PRODUCT#000000001", "MFDS_CANDIDATE"), self.table.items)
        self.assertNotIn(("PRODUCT#000000001", "MFDS"), self.table.items)

    # 후보 제품 정보의 다른 오류는 성분 충돌로 숨기지 않는다.
    def test_suggestion_does_not_hide_non_conflict_ingredient_error(self):
        self.med["name"] = "가상복용시럽"
        self.table = FakeTable(self.snapshot)
        request = {"planId": self.plan["id"], "medicineId": self.med["id"], "expectedRevision": 1}
        candidate = {"ITEM_SEQ": "000000001", "ITEM_NAME": "가상복용시럽플러스", "ENTP_NAME": "합성 업체"}
        failure = import_module("ocr-lambda.backend.mfds").ApiError("API_INVALID_RESPONSE")
        with patch.object(self.mfds, "search", return_value=[candidate]), patch.object(self.mfds, "details", return_value=candidate), patch.object(self.mfds, "ingredients", side_effect=failure):
            with self.assertRaises(api.ApiError) as error:
                api.suggest_mfds_product(self.table, "synthetic-box", request)
        self.assertEqual(error.exception.status, 503)
        self.assertEqual(self.table.writes, [])

    # 이름이 비슷해도 다른 제형·성분이나 서로 다른 명시 함량은 후보에서 제외한다.
    def test_suggestion_rejects_form_ingredient_and_strength_conflicts(self):
        med = {"name": "가상복용시럽(성분가)", "strength": "15ml"}
        self.assertIsNone(api.suggestion_score(med, "가상복용시럽(성분나)"))
        self.assertIsNone(api.suggestion_score(med, "가상복용시럽30ml(성분가)"))
        self.assertIsNone(api.suggestion_score(med, "가상복용정(성분가)"))
        self.assertIsNone(api.suggestion_score(med, "무관한제품시럽(성분가)"))

    # 가까운 점수나 동점의 후보가 여러 개면 어떤 제품도 선택하지 않는다.
    def test_suggestion_rejects_ambiguous_candidates(self):
        self.med["name"] = "가상복용시럽"
        self.table = FakeTable(self.snapshot)
        request = {"planId": self.plan["id"], "medicineId": self.med["id"], "expectedRevision": 1}
        rows = [{"ITEM_SEQ": "000000001", "ITEM_NAME": "가상복용시럽플러스", "ENTP_NAME": "A"},
                {"ITEM_SEQ": "000000002", "ITEM_NAME": "가상복용시럽플러스", "ENTP_NAME": "B"}]
        with patch.object(self.mfds, "search", return_value=rows):
            with self.assertRaises(api.ApiError) as error:
                api.suggest_mfds_product(self.table, "synthetic-box", request)
        self.assertEqual(error.exception.status, 422)
        self.assertEqual(self.table.writes, [])

    # 식약처 조회 중 입력 약이 변경되면 후보를 반환하거나 캐시하지 않는다.
    def test_suggestion_rechecks_snapshot_after_external_lookup(self):
        self.med["name"] = "가상복용시럽"
        self.table = FakeTable(self.snapshot)
        request = {"planId": self.plan["id"], "medicineId": self.med["id"], "expectedRevision": 1}
        candidate = {"ITEM_SEQ": "000000001", "ITEM_NAME": "가상복용시럽플러스", "ENTP_NAME": "합성 업체"}
        def change_details(code):
            self.table.items[("BOX#synthetic-box", "MEDICATION_STATE")]["revision"] = 2
            return candidate
        with patch.object(self.mfds, "search", return_value=[candidate]), patch.object(self.mfds, "details", side_effect=change_details):
            with self.assertRaises(api.ApiError) as error:
                api.suggest_mfds_product(self.table, "synthetic-box", request)
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.table.writes, [])

    # 정상 성분이 없을 때는 충돌이 아닌 별도 누락 상태로 전달한다.
    def test_suggestion_no_data_is_distinct_from_conflict(self):
        self.med["name"] = "가상복용시럽"
        self.table = FakeTable(self.snapshot)
        request = {"planId": self.plan["id"], "medicineId": self.med["id"], "expectedRevision": 1}
        candidate = {"ITEM_SEQ": "000000001", "ITEM_NAME": "가상복용시럽플러스", "ENTP_NAME": "합성 업체"}
        with patch.object(self.mfds, "search", return_value=[candidate]), patch.object(self.mfds, "details", return_value=candidate), patch.object(self.mfds, "ingredients", return_value=[]):
            result = api.suggest_mfds_product(self.table, "synthetic-box", request)
        self.assertEqual(result["ingredientsStatus"], "NO_DATA")

    # 전체 이름 검색이 무결과일 때 세 글자 검색으로 찾은 후보도 미확정 상태만 반환한다.
    def test_suggestion_fallback_when_form_is_added_to_official_name(self):
        self.med["name"] = "가상복용"
        self.med["strength"] = "15ml"
        self.table = FakeTable(self.snapshot)
        request = {"planId": self.plan["id"], "medicineId": self.med["id"], "expectedRevision": 1}
        candidate = {"ITEM_SEQ": "000000001", "ITEM_NAME": "가상복용시럽", "ENTP_NAME": "합성 업체"}
        with patch.object(self.mfds, "search", side_effect=[[], [candidate]]) as search, patch.object(self.mfds, "details", return_value=candidate):
            result = api.suggest_mfds_product(self.table, "synthetic-box", request)
        self.assertEqual(search.call_count, 2)
        self.assertEqual(result["matchStatus"], "POSSIBLE")
        self.assertEqual(result["ingredientsStatus"], "SUCCEEDED")
        self.assertEqual(api.list_product_links(self.table, "synthetic-box"), {"links": []})

    # 새 HTTP 경로는 공개 설정에서도 스냅샷 버전을 검사하고 후보 상태를 돌려준다.
    def test_suggestion_http_route(self):
        self.med["name"] = "가상복용"
        self.table = FakeTable(self.snapshot)
        environment = {"MEDKIT_MEDICATION_TABLE": api.TABLE_NAME, "MEDKIT_BOX_ID": "synthetic-box",
                       "MEDKIT_ALLOWED_ORIGIN": "https://synthetic.example.com", "MEDKIT_MEDICATION_PUBLIC_DEMO": "true"}
        body = {"planId": self.plan["id"], "medicineId": self.med["id"], "expectedRevision": 1}
        event = {"rawPath": "/medicine-products/suggestion", "requestContext": {"http": {"method": "POST"}},
                 "headers": {"content-type": "application/json", "origin": "https://synthetic.example.com"}, "body": json.dumps(body)}
        candidate = {"ITEM_SEQ": "000000001", "ITEM_NAME": "가상복용시럽", "ENTP_NAME": "합성 업체"}
        with patch.dict(os.environ, environment, clear=True), patch.object(api, "get_table", return_value=self.table), patch.object(self.mfds, "search", return_value=[candidate]), patch.object(self.mfds, "details", return_value=candidate):
            response = api.lambda_handler(event, None)
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(json.loads(response["body"])["matchStatus"], "POSSIBLE")


if __name__ == "__main__":
    unittest.main()
