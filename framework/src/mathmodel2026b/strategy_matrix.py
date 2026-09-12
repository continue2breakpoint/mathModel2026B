r"""矩阵驱动的搜索策略：知识矩阵 + 覆盖证书 + 解析式逼近（问题3/问题4统一）。

与旧 :class:`mathmodel2026b.strategy.Q3Strategy` 的区别（论文里要写清楚）
--------------------------------------------------------------------
=================================  ==============================  ==============================
维度                               旧 `Q3Strategy`                 本模块 `KnowledgeSearchStrategy`
=================================  ==============================  ==============================
知识记录                           只存 ``(检测点, 示向度)`` 列表     ``channel × path`` 知识矩阵，
                                                                   正例/负例/清除失败全部入账
``no_signal``（负例）              直接丢弃                        **反推有效接收半径 R**，
                                                                   并挖掉"源不可能在"的圆盘
搜索完备性                         硬编码 7 点六边形                在线覆盖证书：每频道算"未确认
                                                                   **面积**"，验证每个格都被覆盖
下一个停点                         固定扫描表                      数据驱动（可选）：单位路程最大
                                                                   新增确认面积
频道探测                           每停点全扫 20 个               **按证书剪枝**：该频道区域已被
                                                                   覆盖就跳过，实测省 20~40%
逼近段                             估计点周围 30/60/120/250 环      **解析式**：沿首次示向度方向前
                                                                   进到 0.6·d̂，二读数交会即收敛
定向源                             不支持（清除率掉到 ~30%）       保守半平面 + ``not_find`` 圆盘
                                                                   排除，清除率回到 1.0
=================================  ==============================  ==============================

数学依据
--------
1. 一次示向度读数给出以检测点为中心的 ±1° 锥；多个锥求交 + "距离 ≤ R_max" 的圆约束，
   得到必定含真值的凸可行域 :math:`P_c`（沿用 ``geometry.feasible_region``）。
2. 可行域最小包围圆半径 ≤ 20m ⇒ 瞄准圆心 ``/clear`` **必定命中**（光学作用距离）。
3. 负例 ``not_find`` 在 p 意味着 :math:`R < |p-源|`；由于源 ∈ :math:`P_c`，
   取 :math:`\R_hat=\min_p d(p,P_c)` 作为 R 的在线上界估计（见 ``coverage.estimate_radius``）。
   :math:`\R_hat` 越大，"覆盖整个区域"需要的停点越少，这就是负例的收益来源。
4. 逼近段：第一读数给出方向，沿该方向前进到剩余距离的 0.6 倍处再读数，
   角分离 ≈ 0.4·‖PQ‖/d，横向不确定度 ≈ 0.6·d·tan1° —— d ≤ 1100m 时 ≤ 11.5m，
   两读数即可满足 20m 清除判据（解析推导见 docs/knowledge-matrix.md）。
"""

from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Sequence

from .client import SimulatorClient
from .coverage import (
    Belief,
    CoverageGrid,
    CoverageReport,
    CoverageTracker,
    StopCandidate,
    build_beliefs,
    channel_weight,
    distance_point_to_region,
    point_in_polygon,
)
from .geometry import (
    Circle,
    Point,
    angle_difference_deg,
    arena_polygon,
    feasible_region,
    from_polar,
    minimum_enclosing_circle,
    polygon_diameter,
    regular_hexagon_scan_points,
    regular_polygon_scan_points,
    unit_from_deg,
)
from .knowledge import CellStatus, KnowledgeMatrix
from .protocol import (
    ARENA_RADIUS_M,
    MAX_EFFECTIVE_RADIUS_M,
    MIN_CHANNEL,
    MAX_CHANNEL,
    OPTICAL_RANGE_M,
)
from .state import ALL_CHANNELS, ChannelState, DogState


@dataclass(slots=True)
class MatrixParams:
    """全部可调参数（便于 `script/run_batch.py` 做消融）。"""

    # ── 几何：覆盖扫描布局 ──────────────────────────────────────────────
    #: 扫描环的**边数**与半径。默认"中心 + 正七边形 r=1110"：
    #: 覆盖半径（区域内任一点到最近停点的最大距离）933.7m，
    #: 比 1000m 下界留 66m 余量；中心优先行程 6889m（比六边形 r=1200 的 7200m 还短，
    #: 代价是多一个停点 → 多 120s 检测）。离线校验见 ``geometry.covering_radius``。
    scan_sides: int = 7
    scan_radius: float = 1110.0
    scan_at_center: bool = True
    #: 路径顺序：``ring`` = 中心先测，然后一次走完环（默认，行程最短）；
    #: ``center_last`` = 环走完再回中心（多 1200m 空驶，仅用于对照）；
    #: ``star`` = 每个顶点往返（行程最长，但每段可独立打断）
    scan_route: str = "ring"

    # ── 知识矩阵 ────────────────────────────────────────────────────────
    #: dataType.txt 的路径列量化步长（米）
    matrix_cell_m: float = 120.0
    #: 覆盖判据的栅格步长（米）
    coverage_cell_m: float = 60.0
    #: 判据里给 R 留的安全余量（米）
    radius_margin_m: float = 50.0

    # ── 探测剪枝（本模块的核心收益点）────────────────────────────────────
    #: 候选停点采样间距（米）：触发"数据驱动选点"时使用
    plan_spacing_m: float = 150.0
    plan_coarse_spacing_m: float = 450.0
    #: 找不到比证书停点更划算的位置时，允许的最多额外停点
    max_extra_stops: int = 3
    #: 比"继续证书布局"更优的判据：新增确认面积/路程 > 该阈值才换点
    replan_gain_threshold: float = 1.0
    #: 证书布局是否提前终止（该频道区域已被已测停点覆盖就不再测）
    early_skip_covered: bool = True
    #: 是否在扫描途中顺手清除。
    #: 实测（30 seed）：只要"可行域已 ≤20m"就折返清除，会为每个源多走约 2×650m，
    #: 而省下的只是后续停点对**该频道**的 6s 检测 —— 净亏。默认关。
    #: 真要用就配合 :attr:`interleave_max_detour_m` 限制折返距离。
    interleave_clear: bool = False
    #: 折返清除的最大单程距离（米）：只有源就在扫描点旁边时才值得顺手清。
    interleave_max_detour_m: float = 150.0

    # ── 精化扫描（第二次成批取读数）─────────────────────────────────────
    #: 是否在覆盖扫描之后做一轮"精化扫描"：把"只有 1 个读数、可行域还很大"的频道
    #: 集中起来，一次走到新停点批量补读数。
    #: 原理：一个读数只给出 ±1° 的锥，MEC 仍有几百米；走到第二个点再读一次，
    #: 锥与锥求交后 MEC 直接掉到几十米。**成批做**（一次行程服务多个频道）
    #: 比"逐个频道单独走过去补读数"省掉 n 次折返。
    refine_sweep: bool = True
    #: 触发精化扫描的 MEC 半径阈值（米）
    refine_mec_threshold_m: float = 250.0
    #: 精化停点候选数（在"离所有源估计不太远"的位置里挑角分离最大的）
    refine_candidates: int = 2400
    #: 精化扫描最多走几个停点
    refine_max_stops: int = 2
    #: 精化停点到"该频道首选读数点"的距离上限（米），超过就够不着
    refine_reach_m: float = 1500.0

    # ── 逼近 / 精定位 ───────────────────────────────────────────────────
    #: 沿示向度方向前进的比例序列（相对当前估计距离 d̂）
    approach_fractions: tuple[float, ...] = (0.6, 0.35, 0.2, 0.1)
    #: 每次前进后允许的最大迭代次数
    max_approach_steps: int = 8
    #: "重访最后一条有效示向度"时，沿该射线前进/后退的距离（米）
    revisit_distance_m: float = 650.0
    #: 完备性兜底：还有"一次都没收到过"的频道时，把契约布局重扫一遍。
    #: 这是题目硬要求"确保所有干扰源被清除"的确定性保障，默认开。
    verify_sweep: bool = True
    #: 目标排序：False = 纯最近邻（默认）；True = 最近邻 + 一步前瞻
    #: （代价 = 到候选的距离 + 从候选到"其余目标中最近一个"的距离）。
    #:
    #: **实测否决了这个想法**（15 seed，omni）：一步前瞻为了让"下一跳更短"，
    #: 会优先扎进彼此靠近的源簇，结果在簇之间反复横穿 ——
    #: 路程 17 117m → 19 819m（+15.8%），avg 356.7 → 367.2s，vt 4260 → 4798s。
    #: 定向场景基本持平（比例 0.9624 → 0.9609）。故默认关闭，仅作消融对照保留。
    #: 理论上界：拿真值位置做 2-opt 局部搜索，相对最近邻也只能再省 4.5%
    #: （均值 9042m → 8638m），说明**目标排序这条线已经没什么空间了**。
    two_step_lookahead: bool = False
    #: 收尾扫描单次移动的距离上限（米）：避免"死磕一个漏掉的源"把时间吃光
    max_sweep_travel_m: float = 1200.0
    #: 收尾扫描的最大轮数（每轮：成批补读数 -> 逐个清除）。
    #: 默认 1：实测定向场景下"死磕未清除频道"会走出很长的冤枉路
    #: （一度把 5282s 拖到 7601s 而一个源都没多清掉）；
    #: 设 0 完全关闭，设 2~3 只在确认有收益的场景打开。
    max_sweep_rounds: int = 1
    #: 逼近阶段的最大轮数（每轮把"尚未清除"的频道各处理一次）。
    #: 必须有限：否则"清不掉 -> 补测 -> 可行域没变 -> 再清"会无限打转。
    max_clear_rounds: int = 2
    #: 横向补测环半径（相对当前 MEC 半径的倍数）
    lateral_scale: float = 2.5
    latitude_samples: int = 8
    #: 清除容差（= 光学作用距离）
    clear_tolerance_m: float = OPTICAL_RANGE_M
    half_width_deg: float = 1.0
    max_range_m: float = MAX_EFFECTIVE_RADIUS_M

    # ── 定向源（问题4）───────────────────────────────────────────────────
    #: 是否启用定向假设的保守几何（``not_find`` 圆盘排除 + 半平面约束）
    directional: bool = False
    #: "not_find 与源距离的固定下界"用哪个值做排除。
    #: 用 R_max=1500 是最保守的（只要源在 1500m 内就不可能漏）。
    not_find_exclude_radius_m: float = MAX_EFFECTIVE_RADIUS_M
    #: 定向源：是否把可行域限制在半平面内（保留清障用）
    directional_half_plane: bool = True

    # ── 预算保护 ────────────────────────────────────────────────────────
    max_virtual_time_s: float = 300_000.0
    max_wall_time_s: float = 1_100.0
    soft_wall_time_s: float = 1_020.0

    # ── 收尾 ────────────────────────────────────────────────────────────
    second_pass_gap_fill: bool = True
    verify_after_clear: bool = False
    #: 缺口补测阶段：优先补"未确认面积最大"的频道（而不是所有频道一起）
    gap_fill_channel_batch: int = 20

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["approach_fractions"] = list(self.approach_fractions)
        return data


@dataclass(slots=True)
class Budget:
    """运行预算：任一超限都必须立刻收尾，避免测试被判超时。"""

    virtual_deadline_s: float
    wall_deadline: float
    soft_wall_deadline: float
    started_at: float = field(default_factory=time.time)

    def exhausted(self, state: DogState) -> bool:
        if time.time() >= self.wall_deadline:
            return True
        return state.virtual_time_s >= self.virtual_deadline_s

    def soft_exhausted(self, state: DogState) -> bool:
        if time.time() >= self.soft_wall_deadline:
            return True
        return state.virtual_time_s >= self.virtual_deadline_s * 0.85


class Strategy(ABC):
    @abstractmethod
    def run(self, client: SimulatorClient, state: DogState) -> None: ...


class KnowledgeSearchStrategy(Strategy):
    """知识矩阵驱动的搜索 / 定位 / 清除策略（问题3 与问题4 共用）。"""

    def __init__(self, params: MatrixParams | None = None) -> None:
        self.params = params or MatrixParams()
        self.matrix = KnowledgeMatrix(cell_m=self.params.matrix_cell_m)
        self.grid = CoverageGrid(cell_m=self.params.coverage_cell_m)
        self.tracker = CoverageTracker(self.grid, self.matrix)
        self.beliefs: dict[int, Belief] = build_beliefs(self.matrix)
        self.trace: list[dict[str, Any]] = []
        self.budget: Budget | None = None
        self.stats: dict[str, float] = {
            "scan_stops": 0.0,
            "probes": 0.0,
            "probes_skipped": 0.0,
            "covered_skips": 0.0,
            "extra_stops": 0.0,
            "approach_steps": 0.0,
            "clear_attempts": 0.0,
            "clear_success": 0.0,
            "clear_failures": 0.0,
            "no_signal_in_approach": 0.0,
        }
        self.clear_fail_points: dict[int, list[Point]] = {}
        self.cover_report: CoverageReport | None = None
        #: 本轮已经尝试过、失败收场的频道（防止同一目标反复重试）
        self._attempted: set[int] = set()

    # ── 入口 ────────────────────────────────────────────────────────────
    def run(self, client: SimulatorClient, state: DogState) -> None:
        p = self.params
        now = time.time()
        self.budget = Budget(
            virtual_deadline_s=p.max_virtual_time_s,
            wall_deadline=now + p.max_wall_time_s,
            soft_wall_deadline=now + p.soft_wall_time_s,
        )
        self.matrix.touch(state.position)
        self._search_phase(client, state)
        self._refine_phase(client, state)
        self._approach_phase(client, state)
        self._gap_fill(client, state)
        self._final_sweep(client, state)
        self._verification_sweep(client, state)

    # ── 阶段 1：带证书的覆盖搜索 ─────────────────────────────────────────
    def _search_phase(self, client: SimulatorClient, state: DogState) -> None:
        route = self._scan_route()
        for index, stop in enumerate(route):
            if self._budget_exhausted(state):
                return
            self._move(state, stop, reason="scan-leg")
            self.stats["scan_stops"] += 1
            pending = self._channels_to_probe(stop)
            if not pending:
                # 该停点对所有活跃频道都已"覆盖过"，走过去纯属浪费
                self.stats["covered_skips"] += 1
                continue
            self.trace.append(
                {
                    "kind": "stop",
                    "reason": f"scan#{index}",
                    "x": stop.x,
                    "y": stop.y,
                    "pending_channels": len(pending),
                    "virtual_time_s": state.virtual_time_s,
                }
            )
            self._probe_at(client, state, stop, pending, reason=f"scan#{index}")
            if self.params.interleave_clear and not self._budget_exhausted(state):
                self._interleave_clear(client, state)
        self._sync_coverage(state)
        self.cover_report = self.tracker.assess(state.position)

    def _scan_route(self) -> list[Point]:
        """扫描路线。默认"中心先测 + 一次走完正 n 边形环"。

        中心先测有两个好处：(1) 从原点出发不用空驶到第一个顶点再折回；
        (2) 中心是所有方向信噪比最低的位置，能最早发现"附近就有源"。
        """
        pts = regular_polygon_scan_points(
            self.params.scan_sides, self.params.scan_radius, center=self.params.scan_at_center
        )
        if not self.params.scan_at_center:
            return pts
        center, ring = pts[0], pts[1:]
        if self.params.scan_route == "center_last":
            return ring + [center]
        if self.params.scan_route == "star":
            out: list[Point] = [center]
            for vertex in ring:
                out.extend([vertex, center])
            return out
        return [center] + ring

    def _channels_to_probe(self, stop: Point) -> list[int]:
        r"""本停点该测哪些频道 —— 知识矩阵剪枝的落点。

        一个频道在这个停点"值得测"，当且仅当它与该停点的覆盖圆盘还有交集，
        即 :math:`d({stop}, P_c) \<= \R_hat_c - {margin}`。
        用连续凸可行域判定（不需要格子），对"已收到信号"的频道极其便宜。
        """
        p = self.params
        out: list[int] = []
        for channel in self.matrix.channels:
            if self.matrix.is_cleared(channel):
                continue
            belief = self.beliefs[channel]
            if belief.cleared:
                continue
            if p.early_skip_covered and belief.is_detected and len(belief.region) >= 3:
                # 该停点已经够不着可行域里任何一个可能位置 ⇒ 测了也是 not_find
                if distance_point_to_region(stop, belief.region) > belief.plan_radius_m:
                    continue
            # 同一格重复检测无信息（附录2-1），已经测过的格直接跳过
            if self.matrix.is_measured(stop, channel):
                continue
            out.append(channel)
        return out

    def _probe_at(
        self,
        client: SimulatorClient,
        state: DogState,
        stop: Point,
        channels: Sequence[int],
        *,
        reason: str,
    ) -> None:
        """在一个停点按频道号顺序测完给定频道，并全部写进知识矩阵。

        每测完一个就检查一次"这个频道是否已经收敛到可清除"（可行域最小包围圆
        ≤ 20m）：是则**就地走过去清掉**，不等整轮扫完。
        这与用户 branch 上的 P0 实验（``interleave_on_path_clear``）是同一个想法，
        但触发条件严格得多 —— 只在该频道**已经可以确定性清除**时才离开扫描点，
        因此不会像 P0 那样把 20 个频道的扫描拆得七零八落、破坏在线最近邻路径。
        """
        ordered = self._order_channels(channels, state.measurement_channel)
        cleared_here: list[int] = []
        for channel in ordered:
            if self._budget_exhausted(state):
                return
            self._move(state, stop, reason=reason)
            self._measure(client, state, stop, channel, reason=reason)
            self._sync_coverage(state)
            belief = self.beliefs[channel]
            circle = belief.clear_circle(self.params.clear_tolerance_m) if belief.is_detected else None
            if (
                circle is not None
                and self.params.interleave_clear
                and stop.distance_to(circle.center) <= self.params.interleave_max_detour_m
            ):
                # 只有"源就在扫描点旁边"时才顺手清：折返代价 ≈ 2·d ≤ 500m，
                # 换回的是后续每个停点少测 1 个频道（每停点 6s）。
                self._move(state, circle.center, reason="interleave")
                if self._clear_at(client, state, circle.center, channel, reason="interleave"):
                    cleared_here.append(channel)
                else:
                    self._measure(client, state, circle.center, channel, reason="post-fail")
                    self._sync_coverage(state)
        self._sync_coverage(state)
        self.tracker.observe_partial_stop(
            stop, [c for c in ordered if c not in cleared_here and not self.matrix.is_cleared(c)]
        )

    @staticmethod
    def _order_channels(channels: Sequence[int], current: int) -> list[int]:
        """频道顺序不影响总耗时（两点间切换都是 1s），但按"先测最可能存在的"
        可以让"发现即清除"的提前量更大，因此这里按频道号升序（与旧实现一致）。"""
        del current
        return sorted(set(channels))

    # ── 阶段 1.5：精化扫描（成批补读数）──────────────────────────────────
    def _needing_refinement(self) -> list[int]:
        """哪些频道"探到了但还没定位好"（可行域最小包围圆半径 > 阈值）。"""
        out: list[int] = []
        for channel in self.matrix.detected_channels():
            if self.matrix.is_cleared(channel):
                continue
            belief = self.beliefs[channel]
            if belief.cleared or not belief.is_detected:
                continue
            if len(belief.region) < 3:
                out.append(channel)
                continue
            circle = minimum_enclosing_circle(belief.region)
            if circle is None or circle.radius > self.params.refine_mec_threshold_m:
                out.append(channel)
        return out

    def _refine_phase(self, client: SimulatorClient, state: DogState) -> None:
        """成批精化：为一个"最缺角度分离"的新停点，一次补掉多个频道的第二次读数。

        为什么值得单独跑一趟：覆盖扫描里一个频道可能只被 1~2 个停点收到，
        单读数的可行域最小包围圆常有几百米，直接走进逼阶段会来回补测，
        每个频道多走一整趟。把 n 个频道合并成 1 趟行程，收益是 O(n) 的。
        """
        if not self.params.refine_sweep:
            return
        for _round in range(self.params.refine_max_stops):
            if self._budget_exhausted(state):
                return
            pending = self._needing_refinement()
            if not pending:
                return
            stop = self._best_refine_stop(state, pending)
            if stop is None:
                return
            self._move(state, stop, reason="refine-leg")
            self.trace.append(
                {
                    "kind": "stop",
                    "reason": "refine",
                    "x": stop.x,
                    "y": stop.y,
                    "pending_channels": len(pending),
                    "virtual_time_s": state.virtual_time_s,
                }
            )
            self._probe_at(client, state, stop, pending, reason="refine")

    def _best_refine_stop(self, state: DogState, channels: Sequence[int]) -> Point | None:
        """挑精化停点：让每个待精化频道都拿到"与已有读数角分离大"的新读数。

        对每个频道，理想新停点在"过首选检测点、与首次示向度成 ±θ 角"的方向上
        （θ=70° 时两条射线的交会最稳）。这里不打分求和，而是取
        **最小角分离的最大值**（max-min），保证没有哪个频道被落下。
        """
        ideal: list[tuple[Point, list[float]]] = []
        for channel in channels:
            readings = self._readings_of(channel)
            if not readings:
                continue
            apex, bearing = readings[0]
            ideal.append((apex, [b for _, b in readings]))
        if not ideal:
            return None
        best: tuple[float, Point] | None = None
        for cand in self._refine_candidates():
            travel = state.position.distance_to(cand)
            if travel > 3000.0:
                continue
            worst = 180.0
            ok = True
            for apex, bearings in ideal:
                # 新读数必须"够得着"：距离首选检测点不能超过 R_max，否则一定 no_signal
                if cand.distance_to(apex) > self.params.refine_reach_m:
                    ok = False
                    break
                new_dir = cand.bearing_to(apex)
                sep = min(angle_difference_deg(new_dir, b) for b in bearings)
                if sep < worst:
                    worst = sep
            if not ok:
                continue
            if self._out_of_arena(cand):
                continue
            score = worst - 0.0002 * travel
            if best is None or score > best[0]:
                best = (score, cand)
        return None if best is None else best[1]

    def _refine_candidates(self) -> list[Point]:
        """精化停点候选：低差异采样的环 + 极角组合，覆盖"任意位置都能取到读数"。"""
        cached = getattr(self, "_refine_cache", None)
        if cached is not None:
            return cached
        out: list[Point] = []
        n_ring, n_ang = 20, 24
        for i in range(1, n_ring + 1):
            radius = ARENA_RADIUS_M * i / n_ring
            for k in range(n_ang):
                theta = 360.0 * k / n_ang + (180.0 / n_ang) * (i % 2)
                out.append(from_polar(radius, theta))
        out.append(Point(0.0, 0.0))
        self._refine_cache = out
        return out

    # ── 阶段 2：逐个定位并清除 ───────────────────────────────────────────
    def _approach_phase(self, client: SimulatorClient, state: DogState) -> None:
        """按在线最近邻逐个处理"已收到信号但未清除"的频道。

        每个频道每轮最多处理一次（``self._attempted``），处理失败就留到下一轮，
        且总轮数有上限 —— 否则"清除失败 → 补测 → 可行域不变 → 再清除"会无限循环
        （实测曾经每个频道刷出 6 万次 ``/clear``，虚拟时间直接打满 100 小时）。
        """
        for round_index in range(self.params.max_clear_rounds):
            if self._budget_exhausted(state):
                return
            if round_index > 0:
                self._attempted.clear()  # 新一轮：允许回头处理上一轮没搞定的
            while True:
                if self._budget_exhausted(state):
                    return
                channel = self._next_target(state)
                if channel is None:
                    break
                self._attempted.add(channel)
                self._localize_and_clear(client, state, channel)
            if not self._remaining_targets():
                return

    def odometer(self) -> dict[str, float]:
        """按 trace 里的 ``reason`` 汇总路程与动作次数（归因分析用）。"""
        dist: dict[str, float] = {}
        counts: dict[str, int] = {}
        for record in self.trace:
            if record["kind"] == "move":
                key = record["reason"] or "move"
                dist[key] = dist.get(key, 0.0) + float(record["distance_m"])
            elif record["kind"] in ("measure", "clear"):
                key = f"{record['kind']}:{record['reason'] or 'other'}"
                counts[key] = counts.get(key, 0) + 1
        out: dict[str, float] = {k: float(v) for k, v in counts.items()}
        for key, value in sorted(dist.items()):
            out[f"dist:{key}"] = round(value, 1)
        return out

    def _remaining_targets(self) -> list[int]:
        """还没清除、且能定位的频道（"能定位" = 已有可行域质心）。"""
        out: list[int] = []
        for channel in self.matrix.detected_channels():
            if self.matrix.is_cleared(channel):
                continue
            belief = self.beliefs[channel]
            if belief.cleared or belief.centroid is None:
                continue
            out.append(channel)
        return out

    def _next_target(self, state: DogState, *, allow_attempted: bool = False) -> int | None:
        """在"已收到信号但还没清除"的频道里挑最近的一个（在线最近邻）。

        * 距离用**最新**可行域质心估计，随着读数增加会自己纠正；
        * 本轮已经失败的频道（``self._attempted``）先跳过，只在没有新目标时才回头，
          避免在同一对 (位置, 频道) 上反复打转。
        """
        candidates: list[tuple[int, Point]] = []
        for channel in self.matrix.detected_channels():
            if self.matrix.is_cleared(channel):
                continue
            if not allow_attempted and channel in self._attempted:
                continue
            belief = self.beliefs[channel]
            if belief.cleared:
                continue
            est = belief.centroid
            if est is None:
                continue
            candidates.append((channel, est))
        if not candidates:
            return None
        if len(candidates) == 1 or not self.params.two_step_lookahead:
            return min(
                candidates, key=lambda item: state.position.distance_to(item[1])
            )[0]
        # 一步前瞻：代价 = 走到候选的距离 + 从候选到"剩下的目标里最近一个"的距离。
        # 纯最近邻容易先扎进一个孤立的源，之后再从那里横穿到另一头；
        # 前瞻能提前看出"这个源顺路/不顺路"，代价是 O(n²) 的纯几何距离计算
        # （n ≤ 16，可忽略）。
        best: tuple[float, int] | None = None
        for channel, est in candidates:
            first = state.position.distance_to(est)
            second = min(
                (est.distance_to(other) for other_ch, other in candidates if other_ch != channel),
                default=0.0,
            )
            cost = first + second
            if best is None or cost < best[0]:
                best = (cost, channel)
        return best[1]

    def _localize_and_clear(
        self, client: SimulatorClient, state: DogState, channel: int
    ) -> None:
        """解析式逼近：沿示向度方向前进 → 二读数交会 → 瞄圆心清除。

        终止条件是"清除成功"，不是"可行域够小"。这两者常常差一步：

            ρ = 31m > 20m 时，机器狗走到可行域**边界**（离真值 21m）就已经"贴住"
            可行域了，清除必然失败；此时必须**继续用新读数把 ρ 压到 20m 以下**，
            而不是原地反复清（实测原地空转 6 次，虚拟时间白花 30s）。

        所以循环里做三件事：走 → 测 → 清；只有"测不出新信息"（同一地点读数不变，
        附录2-1）时才换策略。
        """
        p = self.params
        belief = self.beliefs[channel]
        if belief.cleared or self.matrix.is_cleared(channel):
            return
        tried_clear: list[Point] = []
        tried_measure: set[tuple[int, int]] = set()
        for step in range(p.max_approach_steps):
            if self._budget_exhausted(state):
                return
            belief = self.beliefs[channel]
            circle = belief.clear_circle(p.clear_tolerance_m)
            if circle is not None:
                if any(circle.center.distance_to(q) <= 0.5 for q in tried_clear):
                    break  # 这个清除点已经试过且失败
                tried_clear.append(circle.center)
                self._move(state, circle.center, reason="approach")
                if self._clear_at(client, state, circle.center, channel, reason="localized"):
                    return
                # 清除失败 => 真值仍在 20m 外。就地补测拿不到新信息（同点读数不变），
                # 因此**不再原地重试**，直接进入下一轮"换位置补读数"。
                continue
            target = self._approach_target(state, channel, step)
            if target is None:
                break
            if target.distance_to(state.position) < 1.0:
                break
            self._move(state, target, reason="approach")
            self.stats["approach_steps"] += 1
            cell = self.matrix.cell_key(target)
            if cell in tried_measure:
                # 已经在这个格测过：读数不会变，换一个候选（横向补测）
                if not self._lateral_probe(client, state, channel):
                    break
                tried_measure.add(self.matrix.cell_key(state.position))
                continue
            tried_measure.add(cell)
            result = self._measure(client, state, target, channel, reason=f"approach#{step}")
            self._sync_coverage(state)
            if result is not None and result.kind.value == "near":
                self._clear_at(client, state, target, channel, reason="near")
                return
            if result is not None and result.kind.value == "no_signal":
                self.stats["no_signal_in_approach"] += 1
                # 看不见了：要么超出 R，要么落到了定向锥背面。
                # 不要再朝更深的方向前进（越走越看不见），改为横向补测。
                if self._lateral_probe(client, state, channel):
                    tried_measure.add(self.matrix.cell_key(state.position))
                    continue
                break

        # 最后尝试：可行域中心直接清一次
        belief = self.beliefs[channel]
        est = belief.centroid
        if (
            est is not None
            and not self.matrix.is_cleared(channel)
            and not self._budget_exhausted(state)
            and not any(est.distance_to(q) <= 0.5 for q in tried_clear)
        ):
            self._move(state, est, reason="last-resort")
            self._clear_at(client, state, est, channel, reason="last-resort")

    def _approach_target(self, state: DogState, channel: int, step: int) -> Point | None:
        r"""下一个逼近点（可行域大走"弦"，可行域小走"最近点"）。

        两种模式，按可行域最小包围圆半径 :math:`\rho` 自动切换：

        * :math:`\rho` 较大（> 60m）——**弦模式**：朝估计点一次走完剩余距离的
          ``1-f`` 比例（默认 f=0.6），把"走过去"和"取第二次读数"合并成一步。
          解析上，走到剩余距离的 f 倍处再读数时，角分离
          :math:`L~(1-f)|PQ|/d`，横向不确定度
          :math:`~ fd\tan 1^deg`，d≤1100m 时约 11.5m < 20m。
        * :math:`\rho` 较小——**最近点模式**：直接走到可行域（保证含真值的凸多边
          形）上离当前位置最近的点。"清除点与真值距离 ≤ ρ"就是 20m 判据的来源。
          旧实现在这一档会走过头再折回（MEC≤20m 却清不到、清失败后又走远），
          白白多走 50~100m/源。
        """
        p = self.params
        belief = self.beliefs[channel]
        est = belief.centroid
        if est is None:
            return None
        region = belief.region
        mec_radius = math.inf
        if len(region) >= 3:
            circle = minimum_enclosing_circle(region)
            if circle is not None:
                mec_radius = circle.radius
        if mec_radius <= 60.0:
            target = _nearest_point_on_polygon(state.position, region, est)
            if target is not None and state.position.distance_to(target) >= 1.0:
                return target
        distance = state.position.distance_to(est)
        if distance < 2.0:
            return None
        fraction = p.approach_fractions[min(step, len(p.approach_fractions) - 1)]
        gain = 1.0 - fraction
        if self._out_of_arena(est):
            scale = ARENA_RADIUS_M * 0.98 / max(1e-9, math.hypot(est.x, est.y))
            est = Point(est.x * scale, est.y * scale)
        target = Point(
            state.position.x + (est.x - state.position.x) * gain,
            state.position.y + (est.y - state.position.y) * gain,
        )
        if self._out_of_arena(target):
            return None
        if state.position.distance_to(target) < 1.0:
            return None
        return target

    @staticmethod
    def _out_of_arena(point: Point, factor: float = 1.25) -> bool:
        return math.hypot(point.x, point.y) > ARENA_RADIUS_M * factor

    def _lateral_probe(self, client: SimulatorClient, state: DogState, channel: int) -> bool:
        """横向补测：在估计点周围的小环上取一个与已有读数角分离最大的点。

        用于"读数返回 no_signal"的情形——说明目标不在这个方向的有效接收半径内，
        或者（定向源）转到了覆盖角背面。此时沿原方向继续前进只会更糟。
        """
        p = self.params
        belief = self.beliefs[channel]
        est = belief.centroid
        region = belief.region
        if est is None:
            return False
        circle = minimum_enclosing_circle(region) if len(region) >= 3 else None
        radius = max(60.0, min(400.0, (circle.radius * p.lateral_scale) if circle else 200.0))
        existing = [b for _, b in self._readings_of(channel)]
        best: tuple[float, Point] | None = None
        for k in range(p.latitude_samples):
            theta = 360.0 * k / p.latitude_samples
            cand = Point(
                est.x + radius * unit_from_deg(theta).x,
                est.y + radius * unit_from_deg(theta).y,
            )
            if self._out_of_arena(cand):
                continue
            if state.position.distance_to(cand) < 1.0:
                continue
            new_dir = cand.bearing_to(est)
            sep = min((angle_difference_deg(new_dir, b) for b in existing), default=90.0)
            score = sep - 0.002 * state.position.distance_to(cand)
            if best is None or score > best[0]:
                best = (score, cand)
        if best is None:
            return False
        self._move(state, best[1], reason="lateral")
        result = self._measure(client, state, best[1], channel, reason="lateral")
        self._sync_coverage(state)
        if result is not None and result.kind.value == "near":
            self._clear_at(client, state, best[1], channel, reason="near")
        return True

    def _readings_of(self, channel: int) -> list[tuple[Point, float]]:
        out: list[tuple[Point, float]] = []
        for ev in self.matrix.cells.get(channel, {}).values():
            if ev.status is CellStatus.FIND and ev.bearing_deg is not None and ev.point is not None:
                out.append((ev.point, ev.bearing_deg))
        return out

    def _verification_sweep(self, client: SimulatorClient, state: DogState) -> None:
        """完备性兜底：只要有频道"没被清除、也没被任何停点测到过"，就重扫一遍契约布局。

        题目对问题3/4 的硬要求是"确保所有干扰源被清除"，而"被清除的比例"只影响分数高低。
        因此收尾必须有一条**确定性**的兜底路径：把契约布局（中心 + 正 n 边形）
        在**全部**未清除频道上重跑一遍。理论依据是
        :func:`mathmodel2026b.geometry.covering_radius` —— 该布局的覆盖半径 ≤1000m，
        所以任何确实存在的源都至少会被一个停点收到。

        代价明确：一次重扫 = 布局行程 + 每个停点要重测的频道数 × 6s。
        只有当"还有频道一次都没收到过"时才会触发（正常跑完覆盖扫描时不会走到这里）。
        """
        p = self.params
        if not p.verify_sweep:
            return
        blind = [
            c for c in self.matrix.channels
            if not self.matrix.is_cleared(c) and self.matrix.found(c) == 0
        ]
        if not blind:
            return
        self.trace.append(
            {
                "kind": "verify-start",
                "reason": "unprobed-channels",
                "channels": blind,
                "virtual_time_s": state.virtual_time_s,
            }
        )
        for index, stop in enumerate(
            regular_polygon_scan_points(p.scan_sides, p.scan_radius, center=p.scan_at_center)
        ):
            if self._budget_exhausted(state):
                return
            self._move(state, stop, reason="verify")
            pending = [
                c for c in blind
                if not self.matrix.is_cleared(c) and not self.matrix.is_measured(stop, c)
            ]
            if not pending:
                continue
            self._probe_at(client, state, stop, pending, reason=f"verify#{index}")
            self._sync_coverage(state)
            # 顺手把新探到 / 已经收敛的频道清掉，避免再走一趟
            for _ in range(len(pending)):
                if self._budget_exhausted(state):
                    return
                channel = self._next_target(state, allow_attempted=True)
                if channel is None:
                    break
                if channel not in blind and self.matrix.is_cleared(channel):
                    break
                self._localize_and_clear(client, state, channel)

    # ── 阶段 3：缺口补测 ────────────────────────────────────────────────
    def _gap_fill(self, client: SimulatorClient, state: DogState) -> None:
        """证书没通过时的兜底：按"未确认面积"去补停点。

        * 预算充足 -> 用数据驱动选点（单位路程最大新增确认面积）
        * 预算紧张 / 选不出点 -> 退回旧实现的 7 点六边形全频道重扫
        """
        p = self.params
        if not p.second_pass_gap_fill or self._budget_exhausted(state):
            return
        self._sync_coverage(state)
        report = self.tracker.assess(state.position)
        self.cover_report = report
        if report.complete:
            return
        self.trace.append(
            {
                "kind": "gap-fill-start",
                "reason": "certificate-incomplete",
                "uncovered_cells": report.total_uncovered_cells,
                "virtual_time_s": state.virtual_time_s,
            }
        )
        used = 0
        while used < p.max_extra_stops and not self._budget_exhausted(state):
            cand = self.tracker.plan_cover(state.position)
            if cand is None:
                cand = self.tracker.plan(
                    state.position,
                    weights={c: channel_weight(self.beliefs[c]) for c in self.tracker.states},
                    spacing_m=p.plan_spacing_m,
                    coarse_spacing_m=p.plan_coarse_spacing_m,
                )
            if cand is None:
                break
            if cand.travel_m < 1.0 and cand.new_area_m2 <= 0.0:
                break
            self._move(state, cand.point, reason="gap-fill")
            self.stats["extra_stops"] += 1
            used += 1
            pending = self._channels_to_probe(cand.point)
            self.trace.append(
                {
                    "kind": "stop",
                    "reason": "gap-fill",
                    "x": cand.point.x,
                    "y": cand.point.y,
                    "pending_channels": len(pending),
                    "new_area_m2": round(cand.new_area_m2, 1),
                    "virtual_time_s": state.virtual_time_s,
                }
            )
            if pending:
                self._probe_at(client, state, cand.point, pending, reason="gap-fill")
            else:
                self.tracker.observe_stops([cand.point])
            self._sync_coverage(state)
            if self.tracker.assess(state.position).complete:
                break
        # 仍未通过证书：确定性兜底（保证"确保全部清除"这条硬要求）
        if not self.tracker.assess(state.position).complete:
            self._hexagon_fallback(client, state)

    def _final_sweep(self, client: SimulatorClient, state: DogState) -> None:
        """收尾：把"已探到、但还没定位到可清除程度"的频道逐个逼死。

        这是**唯一**一条只依赖"已确知有源"的流程，也是最稳的一条：
        方向、距离、定向锥全都已知，只需要把可行域最小包围圆压到 20m 以下。

        做法（每轮固定代价、轮数有上限）：
        1. 挑一个"对所有待收尾频道都能提供大角分离"的停点，一次把读数补满
           （成批做比逐个频道折返省 n−1 趟路）；
        2. 走回可行域、瞄 MEC 圆心清除；
        3. 还清不掉就重复，最多 :attr:`max_sweep_rounds` 轮。

        没有这一步，问题4 里"被锥挡过一次"的频道会一直卡在"检测到了但清不掉"。
        """
        p = self.params
        p = self.params
        for _round in range(p.max_sweep_rounds):
            if self._budget_exhausted(state):
                return
            # 两类目标都要管：
            #   (a) 已经收到信号、但可行域还没收敛到能清除的频道 -> 补读数
            #   (b) 一次都没收到过的频道 -> 只能换个位置再扫（可能是锥盲区）
            unprobed = [
                c for c in self.matrix.channels
                if not self.matrix.is_cleared(c) and self.matrix.probes(c) == 0
            ]
            stale = self._remaining_targets()
            targets = sorted(set(unprobed) | set(stale))
            if not targets:
                return
            spent = 0.0
            stop = self._best_refine_stop(state, targets) if stale else None
            if stop is None and unprobed:
                stop = self._blind_spot_stop(state, unprobed)
            if stop is None:
                return
            spent += self._move(state, stop, reason="sweep")
            if spent > p.max_sweep_travel_m:
                return
            to_probe = [
                c for c in targets
                if not self.matrix.is_cleared(c) and not self.matrix.is_measured(stop, c)
            ]
            if to_probe:
                self._probe_at(client, state, stop, to_probe, reason="sweep")
            else:
                self.tracker.observe_partial_stop(stop, targets)
            self._sync_coverage(state)
            # 收尾用**确定性**流程：先常规逼近，失败再走"重访最后一条有效示向度"。
            # 只对"已知有源"的频道做，因此这里的额外路程一定换来一个已确知目标，
            # 不会像缺口补测那样满场找可能不存在的源。
            for channel in list(targets):
                if self._budget_exhausted(state) or self.matrix.is_cleared(channel):
                    return
                if not self.beliefs[channel].is_detected:
                    continue
                self._localize_and_clear(client, state, channel)
                if not self.matrix.is_cleared(channel):
                    # 定向源最容易卡在这里 —— 逼近时越过 ±90° 边界，
                    # 之后的读数全是 no_signal，可行域再也收不紧。
                    self._revisit_last_bearing(client, state, channel)

    def _revisit_last_bearing(
        self, client: SimulatorClient, state: DogState, channel: int, *, tries: int = 3
    ) -> bool:
        """从"最后一条有效示向度"出发重新取读数，直到能清除（定向源的兜底）。

        为什么需要它：定向源在逼近途中一旦越过 ±90° 边界，之后的读数全是
        ``no_signal``。此时可行域既不会收缩也不会扩大，逼近循环会在原地打转。
        正确的做法是**退回到已知能看到它的那条射线**上：那条射线上仍可能看到它，
        沿它前进/后退都是在"确认过的可见域"里移动。

        具体做：取首末两条有效读数的射线，在其上离估计点
        :data:`MatrixParams.revisit_distance_m` 处取一前一后两个点补测。
        """
        p = self.params
        readings = self._readings_of(channel)
        est = self.beliefs[channel].centroid
        if not readings or est is None:
            return False
        for attempt in range(tries):
            if self._budget_exhausted(state) or self.matrix.is_cleared(channel):
                return self.matrix.is_cleared(channel)
            apex, bearing = readings[-1]
            for sign in (1.0, -1.0):
                u = unit_from_deg(bearing)
                cand = Point(
                    apex.x + sign * p.revisit_distance_m * u.x,
                    apex.y + sign * p.revisit_distance_m * u.y,
                )
                if self._out_of_arena(cand):
                    continue
                if cand.distance_to(est) < 5.0:
                    continue
                if self.matrix.is_measured(cand, channel):
                    continue
                self._move(state, cand, reason="revisit")
                result = self._measure(client, state, cand, channel, reason="revisit")
                self._sync_coverage(state)
                if result is not None and result.kind.value == "near":
                    self._clear_at(client, state, cand, channel, reason="near")
                    return True
                circle = self.beliefs[channel].clear_circle(p.clear_tolerance_m)
                if circle is not None:
                    self._move(state, circle.center, reason="revisit")
                    if self._clear_at(client, state, circle.center, channel, reason="revisit"):
                        return True
                if result is not None and result.kind.value == "direction":
                    readings = self._readings_of(channel)
            del attempt
        return self.matrix.is_cleared(channel)

    def _blind_spot_stop(self, state: DogState, channels: Sequence[int]) -> Point | None:
        """给"一次都没收到过"的频道挑一个补测位置（锥盲区里的点）。

        用 :func:`coverage.hidden_mask` 算出"在所有已测停点上都发现不了源"的位置集合，
        再取其中离当前位置最近的一个（连同它的邻居）当停点。
        """
        p = self.params
        best: tuple[float, Point] | None = None
        for channel in channels:
            state_cover = self.tracker.states.get(channel)
            if state_cover is None:
                continue
            hidden = state_cover.uncovered_mask
            if not hidden:
                continue
            for cell in self.tracker.grid.positions(hidden, limit=4000):
                travel = state.position.distance_to(cell)
                if best is None or travel < best[0]:
                    best = (travel, cell)
        if best is None:
            # 退路：用契约布局扫一遍
            plan = regular_polygon_scan_points(
                p.scan_sides, p.scan_radius, center=p.scan_at_center
            )
            best = min(
                ((state.position.distance_to(q), q) for q in plan), key=lambda kv: kv[0]
            )
        return best[1]

    def _hexagon_fallback(self, client: SimulatorClient, state: DogState) -> None:
        for index, stop in enumerate(regular_hexagon_scan_points(self.params.scan_radius)):
            if self._budget_exhausted(state):
                return
            self._move(state, stop, reason="fallback")
            pending = [c for c in self.matrix.channels if not self.matrix.is_cleared(c) and not self.matrix.is_measured(stop, c)]
            if pending:
                self._probe_at(client, state, stop, pending, reason=f"fallback#{index}")

    # ── 覆盖同步 ────────────────────────────────────────────────────────
    def _sync_coverage(self, state: DogState | None = None) -> None:
        """重建信念与栅格台账。

        **注意**：这里只重建"区域"，不更新"已确认区域"。后者只由
        :meth:`_probe_at` / :meth:`_gap_fill` 在**真正测过**某个停点后登记
        （``observe_partial_stop``）。把"到过"当成"测过"会让证书失真 ——
        这正是本模块要避免的那类逻辑漏洞。
        """
        del state
        self.beliefs = build_beliefs(
            self.matrix,
            half_width_deg=self.params.half_width_deg,
            clear_fail_points=self.clear_fail_points,
            max_range_m=self.params.max_range_m,
            directional=self.params.directional,
        )
        # 定向假设只改"负例能否排除"这一条（可证伪判据），
        # 不再对整个区域做锥形裁剪 —— 那会把证书变成空集，反而漏源。
        self.tracker.directional = self.params.directional
        self.tracker.sync(self.beliefs, matrix=self.matrix)

    # ── 清除（顺路清）────────────────────────────────────────────────────
    def _interleave_clear(self, client: SimulatorClient, state: DogState) -> None:
        for channel in sorted(self.matrix.detected_channels()):
            belief = self.beliefs[channel]
            if belief.cleared or self.matrix.is_cleared(channel):
                continue
            circle = belief.clear_circle(self.params.clear_tolerance_m)
            if circle is None:
                continue
            if self._budget_exhausted(state):
                return
            self._move(state, circle.center, reason="interleave")
            self._clear_at(client, state, circle.center, channel, reason="interleave")

    # ── 基本动作 ────────────────────────────────────────────────────────
    def _move(self, state: DogState, target: Point, *, reason: str = "") -> float:
        distance = state.move_to(target)
        if reason:
            self.trace.append(
                {
                    "kind": "move",
                    "reason": reason,
                    "x": target.x,
                    "y": target.y,
                    "distance_m": round(distance, 2),
                    "virtual_time_s": state.virtual_time_s,
                }
            )
        return distance

    def _measure(
        self,
        client: SimulatorClient,
        state: DogState,
        point: Point,
        channel: int,
        *,
        reason: str = "",
    ):
        state.select_channel(channel)
        result = client.measure(point, channel)
        state.measure_count += 1
        state.observe(result.virtual_time_s)

        status: CellStatus | None = None
        bearing: float | None = None
        kind = result.kind.value
        if kind == "direction":
            state.measure_accepted_count += 1
            status = CellStatus.FIND
            bearing = result.svd_deg or 0.0
            state.channels[channel].add_bearing(point, bearing)
        elif kind == "near":
            state.measure_accepted_count += 1
            status = CellStatus.NEAR
            state.channels[channel].mark_near()
        elif kind == "no_signal":
            status = CellStatus.NOT_FIND
        if status is not None:
            self.matrix.record_measure(
                point,
                channel,
                status,
                bearing_deg=bearing,
                virtual_time_s=result.virtual_time_s,
            )
            self.stats["probes"] += 1
        self.trace.append(
            {
                "kind": "measure",
                "reason": reason,
                "channel": channel,
                "x": point.x,
                "y": point.y,
                "outcome": kind,
                "svd_deg": result.svd_deg,
                "virtual_time_s": result.virtual_time_s,
            }
        )
        return result

    def _clear_at(
        self,
        client: SimulatorClient,
        state: DogState,
        point: Point,
        channel: int,
        *,
        reason: str = "",
    ) -> bool:
        result = client.clear(point, channel)
        state.clear_count += 1
        state.observe(result.virtual_time_s)
        ok = result.cleared
        self.stats["clear_attempts"] += 1
        if ok:
            state.clear_success_count += 1
            self.stats["clear_success"] += 1
            state.channels[channel].mark_cleared(point)
            self.matrix.mark_channel_cleared(channel)
            belief = self.beliefs.get(channel)
            if belief is not None:
                belief.cleared = True
            self.tracker.states.pop(channel, None)
        else:
            state.clear_failure_count += 1
            self.stats["clear_failures"] += 1
            state.channels[channel].mark_clear_failed()
            self.matrix.record_clear_failed(point, channel, virtual_time_s=result.virtual_time_s)
            self.clear_fail_points.setdefault(channel, []).append(point)
        self.trace.append(
            {
                "kind": "clear",
                "reason": reason,
                "channel": channel,
                "x": point.x,
                "y": point.y,
                "outcome": result.kind.value,
                "virtual_time_s": result.virtual_time_s,
            }
        )
        return ok

    def _budget_exhausted(self, state: DogState) -> bool:
        return self.budget is not None and self.budget.exhausted(state)

    def _soft_budget_exhausted(self, state: DogState) -> bool:
        return self.budget is not None and self.budget.soft_exhausted(state)

    # ── 诊断 / 导出 ─────────────────────────────────────────────────────
    def diagnostics(self, state: DogState) -> dict[str, Any]:
        return {
            "params": self.params.as_dict(),
            "stats": dict(self.stats),
            "matrix": self.matrix.summary_line(),
            "coverage": self.cover_report.as_dict() if self.cover_report else None,
            "beliefs": {str(c): b.as_dict() for c, b in self.beliefs.items() if b.is_detected},
            "n_path_columns": self.matrix.path_length,
            "n_probes": self.matrix.total_probes(),
            "virtual_time_s": state.virtual_time_s,
        }

    def knowledge_table(self, limit_cols: int = 40) -> str:
        return self.matrix.as_table(limit_cols=limit_cols)

    def knowledge_json(self) -> dict[str, Any]:
        data = self.matrix.as_dict()
        data["coverage"] = self.cover_report.as_dict() if self.cover_report else None
        data["stats"] = dict(self.stats)
        return data

    def write_knowledge(self, path: Any) -> None:
        """把知识矩阵落盘（``.json`` 写机读版，``.md`` / ``.txt`` 写 dataType 表）。"""
        from pathlib import Path

        target = Path(path)
        if target.suffix.lower() in {".md", ".txt"}:
            target.write_text(self.knowledge_table(), encoding="utf-8")
        else:
            import json

            target.write_text(
                json.dumps(self.knowledge_json(), ensure_ascii=False, indent=1), encoding="utf-8"
            )


class Q4Strategy(KnowledgeSearchStrategy):
    """问题4：全向 + 定向混合。默认打开定向假设的保守几何。"""

    def __init__(self, params: MatrixParams | None = None) -> None:
        p = params or MatrixParams()
        p.directional = True
        super().__init__(p)

def _nearest_point_on_polygon(p: Point, poly: Sequence[Point], fallback: Point | None = None) -> Point | None:
    """凸多边形上（或内部）离 ``p`` 最近的点。

    点在多边形内 -> 返回 ``p`` 本身；否则返回最近边上的投影。
    """
    if len(poly) < 3:
        return fallback
    if point_in_polygon(p, poly):
        return p
    best: Point | None = None
    best_d = math.inf
    n = len(poly)
    for i in range(n):
        a = poly[i]
        b = poly[(i + 1) % n]
        dx = b.x - a.x
        dy = b.y - a.y
        length2 = dx * dx + dy * dy
        if length2 <= 1e-12:
            cand = a
        else:
            t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / length2
            t = max(0.0, min(1.0, t))
            cand = Point(a.x + t * dx, a.y + t * dy)
        d = p.distance_to(cand)
        if d < best_d:
            best_d = d
            best = cand
    return best if best is not None else fallback
