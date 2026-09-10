from __future__ import annotations

from mathmodel2026b.geometry import Point
from mathmodel2026b.state import ChannelState, ChannelStatus, DogState, switch_cost

TRUTH = Point(300.0, -100.0)
# 三个检测点到真值的距离都在 1500m 有效接收半径上界内，且角度分离良好
APEX_A = Point(-1000.0, 0.0)
APEX_B = Point(600.0, 800.0)
APEX_C = Point(600.0, -900.0)


def test_channel_state_transition() -> None:
    state = ChannelState(3)
    assert state.status == ChannelStatus.UNKNOWN
    state.add_bearing(APEX_A, APEX_A.bearing_to(TRUTH))
    assert state.status == ChannelStatus.DETECTED
    state.add_bearing(APEX_B, APEX_B.bearing_to(TRUTH))
    assert state.status == ChannelStatus.LOCALIZING
    state.mark_cleared(TRUTH)
    assert state.status == ChannelStatus.CLEARED
    assert state.cleared_at == TRUTH


def test_dog_has_20_channels() -> None:
    assert set(DogState().channels) == set(range(1, 21))


def test_clear_circle_becomes_available_after_close_bearings() -> None:
    """远距交会只能把可行域收到 ~60m；必须再补几次近距读数才够 20m 判据。"""
    state = ChannelState(5)
    for apex in (APEX_A, APEX_B, APEX_C):
        state.add_bearing(apex, apex.bearing_to(TRUTH))
    assert state.clear_circle() is None  # 远距三读数还不够
    assert state.diameter() < 100.0

    for offset in ((120.0, 60.0), (-80.0, 100.0), (140.0, -60.0)):
        close = Point(TRUTH.x + offset[0], TRUTH.y + offset[1])
        state.add_bearing(close, close.bearing_to(TRUTH))
    circle = state.clear_circle()
    assert circle is not None
    assert circle.center.distance_to(TRUTH) <= 20.0


def test_single_bearing_is_not_enough_to_clear() -> None:
    state = ChannelState(5)
    state.add_bearing(APEX_A, APEX_A.bearing_to(TRUTH))
    assert state.clear_circle() is None
    assert state.diameter() > 100.0


def test_clear_failure_recorded() -> None:
    state = ChannelState(5)
    state.mark_cleared(TRUTH)
    state.mark_clear_failed()
    assert state.clear_failures == 1
    assert state.status is ChannelStatus.LOCALIZING


def test_switch_cost_only_when_channel_changes() -> None:
    assert switch_cost(3, 3) == 0.0
    assert switch_cost(3, 4) == 1.0
