from __future__ import annotations

import math

import pytest

from mathmodel2026b.geometry import (
    Circle,
    Point,
    arena_polygon,
    clip_by_bearing,
    covering_radius,
    feasible_region,
    polygon_layout_cover_radius,
    polygon_layout_min_radius,
    minimum_enclosing_circle,
    polygon_area,
    polygon_centroid,
    polygon_diameter,
    q3_seven_point_cover_valid,
    region_clear_circle as from_module_clear_circle,
    regular_hexagon_scan_points,
)
from mathmodel2026b.protocol import MAX_EFFECTIVE_RADIUS_M


def test_point_distance() -> None:
    assert Point(0, 0).distance_to(Point(3, 4)) == 5


def test_hexagon_has_seven_points() -> None:
    points = regular_hexagon_scan_points(1400)
    assert len(points) == 7
    assert points[0] == Point(0, 0)
    assert all(pytest.approx(p.norm()) == 1400 for p in points[1:])


def test_q3_ring_radius() -> None:
    assert q3_seven_point_cover_valid(1400)
    assert not q3_seven_point_cover_valid(1000)
    assert not q3_seven_point_cover_valid(1800)


def test_seven_point_hexagon_actually_covers_the_arena() -> None:
    """问题3 覆盖性：区域内任一点到最近扫描点 <= 干扰源最小有效接收半径 1000m。"""
    worst = covering_radius(regular_hexagon_scan_points(1400), sample_rings=120)
    assert worst <= 1000.0
    # 上界也要成立：半径过大会在圆心附近留洞（圆心点距离 0，但六边形中点处变远）
    assert worst > 800.0


def test_default_scan_radius_keeps_a_coverage_margin() -> None:
    """默认 1200m 必须留出安全余量，而不是贴着 1122.96m 的理论下界跑。"""
    from mathmodel2026b.strategy import Q3Params

    radius = Q3Params().scan_radius
    worst = covering_radius(regular_hexagon_scan_points(radius), sample_rings=90)
    assert worst <= 1000.0
    assert 1000.0 - worst >= 20.0
    assert radius >= 900.0 * math.sqrt(3) - 100.0 * math.sqrt(19.0)


def test_arena_polygon_contains_the_disk() -> None:
    for p in arena_polygon(1800):
        assert p.norm() >= 1800.0 - 1e-6


def test_polygon_diameter_and_area() -> None:
    square = [Point(-1, -1), Point(1, -1), Point(1, 1), Point(-1, 1)]
    assert polygon_diameter(square) == pytest.approx(2 * math.sqrt(2))
    assert polygon_area(square) == pytest.approx(4.0)
    assert polygon_centroid(square) == pytest.approx(Point(0.0, 0.0))


def test_minimum_enclosing_circle() -> None:
    circle = minimum_enclosing_circle([Point(0, 0), Point(2, 0), Point(1, 2)])
    assert circle is not None
    assert circle.center.distance_to(Point(0, 0)) == pytest.approx(circle.radius)
    assert circle.center.distance_to(Point(2, 0)) == pytest.approx(circle.radius)
    assert circle.center.distance_to(Point(1, 2)) == pytest.approx(circle.radius)


def test_minimum_enclosing_circle_degenerate() -> None:
    assert minimum_enclosing_circle([]) is None
    single = minimum_enclosing_circle([Point(3, 4)])
    assert single == Circle(Point(3, 4), 0.0)


def test_bearing_wedge_contains_true_direction_and_excludes_outside() -> None:
    """问题1 的基础：±1° 扇形必须包含真实方向、排除偏 3° 的方向。"""
    apex = Point(0.0, 0.0)
    poly = clip_by_bearing(arena_polygon(1800), apex, 0.0, 1.0)
    assert len(poly) >= 3
    inside = Point(1000 * math.cos(math.radians(0.5)), 1000 * math.sin(math.radians(0.5)))
    outside = Point(1000 * math.cos(math.radians(3.0)), 1000 * math.sin(math.radians(3.0)))
    assert _contains(poly, inside)
    assert not _contains(poly, outside)


def test_feasible_region_shrinks_with_second_bearing() -> None:
    # 注意两个检测点到真值的距离都必须 <= 有效接收半径上界 1500m，
    # 否则半径约束本身就会把真值裁掉（那是正确行为，不是 bug）。
    truth = Point(200.0, 300.0)
    a = Point(-1000.0, 0.0)
    b = Point(500.0, 866.0)
    one = feasible_region([(a, a.bearing_to(truth))])
    two = feasible_region([(a, a.bearing_to(truth)), (b, b.bearing_to(truth))])
    assert polygon_area(two) < polygon_area(one)
    assert _contains(one, truth)
    assert _contains(two, truth)
    assert polygon_diameter(two) < polygon_diameter(one)


def test_range_bound_is_a_safe_superset() -> None:
    """1500m 半径约束必须仍然包含真值（它只是上界，不是真值）。"""
    truth = Point(900.0, 0.0)
    apex = Point(0.0, 0.0)
    bounded = feasible_region(
        [(apex, 0.0)], half_width_deg=1.0, max_range_m=MAX_EFFECTIVE_RADIUS_M
    )
    assert _contains(bounded, truth)
    # 超出上界的点应被排除
    assert not _contains(bounded, Point(1600.0, 0.0))


def test_range_bound_excludes_a_too_distant_truth() -> None:
    """反向验证：真值超出上界时确实会被裁掉（说明约束真的在起作用）。"""
    apex = Point(0.0, 0.0)
    region = feasible_region([(apex, 0.0)], max_range_m=MAX_EFFECTIVE_RADIUS_M)
    assert polygon_diameter(region) <= 2 * MAX_EFFECTIVE_RADIUS_M + 2.0
    assert not _contains(region, Point(1520.0, 0.0))


def test_three_bearings_pin_down_to_a_small_region() -> None:
    """三个分离良好的远距检测点可把可行域压到 ~60m 量级。

    注意：远距（>1000m）读数在距离 d 处的横向不确定度约为 d*tan(1°) ≈ 17~21m，
    因此仅靠远距交会**不足以**达到 20m 的清除判据；这正是策略里必须"逼近后再测"
    的原因（见下一个测试）。
    """
    truth = Point(200.0, 300.0)
    apexes = [Point(-1000.0, 0.0), Point(500.0, 866.0), Point(500.0, -866.0)]
    readings = [(ap, ap.bearing_to(truth)) for ap in apexes]
    region = feasible_region(readings)
    assert _contains(region, truth)
    assert polygon_diameter(region) < 100.0


def test_close_range_bearings_enable_the_clear_criterion() -> None:
    """远距 3 读数只能收到 ~30m 包围半径；补几次近距读数后才能满足 20m 判据。"""
    truth = Point(200.0, 300.0)
    far = [Point(-1000.0, 0.0), Point(500.0, 866.0), Point(500.0, -866.0)]
    region = feasible_region([(ap, ap.bearing_to(truth)) for ap in far])
    far_circle = minimum_enclosing_circle(region)
    assert far_circle is not None
    assert far_circle.radius > 20.0  # >光学作用距离，还不能确定性清除

    close = [Point(300.0, 400.0), Point(120.0, 200.0), Point(340.0, 240.0)]
    readings = [(ap, ap.bearing_to(truth)) for ap in far + close]
    region = feasible_region(readings)
    assert _contains(region, truth)
    circle = from_module_clear_circle(region)
    assert circle is not None
    assert circle.radius <= 20.0
    assert circle.center.distance_to(truth) <= 20.0


def test_single_bearing_from_25m_also_suffices() -> None:
    """只补一次 25m 处的读数就足以把包围半径压到 13m 左右。"""
    truth = Point(200.0, 300.0)
    far = [Point(-1000.0, 0.0), Point(500.0, 866.0), Point(500.0, -866.0)]
    near = Point(truth.x + 25.0, truth.y)
    readings = [(ap, ap.bearing_to(truth)) for ap in far + [near]]
    region = feasible_region(readings)
    assert _contains(region, truth)
    assert from_module_clear_circle(region) is not None


def _contains(poly: list[Point], p: Point, eps: float = 1e-6) -> bool:
    n = len(poly)
    if n < 3:
        return False
    sign = 0
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        cross = (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x)
        if abs(cross) < eps:
            continue
        s = 1 if cross > 0 else -1
        if sign == 0:
            sign = s
        elif s != sign:
            return False
    return True


def test_analytic_cover_radius_matches_numeric() -> None:
    """解析覆盖半径必须与逐点采样一致（否则布局判据就是在自欺）。"""
    from mathmodel2026b.geometry import regular_polygon_scan_points

    for sides, ring in ((6, 1200.0), (7, 1110.0), (8, 1010.0), (9, 968.0)):
        analytic = polygon_layout_cover_radius(sides, ring)
        numeric = covering_radius(
            regular_polygon_scan_points(sides, ring, center=True), sample_rings=200
        )
        assert analytic == pytest.approx(numeric, abs=3.0), (sides, ring)


def test_default_matrix_layout_passes_its_own_certificate() -> None:
    """矩阵策略的默认扫描布局必须满足**它自己**的覆盖判据。

    判据用的计划半径 = 1000 − ``radius_margin_m``（默认 50m）= 950m，
    并且要求 60m 栅格的四角都在半径内。解析式
    :func:`polygon_layout_cover_radius` 与逐格判据都要过。
    """
    from mathmodel2026b.coverage import CoverageGrid, CoverageTracker, build_beliefs
    from mathmodel2026b.geometry import regular_polygon_scan_points
    from mathmodel2026b.knowledge import KnowledgeMatrix
    from mathmodel2026b.strategy_matrix import MatrixParams

    p = MatrixParams()
    plan_radius = 1000.0 - p.radius_margin_m
    analytic = polygon_layout_cover_radius(p.scan_sides, p.scan_radius)
    assert analytic <= plan_radius, (p.scan_sides, p.scan_radius, analytic)

    # 逐格判据也必须通过（这是策略真正使用的那条）
    grid = CoverageGrid(cell_m=p.coverage_cell_m)
    matrix = KnowledgeMatrix(cell_m=p.matrix_cell_m)
    tracker = CoverageTracker(grid, matrix)
    tracker.sync(build_beliefs(matrix), matrix=matrix)
    tracker.observe_stops(
        regular_polygon_scan_points(p.scan_sides, p.scan_radius, center=True)
    )
    assert tracker.assess().complete

    # 解析最小环半径与布局一致
    assert polygon_layout_min_radius(p.scan_sides, plan_radius) <= p.scan_radius


def test_ring_layout_is_not_a_heading_cover_but_the_lattice_is() -> None:
    """环形布局**不是**问题4 的朝向完备发现层，三角网格才是。

    常见误解："只要区域内每一点都有 ≤1000m 的停点，就有 x ∈ conv(S_x)"。
    这是错的 —— 凸包需要的是"停点把 x **围起来**"，而不是"有一个停点在附近"。
    实测：中心 + 正八边形 r=1010 时，最坏位置 (811.8, 1606.5) 附近只有 1 个
    停点在 1000m 内，凸包退化成单点，到凸包距离 897.7m ⇒ 该位置的定向源
    可以朝外发射而完全不被发现。

    真正可证的构造是等边三角网格（间距 ≤1000m）：任意点所在格三角形的三个
    顶点都在 1000m 内，而 x 属于这个三角形的凸包。
    """
    from mathmodel2026b.coverage import heading_cover_layout, heading_cover_report
    from mathmodel2026b.geometry import regular_polygon_scan_points
    from mathmodel2026b.protocol import MIN_EFFECTIVE_RADIUS_M
    from mathmodel2026b.strategy_matrix import MatrixParams

    p = MatrixParams()
    ring = regular_polygon_scan_points(p.scan_sides, p.scan_radius, center=True)
    rep = heading_cover_report(ring, step_m=60.0)
    # 圆盘覆盖成立……
    assert rep["worst_nearest_m"] <= MIN_EFFECTIVE_RADIUS_M, rep
    # ……但朝向覆盖**不**成立
    assert rep["worst_hull_m"] > 100.0, rep

    lattice = heading_cover_layout(950.0)
    rep2 = heading_cover_report(lattice, step_m=60.0)
    assert rep2["worst_hull_m"] <= 1e-6, rep2
    assert rep2["worst_nearest_m"] <= MIN_EFFECTIVE_RADIUS_M, rep2
