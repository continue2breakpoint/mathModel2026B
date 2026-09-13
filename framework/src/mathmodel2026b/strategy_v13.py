"""问题3 的 v13：顺路免费探针（transit probe）。

v8 的结构性浪费（插桩实证）：66/10seeds 的直接清是"单读数抽奖"
（800m 猜点，命中率 3%）。v10（侧向专程补测）/v11（延迟调度）/
v12（猜距调优）都被数据否决——专程补测的绕路成本 > 精定位的节省。

v13 的思路是**零绕路**补读数：机器人在扫描任务之间移动时，如果直线路径
经过某单读数频道检测点附近（<= transit_probe_range_m），且从路径最近点
看估计点的方位与已有读数方位分离 >= transit_probe_min_sep_deg，
就在路径上的该点停一次，给该频道补第二条读数（6s，无额外行程）。
拿到 2 条好几何读数的频道走正常 v8 流程（直接清 86%）。
"""
# NOTE(端口适配): 用显式两参 super(Cls, self) 而不是零参 super()。
# @dataclass(slots=True) 会为子类重建类对象，零参 super() 依赖的 __class__
# 单元格会指向**原始**类，于是 'Q3V8Params' 实例在父类的方法里调用
# super() 时会抛 TypeError（CPython 文档 dataclasses 章节明确警告）。
from __future__ import annotations

import math
import time
from dataclasses import dataclass

from .client import SimulatorClient
from .geometry import Point, angle_difference_deg, degrees_atan2
from .state import DogState
from .strategy import Budget
from .strategy_v8 import Q3V8Params, Q3V8Strategy


@dataclass(slots=True)
class Q3V13Params(Q3V8Params):
    #: 路径离检测点多近才触发顺路探针（米）
    transit_probe_range_m: float = 400.0
    #: 触发所需的最小方位分离角（度）
    transit_probe_min_sep_deg: float = 12.0
    #: 是否启用
    transit_probe: bool = True


class Q3V13Strategy(Q3V8Strategy):
    """v13：顺路免费补第二条读数，修复单读数抽奖。"""

    def __init__(self, params: Q3V13Params | None = None) -> None:
        self.params: Q3V13Params = params or Q3V13Params()
        super(Q3V13Strategy, self).__init__(self.params)
        self.transit_probes = 0
        self.transit_hits = 0

    def _closest_on_segment(self, a: Point, b: Point, p: Point) -> Point:
        ax, ay, bx, by = a.x, a.y, b.x, b.y
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        if L2 <= 1e-12:
            return Point(ax, ay)
        t = max(0.0, min(1.0, ((p.x - ax) * dx + (p.y - ay) * dy) / L2))
        return Point(ax + t * dx, ay + t * dy)

    def _transit_probe(self, client: SimulatorClient, state: DogState, target: Point) -> None:
        """从当前位置去 target 前，顺路给单读数频道补读数（可能多次停靠）。"""
        if not self.params.transit_probe:
            return
        for _ in range(3):  # 最多顺路停 3 次，避免路径被拉长
            best = None
            cur = state.position
            for channel, chs in state.channels.items():
                if chs.is_cleared or len(chs.readings) != 1:
                    continue
                apex, bearing = chs.readings[-1]
                q = self._closest_on_segment(cur, target, apex)
                d = q.distance_to(apex)
                if d > self.params.transit_probe_range_m or d < 1.0:
                    continue
                if cur.distance_to(q) < 1.0:
                    continue
                est = self._estimate(chs)
                if est is None:
                    continue
                b_q = degrees_atan2(est.y - q.y, est.x - q.x)
                sep = angle_difference_deg(b_q, bearing)
                if sep < self.params.transit_probe_min_sep_deg:
                    continue
                if best is None or d < best[0]:
                    best = (d, channel, q)
            if best is None:
                return
            _, channel, q = best
            n_before = len(state.channels[channel].readings)
            self._move(state, q)
            self._measure(client, state, q, channel, reason="transit")
            self.transit_probes += 1
            if len(state.channels[channel].readings) > n_before:
                self.transit_hits += 1
            # q 已在路径上，继续从 q 出发找下一个顺路点

    # -- 主循环（v7 的 run + 扫描移动前的顺路探针）-----------------------
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

        def task_ok(kind: str, key: int) -> bool:
            if kind == "scan":
                return key in pending_scan
            ch = state.channels.get(key)
            return not (
                ch is None or ch.is_cleared
                or len(ch.readings) < p.schedule_min_readings
                or attempts.get(key, 0) >= p.max_schedule_attempts
            )

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
                est = self._estimate(ch_state)
                if est is not None:
                    tasks.append(("clear", channel, est))
            if not tasks:
                break
            route = self._two_opt(state.position, self._nn_route(state.position, tasks))
            kind, key, pos = route[0]
            self.schedule_log.append((kind, key))
            d0 = state.move_distance_m
            if kind == "scan":
                self._transit_probe(client, state, pos)
                self._move(state, pos)
                state.scan_points_visited += 1
                self._sweep_v5(client, state, pos, reason=f"scan#{key}")
                pending_scan.remove(key)
            else:
                attempts[key] = attempts.get(key, 0) + 1
                # 去清源的路上也可能顺路补别的单读数频道
                est0 = self._estimate(state.channels.get(key)) if state.channels.get(key) else None
                if est0 is not None:
                    self._transit_probe(client, state, est0)
                self._localize_and_clear(client, state, key)
            self.dist_by_kind[kind] += state.move_distance_m - d0

        d0 = state.move_distance_m
        self._finish_pending(client, state)
        self.dist_by_kind["finish"] += state.move_distance_m - d0
