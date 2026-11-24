# 하드웨어 실행 안내

실물 보관함은 Raspberry Pi 카메라를 사용한다. 카메라·마그네틱 센서·링 조명·로컬 큐·S3 전송을 연결하고 포장 탐지와 화질을 확인하는 절차는 [Pi 설치와 촬영 안내](pi/README.md)에 있다.

- [`pi/`](pi/README.md) — 실기기 실행본. 포장 후보는 YOLOE로 찾고 자동 촬영 전후에 [사진 품질](pi/medikit_pi/image_quality.py)을 검사한다.
- [`droidcam/`](droidcam/README.md) — 이전 PC 실험 코드·설치 목록·테스트 기록. 기존 `python hardware/camera_preview.py` 명령과 `local_data/camera_captures/`에 보관한 촬영 결과를 그대로 사용할 수 있다.
- [`tests/`](tests/) — PC 비교 실험의 회귀 테스트. Pi 실행본의 테스트는 [`pi/tests/`](pi/tests/)에 있다.
