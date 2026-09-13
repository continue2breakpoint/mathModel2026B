#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Q4 发现层【方法二】：检测点坐标 q_i 与访问顺序 pi 的联合优化（离线，重活）。

这个脚本为什么存在
------------------
``framework/src/mathmodel2026b/strategy_v17.py`` 里的 ``LAYOUT_Q4_V17``（24 点，
离线路线 18507.5m）是**本脚本产出的**，原先只以 ``_joint_opt_q4.py`` 的形式存在于
2026-09-13 的 Windows 最终工程快照顶层：无法从框架仓库复跑，也没有入口重新出证书。
移植进来以后，"跑布局 → 出交付片段 → 出独立证书"三步都能在仓库内完成。

完备性证书（局部凸包判据，等价形式）::

    对任意 x∈Ω 与任意朝向 n∈S^1，存在检测点 q 使 |q-x|<=1000 且 n·(q-x)>0
    ⟺ x ∈ conv(P ∩ B̄(x,1000))。

本脚本实现的是**采样版**判据：``worst_frontal = max_{x,n} min_{front} |q-x|``，
在有限网格 × 有限朝向上取最大，``<=1000`` 即在该采样上完备；另有
:func:`conv_hull_check` 用 Delaunay 包含测试做方向 B 的独立交叉验证。

.. warning::
   **采样通过 ≠ 连续域完备**。``certify`` 的网格、``conv_hull_check`` 的采样点、
   ``brute_worst`` 的 0.25° 扫描都是**有限样本**：``989.8 m`` 只是样本上的最大值，
   不能当作连续域最坏真值（快照把它写成"近精确判据"，2026-09-13 复核指出该表述不成立）。
   连续域可证明的充分条件在 :mod:`script.verify_layout_certificate`
   （四叉树单元细分，要求 ``unresolved == 0``），本脚本用 ``--continuous-cert`` 调它。

交替迭代（每轮结束细网格重新校验证书，不通过则整体回退）：

A. 固定 pi：**割平面 + 罚函数 SCP** 微调坐标。
   子问题 = L-BFGS-B 解带罚目标（解析梯度）::

       min Σ|段长| + μ Σ_cut [ max(0,|q_j-x|-(R-safety))² + max(0,margin-n·(q_j-x))² ]
       s.t. 每点移动 <= trust（信赖域边界）

   罚函数相对硬约束 SLSQP 的优势：不存在"不可行"——任何违反只产生二次罚项，
   梯度永远存在；μ 逐轮递增逼近硬约束解（经典罚函数延续）。
   种子割 = 粗网格上正面距离 >= seed_thresh 的 (x,n)；
   解后全量校验，把仍被违反的 (x,n)（witness 重指派）加为新割再解，
   直到证书通过（割平面自愈：任何朝向的 witness 翻背都会被下一轮割回来）。
   （v17 顶部注释仍写着 SLSQP，是历史文字；实际实现是这里的 soft-min 罚函数 + L-BFGS-B。）

B. 固定 q：2-opt + or-opt 调整 pi（开放路径，起点固定原点）。

C. 删除-修复步：删点后用同一 SCP 机制补洞（被违反 (x,n) 的 witness 约束
   天然产生"朝 x 移动"的修复力），修复后证书仍完备且 L+λm 改善才接受。

方法一（v9 的 25 点两圈式，行程 18828m）作初始解与兜底。

用法::

    # 复现交付布局（默认 10 轮，最多 2400s；**很慢**，不要轻易全跑）
    python3 script/q4/joint_opt.py

    # 冒烟：不做迭代，只对基线出多分辨率证书 + 交叉验证
    python3 script/q4/joint_opt.py --quick

    # 只跑一轮删点-修复（最省的"真迭代"路径）
    python3 script/q4/joint_opt.py --max-iter 1 --max-candidates 1 --time-budget 600 --no-brute

    # 交付格式：打印可直接粘贴进 strategy_v17.py 的片段（带表头注释）
    python3 script/q4/joint_opt.py --emit
    python3 script/q4/joint_opt.py --in logs/q4/joint_opt/layout.json --emit --emit-out /tmp/frag.py

输出（默认 ``logs/q4/joint_opt/``）：``layout.json``（``{"m","route_len_m","points_in_order"}``）、
``layout_fragment.py``（可粘贴的 ``LAYOUT_Q4_V17`` 片段）、``joint_opt.txt``（全过程文字日志）。

与 ``q4-v18`` 的关系
--------------------
``mathmodel2026b.versioned`` 里的 **q4-v18**（最终交付）从 **q4-v17** **继承**
``LAYOUT_Q4_V17``：v18 只加知识矩阵负例与覆盖式清除兜底，**不改发现层**。
所以本脚本产出的布局片段同时服务 q4-v17 与 q4-v18；动它就要重出连续域证书。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.spatial import Delaunay

SCRIPT_DIR = Path(__file__).resolve().parent            # mathModel2026B/script/q4
REPO_ROOT = SCRIPT_DIR.parent.parent                    # mathModel2026B
sys.path.insert(0, str(REPO_ROOT / "framework" / "src"))
sys.path.insert(0, str(SCRIPT_DIR))

from mathmodel2026b.strategy_v9 import two_tier_nodes  # noqa: E402

try:  # 包内运行：python3 -m script.q4.joint_opt
    from . import layout_io  # noqa: PLC0415
except ImportError:  # 单文件直跑：python3 script/q4/joint_opt.py
    import layout_io  # type: ignore[no-redef]

OMEGA = 1800.0
R = 1000.0
START = np.zeros(2)

#: 交付期的细网格安全余量：证书要求 worst <= R - safety。
FINE_SAFETY = 12.0

#: 多分辨率证书网格（与快照一致）：单网格收敛可能在另一分辨率留洞。
CERT_GRIDS_FULL: tuple[tuple[float, int], ...] = ((60.0, 36), (40.0, 60), (25.0, 90))
CERT_GRIDS_COARSE: tuple[tuple[float, int], ...] = ((120.0, 24), (60.0, 36))

#: 默认输出目录（离线产物不进 framework/，也不进快照目录）。
DEFAULT_OUT_DIR = layout_io.LOG_ROOT / "joint_opt"

out: list[str] = []
LAST_SOLVE: dict = {}          # 最近一次 _solve_pen 的求解器状态（诊断用）


def emit(s: str = "") -> None:
    out.append(s)
    print(s, flush=True)


# ---------------------------------------------------------------- 证书函数
def grid_points(step: float, ring_deg: float = 0.5) -> np.ndarray:
    """Ω 内笛卡尔网格 + 边界圆环（贴边朝外的源是最坏情形集中地）。"""
    xs = np.arange(-OMEGA, OMEGA + 1e-9, step)
    gx, gy = np.meshgrid(xs, xs)
    gx, gy = gx.ravel(), gy.ravel()
    m = np.hypot(gx, gy) <= OMEGA + 1e-9
    inner = np.stack([gx[m], gy[m]], axis=1)
    ang = np.arange(0.0, 2 * math.pi, math.radians(ring_deg))
    ring = np.stack([OMEGA * np.cos(ang), OMEGA * np.sin(ang)], axis=1)
    return np.vstack([inner, ring])


def orientations(n_phi: int) -> np.ndarray:
    a = np.arange(n_phi) * (2 * math.pi / n_phi)
    return np.stack([np.cos(a), np.sin(a)], axis=1)


def frontal_scan(points, step: float, n_phi: int, viol_safety: float | None = None,
                 viol_limit: int = 400, chunk: int = 4000) -> dict:
    """一次扫描：worst / 每点最坏朝向 witness /（可选）被违反的 (x,n) 清单。

    viol_safety 给定时，返回正面距离 > R-viol_safety 的 (x,n) 中最坏的
    viol_limit 个：``[(x, n, witness_j, D, nearest_j), ...]``。

    witness = 最近正面点；nearest = 最近点（不限正面）——当正面 witness
    被其他割冲突卡死时，可把割改指派给 nearest（锥+线性约束把它拉进
    正面半盘），这是交替指派的第二条退路。无正面点时两者同为最近点。

    注意 ``proj > 0`` 是**严格正面**（题面闭半圆盘等价于 ``>=0``）；这里用严格版
    （相当于给朝向留一点安全裕量），不要与题面的闭半圆判据混写成同一个式子。
    """
    Q = np.asarray(points, dtype=float)
    X = grid_points(step)
    U = orientations(n_phi)
    K = len(X)
    witness = np.full(K, -1, dtype=int)
    nvec = np.zeros((K, 2))
    dmax = np.zeros(K)
    worst = -1.0
    w_at = None
    viols: list[tuple[np.ndarray, np.ndarray, int, float, int]] = []
    for s in range(0, K, chunk):
        Xc = X[s : s + chunk]
        P = Q[None, :, :] - Xc[:, None, :]            # (c,m,2)
        dist = np.hypot(P[..., 0], P[..., 1])          # (c,m)
        proj = P @ U.T                                 # (c,m,n)
        A = np.where(proj > 0, dist[..., None], np.inf)
        D = A.min(axis=1)                              # (c,n)
        Widx = A.argmin(axis=1)                        # (c,n)
        nearest = dist.argmin(axis=1)                  # (c,)
        nofront = ~np.isfinite(D)
        if nofront.any():
            Widx[nofront] = np.broadcast_to(nearest[:, None], Widx.shape)[nofront]
        kmax = D.max(axis=1)
        nmax = D.argmax(axis=1)
        cc = np.arange(len(Xc))
        witness[s : s + chunk] = Widx[cc, nmax]
        nvec[s : s + chunk] = U[nmax]
        dmax[s : s + chunk] = kmax
        loc = kmax.max()
        if loc > worst:
            worst = loc
            ci = kmax.argmax()
            w_at = (Xc[ci].copy(), U[nmax[ci]].copy())
        if viol_safety is not None:
            vm = D > R - viol_safety                   # (c,n)
            if vm.any():
                cis, nis = np.nonzero(vm)
                for a_, b_ in zip(cis, nis):
                    viols.append((Xc[a_], U[b_], int(Widx[a_, b_]),
                                  float(D[a_, b_]), int(nearest[a_])))
    if viol_safety is not None and len(viols) > viol_limit:
        viols.sort(key=lambda v: -v[3])
        viols = viols[:viol_limit]
    return {"worst": worst, "w_at": w_at, "X": X, "nvec": nvec,
            "witness": witness, "dmax": dmax, "viols": viols}


def certify(points, step: float = 50.0, n_phi: int = 72):
    """单分辨率证书扫描：返回 ``(worst, X, nvec, witness, dmax, w_at)``。"""
    d = frontal_scan(points, step, n_phi)
    return d["worst"], d["X"], d["nvec"], d["witness"], d["dmax"], d["w_at"]


def violations(points, step: float, n_phi: int, safety: float, limit: int = 400):
    d = frontal_scan(points, step, n_phi, viol_safety=safety, viol_limit=limit)
    return d["viols"]


def complete(points, step: float, n_phi: int, safety: float) -> tuple[bool, float]:
    d = frontal_scan(points, step, n_phi)
    return d["worst"] <= R - safety, d["worst"]


def conv_hull_check(points, x_samples: np.ndarray) -> tuple[int, int]:
    """方向 B 交叉验证：``x ∈ conv(P∩B̄(x,R))``（Delaunay 包含测试，独立实现）。

    只对**给定的有限样本** ``x_samples`` 成立；样本数写在返回值里，别把它读成全域结论。
    """
    Q = np.asarray(points, dtype=float)
    bad = 0
    for x in x_samples:
        S = Q[np.hypot(*(Q - x).T) <= R + 1e-9]
        if len(S) < 3:
            bad += 1
            continue
        try:
            if Delaunay(S).find_simplex(x) < 0:
                bad += 1
        except Exception:  # noqa: BLE001
            bad += 1
    return bad, len(x_samples)


# ---------------------------------------------------------------- 路线
def route_len(order, Q) -> float:
    Q = np.asarray(Q, dtype=float)
    pts = [START] + [Q[i] for i in order]
    return float(sum(math.hypot(*(pts[k + 1] - pts[k])) for k in range(len(pts) - 1)))


def nn_order(Q, start=START) -> list[int]:
    Q = np.asarray(Q, dtype=float)
    rem = list(range(len(Q)))
    cur, order = start, []
    while rem:
        d = [math.hypot(Q[i][0] - cur[0], Q[i][1] - cur[1]) for i in rem]
        j = int(np.argmin(d))
        cur = Q[rem[j]]
        order.append(rem.pop(j))
    return order


def two_opt(order, Q, passes: int = 60) -> list[int]:
    Q = np.asarray(Q, dtype=float)
    n = len(order)
    for _ in range(passes):
        imp = False
        for i in range(n - 1):
            a = START if i == 0 else Q[order[i - 1]]
            b = Q[order[i]]
            for j in range(i + 1, n):
                c = Q[order[j]]
                d = Q[order[j + 1]] if j + 1 < n else None
                delta = math.hypot(*(a - c)) - math.hypot(*(a - b))
                if d is not None:
                    delta += math.hypot(*(b - d)) - math.hypot(*(c - d))
                if delta < -1e-9:
                    order[i : j + 1] = order[i : j + 1][::-1]
                    imp = True
        if not imp:
            break
    return order


def or_opt(order, Q, passes: int = 4) -> list[int]:
    Q = np.asarray(Q, dtype=float)
    for _ in range(passes):
        imp = False
        n = len(order)
        for seg in (1, 2, 3):
            for i in range(n - seg + 1):
                cur = order[:]
                s = cur[i : i + seg]
                rest = cur[:i] + cur[i + seg :]
                best = None
                for k in range(len(rest) + 1):
                    cand = rest[:k] + s + rest[k:]
                    L = route_len(cand, Q)
                    if best is None or L < best[0]:
                        best = (L, cand)
                if best[0] < route_len(cur, Q) - 1e-9:
                    order = best[1]
                    imp = True
        if not imp:
            break
    return order


def best_order(Q) -> list[int]:
    Q = np.asarray(Q, dtype=float)
    return or_opt(two_opt(nn_order(Q), Q), Q)


# ---------------------------------------------------------------- 割平面 SCP 坐标步
def _solve_pen(Q: np.ndarray, order: list[int], cuts, trust: float, mu: float,
               safety: float, margin: float = 0.0, tau: float = 150.0,
               buffer: float = 6.0, kappa: float = 0.3, delta: float = 900.0,
               x0: np.ndarray | None = None, route_weight: float = 1.0) -> np.ndarray:
    """cuts = [(x, n)]：soft-min 罚函数坐标子问题（L-BFGS-B + 信赖域边界）。

    每个割 (x,n) 的约束是"∃j: |q_j-x|<=Rcap ∧ n·(q_j-x)>=margin"（OR 结构）。
    罚项 = μ · softmin_j h(q_j)，其中::

        h_j = max(0,|q_j-x|-(R-safety-buffer))² + max(0,margin-n·(q_j-x))²

    soft-min 用 log-sum-exp 光滑化（τ 退火控制尖锐度；kappa 缩尺其正偏差
    残差——残差是"把候选点拉近铰链制造平手"的吸力，必须压小），指派由
    求解器隐式完成。**割集由 :func:`scp_step` 做活动集管理：只有违反/近绑定的割
    进入目标**——余量充足的割不带电（零力零偏差），吸力偏差只作用于
    本来就在铰链附近的割上。

    buffer 让罚目标低于证书阈值若干米，给收敛容差留余量。
    梯度与目标严格一致（曾试 δ-平顶胖次梯度只改梯度不改值，L-BFGS-B
    线性搜索立刻异常终止，弃用）。
    """
    m = len(Q)
    if not cuts:
        XA = NA = np.zeros((0, 2))
    else:
        XA = np.array([c[0] for c in cuts], dtype=float)  # (A,2)
        NA = np.array([c[1] for c in cuts], dtype=float)  # (A,2)
    Rcap = R - safety - buffer

    def h_terms(V: np.ndarray):
        DX = V[None, :, :] - XA[:, None, :]               # (A,m,2)
        dist = np.hypot(DX[..., 0], DX[..., 1])           # (A,m)
        ex = np.maximum(0.0, dist - Rcap)
        ndot = NA[:, 0:1] * DX[..., 0] + NA[:, 1:2] * DX[..., 1]
        ef = np.maximum(0.0, margin - ndot)
        return dist, ex, ef

    def obj(flat: np.ndarray) -> float:
        V = flat.reshape(m, 2)
        pts = np.vstack([START, V[order]])
        d = pts[1:] - pts[:-1]
        seg = np.maximum(np.hypot(d[:, 0], d[:, 1]), 1.0)
        Lv = float(np.sum(seg))
        if len(XA) == 0:
            return route_weight * Lv
        _, ex, ef = h_terms(V)
        h = ex * ex + ef * ef                             # (A,m)
        hmin = h.min(axis=1)
        s = np.exp(-(h - hmin[:, None]) / tau)
        soft = hmin - kappa * tau * np.log(s.mean(axis=1))
        return route_weight * Lv + mu * float(np.sum(soft))

    def jac(flat: np.ndarray) -> np.ndarray:
        V = flat.reshape(m, 2)
        pts = np.vstack([START, V[order]])
        d = pts[1:] - pts[:-1]
        seg = np.maximum(np.hypot(d[:, 0], d[:, 1]), 1.0)
        g = np.zeros((m + 1, 2))
        g[:-1] -= d / seg[:, None]
        g[1:] += d / seg[:, None]
        gg = np.zeros((m, 2))
        for k, o in enumerate(order):
            gg[o] += g[k + 1]
        if len(XA) == 0:
            return (route_weight * gg).ravel()
        dist, ex, ef = h_terms(V)
        DX = V[None, :, :] - XA[:, None, :]               # (A,m,2)
        ndot = NA[:, 0:1] * DX[..., 0] + NA[:, 1:2] * DX[..., 1]
        h = ex * ex + ef * ef
        hmin = h.min(axis=1)
        s = np.exp(-(h - hmin[:, None]) / tau)
        # w = d(soft)/dh_j = [j==argmin]（hmin 主项，全重）+ κ·s_j/Σs（残差项）
        onehot = np.zeros_like(h)
        onehot[np.arange(len(h)), h.argmin(axis=1)] = 1.0
        w = onehot + kappa * s / s.sum(axis=1, keepdims=True)  # (A,m)
        sfd = np.where(dist > 1e-9, dist, 1.0)
        pj = np.stack([2.0 * ex * DX[..., 0] / sfd - 2.0 * ef * NA[:, 0:1],
                       2.0 * ex * DX[..., 1] / sfd - 2.0 * ef * NA[:, 1:2]],
                      axis=2)                             # (A,m,2) ∂h/∂q_j
        gg = route_weight * gg + mu * np.einsum("am,amk->mk", w, pj)
        return gg.ravel()

    bounds = []
    for i in range(m):
        bounds.append((float(Q[i][0] - trust), float(Q[i][0] + trust)))
        bounds.append((float(Q[i][1] - trust), float(Q[i][1] + trust)))
    res = minimize(obj, (x0 if x0 is not None else Q).ravel(), jac=jac,
                   method="L-BFGS-B", bounds=bounds,
                   options={"maxiter": 5000, "maxfun": 30000,
                            "ftol": 1e-16, "gtol": 1e-8})
    LAST_SOLVE.clear()
    LAST_SOLVE.update(status=int(res.status), nit=int(res.nit),
                      nfev=int(res.nfev), fun=float(res.fun),
                      msg=str(res.message))
    V = np.asarray(res.x, dtype=float).reshape(m, 2)
    return V if np.all(np.isfinite(V)) else Q


def cut_frontal_dist(V, cuts) -> np.ndarray:
    """每个割 (x,n) 在布局 V 上的正面最近距离 D（无正面点为 inf）。"""
    if not cuts:
        return np.zeros(0)
    Q = np.asarray(V, dtype=float)
    XA = np.array([c[0] for c in cuts], dtype=float)
    NA = np.array([c[1] for c in cuts], dtype=float)
    DX = Q[None, :, :] - XA[:, None, :]                  # (A,m,2)
    dist = np.hypot(DX[..., 0], DX[..., 1])
    proj = NA[:, 0:1] * DX[..., 0] + NA[:, 1:2] * DX[..., 1]
    return np.where(proj > 0, dist, np.inf).min(axis=1)


def scp_step(Q: np.ndarray, order: list[int], *, trust: float, safety: float,
             margin: float = 0.0, step: float = 50.0, n_phi: int = 60,
             seed_thresh: float = 550.0, require_improve: bool = True,
             max_cuts: int = 22, tag: str = "scp") -> np.ndarray:
    """割平面 + 活动集 + soft-min 罚函数 SCP。

    割库 cut_set 累积所有种子/曾违反/近绑定的 (x,n)；每轮只把
    ``D >= R-safety-40``（违反或近绑定）的割放入目标（活动集）——
    余量充足的割不带电（零力零偏差）。解后扫描：真违反驱动继续，
    近绑定对（阈值 +40m）全部入库备用。
    信赖域从 100m 起步 ×1.5 爬坡（SCP 小步走：割模型只在当前点附近
    有效，第一步迈太大会有大面积未建模违反、收敛追不回来）。
    margin=0：证书只要求 ndot>0，margin>0 会给 ndot∈(0,margin) 的点
    制造持续外推力（曾致 L 反而 +873m）。卡住时 μ×5 + 信赖域×1.5。
    """
    Q = np.asarray(Q, dtype=float)
    order = list(order)
    L0 = route_len(order, Q)

    def key_of(x, n):
        return (round(float(x[0]), 1), round(float(x[1]), 1),
                round(float(n[0]), 3), round(float(n[1]), 3))

    seed_safety = max(0.0, R - seed_thresh)
    cut_set: set[tuple] = set()
    for c in violations(Q, step * 2, max(24, n_phi // 2),
                        safety=seed_safety, limit=3000):
        cut_set.add(key_of(c[0], c[1]))

    mu = 5.0
    tr = min(trust, 100.0)
    V = Q.copy()
    act_thresh = R - safety - 40.0
    for it in range(max_cuts):
        all_cuts = [(np.array([kx, ky]), np.array([knx, kny]))
                    for (kx, ky, knx, kny) in cut_set]
        Dc = cut_frontal_dist(V, all_cuts)
        cuts = [c for c, d in zip(all_cuts, Dc) if d >= act_thresh]
        # τ 退火：μ 越大 min 越尖锐（服务点选择越明确），避免推力被
        # 多个候选拆分稀释（τ 过大时每个候选都动一半、谁都到不了）。
        tau = max(5.0, 100.0 / math.sqrt(mu))
        V = _solve_pen(Q, order, cuts, tr, mu, safety, margin, tau=tau, x0=V)
        near = violations(V, step, n_phi, safety=safety + 40.0, limit=2500)
        viol = [v for v in near if v[3] > R - safety]
        if not viol:
            L1 = route_len(order, V)
            ok, w = complete(V.tolist(), step, n_phi, safety)
            if ok and (L1 <= L0 + 1e-6 or not require_improve):
                return V
            emit(f"      [{tag}] 轮{it} 完备但变长 L {L0:.0f}->{L1:.0f}，拒绝")
            return Q
        added = 0
        for x, n, j, D, jn in near:
            k = key_of(x, n)
            if k not in cut_set:
                cut_set.add(k)
                added += 1
        if it == 0 or (it + 1) % 3 == 0 or it == max_cuts - 1 or added == 0:
            emit(f"      [{tag}] 轮{it} 违反 {len(viol)} (worst={viol[0][3]:.0f}) "
                 f"活动 {len(cuts)}/{len(cut_set)} +{added}  μ={mu:.0f} tr={tr:.0f}")
        if added == 0:
            emit(f"      [{tag}] solver: {LAST_SOLVE}")
            mu *= 5.0                     # 卡住：罚权重 + 信赖域同时扩张
            tr = min(tr * 1.5, 600.0)
            if mu > 2e4 and tr >= 600.0:
                emit(f"      [{tag}] 轮{it} μ/信任域均到顶仍有 {len(viol)} 违反，放弃")
                return Q
        else:
            mu *= 1.6                     # 正常逐轮加强
            tr = min(tr * 1.5, trust)     # 信赖域爬坡
    return Q


def repair(Q: np.ndarray, *, trust: float = 520.0, safety: float = 10.0,
           max_rounds: int = 3) -> np.ndarray:
    """多轮 witness 重指派的修复：删点后的洞由 SCP 割平面填上。"""
    Q = np.asarray(Q, dtype=float)
    for _ in range(max_rounds):
        if complete(Q.tolist(), step=50.0, n_phi=60, safety=safety)[0]:
            return Q
        order = best_order(Q.tolist())
        Qn = scp_step(Q, order, trust=trust, safety=safety, seed_thresh=0.0,
                      require_improve=False, max_cuts=6, tag="repair")
        if np.allclose(Qn, Q):
            break
        Q = Qn
    return Q


def repair_geom(Q: np.ndarray, *, safety: float = 10.0, alpha: float = 0.5,
                max_iter: int = 80, step: float = 100.0, n_phi: int = 30,
                tag: str = "geom") -> np.ndarray:
    """几何直拉修复（Lloyd 式）：对每个被违反 (x,n)，把**最近点**（不限
    正面）拉向"正面半盘内的目标位"——目标位 = x + t·min(|q-x|, cap)，
    t 为 q-x 方向在正面半平面的投影方向。多点共享时取目标均值。
    粗网格迭代收敛后细网格校验，不通过再细网格拉一轮。"""
    Q = np.asarray(Q, dtype=float).copy()
    cap = R - safety - 18.0

    def pull_round(P, st, nph, iters):
        P = P.copy()
        for _ in range(iters):
            viol = violations(P, st, nph, safety=safety, limit=300)
            if not viol:
                return P, True
            moves: dict[int, list] = {}
            for x, n, j, D, jn in viol:
                jj = int(jn)
                d = P[jj] - x
                dist = float(np.hypot(d[0], d[1]))
                ndot = float(n @ d)
                if dist < 1e-9:
                    t = n.copy()
                elif ndot <= 0.0:
                    # 去掉背面分量，再加一点正面分量，归一化
                    t = d - ndot * n + 0.35 * (R - safety) * n
                    t /= np.hypot(t[0], t[1])
                else:
                    t = d / dist
                r = min(dist, cap)
                moves.setdefault(jj, []).append(x + t * r)
            for jj, ts in moves.items():
                tgt = np.mean(ts, axis=0)
                P[jj] = P[jj] + alpha * (tgt - P[jj])
        return P, False

    P, ok = pull_round(Q, step, n_phi, max_iter)
    if not ok:
        P, ok = pull_round(P, 50.0, 60, 40)     # 粗网格没收敛 → 细网格再拉
    okf, w = complete(P.tolist(), step=40.0, n_phi=72, safety=safety)
    if okf:
        return P
    emit(f"      [{tag}] 几何修复未收敛 (worst={w:.0f})")
    return np.asarray(Q, dtype=float)


# ---------------------------------------------------------------- 删除-修复步
def polish_feas(Q: np.ndarray, *, safety: float = 12.0, trust: float = 80.0,
                mu: float = 80.0, max_rounds: int = 14,
                tag: str = "polish") -> np.ndarray:
    """纯可行性微调（无路程项，route_weight=0）：全部近绑定割常开，
    多网格（40/60 与 25/90 并集）喂割——单网格收敛可能在另一分辨率留洞。
    收敛判据：三分辨率 (60,36)(40,60)(25,90) 证书全过。"""
    Q = np.asarray(Q, dtype=float)
    order = best_order(Q.tolist())
    grids = [(40.0, 60), (25.0, 90)]

    def key_of(x, n):
        return (round(float(x[0]), 1), round(float(x[1]), 1),
                round(float(n[0]), 3), round(float(n[1]), 3))

    def scan(P, lim):
        out_v = []
        for st, nph in grids:
            out_v += list(violations(P, st, nph, safety=safety + 40.0, limit=lim))
        return out_v

    def all_ok(P):
        for st, nph in ((60.0, 36), (40.0, 60), (25.0, 90)):
            if not complete(P.tolist(), st, nph, safety)[0]:
                return False
        return True

    def score(P):
        v = [u for u in scan(P, 2500) if u[3] > R - safety]
        w = max([u[3] for u in v], default=0.0)
        return (len(v), w)

    cut_set: set[tuple] = set()
    for v in scan(Q, 4000):
        cut_set.add(key_of(v[0], v[1]))
    V = Q.copy()
    bestV, bestS = Q.copy(), score(Q)
    for it in range(max_rounds):
        cuts = [(np.array([kx, ky]), np.array([knx, kny]))
                for (kx, ky, knx, kny) in cut_set]
        V = _solve_pen(Q, order, cuts, trust, mu, safety, 0.0,
                       tau=20.0, x0=V, route_weight=0.0)
        near = scan(V, 2500)
        viol = [v for v in near if v[3] > R - safety]
        s = (len(viol), max([v[3] for v in viol], default=0.0))
        if s < bestS:
            bestS, bestV = s, V.copy()
        if not viol and all_ok(V):
            return V
        added = 0
        for x, n, j, D, jn in near:
            k = key_of(x, n)
            if k not in cut_set:
                cut_set.add(k)
                added += 1
        if viol:
            emit(f"      [{tag}] 轮{it} 违反 {len(viol)} (worst={viol[0][3]:.0f}) "
                 f"割库 {len(cut_set)} +{added} μ={mu:.0f}")
        if added == 0 and it >= 2:
            mu *= 3.0
    if all_ok(V):
        return V
    emit(f"      [{tag}] 未收敛（多分辨率），返回最优迭代 {bestS}")
    return bestV


def knife_fix(V: np.ndarray, *, safety: float = 12.0, margin: float = 4.0,
              max_moves: int = 60, tag: str = "knife",
              brute_cap: float = R - 5.0) -> np.ndarray:
    """定向 knife-edge 修复：对每个被违反 (x,n)（含无正面点的洞），把"最小
    位移就能进入正面半盘"的点沿 n 推 (margin - ndot)。位移通常仅数米，
    对其它约束扰动最小；最后用多分辨率+凸包全局复验。"""
    P = np.asarray(V, dtype=float).copy()
    Rcap = R - safety - 2.0
    moved = 0

    def one_pass(grids, budget):
        nonlocal P, moved
        for _ in range(budget):
            bad = []
            for st, nph in grids:
                bad += [u for u in violations(P, st, nph, safety=safety, limit=800)
                        if u[3] > R - safety]
            if not bad:
                return True
            bad.sort(key=lambda u: -u[3])          # inf 优先
            x, n = bad[0][0], bad[0][1]
            dx = P - x
            dist = np.hypot(dx[:, 0], dx[:, 1])
            ndot = dx @ n
            need = margin - ndot                    # 需要增加的正面投影
            newdist = np.hypot(dx[:, 0] + need * n[0], dx[:, 1] + need * n[1])
            ok = (need > 0) & (dist <= R) & (newdist <= Rcap)
            if ok.any():
                j = int(np.argmin(np.where(ok, need, np.inf)))
            else:
                cand = np.where((dist <= R) & (need > 0))[0]
                if len(cand) == 0:
                    emit(f"      [{tag}] 无候选点可修 (x={np.round(x, 0)})")
                    return False
                j = int(cand[np.argmin(need[cand])])
            P[j] = P[j] + need[j] * n
            moved += 1
        return False

    one_pass([(40.0, 60), (25.0, 90)], max_moves)
    ok, msg = _multi_cert_ok(P, safety, brute_cap=brute_cap)
    if ok:
        emit(f"      [{tag}] 成功（{moved} 步）")
        return P
    emit(f"      [{tag}] 追加一轮：{msg}")
    one_pass([(20.0, 120), (25.0, 90)], 40)
    ok, msg = _multi_cert_ok(P, safety, brute_cap=brute_cap)
    if ok:
        emit(f"      [{tag}] 成功（{moved} 步，两轮）")
    else:
        emit(f"      [{tag}] 未收敛（{moved} 步）：{msg}")
    return P


_BRUTE_X: np.ndarray | None = None
_BRUTE_X_KEY: tuple | None = None


def brute_samples(n_samp: int = 6000, ring_deg: float = 0.05) -> np.ndarray:
    """Ω 内随机样本 + Ω 边界细角度环（缓存），用于**有限采样**扫描。"""
    global _BRUTE_X, _BRUTE_X_KEY
    key = (n_samp, ring_deg)
    if _BRUTE_X is None or _BRUTE_X_KEY != key:
        rng = np.random.default_rng(777)
        r = OMEGA * np.sqrt(rng.random(n_samp))
        th = 2 * np.pi * rng.random(n_samp)
        X = np.stack([r * np.cos(th), r * np.sin(th)], axis=1)
        ang = np.arange(0.0, 2 * math.pi, math.radians(ring_deg))
        X = np.vstack([X, np.stack([OMEGA * np.cos(ang), OMEGA * np.sin(ang)], axis=1)])
        _BRUTE_X = X
        _BRUTE_X_KEY = key
    return _BRUTE_X


def brute_worst(P, n_phi: int = 1440, n_samp: int = 6000,
                ring_deg: float = 0.05) -> tuple[float, int]:
    """**有限采样**扫描：随机样本 + 边界环 × 0.25°（默认 n_phi=1440）朝向，
    直接算正面最近距离。返回 ``(样本上的 worst, 无正面点样本数)``。

    .. warning::
       这是"在有限个 (x, n) 上取最大值"，**不是连续域最坏真值**：
       ``989.8 m`` 只是这份样本上的最大值，样本加密/换角向都可能变。
       连续域结论请用 :mod:`script.verify_layout_certificate` 的四叉树证书。
    """
    Q = np.asarray(P, dtype=float)
    X = brute_samples(n_samp, ring_deg)
    U = orientations(n_phi)
    worst, ninf = 0.0, 0
    for i in range(0, len(X), 300):
        Xc = X[i : i + 300]
        DX = Q[None, :, :] - Xc[:, None, :]
        dist = np.hypot(DX[..., 0], DX[..., 1])
        proj = DX @ U.T
        A = np.where(proj > 0, dist[..., None], np.inf)
        D = A.min(axis=1)
        kmax = D.max(axis=1)
        ninf += int(np.isinf(kmax).sum())
        if kmax.max() > worst:
            worst = float(kmax.max())
    return worst, ninf


def _multi_cert_ok(P, safety: float = 12.0, n_samp: int = 800,
                   brute_cap: float | None = None, *,
                   grids: tuple[tuple[float, int], ...] = CERT_GRIDS_FULL,
                   brute_phi: int = 1440, brute_samples_n: int = 6000,
                   brute_ring_deg: float = 0.05, brute: bool = True) -> tuple[bool, str]:
    """多分辨率网格证书 + Delaunay 凸包交叉验证 +（可选）0.25° 采样扫描，
    返回 ``(是否全过, 描述)``。brute_cap 给定时要求采样扫描 worst ≤ brute_cap。

    .. warning::
       三部分都只在**有限样本**上成立，因此本函数通过 ≠ 连续域完备。
       它的作用是"筛查出明显的洞并给出最坏样本点"，不是证明。
    """
    Q = np.asarray(P, dtype=float)
    for st, nph in grids:
        ok, w = complete(Q.tolist(), st, nph, safety)
        if not ok:
            return False, f"证书(step={st:.0f},n_phi={nph}) worst={w:.1f}"
    rng = np.random.default_rng(11)
    Xs = grid_points(40.0)
    samp = Xs[rng.choice(len(Xs), n_samp, replace=False)]
    bad, tot = conv_hull_check(Q, samp)
    if bad:
        return False, f"凸包违反 {bad}/{tot}"
    grid_desc = "+".join(f"{st:.0f}/{nph}" for st, nph in grids)
    if brute_cap is not None and brute:
        bw, ninf = brute_worst(Q, n_phi=brute_phi, n_samp=brute_samples_n,
                               ring_deg=brute_ring_deg)
        if ninf or bw > brute_cap:
            return False, f"采样扫描 worst={bw:.1f}（无正面点 {ninf}）"
        return True, (f"网格证书[{grid_desc}] + 凸包 {n_samp} 采样 + "
                      f"{360.0 / brute_phi:.2f}° 采样扫描 (worst={bw:.1f}) 全过")
    return True, f"网格证书[{grid_desc}] + 凸包 {n_samp} 采样全过"


def delete_repair_step(Q: np.ndarray, lam: float, safety: float = 10.0, *,
                       grids: tuple[tuple[float, int], ...] = CERT_GRIDS_FULL,
                       brute_cap: float | None = R - 5.0,
                       brute: bool = True, brute_phi: int = 1440,
                       brute_samples_n: int = 6000,
                       max_candidates: int | None = None) -> np.ndarray:
    """贪心删除-修复：删点 → SCP 补洞 → 证书通过且 L+λm 改善才接受。

    ``max_candidates`` 限制每轮真正尝试修复的候选数（冒烟用；默认不限制）。
    """
    Q = np.asarray(Q, dtype=float)
    while len(Q) > 8:
        order = best_order(Q.tolist())
        L0 = route_len(order, Q)
        cands = []
        for j in range(len(Q)):
            keep = [i for i in range(len(Q)) if i != j]
            Q2 = Q[keep]
            w, *_ = certify(Q2, step=80.0, n_phi=36)
            dL = route_len(best_order(Q2.tolist()), Q2) - L0
            cands.append((dL, w, j))
        # 只尝试"删后超出不太多（可修复）且路程收益明显"的候选
        cands = [c for c in cands if c[0] <= -150 and c[1] < 1350]
        cands.sort(key=lambda c: (c[0] + 0.3 * max(0.0, c[1] - 1000),))
        if max_candidates is not None:
            cands = cands[:max_candidates]
        if not cands:
            break
        accepted = False
        for dL, w, j in cands:
            keep = [i for i in range(len(Q)) if i != j]
            Q2 = Q[keep]
            Q2r = repair_geom(Q2, safety=safety)
            ok, w2 = complete(Q2r.tolist(), step=25.0, n_phi=90, safety=12.0)
            if not ok and np.isfinite(w2) and w2 < 1030.0:
                # 几何修复只差十几米 → 纯可行性微调收尾（无 L 项，不会乱拉点）
                Q2r = polish_feas(Q2r, safety=12.0)
            ok, msg = _multi_cert_ok(Q2r, 12.0, brute_cap=brute_cap, grids=grids,
                                     brute=brute, brute_phi=brute_phi,
                                     brute_samples_n=brute_samples_n)
            if not ok:
                emit(f"      删#{j} 修复后未过：{msg}")
                continue
            o2 = best_order(Q2r.tolist())
            L2 = route_len(o2, Q2r)
            if L2 + lam * len(Q2r) < L0 + lam * len(Q) - 1e-6:
                emit(f"    删#{j:2d}+修复: m={len(Q)}->{len(Q2r)} "
                     f"L={L0:.0f}->{L2:.0f}  [{msg}]")
                Q = Q2r
                accepted = True
                break
        if not accepted:
            break
    return Q


def continuous_certificate(points, *, min_side: float = 0.02) -> dict:
    """调用 :mod:`script.verify_layout_certificate` 的**连续域**四叉树证书。

    这是比本模块采样判据更强的充分条件（单元内每点、每朝向一致成立）。
    该脚本不存在或导入失败时返回 ``{"ok": None, ...}``，不影响采样流程。
    """
    try:
        if str(REPO_ROOT / "script") not in sys.path:
            sys.path.insert(0, str(REPO_ROOT / "script"))
        import verify_layout_certificate as vlc  # noqa: PLC0415

        return vlc.certificate(points, min_side=min_side)
    except Exception as exc:  # noqa: BLE001
        return {"ok": None, "error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------- 主流程
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--max-iter", type=int, default=10,
                    help="删点-修复主循环轮数（快照为 10；0 = 只做基线与报告）")
    ap.add_argument("--time-budget", type=float, default=2400.0,
                    help="主循环墙钟上限（秒，快照 2400）")
    ap.add_argument("--lam", type=float, default=100.0,
                    help="每个停点的等效成本（米）：目标 L+λm（快照 100）")
    ap.add_argument("--safety", type=float, default=FINE_SAFETY,
                    help="细网格证书安全余量（米，快照 12）")
    ap.add_argument("--base", choices=("v9", "json"), default="v9",
                    help="初始布局：v9 两圈式 25 点（默认）或 --in 指定的 json")
    ap.add_argument("--in", dest="in_path", default=None,
                    help="输入的布局 json（--base json 或 --emit 时使用）")
    ap.add_argument("--out-dir", default=None,
                    help=f"输出目录，默认 {DEFAULT_OUT_DIR}")
    ap.add_argument("--no-write", action="store_true", help="只打印，不写任何文件")
    ap.add_argument("--max-candidates", type=int, default=None,
                    help="每轮删点候选尝试上限（冒烟用；默认不限）")
    ap.add_argument("--cert-level", choices=("full", "coarse"), default="full",
                    help="采样证书网格：full=(60/36,40/60,25/90)；coarse=(120/24,60/36)")
    ap.add_argument("--brute-phi", type=int, default=1440,
                    help="采样扫描的朝向数（1440 = 0.25°）")
    ap.add_argument("--brute-samples", type=int, default=6000,
                    help="采样扫描的随机样本数（快照 6000）")
    ap.add_argument("--brute-ring-deg", type=float, default=0.05,
                    help="边界环角度步长（度，快照 0.05）")
    ap.add_argument("--brute-cap", type=float, default=R - 5.0,
                    help=f"采样扫描的验收阈值（米，快照 {R - 5.0:.0f}）")
    ap.add_argument("--no-brute", action="store_true",
                    help="跳过 0.25° 采样扫描（只保留网格证书 + 凸包交叉验证）")
    ap.add_argument("--continuous-cert", action="store_true",
                    help="结束时另跑 script/verify_layout_certificate 的连续域四叉树证书")
    ap.add_argument("--quick", action="store_true",
                    help="冒烟：--max-iter 0 + coarse 网格 + 不跑采样扫描")
    ap.add_argument("--emit", action="store_true",
                    help="只打印可粘贴的 LAYOUT_Q4_V17 片段（带表头注释）后退出")
    ap.add_argument("--emit-out", default=None,
                    help="配合 --emit：把片段写到该路径")
    ap.add_argument("--emit-var", default=layout_io.LAYOUT_VAR_NAME,
                    help="--emit 的变量名（默认 LAYOUT_Q4_V17）")
    return ap


def _apply_quick(args: argparse.Namespace) -> None:
    args.max_iter = 0
    args.cert_level = "coarse"
    args.no_brute = True
    args.brute_phi = 180
    args.brute_samples = 400
    args.brute_ring_deg = 1.0
    if args.time_budget > 120.0:
        args.time_budget = 120.0


def _resolve_emit_layout(args: argparse.Namespace) -> tuple[list, str]:
    if args.in_path:
        pts, _meta = layout_io.load_layout_json(args.in_path)
        return pts, f"来源：{args.in_path}"
    pts = layout_io.framework_layout()
    return pts, ("来源：framework/src/mathmodel2026b/strategy_v17.py 的 LAYOUT_Q4_V17"
                 "（未给 --in，因此打印交付真值本身，可用于与框架逐点对照）")


def _initial_layout(args: argparse.Namespace) -> tuple[np.ndarray, str]:
    if args.base == "json":
        if not args.in_path:
            raise SystemExit("--base json 需要同时给 --in <layout.json>")
        pts, meta = layout_io.load_layout_json(args.in_path)
        return np.array(pts, dtype=float), f"输入布局 {args.in_path}（m={meta.get('m')}）"
    base = two_tier_nodes(950.0, 1750.0, 12, 1900.0)
    return np.array(base, dtype=float), "基线（方法一）：v9 两圈式 25 点"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.quick:
        _apply_quick(args)

    # ---- --emit：只做格式转换，不跑优化
    if args.emit:
        pts, src = _resolve_emit_layout(args)
        Arr = np.asarray(pts, dtype=float)
        # 片段里的点序**就是**访问顺序，所以表头报的是"该顺序自己的"路线长度；
        # 另外复算一次 NN+2-opt+or-opt 的最短开放路径做参照（两者不必相等：
        # 快照的 18507.5m 是 advance.py 强 TSP 的结果，本模块只有贪心+局部搜索）。
        L_order = route_len(list(range(len(pts))), Arr)
        L_greedy = route_len(best_order(Arr), Arr)
        text = layout_io.emit_layout(
            pts, route_len_m=L_order, var_name=args.emit_var,
            note=[src,
                  f"复算参照：本模块 NN+2-opt+or-opt 得到 {L_greedy:.1f} m"
                  "（顺序步的启发式，比强 TSP 长属正常）",
                  "生成命令：python3 script/q4/joint_opt.py --emit"
                  + (f" --in {args.in_path}" if args.in_path else "")],
        )
        if args.emit_out:
            p = Path(args.emit_out)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        print(text, end="")
        return 0

    t0 = time.time()
    lam = args.lam
    grids = CERT_GRIDS_COARSE if args.cert_level == "coarse" else CERT_GRIDS_FULL
    brute = not args.no_brute
    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_OUT_DIR
    write = not args.no_write
    if write:
        out_dir.mkdir(parents=True, exist_ok=True)

    Q0, base_desc = _initial_layout(args)
    emit(base_desc)
    emit(f"参数：max_iter={args.max_iter} time_budget={args.time_budget:.0f}s "
         f"lam={lam:.0f} safety={args.safety:.0f} 网格={grids} "
         f"采样扫描={'开' if brute else '关'}（phi={args.brute_phi}, "
         f"样本={args.brute_samples}, 环步长={args.brute_ring_deg}°）")
    for step, n_phi in grids:
        w, *_, at = certify(Q0, step=step, n_phi=n_phi)
        emit(f"  证书(step={step:.0f}, n_phi={n_phi}): worst={w:.1f}m "
             f"{'完备' if w <= R else '不完备'}  at={at}")
    rng = np.random.default_rng(7)
    Xs = grid_points(40.0)
    samp = Xs[rng.choice(len(Xs), 400, replace=False)]
    bad, tot = conv_hull_check(Q0, samp)
    emit(f"  凸包交叉验证（方向B）: {tot - bad}/{tot} 通过"
         + ("" if bad == 0 else f"  ← {bad} 个违反！"))

    Q = Q0.copy()
    order = best_order(Q.tolist())
    L = route_len(order, Q)
    emit(f"\n初始路线 L={L:.0f}m  目标 L+λm={L + lam * len(Q):.0f}")
    best = (L + lam * len(Q), L, Q.copy(), order[:])
    fine_safety = args.safety

    run_coord = False   # 基线 25 点已证刚性（坐标步无法缩短 L），只在删点后重启
    for it in range(args.max_iter):
        if time.time() - t0 > args.time_budget:
            emit("  时间到，停止迭代")
            break
        # A. 坐标步（固定 pi）——仅当上一轮删点改变了布局时执行
        if run_coord:
            Qc = scp_step(Q, order, trust=180.0, safety=fine_safety, tag=f"轮{it}A")
            Lc = route_len(order, Qc)
            if Lc < L - 1e-9 and complete(Qc.tolist(), 25.0, 90, fine_safety)[0]:
                Q, L = Qc, Lc
                emit(f"  轮{it} 坐标步: L={L:.0f}m")
        # B. 顺序步（固定 q）
        o2 = best_order(Q.tolist())
        L2 = route_len(o2, Q)
        if L2 < L - 1e-9:
            order, L = o2, L2
            emit(f"  轮{it} 顺序步: L={L:.0f}m")
        # C. 删除-修复步
        Qn = delete_repair_step(Q, lam, safety=10.0, grids=grids, brute=brute,
                                brute_cap=args.brute_cap, brute_phi=args.brute_phi,
                                brute_samples_n=args.brute_samples,
                                max_candidates=args.max_candidates)
        run_coord = len(Qn) != len(Q)
        if run_coord:
            order = best_order(Qn.tolist())
            Q, L = Qn, route_len(order, Qn)
        # 轮末：多分辨率+凸包校验，不通过则回退并停止（后续轮只会重复同样失败）
        ok, msg = _multi_cert_ok(Q, fine_safety, grids=grids, brute=brute,
                                 brute_phi=args.brute_phi,
                                 brute_samples_n=args.brute_samples,
                                 brute_ring_deg=args.brute_ring_deg)
        if not ok:
            emit(f"  轮{it} 证书失败 ({msg})，回退并停止")
            _, L, Q, order = best
            break
        else:
            st, nph = grids[-1]
            w = complete(Q.tolist(), step=st, n_phi=nph, safety=fine_safety)[1]
            obj = L + lam * len(Q)
            if obj < best[0] - 1e-9:
                best = (obj, L, Q.copy(), order[:])
                emit(f"  轮{it} ✓ 新最优: m={len(Q)} L={L:.0f}m worst={w:.1f} obj={obj:.0f}")

    obj, L, Q, order = best
    emit("\n=== 最终结果 ===")
    Lb = route_len(best_order(Q0.tolist()), Q0)
    emit(f"m={len(Q)}  L={L:.0f}m  (基线 {len(Q0)} 点 / {Lb:.0f}m, "
         f"ΔL={L - Lb:+.0f}m ≈ {(Lb - L) / 5:.0f}s)")
    for step, n_phi in grids:
        w, *_, at = certify(Q, step=step, n_phi=n_phi)
        emit(f"  证书(step={step:.0f}, n_phi={n_phi}): worst={w:.1f}m "
             f"{'完备' if w <= R else '不完备'}  at={at}")
    bad, tot = conv_hull_check(Q, samp)
    emit(f"  凸包交叉验证（方向B）: {tot - bad}/{tot} 通过"
         + ("" if bad == 0 else f"  ← {bad} 个违反！"))
    emit("  ⚠ 以上都是**有限采样**判据：采样通过 ≠ 连续域完备。"
         "连续域证书请跑 script/verify_layout_certificate.py")

    cert = None
    if args.continuous_cert:
        cert = continuous_certificate(Q.tolist())
        if cert.get("ok") is None:
            emit(f"  连续域证书（四叉树）: 未能运行（{cert.get('error')}）")
        else:
            emit(f"  连续域证书（四叉树）: ok={cert['ok']} 检查单元={cert['checked']} "
                 f"通过={cert['passed']} 未决={len(cert['unresolved'])}")

    pts = [[round(float(v), 1) for v in Q[i]] for i in order]
    if write:
        jp = layout_io.save_layout_json(
            out_dir / "layout.json", pts, route_len_m=L,
            extra={"source": "script/q4/joint_opt.py", "safety": fine_safety,
                   "grids": [list(g) for g in grids],
                   "certified_lower_bound": False,
                   "continuous_certificate_ok": None if cert is None else cert.get("ok")},
        )
        frag = layout_io.emit_layout(
            pts, route_len_m=L,
            note=["生成命令：python3 script/q4/joint_opt.py（默认参数）",
                  "粘贴前请重跑 script/verify_layout_certificate.py 复核连续域证书"],
        )
        fp = out_dir / "layout_fragment.py"
        fp.write_text(frag, encoding="utf-8")
        tp = out_dir / "joint_opt.txt"
        tp.write_text("\n".join(out) + "\n", encoding="utf-8")
        emit(f"\n输出: {jp} / {fp} / {tp}  (用时 {time.time() - t0:.0f}s)")
    else:
        emit(f"\n未写文件（--no-write），用时 {time.time() - t0:.0f}s")
    emit(f"\n交付片段（--emit 可单独打印）：\n"
         + layout_io.emit_layout(pts, route_len_m=L,
                                 note=["生成命令：python3 script/q4/joint_opt.py --in <layout.json> --emit"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
