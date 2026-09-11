#!/usr/bin/env python3
"""Monte Carlo study for Q1 diameter-circle coverage probability.

Models
------
disk:
    Target G and all observation points P_i are independent uniform by area
    in the same disk of radius R (default 1800 m).  This is the model asked
    for in the question.

ring:
    Target is fixed at the disk centre; observation points are independent
    uniform by area on an annulus around the target.  This is useful as a
    sensitivity check for deliberate observation strategies.

ring_offset:
    Target G is uniform in the disk; observation points are independent
    uniform by area on an annulus centred at G.  This mimics a strategy that
    already knows a rough target location and places observers around it.

For each observation:
    true_bearing = bearing(P_i, G)
    measured     = true_bearing + delta,   delta ~ Uniform[-1 deg, 1 deg]

The feasible region is the intersection of all 2 deg wedges and the target
disk.  We report:
    P_cover  = P(the disk with the region diameter as diameter covers it)
             = P(Welzl basis is a two-point basis)
    P_basis3 = 1 - P_cover
    E[D]     = E[region diameter]
    E[Rmin]  = E[minimum enclosing circle radius]
    P_clear20 = P(Rmin <= 20 m)
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cpp"))

try:
    import geom_cpp  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Cannot import geom_cpp. Build it first with:\n"
        "    ./cpp/build.sh"
    ) from exc


def wrap360(x: float) -> float:
    x = math.fmod(x, 360.0)
    return x + 360.0 if x < 0.0 else x


def rand_in_disk(rng: random.Random, center: geom_cpp.Vec2, radius: float) -> geom_cpp.Vec2:
    r = radius * math.sqrt(rng.random())
    a = rng.uniform(0.0, 2.0 * math.pi)
    return geom_cpp.Vec2(center.x + r * math.cos(a), center.y + r * math.sin(a))


def rand_annulus(rng: random.Random, center: geom_cpp.Vec2, r_min: float, r_max: float) -> geom_cpp.Vec2:
    # Area-uniform on an annulus: sample r^2 uniformly in [r_min^2, r_max^2].
    u = rng.random()
    r = math.sqrt(r_min * r_min + u * (r_max * r_max - r_min * r_min))
    a = rng.uniform(0.0, 2.0 * math.pi)
    return geom_cpp.Vec2(center.x + r * math.cos(a), center.y + r * math.sin(a))


@dataclass
class Observation:
    p: geom_cpp.Vec2
    bearing_deg: float


def make_observation(rng: random.Random, p: geom_cpp.Vec2, target: geom_cpp.Vec2) -> Observation:
    true_bearing = wrap360(math.degrees(math.atan2(target.y - p.y, target.x - p.x)))
    noise = rng.uniform(-1.0, 1.0)
    return Observation(p, wrap360(true_bearing + noise))


def sample_observations(rng: random.Random, model: str, k: int, radius: float) -> tuple[geom_cpp.Vec2, list[Observation]]:
    if model == "disk":
        target = rand_in_disk(rng, geom_cpp.Vec2(0.0, 0.0), radius)
        obs = []
        for _ in range(k):
            p = rand_in_disk(rng, geom_cpp.Vec2(0.0, 0.0), radius)
            obs.append(make_observation(rng, p, target))
        return target, obs

    if model == "ring":
        target = geom_cpp.Vec2(0.0, 0.0)
        obs = []
        for _ in range(k):
            p = rand_annulus(rng, target, 1000.0, 1500.0)
            obs.append(make_observation(rng, p, target))
        return target, obs

    if model == "ring_offset":
        target = rand_in_disk(rng, geom_cpp.Vec2(0.0, 0.0), radius)
        obs = []
        for _ in range(k):
            p = rand_annulus(rng, target, 800.0, 1500.0)
            obs.append(make_observation(rng, p, target))
        return target, obs

    raise ValueError(model)


def run_model(model: str, k: int, samples: int, radius: float, seed: int) -> dict[str, float]:
    rng = random.Random(seed + 1000 * k + 17 * len(model))
    bound = radius
    if model == "ring":
        bound = max(radius, 2000.0)
    elif model == "ring_offset":
        bound = max(radius, 3500.0)

    n_cover = 0
    n_clear = 0
    n_empty = 0
    sum_d = 0.0
    sum_r = 0.0

    for _ in range(samples):
        target, obs = sample_observations(rng, model, k, radius)
        cpp_obs = [
            geom_cpp.Observation(o.p, o.bearing_deg, 1.0)
            for o in obs
        ]
        region = geom_cpp.build_region(cpp_obs, bound, 128)
        if region.is_empty():
            n_empty += 1
            continue

        diameter = geom_cpp.rotating_calipers_diameter(region)
        circle = geom_cpp.welzl_min_enclosing_circle(region, seed=rng.randrange(1 << 30))

        if geom_cpp.diameter_circle_covers(region, diameter):
            n_cover += 1
        if geom_cpp.can_clear(circle, 20.0):
            n_clear += 1

        sum_d += diameter.distance
        sum_r += circle.radius

    valid = samples - n_empty
    p_cover = n_cover / valid if valid else float("nan")
    p_clear = n_clear / valid if valid else float("nan")
    return {
        "model": model,
        "k": k,
        "samples": samples,
        "empty": n_empty,
        "P_cover": p_cover,
        "P_basis3": 1.0 - p_cover,
        "P_clear20": p_clear,
        "E_D": sum_d / valid if valid else float("nan"),
        "E_Rmin": sum_r / valid if valid else float("nan"),
    }


def markdown_table(rows: list[dict[str, float]]) -> str:
    lines = [
        "| model | k | P(cover) | P(basis3) | P(Rmin<=20m) | E[D] | E[Rmin] |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['model']} | {r['k']} | "
            f"{100.0 * r['P_cover']:.2f}% | "
            f"{100.0 * r['P_basis3']:.2f}% | "
            f"{100.0 * r['P_clear20']:.2f}% | "
            f"{r['E_D']:.3f} | "
            f"{r['E_Rmin']:.3f} |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", default=["disk", "ring", "ring_offset"])
    parser.add_argument("--ks", nargs="+", type=int, default=[2, 3, 4])
    parser.add_argument("--samples", type=int, default=5000)
    parser.add_argument("--radius", type=float, default=1800.0)
    parser.add_argument("--seed", type=int, default=20260913)
    args = parser.parse_args()

    rows = []
    for model in args.models:
        for k in args.ks:
            row = run_model(model, k, args.samples, args.radius, args.seed)
            rows.append(row)
            print(row, file=sys.stderr)

    print(markdown_table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
