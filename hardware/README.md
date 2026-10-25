# PC 카메라 입력 실험

Raspberry Pi 카메라가 도착하기 전에 iPhone의 DroidCam 영상을 OpenCV로 읽어 보는 첫 단계다. `camera_preview.py`는 실시간 미리보기와 **수동 프레임 저장**을 제공한다. 물체 탐지 모델, 자동 촬영 판단, Pi 카메라·센서·LED, AWS 전송은 아직 연결하지 않았다. 이 코드는 `plan.md`의 최종 촬영 상태 머신을 대신하지 않는다.

## 환경과 설치

- 현재 개발 환경: Windows, Python 3.14.0, OpenCV 5.0.0 (`opencv-python` 패키지 5.0.0.93), NumPy 2.5.4.
- iPhone의 DroidCam 앱과 Windows의 DroidCam Client를 켜고, PC 클라이언트에 영상이 보이는 상태로 둔다.
- OpenCV는 클라이언트가 제공하는 가상 웹캠을 읽는다. 아이폰의 초점·노출·사진 셔터를 제어하는 코드는 아니다.
- `requirements.txt`는 이 **PC 실험 환경**의 버전 목록이다. Pi 환경의 설치 방법은 실기기 연결 시 별도로 확인한다.

저장소 루트의 PowerShell에서 실행한다. 앞서 `.venv`를 만들었다면 생성 명령은 다시 실행하지 않아도 된다.

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r hardware/requirements.txt
```

가상환경은 별도 활성화 없이 위와 같이 해당 Python 경로를 직접 사용한다. `.venv/`는 Git에서 제외된다. OpenCV가 카메라 입력·화면 표시·JPEG 인코딩을 담당하며 NumPy는 프레임 배열을 다룬다. 기존 웹 의존성만으로는 이 기능을 실행할 수 없어 Python 설치 목록을 추가했다.

## 내일 실행할 미리보기

명령행 도움말과 의존성은 확인했다. 아래 명령의 **실제 카메라 영상과 창 동작은 사용자가 다음 실험에서 확인해야 한다.**

```powershell
.\.venv\Scripts\python.exe hardware/camera_preview.py
```

기본 카메라 번호는 `0`이다. `smart_medikit` 창에 아이폰 대신 노트북 카메라가 보이거나 열기에 실패하면 종료한 뒤 번호를 바꾼다. 카메라 번호는 PC마다 다르며 프로그램이 DroidCam을 이름으로 자동 선택하지는 않는다.

```powershell
.\.venv\Scripts\python.exe hardware/camera_preview.py --camera 1
```

필요하면 번호를 `2` 등으로 바꾸어 확인한다. 올바른 번호인데 Windows의 기본 입력 방식 `dshow`에서 영상이 안 나오면 다음처럼 `msmf`를 시험할 수 있다.

```powershell
.\.venv\Scripts\python.exe hardware/camera_preview.py --camera 1 --backend msmf
```

| 조작 | 동작 |
|---|---|
| 영상 창을 선택하고 `S` | 현재 프레임 한 장을 JPEG로 저장 |
| 영상 창을 선택하고 `Q` 또는 `Esc` | 미리보기 종료 |
| 창의 닫기 버튼 | 미리보기 종료 |
| 터미널에서 `Ctrl+C` | 실행 중단 및 자원 정리 |

키 입력이 반응하지 않으면 영상 창에 포커스를 두고 영문 입력 상태에서 시도한다. 저장 결과와 오류는 PowerShell에 표시된다. 연결이 끊겨 새 프레임을 받지 못하면 종료하므로, DroidCam을 다시 연결한 뒤 명령을 재실행한다. 저장에 실패한 경우에는 오류를 알리고 미리보기를 유지한다.

## 저장 사진과 프로젝트 경계

- 저장 위치는 실행한 폴더와 관계없이 저장소의 `local_data/camera_captures/`다. 이 폴더는 기존 `.gitignore`에 의해 Git에서 제외된다.
- `S`를 누르기 전에는 사진 파일이나 저장 폴더를 만들지 않는다. 별도의 영상 녹화나 서버 업로드도 하지 않는다.
- 파일명은 시간과 임의 식별자를 포함해 기존 사진을 덮어쓰지 않는다. 한국어 Windows 경로를 지원하도록 JPEG 인코딩 후 Python으로 파일을 저장한다.
- 저장 사진은 **PC로 전달된 영상 프레임**이다. 아이폰 기본 카메라의 고해상도 원본 사진과 다르며, 클라이언트에서 적용한 회전·확대 등의 영향도 받을 수 있다.
- 저장 성공은 좋은 화질이나 정확한 약품 식별을 의미하지 않는다. 이번 수동 저장은 입력 실험이며 세션·입출고 이벤트를 만들지 않는다.
- 개인정보가 없는 포장이나 합성 자료로만 시험한다. 실제 처방전이나 환자 정보는 사용하지 않는다. 테스트 사진은 필요한 확인이 끝나면 사용자가 삭제한다.

나중에 Pi로 옮길 때 카메라 입력·실제 정지 사진 촬영은 Picamera2로 연결하고, 별도로 개발할 물체 탐지·화질 판단 로직은 이미지 프레임을 받아 재사용하도록 구성한다.

## 검증

실제 카메라를 열지 않는 테스트는 표준 `unittest`를 사용한다. 카메라·화면 함수는 모의 객체로 대체하고, 파일 저장은 합성 이미지만 사용한다.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s hardware/tests -v
.\.venv\Scripts\python.exe -m py_compile hardware/camera_preview.py hardware/tests/test_camera_preview.py
.\.venv\Scripts\python.exe hardware/camera_preview.py --help
.\.venv\Scripts\python.exe -m pip check
```

테스트 항목은 카메라 선택·잘못된 번호·열기 실패·프레임 단절·빈 프레임·저장 실패·종료 자원 해제·한국어 경로의 JPEG 저장/재읽기다. 실기기에서는 영상 출처, `S` 저장, 저장 파일 열기, `Q`/`Esc`/창 닫기, Wi-Fi 단절 안내를 확인해야 한다. 인식 정확도나 Pi 처리 속도는 아직 검증 대상이 아니다.

2026-10-26 확인 결과: 자동 테스트 11개, Python 문법 검사, `--help`, `pip check`가 통과했다. 제한된 실행 환경에서는 임시 파일 경로를 Git 제외 폴더인 `local_data/test_tmp/`로 지정해 파일 저장 테스트를 실행했다. 실제 카메라와 GUI는 실행하지 않았다.

## 참고

- [OpenCV 카메라 입력·표시·저장 예제](https://docs.opencv.org/4.x/dd/d43/tutorial_py_video_display.html)
- [DroidCam Windows 연결 안내](https://droidcam.app/windows/)
- [OpenCV Python 패키지](https://pypi.org/project/opencv-python/)
