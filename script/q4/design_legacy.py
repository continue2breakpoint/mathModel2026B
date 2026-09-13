#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Q4 发现层布局设计/分析的**历史归档**（2026-09-13 快照五个脚本的统一入口）。

⚠️ 本文件是历史归档（historical archive），不是交付路径
======================================================
本文件把 2026-09-13 的 Windows 最终工程快照顶层的五个脚本原样收进仓库，**只为追溯数字来源**：

``nodes``     ← ``_design_q4_nodes.py``       基准轴向网格 +「内圈网格 + 边界环」候选枚举
``route``     ← ``_design_q4_route.py``       31 点轴向网格：论文蛇形路线 vs NN+2-opt
``v2``        ← ``_design_q4_v2.py``          修掉下半平面缺失后的候选表（选出 950/1750/12/1900）
``analysis``  ← ``_q4_layout_analysis.py``    25 点布局：采样判据 / 单点冗余 / 贪心覆盖子集 / 路线
``fail-diag`` ← ``_q4_fail_diag.py``          失败种子的漏清根因（mock + 策略侧轨迹）

它们记录的是 **v9 两圈式 25 点布局**（``two_tier_nodes(950.0, 1750.0, 12, 1900.0)``）当年
如何被设计出来、又被哪些实验支持。四条必须一起读的边界：

1. **不是交付路径**：本文件不产出交付布局，不写 ``framework/``，也不参与交付链路。
2. **不得作为当前结果引用**：本文件打印的一切数字都是 **历史口径复现**；
   多处结论已被 2026-09-13 的独立复核纠正（见下面"准确性口径"），且 **v9 的 25 点布局
   本身也没有被交付** —— 它被 v17 的 24 点布局取代了。
3. **交付布局在哪**：``mathmodel2026b.strategy_v17.LAYOUT_Q4_V17``（**24 点，离线路线
   18507.5 m**），由 ``script/q4/joint_opt.py``（坐标-顺序联合优化）与
   ``script/q4/advance.py``（强 TSP 提路线 + 删点尝试）产出，独立证书由
   ``python3 script/verify_layout_certificate.py`` 出（连续域四叉树充分条件，要求
   ``unresolved == 0``）。
4. **最终交付继承它**：``mathmodel2026b.versioned`` 里的 ``q4-v18``（最终交付）
   **继承** ``q4-v17`` 的 ``LAYOUT_Q4_V17``；v18 只新增知识矩阵负例与覆盖式清除兜底，
   **不**改发现层布局。``fail-diag`` 跑的是 v9/v14 那条**发现层 25 点**的策略线，
   与交付布局不同源，所以它的成功/失败都不能直接搬到交付版上。

每次运行都会在最前面打印一行 ``[历史归档]`` 横幅，避免把输出误当成交付结果。

准确性口径（2026-09-13 独立复核的三条纠正）
------------------------------------------
1. **采样判据 ≠ 完备性证明**。``nodes`` / ``v2`` / ``analysis`` 里所谓的"完备性"全部来自
   **有限样本**（位置网格 × 离散朝向）上的最坏正面距离，只能**证伪**（找出一个反面样本），
   **不能证明**连续域完备。本文件一律写"采样判据 / 采样完备"，不写"证明 / 完备性证明"。
   连续域可证证书只来自 ``python3 script/verify_layout_certificate.py``。
2. **失败的优化实验不构成不可行结论**。单点删除全部失败、路线/坐标搜索变长，只能说明
   **试过的那些候选、那个搜索方向**没成功：
   「23 点的三个删点修复失败，不证明所有 23 点布局不可行」；
   「两次坐标搜索变长，不证明 25 点布局局部最优或所有可行方向都变长」。
   本文件不把"不可行 / 最优"当全局结论输出（``v2`` 只写"本次枚举的候选里…"）。
3. **清除计时分两种口径**。快照当年的 mock 把所有 ``/clear`` 都按 **5 s** 计（**旧口径**）；
   附件1 §2.3 的官方口径是**未发现 3 s / 已清除 5 s**，即
   ``mathmodel2026b.mock.world.World(..., legacy_clear_timing=True)`` 才复现旧数字，
   两者对同一轨迹满足 ``T_旧 = T_官方 + 2 × 清除失败次数``。
   ``fail-diag`` 默认仍按旧口径跑（复现快照），并在输出里逐行标 ``[旧口径]``；
   ``--clear-timing spec`` 切到官方口径。

复现方式
--------
两行都可用（``script`` 目录没有 ``__init__.py``，靠命名空间包工作）::

    python3 script/q4/design_legacy.py --help
    python3 -m script.q4.design_legacy nodes --quick

    python3 script/q4/design_legacy.py nodes            # 快照 `_design_q4_nodes.py` 全量
    python3 script/q4/design_legacy.py route
    python3 script/q4/design_legacy.py v2
    python3 script/q4/design_legacy.py analysis
    python3 script/q4/design_legacy.py fail-diag --seeds 12 --quick
    python3 script/q4/design_legacy.py v2 --quick --json --no-write

输出（默认 ``logs/q4/design_legacy/<子命令>/``）：``<子命令>.txt``（历史文本输出，
对应快照的 stdout / ``_*.txt``）与 ``<子命令>.json``（``--json`` 时另打一份到 stdout）。
``--no-write`` 只打印不落盘。
"""

from __future__ import annotations

import argparse, json, math, statistics, sys, time
from pathlib import Path
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent          # mathModel2026B/script/q4
REPO_ROOT = SCRIPT_DIR.parent.parent                  # mathModel2026B
sys.path.insert(0, str(REPO_ROOT / "framework" / "src"))
sys.path.insert(0, str(SCRIPT_DIR))

from mathmodel2026b.strategy_v9 import two_tier_nodes  # noqa: E402

try:  # 包内运行
    from . import joint_opt as jo
    from . import layout_io
except ImportError:  # 单文件直跑
    import joint_opt as jo
    import layout_io

# --------------------------------------------------------------------------- 历史常量
#: 目标区域半径（Ω，与框架 ``coverage.OMEGA_R`` 同值）。
OMEGA = 1800.0
#: 源的有效接收半径下界（判据里的 1000 m）。
R = 1000.0
#: 起点（机器狗入口）。
START = (0.0, 0.0)
#: 历史口径折算速度：快照所有"行程 → 秒"都用 5 m/s。
SPEED_MPS = 5.0

#: 快照 ``worst_front`` 的默认采样密度（位置 60 m、朝向 36 个）。
NODES_STEP = 60.0
NODES_N_PHI = 36
#: 基准轴向网格间距。
AXIAL_S = (950.0, 1000.0)
#: ``_design_q4_nodes.py`` 的候选 B 枚举表（环半径, 环点数）。
NODES_OUTER = ((1900.0, 12), (1900.0, 16), (2000.0, 12), (2100.0, 14), (2400.0, 20))
#: ``_design_q4_nodes.py`` 的候选 B 枚举表（内圈间距, 内圈半径）。
NODES_INNER = ((1400.0, 1500.0), (1500.0, 1500.0), (1400.0, 1700.0), (1700.0, 1500.0))
#: ``_design_q4_v2.py`` 的枚举表。
V2_INNER = ((950.0, 1750.0), (950.0, 1900.0), (1000.0, 1750.0), (900.0, 1800.0))
V2_OUTER = ((1900.0, 12), (1950.0, 13), (2000.0, 13), (2100.0, 14), (2400.0, 16))
#: v9 交付布局（历史）：``two_tier_nodes(950, 1750, 12, 1900)``。
V9_TIER = (950.0, 1750.0, 12, 1900.0)

#: ``_q4_layout_analysis.py`` 的采样密度（位置 40 m、朝向 10°）。
ANALYSIS_POS_STEP = 40.0
ANALYSIS_DIR_DEG = 10.0
#: 多起点路线搜索的试验次数（快照 30，``random.Random(1)``）。
ANALYSIS_TRIALS = 30
#: 2-opt 的轮数上限（快照的试验内联 2-opt 是"直到无改进"；jo.two_opt 提前收敛，等价）。
ANALYSIS_TWO_OPT_PASSES = 60
#: 快照 ``greedy_route`` 的 2-opt 轮数（strategy_v9 默认 40）。
GREEDY_2OPT_PASSES = 40

BANNER = (
    "[历史归档] script/q4/design_legacy.py：2026-09-13 快照脚本的**历史归档**，"
    "输出是历史口径复现、不得当当前结果引用；交付布局见 "
    "strategy_v17.LAYOUT_Q4_V17（24 点，离线 18507.5 m，q4-v18 继承之）。"
)

#: 采样判据的标准免责声明（``nodes`` / ``v2`` / ``analysis`` 共用）。
SAMPLE_NOTE = (
    "采样判据：有限样本（位置网格 × 离散朝向）上的最坏正面距离，"
    "只能证伪、不能证明连续域完备。"
)
CERT_HINT = "连续域证书请跑：python3 script/verify_layout_certificate.py（四叉树，要求 unresolved=0）。"
#: "失败实验 ≠ 不可行"的标准声明（``analysis`` 用）。
INFEASIBILITY_NOTE = (
    "[限制] 本项只排除**试过的候选 / 那条搜索方向**：23 点的三个删点修复失败，"
    "不证明所有 23 点布局不可行；路线/坐标搜索变长，不证明 25 点布局局部最优。"
    "（2026-09-13 独立复核）"
)

#: 本模块的输出根目录（离线产物，不进 framework/，也不进快照目录）。
DEFAULT_OUT_ROOT = layout_io.LOG_ROOT / "design_legacy"

#: 收集本次运行的文本输出（落盘为 ``<子命令>.txt``）。
OUT: list[str] = []
#: 本次运行的起始时刻（``main`` 里设置，用于报告墙钟）。
START_TIME = 0.0


def say(s: str = "") -> None:
    """记录并入 stdout 打印一行（对应快照脚本的 ``out.append`` + ``print``）。"""
    OUT.append(s)
    print(s, flush=True)


def _print_banner() -> None:
    """每次运行（含 ``--help``）都在最前面打印 ``[历史归档]`` 横幅。"""
    print(BANNER, flush=True)


# --------------------------------------------------------------------------- 通用小工具
def _at_str(at) -> str:
    """把 ``jo.certify`` 的 witness ``(x, n)`` 格式化成 ``(x, y, 朝向°)``。"""
    if at is None:
        return "-"
    (x, y), (ux, uy) = at
    deg = math.degrees(math.atan2(float(uy), float(ux)))
    return f"({float(x):.0f}, {float(y):.0f}, {deg:.0f}°)"


def _worst_frontal(nodes, step: float, n_phi: int):
    """采样最坏正面距离：**直接复用** ``jo.certify``（``(step, n_phi)`` 网格 × 朝向）。

    快照的 ``worst_front`` 与 ``jo.certify`` 是同一个判据：``certify`` 的采样集是快照
    网格的**超集**（额外加了一圈 0.5° 边界环）。实测两者在本文关心的布局上给出**完全
    相同**的值（25 点两圈式 962.49 m、轴向网格 950.0/1000.0 m），故不再另写一份实现。
    """
    worst, *_rest, at = jo.certify(nodes, step=step, n_phi=n_phi)
    return float(worst), at


def _nn2opt(pts, passes: int):
    """NN + 2-opt + 开放路径长度：复用 ``jo.nn_order`` / ``jo.two_opt`` / ``jo.route_len``。

    等价于快照的 ``nn_route`` + ``two_opt``（同一算法、同一 ``passes``）。
    """
    Q = [[float(x), float(y)] for x, y in pts]
    order = jo.two_opt(jo.nn_order(Q), Q, passes=passes)
    return order, Q, jo.route_len(order, Q)


def _out_dir(args: argparse.Namespace) -> Path:
    return Path(args.out_dir) if args.out_dir else (DEFAULT_OUT_ROOT / args.cmd)


def _finish(args: argparse.Namespace, payload: dict) -> None:
    """落盘 ``<子命令>.txt`` / ``<子命令>.json``（``--no-write`` 时只打印）。"""
    wall = time.time() - START_TIME
    say(f"\n用时 {wall:.2f}s")
    payload = {"historical": True, "subcommand": args.cmd, "quick": bool(args.quick),
               "cite_as": "历史口径复现（非交付结果）；交付布局见 strategy_v17.LAYOUT_Q4_V17",
               "wall_time_s": round(wall, 3), **payload}
    d = _out_dir(args)
    if not args.no_write:
        say(f"\n输出：{d / (args.cmd + '.txt')} ／ {d / (args.cmd + '.json')}")
    text = "\n".join(OUT) + "\n"
    js = json.dumps(payload, ensure_ascii=False, indent=1)
    if args.json:
        print("\n---- JSON ----", flush=True)
        print(js, flush=True)
    if not args.no_write:
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{args.cmd}.txt").write_text(text, encoding="utf-8")
        (d / f"{args.cmd}.json").write_text(js + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- 几何生成器
def lattice_upper(s: float, r_max: float) -> list[tuple[float, float]]:
    """``_design_q4_nodes.py`` 的三角网格生成器：**1:1 保留快照行为**。

    ⚠️ 快照这一版只生成 ``y >= 0`` 的**上半平面**（``v2`` 的 docstring 明确写着
    "只生成 y>=0 会漏掉下半平面"），而且外层 ``while k*dy <= r_max + s`` 与内层
    ``break`` 条件用的是 ``r_max + s``。这里不做任何"顺手修好"，否则 ``nodes`` 的
    表格就不是快照那张表了；下半平面缺失的影响见 ``nodes`` 输出里的说明。
    """
    pts: list[tuple[float, float]] = []
    dy = s * math.sqrt(3) / 2.0
    k = 0
    while k * dy <= r_max + s:
        y = k * dy
        x0 = 0.0 if k % 2 == 0 else s / 2.0
        m = 0
        while True:
            for sx in ((1,) if m == 0 and x0 == 0.0 else (1, -1)):
                x = sx * (x0 + m * s)
                if math.hypot(x, y) <= r_max + 1e-9:
                    pts.append((x, y))
            if abs(x0 + m * s) > r_max + s:
                break
            m += 1
        k += 1
    return pts


def lattice_full(s: float, r_max: float) -> list[tuple[float, float]]:
    """``_design_q4_v2.py`` 的三角网格（含 ``y<0`` 半边、内部去重）：1:1 保留。

    与 ``mathmodel2026b.strategy_v9.two_tier_nodes`` 的内圈生成**逐点一致**（已实测），
    所以 ``v2`` 表里选出的 25 点就是交付过的 v9 布局。
    """
    pts: list[tuple[float, float]] = []
    dy = s * math.sqrt(3) / 2.0
    seen: set[tuple[float, float]] = set()
    k = 0
    while k * dy <= r_max + 1e-9:
        for y in ((k * dy,) if k == 0 else (k * dy, -k * dy)):
            x0 = 0.0 if k % 2 == 0 else s / 2.0
            m = 0
            while True:
                xs = (1,) if (m == 0 and x0 == 0.0) else (1, -1)
                for sx in xs:
                    x = sx * (x0 + m * s)
                    if math.hypot(x, y) <= r_max + 1e-9 and (x, y) not in seen:
                        seen.add((x, y))
                        pts.append((x, y))
                if abs(x0 + m * s) > r_max + 1e-9:
                    break
                m += 1
        k += 1
    return pts


def ring_pts(n: int, r: float) -> list[tuple[float, float]]:
    """外圈环（``_design_q4_nodes.py`` / ``_design_q4_v2.py`` 的内联生成器）。"""
    return [(r * math.cos(2 * math.pi * k / n), r * math.sin(2 * math.pi * k / n)) for k in range(n)]


# --------------------------------------------------------------------------- 子命令 nodes
def cmd_nodes(args: argparse.Namespace) -> int:
    """``_design_q4_nodes.py``：候选 B = 内圈三角网格 + 边界环。"""
    from mathmodel2026b.paper_q4.coverage import axial_grid_nodes, hamiltonian_route_axial

    step, n_phi = args.step, args.n_phi
    say(f"采样密度：位置步长 {step:.0f} m、朝向 {n_phi} 个；路线用 NN+2-opt(passes={args.passes})。")
    say(SAMPLE_NOTE + CERT_HINT)
    say("⚠️ 注：本子命令 1:1 保留快照的 lattice()，它只生成 y>=0 的**上半平面**（下半平面缺失），"
        "所以下表「采样不完备」里有一部分来自这个生成缺陷，不能读作「该候选设计本身不可行」。"
        "修掉该缺陷的版本见子命令 v2。")
    say()
    say("== 基准 ==")

    baseline = []
    for s in AXIAL_S:
        grid = axial_grid_nodes(s)
        route, L = hamiltonian_route_axial(s)
        w, at = _worst_frontal(route, step, n_phi)
        label = "采样完备" if w <= R else "采样不完备"
        say(f"轴向三角网格 s={s:.0f} (论文)          点数={len(grid):<3} "
            f"最坏正面距离={w:7.2f}m ({label}) 行程={L:7.0f}m")
        baseline.append({"s": s, "m": len(grid), "worst_frontal_m": round(w, 2),
                         "sample_complete": bool(w <= R), "paper_route_m": round(float(L), 1),
                         "at": _at_str(at)})

    say()
    say("== 候选 B：内圈网格 + 边界环 ==")
    candidates = []
    n_done = 0
    for r_o, n_o in NODES_OUTER:
        for s_i, r_i in NODES_INNER:
            if args.limit and n_done >= args.limit:
                break
            n_done += 1
            nodes = lattice_upper(s_i, r_i) + ring_pts(n_o, r_o)
            w, at = _worst_frontal(nodes, step, n_phi)
            _order, _Q, L = _nn2opt(nodes, args.passes)
            label = "采样完备" if w <= R else "采样不完备"
            say(f"内圈s={s_i:.0f}@r<= {r_i:.0f} + 环{n_o}@{r_o:.0f}".ljust(34)
                + f" 点数={len(nodes):<3} 最坏正面距离={w:7.2f}m "
                  f"({label} 裕量={R - w:6.1f}) 行程={L:7.0f}m")
            candidates.append({"name": f"内圈s={s_i:.0f}@r<={r_i:.0f}+环{n_o}@{r_o:.0f}",
                               "m": len(nodes), "worst_frontal_m": round(w, 2),
                               "sample_complete": bool(w <= R), "margin_m": round(R - w, 2),
                               "route_m": round(L, 1), "at": _at_str(at)})
        if args.limit and n_done >= args.limit:
            break

    say()
    best = min((c for c in candidates if c["sample_complete"]), key=lambda c: c["route_m"], default=None)
    if best is None:
        n_ok = sum(1 for c in candidates if c["sample_complete"])
        say(f"说明：本表 {len(candidates)} 个候选里 {n_ok} 个在**该采样判据**下达标"
            "（快照全量下的结果与之一致：候选 B 全部采样不完备）；快照据此把 lattice() "
            "改成含下半平面的版本，见子命令 v2。")
    else:
        say(f"本次枚举里行程最短的采样达标候选：{best['name']} 行程={best['route_m']:.0f} m"
            "（只在本次枚举的候选里比较，不是全局最优）。")
    # 追加对照：更强的顺序搜索（jo.best_order 多一步 or-opt），不属于快照输出。
    Q = [[x, y] for x, y in two_tier_nodes(*V9_TIER)]
    say(f"追加对照（非快照输出）：v9 25 点布局上 jo.best_order(NN+2-opt+or-opt) = "
        f"{jo.route_len(jo.best_order(Q), Q):.0f} m。")
    _finish(args, {"step_m": step, "n_phi": n_phi, "passes": args.passes,
                   "limit": args.limit, "baseline": baseline, "candidates": candidates,
                   "sample_judge": SAMPLE_NOTE + CERT_HINT,
                   "note_lattice_upper_half_plane_only": True})
    return 0


# --------------------------------------------------------------------------- 子命令 route
def cmd_route(args: argparse.Namespace) -> int:
    """``_design_q4_route.py``：轴向网格的路线还能压多少。"""
    from mathmodel2026b.paper_q4.coverage import axial_grid_nodes, hamiltonian_route_axial

    say("口径：行程时间按 5 m/s 折算（历史口径，与快照一致）；"
        "检测 5 s／换频道 1 s、清除 未发现 3 s／已清除 5 s（附件1 §2.3）。")
    say(SAMPLE_NOTE)
    say()
    rows = []
    for idx, s in enumerate(AXIAL_S):
        if args.limit and idx >= args.limit:
            break
        grid = axial_grid_nodes(s)
        route, L = hamiltonian_route_axial(s)
        ext = max(math.hypot(*p) for p in route)
        best = min(_nn2opt(route, args.passes)[2] for _ in range(args.tries))
        say(f"s={s:.0f}: 点数={len(route)} 最大半径={ext:.0f}m 论文蛇形路线={L:.0f}m "
            f"NN+2opt={best:.0f}m 可压缩={L - best:.0f}m ({(1 - best / L) * 100:.1f}%)")
        say(f"      行程时间 论文={L / SPEED_MPS:.0f}s  优化后={best / SPEED_MPS:.0f}s  "
            f"省={(L - best) / SPEED_MPS:.0f}s")
        rows.append({"s": s, "m": len(route), "grid_m": len(grid),
                     "max_radius_m": round(float(ext), 1),
                     "paper_route_m": round(float(L), 1), "nn2opt_m": round(float(best), 1),
                     "saving_m": round(float(L - best), 1),
                     "saving_pct": round((1 - best / L) * 100, 2),
                     "paper_travel_s": round(float(L) / SPEED_MPS, 1),
                     "nn2opt_travel_s": round(float(best) / SPEED_MPS, 1)})
    say()
    say("注：本子命令的 31 点轴向网格是 v9 之前的方案，**不是**交付布局；"
        "交付布局的离线路线是 strategy_v17.LAYOUT_Q4_V17 的 18507.5 m（24 点）。")
    _finish(args, {"passes": args.passes, "tries": args.tries, "limit": args.limit,
                   "speed_mps": SPEED_MPS, "rows": rows})
    return 0


# --------------------------------------------------------------------------- 子命令 v2
def cmd_v2(args: argparse.Namespace) -> int:
    """``_design_q4_v2.py``：修掉下半平面后的候选表（选出 950/1750/12/1900）。"""
    from mathmodel2026b.paper_q4.coverage import hamiltonian_route_axial

    step, n_phi = args.step, args.n_phi
    say(f"采样密度：位置步长 {step:.0f} m、朝向 {n_phi} 个；路线用 NN+2-opt(passes={args.passes}) "
        f"× {args.tries} 次取最小。")
    say(SAMPLE_NOTE + CERT_HINT)
    say("注：快照的 best_route(tries=6) 每次都是同一确定性 NN（无打乱），6 次结果完全相同，"
        "所以这一列等于一次 NN+2-opt 的值；1:1 保留 tries 循环。")
    say("注：环点是**直接追加**的（不与内圈去重）。交付框架里的 strategy_v9.two_tier_nodes "
        "会去重，因此当环点与内圈点重合（如 s=950 & r_o=1900）时两者点数会差 1。")
    say()
    say("基准（现有轴向网格）:")
    baseline = []
    for s in AXIAL_S:
        route, L = hamiltonian_route_axial(s)
        w, _at = _worst_frontal(route, step, n_phi)
        say(f"  轴向网格 s={s:.0f}: {len(route)} 点 最坏正面={w:.1f}m 行程={L:.0f}m")
        baseline.append({"s": s, "m": len(route), "worst_frontal_m": round(w, 2),
                         "paper_route_m": round(float(L), 1)})

    say()
    say(f"{'设计':<40}{'点数':>5}{'最坏正面':>10}{'采样完备':>9}{'行程':>9}")
    rows: list[dict] = []
    best = None
    n_done = 0
    for s_i, r_i in V2_INNER:
        for r_o, n_o in V2_OUTER:
            if args.limit and n_done >= args.limit:
                break
            n_done += 1
            nodes = lattice_full(s_i, r_i) + ring_pts(n_o, r_o)
            w, at = _worst_frontal(nodes, step, n_phi)
            L = min(_nn2opt(nodes, args.passes)[2] for _ in range(args.tries))
            ok = w <= R
            say(f"内圈s={s_i:.0f}@r<={r_i:.0f} + 环{n_o}@{r_o:.0f}".ljust(40)
                + f"{len(nodes):>5}{w:>10.1f}{('是' if ok else '否'):>9}{L:>9.0f}"
                  f"  最坏点={_at_str(at)}")
            rows.append({"name": f"内圈s={s_i:.0f}@r<={r_i:.0f}+环{n_o}@{r_o:.0f}",
                         "m": len(nodes), "worst_frontal_m": round(w, 2),
                         "sample_complete": bool(ok), "route_m": round(L, 1),
                         "at": _at_str(at), "s_i": s_i, "r_i": r_i, "n_o": n_o, "r_o": r_o})
            if ok and (best is None or L < best["route_m"]):
                best = rows[-1]
        if args.limit and n_done >= args.limit:
            break

    say()
    if best:
        say(f"本次枚举的候选里行程最短且采样达标：行程={best['route_m']:.0f}m 点数={best['m']} "
            f"最坏正面={best['worst_frontal_m']:.1f}m  (s_i={best['s_i']} r_i={best['r_i']} "
            f"n_o={best['n_o']} r_o={best['r_o']})")
        say("  ⚠️ 措辞限制：这是**本次枚举的候选里**最好的（快照原文写「最优可行设计」）；"
            "它不排除别处存在更省行程的可行设计。")
        say(f"  → 发现层行驶 {best['route_m'] / SPEED_MPS:.0f}s（现轴向网格 28500m/5700s）")
        say("  → 下列数值是**理想计数估算，不是认证下界**（启发式可行解不自动构成下界）：")
        cost = []
        for n in (13, 16):
            probes = (20 - n) * best["m"] + 2 * n
            vt = best["route_m"] / SPEED_MPS + probes * 6 + n * 5
            say(f"     n={n} 时: 检测 {probes} 次, vt≈{vt:.0f}s, s/源≈{vt / n:.0f}")
            cost.append({"n": n, "probes": probes, "vt_s": round(vt, 1), "s_per_source": round(vt / n, 1)})
        say("     计时口径：检测按 6 s/次（5 s 测量 + 1 s 换频道）、每源清除 5 s = 附件1 §2.3 的"
            "**已清除**耗时；估算假设计划一次成功，故不含清除失败项。若出现失败，官方口径记 3 s/次、"
            "旧口径记 5 s（`mathmodel2026b.mock.world.World(..., legacy_clear_timing=True)` 复现旧数字）。")
    else:
        say("本次枚举的候选里没有采样达标的设计。")
        say("  ⚠️ 措辞限制：这只否定**本次枚举的这些 (内圈, 环) 组合**"
            "（快照原文写「无可行设计（全部不完备）」），不能推广为「不存在可行设计」。")
        cost = []
    if best:
        _maybe_write_candidate(args, rows, best)
    _finish(args, {"step_m": step, "n_phi": n_phi, "passes": args.passes, "tries": args.tries,
                   "limit": args.limit, "baseline": baseline, "rows": rows,
                   "best_in_enumerated_candidates": best, "cost_model": cost,
                   "sample_judge": SAMPLE_NOTE + CERT_HINT,
                   "v9_delivered_layout": list(V9_TIER)})
    return 0


def _maybe_write_candidate(args: argparse.Namespace, rows, best) -> None:
    """把 v2 选出的候选布局记成 json（**历史候选**，不是交付布局）。

    这里**故意不用** :func:`layout_io.emit_layout`：它的表头断言"粘贴到
    ``strategy_v17.py`` 的 ``LAYOUT_Q4_V17``"，而本模块产出的是历史候选布局，
    贴进交付文件就是错误。只借 ``save_layout_json`` 的格式，并打上 ``historical`` 标记。
    """
    nodes = lattice_full(best["s_i"], best["r_i"]) + ring_pts(best["n_o"], best["r_o"])
    order, Q, L = _nn2opt(nodes, args.passes)
    pts = [Q[i] for i in order]
    best["route_len_m_in_order"] = round(L, 1)
    if args.no_write:
        return
    p = _out_dir(args) / "v2_candidate_layout.json"
    layout_io.save_layout_json(
        p, pts, route_len_m=L,
        extra={"historical": True,
               "source": "script/q4/design_legacy.py v2（← _design_q4_v2.py）",
               "warning": "历史候选布局：v9 两圈式 25 点，已被 strategy_v17.LAYOUT_Q4_V17 取代，"
                          "不是交付布局，不要粘贴进 framework/",
               "design": {"s_i": best["s_i"], "r_i": best["r_i"],
                          "n_o": best["n_o"], "r_o": best["r_o"]}},
    )
    say(f"  （历史候选布局已记到 {p}；它是历史候选，**不是** LAYOUT_Q4_V17，别贴进 framework/）")


# --------------------------------------------------------------------------- 子命令 analysis
def _analysis_grid(pos_step: float) -> np.ndarray:
    """快照 ``pos_list`` 的等价采样集：Ω 内笛卡尔网格。

    ``jo.grid_points(step, ring_deg=360.0)`` 的边界环退化成重复的 ``(1800, 0)``，
    去重后与快照 ``pos_list`` **逐点相同**（实测 6361 点、集合相等），所以采样集一致。
    """
    return np.unique(jo.grid_points(pos_step, ring_deg=360.0), axis=0)


def _worst_frontal_vec(nodes, G: np.ndarray, U: np.ndarray) -> float:
    """向量化的采样最坏正面距离（与快照 ``worst_front_dist`` 同一判据、同一采样集）。

    判据里的``正面``沿用严格 ``proj > 0``（题面是闭半圆盘 ``>= 0``；严格版留了安全裕量，
    快照与框架 ``joint_opt.frontal_scan`` 用的都是严格版）。
    """
    P = np.asarray(nodes, dtype=float)[None, :, :] - G[:, None, :]
    dist = np.hypot(P[..., 0], P[..., 1])
    proj = P @ U.T
    best = np.where(proj > 0, dist[..., None], np.inf).min(axis=1)
    return float(best.max())


def _covered_pairs(nodes, G: np.ndarray, U: np.ndarray) -> np.ndarray:
    """``(位置,朝向) 对 × 节点`` 的布尔覆盖矩阵：``|p-G| <= R`` 且 ``(p-G)·u > 0``。"""
    Q = np.asarray(nodes, dtype=float)
    P = Q[None, :, :] - G[:, None, :]
    dist = np.hypot(P[..., 0], P[..., 1])
    proj = P @ U.T
    cov = (proj > 0) & (dist[..., None] <= R + 1e-9)
    return cov.reshape(len(G) * len(U), len(nodes))


def _or_opt_hist(start, route, guard: int = 0):
    """快照 ``_q4_layout_analysis.py`` 的 or-opt：**首次改进即整体重扫**（直到无改进）。

    与 ``jo.or_opt``（固定轮数、边扫边落）收敛语义不同，为了 1:1 复现快照的多起点结果，
    这里保留快照版本；路线长度一律用 ``jo.route_len`` 计算。
    """
    route = list(route)
    improved = True
    steps = 0
    while improved:
        improved = False
        steps += 1
        if guard and steps > guard:
            break
        n = len(route)
        for seg in (1, 2, 3):
            for i in range(n - seg + 1):
                piece = route[i:i + seg]
                rest = route[:i] + route[i + seg:]
                if not rest:
                    continue
                base = _plen(start, rest)
                for k in range(len(rest) + 1):
                    cand = rest[:k] + piece + rest[k:]
                    if _plen(start, cand) < base - 1e-9:
                        route = cand
                        improved = True
                        break
                if improved:
                    break
            if improved:
                break
    return route


def _plen(start, rt) -> float:
    """开放路径长度（起点 + 依次经过）：与 ``jo.route_len(range(n), rt)`` 同一个式子。"""
    return jo.route_len(list(range(len(rt))), [[float(x), float(y)] for x, y in rt])


def _nn_from(start, rem) -> list:
    """快照的"打乱后的最近邻"：``rem`` 的顺序会影响并列时的取舍，故不能用 jo.nn_order。"""
    rem = list(rem)
    route, cur = [], start
    while rem:
        j = min(range(len(rem)), key=lambda i: math.hypot(rem[i][0] - cur[0], rem[i][1] - cur[1]))
        cur = rem.pop(j)
        route.append(cur)
    return route


def cmd_analysis(args: argparse.Namespace) -> int:
    """``_q4_layout_analysis.py``：25 点布局的采样判据 / 冗余 / 覆盖子集 / 路线。"""
    nodes = two_tier_nodes(*V9_TIER)
    G = _analysis_grid(args.pos_step)
    n_dir = int(round(360.0 / args.dir_deg))
    U = jo.orientations(n_dir)
    say(f"采样集：Ω 内网格 位置步长={args.pos_step:.0f} m（{len(G)} 点）、朝向 {n_dir} 个"
        f"（{args.dir_deg:.0f}° 步长），共 {len(G) * n_dir} 个 (位置,朝向) 对。")
    say(SAMPLE_NOTE + CERT_HINT)
    say()
    say(f"两圈式节点数 = {len(nodes)}")
    w = _worst_frontal_vec(nodes, G, U)
    verdict = "采样达标" if w <= R else "采样不达标"
    say(f"最坏正面距离 = {w:.1f} m（≤1000 只在本采样集上成立：{verdict}）")

    # 2) 单节点冗余性：删掉任一节点后是否仍在该采样下达标
    idx = range(min(len(nodes), args.limit) if args.limit else len(nodes))
    redundant = []
    for k in idx:
        sub = nodes[:k] + nodes[k + 1:]
        if _worst_frontal_vec(sub, G, U) <= R:
            redundant.append(k)
    say(f"可去除单节点（其余仍采样达标）: {redundant}")
    say("  [限制] 本项只检验「删掉单个节点」这一个方向：结果为空说明这 25 个单删候选都破坏了"
        "采样覆盖，即**该方向的候选**不可行。23 点的三个删点修复失败，"
        "不证明所有 23 点布局不可行。（2026-09-13 独立复核）")

    # 3) 贪心覆盖子集（本次采样下的集合覆盖，贪心不保证全局最小）
    cov = _covered_pairs(nodes, G, U)
    full = cov.any(axis=1)
    covset = cov[full]
    covered = np.zeros(len(covset), dtype=bool)
    chosen: list[int] = []
    while not covered.all():
        gains = (covset & ~covered[:, None]).sum(axis=0)
        if chosen:
            gains[chosen] = -1
        k = int(gains.argmax())
        if gains[k] <= 0:
            say("贪心失败：有 (位置,朝向) 对无法覆盖？")
            break
        covered |= covset[:, k]
        chosen.append(k)
    say(f"贪心覆盖子集（本次采样） = {len(chosen)} / {len(nodes)}"
        f"（快照写「最小定向完备子集」：那是采样口径，且贪心不保证全局最小）")

    # 4) 路线：NN+2opt vs 多起点 NN+2opt+or-opt
    order, Q, L0 = _nn2opt(nodes, GREEDY_2OPT_PASSES)
    say(f"NN+2opt 路线 = {L0:.0f} m")
    import random  # 局部导入：保持题面要求的顶层 import 块逐字不变（快照用 random.Random(1)）
    seeds = [int(x) for x in str(args.seeds).split(",") if x.strip()]
    best_len, best_rt, trial_lens = None, None, []
    for rng_seed in seeds:
        rng = random.Random(rng_seed)
        for _ in range(args.trials):
            rem = list(nodes)
            rng.shuffle(rem)                 # 1:1 保留快照：先打乱，再做最近邻
            rt = _nn_from(START, rem)
            rt = _or_opt_hist(START, rt)
            o2 = jo.two_opt(list(range(len(rt))), [[float(x), float(y)] for x, y in rt],
                            passes=ANALYSIS_TWO_OPT_PASSES)
            rt = [rt[i] for i in o2]
            rt = _or_opt_hist(START, rt)
            L = _plen(START, rt)
            trial_lens.append(L)
            if best_len is None or L < best_len:
                best_len, best_rt = L, list(rt)
    say(f"多起点 NN+2opt+or-opt 最优路线 = {best_len:.0f} m"
        f"（{len(trial_lens)} 次试验，不同结果 {len({round(v, 6) for v in trial_lens})} 个；"
        f"最好比 NN+2opt 短 {L0 - best_len:.0f} m）")
    say("  " + INFEASIBILITY_NOTE)
    say("  本次搜到更短的路线只说明**这条搜索方向**有改进（快照据此以为 18828 m 还能压）；"
        "它不证明 25 点布局的路线已到极限，也不证明坐标搜索「必然变长」。")
    say("best_route = [")
    say(", ".join(f"({x:.1f},{y:.1f})" for (x, y) in best_rt))
    say("]")
    say()
    say("注：交付布局（strategy_v17.LAYOUT_Q4_V17，24 点 / 18507.5 m）比这里的 25 点 / "
        f"{best_len:.0f} m 更短，它的证书来自 script/verify_layout_certificate.py。")
    _finish(args, {"m": len(nodes), "layout": "two_tier_nodes(950,1750,12,1900)（历史 v9）",
                   "pos_step_m": args.pos_step, "dir_deg": args.dir_deg, "n_dirs": n_dir,
                   "worst_frontal_m": round(w, 2), "sample_complete": bool(w <= R),
                   "redundant_nodes": redundant, "greedy_subset_size": len(chosen),
                   "greedy_subset": chosen, "nn2opt_route_m": round(L0, 1),
                   "multi_start_best_route_m": round(best_len, 1),
                   "trials": len(trial_lens), "seeds": seeds,
                   "best_route": [[round(float(x), 1), round(float(y), 1)] for x, y in best_rt],
                   "sample_judge": SAMPLE_NOTE + CERT_HINT, "restrictions": INFEASIBILITY_NOTE})
    return 0


# --------------------------------------------------------------------------- 子命令 fail-diag
def cmd_fail_diag(args: argparse.Namespace) -> int:
    """``_q4_fail_diag.py``：失败种子的漏清根因（mock + 策略侧轨迹）。

    快照用 ``sys.argv`` 里的 ``seeds=...`` / ``v9`` 两个裸 token 选参数；本文件换成
    ``--seeds`` / ``--strategy``（CLI 表面变化，语义不变）。
    """
    from mathmodel2026b.client import HttpSimulatorClient
    from mathmodel2026b.geometry import polygon_diameter
    from mathmodel2026b.mock.server import MockSimulator
    from mathmodel2026b.mock.world import generate_case
    from mathmodel2026b.state import DogState
    from mathmodel2026b.strategy_v9 import Q4V9Params, Q4V9Strategy
    from mathmodel2026b.strategy_v14 import Q4V14Params, Q4V14Strategy

    robot = "000000000000"
    params: dict = {"schedule_min_readings": 2, "scan_probe_limit": 2}   # 快照参数
    if args.strategy == "v9":
        strat_cls, params_cls = Q4V9Strategy, Q4V9Params
    else:
        strat_cls, params_cls = Q4V14Strategy, Q4V14Params
    legacy = args.clear_timing == "legacy"
    seeds = [int(x) for x in str(args.seeds).split(",") if x.strip()]
    if args.limit:
        seeds = seeds[:args.limit]

    say(f"策略={args.strategy} 参数={params}；种子的场景 omni_only=False（快照默认）。")
    if legacy:
        say("计时口径 = **旧口径**（快照）：所有 /clear 都记 5 s（含失败）。"
            "官方口径见附件1 §2.3：未发现 3 s / 已清除 5 s，"
            "`mathmodel2026b.mock.world.World(..., legacy_clear_timing=True)` 才复现旧数字；"
            "`T_旧 = T_官方 + 2 × 清除失败次数`。要跑官方口径加 `--clear-timing spec`。")
    else:
        say("计时口径 = 官方口径（附件1 §2.3）：未发现 3 s / 已清除 5 s。"
            "快照当年的数字是**旧口径**（所有 /clear = 5 s），两者不可直接比较。")
    say("布局提示：v9/v14 用的都是发现层 **25 点两圈式**布局（strategy_v9.two_tier_nodes）；"
        "交付布局是 strategy_v17.LAYOUT_Q4_V17 的 24 点，**与本诊断不同源**。")
    say()

    per_seed: list[dict] = []
    for seed in seeds:
        case = generate_case(seed, omni_only=False)
        by_ch = case.by_channel()
        t0 = time.time()
        with MockSimulator(robot_id=robot, case=case, legacy_clear_timing=legacy) as sim:
            client = HttpSimulatorClient(robot_id=robot, base_url=sim.base_url, verbose=False)
            state = DogState()
            strat = strat_cls(params_cls(**params))
            client.enter()
            strat.run(client, state)
            client.exit()
            s = sim.world.summary()
        wall = time.time() - t0
        tag = "[旧口径]" if legacy else "[官方口径]"
        say(f"== seed={seed} cleared={s['cleared_count']}/{s['n_jammers']} "
            f"漏={s['uncleared_channels']} {tag} vt={s['virtual_time_s']:.0f}s 墙钟={wall:.1f}s")
        entry: dict = {"seed": seed, "cleared": s["cleared_count"], "n_jammers": s["n_jammers"],
                       "uncleared_channels": list(s["uncleared_channels"]),
                       "virtual_time_s": round(float(s["virtual_time_s"]), 1),
                       "wall_time_s": round(wall, 2), "clear_timing": args.clear_timing,
                       "clear_count": s["clear_count"], "measure_count": s["measure_count"],
                       "channel_switch_count": s["channel_switch_count"],
                       "move_distance_m": round(float(s["move_distance_m"]), 1), "channels": []}
        for ch in s["uncleared_channels"]:
            j = by_ch[ch]
            say(f"  ch{ch}: {'定向' if j.direction_deg is not None else '全向'}"
                f" pos=({j.position.x:.0f},{j.position.y:.0f}) R={j.effective_radius_m:.0f}"
                f" dir={j.direction_deg}")
            st = state.channels[ch]
            region = strat._region(st) if st.readings else []
            d = polygon_diameter(region) if len(region) >= 3 else -1
            say(f"    策略侧: readings={len(st.readings)} probe_count={st.probe_count}"
                f" 区域直径={d:.1f} 清除尝试失败={st.clear_failures}")
            ch_entry = {"channel": ch, "kind": "定向" if j.direction_deg is not None else "全向",
                        "pos": [round(float(j.position.x), 1), round(float(j.position.y), 1)],
                        "effective_radius_m": round(float(j.effective_radius_m), 1),
                        "direction_deg": j.direction_deg,
                        "readings": len(st.readings), "probe_count": st.probe_count,
                        "region_diameter_m": round(float(d), 1),
                        "clear_failures": int(st.clear_failures), "trace": []}
            shown = 0
            for t in strat.trace:
                if t.get("channel") != ch:
                    continue
                if args.trace_limit and shown >= args.trace_limit:
                    break
                shown += 1
                line = (f"      [{t['kind']:6s}] {t.get('reason', ''):18s} "
                        f"({t.get('x', 0):.0f},{t.get('y', 0):.0f}) -> {t.get('outcome', '')}"
                        + (f" svd={t.get('svd_deg'):.1f}" if t.get("svd_deg") is not None else ""))
                say(line)
                ch_entry["trace"].append(line.strip())
            entry["channels"].append(ch_entry)
        per_seed.append(entry)

    say()
    if not any(e["uncleared_channels"] for e in per_seed):
        say("本次这些种子都没有漏清：策略侧诊断段为空（当年失败的种子在当前框架的 v14/v9 上"
            "已经不再复现失败）。这正好说明**历史数字不能当当前结果引用** —— 本子命令复现的是"
            "诊断**流程**，跑的是今天的框架代码，不是 2026-09-13 快照当时的代码。"
            "要看到诊断段，请换一批种子（如 --seeds 61,62,…）或换 --strategy v9 / --clear-timing spec。")
    if len(per_seed) > 1:
        vts = [e["virtual_time_s"] for e in per_seed]
        walls = [e["wall_time_s"] for e in per_seed]
        say(f"汇总：{len(per_seed)} 个种子，vt 均值={statistics.mean(vts):.0f}s "
            f"中位={statistics.median(vts):.0f}s，墙钟均值={statistics.mean(walls):.2f}s")
    say("[限制] 上面的漏清只说明**这些种子的这条轨迹**在该口径与当前策略下失败；"
        "它不构成布局/策略不可行的结论，也不证明所有 23 点布局不可行（2026-09-13 复核）。")
    _finish(args, {"strategy": args.strategy, "params": params, "clear_timing": args.clear_timing,
                   "seeds": seeds, "trace_limit": args.trace_limit, "seeds_detail": per_seed,
                   "restrictions": "[限制] 单种子失败只说明该轨迹失败，不证明布局或策略不可行。"})
    return 0


# --------------------------------------------------------------------------- CLI
def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--quick", action="store_true", help="冒烟：把采样密度/枚举规模降到最小")
    p.add_argument("--limit", type=int, default=0,
                   help="只跑前 N 项（缺省 0 = 快照全量；各子命令的「项」见各自 help）")
    p.add_argument("--out-dir", default=None,
                   help=f"输出目录（缺省 {DEFAULT_OUT_ROOT}/<子命令>）")
    p.add_argument("--no-write", action="store_true", help="只打印，不写任何文件")
    p.add_argument("--json", action="store_true",
                   help="另打一份机器可读 JSON（---- JSON ---- 之后；同时落盘 <子命令>.json）")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="design_legacy",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", metavar="<子命令>")

    p_nodes = sub.add_parser(
        "nodes", help="← _design_q4_nodes.py：内圈网格 + 边界环候选枚举",
        description="← 快照 _design_q4_nodes.py：基准轴向网格 +「内圈三角网格 + 边界环」候选。\n\n"
                    "常量与循环 1:1：采样默认 60 m × 36 朝向、候选表 5×4 组、NN+2-opt(passes=10)。\n"
                    "本子命令**保留**快照 lattice() 只生成上半平面的缺陷（见输出里的注）。\n"
                    "--limit N：只跑候选表的前 N 个 (环, 内圈) 组合。",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_common(p_nodes)
    p_nodes.add_argument("--step", type=float, default=NODES_STEP, help="采样位置步长（m，快照 60）")
    p_nodes.add_argument("--n-phi", type=int, default=NODES_N_PHI, help="采样朝向数（快照 36）")
    p_nodes.add_argument("--passes", type=int, default=10, help="2-opt 轮数（快照 10）")
    p_nodes.set_defaults(_func=cmd_nodes, _quick=_quick_nodes)

    p_route = sub.add_parser(
        "route", help="← _design_q4_route.py：31 点轴向网格路线压缩",
        description="← 快照 _design_q4_route.py：论文蛇形路线 vs NN+2-opt（passes=40，6 次取最小）。\n"
                    "--limit N：只跑前 N 个网格间距（快照表为 950/1000 两个）。",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_common(p_route)
    p_route.add_argument("--passes", type=int, default=40, help="2-opt 轮数（快照 40）")
    p_route.add_argument("--tries", type=int, default=6, help="重复次数取最小（快照 6）")
    p_route.set_defaults(_func=cmd_route, _quick=_quick_route)

    p_v2 = sub.add_parser(
        "v2", help="← _design_q4_v2.py：修掉下半平面后的候选表",
        description="← 快照 _design_q4_v2.py：完整三角网格（含 y<0）+ 外圈环的枚举表。\n"
                    "常量与循环 1:1：采样 60 m × 36 朝向、候选表 4×5 组、NN+2-opt(passes=40)×6 次。\n"
                    "选出 950/1750/12/1900 → 25 点 / 962.5 m / 18828 m（= 交付过的 v9 布局）。\n"
                    "--limit N：只跑候选表的前 N 个组合。",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_common(p_v2)
    p_v2.add_argument("--step", type=float, default=NODES_STEP, help="采样位置步长（m，快照 60）")
    p_v2.add_argument("--n-phi", type=int, default=NODES_N_PHI, help="采样朝向数（快照 36）")
    p_v2.add_argument("--passes", type=int, default=40, help="2-opt 轮数（快照 40）")
    p_v2.add_argument("--tries", type=int, default=6, help="重复次数取最小（快照 6）")
    p_v2.set_defaults(_func=cmd_v2, _quick=_quick_v2)

    p_an = sub.add_parser(
        "analysis", help="← _q4_layout_analysis.py：25 点布局的采样/冗余/路线分析",
        description="← 快照 _q4_layout_analysis.py：25 点两圈式布局的 ①采样最坏正面距离 "
                    "②单节点冗余 ③贪心覆盖子集 ④NN+2opt vs 多起点 NN+2opt+or-opt。\n"
                    "常量与循环 1:1：采样 40 m × 10°、多起点 30 次（random.Random(1)）。\n"
                    "--limit N：冗余扫描只查前 N 个节点；--trials 控制多起点次数。",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_common(p_an)
    p_an.add_argument("--pos-step", type=float, default=ANALYSIS_POS_STEP, help="采样位置步长（m，快照 40）")
    p_an.add_argument("--dir-deg", type=float, default=ANALYSIS_DIR_DEG, help="采样朝向步长（度，快照 10）")
    p_an.add_argument("--trials", type=int, default=ANALYSIS_TRIALS, help="多起点路线试验次数（快照 30）")
    p_an.add_argument("--seeds", default="1", help="多起点用的 RNG 种子（逗号分隔；快照 random.Random(1)）")
    p_an.set_defaults(_func=cmd_analysis, _quick=_quick_analysis)

    p_fd = sub.add_parser(
        "fail-diag", help="← _q4_fail_diag.py：失败种子漏清根因（mock）",
        description="← 快照 _q4_fail_diag.py：跑 mock，列漏清频道的真实参数与策略侧完整轨迹。\n"
                    "默认复现快照的**旧口径**（所有 /clear = 5 s）；官方口径（未发现 3 s / 已清除 5 s）\n"
                    "用 --clear-timing spec。--limit N：只跑前 N 个种子。",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_common(p_fd)
    p_fd.add_argument("--seeds", default="12", help="种子列表（逗号分隔；快照 seeds=12）")
    p_fd.add_argument("--strategy", choices=("v14", "v9"), default="v14",
                      help="策略版本（快照默认 v14，裸 token v9 切到 v9）")
    p_fd.add_argument("--clear-timing", choices=("legacy", "spec"), default="legacy",
                      help="清除计时口径：legacy=旧口径(所有 /clear 5s，快照) / spec=官方口径")
    p_fd.add_argument("--trace-limit", type=int, default=0,
                      help="每个频道最多打印的轨迹行数（0 = 全部）")
    p_fd.set_defaults(_func=cmd_fail_diag, _quick=_quick_fail_diag)

    return ap


def _quick_nodes(args: argparse.Namespace) -> None:
    args.step, args.n_phi, args.passes = 180.0, 12, 4
    args.limit = args.limit or 2


def _quick_route(args: argparse.Namespace) -> None:
    args.passes, args.tries = 8, 1


def _quick_v2(args: argparse.Namespace) -> None:
    args.step, args.n_phi, args.passes, args.tries = 180.0, 12, 8, 1
    args.limit = args.limit or 2


def _quick_analysis(args: argparse.Namespace) -> None:
    args.pos_step, args.dir_deg, args.trials = 180.0, 30.0, 2
    args.limit = args.limit or 5


def _quick_fail_diag(args: argparse.Namespace) -> None:
    args.trace_limit = args.trace_limit or 5
    args.limit = args.limit or 1


def main(argv: list[str] | None = None) -> int:
    global START_TIME
    argv_list = list(sys.argv[1:] if argv is None else argv)
    _print_banner()
    ap = build_parser()
    args = ap.parse_args(argv_list)
    func = getattr(args, "_func", None)
    if func is None:
        ap.print_help()
        print("\n提示：五个子命令 nodes / route / v2 / analysis / fail-diag 都是历史归档，"
              "交付布局见 strategy_v17.LAYOUT_Q4_V17。")
        return 2
    OUT.append(BANNER)
    START_TIME = time.time()
    if args.quick:
        args._quick(args)
        say("（--quick 冒烟模式：采样密度/枚举规模已降到最小，数字不是快照全量口径）")
    shown = {k: v for k, v in vars(args).items() if not k.startswith("_")}
    say(f"# 子命令 {args.cmd}｜历史归档运行｜参数：{shown}")
    return func(args)


if __name__ == "__main__":
    raise SystemExit(main())
