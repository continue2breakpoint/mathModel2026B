"""问题3 的 v6：扫描-清除一体化任务调度。

为什么需要
----------
基线（含 v5）把行程切成两段互不相干的路线：

* 覆盖扫描环：中心 + 正七边形 r=1110，约 6889m；
* 清源路线：扫描结束后，对已发现的频道做"在线最近邻"串行清除，约 8020~9167m。

两段相加 ≈ 14900~16000m。但"覆盖扫描点（7 个）∪ 待清源（≈13 个）"一共只有约 20 个
目标，一条 TSP 走完只要 ≈0.7·√(n·A) ≈ 10000m —— **两段分开走白白多花约 5000m
（≈1000s 虚拟时间）**。信息约束（源必须先被探测到）不允许事先规划，但允许**在线合并**：

v6 把两类动作放进同一个任务集——
    * ``scan(i)``：尚未访问的第 i 个覆盖扫描点（要保证覆盖证书，全部必须访问）；
    * ``clear(c)``：已攒够 ``schedule_min_readings`` 条读数、估计位置已知的频道；
每一步取"离当前位置最近的任务"执行，执行完重新调度。这样"清源"不再是扫描之后的
第二段路线，而是插在扫描点之间的顺路任务，两段行程被自然合并。

覆盖证书不受影响：所有扫描点仍然会被访问（只是顺序由最近邻决定），
因此"全部扫描点 no_signal ⇒ 该频道无源"这一判定依然成立。
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
from .strategy_v5 import Q3V5Params, Q3V5Strategy


@dataclass(slots=True)
class Q3V6Params(Q3V5Params):
    #: 攒够几条读数就把该频道登记为"待清除任务"
    schedule_min_readings: int = 2
    #: 同一频道最多被调度几次（防止反复往返）
    max_schedule_attempts: int = 3
    #: 一体化调度时不再在扫描点做顺路清除（交给任务调度）
    clear_while_scanning: bool = False
    #: 扫描点是否必须全部访问（覆盖证书）；关闭仅用于消融实验
    visit_all_scan_points: bool = True


class Q3V6Strategy(Q3V5Strategy):
    """v6：覆盖扫描点与待清源合并为单一任务集，按最近邻在线调度。"""

    def __init__(self, params: Q3V6Params | None = None) -> None:
        self.params: Q3V6Params = params or Q3V6Params()
        super(Q3V6Strategy, self).__init__(self.params)
        #: 调度轨迹（kind, key）。
        #: 必须在 __init__ 里初始化：``run()`` 会重置它，但 ``diagnostics()``
        #: 可能在 run 之前被调用（runner 写 meta、单元测试断言），
        #: 只在 run() 里赋值会 AttributeError。
        self.schedule_log: list[tuple[str, int]] = []
        #: 分阶段里程计：scan / clear / finish
        self.dist_by_kind: dict[str, float] = {"scan": 0.0, "clear": 0.0, "finish": 0.0}

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
                est = self._estimate(ch_state)
                if est is not None:
                    tasks.append(("clear", channel, est))
            if not tasks:
                break
            kind, key, pos = min(tasks, key=lambda t: state.position.distance_to(t[2]))
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

    # -- 诊断 -------------------------------------------------------------
    def diagnostics(self, state: DogState) -> dict[str, Any]:
        data = super(Q3V6Strategy, self).diagnostics(state)
        data["schedule_len"] = len(self.schedule_log)
        data["schedule_head"] = self.schedule_log[:24]
        return data
