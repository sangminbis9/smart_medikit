"""자석 센서와 Raspberry Pi 5의 16구 RGB NeoPixel 링을 제어한다."""

from __future__ import annotations

from contextlib import ExitStack

RING_COLORS = {
    None: (0, 0, 0),
    "green": (0, 255, 0),
    "yellow": (255, 255, 0),
    "white": (255, 255, 255),
}
DEFAULT_RING_BRIGHTNESS = 0.005


# 실물 센서와 링이 같은 핀을 점유하지 않도록 GPIO를 열기 전에 검사한다.
def validate_pins(config: dict):
    if "ring_data_pin" not in config:
        raise ValueError(
            "기존 lid_pin과 센서 극성은 유지하고, 아래 링 설정 중 빠진 항목을 추가하세요.\n"
            "ring_data_pin = 18\nring_brightness = 0.005\nring_pulse_seconds = 0.5"
        )
    ring_pin = config["ring_data_pin"]
    if type(ring_pin) is not int or ring_pin != 18:
        raise ValueError("이 Pi 5 링은 물리 핀 12번인 BCM GPIO18에 연결합니다.")
    lid_pin = config.get("lid_pin")
    if type(lid_pin) is not int or not 0 <= lid_pin <= 27:
        raise ValueError("마그네틱 센서의 lid_pin을 실제 BCM 핀 번호(0~27)로 설정하세요.")
    if lid_pin == ring_pin:
        raise ValueError("센서와 링은 서로 다른 BCM 핀을 사용해야 합니다.")
    brightness = config.get("ring_brightness", DEFAULT_RING_BRIGHTNESS)
    if type(brightness) not in (int, float) or not 0 <= brightness <= 1:
        raise ValueError("ring_brightness는 0~1 사이의 유한한 수여야 합니다.")


class Board:
    # 실기기 패키지는 여기서만 로드해 영상 테스트가 Pi GPIO에 의존하지 않게 한다.
    def __init__(self, config: dict):
        validate_pins(config)
        import adafruit_pixelbuf
        import board
        from adafruit_raspberry_pi5_neopixel_write import free_pio, neopixel_write
        from gpiozero import DigitalInputDevice

        class Pi5Pixelbuf(adafruit_pixelbuf.PixelBuf):
            # PixelBuf가 정렬하고 밝기를 적용한 GRB 바이트를 GPIO18로 전송한다.
            def _transmit(self, buf):
                neopixel_write(board.D18, buf)

        self._closed_when_active = config.get("lid_closed_when_active", True)
        self._color = object()
        with ExitStack() as resources:
            self._sensor = DigitalInputDevice(
                config["lid_pin"], pull_up=config.get("lid_pull_up", True)
            )
            resources.callback(self._sensor.close)
            self._pixels = Pi5Pixelbuf(
                size=16,
                brightness=config.get("ring_brightness", DEFAULT_RING_BRIGHTNESS),
                auto_write=False,
                byteorder="GRB",
            )
            # 소등 전송이 실패해도 PIO와 센서 자원을 순서대로 반환한다.
            resources.callback(free_pio)
            resources.callback(self.set_ring, None)
            self.set_ring(None)
            self._resources = resources.pop_all()

    # 배선 극성을 적용한 물리적 열림 상태를 반환한다.
    def is_open(self) -> bool:
        return bool(self._sensor.is_active) != self._closed_when_active

    # 링 전체를 같은 색으로 바꾸고 이미 표시 중인 색은 다시 전송하지 않는다.
    def set_ring(self, color: str | None) -> None:
        if color not in RING_COLORS:
            raise ValueError("지원하지 않는 링 색상입니다.")
        if self._color != color:
            # 전송 결과가 불확실하면 다음 소등 또는 같은 색 요청을 다시 보낸다.
            self._color = object()
            self._pixels.fill(RING_COLORS[color])
            self._pixels.show()
            self._color = color

    # 링을 끈 뒤 PIO와 자석 센서를 반환한다.
    def close(self) -> None:
        self._resources.close()


class MockBoard:
    # GPIO 없이 센서 입력과 링 색상 전환을 기록한다.
    def __init__(self):
        self.opened = False
        self.color = None
        self.history = []

    # 테스트에서 지정한 뚜껑 열림 상태를 반환한다.
    def is_open(self) -> bool:
        return self.opened

    # 실기기처럼 링 색상이 바뀌었을 때만 기록한다.
    def set_ring(self, color: str | None) -> None:
        if color not in RING_COLORS:
            raise ValueError("지원하지 않는 링 색상입니다.")
        if self.color != color:
            self.color = color
            self.history.append(("ring", color))

    # 테스트 종료 시 링을 끈다.
    def close(self) -> None:
        self.set_ring(None)
