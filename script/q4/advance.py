#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Q4 发现层布局【推进工具】：强 TSP 提路线 / 23 点删点尝试 / 多网格收尾 / 片段写出。

为什么有这个工具
----------------
``framework/src/mathmodel2026b/strategy_v17.py`` 的 ``LAYOUT_Q4_V17``（24 点，离线路线
18507.5m）是离线跑出来的。产出它的最后几步散落在 2026-09-13 Windows 最终工程快照顶层的
四个脚本里：``_q4_advance.py``（24 点采样复验 → 强 TSP → 试删到 23 点）、
``_strong_tsp.py``（多起点随机 NN + 双桥 ILS，只调顺序）、``_fix_layout.py``
（多网格 ``polish_feas`` 补 (40,60) 洞）、``_joint_opt_layout.py``（只有一份片段文本）。
它们各写各的输出、各写各的措辞，既无法从框架仓库复跑，也把**有限采样判据**写成了
"近精确判据 / 精确最坏值"（2026-09-13 独立复核已指出该表述不成立）。本模块把四个脚本
的算法 **1:1** 收进一个入口，统一走 :mod:`layout_io` 的读写格式，并把口径改对。

四个子命令（算法与常数均照抄快照，偏差见各自 docstring 与报告尾部）
------------------------------------------------------------------
``strong-tsp``  ← ``_strong_tsp.py``
    多起点随机 NN（p=0.2，奇偶交替用确定性 ``nn_order``）+ 双桥（4-opt）扰动 ILS，
    每步用模块自带的 2-opt / or-opt 收尾，取最好者。**只改访问顺序、不动坐标**，
    因此对布局证书零风险。结果只是路线长度的**启发式上界**（见下方口径）。
``advance-23``  ← ``_q4_advance.py``
    ① 对当前布局做采样复验（``brute_worst`` + ``_multi_cert_ok``）；
    ② 跑一遍上面的强 TSP；
    ③ 逐个删点，用 ``certify(step=80, n_phi=36)`` 粗筛"删掉后最不坏"的前 N 个候选，
       再走 ``repair_geom(safety=10) → polish_feas(safety=14) → knife_fix(safety=12, margin=4)``
       修复，接受条件是采样判据通过**且** ``L+100m`` 严格变好。快照这次尝试失败了。
``fix``         ← ``_fix_layout.py``
    对输入布局连续做 ``polish_feas(safety=20/24/28)`` 三轮"留裕度优化"（无路程项，
    不会乱拉点），每轮用 ``safety=12`` 验收（验收口径**不随安全余量抬高**）+ 一次
    ``complete(step=20, n_phi=120)`` 加验，通过即停。
``emit``        ← ``_joint_opt_layout.py``
    把布局格式化成 ``LAYOUT_Q4_V17: list[tuple[float, float]] = [...]`` 片段并打印/落盘。
``all``         依次跑 ``strong-tsp → advance-23 → fix → emit``，布局逐步串联。

口径（照读，别把判据读强了）
----------------------------
* ``joint_opt.brute_worst`` / ``joint_opt._multi_cert_ok`` / ``joint_opt.certify`` 都是
  **有限采样判据**（有限网格 × 有限朝向 × 有限随机样本）。采样只能**证伪**（发现新的洞），
  永远不能证明连续域上处处成立；采样最大值不是"精确最坏值"，通过也不是"完备性证明"。
  连续域的**充分条件**证书请跑 ``python3 script/verify_layout_certificate.py``
  （四叉树单元细分，要求 ``unresolved == 0``）。本模块不产出任何连续域结论。
* ``strong-tsp`` 的双桥 ILS 是**启发式**：结果是"已探索顺序里最好者"，即开放路线长度的
  一个**上界**，**不是最优路线**，本模块不给下界、不做精确 TSP。
* ``advance-23`` 的删点修复失败**只否定这一次尝试的候选与修复路径**，
  **不证明**所有 23 点布局不可行（复核原文：「23 点的三个删点修复失败，
  不证明所有 23 点布局不可行」）。

运行时间（快照墙钟口径 + 本机实测）
-----------------------------------
* ``strong-tsp``：默认 ``--time-budget 200``＝快照的"60s 构造 + ILS 到 200s"；
  ``--quick`` 约 3–6s。
* ``advance-23``：默认 ``--time-budget 120``＝快照的"45s 构造 + ILS 到 120s"，
  之后还要一次 0.25° 采样扫描（约 2–4 分钟）和每个删点候选的修复（``knife_fix``
  的 ``(20,120)`` 轮在本机单次约 0.9s、最多 40 步，故单个候选可到分钟级）；
  整跑十分钟量级，**不要随手全跑**，用 ``--quick`` 或 ``--no-brute``。
* ``fix``：每轮 ``polish_feas`` 数十秒，加上默认开启的 0.25° 扫描（每轮 2–4 分钟）。
* ``emit``：<1s。

``--quick`` 的口径（本机实测）
------------------------------
搜索预算压到 3s/阶段（``all`` 为 2s）、删点候选强制压到 **1 个**、关掉 0.25° 采样扫描、
验收网格降为 coarse(120/24,60/36)、``repair_geom`` 只 10 次迭代、``polish_feas`` 只 1 轮，
``advance-23`` 里**跳过** ``knife_fix``（它第二轮用 (20,120) 网格：单次 ``violations``
0.9s × 最多 40 步 ≈ 40s，与 ~10s 目标冲突）。所以 ``--quick`` 只验证"链路能跑通、
判据行为符合预期"，**不产出任何可交付结论**。

用法::

    python3 script/q4/advance.py --help
    python3 -m script.q4.advance strong-tsp --quick        # 包内运行等价
    python3 script/q4/advance.py strong-tsp --time-budget 600
    python3 script/q4/advance.py advance-23 --max-candidates 3 --no-brute
    python3 script/q4/advance.py fix --in logs/q4/advance/layout.json
    python3 script/q4/advance.py emit --no-write           # 只打印片段

输出（默认 ``<out-dir> = logs/q4/advance/``，见 ``layout_io.LOG_ROOT``）
----------------------------------------------------------------------
``<subcommand>.txt``  该子命令的文字报告（含 ``joint_opt`` 内部 ``emit`` 的日志）
``layout.json``       候选/最终布局，``{"m","route_len_m","points_in_order"}``
``layout_fragment.py``可粘贴的 ``LAYOUT_Q4_V17`` 片段（带表头注释）

交付/粘贴兼容性
---------------
``emit`` 出的片段与 ``framework/src/mathmodel2026b/strategy_v17.py`` 里的 ``LAYOUT_Q4_V17``
**逐字符可粘贴**：同样的变量名、坐标一律 ``%.1f``、一点一行、行内顺序**就是**访问顺序
（起点 (0,0) 的开放路径）。``mathmodel2026b.versioned`` 里的 **q4-v18**（问题4 最终交付）
从 **q4-v17** **继承** ``LAYOUT_Q4_V17``：v18 只新增知识矩阵负例与覆盖式清除兜底，
**不改发现层布局**，所以本模块 emit 的片段同时服务 q4-v17 与 q4-v18。
本模块**不写任何框架文件**，粘贴动作交给人。
"""

from __future__ import annotations

import argparse, json, math, sys, time
from pathlib import Path
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent          # mathModel2026B/script/q4
REPO_ROOT = SCRIPT_DIR.parent.parent                  # mathModel2026B
sys.path.insert(0, str(REPO_ROOT / "framework" / "src"))
sys.path.insert(0, str(SCRIPT_DIR))

from mathmodel2026b.strategy_v9 import two_tier_nodes  # noqa: E402

try:  # 包内运行：python3 -m script.q4.advance
    from . import joint_opt as jo
    from . import layout_io
except ImportError:  # 单文件直跑：python3 script/q4/advance.py
    import joint_opt as jo
    import layout_io

#: 默认输出目录（离线产物不进 framework/，也不进快照目录）。
DEFAULT_OUT_DIR = layout_io.LOG_ROOT / "advance"

#: 每个停点的等效成本（米）：目标 L+100m（快照口径）。
LAM = 100.0

#: 快照墙钟口径：构造阶段截止 / 总截止（两者都从搜索起点 t0 起算）。
#: ``_q4_advance.py`` = 45s 构造 + ILS 到 120s；``_strong_tsp.py`` = 60s 构造 + ILS 到 200s。
SNAPSHOT_BUDGET = {"advance-23": 120.0, "strong-tsp": 200.0}
CONSTRUCT_FRAC = {"advance-23": 45.0 / 120.0, "strong-tsp": 60.0 / 200.0}

#: ``--quick`` 的搜索预算（秒）：整跑目标 ~10s，其余时间留给候选扫描/修复/验收。
QUICK_BUDGET = {"advance-23": 3.0, "strong-tsp": 3.0}
#: ``all --quick``：四个子命令串起来，搜索阶段再压一档。
QUICK_BUDGET_ALL = 2.0

_W_CERT = (
    "⚠ 口径：以上都是**有限采样判据**（有限网格 × 离散朝向 × 有限随机样本）。"
    "采样只能**证伪**——它能找出新的洞，但“没找到洞”不等于“连续域上没有洞”；"
    "采样最大值也不是“精确最坏值”，通过更不是“完备性证明”。"
    "连续域（每点、每朝向）的充分条件证书请跑："
    "python3 script/verify_layout_certificate.py（四叉树，要求 unresolved == 0）。"
)
_W_TSP = (
    "⚠ 口径：双桥 ILS 是**启发式**，结果只是“已探索顺序里最好者”，即开放路线长度的"
    "一个**上界**，**不是最优路线**；本工具不做精确 TSP、不给出下界。"
)
_W_DELETE = (
    "⚠ 口径：删点-修复失败只否定**这次尝试的候选与修复路径**，"
    "**不证明**所有 23 点布局不可行（复核原文：「23 点的三个删点修复失败，"
    "不证明所有 23 点布局不可行」）。本工具不作任何不可行性断言。"
)
_W_SHELL = (
    "⚠ 交付兼容：片段与 framework/src/mathmodel2026b/strategy_v17.py 的 LAYOUT_Q4_V17 "
    "逐字符可粘贴（坐标 %.1f、一点一行、行内顺序即访问顺序）；mathmodel2026b.versioned 的 "
    "q4-v18（最终交付）从 q4-v17 **继承**该布局，不改发现层。"
)


# --------------------------------------------------------------------- 报告/输出
def _say_factory(report: list[str]):
    """返回一个 ``say``：既进报告列表，又实时打印（快照的 out+print 习惯）。"""

    def say(s: str = "") -> None:
        report.append(s)
        print(s, flush=True)

    return say


def _capture_joint_opt_emit(say) -> None:
    """快照技巧：``joint_opt`` 调用的是**模块级** ``emit``，替换它即可把
    ``repair_geom`` / ``polish_feas`` / ``knife_fix`` / ``scp_step`` 的日志
    收进本模块的报告（并同时打印）。"""
    jo.emit = say


def _load_input(args) -> tuple[np.ndarray, str]:
    """``--in`` 给定时读 json；否则取框架里的交付真值 ``LAYOUT_Q4_V17``。"""
    if args.in_path:
        pts, meta = layout_io.load_layout_json(args.in_path)
        return np.array(pts, dtype=float), (
            f"--in {args.in_path}（m={meta.get('m')}, route_len_m={meta.get('route_len_m')}）")
    pts = layout_io.framework_layout()
    return np.array(pts, dtype=float), (
        "默认来源：framework/src/mathmodel2026b/strategy_v17.py 的 LAYOUT_Q4_V17"
        "（交付真值；未给 --in）")


def _v9_reference() -> tuple[int, float, float]:
    """方法一（v9 两圈式 25 点）参考基线：快的兜底解，用于对比 obj 变化。"""
    Q = np.array(two_tier_nodes(950.0, 1750.0, 12, 1900.0), dtype=float)
    L = jo.route_len(jo.best_order(Q.tolist()), Q)
    return len(Q), L, L + LAM * len(Q)


def _rounded(points) -> list[list[float]]:
    return [[round(float(x), 1), round(float(y), 1)] for x, y in points]


def _write_report(report: list[str], out_dir: Path | None, name: str, say) -> Path | None:
    if out_dir is None:
        say(f"（--no-write）只打印，未写 {name}.txt")
        return None
    p = out_dir / f"{name}.txt"
    p.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"报告: {p}", flush=True)
    return p


def _write_layout(out_dir: Path | None, say, points, route_len_m: float, *,
                  subcommand: str, source: str, order, extra_note=()) -> tuple[Path | None, Path | None]:
    """落盘 ``layout.json`` + ``layout_fragment.py``（统一走 ``layout_io``）。

    ``points`` 本身按访问顺序排列，故元数据 ``order`` 传 identity，避免"点表已重排、
    元数据还写着旧下标"的两套口径（快照的四个落点正是这里容易分叉）。
    """
    if out_dir is None:
        say("（--no-write）未写 layout.json / layout_fragment.py")
        return None, None
    jp = layout_io.save_layout_json(
        out_dir / "layout.json", points, route_len_m=route_len_m,
        extra={"source": source, "subcommand": f"script/q4/advance.py {subcommand}",
               "order": [int(i) for i in order],
               "sampling_criterion_only": True,
               "continuous_domain_certificate": False,
               "certificate_cmd": "python3 script/verify_layout_certificate.py"},
    )
    note = [
        f"生成命令：python3 script/q4/advance.py {subcommand}",
        f"来源：{source}",
        f"口径：本片段的完备性只经过**有限采样判据**（网格+凸包样本+可选 0.25° 扫描），"
        f"采样只能证伪；连续域请跑 python3 script/verify_layout_certificate.py"
        f"（四叉树充分条件，unresolved == 0）。",
        *_W_SHELL.removeprefix("⚠ 交付兼容：").split("；"),
        *extra_note,
    ]
    frag = layout_io.emit_layout(points, route_len_m=route_len_m, note=note)
    fp = out_dir / "layout_fragment.py"
    fp.write_text(frag, encoding="utf-8")
    say(f"输出: {jp} / {fp}")
    return jp, fp


def _brute_kwargs(args) -> dict:
    """验收门的口径：``--no-brute`` 关掉 0.25° 采样扫描；``--quick`` 另用粗网格。"""
    if args.quick:
        return {"brute_cap": None, "grids": jo.CERT_GRIDS_COARSE, "brute": False}
    return {"brute_cap": None if args.no_brute else args.brute_cap,
            "grids": jo.CERT_GRIDS_FULL, "brute": not args.no_brute}


# --------------------------------------------------------------------- 顺序搜索（两个脚本共用）
def _rand_nn(rng, Q: np.ndarray, m: int, p_rand: float = 0.2) -> list[int]:
    """随机化最近邻：起点随机，每步以概率 p_rand 取次近点（快照 ``rand_nn``）。"""
    first = int(rng.integers(m))
    rem = list(range(m))
    rem.remove(first)
    order, cur = [first], first
    while rem:
        ds = sorted(rem, key=lambda i: np.hypot(*(Q[i] - Q[cur])))
        pick = ds[0] if (len(ds) < 2 or rng.random() > p_rand) else ds[1]
        order.append(pick)
        rem.remove(pick)
        cur = pick
    return order


def _double_bridge(order: list[int], rng) -> list[int]:
    """4-opt 双桥扰动（保持顺序片段）；``n < 8`` 时原样返回（快照口径）。"""
    n = len(order)
    if n < 8:
        return order[:]
    a, b, c = sorted(rng.choice(np.arange(1, n), 3, replace=False).tolist())
    return order[:a] + order[b:c] + order[a:b] + order[c:]


def _heuristic_tsp(Q: np.ndarray, best: list[int], bestL: float, *, construct_s: float,
                   total_s: float, log_construct: bool, say) -> tuple[list[int], float, int, int, float]:
    """快照 ``_q4_advance.py`` / ``_strong_tsp.py`` 的同一段搜索（1:1）：

    阶段1 多起点构造（``k`` 为奇数用 ``rand_nn``、偶数用确定性 ``nn_order``，上限 120 个起点）；
    阶段2 双桥扰动 ILS。两个阶段都从同一 ``t0`` 起算墙钟：``construct_s`` 是构造截止，
    ``total_s`` 是总截止（快照的 45/120 与 60/200 即此二者）。改善判据 ``L < bestL - 1e-9``。
    返回 ``(best, bestL, k, k2, 用时)``。
    """
    m = len(Q)
    rng = np.random.default_rng(2026)          # 快照固定种子
    t0 = time.time()
    k = 0
    while time.time() - t0 < construct_s and k < 120:
        k += 1
        o = _rand_nn(rng, Q, m) if k % 2 else jo.nn_order(Q)
        o = jo.or_opt(jo.two_opt(o, Q), Q)
        L = jo.route_len(o, Q)
        if L < bestL - 1e-9:
            best, bestL = o[:], L
            if log_construct:
                say(f"  构造#{k}: L={L:.1f}")
    if log_construct:
        say(f"阶段1完成 {k} 起点, best={bestL:.1f}")
    k2 = 0
    while time.time() - t0 < total_s:
        k2 += 1
        o = jo.or_opt(jo.two_opt(_double_bridge(best, rng), Q), Q)
        L = jo.route_len(o, Q)
        if L < bestL - 1e-9:
            best, bestL = o[:], L
            if log_construct:
                say(f"  ILS#{k2}: L={L:.1f}")
        if log_construct and k2 % 50 == 0:
            say(f"  ILS {k2} 轮 best={bestL:.1f} [{time.time() - t0:.0f}s]")
    return best, bestL, k, k2, time.time() - t0


def _perm_check(Qin: np.ndarray, Qout: np.ndarray) -> bool:
    """``strong-tsp`` 的自检：输出点集与输入点集（0.1 精度）是否只是重排。"""
    a = sorted(tuple(v) for v in np.round(Qin, 1).tolist())
    b = sorted(tuple(v) for v in np.round(Qout, 1).tolist())
    return a == b


# --------------------------------------------------------------------- strong-tsp
def run_strong_tsp(args, state: dict, out_dir: Path | None) -> dict:
    """← ``_strong_tsp.py``：只改访问顺序的多起点 NN + 双桥 ILS（启发式）。"""
    report: list[str] = []
    say = _say_factory(report)
    _capture_joint_opt_emit(say)
    t_phase = time.time()

    Q = np.asarray(state["Q"], dtype=float)
    m = len(Q)
    say("=" * 74)
    say("strong-tsp ← _strong_tsp.py：多起点随机 NN + 双桥 ILS（启发式，**只改顺序不动坐标**）")
    say(f"来源：{state['src']}")
    say(f"输入：m={m}（输入文件序路线 L={jo.route_len(list(range(m)), Q):.1f}m）")

    o0 = jo.best_order(Q.tolist())
    L0 = jo.route_len(o0, Q)
    say(f"best_order 基线（NN+2-opt+or-opt，同样只是启发式）: L={L0:.1f}m")
    # 快照以 best_order 为唯一起点；这里额外把"输入文件自带的顺序"也当候选起点，
    # 否则 ILS 可能把一个更短的既有顺序丢掉（交付布局的 18507.5m 就短于 best_order）。
    L_in = jo.route_len(list(range(m)), Q)
    best, bestL = (list(range(m)), L_in) if L_in < L0 - 1e-9 else (o0[:], L0)
    m9, L9, obj9 = _v9_reference()
    say(f"v9 参考基线（方法一 {m9} 点）: L={L9:.1f} obj={obj9:.0f}"
        f"（快照 _strong_tsp.py 的原始起点就是它）")

    s = SNAPSHOT_BUDGET["strong-tsp"]
    construct_s = CONSTRUCT_FRAC["strong-tsp"] * args.time_budget
    say(f"搜索参数：构造截止 {construct_s:.1f}s / 总截止 {args.time_budget:.1f}s"
        f"（快照 {CONSTRUCT_FRAC['strong-tsp'] * s:.0f}s / {s:.0f}s），种子 2026")
    best, bestL, k, k2, dt = _heuristic_tsp(
        Q, best, bestL, construct_s=construct_s, total_s=args.time_budget,
        log_construct=True, say=say)

    say("")
    say(f"强 TSP 结果（启发式）: L={bestL:.1f}m (基线 best_order {L0:.1f}m, "
        f"省 {L0 - bestL:.1f}m ≈ {(L0 - bestL) / 5:.0f}s)  [{k}构造+{k2}ILS, {dt:.0f}s]")
    say(f"访问序: {json.dumps([int(i) for i in best])}")
    say(_W_TSP)

    pts_out = _rounded([Q[i] for i in best])
    Qout = np.asarray(pts_out, dtype=float)
    same = _perm_check(Q, Qout)
    say(f"坐标自检：输出点集与输入点集（0.1 精度）是否仅为重排 = {same}"
        f"（True ⇒ 布局坐标未动，采样证书结论不受影响，零证书风险）")
    say(_W_CERT)
    say(_W_SHELL)
    say(f"用时 {time.time() - t_phase:.1f}s")

    L_out = jo.route_len(list(range(len(pts_out))), Qout)
    _write_layout(out_dir, say, pts_out, L_out, subcommand="strong-tsp", source=state["src"],
                  order=list(range(len(pts_out))),
                  extra_note=[f"strong-tsp：顺序启发式上界 {bestL:.1f}m（非最优）"])
    _write_report(report, out_dir, "strong-tsp", say)
    return {"Q": Qout, "points": pts_out, "order": list(range(len(pts_out))), "L": L_out,
            "src": f"strong-tsp（顺序启发式，坐标 = {state['src']}）"}


# --------------------------------------------------------------------- advance-23
def run_advance_23(args, state: dict, out_dir: Path | None) -> dict:
    """← ``_q4_advance.py``：采样复验 → 强 TSP → 尝试删到 23 点。"""
    report: list[str] = []
    say = _say_factory(report)
    _capture_joint_opt_emit(say)
    t_phase = time.time()

    Q = np.asarray(state["Q"], dtype=float)
    m0 = len(Q)
    say("=" * 74)
    say("advance-23 ← _q4_advance.py：采样复验 → 强 TSP → 尝试删到 23 点")
    say(f"来源：{state['src']}")
    o0 = jo.best_order(Q.tolist())
    L0 = jo.route_len(o0, Q)
    L_in = jo.route_len(list(range(m0)), Q)
    say(f"输入 {m0} 点: 文件序 L={L_in:.0f}m, best_order L={L0:.0f}m, "
        f"obj(L+{LAM:.0f}m)={min(L0, L_in) + LAM * m0:.0f}")
    m9, L9, obj9 = _v9_reference()
    say(f"v9 参考基线（方法一 {m9} 点）: L={L9:.0f} obj={obj9:.0f}")

    bk = _brute_kwargs(args)
    # ---------- 1. 采样复验（口径：有限样本，只能证伪） ----------
    say("")
    say("--- 1. 有限采样复验（← 快照“暴力门”；这里改叫采样扫描）---")
    if not bk["brute"]:
        say("  0.25° 采样扫描：跳过（--quick / --no-brute）；"
            "快照此处对输入布局跑 brute_worst(n_phi=1440)，本机量级 2–4 分钟")
    else:
        t_b = time.time()
        bw, ninf = jo.brute_worst(Q)
        say(f"  brute_worst(0.25°, 6000 样本 + 边界环): worst={bw:.1f} 无正面点样本={ninf}"
            f"  [{time.time() - t_b:.0f}s]（有限样本上的最大值）")
    ok0, msg0 = jo._multi_cert_ok(Q, 12.0, brute_cap=bk["brute_cap"],
                                  grids=bk["grids"], brute=bk["brute"])
    say(f"  _multi_cert_ok(safety=12): {'通过' if ok0 else '未通过'}；{msg0}")
    say("  注：上面的 worst 若是 inf，只表示**这些样本里**有样本找不到正面点（采样证伪），"
        "不代表连续域最坏值就是 inf；反之通过也不是连续域证明。")
    say(_W_CERT)

    # ---------- 2. 强 TSP（顺序启发式） ----------
    say("")
    say("--- 2. 强 TSP（← _strong_tsp.py 同一段搜索；启发式，只改顺序）---")
    best, bestL = (list(range(m0)), L_in) if L_in < L0 - 1e-9 else (o0[:], L0)
    construct_s = CONSTRUCT_FRAC["advance-23"] * args.time_budget
    say(f"  搜索参数：构造截止 {construct_s:.1f}s / 总截止 {args.time_budget:.1f}s"
        f"（快照 {CONSTRUCT_FRAC['advance-23'] * SNAPSHOT_BUDGET['advance-23']:.0f}s / "
        f"{SNAPSHOT_BUDGET['advance-23']:.0f}s），种子 2026")
    best, bestL, k, k2, dt = _heuristic_tsp(
        Q, best, bestL, construct_s=construct_s, total_s=args.time_budget,
        log_construct=False, say=say)
    say(f"  强 TSP: L={bestL:.1f} (基线 {L0:.1f}, 省 {L0 - bestL:.1f}m) "
        f"[{k}构造+{k2}ILS, {dt:.0f}s]（启发式上界，非最优）")
    say(f"  访问序: {json.dumps([int(i) for i in best])}")
    say("  " + _W_TSP)

    cur_obj = bestL + LAM * m0
    final_pts, final_L, final_m, final_order = best, bestL, m0, list(range(m0))

    # ---------- 3. 尝试 23 点 ----------
    say("")
    say(f"--- 3. 尝试 {m0 - 1} 点（← 快照的删点-修复；本文件按顺序只改顺序先不动坐标）---")
    t_c = time.time()
    cands = []
    for j in range(m0):
        Q2 = np.delete(Q, j, axis=0)
        w, *_ = jo.certify(Q2.tolist(), step=80.0, n_phi=36)
        cands.append((w, j))
    cands.sort()
    say(f"  删后 coarse worst 前 5（step=80, n_phi=36 的采样；inf 表示该采样下已无正面点）: "
        + "; ".join(f"#{j}(w={'inf' if not math.isfinite(w) else f'{w:.0f}'})"
                    for w, j in cands[:5]) + f"  [扫描 {time.time() - t_c:.1f}s]")
    n_try = min(args.max_candidates, len(cands))
    say(f"  尝试候选数：{n_try}（--max-candidates，快照为前 3）"
        + ("；--quick 已强制压到 1（单候选修复在本机就是数秒~分钟级）" if args.quick else ""))

    t1 = time.time()
    accepted = False
    for w, j in cands[:n_try]:
        say("")
        say(f"=== 删#{j} (coarse w={'inf' if not math.isfinite(w) else f'{w:.0f}'}) ===")
        Q2 = np.delete(Q, j, axis=0)
        if args.quick:
            # --quick：repair_geom 只给 10 次几何拉点（默认 80），polish_feas 只 1 轮，
            # 且**跳过 knife_fix** —— 它内部第二轮用的是 (20,120) 网格，本机单次
            # violations 调用 0.9s、最多 40 步，光这一步就 ~40s，与 ~10s 的 quick 目标冲突。
            Q2r = jo.repair_geom(Q2, safety=10.0, max_iter=10)
            V = jo.polish_feas(Q2r, safety=14.0, max_rounds=1)
            say("  （--quick）跳过 knife_fix(safety=12, margin=4)：其 (20,120) 轮 ~40s")
            K = V
        else:
            Q2r = jo.repair_geom(Q2, safety=10.0)
            V = jo.polish_feas(Q2r, safety=14.0)
            K = jo.knife_fix(V, safety=12.0, margin=4.0,
                             brute_cap=None if bk["brute_cap"] is None else bk["brute_cap"])
        ok, msg = jo._multi_cert_ok(K, 12.0, brute_cap=bk["brute_cap"],
                                    grids=bk["grids"], brute=bk["brute"])
        o2 = jo.best_order(K.tolist())
        L2_in = jo.route_len(list(range(len(K))), K)
        if L2_in < jo.route_len(o2, K) - 1e-9:
            o2 = list(range(len(K)))
        L2 = jo.route_len(o2, K)
        obj = L2 + LAM * len(K)
        say(f"  采样判据: {'通过' if ok else '未通过'}；{msg}")
        say(f"  m={len(K)} L={L2:.0f} obj={obj:.0f} (当前 {cur_obj:.0f}) "
            f"[候选累计 {time.time() - t1:.0f}s]")
        if ok and obj < cur_obj - 1e-6:
            final_pts, final_L, final_m, final_order = _rounded(K), L2, len(K), o2
            cur_obj = obj
            accepted = True
            say(f"  ★ {len(K)} 点：采样判据通过且 obj 更优（{obj:.0f} < 之前）"
                f"——注意仍是采样判据，交付前须跑连续域证书")
            break
    if not accepted:
        say("")
        say(f"  × {m0 - 1} 点尝试未通过（{n_try} 个候选）")
        say("  " + _W_DELETE)
        final_pts = _rounded([Q[i] for i in best])
        final_L = bestL

    say("")
    say(f"最终: m={final_m} L={final_L:.1f} obj={cur_obj:.0f} "
        f"(v9 基线 m={m9} L={L9:.1f} obj={obj9:.0f}, Δobj={cur_obj - obj9:+.0f})")
    say(f"点集(访问序): {final_pts}")
    say(_W_CERT)
    say(_W_SHELL)
    say(f"用时 {time.time() - t_phase:.1f}s")

    Qout = np.asarray(final_pts, dtype=float)
    order_out = list(range(len(final_pts)))
    _write_layout(out_dir, say, final_pts, jo.route_len(order_out, Qout),
                  subcommand="advance-23", source=state["src"], order=order_out,
                  extra_note=[f"advance-23：强 TSP（启发式上界）L={final_L:.1f}m；"
                              f"{m0 - 1} 点尝试{'通过' if accepted else '未通过'}"
                              f"（未通过不构成不可行性结论）"])
    _write_report(report, out_dir, "advance-23", say)
    return {"Q": Qout, "points": final_pts, "order": order_out,
            "L": jo.route_len(order_out, Qout),
            "src": f"advance-23（{m0}→{final_m} 点候选，源：{state['src']}）"}


# --------------------------------------------------------------------- fix
def run_fix(args, state: dict, out_dir: Path | None) -> dict:
    """← ``_fix_layout.py``：多网格 ``polish_feas`` 留裕度优化，验收仍按 safety=12。"""
    report: list[str] = []
    say = _say_factory(report)
    _capture_joint_opt_emit(say)
    t_phase = time.time()

    Q = np.asarray(state["Q"], dtype=float)
    say("=" * 74)
    say("fix ← _fix_layout.py：多网格 polish_feas（留裕度 20/24/28），验收仍按 safety=12")
    say(f"来源：{state['src']}")
    L0 = jo.route_len(jo.best_order(Q.tolist()), Q)
    say(f"输入: m={len(Q)} L={L0:.0f}m")

    bk = _brute_kwargs(args)
    sweeps = (20.0,) if args.quick else (20.0, 24.0, 28.0)
    extra_verify = (40.0, 60) if args.quick else (20.0, 120)
    if args.quick:
        say("（--quick）只跑 safety=20 一轮；polish_feas max_rounds=1；"
            "加验网格降到 (40,60)（默认 (20,120) 单次扫描 0.9s 且逐轮重复）")
    t1 = time.time()
    Q2 = Q
    done = False
    for s in sweeps:
        say("")
        say(f"--- polish_feas(safety={s:.0f})：留裕度优化，验收仍按 12 ---")
        Q2 = jo.polish_feas(Q2, safety=s, **({"max_rounds": 1} if args.quick else {}))
        ok, msg = jo._multi_cert_ok(Q2, 12.0, brute_cap=bk["brute_cap"],
                                    grids=bk["grids"], brute=bk["brute"])
        okf, wf = jo.complete(Q2.tolist(), step=extra_verify[0], n_phi=extra_verify[1],
                              safety=12.0)
        say(f"  验收(s=12): {'通过' if ok else '未通过'}；{msg}；"
            f"加验({extra_verify[0]:.0f},{extra_verify[1]}): "
            f"worst={'inf' if not math.isfinite(wf) else f'{wf:.1f}'} "
            f"({'通过' if okf else '未通过'})")
        if ok and okf:
            done = True
            break

    ok, msg = jo._multi_cert_ok(Q2, 12.0, brute_cap=bk["brute_cap"],
                                grids=bk["grids"], brute=bk["brute"])
    say("")
    say(f"多网格微调 [{time.time() - t1:.0f}s]: {'通过' if ok else '未通过'}；{msg}")
    okf, wf = jo.complete(Q2.tolist(), step=extra_verify[0], n_phi=extra_verify[1], safety=12.0)
    say(f"  加验(step={extra_verify[0]:.0f}, n_phi={extra_verify[1]}): "
        f"worst={'inf' if not math.isfinite(wf) else f'{wf:.1f}'} "
        f"({'通过' if okf else '未通过'})")
    o2 = jo.best_order(Q2.tolist())
    L2 = jo.route_len(o2, Q2)
    m9, L9, obj9 = _v9_reference()
    say(f"结果: m={len(Q2)} L={L2:.0f} (输入 {L0:.0f}, 基线 {m9} 点 {L9:.0f}, "
        f"ΔL={L2 - L9:+.0f})  obj={L2 + LAM * len(Q2):.0f} (基线 {obj9:.0f})")
    say(_W_CERT)

    if ok and okf:
        pts = _rounded([Q2[i] for i in o2])
        say("验收通过：写出候选布局")
        _write_layout(out_dir, say, pts, L2, subcommand="fix", source=state["src"],
                      order=list(range(len(pts))),
                      extra_note=[f"fix：polish_feas 梯度 {list(sweeps)}，验收仍按 safety=12"])
        rc = 0
        state_out = {"Q": np.asarray(pts, dtype=float), "points": pts,
                     "order": list(range(len(pts))), "L": jo.route_len(list(range(len(pts))),
                                                                      np.asarray(pts, dtype=float)),
                     "src": f"fix（源：{state['src']}）"}
    else:
        say("!! 未通过全部验收（采样判据），点集: " + str(_rounded(Q2)))
        say("   这只说明这条“留裕度微调 + 采样验收”的路径没走通；"
            "既不是连续域结论，也不是“该布局不可行”的结论。")
        rc = 1
        state_out = state
    say(_W_SHELL)
    say(f"用时 {time.time() - t_phase:.1f}s")
    _write_report(report, out_dir, "fix", say)
    state_out["rc"] = rc
    return state_out


# --------------------------------------------------------------------- emit
def run_emit(args, state: dict, out_dir: Path | None) -> dict:
    """← ``_joint_opt_layout.py``：打印/写出 ``LAYOUT_Q4_V17`` 片段。"""
    report: list[str] = []
    say = _say_factory(report)
    _capture_joint_opt_emit(say)
    t_phase = time.time()

    pts = [list(p) for p in state["points"]]
    Q = np.asarray(pts, dtype=float)
    m = len(pts)
    say("=" * 74)
    say("emit ← _joint_opt_layout.py：写出/打印 LAYOUT_Q4_V17 片段")
    say(f"来源：{state['src']}")
    L_order = jo.route_len(list(range(m)), Q)
    L_greedy = jo.route_len(jo.best_order(Q.tolist()), Q)
    say(f"点数 m={m}；行内顺序（即访问顺序）路线 L={L_order:.1f}m；"
        f"参照：本模块 NN+2-opt+or-opt 启发式 {L_greedy:.1f}m")

    # 与框架里的交付真值逐点对照（仅当来源就是框架布局时才有意义）
    if not args.in_path:
        cmp = layout_io.compare_layouts(pts, layout_io.framework_layout(), tol=0.05)
        say(f"与框架 LAYOUT_Q4_V17 对照：点数一致={cmp['same_length']} "
            f"最大坐标差={cmp['max_abs_diff']}m 超出容差({cmp['tol_m']}m)的点数="
            f"{cmp['n_points_over_tol']}"
            + ("（即逐字符可粘贴：同一份点集、同一顺序）" if cmp["n_points_over_tol"] == 0
               and cmp["same_length"] else ""))
    note = [
        f"生成命令：python3 script/q4/advance.py emit"
        + (f" --in {args.in_path}" if args.in_path else ""),
        f"来源：{state['src']}",
        f"行内顺序即访问顺序（起点 (0,0) 的开放路径）；离线路线 {L_order:.1f}m",
        "完备性口径：有限采样判据只能证伪；连续域请跑 "
        "python3 script/verify_layout_certificate.py（四叉树充分条件，unresolved == 0）",
        "q4-v18（最终交付）从 q4-v17 继承该布局，v18 不改发现层",
    ]
    text = layout_io.emit_layout(pts, route_len_m=L_order, var_name=layout_io.LAYOUT_VAR_NAME,
                                note=note)
    say("片段（可直接粘贴到 framework/src/mathmodel2026b/strategy_v17.py）：")
    for line in text.rstrip("\n").split("\n"):
        say(line)
    if out_dir is None:
        say("（--no-write）只打印，未写 layout.json / layout_fragment.py")
    else:
        _write_layout(out_dir, say, pts, L_order, subcommand="emit", source=state["src"],
                      order=list(range(m)),
                      extra_note=[f"emit：行内顺序即访问顺序，路线 {L_order:.1f}m"])
    say(_W_SHELL)
    say(f"用时 {time.time() - t_phase:.1f}s")
    _write_report(report, out_dir, "emit", say)
    state["rc"] = 0
    return state


# --------------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python3 script/q4/advance.py emit --no-write\n"
            "  python3 script/q4/advance.py strong-tsp --quick\n"
            "  python3 script/q4/advance.py advance-23 --quick\n"
            "  python3 script/q4/advance.py fix --in logs/q4/advance/layout.json --no-brute\n"
            "\n"
            "口径提醒：brute_worst / _multi_cert_ok 是**有限采样判据**（只能证伪）；\n"
            "连续域证书请跑 python3 script/verify_layout_certificate.py（unresolved == 0）；\n"
            "strong-tsp 结果是启发式**上界**，不是最优路线；删点失败不构成不可行性结论。\n"
        ),
    )
    ap.add_argument("command", choices=("strong-tsp", "advance-23", "fix", "emit", "all"),
                    help="要跑的子命令：strong-tsp / advance-23 / fix / emit / all（顺序串联）")
    ap.add_argument("--in", dest="in_path", default=None,
                    help="起始布局 json（默认：framework/src/mathmodel2026b/strategy_v17.py 的 "
                         "LAYOUT_Q4_V17，即交付真值）")
    ap.add_argument("--out-dir", default=None, help=f"输出目录，默认 {DEFAULT_OUT_DIR}")
    ap.add_argument("--time-budget", type=float, default=None,
                    help="启发式搜索阶段的墙钟上限（秒）：advance-23 快照 120（45 构造 + ILS 到 120），"
                         "strong-tsp 快照 200（60 构造 + ILS 到 200）；缺省按子命令取快照值")
    ap.add_argument("--quick", action="store_true",
                    help="冒烟：整个运行约 10s（搜索预算压到 2–3s、删点候选压到 1 个、"
                         "关掉 0.25° 采样扫描、验收用粗网格、跳过 knife_fix 的 (20,120) 轮）")
    ap.add_argument("--max-candidates", type=int, default=3,
                    help="23 点删点尝试的候选数（快照为前 3）")
    ap.add_argument("--brute-cap", type=float, default=jo.R - 5.0,
                    help=f"0.25° 采样扫描的验收阈值（米，快照 {jo.R - 5.0:.0f}）")
    ap.add_argument("--no-brute", action="store_true",
                    help="验收门里跳过 0.25° 采样扫描（只留网格证书 + 凸包采样）")
    ap.add_argument("--no-write", action="store_true", help="只打印，不写任何文件")
    return ap


def _resolve_budget(args, command: str) -> float:
    if args.quick:
        base = QUICK_BUDGET_ALL if command == "all" else QUICK_BUDGET.get(command, 3.0)
        if args.time_budget is not None:
            return min(float(args.time_budget), base)
        return base
    if args.time_budget is not None:
        return float(args.time_budget)
    return SNAPSHOT_BUDGET.get(command, 200.0)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    t_all = time.time()
    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_OUT_DIR
    if args.no_write:
        out_dir = None
    else:
        out_dir.mkdir(parents=True, exist_ok=True)
    args.time_budget = _resolve_budget(args, args.command)
    if args.quick:
        # 快照的删点修复单候选就是分钟级（repair_geom + polish_feas + knife_fix）；
        # 要把整个 quick 压到 ~10s，候选数必须降到 1（本机实测 3 个候选 = 17.8s）。
        args.max_candidates = min(int(args.max_candidates), 1)

    Q0, src = _load_input(args)
    state = {"Q": Q0, "points": _rounded(Q0), "order": list(range(len(Q0))),
             "L": jo.route_len(list(range(len(Q0))), Q0), "src": src, "rc": 0}

    # 统一的起步回显（--in 用了哪份来源，一定要打出来）
    head = [
        f"命令: python3 script/q4/advance.py {args.command}"
        + (f" --in {args.in_path}" if args.in_path else "")
        + (" --quick" if args.quick else "")
        + (f" --time-budget {args.time_budget:.1f}" if args.time_budget is not None else "")
        + (" --no-brute" if args.no_brute else "")
        + (" --no-write" if args.no_write else ""),
        f"输入来源：{src}",
        f"输出目录：{'（--no-write，不写）' if out_dir is None else out_dir}",
        f"搜索预算：{args.time_budget:.1f}s/阶段；删点候选 {args.max_candidates}；"
        f"采样扫描={'关' if (args.no_brute or args.quick) else f'开(cap={args.brute_cap:.0f})'}；"
        f"验收网格={'coarse' if args.quick else 'full'}",
    ]

    rc = 0
    if args.command == "strong-tsp":
        state = run_strong_tsp(args, state, out_dir)
    elif args.command == "advance-23":
        state = run_advance_23(args, state, out_dir)
    elif args.command == "fix":
        state = run_fix(args, state, out_dir)
        rc = int(state.get("rc", 0))
    elif args.command == "emit":
        state = run_emit(args, state, out_dir)
    else:  # all：strong-tsp → advance-23 → fix → emit
        marks = []
        for cmd in ("strong-tsp", "advance-23", "fix", "emit"):
            t_p = time.time()
            if cmd == "strong-tsp":
                state = run_strong_tsp(args, state, out_dir)
            elif cmd == "advance-23":
                state = run_advance_23(args, state, out_dir)
            elif cmd == "fix":
                state = run_fix(args, state, out_dir)
                rc = int(state.get("rc", 0))
            else:
                state = run_emit(args, state, out_dir)
            marks.append(f"{cmd}: {time.time() - t_p:.1f}s")
        summary = head + ["", "=== all：各阶段用时 ===", *marks,
                          f"最终 m={len(state['points'])} L={state['L']:.1f}m",
                          f"总用时 {time.time() - t_all:.1f}s"]
        print("\n".join(summary), flush=True)
        if out_dir is not None:
            (out_dir / "all.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")

    print(f"完成（{args.command}）：总用时 {time.time() - t_all:.1f}s", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
