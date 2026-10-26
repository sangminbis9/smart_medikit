# Raspberry Pi 자동 촬영 시스템

`plan.md` 2·3절의 촬영 흐름을 구현한다. 사전 학습 **YOLOE-26n**은 상자·처방약 봉투·블리스터·시럽병·연고 튜브 등 포장 후보를 찾고, OpenCV는 새 등장 여부·흔들림·선명도를 확인한다. 한글이 인쇄된 국내 포장도 입력 대상이며 한글 OCR, 제품명 식별, 입출고·재고 확정은 AWS의 PaddleOCR+OpenCV 책임이다. 추가 학습은 하지 않는다. 포장 종류·촬영 환경별 검출률은 실물 영상으로 평가해야 한다.

요청에 따라 실제 Pi 제어 소스는 `Raspberry_Pi/`에 두었다. `../hardware/`의 PC 카메라 수동 미리보기는 별도로 유지한다. 기본 실기기 설치 대상은 **64비트 Raspberry Pi OS Bookworm, Python 3.11**이다. 영상 모드는 Python 3.11 이상을 지원한다. Pi 모델·GPIO·실제 조명/센서는 아직 미확정이며, 실제 Pi에서 성능과 배선 검증을 수행하지 않았다.

## 구성과 동작

| 모듈 | 책임 |
|---|---|
| `camera.py` | Picamera2 AF 메타데이터, 저해상도 미리보기/고해상도 전환, 제한시간·장치 복구, 영상 파일 입력 |
| `gpio.py` | 마그네틱 센서·조명·독립 색상 LED와 모의 입출력 |
| `detection.py` | 사전 학습 YOLOE-26n 모델 준비, CPU 추론, 최신 프레임 하나만 대기하는 작업자 |
| `vision.py` | 전체 화면 배경 비교, 후보 영역 변화·선명도·추적 연속성 |
| `controller.py` | 개폐 디바운스, 준비/포장/내부 촬영 상태 전이 |
| `outbox.py` | SQLite 영속 순번·세션, 원자적 JPEG 저장, 순차 업로드·폴링·재시도 |
| `transport.py` | 명시적으로 확인된 S3 완료 객체 및 인증된 HTTPS 상태 조회 |
| `config.py`, `__main__.py` | 설정 검증과 실기기/영상/모델 준비 명령 |

1. 열림을 디바운스한 뒤 조명·카메라를 켠다. 연속 영상 안정화와 AF를 확인하여 기준 배경을 잡고 흰 LED를 켠다.
2. 화면 전체를 YOLOE로 탐색한다. 준비 배경과 다른 후보가 약 1초 연속 검출되고 프레임·후보 영역이 안정적일 때 고해상도 촬영한다. 준비 전 포장을 보여줬다면 치웠다가 다시 제시한다. 안정된 빈 장면에서 배경을 갱신한다.
3. 사진 자체의 AF·선명도·미리보기 대비 움직임·개폐 상태까지 재확인하고 JPEG를 영속 저장한 뒤 파란 LED를 켠다. 세션당 포장 성공은 한 번이며 화질 실패는 성공으로 세지 않는다.
4. 닫힌 뒤 기본 0.75초 안정화와 화질 검사를 거쳐 내부를 촬영한다. 시도당 기본 3초 제한, 최대 두 번 추가 재시도한다. 실패해도 성공한 포장 사진은 보존한다. 다시 열면 이전 불완전 세션을 완결하고 새 세션을 시작한다.
5. 전송은 촬영과 다른 작업자에서 FIFO로 수행한다. 서버 종료 상태를 받기 전에는 다음 세션을 보내거나 사진을 삭제하지 않는다. 전원 중단 후 기존 식별자와 순번으로 복구한다.

파랑은 **로컬 촬영 성공**이다. 초록은 AWS `COMPLETED`, 노랑은 `REVIEW_REQUIRED`, 빨강은 촬영/서버 오류다. 서버 LED는 닫힌 상태에서 표시하여 현재 촬영 안내를 가리지 않는다. 기본 AWS 미연결 상태에서는 초록/노랑을 만들지 않는다.

## Pi 설치와 최초 실행

프로젝트 루트에서 다음 순서로 준비한다. 아래 Linux/Pi 명령은 제공한 실행 경로이며 이 Windows 개발 환경에서 apt·GPIO·Picamera2 실기기 실행을 검증한 것은 아니다.

```bash
sudo apt update
sudo apt install python3-picamera2 python3-opencv python3-gpiozero python3-lgpio python3-venv git
cd Raspberry_Pi
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -c constraints-pi-bookworm.txt
python -c "import numpy, cv2; from picamera2 import Picamera2; print(numpy.__version__, cv2.__version__)"
cp config.example.toml config.toml
python -m medikit_pi --config config.toml check-config
```

Picamera2/libcamera는 OS 패키지를 사용한다. Pi에서 venv를 `--system-site-packages`로 만들어야 해당 패키지를 읽을 수 있다. Bookworm에서는 apt의 simplejpeg와 NumPy 2.x ABI가 충돌할 수 있어 제공한 제약 파일로 NumPy 1.x·OpenCV 4.11을 함께 고정한다. Pi에서 이후 의존성을 설치할 때도 같은 `-c constraints-pi-bookworm.txt`를 적용한다. 다른 OS 버전에서는 apt 패키지 ABI에 맞춰 검증한 뒤 제약을 조정한다. `rpicam-hello --timeout 3000`으로 카메라가 연결되는지 먼저 확인한다. Pi 모델에 맞는 Camera Module 3 연결 케이블·전원·냉각을 사용한다.

`config.toml`의 `pi_model`을 기록하고 `[gpio]`의 **모든 -1을 실제 BCM 번호**로 바꾼다. 센서 pull-up·닫힘 극성과 출력 active-high 설정을 배선에 맞춘다. 핀 중복/미설정이면 실기기 실행을 거부한다. 이 구현은 흰/파랑/초록/노랑/빨강의 개별 디지털 출력(또는 외부 드라이버 채널)을 사용한다. RGBW LED만 연결하는 경우 회로에 맞는 채널 매핑이 필요하다. LED 저항과 조명용 트랜지스터/드라이버를 사용하고 조명을 GPIO에 직접 전원 연결하지 않는다.

인터넷이 있는 설치 단계에서 모델을 한 번 준비한다.

```bash
python -m medikit_pi --config config.toml prepare-model
python -m medikit_pi --config config.toml run
```

`prepare-model`은 공식 `yoloe-26n-seg.pt`와 텍스트 인코더를 내려받아 설정의 포장 설명을 임베딩으로 저장한다. **학습 명령이 아니다.** 첫 실행에는 CLIP 의존성 설치와 약 254 MB 텍스트 인코더 다운로드가 추가될 수 있다. 모델을 준비한 뒤에는 `run`이 로컬 `.pt`만 읽으며 네트워크에서 모델을 자동 설치하지 않는다. 프롬프트를 바꿨으면 모델을 다시 준비한다. 메모리가 부족한 Pi는 같은 설정으로 다른 64비트 컴퓨터에서 모델을 준비하고 `models/`의 결과 `.pt`를 복사한다. 가중치·설정·사진·DB는 Git에서 제외된다. Ultralytics 라이선스 조건은 배포 전에 확인한다.

`Ctrl+C`로 종료하면 조명·카메라·GPIO를 정리하고 현재 세션의 확보된 사진을 보존한다. 비정상 종료된 촬영은 다음 시작에서 복구한다. 카메라 오류는 기본 2초 간격으로 재시작하고, 내부 촬영은 실패 한도 후 큐에 남긴다. 디스크 쓰기 실패는 성공 표시를 하지 않는다. 동기화가 꺼져 있거나 오랫동안 끊긴 경우 로컬 저장 공간을 운영자가 확인해야 한다.

## CPU·화질 설정

| 설정 | 기본값과 의미 |
|---|---|
| `camera.preview_size`, `still_size` | 768×432 미리보기, 4608×2592 정지 사진; 동일 화면 비율 필요 |
| `camera.fps`, `timeout_seconds` | 15 fps 목표, 카메라 작업 3초 제한 |
| `detector.image_size`, `interval_seconds`, `threads` | 입력 320, 최소 추론 간격 0.4초, PyTorch CPU 스레드 2개 |
| `detector.max_age_seconds` | 1.2초보다 오래된 원본 프레임의 검출은 사용하지 않음 |
| `vision.analysis_width` | 전체 화면을 폭 320으로 줄여 차이 계산 |
| `vision.stable_seconds` | 같은 후보가 약 1초 관측되어야 촬영 |
| `vision.motion_threshold`, `minimum_change`, `pixel_threshold` | 연속 흔들림 및 준비 배경 대비 신규 후보 변화 기준 |
| `vision.sharpness_threshold`, `still_sharpness_threshold` | 미리보기/사진의 Laplacian 분산 기준; 실제 조명과 거리로 조정 |
| `vision.close_settle_seconds`, `close_attempt_seconds`, `close_retries` | 0.75초 안정화, 시도 창 3초, 추가 2회 |

하드웨어 실행은 추론을 별도 스레드에서 처리하고 오래된 대기 프레임을 덮어쓴다. 실제 추론이 `max_age_seconds`보다 느리면 안전하게 촬영하지 않으므로 지연을 측정해 입력 크기·주기·유효시간을 조정한다. 고해상도는 촬영 시에만 전환하고 닫힘 처리가 끝나면 카메라·조명을 정지한다. AF가 실패하거나 제한시간을 넘으면 사진을 성공 처리하지 않는다.

전역 밝기 변화, 뚜껑 이동, 투명/반사 포장, 여러 포장의 교차·가림은 오탐/미탐 요인이다. YOLOE의 영어 프롬프트는 포장 형태에 대한 힌트이며 한국어 인쇄를 읽거나 국내 모든 포장을 알아본다는 보장은 없다. 실제 영상을 보고 임계값을 결정하며 재고를 Pi에서 추정 확정하지 않는다.

## 하드웨어 없는 영상 테스트

### PC 웹캠 실시간 탐지 전용 모드

웹캠 또는 DroidCam의 현재 영상과 YOLOE 후보 상자·영문 포장 분류·신뢰도를 확인할 수 있다. **자동 촬영·사진 저장·GPIO·세션 큐·AWS 전송은 실행하지 않는다.** 핀 설정 없이 `config.example.toml`을 그대로 사용할 수 있다. 기존 가상환경과 준비된 모델이 있는 현재 PC에서는 저장소 루트 PowerShell에서 실행한다.

```powershell
cd Raspberry_Pi
.\.venv\Scripts\python.exe -m medikit_pi --config config.example.toml webcam --camera 0
```

다른 PC에서는 Python 3.11 이상으로 가상환경을 만들고 `requirements.txt`를 설치한 뒤, 같은 설정으로 `prepare-model`을 한 번 실행한다. Picamera2·GPIO 패키지는 필요 없다. 준비된 모델이 없다면 다음 명령을 먼저 실행한다.

```powershell
.\.venv\Scripts\python.exe -m medikit_pi --config config.example.toml prepare-model
```

창의 **왼쪽은 현재 카메라 영상**, **오른쪽은 실제 검출에 사용한 프레임과 후보 상자**다. 비동기 추론이 늦게 끝난 상자를 다른 현재 프레임에 겹쳐 표시하지 않는다. 오른쪽이 검으면 아직 결과가 없거나 `detector.max_age_seconds`를 넘긴 상태다. 기본 신뢰도는 0.25, 추론 요청 간격은 0.4초이며 `--confidence 0.15 --interval 0.1`처럼 이번 실행만 조정할 수 있다. 낮은 신뢰도는 오탐을 늘릴 수 있다. CPU가 느린 경우 설정의 입력 크기·결과 유효시간을 조정한다.

카메라가 다르면 `--camera 1` 또는 `2`를 사용한다. Windows 연결 실패 시 `--backend msmf`를 추가하고, DroidCam은 PC 클라이언트에서 영상이 나오는지 먼저 확인한다. `Q`, `Esc`, 창 닫기 또는 `Ctrl+C`로 종료한다. 이 화면은 포장 후보만 보여주며 한글 약품명이나 제품을 식별하는 화면이 아니다. 실제 카메라·GUI 표시는 해당 PC에서 확인해야 한다.

2026-10-26 검증: 탐지 전용 모드 테스트 10개 추가 후 전체 72개와 하위 검증 3개 통과. 실제 준비 모델의 CPU 추론 결과를 두 화면으로 합성하는 경로도 확인했다. 실제 웹캠과 화면 창은 이번 검증에서 열지 않았다.

### 녹화 영상으로 촬영 알고리즘 검증

Windows/Linux에서 Python 3.11 이상 가상환경에 `requirements-dev.txt`를 설치하고 모델을 준비한다. Picamera2와 GPIO 패키지는 영상 모드에 필요 없다. 실제 환자 정보가 없는 포장 영상만 사용한다.

영상과 같은 시간축의 개폐 기록을 `../local_data/lid-events.json`에 저장한다. `at`는 영상 시작부터 초, `open`은 열린 상태다. 마지막 닫힘 이후 최소 2~3초 이상의 내부 영상(실패 시도 검증은 10초 이상)을 포함한다.

```json
[
  {"at": 0.0, "open": false},
  {"at": 0.5, "open": true},
  {"at": 6.0, "open": false}
]
```

`Raspberry_Pi/`에서 실행한다.

```bash
python -m medikit_pi --config config.toml video --input ../local_data/test.mp4 --lid-events ../local_data/lid-events.json --data-dir ../local_data/video-run --report ../local_data/video-report.json
```

`--show`를 붙이면 GUI가 있는 환경에서 미리보기를 띄운다. PowerShell에서는 `python` 대신 `.\.venv\Scripts\python.exe`를 사용할 수 있다. 영상 모드는 **실제 YOLOE와 OpenCV**를 실행한다. 센서는 JSON, AF 성공은 모의값이며 정지 사진은 영상 원본 프레임이다. 영상 프레임률의 시간축으로 1초 안정화를 계산하므로 느린 PC 추론도 재생 판단 시간을 왜곡하지 않는다. 실제 Pi 처리 속도·자동초점·12 MP 화질 검증을 대신하지 않는다.

테스트 결과는 지정한 데이터 폴더의 세션별 `package.jpg`, `interior.jpg`, `queue.sqlite3`와 보고서에 남는다. 성공하지 않은 사진 파일은 없다. 영상 모드는 AWS 설정과 관계없이 전송하지 않으며 실제 Pi 데이터 폴더 사용을 거부한다. EOF를 닫힘으로 가정하지 않고 미완료 세션을 그대로 보존한다. 자동 단위 테스트의 검출기는 명시적인 모의 객체이고 실행 프로그램의 YOLO를 대체하는 fallback은 없다.

## AWS 연결 경계

현재 저장소에는 `backend/` 구현이 없으며 `docs/CONTRACTS.md`는 초안이다. 기본값 `aws.enabled=false`로 실행해도 모든 촬영과 영속 큐가 동작한다. 서버가 없는 상태를 성공으로 표시하지 않으며 임의 API를 호출하지 않는다.

백엔드 담당자가 [계약 문서 2.1절](../docs/CONTRACTS.md#21-pi-구현-상태와-배포-전-확정-항목-2026-10-26)의 완료 객체, 인증, 상태 응답을 확정한 뒤 다음 값을 **실제 배포 값으로** 설정한다.

```toml
[aws]
enabled = true
contract_confirmed = true
poll_seconds = 2.5
bucket = "실제-비공개-버킷"
region = "실제-리전"
prefix = "합의한/제한경로"
manifest_name = "합의한-완료파일.json"
status_url_template = "https://실제-API-호스트/합의한-경로/{event_id}"
auth_mode = "sigv4"
```

위 값은 설명용이며 사용 가능한 서버 주소가 아니다. 한 세션의 JPEG를 먼저 PUT하고 마지막 JSON 객체에서만 Lambda가 실행되어야 한다. API는 `processing_status`의 기존 enum을 반환하고, 서버도 중복·순서 검증과 실패 이벤트 재처리를 제공해야 한다. `POST /capture-events`의 미구현 요청/응답을 만들지 않았다. prefix와 상태 URL은 처음 전송 전에 확정하고, 미완료 큐가 있는 동안 바꾸지 않는다.

Boto3의 외부 `credential_process` 등 갱신 가능한 공급자로 **단기 세션 토큰이 포함된 자격증명**을 제공한다. 장기 access key/secret을 Pi나 소스에 내장하지 않는다. API가 bearer 방식이면 `auth_mode="bearer"`와 외부 `bearer_token_file` 경로를 추가한다. 토큰·AWS 응답 본문·사진·OCR 원문은 로그에 기록하지 않는다. S3/IAM/API 접근제어와 비용 정책은 별도 팀 배포 작업이며 이 프로그램은 원격 자원이나 IAM 설정을 변경하지 않는다.

`COMPLETED`/`REVIEW_REQUIRED` 확인 후 정상 종료 상태를 먼저 저장하고 Pi 사진을 삭제한다. `FAILED`/통신 실패는 동일 ID를 보존해 최대 60초 백오프로 재시도한다. AWS가 영구 실패하면 해당 선두 세션에서 전송이 대기하며 촬영은 계속 큐에 쌓인다. `REVIEW_REQUIRED`는 다음 세션 전송을 허용한다. 내부 사진이 실패한 경우 `POSITION_UNCERTAIN` 및 기존 위치 유지, 포장 미촬영 시 내부 전후 비교는 서버가 수행해야 한다.

포장 촬영 성공은 실제 반출·복용을 입증하지 않는다. 처방전 상세사진 보완은 기존 웹 흐름을 사용하며, 이름·환자번호 등 개인정보가 있는 영역은 촬영에서 제외하거나 가린 후 사용한다. 이 Pi 모듈은 개인정보 자동 OCR 마스킹을 제공하지 않는다. 로컬 큐는 기본 전송 완료까지 사진을 보관하므로 장치 저장소의 물리 접근도 제한한다.

## 검증 명령과 남은 실험

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
python -m ruff check medikit_pi tests
python -m ruff format --check medikit_pi tests
python -m medikit_pi --help
python -m medikit_pi --config config.example.toml check-config
```

자동 검증은 합성 영상·모의 GPIO/카메라/서버를 사용하여 디바운스, 세션당 1회, AF·선명도 실패, 내부 2회 추가 재시도, 재열림, 재시작 큐 복구, FIFO·중복 ID 유지, 최종 확인 후 삭제를 검사한다. 실제 Pi CPU 지연·메모리, Camera Module 3 Wide AF, 배선 극성, 국내 포장별 검출률, 실제 S3/Lambda/API 연결은 하드웨어와 서버가 준비된 뒤 확인해야 한다.

## 공식 근거

2026-10-26 개발 환경 검증: Python 3.12.14, OpenCV 5.0.0, Ultralytics 8.4.189, PyTorch 2.14.1에서 자동 테스트 62개 및 하위 검증 3개, 기존 PC 카메라 테스트 11개를 통과했다. Ruff 검사·포맷, CLI 도움말·설정 검사, `pip check`를 통과했고 Pi 의존성 제약은 Windows에서 설치 없이 의존성 해석을 확인했다. 실제 `prepare-model` 명령으로 사전 학습 모델의 프롬프트 저장·재로딩·CPU 추론을 확인했고, 실제 YOLOE를 사용하는 `video` 명령으로 합성 AVI 80프레임의 준비→닫힘→내부 촬영→로컬 큐 저장을 확인했다. 합성 영상은 국내 약품 검출 정확도 평가 자료가 아니다. Pi·AWS 실연동은 실행하지 않았다.

- [Ultralytics YOLOE 모델·텍스트 프롬프트·YOLOE-26n](https://docs.ultralytics.com/models/yoloe/)
- [Raspberry Pi Picamera2 매뉴얼](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf)
- [Bookworm NumPy·simplejpeg ABI 호환성](https://github.com/raspberrypi/picamera2/issues/1211)
- [Boto3 외부 자격증명 공급자](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html)
- [S3 이벤트 키 필터](https://docs.aws.amazon.com/AmazonS3/latest/userguide/notification-how-to-filtering.html)
