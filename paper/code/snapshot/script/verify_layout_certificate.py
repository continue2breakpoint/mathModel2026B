#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Q4 两个布局的**独立证书验证器**：连续域四叉树证书 + 采样交叉验证 + 0.25° 扫描。

这个脚本为什么存在
------------------
交付布局的完备性原本是靠采样说清楚的：``_verify_layout.py`` 的多分辨率网格、
2000 点凸包交叉验证，以及 ``_brute_check.py`` 的 0.25° 扫描。采样能**发现**洞，
但"采样没发现洞"不等于"没有洞"——快照把这些采样结果写成了"近精确判据"、
把 989.8 m 写成了最坏真值，2026-09-13 的独立复核指出该表述不成立。
本脚本把复核给出的**连续域充分条件**实现成一个可复跑的命令，并同时保留采样类检查，
三项分开报告、各自写清"证明了什么、没证明什么"。

三项检查
--------
1. **连续单元充分条件（主结果）**——:func:`certificate`。
   把目标圆（半径 1800 m）的包围正方形递归四分；对每个与目标圆相交的单元 C，
   取"到 C 的**所有四个顶点**距离都 ≤ 1000 m 的检测点集" S_C，验证 C 的四个顶点
   都在 conv(S_C) 内；通不过就继续细分，直到最小边长 ``--min-side``。
   局部凸包判据 ``x ∈ conv{q_i : |q_i - x| ≤ 1000}`` 与"任意朝向的闭半圆盘都命中"
   等价；由范数的凸性 + 凸包的凸性，单元 C 的顶点在 conv(S_C) 内 ⇒ **C 内每个 x**
   都满足判据，因此不需要对朝向离散采样。完全落在目标圆外的单元直接剔除。
   判定量：``checked``（与目标圆相交的单元数）/ ``passed``（直接通过）/ ``unresolved``
   （细分到最小边长仍未通过），**要求 unresolved == 0**。

2. **快照的多分辨率网格证书 + Delaunay/凸包交叉验证**——:func:`grid_worst` /
   :func:`hull_cross_check`。与 ``_verify_layout.py`` 同参数复现，用来确认
   "同一份布局在旧口径下也过"。

3. **0.25° 采样扫描**——:func:`brute_scan`。与 ``_brute_check.py`` 同参数
   （12000 个圆内随机样本 + 0.05° 边界环，朝向 0.25°），复现 989.8 m 那个数。

口径警告（务必读完再看数字）
----------------------------
* **0.25° 扫描是有限样本，本身不构成全域证明。** 复核 §2 原文：
  「989.8 m 是采样最大值，不能直接称作连续域最坏真值；0.25° 也不能自动消除盲区。」
  加密采样/换角向都可能给出更大的值；它只能证伪（发现洞），不能证实。
* **四叉树证书是数值证书，不是形式化证明。** 复核 §2 原文：
  「这是连续区域的数值证书，不是新的随机采样；使用浮点凸包及向内余量，没有做
  区间算术形式化验证。它证明的是半径 1000 下的覆盖，不宣称精确最坏值就是 989.8。」
  本实现的向内余量：近邻点判据用 ``|q-v| ≤ 1000 - margin``，凸包包含用
  ``signed_distance ≤ -margin``（``--margin``，默认 1e-7）。这覆盖浮点舍入，
  但**不是**区间算术；若需要形式化保证，应改用定向舍入或精确有理数凸包。
* 证书对应题面**闭半圆盘（n·(q-x) ≥ 0）**的等价判据；框架的检测实现用严格
  ``proj > 0``（复核 §6.4 指出这一口径差异）。严格版是更强的要求，故证书结论
  在"框架实际行为"这一侧仍然安全，但边界（n·(q-x) ≈ 0）仍需物理裕量，
  证书本身不对该裕量负责。
* 采样类检查（2、3）的采样域必须是**目标圆内**。快照 ``_verify_layout.py`` 的
  "随机暴力扫描"在半径 2000 m 的盘里取点，报出 ``worst=inf`` 于
  (-406.57, -1921.60)——该点到原点 1964.1 m > 1800 m，已在 Ω 之外，
  不是布局缺陷。本脚本的检查 3 只在 Ω 内取样（与 ``_brute_check.py`` 一致）。

用法::

    # 两个布局、三项检查全跑，并把结果写成 JSON
    python3 script/verify_layout_certificate.py --json logs/q4/certificate.json

    # 只要 v17（24 点）的连续域证书
    python3 script/verify_layout_certificate.py --layout v17 --min-side 0.02

    # 冒烟：粗网格 + 小样本（秒级）
    python3 script/verify_layout_certificate.py --quick

退出码：三项全过 0，否则 1（便于接进验收脚本）。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
from scipy.spatial import ConvexHull, Delaunay

SCRIPT_DIR = Path(__file__).resolve().parent            # mathModel2026B/script
REPO_ROOT = SCRIPT_DIR.parent                           # mathModel2026B
sys.path.insert(0, str(REPO_ROOT / "framework" / "src"))

from mathmodel2026b.geometry import ARENA_RADIUS_M  # noqa: E402
from mathmodel2026b.strategy_v17 import LAYOUT_Q4_V17  # noqa: E402
from mathmodel2026b.strategy_v9 import two_tier_nodes  # noqa: E402

#: 目标圆半径（题面：1800 m 的场地圆盘）。与框架常量核对后再用。
OMEGA = float(ARENA_RADIUS_M)
#: 检测/接收半径（题面：1000 m）。
RADIUS = 1000.0

#: 快照 ``_verify_layout.py`` 用的多分辨率网格与安全余量。
SNAPSHOT_GRIDS: tuple[tuple[float, int], ...] = (
    (60.0, 36), (40.0, 60), (30.0, 75), (25.0, 90), (20.0, 120), (16.0, 150),
)
SNAPSHOT_SAFETY = 12.0
QUICK_GRIDS: tuple[tuple[float, int], ...] = ((60.0, 36), (25.0, 90))

CAVEATS = {
    "brute_scan_is_finite_sample": (
        "0.25° 扫描是有限样本：样本最大正面距离（例如 v17 的 989.8 m）"
        "不能直接称作连续域最坏真值，0.25° 也不能自动消除盲区。它只能证伪。"
    ),
    "quadtree_is_numerical_certificate": (
        "四叉树证书是连续区域的**数值**证书：使用浮点凸包与向内余量，"
        "没有做区间算术形式化验证。它证明的是半径 1000 m 下的覆盖，"
        "不宣称精确最坏值就是 989.8 m。"
    ),
    "closed_half_disk_convention": (
        "证书对应题面闭半圆盘（n·(q-x) ≥ 0）的等价判据；框架检测实现用严格 "
        "proj > 0（更强）。边界朝向仍需物理裕量，证书不对该裕量负责。"
    ),
    "sample_domain_must_be_inside_omega": (
        "采样类检查只在目标圆（半径 1800 m）内取样。快照 _verify_layout.py 的"
        "随机扫描在半径 2000 m 的盘内取点，其 worst=inf 出现在 1964.1 m 处（Ω 之外），"
        "不是布局缺陷。"
    ),
}


# --------------------------------------------------------------------------- 工具
def _signed_hull_max(corners: np.ndarray, S: np.ndarray) -> float | None:
    """返回 corners 相对 conv(S) 的最大有符号距离（<0 表示都在内部）；退化/不足 3 点返回 None。"""
    if len(S) < 3:
        return None
    try:
        hull = ConvexHull(S)
    except Exception:  # noqa: BLE001  QhullError：共线/重复点 → 二维凸包不存在
        return None
    val = corners @ hull.equations[:, :2].T + hull.equations[:, 2]
    return float(val.max())


def _route_length(points: np.ndarray) -> float:
    """按给定顺序、起点原点的开放路径长度（只做信息展示，不参与判定）。"""
    P = np.vstack([np.zeros(2), np.asarray(points, dtype=float)])
    d = np.hypot(*(P[1:] - P[:-1]).T)
    return float(d.sum())


def _grid_points(step: float, ring_deg: float = 0.5, omega: float = OMEGA) -> np.ndarray:
    """目标圆内的笛卡尔网格 + 边界圆环（贴边朝外的源是最坏情形集中地）。"""
    xs = np.arange(-omega, omega + 1e-9, step)
    gx, gy = np.meshgrid(xs, xs)
    gx, gy = gx.ravel(), gy.ravel()
    m = np.hypot(gx, gy) <= omega + 1e-9
    inner = np.stack([gx[m], gy[m]], axis=1)
    ang = np.arange(0.0, 2 * math.pi, math.radians(ring_deg))
    ring = np.stack([omega * np.cos(ang), omega * np.sin(ang)], axis=1)
    return np.vstack([inner, ring])


def _orientations(n_phi: int) -> np.ndarray:
    a = np.arange(n_phi) * (2 * math.pi / n_phi)
    return np.stack([np.cos(a), np.sin(a)], axis=1)


# --------------------------------------------------------------------------- 检查 1
def certificate(points, *, min_side: float = 0.02, margin: float = 1e-7,
                radius: float = RADIUS, omega: float = OMEGA,
                max_unresolved: int = 100, record_witness: bool = True) -> dict:
    """连续单元充分条件证书（复核 §2 的 ``certificate()``，本脚本独立实现）。

    对与目标圆相交的单元 C：令 ``S_C = {q : max_{v∈V(C)} |q-v| ≤ radius - margin}``，
    要求 V(C) 的每个顶点都在 conv(S_C) 内（向内余量 ``margin``）。
    通过 ⇒ C 内**每个**位置、**每个**朝向都被覆盖；否则继续四分，
    边长 ≤ ``min_side`` 仍不通过则记为一个未决单元。

    返回 ``{"ok", "checked", "passed", "unresolved", "remaining", "discarded",
    "max_depth", "truncated", "min_side", "margin", "n_points", "elapsed_s"}``；
    ``ok`` 要求 ``unresolved`` 为空、细分栈空且未因 ``max_unresolved`` 截断。
    """
    t0 = time.time()
    p = np.asarray(points, dtype=float)
    if p.ndim != 2 or p.shape[1] != 2:
        raise ValueError("points 必须是 (m, 2) 的点集")
    eff = radius - margin

    stack: list[tuple[float, float, float, int]] = [(0.0, 0.0, omega, 0)]
    checked = passed = discarded = 0
    max_depth = 0
    unresolved: list[dict] = []
    truncated = False
    while stack:
        x, y, h, depth = stack.pop()
        max_depth = max(max_depth, depth)
        # 单元到原点的最近距离 > omega ⇒ 整个单元在目标圆外，剔除（安全：圆内部分为空）
        if math.hypot(max(abs(x) - h, 0.0), max(abs(y) - h, 0.0)) > omega:
            discarded += 1
            continue
        checked += 1
        corners = np.array([[x - h, y - h], [x - h, y + h],
                            [x + h, y - h], [x + h, y + h]])
        far = np.linalg.norm(p[:, None, :] - corners[None, :, :], axis=2).max(axis=1)
        S = p[far <= eff]
        dmax = _signed_hull_max(corners, S)
        if dmax is not None and dmax <= -margin:
            passed += 1
            continue
        if 2.0 * h <= min_side:
            if record_witness:
                unresolved.append({
                    "center": [round(x, 6), round(y, 6)],
                    "side": round(2.0 * h, 9),
                    "depth": depth,
                    "n_candidate_points": int(len(S)),
                    "hull_signed_max": None if dmax is None else round(dmax, 9),
                })
            if len(unresolved) >= max_unresolved:
                truncated = True
                break
            continue
        for dx, dy in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
            stack.append((x + dx * h / 2.0, y + dy * h / 2.0, h / 2.0, depth + 1))

    ok = (not unresolved) and (not stack) and (not truncated)
    return {
        "ok": bool(ok),
        "method": "quadtree-cell-sufficient-condition",
        "checked": checked,
        "passed": passed,
        "unresolved": unresolved,
        "unresolved_count": len(unresolved),
        "remaining": len(stack),
        "discarded": discarded,
        "max_depth": max_depth,
        "truncated": truncated,
        "min_side": min_side,
        "margin": margin,
        "radius": radius,
        "omega": omega,
        "n_points": int(len(p)),
        "elapsed_s": round(time.time() - t0, 3),
    }


# --------------------------------------------------------------------------- 检查 2
def grid_worst(points, step: float, n_phi: int, *, radius: float = RADIUS,
               omega: float = OMEGA, chunk: int = 3000) -> dict:
    """单分辨率网格上的最坏正面距离（快照 ``certify``/``complete`` 的等价向量化实现）。

    ``worst = max_{x∈网格} max_{n} min{|q-x| : q 在 n 的正面半盘内}``；
    无正面点的位置记为 ``inf``（并计数）。网格**有限**，因此只是采样判据。
    """
    Q = np.asarray(points, dtype=float)
    X = _grid_points(step, omega=omega)
    U = _orientations(n_phi)
    worst = -1.0
    at = None
    n_nofront = 0
    for s in range(0, len(X), chunk):
        Xc = X[s : s + chunk]
        DX = Q[None, :, :] - Xc[:, None, :]
        dist = np.hypot(DX[..., 0], DX[..., 1])
        proj = DX @ U.T
        D = np.where(proj > 0, dist[..., None], np.inf).min(axis=1)
        kmax = D.max(axis=1)
        n_nofront += int(np.isinf(kmax).sum())
        j = int(kmax.argmax())
        if kmax[j] > worst:
            worst = float(kmax[j])
            at = [round(float(Xc[j][0]), 3), round(float(Xc[j][1]), 3),
                  round(float(U[int(D[j].argmax())][0]), 6),
                  round(float(U[int(D[j].argmax())][1]), 6)]
    return {"step": step, "n_phi": n_phi, "n_grid_points": int(len(X)),
            "worst": worst, "worst_at_xy_n": at, "n_points_without_frontal": n_nofront}


def multiresolution_check(points, *, grids=SNAPSHOT_GRIDS, safety: float = SNAPSHOT_SAFETY,
                          radius: float = RADIUS) -> dict:
    """检查 2a：快照 ``_verify_layout.py`` 的多分辨率网格证书（``worst ≤ radius-safety``）。"""
    rows = []
    for step, n_phi in grids:
        t0 = time.time()
        r = grid_worst(points, step, n_phi, radius=radius)
        r["ok"] = r["worst"] <= radius - safety
        r["elapsed_s"] = round(time.time() - t0, 3)
        rows.append(r)
    worst = max(r["worst"] for r in rows) if rows else float("inf")
    return {"ok": all(r["ok"] for r in rows), "safety": safety,
            "threshold": radius - safety, "rows": rows, "worst_over_grids": worst}


def hull_cross_check(points, *, n_samples: int = 2000, seed: int = 4242,
                     step: float = 30.0, radius: float = RADIUS,
                     omega: float = OMEGA) -> dict:
    """检查 2b：Delaunay 包含测试（方向 B：``x ∈ conv(P∩B̄(x,R))``），独立实现。

    与快照 ``conv_hull_check`` 同法（Delaunay.find_simplex < 0 ⇒ 违反），
    同样只在**有限样本**上成立。
    """
    Q = np.asarray(points, dtype=float)
    Xs = _grid_points(step, omega=omega)
    rng = np.random.default_rng(seed)
    n = min(n_samples, len(Xs))
    samp = Xs[rng.choice(len(Xs), n, replace=False)]
    bad = 0
    examples = []
    for x in samp:
        S = Q[np.hypot(*(Q - x).T) <= radius + 1e-9]
        ok = False
        if len(S) >= 3:
            try:
                ok = Delaunay(S).find_simplex(x) >= 0
            except Exception:  # noqa: BLE001
                ok = False
        if not ok:
            bad += 1
            if len(examples) < 10:
                examples.append([round(float(x[0]), 3), round(float(x[1]), 3)])
    return {"ok": bad == 0, "n_samples": int(n), "n_violations": bad,
            "violations": examples, "sample_step": step, "seed": seed}


# --------------------------------------------------------------------------- 检查 3
def brute_scan(points, *, n_phi: int = 1440, n_samp: int = 12000,
               ring_deg: float = 0.05, seed: int = 777, radius: float = RADIUS,
               omega: float = OMEGA, chunk: int = 200) -> dict:
    """检查 3：0.25° 采样扫描（快照 ``_brute_check.py`` 同参数）。

    Ω 内均匀随机样本 + 边界细角度环，朝向 0.25°，直接算正面最近距离。
    **有限样本**：``worst`` 是样本最大值，不是连续域最坏真值（见模块 docstring）。
    """
    t0 = time.time()
    Q = np.asarray(points, dtype=float)
    rng = np.random.default_rng(seed)
    r = omega * np.sqrt(rng.random(n_samp))
    th = 2 * np.pi * rng.random(n_samp)
    X = np.stack([r * np.cos(th), r * np.sin(th)], axis=1)
    ang = np.arange(0.0, 2 * math.pi, math.radians(ring_deg))
    X = np.vstack([X, np.stack([omega * np.cos(ang), omega * np.sin(ang)], axis=1)])
    U = _orientations(n_phi)
    worst = 0.0
    at = None
    n_nofront = 0
    for i in range(0, len(X), chunk):
        Xc = X[i : i + chunk]
        DX = Q[None, :, :] - Xc[:, None, :]
        dist = np.hypot(DX[..., 0], DX[..., 1])
        proj = DX @ U.T
        D = np.where(proj > 0, dist[..., None], np.inf).min(axis=1)
        kmax = D.max(axis=1)
        n_nofront += int(np.isinf(kmax).sum())
        j = int(kmax.argmax())
        if np.isfinite(kmax[j]) and kmax[j] > worst:
            worst = float(kmax[j])
            at = [round(float(Xc[j][0]), 3), round(float(Xc[j][1]), 3),
                  round(float(U[int(D[j].argmax())][0]), 6),
                  round(float(U[int(D[j].argmax())][1]), 6)]
    return {
        "ok": (n_nofront == 0 and worst <= radius),
        "worst": worst,
        "worst_at_xy_n": at,
        "n_points_without_frontal": n_nofront,
        "n_samples": int(len(X)),
        "n_random_samples": n_samp,
        "ring_deg": ring_deg,
        "n_phi": n_phi,
        "angle_resolution_deg": round(360.0 / n_phi, 4),
        "seed": seed,
        "elapsed_s": round(time.time() - t0, 3),
        "is_whole_domain_proof": False,
    }


# --------------------------------------------------------------------------- 布局
def layout_definitions() -> dict[str, dict]:
    """待验证的布局：交付的 v17（24 点）与 v9 两圈式（25 点，``two_tier_nodes``）。"""
    v17 = [(float(x), float(y)) for x, y in LAYOUT_Q4_V17]
    v9 = [(float(x), float(y)) for x, y in two_tier_nodes(950.0, 1750.0, 12, 1900.0)]
    return {
        "v17": {"name": "LAYOUT_Q4_V17（q4-v17 / q4-v18 继承）", "points": v17,
                "source": "mathmodel2026b.strategy_v17.LAYOUT_Q4_V17",
                "role": "交付发现层布局（24 点）"},
        "v9": {"name": "v9 两圈式（two_tier_nodes(950,1750,12,1900)）", "points": v9,
               "source": "mathmodel2026b.strategy_v9.two_tier_nodes",
               "role": "方法一/初始解与兜底（25 点）"},
    }


def verify_layout(key: str, info: dict, args: argparse.Namespace) -> dict:
    pts = np.asarray(info["points"], dtype=float)
    print(f"\n=== {key}：{info['name']} ===", flush=True)
    print(f"  来源: {info['source']}  点数 m={len(pts)}  "
          f"该顺序路线长度={_route_length(pts):.1f} m", flush=True)

    t0 = time.time()
    cert = certificate(pts, min_side=args.min_side, margin=args.margin,
                       max_unresolved=args.max_unresolved)
    print(f"  [检查1 连续单元充分条件] 检查单元={cert['checked']} 通过={cert['passed']} "
          f"未决={cert['unresolved_count']} 圆外剔除={cert['discarded']} "
          f"栈余={cert['remaining']} 最大深度={cert['max_depth']} "
          f"→ {'通过' if cert['ok'] else '未通过'} [{cert['elapsed_s']}s]", flush=True)
    for u in cert["unresolved"][:5]:
        print(f"      未决单元 center={u['center']} side={u['side']} "
              f"候选点={u['n_candidate_points']} hull_signed_max={u['hull_signed_max']}", flush=True)

    grids = QUICK_GRIDS if args.quick else SNAPSHOT_GRIDS
    mr = multiresolution_check(pts, grids=grids, safety=args.safety)
    for row in mr["rows"]:
        print(f"  [检查2a 网格证书] step={row['step']:.0f}, n_phi={row['n_phi']}: "
              f"worst={row['worst']:.1f}m 阈值={mr['threshold']:.0f} "
              f"{'通过' if row['ok'] else '未通过'} [{row['elapsed_s']}s]", flush=True)
    hc = hull_cross_check(pts, n_samples=args.hull_samples, step=args.hull_step)
    print(f"  [检查2b 凸包交叉验证] {hc['n_samples'] - hc['n_violations']}/{hc['n_samples']} 通过"
          f"{'' if hc['ok'] else '  ← 违反示例 ' + str(hc['violations'][:3])}", flush=True)

    bs = brute_scan(pts, n_phi=args.brute_phi, n_samp=args.brute_samples,
                    ring_deg=args.brute_ring_deg)
    print(f"  [检查3 {360.0 / args.brute_phi:.2f}° 采样扫描] worst={bs['worst']:.1f}m "
          f"无正面点样本={bs['n_points_without_frontal']}/{bs['n_samples']} "
          f"{'通过' if bs['ok'] else '未通过'} [{bs['elapsed_s']}s]", flush=True)
    print("      ⚠ 检查3 是有限样本，只能证伪不能证实；连续域结论只看检查1。", flush=True)

    ok = bool(cert["ok"] and mr["ok"] and hc["ok"] and bs["ok"])
    return {
        "name": info["name"], "source": info["source"], "role": info["role"],
        "n_points": int(len(pts)),
        "route_len_m_of_given_order": round(_route_length(pts), 1),
        "points_in_order": [[round(x, 1), round(y, 1)] for x, y in pts],
        "check1_continuous_cell_sufficient": cert,
        "check2a_multiresolution_grid": mr,
        "check2b_hull_cross_check": hc,
        "check3_brute_scan": bs,
        "all_checks_ok": ok,
        "elapsed_s": round(time.time() - t0, 3),
    }


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--layout", choices=("v17", "v9", "both"), default="both")
    ap.add_argument("--min-side", type=float, default=0.02,
                    help="四叉树最小单元边长（米，复核 audit.py 用 0.02）")
    ap.add_argument("--margin", type=float, default=1e-7,
                    help="浮点向内余量（米）：近邻点判据与凸包内点判据共用")
    ap.add_argument("--max-unresolved", type=int, default=100,
                    help="未决单元记录上限（复核 audit.py 用 100，达到即截断）")
    ap.add_argument("--safety", type=float, default=SNAPSHOT_SAFETY,
                    help="检查2a 的安全余量（米，快照 12）")
    ap.add_argument("--hull-samples", type=int, default=2000,
                    help="检查2b 的采样点数（快照 2000）")
    ap.add_argument("--hull-step", type=float, default=30.0,
                    help="检查2b 采样网格步长（米，快照 30）")
    ap.add_argument("--brute-phi", type=int, default=1440,
                    help="检查3 的朝向数（1440 = 0.25°，快照 1440）")
    ap.add_argument("--brute-samples", type=int, default=12000,
                    help="检查3 的 Ω 内随机样本数（快照 12000）")
    ap.add_argument("--brute-ring-deg", type=float, default=0.05,
                    help="检查3 的边界环角度步长（度，快照 0.05）")
    ap.add_argument("--quick", action="store_true",
                    help="冒烟：检查2a 用粗网格、采样点数与朝向数大幅缩小")
    ap.add_argument("--json", default=None, help="把完整报告写成 JSON 到该路径")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.quick:
        args.hull_samples = min(args.hull_samples, 200)
        args.brute_phi = min(args.brute_phi, 180)
        args.brute_samples = min(args.brute_samples, 400)
        args.brute_ring_deg = max(args.brute_ring_deg, 1.0)
        args.hull_step = max(args.hull_step, 60.0)

    defs = layout_definitions()
    keys = ("v17", "v9") if args.layout == "both" else (args.layout,)

    print("Q4 布局证书验证：三项检查分开报告", flush=True)
    print(f"  Ω = 半径 {OMEGA:.0f} m 的目标圆；接收半径 R = {RADIUS:.0f} m；"
          f"最小单元 {args.min_side} m；向内余量 {args.margin}", flush=True)
    print("  ⚠ 检查1 是连续域的**数值**充分条件（浮点 + 向内余量，非区间算术）；"
          "检查2/3 是有限采样，只能证伪。", flush=True)

    report: dict = {
        "generated_by": "script/verify_layout_certificate.py",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "omega_m": OMEGA, "radius_m": RADIUS,
        "quick": bool(args.quick),
        "caveats": CAVEATS,
        "layouts": {},
    }
    t0 = time.time()
    for key in keys:
        report["layouts"][key] = verify_layout(key, defs[key], args)
    report["elapsed_s"] = round(time.time() - t0, 3)
    report["all_layouts_ok"] = all(v["all_checks_ok"] for v in report["layouts"].values())

    print("\n=== 汇总 ===", flush=True)
    header = f"{'布局':<10}{'点数':>5}{'检查单元':>10}{'通过单元':>10}{'未决':>6}{'检查1':>8}{'检查2':>8}{'检查3':>8}"
    print(header)
    print("-" * len(header))
    for key, res in report["layouts"].items():
        c = res["check1_continuous_cell_sufficient"]
        print(f"{key:<10}{res['n_points']:>5}{c['checked']:>10}{c['passed']:>10}"
              f"{c['unresolved_count']:>6}"
              f"{'通过' if c['ok'] else '未通过':>8}"
              f"{'通过' if (res['check2a_multiresolution_grid']['ok'] and res['check2b_hull_cross_check']['ok']) else '未通过':>8}"
              f"{'通过' if res['check3_brute_scan']['ok'] else '未通过':>8}")
    print(f"\n总计用时 {report['elapsed_s']}s；"
          f"全部通过={report['all_layouts_ok']}")

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"JSON: {p}")
    return 0 if report["all_layouts_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
