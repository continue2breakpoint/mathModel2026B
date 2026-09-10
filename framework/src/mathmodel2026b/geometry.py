"""几何与集合估计工具。

对应问题1（交会定位区域及其直径）与问题3（可行域收缩、最小包围圆清除判据）。

核心对象
--------
``Point``
    平面点。
``bearing_wedge`` / ``clip_by_bearing``
    一个示向度读数只说明"真实方位角与读数之差在 ±1° 内"，因此单次读数给出的是
    一个以检测点为顶点、角宽 2° 的扇形（两条射线之间的无界区域）。
``feasible_region``
    多个扇形与目标区域（半径 1800m 圆）求交，得到必定包含真实位置的凸多边形。
    这是**保守**估计：区间外一定不含真值，区间内不一定。
``minimum_enclosing_circle``
    可行域的最小包围圆。瞄准圆心执行 ``/clear`` 时，只要包围圆半径不超过
    光学作用距离（20m），就构成确定性清除判据。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, hypot, pi, radians, sin, sqrt, tan
from random import Random
from typing import Iterable, Sequence

from .protocol import (
    ARENA_RADIUS_M,
    BEARING_ERROR_DEG,
    MAX_EFFECTIVE_RADIUS_M,
    OPTICAL_RANGE_M,
)

#: 用正 n 边形近似圆。取**外接**多边形（顶点在外），保证
#: "可行域一定包含真值"这一安全性不被近似破坏；n 足够大时误差可忽略。
#: 96 边时外接多边形相对圆的最大外扩约 1800*(sec(pi/96)-1) ≈ 0.97m。
_ARENA_POLYGON_SIDES = 96


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float

    def distance_to(self, other: "Point") -> float:
        return hypot(self.x - other.x, self.y - other.y)

    def moved_towards(self, other: "Point", distance: float) -> "Point":
        d = self.distance_to(other)
        if d == 0.0 or distance <= 0.0:
            return self
        t = min(1.0, distance / d)
        return Point(self.x + (other.x - self.x) * t, self.y + (other.y - self.y) * t)

    def bearing_to(self, other: "Point") -> float:
        """本点指向 ``other`` 的方位角，单位度，范围 [0, 360)。"""
        return degrees_atan2(other.y - self.y, other.x - self.x)

    def norm(self) -> float:
        return hypot(self.x, self.y)

    def is_finite(self) -> bool:
        return self.x == self.x and self.y == self.y and abs(self.x) < 1e9 and abs(self.y) < 1e9


def degrees_atan2(dy: float, dx: float) -> float:
    from math import atan2, degrees

    return degrees(atan2(dy, dx)) % 360.0


def from_polar(radius: float, angle_deg: float) -> Point:
    a = radians(angle_deg)
    return Point(radius * cos(a), radius * sin(a))


def unit_from_deg(angle_deg: float) -> Point:
    angle = radians(angle_deg)
    return Point(cos(angle), sin(angle))


def angle_difference_deg(a: float, b: float) -> float:
    """两个方位角之间的最小夹角，范围 [0, 180]。"""
    d = abs((a - b) % 360.0)
    return d if d <= 180.0 else 360.0 - d


# ---------------------------------------------------------------------------
# 半平面裁剪与凸多边形
# ---------------------------------------------------------------------------
def _clip_half_plane(
    poly: Sequence[Point], a: float, b: float, c: float, eps: float = 1e-9
) -> list[Point]:
    """保留满足 ``a*x + b*y + c >= 0`` 的部分（Sutherland–Hodgman）。"""
    if not poly:
        return []
    out: list[Point] = []
    n = len(poly)
    for i in range(n):
        cur = poly[i]
        nxt = poly[(i + 1) % n]
        d_cur = a * cur.x + b * cur.y + c
        d_nxt = a * nxt.x + b * nxt.y + c
        if d_cur >= -eps:
            out.append(cur)
        if (d_cur > eps and d_nxt < -eps) or (d_cur < -eps and d_nxt > eps):
            t = d_cur / (d_cur - d_nxt)
            out.append(
                Point(cur.x + (nxt.x - cur.x) * t, cur.y + (nxt.y - cur.y) * t)
            )
    return out


def arena_polygon(radius: float = ARENA_RADIUS_M, sides: int = _ARENA_POLYGON_SIDES) -> list[Point]:
    """目标区域的外接正多边形（包含半径 ``radius`` 的整个圆）。"""
    r = radius / cos(pi / sides)
    return [from_polar(r, 360.0 * k / sides) for k in range(sides)]


def clip_by_bearing(
    poly: Sequence[Point],
    apex: Point,
    bearing_deg: float,
    half_width_deg: float = BEARING_ERROR_DEG,
) -> list[Point]:
    """用一个 ±``half_width_deg`` 的示向度扇形裁剪多边形。"""
    left = unit_from_deg(bearing_deg + half_width_deg)
    right = unit_from_deg(bearing_deg - half_width_deg)
    # cross(right, Q - apex) >= 0
    poly = _clip_half_plane(
        poly, -right.y, right.x, right.y * apex.x - right.x * apex.y
    )
    # cross(left, Q - apex) <= 0
    poly = _clip_half_plane(
        poly, left.y, -left.x, left.x * apex.y - left.y * apex.x
    )
    return poly


def circle_polygon(
    center: Point, radius: float, sides: int = _ARENA_POLYGON_SIDES
) -> list[Point]:
    """圆的外接正多边形（包含整个圆，故裁剪结果仍是安全的上界）。"""
    r = radius / cos(pi / sides)
    return [
        Point(center.x + r * cos(2 * pi * k / sides), center.y + r * sin(2 * pi * k / sides))
        for k in range(sides)
    ]


def clip_by_circle(
    poly: Sequence[Point],
    center: Point,
    radius: float,
    sides: int = _ARENA_POLYGON_SIDES,
) -> list[Point]:
    """把多边形裁剪到"以 ``center`` 为心、``radius`` 为半径的圆内"。

    用途：一次 ``direction`` 读数意味着目标距离该检测点不超过其有效接收半径
    （<=1500m）。这是一个**凸**约束，能把仅靠两条射线交会得到的无界/细长区域
    收成一个有界区域，是可行域收缩里最划算的一步。
    """
    by_radius = radius / cos(pi / sides)
    out = list(poly)
    # 逐边半平面裁剪：把外接正多边形的每条边作为 a*x+b*y+c>=0
    for k in range(sides):
        a1 = 2 * pi * k / sides
        a2 = 2 * pi * (k + 1) / sides
        v1 = Point(center.x + by_radius * cos(a1), center.y + by_radius * sin(a1))
        v2 = Point(center.x + by_radius * cos(a2), center.y + by_radius * sin(a2))
        # 内法线方向（指向圆心）
        nx = center.x - (v1.x + v2.x) / 2.0
        ny = center.y - (v1.y + v2.y) / 2.0
        norm = hypot(nx, ny)
        if norm < 1e-12:
            continue
        nx, ny = nx / norm, ny / norm
        # 过 v1，法线 n，内部满足 n·(Q - v1) >= 0
        c = -(nx * v1.x + ny * v1.y)
        out = _clip_half_plane(out, nx, ny, c)
        if len(out) < 3:
            return []
    return out


def feasible_region(
    readings: Iterable[tuple[Point, float]],
    radius: float = ARENA_RADIUS_M,
    half_width_deg: float = BEARING_ERROR_DEG,
    max_range_m: float | None = MAX_EFFECTIVE_RADIUS_M,
    sides: int = _ARENA_POLYGON_SIDES,
) -> list[Point]:
    """多次示向度读数（检测点, 示向度）与目标区域求交，得到保守可行域。

    ``max_range_m`` 为已知的有效接收半径上界（附录2-2 的 1500m）。
    传入 ``None`` 可关闭该约束，便于对照实验。
    """
    poly = arena_polygon(radius, sides)
    for apex, bearing in readings:
        poly = clip_by_bearing(poly, apex, bearing, half_width_deg)
        if len(poly) < 3:
            return []
        if max_range_m is not None:
            poly = clip_by_circle(poly, apex, max_range_m, sides)
            if len(poly) < 3:
                return []
    return poly



# ---------------------------------------------------------------------------
# 直径 / 面积 / 最小包围圆
# ---------------------------------------------------------------------------
def polygon_diameter(poly: Sequence[Point]) -> float:
    """多边形的直径（区域内任意两点距离最大值）。凸多边形时即顶点对最大值。"""
    best = 0.0
    n = len(poly)
    for i in range(n):
        for j in range(i + 1, n):
            d = poly[i].distance_to(poly[j])
            if d > best:
                best = d
    return best


def polygon_area(poly: Sequence[Point]) -> float:
    n = len(poly)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        cur, nxt = poly[i], poly[(i + 1) % n]
        s += cur.x * nxt.y - nxt.x * cur.y
    return abs(s) / 2.0


def polygon_centroid(poly: Sequence[Point]) -> Point | None:
    n = len(poly)
    if n == 0:
        return None
    if n < 3:
        return Point(
            sum(p.x for p in poly) / n, sum(p.y for p in poly) / n
        )
    a2 = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(n):
        cur, nxt = poly[i], poly[(i + 1) % n]
        cross = cur.x * nxt.y - nxt.x * cur.y
        a2 += cross
        cx += (cur.x + nxt.x) * cross
        cy += (cur.y + nxt.y) * cross
    if abs(a2) < 1e-12:
        return Point(sum(p.x for p in poly) / n, sum(p.y for p in poly) / n)
    return Point(cx / (3.0 * a2), cy / (3.0 * a2))


@dataclass(frozen=True, slots=True)
class Circle:
    center: Point
    radius: float


def _circle_from_two(a: Point, b: Point) -> Circle:
    c = Point((a.x + b.x) / 2.0, (a.y + b.y) / 2.0)
    return Circle(c, c.distance_to(a))


def _circle_from_three(a: Point, b: Point, c: Point) -> Circle | None:
    ax, ay, bx, by, cx, cy = a.x, a.y, b.x, b.y, c.x, c.y
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    ux = (
        (ax * ax + ay * ay) * (by - cy)
        + (bx * bx + by * by) * (cy - ay)
        + (cx * cx + cy * cy) * (ay - by)
    ) / d
    uy = (
        (ax * ax + ay * ay) * (cx - bx)
        + (bx * bx + by * by) * (ax - cx)
        + (cx * cx + cy * cy) * (bx - ax)
    ) / d
    center = Point(ux, uy)
    return Circle(center, center.distance_to(a))


def _in_circle(circle: Circle, p: Point, eps: float = 1e-9) -> bool:
    return circle.center.distance_to(p) <= circle.radius + eps


def minimum_enclosing_circle(points: Sequence[Point]) -> Circle | None:
    """Welzl 最小包围圆（随机化增量，确定性种子）。"""
    pts = list(dict.fromkeys(points))
    if not pts:
        return None
    if len(pts) == 1:
        return Circle(pts[0], 0.0)

    rnd = Random(20260913)
    rnd.shuffle(pts)

    circle: Circle | None = None
    for i, p in enumerate(pts):
        if circle is not None and _in_circle(circle, p):
            continue
        circle = Circle(p, 0.0)
        for j in range(i):
            q = pts[j]
            if _in_circle(circle, q):
                continue
            circle = _circle_from_two(p, q)
            for k in range(j):
                r = pts[k]
                if _in_circle(circle, r):
                    continue
                cand = _circle_from_three(p, q, r)
                if cand is not None:
                    circle = cand
    return circle


def region_clear_circle(
    poly: Sequence[Point], tolerance_m: float = OPTICAL_RANGE_M
) -> Circle | None:
    """若可行域小到可以确定性清除，返回"瞄准圆"（圆心 + 半径）。

    判据：最小包围圆半径 <= 光学作用距离。此时瞄准圆心移动，
    到真实位置的距离 <= 半径 <= 20m，``/clear`` 必定成功。
    """
    circle = minimum_enclosing_circle(poly)
    if circle is None:
        return None
    if circle.radius <= tolerance_m:
        return circle
    return None


# ---------------------------------------------------------------------------
# 覆盖扫描（问题3 第一阶段的确定性搜索）
# ---------------------------------------------------------------------------
def regular_hexagon_scan_points(radius: float = 1400.0) -> list[Point]:
    """圆心 + 半径 ``radius`` 正六边形六顶点，共 7 个点。"""
    points = [Point(0.0, 0.0)]
    for k in range(6):
        points.append(from_polar(radius, 60.0 * k))
    return points


def covering_radius(points: Sequence[Point], sample_rings: int = 400) -> float:
    """数值估计：目标区域内任一点到最近扫描点的最大距离。

    极角扫描 + 逐环二分。只用于离线校验扫描方案是否满足
    "覆盖半径 <= 干扰源最小有效接收半径(1000m)"。
    """
    worst = 0.0
    for ring in range(1, sample_rings + 1):
        r = ARENA_RADIUS_M * ring / sample_rings
        for a in range(0, 3600):
            p = from_polar(r, a / 10.0)
            best = min(p.distance_to(q) for q in points)
            if best > worst:
                worst = best
    return worst


def q3_seven_point_cover_valid(radius: float) -> bool:
    """解析判定：7 点六边形方案在该半径下能否覆盖整个目标区域。"""
    lower = 900.0 * sqrt(3.0) - 100.0 * sqrt(19.0)
    upper = 1000.0 * sqrt(3.0)
    return lower <= radius <= upper


def max_first_bearing_range(radius: float = ARENA_RADIUS_M) -> float:
    """从原点出发，能覆盖整个目标区域所需的最小有效接收半径。"""
    return radius


def second_point_offsets(
    first: Point, bearing_deg: float, distance: float
) -> list[Point]:
    """问题2：在过 ``first``、垂直于示向度的方向上取第二检测点候选。"""
    perp = bearing_deg + 90.0
    back = bearing_deg + 180.0
    return [
        Point(first.x + distance * cos(radians(perp)),
              first.y + distance * sin(radians(perp))),
        Point(first.x + distance * cos(radians(back)),
              first.y + distance * sin(radians(back))),
        Point(first.x + distance * cos(radians(perp + 180.0)),
              first.y + distance * sin(radians(perp + 180.0))),
    ]


def lateral_uncertainty(distance_m: float, half_width_deg: float = BEARING_ERROR_DEG) -> float:
    """距离 ``distance_m`` 处、±``half_width_deg`` 角度误差带来的横向不确定度。"""
    return distance_m * tan(radians(half_width_deg))
