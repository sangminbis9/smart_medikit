# 하드웨어 실행 안내

실물 보관함은 Raspberry Pi 5와 Camera Module 3 Wide(IMX708)를 사용한다. 카메라·마그네틱 센서·링 조명·로컬 큐·S3 전송을 연결하는 절차는 [Pi 설치와 촬영 안내](pi/README.md)에 있다.

- 포장 후보는 [YOLOE](pi/medikit_pi/detection.py)로 찾고, 미리보기와 저장할 사진의 [품질](pi/medikit_pi/image_quality.py)을 검사한다.
- 실기기에서 `camera-check`로 탐지·화질을 확인하고 `run --local-test`로 개폐부터 로컬 사진 저장까지 시험할 수 있다. [실물 시험 절차](pi/README.md#camera-module-3-wide-실물-시험) · [자동 테스트](pi/tests/)
