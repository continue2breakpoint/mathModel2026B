"""问题4 策略（适配器）：论文 ``run_q4`` 内核 × 框架 ``SimulatorClient/DogState``。

设计
----
论文的 Q4 算法（发现层：轴向三角网格 31 点定向完备扫描；定位层：正面点楔形交会
+ NBV；辨识层：双假设 + 盘内探针；清除层：MEC≤20m 与朝向精测）是自包含的，
其 ``run_q4(env)`` 只依赖一个与官方 HTTP 语义一一对应的 ``env`` 对象
（``enter/measure/clear/exit``，字段 ``measure_result/svd_deg/clear_result/
virtual_time_s``，见 ``paper_q4/sim_env.py``）。

因此移植采用**适配器模式**而非重写：

* 论文内核原样放在 ``paper_q4/``（从 B 题求解代码逐字节复制，保证行为一致）；
* :class:`PaperEnvAdapter` 把框架的 ``client + state`` 包装成论文 ``env``：
  - ``measure/clear`` 先 ``state.move_to`` 再调 ``client``（与论文 sim_env 的
    "先移动再动作"语义一致，移动距离/频道切换计数进框架统计）；
  - ``enter/exit`` 为 no-op——框架 runner 已经负责真正的 ``/enter``/``/exit``；
  - 响应字段在 ``MeasureResult/ClearResult`` 与论文 dict 之间互译。
* :class:`Q4Strategy` 实现 :class:`~mathmodel2026b.strategy.Strategy`，内部直接
  调 ``paper_q4.q4_strategy.run_q4``。mock 与 live 走同一代码路径，mock 上验证的
  行为可直接搬线上。

安全阀：真实测试有 1200s 墙钟预算，论文内核自身没有墙钟检查；适配器在每次
measure/clear 前检查墙钟，超限抛 :class:`Q4WallBudgetExceeded`，让 runner
正常收尾（已清除的源不受影响，日志完整落盘）。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from .client import SimulatorClient
from .geometry import Point
from .state import DogState
from .strategy import Q3Params, Strategy

#: 论文算法内核目录（已逐字节复制自 B 题求解代码，勿就地修改）。
_PAPER_DIR = Path(__file__).resolve().parent / "paper_q4"
if str(_PAPER_DIR) not in sys.path:
    sys.path.insert(0, str(_PAPER_DIR))


class Q4WallBudgetExceeded(RuntimeError):
    """墙钟预算耗尽：中止论文内核，交由 runner 收尾。"""


class PaperEnvAdapter:
    """论文 ``env`` 接口适配器（见模块 docstring）。"""

    def __init__(
        self, strategy: "Q4Strategy", client: SimulatorClient, state: DogState,
        wall_deadline: float | None = None,
    ) -> None:
        self._strategy = strategy
        self._client = client
        self._state = state
        self._wall_deadline = wall_deadline
        self.cur_channel = state.measurement_channel
        self.n_request = 0

    # -- 生命周期（runner 已处理真正的 /enter 与 /exit）------------------
    def enter(self) -> dict[str, Any]:
        return {"accepted": True, "virtual_time_s": self._state.virtual_time_s}

    def exit(self) -> dict[str, Any]:
        return {
            "accepted": True,
            "exit_reason": "user_exit",
            "virtual_time_s": self._state.virtual_time_s,
        }

    # -- 动作 -------------------------------------------------------------
    def _check_budget(self) -> None:
        if self._wall_deadline is not None and time.time() >= self._wall_deadline:
            raise Q4WallBudgetExceeded(
                f"墙钟预算耗尽（{time.strftime('%H:%M:%S')}），中止 Q4 内核"
            )

    def measure(self, x: float, y: float, ch: int) -> dict[str, Any]:
        self._check_budget()
        point = Point(float(x), float(y))
        state = self._state
        state.move_to(point)
        state.select_channel(ch)
        self.cur_channel = ch
        result = self._client.measure(point, ch)
        self.n_request += 1

        state.measure_count += 1
        state.channels[ch].probe_count += 1
        state.observe(result.virtual_time_s)
        kind = result.kind.value
        if kind == "direction":
            state.measure_accepted_count += 1
            state.channels[ch].add_bearing(point, result.svd_deg or 0.0)
        elif kind == "near":
            state.measure_accepted_count += 1
            state.channels[ch].mark_near()
        self._strategy.trace.append(
            {
                "kind": "measure",
                "reason": "q4",
                "channel": ch,
                "x": point.x,
                "y": point.y,
                "outcome": str(result.kind),
                "svd_deg": result.svd_deg,
                "virtual_time_s": result.virtual_time_s,
            }
        )
        out: dict[str, Any] = {
            "accepted": result.accepted,
            "virtual_time_s": result.virtual_time_s,
            "measure_result": kind,
        }
        if kind == "direction":
            out["svd_deg"] = result.svd_deg
        return out

    def clear(self, x: float, y: float, ch: int) -> dict[str, Any]:
        self._check_budget()
        point = Point(float(x), float(y))
        state = self._state
        state.move_to(point)
        result = self._client.clear(point, ch)
        self.n_request += 1

        state.clear_count += 1
        state.observe(result.virtual_time_s)
        ok = result.cleared
        if ok:
            state.clear_success_count += 1
            state.channels[ch].mark_cleared(point)
        else:
            state.clear_failure_count += 1
            state.channels[ch].mark_clear_failed()
        self._strategy.trace.append(
            {
                "kind": "clear",
                "reason": "q4",
                "channel": ch,
                "x": point.x,
                "y": point.y,
                "outcome": str(result.kind),
                "virtual_time_s": result.virtual_time_s,
            }
        )
        return {
            "accepted": result.accepted,
            "clear_result": "success" if ok else "no_target_in_range",
            "virtual_time_s": result.virtual_time_s,
        }

    # -- 真值（线上不可得，仅供论文 _stats 不崩）---------------------------
    def truth(self) -> list[dict[str, Any]]:
        return []

    def n_total(self) -> int:
        #: 线上拿不到干扰源总数：以已清除数为分母（真实清除率看 GUI / 官方库对账）。
        return max(1, len(self._state.cleared_channels))


class Q4Strategy(Strategy):
    """问题4（全向 + 定向混合）策略：论文 ``run_q4`` 内核的框架适配。"""

    def __init__(
        self, params: Q3Params | None = None, grid_s: float = 950.0
    ) -> None:
        self.params = params or Q3Params()
        self.grid_s = grid_s
        self.trace: list[dict[str, Any]] = []
        self.paper_stats: dict[str, Any] | None = None
        self.paper_states: dict[int, Any] | None = None

    def run(self, client: SimulatorClient, state: DogState) -> None:
        import q4_strategy as paper  # noqa: PLC0415 - paper_q4 目录已入 sys.path

        env = PaperEnvAdapter(
            self, client, state,
            wall_deadline=time.time() + self.params.max_wall_time_s,
        )
        self.paper_stats, self.paper_states = paper.run_q4(
            env, grid_s=self.grid_s, verbose=False
        )
        # 论文侧的频道级结论（含类型辨识/朝向估计）补进框架统计口径，
        # 让 summary 里能直接看到辨识结果。
        if self.paper_states is not None:
            for channel, st in self.paper_states.items():
                if st.state == "cleared":
                    state.channels[channel].status = state.channels[
                        channel
                    ].status.__class__("cleared")

    def diagnostics(self, state: DogState) -> dict[str, Any]:
        kinds = {}
        if self.paper_states is not None:
            kinds = {
                c: {"kind": st.kind, "phi_hat": st.phi_hat, "phi_half": st.phi_half}
                for c, st in self.paper_states.items()
                if st.state == "cleared"
            }
        return {
            "strategy": "q4",
            "grid_s": self.grid_s,
            "params": self.params.as_dict(),
            "trace_len": len(self.trace),
            "paper_stats": self.paper_stats,
            "identified": kinds,
        }
