#!/usr/bin/env python3
"""覆盖扫描设计优化（离线数值搜索，不需要模拟器）。

问题：机器狗要保证"区域里任何一个干扰源都至少在一个检测点上被收到"。
最坏情况有效接收半径 1000 m，所以停点集合的 1000 m 邻域必须覆盖半径 1800 m 的圆域。

目标函数**不能只看路程**：每个停点都要扫 20 个频道，
一次停点的代价 ≈ 20 ×（切换 1 s + 检测 5 s）= 120 s，等价 600 m 路程。

    min  路程/5 + 120 × 停点数
    s.t. 覆盖半径 <= 1000 m

在"正 n 边形巡游 + 每条边上均匀加点 + 原点(免费起点)"这一族里穷举。

用法::

    python3 script/q3_cover_design.py
    python3 script/q3_cover_design.py --margin 30
"""

from __future__ import annotations

import argparse
import math

R_COVER = 1000.0  # 最坏有效接收半径
R_ARENA = 1800.0  # 目标区域半径
STOP_COST_S = 120.0  # 每个停点扫 20 个频道的检测+切换代价
SPEED = 5.0  # m/s


def covering_radius(stops: list[tuple[float, float]], n_angle: int = 720,
                    n_ring: int = 45) -> float:
    worst = 0.0
    for i in range(n_angle):
        th = 2 * math.pi * i / n_angle
        c, s = math.cos(th), math.sin(th)
        for j in range(1, n_ring + 1):
            r = R_ARENA * j / n_ring
            x, y = r * c, r * s
            d = min(math.hypot(x - px, y - py) for px, py in stops)
            if d > worst:
                worst = d
    return worst


def path_len(pts: list[tuple[float, float]]) -> float:
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def ngon_stops(nv: int, radius: float, per_edge: int) -> list[tuple[float, float]]:
    verts = [
        (radius * math.cos(2 * math.pi * k / nv), radius * math.sin(2 * math.pi * k / nv))
        for k in range(nv)
    ]
    pts: list[tuple[float, float]] = [(0.0, 0.0)]
    for k in range(nv):
        a, b = verts[k], verts[(k + 1) % nv]
        pts.append(a)
        for j in range(1, per_edge + 1):
            t = j / (per_edge + 1)
            pts.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
    return pts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="覆盖扫描设计优化")
    ap.add_argument("--margin", type=float, default=10.0,
                    help="要求覆盖半径至少比 1000m 小这么多（安全余量）")
    args = ap.parse_args(argv)

    rows = []
    for nv in range(3, 13):
        for per_edge in (0, 1, 2):
            for r in range(700, 2001, 10):
                pts = ngon_stops(nv, float(r), per_edge)
                cr = covering_radius(pts)
                if cr > R_COVER - args.margin:
                    continue
                L = path_len(pts)
                rows.append((L / SPEED + STOP_COST_S * len(pts), nv, per_edge, r,
                             len(pts), L, cr))
    rows.sort()

    print(f"安全余量 >= {args.margin:.0f} m")
    print(f"{'名次':<5}{'n边形':>6}{'加点':>5}{'半径':>6}{'停点':>6}{'路程(m)':>9}"
          f"{'移动(s)':>8}{'检测(s)':>8}{'合计(s)':>9}{'覆盖半径':>9}")
    for i, (t, nv, pe, r, ns, L, cr) in enumerate(rows[:10], 1):
        print(f"{i:<5}{nv:>6}{pe:>5}{r:>6}{ns:>6}{L:>9.0f}{L / SPEED:>8.0f}"
              f"{STOP_COST_S * ns:>8.0f}{t:>9.0f}{cr:>9.1f}")

    cur_path = 6 * 1200.0
    cur_t = cur_path / SPEED + STOP_COST_S * 7
    print(f"\n当前默认（六边形 r=1200，7 停点）：路程 {cur_path:.0f} m，"
          f"移动 {cur_path / SPEED:.0f} s + 检测 {STOP_COST_S * 7:.0f} s = {cur_t:.0f} s")
    if rows:
        b = rows[0]
        print(f"同余量下最优：n={b[1]} 加点{b[2]} r={b[3]} 停点{b[4]} → {b[0]:.0f} s，"
              f"省 {cur_t - b[0]:.0f} s（{100 * (cur_t - b[0]) / cur_t:.1f}%）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
