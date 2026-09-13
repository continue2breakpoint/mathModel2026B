#!/usr/bin/env python3
"""Q3「上帝信息参考解 / 成本差距解剖」家族的统一端口（离线，7 个快照脚本 → 1 个 CLI）。

为什么有这个工具
================

问题3 的在线策略（源位置未知，只能靠测向读数交会定位）到底"还能快多少"，
一直需要一个**参照物**。快照工程 ``问题34_最终工程文件_2026-09-13/`` 里散落着 7 个
一次性脚本：它们分别给出"上帝信息"（源位置已知、覆盖点可自由摆放、路线事后最优）
下若干口径的成本，以及 v8 / v15 在线策略的成本解剖。这些脚本各自独立、各自把一个
``_floor_q3_*.txt`` / ``_q3_*.txt`` 写在脚本旁边，很难一起对比、也无法在 CI 里冒烟。

本文件把这 7 个脚本**原样**（同公式、同常数、同贪心/局部搜索步骤、同每 seed 表列）
收进一个 CLI，只做四件被允许的事：

1. 统一入口 ``--variant``；
2. **口径措辞修正**（见下一节，这是本端口存在的真正理由）；
3. 加 ``--quick`` 廉价冒烟模式与 ``--json`` 机读输出；
4. 修 import 路径，使其能在本仓库内直接运行。

.. warning::

   **措辞修正（本端口最重要的部分）**

   快照 README/文档把 ``_floor_q3_g.py`` 的 **≈183.7 s/源** 称作「上帝下界」，并据此
   断言 **185 s/源 不可达**。**该说法不成立：**

   * ``_floor_q3_g.py`` 求解"上帝信息"问题用的是**启发式**——路线用多起点最近邻
     （NN）+ 2-opt + or-opt，7 个自由覆盖点用**随机局部搜索**（``optimize_cover``：
     点向随机源收缩或 ±300m 随机扰动，每步都要通过覆盖完备性检查 ``full_cover``）；
   * **可行的**启发式解给出的是**上帝信息最优值的一个上界**（可行解成本 ≥ 最优值），
     因此它**不是**上帝信息问题的下界；而在线问题的最优值 ≥ 上帝信息最优值
     （在线策略也是上帝信息问题的一个合法解），于是"参考解"与"在线成本"之间
     **不存在**可比的大小关系——**不能用它断言 185 s/源 不可达**；
   * 要得到**真正的下界**，必须把某个**松弛问题求解到可证明最优**（给出对偶证书或
     组合下界）。本仓库目前**没有**这样的下界。

   同一口径问题适用于 ``fixed`` / ``d`` / ``f`` / ``base`` 四个变体：它们**全部**是
   启发式可行解，因此也只能称「上帝信息参考解」，不能称「下界 / 地板」。
   其中 ``god`` 变体的 ``greedy_probe_count`` 是**贪心集合覆盖**，对"最少探针数"只是
   **启发式近似、且可能高估最小值**——快照脚本实测 190.8 s/源、而报告写 ≈183.7 s/源，
   差异正来自把贪心得到的 ``k_abs``（中位 8.5）换成 7。因此本端口把该列改名为
   「absent 贪心近似探针数」，并且**不再对任何贪心/启发式量使用"最小/最少/最优"字样**
   （除非同一行就带「启发式/贪心近似」限定语）。

   本文件里每个变体都会打印一行 ``[口径] ...`` 提醒；``--json`` 输出里
   ``heuristic_upper_bound_on_god_optimum`` 恒为 ``true``、``certified_lower_bound``
   恒为 ``false``，成本指标一律叫 ``god_information_reference_s_per_source``。

计时口径（附件1 §2.3 与独立复核 §4）
====================================

官方计时（附件1 §2.3）：``/clear`` **未发现耗时 3 s**、**成功耗时 5 s**，且 ``/clear``
**不切换**测向机频道（因此不产生 1 s 切换）；一次检测/清除的频道切换另计 1 s。

为让归档数字仍可复现，本端口**默认沿用快照常数**：

==============================  ==========================================
变体                            快照计时常数
==============================  ==========================================
``god``/``fixed``/``d``/``f``   SPEED=5.0 m/s，PROBE_S=6.0 s（5 s 测量+1 s 切换），CLEAR_S=2.0 s
``base``                        SPEED=5.0 m/s，每次检测 5+1=6 s，每源清除 5.0 s（快照脚本自用口径）
``route-gap``/``v8-stats``      不直接用常数：跑框架 mock，默认已是附件1 口径（未发现 3 s / 成功 5 s）
==============================  ==========================================

可用 ``--speed`` / ``--probe-s`` / ``--clear-s`` 覆盖前两类；**每次运行都会打印实际
使用的常数**（模拟器类变体则打印框架 mock 的常数）。

**复核结论**指出：快照的 clear 记账（成功/失败一律 3+2 s，或一律 2 s）与附件1 §2.3
不一致，因此**归档的 s/源 必须按新口径重新推导**，不能直接引用；要复现 2026-09-13
之前归档的模拟器数字，请给 ``route-gap`` / ``v8-stats`` 加 ``--legacy-clear-timing``。

variant 一览（括号内为快照脚本，末列为 30 seed 典型墙钟）
========================================================

=========  ==========================================================================  ======
variant    算法（1:1 照搬）                                                              30 seed
=========  ==========================================================================  ======
``god``    ``_floor_q3_g.py``：覆盖点位置完全自由（θ/ring 初始 + 随机局部搜索，保持        ≈124 s
           覆盖完备）+ 联合 TSP（多起点 NN + 2-opt + or-opt）+ absent 贪心近似探针数
``fixed``  ``_floor_q3_fixed.py``：固定 7 停靠（中心 + 六边形@1130）不动 + 联合 TSP        ≈1 s
``d``      ``_floor_q3_d.py``：源清除位置免费充当覆盖点 + 29 个密候选环贪心补点，           ≈3 s
           路线 = TSP(源 ∪ 补点)，对照固定 7 点环方案 C
``f``      ``_floor_q3_f.py``：7 点环 θ 扫描（0~27°，r∈{1123,1150}）+ 环点吸附 300m 内的   ≈25 s
           源（吸附破坏覆盖则退回），另附贪心覆盖点数探测
``base``   ``_floor_q3.py``：方案 A（源自身当覆盖点，覆盖不足则最深处补点）/ B（中心+      ≈5 s
           正七边形@1110）/ C（中心+正六边形@1123）三口径对比
``route-gap`` ``_q3_route_gap.py``：v15 实测行驶 vs 事后 TSP(实际停靠点) vs 上帝信息     ≈24 s
           TSP(启发式)，归因"路线次优"与"信息差"
``v8-stats``  ``_q3_v8_stats.py``：v8 在线策略成本画像（行程/检测/清除按 reason 分解）     ≈24 s
=========  ==========================================================================  ======

用法
====

::

    # 冒烟（god 约 1~2 s）：2 seeds、局部搜索 40 次迭代、TSP 重启 1 次
    python3 mathModel2026B/script/q3_floor.py --variant god --quick

    # 复现归档口径：全部 30 seeds + 机读结果
    python3 mathModel2026B/script/q3_floor.py --variant god --seeds 1-30 --json logs/q3_god.json

    # 随便挑几个 seed，只取前 3 个
    python3 mathModel2026B/script/q3_floor.py --variant base --seeds 1-5,9 --limit 3

    # 覆盖计时常数（按附件1 §2.3 重新推导时用）
    python3 mathModel2026B/script/q3_floor.py --variant fixed --clear-s 5 --speed 4.5

输出：文本全表（与快照同版式）**同时**打印到 stdout 并写入
``mathModel2026B/logs/q3_floor/<variant>.txt``（**绝不**写回快照目录）；
``--json PATH`` 另外写一份机读结果，**默认不写 JSON**。
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "framework" / "src"))

from mathmodel2026b.client import HttpSimulatorClient  # noqa: E402
from mathmodel2026b.geometry import regular_polygon_scan_points  # noqa: E402
from mathmodel2026b.mock.server import MockSimulator  # noqa: E402
from mathmodel2026b.mock.world import generate_case  # noqa: E402
from mathmodel2026b.protocol import (  # noqa: E402
    CHANNEL_SWITCH_SECONDS,
    CLEAR_SECONDS,
    MEASURE_SECONDS,
    MOVE_SPEED_MPS,
    OPTICAL_SECONDS,
)
from mathmodel2026b.state import DogState  # noqa: E402
from mathmodel2026b.strategy_v15 import Q3V15Params, Q3V15Strategy  # noqa: E402
from mathmodel2026b.strategy_v8 import Q3V8Params, Q3V8Strategy  # noqa: E402

#: 平台要求提交代码里不写死真实队号；离线 mock 只需占位队号（可用 ``--robot-id`` 覆盖）。
ROBOT_ID = "000000000000"

#: 环境里的频道总数：快照一律用 ``absent = 20 - n``。
TOTAL_CHANNELS = 20

#: 常见覆盖几何常数（快照各脚本一致）。
COVER_R = 1000.0  # 有效接收半径下界（认证用）
ARENA_R = 1800.0  # 圆域半径
GRID_STEP = 30.0  # 覆盖判定网格步长

DEFAULT_SEEDS = "1-30"
QUICK_SEEDS = "1-2"

VARIANTS = ("god", "fixed", "d", "f", "base", "route-gap", "v8-stats")

VARIANT_TITLES = {
    "god": "上帝信息参考解 G：自由覆盖点随机局部搜索 + 联合 TSP 启发式 + 贪心近似探针数",
    "fixed": "上帝信息参考解（固定 7 停靠：中心 + 六边形@1130）",
    "d": "上帝信息参考解 D：源清除位置免费充当覆盖点 + 贪心补点（对照方案 C）",
    "f": "上帝信息参考解 F：7 点环 θ 扫描 + 环点吸附源（对照方案 C）+ 贪心覆盖点数探测",
    "base": "上帝信息参考解 A/B/C 三口径（源自身当覆盖点 / 8 点环@1110 / 7 点环@1123）",
    "route-gap": "v15 实测路线 vs 事后 TSP vs 上帝信息 TSP（启发式）的差距解剖",
    "v8-stats": "v8 在线策略成本画像（模拟器实测，不含任何下界主张）",
}

SNAPSHOT_SCRIPTS = {
    "god": "_floor_q3_g.py",
    "fixed": "_floor_q3_fixed.py",
    "d": "_floor_q3_d.py",
    "f": "_floor_q3_f.py",
    "base": "_floor_q3.py",
    "route-gap": "_q3_route_gap.py",
    "v8-stats": "_q3_v8_stats.py",
}

#: 每个变体快照自用的计时常数 ``(speed_m_s, probe_s, clear_s)``；``None`` = 模拟器类变体。
VARIANT_TIMING: dict[str, tuple[float, float, float] | None] = {
    "god": (5.0, 6.0, 2.0),
    "fixed": (5.0, 6.0, 2.0),
    "d": (5.0, 6.0, 2.0),
    "f": (5.0, 6.0, 2.0),
    "base": (5.0, 6.0, 5.0),  # 快照 base：probes*5 + probes*1，清除每源 5s
    "route-gap": None,
    "v8-stats": None,
}

TEXT_OUT_DIR = REPO_ROOT / "logs" / "q3_floor"

BANNER_LINES = (
    "[口径修正] 快照 README/文档把 ≈183.7 s/源 称为「上帝下界」并据此断言 185 s/源 不可达 —— 该说法不成立：",
    "本脚本的路线用 NN/2-opt/or-opt 启发式求解、覆盖点用随机局部搜索，得到的是上帝信息问题的**可行解**，",
    "即可行解成本 ≥ 上帝信息最优值；它只是上帝信息最优值的**上界**，因此对在线问题（其成本 ≥ 上帝信息最优值）不构成任何下界。",
    "要得到真正的下界必须把某个松弛问题求解到可证明最优（或给出对偶证书）。",
)

VARIANT_REMINDERS = {
    "god": (
        "[口径] god：本变体的 s/源 是**上帝信息参考解**（启发式可行解），不是认证下界；"
        "greedy_probe_count 是**贪心近似集合覆盖、可能高估**真实最少探针数，"
        "快照脚本实测 190.8 s/源 与报告值 ≈183.7 s/源 的差异正来自把贪心中位 8.5 改成 7。"
    ),
    "fixed": (
        "[口径] fixed：与 god 同属启发式可行解（固定 7 停靠 + 多起点 NN/2-opt/or-opt 联合 TSP），"
        "s/源 196.9 是**参考解**而非地板，同样不构成任何下界。"
    ),
    "d": (
        "[口径] d：源位置免费充当覆盖点 + 贪心补点 + NN/2-opt 的**可行解** → 参考解；"
        "快照把方案 C 标成「当前下界」是错的，本端口已改称「参考解」。"
    ),
    "f": (
        "[口径] f：θ 扫描 + 吸附源 + NN/2-opt 的**可行解** → 参考解；"
        "min_cover_number 给出的「覆盖点数」只是**贪心近似**，不等于真实最小值。"
    ),
    "base": (
        "[口径] base：A/B/C 三方案都是启发式可行解，三者取优只是「更好的参考解」，"
        "**不是 Q3 绝对下界**（快照原文「Q3 绝对下界」已作废）。"
    ),
    "route-gap": (
        "[口径] route-gap：这里的「上帝信息 TSP」同样是启发式上界；(实际-事后 TSP)/源 "
        "只是与启发式参考之间的经验差距，**不能**读作在线策略的次优性证明。"
    ),
    "v8-stats": (
        "[口径] v8-stats：本变体只做在线成本画像（模拟器实测），本身不含任何下界主张。"
    ),
}

ADAPTATIONS = (
    "输出路径：快照把 `_floor_q3_*.txt` / `_q3_*.txt` 写在脚本旁边；端口统一写到 "
    "`mathModel2026B/logs/q3_floor/<variant>.txt`（绝不写回快照目录）。",
    "快照里硬编码的 `/30` 统计分母改为 `/len(seeds)`，以支持 `--seeds` / `--limit` / `--quick`"
    "（30 seeds 时数值完全一致）。",
    "`--quick` 只降低 `god` 的局部搜索迭代数（400→40）与 TSP 重启数（3/2→1/1）、"
    "以及 `f` 的覆盖点贪心候选间距（80m→240m），并把默认 seeds 压到 2 个；"
    "**常规模式与快照逐行一致**。",
    "`route-gap` / `v8-stats` 跑的是本仓库框架 mock，其默认已是附件1 §2.3 口径"
    "（未发现 3 s / 成功 5 s）；快照归档数是旧口径（一律 3+2 s），"
    "用 `--legacy-clear-timing` 复现。",
    "快照把队号写死为 `000000000000`；端口保留该占位符并允许 `--robot-id` 覆盖"
    "（平台要求代码里不出现真实队号）。",
    "`god` 的 `k_abs` 允许为 None（贪心失败时），此时该 seed 的 s/源 打印 `-` 且不参与统计；"
    "快照在这种情况下会直接抛 TypeError。",
    "`route-gap` / `v8-stats` 驱动的是**本仓库当前**框架里的 v15 / v8 策略：快照的冻结副本与"
    "仓库版本已经分叉（`strategy_v15.py` 约 320 行差异），因此 `route-gap` 的「实际」里程在"
    "部分 seed 上会与归档不同（实测 30 seeds 中 12 个 seed 的 实际 距离不同，"
    "而「上帝信息 TSP」列 30/30 完全一致）；`v8-stats` 加 `--legacy-clear-timing` 后与归档 30/30 完全一致。",
)


# --------------------------------------------------------------------------- 30m 网格
def _build_grid_30() -> list[tuple[float, float]]:
    """30m 笛卡尔网格中落在 1800m 圆域内的点（快照各脚本一致）。"""
    grid: list[tuple[float, float]] = []
    n_g = int(ARENA_R / GRID_STEP)
    for i in range(-n_g, n_g + 1):
        for j in range(-n_g, n_g + 1):
            x, y = i * GRID_STEP, j * GRID_STEP
            if math.hypot(x, y) <= ARENA_R + 1e-9:
                grid.append((x, y))
    return grid


GRID_30 = _build_grid_30()
ALL_IDX = frozenset((int(round(x / GRID_STEP)), int(round(y / GRID_STEP))) for (x, y) in GRID_30)


def _cell(value: Any, width: int, kind: str = "int") -> str:
    """表格单元格式化（快照的 f-string 版式；None → ``-``）。"""
    if value is None:
        return f"{'-':>{width}}"
    if kind == "int":
        return f"{value:>{width}}"
    if kind == "f0":
        return f"{value:>{width}.0f}"
    if kind == "f1":
        return f"{value:>{width}.1f}"
    return f"{str(value):>{width}}"


def _summarize(values: list[float]) -> dict[str, float | int | None]:
    vals = list(values)
    return {
        "n": len(vals),
        "median": statistics.median(vals) if vals else None,
        "min": min(vals) if vals else None,
        "max": max(vals) if vals else None,
        "mean": (sum(vals) / len(vals)) if vals else None,
    }


def path_len(route: list[tuple[float, float]], start: tuple[float, float] = (0.0, 0.0)) -> float:
    """开放路径长度：从 ``start`` 出发按 ``route`` 顺序走（快照 ``_path_len`` / ``path_len``）。"""
    L = math.hypot(route[0][0] - start[0], route[0][1] - start[1])
    for i in range(len(route) - 1):
        L += math.hypot(route[i + 1][0] - route[i][0], route[i + 1][1] - route[i][1])
    return L


# ------------------------------------------------------------------- TSP 启发式（⚠️）
def tsp_nn_2opt_oropt(
    points: list[tuple[float, float]],
    start: tuple[float, float] = (0.0, 0.0),
    restarts: int = 6,
    rng: random.Random | None = None,
) -> float:
    """多起点 NN + 2-opt + or-opt（god / fixed 的快照实现，逐行照搬）。

    ⚠️ **启发式**：结果只是"上帝信息问题"的一个**可行解**长度，即最优值的**上界**，
    不是下界。快照把它的输出当作「上帝地板」是错的。
    """
    if not points:
        return 0.0
    best = None
    rng = rng or random.Random(0)
    for _ in range(restarts):
        rem = list(points)
        rng.shuffle(rem)
        cur = start
        route = []
        while rem:
            j = min(range(len(rem)), key=lambda i: math.hypot(rem[i][0] - cur[0], rem[i][1] - cur[1]))
            cur = rem.pop(j)
            route.append(cur)
        # 2-opt
        n = len(route)
        improved = True
        while improved:
            improved = False
            for i in range(n - 1):
                a = start if i == 0 else route[i - 1]
                b = route[i]
                for j in range(i + 1, n):
                    c = route[j]
                    d = route[j + 1] if j + 1 < n else None
                    delta = math.hypot(a[0] - c[0], a[1] - c[1]) - math.hypot(a[0] - b[0], a[1] - b[1])
                    if d is not None:
                        delta += math.hypot(b[0] - d[0], b[1] - d[1]) - math.hypot(c[0] - d[0], c[1] - d[1])
                    if delta < -1e-9:
                        route[i:j + 1] = list(reversed(route[i:j + 1]))
                        improved = True
        # or-opt（段长 1-3 搬到别处）
        improved = True
        while improved:
            improved = False
            n = len(route)
            for seg in (1, 2, 3):
                for i in range(n - seg + 1):
                    piece = route[i:i + seg]
                    rest = route[:i] + route[i + seg:]
                    if not rest:
                        continue
                    base = path_len(rest, start)
                    for k in range(len(rest) + 1):
                        cand = rest[:k] + piece + rest[k:]
                        if path_len(cand, start) < base - 1e-9:
                            route = cand
                            improved = True
                            break
                    if improved:
                        break
                if improved:
                    break
        L = path_len(route, start)
        if best is None or L < best:
            best = L
    return best


def tsp_nn_2opt_capped(
    points: list[tuple[float, float]],
    start: tuple[float, float] = (0.0, 0.0),
    passes: int = 60,
) -> float:
    """单起点 NN + 2-opt（≤ ``passes`` 轮），无 or-opt：d / f 脚本的快照实现。

    ⚠️ **启发式**（同 :func:`tsp_nn_2opt_oropt`）。
    """
    if not points:
        return 0.0
    rem, route, cur = list(points), [], start
    while rem:
        j = min(range(len(rem)), key=lambda i: math.hypot(rem[i][0] - cur[0], rem[i][1] - cur[1]))
        cur = rem.pop(j)
        route.append(cur)
    n = len(route)
    for _ in range(passes):
        imp = False
        for i in range(n - 1):
            a = start if i == 0 else route[i - 1]
            b = route[i]
            for j in range(i + 1, n):
                c = route[j]
                d = route[j + 1] if j + 1 < n else None
                delta = math.hypot(a[0] - c[0], a[1] - c[1]) - math.hypot(a[0] - b[0], a[1] - b[1])
                if d is not None:
                    delta += math.hypot(b[0] - d[0], b[1] - d[1]) - math.hypot(c[0] - d[0], c[1] - d[1])
                if delta < -1e-9:
                    route[i:j + 1] = list(reversed(route[i:j + 1]))
                    imp = True
        if not imp:
            break
    L = math.hypot(route[0][0] - start[0], route[0][1] - start[1])
    for i in range(n - 1):
        L += math.hypot(route[i + 1][0] - route[i][0], route[i + 1][1] - route[i][1])
    return L


def tsp_nn_2opt_multistart(
    points: list[tuple[float, float]],
    rng: random.Random,
    restarts: int = 6,
) -> float:
    """多起点 NN + 2-opt（无 or-opt）：route-gap 脚本的 ``tsp``（起点固定 (0,0)）。

    ⚠️ **启发式**（同 :func:`tsp_nn_2opt_oropt`）。
    """
    if not points:
        return 0.0
    best = None
    for _ in range(restarts):
        rem = list(points)
        rng.shuffle(rem)
        cur = (0.0, 0.0)
        route = []
        while rem:
            j = min(range(len(rem)), key=lambda i: math.hypot(rem[i][0] - cur[0], rem[i][1] - cur[1]))
            cur = rem.pop(j)
            route.append(cur)
        n = len(route)
        improved = True
        while improved:
            improved = False
            for i in range(n - 1):
                a = (0.0, 0.0) if i == 0 else route[i - 1]
                b = route[i]
                for j in range(i + 1, n):
                    c = route[j]
                    d = route[j + 1] if j + 1 < n else None
                    delta = math.hypot(a[0] - c[0], a[1] - c[1]) - math.hypot(a[0] - b[0], a[1] - b[1])
                    if d is not None:
                        delta += math.hypot(b[0] - d[0], b[1] - d[1]) - math.hypot(c[0] - d[0], c[1] - d[1])
                    if delta < -1e-9:
                        route[i:j + 1] = list(reversed(route[i:j + 1]))
                        improved = True
        L = path_len(route)
        if best is None or L < best:
            best = L
    return best


# ---------------------------------------------------------------------- variant: god
def covered_idx(p: tuple[float, float]) -> frozenset[tuple[int, int]]:
    """单点 1000m 圆盘覆盖到的 30m 网格索引（god 脚本实现）。"""
    px, py = p
    pi, pj = int(round(px / GRID_STEP)), int(round(py / GRID_STEP))
    rad = int(COVER_R / GRID_STEP) + 1
    out = set()
    for i in range(pi - rad, pi + rad + 1):
        for j in range(pj - rad, pj + rad + 1):
            x, y = i * GRID_STEP, j * GRID_STEP
            if math.hypot(x, y) <= ARENA_R + 1e-9 and math.hypot(x - px, y - py) <= COVER_R:
                out.add((i, j))
    return frozenset(out)


def full_cover(points: list[tuple[float, float]]) -> bool:
    """覆盖点集的 1000m 邻域是否盖满整个 1800m 圆域（god 脚本实现）。"""
    cov = set()
    for p in points:
        cov |= covered_idx(p)
    return ALL_IDX <= cov


def greedy_probe_count(cover: list[tuple[float, float]], srcs: list[tuple[float, float]]) -> int | None:
    """absent 频道的探针数：从 ``(cover ∪ srcs)`` **贪心**选点盖满全场。

    ⚠️ 这是**启发式（贪心集合覆盖）**：结果**可能高估**真实最少探针数，
    不是最小值、更不是最优值。（快照把它写成「最少探针数」。）
    """
    pts = list(cover) + list(srcs)
    cov: set[tuple[int, int]] = set()
    cnt = 0
    while cov != ALL_IDX:
        best = None
        for p in pts:
            gain = len(covered_idx(p) - cov)
            if best is None or gain > best[0]:
                best = (gain, p)
        if best is None or best[0] == 0:
            return None
        cov |= covered_idx(best[1])
        cnt += 1
    return cnt


def optimize_cover(
    srcs: list[tuple[float, float]],
    rng: random.Random,
    *,
    grid_restarts: int = 3,
    local_restarts: int = 2,
    local_iters: int = 400,
) -> tuple[list[tuple[float, float]], float]:
    """随机局部搜索 7 个覆盖点位置，最小化 TSP(srcs ∪ cover)（god 脚本实现 1:1）。

    ⚠️ 随机局部搜索是**启发式**：返回的是"上帝信息问题"的**可行解**（成本上界），
    不是下界。``grid_restarts`` / ``local_restarts`` / ``local_iters`` 的快照默认值
    分别为 3 / 2 / 400，仅 ``--quick`` 会调低。
    """
    best_cover = None
    best_len = None
    for deg in range(0, 60, 10):
        for rad in (1123.0, 1150.0):
            theta = math.radians(deg)
            cover = [(0.0, 0.0)] + [
                (rad * math.cos(theta + math.pi * t / 3), rad * math.sin(theta + math.pi * t / 3))
                for t in range(6)
            ]
            if not full_cover(cover):
                continue
            L = tsp_nn_2opt_oropt(srcs + cover, restarts=grid_restarts, rng=rng)
            if best_len is None or L < best_len:
                best_len, best_cover = L, cover
    if best_cover is None:
        theta = 0.0
        best_cover = [(0.0, 0.0)] + [
            (1150.0 * math.cos(theta + math.pi * t / 3), 1150.0 * math.sin(theta + math.pi * t / 3))
            for t in range(6)
        ]
        best_len = tsp_nn_2opt_oropt(srcs + best_cover, restarts=grid_restarts, rng=rng)

    cur_cover = list(best_cover)
    cur_len = best_len
    for _ in range(local_iters):
        i = rng.randrange(len(cur_cover))
        old = cur_cover[i]
        # 扰动：向随机源或原位附近收缩
        mode = rng.random()
        if mode < 0.5 and srcs:
            s = srcs[rng.randrange(len(srcs))]
            alpha = rng.uniform(0.2, 0.8)
            new = (old[0] + alpha * (s[0] - old[0]), old[1] + alpha * (s[1] - old[1]))
        else:
            new = (old[0] + rng.uniform(-300, 300), old[1] + rng.uniform(-300, 300))
        cur_cover[i] = new
        if not full_cover(cur_cover):
            cur_cover[i] = old
            continue
        L = tsp_nn_2opt_oropt(srcs + cur_cover, restarts=local_restarts, rng=rng)
        if L < cur_len - 1e-6:
            cur_len = L
        else:
            cur_cover[i] = old
    return cur_cover, cur_len


def run_god(seeds: list[int], cfg: "RunSettings") -> "VariantRun":
    """方案 G（快照 ``_floor_q3_g.py``）1:1。"""
    local_iters = 40 if cfg.quick else 400
    grid_restarts = 1 if cfg.quick else 3
    local_restarts = 1 if cfg.quick else 2
    src_restarts = 6  # 快照固定（源点很少，quick 也保留，代价可忽略）

    rows: list[dict[str, Any]] = []
    for seed in seeds:
        rng = random.Random(seed * 977)
        case = generate_case(seed, omni_only=True)
        srcs = [(j.position.x, j.position.y) for j in case.jammers]
        n = len(srcs)
        absent = TOTAL_CHANNELS - n

        cover, Ljoint = optimize_cover(
            srcs,
            rng,
            grid_restarts=grid_restarts,
            local_restarts=local_restarts,
            local_iters=local_iters,
        )
        Lsrc = tsp_nn_2opt_oropt(srcs, restarts=src_restarts, rng=rng)
        k_abs = greedy_probe_count(cover, srcs)

        vt = None
        if k_abs is not None:
            vt = Ljoint / cfg.speed + (absent * k_abs + 2 * n) * cfg.probe_s + n * cfg.clear_s
        rows.append(
            {
                "seed": seed,
                "n": n,
                "absent": absent,
                "src_tsp_m": Lsrc,
                "joint_tsp_m": Ljoint,
                "k_abs_greedy_approx": k_abs,
                "god_information_reference_s_per_source": (vt / n) if vt is not None else None,
            }
        )

    med = statistics.median
    vals_src = [r["src_tsp_m"] for r in rows]
    vals_joint = [r["joint_tsp_m"] for r in rows]
    vals_k = [r["k_abs_greedy_approx"] for r in rows if r["k_abs_greedy_approx"] is not None]
    vals_vt = [r["god_information_reference_s_per_source"] for r in rows if r["god_information_reference_s_per_source"] is not None]

    lines = [
        f"纯源 TSP(启发式·多起点NN+2opt+oropt)  中位 = {med(vals_src):.0f} m",
        f"联合 TSP(启发式可行解)               中位 = {med(vals_joint):.0f} m",
        f"absent 贪心近似探针数(启发式集合覆盖) 中位 = {med(vals_k):.1f}  ← 贪心近似，非最小值、可能高估",
        f"方案G s/源(上帝信息参考解·启发式)     中位 = {med(vals_vt):.1f}  最小 {min(vals_vt):.1f}  最大 {max(vals_vt):.1f}",
        "",
        "seed  n  absent  Lsrc   Ljoint  k_abs   G s/源",
    ]
    for r in rows:
        lines.append(
            f"{_cell(r['seed'], 4)} {_cell(r['n'], 3)} {_cell(r['absent'], 6)} "
            f"{_cell(r['src_tsp_m'], 7, 'f0')} {_cell(r['joint_tsp_m'], 8, 'f0')} "
            f"{_cell(r['k_abs_greedy_approx'], 5)} "
            f"{_cell(r['god_information_reference_s_per_source'], 9, 'f1')}"
        )
    metrics = {
        "god_information_reference_s_per_source": vals_vt,
        "src_tsp_m": vals_src,
        "joint_tsp_m": vals_joint,
        "k_abs_greedy_approx": vals_k,
    }
    return VariantRun(rows=rows, lines=lines, metrics=metrics, primary="god_information_reference_s_per_source")


# -------------------------------------------------------------------- variant: fixed
def hex_stops(radius: float = 1130.0) -> list[tuple[float, float]]:
    """中心 + 正六边形 6 顶点（半径 ``radius``，θ=0）。"""
    return [(0.0, 0.0)] + [
        (radius * math.cos(math.pi * t / 3.0), radius * math.sin(math.pi * t / 3.0)) for t in range(6)
    ]


def run_fixed(seeds: list[int], cfg: "RunSettings") -> "VariantRun":
    """固定 7 停靠上帝信息参考解（快照 ``_floor_q3_fixed.py``）1:1。"""
    rows: list[dict[str, Any]] = []
    for seed in seeds:
        rng = random.Random(seed * 977)
        case = generate_case(seed, omni_only=True)
        srcs = [(j.position.x, j.position.y) for j in case.jammers]
        n = len(srcs)
        absent = TOTAL_CHANNELS - n
        stops = hex_stops()
        Ljoint = tsp_nn_2opt_oropt(stops + srcs, restarts=8, rng=rng)
        vt = Ljoint / cfg.speed + (absent * 7 + 2 * n) * cfg.probe_s + n * cfg.clear_s
        rows.append(
            {
                "seed": seed,
                "n": n,
                "absent": absent,
                "joint_tsp_m": Ljoint,
                "god_information_reference_s_per_source": vt / n,
            }
        )

    med = statistics.median
    vals_joint = [r["joint_tsp_m"] for r in rows]
    vals_vt = [r["god_information_reference_s_per_source"] for r in rows]
    lines = [
        "固定 7 停靠@1130 上帝信息参考解（启发式可行解，非下界；源位置已知、联合 TSP 启发式、无在线信息损失）",
        f"联合 TSP(启发式) 中位 = {med(vals_joint):.0f} m",
        f"s/源(上帝信息参考解·启发式) 中位 = {med(vals_vt):.1f}  最小 {min(vals_vt):.1f}  最大 {max(vals_vt):.1f}",
        "",
        "seed  n  absent  Ljoint  s/源",
    ]
    for r in rows:
        lines.append(
            f"{_cell(r['seed'], 4)} {_cell(r['n'], 3)} {_cell(r['absent'], 6)} "
            f"{_cell(r['joint_tsp_m'], 7, 'f0')} "
            f"{_cell(r['god_information_reference_s_per_source'], 8, 'f1')}"
        )
    metrics = {
        "god_information_reference_s_per_source": vals_vt,
        "joint_tsp_m": vals_joint,
    }
    return VariantRun(rows=rows, lines=lines, metrics=metrics, primary="god_information_reference_s_per_source")


# ------------------------------------------------------------------------ variant: d
def covered_by(points: list[tuple[float, float]]) -> set[tuple[int, int]]:
    """点集覆盖到的 30m 网格索引（d 脚本实现）。"""
    idx = set()
    for (px, py) in points:
        pi, pj = int(round(px / GRID_STEP)), int(round(py / GRID_STEP))
        rad = int(COVER_R / GRID_STEP) + 1
        for i in range(pi - rad, pi + rad + 1):
            for j in range(pj - rad, pj + rad + 1):
                x, y = i * GRID_STEP, j * GRID_STEP
                if math.hypot(x, y) <= ARENA_R + 1e-9 and math.hypot(x - px, y - py) <= COVER_R:
                    idx.add((i, j))
    return idx


def grid_key_count() -> int:
    return len(GRID_30)


def greedy_cover(
    free_pts: list[tuple[float, float]],
    cand_pts: list[tuple[float, float]],
) -> tuple[list[tuple[float, float]], bool]:
    """``free_pts`` 免费已覆盖；**贪心**补 ``cand_pts`` 直到覆盖全部网格。

    ⚠️ 贪心集合覆盖是**启发式**：选出的点数**可能多于**真实最少点数。
    """
    cov = covered_by(free_pts)
    chosen: list[tuple[float, float]] = []
    rest = list(cand_pts)
    while cov != ALL_IDX and rest:
        best = None
        for p in rest:
            gain = len(covered_by([p]) - cov)
            if best is None or gain > best[0]:
                best = (gain, p)
        if best is None or best[0] == 0:
            break
        cov |= covered_by([best[1]])
        chosen.append(best[1])
        rest.remove(best[1])
    ok = cov == ALL_IDX
    return chosen, ok


def run_d(seeds: list[int], cfg: "RunSettings") -> "VariantRun":
    """方案 D / C 对照（快照 ``_floor_q3_d.py``）1:1。"""
    rows: list[dict[str, Any]] = []
    for seed in seeds:
        case = generate_case(seed, omni_only=True)
        srcs = [(j.position.x, j.position.y) for j in case.jammers]
        n = len(srcs)
        absent = TOTAL_CHANNELS - n

        # 方案 C：固定 7 点环（中心 + 正六边形 r=1123）
        ring7 = [(0.0, 0.0)] + [(p.x, p.y) for p in regular_polygon_scan_points(6, 1123.0, center=False)]
        # 方案 D：源位置免费充当探针点 + 密候选环贪心补充
        # 候选：中心 + 8环@1000 + 8环@1250 + 6环@1600 + 12环@1750（共 29 个候选）
        cand = [(0.0, 0.0)]
        for sides, r in ((8, 1000.0), (8, 1250.0), (6, 1600.0), (12, 1750.0)):
            cand += [(p.x, p.y) for p in regular_polygon_scan_points(sides, r, center=False)]

        chosen, ok = greedy_cover(srcs, cand)
        cover_D = srcs + chosen
        travelD = tsp_nn_2opt_capped(cover_D)  # 源本来就要走，补充点加入路线
        probesD = absent * len(cover_D) + 2 * n
        vtD = travelD / cfg.speed + probesD * cfg.probe_s + n * cfg.clear_s

        travelC = tsp_nn_2opt_capped(ring7 + srcs)
        probesC = absent * len(ring7) + 2 * n
        vtC = travelC / cfg.speed + probesC * cfg.probe_s + n * cfg.clear_s

        rows.append(
            {
                "seed": seed,
                "n": n,
                "absent": absent,
                "extra_cover_points_greedy": len(chosen),
                "cover_complete": ok,
                "travel_d_m": travelD,
                "scheme_d_s_per_source": vtD / n,
                "scheme_c_s_per_source": vtC / n,
            }
        )

    ok_all = sum(1 for r in rows if r["cover_complete"])
    d_vals = [r["scheme_d_s_per_source"] for r in rows]
    c_vals = [r["scheme_c_s_per_source"] for r in rows]
    extra_vals = [r["extra_cover_points_greedy"] for r in rows]
    lines = [
        f"覆盖网格点数 = {grid_key_count()}",
        f"{len(rows)} 案例中 D 方案覆盖完备: {ok_all}/{len(rows)}",
        f"D 补充点数中位 = {statistics.median(extra_vals):.1f}（贪心补点，启发式）",
        f"方案C（固定7点环）      s/源 中位 = {statistics.median(c_vals):.1f}  （快照标为「当前下界」→ 实为启发式可行解/参考解，非下界）",
        f"方案D（源+贪心补充点·启发式可行解）  s/源 中位 = {statistics.median(d_vals):.1f}  最小 {min(d_vals):.1f}",
        "",
        "seed  n  absent  补充点  完备  travelD   D s/源   C s/源",
    ]
    for r in rows:
        lines.append(
            f"{_cell(r['seed'], 4)} {_cell(r['n'], 3)} {_cell(r['absent'], 6)} "
            f"{_cell(r['extra_cover_points_greedy'], 6)} {str(r['cover_complete']):>5} "
            f"{_cell(r['travel_d_m'], 8, 'f0')} "
            f"{_cell(r['scheme_d_s_per_source'], 8, 'f1')} "
            f"{_cell(r['scheme_c_s_per_source'], 8, 'f1')}"
        )
    metrics = {
        "scheme_d_s_per_source": d_vals,
        "scheme_c_s_per_source": c_vals,
        "extra_cover_points_greedy": extra_vals,
    }
    return VariantRun(rows=rows, lines=lines, metrics=metrics, primary="scheme_d_s_per_source")


# ------------------------------------------------------------------------ variant: f
def covered_by_f(points: list[tuple[float, float]]) -> set[tuple[int, int]]:
    """同 :func:`covered_by`，额外跳过已收录索引（f 脚本实现，供密候选贪心用）。"""
    idx: set[tuple[int, int]] = set()
    for (px, py) in points:
        pi, pj = int(round(px / GRID_STEP)), int(round(py / GRID_STEP))
        rad = int(COVER_R / GRID_STEP) + 1
        for i in range(pi - rad, pi + rad + 1):
            for j in range(pj - rad, pj + rad + 1):
                if (i, j) in idx:
                    continue
                x, y = i * GRID_STEP, j * GRID_STEP
                if math.hypot(x, y) <= ARENA_R + 1e-9 and math.hypot(x - px, y - py) <= COVER_R:
                    idx.add((i, j))
    return idx


def full_cover_f(points: list[tuple[float, float]]) -> bool:
    return covered_by_f(points) == ALL_IDX


def min_cover_number(cand_spacing_m: float = 80.0) -> tuple[int, list[tuple[float, float]]]:
    """密候选**贪心**：用多少个 1000m 圆盘能盖满 1800m 圆（f 脚本实现）。

    ⚠️ 贪心集合覆盖是**启发式近似**：``len(chosen)`` **可能大于**真实最少圆盘数，
    所以不能把它读成"最少点数"。``cand_spacing_m`` 是候选环的弧长间距（快照 80m），
    仅 ``--quick`` 会调粗。
    """
    cand = []
    for r in range(0, 1900, 60):
        if r == 0:
            cand.append((0.0, 0.0))
            continue
        k = max(1, int(2 * math.pi * r / cand_spacing_m))
        for t in range(k):
            a = 2 * math.pi * t / k
            cand.append((r * math.cos(a), r * math.sin(a)))
    cov: set[tuple[int, int]] = set()
    chosen: list[tuple[float, float]] = []
    while cov != ALL_IDX:
        best = None
        for p in cand:
            if p in chosen:
                continue
            gain = len(covered_by_f([p]) - cov)
            if best is None or gain > best[0]:
                best = (gain, p)
        if best is None or best[0] == 0:
            break
        cov |= covered_by_f([best[1]])
        chosen.append(best[1])
    return len(chosen), chosen


def cover7(theta: float, rad: float = 1123.0) -> list[tuple[float, float]]:
    """中心 + 正六边形 6 顶点（起始角 ``theta``）。"""
    pts = [(0.0, 0.0)]
    for t in range(6):
        a = theta + math.pi * t / 3
        pts.append((rad * math.cos(a), rad * math.sin(a)))
    return pts


def run_f(seeds: list[int], cfg: "RunSettings") -> "VariantRun":
    """方案 F / C 对照（快照 ``_floor_q3_f.py``）1:1。"""
    cand_spacing = 240.0 if cfg.quick else 80.0
    kmin, _kchosen = min_cover_number(cand_spacing)

    rows: list[dict[str, Any]] = []
    for seed in seeds:
        case = generate_case(seed, omni_only=True)
        srcs = [(j.position.x, j.position.y) for j in case.jammers]
        n = len(srcs)
        absent = TOTAL_CHANNELS - n

        # C：固定 7 点环 θ=0
        ringC = cover7(0.0)
        vtC = (
            tsp_nn_2opt_capped(ringC + srcs) / cfg.speed
            + (absent * 7 + 2 * n) * cfg.probe_s
            + n * cfg.clear_s
        )

        # F：θ 扫描 + 环点吸附源（吸附阈值 300m，保持覆盖完备）
        bestF: tuple[float, int, float, int] | None = None
        for deg in range(0, 30, 3):
            theta = math.radians(deg)
            for rad in (1123.0, 1150.0):
                base = cover7(theta, rad)
                ring = []
                for p in base:
                    near = min(srcs, key=lambda s: math.hypot(s[0] - p[0], s[1] - p[1]), default=None)
                    if near is not None and math.hypot(near[0] - p[0], near[1] - p[1]) <= 300:
                        ring.append(near)
                    else:
                        ring.append(p)
                if not full_cover_f(ring):
                    ring = base  # 吸附破坏覆盖则退回
                vt = (
                    tsp_nn_2opt_capped(list(set(ring + srcs))) / cfg.speed
                    + (absent * 7 + 2 * n) * cfg.probe_s
                    + n * cfg.clear_s
                )
                if bestF is None or vt < bestF[0]:
                    bestF = (vt, deg, rad, sum(1 for a, b in zip(ring, base) if a != b))
        assert bestF is not None
        vtF = bestF[0]
        rows.append(
            {
                "seed": seed,
                "n": n,
                "absent": absent,
                "scheme_c_s_per_source": vtC / n,
                "scheme_f_s_per_source": vtF / n,
                "theta_deg": bestF[1],
                "snapped_points": bestF[3],
            }
        )

    c_vals = [r["scheme_c_s_per_source"] for r in rows]
    f_vals = [r["scheme_f_s_per_source"] for r in rows]
    lines = [
        f"贪心（启发式集合覆盖）覆盖点数近似 = {kmin}"
        f"（7 点环是否够用：{'是' if kmin >= 7 else '否！贪心解更少'}）"
        f"  ← 贪心近似，非最小值"
        + ("；--quick 已把候选间距调粗到 240m" if cfg.quick else ""),
        f"方案C（θ=0 固定环）   s/源 中位 = {statistics.median(c_vals):.1f}",
        f"方案F（θ扫描+吸附源·启发式可行解） s/源 中位 = {statistics.median(f_vals):.1f}  最小 {min(f_vals):.1f}",
        "",
        "seed  n  absent   C s/源   F s/源  θ°  吸附数",
    ]
    for r in rows:
        lines.append(
            f"{_cell(r['seed'], 4)} {_cell(r['n'], 3)} {_cell(r['absent'], 6)} "
            f"{_cell(r['scheme_c_s_per_source'], 8, 'f1')} "
            f"{_cell(r['scheme_f_s_per_source'], 8, 'f1')} "
            f"{_cell(r['theta_deg'], 4)} {_cell(r['snapped_points'], 4)}"
        )
    metrics = {
        "scheme_f_s_per_source": f_vals,
        "scheme_c_s_per_source": c_vals,
    }
    return VariantRun(rows=rows, lines=lines, metrics=metrics, primary="scheme_f_s_per_source")


# --------------------------------------------------------------------- variant: base
def nn(start: tuple[float, float], pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """最近邻建路（base 脚本实现）。"""
    rem, route, cur = list(pts), [], start
    while rem:
        j = min(range(len(rem)), key=lambda i: math.dist(rem[i], cur))
        cur = rem.pop(j)
        route.append(cur)
    return route


def two_opt(
    start: tuple[float, float],
    route: list[tuple[float, float]],
    passes: int = 20,
) -> list[tuple[float, float]]:
    """2-opt（≤ ``passes`` 轮，base 脚本实现）。"""
    n = len(route)
    for _ in range(passes):
        imp = False
        for i in range(n - 1):
            a = start if i == 0 else route[i - 1]
            b = route[i]
            for j in range(i + 1, n):
                c = route[j]
                d = route[j + 1] if j + 1 < n else None
                delta = math.dist(a, c) - math.dist(a, b)
                if d is not None:
                    delta += math.dist(b, d) - math.dist(c, d)
                if delta < -1e-9:
                    route[i:j + 1] = list(reversed(route[i:j + 1]))
                    imp = True
        if not imp:
            break
    return route


def plen(start: tuple[float, float], pts: list[tuple[float, float]]) -> float:
    return sum(math.dist(a, b) for a, b in zip([start] + list(pts), pts))


def cover_radius(pts: list[tuple[float, float]], step: float = 25.0) -> float:
    """区域最坏覆盖半径：25m 网格上到最近覆盖点的最大距离（base 脚本实现）。"""
    worst = 0.0
    n = int(2 * ARENA_R / step) + 1
    for i in range(n):
        gx = -ARENA_R + i * step
        for j in range(n):
            gy = -ARENA_R + j * step
            if gx * gx + gy * gy > ARENA_R * ARENA_R:
                continue
            d = min(math.hypot(gx - px, gy - py) for (px, py) in pts)
            if d > worst:
                worst = d
    return worst


def best_tsp(
    start: tuple[float, float],
    pts: list[tuple[float, float]],
    tries: int = 4,
) -> float:
    """``tries`` 次 NN+2-opt 取最短（base 脚本实现）。⚠️ 启发式，非最优 TSP。"""
    return min(plen(start, two_opt(start, nn(start, pts))) for _ in range(tries))


def run_base(seeds: list[int], cfg: "RunSettings") -> "VariantRun":
    """方案 A/B/C 三口径（快照 ``_floor_q3.py``）1:1。"""
    rows: list[dict[str, Any]] = []
    for seed in seeds:
        case = generate_case(seed, omni_only=True)
        srcs = [(j.position.x, j.position.y) for j in case.jammers]
        n = len(srcs)
        absent = TOTAL_CHANNELS - n
        start = (0.0, 0.0)

        # 方案 A：源自身当覆盖点
        covA = cover_radius(srcs)
        if covA <= COVER_R:
            travelA = best_tsp(start, srcs)
            probesA = absent * n + 2 * n
            sizeA = n
        else:
            # 覆盖不足：加最少量的补点（在未覆盖最深处加点）
            S = list(srcs)
            while cover_radius(S) > COVER_R and len(S) < 40:
                worst, at = 0.0, None
                for i in range(-36, 37):
                    gx = i * 50.0
                    for j in range(-36, 37):
                        gy = j * 50.0
                        if gx * gx + gy * gy > ARENA_R * ARENA_R:
                            continue
                        d = min(math.hypot(gx - px, gy - py) for (px, py) in S)
                        if d > worst:
                            worst, at = d, (gx, gy)
                    # 内层循环结束后再判 at（快照同结构）
                if at is None:
                    break
                S.append(at)
            travelA = best_tsp(start, S)
            probesA = absent * len(S) + 2 * n
            sizeA = len(S)

        # 方案 B：固定 8 点环（中心 + 正七边形 r=1110，覆盖半径 933.7m）
        ring = [(0.0, 0.0)] + [(p.x, p.y) for p in regular_polygon_scan_points(7, 1110.0, center=False)]
        travelB = best_tsp(start, ring + srcs)
        probesB = absent * len(ring) + 2 * n

        # 方案 C：最小 7 点环（中心 + 正六边形 r=1123，覆盖半径恰好 1000m，零裕量）
        ring7 = [(0.0, 0.0)] + [(p.x, p.y) for p in regular_polygon_scan_points(6, 1123.0, center=False)]
        travelC = best_tsp(start, ring7 + srcs)
        probesC = absent * len(ring7) + 2 * n

        def vt(travel: float, probes: int) -> float:
            return travel / cfg.speed + probes * cfg.probe_s + n * cfg.clear_s

        rows.append(
            {
                "seed": seed,
                "n": n,
                "absent": absent,
                "src_cover_radius_m": covA,
                "cover_set_size_a": sizeA,
                "a_travel_m": travelA,
                "a_probes": probesA,
                "scheme_a_s_per_source": vt(travelA, probesA) / n,
                "b_travel_m": travelB,
                "b_probes": probesB,
                "scheme_b_s_per_source": vt(travelB, probesB) / n,
                "c_travel_m": travelC,
                "c_probes": probesC,
                "scheme_c_s_per_source": vt(travelC, probesC) / n,
            }
        )

    a_vals = [r["scheme_a_s_per_source"] for r in rows]
    b_vals = [r["scheme_b_s_per_source"] for r in rows]
    c_vals = [r["scheme_c_s_per_source"] for r in rows]
    best_vals = [
        min(r["scheme_a_s_per_source"], r["scheme_b_s_per_source"], r["scheme_c_s_per_source"])
        for r in rows
    ]
    a_med = statistics.median(a_vals)
    b_med = statistics.median(b_vals)
    c_med = statistics.median(c_vals)

    lines = [
        f"{'seed':>4}{'n':>4}{'无源':>5}{'源覆盖半径':>11}{'A路线':>8}{'A检测':>7}{'A s/源':>9}"
        f"{'B路线':>8}{'B检测':>7}{'B s/源':>9}"
    ]
    for r in rows:
        lines.append(
            f"{r['seed']:>4}{r['n']:>4}{r['absent']:>5}{r['src_cover_radius_m']:>11.0f}"
            f"{r['a_travel_m']:>8.0f}{r['a_probes']:>7}{r['scheme_a_s_per_source']:>9.1f}"
            f"{r['b_travel_m']:>8.0f}{r['b_probes']:>7}{r['scheme_b_s_per_source']:>9.1f}"
        )
    lines.append("")
    lines.append(f"方案A（源自身当覆盖点）        s/源 中位 = {a_med:.1f}")
    lines.append(f"方案B（8 点环，覆盖 933.7m）   s/源 中位 = {b_med:.1f}")
    lines.append(f"方案C（7 点环，覆盖 1000m）    s/源 中位 = {c_med:.1f}")
    lines.append(
        f"三者取优（启发式可行解取优 → 仍只是上帝信息最优值的**上界**，非绝对下界） "
        f"s/源 中位 = {min(a_med, b_med, c_med):.1f}"
    )
    lines.append(
        f"源自身覆盖率：{sum(1 for r in rows if r['src_cover_radius_m'] <= COVER_R)}/{len(rows)} "
        f"个案例的源集合已能覆盖全域(<=1000m)"
    )
    metrics = {
        "scheme_a_s_per_source": a_vals,
        "scheme_b_s_per_source": b_vals,
        "scheme_c_s_per_source": c_vals,
        "min_abc_s_per_source": best_vals,  # 逐 seed 三方案取优（JSON 派生量，见 adaptations）
    }
    return VariantRun(rows=rows, lines=lines, metrics=metrics, primary="min_abc_s_per_source")


# ---------------------------------------------------------------- variant: route-gap
def run_route_gap(seeds: list[int], cfg: "RunSettings") -> "VariantRun":
    """路线差距解剖（快照 ``_q3_route_gap.py``）1:1。"""
    rows: list[dict[str, Any]] = []
    for seed in seeds:
        rng = random.Random(seed * 31)
        case = generate_case(seed, omni_only=True)
        with MockSimulator(
            robot_id=cfg.robot_id, case=case, legacy_clear_timing=cfg.legacy_clear_timing
        ) as sim:
            client = HttpSimulatorClient(robot_id=cfg.robot_id, base_url=sim.base_url, verbose=False)
            state = DogState()
            strat = Q3V15Strategy(
                Q3V15Params(schedule_min_readings=1, scan_sides=6, scan_radius=1130.0, scan_probe_limit=3)
            )
            client.enter()
            strat.run(client, state)
            client.exit()
            s = sim.world.summary()
        n = s["n_jammers"]
        # 实际停靠点：去重的检测点 + 清除点（机器人真实到过、停下做事的地方）
        pts = set()
        for t in strat.trace:
            if t["kind"] in ("measure", "clear"):
                pts.add((round(t["x"] / 10.0), round(t["y"] / 10.0)))
        stop_pts = [(p[0] * 10.0, p[1] * 10.0) for p in pts]
        actual = s["move_distance_m"]
        tsp_actual = tsp_nn_2opt_multistart(stop_pts, rng)
        srcs = [(j.position.x, j.position.y) for j in case.jammers]
        stops = [(0.0, 0.0)] + [
            (1130.0 * math.cos(math.pi * t / 3.0), 1130.0 * math.sin(math.pi * t / 3.0)) for t in range(6)
        ]
        god = tsp_nn_2opt_multistart(stops + srcs, rng)
        rows.append(
            {
                "seed": seed,
                "n": n,
                "actual_m": actual,
                "post_hoc_tsp_m": tsp_actual,
                "god_info_heuristic_tsp_m": god,
                "route_suboptimality_m_per_source": (actual - tsp_actual) / n,
                "post_hoc_to_god_gap_m_per_source": (tsp_actual - god) / n,
                "virtual_time_s": s["virtual_time_s"],
            }
        )

    lines = []
    for r in rows:
        lines.append(
            f"seed={r['seed']:<3} n={r['n']:<3} 实际={r['actual_m']:6.0f} "
            f"事后TSP(自身停靠)={r['post_hoc_tsp_m']:6.0f} "
            f"上帝信息TSP(启发式)={r['god_info_heuristic_tsp_m']:6.0f}  "
            f"(实际-事后)/n={r['route_suboptimality_m_per_source']:5.0f}  "
            f"(事后-上帝)/n={r['post_hoc_to_god_gap_m_per_source']:5.0f}"
        )
    lines.append("")
    lines.append(
        f"路线次优（实际-事后TSP）/源 中位 = "
        f"{statistics.median([r['route_suboptimality_m_per_source'] for r in rows]):.0f} m"
        "  ← 与启发式参考的经验差距，不是次优性证明"
    )
    metrics = {
        "route_suboptimality_m_per_source": [r["route_suboptimality_m_per_source"] for r in rows],
        "post_hoc_to_god_gap_m_per_source": [r["post_hoc_to_god_gap_m_per_source"] for r in rows],
        "actual_m": [r["actual_m"] for r in rows],
        "virtual_time_s": [r["virtual_time_s"] for r in rows],
    }
    return VariantRun(rows=rows, lines=lines, metrics=metrics, primary=None)


# ---------------------------------------------------------------- variant: v8-stats
def run_v8_stats(seeds: list[int], cfg: "RunSettings") -> "VariantRun":
    """v8 成本画像（快照 ``_q3_v8_stats.py``）1:1。"""
    agg_dist: Counter = Counter()
    agg_meas: Counter = Counter()
    agg_clear: Counter = Counter()
    rows: list[dict[str, Any]] = []

    for seed in seeds:
        case = generate_case(seed, omni_only=True)
        with MockSimulator(
            robot_id=cfg.robot_id, case=case, legacy_clear_timing=cfg.legacy_clear_timing
        ) as sim:
            client = HttpSimulatorClient(robot_id=cfg.robot_id, base_url=sim.base_url, verbose=False)
            state = DogState()
            strat = Q3V8Strategy(
                Q3V8Params(schedule_min_readings=1, scan_sides=6, scan_radius=1130.0, scan_probe_limit=3)
            )
            client.enter()
            strat.run(client, state)
            client.exit()
            s = sim.world.summary()
        dbk = getattr(strat, "dist_by_kind", {}) or {}
        n = s["n_jammers"]
        meas_by_reason: Counter = Counter()
        clear_by_reason: Counter = Counter()
        for t in getattr(strat, "trace", []) or []:
            if t["kind"] == "measure":
                meas_by_reason[t.get("reason", "?")] += 1
            elif t["kind"] == "clear":
                clear_by_reason[t.get("reason", "?")] += 1
        for k, v in dbk.items():
            agg_dist[k] += v / n
        for k, v in meas_by_reason.items():
            agg_meas[k] += v / n
        for k, v in clear_by_reason.items():
            agg_clear[k] += v / n
        rows.append(
            {
                "seed": seed,
                "cleared_count": s["cleared_count"],
                "n": n,
                "virtual_time_s": s["virtual_time_s"],
                "average_clear_time_s": s["average_clear_time_s"],
                "move_distance_m": s["move_distance_m"],
                "measure_count": s["measure_count"],
                "dist_per_source": {k: (dbk.get(k, 0) / n) for k in ("scan", "clear", "finish")},
                "measure_per_source_by_reason": {k: v / n for k, v in sorted(meas_by_reason.items())},
                "clear_per_source_by_reason": {k: v / n for k, v in sorted(clear_by_reason.items())},
            }
        )

    lines = []
    for r in rows:
        d = r["dist_per_source"]
        lines.append(
            f"seed={r['seed']:<3} {r['cleared_count']}/{r['n']} vt={r['virtual_time_s']:6.0f} "
            f"avg={r['average_clear_time_s']:6.1f} dist={r['move_distance_m']:6.0f} "
            f"meas={r['measure_count']:4d} "
            f"d/src: scan={d.get('scan', 0):5.0f} clear={d.get('clear', 0):5.0f} "
            f"finish={d.get('finish', 0):5.0f}"
        )

    vt = [r["virtual_time_s"] for r in rows]
    avg = [r["average_clear_time_s"] for r in rows if r["average_clear_time_s"]]
    nrows = len(rows)
    lines.append("")
    lines.append(
        f"vt_med={statistics.median(vt):.0f} avg_med={statistics.median(avg):.2f}"
        "  ← vt/cleared 的模拟器实测值（在线指标），不是任何参考解/下界"
    )
    lines.append(
        "dist per src (mean over seeds): "
        + ", ".join(f"{k}={v / nrows:.0f}" for k, v in sorted(agg_dist.items()))
    )
    lines.append(
        "meas per src (mean over seeds): "
        + ", ".join(f"{k}={v / nrows:.2f}" for k, v in sorted(agg_meas.items()))
    )
    lines.append(
        "clear per src (mean over seeds): "
        + ", ".join(f"{k}={v / nrows:.2f}" for k, v in sorted(agg_clear.items()))
    )
    metrics = {
        "virtual_time_s": vt,
        "average_clear_time_s": avg,
        "move_distance_m": [r["move_distance_m"] for r in rows],
        "measure_count": [r["measure_count"] for r in rows],
    }
    return VariantRun(rows=rows, lines=lines, metrics=metrics, primary=None)


# ------------------------------------------------------------------------ 运行框架
@dataclass
class VariantRun:
    """一个变体的运行结果：每 seed 行、文本全表、汇总指标。"""

    rows: list[dict[str, Any]]
    lines: list[str]
    metrics: dict[str, list[float]]
    primary: str | None


@dataclass
class RunSettings:
    """运行设置（计时口径 + quick + 模拟器选项）。"""

    quick: bool
    speed: float
    probe_s: float
    clear_s: float
    legacy_clear_timing: bool = False
    robot_id: str = ROBOT_ID


RUNNERS = {
    "god": run_god,
    "fixed": run_fixed,
    "d": run_d,
    "f": run_f,
    "base": run_base,
    "route-gap": run_route_gap,
    "v8-stats": run_v8_stats,
}


def parse_seeds(spec: str) -> list[int]:
    """解析 seed 规格：``"1-30"`` / ``"1-30,55"`` / ``"3,7-9"``（保序去重）。"""
    seeds: list[int] = []
    text = spec.replace("，", ",").replace(" ", "")
    if not text:
        raise ValueError("seed 规格为空")
    for part in text.split(","):
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            lo, hi = int(lo_s), int(hi_s)
            if hi < lo:
                raise ValueError(f"seed 区间反了：{part!r}")
            candidates = range(lo, hi + 1)
        else:
            candidates = (int(part),)
        for s in candidates:
            if s < 1:
                raise ValueError(f"seed 必须是正整数：{s}")
            if s not in seeds:
                seeds.append(s)
    if not seeds:
        raise ValueError(f"seed 规格解析后为空：{spec!r}")
    return seeds


def timing_lines(variant: str, cfg: RunSettings) -> list[str]:
    """打印本变体实际使用的计时常数（含附件1 §2.3 与复核口径说明）。"""
    if variant in ("route-gap", "v8-stats"):
        clear_note = (
            "旧口径(所有 /clear 一律 3+2s，复现归档数)"
            if cfg.legacy_clear_timing
            else "附件1 §2.3 口径(未发现 3s / 成功 5s)"
        )
        return [
            f"[计时口径] variant={variant} 由框架 mock 决定：SPEED={MOVE_SPEED_MPS} m/s  "
            f"测量={MEASURE_SECONDS} s  切换={CHANNEL_SWITCH_SECONDS} s  "
            f"光学={OPTICAL_SECONDS} s  清除={CLEAR_SECONDS} s  clear_timing={clear_note}",
            "[计时口径] 本变体不直接用 --speed/--probe-s/--clear-s（那些只作用于离线成本模型变体）。",
        ]
    return [
        f"[计时口径] variant={variant}  SPEED={cfg.speed} m/s  PROBE_S={cfg.probe_s} s  "
        f"CLEAR_S={cfg.clear_s} s  来源=快照常数/命令行覆盖",
        "[计时口径] 官方口径（附件1 §2.3）：/clear 未发现 3 s、成功 5 s，且不切换测向机频道；"
        "复核（review_20260913/审阅结论.md）指出快照的 clear 记账与附件不一致，"
        "归档 s/源 必须按新口径重新推导，不能直接引用。",
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__,
    )
    parser.add_argument(
        "--variant",
        choices=list(VARIANTS),
        default="god",
        help="god（推荐，快照 _floor_q3_g.py）/ fixed / d / f / base / route-gap / v8-stats",
    )
    parser.add_argument(
        "--seeds",
        default=None,
        help=f"seed 规格，如 1-30 / 1-30,55 / 3,7-9（默认 {DEFAULT_SEEDS}；--quick 下默认 {QUICK_SEEDS}）",
    )
    parser.add_argument("--limit", type=int, default=None, help="只取解析后 seed 列表的前 N 个")
    parser.add_argument(
        "--quick",
        action="store_true",
        help="廉价冒烟模式：2 个 seed，god 降低局部搜索迭代(400→40)与 TSP 重启(3/2→1/1)，f 调粗覆盖点贪心候选间距(80m→240m)",
    )
    parser.add_argument("--json", default=None, metavar="PATH", help="写机读结果 JSON（默认不写）")
    parser.add_argument("--clear-s", type=float, default=None, help="覆盖每源清除耗时（秒）")
    parser.add_argument("--probe-s", type=float, default=None, help="覆盖每次检测耗时（含频道切换，秒）")
    parser.add_argument("--speed", type=float, default=None, help="覆盖行驶速度（m/s）")
    parser.add_argument(
        "--legacy-clear-timing",
        action="store_true",
        help="route-gap/v8-stats：把失败的 /clear 也按 5s 计（复现 2026-09-13 之前归档数）",
    )
    parser.add_argument("--robot-id", default=ROBOT_ID, help=f"登录队号（离线 mock 用占位符 {ROBOT_ID}）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started = time.time()

    try:
        seed_spec = args.seeds if args.seeds else (QUICK_SEEDS if args.quick else DEFAULT_SEEDS)
        seeds = parse_seeds(seed_spec)
    except ValueError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2
    if args.limit is not None:
        if args.limit < 1:
            print("[错误] --limit 必须 >= 1", file=sys.stderr)
            return 2
        seeds = seeds[: args.limit]

    base_timing = VARIANT_TIMING[args.variant]
    if base_timing is None:
        # 模拟器类变体：常数由框架 mock 决定，这里只是占位（不参与计算）
        speed, probe_s, clear_s = MOVE_SPEED_MPS, MEASURE_SECONDS + CHANNEL_SWITCH_SECONDS, CLEAR_SECONDS
    else:
        speed, probe_s, clear_s = base_timing
    if args.speed is not None:
        speed = args.speed
    if args.probe_s is not None:
        probe_s = args.probe_s
    if args.clear_s is not None:
        clear_s = args.clear_s

    cfg = RunSettings(
        quick=args.quick,
        speed=speed,
        probe_s=probe_s,
        clear_s=clear_s,
        legacy_clear_timing=args.legacy_clear_timing,
        robot_id=args.robot_id,
    )

    header = [
        "=" * 100,
        f"Q3 上帝信息参考解 / 成本解剖端口  variant={args.variant}"
        f"{'  [--quick 廉价冒烟模式]' if args.quick else ''}",
        f"快照脚本：{SNAPSHOT_SCRIPTS[args.variant]}    口径：{VARIANT_TITLES[args.variant]}",
        f"seeds({len(seeds)})：{seeds if len(seeds) <= 12 else str(seeds[:6]) + ' ... ' + str(seeds[-3:])}",
        "=" * 100,
    ]
    banner = list(BANNER_LINES)
    reminder = [VARIANT_REMINDERS[args.variant]]
    tlines = timing_lines(args.variant, cfg)

    run = RUNNERS[args.variant](seeds, cfg)

    text_body = "\n".join(header + [""] + banner + [""] + reminder + [""] + tlines + [""] + run.lines)

    print(text_body)

    # 文本全表落到仓库 logs/ 下（绝不写回快照目录）
    TEXT_OUT_DIR.mkdir(parents=True, exist_ok=True)
    text_path = TEXT_OUT_DIR / f"{args.variant}.txt"
    text_path.write_text(text_body + "\n", encoding="utf-8")
    print("")
    print(f"[输出] 文本全表已写入：{text_path}")

    # ---- JSON 结果 ----
    s_per_source = {name: _summarize(values) for name, values in run.metrics.items()}
    primary_stats = s_per_source.get(run.primary) if run.primary else None
    summary: dict[str, Any] = dict(s_per_source)
    if run.metrics.get("k_abs_greedy_approx") is not None:
        summary["k_abs_greedy_approx_is_greedy_approximation"] = True

    payload: dict[str, Any] = {
        "tool": "mathModel2026B/script/q3_floor.py",
        "variant": args.variant,
        "variant_title": VARIANT_TITLES[args.variant],
        "snapshot_script": SNAPSHOT_SCRIPTS[args.variant],
        "quick": args.quick,
        "seeds": seeds,
        "seed_count": len(seeds),
        "timing_constants": {
            "speed_m_s": cfg.speed,
            "probe_s": cfg.probe_s,
            "clear_s": cfg.clear_s,
            "applies_to_cost_model": VARIANT_TIMING[args.variant] is not None,
            "legacy_clear_timing": cfg.legacy_clear_timing,
            "official_clear_timing_attachment1_2_3": "未发现 3 s / 成功 5 s，且不切换频道",
            "note": "离线成本模型变体沿用快照常数（归档可复现）；模拟器类变体由框架 mock 决定",
        },
        "columns": list(run.rows[0].keys()) if run.rows else [],
        "rows": run.rows,
        "summary": summary,
        "s_per_source": s_per_source,
        "god_information_reference_s_per_source": primary_stats,
        "heuristic_upper_bound_on_god_optimum": True,
        "certified_lower_bound": False,
        "wording_correction": {
            "snapshot_claim": "快照 README/文档把 _floor_q3_g.py 的 ≈183.7 s/源 称为「上帝下界」，"
            "并据此断言 185 s/源 不可达。",
            "snapshot_claim_valid": False,
            "reason": "god 变体用多起点 NN + 2-opt + or-opt 求路线、用随机局部搜索摆放自由覆盖点，"
            "得到的是上帝信息问题的**可行解**；可行解成本 ≥ 上帝信息最优值，故它只是该最优值的**上界**，"
            "不是下界。在线问题的最优值 ≥ 上帝信息最优值，因此该参考解与在线成本之间没有可比的大小关系。",
            "required_for_a_real_lower_bound": "把某个松弛问题求解到可证明最优（对偶证书 / 组合下界）；"
            "本仓库目前没有这样的下界。",
            "applies_to_variants": ["god", "fixed", "d", "f", "base"],
            "greedy_quantity_note": "greedy_probe_count / min_cover_number / greedy_cover 是贪心集合覆盖，"
            "可能高估真实最少点数；快照 190.8 s/源（贪心 k_abs 中位 8.5）与报告 ≈183.7 s/源（取 7）的差异即由此而来。",
            "banner": list(BANNER_LINES),
        },
        "claim_notes": [VARIANT_REMINDERS[args.variant]],
        "adaptations": list(ADAPTATIONS),
        "text_output_path": str(text_path),
        "wall_time_s": round(time.time() - started, 3),
    }

    if args.json:
        json_path = Path(args.json)
        if json_path.parent != Path(""):
            json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )
        print(f"[JSON] 机读结果已写入：{json_path}")

    wall = time.time() - started
    print(
        f"[耗时] {wall:.1f} s（variant={args.variant}, seeds={len(seeds)}, quick={args.quick}, "
        f"certified_lower_bound=False）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
