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
1. **有效接收半径 R 的安全标量界（上下界方向必须分清）**

   设真值源在 x、有效接收半径 R，可行域 P ∋ x（保证含真值）。

   * 正例（``find``）在 p ⇒ ``|p - x| <= R``。对**所有** x ∈ P 取最小的见证
     距离，得到的是 R 的**安全下界**::

       L = max(1000, max_{正例 p} min_{x∈P} |p - x|)

   * 负例（``not_find``）在 q ⇒ ``R < |q - x|``。对**所有** x ∈ P 取最大，
     得到的是 R 的**安全上界**::

       U = min(1500, min_{负例 q} max_{x∈P} |q - x|)

   两条的方向不能互换：负例**推不出** ``R < min_{x∈P}|q - x|``。

   规划与"必能发现"证书必须用**下界**：只有 ``d(s, x) <= L`` 才敢断言
   "停点 s 一定能收到位于 x 的源"，因为真实的 R 可能就只有 L。历史实现拿
   负例到可行域的**最短**距离当上界去放大规划半径，方向反了 —— 那会把
   "其实收不到"的位置判成"已确认"，是漏源的直接来源（见
   :func:`estimate_radius_bounds` 的文档与回归测试）。

2. **覆盖判据（保守、可复算）**

   规划时假设源可能落在 P_c 内任意一点。"停点集合 S 必定发现该频道"等价于
   "P_c 被半径 ``R_hat`` 的圆盘族覆盖"。本模块用**栅格化**实现：

   * 区域先做**栅格外包**：只要格子与 P_c 相交（包括格心落在区域外、仅边界
     穿过的那种），就必须纳入待确认集合，否则细长区域可能整片被漏掉；
   * 一个格子被停点 s 覆盖，当且仅当该格**四个角**到 s 的距离都 ≤ 计划半径
     （角点包含 ⇒ 整格包含，凸性保证），因此判据对连续区域是**严格保守**的；
   * 计划半径再留 :data:`RADIUS_SAFETY_MARGIN_M` 的余量。

3. **下一个停点 = 单位路程的最大"新增确认面积"**

   记 U_c 为频道 c 尚未确认的区域，w_c 为频道权重（:func:`channel_weight`）：:

       s* = argmax_s  sum_c  w_c * area(U_c ∩ D(s, plan_radius_c))  /  travel(s)

   "已经确认过的面积"收益为 0，所以不会为了重复确认白跑路。

定向源（问题4）另有一条更强的几何设计原则，见
:func:`heading_cover_condition`：对候选源位置 x，取"距离不超过 1000m 的已测
停点"集合 S_x；若 ``x ∈ conv(S_x)``，则**任意发射朝向**都至少有一个停点落在
其可见半平面内 —— 此时停点集合构成的不是"圆盘覆盖"而是"朝向覆盖"，
而"任意有限个停点都能被一个定向源背对"这个说法本身是错的。
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
    """一个频道的可行域 + R 的安全标量界 + 证据计数。"""

    channel: int
    #: 保守可行域（凸多边形）。空 = 还没收到过信号
    region: list[Point] = field(default_factory=list)
    #: R 的**安全下界** L：正例距离的 max-min 见证（见 :func:`estimate_radius_bounds`）
    radius_lo_m: float = MIN_EFFECTIVE_RADIUS_M
    #: R 的**安全上界** U：负例距离的 min-max 见证
    radius_hi_m: float = MAX_EFFECTIVE_RADIUS_M
    #: 上下界的来源标签（论文里要解释"凭什么敢用这个半径"）
    radius_source: str = "prior"
    #: 覆盖判据实际使用的半径 = max(300, radius_lo_m - margin)。
    #: **必须是下界**：真实 R 可能只有 L，用大于 L 的半径规划就会漏源。
    plan_radius_m: float = MIN_EFFECTIVE_RADIUS_M - RADIUS_SAFETY_MARGIN_M
    n_find: int = 0
    n_not_find: int = 0
    near: bool = False
    cleared: bool = False

    @property
    def radius_hat_m(self) -> float:
        """兼容旧字段名：等于安全下界 ``radius_lo_m``。

        .. deprecated::
           历史上这里存的是"负例到可行域最短距离"，方向是错的；现在
           只作为下界的别名保留，新代码请直接用 ``radius_lo_m`` / ``radius_hi_m``。
        """
        return self.radius_lo_m

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
            "radius_lo_m": round(self.radius_lo_m, 1),
            "radius_hi_m": round(self.radius_hi_m, 1),
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
        """目标圆域 Ω 的格集合：**格心**落在圆内。

        这里刻意用格心口径（而不是像 :meth:`mask_inside_polygon` 那样用外包）：
        Ω 是题目给定的硬边界，源不可能落在外面，所以"贴边格"可以忽略；
        而判据的严格保守性由"格子的四个角都必须被覆盖"承担 ——
        格子被算入待确认集合 + 四角都被覆盖 ⇒ 整格（含贴边部分）都被覆盖。

        反而若把Ω的外包也算进来，会引入 Ω 之外的点，而那些点不保证能被
        1000m 覆盖半径的扫描布局照到（实测 7 点布局覆盖半径 933.7m，
        但 Ω 外 1860m 处最坏距离 1101.5m），让证书凭空失败。
        """
        mask = 0
        for j in range(self.n):
            for i in range(self.n):
                c = self.center(i, j)
                if math.hypot(c.x, c.y) <= ARENA_RADIUS_M:
                    mask |= 1 << self.index(i, j)
        return mask

    def mask_intersecting_circle(self, center: Point, radius: float) -> int:
        """与圆盘相交的格子集合（格心可在圆外，只要格子沾到圆）。"""
        i0, j0 = self.to_ij(Point(center.x - radius, center.y - radius))
        i1, j1 = self.to_ij(Point(center.x + radius, center.y + radius))
        r2 = radius * radius
        mask = 0
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                if self._cell_intersects_disk(i, j, center, r2):
                    mask |= 1 << self.index(i, j)
        return mask

    def _cell_intersects_disk(self, i: int, j: int, center: Point, r2: float) -> bool:
        """格子（闭正方形）是否与圆盘相交 —— 圆盘到轴对齐矩形的最小距离判定。"""
        x0 = self.origin + i * self.cell_m
        y0 = self.origin + j * self.cell_m
        x1 = x0 + self.cell_m
        y1 = y0 + self.cell_m
        dx = max(x0 - center.x, 0.0, center.x - x1)
        dy = max(y0 - center.y, 0.0, center.y - y1)
        return dx * dx + dy * dy <= r2

    def mask_inside_polygon(self, poly: Sequence[Point]) -> int:
        """**栅格外包**：与多边形相交的格子（格心落在区域外也要纳入）。

        历史实现只收"格心在区域内"的格子，会整片漏掉"边界穿过、格心在外"的
        格子，对细长可行域尤其致命 —— 那是把"还没确认"的区域从证书里删掉，
        等于自证覆盖。这里改成两遍：先按包围盒取**候选格**（格心在区域内的
        直接用点测试，格心在外的再做矩形-多边形相交测试），保证不漏。

        保守口径下"格心在区域外但格子沾到区域"也算未确认：判据宁可多补一测。
        """
        if len(poly) < 3:
            return 0
        xs = [p.x for p in poly]
        ys = [p.y for p in poly]
        i_lo = max(0, int(math.floor((min(xs) - self.origin) / self.cell_m)) - 1)
        i_hi = min(self.n - 1, int(math.floor((max(xs) - self.origin) / self.cell_m)) + 1)
        j_lo = max(0, int(math.floor((min(ys) - self.origin) / self.cell_m)) - 1)
        j_hi = min(self.n - 1, int(math.floor((max(ys) - self.origin) / self.cell_m)) + 1)
        mask = 0
        for j in range(j_lo, j_hi + 1):
            y = self.origin + (j + 0.5) * self.cell_m
            for i in range(i_lo, i_hi + 1):
                x = self.origin + (i + 0.5) * self.cell_m
                if point_in_polygon(Point(x, y), poly) or self._cell_intersects_polygon(i, j, poly):
                    mask |= 1 << self.index(i, j)
        return mask

    def _cell_intersects_polygon(self, i: int, j: int, poly: Sequence[Point]) -> bool:
        """格子（闭正方形）是否与**凸**多边形相交（分离轴判定）。

        四条盒边法线 + 多边形各边法线都是候选分离轴：只要存在一条轴的投影
        区间不相交，二者就不相交。凸性保证这是充要条件。
        """
        x0 = self.origin + i * self.cell_m
        y0 = self.origin + j * self.cell_m
        x1 = x0 + self.cell_m
        y1 = y0 + self.cell_m
        box_xs = (x0, x1, x1, x0)
        box_ys = (y0, y0, y1, y1)
        # 盒的两条轴
        for axis in ((1.0, 0.0), (0.0, 1.0)):
            lo, hi = self._project(box_xs, box_ys, axis)
            plo, phi = self._project_poly(poly, axis)
            if hi < plo or phi < lo:
                return False
        n = len(poly)
        for k in range(n):
            a = poly[k]
            b = poly[(k + 1) % n]
            nx, ny = -(b.y - a.y), b.x - a.x
            if abs(nx) < 1e-12 and abs(ny) < 1e-12:
                continue
            lo, hi = self._project(box_xs, box_ys, (nx, ny))
            plo, phi = self._project_poly(poly, (nx, ny))
            if hi < plo or phi < lo:
                return False
        return True

    @staticmethod
    def _project(xs: Sequence[float], ys: Sequence[float], axis: tuple[float, float]) -> tuple[float, float]:
        vals = [x * axis[0] + y * axis[1] for x, y in zip(xs, ys)]
        return min(vals), max(vals)

    @staticmethod
    def _project_poly(poly: Sequence[Point], axis: tuple[float, float]) -> tuple[float, float]:
        vals = [p.x * axis[0] + p.y * axis[1] for p in poly]
        return min(vals), max(vals)

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
    """从知识矩阵重建每个频道的信息（正例 → 可行域；负例 → R 的安全上界）。

    ``directional=True``（问题4）只影响**候选位置**的剪枝口径（负例不再做
    圆盘排除，改由 :func:`plausible_directional_mask` 的"同一朝向 + 同一半径"
    联合可行性判定处理）；R 的标量界与规划半径在两种模式下**完全一致** ——
    题目给的 R 先验区间 [1000, 1500] 与是否定向无关，所以"规划半径可以放宽到
    1500m"是错的：真实的 R 可能只有 1000m。
    """
    del directional
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

        lo, hi, source = estimate_radius_bounds(
            region, readings, not_find_points, max_range_m=max_range_m
        )
        belief.radius_lo_m = lo
        belief.radius_hi_m = hi
        belief.radius_source = source
        # 规划半径必须是**安全下界**（减余量）：真实 R 可能就只有 lo。
        belief.plan_radius_m = max(300.0, lo - RADIUS_SAFETY_MARGIN_M)
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


@dataclass(frozen=True, slots=True)
class RadiusBounds:
    """有效接收半径 R 的安全标量界（见模块文档公式 1）。"""

    lower: float
    upper: float
    source: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "lower_m": round(self.lower, 1),
            "upper_m": round(self.upper, 1),
            "source": self.source,
        }


def estimate_radius_bounds(
    region: Sequence[Point],
    readings: Sequence[tuple[Point, float]],
    not_find_points: Sequence[Point],
    *,
    max_range_m: float = MAX_EFFECTIVE_RADIUS_M,
    min_range_m: float = MIN_EFFECTIVE_RADIUS_M,
) -> tuple[float, float, str]:
    r"""R 的安全上下界（模块文档公式 1），返回 ``(L, U, 来源标签)``。

    正例（检测点 p）保证 ``|p - x| <= R``，x ∈ P 未知 ⇒ 取所有 x ∈ P 的**最小**
    见证距离是 R 的安全**下界**::

        L = max(min_range, max_{p} min_{x∈P} |p - x|)

    负例（检测点 q）保证 ``R < |q - x|`` ⇒ 取所有 x ∈ P 的**最大**距离是 R 的
    安全**上界**::

        U = min(max_range, min_{q} max_{x∈P} |q - x|)

    **不要**用负例到 P 的最短距离当 R 的估计：那个量既不是上界也不是下界。
    反例：R=1000、真值在 (1020,0)、负例在原点。负例到可行域最短距离≈0，
    若据此认为"R≈0 或 R 很小"就会把可行域整片判成"已确认"；而按上式
    ``U = max_{x∈P}|q-x| = 1020 > 1000 = L``，判据不会做出任何错误结论。
    """
    lo = float(min_range_m)
    hi = float(max_range_m)
    notes: list[str] = []

    if readings and len(region) >= 3:
        witness = max(distance_point_to_region(p, region) for p, _ in readings)
        lo = max(lo, min(float(max_range_m), witness))
        notes.append("find_lower")
    elif readings:
        # 可行域退化成点/空（读数互相矛盾）：无法给出比先验更强的下界
        notes.append("find_no_region")

    if not_find_points and len(region) >= 3:
        witness_u = min(max_distance_to_region(q, region) for q in not_find_points)
        hi = min(hi, max(float(min_range_m), witness_u))
        notes.append("not_find_upper")
    elif not_find_points:
        notes.append("not_find_no_region")

    if not readings:
        notes = ["prior_min"] if not not_find_points else ["prior_min", *notes]
    source = "+".join(notes) if notes else "prior"
    return (lo, hi, source)


def estimate_radius(
    region: Sequence[Point],
    not_find_points: Sequence[Point],
    *,
    n_find: int,
    readings: Sequence[tuple[Point, float]] | None = None,
    max_range_m: float = MAX_EFFECTIVE_RADIUS_M,
) -> tuple[float, str]:
    """兼容旧调用：返回 ``(安全下界 L, 来源标签)``。

    .. deprecated::
       历史上这里返回的是"负例到可行域最短距离"并把它当成 R 的估计（方向错）。
       现在只返回安全**下界**；需要上界请直接用 :func:`estimate_radius_bounds`。
    """
    if n_find == 0:
        return (MIN_EFFECTIVE_RADIUS_M, "prior_min")
    lo, _hi, source = estimate_radius_bounds(
        region, readings or [], not_find_points, max_range_m=max_range_m
    )
    return (lo, source)


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
        #: 问题4：把"圆盘覆盖"升级成"朝向覆盖"（见 :meth:`heading_mask`）
        self.heading_cover: bool = False
        #: 已登记停点的坐标列表（朝向覆盖掩码是它们累积出来的）
        self._heading_stops: list[tuple[float, float]] = []
        #: 已累积的朝向覆盖掩码（新停点只做增量并入）
        self._heading_mask: int = 0

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

        * 已收到信号的频道 -> 用其凸可行域（栅格外包）
        * 从未收到信号的频道 -> 用整片区域（真的可能在任何地方）再挖掉负例圆盘
        * ``directional=True`` -> 再与"同一朝向 + 同一半径可解释全部观测"的
          联合可行性掩码求**交**（问题4）
        * ``plausible`` 可选：外部再给一层"仍然可能是源"的格掩码
        """
        self.matrix = matrix or self.matrix
        arena_mask = self.grid.mask_inside_arena()
        for channel, belief in beliefs.items():
            if belief.cleared:
                self.states.pop(channel, None)
                self.plausible.pop(channel, None)
                continue
            if belief.region and len(belief.region) >= 3:
                mask = self.grid.mask_inside_polygon(belief.region)
            else:
                # ── 还没收到信号的频道 ──────────────────────────────────
                # 关键一步：它**不是**"到处都可能"。已经在 k 个停点收不到信号，
                # 就说明源不在那些停点 1000m 内 —— 候选区域是"整片区域
                # 减去这些圆盘"。这正是负例的收益来源：
                # 候选区域随扫描推进单调收缩，需要的补测停点因此变少。
                mask = arena_mask
            if self.matrix is not None:
                mask &= ~self._not_find_exclusion(
                    belief.channel, belief, directional=self.directional
                )
            if self.directional:
                # 定向源：用"同一朝向 + 同一半径"的联合可行性判据删掉确定不可能的位置。
                # 候选区域 = "仍然可能是源的位置"，所以这里是**交集**。
                mask &= self._plausible_cache(channel, belief)
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
        margin_m: float | None = None,
        directional: bool = False,
    ) -> int:
        """负例的圆盘排除集：``not_find`` 在 q 表明"源不在 q 的 R 圆盘内"。

        排除半径 = :attr:`Belief.plan_radius_m`（= 安全下界 L − 余量）::

            R >= L >= plan_radius + margin  ⇒  源不可能落在 D(q, plan_radius) 内

        所以挖掉这个圆盘是**安全**的。这里必须与覆盖判据用**同一个**半径：
        历史实现取 ``max(R_hat, 1000) + 40``（R_hat 是"负例到可行域最短距离"），
        既把半径抬到超出安全下界（可能删掉真值位置），又与判据的 950m 不一致 ——
        结果在“计划圆盘”与“排除圆盘”之间留出一圈 10~90m 宽的环，
        凡是"每个停点都返回 no_signal"的频道（绝大多数是不存在的频道）永远
        无法确认，只能靠收尾的全量重扫兜底（实测每局白跑约 8km）。

        反例（方向错误）：R=1000、真值在 (1020,0)、负例在原点。历史实现的
        排除半径 1040 会把真值一起删掉。

        ``directional=True``（问题4）时不用圆盘：定向源背对任何方向都可能，
        负例的信息由 :func:`plausible_directional_mask` 的联合可行性判据处理。
        """
        assert self.matrix is not None
        if directional:
            return 0
        if margin_m is None:
            radius = belief.plan_radius_m
        else:
            radius = belief.plan_radius_m - margin_m
        if radius <= 0.0:
            return 0
        mask = 0
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

    def _plausible_cache(self, channel: int, belief: Belief) -> int:
        """"该位置仍然可能是源"的格掩码（定向源联合可行性判据，带缓存）。

        开销集中在"每格 × 候选朝向 × 约束"，只在该频道**观测数变化**时重算。
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
        bbox = _disk_cell_bbox(self.grid, not_finds, MAX_EFFECTIVE_RADIUS_M)
        candidates = None
        if bbox is not None:
            candidates = [
                (i, j)
                for j in range(bbox[2], bbox[3] + 1)
                for i in range(bbox[0], bbox[1] + 1)
            ]
        mask = plausible_directional_mask(
            self.grid,
            finds,
            not_finds,
            candidate_cells=candidates,
        )
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
            if self.heading_cover:
                self._register_heading_stop(p)
        heading = self._heading_mask if self.heading_cover else None
        for channel, state in self.states.items():
            radius = state.plan_radius_m
            covered = state.covered_mask
            for stop in fresh:
                if state.region_mask & ~covered == 0:
                    break
                disk = self._disk(stop, radius)
                covered |= disk if heading is None else (disk & heading)
            state.covered_mask = covered & state.region_mask

    def observe_partial_stop(self, stop: Point, channels: Iterable[int]) -> None:
        """只对**部分频道**做过检测的停点（按需选测时用）。"""
        cell_key = (round(stop.x, 3), round(stop.y, 3))
        self._seen_stop_keys.add(cell_key)
        if self.heading_cover:
            self._register_heading_stop(stop)
        heading = self._heading_mask if self.heading_cover else None
        for channel in channels:
            state = self.states.get(channel)
            if state is None:
                continue
            disk = self._disk(stop, state.plan_radius_m)
            state.covered_mask |= (disk if heading is None else (disk & heading)) & state.region_mask

    def heading_mask(self, channel: int) -> int:
        r""""该格只要被任何一个停点覆盖就**必定被发现**"的格掩码（问题4）。

        .. warning::
           定向源**不能**用"圆盘覆盖"当发现保证。反例（实测 Q4 seed 7 频道 2）：
           源在 (-1194,-1282)、朝向 209.9°；停点 (-950,-1645) 距它 438m，
           在该源 1031m 的有效接收半径之内，但"源→停点"的方向是 **303.9°**，
           与朝向相差 94.1° > 90°，于是**看不到**。若只按"某个停点在 950m 内"
           就判"该位置已确认"，这个源会被整片漏掉 —— 实测就是这样漏的。

           正确的可证伪条件只有**朝向覆盖**：候选位置 x 满足
           ``x ∈ conv(S_x)``（``S_x`` = 距 x 不超过 1000m 的停点），则任意朝向
           都至少有一个停点落在可见半平面内（见 :func:`heading_cover_condition`）。

        离散化：格心到 ``conv(S_x)`` 的距离 ≤ 格对角线（Minkowski 腐蚀量）时，
        认为**整格**都被朝向覆盖。

        实现是**增量**的：每登记一个新停点就把"它新增覆盖的格"并入累积掩码。
        掩码只增不减，因此增量结果与全量重算等价，但省掉 O(停点数) 倍开销。
        """
        _ = channel
        return self._heading_mask

    def _heading_cells_in_disk(self, center: Point, radius: float) -> Iterator[tuple[int, int, Point]]:
        """圆盘范围内（按包围盒裁剪）的 ``(i, j, 格心)``。"""
        arena = self.grid.mask_inside_arena()
        i0, j0 = self.grid.to_ij(Point(center.x - radius, center.y - radius))
        i1, j1 = self.grid.to_ij(Point(center.x + radius, center.y + radius))
        n = self.grid.n
        r2 = radius * radius
        for j in range(j0, j1 + 1):
            y = self.grid.origin + (j + 0.5) * self.grid.cell_m
            for i in range(i0, i1 + 1):
                idx = j * n + i
                if not (arena >> idx) & 1:
                    continue
                x = self.grid.origin + (i + 0.5) * self.grid.cell_m
                dx = x - center.x
                dy = y - center.y
                if dx * dx + dy * dy <= r2:
                    yield i, j, Point(x, y)

    def _register_heading_stop(self, stop: Point) -> None:
        """登记一个新停点并**增量**更新朝向覆盖掩码（见 :meth:`heading_mask`）。"""
        key = (round(stop.x, 3), round(stop.y, 3))
        if key in self._heading_stops:
            return
        self._heading_stops.append(key)
        erode = self.grid.cell_m * math.sqrt(2.0)
        search_radius = MAX_EFFECTIVE_RADIUS_M + erode
        stops = [Point(x, y) for x, y in self._heading_stops]
        n = self.grid.n
        newly = 0
        for i, j, center in self._heading_cells_in_disk(stop, search_radius):
            idx = j * n + i
            if (self._heading_mask >> idx) & 1:
                continue
            near = [q for q in stops if center.distance_to(q) <= MAX_EFFECTIVE_RADIUS_M + erode]
            if len(near) < 3:
                continue
            if min_convex_distance(center, near) <= erode:
                self._heading_mask |= 1 << idx
                newly += 1
        del newly

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
        r"""三角格候选停点（覆盖整个目标区域，含边界外一圈）。

        用**三角格**（hexagonal lattice）而不是方阵：同样间距下三角格的覆盖半径
        只有方阵的 :math:`1/\sqrt2` 量级，因此"候选集合本身"就是一个合法覆盖，
        贪心集合覆盖才有终止保证。间距取 :math:`1.70\R_hat` 时，
        覆盖半径 :math:`\~ 0.98\R_hat < \R_hat`。

        候选点数量在 :math:`|{区域}|/{间距}^2` 量级（数百个），
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

    #: 覆盖用候选格点间距与 :math:`R_hat` 的比值。三角格覆盖定理给出
    #: :math:`sqrt3R ~ 1.732R` 是"保证覆盖"的最大间距；
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
        * 本方法直接用**贪心集合覆盖**：候选点间距取 :math:`1.70R_hat`
          （三角格定理保证这个间距的格点集合本身就是覆盖），每次挑"能盖住
          最多未确认格"的点，路程只作为并列打破项。

        因为候选格点集合本身是覆盖，这条贪心一定在
        :math:`O({面积}/R_hat^2)` 步内把缺口清零，不会无限打转。
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
# 定向源（问题4）：同一朝向 + 同一半径的联合可行性
# ---------------------------------------------------------------------------
#: 判定"远场负例"的距离：超过它，全向源也收不到，因此与锥朝向无关
FAR_FIELD_M: float = MAX_EFFECTIVE_RADIUS_M

#: 禁区放宽量（度）：判定"负例被朝向挡住"时把禁区两侧各放宽这么多，
#: 让判定偏向保守（宁可多留一个候选位置，也不误删真值位置）。
HEADING_BLOCK_MARGIN_DEG: float = 0.0
#: 数值容差（度）：临界朝向上把"刚好贴边"算作满足约束（保守方向）。
HEADING_TOLERANCE_DEG: float = 1e-6
#: 判定"可行朝向集合非空"的最小剩余弧长（度）。切点处可行集恰好退化成一点
#: （测度为零），按"不存在可行朝向"处理 —— 与"源可以选任意实数朝向"一致。
HEADING_FEASIBLE_EPS_DEG: float = 1e-9


def _heading_sees(
    ux: float,
    uy: float,
    x: float,
    y: float,
    point: Point,
    half_width_deg: float,
    tol: float = 1e-9,
) -> bool:
    """朝向 ``u`` 下，位于 (x,y) 的源是否能看到 ``point``。

    覆盖角 ±90°，再叠加示向度误差 δ：``|wrap(θ_true - θ̂)| <= δ`` 意味着
    即使真实方位与读数差 δ，仍然算"看得到"。
    """
    dx = point.x - x
    dy = point.y - y
    d = math.hypot(dx, dy)
    if d <= 0.0:
        return True
    cos_limit = math.cos(math.pi / 2.0 + math.radians(half_width_deg) + tol)
    return (ux * dx + uy * dy) / d >= cos_limit


def _arc_intersection(
    arcs: Sequence[tuple[float, float]],
) -> tuple[float, float] | None:
    """圆弧区间求交（角度单位：度，区间可跨越 360° 折返）。

    每个区间按 ``[lo, hi]`` 给出，跨度 ≤ 360；``hi`` 允许大于 360 表示跨越
    0°。实现方式是把折返区间复制一份平移 ±360° 后与其它区间做普通的
    一维区间求交 —— 圆上区间只有"折返/不折返"两种，复制平移是标准做法。
    """
    if not arcs:
        return None
    lo, hi = arcs[0]
    for a_lo, a_hi in arcs[1:]:
        best: tuple[float, float] | None = None
        for shift in (-360.0, 0.0, 360.0):
            cand_lo = max(lo, a_lo + shift)
            cand_hi = min(hi, a_hi + shift)
            if cand_lo <= cand_hi + 1e-9:
                if best is None or (cand_hi - cand_lo) > (best[1] - best[0]):
                    best = (cand_lo, cand_hi)
        if best is None:
            return None
        lo, hi = best
    return (lo, hi)


def _subtract_intervals(
    arc: tuple[float, float],
    blocks: Sequence[tuple[float, float]],
) -> list[tuple[float, float]]:
    """从区间 ``arc`` 中逐个挖掉 ``blocks``，返回剩余的区间列表。

    所有区间都在"同一条实数轴"上比较：折返区间用 ``hi > 360`` 表示，
    必要时整体平移 ±360° 对齐后再挖。
    """
    pieces: list[tuple[float, float]] = [arc]
    for b_lo, b_hi in blocks:
        nxt: list[tuple[float, float]] = []
        for p_lo, p_hi in pieces:
            cursor = p_lo
            segments: list[tuple[float, float]] = []
            for shift in (-360.0, 0.0, 360.0):
                s_lo = max(b_lo + shift, p_lo)
                s_hi = min(b_hi + shift, p_hi)
                if s_hi > s_lo:
                    segments.append((s_lo, s_hi))
            segments.sort()
            for s_lo, s_hi in segments:
                if s_lo > cursor:
                    nxt.append((cursor, s_lo))
                cursor = max(cursor, s_hi)
            if cursor < p_hi:
                nxt.append((cursor, p_hi))
        pieces = [(a, b) for a, b in nxt if b - a > 1e-9]
        if not pieces:
            return []
    return pieces


def _has_feasible_heading(
    near_pos: Sequence[tuple[float, float]],
    blocking: Sequence[tuple[float, float]],
    half_width_deg: float,
) -> bool:
    r"""是否存在朝向 u 使"看到全部正例"且"看不到全部 blocking 负例"。

    朝向 u 必须落在每个正例方向的**可见弧**里（源能看见检测点 p
    ⇔ ``|wrap(dir(x→p) - u)| <= 90° + δ``），因此可行集是若干圆心角为
    :math:`180°+2\delta` 的弧的交；每个必须靠朝向解释的负例 q 又要求
    ``u`` 落在以 :math:`\mathrm{dir}(x	o q) \pm 90°` 为**禁区**的外侧
    （``u·(q-x) < 0``）。两侧都是圆弧区间，于是"存在性"就是标准的
    **区间求交 + 区间挖洞**，可以精确判定 —— 不必再靠启发式采样。

    为了让数值判定偏向保守（宁可多留一个候选位置，也不误删真值位置），
    禁区在这里额外放宽 :data:`HEADING_BLOCK_MARGIN_DEG` 度。
    """
    tol = 90.0 + half_width_deg
    arcs = [
        (math.degrees(math.atan2(dy, dx)) - tol, math.degrees(math.atan2(dy, dx)) + tol)
        for dx, dy in near_pos
    ]
    arc = _arc_intersection(arcs)
    if arc is None:
        return False
    blocks: list[tuple[float, float]] = []
    for dx, dy in blocking:
        base = math.degrees(math.atan2(dy, dx))
        blocks.append((base - 90.0 - HEADING_BLOCK_MARGIN_DEG, base + 90.0 + HEADING_BLOCK_MARGIN_DEG))
    remaining = _subtract_intervals(arc, blocks)
    # 只有"长度超过数值噪声"的剩余区间才算"存在可行朝向"：切点处（可行集恰好
    # 退化成一点）按"不存在"处理 —— 与"源可以选任意实数朝向"的连续模型一致，
    # 而历史实现在这种临界点上前后不一致。
    return any(b - a > HEADING_FEASIBLE_EPS_DEG for a, b in remaining)


def plausible_directional_mask(
    grid: "CoverageGrid",
    finds: Sequence[tuple[Point, float]],
    not_finds: Sequence[Point],
    *,
    half_width_deg: float = 1.0,
    radius_min_m: float = MIN_EFFECTIVE_RADIUS_M,
    radius_max_m: float = MAX_EFFECTIVE_RADIUS_M,
    candidate_cells: Sequence[tuple[int, int]] | None = None,
) -> int:
    r"""定向假设下"该位置仍然可能是源"的格掩码 —— 问题4 的核心几何。

    与历史实现（按正例逐个点积判断"负例是否必然可见"）的关键区别是**联合**：

    * 同一个朝向向量 u 必须同时解释**所有**观测，不能一个负例换一套朝向；
    * 同一个半径 R 也必须同时解释所有观测，且 ``R ∈ [R_min, R_max]``。

    对候选源位置 x 记 ``P_x`` 为距离不超过 :math:`R_{\max}` 的**正例**点集：

    1. ``P_x`` 为空（所有正例都太远）⇒ 源在 x 时看不到任何正例，已证伪；
    2. 取 ``r_lo = max_{p∈P_x}|x-p|``、``r_hi = min_{q∈Q_x}|x-q|``
       （``Q_x`` 是距离不超过 :math:`R_{\max}` 的负例点集）；需要
       ``r_lo <= r_hi`` 且 ``r_hi >= R_min`` 才能取到共同的 ``R``；
    3. 若 ``|x-q| <= r_lo``，则任何允许的 R 都够得着 q，必须靠朝向解释：
       ``u·(q-x) < 0``；
    4. 存在满足 1~3 的朝向 u ⇒ x 仍可能是源（保留该格）。

    朝向的存在性用 :func:`_has_feasible_heading` 精确判定（圆弧区间求交 +
    挖洞），不依赖采样。

    反例一（历史实现的错误）：源在原点、朝向 80°，两个测点方位 0° 与 -20°、
    距离都是 500m。两点夹角只有 20°、点积非负，历史实现据此断言"负例方向与
    每个正例方向点积非负 ⇒ 必定可见"，会把真值位置删掉；而实际朝向 80° 的源
    看不到 -20° 的那个点。本函数按同一个 u 求交，不会犯这个错。

    反例二（评审指出的错误命题）："任意有限测点都能被一个定向源背对"不成立 ——
    在候选 x 周围 0°/120°/240° 各放一个足够近的测点，就没有朝向能同时避开三者
    （本函数会正确地证伪 x）。

    反例三（半径方向的错误）：把"负例到可行域最短距离"当作 R 的上界、并据此
    放大排除半径，会把真实源一起删掉。这里 R 的区间由 ``r_lo``/``r_hi`` 联合
    约束，不用任何单侧的"最短距离"估计。
    """
    mask = grid.mask_inside_arena()
    if not not_finds:
        return mask
    if not finds:
        # 还没有任何正例 -> 锥朝向近乎完全未知，任何位置都可能有一个"背对全部
        # 停点"的朝向，因此这里**一个格都不能删**（保守方向，不会漏源）。
        return mask
    if candidate_cells is not None:
        cells = [(i, j) for i, j in candidate_cells if (mask >> grid.index(i, j)) & 1]
    else:
        bbox = _disk_cell_bbox(grid, not_finds, radius_max_m)
        if bbox is None:
            return mask
        cells = [
            (i, j)
            for j in range(bbox[2], bbox[3] + 1)
            for i in range(bbox[0], bbox[1] + 1)
            if (mask >> grid.index(i, j)) & 1
        ]
    if not cells:
        return mask

    origin = grid.origin
    cell = grid.cell_m
    n = grid.n
    r2max = radius_max_m * radius_max_m
    finds_xy = [(p.x, p.y) for p, _ in finds]
    not_find_xy = [(q.x, q.y) for q in not_finds]
    for i, j in cells:
        x = origin + (i + 0.5) * cell
        y = origin + (j + 0.5) * cell
        near_pos: list[tuple[float, float]] = []
        r_lo = 0.0
        for px, py in finds_xy:
            dx = px - x
            dy = py - y
            d2 = dx * dx + dy * dy
            if d2 > r2max:
                continue
            near_pos.append((dx, dy))
            if d2 > r_lo * r_lo:
                r_lo = math.sqrt(d2)
        if not near_pos:
            mask &= ~(1 << (j * n + i))
            continue

        r_hi = radius_max_m
        blocking: list[tuple[float, float]] = []
        for qx, qy in not_find_xy:
            dx = qx - x
            dy = qy - y
            d2 = dx * dx + dy * dy
            if d2 > r2max:
                continue
            d = math.sqrt(d2)
            if d < r_hi:
                r_hi = d
            if d2 <= r_lo * r_lo:
                blocking.append((dx, dy))
        if r_lo > r_hi or r_hi < radius_min_m:
            mask &= ~(1 << (j * n + i))
            continue
        if not blocking:
            continue
        if not _has_feasible_heading(near_pos, blocking, half_width_deg):
            mask &= ~(1 << (j * n + i))
    return mask


def hidden_mask(
    grid: "CoverageGrid",
    finds: Sequence[tuple[Point, float]],
    probes: Sequence[Point],
    *,
    radius_min_m: float = MIN_EFFECTIVE_RADIUS_M,
    half_width_deg: float = 1.0,
    n_heading: int = 36,
) -> int:
    r"""锥盲区掩码：**在任何已测停点上都发现不了源**的候选位置集合。

    与 :func:`plausible_directional_mask` 的语义完全不同，别混：

    * ``plausible_directional_mask`` = "删掉已被证伪的位置"，用于**判据/剪枝**；
    * ``hidden_mask``                = "留下来还没被任何停点照到的位置"，用于**决定去哪补测**。

    判据（对候选源位置 S）：存在一个朝向 u，使得对**所有**已测停点 p 都有
    ``|S-p| > R_{\min}`` 或 ``u·(p-S) < 0``。含义：每个停点要么离得太远，
    要么源可以合法地把锥背对它。

    注意这里**只做朝向推理**：``|S-p| > R_min`` 这一侧用安全下界是对的
    （"距离超过 1000m ⇒ 无论 R 是多少都收不到"）。历史实现在这里没错，
    错在别处（见 :func:`plausible_directional_mask` 与
    :meth:`CoverageTracker._not_find_exclusion`）。
    """
    mask = grid.mask_inside_arena()
    if not probes:
        return mask
    if not finds:
        # 一个正例都没有 -> 锥朝向完全未知：任何位置都可以背对全部停点躲开，
        # 也就是说**整片区域都是盲区**，只能靠"覆盖整个区域"来兜底。
        return mask
    bbox = _disk_cell_bbox(grid, probes, radius_min_m)
    if bbox is None:
        return 0
    i0, i1, j0, j1 = bbox
    hidden = 0
    r2 = radius_min_m * radius_min_m
    origin = grid.origin
    cell = grid.cell_m
    n = grid.n
    step = 360.0 / max(1, n_heading)
    for j in range(j0, j1 + 1):
        y = origin + (j + 0.5) * cell
        for i in range(i0, i1 + 1):
            idx = j * n + i
            if not (mask >> idx) & 1:
                continue
            x = origin + (i + 0.5) * cell
            reachable = False
            for probe in probes:
                dx = probe.x - x
                dy = probe.y - y
                if dx * dx + dy * dy <= r2:
                    reachable = True
                    break
            if not reachable:
                hidden |= 1 << idx
                continue
            for k in range(max(1, n_heading)):
                ang = math.radians(step * k)
                ux = math.cos(ang)
                uy = math.sin(ang)
                if all(
                    (probe.x - x) ** 2 + (probe.y - y) ** 2 > r2
                    or not _heading_sees(ux, uy, x, y, probe, half_width_deg)
                    for probe in probes
                ):
                    hidden |= 1 << idx
                    break
    return hidden


# ---------------------------------------------------------------------------
# 问题4：朝向覆盖（heading cover）设计与证书
# ---------------------------------------------------------------------------
#: 朝向覆盖里"近距离"的门槛：只有距离不超过它的测点才算"能看见候选源"。
#: 取题目给出的有效接收半径**下界**：真实 R 可能就只有 1000m。
HEADING_COVER_RADIUS_M: float = MIN_EFFECTIVE_RADIUS_M


def point_segment_distance(p: Point, a: Point, b: Point) -> float:
    """点到线段的距离。"""
    return _distance_point_segment(p, a, b)


def _dot(u: tuple[float, float], p: Point) -> float:
    return u[0] * p.x + u[1] * p.y


def min_convex_distance(point: Point, points: Sequence[Point]) -> float:
    r"""点到凸包 ``conv(points)`` 的距离；点在凸包内返回 0。

    利用支撑函数的线性规划对偶（GJK 式的"沿方向最大化"）：

    .. math::

        d(x, \mathrm{conv}(S)) = \max_{|u| = 1} \Big[ u\cdot x - \max_{s\in S} u\cdot s \Big]

    内层最大值在 ``S`` 的顶点上取到，所以不需要显式求凸包。数值上用
    "最佳方向 + 90° 扫描 + 二分"三级搜索逼近最优方向：对"点到凸集距离"
    这个单峰函数足够稳定，而且**只会低估**距离（更保守：距离被低估 ⇒ 更
    容易判成"在外面" ⇒ 更不容易误报朝向覆盖成立）。
    """
    pts = list(points)
    if not pts:
        return math.inf
    if len(pts) == 1:
        return point.distance_to(pts[0])
    if len(pts) == 2:
        return point_segment_distance(point, pts[0], pts[1])

    base = math.atan2(point.y - sum(p.y for p in pts) / len(pts), point.x - sum(p.x for p in pts) / len(pts))
    if point.x == 0.0 and point.y == 0.0:
        base = 0.0
    best = _support_gap(point, pts, base)
    coarse = list(range(0, 360, 15))
    for k in coarse:
        best = max(best, _support_gap(point, pts, math.radians(k)))
    if best <= 0.0:
        return 0.0
    # 围绕当前最优方向做细化（三分搜索）
    best_angle = base
    vals = [(math.radians(k), _support_gap(point, pts, math.radians(k))) for k in coarse]
    best_angle = max(vals, key=lambda kv: kv[1])[0]
    lo = best_angle - math.radians(15.0)
    hi = best_angle + math.radians(15.0)
    for _ in range(40):
        m1 = lo + (hi - lo) / 3.0
        m2 = hi - (hi - lo) / 3.0
        if _support_gap(point, pts, m1) < _support_gap(point, pts, m2):
            lo = m1
        else:
            hi = m2
    best = max(best, _support_gap(point, pts, 0.5 * (lo + hi)))
    return max(0.0, best)


def _support_gap(point: Point, pts: Sequence[Point], angle: float) -> float:
    u = (math.cos(angle), math.sin(angle))
    return _dot(u, point) - max(_dot(u, p) for p in pts)


def heading_cover_condition(
    source: Point,
    probes: Sequence[Point],
    *,
    radius_m: float = HEADING_COVER_RADIUS_M,
) -> bool:
    r"""**朝向覆盖的充分条件**：候选源在 :math:`\mathrm{conv}(S_x)` 内。

    记 :math:`S_x = \{q \in \text{已测停点} : |q-x| \le R_0\}`（``R_0`` 取有效
    接收半径的安全下界 1000m）。若

    .. math::

        x \in \mathrm{conv}(S_x)

    则**任意发射朝向 u**，都存在 :math:`q \in S_x` 使得
    :math:`u\cdot(q-x) \ge 0` —— 即 q 落在源的可见半平面内。

    证明（超平面分离）：反设存在朝向 u 使**所有** :math:`q \in S_x` 都有
    :math:`u\cdot(q-x) < 0`，则整片 :math:`\mathrm{conv}(S_x)` 都被严格分离
    半平面 :math:`\{y: u\cdot(y-x) < 0\}` 覆盖；但凸组合
    :math:`\sum \lambda_i (q_i - x) = x - x = 0` 又要求加权平均不小于 0，矛盾。

    注意这与"圆盘覆盖"不是一回事：只要 x 在附近停点的凸包里就够，
    停点不必密到"离 x 不超过 R"。
    """
    near = [q for q in probes if q.distance_to(source) <= radius_m + 1e-9]
    if len(near) < 3:
        return False
    return min_convex_distance(source, near) <= 1e-6


def heading_cover_layout(
    spacing_m: float = 950.0,
    *,
    arena_radius_m: float = ARENA_RADIUS_M,
) -> list[Point]:
    r"""等边三角网格（轴向）停点布局：问题4 的**可证明**朝向完备扫描集合。

    构造：格点 :math:`q_{ij} = (s(i + j/2),\; s\sqrt3/2 \cdot j)`，保留与目标
    圆域相交的闭三角形的顶点（即 ``|q| <= R_arena + s``，含界外补点）。

    完备性证明：任意 :math:`x \in \Omega` 落在某个边长为 ``s`` 的格三角形里，
    该三角形的三个顶点到 x 的距离都 ≤ ``s``，因此当 ``s <= 1000`` 时三者都在
    :math:`S_x` 内，而 :math:`x \in \mathrm{conv}\{\text{三顶点}\} \subseteq
    \mathrm{conv}(S_x)`，由 :func:`heading_cover_condition` 即得"任意朝向都能
    被发现"。**这就是"任意有限测点都能被定向源背对"这一说法的反例来源。**

    取 ``s = 950``（略小于 1000，留数值余量）。
    """
    h = spacing_m * math.sqrt(3.0) / 2.0
    limit = arena_radius_m + spacing_m
    m = int(math.ceil(limit / min(spacing_m, h))) + 1
    out: list[Point] = []
    for j in range(-m, m + 1):
        y = h * j
        for i in range(-m, m + 1):
            x = spacing_m * (i + j / 2.0)
            if math.hypot(x, y) <= limit + 1e-9:
                out.append(Point(x, y))
    return out


def heading_cover_report(
    stops: Sequence[Point],
    *,
    radius_m: float = HEADING_COVER_RADIUS_M,
    step_m: float = 40.0,
    arena_radius_m: float = ARENA_RADIUS_M,
) -> dict[str, Any]:
    """数值化验证朝向覆盖：目标区域内采样点 x 的"到 :math:`\\mathrm{conv}(S_x)` 距离"。

    返回 ``worst_hull_m``（采样点上最坏值，0 表示采样粒度下处处成立）、
    ``worst_hull_at``、``worst_nearest_m``（到最近停点的最大距离，用于对照
    "圆盘覆盖"这一更强的老判据）。**采样通过不等于连续完备**：对三角网格
    布局，连续完备性由 :func:`heading_cover_layout` 的证明给出。
    """
    worst_hull = -1.0
    worst_hull_at: Point | None = None
    worst_near = 0.0
    worst_near_at: Point | None = None
    r = 0.0
    while r <= arena_radius_m:
        n_ring = max(1, int(2.0 * math.pi * r / step_m))
        for k in range(n_ring):
            angle = 2.0 * math.pi * k / n_ring
            x = Point(r * math.cos(angle), r * math.sin(angle))
            near = [q for q in stops if q.distance_to(x) <= radius_m + 1e-9]
            if near:
                d_hull = min_convex_distance(x, near)
            else:
                d_hull = math.inf
            if d_hull > worst_hull:
                worst_hull = d_hull
                worst_hull_at = x
            d_near = min((q.distance_to(x) for q in stops), default=math.inf)
            if d_near > worst_near:
                worst_near = d_near
                worst_near_at = x
        r += step_m
    return {
        "worst_hull_m": worst_hull,
        "worst_hull_at": [round(worst_hull_at.x, 1), round(worst_hull_at.y, 1)] if worst_hull_at else None,
        "worst_nearest_m": worst_near,
        "worst_nearest_at": [round(worst_near_at.x, 1), round(worst_near_at.y, 1)] if worst_near_at else None,
        "radius_m": radius_m,
        "step_m": step_m,
        "n_stops": len(stops),
    }


def _disk_cell_bbox(
    grid: "CoverageGrid",
    points: Sequence[Point],
    radius: float,
) -> tuple[int, int, int, int] | None:
    """若干个半径 ``radius`` 圆盘并集的格坐标包围盒（超出网格则裁剪）。"""
    if not points:
        return None
    i0 = j0 = 10**9
    i1 = j1 = -10**9
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
