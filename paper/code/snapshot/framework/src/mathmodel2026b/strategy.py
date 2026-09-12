"""问题3 的搜索 / 定位 / 清除策略。

数学依据
--------
1. **覆盖**：全向源有效接收半径 R >= 1000m。以圆心 + 半径 ``scan_radius``
   的正六边形六顶点共 7 点为检测点时，只要"覆盖半径"（区域中任一点到最近检测点
   的最大距离）不超过 1000m，任何干扰源都至少在一个检测点上被收到。
   ``geometry.covering_radius`` 可离线校验这一点。
2. **定位**：一次示向度读数给出以检测点为顶点、角宽 2° 的扇形；多个扇形求交得到
   必定包含真实位置的凸多边形（保守可行域）。``direction`` 读数还蕴含
   "距离 <= R <= 1500m"，对应一个凸的半径约束，能把细长区域收成有界区域。
3. **清除判据**：可行域的最小包围圆半径 <= 20m（光学作用距离）时，瞄准圆心执行
   ``/clear`` 必定命中；若尚未满足，就继续用"更近的检测点"取得更紧的扇形。
   距离 d 处的横向不确定度约为 d*tan(1°) ≈ d*0.01745，所以近距测向收敛极快。
4. **误差不是噪声**：同一地点重复检测读数不变，故不做平均，只做集合求交。

所有可调参数集中在 :class:`Q3Params`，便于批量调参。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from math import inf
from typing import Any

from .client import SimulatorClient
from .geometry import (
    Circle,
    Point,
    angle_difference_deg,
    from_polar,
    minimum_enclosing_circle,
    polygon_centroid,
    polygon_diameter,
    regular_hexagon_scan_points,
    unit_from_deg,
)
from .protocol import (
    ARENA_RADIUS_M,
    MAX_EFFECTIVE_RADIUS_M,
    MEASURE_SECONDS,
    OPTICAL_RANGE_M,
)
from .state import ALL_CHANNELS, ChannelState, ChannelStatus, DogState


@dataclass(slots=True)
class Q3Params:
    """可调参数（批量调参的搜索空间）。"""

    # 覆盖扫描
    #: 六边形半径。下界由"覆盖半径 <= 干扰源最小有效接收半径 1000m"决定：
    #: r_min = 900*sqrt(3) - 100*sqrt(19) ≈ 1122.96m（此时区域边界正对六边形
    #: 边中点的位置刚好 1000m）。取 1200m 换取约 31m 的安全余量
    #: （覆盖半径 968.9m），同时比 1400m 少走约 2km。
    #: 用 ``geometry.covering_radius`` 可离线复核任意半径。
    scan_radius: float = 1200.0
    scan_channels: tuple[int, ...] = ALL_CHANNELS
    scan_start_at_center: bool = True

    # 信道处理时机
    localize_when_readings: int = 2  # localize_before_full_scan=True 时生效
    #: 先跑完整轮覆盖扫描再逐个定位。实测比"边扫边定位"少约 4km 折返路程：
    #: 扫描点之间本来就提供 60° 量级的角度分离，逐点交会比"回到原扫描点"更划算。
    localize_before_full_scan: bool = False

    # 逼近 / 精定位
    approach_radii: tuple[float, ...] = (30.0, 60.0, 120.0, 250.0)
    approach_directions: int = 8
    min_angular_separation_deg: float = 25.0
    max_refine_rounds: int = 6
    clear_tolerance_m: float = OPTICAL_RANGE_M
    max_range_bound_m: float = MAX_EFFECTIVE_RADIUS_M
    half_width_deg: float = 1.0

    # 打分权重（下一个检测点的选择）：路程权重过大（>1/1000）会为了省路程
    # 牺牲角度分离，实测清除率会掉到 0.97 左右，不可接受。
    sep_weight: float = 2.0
    travel_weight: float = 1.0 / 1200.0
    #: "靠近源"的权重：候选点到可行域的平均距离（米）的惩罚系数。
    #: 原打分里**完全没有"离源多近"这一项**，而定位结束后仍要走完到源的距离，
    #: 那一段是总路程的大头；加上这一项可以把"取读数"和"走过去"合成一个目标。
    approach_weight: float = 1.0 / 500.0
    #: 收尾阶段的处理顺序：
    #: ``channel`` = 按频道号升序（原始行为，会在区域里来回横跳）；
    #: ``nearest``  = 按"从当前位置最近的下一个源"贪心串成路线。
    visit_order: str = "nearest"
    #: 扫描阶段跳过"已经定位好"的频道：一个频道只对应一个源（附录1-1），
    #: 一旦可行域最小包围圆 <= 清除容差，再测该频道不会有任何新信息，
    #: 白白花掉 1s 切换 + 5s 检测。7 个扫描点 × 20 频道 = 140 次检测，
    #: 是仅次于路程的第二大开销。
    skip_localized_probes: bool = True

    # 预算保护
    max_virtual_time_s: float = 300_000.0
    max_wall_time_s: float = 1_100.0

    # 收尾
    rescan_after_failure: bool = True
    verify_clear: bool = False

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["scan_channels"] = list(self.scan_channels)
        data["approach_radii"] = list(self.approach_radii)
        return data


class Strategy(ABC):
    @abstractmethod
    def run(self, client: SimulatorClient, state: DogState) -> None: ...


@dataclass(slots=True)
class Budget:
    """运行预算：任一超限都必须立刻收尾，避免测试被判超时。"""

    virtual_deadline_s: float
    wall_deadline: float
    started_at: float = field(default_factory=time.time)

    def exhausted(self, state: DogState) -> bool:
        if time.time() >= self.wall_deadline:
            return True
        return state.virtual_time_s >= self.virtual_deadline_s


class Q3Strategy(Strategy):
    """全向源（问题3）的确定性搜索 / 交会定位 / 清除策略。"""

    def __init__(self, params: Q3Params | None = None) -> None:
        self.params = params or Q3Params()
        self.trace: list[dict[str, Any]] = []
        self.budget: Budget | None = None

    # -- 入口 -----------------------------------------------------------
    def run(self, client: SimulatorClient, state: DogState) -> None:
        p = self.params
        self.budget = Budget(
            virtual_deadline_s=p.max_virtual_time_s,
            wall_deadline=time.time() + p.max_wall_time_s,
        )
        self._cover_scan(client, state)
        self._finish_pending(client, state)

    # -- 阶段1：确定性覆盖扫描 -------------------------------------------
    def _cover_scan(self, client: SimulatorClient, state: DogState) -> None:
        p = self.params
        points = regular_hexagon_scan_points(p.scan_radius)
        if not p.scan_start_at_center:
            points = points[1:] + [points[0]]
        for index, point in enumerate(points):
            if self._budget_exhausted(state):
                return
            self._move(state, point)
            state.scan_points_visited += 1
            self._sweep(client, state, point, reason=f"scan#{index}")

    def _sweep(
        self, client: SimulatorClient, state: DogState, point: Point, reason: str
    ) -> None:
        """在一个点上扫描所有尚未清除的频道。"""
        p = self.params
        for channel in p.scan_channels:
            ch_state = state.channels[channel]
            if ch_state.is_cleared:
                continue
            if p.skip_localized_probes and self._is_localized(ch_state):
                # 频道与源一一对应，已经能 20m 内清除的频道再测不会有新信息
                continue
            if self._budget_exhausted(state):
                return
            # 就地定位会把机器狗带离本扫描点，测下一个频道前必须回到 point
            # （move_to 到同一坐标的代价为 0，因此没有额外开销）
            self._move(state, point)
            result = self._measure(client, state, point, channel, reason=reason)
            if result.kind.value == "near":
                self._clear(client, state, point, channel, reason="near")
            elif result.kind.value == "direction" and p.localize_before_full_scan:
                # 已经攒够读数就先把这个频道解决掉，避免之后长距离折返
                if len(ch_state.readings) >= p.localize_when_readings:
                    self._localize_and_clear(client, state, channel)

    # -- 阶段2：收尾（把还差读数的频道补齐）------------------------------
    def _finish_pending(self, client: SimulatorClient, state: DogState) -> None:
        if self.params.visit_order == "nearest":
            # 在线重排：每处理完一个源，就用"当前位置 + 最新估计点"重新挑最近的下一个。
            # 估计点会随着读数增加而变准，因此比一开始排好序更稳。
            remaining = set(state.uncleared_detected)
            while remaining:
                if self._budget_exhausted(state):
                    return
                channel = self._nearest_pending(state, remaining)
                remaining.discard(channel)
                self._localize_and_clear(client, state, channel)
        else:
            for channel in sorted(state.uncleared_detected):
                if self._budget_exhausted(state):
                    return
                self._localize_and_clear(client, state, channel)
        # 兜底：只补扫"从未被探测过"的频道。
        # 注意不能把"没有读数的频道"都当成遗漏——20 个频道里必然有几个根本没有干扰源，
        # 对它们做全区域重扫纯属浪费（实测约 10km 路程）。
        unprobed = [
            c for c, st in state.channels.items() if not st.is_cleared and st.probe_count == 0
        ]
        if unprobed and not self._budget_exhausted(state):
            self._rescan(client, state, unprobed)

    def _nearest_pending(self, state: DogState, remaining: set[int]) -> int:
        """在还没处理的频道里挑"估计位置离当前位置最近"的那个。

        原实现按频道号升序，等于让机器狗在 1800m 的圆域里按频道号随机横跳；
        而"访问所有源"本来就是一个 TSP，最近邻贪心就能拿掉大部分横跳路程。
        """

        def cost(channel: int) -> tuple[float, int]:
            est = self._estimate(state.channels[channel])
            if est is None:
                return (inf, channel)
            return (state.position.distance_to(est), channel)

        return min(remaining, key=cost)

    def _rescan(
        self, client: SimulatorClient, state: DogState, channels: list[int]
    ) -> None:
        p = self.params
        for point in regular_hexagon_scan_points(p.scan_radius):
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

    # -- 定位 + 清除 -----------------------------------------------------
    def _localize_and_clear(
        self, client: SimulatorClient, state: DogState, channel: int
    ) -> None:
        p = self.params
        ch_state = state.channels[channel]
        if ch_state.is_cleared:
            return

        for round_index in range(p.max_refine_rounds):
            if self._budget_exhausted(state):
                return

            circle = self._clear_circle(ch_state)
            if circle is not None:
                self._move(state, circle.center)
                if self._clear(client, state, circle.center, channel, reason="localized") \
                        or ch_state.is_cleared:
                    return
                # 清除失败：当前位置离真值仍 >20m，就地补一次测向再收缩
                self._measure(client, state, circle.center, channel, reason="post-fail")
                continue

            target = self._choose_refine_point(state, ch_state)
            if target is None:
                break
            self._move(state, target)
            result = self._measure(
                client, state, target, channel, reason=f"refine#{round_index}"
            )
            if result.kind.value == "near":
                self._clear(client, state, target, channel, reason="near")
                return
            if result.kind.value == "no_signal":
                ch_state.clear_failures += 1
                if ch_state.clear_failures > 2 * p.max_refine_rounds:
                    break
                # 估计点太远（超出该源有效接收半径），换个更靠近原检测点的候选
                fallback = self._fallback_point(state, ch_state)
                if fallback is None:
                    break
                self._move(state, fallback)
                self._measure(
                    client, state, fallback, channel, reason="refine-fallback"
                )

        # 最终尝试：可行域中心直接清一次
        circle = self._clear_circle(ch_state, tolerance_m=OPTICAL_RANGE_M)
        if circle is None:
            est = self._estimate(ch_state)
            if est is not None and not self._budget_exhausted(state):
                self._move(state, est)
                self._clear(client, state, est, channel, reason="last-resort")

    def _is_localized(self, ch_state: ChannelState) -> bool:
        """该频道是否已经收敛到"瞄中心必定命中"的程度。"""
        circle = self._clear_circle(ch_state)
        return circle is not None

    def _clear_circle(
        self, ch_state: ChannelState, tolerance_m: float | None = None
    ) -> Circle | None:
        p = self.params
        tol = p.clear_tolerance_m if tolerance_m is None else tolerance_m
        region = self._region(ch_state)
        if len(region) < 3:
            return None
        circle = minimum_enclosing_circle(region)
        if circle is not None and circle.radius <= tol:
            return circle
        return None

    def _region(self, ch_state: ChannelState) -> list[Point]:
        from .geometry import feasible_region

        return feasible_region(
            ch_state.readings,
            half_width_deg=self.params.half_width_deg,
            max_range_m=self.params.max_range_bound_m,
        )

    def _estimate(self, ch_state: ChannelState) -> Point | None:
        region = self._region(ch_state)
        if len(region) < 3:
            if ch_state.readings:
                apex, bearing = ch_state.readings[-1]
                return Point(
                    apex.x + 800.0 * unit_from_deg(bearing).x,
                    apex.y + 800.0 * unit_from_deg(bearing).y,
                )
            return None
        return polygon_centroid(region)

    def _choose_refine_point(
        self, state: DogState, ch_state: ChannelState
    ) -> Point | None:
        """选择下一个检测点：角度分离度尽量大、路程尽量短、到估计点尽量近。"""
        p = self.params
        est = self._estimate(ch_state)
        if est is None:
            return None
        region = self._region(ch_state)
        samples = self._region_samples(region, est)
        existing = [bearing for _, bearing in ch_state.readings]
        best: tuple[float, Point] | None = None
        for radius in p.approach_radii:
            for k in range(p.approach_directions):
                theta = 360.0 * k / p.approach_directions
                cand = Point(
                    est.x + radius * unit_from_deg(theta).x,
                    est.y + radius * unit_from_deg(theta).y,
                )
                if cand.norm() > ARENA_RADIUS_M * 1.35:
                    continue
                if ch_state.readings:
                    new_dir = cand.bearing_to(est)
                    sep = min(
                        angle_difference_deg(new_dir, b) for b in existing
                    )
                else:
                    sep = 180.0
                travel = state.position.distance_to(cand)
                score = (
                    p.sep_weight * min(sep, 90.0) / 90.0
                    - p.travel_weight * travel
                )
                if p.approach_weight > 0.0 and samples:
                    mean_d = sum(cand.distance_to(s) for s in samples) / len(samples)
                    score -= p.approach_weight * mean_d
                if best is None or score > best[0]:
                    best = (score, cand)
        if best is None:
            return None
        chosen = best[1]
        # 避免"原地重测"：同地点读数不变，没有信息量
        if state.position.distance_to(chosen) < 1.0:
            return None
        return chosen

    @staticmethod
    def _region_samples(region: list[Point], est: Point, n: int = 200) -> list[Point]:
        """在可行域内均匀撒点：顶点 + 质心 + 在包围盒上按射线采样的内点。

        用于估计"从候选点到源的平均距离"——源必然落在可行域内，
        这个均值就是对"还要走多远"的无先验估计。
        """
        if not region:
            return []
        samples: list[Point] = list(region) + [est]
        xs = [pt.x for pt in region]
        ys = [pt.y for pt in region]
        if max(xs) - min(xs) < 1e-9 and max(ys) - min(ys) < 1e-9:
            return samples
        step = max(1, len(region) // max(1, n // 2))
        for index in range(0, len(region), step):
            a = region[index]
            b = region[(index + 1) % len(region)]
            for t in (0.25, 0.5, 0.75):
                samples.append(Point(a.x + t * (b.x - a.x), a.y + t * (b.y - a.y)))
        return samples

    def _fallback_point(self, state: DogState, ch_state: ChannelState) -> Point | None:
        """退路：回到最早检测到该频道的点附近再取一次读数。"""
        if not ch_state.readings:
            return None
        apex, bearing = ch_state.readings[0]
        base = Point(
            apex.x + 900.0 * unit_from_deg(bearing).x,
            apex.y + 900.0 * unit_from_deg(bearing).y,
        )
        if base.norm() > ARENA_RADIUS_M * 1.35:
            base = from_polar(
                min(ARENA_RADIUS_M * 0.95, base.norm()), apex.bearing_to(base)
            )
        if state.position.distance_to(base) < 1.0:
            return None
        return base

    # -- 基本动作 --------------------------------------------------------
    def _move(self, state: DogState, target: Point) -> float:
        return state.move_to(target)

    def _measure(
        self,
        client: SimulatorClient,
        state: DogState,
        point: Point,
        channel: int,
        reason: str = "",
    ):
        state.select_channel(channel)
        result = client.measure(point, channel)
        state.measure_count += 1
        state.channels[channel].probe_count += 1
        state.observe(result.virtual_time_s)
        self.trace.append(
            {
                "kind": "measure",
                "reason": reason,
                "channel": channel,
                "x": point.x,
                "y": point.y,
                "outcome": str(result.kind),
                "svd_deg": result.svd_deg,
                "virtual_time_s": result.virtual_time_s,
            }
        )
        if result.kind.value == "direction":
            state.measure_accepted_count += 1
            state.channels[channel].add_bearing(point, result.svd_deg or 0.0)
        elif result.kind.value == "near":
            state.measure_accepted_count += 1
            state.channels[channel].mark_near()
        return result

    def _clear(
        self,
        client: SimulatorClient,
        state: DogState,
        point: Point,
        channel: int,
        reason: str = "",
    ) -> bool:
        result = client.clear(point, channel)
        ch_state = state.channels[channel]
        state.clear_count += 1
        state.observe(result.virtual_time_s)
        ok = result.cleared
        if ok:
            state.clear_success_count += 1
            ch_state.mark_cleared(point)
        else:
            state.clear_failure_count += 1
            ch_state.mark_clear_failed()
        self.trace.append(
            {
                "kind": "clear",
                "reason": reason,
                "channel": channel,
                "x": point.x,
                "y": point.y,
                "outcome": str(result.kind),
                "virtual_time_s": result.virtual_time_s,
            }
        )
        return ok

    def _budget_exhausted(self, state: DogState) -> bool:
        return self.budget is not None and self.budget.exhausted(state)

    # -- 诊断 ------------------------------------------------------------
    def diagnostics(self, state: DogState) -> dict[str, Any]:
        return {
            "params": self.params.as_dict(),
            "trace_len": len(self.trace),
            "channels": {
                c: {
                    "status": str(st.status),
                    "readings": len(st.readings),
                    "distinct_points": st.distinct_points,
                    "region_diameter_m": (
                        polygon_diameter(self._region(st))
                        if st.readings
                        else None
                    ),
                }
                for c, st in state.channels.items()
                if st.status is not ChannelStatus.UNKNOWN
            },
        }


#: 兼容旧名字（README / docs 里称 "baseline"）。
#: 当前默认参数（六边形 1200m + 全频道扫描 + 在线最近邻序 + 逼近半径 30/60/120/250）
#: 在 mock 上随机案例里清除率 1.000，平均定位清除时间约 350s（虚拟时间约 4430s）。
#: 200 seed 验证：未全清除 0/200。
class Q3BaselineStrategy(Q3Strategy):
    def __init__(self, ring_radius: float = 1200.0) -> None:
        params = Q3Params()
        params.scan_radius = ring_radius
        super().__init__(params)
