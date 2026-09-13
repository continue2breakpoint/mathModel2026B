#!/usr/bin/env python3
"""离线优化 Q4 发现层点集与路径（方法二原型）。

示例：

    # 稳妥版：每个候选移动都用连续证书复核，较慢
    python scripts/optimize_q4_layout.py --strict --iterations 2 --out /tmp/q4_layout.json

    # 快速实验版：只用采样证书打分，轮末连续证书回退
    python scripts/optimize_q4_layout.py --fast --iterations 8 --out /tmp/q4_layout.json

输出 JSON 中 `points` 与 `order` 可直接喂给自定义 strategy：

    route = [res.points[i] for i in res.order]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mathmodel2026b.coverage import heading_cover_layout  # noqa: E402
from mathmodel2026b.geometry import Point  # noqa: E402
from mathmodel2026b.joint_layout import (  # noqa: E402
    JointLayoutOptimizer,
    JointLayoutParams,
    nearest_neighbor_order,
    route_length,
    two_opt_open,
)
from mathmodel2026b.strategy_matrix import KnowledgeSearchStrategy  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="方法二 Q4 发现层联合优化")
    ap.add_argument("--spacing", type=float, default=950.0, help="初始轴向网格间距")
    ap.add_argument("--iterations", type=int, default=2)
    ap.add_argument("--sample-step", type=float, default=120.0, help="优化打分采样步长")
    ap.add_argument("--cell-m", type=float, default=120.0, help="连续证书根单元")
    ap.add_argument("--min-cell-m", type=float, default=10.0, help="连续证书最小单元")
    ap.add_argument("--move-steps", type=str, default="100,50,20", help="局部移动步长，逗号分隔")
    ap.add_argument("--strict", action="store_true", help="每个候选都做连续证书复核（完备性优先）")
    ap.add_argument("--fast", action="store_true", help="只做采样检查，轮末复核（速度快）")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, default=Path("q4_joint_layout.json"))
    args = ap.parse_args(argv)

    strict = True
    if args.fast:
        strict = False
    if args.strict:
        strict = True

    points = heading_cover_layout(args.spacing)
    chain = KnowledgeSearchStrategy._unit_edge_hamiltonian_chain(  # noqa: SLF001
        points, prefer_start=Point(0.0, 0.0)
    )
    if chain is None:
        order = nearest_neighbor_order(points, Point(0.0, 0.0))
        order = two_opt_open(points, order, Point(0.0, 0.0))
    else:
        order = [points.index(p) for p in chain]

    params = JointLayoutParams(
        max_iterations=args.iterations,
        sample_step_m=args.sample_step,
        move_steps_m=tuple(float(x) for x in args.move_steps.split(",") if x.strip()),
        certificate_cell_m=args.cell_m,
        certificate_min_cell_m=args.min_cell_m,
        random_seed=args.seed,
        strict_accept=strict,
    )
    optimizer = JointLayoutOptimizer(params)
    started = time.perf_counter()
    result = optimizer.optimize(points, order)
    wall = time.perf_counter() - started

    print(f"initial route = {route_length(points, order, Point(0, 0)):.1f} m")
    print(f"final   route = {result.route_length_m:.1f} m")
    print(f"n_points      = {len(result.points)}")
    print(f"continuous    = {result.certificate.ok}")
    print(f"wall          = {wall:.1f} s")
    payload = result.as_dict()
    payload["wall_time_s"] = wall
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
