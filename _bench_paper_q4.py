#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同框架 A/B：论文 Q4 内核 vs 我们自己的两套策略，跑在**完全相同**的 mock 案例上。

每个策略用同一个 ``generate_case(seed, ...)``，因此源的位置/频道/类型完全一致，
差异只能归因到策略本身。唯一不可控项是 ±1° 误差的具体实现方式（各侧 mock 不同），
所以结论应看"全清场数"这种稳健量，而不是单场小数后几位。

``--mode omni``（问题3 全向）
    q3        框架 Q3Strategy（main 骨架）
    matrix    知识矩阵 KnowledgeSearchStrategy
    paper-q3  论文内核 run_q3（**未采纳的对照臂**：慢约 1.7 倍，见 docs）

``--mode dir``（问题4 全向+定向混合）
    q3        框架 Q3Strategy（当作全向策略直接套用，会掉清除率）
    matrix    KnowledgeSearchStrategy(directional=True)
    paper-q4  论文 Q4 内核（本分支新接入）

用法::

    python3 _bench_paper_q4.py --mode omni --seeds 1-30
    python3 _bench_paper_q4.py --mode dir  --seeds 1-30
    python3 _bench_paper_q4.py --mode dir  --seeds 1-20 --n-directional 8
    python3 _bench_paper_q4.py --mode dir  --seeds 1-10 --only paper-q4
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
from mathmodel2026b.strategy_matrix import (  # noqa: E402
    KnowledgeSearchStrategy,
    MatrixParams,
)
from mathmodel2026b.strategy_matrix import Q4Strategy as MatrixQ4Strategy  # noqa: E402
from mathmodel2026b.strategy_q4 import PaperEnvAdapter  # noqa: E402
from mathmodel2026b.strategy_q4 import Q4Strategy as PaperQ4Strategy  # noqa: E402

ROBOT = "000000000000"  # 占位队号


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


class _PaperQ3Arm:
    """论文内核 ``run_q3`` 的对照臂（仅用于 A/B，不建议上线：比我们慢约 1.7 倍）。"""

    def __init__(self, mode: str = "two_stage") -> None:
        self.mode = mode
        self.params = Q3Params()
        self.trace: list[dict] = []

    def run(self, client, state) -> None:
        import coverage as paper_cov  # paper_q4/ 已由 strategy_q4 插入 sys.path
        import q3_strategy as paper_q3

        route, _ = paper_cov.open_route_7(paper_cov.RECOMMENDED_RHO)
        env = PaperEnvAdapter(self, client, state, wall_deadline=None)
        paper_q3.run_q3(env, route, mode=self.mode, verbose=False)


def build(name: str, *, scan_layout: str | None = None):
    if name == "q3":
        return Q3Strategy(Q3Params())
    if name == "matrix":
        return KnowledgeSearchStrategy(MatrixParams())
    if name == "matrix-dir":
        # ``scan_layout="axial"`` = 31 点三角网格（**朝向完备**的发现层，
        # 见 coverage.heading_cover_layout）；默认 polygon 布局的朝向覆盖
        # 不完备，定向源会漏（实测 30 例只清 12 例）。
        params = MatrixParams(directional=True)
        if scan_layout:
            params.scan_layout = scan_layout
        return MatrixQ4Strategy(params)
    if name == "paper-q3":
        return _PaperQ3Arm("two_stage")
    if name == "paper-q4":
        return PaperQ4Strategy()
    raise SystemExit(f"未知策略：{name}")


def run_one(
    seed: int,
    which: str,
    *,
    directional: bool,
    n_directional: int | None,
    scan_layout: str | None = None,
) -> dict:
    case = generate_case(seed, omni_only=not directional, n_directional=n_directional)
    with MockSimulator(robot_id=ROBOT, case=case) as sim:
        client = HttpSimulatorClient(robot_id=ROBOT, base_url=sim.base_url, verbose=False)
        state = DogState()
        strategy = build(which, scan_layout=scan_layout)
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
        return {
            "seed": seed,
            "error": error,
            "cleared": summary["cleared_count"],
            "n": summary["n_jammers"],
            "vt": summary["virtual_time_s"],
            "avg": summary["average_clear_time_s"],
            "dist": summary["move_distance_m"],
            "meas": summary["measure_count"],
            "wall": wall,
            "uncleared": summary["uncleared_channels"],
        }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["omni", "dir"], default="omni")
    ap.add_argument("--seeds", default="1-30")
    ap.add_argument("--n-directional", type=int, default=None,
                    help="固定定向源个数；默认由案例生成器随机（n_dir = randint(1, n//2)）")
    ap.add_argument("--only", default=None, help="逗号分隔的策略名")
    ap.add_argument(
        "--scan-layout",
        default=None,
        choices=["polygon", "axial"],
        help="矩阵策略的发现层布局（仅对 matrix-dir 生效）：axial=朝向完备三角网格",
    )
    ap.add_argument("--quiet", action="store_true", help="只打印汇总表")
    args = ap.parse_args()

    seeds = parse_seeds(args.seeds)
    directional = args.mode == "dir"
    default = ["q3", "matrix", "paper-q3"] if not directional else ["q3", "matrix-dir", "paper-q4"]
    names = args.only.split(",") if args.only else default

    results: dict[str, list[dict]] = {}
    for which in names:
        rows: list[dict] = []
        for seed in seeds:
            row = run_one(seed, which, directional=directional,
                          n_directional=args.n_directional,
                          scan_layout=args.scan_layout)
            rows.append(row)
            if not args.quiet:
                print(
                    f"  [{which:10s}] seed={seed:<3} clear={row['cleared']:>2}/{row['n']:<2} "
                    f"vt={row['vt']:8.0f} avg={(row['avg'] or 0):7.1f} "
                    f"dist={row['dist']:7.0f} meas={row['meas']:4d} wall={row['wall']:5.2f}"
                    + (f"  ERR={row['error']}" if row["error"] else ""),
                    flush=True,
                )
        results[which] = rows

    print(f"\n############ summary ({args.mode}, seeds={len(seeds)}, "
          f"n_directional={args.n_directional}) ############")
    header = (f"{'strategy':<11}{'full':>8}{'ratioMean':>11}{'ratioMin':>10}"
              f"{'vt_med':>10}{'avg_med':>10}{'dist_med':>11}{'meas_med':>10}{'wall_med':>10}")
    print(header)
    for which, rows in results.items():
        ok = [r for r in rows if r["error"] is None]
        if not ok:
            print(f"{which:<11}  全部异常")
            continue
        full = f"{sum(1 for r in ok if r['cleared'] == r['n'])}/{len(ok)}"
        avgs = [r["avg"] for r in ok if r["avg"]]
        print(
            f"{which:<11}{full:>8}"
            f"{statistics.fmean(r['cleared'] / r['n'] for r in ok):>11.4f}"
            f"{min(r['cleared'] / r['n'] for r in ok):>10.3f}"
            f"{statistics.median(r['vt'] for r in ok):>10.0f}"
            f"{(statistics.median(avgs) if avgs else float('nan')):>10.2f}"
            f"{statistics.median(r['dist'] for r in ok):>11.0f}"
            f"{statistics.median(r['meas'] for r in ok):>10.1f}"
            f"{statistics.median(r['wall'] for r in ok):>10.2f}"
        )

    for which, rows in results.items():
        bad = [r for r in rows if r["error"] is None and r["cleared"] < r["n"]]
        if bad:
            print(f"  {which} 未全清："
                  + ", ".join(f"{r['seed']}({r['cleared']}/{r['n']})" for r in bad))
        errs = [r for r in rows if r["error"]]
        if errs:
            print(f"  {which} 异常："
                  + "; ".join(f"seed{r['seed']}: {r['error']}" for r in errs[:4]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
