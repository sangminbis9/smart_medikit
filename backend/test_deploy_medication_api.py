"""학교 자동 소유 태그의 지연과 테이블 생성 경계를 AWS 호출 없이 검증한다."""

import contextlib
import copy
import io
import unittest
from unittest.mock import Mock, patch

from backend import deploy_medication_api as deploy


class AwsFailure(Exception):
    # AWS 오류 코드와 작업명만 모의하고 원문은 응답에 노출하지 않는다.
    def __init__(self, code, operation="DescribeTable"):
        super().__init__("출력하면 안 되는 합성 원문")
        self.response = {"Error": {"Code": code}}
        self.operation_name = operation


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
