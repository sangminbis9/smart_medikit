#!/usr/bin/env python3
"""현재 smart_medikit/hardware/pi에 IMX219 및 pinctrl 입력 호환 모드를 추가한다."""
import argparse
from pathlib import Path
import re
import shutil
import sys


def patch_one(text, before, after, name):
    if after in text:
        return text
    if before not in text:
        raise RuntimeError(f'{name}: 예상한 기존 코드를 찾을 수 없습니다. GitHub 버전을 확인하세요.')
    return text.replace(before, after, 1)


def patch_camera(text):
    text = patch_one(text, '        self.timeout = float(config.get("timeout_seconds", 3.0))',
      '        self.timeout = float(config.get("timeout_seconds", 3.0))\n        self.require_autofocus = config.get("require_autofocus", True)', 'camera timeout')
    text = patch_one(text,
'''                self._camera = Picamera2(self.config.get("camera_index", 0))
                if "AfMode" not in self._camera.camera_controls:
                    raise CameraError("자동초점 카메라가 필요합니다.")
                common = {"AfMode": controls.AfModeEnum.Continuous}''',
'''                options = {}
                if self.config.get("tuning_file"):
                    options["tuning"] = str(self.config["tuning_file"])
                self._camera = Picamera2(self.config.get("camera_index", 0), **options)
                if self.require_autofocus and "AfMode" not in self._camera.camera_controls:
                    raise CameraError("자동초점 카메라가 필요합니다.")
                # 임시 고정초점 모드는 AF 평가 대신 선명도 검사를 사용한다.
                if not self.require_autofocus:
                    LOGGER.warning("임시 비AF 카메라: AF 검증 불가, 이미지 선명도 검사 사용")
                common = (
                    {"AfMode": controls.AfModeEnum.Continuous}
                    if self.require_autofocus else {}
                )''', 'camera start')
    text = patch_one(text,
'''            focused = metadata.get("AfState") == self._controls.AfStateEnum.Focused
            return Frame(image, focused, time.monotonic())''',
'''            focused = (
                metadata.get("AfState") == self._controls.AfStateEnum.Focused
                if self.require_autofocus else True
            )
            return Frame(
                image, focused, time.monotonic(),
                autofocus_simulated=not self.require_autofocus,
            )''', 'camera frame')
    text = patch_one(text,
'''            while True:
                job = self._camera.capture_request(wait=False, flush=True)
                request = self._wait_until(job, deadline)
                try:
                    state = request.get_metadata().get("AfState")
                except Exception:
                    request.release()
                    raise
                # 고해상도 전환 뒤 렌즈 탐색이 끝난 프레임만 화질 검사로 넘긴다.
                if state in (
                    self._controls.AfStateEnum.Focused,
                    self._controls.AfStateEnum.Failed,
                ):
                    frame = self._frame(request)
                    break
                request.release()''',
'''            while True:
                job = self._camera.capture_request(wait=False, flush=True)
                request = self._wait_until(job, deadline)
                # AF가 없는 카메라에서는 모드 전환 후 프레임의 선명도를 평가한다.
                if not self.require_autofocus:
                    frame = self._frame(request)
                    break
                try:
                    state = request.get_metadata().get("AfState")
                except Exception:
                    request.release()
                    raise
                # 고해상도 전환 뒤 렌즈 탐색이 끝난 프레임만 화질 검사로 넘긴다.
                if state in (
                    self._controls.AfStateEnum.Focused,
                    self._controls.AfStateEnum.Failed,
                ):
                    frame = self._frame(request)
                    break
                request.release()''', 'camera capture')
    text = patch_one(text,
'''    def refocus(self) -> bool:
        if not self._running:''',
'''    def refocus(self) -> bool:
        if not self.require_autofocus:
            return False
        if not self._running:''', 'camera refocus')
    return text


def patch_gpio(text):
    cls = '''

class PinctrlInput:
    # 테스트에서 확인된 pinctrl 명령으로 접점의 실제 전압을 읽는다.
    def __init__(self, pin):
        self.pin = pin

    @property
    def is_active(self):
        result = subprocess.run(
            ["pinctrl", "get", str(self.pin)], text=True,
            capture_output=True, check=True, timeout=2,
        )
        match = re.search(r"\\|\\s*(hi|lo)\\b", result.stdout)
        if match is None:
            raise RuntimeError("GPIO 접점 상태를 읽지 못했습니다.")
        return match.group(1) == "lo"

    def close(self):
        pass


class UnconnectedOutput:
    # 출력 하드웨어가 없는 조립 단계에서는 GPIO를 점유하지 않는다.
    def __init__(self):
        self.value = False

    def off(self):
        self.value = False

    def close(self):
        pass
'''
    text = patch_one(text, 'from __future__ import annotations',
       'from __future__ import annotations\n\nimport re\nimport subprocess', 'gpio imports')
    if 'class PinctrlInput:' not in text:
        index = text.index('\n\n# GPIO를 열지 않고 센서')
        text = text[:index] + cls + text[index:]
    text = patch_one(text,
'''    if any(type(pin) is not int or not 0 <= pin <= 27 for pin in pins):
        raise ValueError("센서·조명·LED의 BCM 핀을 0~27로 설정하세요.")
    if len(set(pins)) != len(pins):
        raise ValueError("센서·조명·LED는 서로 다른 BCM 핀을 사용해야 합니다.")''',
'''    # 미연결 출력은 핀 번호 -1을 유지해 실제 GPIO를 건드리지 않는다.
    if not config.get("outputs_connected", True):
        lid = config.get("lid_pin", -1)
        if type(lid) is not int or not 0 <= lid <= 27:
            raise ValueError("개폐 센서의 BCM 핀을 0~27로 설정하세요.")
        if any(pin != -1 for pin in pins[1:]):
            raise ValueError("미연결 출력의 핀은 모두 -1이어야 합니다.")
        return
    if any(type(pin) is not int or not 0 <= pin <= 27 for pin in pins):
        raise ValueError("센서·조명·LED의 BCM 핀을 0~27로 설정하세요.")
    if len(set(pins)) != len(pins):
        raise ValueError("센서·조명·LED는 서로 다른 BCM 핀을 사용해야 합니다.")''', 'gpio validate')
    text = patch_one(text,
'''            self._sensor = DigitalInputDevice(
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
                self._devices.append(led)''',
'''            # 호환 모드에서도 실제 개폐 신호를 사용하고 상태 머신은 유지한다.
            if config.get("sensor_backend", "gpiozero") == "pinctrl":
                if not config.get("lid_pull_up", True):
                    raise ValueError("pinctrl 테스트 모드는 풀업 접점만 지원합니다.")
                self._sensor = PinctrlInput(config["lid_pin"])
            else:
                self._sensor = DigitalInputDevice(
                    config["lid_pin"], pull_up=config.get("lid_pull_up", True)
                )
            self._devices.append(self._sensor)
            active_high = config.get("output_active_high", True)
            connected = config.get("outputs_connected", True)
            self._lamp = (
                OutputDevice(config["lamp_pin"], active_high=active_high, initial_value=False)
                if connected else UnconnectedOutput()
            )
            self._devices.append(self._lamp)
            for color in self.COLORS:
                led = (
                    OutputDevice(config[f"{color}_pin"], active_high=active_high, initial_value=False)
                    if connected else UnconnectedOutput()
                )
                self._leds[color] = led
                self._devices.append(led)''', 'gpio board')
    return text


def set_line(section, key, value, txt):
    # TOML 기존 섹션만 수정하며 중복 키를 만들지 않는다.
    lines = txt.splitlines(keepends=True)
    header = '[' + section + ']'
    start = next((i for i, line in enumerate(lines) if line.strip() == header), None)
    if start is None:
        raise ValueError(f'설정 섹션 없음: {header}')
    stop = next((i for i in range(start + 1, len(lines)) if lines[i].startswith('[')), len(lines))
    pat = re.compile(r'^\s*' + re.escape(key) + r'\s*=')
    for i in range(start + 1, stop):
        if pat.match(lines[i]):
            lines[i] = f'{key} = {value}\n'
            return ''.join(lines)
    lines.insert(stop, f'{key} = {value}\n')
    return ''.join(lines)


def patch_config(txt):
    txt = re.sub(r'(?m)^pi_model\s*=.*$', 'pi_model = "Raspberry Pi 5 4GB"', txt, count=1)
    # 기존 실사용 큐와 분리한 실기기 검증용 저장 경로를 사용한다.
    txt = re.sub(r'(?m)^data_dir\s*=.*$', 'data_dir = "../../local_data/hardware_pi_imx219_validation"', txt, count=1)
    for section, key, val in [
        ('camera','preview_size','[768, 576]'),
        ('camera','still_size','[3280, 2464]'),
        ('camera','require_autofocus','false'),
        ('camera','tuning_file','"/home/pi/pi5_imx219_120d.json"'),
        ('gpio','lid_pin','17'),
        ('gpio','lid_pull_up','true'),
        ('gpio','lid_closed_when_active','true'),
        ('gpio','outputs_connected','false'),
        ('gpio','sensor_backend','"pinctrl"'),
        ('aws','enabled','false'),
    ]:
        txt = set_line(section,key,val,txt)
    for key in ('lamp_pin','white_pin','blue_pin','green_pin','yellow_pin','red_pin'):
        txt = set_line('gpio', key, '-1', txt)
    return txt


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path.home()/'smart_medikit'/'hardware'/'pi')
    args=parser.parse_args()
    root=args.root.resolve()
    paths=[root/'medikit_pi'/'camera.py',root/'medikit_pi'/'gpio.py',root/'config.toml']
    if not all(p.exists() for p in paths):
        raise SystemExit(f'먼저 GitHub 저장소 설치 및 config.toml 생성이 필요합니다: {root}')
    old=[p.read_text(encoding='utf-8') for p in paths]
    try:
        new=[patch_camera(old[0]),patch_gpio(old[1]),patch_config(old[2])]
        for index in (0,1):
            compile(new[index],str(paths[index]),'exec')
        import tomllib
        config=tomllib.loads(new[2])
        assert config['gpio']['lid_pin']==17
        assert config['aws']['enabled'] is False
        for p, before, after in zip(paths,old,new):
            if before==after:
                print('기존 수정 유지:',p)
                continue
            bak=p.with_suffix(p.suffix+'.before-imx219')
            if not bak.exists():
                shutil.copy2(p,bak)
            p.write_text(after,encoding='utf-8')
            print('수정:',p,'백업:',bak)
    except Exception as e:
        raise SystemExit(f'적용 중단(원본 파일은 보존됨): {e}')
    print('\nGitHub 원격 저장소는 변경되지 않았습니다.')
    print('주의: 이 모드는 AF 신뢰성을 확인하지 못하며, 미연결 조명/LED를 제어하지 않습니다.')

if __name__=='__main__':
    main()
