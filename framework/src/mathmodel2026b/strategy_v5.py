"""问题3 的 v5 优化策略（在 ``Q3Strategy`` 上做增量，不改基线，便于 A/B）。

三个杠杆（按预期收益排序）：

1. **扫描阶段检测预算剪枝**（``scan_probe_limit``）
   基线在 7 个扫描点上把 20 个频道**每个点都测一遍**（≈140 次检测 = 700s + 约 130s
   切换），而其中真正有源的只有 10~16 个频道、且每个源只需要 2 条分离读数就能进入
   精定位。达到 ``scan_probe_limit`` 条读数后立即停止扫描该频道，把预算留给收尾。

2. **扫描布局换成中心 + 正 n 边形环**（``scan_sides`` / ``scan_radius``）
   覆盖半径 = max(ρ·sin(π/n), √(ω²+ρ²-2ωρcos(π/n)))，行程 = ρ + (n-1)·2ρ·sin(π/n)。
   基线是"中心 + 六边形 r=1200"（覆盖 968.9m，行程 7200m）；
   中心 + 正七边形 r=1110（覆盖 933.7m，行程 6889m）在**覆盖余量更大**的同时更短。
   用 ``geometry.polygon_layout_cover_radius`` 可离线复核任意组合。

3. **顺路清除**（``clear_while_scanning``）
   扫描途中若某频道可行域已收敛到"瞄圆心必命中"（MEC ≤ 20m），就地清掉，
   省掉收尾阶段为它专程跑一趟的往返路程。
"""

# NOTE(端口适配): 用显式两参 super(Cls, self) 而不是零参 super()。
# @dataclass(slots=True) 会为子类重建类对象，零参 super() 依赖的 __class__
# 单元格会指向**原始**类，于是 'Q3V8Params' 实例在 Q3V5Params 的方法里调用
# super() 时会抛 TypeError（CPython 文档 dataclasses 章节明确警告）。
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .client import SimulatorClient
from .geometry import Point, regular_polygon_scan_points
from .state import DogState
from .strategy import Q3Params, Q3Strategy


def _point_seg_dist(p: Point, a: Point, b: Point) -> float:
    """点 p 到线段 ab 的距离。"""
    dx, dy = b.x - a.x, b.y - a.y
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return p.distance_to(a)
    t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / (dx * dx + dy * dy)
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return p.distance_to(Point(a.x + t * dx, a.y + t * dy))


def _point_poly_min_dist(p: Point, poly: list[Point]) -> float:
    """点 p 到凸多边形的最小距离（内部为 0）。"""
    n = len(poly)
    inside = True
    sign = 0
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        cross = (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x)
        if abs(cross) < 1e-9:
            continue
        s = 1 if cross > 0 else -1
        if sign == 0:
            sign = s
        elif s != sign:
            inside = False
            break
    if inside:
        return 0.0
    return min(_point_seg_dist(p, poly[i], poly[(i + 1) % n]) for i in range(n))


@dataclass(slots=True)
class Q3V5Params(Q3Params):
    #: 扫描布局：环上顶点数（中心点恒定参与）
    scan_sides: int = 7
    #: 扫描布局：环半径（米）
    scan_radius: float = 1110.0
    #: 扫描阶段每个频道最多取几条读数，达到即交给收尾阶段精定位
    scan_probe_limit: int = 2
    #: 扫描途中已可确定性清除的频道，就地清掉
    clear_while_scanning: bool = True
    #: 收尾阶段精定位最大轮数（基线 6）
    max_refine_rounds: int = 4
    #: 扫描时按"距离上界"剪枝：若当前点到该频道可行域整体的最小距离 > 1500m
    #: （有效接收半径上界），则该次检测**必然**是 no_signal，可直接跳过。
    #: 这是可证明安全的剪枝（可行域必定含真值），不损失任何信息。
    prune_by_range: bool = False

    def as_dict(self) -> dict[str, Any]:
        data = super(Q3V5Params, self).as_dict()
        data["scan_layout"] = f"center+{self.scan_sides}gon@{self.scan_radius:g}"
        return data


class Q3V5Strategy(Q3Strategy):
    """v5：布局可调 + 检测预算剪枝 + 顺路清除。"""

    def __init__(self, params: Q3V5Params | None = None) -> None:
        self.params: Q3V5Params = params or Q3V5Params()
        super(Q3V5Strategy, self).__init__(self.params)

    # -- 布局 -------------------------------------------------------------
    def _scan_points(self) -> list[Point]:
        p = self.params
        return regular_polygon_scan_points(p.scan_sides, p.scan_radius, center=True)

    def scan_plan(self) -> dict[str, float]:
        """离线自检：覆盖半径与计划行程（不含频道检测耗时）。"""
        from .geometry import polygon_layout_cover_radius

        p = self.params
        cover = polygon_layout_cover_radius(p.scan_sides, p.scan_radius)
        travel = p.scan_radius + (p.scan_sides - 1) * 2.0 * p.scan_radius * __import__(
            "math"
        ).sin(__import__("math").pi / p.scan_sides)
        return {"cover_radius_m": cover, "travel_m": travel}

    # -- 阶段1：覆盖扫描 ---------------------------------------------------
    def _cover_scan(self, client: SimulatorClient, state: DogState) -> None:
        for index, point in enumerate(self._scan_points()):
            if self._budget_exhausted(state):
                return
            self._move(state, point)
            state.scan_points_visited += 1
            self._sweep_v5(client, state, point, reason=f"scan#{index}")

    def _sweep_v5(
        self, client: SimulatorClient, state: DogState, point: Point, reason: str = ""
    ) -> None:
        p = self.params
        for channel in p.scan_channels:
            ch_state = state.channels[channel]
            if ch_state.is_cleared:
                continue
            if self._is_localized(ch_state):
                if p.clear_while_scanning:
                    circle = self._clear_circle(ch_state)
                    if circle is not None:
                        self._move(state, circle.center)
                        self._clear(
                            client, state, circle.center, channel, reason="scan-opportunistic"
                        )
                        self._move(state, point)
                continue
            # 已攒够读数 -> 不再在扫描点浪费检测预算（收尾阶段用更近的点精定位）
            if len(ch_state.readings) >= p.scan_probe_limit:
                continue
            if p.prune_by_range and self._out_of_range(ch_state, point):
                continue
            if self._budget_exhausted(state):
                return
            self._move(state, point)
            result = self._measure(client, state, point, channel, reason=reason)
            if result.kind.value == "near":
                self._clear(client, state, point, channel, reason="near")

    def _out_of_range(self, ch_state, point: Point) -> bool:
        """当前检测点到该频道可行域整体的最小距离是否已超过有效接收半径上界。

        若超过，则无论真值落在可行域内何处，该点都收不到信号（``R <= 1500m``），
        这次检测是**必然**的 no_signal —— 跳过它不损失任何信息，是可证明安全的剪枝。
        """
        from .protocol import MAX_EFFECTIVE_RADIUS_M

        if not ch_state.readings:
            return False  # 还没读数，可行域=整个目标区域，剪不动
        region = self._region(ch_state)
        if len(region) < 3:
            return False
        return _point_poly_min_dist(point, region) > MAX_EFFECTIVE_RADIUS_M

    # -- 阶段2 兜底重扫：沿用新布局 ----------------------------------------
    def _rescan(
        self, client: SimulatorClient, state: DogState, channels: list[int]
    ) -> None:
        for point in self._scan_points():
            if self._budget_exhausted(state):
                return
            if not [c for c in channels if not state.channels[c].is_cleared]:
                return
            self._move(state, point)
            for channel in channels:
                ch_state = state.channels[channel]
                if ch_state.is_cleared or ch_state.readings:
                    continue
                if self._budget_exhausted(state):
                    return
                self._move(state, point)
                result = self._measure(client, state, point, channel, reason="rescan")
                if result.kind.value == "near":
                    self._clear(client, state, point, channel, reason="near")
