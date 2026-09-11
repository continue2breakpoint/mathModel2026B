#!/usr/bin/env python3
"""诊断：**只跑覆盖扫描之后**，每个频道的可行域有多好？

回答的问题是"覆盖扫描本身已经把源定得多准了"——如果扫描结束后多数频道的
可行域最小包围圆已经 <= 20m，那后面的 refine 就是纯浪费；如果还差很多，
就要看差多少、差在哪个方向（沿距离方向还是横向）。

用法::

    python3 script/q3_diagnose_scan.py --seeds 1-20
"""

from __future__ import annotations

import argparse
import statistics as stats
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(REPO_ROOT / "framework" / "src"))

from mathmodel2026b.client import RecordingClient  # noqa: E402
from mathmodel2026b.geometry import (  # noqa: E402
    Point,
    feasible_region,
    minimum_enclosing_circle,
    polygon_diameter,
)
from mathmodel2026b.mock.server import MockSimulator  # noqa: E402
from mathmodel2026b.state import DogState  # noqa: E402
from mathmodel2026b.strategy import Q3Params, Q3Strategy  # noqa: E402
from mathmodel2026b.client import HttpSimulatorClient  # noqa: E402


class ScanOnlyStrategy(Q3Strategy):
    """只跑覆盖扫描，然后停手。"""

    def run(self, client, state) -> None:  # type: ignore[override]
        import time

        from mathmodel2026b.strategy import Budget

        p = self.params
        self.budget = Budget(
            virtual_deadline_s=p.max_virtual_time_s,
            wall_deadline=time.time() + p.max_wall_time_s,
        )
        self._cover_scan(client, state)


def parse_seeds(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out.extend(range(int(a), int(b) + 1) if b else [int(a)])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="覆盖扫描后的可行域质量诊断")
    ap.add_argument("--seeds", default="1-20")
    ap.add_argument("--scan-radius", type=float, default=1200.0)
    args = ap.parse_args(argv)

    seeds = parse_seeds(args.seeds)
    rows = []
    for seed in seeds:
        params = Q3Params()
        params.scan_radius = args.scan_radius
        sim = MockSimulator(robot_id="000000000000", seed=seed).start()
        try:
            state = DogState()
            client = RecordingClient(
                HttpSimulatorClient(robot_id="000000000000", base_url=sim.base_url),
                hook=None,
            )
            client.enter()
            ScanOnlyStrategy(params).run(client, state)
            for ch, ch_state in state.channels.items():
                if not ch_state.readings:
                    continue
                region = feasible_region(
                    ch_state.readings,
                    half_width_deg=params.half_width_deg,
                    max_range_m=params.max_range_bound_m,
                )
                circle = minimum_enclosing_circle(region) if len(region) >= 3 else None
                truth = next(j for j in sim.world.case.jammers if j.channel == ch)
                rows.append(
                    {
                        "seed": seed,
                        "channel": ch,
                        "n_readings": len(ch_state.readings),
                        "region_pts": len(region),
                        "diameter_m": polygon_diameter(region) if region else None,
                        "enc_circle_r_m": circle.radius if circle else None,
                        "true_dist_to_last_apex_m": Point(
                            ch_state.readings[-1][0].x, ch_state.readings[-1][0].y
                        ).distance_to(truth.position),
                        "max_range_of_source_m": truth.effective_radius_m,
                    }
                )
            client.exit()
        finally:
            sim.stop()

    print(f"seeds={len(seeds)}  被探测到的频道数={len(rows)}")
    by_n = {}
    for r in rows:
        by_n.setdefault(r["n_readings"], []).append(r)
    print(f"\n{'读数个数':<8}{'频道数':>8}{'可行域直径中位(m)':>20}{'包围圆半径中位(m)':>20}{'<=20m 比例':>12}")
    for n in sorted(by_n):
        g = by_n[n]
        dia = [x["diameter_m"] for x in g if x["diameter_m"]]
        rad = [x["enc_circle_r_m"] for x in g if x["enc_circle_r_m"] is not None]
        good = sum(1 for x in rad if x <= 20) / len(rad) if rad else 0
        print(f"{n:<8}{len(g):>8}"
              f"{(stats.median(dia) if dia else float('nan')):>20.0f}"
              f"{(stats.median(rad) if rad else float('nan')):>20.0f}"
              f"{good:>12.1%}")

    rad_all = [x["enc_circle_r_m"] for x in rows if x["enc_circle_r_m"] is not None]
    if rad_all:
        rad_all.sort()
        print(f"\n全部有 3+ 条读数的频道：n={len(rad_all)}")
        for q in (0.1, 0.25, 0.5, 0.75, 0.9):
            print(f"  P{int(q * 100):<3d} 包围圆半径 = {rad_all[int(q * (len(rad_all) - 1))]:.0f} m")
        print(f"  已经 <=20m（可立即清除）的比例 = "
              f"{sum(1 for r in rad_all if r <= 20) / len(rad_all):.1%}")
        print(f"  已经 <=60m 的比例 = {sum(1 for r in rad_all if r <= 60) / len(rad_all):.1%}")
        print(f"  已经 <=100m 的比例 = {sum(1 for r in rad_all if r <= 100) / len(rad_all):.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
