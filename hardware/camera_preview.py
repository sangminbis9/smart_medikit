"""기존 실행 명령으로 DroidCam 비교 실험을 시작한다."""

from pathlib import Path
import sys

# 파일 경로로 실행해도 저장소 패키지에서 공통 품질 판정을 가져온다.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hardware.droidcam.camera_preview import main


if __name__ == "__main__":
    raise SystemExit(main())
