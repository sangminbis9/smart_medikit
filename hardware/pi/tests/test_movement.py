"""내부 영상 후보 비교가 불확실한 상황에서 확정을 피하는지 검사한다."""

import numpy as np

from medikit_pi.detection import Detection
from medikit_pi.movement import compare_interior


# 같은 크기의 합성 내부 영상을 만든다.
def image():
    return np.zeros((100, 100, 3), dtype=np.uint8)


# 단일 후보의 등장과 소실은 확정이 아닌 방향 후보로만 반환한다.
def test_single_candidate_is_always_reviewed():
    frame = image()
    item = Detection((0.1, 0.1, 0.4, 0.4), 0.9, "box")
    for first, second, direction in [([], [item], "IN"), ([item], [], "OUT")]:
        result = compare_interior(frame, frame, first, second)
        assert result["processing_status"] == "REVIEW_REQUIRED"
        assert result["candidate"]["direction"] == direction
        assert result["candidate"]["certainty"] == "ESTIMATED"


# 후보가 같거나 동시에 이동한 경우 방향을 확정하지 않는다.
def test_no_change_or_moved_item_is_not_classified():
    frame = image()
    old = Detection((0.1, 0.1, 0.3, 0.3), 0.9, "box")
    moved = Detection((0.6, 0.6, 0.8, 0.8), 0.9, "box")
    for after in ([old], [moved]):
        assert compare_interior(frame, frame, [old], after)["candidate"] is None


# 사진 오류와 전역 장면 변화에서는 후보를 반환하지 않는다.
def test_invalid_or_changed_scene_is_not_classified():
    frame = image()
    item = Detection((0.1, 0.1, 0.4, 0.4), 0.9, "box")
    assert compare_interior(None, frame, [], [item])["candidate"] is None
    assert compare_interior(frame, frame[:10], [], [item])["candidate"] is None
    assert compare_interior(frame, 255 - frame, [], [item])["reason"] == "SCENE_CHANGED"
    bad = Detection((float("nan"), 0, 1, 1), 0.9, "box")
    assert compare_interior(frame, frame, [], [bad])["reason"] == "INVALID_DETECTION"


# 한 후보가 여러 후보와 겹치면 가림과 중복 검출을 구분하지 않는다.
def test_ambiguous_matches_are_not_classified():
    frame = image()
    item = Detection((0.1, 0.1, 0.4, 0.4), 0.9, "box")
    result = compare_interior(frame, frame, [item], [item, item])
    assert result["reason"] == "AMBIGUOUS_OVERLAP"
    assert result["candidate"] is None


# 최상단 포장에 가려지거나 다시 드러난 후보는 입출고로 단정하지 않는다.
def test_top_placement_may_hide_another_package():
    frame = image()
    lower = Detection((0.1, 0.1, 0.4, 0.4), 0.9, "box")
    upper = Detection((0.3, 0.1, 0.6, 0.4), 0.9, "box")
    assert compare_interior(frame, frame, [lower], [lower, upper])["reason"] == "POSSIBLE_OCCLUSION"
    assert compare_interior(frame, frame, [lower, upper], [lower])["reason"] == "POSSIBLE_OCCLUSION"
