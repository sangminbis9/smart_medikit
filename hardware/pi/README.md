# Raspberry Pi 설치·AWS 연결 — hardware/pi 실행본

최근 단계별 촬영·입출고 연결의 설치 대상은 **이 폴더(`hardware/pi`)**다. `Raspberry_Pi/`와 패키지명이 같지만 별도 코드이므로 명령은 반드시 이 폴더에서 실행한다. 예제에는 배포된 학교 계정·서울 리전 주소를 채웠으며 `aws.enabled=false`만 유지한다. 실제 설정은 Git에서 제외된 `config.toml`에 저장한다. 아래 절차는 설치 준비용이며 실제 Pi에서 실행 완료한 기록이 아니다.

## 내부 사진 전후 비교 실험

2026-11-10 추가: 실기기 `aws.staged_capture=true` 모드는 포장 저장 직후 OCR 전송을 시작하고 내부 저장 후 별도 로컬 분석을 시작한다. OCR 전송 실패나 지연은 로컬 판단을 막지 않는다. 시작할 때 뚜껑이 닫혀 있으면 기준 사진을 먼저 확보한다. 자동 모드는 최상단 새 후보 하나를 방향 확인 없이 반입 추정으로 처리한다. 아래 CLI는 기존 보수적 실험 방식이다. 기준 사진이 없으면 첫 동작을 임의 반입으로 만들지 않는다.

실제 연결에는 `prefix=medkit-sessions`, `ocr_input_prefix=ocr-input`, `manifest_name=closed-ready.json`, 배포된 상태 URL과 단기 SigV4 인증이 필요하다. AWS는 예제에서 기본 비활성화다. 마지막 내부 기준 JPEG 한 장은 로컬 SQLite에 보관하며 완료된 세션 사진은 기존 정리 정책을 따른다. AWS S3부터의 합성 이미지 통합 검증은 완료했지만 실제 Pi 촬영·배선·로그인은 검증하지 않았다. [종단 점검](../../docs/PIPELINE_AUDIT.md)

`medikit_pi.movement`는 같은 구도에서 닫힌 보관함의 전후 사진을 각각 YOLOE로 탐지해 위치가 겹치는 후보를 대응시키고, 한 후보만 늘거나 줄면 반입 또는 반출 **추정 후보**를 만든다. 반입한 약은 최상단에 놓는 운영 조건을 전제로 하며, 새 포장에 아래 포장이 가려지거나 다시 드러날 가능성이 보이면 방향을 보류한다. 여러 변화·중복 대응·장면 전체 변화·사진 오류도 방향을 제안하지 않는다. 모든 결과는 `REVIEW_REQUIRED`이며 약품 식별, 실제 이동 및 복용을 확정하지 않는다. 포장 가림·위치 이동·탐지 실패가 입출고처럼 보일 수 있어 **실물 데이터로 검증하기 전 재고·알림·24시간 처리에 연결하지 않는다.**

위 설명 중 보수적 비교는 아래 독립 CLI에 해당한다. `run`의 단계별 자동 모드에는 촬영·기준 사진·비교 작업자가 연결되어 있다. 아래 CLI는 별도로 준비한 두 사진만 비교하며 서버로 보내지 않는다. 출력 좌표도 약품 ID가 아니다.

```bash
cd hardware/pi
python -m medikit_pi.movement --config config.toml --before before.jpg --after after.jpg
```

[plan.md](../../plan.md) 2·3절의 촬영 흐름을 구현한다. 사전 학습 **YOLOE-26n**은 상자·처방약 봉투·블리스터·시럽병·연고 튜브 등 포장 후보를 찾고, OpenCV는 새 등장 여부·흔들림·선명도를 확인한다. 한글이 인쇄된 국내 포장도 입력 대상이며 한글 OCR, 제품명 식별, 입출고·재고 확정은 AWS의 PaddleOCR+OpenCV 책임이다. 추가 학습은 하지 않는다. 포장 종류·촬영 환경별 검출률은 실물 영상으로 평가해야 한다.

사용자 요청으로 커밋 `17b14bc`의 [`Raspberry_Pi/`](../../Raspberry_Pi/README.md)에서 추적 중인 파일을 복사했다. 원본은 삭제하거나 수정하지 않았으며, 두 위치의 변경은 자동 동기화되지 않는다. 이번 복사에서는 실행 코드·테스트를 그대로 두고 이 안내와 예제 설정의 경로만 조정했다. 실제 설정·가상환경·모델·사진·대기열은 복사하지 않았다.

기존 [PC 카메라 미리보기](../README.md)는 그대로 사용하며, 이 복사본의 기능을 호출하거나 AWS에 업로드하지 않는다. 복사본도 기본 `aws.enabled=false`이며 로컬 데이터는 원본과 분리된 `local_data/hardware_pi/`에 저장한다. 단, 복사본의 Pi 실행 모드는 전송을 꺼도 큐를 쌓으므로 이후 AWS를 활성화하면 그 큐도 전송 대상이다.

기본 실기기 설치 대상은 **64비트 Raspberry Pi OS Bookworm, Python 3.11**이다. 영상 모드는 Python 3.11 이상을 지원한다. Pi 모델·GPIO·실제 조명/센서는 아직 미확정이며, 실제 Pi에서 성능과 배선 검증을 수행하지 않았다. 아래 2026-10-26 검증 기록은 원본의 기록이며 실기기 통합 성공을 뜻하지 않는다.

S3 전송을 읽어 볼 때는 [`transport.py`](medikit_pi/transport.py)의 `S3Transport.upload()`와 `status()`, [`outbox.py`](medikit_pi/outbox.py)의 `Outbox`와 `SyncWorker.step()`, [`__main__.py`](medikit_pi/__main__.py)의 `run_hardware()` 순서로 확인한다. 버킷·리전·상태 URL은 [`config.example.toml`](config.example.toml)의 `[aws]`에 배포값이 입력되어 있고 전송은 기본적으로 꺼져 있다.

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

### 1. OS·카메라·Python 환경

지원 기준은 **64비트 Raspberry Pi OS Bookworm / Python 3.11 / Picamera2 카메라**다. 우선 `uname -m`이 `aarch64`인지, `python3 --version`을 확인한다. 실제 Pi 모델은 아직 지정하지 않았다. Camera Module 3 계열 AF 카메라를 전제로 하며 USB 카메라는 `run`의 Picamera2 입력을 대신하지 않는다.

새 Pi의 사용자 홈에서 다음 순서로 준비한다. 저장소를 이미 받았다면 clone을 반복하지 말고 기존 저장소의 `hardware/pi`로 이동한다. 아래 Linux/Pi 명령은 제공한 실행 경로이며 이 Windows 개발 환경에서 apt·GPIO·Picamera2 실기기 실행을 검증한 것은 아니다.

```bash
sudo apt update
sudo apt install python3-picamera2 python3-opencv python3-gpiozero python3-lgpio python3-venv git curl unzip
git clone https://github.com/sangminbis9/smart_medikit.git
cd smart_medikit
cd hardware/pi
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -c constraints-pi-bookworm.txt
python -c "import numpy, cv2; from picamera2 import Picamera2; print(numpy.__version__, cv2.__version__)"
# 기존 설정이 있으면 덮어쓰지 않는다.
test -f config.toml || cp config.example.toml config.toml
python -m medikit_pi --config config.toml check-config
```

Picamera2/libcamera는 OS 패키지를 사용한다. Pi에서 venv를 `--system-site-packages`로 만들어야 해당 패키지를 읽을 수 있다. Bookworm에서는 apt의 simplejpeg와 NumPy 2.x ABI가 충돌할 수 있어 제공한 제약 파일로 NumPy 1.x·OpenCV 4.11을 함께 고정한다. Pi에서 이후 의존성을 설치할 때도 같은 `-c constraints-pi-bookworm.txt`를 적용한다. 다른 OS 버전에서는 apt 패키지 ABI에 맞춰 검증한 뒤 제약을 조정한다. `rpicam-hello --timeout 3000`으로 카메라가 연결되는지 먼저 확인한다. Pi 모델에 맞는 Camera Module 3 연결 케이블·전원·냉각을 사용한다.

`config.toml`의 `pi_model`을 기록하고 `[gpio]`의 **모든 -1을 실제 BCM 번호**로 바꾼다. 센서 pull-up·닫힘 극성과 출력 active-high 설정을 배선에 맞춘다. 핀 중복/미설정이면 실기기 실행을 거부한다. 이 구현은 흰/파랑/초록/노랑/빨강의 개별 디지털 출력(또는 외부 드라이버 채널)을 사용한다. RGBW LED만 연결하는 경우 회로에 맞는 채널 매핑이 필요하다. LED 저항과 조명용 트랜지스터/드라이버를 사용하고 조명을 GPIO에 직접 전원 연결하지 않는다.

### 2. 사용자가 채울 값과 실물 확인

| 항목 | 설정·확인 내용 |
|---|---|
| `pi_model` | 사용 중인 Pi 모델·RAM을 기록한다. `unconfirmed`를 실제 값으로 바꾼다. |
| `gpio.lid_pin` | 자석 센서의 **BCM** 번호. 커넥터의 물리 핀 번호와 구분한다. |
| `gpio.lamp_pin` | 촬영 조명 드라이버 제어 핀. GPIO에 5V를 넣지 않는다. |
| `white_pin`·`blue_pin`·`green_pin`·`yellow_pin`·`red_pin` | 개별 LED 출력 5개. 핀은 모두 서로 달라야 한다. 실제 LED 구성과 다르면 코드·회로 매핑이 필요하다. |
| `lid_pull_up`·`lid_closed_when_active` | 닫힘을 닫힘으로 읽는지 확인한다. 기본 pull-up에서는 GND로 연결될 때 active다. |
| `output_active_high` | 드라이버가 HIGH에서 켜지는지 확인한다. 모든 출력에 공통 적용된다. |
| 카메라·조명 | 고정된 구도에서 뚜껑을 닫아도 내부 전체가 보이는지, 최상단 약의 가림·반사·초점 상태를 확인한다. |
| 전원·냉각·시간·인터넷 | Pi 규격 전원, 발열, 여유 저장 공간, NTP 시간 동기화, 외부 HTTPS 연결을 확인한다. |

핀 번호를 임의의 예제로 채우지 않았다. 배선을 먼저 결정한 뒤 `nano config.toml`로 수정한다. `check-config`는 형식만 검사하며 실제 GPIO를 움직이지 않는다.

### 3. 탐지 모델 준비

인터넷이 있는 설치 단계에서 모델을 한 번 준비한다.

```bash
python -m medikit_pi --config config.toml prepare-model
python -m medikit_pi --config config.toml check-config --hardware
```

`prepare-model`은 공식 `yoloe-26n-seg.pt`와 텍스트 인코더를 내려받아 설정의 포장 설명을 임베딩으로 저장한다. **학습 명령이 아니다.** 첫 실행에는 CLIP 의존성 설치와 약 254 MB 텍스트 인코더 다운로드가 추가될 수 있다. 모델을 준비한 뒤에는 `run`이 로컬 `.pt`만 읽으며 네트워크에서 모델을 자동 설치하지 않는다. 프롬프트를 바꿨으면 모델을 다시 준비한다. 메모리가 부족한 Pi는 같은 설정으로 다른 64비트 컴퓨터에서 모델을 준비하고 `models/`의 결과 `.pt`를 복사한다. 가중치·설정·사진·DB는 Git에서 제외된다. Ultralytics 라이선스 조건은 배포 전에 확인한다.

`check-config --hardware`는 GPIO 번호·중복, `pi_model`, 모델 파일 존재까지 확인한다. 카메라 작동·모델 로딩·센서 극성·실제 배선의 성공을 뜻하지 않는다. `rpicam-hello --timeout 3000`으로 카메라를 따로 확인한다.

### 4. AWS 단기 로그인 (Pi에서 실행)

최신 **AWS CLI v2**를 [AWS 공식 Linux ARM 설치 안내](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)에 따라 설치하고 `aws --version`을 확인한다. 배포 주소는 예제에 이미 있다. 장기 access key/secret을 입력하는 `aws configure` 방식은 사용하지 않는다.

[AWS 공식 콘솔 로그인 안내](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html)의 `aws login --remote`와 `credential_process`를 사용한다. CLI는 2.32.0 이상이어야 하며 학교 계정에 `SignInLocalDevelopmentAccess`에 해당하는 로그인 권한이 필요하다. 이 권한이 실제 허용되는지는 아직 미검증이고, 아래 명령은 IAM 정책을 추가하지 않는다.

```bash
aws login --remote --profile medkit-signin --region ap-northeast-2
aws configure set region ap-northeast-2 --profile medkit-pi
aws configure set credential_process 'aws configure export-credentials --profile medkit-signin --format process' --profile medkit-pi
export AWS_PROFILE=medkit-pi
export AWS_EC2_METADATA_DISABLED=true
```

Pi 터미널에 나온 로그인 주소를 PC에서 열어 **학교 계정으로 MFA까지** 완료한다. 표시되는 인증 코드는 Pi 터미널에 직접 입력하고 채팅·문서에는 남기지 않는다. 프로필 설정과 단기 로그인 캐시는 해당 Linux 사용자 홈의 `.aws`에 저장되므로 Pi 앱도 **같은 사용자**로 실행한다. `sudo python`으로 실행하지 않는다. 기존 `AWS_ACCESS_KEY_ID`·`AWS_SECRET_ACCESS_KEY`·`AWS_SESSION_TOKEN` 환경변수가 있으면 프로필보다 우선할 수 있으므로 이 앱 실행용 셸에서는 제거한다. `export-credentials`를 단독 실행하거나 결과를 로그에 복사하지 않는다.

로그인 세션은 최대 12시간 범위에서 갱신되고 이후 재로그인이 필요하다. 시연 전 같은 `aws login` 명령을 실행한다. 권한 오류가 나면 학교 관리자에게 로그인 허용과 아래 작업 권한을 확인받는다. 기존 Lambda 실행 역할을 Pi에 그대로 연결하거나 콘솔 내부 세션을 추출하는 방식은 사용하지 않는다. 상시 무인 운용용 장치 인증은 이번 대화형 설치 범위 밖이다.

| Pi에 필요한 권한 | 대상 |
|---|---|
| `s3:PutObject`, `s3:GetObject` | `arn:aws:s3:::inha-capstone-02-s3/ocr-input/*`, `arn:aws:s3:::inha-capstone-02-s3/medkit-sessions/*` (재전송 시 HeadObject 포함) |
| `execute-api:Invoke` | `arn:aws:execute-api:ap-northeast-2:842544789367:yqcfg2houe/*/GET/capture-events/*` |
| `s3:ListBucket` | `arn:aws:s3:::inha-capstone-02-s3` (`check-aws`의 HeadBucket 점검용; 실제 업로드와 다른 권한) |

이는 필요한 동작 목록이며 IAM을 변경하는 배포 정책이 아니다. KMS 암호화 키가 별도로 강제되는 환경이라면 해당 키의 권한도 관리자가 확인해야 한다. Pi는 DynamoDB 직접 쓰기나 Lambda 배포 권한이 필요 없다.

### 5. 연결 점검 후 촬영 활성화

AWS 전송을 끈 채로 먼저 실행한다.

```bash
python -m medikit_pi --config config.toml check-aws
```

이 명령은 **단기 토큰·학교 계정 → S3 버킷 소유 계정/HeadBucket → SigV4 상태 API** 순서로 확인한다. 임의의 새 `pi-check-*` ID를 조회해 `PROCESSING`을 확인하며 사진 업로드·촬영 기록 생성·재고 변경·푸시 발송은 하지 않는다. 따라서 성공해도 `PutObject`, OCR 식별, 실제 입출고 정확도까지 검증한 것은 아니다. 오류는 단계와 예외 유형만 출력한다. 1단계 실패는 로그인·프로필·시간·네트워크, 2단계는 버킷/조회 권한, 3단계는 API Invoke 권한·SigV4를 점검한다.

실행 전 `config.toml`의 `[aws] enabled = true`로 바꾼다. `contract_confirmed=true`, `staged_capture=true`와 배포 주소는 유지한다. 새 터미널에서는 위 `AWS_PROFILE` 설정도 다시 적용한다.

```bash
python -m medikit_pi --config config.toml check-config --hardware
python -m medikit_pi --config config.toml run
```

처음에는 뚜껑을 닫고 시작해 내부 기준 사진 처리를 마친 뒤 연다. 흰 LED 후 포장을 보여주고 파란 LED가 켜지면 약을 **최상단**에 넣고 닫는다. 닫힘 사진 분석은 OCR 완료를 기다리지 않지만 서버의 약품 등록·알림은 식별과 이동 결과가 합쳐진 뒤 진행된다.

**현재 구현은 반출 때도 꺼낸 약을 카메라에 보여 포장 촬영을 남겨야 한다.** 포장 사진 없이 꺼내기만 하면 어떤 약인지 연결하지 않아 반출 재고·복용 확인 알림이 생성되지 않는다. 재반입도 같은 포장을 촬영한다. 동시에 여러 약을 움직이지 않고 한 세션에 약 하나씩 다룬다. 물리적으로 하나의 제품은 하나의 포장이라는 운영 조건을 지킨다.

### 6. 최초 실물 검증 순서

1. 빈 통·닫힘 상태에서 시작해 기준 사진을 확보한다. 센서가 뒤집혀 있거나 닫힘 촬영이 실패하면 먼저 배선·구도·조명을 수정한다.
2. 개인정보 없는 약 포장 1개를 촬영해 넣고 닫는다. 홈의 신규 약 이름·최초 등록 안내·푸시를 확인한다.
3. 같은 약을 꺼내 포장을 촬영하고 닫는다. 반출 알림을 눌러 복용 기록의 확인 대기/Y/N을 확인한다. 테스트 중 실제로 복용하지 않았다면 N으로 기록한다.
4. 같은 약을 다시 촬영해 넣는다. 중복 포장이 생기지 않고 재반입이 반영되는지 확인한다.
5. 인터넷을 잠시 끊고 한 세션을 만든 뒤 복구한다. 로컬 큐가 같은 ID로 재전송되고 포장·이력·알림이 중복되지 않는지 확인한다.
6. 별도 합성 검증 대상으로 24시간 미반입 처리와 재반입 시 이전 폐기 대상 해제를 확인한다. 약의 실제 폐기를 입증하는 시험은 아니다.

전송을 끈 `run`도 큐를 쌓는다. 나중에 활성화하면 그 사진들이 전송되므로 로컬 촬영 시험은 다른 `data_dir`를 사용한다. **실사용을 시작한 큐 DB를 지우거나 옛 사본으로 되돌리지 않는다.** 세션 순번도 저장되어 있어 초기화하면 서버의 기존 `last_sequence`와 충돌할 수 있다. 처음 생성하는 실기기 큐만 새 경로를 사용하고, 그 이후에는 같은 큐·보관함 ID를 유지한다.

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

웹캠 또는 DroidCam의 현재 영상과 YOLOE 후보 상자·영문 포장 분류·신뢰도를 확인할 수 있다. **자동 촬영·사진 저장·GPIO·세션 큐·AWS 전송은 실행하지 않는다.** 핀 설정 없이 `config.example.toml`을 그대로 사용할 수 있다. 이 복사본에 별도 가상환경과 모델을 준비한 뒤 저장소 루트 PowerShell에서 실행한다. 기존 DroidCam 촬영 테스트에는 부모 폴더의 실행 명령을 계속 사용한다.

```powershell
cd hardware/pi
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

영상과 같은 시간축의 개폐 기록을 `../../local_data/lid-events.json`에 저장한다. `at`는 영상 시작부터 초, `open`은 열린 상태다. 마지막 닫힘 이후 최소 2~3초 이상의 내부 영상(실패 시도 검증은 10초 이상)을 포함한다.

```json
[
  {"at": 0.0, "open": false},
  {"at": 0.5, "open": true},
  {"at": 6.0, "open": false}
]
```

`hardware/pi/`에서 실행한다.

```bash
python -m medikit_pi --config config.toml video --input ../../local_data/test.mp4 --lid-events ../../local_data/lid-events.json --data-dir ../../local_data/hardware-pi-video-run --report ../../local_data/hardware-pi-video-report.json
```

`--show`를 붙이면 GUI가 있는 환경에서 미리보기를 띄운다. PowerShell에서는 `python` 대신 `.\.venv\Scripts\python.exe`를 사용할 수 있다. 영상 모드는 **실제 YOLOE와 OpenCV**를 실행한다. 센서는 JSON, AF 성공은 모의값이며 정지 사진은 영상 원본 프레임이다. 영상 프레임률의 시간축으로 1초 안정화를 계산하므로 느린 PC 추론도 재생 판단 시간을 왜곡하지 않는다. 실제 Pi 처리 속도·자동초점·12 MP 화질 검증을 대신하지 않는다.

테스트 결과는 지정한 데이터 폴더의 세션별 `package.jpg`, `interior.jpg`, `queue.sqlite3`와 보고서에 남는다. 성공하지 않은 사진 파일은 없다. 영상 모드는 AWS 설정과 관계없이 전송하지 않으며 실제 Pi 데이터 폴더 사용을 거부한다. EOF를 닫힘으로 가정하지 않고 미완료 세션을 그대로 보존한다. 자동 단위 테스트의 검출기는 명시적인 모의 객체이고 실행 프로그램의 YOLO를 대체하는 fallback은 없다.

## AWS 연결 경계

2026-11-10 `backend/`의 단계 수신·재고·이력 API와 웹을 배포했다. 실제 Pi 설치·배선·단기 자격증명은 아직 확인하지 않아 예제의 `aws.enabled=false`는 유지한다. 촬영과 영속 큐는 로컬에서 동작하며, 서버 저장 성공 전에는 전송 완료로 표시하지 않는다.

배포 값은 [config.example.toml](config.example.toml)에 한 곳으로 모았다. [단계 분리 계약 2.2절](../../docs/CONTRACTS.md#22-촬영-단계-분리-2026-11-10-aws웹-배포-실기기-검증-전)을 따르며 위 설치 절차의 인증·배선 점검 후 `config.toml`에서만 전송을 켠다. `Raspberry_Pi` 원본에는 이번 변경이 자동 반영되지 않는다.

포장 JPEG는 기존 OCR Lambda를 즉시 실행하고 내부 판단은 별도 작업자로 수행한다. 각 단계 JSON과 종료 객체는 새 촬영 Lambda가 결합한다. API는 `processing_status`의 기존 enum을 반환하며 SigV4 요청 `200`·무인증 요청 `403`을 확인했다. `POST /capture-events`는 사용하지 않는다. prefix·보관함 ID·상태 URL을 미완료 큐가 있는 동안 바꾸지 않는다. 실제 Pi의 전체 촬영 경로는 별도 검증이 필요하다.

Boto3의 외부 `credential_process` 등 갱신 가능한 공급자로 **단기 세션 토큰이 포함된 자격증명**을 제공한다. 현재 촬영 API는 SigV4 전용이다. 코드의 기존 bearer 호환 경로를 실제 배포에서 지원하는 것으로 해석하지 않는다. 토큰·AWS 응답 본문·사진·OCR 원문은 로그에 기록하지 않는다. 이 설치 준비는 원격 자원이나 IAM 설정을 변경하지 않는다.

`COMPLETED`/`REVIEW_REQUIRED` 확인 후 정상 종료 상태를 먼저 저장하고 Pi 세션 사진을 삭제한다(마지막 내부 기준 사진은 별도 보존). `FAILED`/통신 실패는 동일 ID를 보존해 최대 60초 백오프로 재시도한다. AWS가 영구 실패하면 해당 선두 세션에서 전송이 대기하며 촬영은 계속 큐에 쌓인다. `REVIEW_REQUIRED`는 다음 세션 전송을 허용한다. 현재 단계 수신기는 포장 사진 없는 내부 촬영 세션을 기준 확보용으로 종료하며 재고를 바꾸지 않는다. 실제 약품 연결에는 포장 촬영이 필요하다.

포장 촬영 성공은 실제 반출·복용을 입증하지 않는다. 처방전 상세사진 보완은 기존 웹 흐름을 사용하며, 이름·환자번호 등 개인정보가 있는 영역은 촬영에서 제외하거나 가린 후 사용한다. 이 Pi 모듈은 개인정보 자동 OCR 마스킹을 제공하지 않는다. 로컬 큐는 기본 전송 완료까지 사진을 보관하므로 장치 저장소의 물리 접근도 제한한다.

## 검증 명령과 남은 실험

2026-11-11 종단 수정: 자동 반입은 유지하며 사라진 포장과 새로 드러난 포장이 함께 있으면 서버의 제품 보관 상태와 결합한다. `cabinet` 설정의 305×165×190 mm와 내부 사진 네 모서리(좌상·우상·우하·좌하, 각 0~1)를 사용해 좌표를 전송한다. 실제 내부 사진으로 모서리를 맞춘 뒤 `calibrated=true`로 바꾼다. 기본 `false`에서는 전체 영상 기준의 불확실한 추정 위치이며 Z와 상대 크기도 실측값이 아니다. 영상의 반출 정확도는 쌓인 실제 포장으로 검증해야 한다.

서버 단계·OCR가 15분 동안 완료되지 않으면 촬영 확인 대기로 넘기고 복약 화면에서 약품과 실제 반입/반출/변경 없음을 정정할 수 있다. 원본 S3와 서버 기록은 유지된다. 제품을 찾을 수 없으면 변경 없음으로 닫고 포장을 다시 촬영한다. 네트워크 자체가 끊긴 경우에는 서버 응답을 받지 못하므로 로컬 큐를 계속 보존한다.

2026-11-11 설치 준비 검증: 기존 Windows 가상환경에서 이 폴더를 작업 디렉터리로 **88개 테스트·하위 검사 3개 통과**. 새 점검 8개는 전송 비활성 기본값, 쓰기 없는 점검, 다른 계정·장기 키·버킷 거부·예상 밖 API 응답 차단, 오류 로그의 비밀값 제외, GPIO 미설정·중복 거부를 확인한다. 최초 실행은 Windows 샌드박스 임시 파일 권한 문제로 실패했고 정상 권한에서 재실행했다. 실제 Pi 배선·Linux 설치·학교 계정의 `aws login --remote` 권한·실물 촬영 전체 경로는 아직 검증하지 않았다.

같은 날 변경한 Python 4개 파일의 Ruff 검사·포맷, CLI 도움말·예제 형식 검사를 통과했다. 기존 CloudShell 단기 세션에서는 점검과 동일한 STS 계정 대조·소유자 지정 HeadBucket·새 임의 ID의 SigV4 상태 조회를 실행해 **계정 일치·버킷 접근·HTTP 200/PROCESSING**을 확인했다. 사진이나 촬영 이력은 생성하지 않았다. CloudShell 인증 성공은 Pi의 대화형 로그인 권한 확인을 대신하지 않는다.

`hardware/pi/`를 작업 폴더로 한 별도 Python 프로세스에서 실행한다. 원본과 복사본은 같은 패키지·테스트 이름을 사용하므로 저장소 루트에서 두 테스트 묶음을 한꺼번에 수집하지 않는다. 기존 PC 검증은 부모 README의 명령을 사용한다. PC와 Pi용 의존성 제약을 한 가상환경에 섞지 않는다.

2026-11-02 복사본 검증: 기존 PC 테스트 100개와 복사본 테스트 72개·하위 검증 3개, Ruff 검사·포맷 및 설정 검사를 통과했다. 실행 코드·테스트·의존성 파일 24개가 원본과 동일하며, 복사본 패키지 로딩 위치·별도 데이터 경로·AWS 비활성 기본값을 확인했다. 검증 도구는 Git 제외 임시 폴더에 설치하여 기존 PC 가상환경을 변경하지 않았다. 실제 카메라·GPIO·모델 다운로드·AWS 연결은 실행하지 않았다.

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
