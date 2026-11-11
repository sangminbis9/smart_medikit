"""내부 사진의 포장 후보 변화를 확인 대상으로 제안한다."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np

from .config import load_config
from .detection import Detection, YOLOEDetector
from .vision import gray_frame, iou, motion_score


# 유효한 정규화 좌표와 신뢰도를 가진 후보만 비교한다.
def valid_detection(item: Detection) -> bool:
    x1, y1, x2, y2 = item.box
    return (
        all(math.isfinite(v) and 0 <= v <= 1 for v in item.box)
        and x1 < x2 and y1 < y2
        and math.isfinite(item.confidence) and 0 <= item.confidence <= 1
    )


# 내부 후보의 가림 변화를 구분하고 제품 상태 결합이 필요한 경우 별도로 표시한다.
def compare_interior(before, after, before_items, after_items, *, automatic_in=False):
    result = {"processing_status": "REVIEW_REQUIRED", "candidate": None, "reason": None}
    if (before is None or after is None or before.size == 0 or after.size == 0
            or before.shape != after.shape):
        result["reason"] = "IMAGE_UNAVAILABLE_OR_MISMATCH"
        return result
    if any(not valid_detection(item) for item in [*before_items, *after_items]):
        result["reason"] = "INVALID_DETECTION"
        return result
    # 큰 전역 변화는 카메라 이동이나 조명 차이일 수 있어 방향을 제안하지 않는다.
    if motion_score(gray_frame(before), gray_frame(after)) > 0.18:
        result["reason"] = "SCENE_CHANGED"
        return result
    # 후보가 여러 개와 겹치면 가림이나 포장 이동을 구분할 수 없다.
    links = [(i, j) for i, first in enumerate(before_items)
             for j, second in enumerate(after_items) if iou(first.box, second.box) >= 0.5]
    if (len({i for i, _ in links}) != len(links)
            or len({j for _, j in links}) != len(links)):
        result["reason"] = "AMBIGUOUS_OVERLAP"
        return result
    removed = [item for i, item in enumerate(before_items) if i not in {a for a, _ in links}]
    added = [item for j, item in enumerate(after_items) if j not in {b for _, b in links}]
    # 사라진 위 포장 아래에서 새 후보가 드러난 장면을 신규 반입으로 오인하지 않는다.
    if automatic_in and removed and added:
        if len(removed) == 1 or len(added) == 1:
            result.update(processing_status='COMPLETED', reason='OCCLUSION_STATE_REQUIRED',
                          candidate={'direction': 'STATE', 'certainty': 'ESTIMATED',
                                     'box': added[0].box, 'removed_box': removed[0].box})
        else:
            result['reason'] = 'MULTIPLE_CHANGES'
        return result
    # 사라진 포장이 없는 새 후보는 사용 규칙에 따라 최상단 반입으로 처리한다.
    if automatic_in and len(added) == 1 and not removed:
        result.update(processing_status="COMPLETED", reason="TOP_PLACEMENT",
                      candidate={"direction": "IN", "certainty": "ESTIMATED", "box": added[0].box})
        return result
    if automatic_in and len(removed) == 1 and not added:
        result.update(processing_status='COMPLETED', reason='SINGLE_REMOVAL',
                      candidate={'direction': 'OUT', 'certainty': 'ESTIMATED', 'box': removed[0].box})
        return result
    # 맨 위에 놓은 포장이 다른 포장을 가리거나 드러내면 방향 판단을 보류한다.
    if (any(iou(item.box, other.box) > 0 for item in removed for other in after_items)
            or any(iou(item.box, other.box) > 0 for item in added for other in before_items)):
        result["reason"] = "POSSIBLE_OCCLUSION"
        return result
    if len(removed) == 1 and not added:
        result["candidate"] = {"direction": "OUT", "certainty": "ESTIMATED", "box": removed[0].box}
    elif len(added) == 1 and not removed:
        result["candidate"] = {"direction": "IN", "certainty": "ESTIMATED", "box": added[0].box}
    else:
        result["reason"] = "NO_SINGLE_CHANGE"
    return result


# 로컬 사진 두 장과 준비된 모델로 후보를 평가하고 결과만 출력한다.
def main():
    parser = argparse.ArgumentParser(description="보관함 전후 사진의 포장 변화 실험")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    args = parser.parse_args()
    before = cv2.imdecode(np.fromfile(args.before, dtype=np.uint8), cv2.IMREAD_COLOR)
    after = cv2.imdecode(np.fromfile(args.after, dtype=np.uint8), cv2.IMREAD_COLOR)
    if before is None or after is None or before.shape != after.shape:
        print(json.dumps({"processing_status": "REVIEW_REQUIRED", "candidate": None,
                          "reason": "IMAGE_UNAVAILABLE_OR_MISMATCH"}))
        return
    config = load_config(args.config)["detector"]
    detector = YOLOEDetector(config["model_path"], config["image_size"],
                             config["confidence"], config["threads"], config["prompts"])
    result = compare_interior(before, after, detector.predict(before), detector.predict(after))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()


# 보정한 내부 네 모서리를 실제 mm로 변환하며 미보정 좌표는 불확실 추정으로 남긴다.
def locate_candidate(candidate, geometry=None):
    geometry = geometry or {}
    bounds = candidate.get('box')
    if not bounds or len(bounds) != 4:
        return None
    width, depth, height = [float(geometry.get(k, default)) for k, default in
                            [('width_mm', 305), ('depth_mm', 165), ('height_mm', 190)]]
    corners = geometry.get('corners', [[0, 0], [1, 0], [1, 1], [0, 1]])
    source = np.array(corners, dtype=np.float32)
    target = np.array([[0, 0], [width, 0], [width, depth], [0, depth]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(source, target)
    x1, y1, x2, y2 = bounds
    center = np.array([[[(x1 + x2) / 2, (y1 + y2) / 2]]], dtype=np.float32)
    x, y = cv2.perspectiveTransform(center, matrix)[0, 0]
    area = (x2 - x1) * (y2 - y1)
    return {'position': {'x': round(float(np.clip(x, 0, width)), 2),
                         'y': round(float(np.clip(y, 0, depth)), 2), 'z_estimated': 0,
                         'certainty': 'ESTIMATED' if geometry.get('calibrated') else 'UNCERTAIN'},
            'size_class': 'SMALL' if area < 0.08 else 'MEDIUM' if area < 0.25 else 'LARGE',
            'dimensions': {'width': width, 'depth': depth, 'height': height}}
