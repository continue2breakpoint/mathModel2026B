#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""官方模拟器（jammers-simulator.exe）**演练运行器** · 问题3 / 问题4 最终策略。

为什么有这个脚本
----------------
mock 上的数字只能做**相对比较**——题面明确"以官方为准"，而官方真值（干扰源总数、
定向源个数、案例编码）只有在模拟器里跑完一局、落盘 ``*.result.json`` 之后才拿得到。
所以正式测试之前必须有一遍"用**最终交付策略**打官方接口"的完整演练：

* 接口：``http://127.0.0.1:2026``（``robot-protocol-v1``），端口号默认取环境变量
  ``JAMMERS_ROBOT_PORT``（未设置 = 2026）；
* 半自动：本脚本轮询 ``/enter``，直到你在官方 GUI 里点【问题3/4 演练测试】→【开始】
  并等完 5s 倒计时，接口一开放就自动进场；
* 跑完整局 → ``/exit`` → 读模拟器落盘的 ``*.result.json`` 取**官方真值**，
  并与"脚本自己清掉了几个源"对比；同时**校验这一局开的确实是问题3/4**
  （点错入口是演练里最常见、也最贵的一种错误）；
* 每局按 ``/enter`` 返回的 ``remaining_real_duration_s`` **减 30s 安全余量**作为策略
  墙钟预算——**不写死 1200s**：官方的现实时长上限是平台配置，硬编码会在
  上限被调小时让策略超时、被调大时白白浪费预算。

策略来源与"为什么不用 runner.run_once"
--------------------------------------
最终策略一律用 :func:`mathmodel2026b.versioned.build_method` 构造，**臂名取自注册表**
（注册表里标 ``final`` 的那一条；问题3 现为 ``q3-v18``、问题4 为 ``q4-v18``），
交付参数覆盖同样由注册表统一给出，
本文件不抄任何参数默认值；只有"墙钟预算"是按 ``/enter`` 的返回值临时注入的。

链路层沿用 ``script/run.py`` 已有的约定：队号解析走
``jammers_paths`` / ``resolve_robot_id``（不写死队号），robot 端口取
``JAMMERS_ROBOT_PORT``，HTTP 走 :class:`~mathmodel2026b.client.HttpSimulatorClient`，
动作计数用框架自带的 :class:`~mathmodel2026b.client.RecordingClient` 装饰器
（它存在的意义就是"每次调用留一条结构化记录"，不必另写包装类）。
这里**没有**直接用 ``runner.RunConfig`` / ``run_once``，原因是那一层自己发 ``/enter``
而且没有"先把 ``/enter`` 轮询到 GUI 开始、再拿 ``remaining_real_duration_s`` 标定墙钟
预算"的钩子——演练恰恰就是要在这两步之间做决策。要写标准 run 日志仍可用
``script/run.py --mode live``（它的 live 分支与本脚本打的是同一个 robot 端口）。

用法::

    # 问题3、4 各 5 局（每局都需要在 GUI 点一次开始）
    python3 script/official_drill_q34.py

    # 先跑 1 局验证链路；只演练问题4；显式指定队号
    python3 script/official_drill_q34.py --repeat 1 --problem 3
    python3 script/official_drill_q34.py --problem 4 --repeat 3 --robot-id "<队号>"

    # 队号默认从 login-jammers 配置读（同 run.py），避免在代码里写死；
    # 也可用环境变量 JAMMERS_TEAM_NO 覆盖。提交材料前请确认仓库里没有真实队号
    # （平台公告明确要求隐去）。
    JAMMERS_ROBOT_PORT=2026 python3 script/official_drill_q34.py

    # 没有官方模拟器时：用本地 mock 走**完全同一条**流程（含预算标定、计数、落盘）
    python3 script/official_drill_q34.py --dry-run --repeat 1

退出码：``0`` 至少完整跑通一局；``2`` 一局都没进场（robot 端口关闭/队号缺失），
此时会打印一份中文排查清单。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(REPO_ROOT / "framework" / "src"))

# 队号解析与 robot 端口口径直接复用 script/run.py：
# 队号 = login-jammers 的账号配置（可用 --robot-id / JAMMERS_TEAM_NO 覆盖），
# 端口 = JAMMERS_ROBOT_PORT（默认 2026）。两条约定各写一份迟早会漂移。
from run import DEFAULT_ROBOT_URL, PLACEHOLDER_TEAM_NO  # noqa: E402
from mathmodel2026b.client import (  # noqa: E402
    HttpSimulatorClient,
    RecordingClient,
    RobotPortGatedError,
)
from mathmodel2026b.mock.server import MockSimulator  # noqa: E402
from mathmodel2026b.state import DogState  # noqa: E402
from mathmodel2026b.versioned import (  # noqa: E402
    DECISION_METHODS,
    available_methods,
    build_method,
)

#: 注册表里**没有** ``final`` 标记时退回的历史交付版（正常情况下用不到：
#: 交付版唯一定义在 :mod:`mathmodel2026b.versioned`，见 :func:`final_arm`）。
FALLBACK_ARMS: dict[int, str] = {3: "q3-v15", 4: "q4-v18"}


def final_arm(problem: int) -> str:
    """本问题当前的**最终交付臂**：注册表里 ``final=True`` 的那一条。

    直接问注册表而不是写死 ``q3-v15``：交付版换代（例如问题3 由 ``q3-v15``
    改为 ``q3-v18``）时，演练脚本必须跟着换——否则"演练通过"证明的不是交付版。
    """
    for key in available_methods(problem):
        if DECISION_METHODS[key].final:
            return key
    return FALLBACK_ARMS[problem]


def final_arm_note(problem: int) -> str:
    """``问题3=q3-v18（★注册表最终交付）`` 这样的可读标签。"""
    arm = final_arm(problem)
    tag = "★注册表最终交付" if DECISION_METHODS[arm].final else "注册表无 final 标记，退回历史交付"
    return f"{arm}（{tag}）"

#: 现实时间安全余量：**不写死** 1200s 上限，而是用 ``remaining_real_duration_s - 30``。
SAFETY_MARGIN_S = 30.0
#: 预算下限（防止 remaining 很小或为 0 时策略拿不到任何预算）。
MIN_BUDGET_S = 60.0

#: 官方模拟器落盘真值的目录名（``JammersSimulatorData/behavior-logs``）。
BEHAVIOR_LOGS_DIRNAME = "behavior-logs"
#: 自动探测真值目录时最多下探的层数 / 最多访问的目录数（避免在大目录上爬很久）。
#: 官方布局是 ``<模拟器根>/JammersSimulatorData/behavior-logs``，从仓库向上二级
#: 作为根起算通常要下探 7 层，因此这里留到 8。
DISCOVER_MAX_DEPTH = 8
DISCOVER_MAX_DIRS = 6000
#: 自动探测时**不进**的目录（体积大且不可能藏着模拟器数据）。
DISCOVER_SKIP = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "framework",
    "docs", "logs", "runs", "results", "batches", "audit", "bench",
}


# --------------------------------------------------------------------------
# 真值目录
# --------------------------------------------------------------------------
def discover_data_dir(roots: list[Path]) -> Path | None:
    """在几个候选根目录下**有限深度**地找 ``behavior-logs``（找得到就用，找不到返回 None）。"""
    queue: list[tuple[Path, int]] = [(root, 0) for root in roots if root.is_dir()]
    visited = 0
    while queue and visited < DISCOVER_MAX_DIRS:
        current, depth = queue.pop(0)
        visited += 1
        if current.name == BEHAVIOR_LOGS_DIRNAME:
            return current
        if depth >= DISCOVER_MAX_DEPTH:
            continue
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir(follow_symlinks=False):
                continue
            if entry.name.startswith(".") or entry.name in DISCOVER_SKIP:
                continue
            queue.append((Path(entry.path), depth + 1))
    return None


def resolve_data_dir(explicit: str | None) -> Path | None:
    """真值目录优先级：``--data-dir`` > 环境变量 ``JAMMERS_SIM_DATA_DIR`` > 自动探测。"""
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("JAMMERS_SIM_DATA_DIR")
    if env:
        return Path(env).expanduser()
    return discover_data_dir([Path.cwd(), REPO_ROOT, REPO_ROOT.parent])


def read_official_truth(data_dir: Path | None, since_ts: float) -> tuple[Path, dict] | None:
    """读本局结束后模拟器落盘的 ``*.result.json``（取 ``since_ts`` 之后最新的一份）。

    ``since_ts`` 过滤是必要的：``behavior-logs`` 里留着一堆历史对局，不过滤就会把
    上一局（甚至几天前那一局）的源数当成这一局的真值。
    """
    if data_dir is None or not data_dir.is_dir():
        return None
    best: tuple[Path, dict] | None = None
    best_mtime = -1.0
    for path in sorted(data_dir.glob("*.result.json")):
        try:
            mtime = path.stat().st_mtime
            if mtime < since_ts - 5.0:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if mtime > best_mtime:
            best, best_mtime = (path, data), mtime
    return best


def problem_from_filename(path: Path) -> int | None:
    """从 ``practice-p3-....result.json`` / ``formal-p4-...`` 里解出问题号（交叉校验用）。"""
    stem = path.name
    for marker in ("-p3-", "-p4-"):
        if marker in stem:
            return int(marker[-2])
    return None


def truth_problem(data: dict, path: Path) -> tuple[int | None, str | None]:
    """返回 ``(问题号, 不一致说明)``；问题号优先取 JSON 字段，其次文件名。"""
    declared = data.get("problem_no")
    declared_int = int(declared) if isinstance(declared, (int, float)) else None
    from_name = problem_from_filename(path)
    if declared_int is not None and from_name is not None and declared_int != from_name:
        return declared_int, (
            f"result.json 的 problem_no={declared_int} 与文件名暗示的问题{from_name} 不一致"
        )
    return (declared_int if declared_int is not None else from_name), None


# --------------------------------------------------------------------------
# 动作计数（复用框架的 RecordingClient 装饰器，而不是另写一个包装类）
# --------------------------------------------------------------------------
class ActionTally:
    """把 ``RecordingClient`` 的每条记录累加成本局的动作计数。"""

    def __init__(self) -> None:
        self.measure = 0
        self.clear = 0
        self.clear_ok = 0

    def __call__(self, record: dict[str, Any]) -> None:
        action = record.get("action")
        if action == "measure":
            self.measure += 1
        elif action == "clear":
            self.clear += 1
            if record.get("outcome") == "success":
                self.clear_ok += 1


# --------------------------------------------------------------------------
# 等待进场
# --------------------------------------------------------------------------
def port_closed_help(base_url: str, *, waited_s: float) -> str:
    """端口关闭时的**可执行**排查清单（演练现场最需要的是"下一步该做什么"）。"""
    port = base_url.rsplit(":", 1)[-1]
    env_port = os.environ.get("JAMMERS_ROBOT_PORT")
    return "\n".join(
        [
            f"[致命] 无法连上官方模拟器的 robot 接口 {base_url}（已等 {waited_s:.0f}s）。",
            "  这个端口只在**测试进行中**开放（官方 portguard 门控），所以『连接被拒绝』",
            "  通常不是网络问题。请按顺序排查：",
            "    1) 官方模拟器 jammers-simulator.exe 是否已启动？（没启动时端口一定是关的）",
            "    2) 是否已在 GUI 里点【问题3/4 演练测试】→【开始】，并等完倒计时？",
            "    3) 端口号是否一致？官方默认 2026；用 --robot-url 或环境变量指定。"
            f"当前端口 {port}"
            + (f"（来自 JAMMERS_ROBOT_PORT={env_port}）" if env_port else "（默认值）"),
            "    4) 队号是否与登录账号一致？robot_id 必须等于登录队号"
            "（--robot-id / JAMMERS_TEAM_NO / login-jammers 的 config.json）。",
            "    5) 想「先启动脚本、再在 GUI 点开始」：这是**默认行为**，会一直轮询",
            "       （--wait-s，默认 900s）；只想探一次就报诊断：--wait-s 0。",
            "    6) 先做线上链路预检：python3 script/preflight.py",
            "    7) 没有官方模拟器、只想验证脚本链路：加 --dry-run（用本地 mock 走同一条流程）。",
        ]
    )


def wait_for_session(
    client: HttpSimulatorClient,
    problem: int,
    wait_s: float,
) -> tuple[bool, float, str | None]:
    """轮询 ``/enter`` 直到接口开放；返回 ``(是否进场, 现实剩余秒数, 最后一条错误)``。

    未开始测试时端口是关闭的（``RobotPortGatedError``），属于**正常现象**而不是故障，
    因此第一次失败就把提示打出来，并且只在第一次打（避免刷屏）。
    ``wait_s == 0`` 表示"只探一次、立刻给出诊断"，仍然会真的发一次 ``/enter``。
    """
    started = time.time()
    deadline = started + max(0.0, wait_s)
    told_gated = False
    told_exc = False
    last_error: str | None = None
    attempts = 0
    while True:
        if attempts > 0 and time.time() >= deadline:
            return False, 0.0, last_error
        attempts += 1
        try:
            entered = client.enter()
        except RobotPortGatedError as exc:
            last_error = str(exc)
            if not told_gated:
                if wait_s > 0:
                    print(
                        f"    尚未进场：请在模拟器点击【问题{problem} 演练测试】→【开始】，"
                        f"倒计时结束后本脚本会自动进场（最长等 {wait_s:.0f}s）。",
                        flush=True,
                    )
                    print(
                        "    （没启动模拟器/没点开始的时候，robot 端口就是关闭的，属正常；"
                        "只想立刻看诊断：--wait-s 0）",
                        flush=True,
                    )
                else:
                    print(
                        f"    只探一次（--wait-s 0）：问题{problem} 未进场，"
                        "下面给出诊断与排查清单。",
                        flush=True,
                    )
                told_gated = True
            time.sleep(3.0)
            continue
        except Exception as exc:  # noqa: BLE001 - 门控关闭时常见 WinError 10053，属正常
            last_error = f"{type(exc).__name__}: {exc}"
            if not told_exc:
                print(
                    f"    /enter 暂不可用（{last_error}）——未开始测试时属正常，继续等待…",
                    flush=True,
                )
                told_exc = True
            time.sleep(3.0)
            continue
        if entered.accepted:
            return True, float(entered.remaining_real_duration_s), None
        print("    /enter accepted=false（可能已有机器狗在场），3s 后重试", flush=True)
        time.sleep(3.0)


# --------------------------------------------------------------------------
# 一局
# --------------------------------------------------------------------------
def build_final_strategy(problem: int, budget_s: float):
    """构造最终交付策略，并把本局的墙钟预算注入参数（其余参数全部来自注册表）。"""
    arm = final_arm(problem)
    if arm not in DECISION_METHODS:
        raise SystemExit(f"未知臂 {arm!r}；可用：{', '.join(available_methods())}")
    return arm, build_method(arm, max_wall_time_s=budget_s)


def play_round(
    client: HttpSimulatorClient,
    problem: int,
    budget_s: float,
) -> tuple[ActionTally, DogState, Any, float, str | None]:
    """执行一局（``/enter`` 已在 :func:`wait_for_session` 里完成）。"""
    tally = ActionTally()
    counted = RecordingClient(client, hook=tally)
    state = DogState()
    _arm, strategy = build_final_strategy(problem, budget_s)
    t0 = time.perf_counter()
    error: str | None = None
    try:
        strategy.run(counted, state)
    except Exception as exc:  # noqa: BLE001 - 线上演练必须留下记录，不能静默中断
        error = f"{type(exc).__name__}: {exc}"
    wall_s = time.perf_counter() - t0
    try:
        client.exit()
    except Exception as exc:  # noqa: BLE001
        error = error or f"exit 失败：{type(exc).__name__}: {exc}"
    return tally, state, strategy, wall_s, error


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    port = os.environ.get("JAMMERS_ROBOT_PORT", "2026")
    ap = argparse.ArgumentParser(
        description=(
            "官方模拟器演练运行器（问题3/4 最终交付策略）：轮询 /enter 等 GUI 开始、"
            "跑完整局、读 *.result.json 官方真值并校验问题号"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--problem", default="3,4", help="3 / 4 / 3,4（默认 3,4）")
    ap.add_argument("--repeat", type=int, default=5, help="每问局数（默认 5）")
    ap.add_argument(
        "--robot-url", "--base-url", dest="robot_url", default=DEFAULT_ROBOT_URL,
        help=(
            f"官方 robot 接口地址（默认 {DEFAULT_ROBOT_URL}，即环境变量 "
            f"JAMMERS_ROBOT_PORT={port}；未设置时为 2026）"
        ),
    )
    ap.add_argument(
        "--robot-id", default=None,
        help="登录队号（robot_id 必须等于登录队号）；默认读 login-jammers 配置 / JAMMERS_TEAM_NO",
    )
    ap.add_argument("--login-jammers", default=None, help="login-jammers 仓库根目录")
    ap.add_argument(
        "--data-dir", default=None,
        help=(
            "官方真值目录 JammersSimulatorData/behavior-logs；"
            "默认取环境变量 JAMMERS_SIM_DATA_DIR，未设置则在当前目录/仓库附近自动探测"
        ),
    )
    ap.add_argument(
        "--wait-s", type=float, default=900.0,
        help="每局等待「在 GUI 里点开始」的超时秒数（默认 900；0 = 只探一次，立刻给出诊断）",
    )
    ap.add_argument("--out", default="官方演练_问题34_结果.txt", help="演练记录输出文件")
    ap.add_argument(
        "--dry-run", action="store_true",
        help=(
            "不用官方模拟器：在进程内起 mock 走**完全同一条**流程"
            "（轮询进场 → 预算标定 → 计数 → 结果落盘），用于验证脚本链路"
        ),
    )
    ap.add_argument(
        "--case-seed", type=int, default=1, help="--dry-run 下 mock 案例的种子（默认 1）",
    )
    ap.add_argument(
        "--legacy-timing", action="store_true",
        help=(
            "旧 mock 计时（失败的 /clear 也按 5s 计）。"
            "**只对 --dry-run 生效**：官方模拟器的计时由官方实现决定，脚本无从干预；"
            "加它只是为了在 dry-run 里复现 2026-09-13 之前归档的数字"
        ),
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.repeat < 1:
        raise SystemExit("--repeat 至少为 1")
    problems = [int(x) for x in str(args.problem).replace("，", ",").split(",") if x.strip()]
    if not problems or any(p not in (3, 4) for p in problems):
        raise SystemExit(f"--problem 只支持 3 / 4 / 3,4；收到 {args.problem!r}")

    # 队号：与 run.py 同一口径（命令行 > 环境变量/配置 > 占位队号）
    from run import resolve_robot_id  # noqa: PLC0415  # 放到这里避免与上面的 import 混排

    robot_id = resolve_robot_id(args.robot_id, args.login_jammers)
    if not args.dry_run and robot_id == PLACEHOLDER_TEAM_NO:
        print(
            "[致命] 没有可用的队号：连官方模拟器时 robot_id 必须等于**登录队号**。\n"
            "  · --robot-id <队号>\n"
            "  · 或设置 JAMMERS_TEAM_NO\n"
            "  · 或在 login-jammers/linux-client/config.json 里填 team_no\n"
            "（只想验证脚本链路可以用 --dry-run，此时队号无关紧要）",
            file=sys.stderr,
        )
        return 2

    # dry-run 不需要官方真值目录，也不必为此在大目录上爬一遍
    data_dir = None if args.dry_run else resolve_data_dir(args.data_dir)
    lines: list[str] = []

    def emit(text: str = "") -> None:
        lines.append(text)
        print(text, flush=True)

    emit("=" * 78)
    emit(" 官方模拟器演练运行 · 问题 3 / 问题 4 最终交付策略")
    emit(f" 时间: {datetime.now():%Y-%m-%d %H:%M:%S}")
    emit(f" 队号: {robot_id}   接口: {args.robot_url}   每问局数: {args.repeat}")
    emit(f" 交付臂: 问题3={final_arm_note(3)}  问题4={final_arm_note(4)}")
    if args.dry_run:
        emit(
            f" 模式: --dry-run（本地 mock，seed={args.case_seed}，"
            f"legacy_timing={args.legacy_timing}）——真值来自 mock 案例，不是官方 result.json"
        )
    else:
        emit(" 模式: 官方演练测试（不限次数，不消耗正式机会）")
        emit(
            f" 真值目录: {data_dir if data_dir else '未找到（本局无法给出官方源数，可用 --data-dir 指定）'}"
        )
    emit(f" 现实时间预算: /enter 返回的 remaining_real_duration_s − {SAFETY_MARGIN_S:.0f}s")
    emit("=" * 78)

    rows: dict[int, list[dict[str, Any]]] = {p: [] for p in problems}
    entered_any = False
    timed_out = False

    for problem in problems:
        emit("")
        emit(f"########## 问题 {problem} ##########")
        emit(f" 交付臂: {final_arm_note(problem)}")
        for index in range(1, args.repeat + 1):
            emit("")
            emit(f"--- 问题{problem} 第 {index}/{args.repeat} 局 ---")
            sim: MockSimulator | None = None
            try:
                if args.dry_run:
                    sim = MockSimulator(
                        robot_id=robot_id,
                        seed=args.case_seed,
                        omni_only=(problem == 3),
                        legacy_clear_timing=args.legacy_timing,
                    ).start()
                    base_url = sim.base_url
                else:
                    base_url = args.robot_url
                client = HttpSimulatorClient(robot_id=robot_id, base_url=base_url, verbose=False)
                session_t0 = time.time()
                entered, remaining, last_error = wait_for_session(client, problem, args.wait_s)
                if not entered:
                    timed_out = True
                    if args.wait_s <= 0:
                        emit(port_closed_help(base_url, waited_s=0.0))
                    else:
                        emit(f"    等待超时（{args.wait_s:.0f}s）未进入测试，跳过本局。")
                        emit(port_closed_help(base_url, waited_s=args.wait_s))
                    if last_error:
                        emit(f"    最后一次 /enter 失败原因：{last_error.splitlines()[0]}")
                    break

                entered_any = True
                budget = max(MIN_BUDGET_S, remaining - SAFETY_MARGIN_S)
                emit(
                    f"    已进场：现实剩余 {remaining:.0f}s → 策略墙钟预算 {budget:.0f}s"
                    f"（余量 {SAFETY_MARGIN_S:.0f}s）"
                )
                tally, state, strategy, wall_s, error = play_round(client, problem, budget)

                cleared = len(state.cleared_channels)
                vt = float(getattr(state, "virtual_time_s", 0.0) or 0.0)
                avg = (vt / cleared) if cleared else None
                time.sleep(1.0)  # 等模拟器把 result.json 落盘

                truth_path: Path | None = None
                truth: dict[str, Any] | None = None
                truth_source = "official"
                if args.dry_run and sim is not None:
                    summary = sim.world.summary()
                    truth = {
                        "problem_no": problem,
                        "case_code": f"mock-seed{args.case_seed}",
                        "jammer_count": summary["n_jammers"],
                        "omnidirectional_jammer_count": (
                            summary["n_jammers"] if problem == 3 else None
                        ),
                        "directional_jammer_count": None,
                    }
                    truth_source = "mock"
                else:
                    found = read_official_truth(data_dir, session_t0)
                    if found is not None:
                        truth_path, truth = found

                declared_problem: int | None = None
                mismatch_note: str | None = None
                mismatch = False
                if truth is not None and truth_source == "official" and truth_path is not None:
                    declared_problem, mismatch_note = truth_problem(truth, truth_path)
                    mismatch = declared_problem is not None and declared_problem != problem
                    if mismatch:
                        emit(
                            f"    ⚠ 本局模拟器里开的是【问题{declared_problem}】测试，"
                            f"但脚本按问题{problem} 的策略执行（下一局请点对入口）"
                        )
                    elif mismatch_note:
                        emit(f"    ⚠ 真值文件自相矛盾：{mismatch_note}")

                total = None
                if truth is not None and not mismatch:
                    total = truth.get("jammer_count")
                    if not isinstance(total, (int, float)):
                        total = None
                n_dir = None if truth is None or mismatch else truth.get("directional_jammer_count")
                case_code = None if truth is None else truth.get("case_code")

                row = {
                    "problem": problem,
                    "run": index,
                    "arm": final_arm(problem),
                    "cleared": cleared,
                    "total": total,
                    "vt": vt,
                    "avg": avg,
                    "wall": wall_s,
                    "meas": tally.measure,
                    "clear_calls": tally.clear,
                    "clear_ok": tally.clear_ok,
                    "case_code": case_code,
                    "directional": n_dir,
                    "error": error,
                    "mismatch": mismatch,
                    "mismatch_note": mismatch_note,
                    "budget_s": budget,
                    "truth_source": truth_source,
                    "truth_file": str(truth_path) if truth_path else None,
                    "trace_len": len(getattr(strategy, "trace", []) or []),
                }
                rows[problem].append(row)

                if total is None:
                    verdict = "?（拿不到官方源数）"
                elif cleared >= total:
                    verdict = "✓ 全清"
                else:
                    verdict = f"✗ 漏 {total - cleared}"
                emit(
                    f"    结果: 清除 {cleared}/{total if total is not None else '?'} {verdict}"
                    f"  |  虚拟时间 {vt:.0f}s"
                    + (f"  平均源耗时 {avg:.1f}s" if avg is not None else "")
                    + f"  |  检测 {tally.measure} 次  清除 {tally.clear} 次"
                    f"（命中 {tally.clear_ok}）  |  墙钟 {wall_s:.1f}s"
                )
                if case_code:
                    emit(
                        f"    案例编码: {case_code}"
                        + (f"（定向源 {n_dir} 个）" if n_dir is not None else "")
                    )
                if truth_source == "mock":
                    emit("    （--dry-run：真值取自本地 mock 案例，非官方 result.json）")
                elif truth_path is not None:
                    emit(f"    官方真值: {truth_path.name}")
                if error:
                    emit(f"    !! 异常: {error}")
            finally:
                if sim is not None:
                    try:
                        sim.stop()
                    except Exception:  # noqa: BLE001
                        pass

        # 本问小结
        valid = [r for r in rows[problem] if r["error"] is None]
        if valid:
            known = [r for r in valid if r["total"] is not None]
            full = sum(1 for r in known if r["cleared"] >= r["total"])
            vts = [r["vt"] for r in valid]
            avgs = [r["avg"] for r in valid if r["avg"] is not None]
            emit("")
            emit(
                f"  问题{problem} 小结: 全清 {full}/{len(valid)} 局"
                f"  |  清除 {sum(r['cleared'] for r in valid)}"
                f"/{sum(r['total'] for r in known) if known else '?'} 源"
                f"  |  虚拟时间中位 {statistics.median(vts):.0f}s"
                + (f"  |  平均源耗时中位 {statistics.median(avgs):.1f}s" if avgs else "")
                + f"  |  墙钟均值 {statistics.mean([r['wall'] for r in valid]):.1f}s"
            )

    emit("")
    emit("=" * 78)
    emit(" 总览")
    emit("=" * 78)
    for problem in problems:
        valid = [r for r in rows[problem] if r["error"] is None]
        if not valid:
            emit(f" 问题{problem}: 无有效数据")
            continue
        known = [r for r in valid if r["total"] is not None]
        full = sum(1 for r in known if r["cleared"] >= r["total"])
        avgs = [r["avg"] for r in valid if r["avg"] is not None]
        emit(
            f" 问题{problem} [{final_arm(problem)}]: {full}/{len(valid)} 局全清"
            f"  |  清除 {sum(r['cleared'] for r in valid)}"
            f"/{sum(r['total'] for r in known) if known else '?'} 源"
            + (f"  |  平均源耗时中位 {statistics.median(avgs):.1f}s" if avgs else "")
        )
    emit("")
    emit(" 提醒: 正式测试每问仅 3 次机会（共 6 次），演练不限次；")
    emit("       正式测试结束后请导出加密行为日志（勿改名）作为支撑材料。")
    emit("")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"（明细已写入 {out_path}）", flush=True)

    if timed_out and not entered_any:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
