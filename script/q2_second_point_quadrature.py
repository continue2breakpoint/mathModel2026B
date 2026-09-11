#!/usr/bin/env python3
"""Deterministic quadrature study for Q2: choose the second observation point.

Model
-----
* The first point is S1=(0,0) in local coordinates and the measured first
  bearing is 0 deg.  This is the recommended choice when S1 can be planned
  before the source is localized: the centre of the target disk maximises
  the first-detection probability for a uniformly distributed source.
* The source effective radius is rho ~ Uniform[1000, 1500] m.  A successful
  first detection at distance d1 therefore gives the radial posterior weight

        K1(d1) = (1500 - max(1000, d1))_+ .

* For a candidate second point S2=(a,b), let d2(G) be the distance from G to
  S2.  Conditional on a successful second detection, the joint radial weight
  is

        K12 = (1500 - max(1000, d1, d2))_+ .

* The second measured bearing is theta2 = beta2(G) + delta2, where
  beta2(G) is the true bearing from S2 to G and delta2 ~ Uniform[-1,1] deg.
  The feasible region after the two observations is mathematically the
  intersection of the target disk, the two detection disks of radius 1500 m,
  and the two 2-degree wedges.  For the recommended first point at the disk
  centre, the target disk is redundant.  For points in the recommended
  candidate region, the second 1500 m detection disk is also non-binding;
  the code therefore clips by the first 1500 m disk and the two wedges.  If
  the function is called far outside that candidate region, add a second
  disk clip.

The code integrates exactly over (r, phi, delta2) using Gauss-Legendre
quadrature and evaluates the convex-polygon metrics (area, diameter, minimum
enclosing circle) with the C++ geometry kernel.

Outputs
-------
For every candidate point it prints
    p_det, E[area], E[Rmin], sd[Rmin], P(Rmin <= 20 m), E[N20_lower].
Here N20_lower is a *valid lower bound* for the number of radius-20 m disks
needed to cover the feasible region.  The exact minimum covering number is a
separate continuous covering problem and is intentionally not solved here.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cpp"))

try:
    import geom_cpp  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Cannot import geom_cpp. Build it first with:\n"
        "    ./cpp/build.sh"
    ) from exc


DEG2RAD = math.pi / 180.0
TARGET_RADIUS = 1800.0
RHO_MIN = 1000.0
RHO_MAX = 1500.0
HALF_WIDTH_DEG = 1.0
HALF_WIDTH_RAD = HALF_WIDTH_DEG * DEG2RAD
CLEAR_RADIUS = 20.0
POSTERIOR_MEAN_R = 855.2631578944586


@dataclass
class QuadGrid:
    """Tensor Gauss-Legendre nodes for the integral over G=(r,phi)."""

    nodes: list[tuple[float, float, float, float, float]]
    # (r, phi, cos(phi), sin(phi), r*dr*dphi)
    delta_nodes: np.ndarray
    delta_weights: np.ndarray
    first_success_norm: float


def _gauss_legendre(n: int) -> tuple[np.ndarray, np.ndarray]:
    return np.polynomial.legendre.leggauss(n)


def make_grid(
    n_r_low: int = 16,
    n_r_high: int = 16,
    n_phi: int = 12,
    n_delta: int = 7,
) -> QuadGrid:
    """Build quadrature nodes.

    The r-integral is split at r=1000 because K1 has a kink there.  The phi
    and delta integrals use the natural interval [-1 deg, 1 deg].
    """
    # r in [0, 1000]
    x1, w1 = _gauss_legendre(n_r_low)
    r1 = 500.0 * (x1 + 1.0)
    dr1 = 500.0 * w1

    # r in [1000, 1500]
    x2, w2 = _gauss_legendre(n_r_high)
    r2 = 1250.0 + 250.0 * x2
    dr2 = 250.0 * w2

    r = np.concatenate([r1, r2])
    dr = np.concatenate([dr1, dr2])
    k1 = np.maximum(0.0, RHO_MAX - np.maximum(RHO_MIN, r))
    r_weight = r * dr

    xp, wp = _gauss_legendre(n_phi)
    phi = HALF_WIDTH_RAD * xp
    dphi = HALF_WIDTH_RAD * wp

    xd, wd = _gauss_legendre(n_delta)
    delta = HALF_WIDTH_DEG * xd
    delta_weight = HALF_WIDTH_DEG * wd

    nodes: list[tuple[float, float, float, float, float]] = []
    for i in range(r.size):
        for j in range(phi.size):
            w = float(r_weight[i] * dphi[j])
            nodes.append((float(r[i]), float(phi[j]), float(math.cos(phi[j])), float(math.sin(phi[j])), w))

    first_norm = 0.0
    idx = 0
    for i in range(r.size):
        for _ in range(phi.size):
            first_norm += nodes[idx][4] * float(k1[i])
            idx += 1

    return QuadGrid(nodes, delta, delta_weight, first_norm)


def distance(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


def region_metrics(
    second_point: "geom_cpp.Vec2",
    true_bearing_deg: float,
    delta_deg: float,
    bound_sides: int,
) -> tuple[float, float, float] | None:
    """Return (area, diameter, minimum-enclosing-circle radius) of R."""
    # D(S1,1500) is represented by the bound_radius polygon in
    # build_region.  For the recommended candidate points D(S2,1500) is
    # non-binding; outside that region a second disk clip must be added.
    obs1 = geom_cpp.Observation(
        geom_cpp.Vec2(0.0, 0.0), 0.0, HALF_WIDTH_DEG, RHO_MAX
    )
    obs2 = geom_cpp.Observation(
        second_point,
        true_bearing_deg + delta_deg,
        HALF_WIDTH_DEG,
        RHO_MAX,
    )
    region = geom_cpp.build_region([obs1, obs2], RHO_MAX, bound_sides)
    if region.is_empty():
        return None

    verts = region.vertices
    n = len(verts)
    area2 = 0.0
    for i in range(n):
        p = verts[i]
        q = verts[(i + 1) % n]
        area2 += p.x * q.y - q.x * p.y
    area = 0.5 * abs(area2)

    diameter = geom_cpp.rotating_calipers_diameter(region).distance
    circle = geom_cpp.brute_force_min_enclosing_circle(region)
    return float(area), float(diameter), float(circle.radius)


def covering_lower_bound(area: float, diameter: float, r_min: float) -> int:
    """A rigorous lower bound for covering the region by radius-20 disks."""
    if r_min <= CLEAR_RADIUS + 1e-9:
        return 1
    from_area = area / (math.pi * CLEAR_RADIUS * CLEAR_RADIUS)
    from_diameter = diameter / (2.0 * CLEAR_RADIUS)
    return max(2, int(math.ceil(max(from_area, from_diameter) - 1e-9)))


def guaranteed_detection_region(a: float, b: float, r_min: float = RHO_MIN) -> bool:
    """Whether S2=(a,b) guarantees d2<=rho_min for every possible G.

    The possible G-set is the 1500 m long 2-degree sector.  Its extreme
    points are the apex (0,0), (1500 cos eps, 1500 sin eps), and
    (1500 cos eps, -1500 sin eps).  Intersecting the three disks of radius
    r_min around these points gives the guarantee set.
    """
    c = math.cos(HALF_WIDTH_RAD)
    s = math.sin(HALF_WIDTH_RAD)
    g_plus = (RHO_MAX * c, RHO_MAX * s)
    g_minus = (RHO_MAX * c, -RHO_MAX * s)
    tests = [
        a * a + b * b,
        (a - g_plus[0]) ** 2 + (b - g_plus[1]) ** 2,
        (a - g_minus[0]) ** 2 + (b - g_minus[1]) ** 2,
    ]
    return all(t <= r_min * r_min + 1e-9 for t in tests)


def angle_condition(a: float, b: float, r_star: float = POSTERIOR_MEAN_R) -> bool:
    """Keep roughly orthogonal intersection angles: 30 deg <= gamma <= 150 deg."""
    return abs(a - r_star) <= math.sqrt(3.0) * abs(b)


def evaluate_point(
    a: float,
    b: float,
    grid: QuadGrid,
    bound_sides: int = 192,
) -> dict[str, float]:
    """Evaluate one candidate S2=(a,b) in local coordinates."""
    second_point = geom_cpp.Vec2(a, b)

    num_det = 0.0
    weight_sum = 0.0
    area_sum = 0.0
    r_sum = 0.0
    r2_sum = 0.0
    clear_sum = 0.0
    n_lower_sum = 0.0
    empty = 0

    for r, phi, c, s, w in grid.nodes:
        gx = r * c
        gy = r * s
        d2 = distance(a, b, gx, gy)
        k12 = max(0.0, RHO_MAX - max(RHO_MIN, r, d2))
        nd = k12 * w
        num_det += nd
        if nd <= 0.0:
            continue

        beta2 = geom_cpp.bearing_deg(second_point, geom_cpp.Vec2(gx, gy))
        for delta, d_weight in zip(grid.delta_nodes, grid.delta_weights):
            metrics = region_metrics(second_point, beta2, float(delta), bound_sides)
            if metrics is None:
                empty += 1
                continue
            area, diameter, r_min = metrics
            ww = nd * float(d_weight)
            weight_sum += ww
            area_sum += ww * area
            r_sum += ww * r_min
            r2_sum += ww * r_min * r_min
            if r_min <= CLEAR_RADIUS + 1e-9:
                clear_sum += ww
            n_lower_sum += ww * covering_lower_bound(area, diameter, r_min)

    p_det = num_det / grid.first_success_norm if grid.first_success_norm > 0.0 else float("nan")
    if weight_sum <= 0.0:
        return {
            "a": a,
            "b": b,
            "p_det": p_det,
            "E_area": float("nan"),
            "E_Rmin": float("nan"),
            "sd_Rmin": float("nan"),
            "P_clear20": float("nan"),
            "E_N20_lower": float("nan"),
            "empty": float(empty),
        }

    e_r = r_sum / weight_sum
    var_r = max(0.0, r2_sum / weight_sum - e_r * e_r)
    return {
        "a": a,
        "b": b,
        "p_det": p_det,
        "E_area": area_sum / weight_sum,
        "E_Rmin": e_r,
        "sd_Rmin": math.sqrt(var_r),
        "P_clear20": clear_sum / weight_sum,
        "E_N20_lower": n_lower_sum / weight_sum,
        "empty": float(empty),
    }


def parse_points(text: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for token in text.replace(";", " ").split():
        if not token:
            continue
        parts = token.split(",")
        if len(parts) != 2:
            raise ValueError(f"bad point token: {token!r}")
        points.append((float(parts[0]), float(parts[1])))
    return points


def markdown_table(rows: list[dict[str, float]], top: int) -> str:
    lines = [
        "| S2=(a,b) | p_det | E[area] | E[Rmin] | sd[Rmin] | P(Rmin<=20) | E[N20_lb] |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows[:top]:
        lines.append(
            f"| ({row['a']:.0f}, {row['b']:.0f}) | "
            f"{row['p_det']:.6f} | {row['E_area']:.2f} | {row['E_Rmin']:.3f} | "
            f"{row['sd_Rmin']:.3f} | {100.0 * row['P_clear20']:.2f}% | "
            f"{row['E_N20_lower']:.3f} |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--points",
        type=str,
        default="",
        help='candidate local points, e.g. "700,450 850,520 900,550"',
    )
    parser.add_argument("--nr-low", type=int, default=16)
    parser.add_argument("--nr-high", type=int, default=16)
    parser.add_argument("--nphi", type=int, default=12)
    parser.add_argument("--ndelta", type=int, default=7)
    parser.add_argument("--bound-sides", type=int, default=192)
    parser.add_argument("--grid", type=int, default=0, help="run a coarse search on an N x N local grid")
    parser.add_argument("--top", type=int, default=12)
    args = parser.parse_args()

    grid = make_grid(args.nr_low, args.nr_high, args.nphi, args.ndelta)

    points: list[tuple[float, float]] = []
    if args.points:
        points.extend(parse_points(args.points))

    if args.grid > 0:
        # A reasonable local search box.  The lower side can be mirrored.
        xs = np.linspace(500.0, 1000.0, args.grid)
        ys = np.linspace(1.0, 700.0, args.grid)
        for x in xs:
            for y in ys:
                if guaranteed_detection_region(float(x), float(y)) and angle_condition(float(x), float(y)):
                    points.append((float(x), float(y)))

    if not points:
        points = [
            (650.0, 450.0),
            (700.0, 450.0),
            (750.0, 500.0),
            (800.0, 520.0),
            (850.0, 520.0),
            (860.0, 510.0),
            (900.0, 550.0),
        ]

    rows = [evaluate_point(a, b, grid, args.bound_sides) for a, b in points]
    rows = [r for r in rows if math.isfinite(r["E_Rmin"])]

    print("## All evaluated candidates")
    print(markdown_table(sorted(rows, key=lambda r: r["E_Rmin"]), args.top))
    print()

    valid = [r for r in rows if r["p_det"] >= 0.999]
    if valid:
        print("## Candidates with p_det >= 0.999, sorted by E[Rmin]")
        print(markdown_table(sorted(valid, key=lambda r: r["E_Rmin"]), args.top))
        print()
        print("## Candidates with p_det >= 0.999, sorted by P(Rmin<=20)")
        print(markdown_table(sorted(valid, key=lambda r: -r["P_clear20"]), args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
