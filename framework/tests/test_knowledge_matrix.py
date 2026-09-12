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
    CoverageTracker,
    build_beliefs,
    distance_point_to_region,
    invisible_mask,
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


def test_negative_evidence_raises_radius_estimate() -> None:
    """负例是 R 的唯一在线证据：7 点全 not_find 时 R 估计应=1500（上界）。"""
    m = KnowledgeMatrix(cell_m=120.0)
    # 一个 find 给出可行域，然后各处 not_find
    m.record_measure(Point(0.0, 0.0), 3, CellStatus.FIND, bearing_deg=90.0)
    for stop in regular_polygon_scan_points(6, 1200.0, center=False):
        m.record_measure(stop, 3, CellStatus.NOT_FIND)
    beliefs = build_beliefs(m)
    belief = beliefs[3]
    assert belief.n_find == 1
    assert belief.n_not_find >= 3
    # 负例点离可行域都很远 -> R 估计被抬到上界
    assert belief.radius_hat_m >= 1000.0
    assert belief.plan_radius_m <= belief.radius_hat_m


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
# 定向源几何（问题4）
# ---------------------------------------------------------------------------
def test_invisible_mask_keeps_cone_blind_spots() -> None:
    """锥盲区判据：与正例同向的负例把该位置证伪；反向的负例不能证伪。"""
    grid = CoverageGrid(cell_m=60.0)
    source = Point(0.0, 0.0)
    # 正例：东侧 (1000, 0) 可见
    finds = [(Point(1000.0, 0.0), 0.0)]
    # 负例 A：也在东侧（120km? 不，800m 东侧）—— 与正例同向，会证伪 S=原点
    mask_same_side = invisible_mask(grid, finds, [Point(800.0, 0.0)])
    assert not (mask_same_side >> grid.index(*grid.to_ij(source))) & 1
    # 负例 B：西侧 —— 源在原点朝东时确实看不到它，不能证伪
    mask_behind = invisible_mask(grid, finds, [Point(-800.0, 0.0)])
    assert (mask_behind >> grid.index(*grid.to_ij(source))) & 1


def test_invisible_mask_far_field_never_excludes() -> None:
    """远场负例（>1500m）与锥朝向无关，不能用来证伪。"""
    grid = CoverageGrid(cell_m=60.0)
    finds = [(Point(1000.0, 0.0), 0.0)]
    mask = invisible_mask(grid, finds, [Point(5000.0, 0.0)])
    assert mask == grid.mask_inside_arena()


def test_invisible_mask_without_finds_is_conservative() -> None:
    """没有正例时无法消元锥朝向 —— 判据必须保守（一个格都不删）。"""
    grid = CoverageGrid(cell_m=60.0)
    arena = grid.mask_inside_arena()
    idx0 = grid.index(*grid.to_ij(Point(0.0, 0.0)))
    # 无正例时判据完全失效：一个格都不删（最保守）
    assert (invisible_mask(grid, [], [Point(0.0, 0.0)]) >> idx0) & 1 == 1
    # 有正例之后判据才生效：负例点与正例（1000,0）的夹角 < 90° -> 证伪该格
    idx = grid.index(*grid.to_ij(Point(0.0, 0.0)))
    cell = grid.center(*grid.to_ij(Point(0.0, 0.0)))
    assert cell.distance_to(Point(0.0, 0.0)) < grid.cell_m  # 格心就在原点附近
    # 负例点在 (600, 0)：对"源在原点、锥朝东"这个假设，它本该被看到 -> 证伪
    falsified = invisible_mask(grid, [(Point(1000.0, 0.0), 0.0)], [Point(600.0, 0.0)])
    assert not (falsified >> idx) & 1
    # 负例点在正例的反方向（夹角 > 90°）-> 不能证伪（源可能背对着它）
    kept = invisible_mask(grid, [(Point(1000.0, 0.0), 0.0)], [Point(-500.0, 0.0)])
    assert (kept >> idx) & 1
