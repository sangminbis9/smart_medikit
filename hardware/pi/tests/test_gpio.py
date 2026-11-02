import sys
from types import SimpleNamespace

import pytest
from medikit_pi.gpio import Board, MockBoard


class FakeDevice:
    # 핀과 초기 출력값을 기록한다.
    def __init__(self, pin, **kwargs):
        self.pin = pin
        self.options = kwargs
        self.value = kwargs.get("initial_value", False)
        self.is_active = True
        self.closed = False

    # 종료 시 출력을 끈다.
    def off(self):
        self.value = False

    # 핀 해제를 기록한다.
    def close(self):
        self.closed = True


# 실제 GPIO 패키지 없이 핀 장치를 모의한다.
@pytest.fixture
def board(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "gpiozero",
        SimpleNamespace(DigitalInputDevice=FakeDevice, OutputDevice=FakeDevice),
    )
    names = ("lid", "lamp", "white", "blue", "green", "yellow", "red")
    config = {f"{name}_pin": pin for pin, name in enumerate(names, 2)}
    return Board(config)


# 자석 센서 극성과 상태 LED 상호 배타성을 확인한다.
def test_lid_polarity_and_exclusive_led(board):
    assert not board.is_open()
    board._sensor.is_active = False
    assert board.is_open()
    board.status("white")
    board.status("blue")
    assert board._leds["blue"].value
    assert sum(led.value for led in board._leds.values()) == 1
    board.lamp(True)
    devices = board._devices.copy()
    board.close()
    assert all(device.closed for device in devices)
    assert not any(device.value for device in devices)


# 설정하지 않은 핀이나 중복 핀을 실제 장치 접근 전에 거부한다.
def test_unconfigured_and_duplicate_pins_rejected():
    with pytest.raises(ValueError, match="BCM"):
        Board({})
    names = ("lid", "lamp", "white", "blue", "green", "yellow", "red")
    with pytest.raises(ValueError, match="서로 다른"):
        Board({f"{name}_pin": 3 for name in names})


# 테스트 보드가 입력과 조명 종료 상태를 제공하는지 검사한다.
def test_mock_board_records_outputs():
    board = MockBoard()
    board.opened = True
    assert board.is_open()
    board.lamp(True)
    board.status("blue")
    board.close()
    assert not board.lamp_on
    assert board.color is None
    assert ("status", "blue") in board.history


# 일부 출력 끄기가 실패해도 모든 핀을 반환하는지 검사한다.
def test_gpio_close_releases_all_devices_after_output_error(board, monkeypatch):
    devices = board._devices.copy()

    # 연결이 끊긴 출력 장치의 오류를 모의한다.
    def fail_off():
        raise OSError("연결 끊김")

    monkeypatch.setattr(board._leds["blue"], "off", fail_off)
    with pytest.raises(OSError):
        board.close()
    assert all(device.closed for device in devices)
