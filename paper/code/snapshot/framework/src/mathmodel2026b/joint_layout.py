r"""Q4 检测层：局部凸包完备性证书 + 点集/路径联合优化（方法二原型）。

数学对象
--------
目标域 :math:`\Omega=\{x:\|x\|\le 1800\}`，检测点集 :math:`P=\{q_i\}`。
若源位于 :math:`x`、定向方向为 :math:`n`，其可见半圆盘为

.. math::

    K(x,n)=\{y:\|y-x\|\le 1000,\ n^\top(y-x)\ge 0\}.

发现完备性的等价判据是

.. math::

    \forall x\in\Omega,\quad
    x\in\operatorname{conv}(P\cap \overline B(x,1000)).

方向 A：若上式成立，则任意 :math:`n` 都存在 :math:`q_i` 落在
:math:`K(x,n)` 内。证明：若所有近邻 :math:`q_i` 都满足
:math:`n^\top(q_i-x)<0`，则它们的凸包整体在严格负半空间内，与
:math:`x\in\operatorname{conv}(P\cap B)` 矛盾。

方向 B：单个 :math:`x` 的上式等价的另一面是超平面分离：若
:math:`x\notin\operatorname{conv}(S_x)`，则存在 :math:`n` 使
:math:`n^\top(q-x)<0` 对所有 :math:`q\in S_x` 成立，于是该朝向能避开所有
:math:`q`。但注意：**“任意连续路径完备”并不自动意味着“存在有限子集完备”**。
闭合覆盖的紧性不能直接给出有限子覆盖。实际题目里我们本来就执行有限检测点，
因此本模块直接从有限点集 :math:`P` 出发，把上述局部凸包式当作**硬证书**。

连续域的证书
------------
对任意有限点集，直接求连续域上的可行集很难，但可以用“正方形单元 + 充分条件”
做严格证书：

* 把 :math:`\Omega` 的包围盒切成正方形单元，并裁剪到外接多边形；
* 对单元 :math:`C`，取
  :math:`S_C=\{p_i:\max_{v\in\operatorname{vert}(C)}\|p_i-v\|\le 1000-\eta\}`；
* 若 :math:`\operatorname{vert}(C)\subset\operatorname{conv}(S_C)`，则
  :math:`C\subseteq\operatorname{conv}(S_C)`（凸性），且每个
  :math:`p_i\in S_C` 对任意 :math:`x\in C` 都在 1000m 内（范数凸性），
  因此 :math:`x\in\operatorname{conv}(P\cap B(x,1000))`。
* 若单元不够小导致上式不成立，就递归四分，直到 `min_cell_m`；仍未通过则
  证书返回 `uncertain`，外部必须回退。

这个证书是**充分而非必要**的，所以通过即安全；不通过只表示“可能不完备”，
优化器应回退或继续细分。

联合优化
--------
本模块实现方法二的工程原型：

1. 初始点集用 method-1 的轴向三角网格（间距 950m）；
2. 用 2-opt 优化开放访问顺序；
3. 尝试删除冗余点、微调点坐标，接受条件为：
   * 细采样局部凸包检查仍通过；
   * 目标函数（路程/检测成本）下降；
4. 每轮后用连续域证书复核，不通过就回退到上一份已证明可行的点集。

可选 SOCP 精调在 :func:`socp_refine`，依赖 `cvxpy`；没有安装时本地优化仍可
独立运行。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .coverage import heading_cover_layout
from .geometry import Point, arena_polygon

MIN_RADIUS_M = 1000.0
ARENA_RADIUS_M = 1800.0
EPS = 1e-8


# ---------------------------------------------------------------------------
# 基础二维几何
# ---------------------------------------------------------------------------
def _cross(o: Point, a: Point, b: Point) -> float:
    return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x)


def convex_hull(points: Sequence[Point]) -> list[Point]:
    """Andrew monotone chain；返回逆时针凸包，可少于 3 点。"""
    pts = sorted({(round(p.x, 10), round(p.y, 10)) for p in points})
    if len(pts) <= 1:
        return [Point(x, y) for x, y in pts]

    def build(seq: list[tuple[float, float]]) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for p in seq:
            while len(out) >= 2:
                o, a = out[-2], out[-1]
                cross = (a[0] - o[0]) * (p[1] - o[1]) - (a[1] - o[1]) * (p[0] - o[0])
                if cross > 1e-12:
                    break
                out.pop()
            out.append(p)
        return out

    lower = build(pts)
    upper = build(list(reversed(pts)))
    return [Point(x, y) for x, y in lower[:-1] + upper[:-1]]


def point_in_convex_hull(point: Point, points: Sequence[Point], tol: float = 1e-7) -> bool:
    """点是否在有限点集凸包内（含边界）。"""
    if not points:
        return False
    hull = convex_hull(points)
    if not hull:
        return False
    if len(hull) == 1:
        return point.distance_to(hull[0]) <= tol
    if len(hull) == 2:
        a, b = hull
        cross = abs((b.x - a.x) * (point.y - a.y) - (b.y - a.y) * (point.x - a.x))
        if cross > tol:
            return False
        return (
            min(a.x, b.x) - tol <= point.x <= max(a.x, b.x) + tol
            and min(a.y, b.y) - tol <= point.y <= max(a.y, b.y) + tol
        )
    for i in range(len(hull)):
        a = hull[i]
        b = hull[(i + 1) % len(hull)]
        if (b.x - a.x) * (point.y - a.y) - (b.y - a.y) * (point.x - a.x) < -tol:
            return False
    return True


def _clip_halfplane(poly: Sequence[Point], a: float, b: float, c: float, tol: float = 1e-10) -> list[Point]:
    """保留 ``a*x + b*y + c >= 0``，Sutherland-Hodgman。"""
    if not poly:
        return []
    out: list[Point] = []
    n = len(poly)
    for i in range(n):
        cur = poly[i]
        nxt = poly[(i + 1) % n]
        d_cur = a * cur.x + b * cur.y + c
        d_nxt = a * nxt.x + b * nxt.y + c
        if d_cur >= -tol:
            out.append(cur)
        if (d_cur > tol and d_nxt < -tol) or (d_cur < -tol and d_nxt > tol):
            t = d_cur / (d_cur - d_nxt)
            out.append(Point(cur.x + (nxt.x - cur.x) * t, cur.y + (nxt.y - cur.y) * t))
    return out


def clip_polygon(subject: Sequence[Point], clipper: Sequence[Point]) -> list[Point]:
    """用凸 clipper（逆时针）裁剪 subject（Sutherland-Hodgman）。

    这里只要求 clipper 凸；subject 可以是任意多边形。返回交叠凸多边形。
    """
    if len(subject) < 3 or len(clipper) < 3:
        return []
    out = list(subject)
    n = len(clipper)
    for i in range(n):
        a = clipper[i]
        b = clipper[(i + 1) % n]
        # 保留 a->b 左侧：cross(b-a, q-a) >= 0
        nx = -(b.y - a.y)
        ny = b.x - a.x
        c = -(nx * a.x + ny * a.y)
        out = _clip_halfplane(out, nx, ny, c)
        if len(out) < 3:
            return []
    return out


def _polygon_area(poly: Sequence[Point]) -> float:
    if len(poly) < 3:
        return 0.0
    s = 0.0
    for i in range(len(poly)):
        a = poly[i]
        b = poly[(i + 1) % len(poly)]
        s += a.x * b.y - b.x * a.y
    return abs(s) * 0.5


def _polygon_center(poly: Sequence[Point]) -> Point:
    if not poly:
        return Point(0.0, 0.0)
    return Point(
        sum(p.x for p in poly) / len(poly),
        sum(p.y for p in poly) / len(poly),
    )


# ---------------------------------------------------------------------------
# 局部凸包检查与连续域证书
# ---------------------------------------------------------------------------
def local_hull_holds_at(
    x: Point,
    points: Sequence[Point],
    *,
    radius_m: float = MIN_RADIUS_M,
    tol: float = 1e-7,
) -> bool:
    """单点检查：``x in conv({p_i: ||p_i-x|| <= 1000})``。"""
    near = [p for p in points if p.distance_to(x) <= radius_m + tol]
    return point_in_convex_hull(x, near, tol=tol)


@dataclass(slots=True)
class CertificateReport:
    """连续域局部凸包证书结果。"""

    ok: bool
    n_points: int
    root_cells: int
    checked_cells: int
    uncertain_cells: int
    max_depth: int
    witness: Point | None = None
    method: str = "cell-subdivision"

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "n_points": self.n_points,
            "root_cells": self.root_cells,
            "checked_cells": self.checked_cells,
            "uncertain_cells": self.uncertain_cells,
            "max_depth": self.max_depth,
            "witness": [self.witness.x, self.witness.y] if self.witness else None,
            "method": self.method,
        }


def _cell_sufficient(
    cell: Sequence[Point],
    points: Sequence[Point],
    *,
    radius_m: float,
    safety_margin_m: float,
    tol: float,
) -> bool:
    """单元 C 的充分条件：顶点都在 ``conv(S_C)`` 内。"""
    if len(cell) < 3 or _polygon_area(cell) <= 1e-9:
        return True
    effective_radius = radius_m - safety_margin_m
    S = [
        p
        for p in points
        if all(p.distance_to(v) <= effective_radius for v in cell)
    ]
    if not S:
        return False
    return all(point_in_convex_hull(v, S, tol=tol) for v in cell)


class _Certifier:
    def __init__(
        self,
        points: Sequence[Point],
        *,
        cell_m: float,
        min_cell_m: float,
        radius_m: float,
        arena_radius_m: float,
        safety_margin_m: float,
    ) -> None:
        self.points = list(points)
        self.cell_m = float(cell_m)
        self.min_cell_m = float(min_cell_m)
        self.radius_m = float(radius_m)
        self.arena = arena_polygon(arena_radius_m)
        self.extent = arena_radius_m * 1.01
        self.safety_margin_m = float(safety_margin_m)
        self.tol = max(1e-7, self.safety_margin_m * 10.0)
        self.checked = 0
        self.uncertain = 0
        self.max_depth = 0
        self.witness: Point | None = None

    def verify_square(self, x0: float, y0: float, size: float, depth: int = 0) -> bool:
        self.max_depth = max(self.max_depth, depth)
        x1, y1 = x0 + size, y0 + size
        square = [Point(x0, y0), Point(x1, y0), Point(x1, y1), Point(x0, y1)]
        cell = clip_polygon(square, self.arena)
        if len(cell) < 3 or _polygon_area(cell) <= 1e-9:
            return True
        self.checked += 1
        if _cell_sufficient(
            cell,
            self.points,
            radius_m=self.radius_m,
            safety_margin_m=self.safety_margin_m,
            tol=self.tol,
        ):
            return True
        if size <= self.min_cell_m + 1e-9:
            self.uncertain += 1
            if self.witness is None:
                self.witness = _polygon_center(cell)
            return False
        half = size * 0.5
        return all(
            self.verify_square(x0 + dx, y0 + dy, half, depth + 1)
            for dx, dy in ((0.0, 0.0), (half, 0.0), (0.0, half), (half, half))
        )


def verify_continuous_local_hull(
    points: Sequence[Point],
    *,
    cell_m: float = 100.0,
    min_cell_m: float = 10.0,
    radius_m: float = MIN_RADIUS_M,
    arena_radius_m: float = ARENA_RADIUS_M,
    safety_margin_m: float = 1e-6,
) -> CertificateReport:
    """连续域局部凸包充分证书。

    返回 ``ok=False`` 只表示当前细分下无法证明，不一定代表真实几何不完备；
    外部应回退、继续细分或改变点集。
    """
    pts = list(points)
    if not pts:
        return CertificateReport(False, 0, 0, 0, 1, 0, Point(0.0, 0.0))
    cert = _Certifier(
        pts,
        cell_m=cell_m,
        min_cell_m=min_cell_m,
        radius_m=radius_m,
        arena_radius_m=arena_radius_m,
        safety_margin_m=safety_margin_m,
    )
    n_cells = max(1, int(math.ceil(2.0 * cert.extent / max(cell_m, 1e-9))))
    size = 2.0 * cert.extent / n_cells
    ok = True
    for ix in range(n_cells):
        x0 = -cert.extent + ix * size
        for iy in range(n_cells):
            y0 = -cert.extent + iy * size
            if not cert.verify_square(x0, y0, size):
                ok = False
    return CertificateReport(
        ok=ok,
        n_points=len(pts),
        root_cells=n_cells * n_cells,
        checked_cells=cert.checked,
        uncertain_cells=cert.uncertain,
        max_depth=cert.max_depth,
        witness=cert.witness,
    )


def sample_points(
    *,
    step_m: float = 120.0,
    arena_radius_m: float = ARENA_RADIUS_M,
    include_boundary: bool = True,
) -> list[Point]:
    """生成用于优化打分的确定性采样点。"""
    ext = arena_radius_m * 1.001
    n = max(1, int(math.ceil(2.0 * ext / max(step_m, 1e-9))))
    ds = 2.0 * ext / n
    out: list[Point] = []
    offset = 0.0
    for iy in range(n + 1):
        y = -ext + iy * ds
        for ix in range(n + 1):
            x = -ext + ix * ds + (0.5 * ds if iy % 2 else 0.0)
            p = Point(x, y)
            if p.norm() <= arena_radius_m + 1e-9:
                out.append(p)
    if include_boundary:
        # 极坐标边界加密，专门约束贴边源。
        for k in range(360):
            out.append(
                Point(
                    arena_radius_m * math.cos(math.radians(k)),
                    arena_radius_m * math.sin(math.radians(k)),
                )
            )
        out.append(Point(0.0, 0.0))
    # 去重
    seen: set[tuple[float, float]] = set()
    uniq: list[Point] = []
    for p in out:
        key = (round(p.x, 6), round(p.y, 6))
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def verify_per_channel_certificate(
    points_by_channel: dict[int, Sequence[Point]],
    *,
    cell_m: float = 100.0,
    min_cell_m: float = 10.0,
    radius_m: float = MIN_RADIUS_M,
    arena_radius_m: float = ARENA_RADIUS_M,
    safety_margin_m: float = 1e-6,
) -> dict[int, CertificateReport]:
    """逐频道证书：每个频道只把“实际测过该频道的停点”计入 ``P_c``。

    优化器当前以全局点集为变量；如果需要“某停点只测部分频道”，在调用方维护
    ``points_by_channel[c]`` 并用本函数独立复核即可。
    """
    return {
        channel: verify_continuous_local_hull(
            points,
            cell_m=cell_m,
            min_cell_m=min_cell_m,
            radius_m=radius_m,
            arena_radius_m=arena_radius_m,
            safety_margin_m=safety_margin_m,
        )
        for channel, points in points_by_channel.items()
    }


def sampled_certificate_ok(
    points: Sequence[Point],
    samples: Sequence[Point],
    *,
    radius_m: float = MIN_RADIUS_M,
    safety_margin_m: float = 1e-6,
) -> tuple[bool, Point | None]:
    """有限采样版检查；只用于优化器打分，不作为最终证明。"""
    effective_radius = radius_m - safety_margin_m
    for x in samples:
        near = [p for p in points if p.distance_to(x) <= effective_radius + 1e-9]
        if not point_in_convex_hull(x, near, tol=max(1e-7, safety_margin_m * 10.0)):
            return False, x
    return True, None


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
def route_length(points: Sequence[Point], order: Sequence[int], start: Point) -> float:
    if not order:
        return 0.0
    total = start.distance_to(points[order[0]])
    for i in range(len(order) - 1):
        total += points[order[i]].distance_to(points[order[i + 1]])
    return total


def nearest_neighbor_order(points: Sequence[Point], start: Point) -> list[int]:
    remaining = list(range(len(points)))
    order: list[int] = []
    cur = start
    while remaining:
        idx = min(remaining, key=lambda i: cur.distance_to(points[i]))
        remaining.remove(idx)
        order.append(idx)
        cur = points[idx]
    return order


def two_opt_open(points: Sequence[Point], order: Sequence[int], start: Point, max_rounds: int = 8) -> list[int]:
    """开放路径 2-opt，起点固定、终点自由。"""
    route = list(order)
    n = len(route)
    if n < 3:
        return route
    for _ in range(max_rounds):
        improved = False
        for i in range(n - 1):
            for j in range(i + 1, n):
                before = start if i == 0 else points[route[i - 1]]
                after = None if j + 1 >= n else points[route[j + 1]]
                old = before.distance_to(points[route[i]])
                new = before.distance_to(points[route[j]])
                if after is not None:
                    old += points[route[j]].distance_to(after)
                    new += points[route[i]].distance_to(after)
                if new < old - 1e-9:
                    route[i : j + 1] = reversed(route[i : j + 1])
                    improved = True
        if not improved:
            break
    return route


# ---------------------------------------------------------------------------
# 联合优化
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class JointLayoutParams:
    radius_m: float = MIN_RADIUS_M
    arena_radius_m: float = ARENA_RADIUS_M
    start: Point = field(default_factory=lambda: Point(0.0, 0.0))
    travel_speed_mps: float = 5.0
    #: 每个停点的检测代价（秒）。固定 m 时不影响坐标；删点时用于权衡。
    measure_cost_per_stop_s: float = 0.0
    #: 优化阶段采样步长与安全余量
    sample_step_m: float = 120.0
    safety_margin_m: float = 1e-6
    #: 连续证书细度
    certificate_cell_m: float = 100.0
    certificate_min_cell_m: float = 10.0
    #: 局部搜索
    max_iterations: int = 12
    move_steps_m: tuple[float, ...] = (200.0, 100.0, 50.0, 20.0, 10.0)
    random_seed: int = 0
    enable_deletion: bool = True
    enable_moves: bool = True
    #: True 时每个候选都要连续证书复核（完备性优先，但计算量大）；
    #: False 时仅在每轮末复核并回退。
    strict_accept: bool = True


@dataclass(slots=True)
class JointLayoutResult:
    points: list[Point]
    order: list[int]
    route_length_m: float
    objective: float
    certificate: CertificateReport
    sampled_ok: bool
    iterations: int
    history: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "n_points": len(self.points),
            "route_length_m": round(self.route_length_m, 3),
            "objective": round(self.objective, 6),
            "certificate": self.certificate.as_dict(),
            "sampled_ok": self.sampled_ok,
            "iterations": self.iterations,
            "points": [[round(p.x, 3), round(p.y, 3)] for p in self.points],
            "order": self.order,
        }


class JointLayoutOptimizer:
    """方法二原型：固定证书下联合优化点集和访问顺序。"""

    def __init__(self, params: JointLayoutParams | None = None) -> None:
        self.params = params or JointLayoutParams()
        self.samples = sample_points(step_m=self.params.sample_step_m, arena_radius_m=self.params.arena_radius_m)

    def objective(self, points: Sequence[Point], order: Sequence[int]) -> float:
        return (
            route_length(points, order, self.params.start) / self.params.travel_speed_mps
            + self.params.measure_cost_per_stop_s * len(points)
        )

    def _acceptable(self, points: Sequence[Point]) -> bool:
        ok, _ = sampled_certificate_ok(
            points,
            self.samples,
            radius_m=self.params.radius_m,
            safety_margin_m=self.params.safety_margin_m,
        )
        if not ok:
            return False
        if self.params.strict_accept:
            return self._continuous_ok(points).ok
        return True

    def _continuous_ok(self, points: Sequence[Point]) -> CertificateReport:
        return verify_continuous_local_hull(
            points,
            cell_m=self.params.certificate_cell_m,
            min_cell_m=self.params.certificate_min_cell_m,
            radius_m=self.params.radius_m,
            arena_radius_m=self.params.arena_radius_m,
            safety_margin_m=self.params.safety_margin_m,
        )

    def _best_order(self, points: Sequence[Point], order: Sequence[int]) -> list[int]:
        return two_opt_open(points, order, self.params.start)

    def optimize(
        self,
        initial_points: Sequence[Point] | None = None,
        initial_order: Sequence[int] | None = None,
    ) -> JointLayoutResult:
        p = self.params
        points = list(initial_points) if initial_points else list(heading_cover_layout(950.0))
        order = list(initial_order) if initial_order else nearest_neighbor_order(points, p.start)
        order = self._best_order(points, order)
        obj = self.objective(points, order)
        # 始终保存一份“已通过连续证书”的快照；搜索越界时回退到它。
        certified_points = list(points)
        certified_order = list(order)
        certified_obj = obj
        certified_cert = self._continuous_ok(points)
        history: list[dict] = []
        iterations = 0
        improved = True
        rng = random.Random(p.random_seed)

        while improved and iterations < p.max_iterations:
            iterations += 1
            improved = False
            # 1) 删除冗余点
            if p.enable_deletion:
                i = len(points) - 1
                while i >= 0:
                    if len(points) <= 3:
                        break
                    cand = points[:i] + points[i + 1 :]
                    if not self._acceptable(cand):
                        i -= 1
                        continue
                    # 删除第 i 个点后，把原顺序里的索引重新映射。
                    cand_order = [j for j in order if j != i]
                    idx_map = {old: new for new, old in enumerate(k for k in range(len(points)) if k != i)}
                    cand_order = [idx_map[j] for j in cand_order]
                    cand_order = self._best_order(cand, cand_order)
                    cand_obj = self.objective(cand, cand_order)
                    if cand_obj < obj - 1e-9:
                        points, order, obj = cand, cand_order, cand_obj
                        improved = True
                    i -= 1

            # 2) 坐标微调：每个点尝试朝邻居中点、质心和随机方向移动
            if p.enable_moves and points:
                cx = sum(q.x for q in points) / len(points)
                cy = sum(q.y for q in points) / len(points)
                centroid = Point(cx, cy)
                for i in range(len(points)):
                    base_order = order
                    route_pos = order.index(i)
                    dirs: list[tuple[float, float]] = [(centroid.x - points[i].x, centroid.y - points[i].y)]
                    if route_pos > 0:
                        dirs.append((points[order[route_pos - 1]].x - points[i].x, points[order[route_pos - 1]].y - points[i].y))
                    if route_pos + 1 < len(order):
                        dirs.append((points[order[route_pos + 1]].x - points[i].x, points[order[route_pos + 1]].y - points[i].y))
                    for _ in range(12):
                        ang = rng.uniform(0.0, 2.0 * math.pi)
                        dirs.append((math.cos(ang), math.sin(ang)))
                    best_cand: tuple[float, list[Point], list[int]] | None = None
                    for dx, dy in dirs:
                        norm = math.hypot(dx, dy)
                        if norm <= 1e-12:
                            continue
                        ux, uy = dx / norm, dy / norm
                        for step in p.move_steps_m:
                            q = Point(points[i].x + ux * step, points[i].y + uy * step)
                            if abs(q.x) > p.arena_radius_m * 1.5 or abs(q.y) > p.arena_radius_m * 1.5:
                                continue
                            cand = points[:i] + [q] + points[i + 1 :]
                            if not self._acceptable(cand):
                                continue
                            cand_order = self._best_order(cand, base_order)
                            cand_obj = self.objective(cand, cand_order)
                            if cand_obj < obj - 1e-9 and (best_cand is None or cand_obj < best_cand[0]):
                                best_cand = (cand_obj, cand, cand_order)
                    if best_cand is not None:
                        obj, points, order = best_cand
                        improved = True

            # 3) 固定点集，重新优化顺序
            new_order = self._best_order(points, order)
            new_obj = self.objective(points, new_order)
            if new_obj < obj - 1e-9:
                order, obj = new_order, new_obj
                improved = True

            # 每轮结束重新核对连续证书。通过则保存快照；失败则回退到上一份
            # 已证明可行的点集，避免局部搜索把证书弄坏。
            cert_now = self._continuous_ok(points)
            if not cert_now.ok:
                history.append(
                    {
                        "iteration": iterations,
                        "n_points": len(points),
                        "objective": obj,
                        "continuous_ok": False,
                        "fallback": "last-certified",
                    }
                )
                points, order, obj = certified_points, certified_order, certified_obj
                break
            if obj < certified_obj - 1e-9:
                certified_points, certified_order, certified_obj = list(points), list(order), obj
                certified_cert = cert_now
            history.append(
                {
                    "iteration": iterations,
                    "n_points": len(points),
                    "objective": obj,
                    "continuous_ok": True,
                }
            )

        # 返回已通过连续证书的最优点集。
        sampled_ok, _ = sampled_certificate_ok(
            certified_points,
            self.samples,
            radius_m=p.radius_m,
            safety_margin_m=p.safety_margin_m,
        )
        return JointLayoutResult(
            points=certified_points,
            order=certified_order,
            route_length_m=route_length(certified_points, certified_order, p.start),
            objective=certified_obj,
            certificate=certified_cert,
            sampled_ok=sampled_ok,
            iterations=iterations,
            history=history,
        )


# ---------------------------------------------------------------------------
# 可选 SOCP 精调（依赖 cvxpy）
# ---------------------------------------------------------------------------
def _barycentric_weights(
    x: Point,
    points: Sequence[Point],
    *,
    radius_m: float,
    safety_margin_m: float,
) -> dict[int, float] | None:
    """在 2D 中找一组近邻点，使 x 是它们的凸组合。

    最多枚举三元组；找到后返回 {index: weight}。没有则返回 None。
    """
    allowed = [i for i, p in enumerate(points) if p.distance_to(x) <= radius_m - safety_margin_m]
    if not allowed:
        return None
    # 单点 / 两点退化情况
    for i in allowed:
        if x.distance_to(points[i]) <= 1e-8:
            return {i: 1.0}
    for ai in range(len(allowed)):
        i = allowed[ai]
        for aj in range(ai + 1, len(allowed)):
            j = allowed[aj]
            a, b = points[i], points[j]
            vx, vy = b.x - a.x, b.y - a.y
            det = vx * vx + vy * vy
            if det <= 1e-12:
                continue
            t = ((x.x - a.x) * vx + (x.y - a.y) * vy) / det
            proj = Point(a.x + t * vx, a.y + t * vy)
            if proj.distance_to(x) <= 1e-7 and -1e-9 <= t <= 1 + 1e-9:
                return {i: 1.0 - t, j: t} if t > 1e-12 and t < 1 - 1e-12 else {i: 1.0}
    for ai in range(len(allowed)):
        i = allowed[ai]
        for aj in range(ai + 1, len(allowed)):
            j = allowed[aj]
            for ak in range(aj + 1, len(allowed)):
                k = allowed[ak]
                a, b, c = points[i], points[j], points[k]
                den = (b.y - c.y) * (a.x - c.x) + (c.x - b.x) * (a.y - c.y)
                if abs(den) <= 1e-12:
                    continue
                w1 = ((b.y - c.y) * (x.x - c.x) + (c.x - b.x) * (x.y - c.y)) / den
                w2 = ((c.y - a.y) * (x.x - c.x) + (a.x - c.x) * (x.y - c.y)) / den
                w3 = 1.0 - w1 - w2
                if min(w1, w2, w3) >= -1e-8:
                    weights = {i: max(0.0, w1), j: max(0.0, w2), k: max(0.0, w3)}
                    total = sum(weights.values())
                    if total > 0:
                        return {key: value / total for key, value in weights.items()}
    return None


def socp_refine(
    points: Sequence[Point],
    order: Sequence[int],
    *,
    samples: Sequence[Point] | None = None,
    radius_m: float = MIN_RADIUS_M,
    safety_margin_m: float = 1e-6,
    regularization: float = 1e-3,
    max_rounds: int = 3,
    solver: str | None = None,
) -> tuple[list[Point], list[int], str]:
    r"""可选 SOCP 精调：固定加权组合，微调坐标降低路程。

    这是方法二里“固定顺序用 SOCP 微调坐标”的接口。权重 :math:`\lambda_{ij}`
    由当前点集确定；每轮求解一个 SOCP，再用新点集重新求权重。需要 ``cvxpy``。
    """
    try:
        import cvxpy as cp  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("socp_refine 需要 `pip install cvxpy`") from exc

    import numpy as np  # noqa: PLC0415

    samples = list(samples or sample_points(step_m=120.0))
    pts = [Point(p.x, p.y) for p in points]
    ord_list = list(order)
    note = "socp"
    for _ in range(max_rounds):
        weights: list[dict[int, float]] = []
        for x in samples:
            w = _barycentric_weights(
                x, pts, radius_m=radius_m, safety_margin_m=safety_margin_m
            )
            if not w:
                # 当前采样点不满足局部凸包 -> 拒绝 SOCP 精调
                return pts, ord_list, "socp-skipped:barycentric-failed"
            weights.append(w)
        n = len(pts)
        q = cp.Variable((n, 2))
        constraints = [q <= 2500.0, q >= -2500.0]
        # 固定组合：每个样本 x 仍是同一组近邻的凸组合
        for x, w in zip(samples, weights):
            expr = 0
            for i, lam in w.items():
                expr = expr + lam * q[i, :]
                constraints.append(cp.norm(q[i, :] - np.array([x.x, x.y])) <= radius_m - safety_margin_m)
            constraints.append(expr == np.array([x.x, x.y]))
        route_cost = 0
        for a, b in zip(ord_list[:-1], ord_list[1:]):
            route_cost += cp.norm(q[a, :] - q[b, :], 2)
        reg = 0
        for i, p in enumerate(pts):
            reg += cp.norm(q[i, :] - np.array([p.x, p.y]), 2)
        problem = cp.Problem(cp.Minimize(route_cost + regularization * reg), constraints)
        try:
            problem.solve(solver=solver, verbose=False)
        except Exception as exc:  # noqa: BLE001
            return pts, ord_list, f"socp-failed:{type(exc).__name__}:{exc}"
        if problem.status not in ("optimal", "optimal_inaccurate") or q.value is None:
            return pts, ord_list, f"socp-status:{problem.status}"
        pts = [Point(float(q.value[i, 0]), float(q.value[i, 1])) for i in range(n)]
        ord_list = two_opt_open(pts, ord_list, Point(0.0, 0.0))
    return pts, ord_list, note


__all__ = [
    "CertificateReport",
    "JointLayoutOptimizer",
    "JointLayoutParams",
    "JointLayoutResult",
    "MIN_RADIUS_M",
    "convex_hull",
    "local_hull_holds_at",
    "point_in_convex_hull",
    "route_length",
    "sample_points",
    "sampled_certificate_ok",
    "socp_refine",
    "two_opt_open",
    "verify_continuous_local_hull",
    "verify_per_channel_certificate",
]
