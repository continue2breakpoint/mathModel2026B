# -*- coding: utf-8 -*-
"""2026 CUMCM B题 问题1：交会定位区域直径计算与“以直径为直径的圆”覆盖判定。

数学内核（纯标准库实现，便于写入支撑材料）：
  1) 误差楔形 W_i = {P: theta_i-1° <= arg(P-S_i) <= theta_i+1°}，等价于两个有向半平面之交；
  2) 定位区域 Π = ⋂W_i：Sutherland–Hodgman 半平面裁剪凸多边形（O(n·m)）；
  3) 区域直径 D = max ||a-b||（凸多边形最远点必在顶点，枚举顶点对）；
  4) “以直径为直径的圆” C* 覆盖判定：Thales 定理，顶点 v 在 C* 内 <=> <v-A, v-B> <= 0；
  5) 若不覆盖：Welzl 随机增量求最小覆盖圆 (o, r_meb)，r_meb >= D/2。

蒙特卡洛验证（numpy）：随机布源 + 注入 ±1° 有界误差，检验
  真源包含率（应≈100%）、直径圆覆盖率的分布、r_meb/D 比值、覆盖判定与越界顶点一致性。

用法：python 问题1_求解.py --cases 2000 --seed 42
产物：results/问题1_结果.csv、results/q1_examples.json、控制台摘要。
"""
import argparse
import json
import math
import os
import random
import sys

try:
    import numpy as np
    HAVE_NP = True
except Exception:
    HAVE_NP = False

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(PROJECT_ROOT, "results")
EPS = 1e-9


# ---------------------------------------------------------------- 几何内核
def halfplanes_from_observation(sx, sy, theta):
    """示向度 theta(度,[0,360)) -> 两个半平面 (a,b,c)，内部满足 a*x+b*y+c >= 0。

    楔形 = 下界射线 alpha=theta-1 的逆时针侧 ∩ 上界射线 beta=theta+1 的顺时针侧。
    """
    a1 = math.radians(theta - 1.0)
    b1 = math.radians(theta + 1.0)
    ux1, uy1 = math.cos(a1), math.sin(a1)   # 下界射线方向
    ux2, uy2 = math.cos(b1), math.sin(b1)   # 上界射线方向
    # cross(u1, P-S) >= 0  =>  a*x+b*y+c >= 0
    hp_low = (-uy1, ux1, uy1 * sx - ux1 * sy)
    # cross(u2, P-S) <= 0  =>  a*x+b*y+c >= 0
    hp_up = (uy2, -ux2, -uy2 * sx + ux2 * sy)
    return [hp_low, hp_up]


def clip_poly(poly, hp):
    """Sutherland–Hodgman：凸多边形对半平面裁剪，返回新顶点列表。"""
    a, b, c = hp
    out = []
    n = len(poly)
    if n == 0:
        return out
    for i in range(n):
        cur = poly[i]
        nxt = poly[(i + 1) % n]
        cur_in = a * cur[0] + b * cur[1] + c >= -EPS
        nxt_in = a * nxt[0] + b * nxt[1] + c >= -EPS
        if cur_in:
            out.append(cur)
        if cur_in != nxt_in:
            denom = a * (nxt[0] - cur[0]) + b * (nxt[1] - cur[1])
            if abs(denom) < 1e-12:
                continue
            t = -(a * cur[0] + b * cur[1] + c) / denom
            t = min(1.0, max(0.0, t))
            out.append((cur[0] + t * (nxt[0] - cur[0]), cur[1] + t * (nxt[1] - cur[1])))
    # 去重（数值容差内）
    dedup = []
    for p in out:
        if not dedup or math.hypot(p[0] - dedup[-1][0], p[1] - dedup[-1][1]) > 1e-6:
            dedup.append(p)
    return dedup


def localization_polygon(obs, bbox=3500.0):
    """obs: [(sx, sy, theta_deg), ...] -> 定位区域凸多边形顶点（有序）或退化。"""
    poly = [(-bbox, -bbox), (bbox, -bbox), (bbox, bbox), (-bbox, bbox)]
    for (sx, sy, th) in obs:
        for hp in halfplanes_from_observation(sx, sy, th):
            poly = clip_poly(poly, hp)
            if len(poly) < 3:
                return poly
    return poly


def polygon_diameter(poly):
    """凸多边形直径：最远顶点对（O(m^2) 枚举，m 很小足够）。返回 (D, A, B)。"""
    best = 0.0
    A = B = poly[0]
    m = len(poly)
    for i in range(m):
        for j in range(i + 1, m):
            d = math.hypot(poly[i][0] - poly[j][0], poly[i][1] - poly[j][1])
            if d > best:
                best = d
                A, B = poly[i], poly[j]
    return best, A, B


def polygon_diameter_calipers(poly):
    """凸多边形直径的旋转卡壳（Rotating Calipers）算法，复杂度 O(m)。

    凸集直径必由一对对踵点取得；双指针沿边界同步推进，每条边只与其对踵点
    比较距离。要求顶点按顺序排列（内部自动校正为逆时针 CCW）。
    返回 (D, A, B)，与 polygon_diameter 的枚举结果交叉验证。
    """
    P = [(float(v[0]), float(v[1])) for v in poly]
    n = len(P)
    if n == 1:
        return 0.0, P[0], P[0]
    # 校正为逆时针（signed area > 0）
    area2 = sum(P[i][0] * P[(i + 1) % n][1] - P[(i + 1) % n][0] * P[i][1]
                for i in range(n))
    if area2 < 0:
        P = P[::-1]
    if n == 2:
        return math.hypot(P[0][0] - P[1][0], P[0][1] - P[1][1]), P[0], P[1]

    def cross_edge(edge, i, k):
        # edge=(ex,ey) 为边 i->i+1 方向，cross(edge, P[k]-P[i])
        return edge[0] * (P[k][1] - P[i][1]) - edge[1] * (P[k][0] - P[i][0])

    best = -1.0
    A = B = P[0]
    j = 1
    for i in range(n):
        ni = (i + 1) % n
        edge = (P[ni][0] - P[i][0], P[ni][1] - P[i][1])
        # 推进对踵点 j，直到叉积不再增大
        while True:
            nj = (j + 1) % n
            if cross_edge(edge, i, nj) > cross_edge(edge, i, j) + 1e-9:
                j = nj
            else:
                break
        # 直径候选：(i,j)、(ni,j)、(i,nj)
        for (a, b) in ((i, j), (ni, j), (i, (j + 1) % n)):
            d = math.hypot(P[a][0] - P[b][0], P[a][1] - P[b][1])
            if d > best:
                best, A, B = d, P[a], P[b]
    return best, A, B


def thales_cover_check(poly, A, B):
    """以 AB 为直径的圆 C* 是否覆盖全部顶点（Thales：<v-A,v-B> <= 0）。
    返回 (covers, worst_vertex, worst_dot)。"""
    worst = None
    worst_dot = -1e18
    for v in poly:
        dot = (v[0] - A[0]) * (v[0] - B[0]) + (v[1] - A[1]) * (v[1] - B[1])
        if dot > worst_dot:
            worst_dot = dot
            worst = v
    return worst_dot <= EPS, worst, worst_dot


# ------------------------------------------------------------- 最小覆盖圆(Welzl)
def _circle_2(p, q):
    return ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2,
            math.hypot(p[0] - q[0], p[1] - q[1]) / 2)


def _circle_3(p, q, r):
    d = 2 * (p[0] * (q[1] - r[1]) + q[0] * (r[1] - p[1]) + r[0] * (p[1] - q[1]))
    if abs(d) < 1e-12:
        return None
    px, py, qx, qy, rx, ry = p[0], p[1], q[0], q[1], r[0], r[1]
    ux = (px * px + py * py) * (qy - ry) + (qx * qx + qy * qy) * (ry - py) + (rx * rx + ry * ry) * (py - qy)
    uy = (px * px + py * py) * (rx - qx) + (qx * qx + qy * qy) * (px - rx) + (rx * rx + ry * ry) * (qx - px)
    cx, cy = ux / d, uy / d
    return (cx, cy, math.hypot(cx - px, cy - py))


def _in_circle(c, p):
    return math.hypot(p[0] - c[0], p[1] - c[1]) <= c[2] + 1e-7


def _mec(pts, r):
    if len(pts) == 0 or len(r) == 3:
        if len(r) == 0:
            return (0.0, 0.0, 0.0)
        if len(r) == 1:
            return (r[0][0], r[0][1], 0.0)
        if len(r) == 2:
            return _circle_2(r[0], r[1])
        c3 = _circle_3(r[0], r[1], r[2])
        if c3 is not None:
            return c3
        # 三点共线退化：取最远两点为直径
        c12 = _circle_2(r[0], r[1])
        c23 = _circle_2(r[1], r[2])
        c13 = _circle_2(r[0], r[2])
        return max([c12, c23, c13], key=lambda c: c[2])
    c = _mec(pts[1:], r)
    if _in_circle(c, pts[0]):
        return c
    return _mec(pts[1:], r + [pts[0]])


def welzl_min_enclosing_circle(points):
    """Welzl 随机增量，期望 O(n)。输入顶点列表，返回 (ox, oy, r)。"""
    pts = points[:]
    random.shuffle(pts)
    c = _mec(pts, [])
    if c[2] == 0.0 and len(points) == 1:
        return c
    return c


# ------------------------------------------------- Jung 定理与清除判据
JUNG_BOUND = 1.0 / math.sqrt(3.0)   # 平面点集 MEC 半径上界 R <= D/√3（正三角形取等）
CLEAR_RADIUS = 20.0                 # /clear 距真实干扰源 ≤20 m 必成功


def jung_check(D, r_meb, tol=1e-6):
    """Jung 定理：D/2 <= r_meb <= D/√3。返回 (满足下界, 满足上界)。"""
    lo = r_meb >= D / 2.0 - tol
    hi = r_meb <= JUNG_BOUND * D + tol
    return lo, hi


def clearance_guarantee(mec):
    """清除保证判据：定位区域 Π 被其最小覆盖圆 MEC=(C,r) 覆盖，真实源 G∈Π，
    故 |G-C|≤r。当 r≤20 m 时机器狗走到圆心 C 调用 /clear 必成功（题面 ≤20 m）。
    返回 (是否保证, 圆心, 半径)。"""
    cx, cy, r = mec
    return (r <= CLEAR_RADIUS + 1e-9), (cx, cy), r


# ----------------------------------------------------------------- 验证工具
def in_wedge(p, sx, sy, theta):
    ang = math.degrees(math.atan2(p[1] - sy, p[0] - sx)) % 360.0
    d = (ang - theta) % 360.0
    if d > 180.0:
        d -= 360.0
    return abs(d) <= 1.0 + 1e-7


def source_in_region(G, obs):
    return all(in_wedge(G, sx, sy, th) for (sx, sy, th) in obs)


def point_in_convex_poly(p, poly):
    """凸多边形（有序顶点）内的快速判定（半平面法）。"""
    m = len(poly)
    if m < 3:
        return False
    sign = None
    for i in range(m):
        a = poly[i]
        b = poly[(i + 1) % m]
        cr = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
        if abs(cr) < 1e-9:
            continue
        s = 1 if cr > 0 else -1
        if sign is None:
            sign = s
        elif s != sign:
            return False
    return True


def run_montecarlo(n_cases, seed, min_pts=2, max_pts=4):
    rng = np.random.default_rng(seed)
    rows = []
    examples = []
    for _ in range(n_cases):
        n = int(rng.integers(min_pts, max_pts + 1))
        # 源：Ω 内均匀
        while True:
            gx, gy = rng.uniform(-1800, 1800, 2)
            if gx * gx + gy * gy <= 1800 * 1800:
                break
        G = (float(gx), float(gy))
        # 检测点：以 G 为中心，随机方向、距离 200–1400，避免过近/过远
        S = []
        for _i in range(n):
            ang = rng.uniform(0, 360)
            dist = rng.uniform(200, 1400)
            sx = G[0] + dist * math.cos(math.radians(ang))
            sy = G[1] + dist * math.sin(math.radians(ang))
            S.append((float(sx), float(sy)))
        # 示向度 = 真实方位 + 有界误差
        obs = []
        for (sx, sy) in S:
            phi = math.degrees(math.atan2(G[1] - sy, G[0] - sx)) % 360.0
            eps = float(rng.uniform(-1.0, 1.0))
            th = (phi + eps) % 360.0
            obs.append((sx, sy, th))
        poly = localization_polygon(obs)
        if len(poly) < 3:
            rows.append(dict(n=n, gx=G[0], gy=G[1], D=0.0, covers="degenerate",
                             r_meb=0.0, ratio=0.0, inside=False))
            continue
        D, A, B = polygon_diameter(poly)
        # 旋转卡壳交叉验证（O(m) 与 O(m^2) 结果必须一致）
        Dc, _, _ = polygon_diameter_calipers(poly)
        calipers_ok = abs(Dc - D) <= 1e-6 * max(1.0, D)
        covers, worst, worst_dot = thales_cover_check(poly, A, B)
        ox, oy, r = welzl_min_enclosing_circle(poly)
        inside = source_in_region(G, obs) or point_in_convex_poly(G, poly)
        ratio = (r / D) if D > 0 else 1.0
        _, jung_hi = jung_check(D, r)
        rows.append(dict(n=n, gx=G[0], gy=G[1], D=D, covers=bool(covers),
                         r_meb=r, ratio=ratio, inside=bool(inside),
                         calipers_ok=bool(calipers_ok), jung_hi=bool(jung_hi)))
        if len(examples) < 8 and not covers:
            examples.append(dict(n=n, G=G, S=S, obs=obs, poly=poly, D=D, A=A, B=B,
                                 r_meb=(ox, oy, r), covers=False, worst=worst))
        elif len(examples) < 12 and len([e for e in examples if e["covers"]]) < 4:
            # 尽量包含覆盖的正例（覆盖判定统计见下，示例集保持代表性）
            pass
    return rows, examples


def summarize(rows):
    if not rows:
        return None
    valid = [r for r in rows if r["D"] > 0]
    deg = [r for r in rows if r["D"] == 0]
    g_inside = sum(1 for r in rows if r["inside"]) / len(rows)
    cov_rate = sum(1 for r in valid if r["covers"]) / len(valid) if valid else 0.0
    ratios = sorted(r["ratio"] for r in valid)
    Ds = sorted(r["D"] for r in valid)
    def q(a, p):
        if not a:
            return 0.0
        k = (len(a) - 1) * p
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return a[int(k)]
        return a[f] * (c - k) + a[c] * (k - f)
    return dict(
        total=len(rows), degenerate=len(deg), inside_rate=g_inside,
        cover_rate=cov_rate,
        ratio_median=q(ratios, 0.5), ratio_min=ratios[0], ratio_max=ratios[-1],
        D_median=q(Ds, 0.5), D_p95=q(Ds, 0.95),
        calipers_match=sum(1 for r in valid if r.get("calipers_ok", True)) / len(valid) if valid else 1.0,
        jung_hold=sum(1 for r in valid if r.get("jung_hi", True)) / len(valid) if valid else 1.0,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out-csv", default=os.path.join(RESULTS_DIR, "问题1_结果.csv"))
    ap.add_argument("--out-json", default=os.path.join(RESULTS_DIR, "q1_examples.json"))
    ap.add_argument("--out-detail", default=os.path.join(RESULTS_DIR, "问题1_逐案例.csv"))
    args = ap.parse_args()

    if not HAVE_NP:
        print("[FATAL] 需要 numpy：pip install numpy")
        sys.exit(1)
    random.seed(args.seed)
    os.makedirs(RESULTS_DIR, exist_ok=True)

    print("== 问题1 蒙特卡洛验证（合成真值，仅供自测）==", flush=True)
    print("  案例数=%d, 每案例检测点数∈[2,4], 误差∈[-1,1]°, 种子=%d" % (args.cases, args.seed), flush=True)
    rows, examples = run_montecarlo(args.cases, args.seed)

    # 按 n 分组统计
    import collections
    by_n = collections.defaultdict(list)
    for r in rows:
        by_n[r["n"]].append(r)
    header = ["n", "cases", "inside_rate", "cover_rate", "ratio_median", "D_median",
              "calipers_match", "jung_hold"]
    lines = [",".join(header)]
    for n in sorted(by_n):
        s = summarize(by_n[n])
        lines.append("%d,%d,%.4f,%.4f,%.4f,%.1f,%.4f,%.4f" % (
            n, s["total"], s["inside_rate"], s["cover_rate"], s["ratio_median"],
            s["D_median"], s["calipers_match"], s["jung_hold"]))
    s_all = summarize(rows)
    lines.append("all,%d,%.4f,%.4f,%.4f,%.1f,%.4f,%.4f" % (
        s_all["total"], s_all["inside_rate"], s_all["cover_rate"],
        s_all["ratio_median"], s_all["D_median"],
        s_all["calipers_match"], s_all["jung_hold"]))
    with open(args.out_csv, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lines) + "\n")

    # 逐案例明细（论文复现与出图使用）
    import csv as _csv
    detail_path = args.out_detail
    with open(detail_path, "w", encoding="utf-8-sig", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["case", "n", "gx", "gy", "D", "covers", "r_meb", "ratio", "inside"])
        for i, r in enumerate(rows):
            w.writerow([i + 1, r["n"], "%.3f" % r["gx"], "%.3f" % r["gy"],
                        "%.3f" % r["D"], int(r["covers"]), "%.3f" % r["r_meb"],
                        "%.6f" % r["ratio"], int(r["inside"])])

    # 示例案例（用于出图与论文）
    # 补充 4 个覆盖的正例：从 valid & covers 中取
    cov_ex = []
    for r in rows:
        if r["D"] > 0 and r["covers"] and len(cov_ex) < 4:
            cov_ex.append(r)
    # 重建覆盖正例的几何（重新生成太贵：直接从 rows 不可得 poly，此处标记由出图脚本重建）
    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump({"examples": examples, "cover_row_indices": [rows.index(e) for e in cov_ex]},
                  f, ensure_ascii=False, indent=1, default=str)

    print("  真源包含率: %.4f（应≈1）" % s_all["inside_rate"], flush=True)
    print("  直径圆覆盖率: %.4f" % s_all["cover_rate"], flush=True)
    print("  退化案例数: %d" % s_all["degenerate"], flush=True)
    print("  r_meb/D 中位数: %.4f  范围 [%.4f, %.4f]（Jung 上界 1/√3=%.4f）" % (
        s_all["ratio_median"], s_all["ratio_min"], s_all["ratio_max"], JUNG_BOUND), flush=True)
    print("  旋转卡壳 O(m) 与枚举 O(m²) 直径一致率: %.4f" % s_all["calipers_match"], flush=True)
    print("  Jung 上界 r_meb≤D/√3 满足率: %.4f" % s_all["jung_hold"], flush=True)
    print("  D 中位数: %.1f m   P95: %.1f m" % (s_all["D_median"], s_all["D_p95"]), flush=True)
    print("  结果CSV: %s" % args.out_csv, flush=True)
    print("  示例JSON: %s" % args.out_json, flush=True)


if __name__ == "__main__":
    main()
