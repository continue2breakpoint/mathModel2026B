from __future__ import annotations

import math

from mathmodel2026b.coverage import heading_cover_layout
from mathmodel2026b.geometry import Point
from mathmodel2026b.joint_layout import (
    convex_hull,
    local_hull_holds_at,
    point_in_convex_hull,
    sample_points,
    sampled_certificate_ok,
    verify_continuous_local_hull,
    verify_per_channel_certificate,
)


def test_convex_hull_contains_center() -> None:
    pts = [Point(-1, -1), Point(1, -1), Point(1, 1), Point(-1, 1)]
    assert point_in_convex_hull(Point(0, 0), pts)
    assert point_in_convex_hull(Point(1, 0), pts)
    assert not point_in_convex_hull(Point(2, 0), pts)
    assert len(convex_hull(pts)) == 4


def test_axial_950_passes_local_hull_on_samples() -> None:
    pts = heading_cover_layout(950.0)
    samples = sample_points(step_m=150.0)
    ok, witness = sampled_certificate_ok(pts, samples)
    assert ok, witness


def test_axial_950_passes_continuous_certificate() -> None:
    pts = heading_cover_layout(950.0)
    report = verify_continuous_local_hull(
        pts, cell_m=120.0, min_cell_m=10.0
    )
    assert report.ok
    assert report.uncertain_cells == 0


def test_single_point_fails_certificate() -> None:
    ok, witness = sampled_certificate_ok([Point(0, 0)], sample_points(step_m=300.0))
    assert not ok
    assert witness is not None


def test_per_channel_certificate() -> None:
    pts = heading_cover_layout(950.0)
    reports = verify_per_channel_certificate({1: pts, 2: pts}, cell_m=150.0, min_cell_m=15.0)
    assert set(reports) == {1, 2}
    assert all(report.ok for report in reports.values())


def test_local_hull_holds_at_center_for_axial_grid() -> None:
    pts = heading_cover_layout(950.0)
    for x in [Point(0, 0), Point(500, 500), Point(-1500, 0), Point(0, 1700), Point(1700, 0)]:
        # 边界点可能只是勉强在边界上；用 1e-6 容差判定。
        assert local_hull_holds_at(x, pts, tol=1e-6), x
