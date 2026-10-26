"""BCM 핀 설정에 따른 자석 센서, 조명 및 상태 LED 제어."""

from __future__ import annotations


class Board:
    COLORS = ("white", "blue", "green", "yellow", "red")

    # 핀 충돌과 미설정을 확인한 뒤 모든 출력을 꺼진 상태로 연다.
    def __init__(self, config: dict):
        names = ("lid_pin", "lamp_pin", *(f"{color}_pin" for color in self.COLORS))
        pins = [config.get(name, -1) for name in names]
        if any(type(pin) is not int or not 0 <= pin <= 27 for pin in pins):
            raise ValueError("센서·조명·LED의 BCM 핀을 0~27로 설정하세요.")
        if len(set(pins)) != len(pins):
            raise ValueError("센서·조명·LED는 서로 다른 BCM 핀을 사용해야 합니다.")
        from gpiozero import DigitalInputDevice, OutputDevice

        self._devices = []
        self._leds = {}
        self._closed_when_active = config.get("lid_closed_when_active", True)
        try:
            self._sensor = DigitalInputDevice(
                config["lid_pin"], pull_up=config.get("lid_pull_up", True)
            )
            self._devices.append(self._sensor)
            active_high = config.get("output_active_high", True)
            self._lamp = OutputDevice(
                config["lamp_pin"], active_high=active_high, initial_value=False
            )
            self._devices.append(self._lamp)
            for color in self.COLORS:
                led = OutputDevice(
                    config[f"{color}_pin"], active_high=active_high, initial_value=False
                )
                self._leds[color] = led
                self._devices.append(led)
        except Exception:
            try:
                self.close()
            except Exception:
                pass
            raise

    # 배선 극성을 적용한 물리적 열림 상태를 반환한다.
    def is_open(self) -> bool:
        return bool(self._sensor.is_active) != self._closed_when_active

    # 카메라 촬영용 조명을 켜거나 끈다.
    def lamp(self, enabled: bool) -> None:
        self._lamp.value = bool(enabled)

    # 한 번에 하나의 상태 LED만 켜며 None이면 모두 끈다.
    def status(self, color: str | None) -> None:
        if color is not None and color not in self.COLORS:
            raise ValueError("지원하지 않는 상태 LED입니다.")
        for name, led in self._leds.items():
            led.value = name == color

    # 조명과 LED를 끈 뒤 확보했던 모든 GPIO 핀을 반환한다.
    def close(self) -> None:
        first_error = None
        for device in reversed(self._devices):
            try:
                try:
                    if hasattr(device, "off"):
                        device.off()
                finally:
                    device.close()
            except Exception as exc:
                first_error = first_error or exc
        self._devices.clear()
        if first_error is not None:
            raise first_error


class MockBoard:
    # GPIO 없이 센서 입력과 출력 이력을 기록한다.
    def __init__(self):
        self.opened = False
        self.lamp_on = False
        self.color = None
        self.history = []

    # 테스트에서 지정한 뚜껑 열림 상태를 반환한다.
    def is_open(self) -> bool:
        return self.opened

    # 조명 변경 이력을 기록한다.
    def lamp(self, enabled: bool) -> None:
        self.lamp_on = bool(enabled)
        self.history.append(("lamp", self.lamp_on))

    # 상태 LED 변경 이력을 기록한다.
    def status(self, color: str | None) -> None:
        if color is not None and color not in Board.COLORS:
            raise ValueError("지원하지 않는 상태 LED입니다.")
        self.color = color
        self.history.append(("status", color))

    # 테스트 종료 시 출력이 꺼지는지 확인할 수 있게 기록한다.
    def close(self) -> None:
        self.lamp(False)
        self.status(None)
