#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Q4 诊断脚本统一入口：离线布局/优化器诊断 + 在线策略/仿真诊断（从 2026-09-13 快照移植）。

为什么有这个文件
----------------
2026-09-13 的 Windows 最终工程快照顶层散着 15 个取证脚本（``_diag_knife.py``、
``_diag_polish.py``、``_diag_repair.py``、``_diag_joint.py``、``_diag_v9.py``、
``_diag_v9b.py``、``_diag_clears.py``、``_diag_probe.py``、``_diag_reason.py``、
``_diag_cover2.py``、``_diag_v17_seed25.py``、``_diag_v17_miss.py``、
``_q4_visit_check.py``、``_q4_v14_stats.py``、``_ab_q4_v17.py``）。它们回答的是
"布局为什么是这样、策略为什么漏清、改一步会不会更好"这类问题，但每个脚本各自
``sys.path`` 到快照目录、各自把结果写到脚本旁边、各自裸解析 ``sys.argv``：

* **不可复跑**：快照目录不在框架仓库里，进了仓库就跑不起来；
* **无法盘点**：想知道"手上到底有哪些诊断"只能去读文件名；
* **口径混杂**：有的脚本报虚拟时间、有的不报，而快照 mock 的 ``/clear`` 计时
  与附件1 §2.3 不一致（见下），归档数字与正确口径混在一处。

本模块把这 15 个脚本收成**一个子命令入口**：同一套 ``--seeds / --limit / --quick``、
同一处输出目录（``logs/q4/diagnose/``）、同一行表头（诊断对象 + 布局/策略 + 参数 +
种子），并**逐条保留**原脚本的参数、种子与打印列。离线部分复用
:mod:`script.q4.joint_opt`（即快照 ``_joint_opt_q4.py``），不复制其内部实现。
在线部分沿用快照的 ``MockSimulator`` + ``HttpSimulatorClient``（每个案例会在本机
拉起一个临时 HTTP mock 服务器，端口随机；``--client direct`` 可换成
``script/audit_q34.py`` 那套 ``DirectClient``，同样走 ``parse_*`` 解析路径但不监听端口）。

子命令索引（等价于 ``python3 script/q4/diagnose.py list``）
--------------------------------------------------------
================  ======  ==========================================================
子命令            类别    诊断什么 / 典型耗时
================  ======  ==========================================================
``clears``        online  每次 ``/clear`` 尝试归因（reason × 命中率 × 估计误差）；
                          ``direct`` 尝试按当时读数条数分桶。10 种子，~8 s
                          （``--quick`` ~2 s）。快照 ``_diag_clears.py``
``probe``         online  逐频道检测分布：扫描检出次数 / 读数条数 / 是否清除。3 种子，
                          ~3 s（``--quick`` ~1 s）。快照 ``_diag_probe.py``
``reason``        online  检测预算去向：``scan`` / ``refine`` / 其它 reason 的次数与里程
                          分解。种子 1–10，~7 s（``--quick`` ~2 s）。快照 ``_diag_reason.py``
``cover2``        online*  seed 25 信道 2（定向源）在两个布局下的覆盖点数与读数几何。
                          纯几何、不跑仿真，<1 s。快照 ``_diag_cover2.py``
``v17-seed25``    online  seed 25 信道 2 深挖：v14 vs v17 的读数、估计点、可行域、
                          schedule_log 与清除记录。~2 s。快照 ``_diag_v17_seed25.py``
``v17-miss``      online  定位 seed 25 漏清源：真值位置/朝向 × 布局各点的正面最近距离。
                          ~2 s。快照 ``_diag_v17_miss.py``
``visit-check``   online  核实 v14 实际访问的扫描点数与行驶分解。种子 23/1/10，~3 s
                          （``--quick`` ~1 s）。快照 ``_q4_visit_check.py``
``v14-stats``     online  v14 成本画像：里程按 scan/clear/finish 分解、检测/清除按
                          reason 分解。种子 1–30，~30 s（``--quick`` ~3 s）。
                          快照 ``_q4_v14_stats.py``
``ab-v17``        online  布局 A/B：v14(25 点) vs v14r(环形兜底) vs v17(24 点)，同批
                          case 同参数比虚拟时间。种子 1–30（3 臂 90 局），~90 s
                          （``--quick`` ~6 s）。快照 ``_ab_q4_v17.py``
``knife``         offline 删#5/#6 → ``repair_geom`` → ``polish_feas`` → ``knife_fix``，
                          看 24 点能否真正完备。~40–80 s（``--quick`` 只跑删#5 ≈ 28 s）。
                          快照 ``_diag_knife.py``
``polish``        offline 删#5 几何修复后细网格差 ~14 m，纯可行性微调能否收尾（无路程项）。
                          ~27 s（``--quick`` ~8 s）。快照 ``_diag_polish.py``
``repair``        offline ``delete_repair_step``（几何修复 + SCP 微调 + 细网格验收）
                          在基线上能否完成删点。默认**数分钟**（每轮遍历全部候选并跑
                          0.25° 采样扫描），``--quick`` ~8 s。快照 ``_diag_repair.py``
``joint``         offline 联合优化步诊断：单点删除可行性扫描 + 坐标步 ``scp_step``
                          位移/求解状态 + witness 负载分布。默认 ~170 s，``--quick`` ~6 s。
                          快照 ``_diag_joint.py``
``v9``            online  v9 定向场景漏清归因：漏掉的频道是"从未被探测到"还是
                          "探测到但没清掉"。种子 2/3/7/9/13，~5 s（``--quick`` ~2 s）。
                          快照 ``_diag_v9.py``
``v9b``           online  v9 定向源精定位逐轮日志：可行域直径 / MEC 是否在收缩、落空在
                          哪一步。seed 3，~1 s。快照 ``_diag_v9b.py``
``list``          meta    打印本索引（子命令 + 中文说明 + 典型耗时）。
================  ======  ==========================================================

* ``cover2`` 不仿真、不报告虚拟时间，只是"用仿真世界生成案例"的几何核对（不涉及计时口径）。

三条必须写明的口径（2026-09-13 独立复核的结论）
--------------------------------------------
1. **``/clear`` 计时**：附件1 §2.3 规定失败的 ``/clear`` 计 3 s、成功的计 5 s，
   且 ``/clear`` 不切换测向机频道。快照 ``mock/world.py`` 曾把失败也按 3+2=5 s 计，
   复核后框架已修正，并留了 ``legacy_clear_timing=True`` 复现旧数字。本模块**任何**
   报告虚拟时间的子命令都会先打印自己用的是哪种口径，并提供 ``--legacy-timing``
   复现归档数字；两种口径对同一动作轨迹满足 ``T_legacy = T_correct + 2·N_clear_failure``。
2. **采样判据 ≠ 证明**：``certify`` / ``conv_hull_check`` / ``brute_worst`` /
   ``_multi_cert_ok`` 都只在**有限样本**（有限网格 × 有限朝向 / 有限随机点）上求值。
   采样只能**证伪**（发现洞），不能证明完备；报出来的 ``worst`` 是样本最大值，
   **不是**连续域最坏真值。连续域可证明的充分条件在
   ``python3 script/verify_layout_certificate.py``（四叉树单元细分，要求 ``unresolved == 0``）。
3. **射线推进只是启发式**：``strategy_v14`` 的 ``no_signal ⇒ hi = t``（"越过了源"）
   **不成立**——有界测向误差下狗可能在到达源之前就已经转到源的背面（复核给出的反例：
   A=(0,0)、源 G=(1000,0)、接收半径 1200 m、源定向 90.1°、读数 −1°，沿读数走 450 m
   得 q≈(449.93,−7.85)，距源约 550 m 却已在源背面）。凡复现该推理的诊断都把射线推进
   标为**启发式**，不得当作"必清"的证明；稳妥兜底是 q4-v18 的可证明覆盖（对保守可行域
   F 铺边长 25 m 的格，格心到格内任意点 ≤ 25/√2 ≈ 17.68 m < 20 m，逐格光学清除）。

输出
----
默认写到 ``logs/q4/diagnose/``（``--out-dir`` 可改，``--no-write`` 只打印）：
``<子命令>.txt``（逐行文本，含表头与出处）与 ``<子命令>.json``（结构化摘要）。
``--json`` 额外把结构化摘要打到标准输出。个别子命令成功时还会多写布局产物
（``knife`` 会写 ``knife_layout.json`` / ``knife_layout_fragment.py``），
一律落在同一个 ``--out-dir`` 下，不再写回脚本旁边。

用法::

    python3 script/q4/diagnose.py list
    python3 script/q4/diagnose.py --help

    # 廉价冒烟（每个子命令都远快于 60 s）
    python3 script/q4/diagnose.py knife --quick
    python3 script/q4/diagnose.py joint --quick
    python3 script/q4/diagnose.py v9b --quick --legacy-timing

    # 复现归档数字（旧 mock 计时）
    python3 script/q4/diagnose.py reason --legacy-timing --seeds 1-10

    # 指定布局 json（仅 cover2 / v17-miss 消费；默认框架里那份 LAYOUT_Q4_V17）
    python3 script/q4/diagnose.py cover2 --in logs/q4/joint_opt/layout.json

包内运行与单文件直跑都可用::

    python3 -m script.q4.diagnose list
    python3 script/q4/diagnose.py list
"""

from __future__ import annotations

import argparse, json, math, statistics, sys, time
from pathlib import Path
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent          # mathModel2026B/script/q4
REPO_ROOT = SCRIPT_DIR.parent.parent                  # mathModel2026B
sys.path.insert(0, str(REPO_ROOT / "framework" / "src"))
sys.path.insert(0, str(SCRIPT_DIR))

from mathmodel2026b.mock.world import World, generate_case  # noqa: E402
from mathmodel2026b.state import DogState                   # noqa: E402
from mathmodel2026b.protocol import parse_clear, parse_enter, parse_exit, parse_measure  # noqa: E402

try:  # 包内运行
    from . import joint_opt as jo
    from . import layout_io
except ImportError:  # 单文件直跑
    import joint_opt as jo
    import layout_io

import dataclasses  # noqa: E402
import unicodedata  # noqa: E402
from collections import Counter, defaultdict  # noqa: E402

from mathmodel2026b.client import HttpSimulatorClient  # noqa: E402
from mathmodel2026b.geometry import (  # noqa: E402
    Point,
    angle_difference_deg,
    degrees_atan2,
    polygon_diameter,
)
from mathmodel2026b.mock.server import MockSimulator  # noqa: E402
from mathmodel2026b.strategy_v8 import Q3V8Params, Q3V8Strategy  # noqa: E402
from mathmodel2026b.strategy_v9 import (  # noqa: E402
    Q4V9Params,
    Q4V9Strategy,
    two_tier_nodes,
)
from mathmodel2026b.strategy_v10 import Q3V10Params, Q3V10Strategy  # noqa: E402
from mathmodel2026b.strategy_v14 import Q4V14Params, Q4V14Strategy  # noqa: E402
from mathmodel2026b.strategy_v17 import (  # noqa: E402
    LAYOUT_Q4_V17,
    Q4V17Params,
    Q4V17Strategy,
)

#: 协议要求的登录队号（mock 会校验）。
ROBOT = "000000000000"

#: 诊断产物的统一落点：与 ``script/q4/joint_opt.py`` 的 ``logs/q4/joint_opt/`` 同根。
DEFAULT_OUT_DIR = layout_io.LOG_ROOT / "diagnose"

#: v9 两圈式 25 点布局的可复现参数（快照所有离线诊断的基线）。
V9_TIER = (950.0, 1750.0, 12, 1900.0)

#: 采样类判据的固定提醒（要求：不得把有限样本写成完备性证明）。
SAMPLING_CAVEAT = (
    "注意：certify / conv_hull_check / brute_worst / _multi_cert_ok 都是**有限采样**判据"
    "——采样只能证伪（找出洞），不能证明完备；这里的 worst 只是样本上的最大值，不是连续域"
    "最坏真值。连续域可证明的充分条件请跑 python3 script/verify_layout_certificate.py"
    "（四叉树细分，要求 unresolved == 0）。"
)

#: 有限仿真全清的固定提醒。
FINITE_SIM_CAVEAT = (
    "注意：有限个仿真案例全部清除**只说明这些案例**；不能把有限仿真全清写成对所有案例的"
    "概率 1 保证。"
)

#: 射线推进的固定提醒（复核反例见模块 docstring）。
RAY_CAVEAT = (
    "注意：v14 的射线推进（no_signal ⇒ hi = t，“越过了源”）是**启发式**，不是定理"
    "——有界测向误差下狗可能在到达源之前就已转到源背面（复核反例：源(1000,0)、R=1200、"
    "定向 90.1°、读数 −1°）。它不得用作“必清”的证明；可证明的兜底是 q4-v18 的 25 m "
    "网格覆盖清除（格心到格内任意点 ≤ 25/√2 ≈ 17.68 m < 20 m）。"
)

TIMING_SPEC = (
    "计时口径：spec（附件1 §2.3）——失败的 /clear 计 3 s、成功的 /clear 计 5 s，"
    "/clear 不切换测向机频道；对应 World(..., legacy_clear_timing=False)"
)
TIMING_LEGACY = (
    "计时口径：legacy（旧 mock）——失败的 /clear 也按 3+2=5 s 计；仅用于复现 2026-09-13 "
    "之前归档的数字，与附件1 §2.3 不一致（T_legacy = T_correct + 2·N_clear_failure）"
)


def timing_note(legacy: bool) -> str:
    return TIMING_LEGACY if legacy else TIMING_SPEC


def spec_note(legacy: bool) -> str:
    return ("  ↑ 若要与 2026-09-13 之前的归档数字对齐，用 --legacy-timing 重跑"
            if not legacy else
            "  ↑ 这是旧 mock 口径；正确口径请去掉 --legacy-timing 重跑")


# --------------------------------------------------------------------------- 小工具
class DirectClient:
    """与 HTTP mock **同一条**协议解析路径（``parse_*``），只是省掉传输开销/端口。

    与 ``script/audit_q34.py`` 的 ``DirectClient`` 同款：刻意不直接用 ``World`` 的
    返回值，而是把响应交给策略真正看到的解析器，免得"模拟器发了什么"与"策略看到什么"
    的差异被诊断脚本自己掩盖。
    """

    def __init__(self, world: World) -> None:
        self.world = world

    def enter(self):
        return parse_enter(self.world.enter())

    def exit(self):
        return parse_exit(self.world.exit())

    def measure(self, position, channel):
        return parse_measure(self.world.measure(position, channel)[0])

    def clear(self, position, channel):
        return parse_clear(self.world.clear(position, channel)[0])


def parse_seeds(spec: str) -> list[int]:
    """``1-5,25`` → ``[1, 2, 3, 4, 5, 25]``（快照 ``_ab_q4_v17.py`` 同款）。"""
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def _kv_value(v: str):
    """快照的裸参数解析规则：含 ``.`` → float，纯数字 → int，否则 str。"""
    return float(v) if "." in v else (int(v) if v.isdigit() else v)


def params_from_args(args: argparse.Namespace) -> dict:
    params: dict = {}
    for item in getattr(args, "param", None) or []:
        if "=" not in item:
            raise SystemExit(f"--param 需要 K=V 形式，收到 {item!r}")
        k, v = item.split("=", 1)
        params[k] = _kv_value(v)
    return params


def _seeds(args: argparse.Namespace, default, quick) -> list[int]:
    """种子解析：``--seeds`` 覆盖默认；``--quick`` 用更少的默认；``--limit`` 截断。"""
    seeds = parse_seeds(args.seeds) if args.seeds else list(default)
    if args.quick:
        seeds = list(quick)
    if args.limit is not None:
        seeds = seeds[: max(0, int(args.limit))]
    if not seeds:
        raise SystemExit("种子列表为空（--seeds/--limit 组合把种子截没了）")
    return seeds


def seeds_desc(seeds: list[int]) -> str:
    if len(seeds) <= 8:
        return f"{seeds}（{len(seeds)} 个）"
    return f"{seeds[0]}..{seeds[-1]}（{len(seeds)} 个）"


def _dwidth(s: str) -> int:
    """终端显示宽度（CJK 全角按 2 列算），只用于 ``list`` 的表格对齐。"""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _dpad(s: str, width: int) -> str:
    return s + " " * max(0, width - _dwidth(s))


def baseline_v9_layout() -> np.ndarray:
    """离线诊断的基线：v9 两圈式 25 点（与快照 ``two_tier_nodes(950,1750,12,1900)`` 一致）。"""
    return np.array(two_tier_nodes(*V9_TIER), dtype=float)


def layout_arm(args: argparse.Namespace) -> tuple[str, np.ndarray]:
    """``cover2`` / ``v17-miss`` 比对的"方法二布局"这一臂（``--in`` 可换成任意 json）。"""
    if args.in_path:
        pts, _meta = layout_io.load_layout_json(args.in_path)
        arr = np.array(pts, dtype=float)
        return f"输入布局({args.in_path}, m={len(arr)})", arr
    arr = np.array(layout_io.framework_layout(), dtype=float)
    return f"v17-{len(arr)}", arr


def framework_layout_desc(args: argparse.Namespace) -> str:
    if args.in_path:
        return f"--in {args.in_path}"
    return ("framework/src/mathmodel2026b/strategy_v17.py 的 LAYOUT_Q4_V17"
            f"（{len(LAYOUT_Q4_V17)} 点）")


class Diag:
    """一次诊断运行的文本缓冲 + 产物落盘（默认 ``logs/q4/diagnose/``）。"""

    def __init__(self, args: argparse.Namespace, name: str) -> None:
        self.args = args
        self.name = name
        self.lines: list[str] = []
        self.result: dict = {"subcommand": name, "quick": bool(args.quick)}
        self.out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_OUT_DIR
        self.write = not args.no_write
        self.t0 = time.time()
        self.written: list[str] = []

    # -- 输出 -----------------------------------------------------------
    def emit(self, s: str = "") -> None:
        self.lines.append(s)
        print(s, flush=True)

    def head(self, *, what: str, layout: str, params: dict, seeds: list[int] | None) -> None:
        """统一的表头：诊断对象 / 布局（策略）/ 参数 / 种子。"""
        self.emit(f"=== diagnose/{self.name} ===")
        self.emit(f"诊断对象：{what}")
        self.emit(f"布局/策略：{layout}")
        self.emit(f"参数：{params}")
        self.emit(f"种子：{'-' if seeds is None else seeds_desc(seeds)}")
        self.emit(f"运行模式：{'--quick（廉价：少种子/少候选/粗网格）' if self.args.quick else '默认（快照参数）'}"
                  f"；客户端={self.args.client}；输出目录={self.out_dir}"
                  + ("（--no-write，不落盘）" if not self.write else ""))
        self.result["layout"] = layout
        self.result["params"] = {k: str(v) for k, v in params.items()}
        self.result["seeds"] = seeds

    def note(self, text: str) -> None:
        self.emit(f"  ⚠ {text}")

    def timing(self) -> None:
        self.emit(f"  {timing_note(self.args.legacy_timing)}")
        self.emit(spec_note(self.args.legacy_timing))
        self.result["legacy_timing"] = bool(self.args.legacy_timing)

    # -- 落盘 -----------------------------------------------------------
    def save_extra_text(self, filename: str, text: str) -> Path | None:
        if not self.write:
            return None
        self.out_dir.mkdir(parents=True, exist_ok=True)
        p = self.out_dir / filename
        p.write_text(text, encoding="utf-8", newline="\n")
        self.written.append(str(p))
        return p

    def finish(self, **result) -> dict:
        dt = time.time() - self.t0
        self.result.update(result)
        self.result["elapsed_s"] = round(dt, 2)
        self.emit(f"[用时 {dt:.1f}s]")
        if self.write:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            txt = self.out_dir / f"{self.name}.txt"
            txt.write_text("\n".join(self.lines) + "\n", encoding="utf-8", newline="\n")
            self.written.append(str(txt))
            self.result["written"] = self.written
            (self.out_dir / f"{self.name}.json").write_text(
                json.dumps(self.result, ensure_ascii=False, indent=1) + "\n",
                encoding="utf-8", newline="\n",
            )
        else:
            self.result["written"] = []
        self.emit(f"产物：{'（--no-write，未落盘）' if not self.write else ' / '.join(self.written)}")
        if self.args.json:
            print(json.dumps(self.result, ensure_ascii=False, indent=1))
        return self.result


# --------------------------------------------------------------------------- 在线仿真
def run_online(seed: int, factory, *, omni_only: bool, legacy_timing: bool,
               client_kind: str = "http", prepare=None):
    """跑一局策略，返回 ``(case, state, strat, summary)``。

    ``client_kind="http"``（默认，快照行为）：``MockSimulator`` 在本机拉起临时 HTTP
    mock 服务器，``HttpSimulatorClient`` 走真实 ``robot-protocol-v1`` 请求/校验链路。
    ``client_kind="direct"``：``World`` + ``DirectClient``，省掉端口与传输，仍走 ``parse_*``。
    """
    case = generate_case(seed, omni_only=omni_only)
    if client_kind == "direct":
        world = World(case=case, robot_id=ROBOT, legacy_clear_timing=legacy_timing)
        client = DirectClient(world)
        state = DogState()
        strat = factory()
        if prepare is not None:
            prepare(strat, case, state)
        client.enter()
        strat.run(client, state)
        client.exit()
        return case, state, strat, world.summary()

    with MockSimulator(robot_id=ROBOT, case=case, legacy_clear_timing=legacy_timing) as sim:
        client = HttpSimulatorClient(robot_id=ROBOT, base_url=sim.base_url, verbose=False)
        state = DogState()
        strat = factory()
        if prepare is not None:
            prepare(strat, case, state)
        client.enter()
        strat.run(client, state)
        client.exit()
        summary = sim.world.summary()
    return case, state, strat, summary


# =========================================================================== 在线诊断
def cmd_clears(args: argparse.Namespace) -> int:
    """快照 ``_diag_clears.py``：插桩 ``_clear``，统计 reason × 命中率 × 估计误差。"""
    d = Diag(args, "clears")
    name = args.strategy
    if name == "v10":
        pcls, scls = Q3V10Params, Q3V10Strategy
    else:
        pcls, scls = Q3V8Params, Q3V8Strategy
    allowed = {f.name for f in dataclasses.fields(pcls)}
    base = {"schedule_min_readings": 1, "scan_sides": 6, "scan_radius": 1130}
    base.update(params_from_args(args))
    params = pcls(**{k: v for k, v in base.items() if k in allowed})
    seeds = _seeds(args, range(1, 11), [1, 2])

    d.head(
        what="每次 /clear 尝试的归因：reason、当时读数条数、到真值的估计误差、成败；"
             "并把 direct 尝试按读数条数分桶（全向源场景 omni_only=True）",
        layout=f"{name} 策略（{pcls.__name__}），布局由策略自身发现层决定",
        params={k: v for k, v in base.items() if k in allowed},
        seeds=seeds,
    )
    d.timing()
    d.note(FINITE_SIM_CAVEAT)

    all_attempts: list[tuple] = []
    rows = []
    for seed in seeds:
        attempts: list[tuple] = []

        def prepare(strat, case, state, *, seed=seed, attempts=attempts):
            truth = {j.channel: (j.position.x, j.position.y) for j in case.jammers}
            orig_clear = strat._clear

            def clear(client_, state_, point, channel, reason=""):
                n_rd = len(state_.channels[channel].readings) if channel in state_.channels else -1
                tx, ty = truth.get(channel, (None, None))
                err = math.hypot(point.x - tx, point.y - ty) if tx is not None else -1
                ok = orig_clear(client_, state_, point, channel, reason=reason)
                attempts.append((seed, channel, reason, n_rd, round(err, 1), ok))
                return ok

            strat._clear = clear

        _case, _state, _strat, s = run_online(
            seed, lambda: scls(params), omni_only=True,
            legacy_timing=args.legacy_timing, client_kind=args.client, prepare=prepare,
        )
        all_attempts.extend(attempts)
        rows.append({"seed": seed, "cleared": s["cleared_count"], "n": s["n_jammers"],
                     "vt": round(s["virtual_time_s"], 2),
                     "dist": round(s["move_distance_m"], 2)})
        d.emit(f"seed={seed} clear={s['cleared_count']}/{s['n_jammers']} "
               f"vt={s['virtual_time_s']:.0f} dist={s['move_distance_m']:.0f}")

    by_reason: dict = {}
    for (_sd, _ch, reason, _n_rd, err, ok) in all_attempts:
        dd = by_reason.setdefault(reason, Counter())
        dd["n"] += 1
        dd["hit"] += 1 if ok else 0
        dd["err_sum"] += err if err >= 0 else 0
    d.emit("")
    d.emit("reason        n   hit%   err_avg")
    for reason, dd in sorted(by_reason.items()):
        d.emit(f"{reason:<10} {dd['n']:>5} {dd['hit'] / dd['n'] * 100:5.1f}% "
               f"{dd['err_sum'] / dd['n']:7.1f}m")

    by_rd: dict = {}
    for (_sd, _ch, reason, n_rd, _err, ok) in all_attempts:
        if reason != "direct":
            continue
        dd = by_rd.setdefault(n_rd, Counter())
        dd["n"] += 1
        dd["hit"] += 1 if ok else 0
    d.emit("")
    d.emit("direct 尝试按读数条数：")
    for n_rd, dd in sorted(by_rd.items()):
        d.emit(f"  readings={n_rd}: n={dd['n']} hit={dd['hit'] / dd['n'] * 100:.0f}%")

    return d.finish(cases=rows, attempts=len(all_attempts),
                    by_reason={k: dict(v) for k, v in by_reason.items()},
                    by_readings={k: dict(v) for k, v in by_rd.items()}) and 0


def cmd_probe(args: argparse.Namespace) -> int:
    """快照 ``_diag_probe.py``：逐频道检测分布（扫描检出 / 其它检出 / 读数 / 是否清除）。"""
    d = Diag(args, "probe")
    params = params_from_args(args)
    seeds = _seeds(args, (1, 2, 3), [1])
    d.head(
        what="逐频道检测分布：每个频道被扫描检出几次、其它 cause 检出几次、拿到几条读数、"
             "最终是否清除（全向源场景 omni_only=True）",
        layout=f"v8（Q3V8Params），参数={params or '默认'}",
        params={"strategy": "v8", "params": params or {}},
        seeds=seeds,
    )
    d.timing()
    d.note(FINITE_SIM_CAVEAT)

    hist_absent: Counter = Counter()
    hist_present: Counter = Counter()
    rows = []
    for seed in seeds:
        _case, state, strat, s = run_online(
            seed, lambda: Q3V8Strategy(Q3V8Params(**params)), omni_only=True,
            legacy_timing=args.legacy_timing, client_kind=args.client,
        )
        truth = {j.channel for j in _case.jammers}
        per: dict[int, Counter] = defaultdict(Counter)
        for t in strat.trace:
            if t.get("kind") == "measure":
                per[t["channel"]][str(t.get("reason", ""))] += 1
        d.emit(f"-- seed={seed} n={s['n_jammers']} vt={s['virtual_time_s']:.0f} "
               f"dist={s['move_distance_m']:.0f} meas={s['measure_count']} "
               f"cleared={s['cleared_count']}/{s['n_jammers']}")
        for c in range(1, 21):
            st = state.channels[c]
            scans = sum(v for k, v in per[c].items() if k.startswith("scan"))
            other = sum(v for k, v in per[c].items() if not k.startswith("scan"))
            tag = "有源" if c in truth else "无源"
            d.emit(f"    ch{c:<3}{tag}  扫描检测={scans:<2} 其它检测={other:<2} "
                   f"读数={len(st.readings):<2} 清除={st.is_cleared}")
            (hist_present if c in truth else hist_absent)[scans] += 1
        rows.append({"seed": seed, "cleared": s["cleared_count"], "n": s["n_jammers"],
                     "vt": round(s["virtual_time_s"], 2)})
    d.emit(f"扫描检测次数分布  有源频道: {dict(sorted(hist_present.items()))}")
    d.emit(f"扫描检测次数分布  无源频道: {dict(sorted(hist_absent.items()))}")

    return d.finish(cases=rows,
                    hist_present={int(k): v for k, v in sorted(hist_present.items())},
                    hist_absent={int(k): v for k, v in sorted(hist_absent.items())}) and 0


def cmd_reason(args: argparse.Namespace) -> int:
    """快照 ``_diag_reason.py``：检测预算去向（scan / refine / 其它）与平均里程。"""
    d = Diag(args, "reason")
    params = params_from_args(args)
    seeds = _seeds(args, range(1, 11), [1, 2])
    d.head(
        what="检测预算去向：把 measure 的 reason 归并成 scan / refine / 其它，"
             "统计每场次数与占比，并给出平均虚拟时间与平均里程（全向源 omni_only=True）",
        layout=f"v8（Q3V8Params），参数={params or '默认'}",
        params={"strategy": "v8", "params": params or {}},
        seeds=seeds,
    )
    d.timing()
    d.note(FINITE_SIM_CAVEAT)

    cnt: Counter = Counter()
    vt_sum = dist_sum = 0.0
    for seed in seeds:
        _case, _state, strat, s = run_online(
            seed, lambda: Q3V8Strategy(Q3V8Params(**params)), omni_only=True,
            legacy_timing=args.legacy_timing, client_kind=args.client,
        )
        vt_sum += s["virtual_time_s"]
        dist_sum += s["move_distance_m"]
        for t in strat.trace:
            if t.get("kind") == "measure":
                r = str(t.get("reason", ""))
                key = "scan" if r.startswith("scan") else (
                    "refine" if r.startswith("refine") else r)
                cnt[key] += 1
    total = sum(cnt.values())
    n_seed = len(seeds)
    d.emit(f"params={params}  seeds={seeds[0]}-{seeds[-1]}  avg vt={vt_sum / n_seed:.0f}  "
           f"avg dist={dist_sum / n_seed:.0f}  avg meas={total / n_seed:.1f}")
    for k, v in cnt.most_common():
        d.emit(f"  {k:<16} {v / n_seed:7.1f} 次/场   {100 * v / total:5.1f}%")
    d.note(FINITE_SIM_CAVEAT + "（上述均值只在本次报告的种子集合上成立）")

    return d.finish(avg_vt=round(vt_sum / n_seed, 2), avg_dist=round(dist_sum / n_seed, 2),
                    avg_meas=round(total / n_seed, 3), reasons=dict(cnt)) and 0


def cmd_cover2(args: argparse.Namespace) -> int:
    """快照 ``_diag_cover2.py``：seed 25 信道 2 在两个布局下的覆盖点数与读数几何。"""
    d = Diag(args, "cover2")
    seeds = _seeds(args, (25,), [25])
    seed = seeds[0]
    label, arm = layout_arm(args)
    d.head(
        what="核对 seed 25 信道 2（定向源）在 v9 25 点布局与“方法二”布局下的覆盖点数"
             "与各点读数方位（纯几何核对，不跑仿真）",
        layout=f"v9-25 = two_tier_nodes(*{V9_TIER})；{label} = {framework_layout_desc(args)}",
        params={"布局臂": ["v9-25", label], "距离阈值": 1000.0, "波束半角": 90.0},
        seeds=[seed],
    )
    d.emit("  （本子命令不做仿真、不报告虚拟时间：--legacy-timing 不适用）")

    case = generate_case(seed, omni_only=False)
    jam = [x for x in case.jammers if x.channel == 2]
    if not jam:
        d.emit(f"seed={seed} 的案例里没有信道 2（频道取 1..20 的随机子集），无法核对；"
               f"可用 --seeds 换一个含信道 2 的种子。")
        return d.finish(skipped="no-channel-2") and 0
    j = jam[0]
    d.emit(f"信道2: pos=({j.position.x:.1f},{j.position.y:.1f}) "
           f"dir={j.direction_deg:.2f} R_eff={j.effective_radius_m:.0f}")

    cover = {}
    for name, pts in (("v9-25", two_tier_nodes(*V9_TIER)), (label, arm.tolist())):
        d.emit(f"\n{name}:")
        n_cov = 0
        for i, (px, py) in enumerate(pts):
            dist = np.hypot(px - j.position.x, py - j.position.y)
            bearing_s2p = degrees_atan2(py - j.position.y, px - j.position.x)
            diff = angle_difference_deg(bearing_s2p, j.direction_deg)
            if dist <= 1000.0 and diff <= 90.0:
                n_cov += 1
                brg = degrees_atan2(j.position.y - py, j.position.x - px)
                d.emit(f"  #{i:2d} ({px:7.1f},{py:7.1f}) dist={dist:5.0f} "
                       f"波束偏角={diff:5.1f}° 读数方位={brg:6.1f}°")
        d.emit(f"  → 覆盖点数 = {n_cov}")
        cover[name] = n_cov

    return d.finish(seed=seed, channel=2, coverage=cover,
                    jammer={"x": round(j.position.x, 2), "y": round(j.position.y, 2),
                            "direction_deg": j.direction_deg,
                            "effective_radius_m": round(j.effective_radius_m, 1)}) and 0


def cmd_v17_seed25(args: argparse.Namespace) -> int:
    """快照 ``_diag_v17_seed25.py``：seed 25 信道 2 深挖（v14 vs v17）。"""
    d = Diag(args, "v17-seed25")
    seeds = _seeds(args, (25,), [25])
    seed = seeds[0]
    params = {"schedule_min_readings": 2, "scan_probe_limit": 2}
    d.head(
        what="深挖 seed 25 信道 2：v14（v9 25 点发现层）与 v17（方法二 24 点布局）各自的"
             "读数、估计点、可行域顶点数、schedule_log 与清除记录",
        layout=f"v14 = Q4V14Strategy；v17 = Q4V17Strategy（LAYOUT_Q4_V17，{len(LAYOUT_Q4_V17)} 点）",
        params=params,
        seeds=[seed],
    )
    d.timing()
    d.note(RAY_CAVEAT)
    d.note(FINITE_SIM_CAVEAT)

    result = {"seed": seed}
    for which in ("v14", "v17"):
        if which == "v14":
            factory = lambda: Q4V14Strategy(Q4V14Params(**params))  # noqa: E731
        else:
            factory = lambda: Q4V17Strategy(Q4V17Params(**params))  # noqa: E731
        _case, state, strat, s = run_online(
            seed, factory, omni_only=False,
            legacy_timing=args.legacy_timing, client_kind=args.client,
        )
        ch = state.channels[2]
        d.emit(f"\n=== {which}: cleared={s['cleared_count']}/{s['n_jammers']} "
               f"vt={s['virtual_time_s']:.0f} ===")
        d.emit(f"  信道2: is_cleared={ch.is_cleared} readings={len(ch.readings)}")
        for apex, brg in ch.readings:
            d.emit(f"    apex=({apex.x:7.1f},{apex.y:7.1f}) bearing={brg:6.2f}°")
        est = strat._estimate(ch)
        d.emit(f"  估计点: {None if est is None else (round(est.x, 1), round(est.y, 1))}")
        reg = strat._region(ch)
        d.emit(f"  可行域顶点数={len(reg)}")
        n2 = [t for t in strat.schedule_log if t[1] == 2]
        d.emit(f"  schedule_log 中信道2 的任务: {n2}")
        clears2 = [t for t in strat.trace
                   if t.get("kind") == "clear" and t.get("channel") == 2]
        d.emit(f"  trace 中信道2 清除记录 {len(clears2)} 条:")
        for t in clears2[:6]:
            d.emit(f"    {t}")
        result[which] = {"cleared": s["cleared_count"], "n": s["n_jammers"],
                         "vt": round(s["virtual_time_s"], 2),
                         "ch2_cleared": bool(ch.is_cleared),
                         "ch2_readings": len(ch.readings),
                         "ch2_region_vertices": len(reg),
                         "ch2_clears": len(clears2)}

    return d.finish(**result) and 0


def _jammer_xy(j):
    """真值源坐标：先按快照的 ``j.x/j.y``（或 dict）读，再退回仓库的 ``j.position``。"""
    if hasattr(j, "x") and hasattr(j, "y"):
        return float(j.x), float(j.y)
    if isinstance(j, dict) and "x" in j and "y" in j:
        return float(j["x"]), float(j["y"])
    if hasattr(j, "position"):
        return float(j.position.x), float(j.position.y)
    raise TypeError(f"无法读取源坐标：{type(j).__name__}")


def _jammer_ori(j):
    """真值源朝向：快照的 ``orientation_deg``/``theta_deg`` → 仓库的 ``direction_deg``。"""
    for name in ("orientation_deg", "theta_deg", "direction_deg"):
        if hasattr(j, name):
            v = getattr(j, name)
            return None if v is None else float(v)
    if isinstance(j, dict):
        for name in ("orientation_deg", "theta_deg", "direction_deg"):
            if j.get(name) is not None:
                return float(j[name])
    return None


def cmd_v17_miss(args: argparse.Namespace) -> int:
    """快照 ``_diag_v17_miss.py``：漏清源定位 + 真值源 × 布局的正面最近距离。"""
    d = Diag(args, "v17-miss")
    seeds = _seeds(args, (25,), [25])
    seed = seeds[0]
    label, arm = layout_arm(args)
    params = {"schedule_min_readings": 2, "scan_probe_limit": 2}
    d.head(
        what="定位漏清的源：先跑 v14/v17 列出未清除信道与读数，再把**真值**源位置/朝向"
             "对两个布局各点算正面最近距离（真值仅供离线诊断，策略看不到）",
        layout=f"v9-25 = two_tier_nodes(*{V9_TIER})；{label} = {framework_layout_desc(args)}",
        params=params,
        seeds=[seed],
    )
    d.timing()
    d.note(RAY_CAVEAT)
    d.note(FINITE_SIM_CAVEAT)

    case = generate_case(seed, omni_only=False)
    jammers = case.jammers if hasattr(case, "jammers") else None
    d.emit(f"case.jammers: {jammers}")
    d.emit(f"case 属性: {[a for a in dir(case) if not a.startswith('_')]}")

    result = {"seed": seed, "per_strategy": {}}
    for which in ("v14", "v17"):
        if which == "v14":
            factory = lambda: Q4V14Strategy(Q4V14Params(**params))  # noqa: E731
        else:
            factory = lambda: Q4V17Strategy(Q4V17Params(**params))  # noqa: E731
        _case, state, _strat, s = run_online(
            seed, factory, omni_only=False,
            legacy_timing=args.legacy_timing, client_kind=args.client,
        )
        d.emit(f"\n{which}: cleared={s['cleared_count']}/{s['n_jammers']} "
               f"vt={s['virtual_time_s']:.0f} scan_visited={state.scan_points_visited}")
        uncleared = []
        for ch, st in state.channels.items():
            if not st.is_cleared:
                d.emit(f"  未清除信道 {ch}: readings={len(st.readings)} "
                       f"pos_est={getattr(st, 'position_est', None)}")
                uncleared.append({"channel": ch, "readings": len(st.readings),
                                  "probe_count": st.probe_count,
                                  "clear_attempts": st.clear_attempts,
                                  "clear_failures": st.clear_failures})
        result["per_strategy"][which] = {
            "cleared": s["cleared_count"], "n": s["n_jammers"],
            "vt": round(s["virtual_time_s"], 2),
            "uncleared_channels": s["uncleared_channels"],
            "detected_uncleared": uncleared,
        }

    # 真值源位置 × 两个布局的正面距离
    try:
        pts_v9 = np.array(two_tier_nodes(*V9_TIER), dtype=float)
        pts_arm = np.asarray(arm, dtype=float)
        d.emit("\n真值源 vs 布局正面距离：")
        truth_rows = []
        for j in case.jammers:
            x0, x1 = _jammer_xy(j)
            x = np.array([x0, x1], dtype=float)
            ori = _jammer_ori(j)
            line = (f"  源@({x[0]:.0f},{x[1]:.0f}) |x|={np.hypot(*x):.0f}"
                    f" 朝向={'(全向)' if ori is None else f'{ori:.1f}°'}")
            row = {"x": round(x0, 2), "y": round(x1, 2),
                   "direction_deg": None if ori is None else round(ori, 3), "frontal": {}}
            if ori is not None:
                th = np.radians(float(ori))
                n = np.array([np.cos(th), np.sin(th)])
                for nm, P in (("v9-25", pts_v9), (label, pts_arm)):
                    dv = P - x
                    dist = np.hypot(dv[:, 0], dv[:, 1])
                    ndot = dv @ n
                    fr = np.where((ndot > 0) & (dist <= 1000.0), dist, np.inf)
                    best = float(fr.min())
                    row["frontal"][nm] = None if not np.isfinite(best) else round(best, 1)
                    line += (f" | {nm}: 正面最近={best:.0f}" if np.isfinite(best)
                             else f" | {nm}: 无正面岗")
            truth_rows.append(row)
            d.emit(line)
        result["truth"] = truth_rows
    except Exception as exc:  # noqa: BLE001
        d.emit(f"真值读取失败: {exc}")
        result["truth_error"] = f"{type(exc).__name__}: {exc}"

    return d.finish(**result) and 0


def cmd_visit_check(args: argparse.Namespace) -> int:
    """快照 ``_q4_visit_check.py``：核实 v14 实际访问的扫描点数与行驶分解。"""
    d = Diag(args, "visit-check")
    seeds = _seeds(args, (23, 1, 10), [23])
    params = {"schedule_min_readings": 2, "scan_probe_limit": 2}
    d.head(
        what="核实 v14 实际访问的扫描点数量（visited / n_scan）与行驶分解 dist_by_kind、"
             "调度表长度",
        layout=f"v14 = Q4V14Strategy（v9 25 点发现层），参数={params}",
        params=params,
        seeds=seeds,
    )
    d.timing()
    d.note(RAY_CAVEAT)
    d.note(FINITE_SIM_CAVEAT)

    rows = []
    for seed in seeds:
        _case, state, strat, s = run_online(
            seed, lambda: Q4V14Strategy(Q4V14Params(**params)), omni_only=False,
            legacy_timing=args.legacy_timing, client_kind=args.client,
        )
        n_scan = len(strat._scan_points())
        d.emit(f"seed={seed} n={s['n_jammers']} visited={state.scan_points_visited}/{n_scan} "
               f"dist={s['move_distance_m']:.0f} by_kind="
               f"{ {k: round(v) for k, v in strat.dist_by_kind.items()} } "
               f"schedule_len={len(strat.schedule_log)} head={strat.schedule_log[:30]}")
        rows.append({"seed": seed, "visited": state.scan_points_visited, "n_scan": n_scan,
                     "dist": round(s["move_distance_m"], 1),
                     "by_kind": {k: round(v, 1) for k, v in strat.dist_by_kind.items()},
                     "schedule_len": len(strat.schedule_log),
                     "vt": round(s["virtual_time_s"], 2)})

    return d.finish(cases=rows) and 0


def cmd_v14_stats(args: argparse.Namespace) -> int:
    """快照 ``_q4_v14_stats.py``：v14 成本画像（里程/检测/清除按类型与 reason 分解）。"""
    d = Diag(args, "v14-stats")
    seeds = _seeds(args, range(1, 31), [1, 2, 3])
    params = {"schedule_min_readings": 2, "scan_probe_limit": 2}
    d.head(
        what="v14 成本画像：里程按任务类型（scan/clear/finish）分解、检测与清除按 reason "
             "分解，按“每源”归一并取各案例的中位数",
        layout=f"v14 = Q4V14Strategy（v9 25 点发现层），参数={params}",
        params=params,
        seeds=seeds,
    )
    d.timing()
    d.note(RAY_CAVEAT)
    d.note(FINITE_SIM_CAVEAT)

    agg_dist: Counter = Counter()
    agg_meas: Counter = Counter()
    agg_clear: Counter = Counter()
    rows = []
    for seed in seeds:
        _case, _state, strat, s = run_online(
            seed, lambda: Q4V14Strategy(Q4V14Params(**params)), omni_only=False,
            legacy_timing=args.legacy_timing, client_kind=args.client,
        )
        dbk = strat.dist_by_kind
        n = s["n_jammers"]
        meas_by_reason: Counter = Counter()
        clear_by_reason: Counter = Counter()
        for t in strat.trace:
            if t["kind"] == "measure":
                meas_by_reason[t.get("reason", "?")] += 1
            elif t["kind"] == "clear":
                clear_by_reason[t.get("reason", "?")] += 1
        agg_dist.update({k: v / n for k, v in dbk.items()})
        for k, v in meas_by_reason.items():
            agg_meas[k] += v / n
        for k, v in clear_by_reason.items():
            agg_clear[k] += v / n
        avg_clear = s["average_clear_time_s"]
        rows.append((seed, s["cleared_count"], n, s["virtual_time_s"], avg_clear,
                     dbk.get("scan", 0) / n, dbk.get("clear", 0) / n, dbk.get("finish", 0) / n))
        d.emit(f"seed={seed:<3} {s['cleared_count']}/{n} vt={s['virtual_time_s']:6.0f} "
               f"avg={'  n/a' if avg_clear is None else f'{avg_clear:6.1f}'} "
               f"dist/src: scan={dbk.get('scan', 0) / n:6.0f} "
               f"clear={dbk.get('clear', 0) / n:6.0f} finish={dbk.get('finish', 0) / n:6.0f}")

    vt = [r[3] for r in rows]
    avg = [r[4] for r in rows if r[4] is not None]
    d.emit("")
    d.emit(f"vt_med={statistics.median(vt):.0f} "
           f"avg_med={statistics.median(avg):.2f}" if avg else
           f"vt_med={statistics.median(vt):.0f} avg_med=n/a（没有任何案例清除成功）")
    d.emit("dist per src (mean): " + ", ".join(f"{k}={v:.0f}" for k, v in sorted(agg_dist.items())))
    d.emit("meas per src (mean): " + ", ".join(f"{k}={v:.2f}" for k, v in sorted(agg_meas.items())))
    d.emit("clear per src (mean): " + ", ".join(f"{k}={v:.2f}" for k, v in sorted(agg_clear.items())))
    d.note(FINITE_SIM_CAVEAT)

    return d.finish(
        vt_med=round(statistics.median(vt), 2),
        avg_med=None if not avg else round(statistics.median(avg), 3),
        dist_per_src={k: round(v, 2) for k, v in sorted(agg_dist.items())},
        meas_per_src={k: round(v, 3) for k, v in sorted(agg_meas.items())},
        clear_per_src={k: round(v, 3) for k, v in sorted(agg_clear.items())},
        cases=[{"seed": r[0], "cleared": r[1], "n": r[2], "vt": round(r[3], 2),
                "avg_clear_time_s": r[4]} for r in rows],
    ) and 0


def cmd_ab_v17(args: argparse.Namespace) -> int:
    """快照 ``_ab_q4_v17.py``：v14 / v14r / v17 三臂 A/B（同批 case、同参数）。"""
    d = Diag(args, "ab-v17")
    seeds = _seeds(args, range(1, 31), [1, 2])
    arms = ["v14", "v14r", "v17"] if args.only == "both" else args.only.split(",")
    bad = [a for a in arms if a not in ("v14", "v14r", "v17")]
    if bad:
        raise SystemExit(f"--only 只认 v14 / v14r / v17 / both，收到 {bad}")
    d.head(
        what="Q4 布局 A/B：v14（v9 25 点发现层）vs v14r（v17 代码但 use_opt_layout=False，"
             "隔离环形兜底贡献）vs v17（方法二 24 点布局），同批 case、同参数比虚拟时间",
        layout=f"v14 = Q4V14Strategy（25 点）；v17 = Q4V17Strategy（LAYOUT_Q4_V17，"
               f"{len(LAYOUT_Q4_V17)} 点）；v14r = Q4V17Strategy(use_opt_layout=False)",
        params={"schedule_min_readings": 2, "scan_probe_limit": 2, "arms": arms},
        seeds=seeds,
    )
    d.timing()
    d.note(RAY_CAVEAT)
    d.note(FINITE_SIM_CAVEAT)
    d.emit(f"布局点数: v14={len(two_tier_nodes(*V9_TIER))}(v9两圈式) v17={len(LAYOUT_Q4_V17)}")

    res: dict[str, list[dict]] = {a: [] for a in arms}
    for seed in seeds:
        row = f"seed={seed:<3}"
        for a in arms:
            if a == "v14":
                factory = lambda: Q4V14Strategy(Q4V14Params(schedule_min_readings=2,
                                                            scan_probe_limit=2))  # noqa: E731
            elif a == "v14r":
                factory = lambda: Q4V17Strategy(Q4V17Params(schedule_min_readings=2,
                                                            scan_probe_limit=2,
                                                            use_opt_layout=False))  # noqa: E731
            else:
                factory = lambda: Q4V17Strategy(Q4V17Params(schedule_min_readings=2,
                                                            scan_probe_limit=2))  # noqa: E731
            _case, _state, strat, s = run_online(
                seed, factory, omni_only=False,
                legacy_timing=args.legacy_timing, client_kind=args.client,
            )
            r = {"vt": s["virtual_time_s"], "cleared": s["cleared_count"],
                 "n": s["n_jammers"], "avg": s["average_clear_time_s"],
                 "scan_d": strat.dist_by_kind.get("scan", 0.0)}
            res[a].append(r)
            row += (f"  {a}: vt={r['vt']:6.0f} {r['cleared']}/{r['n']}"
                    f" scan={r['scan_d']:6.0f}")
        d.emit(row)

    summary: dict[str, dict] = {}
    d.emit("")
    for a in arms:
        vts = [r["vt"] for r in res[a]]
        cl = sum(r["cleared"] for r in res[a])
        nn = sum(r["n"] for r in res[a])
        sd = statistics.mean([r["scan_d"] for r in res[a]])
        d.emit(f"{a}: vt_med={statistics.median(vts):.1f} "
               f"vt_mean={statistics.mean(vts):.1f} 清除 {cl}/{nn} "
               f"scan_dist_mean={sd:.0f}m")
        summary[a] = {"vt_med": round(statistics.median(vts), 2),
                      "vt_mean": round(statistics.mean(vts), 2),
                      "cleared": cl, "sources": nn, "scan_dist_mean": round(sd, 1)}
    if "v14" in res and "v17" in res:
        dv = [res["v14"][i]["vt"] - res["v17"][i]["vt"] for i in range(len(seeds))]
        d.emit(f"Δvt(v14-v17): med={statistics.median(dv):+.1f} "
               f"mean={statistics.mean(dv):+.1f} "
               f"min={min(dv):+.0f} max={max(dv):+.0f}")
        summary["delta_v14_v17"] = {"med": round(statistics.median(dv), 2),
                                    "mean": round(statistics.mean(dv), 2),
                                    "min": round(min(dv), 1), "max": round(max(dv), 1)}
    if "v14" in res and "v14r" in res:
        dr = [res["v14"][i]["vt"] - res["v14r"][i]["vt"] for i in range(len(seeds))]
        d.emit(f"Δvt(v14-v14r): med={statistics.median(dr):+.1f} "
               f"mean={statistics.mean(dr):+.1f}")
        summary["delta_v14_v14r"] = {"med": round(statistics.median(dr), 2),
                                     "mean": round(statistics.mean(dr), 2)}
    d.note(FINITE_SIM_CAVEAT + "（配对差值只在本次种子集合上成立）")

    return d.finish(arms=summary,
                    cases=[{"seed": s, **{a: res[a][i] for a in arms}}
                           for i, s in enumerate(seeds)]) and 0


# =========================================================================== 在线：v9
def cmd_v9(args: argparse.Namespace) -> int:
    """快照 ``_diag_v9.py``：v9 定向场景漏清归因（未探测到 vs 探测到没清掉）。"""
    d = Diag(args, "v9")
    params = params_from_args(args)
    seeds = _seeds(args, [2, 3, 7, 9, 13], [2, 3])
    d.head(
        what="v9（定向场景）漏清归因：漏掉的频道是“从未被探测到”还是“探测到但没清掉”"
             "（读 v9 自己的 Q4V9Strategy 状态，含定向源统计，omni_only=False）",
        layout=f"v9 = Q4V9Strategy（两圈式 25 点发现层），参数={params or '默认'}",
        params={"strategy": "v9", "params": params or {}},
        seeds=seeds,
    )
    d.timing()
    d.note(FINITE_SIM_CAVEAT)

    rows = []
    for seed in seeds:
        _case, state, _strat, s = run_online(
            seed, lambda: Q4V9Strategy(Q4V9Params(**params)), omni_only=False,
            legacy_timing=args.legacy_timing, client_kind=args.client,
        )
        truth = {j.channel: j for j in _case.jammers}
        d.emit(f"-- seed={seed} n={s['n_jammers']} 定向源="
               f"{sum(1 for j in _case.jammers if not j.is_omni)} "
               f"cleared={s['cleared_count']}/{s['n_jammers']} vt={s['virtual_time_s']:.0f} "
               f"dist={s['move_distance_m']:.0f} meas={s['measure_count']}")
        missed = []
        for c in s["uncleared_channels"]:
            st = state.channels[c]
            j = truth.get(c)
            d.emit(
                f"    漏 ch{c:<3} 类型={'定向' if (j and not j.is_omni) else '全向'} "
                f"读数={len(st.readings)} 探测次数={st.probe_count} "
                f"清除尝试={st.clear_attempts} 失败={st.clear_failures}"
            )
            missed.append({"channel": c,
                           "kind": "directional" if (j and not j.is_omni) else "omni",
                           "readings": len(st.readings), "probe_count": st.probe_count,
                           "clear_attempts": st.clear_attempts,
                           "clear_failures": st.clear_failures})
        rows.append({"seed": seed, "cleared": s["cleared_count"], "n": s["n_jammers"],
                     "directional": sum(1 for j in _case.jammers if not j.is_omni),
                     "vt": round(s["virtual_time_s"], 2), "missed": missed})

    return d.finish(cases=rows) and 0


def cmd_v9b(args: argparse.Namespace) -> int:
    """快照 ``_diag_v9b.py``：v9 定向源精定位逐轮日志（可行域直径 / MEC 收缩）。"""
    d = Diag(args, "v9b")
    params = params_from_args(args)
    seeds = _seeds(args, (3,), [3])
    watch = [int(x) for x in args.watch.split(",") if x.strip()] if args.watch else []
    d.head(
        what="v9 定向源精定位逐轮日志：进入 _localize_and_clear 时的可行域直径与 MEC 是否"
             "在收缩、本轮 measure/clear 轨迹与落空发生在哪一步（omni_only=False）",
        layout=f"v9 = LogV9(Q4V9Strategy 子类)，参数={params or '默认'}",
        params={"strategy": "v9(LogV9)", "params": params or {}, "watch": watch},
        seeds=seeds,
    )
    d.timing()
    d.note(RAY_CAVEAT)
    d.note(FINITE_SIM_CAVEAT)

    class LogV9(Q4V9Strategy):
        def __init__(self, params=None, watch=()) -> None:
            super().__init__(params)
            self.watch = set(watch)

        def _localize_and_clear(self, client, state, channel):
            ch = state.channels[channel]
            if channel in self.watch and not ch.is_cleared:
                region = self._region(ch) if ch.readings else []
                diam = polygon_diameter(region) if len(region) >= 3 else -1
                circle = self._clear_circle(ch)
                d.emit(f"   >> ch{channel} 开始精定位: 读数={len(ch.readings)} "
                       f"区域直径={diam:.1f} "
                       f"MEC={'None' if circle is None else round(circle.radius, 1)}")
            n0 = len(self.trace)
            Q4V9Strategy._localize_and_clear(self, client, state, channel)
            if channel in self.watch:
                steps = []
                for t in self.trace[n0:]:
                    if t.get("kind") == "measure":
                        steps.append(f"{t.get('reason')}:{t.get('outcome')}")
                    elif t.get("kind") == "clear":
                        steps.append(f"clear:{t.get('outcome')}")
                d.emit(f"   >> ch{channel} 结束 清除={ch.is_cleared} "
                       f"轨迹={' '.join(steps)}")

    rows = []
    for seed in seeds:
        _case, _state, _strat, s = run_online(
            seed, lambda: LogV9(Q4V9Params(**params), watch=watch), omni_only=False,
            legacy_timing=args.legacy_timing, client_kind=args.client,
        )
        d.emit(f"-- seed={seed} cleared={s['cleared_count']}/{s['n_jammers']} "
               f"漏={s['uncleared_channels']}")
        rows.append({"seed": seed, "cleared": s["cleared_count"], "n": s["n_jammers"],
                     "uncleared": s["uncleared_channels"],
                     "vt": round(s["virtual_time_s"], 2)})

    if not watch:
        d.note("未给 --watch（快照默认也是空）：只打印每局汇总；"
               "要逐轮日志请加 --watch <频道,频道>。")
    return d.finish(cases=rows) and 0


# =========================================================================== 离线诊断
def _patch_jo_emit(d: Diag) -> None:
    """把 joint_opt 内部的 ``emit`` 接到本次诊断的缓冲（快照各脚本的 ``jo.emit = ...``）。"""
    jo.emit = d.emit  # type: ignore[assignment]


def cmd_knife(args: argparse.Namespace) -> int:
    """快照 ``_diag_knife.py``：删#J → 几何修复 → 可行性微调 → knife-edge 定向修复。"""
    d = Diag(args, "knife")
    quick = args.quick
    # --quick 的省法：只跑第一个删点候选 + 证书网格降为 coarse。
    # 刻意**不**削减 polish 轮数：knife_fix 的第二步（20m/120 朝向的粗筛）在"微调没收敛"
    # 的输入上会跑满 40 轮，实测反而比完整跑一遍更慢（86 s vs ~33 s）。
    cands = args.candidates if args.candidates else ([5] if quick else [5, 6])
    polish_rounds = args.polish_rounds
    knife_moves = args.knife_moves
    grids = jo.CERT_GRIDS_COARSE if (
        args.cert_level == "coarse" or quick) else jo.CERT_GRIDS_FULL
    _patch_jo_emit(d)
    d.head(
        what="删#J（几何修复 → 纯可行性微调 → knife-edge 定向修复）：24 点能否真正完备"
             "，以及修复后的点集/路线/目标值",
        layout=f"基线 v9 两圈式 25 点 = two_tier_nodes(*{V9_TIER})；"
               f"修复链 repair_geom(safety=10) → polish_feas(safety=14) → knife_fix(safety=12, margin=4)",
        params={"删点候选": cands, "polish 轮数": polish_rounds, "knife 步数上限": knife_moves,
                "证书网格": [list(g) for g in grids],
                "验收": "_multi_cert_ok(12) + complete(20,120,12)"},
        seeds=None,
    )
    d.note(SAMPLING_CAVEAT)

    Q0 = baseline_v9_layout()
    L0 = jo.route_len(jo.best_order(Q0.tolist()), Q0)
    d.emit(f"基线: m=25 L={L0:.0f}m obj={L0 + 2500:.0f}")

    t0 = time.time()
    found = None
    for J in cands:
        d.emit(f"\n=== 删#{J} ===")
        Q2 = np.delete(Q0, J, axis=0)
        Q2r = jo.repair_geom(Q2, safety=10.0)
        ok, msg = jo._multi_cert_ok(Q2r, 12.0, grids=grids)
        d.emit(f"几何修复: {msg}")
        V = jo.polish_feas(Q2r, safety=14.0, max_rounds=polish_rounds)
        ok, msg = jo._multi_cert_ok(V, 12.0, grids=grids)
        okf, wf = jo.complete(V.tolist(), step=20.0, n_phi=120, safety=12.0)
        d.emit(f"微调后: {msg} / (20,120)={wf:.0f}{'✓' if okf else '✗'}")
        K = jo.knife_fix(V, safety=12.0, margin=4.0, max_moves=knife_moves)
        ok, msg = jo._multi_cert_ok(K, 12.0, grids=grids)
        okf, wf = jo.complete(K.tolist(), step=20.0, n_phi=120, safety=12.0)
        o2 = jo.best_order(K.tolist())
        L2 = jo.route_len(o2, K)
        d.emit(f"knife 修复: {msg} / (20,120)={wf:.0f}{'✓' if okf else '✗'}")
        d.emit(f"  m={len(K)} L={L2:.0f} (基线 {L0:.0f}, ΔL={L2 - L0:+.0f}) "
               f"obj={L2 + 100 * len(K):.0f} (基线 {L0 + 2500:.0f}) "
               f"[{time.time() - t0:.0f}s]")
        # 快照就是拿这次 _multi_cert_ok 的 ok 与 complete(20,120) 的 okf 一起判定
        if ok and okf:
            pts = [[round(float(v), 1) for v in K[i]] for i in o2]
            jp = layout_io.save_layout_json(
                d.out_dir / "knife_layout.json", pts, route_len_m=L2,
                extra={"source": "script/q4/diagnose.py knife",
                       "deleted_index": J, "safety": 12.0,
                       "certified_lower_bound": False,
                       "note": "有限采样判据；连续域证书请跑 script/verify_layout_certificate.py"},
            ) if d.write else None
            frag = layout_io.emit_layout(
                pts, route_len_m=L2,
                note=[f"生成命令：python3 script/q4/diagnose.py knife（删#{J}）",
                      "粘贴前请重跑 script/verify_layout_certificate.py 复核连续域证书"],
            )
            fp = d.save_extra_text("knife_layout_fragment.py", frag)
            d.emit(f"  ★ 已写 {jp} / {fp}")
            d.emit(f"  点集(访问序): {pts}")
            found = {"deleted_index": J, "m": len(pts), "route_len_m": round(L2, 1),
                     "points_in_order": pts}
            break

    d.note("上面每个“完备”都是**有限采样**结果；“未收敛”也只在采样意义上成立。")
    return d.finish(baseline_route_len_m=round(L0, 1), candidates=cands, found=found) and 0


def cmd_polish(args: argparse.Namespace) -> int:
    """快照 ``_diag_polish.py``：删#5 几何修复后，纯可行性微调能否收尾。"""
    d = Diag(args, "polish")
    quick = args.quick
    rounds = args.polish_rounds if args.polish_rounds is not None else (3 if quick else 14)
    _patch_jo_emit(d)
    d.head(
        what="删#5 → repair_geom(safety=10) 后细网格 worst 仍差 ~14 m 时，纯可行性微调"
             "（polish_feas，无路程项、多网格喂割）能否把细网格补齐",
        layout=f"基线 v9 两圈式 25 点 = two_tier_nodes(*{V9_TIER})；验收 complete(25,90,12)",
        params={"删除索引": 5, "polish 轮数": rounds, "repair_geom safety": 10.0,
                "polish safety": 12.0},
        seeds=None,
    )
    d.note(SAMPLING_CAVEAT)

    Q0 = baseline_v9_layout()
    L0 = jo.route_len(jo.best_order(Q0.tolist()), Q0)
    d.emit(f"基线: m={len(Q0)} L={L0:.0f}m")

    j = 5
    keep = [i for i in range(len(Q0)) if i != j]
    Q2 = Q0[keep]
    t1 = time.time()
    Q2r = jo.repair_geom(Q2, safety=10.0)
    ok, w = jo.complete(Q2r.tolist(), step=25.0, n_phi=90, safety=12.0)
    d.emit(f"几何修复: worst_fine={w:.1f} ({'通过' if ok else '未通过'})")
    result = {"geometry_ok": bool(ok), "worst_fine": round(w, 1)}

    if not ok:
        Q3 = jo.polish_feas(Q2r, safety=12.0, max_rounds=rounds)
        ok, w = jo.complete(Q3.tolist(), step=25.0, n_phi=90, safety=12.0)
        o3 = jo.best_order(Q3.tolist())
        L3 = jo.route_len(o3, Q3)
        shift = np.hypot(*(Q3 - Q2r).T)
        d.emit(f"微调后: m={len(Q3)} worst_fine={w:.1f} ({'通过' if ok else '未通过'}) "
               f"L={L3:.0f} (dL={L3 - L0:+.0f})  max_shift={shift.max():.1f}")
        d.emit(f"点集: {np.round(Q3, 1).tolist()}")
        result.update({"polish_ok": bool(ok), "worst_fine_after": round(w, 1),
                       "route_len_m": round(L3, 1), "dL": round(L3 - L0, 1),
                       "max_shift_m": round(float(shift.max()), 2),
                       "points": np.round(Q3, 1).tolist()})
    else:
        d.emit("几何修复已通过，快照在此不再跑 polish_feas（脚本原逻辑：if not ok 才微调）")
    d.emit(f"[{time.time() - t1:.0f}s]")

    return d.finish(**result) and 0


def cmd_repair(args: argparse.Namespace) -> int:
    """快照 ``_diag_repair.py``：delete_repair_step 在基线上能否完成删点。"""
    d = Diag(args, "repair")
    quick = args.quick
    max_cand = args.max_candidates if args.max_candidates is not None else (1 if quick else None)
    brute = (not args.no_brute) and not quick
    _patch_jo_emit(d)
    d.head(
        what="delete_repair_step（删点 → 几何修复 + SCP 微调 → 细网格验收 → L+λm 改善才接受）"
             "在 v9 25 点基线上能否真的删掉一个点",
        layout=f"基线 v9 两圈式 25 点 = two_tier_nodes(*{V9_TIER})；"
               f"λ=100（每停点等效 100 m），验收 complete(25,90,12)",
        params={"λ": 100.0, "每轮候选上限": max_cand if max_cand is not None else "不限",
                "0.25° 采样扫描": brute},
        seeds=None,
    )
    d.note(SAMPLING_CAVEAT)
    if max_cand == 1 or not brute:
        d.note("--quick 只试 1 个候选且跳过 0.25° 采样扫描：**不会**得到快照的完整删点结论，"
               "只验证链路能跑通。")

    Q0 = baseline_v9_layout()
    L0 = jo.route_len(jo.best_order(Q0.tolist()), Q0)
    d.emit(f"基线: m={len(Q0)} L={L0:.0f}m  obj={L0 + 100 * len(Q0):.0f}")

    t1 = time.time()
    Qn = jo.delete_repair_step(Q0, 100.0, max_candidates=max_cand, brute=brute)
    dt = time.time() - t1

    ok, w = jo.complete(Qn.tolist(), step=25.0, n_phi=90, safety=12.0)
    o2 = jo.best_order(Qn.tolist())
    L2 = jo.route_len(o2, Qn)
    d.emit(f"\n结果: m={len(Qn)} L={L2:.0f} worst_fine={w:.1f} "
           f"({'完备' if ok else '不完备'})  obj={L2 + 100 * len(Qn):.0f} "
           f"(基线 obj={L0 + 100 * len(Q0):.0f})  [{dt:.0f}s]")
    d.emit(f"点集: {np.round(Qn, 1).tolist()}")
    d.note("worst_fine 与“完备”都来自有限网格 (25,90) 采样。")

    return d.finish(baseline={"m": len(Q0), "route_len_m": round(L0, 1),
                              "obj": round(L0 + 100 * len(Q0), 1)},
                    result={"m": len(Qn), "route_len_m": round(L2, 1),
                            "worst_fine": round(w, 1), "complete_on_grid": bool(ok),
                            "obj": round(L2 + 100 * len(Qn), 1),
                            "points": np.round(Qn, 1).tolist()},
                    seconds=round(dt, 2)) and 0


def cmd_joint(args: argparse.Namespace) -> int:
    """快照 ``_diag_joint.py``：单点删除扫描 + 坐标步 scp_step + witness 负载分布。"""
    d = Diag(args, "joint")
    quick = args.quick
    max_cuts = 3 if quick and args.scp_max_cuts == 22 else args.scp_max_cuts
    scp_step_grid = (100.0, 36) if quick else (args.scp_step, args.scp_n_phi)
    _patch_jo_emit(d)
    d.head(
        what="联合优化步诊断：① 单点删除扫描（删后 worst 与 ΔL）② 坐标步 scp_step 的位移与"
             "求解器行为 ③ witness 负载（每个点是多少个网格点最坏朝向的证人）",
        layout=f"基线 v9 两圈式 25 点 = two_tier_nodes(*{V9_TIER})；"
               f"certify(50,60) 做负载统计、certify(60,48) 做删除扫描",
        params={"scp trust": 180.0, "scp safety": 12.0, "scp max_cuts": max_cuts,
                "scp 种子网格": f"step={scp_step_grid[0]:.0f}, n_phi={scp_step_grid[1]}"},
        seeds=None,
    )
    d.note(SAMPLING_CAVEAT)
    d.note("scp_step 的“无违反”只是采样意义上的；快照的坐标步卡住也常在采样边界上。")

    Q0 = baseline_v9_layout()
    order = jo.best_order(Q0.tolist())
    L0 = jo.route_len(order, Q0)
    d.emit(f"基线: m={len(Q0)} L={L0:.0f}m")

    # -- 1) 单点删除：删后 worst 与 ΔL
    d.emit("\n单点删除扫描（step=60, n_phi=48, safety=20 → 需 worst<=980）:")
    del_rows = []
    for j in range(len(Q0)):
        keep = [i for i in range(len(Q0)) if i != j]
        Q2 = Q0[keep]
        w, *_ = jo.certify(Q2, step=60.0, n_phi=48)
        o2 = jo.best_order(Q2.tolist())
        L2 = jo.route_len(o2, Q2)
        ok = "可删" if w <= 980.0 else "    "
        d.emit(f"  删#{j:2d} ({Q0[j][0]:7.1f},{Q0[j][1]:7.1f}): worst={w:7.1f} "
               f"L={L2:7.0f} (dL={L2 - L0:+6.0f})  {ok}")
        del_rows.append({"index": j, "worst": round(w, 1), "L": round(L2, 1),
                         "dL": round(L2 - L0, 1), "deletable": bool(w <= 980.0)})

    # -- 2) 坐标步：直接调 scp_step，看移动了多少
    d.emit("\n坐标步诊断:")
    Qc = jo.scp_step(Q0, order, trust=180.0, safety=12.0, max_cuts=max_cuts,
                     step=scp_step_grid[0], n_phi=scp_step_grid[1], tag="diag")
    shift = np.hypot(*(Qc - Q0).T)
    Lc = jo.route_len(order, Qc)
    wc, *_ = jo.certify(Qc, step=50.0, n_phi=60)
    d.emit(f"  max_shift={shift.max():.1f}m mean_shift={shift.mean():.1f}m")
    d.emit(f"  L: {L0:.0f} -> {Lc:.0f} (dL={Lc - L0:+.0f})  worst={wc:.1f}")
    # 快照此处的注释说要看"坐标步 SLSQP 状态"，但正文没有打印；端口里把它放进 JSON 摘要
    # 的 coordinate_step.solver，不额外增加打印列。
    # -- 3) witness 负载分布
    worst, _X, _nvec, witness, dmax, _at = jo.certify(Q0, step=50.0, n_phi=60)
    load = np.bincount(witness[witness >= 0], minlength=len(Q0))
    d.emit("\nwitness 负载（每个点是多少个网格点最坏朝向的证人）:")
    for j in np.argsort(-load):
        d.emit(f"  #{j:2d} ({Q0[j][0]:7.1f},{Q0[j][1]:7.1f}): load={load[j]:5d}")
    d.emit(f"  active(dmax>=760)网格点数: {(dmax >= 760).sum()} / {len(dmax)}")

    return d.finish(
        baseline_route_len_m=round(L0, 1),
        deletions=del_rows,
        coordinate_step={"max_shift_m": round(float(shift.max()), 2),
                         "mean_shift_m": round(float(shift.mean()), 2),
                         "route_len_m": round(Lc, 1), "dL": round(Lc - L0, 1),
                         "worst_after": round(wc, 1), "solver": dict(jo.LAST_SOLVE)},
        witness_load={int(j): int(load[j]) for j in np.argsort(-load)},
        active_grid_points=int((dmax >= 760).sum()),
    ) and 0


# =========================================================================== list
SUBCOMMANDS: tuple[tuple[str, str, str, str], ...] = (
    ("clears", "online", "~8s（--quick ~2s）",
     "v8/v10 每次 /clear 尝试归因：reason × 命中率 × 到达真值的估计误差；direct 尝试按当时读数条数分桶"),
    ("probe", "online", "~3s（--quick ~1s）",
     "逐频道检测分布：扫描检出次数 / 其它检出次数 / 读数条数 / 最终是否清除，并给出有源与无源频道的检出次数直方图"),
    ("reason", "online", "~7s（--quick ~2s）",
     "检测预算去向：把 measure 的 reason 归并成 scan / refine / 其它，给出每场次数、占比与平均虚拟时间/里程"),
    ("cover2", "online*", "<1s",
     "seed 25 信道 2（定向源）在 v9 25 点与方法二布局下的覆盖点数与各点读数方位（纯几何核对，不仿真）"),
    ("v17-seed25", "online", "~2s",
     "seed 25 信道 2 深挖：v14 与 v17 各自的读数、估计点、可行域顶点数、schedule_log 与清除记录"),
    ("v17-miss", "online", "~2s",
     "定位漏清的源：v14/v17 未清除信道与读数，再把真值源位置/朝向对两个布局算正面最近距离"),
    ("visit-check", "online", "~3s（--quick ~1s）",
     "核实 v14 实际访问的扫描点数 visited/n_scan 与行驶分解 dist_by_kind、调度表长度"),
    ("v14-stats", "online", "~30s（--quick ~3s）",
     "v14 成本画像：里程按 scan/clear/finish 分解、检测与清除按 reason 分解，按每源归一并取中位数"),
    ("ab-v17", "online", "~90s（--quick ~6s）",
     "布局 A/B：v14(25 点) vs v14r(环形兜底隔离) vs v17(24 点) 同批 case 同参数比虚拟时间与扫描里程"),
    ("knife", "offline", "~40-80s（--quick ~28s）",
     "删#5/#6 → 几何修复 → 纯可行性微调 → knife-edge 定向修复：24 点能否真正完备，成功则写出布局产物；"
     "--quick 只跑删#5 且证书网格降为 coarse（不减 polish 轮数：微调没收敛时 knife_fix 反而更慢）"),
    ("polish", "offline", "~27s（--quick ~8s）",
     "删#5 几何修复后细网格仍差 ~14 m 时，polish_feas（无路程项、多网格喂割）能否把细网格补齐"),
    ("repair", "offline", "默认数分钟（--quick ~8s）",
     "delete_repair_step（几何修复 + SCP 微调 + 细网格验收 + L+λm 改善）在 25 点基线上能否真的删掉一个点"),
    ("joint", "offline", "~170s（--quick ~6s）",
     "联合优化步诊断：单点删除可行性扫描 + 坐标步 scp_step 位移/求解器状态 + witness 负载分布"),
    ("v9", "online", "~5s（--quick ~2s）",
     "v9 定向场景漏清归因：漏掉的频道是“从未被探测到”还是“探测到但没清掉”（含逐频道计数）"),
    ("v9b", "online", "~1s",
     "v9 定向源精定位逐轮日志：进入精定位时的可行域直径/MEC 是否在收缩、落空发生在哪一步"),
    ("list", "meta", "<1s", "打印本索引（子命令 + 中文说明 + 典型耗时）"),
)


def cmd_list(args: argparse.Namespace) -> int:
    """打印子命令索引（子命令 + 中文说明 + 典型耗时）。"""
    d = Diag(args, "list")
    d.emit("diagnose 子命令索引（快照 15 个取证脚本 → 1 个入口）")
    d.emit("")
    d.emit("类别：offline = 复用 script.q4.joint_opt（快照 _joint_opt_q4.py），不跑策略；")
    d.emit("      online = 跑策略仿真；online* = 用仿真世界生成案例但只做几何核对。")
    d.emit("耗时是实测参考（HTTP mock 每局约 0.9 s，--client direct 约 0.1 s）。")
    d.emit("")
    name_w = max(_dwidth(n) for n, _k, _t, _s in SUBCOMMANDS) + 2
    time_w = max(_dwidth(t) for _n, _k, t, _s in SUBCOMMANDS) + 2
    d.emit(f"{_dpad('子命令', name_w)}{_dpad('类别', 9)}{_dpad('典型耗时', time_w)}诊断什么")
    d.emit("-" * 108)
    for n, k, t, s in SUBCOMMANDS:
        d.emit(f"{_dpad(n, name_w)}{_dpad(k, 9)}{_dpad(t, time_w)}{s}")
    d.emit("")
    d.emit("通用选项：--seeds 1-5,25 | --limit N | --quick | --in PATH（仅 cover2/v17-miss 消费）")
    d.emit("          --out-dir DIR（默认 logs/q4/diagnose/）| --no-write | --json")
    d.emit("          --legacy-timing（旧 mock 计时口径，复现 2026-09-13 之前归档数字）")
    d.emit("          --client http|direct | --param K=V（策略参数，v9/v9b/clears/probe/reason）")
    d.emit("")
    d.emit("三条固定口径：")
    d.emit(f"  1. {SAMPLING_CAVEAT}")
    d.emit(f"  2. {FINITE_SIM_CAVEAT}")
    d.emit(f"  3. {RAY_CAVEAT}")
    d.emit("")
    d.emit(f"输出目录：{d.out_dir}（--out-dir 可改，--no-write 只打印）")

    return d.finish(index=[{"subcommand": n, "kind": k, "runtime": t, "what": s}
                           for n, k, t, s in SUBCOMMANDS]) and 0


# =========================================================================== CLI
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--version", action="version", version="script/q4/diagnose.py (Q4 诊断统一入口)")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--seeds", default=None,
                        help="种子表达式，如 1-5,25（覆盖各子命令的默认种子集）")
    common.add_argument("--limit", type=int, default=None,
                        help="最多取前 N 个种子（在线子命令；只取单个种子的子命令取第 1 个）。"
                             "离线子命令的候选数用各自选项：knife --candidates / "
                             "repair --max-candidates / joint --scp-max-cuts")
    common.add_argument("--quick", action="store_true",
                        help="廉价模式：少种子 / 少候选 / 粗网格 / 跳过 0.25° 采样扫描，"
                             "保证每个子命令远快于 60 s")
    common.add_argument("--in", dest="in_path", default=None,
                        help="布局 json（覆盖默认的 layout_io.framework_layout()；"
                             "目前由 cover2 / v17-miss 消费）")
    common.add_argument("--out-dir", default=None,
                        help=f"产物目录，默认 {DEFAULT_OUT_DIR}")
    common.add_argument("--no-write", action="store_true", help="只打印，不写任何文件")
    common.add_argument("--json", action="store_true",
                        help="结束时额外把结构化摘要打到标准输出")
    common.add_argument("--legacy-timing", action="store_true",
                        help="用旧 mock 计时（失败的 /clear 也按 5 s）复现 2026-09-13 之前的归档数字；"
                             "默认按附件1 §2.3：失败 3 s / 成功 5 s")
    common.add_argument("--client", choices=("http", "direct"), default="http",
                        help="在线子命令的客户端：http=MockSimulator+HttpSimulatorClient（默认，"
                             "本机临时 HTTP mock 服务器）；direct=World+DirectClient（不监听端口）")
    common.add_argument("--param", action="append", metavar="K=V",
                        help="策略参数覆盖（可重复），如 --param scan_probe_limit=2")

    sub = ap.add_subparsers(dest="command", metavar="{...}")

    def add(name: str, fn, help_text: str, **extra) -> None:
        p = sub.add_parser(name, parents=[common], help=help_text, description=help_text,
                           formatter_class=argparse.RawDescriptionHelpFormatter)
        for a, kw in extra.items():
            p.add_argument(*kw.pop("flags"), **kw)
        p.set_defaults(func=fn)

    add("list", cmd_list, "打印子命令索引（子命令 + 中文说明 + 典型耗时）")

    add("clears", cmd_clears,
        "v8/v10 每次 /clear 尝试归因（reason × 命中率 × 估计误差；direct 按读数条数分桶）",
        strategy={"flags": ("--strategy",), "choices": ("v8", "v10"), "default": "v8",
                  "help": "被插桩的策略（快照 sys.argv[1]，默认 v8）"})
    add("probe", cmd_probe, "逐频道检测分布：扫描检出次数 / 读数条数 / 是否清除")
    add("reason", cmd_reason, "检测预算去向：scan / refine / 其它 reason 的次数与里程分解")
    add("cover2", cmd_cover2, "seed 25 信道 2（定向源）在两个布局下的覆盖点数与读数几何")
    add("v17-seed25", cmd_v17_seed25, "seed 25 信道 2 深挖：v14 vs v17 读数/估计点/清除记录")
    add("v17-miss", cmd_v17_miss, "定位漏清源：真值位置/朝向 × 布局各点的正面最近距离")
    add("visit-check", cmd_visit_check, "核实 v14 实际访问的扫描点数与行驶分解")
    add("v14-stats", cmd_v14_stats, "v14 成本画像：里程/检测/清除按类型与 reason 分解")
    add("ab-v17", cmd_ab_v17, "布局 A/B：v14 vs v14r vs v17（同批 case 同参数比虚拟时间）",
        only={"flags": ("--only",), "default": "both",
              "help": "只跑某些臂：both / v14 / v14r / v17（可用逗号组合，快照同名参数）"})
    add("v9", cmd_v9, "v9 定向场景漏清归因（未探测到 vs 探测到没清掉）",
        )
    add("v9b", cmd_v9b, "v9 定向源精定位逐轮日志（可行域直径/MEC 收缩、落空在哪一步）",
        watch={"flags": ("--watch",), "default": "",
               "help": "只对这些频道打印逐轮日志（快照的 watch=，逗号分隔；默认空=只打印汇总）"})

    add("knife", cmd_knife, "删#J → 几何修复 → 可行性微调 → knife-edge 定向修复",
        candidates={"flags": ("--candidates",), "type": lambda s: [int(v) for v in s.split(",")],
                    "default": None,
                    "help": "删点候选索引（默认 5,6 = 快照 for J in (5,6)；--quick 只用 5）"},
        polish_rounds={"flags": ("--polish-rounds",), "type": int, "default": 14,
                       "help": "polish_feas 的 max_rounds（快照默认 14）"},
        knife_moves={"flags": ("--knife-moves",), "type": int, "default": 60,
                     "help": "knife_fix 的 max_moves（快照默认 60）"},
        cert_level={"flags": ("--cert-level",), "choices": ("full", "coarse"), "default": "full",
                    "help": "证书网格：full=(60/36,40/60,25/90)；coarse=(120/24,60/36)"})
    add("polish", cmd_polish, "删#5 几何修复后纯可行性微调能否收尾（无路程项）",
        polish_rounds={"flags": ("--polish-rounds",), "type": int, "default": None,
                       "help": "polish_feas 的 max_rounds（默认 14 = 快照；--quick 用 3）"})
    add("repair", cmd_repair, "delete_repair_step 在 25 点基线上能否完成删点",
        max_candidates={"flags": ("--max-candidates",), "type": int, "default": None,
                        "help": "每轮尝试修复的候选上限（默认不限；--quick 用 1）"},
        no_brute={"flags": ("--no-brute",), "action": "store_true",
                  "help": "跳过 0.25° 采样扫描验收（快照默认开；--quick 也自动跳过）"})
    add("joint", cmd_joint, "单点删除扫描 + 坐标步 scp_step + witness 负载分布",
        scp_max_cuts={"flags": ("--scp-max-cuts",), "type": int, "default": 22,
                      "help": "scp_step 的 max_cuts（快照默认 22；--quick 用 3）"},
        scp_step={"flags": ("--scp-step",), "type": float, "default": 50.0,
                  "help": "scp_step 的种子网格步长（快照默认 50）"},
        scp_n_phi={"flags": ("--scp-n-phi",), "type": int, "default": 60,
                   "help": "scp_step 的朝向数（快照默认 60）"})

    return ap


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        print("\n可用子命令：\n  " + " ".join(f"{n}" for n, _k, _t, _s in SUBCOMMANDS))
        print("\n完整索引：python3 script/q4/diagnose.py list")
        return 0
    rc = args.func(args)
    return int(rc or 0)


if __name__ == "__main__":
    raise SystemExit(main())
