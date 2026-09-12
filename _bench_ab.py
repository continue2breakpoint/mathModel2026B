#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A/B benchmark: ``Q3Strategy`` (upstream) vs ``KnowledgeSearchStrategy`` (matrix).

Both run on *identical* mock cases (same generator seed), so every difference is
attributable to the strategy. Prints per-seed rows plus medians, clearance rate
and the matrix strategy's own odometer (which move leg ate the distance).

Usage::

    python _bench_ab.py --seeds 1-30                 # omni (Q3)
    python _bench_ab.py --seeds 1-30 --directional   # omni + directional mix (Q4)
    python _bench_ab.py --seeds 1-10 --only matrix
    python _bench_ab.py --seeds 1-30 --directional --param scan_layout=axial

.. note::
   ``--directional`` 现在会**同时**把 ``directional=true`` 传进策略参数
   （并按 ``strategy_matrix.Q4Strategy`` 构造矩阵策略）。历史版本只把
   ``directional`` 传给场景生成器，策略仍以 ``directional=False`` 运行，
   因此旧文档里"``--directional`` 下的矩阵结果"不能归因于定向逻辑 ——
   这正是 ``docs/method-review-next-steps.md`` §2.4 指出的口径错误。
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "framework" / "src"))

from mathmodel2026b.client import HttpSimulatorClient  # noqa: E402
from mathmodel2026b.mock.server import MockSimulator  # noqa: E402
from mathmodel2026b.mock.world import generate_case  # noqa: E402
from mathmodel2026b.state import DogState  # noqa: E402
from mathmodel2026b.strategy import Q3Params, Q3Strategy  # noqa: E402
from mathmodel2026b.strategy_matrix import MatrixParams  # noqa: E402
from mathmodel2026b.strategy_matrix import KnowledgeSearchStrategy, Q4Strategy  # noqa: E402

ROBOT = "000000000000"


def parse_seeds(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.extend(range(int(lo), int(hi) + 1))
        elif part:
            out.append(int(part))
    return sorted(set(out))


def run_one(seed: int, which: str, *, directional: bool, n_directional: int | None, params: dict):
    case = generate_case(seed, omni_only=not directional, n_directional=n_directional)
    with MockSimulator(robot_id=ROBOT, case=case) as sim:
        client = HttpSimulatorClient(robot_id=ROBOT, base_url=sim.base_url, verbose=False)
        state = DogState()
        if which == "upstream":
            strategy = Q3Strategy(Q3Params())
        else:
            # 关键修正：``--directional`` 必须同时进入**策略参数**，否则矩阵策略
            # 仍以全向假设运行，测出来的差异与定向逻辑无关。
            matrix_params = MatrixParams(**{"directional": directional, **params})
            strategy = (
                Q4Strategy(matrix_params) if directional else KnowledgeSearchStrategy(matrix_params)
            )
        client.enter()
        started = time.perf_counter()
        error = None
        try:
            strategy.run(client, state)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        wall = time.perf_counter() - started
        try:
            client.exit()
        except Exception:  # noqa: BLE001
            pass
        summary = sim.world.summary()
        odo = strategy.odometer() if which != "upstream" else {}
        return {
            "seed": seed,
            "error": error,
            "cleared": summary["cleared_count"],
            "n": summary["n_jammers"],
            "vt": summary["virtual_time_s"],
            "avg": summary["average_clear_time_s"],
            "dist": summary["move_distance_m"],
            "meas": summary["measure_count"],
            "clear_n": summary["clear_count"],
            "wall": wall,
            "uncleared": summary["uncleared_channels"],
            "odo": {k: v for k, v in odo.items() if k.startswith("dist:")},
        }


def stats(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["error"] is None]
    out: dict = {"runs": len(rows), "errors": len(rows) - len(ok)}
    if not ok:
        return out
    out["ratio_mean"] = statistics.fmean(r["cleared"] / r["n"] for r in ok)
    out["full"] = sum(1 for r in ok if r["cleared"] == r["n"])
    out["vt_med"] = statistics.median(r["vt"] for r in ok)
    avgs = [r["avg"] for r in ok if r["avg"]]
    out["avg_med"] = statistics.median(avgs) if avgs else None
    out["dist_med"] = statistics.median(r["dist"] for r in ok)
    out["meas_med"] = statistics.median(r["meas"] for r in ok)
    out["clear_med"] = statistics.median(r["clear_n"] for r in ok)
    out["wall_med"] = statistics.median(r["wall"] for r in ok)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="1-30")
    ap.add_argument("--only", default="both", choices=["both", "upstream", "matrix"])
    ap.add_argument("--directional", action="store_true")
    ap.add_argument("--n-directional", type=int, default=None)
    ap.add_argument("--param", action="append", default=[], help="matrix 参数覆盖 key=value")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    params: dict = {}
    for item in args.param:
        key, raw = item.split("=", 1)
        if raw.lower() in ("true", "false"):
            params[key] = raw.lower() == "true"
        elif raw.replace(".", "", 1).replace("-", "", 1).isdigit():
            params[key] = float(raw) if "." in raw else int(raw)
        else:
            params[key] = raw

    seeds = parse_seeds(args.seeds)
    which_list = ["upstream", "matrix"] if args.only == "both" else [args.only]
    results: dict[str, list[dict]] = {}
    for which in which_list:
        rows: list[dict] = []
        for seed in seeds:
            row = run_one(
                seed, which, directional=args.directional, n_directional=args.n_directional, params=params
            )
            rows.append(row)
            if not args.quiet:
                print(
                    f"  [{which:8s}] seed={seed:<3} clear={row['cleared']:>2}/{row['n']:<2} "
                    f"vt={row['vt']:8.0f} avg={(row['avg'] or 0):7.1f} dist={row['dist']:7.0f} "
                    f"meas={row['meas']:4d} clear_calls={row['clear_n']:3d}"
                    + (f"  ERR={row['error']}" if row["error"] else ""),
                    flush=True,
                )
        results[which] = rows

    mode = "directional" if args.directional else "omni"
    print(f"\n################ summary ({mode}) ################")
    for which, rows in results.items():
        s = stats(rows)
        print(f"\n--- {which} ---")
        print(f"  runs={s['runs']} errors={s['errors']} full_clear={s.get('full')}/{s['runs']-s['errors']}")
        print(f"  cleared_ratio mean   = {s.get('ratio_mean', float('nan')):.4f}")
        print(f"  virtual_time  median = {s.get('vt_med', float('nan')):.1f} s")
        print(f"  avg_clear     median = {(s.get('avg_med') or float('nan')):.2f} s/源")
        print(f"  move_distance median = {s.get('dist_med', float('nan')):.1f} m")
        print(f"  measure_count median = {(s.get('meas_med') or 0):.1f}")
        print(f"  wall          median = {s.get('wall_med', float('nan')):.2f} s")
        imperfect = [r for r in rows if r["error"] is None and r["cleared"] < r["n"]]
        if imperfect:
            print("  imperfect seeds: " + ", ".join(f"{r['seed']}({r['cleared']}/{r['n']})" for r in imperfect))

    if len(results) == 2:
        a, b = results["upstream"], results["matrix"]
        print("\n--- delta (matrix vs upstream, medians) ---")
        for key, label in (("vt", "virtual_time"), ("dist", "move_distance"), ("meas", "measure_count")):
            va = [r[key] for r in a if r["error"] is None]
            vb = [r[key] for r in b if r["error"] is None]
            if va and vb:
                ma, mb = statistics.median(va), statistics.median(vb)
                print(f"  {label:14s} {ma:9.1f} -> {mb:9.1f}  ({(mb / ma - 1) * 100:+.1f}%)")
        aa = [r["avg"] for r in a if r["error"] is None and r["avg"]]
        bb = [r["avg"] for r in b if r["error"] is None and r["avg"]]
        if aa and bb:
            ma, mb = statistics.median(aa), statistics.median(bb)
            print(f"  {'avg_clear':14s} {ma:9.2f} -> {mb:9.2f}  ({(mb / ma - 1) * 100:+.1f}%)")
        fa = sum(1 for r in a if r["error"] is None and r["cleared"] == r["n"])
        fb = sum(1 for r in b if r["error"] is None and r["cleared"] == r["n"])
        print(f"  {'full_clear':14s} {fa:>4}/{len(a):<4} -> {fb:>4}/{len(b):<4}")

    # odometer aggregate for matrix
    if "matrix" in results:
        agg: dict[str, list[float]] = {}
        for row in results["matrix"]:
            for key, value in (row.get("odo") or {}).items():
                agg.setdefault(key, []).append(value)
        if agg:
            print("\n--- matrix odometer (median m per run) ---")
            for key, values in sorted(agg.items(), key=lambda kv: -statistics.median(kv[1])):
                print(f"  {key:22s} {statistics.median(values):9.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
