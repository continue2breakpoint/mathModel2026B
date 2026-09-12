"""知识矩阵（``channel × path``）的单元测试。

覆盖三件事：
1. 矩阵本身的行为 —— 同格重测、状态迁移、路径列增长、R 的夹逼；
2. 与 ``dataType.txt`` 的双向读写（``as_table`` / ``loads_table``）；
3. 覆盖证书与规划 —— 7 点覆盖必须判定为"完备"，且停点规划确实在减少缺口。
"""

from __future__ import annotations

import math

import pytest

from mathmodel2026b.coverage import (
    CoverageGrid,
    max_distance_to_region,
    CoverageTracker,
    build_beliefs,
    distance_point_to_region,
    estimate_radius_bounds,
    heading_cover_condition,
    heading_cover_layout,
    heading_cover_report,
    hidden_mask,
    min_convex_distance,
    plausible_directional_mask,
    point_in_polygon,
)
from mathmodel2026b.geometry import Point, regular_polygon_scan_points
from mathmodel2026b.knowledge import CellStatus, KnowledgeMatrix


# ---------------------------------------------------------------------------
# 知识矩阵
# ---------------------------------------------------------------------------
def test_matrix_grows_columns_and_dedupes_cells() -> None:
    m = KnowledgeMatrix(cell_m=120.0)
    assert m.path_length == 0
    m.record_measure(Point(0.0, 0.0), 3, CellStatus.FIND, bearing_deg=45.0)
    assert m.path_length == 1
    # 同一格（120m 内）再测：不新增列，也不新增信息（附录2-1）
    m.record_measure(Point(30.0, 20.0), 3, CellStatus.FIND, bearing_deg=45.0)
    assert m.path_length == 1
    assert m.evidence(3, m.cell_key(Point(0.0, 0.0))).probes == 2
    assert m.probes(3) == 2
    # 换一格 -> 新增列
    m.record_measure(Point(1200.0, 0.0), 3, CellStatus.NOT_FIND)
    assert m.path_length == 2
    assert m.not_found(3) == 1


def test_matrix_status_transitions_and_clear() -> None:
    m = KnowledgeMatrix()
    m.record_measure(Point(0.0, 0.0), 5, CellStatus.NOT_FIND)
    assert m.status_at(Point(0.0, 0.0), 5) is CellStatus.NOT_FIND
    assert not m.is_cleared(5)
    m.mark_channel_cleared(5)
    assert m.is_cleared(5)
    assert all(ev.status is CellStatus.CLEARED for ev in m.cells[5].values())


def test_matrix_record_clear_failed_counts() -> None:
    m = KnowledgeMatrix()
    m.record_clear_failed(Point(0.0, 0.0), 7)
    m.record_clear_failed(Point(0.0, 0.0), 7)
    ev = m.evidence(7, m.cell_key(Point(0.0, 0.0)))
    assert ev is not None and ev.failures == 2
    assert ev.status is CellStatus.CLEAR_FAILED


def test_data_type_table_roundtrip() -> None:
    """``as_table`` 出来的表必须能被 ``loads_table`` 读回去。"""
    src = KnowledgeMatrix(cell_m=120.0)
    src.record_measure(Point(0.0, 0.0), 1, CellStatus.FIND, bearing_deg=12.5)
    src.record_measure(Point(0.0, 0.0), 2, CellStatus.NOT_FIND)
    src.record_measure(Point(1200.0, 0.0), 1, CellStatus.FIND, bearing_deg=190.0)
    src.record_measure(Point(1200.0, 0.0), 2, CellStatus.CLEARED)

    text = src.as_table(limit_cols=10)
    assert text.startswith("| Channel")
    assert '"status": "find"' in text
    assert '"status": "not find"' in text

    dst = KnowledgeMatrix(cell_m=120.0)
    written = dst.loads_table(text)
    assert written == 4
    assert dst.found(1) == 2
    assert dst.not_found(2) == 1
    assert dst.is_cleared(2)

    # 示向度能从 angle 区间还原
    ev = dst.evidence(1, dst.cell_key(Point(0.0, 0.0)))
    assert ev is not None and ev.bearing_deg == pytest.approx(12.5, abs=0.01)


def test_data_type_user_style_table_parses() -> None:
    """用户给的 ``dataType.txt`` 原样（含 ``not clear`` / ``not measure``）必须能解析。"""
    bs = chr(92)
    text = (
        f"| Channel{bs}Path | (-20, -20) | (1180, -20) |\n"
        "| - | - | - |\n"
        '| 1 | { "status": "find", "angle": { "lower": 2, "upper": 4 } } |'
        ' { "status": "find", "angle": { "lower": 236, "upper": 238 } } |\n'
        '| 2 | { "status": "not find", "angle": { "lower": 0, "upper": 359 } } |'
        ' { "status": "find", "angle": { "lower": 21, "upper": 27 } } |\n'
        '| 3 | { "status": "not clear", "angle": { "lower": 0, "upper": 359 } } |'
        ' { "status": "clear", "angle": { "lower": 0, "upper": 359 } } |\n'
        '| 4 | { "status": "not measure" } |'
        ' { "status": "find", "angle": { "lower": 236, "upper": 238 } } |\n'
    )
    m = KnowledgeMatrix()
    written = m.loads_table(text)
    assert written == 7, m.as_table(limit_cols=2)  # "not measure" 不计入单元格
    assert m.found(1) == 2
    assert m.not_found(2) == 1
    col0 = Point(-20.0, -20.0)
    assert m.status_at(col0, 3) is CellStatus.CLEAR_FAILED
    assert m.status_at(col0, 4) is CellStatus.NOT_MEASURED
    assert m.status_at(col0, 2) is CellStatus.NOT_FIND
    # 2 号频道第二格是 find，角区间 [21,27] -> 中心 24
    hits = [ev.bearing_deg for ev in m.cells[2].values() if ev.status is CellStatus.FIND]
    assert hits and hits[0] == pytest.approx(24.0, abs=0.01)


def test_radius_bounds_without_evidence_is_prior() -> None:
    m = KnowledgeMatrix()
    lower, upper = m.radius_bounds(4)
    assert lower == pytest.approx(1000.0)
    assert upper == pytest.approx(1500.0)


# ---------------------------------------------------------------------------
# 信念与覆盖证书
# ---------------------------------------------------------------------------
def _feed_ring_scan(matrix: KnowledgeMatrix, stops, channels=(1,), result=CellStatus.NOT_FIND):
    for stop in stops:
        for channel in channels:
            matrix.record_measure(stop, channel, result)


def test_negative_evidence_never_raises_plan_radius_above_lower_bound() -> None:
    """负例只能给出 R 的**上界**，绝不能拿它把规划半径抬到安全下界之上。

    评审 2.1 的反例：R=1000、真值在 (1020,0)、负例在原点。负例到可行域的
    *最短*距离≈0，历史实现据此把 R 估成小值、又按 ``max(R,1000)+40`` 放大
    排除圆盘，会把真值一起删掉。正确做法是：正例距离给**下界**、负例距离给
    **上界**，规划半径只准用下界。
    """
    m = KnowledgeMatrix(cell_m=120.0)
    m.record_measure(Point(0.0, 0.0), 3, CellStatus.FIND, bearing_deg=90.0)
    for stop in regular_polygon_scan_points(6, 1200.0, center=False):
        m.record_measure(stop, 3, CellStatus.NOT_FIND)
    beliefs = build_beliefs(m)
    belief = beliefs[3]
    assert belief.n_find == 1
    assert belief.n_not_find >= 3
    # 规划半径必须来自安全下界，且留了余量
    assert belief.plan_radius_m <= belief.radius_lo_m
    assert belief.plan_radius_m < belief.radius_lo_m  # 余量
    assert belief.radius_lo_m >= 1000.0
    assert belief.radius_hi_m <= 1500.0
    # 下界不超过上界（否则证据自相矛盾，应当由调用方按保守处理）
    assert belief.radius_lo_m <= belief.radius_hi_m


def test_radius_bounds_direction_counterexample() -> None:
    """评审 2.1 的正例/负例方向反例（纯几何，不经知识矩阵）。"""
    from mathmodel2026b.geometry import arena_polygon

    region = arena_polygon(1800.0)
    # 正例在 (0,0) 向 90° 看 -> 可行域是 x=0 上的一条射线区域；这里直接给整片区域
    lo, hi, _src = estimate_radius_bounds(
        region, [(Point(1000.0, 0.0), 0.0)], [Point(0.0, 0.0)]
    )
    # 正例到可行域的最小距离是 0（可行域含该点）-> 下界只能靠题目先验 1000
    assert lo == pytest.approx(1000.0)
    # 负例在原点：可行域内最远点离它 ≈1800m -> 上界被抬到 1500（题目上界）
    assert hi == pytest.approx(1500.0)

    # 真正压住上界的例子：让可行域很小、负例离它很近
    tiny = [
        Point(990.0, -5.0),
        Point(1000.0, -5.0),
        Point(1000.0, 5.0),
        Point(990.0, 5.0),
    ]
    lo2, hi2, _ = estimate_radius_bounds(tiny, [(Point(0.0, 0.0), 0.0)], [Point(1000.0, 0.0)])
    # max_{x∈P}|x - q| = |(990,-5)-(1000,0)| = sqrt(125) ≈ 11.18m < 题目下界 1000m
    # -> 上界被"R >= 1000"这条先验截断（说明这组证据与先验冲突，不能据此放宽半径）
    assert max_distance_to_region(Point(1000.0, 0.0), tiny) == pytest.approx(
        math.hypot(10.0, 5.0), abs=0.01
    )
    assert hi2 == pytest.approx(1000.0)
    assert lo2 == pytest.approx(1000.0)
    assert lo2 <= hi2  # 区间永不倒挂


def test_seven_point_cover_passes_certificate() -> None:
    """中心 + 正六边形 r=1200 的覆盖半径 ≤ 1000m，证书必须判"完备"。"""
    grid = CoverageGrid(cell_m=60.0)
    matrix = KnowledgeMatrix(cell_m=120.0)
    tracker = CoverageTracker(grid, matrix)
    beliefs = build_beliefs(matrix)  # 全部未检出 -> 整片区域
    tracker.sync(beliefs, matrix=matrix)
    stops = regular_polygon_scan_points(6, 1200.0, center=True)
    tracker.observe_stops(stops)
    report = tracker.assess()
    assert report.complete, report.as_dict()


def test_single_vs_seven_stops_certificate() -> None:
    grid = CoverageGrid(cell_m=60.0)
    matrix = KnowledgeMatrix(cell_m=120.0)
    tracker = CoverageTracker(grid, matrix)
    tracker.sync(build_beliefs(matrix), matrix=matrix)
    tracker.observe_stops([Point(0.0, 0.0)])
    assert not tracker.assess().complete
    tracker.observe_stops(regular_polygon_scan_points(6, 1200.0, center=False))
    assert tracker.assess().complete


def test_planner_shrinks_gap() -> None:
    """数据驱动规划：一路补测必须能把"未确认面积"降到 0。

    注意不能断言每步单调下降：新增停点同时也会扩张"已确认区域"，
    缺口（区域 − 已确认）在个别步上可能反弹。这里断言的是"能收敛到完备"。
    """
    grid = CoverageGrid(cell_m=60.0)
    matrix = KnowledgeMatrix(cell_m=120.0)
    tracker = CoverageTracker(grid, matrix)
    tracker.sync(build_beliefs(matrix), matrix=matrix)
    position = Point(0.0, 0.0)
    assert tracker.assess(position).total_uncovered_cells > 0
    moved = 0.0
    for _ in range(40):
        candidate = tracker.plan_cover(position)
        if candidate is None:
            break
        moved += candidate.travel_m
        position = candidate.point
        tracker.observe_stops([position])
        if tracker.assess(position).complete:
            break
    assert tracker.assess(position).complete, tracker.assess(position).as_dict()
    assert moved > 0.0


def test_point_in_polygon_and_distance() -> None:
    square = [Point(-10, -10), Point(10, -10), Point(10, 10), Point(-10, 10)]
    assert point_in_polygon(Point(0, 0), square)
    assert not point_in_polygon(Point(20, 0), square)
    assert distance_point_to_region(Point(0, 0), square) == 0.0
    assert distance_point_to_region(Point(20, 0), square) == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# 定向源几何（问题4）：评审 2.1 / 2.2 的每一个反例都变成回归测试
# ---------------------------------------------------------------------------
def _idx(grid: CoverageGrid, point: Point) -> int:
    return grid.index(*grid.to_ij(point))


def test_directional_joint_heading_counterexample_from_review() -> None:
    """评审 2.2 反例：源在原点、朝向 80°，测点方位 0° 与 -20°、距离 500m。

    历史实现按"负例方向与**每个**正例方向点积非负 ⇒ 必定可见"逐点判断，
    会把这个位置证伪；但朝向 80° 的源看不到 -20° 那个点（夹角 100° > 90°），
    所以只要**同一个朝向**能解释全部观测，该位置就必须保留。

    这里直接检验朝向可行性判据（不经过栅格，避免格心偏移额外引入半径约束）。
    """
    from mathmodel2026b.coverage import _has_feasible_heading

    near_pos = [(500.0, 0.0), (500.0, -180.0)]  # x→正例 的向量
    blocking = [(500.0, -180.0)]
    assert _has_feasible_heading(near_pos, blocking, 1.0)
    # 显式验证朝向 80°：看得到 (500,0)，看不到 (500,-180)
    u = (math.cos(math.radians(80.0)), math.sin(math.radians(80.0)))
    assert u[0] * 500.0 + u[1] * 0.0 > 0.0
    assert u[0] * 500.0 + u[1] * (-180.0) < 0.0
    # 对照：0°/120°/240° 三个等距方向 —— 没有朝向能同时避开三者
    triangle = [(500.0, 0.0), (-250.0, 433.0127), (-250.0, -433.0127)]
    assert not _has_feasible_heading(triangle, triangle, 1.0)


def test_directional_three_surrounding_probes_falsify_center() -> None:
    """评审 2.2：0°/120°/240° 三个足够近的测点，没有朝向能同时避开三者。

    所以"任意有限测点都可以被一个定向源背对"是错的；这三点的**凸包含原点**，
    正是朝向覆盖充分条件的来源。栅格判据必须把原点所在格删掉。
    """
    grid = CoverageGrid(cell_m=60.0)
    origin = Point(0.0, 0.0)
    probes = [Point(500.0, 0.0), Point(-250.0, 433.0127), Point(-250.0, -433.0127)]
    assert heading_cover_condition(origin, probes)
    mask = plausible_directional_mask(
        grid, [(q, q.bearing_to(origin)) for q in probes], probes
    )
    assert not (mask >> _idx(grid, origin)) & 1


def test_directional_radius_must_be_joint() -> None:
    """评审 2.2：半径 R 也必须与朝向一起联合解释，不能"见负例就证伪"。

    * 负例比所有正例都近（1000m 正例、600m 负例）：不存在能同时解释两者的 R，
      因此该候选位置**必须**被删掉 —— 这条约束来自半径，不是朝向。
    * 负例更远（1000m 正例、1400m 负例）：R 取 1000~1400 即可解释"收不到"，
      位置必须保留。
    """
    grid = CoverageGrid(cell_m=60.0)
    origin = Point(0.0, 0.0)
    mask = plausible_directional_mask(
        grid, [(Point(1000.0, 0.0), 0.0)], [Point(600.0, 0.0)]
    )
    assert not (mask >> _idx(grid, origin)) & 1
    mask2 = plausible_directional_mask(
        grid, [(Point(1000.0, 0.0), 0.0)], [Point(1400.0, 0.0)]
    )
    assert (mask2 >> _idx(grid, origin)) & 1


def test_directional_far_field_negative_never_excludes() -> None:
    """远场负例（>1500m）与锥朝向无关，不能用来证伪。"""
    grid = CoverageGrid(cell_m=60.0)
    finds = [(Point(1000.0, 0.0), 0.0)]
    mask = plausible_directional_mask(grid, finds, [Point(5000.0, 0.0)])
    assert mask == grid.mask_inside_arena()


def test_directional_without_finds_keeps_everything_plausible() -> None:
    """没有正例时锥朝向完全未知 —— 判据必须保守（一个格都不删）。"""
    grid = CoverageGrid(cell_m=60.0)
    mask = plausible_directional_mask(grid, [], [Point(0.0, 0.0), Point(300.0, 300.0)])
    assert mask == grid.mask_inside_arena()


def test_directional_negative_behind_positive_is_kept() -> None:
    """负例在正例的反方向（且比正例更远）：源可以把锥背对它，不能证伪。"""
    grid = CoverageGrid(cell_m=60.0)
    origin = Point(0.0, 0.0)
    mask = plausible_directional_mask(
        grid, [(Point(1000.0, 0.0), 0.0)], [Point(-1400.0, 0.0)]
    )
    assert (mask >> _idx(grid, origin)) & 1


def test_hidden_mask_matches_plausible_semantics() -> None:
    """``hidden_mask`` = "所有已测停点都照不到"的位置（决定去哪补测）。"""
    grid = CoverageGrid(cell_m=60.0)
    finds = [(Point(1000.0, 0.0), 0.0)]
    # 正例在东侧意味着源在原点附近时朝向朝东；(-800,0) 在这个朝向下看不到 -> 盲区
    hidden = hidden_mask(grid, finds, [Point(-800.0, 0.0)])
    assert (hidden >> _idx(grid, Point(0.0, 0.0))) & 1
    # 没有正例时整片区域都是盲区（只能靠覆盖整个区域兜底）
    assert hidden_mask(grid, [], [Point(0.0, 0.0)]) == grid.mask_inside_arena()


# ---------------------------------------------------------------------------
# 朝向覆盖设计：把 Q4 从"圆盘覆盖"升级成"距离范围内的朝向覆盖"
# ---------------------------------------------------------------------------
def test_heading_cover_condition_is_convex_hull_membership() -> None:
    """凸包内 ⇒ 成立；凸包外 ⇒ 不成立；再叠加距离门槛。"""
    triangle = [Point(1000.0, 0.0), Point(-500.0, 866.0), Point(-500.0, -866.0)]
    assert min_convex_distance(Point(0.0, 0.0), triangle) == pytest.approx(0.0, abs=1e-6)
    assert heading_cover_condition(Point(0.0, 0.0), triangle)
    assert min_convex_distance(Point(1500.0, 0.0), triangle) == pytest.approx(500.0, abs=1e-6)
    assert not heading_cover_condition(Point(1500.0, 0.0), triangle)
    # 距离门槛：凸包顶点太远就不算"能看见"（真实 R 可能只有 1000m）
    assert not heading_cover_condition(Point(0.0, 0.0), triangle, radius_m=400.0)


def test_axial_heading_cover_layout_is_complete() -> None:
    """轴向三角网格 s=950：31 点，任意朝向下都能被发现（数值抽样验证）。

    对照：中心 + 正七边形 r=1110（现行 ``scan_sides=7`` 布局）不是朝向完备的，
    边界朝外的定向源会漏 —— 这正是 paper-q4 能 30/30 全清、而框架策略漏源
    的根因。
    """
    stops = heading_cover_layout(950.0)
    assert len(stops) == 31
    report = heading_cover_report(stops, step_m=80.0)
    assert report["worst_hull_m"] <= 1e-6, report

    seven = regular_polygon_scan_points(7, 1110.0, center=True)
    rep7 = heading_cover_report(seven, step_m=80.0)
    assert rep7["worst_hull_m"] > 1.0, rep7
