#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第 3、4 问 · 最终模拟测试（**验收脚本**）。

为什么有这个脚本
----------------
``script/bench_q34.py`` 是"多维对照"（历代版本、独立路线、压力场景），
而验收要看的是另一件事：**最终交付策略在标准案例上跑一批种子，逐局明细 +
小结 + 与大规模基准的对照**，作为交付记录留存。这是原 Windows 工程里
``最终验收_问题34.py`` 的职责，本文件是它在框架侧的移植版。

与原版的差异（都是为了让"跑出来的数字"能追溯到框架当前状态）
------------------------------------------------------------
1. **策略不再手写构造**：一律 ``build_method(key)`` 从
   :mod:`mathmodel2026b.versioned` 取，交付参数覆盖（``schedule_min_readings=1``、
   ``or_opt_passes=4``、``scan_sides=6`` / ``scan_radius=1130`` /
   ``scan_probe_limit=3`` …）由注册表统一给出，本文件不抄任何默认值。
   ``versioned.Q3_SCAN_PARAMS`` 就是原来那组写死在 ``FINAL_PARAMS`` 里的定稿扫描参数，
   现已并入交付覆盖（归档口径下它在 seed 1–30 上给出 264.81，与最终工程 README
   逐位一致）。要跑别的配置用 ``--param`` 显式覆盖。
2. **臂按注册表核对**：问题3 = ``q3-v18``、问题4 = ``q4-v18``、对照臂 = ``q4-v17``
   （2026-09-13 起 ``q3-v18`` 取代 ``q3-v15``、``q4-v18`` 取代 ``q4-v17`` 成为交付版，
   被取代者降为冻结对照臂）。这三项是**默认值**，可在命令行用 ``--q3-arm`` /
   ``--q4-arm`` / ``--baseline-arm`` 换。注册表里的"当前最终交付"如果又换了一代，
   脚本会在对应段落打印一条 ``⚠`` 漂移提示并告诉你用哪个开关切换，
   同时每段的方括号里显示该臂**在注册表里的真实状态**（★最终交付／◆阶段有益／…）。
3. **计时口径可选**：``--legacy-timing`` 用旧 mock 计时（失败的 ``/clear``
   也按 5s 计）复现归档数字；默认是附件1 §2.3 的正确口径（未发现 3s、已清除 5s）。

用法::

    # 默认种子 1-5，问题3 + 问题4 + 问题4 对照臂
    python3 script/acceptance_q34.py

    # 换一批种子 / 不跑对照臂 / 指定输出文件
    python3 script/acceptance_q34.py --seeds 6-10
    python3 script/acceptance_q34.py --seeds 1-5 --no-baseline
    python3 script/acceptance_q34.py --out 验收_20260913.txt

    # 复现 2026-09-13 之前归档的验收数字（旧 /clear 计时）
    python3 script/acceptance_q34.py --seeds 1-5 --legacy-timing

结果文本写到 ``--out``（默认当前目录下的 ``最终验收_问题34_结果.txt``）并同时打印。
需要**完整参数 dump / 逐例 JSON** 时看 ``script/bench_q34.py`` 的
``logs/bench/*.json``（里面有 ``diagnostics.params.as_dict()``）。
"""

from __future__ import annotations

import argparse
import platform
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

from run import coerce  # noqa: E402  # --param 的取值口径与 run.py 一致
from mathmodel2026b.client import HttpSimulatorClient  # noqa: E402
from mathmodel2026b.mock.server import MockSimulator  # noqa: E402
from mathmodel2026b.mock.world import generate_case  # noqa: E402
from mathmodel2026b.state import DogState  # noqa: E402
from mathmodel2026b.versioned import (  # noqa: E402
    DECISION_METHODS,
    available_methods,
    build_method,
)

#: 离线 mock 用的占位队号（不要写死真实队号）。
ROBOT = "000000000000"

#: 验收臂（注册表键）。这是**默认值**，不是"交付版"的第二份声明：
#: 交付版唯一定义在 :mod:`mathmodel2026b.versioned`（``final=True`` 的那一条），
#: 脚本只负责在两者不一致时把漂移喊出来（见 :func:`drift_note`）。
#: 默认臂 = 注册表里每个问题的 ``final=True`` 条目（2026-09-13 起为 ``q3-v18`` /
#: ``q4-v18``）。这里写死是为了让"验收跑的是哪一版"有据可查；注册表若再次换代，
#: 脚本启动时会打印 ⚠️ 漂移提示并给出正确的开关。
DEFAULT_ARMS: dict[str, str] = {
    "q3": "q3-v18",        # 问题3 最终交付
    "q4": "q4-v18",        # 问题4 最终交付
    "baseline": "q4-v17",  # 问题4 冻结对照臂（v18 的直接父类）
}

#: 默认输出文件名（写在工作目录下；--out 可覆盖）。
DEFAULT_OUT = "最终验收_问题34_结果.txt"

#: 本题的**唯一计分口径**：平均源耗时 = 虚拟总时间 / 清除源数。
METRIC_NOTE = "avg = average_clear_time_s = 虚拟总时间 / 已清除源数（题面口径）"

#: 大规模基准参照。**注意口径**：带"旧口径"的条目是 ``/clear`` 计时修正前
#: 归档的数字（失败清除按 5s），用 ``--legacy-timing`` 才能逐位复现；
#: 不带该标注的条目与计时无关（全清率、局数）。
REFERENCE: dict[str, str] = {
    "q3-v18": (
        "30 种子（修正口径）：30/30 全清、**平均源耗时中位 256.11 s/源**；"
        "1–60 → 275.54、61–160 → 272.08，三组均全清"
    ),
    "q3-v15": (
        "交付配置（定稿布局 6/1130/3 + or-opt=4）30 种子：30/30 全清、256.68 s/源；"
        "归档口径（定稿布局 + or-opt 关）= **264.81**，与最终工程 README 逐位一致。"
        "上一版注册表的旧布局 7/1110/2 给出的是 273.73 —— 两者不是同一配置"
    ),
    "q4-v18": (
        "持有集 61–160（混合 + 全定向压力场景）与 161–260：100/100 局、源全清，"
        "代价约 +0.4~2.6 s/局（见 versioned 注册表的 effect 栏）"
    ),
    "q4-v17": (
        "调参种子 1–60：760/760 全清、vt 中位 6242.8 s；但**未参与调参的持有集** "
        "61–160 只有 97/100 —— 这正是它被 v18 取代、降为冻结对照臂的原因"
    ),
}


# --------------------------------------------------------------------------
# 臂与单局执行
# --------------------------------------------------------------------------
def parse_seeds(spec: str) -> list[int]:
    """``"1-5,11"`` -> ``[1..5, 11]``（去重升序）。"""
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


def parse_params(items: list[str]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--param 需要 KEY=VALUE 形式：{item!r}")
        key, raw = item.split("=", 1)
        params[key.strip()] = coerce(raw)
    return params


def delivery_note(key: str, overrides: dict[str, Any]) -> str:
    """把"这一代交付了什么参数"讲清楚（不复制默认值，只展示注册表覆盖）。"""
    if key not in DECISION_METHODS:
        raise SystemExit(f"未知臂 {key!r}；可用：{', '.join(available_methods())}")
    spec = DECISION_METHODS[key]
    items = dict(spec.overrides)
    items.update(overrides)
    rendered = ", ".join(f"{k}={v!r}" for k, v in sorted(items.items())) or "（类默认值）"
    return f"注册表覆盖: {rendered}"


def status_note(key: str) -> str:
    """臂在当前注册表里的状态（★最终交付 / ◆阶段有益 / ✗负结果 …），与 list_methods 同源。"""
    from mathmodel2026b.versioned import method_tag  # noqa: PLC0415

    return method_tag(DECISION_METHODS[key])


def registry_final(problem: int) -> str | None:
    """注册表里当前标为 ``final`` 的臂（每个问题至多一个）。"""
    for candidate in available_methods(problem):
        if DECISION_METHODS[candidate].final:
            return candidate
    return None


def drift_note(key: str, problem: int, option: str, *, is_baseline: bool = False) -> str | None:
    """默认臂与注册表当前交付版不一致时，明确说出来（而不是静默跑一个过时的臂）。

    ``is_baseline=True`` 时**不报警**：对照臂本来就不该等于当前交付版，
    对它喊"漂移"只会制造噪声。它的角色写在段落标题的 ``（对照臂）`` 里。
    """
    if is_baseline:
        return None
    final = registry_final(problem)
    if final and final != key:
        return (
            f"⚠ 注册表当前把 {final} 标为问题{problem} 的最终交付，"
            f"本脚本这一项仍是 {key}（历史交付/对照口径）；换它请加 {option} {final}"
        )
    return None


def run_one(
    seed: int,
    key: str,
    overrides: dict[str, Any],
    *,
    directional: bool,
    legacy_timing: bool,
) -> dict[str, Any]:
    """跑一局：``enter → run → exit``，指标取 mock ``World`` 的权威统计。"""
    case = generate_case(seed, omni_only=not directional)
    with MockSimulator(
        robot_id=ROBOT,
        case=case,
        legacy_clear_timing=legacy_timing,
    ) as sim:
        client = HttpSimulatorClient(robot_id=ROBOT, base_url=sim.base_url, verbose=False)
        state = DogState()
        strategy = build_method(key, **overrides)
        t0 = time.perf_counter()
        error: str | None = None
        try:
            client.enter()
            strategy.run(client, state)
        except Exception as exc:  # noqa: BLE001 - 单局失败要留在验收记录里，而不是打断整批
            error = f"{type(exc).__name__}: {exc}"
        try:
            client.exit()
        except Exception as exc:  # noqa: BLE001
            error = error or f"exit 失败：{type(exc).__name__}: {exc}"
        wall = time.perf_counter() - t0
        summary = sim.world.summary()
    return {
        "seed": seed,
        "arm": key,
        "error": error,
        "cleared": summary["cleared_count"],
        "n": summary["n_jammers"],
        "vt": summary["virtual_time_s"],
        "avg": summary["average_clear_time_s"],
        "dist": summary["move_distance_m"],
        "meas": summary["measure_count"],
        "clear_calls": summary["clear_count"],
        "wall": wall,
        "uncleared": summary["uncleared_channels"],
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if r["error"] is None]
    avgs = [r["avg"] for r in ok if r["avg"]]
    return {
        "n_runs": len(rows),
        "n_ok": len(ok),
        "n_full": sum(1 for r in ok if r["cleared"] == r["n"]),
        "cleared": sum(r["cleared"] for r in ok),
        "total": sum(r["n"] for r in ok),
        "vt_med": statistics.median([r["vt"] for r in ok]) if ok else None,
        "avg_med": statistics.median(avgs) if avgs else None,
        "avg_mean": statistics.mean(avgs) if avgs else None,
        "dist_med": statistics.median([r["dist"] for r in ok]) if ok else None,
        "meas_med": statistics.median([r["meas"] for r in ok]) if ok else None,
        "wall_mean": statistics.mean([r["wall"] for r in ok]) if ok else None,
    }


# --------------------------------------------------------------------------
# 输出块
# --------------------------------------------------------------------------
def detail_lines(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        f"{'#':>2}  {'seed':>4}  {'清除':>7}  {'vt(s)':>8}  {'平均源耗时':>10}  "
        f"{'行驶(m)':>8}  {'检测':>5}  {'清除调用':>8}  {'墙钟(s)':>7}"
    ]
    for index, row in enumerate(rows, 1):
        avg = row["avg"] if row["avg"] is not None else 0.0
        lines.append(
            f"{index:>2}  {row['seed']:>4}  {row['cleared']:>3}/{row['n']:<3}  "
            f"{row['vt']:>8.0f}  {avg:>10.1f}  {row['dist']:>8.0f}  "
            f"{row['meas']:>5}  {row['clear_calls']:>8}  {row['wall']:>7.2f}"
            + (f"   ERR={row['error']}" if row["error"] else "")
        )
    return lines


def summary_line(stats: dict[str, Any]) -> str:
    def num(value: Any, digits: int) -> str:
        return "-" if value is None else f"{value:.{digits}f}"

    return (
        f"  小结: 全清 {stats['n_full']}/{stats['n_runs']}  |  "
        f"源清除 {stats['cleared']}/{stats['total']}  |  "
        f"vt 中位 {num(stats['vt_med'], 0)} s  |  "
        f"平均源耗时中位 {num(stats['avg_med'], 2)} s/源  |  "
        f"行驶中位 {num(stats['dist_med'], 0)} m  |  "
        f"墙钟均值 {num(stats['wall_mean'], 2)} s"
    )


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="第 3、4 问最终模拟测试（验收）：最终交付策略跑 N 个种子并留档",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--seeds", default="1-5", help="种子，如 1-5 或 1,3,7（默认 1-5）")
    ap.add_argument(
        "--no-baseline", action="store_true",
        help=f"不跑问题4 的对照臂（默认跑 {DEFAULT_ARMS['baseline']}）",
    )
    ap.add_argument(
        "--out", default=DEFAULT_OUT,
        help=f"结果文本文件（默认工作目录下的 {DEFAULT_OUT}）",
    )
    ap.add_argument(
        "--legacy-timing", action="store_true",
        help=(
            "用旧 mock 计时：**失败的 /clear 也按 5s 计**，仅用于复现 2026-09-13 "
            "之前归档的验收/基准数字；默认是附件1 §2.3 的正确口径（未发现 3s、已清除 5s）"
        ),
    )
    ap.add_argument(
        "--q3-arm", default=DEFAULT_ARMS["q3"],
        help=f"问题3 的臂（注册表键，默认 {DEFAULT_ARMS['q3']}）",
    )
    ap.add_argument(
        "--q4-arm", default=DEFAULT_ARMS["q4"],
        help=f"问题4 的臂（注册表键，默认 {DEFAULT_ARMS['q4']}）",
    )
    ap.add_argument(
        "--baseline-arm", default=DEFAULT_ARMS["baseline"],
        help=f"问题4 对照臂（注册表键，默认 {DEFAULT_ARMS['baseline']}）",
    )
    ap.add_argument(
        "--param", action="append", default=[], metavar="KEY=VALUE",
        help="追加/覆盖策略参数（对所有臂生效；用于复现历史「定稿参数」，例如 --param scan_sides=6）",
    )
    ap.add_argument(
        "--fail-on-uncleared", action="store_true",
        help="出现未全清/异常时返回非零（默认 0，结论写在文本里）",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    seeds = parse_seeds(args.seeds)
    if not seeds:
        raise SystemExit("--seeds 解析为空")
    overrides = parse_params(args.param)

    blocks: list[tuple[str, str, list[dict[str, Any]], dict[str, Any]]] = []
    lines: list[str] = []

    def emit(text: str = "") -> None:
        lines.append(text)
        print(text, flush=True)

    emit("=" * 74)
    emit(" 第 3、4 问 · 最终模拟测试（验收）")
    emit(f" 时间: {datetime.now():%Y-%m-%d %H:%M:%S}")
    emit(
        f" 环境: Python {platform.python_version()} | 本地 mock 模拟器（确定性种子） | "
        f"{METRIC_NOTE}"
    )
    emit(
        " 计时口径: "
        + (
            "旧口径（失败 /clear 也按 5s，用于复现归档数字）"
            if args.legacy_timing
            else "正确口径（未发现 /clear 3s、已清除 5s，附件1 §2.3）"
        )
    )
    emit(f" 种子: {seeds}（每问 {len(seeds)} 次）")
    emit(f" 本次臂: 问题3={args.q3_arm}  问题4={args.q4_arm}  "
         f"对照={'(已关闭)' if args.no_baseline else args.baseline_arm}")
    emit("=" * 74)

    # (段落标题, 臂, 场景说明, 是否定向, 该臂对应的命令行开关)
    plan: list[tuple[str, str, str, bool, str]] = [
        ("问题3", args.q3_arm, "全向源", False, "--q3-arm"),
        ("问题4", args.q4_arm, "全向+定向混合", True, "--q4-arm"),
    ]
    if not args.no_baseline:
        plan.append(
            ("问题4", args.baseline_arm, "全向+定向混合（对照臂）", True, "--baseline-arm")
        )
    for _label, key, _scenario, _directional, _option in plan:
        if key not in DECISION_METHODS:
            raise SystemExit(f"未知臂 {key!r}；可用：{', '.join(available_methods())}")

    for problem, key, scenario, directional, option in plan:
        spec = DECISION_METHODS[key]
        emit("")
        emit(f"【{problem} · {scenario}】{key}  [{status_note(key)}]")
        emit(f"  {delivery_note(key, overrides)}")
        emit(f"  基准参照: {REFERENCE.get(key, '（无归档参照）')}")
        emit(f"  臂说明: {spec.adds}")
        note = drift_note(
            key, 3 if problem == "问题3" else 4, option,
            is_baseline=(key == args.baseline_arm and problem == "问题4"),
        )
        if note:
            emit(f"  {note}")
        rows: list[dict[str, Any]] = []
        for seed in seeds:
            row = run_one(
                seed, key, overrides,
                directional=directional, legacy_timing=args.legacy_timing,
            )
            rows.append(row)
        for line in detail_lines(rows):
            emit(line)
        stats = summarize(rows)
        emit(summary_line(stats))
        bad = [r for r in rows if r["cleared"] < r["n"] or r["error"]]
        if bad:
            emit(
                "  未全清: "
                + ", ".join(f"seed{r['seed']}({r['cleared']}/{r['n']})" for r in bad)
            )
        blocks.append((problem, key, rows, stats))

    # ---------------- 结论 ----------------
    emit("")
    emit("=" * 74)
    emit(" 结论")
    emit("=" * 74)
    all_full = True
    for problem, key, _rows, stats in blocks:
        flag = "✓ 全部全清" if stats["n_full"] == stats["n_runs"] else "✗ 存在未全清"
        all_full = all_full and stats["n_full"] == stats["n_runs"]
        avg_med = "-" if stats["avg_med"] is None else f"{stats['avg_med']:.2f}"
        emit(
            f" {problem} [{key}]: {stats['n_full']}/{stats['n_runs']} 次全清"
            f"（{stats['cleared']}/{stats['total']} 源）  {flag}   "
            f"平均源耗时中位 {avg_med} s/源"
        )

    deliver = next((s for p, k, _r, s in blocks if p == "问题4" and k == args.q4_arm), None)
    base = next(
        (s for p, k, _r, s in blocks if p == "问题4" and k == args.baseline_arm), None
    )
    if deliver and base and deliver["avg_med"] and base["avg_med"]:
        delta = (deliver["avg_med"] - base["avg_med"]) / base["avg_med"] * 100.0
        emit(
            f" 问题4 本次抽检对比: {args.baseline_arm} {base['avg_med']:.2f} → "
            f"{args.q4_arm} {deliver['avg_med']:.2f} s/源（{delta:+.1f}%）"
        )
        emit("   （抽检局数少、方差大，交付结论以 script/bench_q34.py 的多臂/多种子口径为准）")

    emit("")
    emit(f" 大规模基准参照（本次只有 {len(seeds)} 个种子，用于确认可复现性）:")
    for key in _reference_keys(args):
        emit(f"   {key}: {REFERENCE[key]}")
    emit(
        " 注：带「旧口径」的参照数字是 /clear 计时修正前归档的，"
        "复现请加 --legacy-timing；重新测量用 python3 script/bench_q34.py"
    )
    emit(f" 总体: {'全部通过（无未全清）' if all_full else '存在未全清，见上方明细'}")
    emit("")

    out_path = Path(args.out)
    if out_path.parent and str(out_path.parent) != ".":
        out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    emit(f"（明细已写入 {out_path}）")

    if args.fail_on_uncleared and not all_full:
        return 1
    return 0


def _reference_keys(args: argparse.Namespace) -> list[str]:
    keys = [args.q3_arm, args.q4_arm]
    if not args.no_baseline:
        keys.append(args.baseline_arm)
    return [k for k in keys if k in REFERENCE]


if __name__ == "__main__":
    raise SystemExit(main())
