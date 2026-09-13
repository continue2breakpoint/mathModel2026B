"""问题4 的 v9：把 v8 的"任务合并 + 直接清 + 范围剪枝"搬到定向场景。

与 v8 的唯一结构差别在**发现层布局**：
全向源用"中心 + 正七边形环"（覆盖半径 933.7m）即可；
定向源必须用**朝向完备**的布局，因为源只在正面半平面内有信号。完备性条件是

    对任意位置 G 与朝向 φ，存在被访问节点 p 使 |p-G| ≤ 1000 且 (p-G)·u(φ) > 0。

由"源恰好位于某个节点上、朝向背离所有邻居"这一最坏情形可推出
**节点间距必须 ≤ 1000m**（此时最近正面节点距离恰等于间距）。因此采用论文的
轴向三角网格 s=950（31 点，最坏正面距离 946.06m），其贪心路线已是 TSP 最优
（NN+2-opt 压缩量为 0，见 ``_design_q4_route.py``），行程 28500m。

其余机制（检测预算剪枝、任务合并调度、直接清、范围上界剪枝）与 v8 相同。
定向源的示向度在背面为 no_signal，可行域仍由正面读数求交得到，故所有剪枝
（含范围上界剪枝）依旧安全。
"""

# NOTE(端口适配): 用显式两参 super(Cls, self) 而不是零参 super()。
# @dataclass(slots=True) 会为子类重建类对象，零参 super() 依赖的 __class__
# 单元格会指向**原始**类，于是 'Q3V8Params' 实例在 Q3V5Params 的方法里调用
# super() 时会抛 TypeError（CPython 文档 dataclasses 章节明确警告）。
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .client import SimulatorClient
from .geometry import Point
from .protocol import OPTICAL_RANGE_M
from .state import DogState
from .strategy_v8 import Q3V8Params, Q3V8Strategy


def two_tier_nodes(s_i: float = 950.0, r_i: float = 1750.0,
                   n_o: int = 12, r_o: float = 1900.0) -> list[tuple[float, float]]:
    """两圈式朝向完备发现层：**内圈三角网格 + 外圈环**。

    为什么比"31 点轴向网格"省：轴向网格为了兼顾域外，把整片网格铺到半径 2513m
    （31 点、行程 28500m）；但域外节点其实只需要覆盖"贴边朝外的源"这一个用途，
    用一圈 12 点（弦长 983m ≤1000m）就够，域内再铺间距 ≤1000m 的网格即可。

    间距约束的来源：最坏情形是"源恰好落在某个节点上、朝向背离最近邻居"，
    此时最近的正面节点距离恰等于节点间距，故间距必须 ≤1000m。

    实测（``_design_q4_v2.py``）：s_i=950、r_i≤1750、环 12@1900 → 25 点，
    最坏正面距离 962.5m（完备，裕量 37.5m），NN+2-opt 行程 18828m
    （对照轴向网格 31 点 / 950.0m / 28500m）。
    """
    pts: list[tuple[float, float]] = []
    seen: set[tuple[float, float]] = set()
    dy = s_i * math.sqrt(3) / 2.0
    k = 0
    while k * dy <= r_i + 1e-9:
        for y in ((k * dy,) if k == 0 else (k * dy, -k * dy)):
            x0 = 0.0 if k % 2 == 0 else s_i / 2.0
            m = 0
            while True:
                xs = (1,) if (m == 0 and x0 == 0.0) else (1, -1)
                for sx in xs:
                    x = sx * (x0 + m * s_i)
                    if math.hypot(x, y) <= r_i + 1e-9 and (x, y) not in seen:
                        seen.add((x, y))
                        pts.append((x, y))
                if abs(x0 + m * s_i) > r_i + 1e-9:
                    break
                m += 1
        k += 1
    for k in range(n_o):
        a = 2.0 * math.pi * k / n_o
        p = (r_o * math.cos(a), r_o * math.sin(a))
        if p not in seen:
            seen.add(p)
            pts.append(p)
    return pts


def greedy_route(nodes: list[tuple[float, float]],
                 start: tuple[float, float] = (0.0, 0.0),
                 two_opt_passes: int = 40) -> list[tuple[float, float]]:
    """最近邻 + 2-opt 的开放路线（固定起点）。"""
    rem, route, cur = list(nodes), [], start
    while rem:
        j = min(range(len(rem)), key=lambda i: math.hypot(rem[i][0] - cur[0], rem[i][1] - cur[1]))
        cur = rem.pop(j)
        route.append(cur)
    n = len(route)
    for _ in range(two_opt_passes):
        imp = False
        for i in range(n - 1):
            a = start if i == 0 else route[i - 1]
            b = route[i]
            for j in range(i + 1, n):
                c = route[j]
                d = route[j + 1] if j + 1 < n else None
                delta = math.hypot(a[0] - c[0], a[1] - c[1]) - math.hypot(a[0] - b[0], a[1] - b[1])
                if d is not None:
                    delta += math.hypot(b[0] - d[0], b[1] - d[1]) - math.hypot(c[0] - d[0], c[1] - d[1])
                if delta < -1e-9:
                    route[i : j + 1] = list(reversed(route[i : j + 1]))
                    imp = True
        if not imp:
            break
    return route


@dataclass(slots=True)
class Q4V9Params(Q3V8Params):
    #: 轴向三角网格间距（必须 ≤ 1000m 才定向完备）
    discovery_s: float = 950.0
    #: 精定位候选点允许偏离"正面方向"的最大角度（定向源背面永远无信号）
    front_cone_deg: float = 60.0
    #: 发现层改用"内圈网格 + 外圈环"两圈式（行程 18828m vs 轴向网格 28500m）
    two_tier_discovery: bool = True
    tier_s_i: float = 950.0
    tier_r_i: float = 1750.0
    tier_n_o: int = 12
    tier_r_o: float = 1900.0


class Q4V9Strategy(Q3V8Strategy):
    """v9：定向场景 = 朝向完备发现层（轴向三角网格）+ v8 的任务合并机制。"""

    def __init__(self, params: Q4V9Params | None = None) -> None:
        self.params: Q4V9Params = params or Q4V9Params()
        super(Q4V9Strategy, self).__init__(self.params)

    def _scan_points(self) -> list[Point]:
        p = self.params
        if p.two_tier_discovery:
            nodes = two_tier_nodes(p.tier_s_i, p.tier_r_i, p.tier_n_o, p.tier_r_o)
            return [Point(x, y) for (x, y) in greedy_route(nodes)]
        from .paper_q4.coverage import hamiltonian_route_axial

        route, _ = hamiltonian_route_axial(p.discovery_s)
        return [Point(x, y) for (x, y) in route]

    def scan_plan(self) -> dict[str, Any]:
        from .paper_q4.coverage import hamiltonian_route_axial

        route, length = hamiltonian_route_axial(self.params.discovery_s)
        return {"n_nodes": len(route), "travel_m": length, "note": "朝向完备（间距<=1000m）"}

    # -- 定向感知的精定位 ------------------------------------------------
    def _probe_region_interior(self, client, state, channel, ch_state) -> None:
        """在可行域内部（沿其最长弦）取点检测：长条情形下唯一稳的取点方式。

        可行域必定包含真值，所以"在可行域内部取点"到真值的距离不会超过可行域直径；
        这些点又基本落在源的正面侧（可行域由正面读数求交而来），因此几乎必出读数，
        拿到读数后可行域会立刻被"斜切"收缩。
        """
        region = self._region(ch_state)
        if len(region) < 3:
            return
        # 最长弦（顶点数很少，直接枚举）
        best = (0.0, None, None)
        for i in range(len(region)):
            for j in range(i + 1, len(region)):
                d = region[i].distance_to(region[j])
                if d > best[0]:
                    best = (d, region[i], region[j])
        if best[1] is None or best[0] < 60.0:
            return
        a, b = best[1], best[2]
        for frac in (0.5, 0.25, 0.75):
            if self._budget_exhausted(state) or ch_state.is_cleared:
                return
            q = Point(a.x + frac * (b.x - a.x), a.y + frac * (b.y - a.y))
            if state.position.distance_to(q) < 1.0:
                continue
            self._move(state, q)
            res = self._measure(client, state, q, channel, reason="region-interior")
            if res.kind.value == "near":
                self._clear(client, state, q, channel, reason="near")
                return
            if res.kind.value == "direction":
                circle = self._clear_circle(ch_state)
                if circle is not None:
                    self._move(state, circle.center)
                    if self._clear(client, state, circle.center, channel, reason="interior-clear"):
                        return

    def _choose_refine_point(self, state: DogState, ch_state):
        """精定位候选点：**限制在正面锥内**。

        基线打分里的 ``sep_weight``（追求角度分离）会把候选点推到"与已有读数方位差
        最大"的方向 —— 对定向源而言那正是**背面**，测了必然 no_signal，可行域不收缩，
        流程在同一侧反复空转（``_diag_v9b.py`` 实测漏清全部由此产生）。

        由于源必然朝向"曾成功收到它的那个检测点"，所以
        ``(估计点 → 最近一次成功检测点)`` 的方向就是正面的一个可靠代理：
        只在该方向 ±``front_cone_deg`` 内取候选点，其余方向一概不取。
        """
        est = self._estimate(ch_state)
        if est is None:
            return None
        front_dir = None
        if ch_state.readings:
            apex = ch_state.readings[-1][0]
            if est.distance_to(apex) > 1.0:
                front_dir = est.bearing_to(apex)
        if front_dir is None:
            return super(Q4V9Strategy, self)._choose_refine_point(state, ch_state)

        from .geometry import angle_difference_deg, unit_from_deg
        from .protocol import ARENA_RADIUS_M

        p = self.params
        region = self._region(ch_state)
        samples = self._region_samples(region, est) if region else []
        best = None
        for radius in p.approach_radii:
            for k in range(p.approach_directions * 3):
                theta = 360.0 * k / (p.approach_directions * 3)
                if angle_difference_deg(theta, front_dir) > p.front_cone_deg:
                    continue
                cand = Point(
                    est.x + radius * unit_from_deg(theta).x,
                    est.y + radius * unit_from_deg(theta).y,
                )
                if cand.norm() > ARENA_RADIUS_M * 1.35:
                    continue
                score = -p.travel_weight * state.position.distance_to(cand)
                if samples:
                    score -= p.approach_weight * (
                        sum(cand.distance_to(s) for s in samples) / len(samples)
                    )
                if best is None or score > best[0]:
                    best = (score, cand)
        if best is None:
            return super(Q4V9Strategy, self)._choose_refine_point(state, ch_state)
        chosen = best[1]
        if state.position.distance_to(chosen) < 1.0:
            return super(Q4V9Strategy, self)._choose_refine_point(state, ch_state)
        return chosen
    def _localize_and_clear(self, client: SimulatorClient, state: DogState, channel: int) -> None:
        """v8 的"直接清优先" + **镜像探点**。

        定向源只在正面半平面有信号：若精定位候选点落在源背面，读数会是
        no_signal，可行域不收缩，流程会在同一侧反复空转（实测漏清的频道全是
        这种情形）。这里在落空后立刻把候选点关于估计点**镜像**到另一侧再测一次，
        把"背面"变成"正面"，避免死循环。
        """
        p = self.params
        ch_state = state.channels[channel]
        if ch_state.is_cleared:
            return
        if p.try_direct_clear:
            est = self._estimate(ch_state)
            if est is not None:
                self._move(state, est)
                if self._clear(client, state, est, channel, reason="direct"):
                    return
                self._measure(client, state, est, channel, reason="direct-fail")

        # 可行域是"长条"时（只有 1~2 条读数），质心可能离真值数百米，
        # 围绕质心的候选点全部落在长条外 → 反复空转。改为**直接在可行域内部取点**：
        # 长条内任意点到真值的距离都 ≤ 直径，且基本位于正面侧，几乎必出读数。
        if len(ch_state.readings) <= 2 and not ch_state.is_cleared:
            self._probe_region_interior(client, state, channel, ch_state)

        for round_index in range(max(p.max_refine_rounds, 3)):
            if self._budget_exhausted(state):
                return
            circle = self._clear_circle(ch_state)
            if circle is not None:
                self._move(state, circle.center)
                if self._clear(client, state, circle.center, channel, reason="localized"):
                    return
                self._measure(client, state, circle.center, channel, reason="post-fail")
                continue
            cand = self._choose_refine_point(state, ch_state)
            if cand is None:
                break
            self._move(state, cand)
            result = self._measure(client, state, cand, channel, reason=f"refine#{round_index}")
            if result.kind.value == "near":
                self._clear(client, state, cand, channel, reason="near")
                return
            if result.kind.value == "no_signal":
                # 定向源背面没有信号：沿"估计点 ↔ 最近一次成功检测点"连线补测。
                # 源必然朝向曾成功收到它的那个检测点（否则那次不会有读数），
                # 因此这条线段上的点**必定落在正面半平面内**。
                est = self._estimate(ch_state)
                apex = ch_state.readings[-1][0] if ch_state.readings else None
                got = False
                if est is not None and apex is not None and est.distance_to(apex) > 1.0:
                    for frac in (0.15, 0.4, 0.7):
                        p2 = Point(
                            est.x + frac * (apex.x - est.x), est.y + frac * (apex.y - est.y)
                        )
                        if state.position.distance_to(p2) < 1.0:
                            continue
                        self._move(state, p2)
                        res2 = self._measure(
                            client, state, p2, channel, reason="refine-frontline"
                        )
                        if res2.kind.value == "near":
                            self._clear(client, state, p2, channel, reason="near")
                            return
                        if res2.kind.value == "direction":
                            got = True
                            break
                if got:
                    continue
                # 再退一步：关于估计点镜像到另一侧
                if est is not None:
                    mirror = Point(2.0 * est.x - cand.x, 2.0 * est.y - cand.y)
                    if state.position.distance_to(mirror) > 1.0:
                        self._move(state, mirror)
                        res2 = self._measure(client, state, mirror, channel, reason="refine-mirror")
                        if res2.kind.value == "near":
                            self._clear(client, state, mirror, channel, reason="near")
                            return
                        if res2.kind.value == "direction":
                            continue
                fallback = self._fallback_point(state, ch_state)
                if fallback is None:
                    break
                self._move(state, fallback)
                self._measure(client, state, fallback, channel, reason="refine-fallback")

        circle = self._clear_circle(ch_state, tolerance_m=OPTICAL_RANGE_M)
        if circle is None:
            est = self._estimate(ch_state)
            if est is not None and not self._budget_exhausted(state):
                self._move(state, est)
                self._clear(client, state, est, channel, reason="last-resort")
