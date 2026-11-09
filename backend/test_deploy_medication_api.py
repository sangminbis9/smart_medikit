"""학교 자동 소유 태그의 지연과 테이블 생성 경계를 AWS 호출 없이 검증한다."""

import contextlib
import copy
import io
import json
import os
import unittest
import zipfile
from unittest.mock import Mock, patch

from backend import deploy_medication_api as deploy
from backend import medication_api as api
from backend import test_medication_api as fixtures


class AwsFailure(Exception):
    # AWS 오류 코드와 작업명만 모의하고 원문은 응답에 노출하지 않는다.
    def __init__(self, code, operation="DescribeTable"):
        super().__init__("출력하면 안 되는 합성 원문")
        self.response = {"Error": {"Code": code}}
        self.operation_name = operation


class MedicationMfdsKeyTests(unittest.TestCase):
    # 다른 계정·리전·함수의 키는 읽지 않고 승인된 함수만 확인한다.
    def test_only_named_same_account_and_region_source_is_read(self):
        lambdas = Mock()
        synthetic_key = "synthetic-test-key"
        lambdas.get_function_configuration.return_value = {
            "FunctionArn": f"arn:aws:lambda:ap-northeast-2:{deploy.TABLE_OWNER_ACCOUNT}:function:{deploy.MFDS_KEY_SOURCE_FUNCTION}",
            "Environment": {"Variables": {"MFDS_API_KEY": synthetic_key}},
        }
        self.assertEqual(deploy.read_mfds_key_from_ocr(lambdas, deploy.TABLE_OWNER_ACCOUNT, "ap-northeast-2", deploy.MFDS_KEY_SOURCE_FUNCTION), synthetic_key)
        for account, region, name in (("000000000000", "ap-northeast-2", deploy.MFDS_KEY_SOURCE_FUNCTION),
                                      (deploy.TABLE_OWNER_ACCOUNT, "us-east-1", deploy.MFDS_KEY_SOURCE_FUNCTION),
                                      (deploy.TABLE_OWNER_ACCOUNT, "ap-northeast-2", "unrelated")):
            with self.assertRaises(RuntimeError) as error:
                deploy.read_mfds_key_from_ocr(lambdas, account, region, name)
            self.assertNotIn(synthetic_key, str(error.exception))
        self.assertEqual(lambdas.get_function_configuration.call_count, 1)

    # 함수 ARN 불일치나 키 누락은 키 원문 없이 실패한다.
    def test_invalid_source_and_missing_key_fail_closed(self):
        lambdas = Mock()
        lambdas.get_function_configuration.return_value = {
            "FunctionArn": "arn:aws:lambda:ap-northeast-2:000000000000:function:unrelated",
            "Environment": {"Variables": {"MFDS_API_KEY": "synthetic-secret"}},
        }
        with self.assertRaises(RuntimeError) as error:
            deploy.read_mfds_key_from_ocr(lambdas, deploy.TABLE_OWNER_ACCOUNT, "ap-northeast-2", deploy.MFDS_KEY_SOURCE_FUNCTION)
        self.assertNotIn("synthetic-secret", str(error.exception))
        lambdas.get_function_configuration.return_value = {
            "FunctionArn": f"arn:aws:lambda:ap-northeast-2:{deploy.TABLE_OWNER_ACCOUNT}:function:{deploy.MFDS_KEY_SOURCE_FUNCTION}",
            "Environment": {"Variables": {}},
        }
        with self.assertRaises(RuntimeError):
            deploy.read_mfds_key_from_ocr(lambdas, deploy.TABLE_OWNER_ACCOUNT, "ap-northeast-2", deploy.MFDS_KEY_SOURCE_FUNCTION)

    # 배포 ZIP에는 원본 공식 조회기만 포함하고 환경변수나 키 파일은 넣지 않는다.
    def test_archive_reuses_original_client_without_secrets(self):
        with zipfile.ZipFile(io.BytesIO(deploy.source_archive())) as archive:
            self.assertEqual(set(archive.namelist()), {"medication_api.py", "medkit_mfds.py"})
            self.assertIn(b"class MfdsClient", archive.read("medkit_mfds.py"))

    # 실제 품목코드가 경로 변수에 들어와도 승인된 API의 GET 경로만 호출하도록 제한한다.
    def test_product_detail_permission_uses_route_wildcard(self):
        lambdas = Mock()
        lambdas.get_policy.return_value = {"Policy": '{"Statement": []}'}
        deploy.ensure_permission(lambdas, deploy.TABLE_OWNER_ACCOUNT, "ap-northeast-2", "synthetic-api", "GET /medicine-products/{productCode}", "MedicationProductDetail")
        self.assertEqual(lambdas.add_permission.call_args.kwargs["SourceArn"], f"arn:aws:execute-api:ap-northeast-2:{deploy.TABLE_OWNER_ACCOUNT}:synthetic-api/*/GET/medicine-products/*")


class FakeClock:
    # 실제 대기 없이 재시도 총 시간을 확인한다.
    def __init__(self):
        self.elapsed = 0

    # 누적된 가상 단조 시간을 반환한다.
    def monotonic(self):
        return self.elapsed

    # 요청한 대기 시간만 가상 시계에 더한다.
    def sleep(self, seconds):
        self.elapsed += seconds


# 승인한 계정·리전·이름과 키 계약을 갖는 합성 메타데이터를 만든다.
def table_metadata():
    return {
        "TableName": deploy.TABLE,
        "TableArn": f"arn:aws:dynamodb:ap-northeast-2:{deploy.TABLE_OWNER_ACCOUNT}:table/{deploy.TABLE}",
        "KeySchema": copy.deepcopy(deploy.KEY_SCHEMA),
        "AttributeDefinitions": [{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
        "BillingModeSummary": {"BillingMode": "PAY_PER_REQUEST"},
        "TableStatus": "ACTIVE",
    }


# 학교 자동 태그 목록을 SDK 응답 모양으로 만든다.
def tag_response(values=None):
    values = deploy.TABLE_OWNER_TAGS if values is None else values
    return {"Tags": [{"Key": key, "Value": value} for key, value in values.items()]}


class MedicationDeploymentTableTests(unittest.TestCase):
    # 모든 AWS 호출과 대기를 모의 객체로 격리한다.
    def setUp(self):
        self.client = Mock()
        self.client.meta.region_name = "ap-northeast-2"
        self.client.describe_table.return_value = {"Table": table_metadata()}
        self.client.list_tags_of_resource.return_value = tag_response()
        self.clock = FakeClock()
        for name in ("monotonic", "sleep"):
            replacement = patch.object(deploy.time, name, getattr(self.clock, name))
            replacement.start()
            self.addCleanup(replacement.stop)

    # TagResource 없는 신규 생성 뒤 학교 소유권을 확인하고 항목은 쓰지 않는다.
    def test_creation_omits_tags_and_checks_automatic_owner(self):
        result = deploy.ensure_table(self.client, None)
        self.assertNotIn("Tags", self.client.create_table.call_args.kwargs)
        self.assertEqual(result["TableArn"], table_metadata()["TableArn"])
        self.client.tag_resource.assert_not_called()
        self.client.put_item.assert_not_called()
        self.assertEqual(self.clock.elapsed, 0)

    # 일부 소유 태그만 먼저 보이면 두 태그가 확인될 때까지 한정 대기한다.
    def test_delayed_automatic_tags_are_retried(self):
        self.client.list_tags_of_resource.side_effect = [tag_response({}), tag_response({"username": "inha-capstone-02"}), tag_response()]
        deploy.ensure_table(self.client, None)
        self.assertEqual(self.clock.elapsed, 4)
        self.assertEqual(self.client.describe_table.call_count, 3)

    # 생성 직후 소유 태그 기반 조회가 잠시 거부되어도 제한 안에서 다시 확인한다.
    def test_temporary_read_access_denial_is_retried(self):
        self.client.describe_table.side_effect = [AwsFailure("AccessDeniedException"), {"Table": table_metadata()}]
        deploy.ensure_table(self.client, None)
        self.assertEqual(self.clock.elapsed, 2)
        self.assertEqual(self.client.describe_table.call_count, 2)

    # 지속되는 권한 실패를 자동 태그 성공으로 간주하지 않고 60초 안에 중단한다.
    def test_persistent_access_denial_stops_at_limit(self):
        self.client.describe_table.side_effect = AwsFailure("AccessDeniedException")
        with self.assertRaisesRegex(RuntimeError, "조회 권한"):
            deploy.ensure_table(self.client, None)
        self.assertEqual(self.clock.elapsed, 60)
        self.client.put_item.assert_not_called()

    # 태그가 끝내 없으면 소유권 검사를 생략하지 않는다.
    def test_missing_automatic_tags_do_not_bypass_ownership(self):
        self.client.list_tags_of_resource.return_value = tag_response({})
        with self.assertRaisesRegex(RuntimeError, "자동 소유 태그"):
            deploy.ensure_table(self.client, None)
        self.assertEqual(self.clock.elapsed, 60)

    # 다른 소유자나 다른 관리 도구의 명시적인 태그는 즉시 거부한다.
    def test_conflicting_owner_or_manager_is_not_retried(self):
        for changed in ({"username": "another-user"}, {"group": "another-group"}, {"ManagedBy": "another-project"}):
            with self.subTest(changed=changed):
                self.client.list_tags_of_resource.return_value = tag_response({**deploy.TABLE_OWNER_TAGS, **changed})
                with self.assertRaisesRegex(RuntimeError, "소유 태그"):
                    deploy.ensure_table(self.client, table_metadata())
        self.assertEqual(self.clock.elapsed, 0)
        self.client.create_table.assert_not_called()

    # 같은 학교 태그라도 다른 계정 ARN이나 저장 키를 가진 테이블은 받지 않는다.
    def test_exact_arn_and_schema_are_required(self):
        invalid = table_metadata()
        invalid["TableArn"] = invalid["TableArn"].replace(deploy.TABLE_OWNER_ACCOUNT, "111111111111")
        self.client.describe_table.return_value = {"Table": invalid}
        with self.assertRaisesRegex(RuntimeError, "ARN"):
            deploy.ensure_table(self.client, invalid)
        invalid = table_metadata()
        invalid["KeySchema"] = [{"AttributeName": "other", "KeyType": "HASH"}]
        self.client.describe_table.return_value = {"Table": invalid}
        with self.assertRaisesRegex(RuntimeError, "키 구조"):
            deploy.ensure_table(self.client, invalid)
        self.assertEqual(self.clock.elapsed, 0)

    # 이전 생성이 성공한 뒤 재실행해도 기존 항목과 태그를 수정하지 않는다.
    def test_existing_table_after_partial_attempt_is_only_verified(self):
        self.client.create_table.side_effect = AwsFailure("ResourceInUseException", "CreateTable")
        self.client.list_tags_of_resource.return_value = tag_response({**deploy.TABLE_OWNER_TAGS, "ManagedBy": deploy.NAME})
        deploy.ensure_table(self.client, None)
        self.client.tag_resource.assert_not_called()
        self.client.update_table.assert_not_called()
        self.client.put_item.assert_not_called()

    # 오류 출력에는 안전한 작업명만 더하고 AWS 원문은 넣지 않는다.
    def test_error_reports_sanitized_operation_without_original_message(self):
        arguments = ["deploy", "--account", deploy.TABLE_OWNER_ACCOUNT, "--box-id", "inha-capstone-02-medkit", "--origin", "https://synthetic.example.com"]
        for operation, expected in (("CreateTable", "CreateTable"), ("secret with spaces", "UnknownOperation")):
            output = io.StringIO()
            with patch.object(deploy.sys, "argv", arguments), patch.object(deploy, "deploy", side_effect=AwsFailure("AccessDeniedException", operation)), contextlib.redirect_stderr(output):
                self.assertEqual(deploy.main(), 1)
            self.assertIn(expected, output.getvalue())
            self.assertNotIn("합성 원문", output.getvalue())
            self.assertNotIn("secret with spaces", output.getvalue())


class MedicationPublicDeploymentTests(unittest.TestCase):
    # 실제 비밀번호 대신 기존 합성 해시를 사용하는 배포 입력을 준비한다.
    def setUp(self):
        fixtures.MedicationApiTests.setUpClass()
        self.variables = dict(fixtures.MedicationApiTests.environment)
        self.origin = self.variables["MEDKIT_ALLOWED_ORIGIN"]

    # 최초 공개 배포는 비밀번호 입력이나 인증정보 생성을 요구하지 않는다.
    def test_first_public_deployment_does_not_prompt_for_password(self):
        with patch.object(deploy, "prompt_credentials") as prompt:
            result = deploy.build_environment(None, "synthetic-box", self.origin, False, True)["Variables"]
        prompt.assert_not_called()
        self.assertEqual(result["MEDKIT_MEDICATION_PUBLIC_DEMO"], "true")
        self.assertNotIn("MEDKIT_PASSWORD_HASH", result)
        self.assertNotIn("MEDKIT_SESSION_SECRET", result)

    # 명시 전환 뒤 옵션 없는 재배포도 공개 flag와 기존 비밀값을 조용히 바꾸지 않는다.
    def test_public_selection_and_redeployment_preserve_existing_secrets(self):
        function = {"Configuration": {"Environment": {"Variables": self.variables}}}
        with patch.object(deploy, "prompt_credentials") as prompt:
            environment = deploy.build_environment(function, "synthetic-box", self.origin, False, True)
            redeployed = deploy.build_environment({"Configuration": {"Environment": environment}}, "synthetic-box", self.origin, False)
        prompt.assert_not_called()
        self.assertEqual(redeployed["Variables"]["MEDKIT_MEDICATION_PUBLIC_DEMO"], "true")
        for key in ("MEDKIT_PASSWORD_HASH", "MEDKIT_SESSION_SECRET"):
            self.assertEqual(redeployed["Variables"][key], self.variables[key])

    # 공개 선택이 없으면 기존 비밀번호 모드를 유지하며 새 비공개 배포만 비밀번호를 요청한다.
    def test_private_default_remains_password_protected(self):
        function = {"Configuration": {"Environment": {"Variables": self.variables}}}
        credentials = (self.variables["MEDKIT_PASSWORD_HASH"], self.variables["MEDKIT_SESSION_SECRET"])
        with patch.object(deploy, "prompt_credentials", return_value=credentials) as prompt:
            existing = deploy.build_environment(function, "synthetic-box", self.origin, False)
            prompt.assert_not_called()
            new = deploy.build_environment(None, "synthetic-box", self.origin, False)
            prompt.assert_called_once()
        self.assertEqual(existing["Variables"]["MEDKIT_MEDICATION_PUBLIC_DEMO"], "false")
        self.assertEqual(new["Variables"]["MEDKIT_MEDICATION_PUBLIC_DEMO"], "false")

    # 공개 검증은 토큰 없이 읽고 반드시 실패할 입력만 보내 실제 항목을 쓰지 않는다.
    def test_public_verification_never_creates_or_changes_snapshot(self):
        for initial in (None, {"revision": 7, "state": fixtures.state(fixtures.plan())}):
            with self.subTest(existing=initial is not None):
                table = fixtures.FakeTable(initial)
                client = Mock()

                # 네트워크 없이 같은 핸들러를 실행하고 인증 헤더가 없는지 확인한다.
                def invoke(**request):
                    event = json.loads(request["Payload"])
                    self.assertNotIn("authorization", event["headers"])
                    response = api.lambda_handler(event, None)
                    return {"Payload": io.BytesIO(json.dumps(response).encode())}

                client.invoke.side_effect = invoke
                output = io.StringIO()
                with patch.dict(os.environ, {**self.variables, "MEDKIT_MEDICATION_PUBLIC_DEMO": "true"}, clear=True), patch.object(api, "get_table", return_value=table), contextlib.redirect_stdout(output):
                    deploy.verify_public_demo(client, self.origin)
                self.assertEqual(client.invoke.call_count, 2)
                self.assertEqual(table.item, initial)
                self.assertEqual(table.writes, [])
                self.assertIn("PUBLIC_GET_STATUS=200", output.getvalue())
                self.assertIn("PUBLIC_INVALID_PUT_STATUS=422", output.getvalue())
                self.assertNotIn("합성 처방", output.getvalue())

    # 비공개 배포의 무인증 거부 검증은 공개 기능 추가 뒤에도 그대로 동작한다.
    def test_private_deny_verification_remains_unchanged(self):
        client = Mock()

        # 실제 핸들러로 무인증 요청의 결과만 반환한다.
        def invoke(**request):
            response = api.lambda_handler(json.loads(request["Payload"]), None)
            return {"Payload": io.BytesIO(json.dumps(response).encode())}

        client.invoke.side_effect = invoke
        with patch.dict(os.environ, self.variables, clear=True), patch.object(api, "get_table") as table:
            deploy.verify_deny(client, self.origin)
        self.assertEqual(client.invoke.call_count, 2)
        table.assert_not_called()


class MedicationDeploymentRevisionTests(unittest.TestCase):
    # 코드 갱신 응답과 활성화 완료 후 revision이 다른 상황을 준비한다.
    def setUp(self):
        self.role = f"arn:aws:iam::{deploy.TABLE_OWNER_ACCOUNT}:role/{deploy.ROLE_NAME}"
        self.configuration = {
            "RevisionId": "before-update",
            "FunctionArn": f"arn:aws:lambda:ap-northeast-2:{deploy.TABLE_OWNER_ACCOUNT}:function:{deploy.NAME}",
            "Role": self.role,
            "Runtime": "python3.12",
            "Handler": deploy.HANDLER,
            "Timeout": 10,
            "MemorySize": 256,
            "Environment": {"Variables": {"MEDKIT_BOX_ID": "synthetic-box"}},
        }
        self.function = {"Configuration": copy.deepcopy(self.configuration)}
        self.current = copy.deepcopy(self.configuration)
        self.current["RevisionId"] = "after-activation"
        self.client = Mock()
        self.client.update_function_code.return_value = {"RevisionId": "code-response"}
        self.client.get_function_configuration.return_value = self.current
        self.client.get_account_settings.return_value = {"AccountLimit": {"UnreservedConcurrentExecutions": 102}}

    # 활성화 후 재조회한 revision을 사용하되 최초 코드 수정에도 기존 revision을 요구한다.
    def test_configuration_update_uses_post_wait_revision(self):
        desired = {"Variables": {"MEDKIT_BOX_ID": "synthetic-box", "MEDKIT_ALLOWED_ORIGIN": "https://synthetic.example.com"}}
        result = deploy.ensure_function(self.client, self.function, self.role, desired, b"synthetic-archive")
        self.assertEqual(result, self.configuration["FunctionArn"])
        self.assertEqual(self.client.update_function_code.call_args.kwargs["RevisionId"], "before-update")
        updated = self.client.update_function_configuration.call_args.kwargs
        self.assertEqual(updated["RevisionId"], "after-activation")
        self.assertEqual(updated["Environment"], desired)
        self.assertNotEqual(updated["RevisionId"], self.client.update_function_code.return_value["RevisionId"])

    # 다른 작업자가 환경변수를 바꿨으면 최신 revision을 알아도 설정을 덮어쓰지 않는다.
    def test_concurrent_environment_change_blocks_configuration_update(self):
        self.current["Environment"]["Variables"]["MEDKIT_ALLOWED_ORIGIN"] = "https://other-change.example.com"
        with self.assertRaisesRegex(RuntimeError, "설정이 변경"):
            deploy.ensure_function(self.client, self.function, self.role, self.configuration["Environment"], b"synthetic-archive")
        self.client.update_function_configuration.assert_not_called()
        self.client.put_function_concurrency.assert_not_called()


# 직접 실행과 unittest 모듈 실행에서 같은 모의 검증을 제공한다.
if __name__ == "__main__":
    unittest.main()
