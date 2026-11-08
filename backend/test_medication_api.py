"""실제 사용자 정보와 AWS 호출 없이 공유 복용약 인증·검증·조건부 저장을 확인한다."""

import base64
import copy
import hashlib
import hmac
import json
import os
import unittest
from decimal import Decimal
from unittest.mock import patch

from backend import medication_api as api


# 실제 복용법과 무관한 합성 일정으로 날짜와 회차 연결을 검사한다.
def schedule(**changes):
    value = {"startDate": "2026-11-01", "endDate": "2026-11-30", "weekdays": [0, 1, 2, 3, 4, 5, 6], "times": ["08:00", "20:00"]}
    value.update(changes)
    return value


# 임상 의미가 없는 식별용 문자열만 담은 약 항목을 만든다.
def medicine(identifier, timing=None):
    return {"id": identifier, "name": f"합성 약 {identifier}", "strength": "", "dose": "", "timing": timing}


# 약 이름을 모르는 묶음과 여러 약이 있는 묶음을 모두 준비할 수 있게 한다.
def plan(identifier="synthetic-plan", medicines=None):
    return {"id": identifier, "title": "합성 처방 묶음", "timing": {"mode": "regular", "schedule": schedule()}, "notes": "", "medicines": medicines or [], "endedOn": None}


# 매번 새 상태를 만들어 테스트 사이에 입력이 공유되지 않게 한다.
def state(value=None, records=None):
    return {"version": 1, "plans": [] if value is None else [value], "records": records or []}


# 프론트엔드와 동일한 한국 시간 회차 식별자를 가진 합성 확인을 만든다.
def record(value, result="all", taken=None, day="2026-11-08", slot="08:00"):
    return {"occurrenceId": f"medication:{api.encoded_plan_id(value['id'])}:{day}T{slot}+09:00", "result": result, "takenMedicineIds": taken or [], "recordedAt": "2026-11-08T00:01:00.000Z"}


class ConditionalFailure(Exception):
    # boto3 없이도 조건부 쓰기 실패 코드를 동일하게 전달한다.
    def __init__(self):
        super().__init__("조건부 쓰기 검증용 오류")
        self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class FakeTable:
    # 단일 스냅샷과 호출 내역만 보관하며 네트워크를 사용하지 않는다.
    def __init__(self, item=None):
        self.item = copy.deepcopy(item)
        self.reads = []
        self.writes = []
        self.concurrent_write = False

    # 강한 일관성 요청을 기록하고 저장값의 복사본을 반환한다.
    def get_item(self, **request):
        self.reads.append(copy.deepcopy(request))
        return {} if self.item is None else {"Item": copy.deepcopy(self.item)}

    # 조회와 쓰기 사이에 발생한 다른 기기의 저장도 조건식으로 차단한다.
    def put_item(self, **request):
        if self.concurrent_write:
            self.concurrent_write = False
            self.item = {"revision": (self.item or {}).get("revision", 0) + 1, "state": state()}
        condition = request["ConditionExpression"]
        if condition == "attribute_not_exists(pk)":
            matches = self.item is None
        else:
            expected = request["ExpressionAttributeValues"][":expected"]
            matches = self.item is not None and self.item["revision"] == expected
        if not matches:
            raise ConditionalFailure()
        self.writes.append(copy.deepcopy(request))
        self.item = copy.deepcopy(request["Item"])
        return {}


class MedicationApiTests(unittest.TestCase):
    # 테스트용 비밀번호 해시만 한 번 계산하고 실제 인증정보는 읽지 않는다.
    @classmethod
    def setUpClass(cls):
        cls.password = "synthetic-test-password"
        salt = b"synthetic-salt16"
        digest = hashlib.pbkdf2_hmac("sha256", cls.password.encode(), salt, 200000)
        cls.environment = {
            "MEDKIT_MEDICATION_TABLE": api.TABLE_NAME,
            "MEDKIT_BOX_ID": "synthetic-box",
            "MEDKIT_ALLOWED_ORIGIN": "https://synthetic.example.com",
            "MEDKIT_PASSWORD_HASH": f"pbkdf2_sha256$200000${api.encode_urlsafe(salt)}${api.encode_urlsafe(digest)}",
            "MEDKIT_SESSION_SECRET": api.encode_urlsafe(b"synthetic-session-secret-32bytes-long"),
        }

    # 인증과 DynamoDB를 매번 격리된 합성 환경으로 바꾼다.
    def setUp(self):
        self.env_patch = patch.dict(os.environ, self.environment, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.table = FakeTable()
        self.table_patch = patch.object(api, "get_table", return_value=self.table)
        self.table_mock = self.table_patch.start()
        self.addCleanup(self.table_patch.stop)
        self.token = api.create_session(api.configuration(), {"password": self.password})["token"]

    # HTTP API payload v2 모양을 만들고 비밀값을 출력하지 않는다.
    def event(self, method="GET", path="/medications", body=None, authenticated=True):
        headers = {"origin": self.environment["MEDKIT_ALLOWED_ORIGIN"], "content-type": "application/json"}
        if authenticated:
            headers["authorization"] = f"Bearer {self.token}"
        return {"rawPath": path, "requestContext": {"http": {"method": method}}, "headers": headers, "body": None if body is None else json.dumps(body, ensure_ascii=False)}

    # Lambda 응답을 호출하고 결과 본문만 테스트에 반환한다.
    def call(self, event):
        response = api.lambda_handler(event, None)
        body = json.loads(response["body"]) if response["body"] else None
        return response, body

    # 정상적인 저장 요청은 현재 버전을 명시적으로 포함한다.
    def put(self, value, revision=0):
        return self.call(self.event("PUT", body={"expected_revision": revision, "state": value}))

    # 인증이 없는 읽기·쓰기는 테이블에 접근하기 전에 차단한다.
    def test_authentication_required_before_storage(self):
        for method in ("GET", "PUT"):
            with self.subTest(method=method):
                response, body = self.call(self.event(method, authenticated=False))
                self.assertEqual(response["statusCode"], 401)
                self.assertEqual(body["code"], "SESSION_REQUIRED")
        self.table_mock.assert_not_called()

    # 비밀번호가 맞을 때만 2시간짜리 세션을 발급하며 해시나 비밀번호를 반환하지 않는다.
    def test_password_session_and_wrong_password(self):
        response, body = self.call(self.event("POST", "/session", {"password": self.password}, False))
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(set(body), {"token", "expires_at"})
        payload = json.loads(api.decode_urlsafe(body["token"].split(".")[0]))
        self.assertEqual(payload["exp"] - payload["iat"], 7200)
        self.assertNotIn(self.password, response["body"])
        self.assertNotIn("digest", response["body"])
        response, body = self.call(self.event("POST", "/session", {"password": "wrong"}, False))
        self.assertEqual(response["statusCode"], 401)
        self.assertEqual(body["code"], "INVALID_PASSWORD")
        self.table_mock.assert_not_called()

    # 서명과 만료 시각 또는 보관함 범위가 바뀐 세션은 저장 권한이 없다.
    def test_tampered_expired_and_different_box_tokens(self):
        event = self.event()
        event["headers"]["authorization"] = f"Bearer {self.token[:-2]}xx"
        self.assertEqual(self.call(event)[0]["statusCode"], 401)
        claims = json.loads(api.decode_urlsafe(self.token.split(".")[0]))
        with patch.object(api.time, "time", return_value=claims["exp"]):
            self.assertEqual(self.call(self.event())[0]["statusCode"], 401)
        other = dict(api.configuration(), box_id="another-box")
        event["headers"]["authorization"] = "Bearer " + api.create_session(other, {"password": self.password})["token"]
        self.assertEqual(self.call(event)[0]["statusCode"], 401)
        self.table_mock.assert_not_called()

    # 서명키 교체가 이전 세션을 무효화하며 공개 조회 환경변수는 예외가 아니다.
    def test_rotation_and_public_read_flag_cannot_bypass_auth(self):
        with patch.dict(os.environ, {"MEDKIT_SESSION_SECRET": api.encode_urlsafe(b"different-secret-with-at-least-32-bytes"), "MEDKIT_DEMO_PUBLIC_READ": "true"}):
            self.assertEqual(self.call(self.event())[0]["statusCode"], 401)
            self.assertEqual(self.call(self.event(authenticated=False))[0]["statusCode"], 401)
        self.table_mock.assert_not_called()

    # 설정이 누락되면 공개 상태로 떨어지지 않으며 허용 출처도 정확히 검사한다.
    def test_fail_closed_configuration_and_origin(self):
        with patch.dict(os.environ, {"MEDKIT_PASSWORD_HASH": ""}):
            self.assertEqual(self.call(self.event())[0]["statusCode"], 503)
        event = self.event()
        event["headers"]["origin"] = "https://synthetic.example.com.attacker.invalid"
        response, body = self.call(event)
        self.assertEqual(response["statusCode"], 403)
        self.assertEqual(body["code"], "ORIGIN_NOT_ALLOWED")
        self.assertNotIn("Access-Control-Allow-Origin", response["headers"])
        with patch.dict(os.environ, {"MEDKIT_MEDICATION_TABLE": "inha-capstone-02-medkit-data"}):
            self.assertEqual(self.call(self.event())[0]["statusCode"], 503)
        self.table_mock.assert_not_called()

    # 빈 저장소 조회는 서버 항목을 만들지 않고 캐시 방지와 정확한 CORS를 반환한다.
    def test_empty_snapshot_and_response_headers(self):
        response, body = self.call(self.event())
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body, {"revision": 0, "state": state()})
        self.assertEqual(response["headers"]["Cache-Control"], "no-store")
        self.assertEqual(response["headers"]["Access-Control-Allow-Origin"], self.environment["MEDKIT_ALLOWED_ORIGIN"])
        self.assertEqual(self.table.reads[0], {"Key": {"pk": "BOX#synthetic-box", "sk": "MEDICATION_STATE"}, "ConsistentRead": True})
        self.assertEqual(self.table.writes, [])

    # 최초 쓰기는 항목 부재를, 후속 쓰기는 이전 revision을 조건으로 사용한다.
    def test_create_update_and_stale_revision(self):
        value = state(plan())
        response, body = self.put(value)
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["revision"], 1)
        self.assertEqual(self.table.writes[0]["ConditionExpression"], "attribute_not_exists(pk)")
        value["plans"][0]["notes"] = "검증용 변경"
        response, body = self.put(value, 1)
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["revision"], 2)
        self.assertEqual(self.table.writes[1]["ExpressionAttributeValues"], {":expected": 1})
        response, body = self.put(state(), 1)
        self.assertEqual(response["statusCode"], 409)
        self.assertEqual(body["code"], "REVISION_CONFLICT")
        self.assertEqual(len(self.table.writes), 2)

    # 읽은 직후 다른 기기가 저장해도 DynamoDB 조건식이 마지막 덮어쓰기를 막는다.
    def test_racing_conditional_write(self):
        self.table.concurrent_write = True
        response, body = self.put(state(plan()))
        self.assertEqual(response["statusCode"], 409)
        self.assertEqual(body["code"], "REVISION_CONFLICT")
        self.assertEqual(self.table.writes, [])

    # 손상된 서버 항목을 빈 상태로 취급해 지우지 않는다.
    def test_corrupted_server_state_blocks_read_and_write(self):
        self.table.item = {"revision": 1, "state": {"version": 1, "plans": None, "records": []}}
        self.assertEqual(self.call(self.event())[1]["code"], "STORED_DATA_INVALID")
        self.assertEqual(self.put(state(), 1)[1]["code"], "STORED_DATA_INVALID")
        self.assertEqual(self.table.writes, [])

    # boto3가 반환하는 Decimal 정수도 JSON 계약에는 정수로 정규화한다.
    def test_dynamodb_decimal_fields_are_normalized(self):
        value = state(plan())
        value["version"] = Decimal(1)
        value["plans"][0]["timing"]["schedule"]["weekdays"] = [Decimal(0), Decimal(1)]
        self.table.item = {"revision": Decimal(2), "state": value}
        response, body = self.call(self.event())
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["revision"], 2)
        self.assertEqual(body["state"]["plans"][0]["timing"]["schedule"]["weekdays"], [0, 1])

    # 윤년·기간 역전·중복 요일·중복 시각과 빈 약 이름을 저장 전에 거부한다.
    def test_invalid_schedule_and_medicine_fields(self):
        invalid_schedules = [schedule(startDate="2026-02-29"), schedule(startDate="2100-02-29"), schedule(startDate="0000-01-01"), schedule(endDate="2026-10-31"), schedule(weekdays=[]), schedule(weekdays=[1, 1]), schedule(weekdays=[True]), schedule(times=["24:00"]), schedule(times=["08:00", "08:00"])]
        for invalid in invalid_schedules:
            with self.subTest(schedule=invalid):
                value = plan()
                value["timing"]["schedule"] = invalid
                self.assertEqual(self.put(state(value))[0]["statusCode"], 422)
        value = plan(medicines=[medicine("a")])
        value["medicines"][0]["name"] = "  "
        self.assertEqual(self.put(state(value))[0]["statusCode"], 422)
        value = plan()
        value["timing"]["schedule"] = schedule(startDate="2000-02-29", endDate="")
        self.assertEqual(self.put(state(value))[0]["statusCode"], 200)

    # 목록만 등록하거나 이름 없이 묶음 전체를 확인하는 계약을 유지한다.
    def test_unscheduled_and_unidentified_plans(self):
        value = plan()
        value["timing"]["schedule"] = None
        self.assertEqual(self.put(state(value))[0]["statusCode"], 200)
        response, _ = self.put(state(value, [record(value)]), 1)
        self.assertEqual(response["statusCode"], 422)
        value["timing"]["schedule"] = schedule()
        response, body = self.put(state(value), 1)
        self.assertEqual(response["statusCode"], 200)
        response, body = self.put(state(value, [record(value, "some")]), 2)
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["state"]["records"][0]["takenMedicineIds"], [])

    # 개별 일정과 필요시 약을 반영해 그 회차에 없는 약의 복용 확인을 거부한다.
    def test_override_and_as_needed_occurrence_membership(self):
        value = plan(medicines=[medicine("common"), medicine("evening", {"mode": "regular", "schedule": schedule(times=["20:00"])}), medicine("needed", {"mode": "as_needed", "schedule": None})])
        morning = record(value, taken=["common"])
        response, body = self.put(state(value, [morning]))
        self.assertEqual(response["statusCode"], 200)
        bad = record(value, taken=["common", "needed"])
        self.assertEqual(self.put(state(value, [bad]), 1)[0]["statusCode"], 422)
        evening = record(value, "some", ["evening"], slot="20:00")
        response, body = self.put(state(value, [body["state"]["records"][0], evening]), 1)
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(len(body["state"]["records"]), 2)

    # 모든 약이 다른 시각을 쓰면 공통 시간에 빈 회차가 생기지 않는다.
    def test_no_empty_common_occurrence_when_medicines_override(self):
        value = plan(medicines=[medicine("override", {"mode": "regular", "schedule": schedule(times=["12:00"])})])
        self.assertEqual(self.put(state(value, [record(value)]))[0]["statusCode"], 422)
        self.assertEqual(self.put(state(value, [record(value, taken=["override"], slot="12:00")]))[0]["statusCode"], 200)

    # 선택한 약 집합이 결과와 일치해야 하며 외부·중복 약 ID를 받지 않는다.
    def test_partial_all_and_unrelated_medicine_validation(self):
        value = plan(medicines=[medicine("a"), medicine("b")])
        invalid = [("all", ["a"]), ("some", []), ("some", ["a", "b"]), ("some", ["a", "a"]), ("some", ["unknown"]), ("none", ["a"]), ("deferred", ["a"])]
        for result, ids in invalid:
            with self.subTest(result=result, ids=ids):
                self.assertEqual(self.put(state(value, [record(value, result, ids)]))[0]["statusCode"], 422)
        self.assertEqual(self.put(state(value, [record(value, "some", ["a"])]))[0]["statusCode"], 200)

    # 서버가 새 확인 시각을 기록하고 그대로 유지된 기록의 시각은 바꾸지 않는다.
    def test_server_confirmation_timestamp_and_preservation(self):
        value = plan()
        with patch.object(api, "recorded_now", return_value="2026-11-08T01:00:00.000Z"):
            response, body = self.put(state(value, [record(value)]))
        self.assertEqual(response["statusCode"], 200)
        saved = body["state"]
        self.assertEqual(saved["records"][0]["recordedAt"], "2026-11-08T01:00:00.000Z")
        saved["plans"][0]["notes"] = "메모만 변경"
        with patch.object(api, "recorded_now", return_value="2026-11-08T02:00:00.000Z"):
            response, body = self.put(saved, 1)
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["state"]["records"][0]["recordedAt"], "2026-11-08T01:00:00.000Z")

    # 같은 약 ID의 용량이 바뀌어도 이전 확인을 새 구성에 재사용하지 않는다.
    def test_regimen_changes_require_record_reset(self):
        value = plan(medicines=[medicine("a")])
        _, body = self.put(state(value, [record(value, taken=["a"])]))
        changed = body["state"]
        changed["plans"][0]["medicines"][0]["dose"] = "합성 수정 문구"
        response, body = self.put(changed, 1)
        self.assertEqual(response["statusCode"], 422)
        self.assertEqual(body["code"], "RECORDS_REQUIRE_RESET")
        changed["records"] = []
        self.assertEqual(self.put(changed, 1)[0]["statusCode"], 200)

    # 종료일 당일 확인은 보존하고 이후 기록은 제외해야 저장된다.
    def test_ending_preserves_past_records_and_rejects_future_records(self):
        value = plan()
        _, body = self.put(state(value, [record(value), record(value, day="2026-11-09")]))
        changed = body["state"]
        changed["plans"][0]["endedOn"] = "2026-11-08"
        self.assertEqual(self.put(changed, 1)[0]["statusCode"], 422)
        changed["records"] = changed["records"][:1]
        response, body = self.put(changed, 1)
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(len(body["state"]["records"]), 1)

    # 삭제한 묶음의 확인 기록을 남기는 요청은 고아 기록으로 차단한다.
    def test_deleted_plan_cannot_leave_records(self):
        value = plan()
        orphan = state(records=[record(value)])
        self.assertEqual(self.put(orphan)[0]["statusCode"], 422)
        self.assertEqual(self.put(state(value, [record(value)]))[0]["statusCode"], 200)
        self.assertEqual(self.put(state(), 1)[0]["statusCode"], 200)
        self.assertEqual(self.table.item["state"], state())

    # 중복 묶음·약·회차와 스키마 바깥 필드를 별도로 거부한다.
    def test_duplicates_unknown_fields_and_invalid_timestamp(self):
        value = plan(medicines=[medicine("a")])
        duplicate_plans = state(value)
        duplicate_plans["plans"].append(copy.deepcopy(value))
        duplicate_medicines = state(plan(medicines=[medicine("a"), medicine("a")]))
        duplicate_records = state(value, [record(value, taken=["a"]), record(value, taken=["a"])])
        unknown = dict(state(), patient_name="저장 금지 합성 값")
        bad_timestamp = state(value, [record(value, taken=["a"])])
        bad_timestamp["records"][0]["recordedAt"] = "2026-02-30T00:00:00Z"
        for invalid in (duplicate_plans, duplicate_medicines, duplicate_records, unknown, bad_timestamp):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.put(invalid)[0]["statusCode"], 422)

    # 각 배열 상한과 전송 바이트 상한을 분명한 오류로 반환한다.
    def test_size_and_count_limits(self):
        too_many = state()
        too_many["plans"] = [plan(f"p{index}") for index in range(101)]
        self.assertEqual(self.put(too_many)[1]["code"], "LIMIT_EXCEEDED")
        value = plan(medicines=[medicine(f"m{index}") for index in range(31)])
        self.assertEqual(self.put(state(value))[1]["code"], "LIMIT_EXCEEDED")
        value = plan()
        value["timing"]["schedule"]["times"] = [f"{index:02}:00" for index in range(13)]
        self.assertEqual(self.put(state(value))[1]["code"], "LIMIT_EXCEEDED")
        with self.assertRaises(api.ApiError) as failure:
            api.validate_state(state(plan(), [record(plan())] * 2001))
        self.assertEqual(failure.exception.code, "LIMIT_EXCEEDED")
        event = self.event("PUT", body={"expected_revision": 0, "state": state()})
        event["body"] = " " * (api.MAX_BYTES + 1)
        self.assertEqual(self.call(event)[0]["statusCode"], 413)

    # 숫자형 버전의 불리언·소수와 JSON 중복 키를 우회 입력으로 허용하지 않는다.
    def test_json_and_revision_input_boundaries(self):
        for revision in (True, -1, 0.5, api.MAX_REVISION + 1, 10**1000):
            with self.subTest(revision=revision):
                self.assertEqual(self.put(state(), revision)[0]["statusCode"], 422)
        event = self.event("PUT")
        event["body"] = '{"expected_revision":0,"expected_revision":1,"state":{}}'
        self.assertEqual(self.call(event)[0]["statusCode"], 400)
        event["body"] = '{"expected_revision":NaN,"state":{}}'
        self.assertEqual(self.call(event)[0]["statusCode"], 400)
        event["headers"]["content-type"] = "text/plain"
        self.assertEqual(self.call(event)[0]["statusCode"], 415)

    # API Gateway의 base64 본문도 같은 검증을 거치며 경로와 메서드는 제한한다.
    def test_encoded_request_options_and_route_boundary(self):
        event = self.event("PUT", body={"expected_revision": 0, "state": state(plan())})
        event["body"] = base64.b64encode(event["body"].encode()).decode()
        event["isBase64Encoded"] = True
        self.assertEqual(self.call(event)[0]["statusCode"], 200)
        self.assertEqual(self.call(self.event("OPTIONS", authenticated=False))[0]["statusCode"], 204)
        self.assertEqual(self.call(self.event("GET", "/inventory"))[0]["statusCode"], 404)
        self.assertEqual(self.call(self.event("DELETE"))[0]["statusCode"], 405)

    # 내부 예외 메시지나 저장된 본문을 클라이언트 오류에 담지 않는다.
    def test_storage_failures_do_not_expose_payload_or_exception(self):
        self.table_mock.side_effect = RuntimeError("노출 금지 합성 내부 오류")
        response, body = self.call(self.event())
        self.assertEqual(response["statusCode"], 503)
        self.assertEqual(body["code"], "MEDICATIONS_UNAVAILABLE")
        self.assertNotIn("노출 금지", response["body"])
        self.assertNotIn(self.password, response["body"])

    # 서명된 토큰도 예상한 대상과 구조가 아니면 거부한다.
    def test_signed_but_invalid_claims_are_rejected(self):
        claims = json.loads(api.decode_urlsafe(self.token.split(".")[0]))
        claims["aud"] = "different-service"
        payload = api.encode_urlsafe(json.dumps(claims).encode())
        signature = api.encode_urlsafe(hmac.new(api.configuration()["secret"], payload.encode(), hashlib.sha256).digest())
        event = self.event()
        event["headers"]["authorization"] = f"Bearer {payload}.{signature}"
        self.assertEqual(self.call(event)[0]["statusCode"], 401)


class MedicationPublicDemoTests(unittest.TestCase):
    # 공개 모드에는 비밀번호와 서명키가 없어도 명시된 범위만 설정한다.
    def setUp(self):
        self.environment = {
            "MEDKIT_MEDICATION_TABLE": api.TABLE_NAME,
            "MEDKIT_BOX_ID": "synthetic-box",
            "MEDKIT_ALLOWED_ORIGIN": "https://synthetic.example.com",
            "MEDKIT_MEDICATION_PUBLIC_DEMO": "true",
        }
        environment = patch.dict(os.environ, self.environment, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.table = FakeTable()
        table = patch.object(api, "get_table", return_value=self.table)
        self.table_mock = table.start()
        self.addCleanup(table.stop)

    # 토큰을 보내지 않는 공개 시연 요청을 호출한다.
    def call(self, method="GET", body=None, path="/medications", origin=None):
        event = {
            "rawPath": path,
            "requestContext": {"http": {"method": method}},
            "headers": {"content-type": "application/json", "origin": origin or self.environment["MEDKIT_ALLOWED_ORIGIN"]},
            "body": None if body is None else json.dumps(body),
        }
        response = api.lambda_handler(event, None)
        return response, json.loads(response["body"])

    # 명시적 공개 모드에서는 누구나 조회·수정하고 빈 목록으로 삭제할 수 있다.
    def test_public_read_create_update_and_delete_without_credentials(self):
        response, body = self.call()
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body, {"revision": 0, "state": state()})
        value = state(plan())
        response, body = self.call("PUT", {"expected_revision": 0, "state": value})
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["revision"], 1)
        value["plans"][0]["notes"] = "합성 공개 시연 수정"
        response, body = self.call("PUT", {"expected_revision": 1, "state": value})
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["state"]["plans"][0]["notes"], "합성 공개 시연 수정")
        response, body = self.call("PUT", {"expected_revision": 2, "state": state()})
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body, {"revision": 3, "state": state()})
        self.assertEqual(response["headers"]["Cache-Control"], "no-store")

    # 누락·거짓·비슷한 문자열은 공개 승인으로 취급하지 않는다.
    def test_public_flag_requires_exact_true_and_default_auth_stays_closed(self):
        MedicationApiTests.setUpClass()
        for value in (None, "false", "TRUE", "1", " true ", ""):
            with self.subTest(value=value):
                environment = dict(MedicationApiTests.environment)
                if value is not None:
                    environment["MEDKIT_MEDICATION_PUBLIC_DEMO"] = value
                with patch.dict(os.environ, environment, clear=True):
                    self.assertEqual(self.call()[0]["statusCode"], 401)
        with patch.dict(os.environ, {"MEDKIT_MEDICATION_PUBLIC_DEMO": "false"}):
            self.assertEqual(self.call()[0]["statusCode"], 503)
        self.table_mock.assert_not_called()

    # 공개 시연은 불필요한 비밀번호 세션 발급 경로를 닫는다.
    def test_public_session_is_unavailable(self):
        response, body = self.call("POST", {"password": "unused-synthetic-password"}, "/session")
        self.assertEqual(response["statusCode"], 404)
        self.assertEqual(body["code"], "SESSION_NOT_AVAILABLE")
        self.table_mock.assert_not_called()

    # 인증 생략과 무관하게 기존·동시 저장의 revision 충돌을 막는다.
    def test_public_stale_and_racing_writes_are_rejected(self):
        self.assertEqual(self.call("PUT", {"expected_revision": 0, "state": state(plan())})[0]["statusCode"], 200)
        response, body = self.call("PUT", {"expected_revision": 0, "state": state()})
        self.assertEqual(response["statusCode"], 409)
        self.assertEqual(body["code"], "REVISION_CONFLICT")
        self.table.concurrent_write = True
        self.assertEqual(self.call("PUT", {"expected_revision": 1, "state": state()})[0]["statusCode"], 409)
        self.assertEqual(len(self.table.writes), 1)

    # 공개 상태에서도 손상 항목을 읽거나 빈 상태로 덮어쓰지 않는다.
    def test_public_corrupt_data_and_malformed_state_fail(self):
        response, _ = self.call("PUT", {"expected_revision": 0, "state": {"version": 1, "plans": "invalid", "records": []}})
        self.assertEqual(response["statusCode"], 422)
        self.table.item = {"revision": 1, "state": {"version": 1, "plans": None, "records": []}}
        self.assertEqual(self.call()[1]["code"], "STORED_DATA_INVALID")
        self.assertEqual(self.call("PUT", {"expected_revision": 1, "state": state()})[1]["code"], "STORED_DATA_INVALID")
        self.assertEqual(self.table.writes, [])

    # 공개 시연에서도 출처와 배열 크기 제한은 그대로 적용한다.
    def test_public_origin_and_size_limits_remain(self):
        self.assertEqual(self.call(origin="https://other.example.com")[0]["statusCode"], 403)
        oversized = state()
        oversized["plans"] = [plan(f"p{index}") for index in range(101)]
        response, body = self.call("PUT", {"expected_revision": 0, "state": oversized})
        self.assertEqual(response["statusCode"], 422)
        self.assertEqual(body["code"], "LIMIT_EXCEEDED")
        self.assertEqual(self.table.writes, [])


# 파일 직접 실행과 unittest 모듈 실행에서 같은 합성 검증을 제공한다.
if __name__ == "__main__":
    unittest.main()
