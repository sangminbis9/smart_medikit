"""설치 전 점검이 사진·재고를 변경하지 않고 잘못된 연결을 거부하는지 확인한다."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from medikit_pi.__main__ import main
from medikit_pi.config import load_config
from medikit_pi.gpio import validate_pins
from medikit_pi.transport import S3Transport


# 예제에 배포 주소가 있어도 실제 촬영 업로드는 기본 비활성인지 확인한다.
def test_deployment_defaults_are_off():
    config = load_config(Path(__file__).parents[1] / "config.example.toml")
    assert config["box_id"] == "inha-capstone-02-medkit"
    assert config["aws"]["enabled"] is False
    assert config["aws"]["staged_capture"] is True
    assert config["aws"]["contract_confirmed"] is True
    assert config["aws"]["prefix"] == "medkit-sessions"
    assert config["aws"]["ocr_input_prefix"] == "ocr-input"
    assert config["aws"]["manifest_name"] == "closed-ready.json"
    assert config["aws"]["status_url_template"].endswith("/capture-events/{event_id}")


# 실제 AWS 연결 대신 단기 인증과 서비스별 클라이언트를 주입한다.
@pytest.fixture
def connection():
    config = load_config(Path(__file__).parents[1] / "config.example.toml")["aws"]
    session, sts, s3 = Mock(), Mock(), Mock()
    session.get_credentials.return_value.get_frozen_credentials.return_value = SimpleNamespace(
        token="synthetic-token"
    )
    session.client.side_effect = [sts, s3]
    sts.get_caller_identity.return_value = {"Account": config["expected_account_id"]}
    transport = S3Transport(config, session=session)
    transport.status = Mock(return_value="PROCESSING")
    return transport, session, sts, s3


# 점검은 STS·버킷 HEAD·새 ID 조회만 하며 촬영 큐와 쓰기 API를 사용하지 않는다.
def test_connection_is_read_only(connection):
    transport, session, sts, s3 = connection
    assert transport.check_connection() == "PROCESSING"
    sts.get_caller_identity.assert_called_once_with()
    s3.head_bucket.assert_called_once_with(
        Bucket="inha-capstone-02-s3", ExpectedBucketOwner="842544789367"
    )
    assert [call[0] for call in s3.method_calls] == ["head_bucket"]
    assert [call.args[0] for call in session.client.call_args_list] == ["sts", "s3"]
    probe = transport.status.call_args.args[0]
    assert probe.startswith("pi-check-") and len(probe) == 41


# 다른 계정으로 로그인했으면 S3와 상태 API에 접근하기 전에 중단한다.
def test_wrong_account_stops_before_bucket(connection):
    transport, session, sts, s3 = connection
    sts.get_caller_identity.return_value = {"Account": "000000000000"}
    with pytest.raises(ValueError, match="다른 자격증명"):
        transport.check_connection()
    s3.head_bucket.assert_not_called()
    transport.status.assert_not_called()


# 단기 토큰 없는 키는 점검 단계부터 거부한다.
def test_long_lived_key_cannot_pass_preflight(connection):
    transport, session, sts, s3 = connection
    session.get_credentials.return_value.get_frozen_credentials.return_value.token = None
    with pytest.raises(RuntimeError, match="장기 AWS 키"):
        transport.check_connection()
    session.client.assert_not_called()


# 버킷 조회가 거부되면 성공으로 감추거나 사진을 써서 우회하지 않는다.
def test_bucket_denied_stops_probe(connection):
    transport, session, sts, s3 = connection
    s3.head_bucket.side_effect = PermissionError("synthetic")
    with pytest.raises(PermissionError):
        transport.check_connection()
    transport.status.assert_not_called()
    s3.put_object.assert_not_called()


# 완료된 실제 이벤트 응답을 연결 점검 성공으로 받아들이지 않는다.
def test_unexpected_probe_response_is_rejected(connection):
    transport, session, sts, s3 = connection
    transport.status.return_value = "COMPLETED"
    with pytest.raises(ValueError, match="계약"):
        transport.check_connection()


# 점검 실패 로그에는 인증 공급자 예외 본문을 남기지 않는다.
def test_cli_failure_redacts_provider_error(monkeypatch, caplog):
    check = Mock(side_effect=RuntimeError("SYNTHETIC_SECRET_MUST_NOT_APPEAR"))
    monkeypatch.setattr(S3Transport, "check_connection", check)
    monkeypatch.setattr("boto3.Session", Mock())
    path = Path(__file__).parents[1] / "config.example.toml"
    assert main(["--config", str(path), "check-aws"]) == 1
    assert "SYNTHETIC_SECRET" not in caplog.text


# 핀 미설정과 중복 핀을 GPIO 라이브러리 없이 검사한다.
def test_hardware_pin_preflight():
    path = Path(__file__).parents[1] / "config.example.toml"
    assert main(["--config", str(path), "check-config"]) == 0
    assert main(["--config", str(path), "check-config", "--hardware"]) == 1
    pins = {
        name: i + 2
        for i, name in enumerate(
            ("lid_pin", "lamp_pin", "white_pin", "blue_pin", "green_pin", "yellow_pin", "red_pin")
        )
    }
    validate_pins(pins)
    pins["red_pin"] = pins["lid_pin"]
    with pytest.raises(ValueError, match="서로 다른"):
        validate_pins(pins)
