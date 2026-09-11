#!/usr/bin/env python3
"""Zone geometry for Q2 when the effective radius rho is *not* a decision variable.

Model
-----
The effective reception radius rho of a jammer is a property of that jammer:
it is fixed, unknown, and (per B题 附录2.2) lies in [RHO_MIN, RHO_MAX] =
[1000, 1500] m.  It is *not* chosen by us, exactly like S1 and theta1.

Because one single rho governs both the P1 and the P2 measurement of the same
jammer, the P1 detection at range d1 = |G - S1| tells us

        rho >= max(RHO_MIN, d1).

That conditional lower bound is what makes the three zones below asymmetric:

* certain  (一定测到):  d2 <= max(d1, RHO_MIN)  for every possible G.
* blind    (一定测不到): d2 > RHO_MAX            for every possible G.
* else:     0 < Pr(detect at S2) < 1.

Possible source set after a successful P1 reading theta1 (half-width eps):

        A1 = W(S1, theta1, +-eps)  ∩  D(S1, RHO_MAX)  ∩  D(O, R_TARGET).

(the same set for a hypothetical radius r is A(r) with D(S1, r)).

Closed forms
------------
`certain_margin(S2) = max(|S2-S1|, |S2-S1-rho_min*e(theta1+eps)|,
                          |S2-S1-rho_min*e(theta1-eps)|) - rho_min`

is exact for the un-clipped sector; <= 0  <=>  S2 belongs to

        V = D(S1, rho_min) ∩ D(S1+rho_min e(theta1+eps), rho_min)
                           ∩ D(S1+rho_min e(theta1-eps), rho_min).

Proof sketch: split A1 at d1 = rho_min.
  * near part (d1 <= rho_min): the constraint is d2 <= rho_min; d2(.) is
    convex, so its maximum over the convex near sector is attained at one of
    the three extreme points S1, rho_min e(theta1+-eps).
  * far part (d1 >= rho_min): the constraint is d2 <= d1, which is the
    half-plane  (S2-S1)·G >= (S2-S1)·(S1+S2)/2  in G.  Over the annular
    sector the minimum of that linear form is attained on the inner arc
    r = rho_min, giving |S2| <= 2 rho_min cos(angle(S2-S1)+-eps), which is
    exactly the same two disks D(rho_min e(theta1+-eps), rho_min).
Hence both parts reduce to the same three disks.

Note the commonly quoted region built from the *far* corners
1500*e(theta1+-eps) is strictly smaller and therefore only sufficient, not
necessary; see docs/q2_second_point_strategy.md.

`blind_gap(S2) = dist(S2, A1) - RHO_MAX`  (> 0  <=>  certainly no signal).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "cpp") not in sys.path:
    sys.path.insert(0, str(ROOT / "cpp"))

import geom_cpp  # type: ignore  # noqa: E402

DEG2RAD = math.pi / 180.0
RAD2DEG = 180.0 / math.pi
R_TARGET = 1800.0
RHO_MIN = 1000.0
RHO_MAX = 1500.0
EPS_DEG = 1.0

MODEL_UNIFORM = "uniform"
MODEL_FIXED = "fixed"
MODEL_INTERVAL = "interval"
RHO_MODELS = (MODEL_UNIFORM, MODEL_FIXED, MODEL_INTERVAL)


def unit_deg(deg: float) -> tuple[float, float]:
    a = deg * DEG2RAD
    return math.cos(a), math.sin(a)


def wrap360(x: float) -> float:
    x = math.fmod(x, 360.0)
    return x + 360.0 if x < 0.0 else x


def add(p: tuple[float, float], q: tuple[float, float]) -> tuple[float, float]:
    return p[0] + q[0], p[1] + q[1]


def scaled(p: tuple[float, float], s: float) -> tuple[float, float]:
    return p[0] * s, p[1] * s


def ray_target_radius(s1: tuple[float, float], direction_deg: float,
                      r_target: float = R_TARGET) -> float:
    """Distance from S1 to the target-disk boundary along ``direction_deg``."""
    ux, uy = unit_deg(direction_deg)
    b = 2.0 * (s1[0] * ux + s1[1] * uy)
    c = s1[0] * s1[0] + s1[1] * s1[1] - r_target * r_target
    disc = b * b - 4.0 * c
    if disc <= 0.0:
        return 0.0
    return (-b + math.sqrt(disc)) / 2.0


# ---------------------------------------------------------------------------
# Possible source set A(rho)
# ---------------------------------------------------------------------------
def possible_set_polygon(
    s1: tuple[float, float],
    theta1_deg: float,
    rho: float,
    eps_deg: float = EPS_DEG,
    r_target: float = R_TARGET,
    bound_sides: int = 720,
    disk_sides: int = 720,
) -> np.ndarray:
    """Vertices (n,2) of A(rho) = wedge ∩ D(S1,rho) ∩ D(O,r_target).

    The target-disk polygon of the kernel is circumscribed, so the returned
    polygon is a slight superset of A(rho); distances to it are therefore
    slight underestimates.  With 720 sides the bias is below 1 cm.
    """
    obs = geom_cpp.Observation(
        geom_cpp.Vec2(s1[0], s1[1]), theta1_deg, eps_deg, rho
    )
    region = geom_cpp.build_region([obs], r_target, bound_sides)
    if region.is_empty():
        return np.zeros((0, 2), dtype=float)
    region = geom_cpp.clip_region_by_disk(
        region, geom_cpp.Vec2(s1[0], s1[1]), rho, disk_sides
    )
    if region.is_empty():
        return np.zeros((0, 2), dtype=float)
    return np.array([[v.x, v.y] for v in region.vertices], dtype=float)


def distance_to_polygon(points: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Vectorised point-to-convex-polygon distance (inside -> 0)."""
    pts = np.atleast_2d(np.asarray(points, dtype=float))
    if poly.shape[0] < 3:
        return np.full(pts.shape[0], np.inf)
    n = poly.shape[0]
    best = np.full(pts.shape[0], np.inf)
    outside = np.zeros(pts.shape[0], dtype=bool)
    for i in range(n):
        a = poly[i]
        b = poly[(i + 1) % n]
        e = b - a
        cross = e[0] * (pts[:, 1] - a[1]) - e[1] * (pts[:, 0] - a[0])
        # polygon is CCW -> strictly inside means cross > 0 for every edge
        outside |= cross <= 0.0
        denom = float(e[0] * e[0] + e[1] * e[1])
        if denom <= 0.0:
            continue
        t = ((pts[:, 0] - a[0]) * e[0] + (pts[:, 1] - a[1]) * e[1]) / denom
        t = np.clip(t, 0.0, 1.0)
        proj = np.column_stack([a[0] + t * e[0], a[1] + t * e[1]])
        d = np.hypot(pts[:, 0] - proj[:, 0], pts[:, 1] - proj[:, 1])
        best = np.minimum(best, d)
    best[~outside] = 0.0
    return best


def blind_gap(
    s1: tuple[float, float],
    theta1_deg: float,
    s2: tuple[float, float] | np.ndarray,
    rho_max: float = RHO_MAX,
    eps_deg: float = EPS_DEG,
    r_target: float = R_TARGET,
    poly: np.ndarray | None = None,
    sides: int = 720,
) -> np.ndarray:
    """dist(S2, A1) - rho_max;  > 0 means the second reading is certainly blank."""
    if poly is None:
        poly = possible_set_polygon(s1, theta1_deg, rho_max, eps_deg, r_target,
                                    bound_sides=sides, disk_sides=sides)
    pts = np.atleast_2d(np.asarray(s2, dtype=float))
    return distance_to_polygon(pts, poly) - rho_max


# ---------------------------------------------------------------------------
# Certain-detection region V
# ---------------------------------------------------------------------------
def certain_circle_centers(
    s1: tuple[float, float],
    theta1_deg: float,
    rho_min: float = RHO_MIN,
    eps_deg: float = EPS_DEG,
) -> list[tuple[float, float]]:
    return [
        s1,
        add(s1, scaled(unit_deg(theta1_deg + eps_deg), rho_min)),
        add(s1, scaled(unit_deg(theta1_deg - eps_deg), rho_min)),
    ]


def certain_margin(
    s1: tuple[float, float],
    theta1_deg: float,
    s2: tuple[float, float] | np.ndarray,
    rho_min: float = RHO_MIN,
    eps_deg: float = EPS_DEG,
) -> np.ndarray:
    """max_gap - rho_min;  <= 0  <=>  guaranteed detection for every possible G."""
    pts = np.atleast_2d(np.asarray(s2, dtype=float))
    worst = np.full(pts.shape[0], -np.inf)
    for cx, cy in certain_circle_centers(s1, theta1_deg, rho_min, eps_deg):
        d = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)
        worst = np.maximum(worst, d)
    return worst - rho_min


class ZoneModel:
    """Pre-computed geometry of the three zones for one (S1, theta1, rho) setup.

    * ``near_poly``  - polygon of A(rho_min) = wedge ∩ D(S1,rho_min) ∩ D(O,R)
    * ``outer_poly`` - polygon of A1       = wedge ∩ D(S1,rho_max) ∩ D(O,R)
    * ``phi``/``far_len`` - sampled far boundary length L(phi) of A1

    All quantities are vectorised over an array of candidate points S2.
    """

    def __init__(
        self,
        s1: tuple[float, float],
        theta1_deg: float,
        rho_min: float = RHO_MIN,
        rho_max: float = RHO_MAX,
        eps_deg: float = EPS_DEG,
        r_target: float = R_TARGET,
        sides: int = 720,
        n_phi: int = 257,
    ) -> None:
        self.s1 = (float(s1[0]), float(s1[1]))
        self.theta1_deg = float(theta1_deg)
        self.rho_min = float(rho_min)
        self.rho_max = float(rho_max)
        self.eps_deg = float(eps_deg)
        self.r_target = float(r_target)

        self.near_poly = possible_set_polygon(
            self.s1, self.theta1_deg, self.rho_min, self.eps_deg, self.r_target,
            bound_sides=sides, disk_sides=sides,
        )
        self.outer_poly = possible_set_polygon(
            self.s1, self.theta1_deg, self.rho_max, self.eps_deg, self.r_target,
            bound_sides=sides, disk_sides=sides,
        )
        phi = np.linspace(-self.eps_deg * DEG2RAD, self.eps_deg * DEG2RAD, n_phi)
        self.phi = phi
        self.far_len = np.array([
            min(self.rho_max,
                ray_target_radius(self.s1,
                                  self.theta1_deg + math.degrees(float(p)),
                                  self.r_target))
            for p in phi
        ])
        # Far part {G in A1 : d1 > rho_min}: the function
        #   h(r, phi) = |S2 - G| - |G - S1| = |S2 - G| - r
        # is strictly decreasing in r (dh/dr = (r - V.e)/d2 - 1 < 0), so the
        # maximum over the far part is attained on its inner arc r = rho_min.
        # The arc exists only where the outer boundary L(phi) exceeds rho_min.
        self.far_active = self.far_len > self.rho_min + 1e-9
        rot = self.theta1_deg * DEG2RAD
        self.arc_pts = np.column_stack([
            self.s1[0] + self.rho_min * np.cos(rot + phi[self.far_active]),
            self.s1[1] + self.rho_min * np.sin(rot + phi[self.far_active]),
        ])

    # -- zones ---------------------------------------------------------
    def certain_margin(
        self, s2: tuple[float, float] | np.ndarray
    ) -> np.ndarray:
        """Exact max over G in A1 of [ d2(G) - max(d1(G), rho_min) ].

        ``<= 0`` means the second reading succeeds for *every* admissible
        source and *every* admissible rho (the closed-form three-disk region
        of `certain_margin` is recovered whenever the target disk does not cut
        the rho_min sector; otherwise this version is exact and tighter).

        Near part (d1 <= rho_min, constraint d2 <= rho_min): d2 is convex on
        the convex polygon A(rho_min), so its maximum is at a polygon vertex.
        Far part (d1 > rho_min, constraint d2 <= d1): the maximum of
        d2 - d1 is on the inner arc r = rho_min, as noted above.
        """
        pts = np.atleast_2d(np.asarray(s2, dtype=float))
        n = pts.shape[0]
        out = np.full(n, -np.inf)

        worst = np.full(n, -np.inf)
        for vx, vy in self.near_poly:
            d = np.hypot(pts[:, 0] - vx, pts[:, 1] - vy)
            worst = np.maximum(worst, d)
        if self.near_poly.shape[0]:
            out = np.maximum(out, worst - self.rho_min)

        if self.arc_pts.shape[0]:
            worst = np.full(n, -np.inf)
            for vx, vy in self.arc_pts:
                d = np.hypot(pts[:, 0] - vx, pts[:, 1] - vy)
                worst = np.maximum(worst, d)
            out = np.maximum(out, worst - self.rho_min)
        return out

    def blind_gap(self, s2: tuple[float, float] | np.ndarray) -> np.ndarray:
        pts = np.atleast_2d(np.asarray(s2, dtype=float))
        return distance_to_polygon(pts, self.outer_poly) - self.rho_max

    def classify(self, s2: tuple[float, float] | np.ndarray,
                 tol: float = 1e-9) -> list[str]:
        pts = np.atleast_2d(np.asarray(s2, dtype=float))
        margins = self.certain_margin(pts)
        gaps = self.blind_gap(pts)
        return [
            "certain" if m <= tol else ("blind" if g > tol else "probabilistic")
            for m, g in zip(margins, gaps)
        ]


def detection_probability(
    s1: tuple[float, float],
    theta1_deg: float,
    s2: tuple[float, float],
    model: str = MODEL_UNIFORM,
    rho_min: float = RHO_MIN,
    rho_max: float = RHO_MAX,
    rho_fixed: float = RHO_MAX,
    eps_deg: float = EPS_DEG,
    r_target: float = R_TARGET,
    n_phi: int = 33,
    n_sub: int = 8,
) -> float:
    """Kink-aware quadrature of Pr(successful second reading | P1 reading).

        p_det = ∫ k12 dG / ∫ k1 dG,
        k1  = w(max(rho_min, d1)),  k12 = w(max(rho_min, d1, d2)),

    with w(d) = (rho_max - d)_+ for the uniform model, 1{d <= rho_fixed} for
    the fixed model and 1{d <= rho_max} for the interval-only model.

    The radial integral is split at every kink of the integrand: r = rho_min
    and the (up to four) intersections of the ray with the circles of radius
    rho_min / rho_max centred at S2.  Without those splits the plain
    Gauss-Legendre rule in this project overestimated p_det by ~1e-3.
    """
    xp, wp = np.polynomial.legendre.leggauss(n_phi)
    phis = eps_deg * DEG2RAD * xp
    wphis = eps_deg * DEG2RAD * wp
    xg, wg = np.polynomial.legendre.leggauss(n_sub)
    if model == MODEL_FIXED:
        def wfun(d: float) -> float:
            return 1.0 if d <= rho_fixed + 1e-9 else 0.0
    elif model == MODEL_INTERVAL:
        def wfun(d: float) -> float:
            return 1.0 if d <= rho_max + 1e-9 else 0.0
    elif model == MODEL_UNIFORM:
        def wfun(d: float) -> float:
            return max(0.0, rho_max - max(rho_min, d))
    else:
        raise ValueError(f"unknown rho model: {model!r}")
    w = (s2[0] - s1[0], s2[1] - s1[1])
    w2 = w[0] * w[0] + w[1] * w[1]
    num = 0.0
    den = 0.0
    for phi, wphi in zip(phis, wphis):
        ang = theta1_deg * DEG2RAD + float(phi)
        ux, uy = math.cos(ang), math.sin(ang)
        length = min(rho_max, ray_target_radius(s1, math.degrees(ang), r_target))
        if length <= 1e-12:
            continue
        proj = ux * w[0] + uy * w[1]
        cuts = {0.0, length, rho_min}
        for radius in {rho_min, rho_max}:
            disc = proj * proj - w2 + radius * radius
            if disc > 0.0:
                root = math.sqrt(disc)
                cuts.add(proj - root)
                cuts.add(proj + root)
        edges = sorted(c for c in cuts if -1e-9 <= c <= length + 1e-9)
        for lo, hi in zip(edges[:-1], edges[1:]):
            lo = max(0.0, lo)
            hi = min(length, hi)
            if hi - lo <= 1e-12:
                continue
            rr = 0.5 * (lo + hi) + 0.5 * (hi - lo) * xg
            wr = 0.5 * (hi - lo) * wg
            for r, wrr in zip(rr, wr):
                rr_ = float(r)
                gx = s1[0] + rr_ * ux
                gy = s1[1] + rr_ * uy
                d2 = math.hypot(gx - s2[0], gy - s2[1])
                k1 = wfun(rr_)
                k12 = wfun(rr_ if rr_ >= d2 else d2)
                wgt = float(wphi) * float(wrr) * rr_
                den += k1 * wgt
                num += k12 * wgt
    if den <= 0.0:
        return float("nan")
    return num / den


def disks_intersection_polygon(
    centers: list[tuple[float, float]], radius: float, samples: int = 720
) -> np.ndarray:
    """Boundary polyline of ∩ D(center, radius) by ray casting (plot overlay).

    Only disks of the *same* radius are supported, which is all the certain
    zone needs.  The intersection of D(c, r) over a centre set C equals the
    intersection over the extreme points of conv(C), so a handful of centres
    already determines the region exactly.
    """
    if not centers:
        return np.zeros((0, 2), dtype=float)
    c = np.asarray(centers, dtype=float)
    if c.shape[0] == 1:
        a = np.linspace(0.0, 2.0 * math.pi, samples, endpoint=False)
        return np.column_stack([c[0, 0] + radius * np.cos(a),
                                c[0, 1] + radius * np.sin(a)])

    # starting interior point: centroid, then alternating projection
    p = c.mean(axis=0)
    for _ in range(64):
        moved = False
        for ci in c:
            d = p - ci
            n = float(np.hypot(d[0], d[1]))
            if n > radius:
                p = ci + d * (radius / n)
                moved = True
        if not moved:
            break
    if np.any(np.hypot(p[0] - c[:, 0], p[1] - c[:, 1]) > radius + 1e-6):
        return np.zeros((0, 2), dtype=float)

    # ray cast: boundary distance along each direction
    ang = np.linspace(0.0, 2.0 * math.pi, samples, endpoint=False)
    dirs = np.column_stack([np.cos(ang), np.sin(ang)])
    t = np.full(samples, np.inf)
    for ci in c:
        f = p - ci
        b = dirs @ f
        cc = float(f @ f) - radius * radius
        disc = b * b - cc
        ok = disc >= 0.0
        root = np.sqrt(np.where(ok, disc, 0.0))
        cand = np.where(ok, -b + root, np.inf)
        cand = np.where(cand > 1e-9, cand, np.inf)
        t = np.minimum(t, cand)
    if not np.isfinite(t).all():
        return np.zeros((0, 2), dtype=float)
    pts = p[None, :] + dirs * t[:, None]
    return np.vstack([pts, pts[:1]])


# ---------------------------------------------------------------------------
# rho model: detection weights
# ---------------------------------------------------------------------------
def detection_weight(
    d: np.ndarray | float,
    model: str = MODEL_UNIFORM,
    rho_min: float = RHO_MIN,
    rho_max: float = RHO_MAX,
    rho_fixed: float = RHO_MAX,
) -> np.ndarray:
    """Unnormalised likelihood of a successful reading at required range ``d``.

    ``d`` is max(d1, d2) for the joint event, d1 for the P1 event alone.
    """
    dd = np.asarray(d, dtype=float)
    if model == MODEL_FIXED:
        return (dd <= rho_fixed + 1e-9).astype(float)
    if model == MODEL_INTERVAL:
        # only the interval is known: no distribution, detection is a 0/1 event
        return (dd <= rho_max + 1e-9).astype(float)
    if model != MODEL_UNIFORM:
        raise ValueError(f"unknown rho model: {model!r}")
    return np.maximum(0.0, rho_max - np.maximum(rho_min, dd))


def classify(
    s2: tuple[float, float],
    certain_margin_value: float,
    blind_gap_value: float,
    tol: float = 1e-9,
    gap_tol: float = 1e-6,
) -> str:
    """Zone label of a candidate S2.

    ``certain``      margin <= 0 : detection succeeds for every admissible
                     (G, rho), so p_det = 1.
    ``blind``        dist(S2, A1) >= rho_max : the set of sources that could be
                     heard is empty or a measure-zero tangency, so p_det = 0 and
                     the second measurement carries no information.  ``gap_tol``
                     only absorbs floating-point noise on that tangency.
    ``probabilistic`` otherwise: 0 < p_det < 1.
    """
    if certain_margin_value <= tol:
        return "certain"
    if blind_gap_value > -gap_tol:
        return "blind"
    return "probabilistic"
