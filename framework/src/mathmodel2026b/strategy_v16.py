"""问题3 的 v16：v15 之上，1 读数信道改用"横向交叉探针"。

v8~v15 对 1 读数信道（``schedule_min_readings=1`` 下任务集的主力）的流程是：
到楔形质心 -> 直接清（质心可偏离真值数百米，约半数失败，5s + 绕路）
-> 在质心补测一条读数（探针在射线上，与原读数近平行，信息量极低）
-> 才走基类的横向精定位。前两步基本是纯浪费。

v16 的改法：
* **调度锚点 = 横向交叉探针点**：在射线侧方取一个"大概率可闻、与原读数
  交角大、顺路"的点（apex + d·u ± L·u⊥），机器人到达后立即测量。
  一条横向读数与原读数交会，可行域从 100~1500m 的楔形缩到 ~30-60m，
  随后的直接清/定位清大概率一次命中。
* 若横向探针 no_signal：排除圆盘（v15）已把楔形近侧切掉，估计改善后
  交给 v8 流程兜底。

正确性不变：所有判定（MEC<=20 才确定清、no_signal 只用于收缩）均为保守。
"""
# NOTE(端口适配): 用显式两参 super(Cls, self) 而不是零参 super()。
# @dataclass(slots=True) 会为子类重建类对象，零参 super() 依赖的 __class__
# 单元格会指向**原始**类，于是 'Q3V15Params' 实例在父类的方法里调用
# super() 时会抛 TypeError（CPython 文档 dataclasses 章节明确警告）。
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .client import SimulatorClient
from .geometry import Point, unit_from_deg
from .state import DogState
from .strategy import Budget
from .strategy_v15 import Q3V15Params, Q3V15Strategy


@dataclass(slots=True)
class Q3V16Params(Q3V15Params):
    #: 1 读数信道先做横向交叉探针（而非去质心直接清）
    cross_probe_first: bool = True
    #: 交叉探针候选（沿射线距离, 横向偏移）
    cross_offsets: tuple[tuple[float, float], ...] = (
        (600.0, 500.0),
        (800.0, 400.0),
        (1000.0, 300.0),
        (400.0, 600.0),
    )
    #: 探针对可行域样本的可闻覆盖率下限（低于此值认为大概率 no_signal）
    cross_min_coverage: float = 0.40


class Q3V16Strategy(Q3V15Strategy):
    """v16：1 读数信道的横向交叉探针。"""

    def __init__(self, params: Q3V16Params | None = None) -> None:
        self.params: Q3V16Params = params or Q3V16Params()
        super(Q3V16Strategy, self).__init__(self.params)

    # -- 横向交叉探针点 ------------------------------------------------------
    def _cross_probe_point(self, state: DogState, ch_state, flip: bool = False) -> Point | None:
        """在射线侧方取交叉探针点：可闻覆盖率高、交角大、路程短。"""
        if not ch_state.readings:
            return None
        apex, bearing = ch_state.readings[-1]
        u = unit_from_deg(bearing)
        perp = Point(-u.y, u.x)
        region = self._region(ch_state)
        est = self._estimate(ch_state)
        if est is None:
            return None
        samples = self._region_samples(region, est) if region else [est]

        best: tuple[float, Point] | None = None
        for d, lat in self.params.cross_offsets:
            for side in ((1.0, -1.0) if not flip else (-1.0, 1.0)):
                q = Point(
                    apex.x + d * u.x + side * lat * perp.x,
                    apex.y + d * u.y + side * lat * perp.y,
                )
                if q.norm() > 2400.0:
                    continue
                cover = sum(1.0 for s in samples if q.distance_to(s) <= 1000.0) / len(samples)
                if cover < self.params.cross_min_coverage:
                    continue
                travel = state.position.distance_to(q)
                score = 2.0 * cover - travel / 500.0
                if best is None or score > best[0]:
                    best = (score, q)
        if best is None:
            return None
        return best[1]

    # -- 调度锚点：1 读数 -> 交叉探针点 --------------------------------------
    def _schedule_point(self, ch_state) -> Point | None:
        est = self._estimate(ch_state)
        if est is None:
            return None
        p = self.params
        if p.cross_probe_first and len(ch_state.readings) == 1:
            probe = self._cross_probe_point(_ProbeStateView(self, ch_state), ch_state)
            if probe is not None:
                return probe
        return est

    # -- 精定位：1 读数先交叉探测 --------------------------------------------
    def _localize_and_clear(self, client: SimulatorClient, state: DogState, channel: int) -> None:
        ch_state = state.channels[channel]
        if ch_state.is_cleared:
            return
        p = self.params
        if p.cross_probe_first and len(ch_state.readings) == 1 and not self._budget_exhausted(state):
            cand = self._cross_probe_point(state, ch_state)
            tried = False
            if cand is not None:
                self._move(state, cand)
                res = self._measure(client, state, cand, channel, reason="cross")
                tried = True
                if res.kind.value == "near":
                    self._clear(client, state, cand, channel, reason="near")
                    return
                if res.kind.value == "no_signal" and not ch_state.is_cleared:
                    # 换另一侧再试一次（源在射线另一侧的情形）
                    cand2 = self._cross_probe_point(state, ch_state, flip=True)
                    if cand2 is not None and state.position.distance_to(cand2) > 1.0:
                        self._move(state, cand2)
                        res2 = self._measure(client, state, cand2, channel, reason="cross2")
                        if res2.kind.value == "near":
                            self._clear(client, state, cand2, channel, reason="near")
                            return
            if tried and not ch_state.is_cleared and ch_state.readings:
                # 已有 >=2 条读数：先试定位清/直接清，再考虑第二轮交叉
                circle = self._clear_circle(ch_state)
                if circle is not None:
                    self._move(state, circle.center)
                    if self._clear(client, state, circle.center, channel, reason="localized"):
                        return
                est = self._estimate(ch_state)
                if est is not None:
                    self._move(state, est)
                    if self._clear(client, state, est, channel, reason="direct"):
                        return
        super(Q3V16Strategy, self)._localize_and_clear(client, state, channel)

    # -- 主循环（v7 的 2-opt 调度 + 交叉探针锚点）-----------------------------
    def run(self, client: SimulatorClient, state: DogState) -> None:
        p = self.params
        self.budget = Budget(
            virtual_deadline_s=p.max_virtual_time_s,
            wall_deadline=time.time() + p.max_wall_time_s,
        )
        points = self._scan_points()
        pending_scan: list[int] = list(range(len(points)))
        attempts: dict[int, int] = {}
        self.schedule_log: list[tuple[str, int]] = []
        self.dist_by_kind: dict[str, float] = {"scan": 0.0, "clear": 0.0, "finish": 0.0}

        while True:
            if self._budget_exhausted(state):
                break
            tasks: list[tuple[str, int, Point]] = []
            if p.visit_all_scan_points:
                tasks += [("scan", i, points[i]) for i in pending_scan]
            for channel, ch_state in state.channels.items():
                if ch_state.is_cleared:
                    continue
                if len(ch_state.readings) < p.schedule_min_readings:
                    continue
                if attempts.get(channel, 0) >= p.max_schedule_attempts:
                    continue
                tp = self._schedule_point(ch_state)
                if tp is not None:
                    tasks.append(("clear", channel, tp))
            if not tasks:
                break
            route = self._two_opt(state.position, self._nn_route(state.position, tasks))
            kind, key, pos = route[0]
            self.schedule_log.append((kind, key))
            d0 = state.move_distance_m
            if kind == "scan":
                self._move(state, pos)
                state.scan_points_visited += 1
                self._sweep_v5(client, state, pos, reason=f"scan#{key}")
                pending_scan.remove(key)
            else:
                attempts[key] = attempts.get(key, 0) + 1
                self._localize_and_clear(client, state, key)
            self.dist_by_kind[kind] += state.move_distance_m - d0

        d0 = state.move_distance_m
        self._finish_pending(client, state)
        self.dist_by_kind["finish"] += state.move_distance_m - d0

    # -- 诊断 ---------------------------------------------------------------
    def diagnostics(self, state: DogState) -> dict[str, Any]:
        return super(Q3V16Strategy, self).diagnostics(state)


class _ProbeStateView:
    """给 _cross_probe_point 一个'位置=估计点'的只读视图（调度期机器人还没动）。"""

    def __init__(self, strategy: Q3V16Strategy, ch_state) -> None:
        self._strategy = strategy
        self._ch_state = ch_state

    @property
    def position(self) -> Point:
        est = self._strategy._estimate(self._ch_state)
        return est if est is not None else Point(0.0, 0.0)
