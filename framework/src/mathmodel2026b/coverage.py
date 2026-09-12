"""覆盖完备性证书与"下一个停点"规划：把知识矩阵变成可计算的决策。

这一层回答整个搜索里最重要的一个问题：

    还有没有哪块区域，可能藏着某个还没清除的干扰源，而机器狗一次都没测到？

只有这个问题能回答"是/否"的时候，"确保所有干扰源被清除"才不是一句空话。
原来的实现把这件事**硬编码**成 7 个固定扫描点（``geometry.q3_seven_point_cover_valid``），
一旦要省时间（少测几个频道、少走几个点）就没有判据可用了；而且它对
``no_signal``（负例）完全没有利用，等于把有效接收半径 R 的先验永远锁死在最坏值 1000m。
本模块把它变成**在线可计算**的量。

三条核心公式
------------
1. **有效接收半径 R 的在线夹逼**——负例的唯一用途

   一个 ``not_find`` 观测在点 p，若该点本可以测到源，则必有 ``R < |p - 源|``。
   源位置未知但落在可行域 :math:`P_c` 内，取可行域内离 p 最近的点做保守估计：

   .. math:: \\hat R=\\mathrm{clip}\\!\\left(\\min_{p\\in \\text{not\\_find}} d(p,P_c),\\;R_{\\min},\\;R_{\\max}\\right)

   没有负例时只能退回 :math:`R_{\\min}=1000` m —— 这正是旧实现 7 点覆盖半径必须
   ≤1000m 的原因。有了负例，:math:`\\hat R` 常常能抬到 1200~1500m，
   覆盖同一片区域需要的停点就少得多。

2. **覆盖判据（保守、可复算）**

   规划时假设源可能落在 :math:`P_c` 内任意一点。"停点集合 S 必定发现该频道"
   等价于 :math:`P_c` 被半径 :math:`\\hat R` 的圆盘族覆盖。本模块用**栅格化**实现：

   * 把 :math:`P_c` 离散成 60m 栅格（在位掩码上做集合运算，快）
   * 一个格子被停点 s 覆盖，当且仅当该格**四个角**到 s 的距离都 ≤
     :attr:`CoverageGrid.radius_m`（角点包含 ⇒ 整格包含，凸性保证）；
     这使判据对连续区域是**严格保守**的
   * :math:`\\hat R` 上再留 :data:`RADIUS_SAFETY_MARGIN_M` 的余量

3. **下一个停点 = 单位路程的最大"新增确认面积"**

   .. math:: s^\\star=\\arg\\max_s \\frac{\\sum_c w_c\\,|U_c\\cap D(s,\\hat R)|}{\\text{travel}(s)}

   其中 :math:`U_c` 是频道 c 尚未被确认的区域，:math:`w_c` 见 :func:`channel_weight`。
   "已经确认过的面积"收益为 0，所以不会为了重复确认白跑路。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .geometry import (
    Point,
    _clip_half_plane,  # noqa: PLC2701 - 复用现成的半平面裁剪
    arena_polygon,
    clip_by_bearing,
    clip_by_circle,
    minimum_enclosing_circle,
    polygon_area,
    polygon_centroid,
    regular_hexagon_scan_points,
)
from .knowledge import CellStatus, KnowledgeMatrix
from .protocol import (
    ARENA_RADIUS_M,
    MAX_EFFECTIVE_RADIUS_M,
    MIN_EFFECTIVE_RADIUS_M,
)

#: 覆盖判据给 R 留的安全余量（米）。规划用 ``R_hat - margin``。
RADIUS_SAFETY_MARGIN_M: float = 50.0
#: 覆盖栅格步长（米）。60m 时整个 3600m 的包围盒只有 60×60=3600 格，
#: 位掩码集合运算可以忽略不计；格子角点包含判定让判据严格保守。
GRID_CELL_M: float = 60.0
#: 覆盖判据里允许的"残留未确认面积"（m²）。0 表示严格。
#: 默认留 1 个格子：单格残余在 60m 栅格下等价于"最多一格没看"
RESIDUAL_TOLERANCE_CELLS: int = 0


# ---------------------------------------------------------------------------
# 每频道的信念状态
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Belief:
    """一个频道的可行域 + R 估计 + 证据计数。"""

    channel: int
    #: 保守可行域（凸多边形）。空 = 还没收到过信号
    region: list[Point] = field(default_factory=list)
    #: 在线估计的有效接收半径（米）
    radius_hat_m: float = MIN_EFFECTIVE_RADIUS_M
    #: R 估计的来源（论文里要解释"凭什么敢用 1300m"）
    radius_source: str = "prior_min"
    #: 覆盖判据实际使用的半径 = max(300, radius_hat - margin)
    plan_radius_m: float = MIN_EFFECTIVE_RADIUS_M - RADIUS_SAFETY_MARGIN_M
    n_find: int = 0
    n_not_find: int = 0
    near: bool = False
    cleared: bool = False

    @property
    def area_m2(self) -> float:
        return polygon_area(self.region)

    @property
    def centroid(self) -> Point | None:
        return polygon_centroid(self.region)

    @property
    def is_detected(self) -> bool:
        return self.n_find > 0 or self.near

    def clear_circle(self, tolerance_m: float) -> Any:
        """可行域最小包围圆半径 ≤ tolerance 时的瞄准圆（否则 None）。"""
        circle = minimum_enclosing_circle(self.region)
        if circle is not None and circle.radius <= tolerance_m:
            return circle
        return None

    def as_dict(self) -> dict[str, Any]:
        c = self.centroid
        return {
            "channel": self.channel,
            "area_m2": round(self.area_m2, 1),
            "radius_hat_m": round(self.radius_hat_m, 1),
            "radius_source": self.radius_source,
            "plan_radius_m": round(self.plan_radius_m, 1),
            "n_find": self.n_find,
            "n_not_find": self.n_not_find,
            "near": self.near,
            "cleared": self.cleared,
            "centroid": [round(c.x, 1), round(c.y, 1)] if c else None,
        }


# ---------------------------------------------------------------------------
# 几何小工具
# ---------------------------------------------------------------------------
def point_in_polygon(point: Point, poly: Sequence[Point]) -> bool:
    """凸多边形内的点测试（含边界）。"""
    n = len(poly)
    if n < 3:
        return False
    sign = 0
    for i in range(n):
        a = poly[i]
        b = poly[(i + 1) % n]
        cross = (b.x - a.x) * (point.y - a.y) - (b.y - a.y) * (point.x - a.x)
        if abs(cross) < 1e-9:
            continue
        s = 1 if cross > 0 else -1
        if sign == 0:
            sign = s
        elif s != sign:
            return False
    return True


def distance_point_to_region(point: Point, region: Sequence[Point]) -> float:
    """点到凸多边形的最小距离；点在多边形内返回 0。"""
    if not region:
        return math.inf
    if len(region) < 3:
        return min(point.distance_to(p) for p in region)
    if point_in_polygon(point, region):
        return 0.0
    best = math.inf
    n = len(region)
    for i in range(n):
        best = min(best, _distance_point_segment(point, region[i], region[(i + 1) % n]))
    return best


def max_distance_to_region(point: Point, region: Sequence[Point]) -> float:
    """点到凸多边形的最大距离（凸性 ⇒ 顶点取到）。"""
    return max((point.distance_to(p) for p in region), default=0.0)


def region_min_distance(a: Sequence[Point], b: Sequence[Point]) -> float:
    """两个凸多边形之间的最小距离（用于"负例点离可行域多远"）。"""
    if not a or not b:
        return math.inf
    if any(point_in_polygon(p, b) for p in a) or any(point_in_polygon(p, a) for p in b):
        return 0.0
    best = math.inf
    for p in a:
        best = min(best, distance_point_to_region(p, b))
        if best == 0.0:
            return 0.0
    for p in b:
        best = min(best, distance_point_to_region(p, a))
    return best


def _distance_point_segment(p: Point, a: Point, b: Point) -> float:
    dx = b.x - a.x
    dy = b.y - a.y
    length2 = dx * dx + dy * dy
    if length2 <= 1e-12:
        return p.distance_to(a)
    t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / length2
    t = max(0.0, min(1.0, t))
    return math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy))


# ---------------------------------------------------------------------------
# 覆盖栅格
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class CoverageGrid:
    """把平面离散成正方形格，用**整数位掩码**表示区域/已覆盖集合。

    位掩码让"新增确认面积"退化成一次 ``&`` 加 ``bit_count()``，
    这样"上千个候选停点 × 20 个频道"的打分可以在毫秒级完成，
    实时重规划才跑得动。
    """

    cell_m: float = GRID_CELL_M
    half_extent_m: float = ARENA_RADIUS_M + 60.0
    #: 派生量（在 __post_init__ 里算好后缓存，避免每次取用都重算）
    n: int = field(init=False, default=0)
    origin: float = field(init=False, default=0.0)
    cell_area_m2: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        self.n = max(1, int(math.ceil(2.0 * self.half_extent_m / self.cell_m)) + 2)
        self.origin = -self.n * self.cell_m / 2.0
        self.cell_area_m2 = self.cell_m * self.cell_m

    # -- 坐标 -----------------------------------------------------------
    def to_ij(self, point: Point) -> tuple[int, int]:
        """落在 ``point`` 的格子编号。**必须与 :meth:`center` 严格互逆**：

        网格的 0 号格覆盖 ``[origin, origin+cell)``，因此第 i 格是
        ``[origin + i*cell, origin + (i+1)*cell)``，格心 ``origin + (i+0.5)*cell``。
        负数要用 floor（``//``）而不是截断取整，否则原点附近的编号会错一格。
        """
        i = int(math.floor((point.x - self.origin) / self.cell_m))
        j = int(math.floor((point.y - self.origin) / self.cell_m))
        return (max(0, min(self.n - 1, i)), max(0, min(self.n - 1, j)))

    def center(self, i: int, j: int) -> Point:
        """第 (i, j) 格的格心（必须落在该格内）。"""
        return Point(
            self.origin + (i + 0.5) * self.cell_m,
            self.origin + (j + 0.5) * self.cell_m,
        )

    def corners(self, i: int, j: int) -> tuple[Point, Point, Point, Point]:
        x0 = self.origin + i * self.cell_m
        y0 = self.origin + j * self.cell_m
        x1 = x0 + self.cell_m
        y1 = y0 + self.cell_m
        return (Point(x0, y0), Point(x1, y0), Point(x1, y1), Point(x0, y1))

    def index(self, i: int, j: int) -> int:
        return j * self.n + i

    @property
    def n_cells(self) -> int:
        return self.n * self.n

    # -- 掩码构造 --------------------------------------------------------
    def mask_inside_arena(self) -> int:
        mask = 0
        for j in range(self.n):
            for i in range(self.n):
                c = self.center(i, j)
                if math.hypot(c.x, c.y) <= ARENA_RADIUS_M:
                    mask |= 1 << self.index(i, j)
        return mask

    def mask_inside_polygon(self, poly: Sequence[Point]) -> int:
        """格心落在多边形内的格集合（用于把可行域离散化）。"""
        if len(poly) < 3:
            return 0
        xs = [p.x for p in poly]
        ys = [p.y for p in poly]
        i_lo = max(0, int((min(xs) - self.origin) // self.cell_m))
        i_hi = min(self.n - 1, int((max(xs) - self.origin) // self.cell_m) + 1)
        j_lo = max(0, int((min(ys) - self.origin) // self.cell_m))
        j_hi = min(self.n - 1, int((max(ys) - self.origin) // self.cell_m) + 1)
        mask = 0
        for j in range(j_lo, j_hi + 1):
            y = self.origin + (j + 0.5) * self.cell_m
            for i in range(i_lo, i_hi + 1):
                x = self.origin + (i + 0.5) * self.cell_m
                if point_in_polygon(Point(x, y), poly):
                    mask |= 1 << self.index(i, j)
        return mask

    def mask_disk_corners(self, center: Point, radius: float) -> int:
        """四个角都落在半径 ``radius`` 圆盘内的格集合 —— 严格保守的"覆盖"定义。

        只扫描**可能满足**的格：格的四个角距圆心最远不超过
        ``|格心-圆心| + √2·cell/2``，因此格心必须落在
        ``radius - √2·cell/2`` 的圆内（``radius`` 不足该半对角线时退化为 0）。
        """
        if radius <= 0.0:
            return 0
        inner = radius - self.cell_m * math.sqrt(2.0) / 2.0
        if inner <= 0.0:
            return 0
        i0, j0 = self.to_ij(Point(center.x - inner, center.y - inner))
        i1, j1 = self.to_ij(Point(center.x + inner, center.y + inner))
        r2 = radius * radius
        mask = 0
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                ok = True
                for corner in self.corners(i, j):
                    dx = corner.x - center.x
                    dy = corner.y - center.y
                    if dx * dx + dy * dy > r2:
                        ok = False
                        break
                if ok:
                    mask |= 1 << self.index(i, j)
        return mask

    def mask_disk_center(self, center: Point, radius: float) -> int:
        """格心落在圆盘内的格集合（打分用，比角点判定松、但快且方向正确）。"""
        i0, j0 = self.to_ij(Point(center.x - radius, center.y - radius))
        i1, j1 = self.to_ij(Point(center.x + radius, center.y + radius))
        r2 = radius * radius
        mask = 0
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                c = self.center(i, j)
                dx = c.x - center.x
                dy = c.y - center.y
                if dx * dx + dy * dy <= r2:
                    mask |= 1 << self.index(i, j)
        return mask

    def cells_in_disk(self, center: Point, radius: float) -> Iterator[Point]:
        """圆盘内所有格的**格心**（迭代器，避免构造中间列表）。"""
        if radius <= 0.0:
            return
        i0, j0 = self.to_ij(Point(center.x - radius, center.y - radius))
        i1, j1 = self.to_ij(Point(center.x + radius, center.y + radius))
        r2 = radius * radius
        for j in range(j0, j1 + 1):
            y = self.origin + (j + 0.5) * self.cell_m
            dy = y - center.y
            dy2 = dy * dy
            if dy2 > r2:
                continue
            for i in range(i0, i1 + 1):
                x = self.origin + (i + 0.5) * self.cell_m
                dx = x - center.x
                if dx * dx + dy2 <= r2:
                    yield Point(x, y)

    def positions(self, mask: int, limit: int | None = None) -> list[Point]:
        """位掩码里所有格的格心坐标（可选截断）。"""
        out: list[Point] = []
        remaining = mask
        while remaining:
            low = remaining & -remaining
            idx = low.bit_length() - 1
            remaining ^= low
            out.append(self.center(idx % self.n, idx // self.n))
            if limit is not None and len(out) >= limit:
                break
        return out


# ---------------------------------------------------------------------------
# 信念构造
# ---------------------------------------------------------------------------
def build_beliefs(
    matrix: KnowledgeMatrix,
    *,
    half_width_deg: float = 1.0,
    clear_fail_points: dict[int, list[Point]] | None = None,
    max_range_m: float = MAX_EFFECTIVE_RADIUS_M,
    directional: bool = False,
) -> dict[int, Belief]:
    """从知识矩阵重建每个频道的信息（正例 → 可行域；负例 → R 的上界）。

    ``directional=True``（问题4）时 :math:`\hat R` 改用**乐观**上界：
    定向源的"收不到"可能只是背对着，因此负例只能给出很弱的下界。
    覆盖/剪枝判据必须按"源的有效接收半径可能大到 1500m"来规划 ——
    用保守的 1000m 会把"8 个停点全在 1000m 外"的源误判成"已确认"。
    """
    beliefs: dict[int, Belief] = {}
    fail_points = clear_fail_points or {}
    for channel in matrix.channels:
        readings: list[tuple[Point, float]] = []
        near_points: list[Point] = []
        not_find_points: list[Point] = []
        for ev in matrix.cells.get(channel, {}).values():
            # 一律使用**真实检测坐标**：格心会把 1° 的角度误差人为放大到 3°+
            point = ev.point
            if point is None:
                continue
            if ev.status is CellStatus.FIND and ev.bearing_deg is not None:
                readings.append((point, ev.bearing_deg))
            elif ev.status is CellStatus.NOT_FIND:
                not_find_points.append(point)
            elif ev.status is CellStatus.NEAR:
                near_points.append(point)
        for p in fail_points.get(channel, []):
            not_find_points.append(p)

        belief = Belief(
            channel=channel,
            n_find=len(readings),
            n_not_find=len(not_find_points),
            near=bool(near_points),
            cleared=matrix.is_cleared(channel),
        )
        region = _region_from_readings(readings, half_width_deg=half_width_deg, max_range_m=max_range_m)
        for p in near_points:
            if region:
                region = clip_by_circle(region, p, 5.0)
            else:
                region = clip_by_circle(arena_polygon(ARENA_RADIUS_M), p, 5.0)
        belief.region = region

        radius_hat, source = estimate_radius(region, not_find_points, n_find=len(readings))
        belief.radius_hat_m = radius_hat
        belief.radius_source = source
        if directional:
            # 乐观：R 可能高达 1500m，只有"超过 1500m"才敢说收不到
            belief.plan_radius_m = min(
                MAX_EFFECTIVE_RADIUS_M + 40.0,
                max(radius_hat, MIN_EFFECTIVE_RADIUS_M) + RADIUS_SAFETY_MARGIN_M,
            )
        else:
            belief.plan_radius_m = max(
                300.0, min(radius_hat, MAX_EFFECTIVE_RADIUS_M) - RADIUS_SAFETY_MARGIN_M
            )
        beliefs[channel] = belief
    return beliefs


def _region_from_readings(
    readings: Sequence[tuple[Point, float]],
    *,
    half_width_deg: float,
    max_range_m: float,
) -> list[Point]:
    if not readings:
        return []
    poly = arena_polygon(ARENA_RADIUS_M)
    for apex, bearing in readings:
        poly = clip_by_bearing(poly, apex, bearing, half_width_deg)
        if len(poly) < 3:
            return []
        poly = clip_by_circle(poly, apex, max_range_m)
        if len(poly) < 3:
            return []
    return poly


def estimate_radius(
    region: Sequence[Point],
    not_find_points: Sequence[Point],
    *,
    n_find: int,
) -> tuple[float, str]:
    """R 的在线夹逼（见模块文档公式 1）。

    返回 ``(R_hat, 来源标签)``。
    """
    if n_find == 0:
        return (MIN_EFFECTIVE_RADIUS_M, "prior_min")
    if not not_find_points or len(region) < 3:
        return (MAX_EFFECTIVE_RADIUS_M, "prior_max")
    best = min(distance_point_to_region(p, region) for p in not_find_points)
    if math.isinf(best):
        return (MAX_EFFECTIVE_RADIUS_M, "prior_max")
    if best > MAX_EFFECTIVE_RADIUS_M:
        return (MAX_EFFECTIVE_RADIUS_M, "prior_max")
    if best < MIN_EFFECTIVE_RADIUS_M:
        # 证据说 R < 1000m，但题目保证 R ≥ 1000m —— 说明这些负例点
        # 落在覆盖角之外（定向源），或者可行域估计偏了。按保守取 R_min。
        return (MIN_EFFECTIVE_RADIUS_M, "not_find_conflict")
    return (best, "not_find_bound")


# ---------------------------------------------------------------------------
# 覆盖判据
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class ChannelCoverState:
    """一个频道在栅格上的覆盖台账。"""

    channel: int
    region_mask: int
    covered_mask: int = 0
    plan_radius_m: float = 950.0
    n_cells: int = 0

    @property
    def uncovered_mask(self) -> int:
        return self.region_mask & ~self.covered_mask

    @property
    def uncovered_cells(self) -> int:
        return self.uncovered_mask.bit_count()

    @property
    def complete(self) -> bool:
        return self.uncovered_cells <= 0


@dataclass(slots=True)
class CoverageReport:
    """一次覆盖完备性判定（面向日志/论文的完整解释）。"""

    complete: bool
    grid_cell_m: float
    #: 频道 -> 未确认格数
    uncovered_cells: dict[int, int] = field(default_factory=dict)
    #: 频道 -> 未确认区域的一个代表点（离当前位置最近的缺口格）
    gap_hint: dict[int, Point] = field(default_factory=dict)
    #: 未确认格总数
    total_uncovered_cells: int = 0
    #: 参与判定的停点数
    n_stops: int = 0
    #: R 估计的来源分布（论文里解释"为什么敢少测"）
    radius_sources: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "grid_cell_m": self.grid_cell_m,
            "total_uncovered_cells": self.total_uncovered_cells,
            "uncovered_cells": {str(c): n for c, n in sorted(self.uncovered_cells.items()) if n},
            "n_stops": self.n_stops,
            "radius_sources": dict(sorted(self.radius_sources.items())),
        }


class CoverageTracker:
    """维护"每个频道的未确认区域"，并统一承担覆盖判据与停点规划。

    使用方式::

        tracker = CoverageTracker(grid, matrix)
        tracker.sync(beliefs, plausible=fn)      # 每次拿到新观测后同步
        tracker.observe_stops(stops)             # 登记"这些点测过了"
        report = tracker.assess(position)        # 判据：还缺哪里
        cand = tracker.plan(position)            # 决策：下一个停点

    实现要点（性能）
    ----------------
    * 全部用**整数位掩码**：一个频道的区域 = 一个 Python 大整数，
      "新增确认面积" = ``(uncovered & disk).bit_count()``，
      上千个候选 × 20 个频道可以在毫秒级完成 —— 实时重规划才跑得动。
    * 距离全部在**格坐标**下比较（1 格 = ``cell_m`` 米），
      省掉每格构造 ``Point`` 的开销（这是最初版本 2.3s/次 的瓶颈）。
    * 相同 (掩码, 半径) 的频道**去重打分**：搜索初期 20 个频道状态完全一样，
      只算一次再乘以数量。
    """

    #: 定向假设：负例不做圆盘排除，改用锥一致性判据（问题4 打开）
    directional: bool = False

    def __init__(self, grid: CoverageGrid | None = None, matrix: KnowledgeMatrix | None = None) -> None:
        self.grid = grid or CoverageGrid()
        self.matrix = matrix
        self.directional = False
        self.states: dict[int, ChannelCoverState] = {}
        #: 频道 -> 该频道的"仍有可能是源"的格掩码（定向假设下会比区域掩码更大）
        self.plausible: dict[int, int] = {}
        self._candidate_cache: dict[int, list[tuple[int, int]]] = {}
        self._disk_cache: dict[tuple[float, float], int] = {}
        self._bbox_cache: dict[int, tuple[int, int, int, int]] = {}
        self._seen_stop_keys: set[tuple[float, float]] = set()
        #: 锥盲区掩码缓存（开销大，只在"该频道观测数变化"时重算）
        self._dir_cache: dict[int, tuple[int, int]] = {}
        self._dir_mask: dict[int, int] = {}

    # -- 坐标换算 -------------------------------------------------------
    def _to_cell_coord(self, point: Point) -> tuple[float, float]:
        return ((point.x - self.grid.origin) / self.grid.cell_m - 0.5,
                (point.y - self.grid.origin) / self.grid.cell_m - 0.5)

    def _from_cell_coord(self, cx: float, cy: float) -> Point:
        return Point(self.grid.origin + (cx + 0.5) * self.grid.cell_m,
                     self.grid.origin + (cy + 0.5) * self.grid.cell_m)

    # -- 同步 -----------------------------------------------------------
    def sync(
        self,
        beliefs: dict[int, Belief],
        *,
        matrix: KnowledgeMatrix | None = None,
        plausible: "Callable[[int, Belief], int] | None" = None,
    ) -> None:
        """重建每个频道在栅格上的区域掩码。

        * 已收到信号的频道 -> 用其凸可行域
        * 从未收到信号的频道 -> 用整片区域（真的可能在任何地方）
        * ``plausible`` 可选：给出"仍然可能是源"的格掩码（定向源的锥约束），
          默认等于区域掩码
        """
        self.matrix = matrix or self.matrix
        arena_mask = self.grid.mask_inside_arena()
        for channel, belief in beliefs.items():
            if belief.cleared:
                self.states.pop(channel, None)
                self.plausible.pop(channel, None)
                continue
            raw_mask = 0
            if belief.region and len(belief.region) >= 3:
                raw_mask = mask = self.grid.mask_inside_polygon(belief.region)
                if self.matrix is not None:
                    mask &= ~self._not_find_exclusion(
                        belief.channel, belief, directional=self.directional
                    )
            else:
                # ── 还没收到信号的频道 ──────────────────────────────────
                # 关键一步：它**不是**"到处都可能"。已经在 k 个停点收不到信号，
                # 就说明源不在那些停点的有效接收半径内 —— 候选区域是"整片区域
                # 减去这些圆盘"。这正是本模块要利用的负例收益：
                # 候选区域随扫描推进单调收缩，需要的补测停点因此变少。
                raw_mask = mask = arena_mask
                if self.matrix is not None:
                    mask &= ~self._not_find_exclusion(
                        belief.channel, belief, directional=self.directional
                    )
            if self.directional:
                # 定向源的证伪判据：把"无论如何解释不通"的位置从**待确认区域**里删掉，
                # 剩下的就是"锥盲区"，补测停点只需要覆盖它。
                # 注意掩码只影响"判据/规划"这一层；几何仍由连续可行域决定，删掉的是
                # 已被证伪的格子，不会动可行域本身。
                blind = mask & self._invisible_cache(channel, belief)
                # 保底：删得比原来的十分之一还少就保留原掩码（避免安全阀误判成"已确认"）
                if blind.bit_count() >= max(1, raw_mask.bit_count() // 10):
                    mask = blind
            prev = self.states.get(channel)
            covered = prev.covered_mask if prev is not None else 0
            self.states[channel] = ChannelCoverState(
                channel=channel,
                region_mask=mask,
                covered_mask=covered & mask,
                plan_radius_m=belief.plan_radius_m,
                n_cells=mask.bit_count(),
            )
            self.plausible[channel] = (
                plausible(channel, belief) if plausible is not None else mask
            )

    def set_plan_radius(self, channel: int, radius_m: float) -> None:
        state = self.states.get(channel)
        if state is not None:
            state.plan_radius_m = max(300.0, radius_m)

    def _not_find_exclusion(
        self,
        channel: int,
        belief: Belief,
        margin_m: float = 40.0,
        directional: bool = False,
    ) -> int:
        """负例的圆盘排除集：``not_find`` 在 p 表明"源不在 p 的 R 圆盘内"。

        半径取 ``max(R_hat, R_min) + margin``：R_hat 是负例反推的上界；未检出的频道
        用先验下界 1000m 起步，避免一上来就排除太多。

        ``directional=True``（问题4）时**不用圆盘**：定向源背对时收不到，
        用圆盘排除会连真实位置一起删掉（这正是旧实现清除率只有 30% 的原因）。
        定向情形交给 :func:`invisible_mask` 的锥一致性判据处理。
        """
        assert self.matrix is not None
        if directional:
            return 0
        mask = 0
        radius = max(belief.radius_hat_m, MIN_EFFECTIVE_RADIUS_M) + margin_m
        for ev in self.matrix.cells.get(channel, {}).values():
            if ev.status is not CellStatus.NOT_FIND or ev.point is None:
                continue
            mask |= self._disk(ev.point, radius)
        return mask

    # -- 掩码 -----------------------------------------------------------
    def _disk(self, point: Point, radius_m: float, clip: int | None = None) -> int:
        """格心落在圆盘内的格掩码。

        ``clip`` 传入某个区域掩码时，只计算与该区域**包围盒**相交的部分 ——
        这是让"候选点 × 频道"打分保持毫秒级的关键：搜索初期未确认区域是整个
        目标区域（2828 格），若不裁剪，每个候选点都要扫满 784 格。
        """
        key = (round(point.x, 3), round(point.y, 3), round(radius_m, 3))
        if clip is None:
            cached = self._disk_cache.get(key)
            if cached is not None:
                return cached
        cx, cy = self._to_cell_coord(point)
        r = radius_m / self.grid.cell_m
        i0 = max(0, int(math.floor(cx - r)))
        i1 = min(self.grid.n - 1, int(math.ceil(cx + r)))
        j0 = max(0, int(math.floor(cy - r)))
        j1 = min(self.grid.n - 1, int(math.ceil(cy + r)))
        if clip is not None:
            bi0, bi1, bj0, bj1 = self._bbox(clip)
            i0 = max(i0, bi0)
            i1 = min(i1, bi1)
            j0 = max(j0, bj0)
            j1 = min(j1, bj1)
            if i1 < i0 or j1 < j0:
                return 0
        r2 = r * r
        mask = 0
        n = self.grid.n
        for j in range(j0, j1 + 1):
            dy = j - cy
            dy2 = dy * dy
            if dy2 > r2:
                continue
            half = math.sqrt(r2 - dy2)
            ia = max(i0, int(math.ceil(cx - half)))
            ib = min(i1, int(math.floor(cx + half)))
            if ib < ia:
                continue
            base = j * n + ia
            width = ib - ia + 1
            mask |= ((1 << width) - 1) << base
        if clip is None:
            if len(self._disk_cache) > 4096:
                self._disk_cache.clear()
            self._disk_cache[key] = mask
        return mask

    def _bbox(self, mask: int) -> tuple[int, int, int, int]:
        """位掩码的包围盒 ``(i0, i1, j0, j1)``（带缓存）。"""
        cached = self._bbox_cache.get(mask)
        if cached is not None:
            return cached
        n = self.grid.n
        i0, i1, j0, j1 = n, -1, n, -1
        remaining = mask
        while remaining:
            low = remaining & -remaining
            idx = low.bit_length() - 1
            remaining ^= low
            i = idx % n
            j = idx // n
            if i < i0:
                i0 = i
            if i > i1:
                i1 = i
            if j < j0:
                j0 = j
            if j > j1:
                j1 = j
        box = (i0, i1, j0, j1)
        if len(self._bbox_cache) > 256:
            self._bbox_cache.clear()
        self._bbox_cache[mask] = box
        return box

    def _invisible_cache(self, channel: int, belief: Belief) -> int:
        """锥盲区掩码（按观测数缓存，开销 O(负例数 × 圆盘格数)）。

        测试半径用 :data:`~mathmodel2026b.protocol.MAX_EFFECTIVE_RADIUS_M`（乐观）：
        只有"任何可能的 R 下都必然收到"的停点，才能拿来证伪一个候选位置。
        """
        assert self.matrix is not None
        finds = [
            (ev.point, ev.bearing_deg)
            for ev in self.matrix.cells.get(channel, {}).values()
            if ev.status is CellStatus.FIND and ev.point is not None and ev.bearing_deg is not None
        ]
        not_finds = self.matrix.exclude_points(channel, 0.0)
        key = (len(finds), len(not_finds))
        if self._dir_cache.get(channel) == key:
            return self._dir_mask.get(channel, -1)
        mask = invisible_mask(self.grid, finds, not_finds)
        self._dir_cache[channel] = key
        self._dir_mask[channel] = mask
        return mask

    # -- 判据 -----------------------------------------------------------
    def observe_stops(self, stops: Iterable[Point]) -> None:
        """登记"这些位置已经对**所有活跃频道**做过检测"，增量更新已确认掩码。

        增量很关键：判据要在每次移动后都能问一次。同一坐标只算一次
        （同一地点重复检测读数不变，附录2-1）。
        """
        fresh = [p for p in stops if (round(p.x, 3), round(p.y, 3)) not in self._seen_stop_keys]
        if not fresh:
            return
        for p in fresh:
            self._seen_stop_keys.add((round(p.x, 3), round(p.y, 3)))
        for state in self.states.values():
            radius = state.plan_radius_m
            covered = state.covered_mask
            for stop in fresh:
                if state.region_mask & ~covered == 0:
                    break
                covered |= self._disk(stop, radius)
            state.covered_mask = covered & state.region_mask

    def observe_partial_stop(self, stop: Point, channels: Iterable[int]) -> None:
        """只对**部分频道**做过检测的停点（按需选测时用）。"""
        cell_key = (round(stop.x, 3), round(stop.y, 3))
        self._seen_stop_keys.add(cell_key)
        for channel in channels:
            state = self.states.get(channel)
            if state is None:
                continue
            state.covered_mask |= self._disk(stop, state.plan_radius_m) & state.region_mask

    def assess(self, position: Point | None = None) -> CoverageReport:
        report = CoverageReport(complete=True, grid_cell_m=self.grid.cell_m, n_stops=len(self._seen_stop_keys))
        for channel, state in sorted(self.states.items()):
            uncovered = state.uncovered_mask
            count = uncovered.bit_count()
            if count:
                report.complete = False
                report.uncovered_cells[channel] = count
                report.total_uncovered_cells += count
                if position is not None:
                    report.gap_hint[channel] = self._nearest_cell(uncovered, position)
        return report

    def is_channel_covered(self, channel: int) -> bool:
        state = self.states.get(channel)
        return state is None or state.complete

    def plausible_area_m2(self, channel: int) -> float:
        state = self.states.get(channel)
        return 0.0 if state is None else state.region_mask.bit_count() * self.grid.cell_area_m2

    def _nearest_cell(self, mask: int, position: Point) -> Point:
        best: Point | None = None
        best_d = math.inf
        remaining = mask
        n = self.grid.n
        while remaining:
            low = remaining & -remaining
            idx = low.bit_length() - 1
            remaining ^= low
            c = self.grid.center(idx % n, idx // n)
            d = position.distance_to(c)
            if d < best_d:
                best_d = d
                best = c
        return best or position

    # -- 规划 -----------------------------------------------------------
    def candidate_lattice(self, spacing_m: float | None = None) -> list[Point]:
        """三角格候选停点（覆盖整个目标区域，含边界外一圈）。

        用**三角格**（hexagonal lattice）而不是方阵：同样间距下三角格的覆盖半径
        只有方阵的 :math:`1/\\sqrt2` 量级，因此"候选集合本身"就是一个合法覆盖，
        贪心集合覆盖才有终止保证。间距取 :math:`1.70\\hat R` 时，
        覆盖半径 :math:`\\approx 0.98\\hat R < \\hat R`。

        候选点数量在 :math:`|\\text{区域}|/\\text{间距}^2` 量级（数百个），
        每个候选打分的成本是"与各频道未确认掩码求交"，整体在几十毫秒内。
        """
        spacing = spacing_m or self.grid.cell_m
        spacing = max(20.0, float(spacing))
        cached = self._candidate_cache.get(spacing)
        if cached is not None:
            return cached
        row_h = spacing * math.sqrt(3.0) / 2.0
        limit = ARENA_RADIUS_M + spacing * 0.75
        k_max = int(math.ceil(limit / row_h)) + 1
        j_max = int(math.ceil(limit / spacing)) + 2
        out: list[Point] = []
        for k in range(-k_max, k_max + 1):
            y = k * row_h
            offset = spacing / 2.0 if (k % 2) else 0.0
            for j in range(-j_max, j_max + 1):
                x = j * spacing + offset
                if math.hypot(x, y) <= limit:
                    out.append(Point(x, y))
        self._candidate_cache[spacing] = out
        return out

    #: 覆盖用候选格点间距与 :math:`\hat R` 的比值。三角格覆盖定理给出
    #: :math:`\sqrt3\,R \approx 1.732R` 是"保证覆盖"的最大间距；
    #: 这里取 1.70R 留一点余量，使"候选格点集合本身"就是合法覆盖。
    COVER_LATTICE_RATIO: float = 1.70
    #: 贪心集合覆盖的"收益容忍度"：新增面积达到最优的 80% 就算合格，
    #: 合格集合里再挑最近的。这样既保证覆盖效率，又不会为了多盖几格横穿区域。
    COVER_GAIN_SLACK: float = 0.80

    def plan_cover(self, position: Point) -> "StopCandidate | None":
        """贪心集合覆盖版的下一个停点（缺口补测阶段用）。

        与 :meth:`plan` 的差别只在**打分方式**，但结果差别很大：

        * :meth:`plan` 用"新增确认面积 / 路程"。这天然偏好近处的小收益，
          走到最后会退化成"贴着未确认区域边缘打转"——实测 18 个停点之后
          缺口还有一半，机器狗在离原点 1700m 的边界带绕圈；
        * 本方法直接用**贪心集合覆盖**：候选点间距取 :math:`1.70\hat R`
          （三角格定理保证这个间距的格点集合本身就是覆盖），每次挑"能盖住
          最多未确认格"的点，路程只作为并列打破项。

        因为候选格点集合本身是覆盖，这条贪心一定在
        :math:`O(\text{面积}/\hat R^2)` 步内把缺口清零，不会无限打转。
        """
        if not self.states:
            return None
        pending = {c: s for c, s in self.states.items() if s.uncovered_mask}
        if not pending:
            return None
        radius = min(state.plan_radius_m for state in pending.values())
        spacing = max(self.grid.cell_m, radius * self.COVER_LATTICE_RATIO)
        scored_candidates: list[StopCandidate] = []
        for cand in self.candidate_lattice(spacing):
            travel = position.distance_to(cand)
            total = 0.0
            per: dict[int, float] = {}
            for channel, state in pending.items():
                cells = (state.uncovered_mask & self._disk(cand, state.plan_radius_m)).bit_count()
                if cells:
                    area = cells * self.grid.cell_area_m2
                    per[channel] = area
                    total += area
            if total <= 0.0:
                continue
            scored_candidates.append(
                StopCandidate(
                    point=cand,
                    travel_m=travel,
                    new_area_m2=total,
                    per_channel=per,
                    channels_helped=len(per),
                )
            )
        if not scored_candidates:
            return None
        # 两段式：先找出"最能盖"的那一档（容忍 COVER_GAIN_SLACK 的差距），
        # 再在这一档里挑**离当前位置最近**的。
        # 只用"最大新增面积"会退化成"每次跳到区域另一头"（路线反复横穿，
        # 实测 25 个停点还没走完）；只用"面积/路程"又会贴着边缘打转。
        best_gain = max(c.new_area_m2 for c in scored_candidates)
        threshold = best_gain * self.COVER_GAIN_SLACK
        near = [c for c in scored_candidates if c.new_area_m2 >= threshold]
        best = min(near, key=lambda c: (c.travel_m, -c.new_area_m2))
        return best

    def plan(
        self,
        position: Point,
        *,
        weights: dict[int, float] | None = None,
        spacing_m: float | None = None,
        keep_ratio: float = 0.25,
        coarse_spacing_m: float | None = None,
    ) -> "StopCandidate | None":
        """选下一个停点（模块文档公式 3），两阶段打分。

        阶段 1 用粗候选快速筛出 ``keep_ratio`` 比例的种子点（只看"能碰到多少未确认格"，
        不看路程），阶段 2 在种子点周围的细候选上做完整打分。
        这样既保留"位置可以任意细"，又把候选数从几千降到几十。
        """
        if not self.states:
            return None
        pending = {c: s for c, s in self.states.items() if s.uncovered_mask}
        if not pending:
            return None
        cell_area = self.grid.cell_area_m2
        groups: dict[tuple[int, float], list[int]] = {}
        for channel, state in pending.items():
            groups.setdefault((state.uncovered_mask, round(state.plan_radius_m, 1)), []).append(channel)

        def weighted_gain(disk: int, clip: int | None = None) -> tuple[float, dict[int, float], float]:
            """给定候选停点的圆盘掩码，算加权新增确认面积。

            若候选点完全够不着某个频道（``disk & mask == 0``），该频道贡献 0 ——
            因为"确认面积"定义就是"这个停点能覆盖到的未确认格"。
            """
            del clip
            total = 0.0
            raw = 0.0
            per: dict[int, float] = {}
            for (mask, radius), channels in groups.items():
                cells = (mask & disk).bit_count()
                raw += cells * len(channels)
                if not cells:
                    continue
                area = cells * cell_area
                w = 1.0 if weights is None else sum(weights.get(c, 1.0) for c in channels) / len(channels)
                gain = area * w * len(channels)
                total += gain
                for c in channels:
                    per[c] = area * (1.0 if weights is None else weights.get(c, 1.0))
            return total, per, raw

        coarse = self.candidate_lattice(coarse_spacing_m or max(self.grid.cell_m, (spacing_m or self.grid.cell_m) * 2))
        max_radius = max(g[1] for g in groups)
        seeds: list[tuple[float, Point, dict[int, float], float]] = []
        for cand in coarse:
            disk = self._disk(cand, max_radius)
            total, per, raw = weighted_gain(disk)
            if total <= 0.0:
                continue
            seeds.append((raw, cand, per, total))
        if not seeds:
            return None
        seeds.sort(key=lambda s: s[0], reverse=True)
        keep = max(1, int(len(seeds) * keep_ratio))
        seeds = seeds[:keep]

        radius_span = max(1, int(round((max_radius + 120.0) / self.grid.cell_m)))
        best: StopCandidate | None = None
        for _, seed, _, _ in seeds:
            sx, sy = self._to_cell_coord(seed)
            for dj in range(-radius_span, radius_span + 1, 2):
                for di in range(-radius_span, radius_span + 1, 2):
                    cand = self._from_cell_coord(sx + di, sy + dj)
                    travel = position.distance_to(cand)
                    if best is not None and travel >= (best.travel_m + 1.0) * 4.0:
                        continue
                    disk = self._disk(cand, max_radius)
                    total, per, _ = weighted_gain(disk)
                    if total <= 0.0:
                        continue
                    scored = StopCandidate(
                        point=cand,
                        travel_m=travel,
                        new_area_m2=total,
                        per_channel=per,
                        channels_helped=len(per),
                    )
                    if best is None or scored.score > best.score:
                        best = scored
        return best


@dataclass(slots=True)
class StopCandidate:
    """候选停点的打分明细（用于日志里解释"为什么去这里"）。"""

    point: Point
    travel_m: float
    new_area_m2: float
    per_channel: dict[int, float] = field(default_factory=dict)
    channels_helped: int = 0

    @property
    def score(self) -> float:
        if self.travel_m <= 1.0:
            return self.new_area_m2 * 1e6
        return self.new_area_m2 / (self.travel_m + 1.0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "x": round(self.point.x, 1),
            "y": round(self.point.y, 1),
            "travel_m": round(self.travel_m, 1),
            "new_area_m2": round(self.new_area_m2, 1),
            "channels": self.channels_helped,
            "score": round(self.score, 3),
        }


def channel_weight(belief: Belief, *, near_weight: float = 3.0) -> float:
    """频道权重：越不确定、越"快能收工"的频道越优先确认。

    * 已清除 -> 0
    * ``near``（信号过强，走过去就能清）-> ``near_weight``
    * 从未收到信号 -> 1.0（整片区域都要确认）
    * 已收到信号 -> 未确认比例（可行域越大越优先），下限 0.05
    """
    if belief.cleared:
        return 0.0
    if belief.near:
        return near_weight
    if not belief.region:
        return 1.0
    arena_area = math.pi * ARENA_RADIUS_M ** 2
    return max(0.05, min(2.0, belief.area_m2 / arena_area * 6.0))


def fallback_hexagon_plan() -> list[Point]:
    """退化方案：旧实现的 7 点覆盖扫描（覆盖半径 ≈969m ≤ 1000m）。"""
    return regular_hexagon_scan_points(1200.0)

# ---------------------------------------------------------------------------
# 定向源（问题4）：负例的"半平面"排除
# ---------------------------------------------------------------------------
#: 判定"远场负例"的距离：超过它，全向源也收不到，因此与锥朝向无关
FAR_FIELD_M: float = MAX_EFFECTIVE_RADIUS_M


# ---------------------------------------------------------------------------
# 定向源（问题4）：锥一致性裁剪
# ---------------------------------------------------------------------------
#: 判定"远场负例"的距离：超过它，全向源也收不到，因此与锥朝向无关
FAR_FIELD_M: float = MAX_EFFECTIVE_RADIUS_M


def invisible_mask(
    grid: "CoverageGrid",
    finds: Sequence[tuple[Point, float]],
    not_finds: Sequence[Point],
    *,
    radius_min_m: float = MIN_EFFECTIVE_RADIUS_M,
    radius_max_m: float = MAX_EFFECTIVE_RADIUS_M,
) -> int:
    r"""定向假设下"在该处仍然可能是源"的格掩码 —— 问题4 的核心几何。

    定向源的覆盖角是 ±90°，所以"在某点收不到"有两个互斥解释：
    距离超出 R，**或者**该点落到了锥的背面。于是存在一个致命盲区：

        某个位置只被 1~2 个停点"够得着"，而这几个停点又都在锥的背面时，
        无论在这几个停点上怎么扫都发现不了它。

    **可证伪**判据（对候选源位置 S）：存在某个已测停点 p，使得

    .. math::

        |S-p| \le R_{	ext{test}}
        \quad	ext{且}\quad
        orall apex:\; ngle(\mathrm{dir}(S	o p),\mathrm{dir}(S	o apex)) \le 90^\circ

    那么"源在 S"这个假设下，p 处**必定**能收到信号（距离够近、方向也朝着它）。
    既然 p 上记的是 ``not_find``，S 处就不可能是源，可以删掉。

    其中 :math:`R_{	ext{test}}` 取 :math:`R_{\max}=1500`（附录2-2 的上界）：
    这是**乐观**取法 —— 只要"可能存在一个有效接收半径大到 1500m 的源"，
    这个位置就不能被排除。用保守的 1000m 会漏掉"R 其实有 1300m、
    而扫描点恰好都在 1000~1300m 之间"的源，实测正是问题4 漏源的主因
    （8 个停点全在 1000m 外、全 `no_signal`，于是整片区域被误判为"已确认"）。

    没有正例时这条判据完全失效（一个定向源总能把锥背对任意有限个停点），
    因此 ``finds`` 为空直接返回全区域 —— 这是**保守**方向，不会漏源。
    """
    mask = grid.mask_inside_arena()
    if not not_finds:
        return mask
    if not finds:
        # 还没有任何正例 -> 锥朝向完全未知。一个定向源总可以把锥背对
        # 任意有限个停点，因此这里**一个格都不能删**。
        # 这也是问题4 与问题3 的真正差别：负例在定向场景下信息量近乎为零。
        return mask
    if radius_max_m <= radius_min_m:
        radius_max_m = radius_min_m
    # 外层扫"离某个负例不超过 R_max"的格（超出就是距离原因，与锥无关），
    # 内层逐个负例做"半平面"判定。反过来按负例扫会让同一个格被重复计算。
    bbox = _disk_cell_bbox(grid, not_finds, radius_max_m)
    if bbox is None:
        return mask
    i0, i1, j0, j1 = bbox
    r2 = radius_max_m * radius_max_m
    for j in range(j0, j1 + 1):
        y = grid.origin + (j + 0.5) * grid.cell_m
        for i in range(i0, i1 + 1):
            idx = grid.index(i, j)
            if not (mask >> idx) & 1:
                continue
            x = grid.origin + (i + 0.5) * grid.cell_m
            for p in not_finds:
                px = p.x - x
                py = p.y - y
                if px * px + py * py > r2:
                    continue  # 远场：任何假设下都收不到，不能用于证伪
                visible = True
                for apex, _svd in finds:
                    if px * (apex.x - x) + py * (apex.y - y) < 0.0:
                        visible = False  # 与已知可见方向相反 -> 可能只是背对着
                        break
                if visible:
                    mask &= ~(1 << idx)
                    break
    return mask


def _disk_cell_bbox(
    grid: "CoverageGrid",
    points: Sequence[Point],
    radius: float,
) -> tuple[int, int, int, int] | None:
    """若干个半径 ``radius`` 圆盘并集的格坐标包围盒（超出目标区域则裁剪）。"""
    if not points:
        return None
    i0 = j0 = 10 ** 9
    i1 = j1 = -10 ** 9
    for p in points:
        a, b = grid.to_ij(Point(p.x - radius, p.y - radius))
        c, d = grid.to_ij(Point(p.x + radius, p.y + radius))
        i0, j0 = min(i0, a), min(j0, b)
        i1, j1 = max(i1, c), max(j1, d)
    i0 = max(0, i0)
    j0 = max(0, j0)
    i1 = min(grid.n - 1, i1)
    j1 = min(grid.n - 1, j1)
    if i1 < i0 or j1 < j0:
        return None
    return (i0, i1, j0, j1)
    if not finds:
        # 还没有任何正例 -> 锥朝向完全未知。一个定向源总可以把锥背对
        # 任意有限个停点，因此这里**一个格都不能删**。
        # 这也是问题4 与问题3 的真正差别：负例在定向场景下信息量近乎为零。
        return mask
    if radius_max_m <= radius_min_m:
        radius_max_m = radius_min_m
    for p in not_finds:
        for cell in grid.cells_in_disk(p, radius_max_m):
            idx = grid.index(*grid.to_ij(cell))
            if not (mask >> idx) & 1:
                continue
            px = p.x - cell.x
            py = p.y - cell.y
            visible = True
            for apex, _svd in finds:
                if px * (apex.x - cell.x) + py * (apex.y - cell.y) < 0.0:
                    visible = False  # 与已知可见方向相反 -> 可能只是背对着
                    break
            if visible:
                mask &= ~(1 << idx)
    return mask


def hidden_mask(
    grid: "CoverageGrid",
    finds: Sequence[tuple[Point, float]],
    probes: Sequence[Point],
    *,
    radius_min_m: float = MIN_EFFECTIVE_RADIUS_M,
) -> int:
    r"""锥盲区掩码：**在一个已测停点上都发现不了源**的候选位置集合。

    与 :func:`invisible_mask` 的区别是语义完全不同，别混：

    * ``invisible_mask`` = "删掉已被证伪的位置"，用于**判据/剪枝**；
    * ``hidden_mask``    = "留下来还没被任何停点照到的位置"，用于**决定去哪补测**。

    判据（对候选源位置 S）：

    .. math::

        \forall p \in \text{已测停点}:\quad
        |S-p| > R_{\min}
        \;\;\text{或}\;\;
        \exists apex:\; \angle(\mathrm{dir}(S\to p),\mathrm{dir}(S\to apex)) > 90^\circ

    含义：每个停点要么离得太远（收不到），要么方向与已确认能看到的方向相反
    （源完全可以把锥背对它）。这样的位置就是**锥盲区** —— 无论在这几个停点上
    怎么加大发射功率式的重扫都没用，必须换一个"能同时看到这些停点"的位置去测。

    实现上先把候选位置缩到"离某个停点不超过 :math:`R_{\min}`"的格
    （再远就是纯距离问题），再逐个停点做半平面判定，成本可忽略。
    """
    mask = grid.mask_inside_arena()
    if not probes:
        return mask
    if not finds:
        # 一个正例都没有 -> 锥朝向完全未知，任何位置都可以背对全部停点躲开，
        # 也就是说**整片区域都是盲区**，只能靠"覆盖整个区域"来兜底。
        return mask
    bbox = _disk_cell_bbox(grid, probes, radius_min_m)
    if bbox is None:
        return 0
    i0, i1, j0, j1 = bbox
    hidden = 0
    r2 = radius_min_m * radius_min_m
    for j in range(j0, j1 + 1):
        y = grid.origin + (j + 0.5) * grid.cell_m
        for i in range(i0, i1 + 1):
            idx = grid.index(i, j)
            if not (mask >> idx) & 1:
                continue
            x = grid.origin + (i + 0.5) * grid.cell_m
            for p in probes:
                dx = p.x - x
                dy = p.y - y
                if dx * dx + dy * dy > r2:
                    continue  # 距离原因收不到，与锥朝向无关 -> 仍可能藏
                exposed = True
                for apex, _svd in finds:
                    if dx * (apex.x - x) + dy * (apex.y - y) < 0.0:
                        exposed = False  # 停点方向与已知可见方向相反，源可以背对它
                        break
                if exposed:
                    # 这个停点本应看到它 -> 不属于盲区
                    break
            else:
                # 所有停点都"够不着"或"可以背对" -> 该位置属于锥盲区
                hidden |= 1 << idx
    return hidden
