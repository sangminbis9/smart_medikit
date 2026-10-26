"""외부 설정을 읽고 잘못된 촬영 조건을 실행 전에 거부한다."""

import math
import tomllib
from pathlib import Path


# 설정 파일의 상대 경로를 고정하고 안전한 범위만 허용한다.
def load_config(path: Path) -> dict:
    with path.open("rb") as source:
        config = tomllib.load(source)
    for section in ("camera", "gpio", "detector", "vision", "aws"):
        if not isinstance(config.get(section), dict):
            raise ValueError(f"설정 섹션이 필요합니다: {section}")
    if not isinstance(config.get("box_id"), str) or not config["box_id"].strip():
        raise ValueError("box_id가 필요합니다.")
    for section, keys in {
        "aws": ("enabled", "contract_confirmed"),
        "gpio": ("lid_pull_up", "lid_closed_when_active", "output_active_high"),
    }.items():
        for key in keys:
            if type(config[section][key]) is not bool:
                raise ValueError(f"true 또는 false 설정이 필요합니다: {section}.{key}")
    for section, keys in {
        "camera": ("fps", "timeout_seconds", "restart_seconds"),
        "gpio": ("debounce_seconds",),
        "detector": ("image_size", "threads", "interval_seconds", "max_age_seconds"),
        "vision": (
            "analysis_width",
            "warmup_seconds",
            "stable_seconds",
            "close_settle_seconds",
            "close_attempt_seconds",
            "package_retry_seconds",
        ),
    }.items():
        for key in keys:
            value = config[section][key]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"양의 유한한 설정값이 필요합니다: {section}.{key}")
    for key in (
        "motion_threshold",
        "minimum_change",
        "min_area",
        "max_area",
        "min_iou",
        "capture_motion_threshold",
    ):
        if not 0 < config["vision"][key] <= 1:
            raise ValueError(f"0 초과 1 이하 값이 필요합니다: vision.{key}")
    vision = config["vision"]
    if (
        not 0.5 <= vision["close_settle_seconds"] <= 1.0
        or vision["close_attempt_seconds"] <= vision["close_settle_seconds"]
    ):
        raise ValueError("닫힘 안정화는 0.5~1초이며 시도 제한시간보다 짧아야 합니다.")
    if type(vision["close_retries"]) is not int or not 0 <= vision["close_retries"] <= 2:
        raise ValueError("내부 촬영 추가 재시도는 최대 2회입니다.")
    if not 2 <= config["aws"]["poll_seconds"] <= 3:
        raise ValueError("AWS 상태 조회 간격은 2~3초입니다.")
    if vision["min_area"] >= vision["max_area"]:
        raise ValueError("후보 최소 면적은 최대 면적보다 작아야 합니다.")
    if not 0 < config["detector"]["confidence"] <= 1:
        raise ValueError("탐지 신뢰도는 0 초과 1 이하입니다.")
    for key in ("sharpness_threshold", "still_sharpness_threshold"):
        if not math.isfinite(vision[key]) or vision[key] <= 0:
            raise ValueError("선명도 기준은 양의 유한한 값이어야 합니다.")
    for key in ("preview_size", "still_size"):
        size = config["camera"][key]
        if len(size) != 2 or any(type(n) is not int or n < 32 for n in size):
            raise ValueError("영상 크기는 양의 정수 두 개입니다.")
    preview, still = config["camera"]["preview_size"], config["camera"]["still_size"]
    if preview[0] * still[1] != preview[1] * still[0]:
        raise ValueError("후보 좌표를 유지하려면 미리보기와 사진의 화면 비율이 같아야 합니다.")
    if not 1 <= config["camera"]["jpeg_quality"] <= 100:
        raise ValueError("JPEG 품질은 1~100입니다.")
    for section, key, minimum in (
        ("camera", "jpeg_quality", 1),
        ("camera", "camera_index", 0),
        ("camera", "still_delay_frames", 0),
        ("detector", "threads", 1),
        ("detector", "image_size", 32),
        ("vision", "analysis_width", 32),
        ("vision", "pixel_threshold", 1),
    ):
        if type(config[section][key]) is not int or config[section][key] < minimum:
            raise ValueError(f"{minimum} 이상의 정수 설정이 필요합니다: {section}.{key}")
    if config["detector"]["image_size"] % 32 or vision["pixel_threshold"] > 254:
        raise ValueError("YOLO 입력 크기는 32의 배수, 픽셀 변화 임계값은 1~254입니다.")
    config["data_dir"] = (path.parent / config["data_dir"]).resolve()
    config["detector"]["model_path"] = (path.parent / config["detector"]["model_path"]).resolve()
    token = config["aws"].get("bearer_token_file")
    if token:
        config["aws"]["bearer_token_file"] = str((path.parent / token).resolve())
    return config
