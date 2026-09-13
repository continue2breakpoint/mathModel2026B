"""问题3 的 v7：在 v6「扫描-清除一体化任务集」上换成带前瞻的路由规划。

v6 的调度是**纯最近邻**（每一步只挑当前最近的任务），实测清源段每源平均要走
460~885m，比"访问这些源的最短路线"下界高约 48%。原因是最近邻短视：它不知道
"先绕远一点、后面能连成一串"。

v7 每一步都对**整个待办任务集**（未访扫描点 ∪ 待清源）求一条开放路径：
    1) 从当前位置做最近邻构造初始解；
    2) 做若干轮 2-opt（固定起点，允许反转任意子段）改进；
    3) 执行路径上的**第一个**任务，然后重新规划。
因为 2-opt 可以反转包含首段的子段，规划结果与"只看眼前"不同：它会为了后续
连成一串而先走稍远的任务，这正是要买的前瞻性。
"""

# NOTE(端口适配): 用显式两参 super(Cls, self) 而不是零参 super()。
# @dataclass(slots=True) 会为子类重建类对象，零参 super() 依赖的 __class__
# 单元格会指向**原始**类，于是 'Q3V8Params' 实例在 Q3V5Params 的方法里调用
# super() 时会抛 TypeError（CPython 文档 dataclasses 章节明确警告）。
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .client import SimulatorClient
from .geometry import Point
from .state import DogState
from .strategy import Budget
from .strategy_v6 import Q3V6Params, Q3V6Strategy


@dataclass(slots=True)
class Q3V7Params(Q3V6Params):
    #: 2-opt 最大轮数（每轮 O(n²) 次交换尝试）
    two_opt_passes: int = 8
    #: 是否在每次任务后重规划（False=只在任务集变化时重规划）
    replan_each_step: bool = True
    #: 批量规划：一次规划后按计划执行，任务集变化才重规划（False=每步重规划取首任务）
    follow_plan: bool = False


class Q3V7Strategy(Q3V6Strategy):
    """v7：任务集路由用 NN + 2-opt，带前瞻地调度扫描与清源。"""

    def __init__(self, params: Q3V7Params | None = None) -> None:
        self.params: Q3V7Params = params or Q3V7Params()
        super(Q3V7Strategy, self).__init__(self.params)

    # -- 路由规划 ---------------------------------------------------------
    def _nn_route(self, start: Point, tasks: list[tuple[str, int, Point]]):
        remaining = list(tasks)
        route: list[tuple[str, int, Point]] = []
        cur = start
        while remaining:
            j = min(range(len(remaining)), key=lambda i: cur.distance_to(remaining[i][2]))
            nxt = remaining.pop(j)
            route.append(nxt)
            cur = nxt[2]
        return route

    def _two_opt(self, start: Point, route: list[tuple[str, int, Point]]):
        n = len(route)
        if n < 3:
            return route
        passes = 0
        while passes < self.params.two_opt_passes:
            passes += 1
            improved = False
            for i in range(n - 1):
                a = start if i == 0 else route[i - 1][2]
                b = route[i][2]
                for j in range(i + 1, n):
                    c = route[j][2]
                    d = route[j + 1][2] if j + 1 < n else None
                    delta = a.distance_to(c) - a.distance_to(b)
                    if d is not None:
                        delta += b.distance_to(d) - c.distance_to(d)
                    if delta < -1e-9:
                        route[i : j + 1] = list(reversed(route[i : j + 1]))
                        improved = True
            if not improved:
                break
        return route

    # -- 主循环 -----------------------------------------------------------
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
        plan: list[tuple[str, int, Point]] = []

        def task_ok(task: tuple[str, int, Point]) -> bool:
            kind, key, _ = task
            if kind == "scan":
                return key in pending_scan
            return not (
                state.channels[key].is_cleared
                or len(state.channels[key].readings) < p.schedule_min_readings
                or attempts.get(key, 0) >= p.max_schedule_attempts
            )

        def plan_valid() -> bool:
            return all(task_ok(t) for t in plan)

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
            if p.follow_plan:
                # 批量规划：计划仍然有效就照着走，任务集变化才重新规划
                if not plan or not plan_valid():
                    plan = self._two_opt(state.position, self._nn_route(state.position, tasks))
                while plan and not task_ok(plan[0]):
                    plan.pop(0)
                if not plan:
                    plan = self._two_opt(state.position, self._nn_route(state.position, tasks))
                kind, key, pos = plan.pop(0)
            else:
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
