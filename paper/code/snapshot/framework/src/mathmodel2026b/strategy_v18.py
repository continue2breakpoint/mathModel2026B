"""问题3 / 问题4 的 v18：把**知识矩阵的负例能力**与**可证明的覆盖式清除**并进迭代版。

为什么需要 v18
--------------
2026-09-13 的独立复核（``review_20260913/审阅结论.md``）在**未参与调参的种子 61–160**
上发现：v17 的"760/760 全清"并不覆盖后续分布 —— 默认混合与全定向压力场景各只有
**97/100** 局全清。漏掉的源**全部已经拿到过测向读数**，问题出在精定位与清除兜底：

* ``mixed 80/ch19``：末次估计误差约 33.3m，15m 六点环的最近试清距离仍是 **21.51m**，全失败。
* ``all_directional 128/ch11``：射线检测曾走到距源 **15.9m** 处，因位于波束背面返回
  ``no_signal``，随后错误收缩距离上界，最终地毯清除点跑到 **240m** 外。

根因有两条，正是 v18 要修的两条：

1. **射线推进不是证明。** ``v14._walk_the_ray`` 在 ``no_signal`` 后直接 ``hi = t``，
   其正确性依赖"读数射线穿过真源"。但测向有 ±1° 误差，在波束边缘附近机器狗可能
   **还没接近源就已经走到背面**（复核给出了显式反例）。因此射线推进只能当启发式，
   不能污染保守可行域。
2. **环形试清不是覆盖。** "中心 + 半径 15m 的六点环"对任意误差方向只保证覆盖到
   约 ``15cos30° + √(20² − 15²sin²30°) ≈ 31.53m``，盖不住 35–40m 的估计误差；
   单纯加大环半径还会在环间留空隙。

v18 = v17 的在线机制（24 点联合优化布局 + 射线推进 + 地毯清除 + 环形兜底）
**+ 下面三层**，三层都来自"知识矩阵"这条独立路线，也都有充分性论证：

第一层：失败清除的 20m 排除圆（附件1 §2.3）
    ``/clear`` 返回"未发现" ⟹ 以清除点为心的 20m 闭圆盘内**没有**该频道的源。
    这条负例**与源的朝向无关**（光学/激光），对定向源同样严格成立 —— 比迭代版
    原先唯一可用的 ``no_signal``（定向时是"超半径 ∪ 落在背面"的析取）强得多。
    格完全落入某个排除圆时即可安全丢弃（见 :mod:`mathmodel2026b.knowledge_layer`）。

第二层：覆盖式清除计划（``region_clear_plan``）
    把保守可行域 ``F`` 用边长 25m 的格铺开：格心到格内任意点 ≤ ``25/√2 = 17.678m``
    < 20m，因此"依次清除所有与 ``F`` 相交的格心"在**完整执行**时保证命中真值。
    这不是启发式而是充分条件（``guaranteed`` 字段），只在预算中断或格心不可达时失效。

第三层：代价模型（"两次 clear 可能比 measure 再 clear 更便宜"）
    修正 mock 计时后（失败 ``/clear`` = 3s、成功 = 5s；``/measure`` = 5s + 换频道 1s）：

    ========  ====================  ==========================
    覆盖格数 k  clear 计划固定耗时     一次 measure + 一次 clear
    ========  ====================  ==========================
    1         5s                    11s
    2         8s                    11s
    3         11s                   11s
    ========  ====================  ==========================

    k ≤ 2 时计划在固定耗时上**严格占优且带覆盖保证**；k = 3 打平，由路程决定。
    所以 v18 在走"直接清 / 射线推进"之前，先算一遍覆盖计划；划算就直接执行。

三者合起来，把"精定位失败 → 清除兜底"从**半径 15m 的环**升级成
**半径 20m 的格覆盖**，并把每次失败清除变成可复用的排除信息。

与 v17 的关系
-------------
``q4-v17`` 保留为**对照臂**（冻结基线），``q4-v18`` 是问题4 的交付版。
两者可用 ``Q4V18Params`` 的 ``region_clear_*`` / ``use_knowledge_matrix`` 开关逐层消融，
见 ``framework/tests/test_v18_region_clear.py`` 与 ``script/bench_q34.py``。
"""

from __future__ import annotations

from dataclasses import dataclass

from .client import SimulatorClient
from .geometry import Point
from .knowledge_layer import ChannelKnowledge, RegionClearPlan, plan_beats_measure, region_clear_plan
from .protocol import MOVE_SPEED_MPS, OPTICAL_RANGE_M
from .state import DogState
from .strategy_v15 import Q3V15Params, Q3V15Strategy
from .strategy_v17 import Q4V17Params, Q4V17Strategy

__all__ = [
    "Q3V18Params",
    "Q3V18Strategy",
    "Q4V18Params",
    "Q4V18Strategy",
]


class _CoverClearMixin:
    """覆盖式清除的公共实现（问题3 与问题4 共用）。

    依赖宿主类提供：``params``（含 region_clear_* 字段）、``knowledge``、
    ``_region``、``_estimate``、``_move``、``_clear``、``_budget_exhausted``、
    ``_choose_refine_point``、``state`` 语义。
    """

    #: 覆盖计划的运行期统计（写入 ``diagnostics``）
    def _init_cover_stats(self) -> None:
        self.cover_stats: dict[str, float] = {
            "plans": 0.0,           # 前置（代价可接受）计划数
            "fallbacks": 0.0,       # 后置兜底计划数
            "probes": 0.0,          # 计划里的清除探针总数
            "cleared": 0.0,         # 由计划直接清掉的源数
            "cells_excluded": 0.0,  # 被排除圆安全剔除的格数
            "guaranteed_runs": 0.0,  # 完整执行、覆盖性质成立的计划数
            "region_violated": 0.0,  # 完整执行却没命中 ⟹ 可行域不含真值（口径被证伪）
            "budget_truncated": 0.0,  # 因预算中断而未能走完的计划数
        }

    # -- 排除圆 ----------------------------------------------------------
    def _exclusions(self, channel: int) -> list[tuple[Point, float]]:
        if not getattr(self.params, "use_failed_clear_exclusions", False):
            return []
        return self.knowledge.exclusion_disks(
            channel, getattr(self.params, "region_clear_exclusion_radius_m", OPTICAL_RANGE_M)
        )

    # -- 计划构造 --------------------------------------------------------
    def _region_plan(self, state: DogState, channel: int, *, max_probes: int | None):
        ch_state = state.channels[channel]
        if ch_state.is_cleared:
            return None
        region = self._region(ch_state)
        if len(region) < 3:
            return None
        return region_clear_plan(
            region,
            state.position,
            tol_m=OPTICAL_RANGE_M,
            step_m=getattr(self.params, "region_clear_step_m", 25.0),
            exclusions=self._exclusions(channel),
            max_probes=max_probes,
            reach_factor=getattr(self.params, "region_clear_reach_factor", 1.35),
        )

    def _next_measure_travel(self, state: DogState, channel: int) -> float:
        """"再测一次"要走的距离：优先用策略自己会选的补测点。"""
        ch_state = state.channels[channel]
        cand: Point | None = None
        choose = getattr(self, "_choose_refine_point", None)
        if callable(choose):
            try:
                cand = choose(state, ch_state)
            except Exception:  # noqa: BLE001 - 补测点只是代价模型里的参考
                cand = None
        if cand is None:
            cand = self._estimate(ch_state)
        if cand is None:
            return 0.0
        return state.position.distance_to(cand)

    # -- 计划执行 --------------------------------------------------------
    def _execute_cover_plan(
        self,
        client: SimulatorClient,
        state: DogState,
        channel: int,
        plan: RegionClearPlan,
        *,
        reason: str,
    ) -> bool:
        """依次走到每个格心试清。返回是否清掉该源。

        计划被完整执行且 :attr:`RegionClearPlan.guaranteed` 为真时，这次清除**必然**
        命中真值（覆盖论证见 :mod:`mathmodel2026b.knowledge_layer`）。
        """
        self.cover_stats["probes"] += plan.n_probes
        ran = 0
        for q in plan.centers:
            if self._budget_exhausted(state):
                plan.completed = False
                self.cover_stats["budget_truncated"] += 1
                break
            if state.channels[channel].is_cleared:
                break
            self._move(state, q)
            ran += 1
            if self._clear(client, state, q, channel, reason=reason):
                if plan.completed:
                    self.cover_stats["guaranteed_runs"] += 1
                self.cover_stats["cleared"] += 1
                return True
        if ran < plan.n_probes:
            plan.completed = False
        elif plan.guaranteed:
            # 走完了全部格心仍未清掉 ⟹ 真值不在（当前口径下的）可行域内。
            # 覆盖性质本身成立（格完整、格心可达），但"真值 ∈ F"这个前提被证伪，
            # 如实记一笔，供诊断与后续射线回退。
            self.cover_stats["region_violated"] += 1
        return False

    def _try_cover_clear(
        self,
        client: SimulatorClient,
        state: DogState,
        channel: int,
        *,
        reason: str,
        max_probes: int,
        cost_gated: bool,
    ) -> bool:
        """构造并（可选地按代价模型筛选后）执行一次覆盖清除计划。"""
        if not getattr(self.params, "use_region_clear", False):
            return False
        plan = self._region_plan(state, channel, max_probes=max_probes)
        if plan is None:
            return False
        if cost_gated and not plan_beats_measure(
            plan,
            measure_travel_m=self._next_measure_travel(state, channel),
            speed_mps=MOVE_SPEED_MPS,
        ):
            return False
        self.cover_stats["plans" if cost_gated else "fallbacks"] += 1
        self.cover_stats["cells_excluded"] += plan.cells_excluded
        self.last_cover_plan = plan
        if len(self.cover_log) < 256:
            self.cover_log.append((channel, reason, plan.as_dict()))
        return self._execute_cover_plan(client, state, channel, plan, reason=reason)


# ---------------------------------------------------------------------------
# 问题3：v15 + 知识矩阵 + 覆盖式清除
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Q3V18Params(Q3V15Params):
    #: 把观测喂进 ``KnowledgeMatrix``（失败清除圆 + 格去重台账）
    use_knowledge_matrix: bool = True
    #: 知识矩阵的路径列量化步长（米），与 ``dataType.txt`` 口径一致
    knowledge_cell_m: float = 120.0
    #: 失败清除圆的半径（米）= 光学作用距离
    region_clear_exclusion_radius_m: float = OPTICAL_RANGE_M
    #: 是否启用"覆盖式清除"
    use_region_clear: bool = True
    #: 方格边长（米）；必须满足 step/√2 ≤ 20
    region_clear_step_m: float = 25.0
    #: 前置阶段（走直接清之前）允许的最大清除点数
    region_clear_max_probes: int = 2
    #: 后置兜底（前面全失败之后）允许的最大清除点数
    region_clear_fallback_max_probes: int = 64
    #: 格心可达范围倍数（相对目标区域半径）
    region_clear_reach_factor: float = 1.35
    #: 是否用失败清除圆做格剔除
    use_failed_clear_exclusions: bool = True


class Q3V18Strategy(_CoverClearMixin, Q3V15Strategy):
    """问题3 的 v18：v15（no_signal 排除 + 修正后的 or-opt）叠加覆盖式清除。"""

    def __init__(self, params: Q3V18Params | None = None) -> None:
        self.params: Q3V18Params = params or Q3V18Params()
        super(Q3V18Strategy, self).__init__(self.params)
        self.knowledge = ChannelKnowledge(cell_m=self.params.knowledge_cell_m)
        self._init_cover_stats()
        self.last_cover_plan: RegionClearPlan | None = None
        self.cover_log: list[tuple[int, str, dict]] = []

    # -- 观测入账 --------------------------------------------------------
    def _measure(self, client, state, point, channel, reason=""):
        result = super(Q3V18Strategy, self)._measure(client, state, point, channel, reason=reason)
        if self.params.use_knowledge_matrix:
            self.knowledge.record_measure(
                point,
                channel,
                kind=result.kind.value,
                bearing_deg=result.svd_deg,
                virtual_time_s=result.virtual_time_s,
            )
        return result

    def _clear(self, client, state, point, channel, reason="") -> bool:
        ok = super(Q3V18Strategy, self)._clear(client, state, point, channel, reason=reason)
        if self.params.use_knowledge_matrix:
            if ok:
                self.knowledge.record_clear_success(point, channel)
            else:
                self.knowledge.record_clear_failed(point, channel)
        return ok

    # -- 覆盖式清除的两个介入点 -------------------------------------------
    def _after_direct_clear_failed(self, client, state, channel) -> bool:
        """直接清失败后：若覆盖计划在固定耗时上不吃亏就直接执行。

        k ≤ 2 时计划固定耗时 5/8s < "再测一次 + 再清一次" 的 11s，
        且带覆盖保证；k = 3 打平，由路程判据决定。
        """
        return self._try_cover_clear(
            client,
            state,
            channel,
            reason="cover-direct",
            max_probes=self.params.region_clear_max_probes,
            cost_gated=True,
        )

    def _localize_and_clear(self, client: SimulatorClient, state: DogState, channel: int) -> None:
        if state.channels[channel].is_cleared:
            return
        # v15 的完整流程（直接清 → [扩展点] → 补读数 → no_signal 排除 → 精定位）
        super(Q3V18Strategy, self)._localize_and_clear(client, state, channel)
        if state.channels[channel].is_cleared:
            return
        # 兜底：完整覆盖保守可行域（保证性清除）
        self._try_cover_clear(
            client,
            state,
            channel,
            reason="cover-fallback",
            max_probes=self.params.region_clear_fallback_max_probes,
            cost_gated=False,
        )

    # -- 诊断 ------------------------------------------------------------
    def diagnostics(self, state: DogState) -> dict:
        data = super(Q3V18Strategy, self).diagnostics(state)
        data["cover"] = dict(self.cover_stats)
        data["knowledge"] = (
            self.knowledge.summary_line() if self.params.use_knowledge_matrix else None
        )
        if self.last_cover_plan is not None:
            data["last_cover_plan"] = self.last_cover_plan.as_dict()
        return data


# ---------------------------------------------------------------------------
# 问题4：v17 + 知识矩阵 + 覆盖式清除（交付版）
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Q4V18Params(Q4V17Params):
    #: 把观测喂进 ``KnowledgeMatrix``（失败清除圆 + 格去重台账）
    use_knowledge_matrix: bool = True
    #: 知识矩阵的路径列量化步长（米），与 ``dataType.txt`` 口径一致
    knowledge_cell_m: float = 120.0
    #: 失败清除圆的半径（米）= 光学作用距离；定向源同样严格成立
    region_clear_exclusion_radius_m: float = OPTICAL_RANGE_M
    #: 是否启用"覆盖式清除"
    use_region_clear: bool = True
    #: 方格边长（米）；必须满足 step/√2 ≤ 20
    region_clear_step_m: float = 25.0
    #: 前置阶段允许的最大清除点数（代价判据同时生效）
    region_clear_max_probes: int = 3
    #: 后置兜底允许的最大清除点数
    region_clear_fallback_max_probes: int = 64
    #: 格心可达范围倍数
    region_clear_reach_factor: float = 1.35
    #: 是否用失败清除圆做格剔除
    use_failed_clear_exclusions: bool = True


class Q4V18Strategy(_CoverClearMixin, Q4V17Strategy):
    """问题4 的 v18（交付版）：v17 在线机制 + 知识矩阵负例 + 覆盖式清除。

    与 v17 的差异只有三处，都可单独消融：

    * ``use_knowledge_matrix``：把 ``/clear`` 失败转成 20m 排除圆并做格台账。
    * ``use_region_clear``：在"直接清/射线推进"之前按代价模型判断是否直接执行
      覆盖计划；全部失败后再无条件执行一次覆盖兜底（``region_clear_fallback_*``）。
    * ``use_failed_clear_exclusions``：剔除被排除圆完全包含的格。
    """

    def __init__(self, params: Q4V18Params | None = None) -> None:
        self.params: Q4V18Params = params or Q4V18Params()
        super(Q4V18Strategy, self).__init__(self.params)
        self.knowledge = ChannelKnowledge(cell_m=self.params.knowledge_cell_m)
        self._init_cover_stats()
        self.last_cover_plan: RegionClearPlan | None = None
        self.cover_log: list[tuple[int, str, dict]] = []

    # -- 观测入账 --------------------------------------------------------
    def _measure(self, client, state, point, channel, reason=""):
        result = super(Q4V18Strategy, self)._measure(client, state, point, channel, reason=reason)
        if self.params.use_knowledge_matrix:
            self.knowledge.record_measure(
                point,
                channel,
                kind=result.kind.value,
                bearing_deg=result.svd_deg,
                virtual_time_s=result.virtual_time_s,
            )
        return result

    def _clear(self, client, state, point, channel, reason="") -> bool:
        ok = super(Q4V18Strategy, self)._clear(client, state, point, channel, reason=reason)
        if self.params.use_knowledge_matrix:
            if ok:
                self.knowledge.record_clear_success(point, channel)
            else:
                self.knowledge.record_clear_failed(point, channel)
        return ok

    # -- 覆盖式清除的两个介入点 -------------------------------------------
    def _after_direct_clear_failed(self, client, state, channel) -> bool:
        """直接清失败后：若覆盖计划在固定耗时上不吃亏就直接执行。

        定向源的可行域常常是比对完 ±1° 锥之后剩下的细长条；直接清失败说明
        估计点偏离 >20m，而这条细长条往往只需要 1~3 个 20m 圆就能盖住 ——
        这时"覆盖计划"比"再走一趟补读数"更便宜，也更可靠。
        """
        return self._try_cover_clear(
            client,
            state,
            channel,
            reason="cover-direct",
            max_probes=self.params.region_clear_max_probes,
            cost_gated=True,
        )

    def _localize_and_clear(self, client: SimulatorClient, state: DogState, channel: int) -> None:
        if state.channels[channel].is_cleared:
            return
        # v17 的完整流程（直接清 → [扩展点] → 射线推进 → 基类 → 15m 环形试探）
        super(Q4V18Strategy, self)._localize_and_clear(client, state, channel)
        if state.channels[channel].is_cleared:
            return
        # 兜底：覆盖整个保守可行域（复核里 97/100 → 100/100 的那一步）
        self._try_cover_clear(
            client,
            state,
            channel,
            reason="cover-fallback",
            max_probes=self.params.region_clear_fallback_max_probes,
            cost_gated=False,
        )

    # -- 诊断 ------------------------------------------------------------
    def diagnostics(self, state: DogState) -> dict:
        data = super(Q4V18Strategy, self).diagnostics(state)
        data["cover"] = dict(self.cover_stats)
        data["knowledge"] = (
            self.knowledge.summary_line() if self.params.use_knowledge_matrix else None
        )
        if self.last_cover_plan is not None:
            data["last_cover_plan"] = self.last_cover_plan.as_dict()
        return data
