"""知识矩阵（``knowledge.py``）接入 v14/v17/v18 系列的适配层。

为什么需要这一层
----------------
``strategy_matrix.py`` 的 ``KnowledgeSearchStrategy`` 自带一整套 ``channel × path``
观测台账，但它是**独立的一条技术路线**（自带布局、自带调度），与 v8→v9→v14→v17
这条"迭代优化版"血脉不共享任何代码。于是矩阵里最有价值的两样东西在迭代版里缺席：

1. **失败清除的 20m 排除圆**。题目附件1 §2.3 明确："只要 20m 范围内有指定频道的
   干扰源，就一定能精确定位并清除。" 它的逆否命题是：``/clear`` 返回"未发现"
   ⟹ **以清除点为心、20m 为半径的闭圆盘内没有该频道的源**。
   注意这条信息**与源的朝向无关**（光学/激光，不是测向），因此它对定向源同样
   严格成立 —— 而迭代版唯一在用的负例是 ``no_signal``，对定向源只是"超出半径
   或落在波束背面"的析取，只能给出 1000~1500m 级的弱排除，还容易被射线推进
   误用（见 ``docs/review-fixes-2026-09-13.md`` §3）。
2. **按格去重与"哪些格还没看过"的索引**。``KnowledgeMatrix`` 已经把路径点量化成
   120m 的列，"同一格重复测无信息"变成 O(1) 查询。

本模块只做两件事，且**不改变**任何既有策略的可行域口径：

* :class:`ChannelKnowledge` —— 把 v* 策略的 ``_measure`` / ``_clear`` 观测喂进
  ``KnowledgeMatrix``，并对外提供"已确认无源的圆盘集合"。
* :func:`region_clear_plan` —— 把"可行域 F 用半径 ``tol`` 的清除圆覆盖"做成一个
  **可证明充分**的离散计划：25m 方格 + 双角点判据。

覆盖保证的论证（``step = 25``、``tol = 20``）
--------------------------------------------
记方格边长 ``s``，清除圆半径 ``tol``。取所有满足
``dist(center(c), F) <= s/√2`` 的格 ``c``（``F`` 是保守可行域，真值必在其中）：

* **覆盖**：设 ``p ∈ F``。``p`` 落在它的本格 ``c_p`` 内，故
  ``dist(center(c_p), F) <= dist(center(c_p), p) <= s/√2``，于是 ``c_p`` 被选中，
  而 ``|center(c_p) − p| <= s/√2 = 17.678 < 20 = tol``。∎
* **剔除**：格 ``c`` 若**四个角点**都在某个失败清除圆 ``B(q, tol)`` 内，则整个
  方格都在该圆内（范数是凸函数，在正方形上的最大值必在顶点取得），而该圆内
  确定无源，故 ``c`` 可安全丢弃。∎

两条都是充分条件而非启发式：只要计划被完整执行且 ``unreachable == 0``，
``guaranteed`` 即为 ``True``，即"真值必被某一次 ``/clear`` 命中并被清除"。

代价模型（"两次 clear 可能比 measure 再 clear 更便宜"）
-------------------------------------------------------
一次失败 ``/clear`` 只花 3s（光学精确定位），成功才再花 2s（激光）；
一次 ``/measure`` 花 5s，换频道再加 1s，而且**它不保证能清除**。
于是对 k 个清除点的保证性计划：

========  ==================  ==========================
k         clear 计划固定耗时   一次 measure + 一次 clear
========  ==================  ==========================
1         3 + 2 = 5s          5 + 1 + 5 = 11s
2         3·2 + 2 = 8s        11s
3         3·3 + 2 = 11s       11s
========  ==================  ==========================

k ≤ 2 时计划在**固定耗时**上严格占优，且带保证；k ≥ 3 时打平，要再看路程。
:func:`plan_beats_measure` 用"路程折算成秒"把这条判据写完整：

.. math::

    \\frac{L_{plan}}{v} + 3k + 2 \\;\\le\\;
    \\frac{L_{measure}}{v} + 5 + s_{switch} + 3 + 2

右边是"先测一次、再在估计点清一次"的**乐观**代价（假设那一测直接把源定到
20m 内、那次清除命中）。计划只要不劣于这个下界，就应该先执行计划。

历史口径
--------
"测量 5s / 失败清除 5s"是 2026-09-13 之前 mock 的错误计时（``World`` 无条件
``+3+2``）。修正后失败清除是 3s，本模块的判据才成立；用
``World(legacy_clear_timing=True)`` 可以复现旧数字，但那时的 k≤2 优势会被抹平。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .geometry import Point
from .knowledge import CellStatus, KnowledgeMatrix
from .protocol import (
    ARENA_RADIUS_M,
    CHANNEL_SWITCH_SECONDS,
    CLEAR_SECONDS,
    MEASURE_SECONDS,
    MOVE_SPEED_MPS,
    OPTICAL_RANGE_M,
    OPTICAL_SECONDS,
)

__all__ = [
    "ChannelKnowledge",
    "RegionClearPlan",
    "cells_covering_region",
    "plan_beats_measure",
    "region_clear_plan",
]

#: 方格边长默认值（米）。必须满足 ``s / √2 <= OPTICAL_RANGE_M`` 才有覆盖保证：
#: ``25/√2 = 17.678 <= 20``，留 2.32m 余量（足够吸收示向度 0.01° 序列化与
#: 浮点误差带来的可行域抖动）。
DEFAULT_CELL_STEP_M: float = 25.0

_EPS: float = 1e-7


# ---------------------------------------------------------------------------
# 知识矩阵适配层
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class ChannelKnowledge:
    """把 v* 系列的观测喂进 ``KnowledgeMatrix``，并给出"确定无源"的排除圆。

    ``KnowledgeMatrix`` 的 120m 量化只用于"同一路径格是否测过"的索引；
    **所有关于源位置的推断仍然走连续可行域**（``strategy._region``），
    因此这里不会把量化误差引入 20m 判据。
    """

    #: 路径列量化步长（米），与 ``dataType.txt`` 口径一致
    cell_m: float = 120.0
    matrix: KnowledgeMatrix = field(init=False)

    def __post_init__(self) -> None:
        self.matrix = KnowledgeMatrix(cell_m=float(self.cell_m))

    # -- 写入 -----------------------------------------------------------
    def record_measure(
        self,
        point: Point,
        channel: int,
        *,
        kind: str,
        bearing_deg: float | None = None,
        virtual_time_s: float = 0.0,
    ) -> None:
        """记录一次 ``/measure``。``kind`` 取协议里的 ``direction``/``near``/``no_signal``。"""
        status = _MEASURE_STATUS.get(kind)
        if status is None:
            return
        self.matrix.record_measure(
            point,
            channel,
            status,
            bearing_deg=bearing_deg if status is CellStatus.FIND else None,
            virtual_time_s=virtual_time_s,
        )

    def record_clear_failed(self, point: Point, channel: int, virtual_time_s: float = 0.0) -> None:
        """记录一次**失败**的 ``/clear``（附件1 §2.3 的 20m 强负例）。"""
        self.matrix.record_clear_failed(point, channel, virtual_time_s=virtual_time_s)

    def record_clear_success(self, point: Point, channel: int, virtual_time_s: float = 0.0) -> None:
        self.matrix.mark_channel_cleared(channel)
        self.matrix.record_measure(
            point, channel, CellStatus.CLEARED, virtual_time_s=virtual_time_s
        )

    # -- 读取 -----------------------------------------------------------
    def measured_before(self, point: Point, channel: int) -> bool:
        """该格该频道是否已测过（同格重测读数不变，无信息量）。"""
        return self.matrix.is_measured(point, channel)

    def exclusion_points(self, channel: int) -> list[Point]:
        """确定"周围 20m 内无该频道源"的清除失败点。"""
        return self.matrix.observation_points(channel, CellStatus.CLEAR_FAILED)

    def exclusion_disks(
        self, channel: int, radius_m: float = OPTICAL_RANGE_M
    ) -> list[tuple[Point, float]]:
        """``(圆心, 半径)`` 形式的排除圆盘；圆心内 20m 确定无源。"""
        return [(q, float(radius_m)) for q in self.exclusion_points(channel)]

    def not_find_points(self, channel: int) -> list[Point]:
        """``no_signal`` 的检测点（全向源时是"距离 > R"的弱负例，定向源时必须保守处理）。"""
        return self.matrix.observation_points(channel, CellStatus.NOT_FIND)

    def is_cleared(self, channel: int) -> bool:
        return self.matrix.is_cleared(channel)

    # -- 导出 -----------------------------------------------------------
    def table(self, **kwargs) -> str:
        return self.matrix.as_table(**kwargs)

    def to_dict(self, **kwargs) -> dict:
        return self.matrix.as_dict(**kwargs)

    def summary_line(self) -> str:
        return self.matrix.summary_line()


_MEASURE_STATUS: dict[str, CellStatus] = {
    "direction": CellStatus.FIND,
    "near": CellStatus.NEAR,
    "no_signal": CellStatus.NOT_FIND,
}


# ---------------------------------------------------------------------------
# 可行域覆盖式清除计划
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class RegionClearPlan:
    """把保守可行域 ``F`` 用半径 ``radius_m`` 的清除圆覆盖的一次性计划。"""

    centers: list[Point]
    radius_m: float
    step_m: float
    #: 与 ``F`` 相交（即 `dist(center, F) <= step/√2`）的格数
    cells_total: int
    #: 被失败清除圆**完全**包含而安全剔除的格数
    cells_excluded: int
    #: 格心不可达（超出竞技场可达范围）的格数；> 0 则 :attr:`guaranteed` 为 False
    cells_unreachable: int
    #: 从起点依次走完全部清除点的路程（米）
    travel_m: float
    #: 计划是否被完整执行（预算未中断）
    completed: bool = True
    #: 计划是否给出"真值必被命中"的充分保证
    guaranteed: bool = True

    @property
    def n_probes(self) -> int:
        return len(self.centers)

    def probe_cost_s(self) -> float:
        """固定耗时：k 次失败光学 3s + 命中的那一次再 2s（题目保证必有一次命中）。"""
        if not self.centers:
            return 0.0
        return OPTICAL_SECONDS * len(self.centers) + CLEAR_SECONDS

    def total_cost_s(self, speed_mps: float = MOVE_SPEED_MPS) -> float:
        return self.travel_m / speed_mps + self.probe_cost_s()

    def as_dict(self) -> dict:
        return {
            "n_probes": self.n_probes,
            "radius_m": self.radius_m,
            "step_m": self.step_m,
            "cells_total": self.cells_total,
            "cells_excluded": self.cells_excluded,
            "cells_unreachable": self.cells_unreachable,
            "travel_m": round(self.travel_m, 3),
            "probe_cost_s": self.probe_cost_s(),
            "total_cost_s": round(self.total_cost_s(), 3),
            "completed": self.completed,
            "guaranteed": self.guaranteed,
        }


def _is_within_reach(point: Point, factor: float) -> bool:
    return math.hypot(point.x, point.y) <= ARENA_RADIUS_M * factor


def _cell_fully_inside_disk(
    cx: float, cy: float, half: float, disk: tuple[Point, float]
) -> bool:
    """方格四角是否都在圆盘内 ⟹ 整个方格在圆盘内（范数凸性，最大值在顶点处取得）。"""
    q, r = disk
    r2 = (r + _EPS) ** 2
    for dx in (-half, half):
        for dy in (-half, half):
            if (cx + dx - q.x) ** 2 + (cy + dy - q.y) ** 2 > r2:
                return False
    return True


def _point_poly_min_dist(p: Point, poly: Sequence[Point]) -> float:
    """点 ``p`` 到凸多边形的最小距离（内部为 0）。"""
    n = len(poly)
    if n < 3:
        return math.inf
    inside = True
    sign = 0
    best = math.inf
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        cross = (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x)
        if abs(cross) >= 1e-9:
            s = 1 if cross > 0 else -1
            if sign == 0:
                sign = s
            elif s != sign:
                inside = False
        # 点到线段的距离
        dx, dy = b.x - a.x, b.y - a.y
        seg2 = dx * dx + dy * dy
        if seg2 <= 1e-18:
            d = p.distance_to(a)
        else:
            t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / seg2
            t = max(0.0, min(1.0, t))
            d = math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy))
        if d < best:
            best = d
    if inside:
        return 0.0
    return best


def _point_in_convex(p: Point, poly: Sequence[Point]) -> bool:
    """点是否在凸多边形内（含边界）。"""
    sign = 0
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        cross = (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x)
        if abs(cross) < 1e-9:
            continue
        s = 1 if cross > 0 else -1
        if sign == 0:
            sign = s
        elif s != sign:
            return False
    return True


def _segments_cross(
    p1: Point, p2: Point, p3: Point, p4: Point
) -> bool:
    """线段 p1p2 与 p3p4 是否相交（含端点接触）。"""
    def orient(a: Point, b: Point, c: Point) -> float:
        return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)

    d1 = orient(p3, p4, p1)
    d2 = orient(p3, p4, p2)
    d3 = orient(p1, p2, p3)
    d4 = orient(p1, p2, p4)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return True
    return False


def _square_intersects_polygon(
    cx: float, cy: float, half: float, poly: Sequence[Point]
) -> bool:
    """轴对齐方格与凸多边形是否有公共点（精确判据，比"格心到多边形距离"紧得多）。

    覆盖论证只需要**与区域相交的格**被选中：设 ``p ∈ F``，则 ``p`` 落在自己的
    本格 ``c_p`` 内，故 ``c_p ∩ F ≠ ∅``，``c_p`` 必被选中，而
    ``|center(c_p) − p| <= half·√2 < tol``。用精确相交判据可以少选一大圈
    "格心落在区域内 17.7m"的邻格，格数下降约 3 倍。
    """
    corners = [
        Point(cx - half, cy - half),
        Point(cx + half, cy - half),
        Point(cx + half, cy + half),
        Point(cx - half, cy + half),
    ]
    # 1) 多边形任一顶点在方格内
    for v in poly:
        if cx - half - _EPS <= v.x <= cx + half + _EPS and cy - half - _EPS <= v.y <= cy + half + _EPS:
            return True
    # 2) 方格任一角点在多边形内
    for c in corners:
        if _point_in_convex(c, poly):
            return True
    # 3) 边相交
    for i in range(4):
        a, b = corners[i], corners[(i + 1) % 4]
        for j in range(len(poly)):
            if _segments_cross(a, b, poly[j], poly[(j + 1) % len(poly)]):
                return True
    return False


def cells_covering_region(
    region: Sequence[Point],
    *,
    step_m: float = DEFAULT_CELL_STEP_M,
    exclusions: Iterable[tuple[Point, float]] = (),
    reach_factor: float = 1.35,
) -> tuple[list[tuple[float, float]], int, int, int]:
    """枚举覆盖凸多边形 ``region`` 的格心。

    返回 ``(centers, cells_total, cells_excluded, cells_unreachable)``：

    * ``centers`` —— 需要依次清除的格心（``cells_total − cells_excluded −
      cells_unreachable`` 个）。
    * ``cells_total`` —— 与 ``region`` 相交的格数（覆盖所需的**全部**格）。
    * ``cells_excluded`` —— 被失败清除圆完全包含、可安全丢弃的格数。
    * ``cells_unreachable`` —— 格心超出可达范围、无法作为清除点的格数。
    """
    if len(region) < 3:
        return [], 0, 0, 0

    xs = [v.x for v in region]
    ys = [v.y for v in region]
    half = step_m / 2.0

    # 格网对齐到可行域包围盒的**下界**（而不是居中）：任意平移后的格网仍是
    # 合法格网，覆盖论证不变，但格数从 ceil(span/step)+1 降到 ceil(span/step)。
    ox = min(xs)
    oy = min(ys)
    nx = max(1, math.ceil((max(xs) - ox) / step_m)) if max(xs) > ox else 1
    ny = max(1, math.ceil((max(ys) - oy) / step_m)) if max(ys) > oy else 1

    disks = list(exclusions)
    centers: list[tuple[float, float]] = []
    total = excluded = unreachable = 0

    for i in range(nx):
        cx = ox + (i + 0.5) * step_m
        for j in range(ny):
            cy = oy + (j + 0.5) * step_m
            if not _square_intersects_polygon(cx, cy, half, region):
                continue
            total += 1
            if any(_cell_fully_inside_disk(cx, cy, half, d) for d in disks):
                excluded += 1
                continue
            if not _is_within_reach(Point(cx, cy), reach_factor):
                unreachable += 1
                continue
            centers.append((cx, cy))
    return centers, total, excluded, unreachable


def _nearest_neighbour_order(
    centers: Sequence[tuple[float, float]], start: Point
) -> tuple[list[Point], float]:
    """最近邻访问序 + 总路程（格数很少时够用；不追求 TSP 最优）。"""
    remaining = [Point(x, y) for (x, y) in centers]
    ordered: list[Point] = []
    travel = 0.0
    cur = start
    while remaining:
        k = min(range(len(remaining)), key=lambda i: cur.distance_to(remaining[i]))
        nxt = remaining.pop(k)
        travel += cur.distance_to(nxt)
        ordered.append(nxt)
        cur = nxt
    return ordered, travel


def region_clear_plan(
    region: Sequence[Point],
    start: Point,
    *,
    tol_m: float = OPTICAL_RANGE_M,
    step_m: float | None = None,
    exclusions: Iterable[tuple[Point, float]] = (),
    max_probes: int | None = None,
    reach_factor: float = 1.35,
) -> RegionClearPlan | None:
    """构造"覆盖保守可行域"的清除计划；格数超过 ``max_probes`` 时返回 ``None``。

    ``tol_m`` 是清除半径（题目 20m）。``step_m`` 缺省取
    ``min(DEFAULT_CELL_STEP_M, tol_m * √2)``，保证 ``step/√2 <= tol``。
    """
    if len(region) < 3:
        return None
    if step_m is None:
        step_m = min(DEFAULT_CELL_STEP_M, tol_m * math.sqrt(2.0))
    if step_m / math.sqrt(2.0) > tol_m + _EPS:
        raise ValueError(
            f"step_m={step_m} 不满足 step/√2 <= tol_m={tol_m}，覆盖保证不成立"
        )

    cells, total, excluded, unreachable = cells_covering_region(
        region, step_m=step_m, exclusions=exclusions, reach_factor=reach_factor
    )
    if not cells:
        return None
    if max_probes is not None and len(cells) > max_probes:
        return None

    ordered, travel = _nearest_neighbour_order(cells, start)
    return RegionClearPlan(
        centers=ordered,
        radius_m=float(tol_m),
        step_m=float(step_m),
        cells_total=total,
        cells_excluded=excluded,
        cells_unreachable=unreachable,
        travel_m=travel,
        guaranteed=unreachable == 0,
    )


def plan_beats_measure(
    plan: RegionClearPlan,
    *,
    measure_travel_m: float,
    switch: bool = True,
    speed_mps: float = MOVE_SPEED_MPS,
) -> bool:
    """代价判据：先执行清除计划，是否不劣于"再测一次、然后清一次"。

    右边是**乐观下界**（假设那一测直接把源定到 20m 内、那次清除命中）：

    .. math::

        L_{plan}/v + 3k + 2 \\;\\le\\;
        L_{measure}/v + 5 + s_{switch} + 3 + 2

    实际情况下"再测一次"往往还要再走一趟、再清一次（甚至多次），所以这个判据
    是保守的：判据为真时，计划在**固定耗时**上已经不吃亏，而且它带覆盖保证。
    """
    if plan is None or not plan.centers:
        return False
    switch_s = CHANNEL_SWITCH_SECONDS if switch else 0.0
    measure_side = (
        measure_travel_m / speed_mps
        + MEASURE_SECONDS
        + switch_s
        + OPTICAL_SECONDS
        + CLEAR_SECONDS
    )
    return plan.total_cost_s(speed_mps) <= measure_side + _EPS
