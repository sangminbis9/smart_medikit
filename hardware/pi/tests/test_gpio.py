import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from medikit_pi.gpio import Board, MockBoard


# GPIO18 전송·PIO 해제·센서 해제를 장치 없는 환경에서 관찰한다.
@pytest.fixture
def drivers(monkeypatch):
    writer = Mock()
    release = Mock()
    sensor = Mock()
    sensor.return_value.is_active = True
    pixel_setup = Mock()

    class PixelBuf:
        # 실기기 버퍼로 전달하는 크기·순서·밝기 설정을 기록한다.
        def __init__(self, **options):
            pixel_setup(**options)

        # 링 전체에 적용할 RGB 튜플을 보관한다.
        def fill(self, rgb):
            self.rgb = rgb

        # 실기기 라이브러리처럼 하위 클래스의 전송 메서드를 호출한다.
        def show(self):
            self._transmit(self.rgb)

    monkeypatch.setitem(sys.modules, "board", SimpleNamespace(D18=18))
    monkeypatch.setitem(sys.modules, "adafruit_pixelbuf", SimpleNamespace(PixelBuf=PixelBuf))
    monkeypatch.setitem(sys.modules, "gpiozero", SimpleNamespace(DigitalInputDevice=sensor))
    monkeypatch.setitem(
        sys.modules,
        "adafruit_raspberry_pi5_neopixel_write",
        SimpleNamespace(neopixel_write=writer, free_pio=release),
    )
    return SimpleNamespace(writer=writer, release=release, sensor=sensor, pixel_setup=pixel_setup)


# 한 선으로 전체 링을 구동하고 색이 유지되면 프레임마다 재전송하지 않는다.
def test_ring_uses_pi5_writer_and_cleans_up(drivers):
    board = Board({"lid_pin": 17, "ring_data_pin": 18})
    drivers.pixel_setup.assert_called_once_with(
        size=16, brightness=0.005, auto_write=False, byteorder="GRB"
    )
    assert not board.is_open()
    drivers.sensor.return_value.is_active = False
    assert board.is_open()
    for color in ("green", "yellow", "yellow", "white"):
        board.set_ring(color)
    board.close()
    board.close()
    assert drivers.writer.call_args_list == [
        call(18, (0, 0, 0)),
        call(18, (0, 255, 0)),
        call(18, (255, 255, 0)),
        call(18, (255, 255, 255)),
        call(18, (0, 0, 0)),
    ]
    drivers.release.assert_called_once()
    drivers.sensor.return_value.close.assert_called_once()


# 초기 소등이 실패해도 센서와 PIO가 다음 실행을 막지 않게 반환되는지 확인한다.
def test_failed_initial_write_releases_resources(drivers):
    drivers.writer.side_effect = OSError("PIO")
    with pytest.raises(OSError, match="PIO"):
        Board({"lid_pin": 17, "ring_data_pin": 18})
    drivers.release.assert_called_once()
    drivers.sensor.return_value.close.assert_called_once()


# 색상 전송이 실패하면 이전 색을 신뢰하지 않고 종료 시 소등을 실제로 보낸다.
def test_failed_color_write_still_blanks_ring_on_close(drivers):
    board = Board({"lid_pin": 17, "ring_data_pin": 18})
    drivers.writer.side_effect = OSError("PIO")
    with pytest.raises(OSError, match="PIO"):
        board.set_ring("green")
    drivers.writer.side_effect = None
    board.close()
    assert drivers.writer.call_args_list[-1] == call(18, (0, 0, 0))
    drivers.release.assert_called_once()


# 별도 LED용 옛 설정, 중복 핀 및 잘못된 밝기를 장치 접근 전에 거부한다.
@pytest.mark.parametrize(
    "config",
    [
        {"lid_pin": 17, "lamp_pin": 4},
        {"lid_pin": 18, "ring_data_pin": 18},
        {"lid_pin": True, "ring_data_pin": 18},
        {"lid_pin": 17, "ring_data_pin": 12},
        {"lid_pin": 17, "ring_data_pin": 18, "ring_brightness": float("nan")},
    ],
)
def test_invalid_board_config_never_opens_pins(config, drivers):
    with pytest.raises(ValueError):
        Board(config)
    drivers.sensor.assert_not_called()
    drivers.writer.assert_not_called()


# 영상 모드에서도 동일한 색을 반복 기록하지 않고 종료할 때 소등한다.
def test_mock_board_records_ring_transitions():
    board = MockBoard()
    board.opened = True
    assert board.is_open()
    board.set_ring("green")
    board.set_ring("green")
    board.close()
    assert board.color is None
    assert board.history == [("ring", "green"), ("ring", None)]
