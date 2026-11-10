"""학교 자동 태깅 환경의 생성·재시도·소유권 검증을 확인한다."""

from unittest.mock import Mock, patch
import pytest
import deploy_activity_api as deploy


# 합성 테이블과 학교 자동 소유 태그 응답을 준비한다.
def table_client(exists=False):
    client = Mock()
    client.get_paginator.return_value.paginate.return_value = [{'TableNames': [deploy.TABLE] if exists else []}]
    client.describe_table.return_value = {'Table': {
        'TableArn': f'arn:aws:dynamodb:{deploy.REGION}:{deploy.ACCOUNT}:table/{deploy.TABLE}',
        'TableStatus': 'ACTIVE',
        'KeySchema': [{'AttributeName': 'pk', 'KeyType': 'HASH'}, {'AttributeName': 'sk', 'KeyType': 'RANGE'}],
    }}
    client.list_tags_of_resource.return_value = {'Tags': [{'Key': key, 'Value': value} for key, value in deploy.OWNER_TAGS.items()]}
    return client


# 최초 테이블 생성은 수동 태그 권한을 요청하지 않는다.
def test_create_without_tag_resource():
    client = table_client()
    deploy.ensure_table(client)
    assert 'Tags' not in client.create_table.call_args.kwargs
    client.list_tags_of_resource.assert_called_once()


# 기존 테이블은 재생성하지 않으며 타인의 소유 태그를 허용하지 않는다.
def test_existing_table_owner_mismatch():
    client = table_client(True)
    client.list_tags_of_resource.return_value['Tags'][0]['Value'] = 'other-team'
    with pytest.raises(RuntimeError, match='소유 태그'):
        deploy.ensure_table(client)
    client.create_table.assert_not_called()


# 학교 자동 태그가 늦게 도착하면 항목 변경 없이 다시 확인한다.
def test_wait_for_automatic_tags():
    client = table_client(True)
    client.list_tags_of_resource.side_effect = [{'Tags': []}, client.list_tags_of_resource.return_value]
    with patch.object(deploy.time, 'sleep'):
        deploy.ensure_table(client)
    assert client.list_tags_of_resource.call_count == 2
    client.create_table.assert_not_called()


# 생성 응답 직후 태그 조회가 아직 자원을 찾지 못하면 같은 읽기만 재시도한다.
def test_tags_resource_propagation():
    client = table_client()
    error = RuntimeError('전파 대기')
    error.response = {'Error': {'Code': 'ResourceNotFoundException'}}
    client.list_tags_of_resource.side_effect = [error, client.list_tags_of_resource.return_value]
    with patch.object(deploy.time, 'sleep'):
        deploy.ensure_table(client)
    client.create_table.assert_called_once()
    assert client.list_tags_of_resource.call_count == 2


# 미생성 함수에는 소유 태그 기반 개별 조회를 시도하지 않는다.
def test_absent_function_uses_listing():
    client = Mock()
    client.get_paginator.return_value.paginate.return_value = [{'Functions': []}]
    assert deploy.existing_function(client, 'smart-medikit-notifications') is None
    client.get_function.assert_not_called()


# 같은 이름이라도 다른 관리 태그를 가진 함수는 갱신하지 않는다.
def test_function_owner_mismatch():
    client = Mock()
    client.get_paginator.return_value.paginate.return_value = [{'Functions': [{'FunctionName': 'test'}]}]
    client.get_function.return_value = {'Tags': {}, 'Configuration': {'Role': deploy.ROLE}}
    with pytest.raises(RuntimeError, match='소유 태그'):
        deploy.existing_function(client, 'test')


# 등록된 키가 불완전해도 자동으로 교체하여 구독을 무효화하지 않는다.
def test_incomplete_existing_keys_stop():
    with patch.object(deploy, 'existing_function', return_value={'Environment': {'Variables': {}}}), patch.object(deploy.subprocess, 'run') as process:
        with pytest.raises(RuntimeError, match='불완전'):
            deploy.vapid_keys(Mock())
        process.assert_not_called()
