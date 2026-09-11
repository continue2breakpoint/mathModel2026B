#!/usr/bin/env python3
"""Randomized tests for the C++ geometry kernels.

The test generates:
  * a hidden target point G;
  * several observation points P_i;
  * the true bearing from P_i to G;
  * a random bearing error in [-1, 1] degrees.

Then it builds the wedge-intersection region and compares:
  * C++ rotating-calipers diameter vs Python brute force;
  * C++ Welzl minimum enclosing circle vs Python brute force;
  * diameter-circle coverage decisions;
  * true target containment.
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cpp"))

import geom_cpp  # type: ignore  # noqa: E402


def wrap360(x: float) -> float:
    x = math.fmod(x, 360.0)
    return x + 360.0 if x < 0.0 else x


def dist(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def brute_diameter(verts):
    best_d = -1.0
    best = None
    n = len(verts)
    for i in range(n):
        for j in range(i + 1, n):
            d = dist(verts[i], verts[j])
            if d > best_d:
                best_d = d
                best = (verts[i], verts[j])
    if best is None:
        return 0.0, (geom_cpp.Vec2(0.0, 0.0), geom_cpp.Vec2(0.0, 0.0))
    return best_d, best


def circle_contains(c, p, eps=1e-7) -> bool:
    return dist(c["center"], p) <= c["radius"] + eps


def circle_from_two(a, b):
    cx = (a.x + b.x) / 2.0
    cy = (a.y + b.y) / 2.0
    return {"center": geom_cpp.Vec2(cx, cy), "radius": math.hypot(cx - a.x, cy - a.y), "basis": (a, b)}


def circle_from_three(a, b, c):
    d = 2.0 * (a.x * (b.y - c.y) + b.x * (c.y - a.y) + c.x * (a.y - b.y))
    if abs(d) < 1e-12:
        return None
    a2 = a.x * a.x + a.y * a.y
    b2 = b.x * b.x + b.y * b.y
    c2 = c.x * c.x + c.y * c.y
    ux = (a2 * (b.y - c.y) + b2 * (c.y - a.y) + c2 * (a.y - b.y)) / d
    uy = (a2 * (c.x - b.x) + b2 * (a.x - c.x) + c2 * (b.x - a.x)) / d
    center = geom_cpp.Vec2(ux, uy)
    return {"center": center, "radius": math.hypot(ux - a.x, uy - a.y), "basis": (a, b, c)}


def brute_min_circle(verts):
    pts = list(verts)
    if not pts:
        return {"center": geom_cpp.Vec2(0.0, 0.0), "radius": 0.0, "basis": ()}
    best = circle_from_two(pts[0], pts[0])
    best_radius = float("inf")

    def all_inside(c) -> bool:
        return all(circle_contains(c, p, 1e-7) for p in pts)

    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            c = circle_from_two(pts[i], pts[j])
            if all_inside(c) and c["radius"] < best_radius:
                best, best_radius = c, c["radius"]
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            for k in range(j + 1, len(pts)):
                c = circle_from_three(pts[i], pts[j], pts[k])
                if c is not None and all_inside(c) and c["radius"] < best_radius:
                    best, best_radius = c, c["radius"]
    if math.isinf(best_radius):
        # Collinear fallback.
        d, (a, b) = brute_diameter(pts)
        return circle_from_two(a, b)
    return best


def random_target_in_disk(rng: random.Random, radius: float):
    r = radius * math.sqrt(rng.random())
    a = rng.uniform(0.0, 2.0 * math.pi)
    return geom_cpp.Vec2(r * math.cos(a), r * math.sin(a))


def random_observations(rng: random.Random, target, n: int | None = None):
    if n is None:
        n = rng.randint(2, 8)
    obs = []
    for _ in range(n):
        # Put observers at informative distances around the hidden target.
        r = rng.uniform(800.0, 1500.0)
        a = rng.uniform(0.0, 2.0 * math.pi)
        px = target.x + r * math.cos(a)
        py = target.y + r * math.sin(a)
        true_bearing = wrap360(math.degrees(math.atan2(target.y - py, target.x - px)))
        noise = rng.uniform(-1.0, 1.0)  # inside the +/-1 degree error bound
        obs.append(geom_cpp.Observation(geom_cpp.Vec2(px, py), wrap360(true_bearing + noise)))
    return obs


def run_case(rng: random.Random, case_id: int, n_obs: int | None = None):
    target = random_target_in_disk(rng, 1200.0)
    obs = random_observations(rng, target, n_obs)
    region = geom_cpp.build_region(obs, 4000.0, 128)

    assert not region.is_empty(), f"case {case_id}: empty region"
    assert geom_cpp.point_in_region(target, region, 1e-7), (
        f"case {case_id}: true target is not in the constructed region"
    )

    verts = region.vertices
    d_cpp = geom_cpp.rotating_calipers_diameter(region)
    d_py, _ = brute_diameter(verts)
    assert abs(d_cpp.distance - d_py) <= 1e-7 * max(1.0, d_py), (
        f"case {case_id}: diameter mismatch {d_cpp.distance} vs {d_py}"
    )

    c_cpp = geom_cpp.welzl_min_enclosing_circle(region, seed=20260913 + case_id)
    c_py = brute_min_circle(verts)
    assert abs(c_cpp.radius - c_py["radius"]) <= 1e-6 * max(1.0, c_py["radius"]), (
        f"case {case_id}: min-circle mismatch {c_cpp.radius} vs {c_py['radius']}"
    )
    assert c_cpp.radius + 1e-7 >= d_cpp.distance / 2.0, (
        f"case {case_id}: impossible min circle smaller than half diameter"
    )

    cover_cpp = geom_cpp.diameter_circle_covers(region, d_cpp)
    a, b = d_cpp.a, d_cpp.b
    cover_py = all(
        (v.x - a.x) * (v.x - b.x) + (v.y - a.y) * (v.y - b.y) <= 1e-8
        for v in verts
    )
    assert cover_cpp == cover_py, f"case {case_id}: coverage mismatch"

    return {
        "n_obs": len(obs),
        "n_vertices": len(verts),
        "diameter": d_cpp.distance,
        "min_radius": c_cpp.radius,
        "basis_size": c_cpp.basis_size(),
        "diameter_cover": cover_cpp,
        "clear20": geom_cpp.can_clear(c_cpp, 20.0),
    }


def test_collinear_welzl():
    rng = random.Random(123)
    pts = []
    for _ in range(20):
        t = rng.uniform(-10.0, 10.0)
        pts.append(geom_cpp.Vec2(t, 2.0 * t + 1.0))
    c_cpp = geom_cpp.welzl_min_enclosing_circle_points(pts, seed=7)
    c_py = brute_min_circle(pts)
    assert abs(c_cpp.radius - c_py["radius"]) < 1e-6
    assert c_cpp.basis_size() in (1, 2)


def main() -> int:
    rng = random.Random(20260913)
    samples = []
    for case_id in range(300):
        samples.append(run_case(rng, case_id))

    test_collinear_welzl()

    cover_count = sum(1 for s in samples if s["diameter_cover"])
    clear_count = sum(1 for s in samples if s["clear20"])
    basis_counts = {k: sum(1 for s in samples if s["basis_size"] == k) for k in (1, 2, 3)}

    print("all random tests passed")
    print(f"cases                 : {len(samples)}")
    print(f"diameter-covered cases: {cover_count}/{len(samples)}")
    print(f"20m-clearable cases   : {clear_count}/{len(samples)}")
    print(f"Welzl basis sizes     : {basis_counts}")
    print("first 5:")
    for s in samples[:5]:
        print("  ", s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
