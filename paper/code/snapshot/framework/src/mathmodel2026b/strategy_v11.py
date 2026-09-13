"""问题3 的 v11：单读数频道延迟调度——"读数够才清，读数不够等扫描"。

插桩（``_diag_clears.py``）显示 v8 的直接清：
    readings=2 → 命中 86%；readings=1 → 命中 3%（800m 瞎猜）。
离线研究（``_study_est.py``）显示 **84% 的源本来就有 >=2 个扫描停点可见**——
它们会在扫描过程中自然拿到第二条好几何读数，根本不需要在单读数时就去清。
剩下 16%（只有一个停点可见）才真正需要专门补测，交给收尾阶段处理。

v11 的调度闸门（替换 v6/v7 的 ``schedule_min_readings``）：
    频道可清  <=>  读数 >= 2  或  （读数 == 1 且 扫描点已全部访问）

与 ``schedule_min_readings=2`` 的区别：后者对"第二停点在扫描后期"的频道
强行等待，破坏了顺路清除的合并收益（实测 314 vs 267）；v11 只对
"确实还有机会白拿第二条读数"的频道等待。
"""
# NOTE(端口适配): 用显式两参 super(Cls, self) 而不是零参 super()。
# @dataclass(slots=True) 会为子类重建类对象，零参 super() 依赖的 __class__
# 单元格会指向**原始**类，于是 'Q3V8Params' 实例在父类的方法里调用
# super() 时会抛 TypeError（CPython 文档 dataclasses 章节明确警告）。
from __future__ import annotations

import time
from dataclasses import dataclass

from .client import SimulatorClient
from .geometry import Point
from .state import DogState
from .strategy import Budget
from .strategy_v8 import Q3V8Params, Q3V8Strategy


@dataclass(slots=True)
class Q3V11Params(Q3V8Params):
    #: 可清判定里"读数充足"的阈值
    clear_ready_readings: int = 2


class Q3V11Strategy(Q3V8Strategy):
    """v11：读数够了才调度清除；单读数频道等扫描结束。"""

    def __init__(self, params: Q3V11Params | None = None) -> None:
        self.params: Q3V11Params = params or Q3V11Params()
        super(Q3V11Strategy, self).__init__(self.params)

    def _channel_clearable(self, state: DogState, channel: int, pending_count: int) -> bool:
        ch = state.channels.get(channel)
        if ch is None or ch.is_cleared:
            return False
        n_rd = len(ch.readings)
        if n_rd == 0:
            return False
        if n_rd >= self.params.clear_ready_readings:
            return True
        # 单读数：扫描点没走完就再等等（可能白拿第二条）
        return pending_count == 0

    # -- 主循环（v7 的 run 加了可清闸门）--------------------------------
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
            return self._channel_clearable(state, key, len(pending_scan)) and attempts.get(key, 0) < p.max_schedule_attempts

        while True:
            if self._budget_exhausted(state):
                break
            tasks: list[tuple[str, int, Point]] = []
            if p.visit_all_scan_points:
                tasks += [("scan", i, points[i]) for i in pending_scan]
            for channel, ch_state in state.channels.items():
                if not self._channel_clearable(state, channel, len(pending_scan)):
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
