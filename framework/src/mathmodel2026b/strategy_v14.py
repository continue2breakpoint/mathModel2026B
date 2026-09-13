"""问题4 的 v14：v9 两圈式发现层 + **沿射线推进**（walk-the-ray）精定位。

v9 的漏清根因（``_q4_fail_diag.py``，seeds 6/11/12/26 全同型）：
定向源 R≈1000~1100、只被 1~2 个发现层节点听到，可行域是细长楔形（直径 100~1500m）。
所有补救探点（region-interior / frontline / mirror / fallback）都取在可行域内部或
估计点附近——但**源朝向≈指向当初听到它的那个顶点**，可行域内部的点大多在源的
**背面**（`(p-G)·u(φ) <= 0`），于是反复 no_signal、可行域不收缩，单信道烧掉
51~127 次检测仍漏清。

v14 的结构修复：
1. **沿射线推进**（正确性证明）：设 A 为最近一次成功读数的顶点，u 为该读数的
   示向单位向量，源 G = A + t_G·u + ε（横向 ε 来自 ±1° 量化，≤0.0175·t_G）。
   源的正面方向 u(φ) 满足 u(φ)·(A−G) > 0（A 处有读数）⟹ u(φ)·u < 0。
   射线上的点 q(t) = A + t·u：
   * t < t_G：q−G = (t−t_G)u + ... 与 A−G 同向 ⇒ 正面；且 |q−G| ≤ |A−G| ≤ R ⇒ **必出读数**；
   * t > t_G：(q−G)·u(φ) = (t−t_G)(u·u(φ)) < 0 ⇒ 背面 ⇒ **必 no_signal**（定向源）。
   于是"步进 + no_signal 二分回退"在 O(log) 次检测内把源距压到小区间。
   对全向源同样安全（无正面约束，no_signal ⟺ 越过 R，仍是上界）。
2. **地毯式清除**：区间收敛到 <40m 后，在区间内 0.2/0.5/0.8 分数点直接 /clear
   —— 清除判据（≤20m）**不要求正面**，只要求距离；区间内任一点到最近分数点
   ≤ 0.3×区间长 + 横向 ε < 20m，必命中。这根治"越过源后 near 判据永远失败"
   的残留漏清（seed 12 实测 2.2m 距离仍 no_signal 的情形）。
3. **同点免重测/免重清备忘录**：同一 (频道, 检测点) 的读数与清除结果是确定的
   （附录2-1），重复是纯浪费——v9 失败种子里同一批点被反复测 4+ 遍。
4. **大可行域信道的调度锚点 = 射线入口点**（v14.1）：楔形域质心可偏离真值数百
   米，调度器把机器人派到质心只会白走一趟；改为派到"可行域在射线上的投影区间
   的中点"，机器人到达后立即开始沿射线二分。同时这类信道**先走射线再直接清**，
   避免先去坏估计点的往返。

其余机制（两圈式 25 节点朝向完备发现层、任务合并调度、小可行域直接清、范围剪枝）
与 v9 相同。
"""

# NOTE(端口适配): 用显式两参 super(Cls, self) 而不是零参 super()。
# @dataclass(slots=True) 会为子类重建类对象，零参 super() 依赖的 __class__
# 单元格会指向**原始**类，于是 'Q3V8Params' 实例在 Q3V5Params 的方法里调用
# super() 时会抛 TypeError（CPython 文档 dataclasses 章节明确警告）。
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from .client import SimulatorClient
from .geometry import Point, polygon_diameter, unit_from_deg
from .state import DogState
from .strategy import Budget
from .strategy_v9 import Q4V9Params, Q4V9Strategy


@dataclass(slots=True)
class Q4V14Params(Q4V9Params):
    #: 可行域直径超过该值且未清除时触发沿射线推进（正常 2 读数全向源直径 ~30-60m）
    ray_trigger_diameter_m: float = 100.0
    #: 沿射线推进的最大检测次数
    ray_max_steps: int = 12
    #: 步进上限（相对区间宽度）
    ray_step_max_m: float = 450.0
    #: 区间收敛阈值（小于该值转地毯式清除）
    ray_carpet_width_m: float = 40.0
    #: 同点免重测（确定性误差下重复测量无信息量）
    dedup_measures: bool = True
    #: 大可行域信道的调度锚点用射线入口点（实验性；False=估计点，30 种子更优）
    ray_entry_scheduling: bool = False
    #: 大可行域信道先走射线再直接清（实验性；False=直接清优先，30 种子更优）
    ray_first: bool = False


class Q4V14Strategy(Q4V9Strategy):
    """v14：沿射线推进精定位 + 免重测备忘录 + 射线入口调度。"""

    def __init__(self, params: Q4V14Params | None = None) -> None:
        self.params: Q4V14Params = params or Q4V14Params()
        super(Q4V14Strategy, self).__init__(self.params)
        #: (channel, gx, gy) -> 最近一次测量结果（0.5m 网格量化）
        self._measure_cache: dict[tuple[int, int, int], Any] = {}
        #: 已确认清除失败的 (channel, gx, gy)，确定性下不再重试
        self._failed_clears: set[tuple[int, int, int]] = set()

    # -- 同点免重测 / 免重清 -------------------------------------------------
    def _measure(self, client, state, point, channel, reason=""):
        if self.params.dedup_measures:
            key = (channel, round(point.x * 2.0), round(point.y * 2.0))
            if key in self._measure_cache:
                return self._measure_cache[key]
        result = super(Q4V14Strategy, self)._measure(client, state, point, channel, reason=reason)
        if self.params.dedup_measures:
            key = (channel, round(point.x * 2.0), round(point.y * 2.0))
            self._measure_cache[key] = result
        return result

    def _measured_before(self, channel: int, point: Point) -> bool:
        key = (channel, round(point.x * 2.0), round(point.y * 2.0))
        return key in self._measure_cache

    def _clear(self, client, state, point, channel, reason="") -> bool:
        """清除失败点备忘：同 (频道, 点) 的清除结果是确定的，失败后不再重试。"""
        if self.params.dedup_measures:
            key = (channel, round(point.x * 2.0), round(point.y * 2.0))
            if key in self._failed_clears:
                return False
        ok = super(Q4V14Strategy, self)._clear(client, state, point, channel, reason=reason)
        if self.params.dedup_measures and not ok:
            key = (channel, round(point.x * 2.0), round(point.y * 2.0))
            self._failed_clears.add(key)
        return ok

    # -- 射线几何 -----------------------------------------------------------
    def _region_diameter(self, ch_state) -> float:
        region = self._region(ch_state)
        return polygon_diameter(region) if len(region) >= 3 else 1500.0

    def _ray_bounds(self, ch_state) -> tuple[Point, float, float, float] | None:
        """返回 (apex, bearing, lo, hi)：源距 apex 的距离区间（可行域投影收紧）。

        源在可行域内 ⟹ 其在射线方向上的投影落在可行域顶点投影的 [min, max] 内。
        """
        if not ch_state.readings:
            return None
        apex, bearing = ch_state.readings[-1]
        u = unit_from_deg(bearing)
        region = self._region(ch_state)
        if len(region) >= 3:
            projs = [(v.x - apex.x) * u.x + (v.y - apex.y) * u.y for v in region]
            lo = max(0.0, min(projs))
            hi = max(projs)
            if hi <= lo:
                hi = lo + 1.0
        else:
            lo, hi = 0.0, 1500.0
        return apex, bearing, lo, hi

    def _ray_entry_point(self, ch_state) -> Point | None:
        """大可行域信道的调度锚点：射线上的第一个探测点。"""
        bounds = self._ray_bounds(ch_state)
        if bounds is None:
            return None
        apex, _bearing, lo, hi = bounds
        u = unit_from_deg(_bearing)
        t = lo + max(80.0, min(self.params.ray_step_max_m, (hi - lo) / 2.0))
        return Point(apex.x + t * u.x, apex.y + t * u.y)

    def _schedule_point(self, ch_state) -> Point | None:
        """调度锚点：默认估计点（直接清命中率高、路线扰动小）。

        楔形域质心虽可偏离真值数百米，但实验（30 种子消融）表明把锚点换成
        射线入口会扰动 2-opt 路线交织，总行驶反而增加；仅当显式开启
        ``ray_entry_scheduling`` 时使用射线入口。
        """
        est = self._estimate(ch_state)
        if est is None:
            return None
        if (
            self.params.ray_entry_scheduling
            and self._region_diameter(ch_state) > self.params.ray_trigger_diameter_m
        ):
            return self._ray_entry_point(ch_state) or est
        return est

    # -- 沿射线推进 --------------------------------------------------------
    def _walk_the_ray(self, client: SimulatorClient, state: DogState, channel: int) -> bool:
        """从最后成功读数的顶点沿读数方向步进逼近源（正确性见模块 docstring）。"""
        p = self.params
        ch_state = state.channels[channel]
        apex, bearing = ch_state.readings[-1]
        lo = 0.0  # 源距当前顶点的距离下界
        hi = math.inf  # 距离上界
        for _ in range(p.ray_max_steps):
            if self._budget_exhausted(state) or ch_state.is_cleared:
                return False

            # 可行域收敛（MEC<=20m）-> 直接清
            circle = self._clear_circle(ch_state)
            if circle is not None:
                self._move(state, circle.center)
                if self._clear(client, state, circle.center, channel, reason="ray-localized"):
                    return True

            # 可行域在射线上的投影收紧距离区间（源必在可行域内）
            region = self._region(ch_state)
            u = unit_from_deg(bearing)
            if len(region) >= 3:
                projs = [(v.x - apex.x) * u.x + (v.y - apex.y) * u.y for v in region]
                lo = max(lo, min(projs))
                hi = min(hi, max(projs))
            else:
                hi = min(hi, 1500.0)
            if hi <= lo:
                hi = lo + 1.0

            if hi - lo < p.ray_carpet_width_m:
                # 区间已缩无可缩。清除判据与朝向无关（<=20m 即可清），
                # 在区间内多个分数点尝试清除，只要有一点落在源 20m 内即成功。
                for frac in (0.5, 0.2, 0.8):
                    t = lo + frac * (hi - lo)
                    q = Point(apex.x + t * u.x, apex.y + t * u.y)
                    self._move(state, q)
                    if self._clear(client, state, q, channel, reason="ray-carpet"):
                        return True
                break

            t = lo + max(80.0, min(p.ray_step_max_m, (hi - lo) / 2.0))
            if math.isfinite(hi):
                t = min(t, hi)
            q = Point(apex.x + t * u.x, apex.y + t * u.y)
            if self._measured_before(channel, q):
                t = (lo + hi) / 2.0
                q = Point(apex.x + t * u.x, apex.y + t * u.y)
                if self._measured_before(channel, q):
                    break
            self._move(state, q)
            res = self._measure(client, state, q, channel, reason="ray")
            kind = res.kind.value
            if kind == "near":
                self._clear(client, state, q, channel, reason="near")
                return ch_state.is_cleared
            if kind == "direction":
                # 顶点前移、重新瞄准。关键：**区间平移继承**而非重置——
                # 源距新顶点的距离 ≈ 旧距离 − 前移量 t（同一射线上），重置会在
                # 近平行楔形（投影区间数百米）上从零二分、耗尽步数预算（seed 7）。
                # ±1° 量化与新旧射线方向差用 δ 裕量覆盖。
                delta = 0.05 * t + 10.0
                lo = max(0.0, lo - t - delta)
                hi = hi - t + delta if math.isfinite(hi) else math.inf
                if hi <= lo:
                    hi = lo + 1.0
                apex = q
                bearing = res.svd_deg if res.svd_deg is not None else bearing
                continue
            # no_signal：越过了源。源距 ∈ (lo, t)
            hi = t
        return False

    # -- 主精定位流程 ------------------------------------------------------
    def _localize_and_clear(self, client: SimulatorClient, state: DogState, channel: int) -> None:
        ch_state = state.channels[channel]
        if ch_state.is_cleared:
            return
        p = self.params

        # 1) 大可行域（楔形）：沿射线推进（默认在直接清之后，见 ray_first）
        if (
            ch_state.readings
            and not ch_state.is_cleared
            and self._region_diameter(ch_state) > p.ray_trigger_diameter_m
            and p.ray_first
        ):
            if self._walk_the_ray(client, state, channel):
                return

        # 2) 直接清优先（2 条以上好读数时估计点大概率 <=20m）
        if not ch_state.is_cleared and p.try_direct_clear:
            est = self._estimate(ch_state)
            if est is not None:
                self._move(state, est)
                if self._clear(client, state, est, channel, reason="direct"):
                    return
                self._measure(client, state, est, channel, reason="direct-fail")

        # 3) 大可行域（楔形）：沿射线推进（默认在直接清失败后触发）
        if (
            ch_state.readings
            and not ch_state.is_cleared
            and self._region_diameter(ch_state) > p.ray_trigger_diameter_m
            and not p.ray_first
        ):
            if self._walk_the_ray(client, state, channel):
                return

        # 4) 基类兜底精定位（正面锥受限的候选点 + no_signal 退路）
        if not ch_state.is_cleared:
            from .strategy import Q3Strategy

            Q3Strategy._localize_and_clear(self, client, state, channel)

    # -- 主循环（v7 的 2-opt 调度 + 射线入口锚点）---------------------------
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

    # -- 诊断 -------------------------------------------------------------
    def diagnostics(self, state: DogState) -> dict[str, Any]:
        data = super(Q4V14Strategy, self).diagnostics(state)
        data["measure_cache_size"] = len(self._measure_cache)
        return data
