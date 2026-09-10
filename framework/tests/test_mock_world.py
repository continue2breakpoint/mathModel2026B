from __future__ import annotations

from math import hypot

import pytest

from mathmodel2026b.geometry import Point, angle_difference_deg, degrees_atan2
from mathmodel2026b.mock.world import Case, Jammer, World, bearing_error, generate_case
from mathmodel2026b.protocol import (
    ARENA_RADIUS_M,
    MAX_EFFECTIVE_RADIUS_M,
    MIN_EFFECTIVE_RADIUS_M,
)


def test_generate_case_is_deterministic() -> None:
    a = generate_case(42)
    b = generate_case(42)
    assert a.as_dict() == b.as_dict()
    assert a.n_jammers == b.n_jammers


def test_generate_case_respects_the_rules() -> None:
    for seed in range(1, 30):
        case = generate_case(seed)
        assert 10 <= case.n_jammers <= 16
        channels = [j.channel for j in case.jammers]
        assert len(set(channels)) == len(channels)
        assert all(1 <= c <= 20 for c in channels)
        for jammer in case.jammers:
            assert jammer.position.norm() <= ARENA_RADIUS_M + 1e-6
            assert MIN_EFFECTIVE_RADIUS_M <= jammer.effective_radius_m <= MAX_EFFECTIVE_RADIUS_M
            assert jammer.is_omni


def test_directional_case_marks_some_jammers() -> None:
    case = generate_case(7, omni_only=False)
    assert any(not j.is_omni for j in case.jammers)
    assert any(j.is_omni for j in case.jammers)


def test_bearing_error_is_bounded_and_deterministic() -> None:
    p = Point(123.4, -56.7)
    first = bearing_error(1, 5, p)
    assert first == bearing_error(1, 5, p)
    for seed in range(5):
        for channel in range(1, 21):
            err = bearing_error(seed, channel, Point(seed * 10.0, channel * 3.0))
            assert -1.0 <= err <= 1.0


def test_bearing_error_changes_with_location_not_with_repeats() -> None:
    assert bearing_error(1, 3, Point(0.0, 0.0)) != bearing_error(1, 3, Point(500.0, 500.0))


def _world(jammer: Jammer, **kwargs) -> World:
    case = Case(seed=11, jammers=[jammer], n_jammers=1)
    return World(case=case, robot_id="team", **kwargs)


def test_timing_model_matches_appendix() -> None:
    """附录2：移动 5m/s、切频道 1s、测向 5s、光学 3s + 激光 2s。"""
    jammer = Jammer(channel=1, position=Point(1000.0, 0.0), effective_radius_m=1200.0)
    world = _world(jammer)
    world.enter()

    world.measure(Point(0.0, 0.0), 1)  # 原地、同频道：只花 5s
    assert world.virtual_time_s == pytest.approx(5.0)

    world.measure(Point(10.0, 0.0), 1)  # 移动 10m = 2s，再测 5s
    assert world.virtual_time_s == pytest.approx(12.0)

    world.measure(Point(10.0, 0.0), 2)  # 切频道 1s + 测 5s
    assert world.virtual_time_s == pytest.approx(18.0)

    world.clear(Point(10.0, 0.0), 2)  # 光学 3s + 激光 2s
    assert world.virtual_time_s == pytest.approx(23.0)
    assert world.channel_switch_count == 1


def test_no_signal_beyond_effective_radius() -> None:
    jammer = Jammer(channel=4, position=Point(1500.0, 0.0), effective_radius_m=1000.0)
    world = _world(jammer)
    world.enter()
    response, outcome = world.measure(Point(0.0, 0.0), 4)
    assert outcome.kind == "no_signal"
    assert response["measure_result"] == "no_signal"
    assert "svd_deg" not in response


def test_direction_reading_is_within_one_degree_of_truth() -> None:
    jammer = Jammer(channel=9, position=Point(800.0, 300.0), effective_radius_m=1200.0)
    world = _world(jammer)
    world.enter()
    apex = Point(-200.0, 400.0)
    response, outcome = world.measure(apex, 9)
    assert outcome.kind == "direction"
    truth = degrees_atan2(
        jammer.position.y - apex.y, jammer.position.x - apex.x
    )
    assert angle_difference_deg(response["svd_deg"], truth) <= 1.0 + 1e-9


def test_near_when_closer_than_five_metres() -> None:
    jammer = Jammer(channel=2, position=Point(100.0, 0.0), effective_radius_m=1000.0)
    world = _world(jammer)
    world.enter()
    _, outcome = world.measure(Point(103.0, 0.0), 2)
    assert outcome.kind == "near"


def test_clear_succeeds_only_within_twenty_metres() -> None:
    jammer = Jammer(channel=3, position=Point(500.0, 0.0), effective_radius_m=1000.0)
    world = _world(jammer)
    world.enter()
    _, miss = world.clear(Point(530.0, 0.0), 3)
    assert miss.kind == "no_target_in_range"
    _, hit = world.clear(Point(515.0, 0.0), 3)
    assert hit.kind == "success"
    assert world.cleared_count == 1
    # 清除后该频道应当读不到信号
    _, outcome = world.measure(Point(515.0, 0.0), 3)
    assert outcome.kind == "no_signal"


def test_clearing_ignores_coverage_angle() -> None:
    """附录2-8：清除是否成功只与距离有关，与覆盖角无关。"""
    jammer = Jammer(
        channel=8,
        position=Point(300.0, 0.0),
        effective_radius_m=1000.0,
        direction_deg=0.0,
    )
    world = _world(jammer)
    world.enter()
    behind = Point(280.0, 0.0)  # 位于定向源背向侧
    assert not jammer.covers(behind)
    _, outcome = world.clear(behind, 8)
    assert outcome.kind == "success"


def test_summary_metrics() -> None:
    jammer = Jammer(channel=6, position=Point(600.0, 0.0), effective_radius_m=1000.0)
    world = _world(jammer)
    world.enter()
    world.clear(Point(600.0, 0.0), 6)
    summary = world.summary()
    assert summary["cleared_count"] == 1
    assert summary["cleared_ratio"] == 1.0
    assert summary["average_clear_time_s"] == pytest.approx(summary["virtual_time_s"])
    assert summary["move_distance_m"] == pytest.approx(hypot(600.0, 0.0))
