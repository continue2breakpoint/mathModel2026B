#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""问题3/问题4 的**统一 A/B 基准驱动**（历代版本 + 独立路线，跑同一批 mock 案例）。

为什么有这个脚本
----------------
2026-09-13 之前，问题3/4 的基准散在五个各写各的临时脚本里：
``_bench_v5.py``（问题3 历代臂）、``_ab_q4_v17.py``（问题4 布局三臂 A/B）、
``_bench_ab.py``（上游 q3 vs 知识矩阵）、``_bench_paper_q4.py``（三条技术路线对照）、
``_bench_versions.py``（历代回归）。它们各自维护一份 import 与一份"交付参数"副本，
于是反复出现三类事故：

1. **交付配置抄了五份。** ``schedule_min_readings=1`` / ``or_opt_passes=0`` 这类
   定稿参数在每个脚本里各写一遍，改一处漏四处，"跑出来的数字"与"文档里的数字"
   就对不上了。
2. **口径漂移。** ``_bench_ab.py`` 早期的 ``--directional`` 只传给案例生成器、
   没传进**策略参数**，于是"定向场景下的矩阵结果"其实跑的是全向假设
   （见 ``docs/method-review-next-steps.md`` §2.4）。
3. **数字不可复现。** mock 的 ``/clear`` 计时修正后（失败 3s、成功 5s，
   见 :class:`mathmodel2026b.mock.world.World`），旧脚本没有任何开关能在
   同一批种子上重放归档数字。

所以这里把"臂"收敛到**唯一权威来源** :mod:`mathmodel2026b.versioned`：每个臂都用
``build_method(key, **overrides)`` 构造，交付参数覆盖必然生效；本文件里**不出现
任何参数默认值**（只有命令行显式给的 ``--param`` 覆盖）。

两种模式
--------
``omni``  问题3：``generate_case(seed, omni_only=True)``，全向源。
``dir``   问题4：``generate_case(seed, omni_only=False)``，全向 + 定向混合；
          加 ``--n-directional 16`` 就是"全定向"压力场景（默认生成器只覆盖
          定向占比 1..⌊n/2⌋，压不到高占比）。

用法::

    # 问题3：最终版 / 路线B 上一代 / 路线A（知识矩阵）/ 已过时基线
    python3 script/bench_q34.py --mode omni --seeds 1-30

    # 问题4：最终交付 / 冻结对照 / 上一代交付 / 论文冻结内核
    python3 script/bench_q34.py --mode dir --seeds 1-30

    # 只比两代，或跑持有集 / 全定向压力场景
    python3 script/bench_q34.py --mode dir --seeds 1-10 --arms q4-v18,q4-v17
    python3 script/bench_q34.py --mode dir --seeds 61-160 --n-directional 16

    # 复现 2026-09-13 之前归档的数字（失败 /clear 也按 5s 计）
    python3 script/bench_q34.py --mode omni --seeds 1-30 --legacy-timing

    # 列出全部可选臂（含"负结果/不适用"标注）
    python3 script/bench_q34.py --list

每个臂输出：全清局数 / 总局数、清除源数 / 总源数、``average_clear_time_s`` 的中位与均值、
``virtual_time_s`` 中位、``move_distance_m`` 中位、墙钟中位。结果写到
``logs/bench/<mode>_<arms>_<start>_<end>.json``（含逐例明细）与同名 ``.txt``（表格），
表格同时打到标准输出。
"""

from __future__ import annotations

import argparse
import json
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

# ``coerce`` 复用 run.py 的 ``--param KEY=VALUE`` 取值口径（int/float/bool/元组），
# 避免两个脚本对 "1200" 与 "1200.0" 给出不同类型。
from run import coerce  # noqa: E402
from mathmodel2026b.client import HttpSimulatorClient  # noqa: E402
from mathmodel2026b.mock.server import MockSimulator  # noqa: E402
from mathmodel2026b.mock.world import generate_case  # noqa: E402
from mathmodel2026b.state import DogState  # noqa: E402
from mathmodel2026b.versioned import (  # noqa: E402
    DECISION_METHODS,
    available_methods,
    build_method,
)

#: 离线 mock 用的占位队号（**不要**写死真实队号，见 script/run.py）。
ROBOT = "000000000000"

OUT_DIR = REPO_ROOT / "logs" / "bench"

#: 每个模式的**基线臂**（顺序即表格顺序）。这些是"历代交付 + 路线入口 + 对照"，
#: 都是长期存在的键；**当前**的"最终交付"由注册表决定（见 :func:`default_arms`），
#: 不写死在这里——注册表换了交付版，基准表就跟着换，不需要改本文件。
BASE_ARMS: dict[str, tuple[str, ...]] = {
    # 问题3：上一代交付 / 路线B 入口（v8）/ 路线A（知识矩阵）/ 已过时基线（量化历代改进）
    "omni": ("q3-v15", "q3-v8", "matrix", "q3"),
    # 问题4：冻结对照 / 上一代交付 / 路线A（论文冻结内核）
    "dir": ("q4-v17", "q4-v14", "paper-q4"),
}

#: 模式隐含的参数覆盖。``matrix`` 在问题4 下必须把 ``directional`` 交给**策略参数**
#: （不只是案例生成器）——这正是 ``_bench_ab.py`` 当年踩过的口径错误。
MODE_PARAM_OVERRIDES: dict[str, dict[str, dict[str, Any]]] = {
    "dir": {"matrix": {"directional": True}},
}


def registry_final(problem: int) -> str | None:
    """注册表里当前标为 ``final`` 的臂（每个问题至多一个）。"""
    for key in available_methods(problem):
        if DECISION_METHODS[key].final:
            return key
    return None


def default_arms(mode: str) -> list[str]:
    """默认臂 = 注册表的**当前最终交付**（若有）+ 本问题的历代交付/路线入口/对照。"""
    keys = list(BASE_ARMS[mode])
    final = registry_final(3 if mode == "omni" else 4)
    if final and final not in keys:
        keys.insert(0, final)
    return keys


def default_arms_note() -> str:
    """``--help`` 里展示默认臂（含注册表当前交付版）。"""
    return "；".join(
        f"{mode} → " + ",".join(default_arms(mode)) for mode in ("omni", "dir")
    )


# --------------------------------------------------------------------------
# 命令行解析
# --------------------------------------------------------------------------
def parse_seeds(spec: str) -> list[int]:
    """``"1-30,200"`` -> ``[1..30, 200]``（去重升序）。"""
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
    """``--param KEY=VALUE`` -> ``{KEY: value}``（类型由 :func:`run.coerce` 决定）。"""
    params: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--param 需要 KEY=VALUE 形式：{item!r}")
        key, raw = item.split("=", 1)
        params[key.strip()] = coerce(raw)
    return params


def check_param_keys(key: str, overrides: dict[str, Any]) -> None:
    """``--param`` 的键必须真的存在于该臂的参数 dataclass 上。

    只看 ``__slots__`` 是不够的：slotted 子类只声明自己的字段，继承来的字段
    （例如 ``Q3V15Params.schedule_min_readings`` 来自 ``Q3V8Params``）会被漏掉。
    因此用 ``dataclasses.fields``（与 ``script/run.py::apply_param_overrides`` 同一口径）。
    """
    from dataclasses import fields, is_dataclass

    from mathmodel2026b.versioned import _resolve_ref  # noqa: PLC0415

    params_cls = _resolve_ref(DECISION_METHODS[key].params_ref)
    valid = (
        {field.name for field in fields(params_cls)}
        if is_dataclass(params_cls)
        else set(getattr(params_cls, "__slots__", ()) or ())
    )
    unknown = sorted(k for k in overrides if k not in valid)
    if unknown:
        raise SystemExit(
            f"未知参数 {unknown}（臂 {key}）；可用：{', '.join(sorted(valid))}"
        )


def build_arm(key: str, overrides: dict[str, Any]):
    """构造一个臂的策略实例（交付参数 + 本脚本覆盖，全部经注册表）。"""
    if key not in DECISION_METHODS:
        raise SystemExit(
            f"未知臂 {key!r}；可用：{', '.join(available_methods())}"
        )
    check_param_keys(key, overrides)
    return build_method(key, **overrides)


def arm_label(key: str, overrides: dict[str, Any]) -> str:
    """表格里显示的臂名；有覆盖时把覆盖写出来（否则读数无法归因）。"""
    if not overrides:
        return key
    return key + "@" + ",".join(f"{k}={v}" for k, v in sorted(overrides.items()))


def arm_warnings(mode: str, key: str, overrides: dict[str, Any]) -> list[str]:
    """臂与模式不匹配时给出**显式**告警（比静默跑出一个错口径的数字好）。"""
    spec = DECISION_METHODS.get(key)
    if spec is None:
        return []
    problem = 3 if mode == "omni" else 4
    notes: list[str] = []
    if problem not in spec.problem:
        notes.append(
            f"臂 {key} 声明只解问题 {'/'.join(str(p) for p in spec.problem)}，"
            f"在 mode={mode}（问题{problem}）下属于跨问题套用，结论只能当反例看"
        )
    elif problem == 4 and spec.fails_q4_requirement:
        notes.append(f"臂 {key} 在问题4 上**不满足**『确保全部清除』，只可用于对照")
    if problem == 3 and key == "matrix" and overrides.get("directional"):
        notes.append("臂 matrix 在问题3 下不该打开 directional")
    return notes


# --------------------------------------------------------------------------
# 跑一个 (臂, 种子) 单元
# --------------------------------------------------------------------------
def run_case(
    seed: int,
    mode: str,
    key: str,
    overrides: dict[str, Any],
    *,
    n_directional: int | None,
    legacy_timing: bool,
    repeat_index: int = 0,
) -> dict[str, Any]:
    """跑一局：``enter → strategy.run → exit``，指标取 mock ``World`` 的权威统计。"""
    case = generate_case(
        seed,
        omni_only=mode == "omni",
        n_directional=n_directional,
    )
    strategy = build_arm(key, overrides)
    with MockSimulator(
        robot_id=ROBOT,
        case=case,
        legacy_clear_timing=legacy_timing,
    ) as sim:
        client = HttpSimulatorClient(robot_id=ROBOT, base_url=sim.base_url, verbose=False)
        state = DogState()
        started = time.perf_counter()
        error: str | None = None
        try:
            client.enter()
            strategy.run(client, state)
        except Exception as exc:  # noqa: BLE001 - 一局失败不应打断整张表
            error = f"{type(exc).__name__}: {exc}"
        try:
            client.exit()
        except Exception as exc:  # noqa: BLE001
            error = error or f"exit 失败：{type(exc).__name__}: {exc}"
        wall_s = time.perf_counter() - started
        summary = sim.world.summary()

    return {
        "seed": seed,
        "repeat_index": repeat_index,
        "arm": arm_label(key, overrides),
        "key": key,
        "overrides": dict(overrides),
        "error": error,
        "cleared_count": summary["cleared_count"],
        "n_jammers": summary["n_jammers"],
        "average_clear_time_s": summary["average_clear_time_s"],
        "virtual_time_s": summary["virtual_time_s"],
        "move_distance_m": summary["move_distance_m"],
        "measure_count": summary["measure_count"],
        "clear_count": summary["clear_count"],
        "channel_switch_count": summary["channel_switch_count"],
        "uncleared_channels": summary["uncleared_channels"],
        "wall_s": round(wall_s, 4),
        "scan_points_visited": state.scan_points_visited,
        "diagnostics": strategy.diagnostics(state),
    }


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """把一个臂的全部局汇总成一张表所需的量。"""
    ok = [r for r in rows if r["error"] is None]
    avgs = [r["average_clear_time_s"] for r in ok if r["average_clear_time_s"]]
    vts = [r["virtual_time_s"] for r in ok]
    dists = [r["move_distance_m"] for r in ok]
    walls = [r["wall_s"] for r in ok]

    by_seed: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        by_seed.setdefault(row["seed"], []).append(row)
    full_seeds = sorted(
        seed
        for seed, group in by_seed.items()
        if all(r["error"] is None and r["cleared_count"] == r["n_jammers"] for r in group)
    )

    return {
        "n_seeds": len(by_seed),
        "n_runs": len(rows),
        "n_errors": len(rows) - len(ok),
        "full_clear_seeds": len(full_seeds),
        "full_clear_runs": sum(
            1 for r in ok if r["cleared_count"] == r["n_jammers"]
        ),
        "full_clear_seed_list": full_seeds,
        "cleared_sources": sum(r["cleared_count"] for r in rows),
        "total_sources": sum(r["n_jammers"] for r in rows),
        "failed_seeds": sorted(
            {
                r["seed"]
                for r in rows
                if r["error"] is not None or r["cleared_count"] != r["n_jammers"]
            }
        ),
        "avg_clear_time_median": (
            round(statistics.median(avgs), 2) if avgs else None
        ),
        "avg_clear_time_mean": round(statistics.mean(avgs), 2) if avgs else None,
        "virtual_time_median": round(statistics.median(vts), 1) if vts else None,
        "move_distance_median": round(statistics.median(dists), 1) if dists else None,
        "measure_count_median": (
            round(statistics.median([r["measure_count"] for r in ok]), 1) if ok else None
        ),
        "wall_median_s": round(statistics.median(walls), 3) if walls else None,
        "wall_mean_s": round(statistics.mean(walls), 3) if walls else None,
        "wall_max_s": round(max(walls), 3) if walls else None,
    }


# --------------------------------------------------------------------------
# 输出（表格按**显示宽度**对齐：中文占 2 列，否则表头会错位）
# --------------------------------------------------------------------------
#: 汇总表列宽（显示宽度，非字符数）
TABLE_COLUMNS: tuple[tuple[str, int, str], ...] = (
    ("arm", 24, "<"),
    ("全清局", 9, ">"),
    ("清除源", 12, ">"),
    ("avg中位", 10, ">"),
    ("avg均值", 10, ">"),
    ("vt中位", 10, ">"),
    ("路程中位", 11, ">"),
    ("墙钟中位", 10, ">"),
)


def _display_width(text: str) -> int:
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _cell(text: str, width: int, align: str = ">") -> str:
    pad = max(0, width - _display_width(text))
    return text + " " * pad if align == "<" else " " * pad + text


def render_table(results: list[tuple[str, dict[str, Any], list[dict[str, Any]]]]) -> list[str]:
    """渲染汇总表（人读版，同时写进 ``.txt``）。"""
    header = "".join(_cell(name, width, align) for name, width, align in TABLE_COLUMNS)
    total_width = sum(width for _, width, _ in TABLE_COLUMNS)
    lines = [header, "-" * total_width]
    for label, stats, rows in results:
        cells = (
            _cell(label, 24, "<"),
            _cell(f"{stats['full_clear_seeds']}/{stats['n_seeds']}", 9),
            _cell(f"{stats['cleared_sources']}/{stats['total_sources']}", 12),
            _cell(_fmt(stats["avg_clear_time_median"], 2), 10),
            _cell(_fmt(stats["avg_clear_time_mean"], 2), 10),
            _cell(_fmt(stats["virtual_time_median"], 1), 10),
            _cell(_fmt(stats["move_distance_median"], 1), 11),
            _cell(_fmt(stats["wall_median_s"], 3), 10),
        )
        lines.append("".join(cells))
        if stats["failed_seeds"]:
            failed = set(stats["failed_seeds"])
            bad = ", ".join(
                f"{r['seed']}({r['cleared_count']}/{r['n_jammers']})"
                for r in rows
                if r["seed"] in failed
            )
            lines.append(_cell("", 24, "<") + f"未全清：{bad}")
        if stats["n_errors"]:
            errs = [r for r in rows if r["error"]]
            lines.append(
                _cell("", 24, "<")
                + f"异常 {stats['n_errors']} 局："
                + "; ".join(f"seed{r['seed']}: {r['error']}" for r in errs[:3])
            )
    lines.append("")
    lines.append("（全清局 = 该臂全清的**种子**数/总种子数；有 --repeat 时要求该种子的每一局都全清）")
    return lines


def _fmt(value: Any, digits: int) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=(
            "问题3/问题4 统一 A/B 基准：用 mathmodel2026b.versioned 注册表构造臂"
            "（交付参数必然生效），在同一批 mock 案例上比全清率与平均源耗时"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--mode", choices=("omni", "dir"), default="omni",
        help="omni=问题3 全向源；dir=问题4 全向+定向混合",
    )
    ap.add_argument(
        "--seeds", default="1-30",
        help="种子表达式：1-30 或 1,3,7 或 1-10,61-70（默认 1-30）",
    )
    ap.add_argument(
        "--arms", default=None,
        help=(
            "逗号分隔的臂名（= versioned 注册表键）。默认："
            + default_arms_note()
            + "（首个是本问题在注册表里的当前最终交付）"
        ),
    )
    ap.add_argument(
        "--n-directional", type=int, default=None,
        help="固定定向源个数（仅 --mode dir）；16 = 全定向压力场景。默认由案例生成器随机（1..n/2）",
    )
    ap.add_argument(
        "--param", action="append", default=[], metavar="KEY=VALUE",
        help="追加/覆盖策略参数（对**所有**臂生效，用于消融；键必须是该臂参数 dataclass 的字段）",
    )
    ap.add_argument(
        "--repeat", type=int, default=1,
        help="每个 (臂, 种子) 重复跑几局（默认 1）。mock 是确定性的，>1 只用来观察墙钟抖动",
    )
    ap.add_argument(
        "--legacy-timing", action="store_true",
        help=(
            "用旧 mock 计时：**失败的 /clear 也按 5s 计**。"
            "默认（不带此开关）按附件1 §2.3 的正确口径：未发现 3s、已清除 5s。"
            "此开关只用于复现 2026-09-13 之前归档的基准数字"
            "（两者满足 T_legacy = T_correct + 2 × 失败清除次数）"
        ),
    )
    ap.add_argument("--out-dir", default=None, help=f"输出目录（默认 {OUT_DIR}）")
    ap.add_argument("--quiet", action="store_true", help="不打印逐局明细，只打表格")
    ap.add_argument("--list", action="store_true", help="列出全部可选臂后退出")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list:
        print(f"{'arm':<12}{'问题':<10}状态")
        print("-" * 60)
        for key in available_methods():
            spec = DECISION_METHODS[key]
            print(f"{key:<12}{'/'.join(str(p) for p in spec.problem):<10}{_tag(spec)}")
        print()
        for mode in ("omni", "dir"):
            print(f"默认臂 mode={mode}: {', '.join(default_arms(mode))}")
        print("（默认集 = 该问题在注册表里的当前最终交付 + 历代交付 / 路线入口 / 对照）")
        return 0

    if args.repeat < 1:
        raise SystemExit("--repeat 至少为 1")
    if args.mode == "omni" and args.n_directional:
        print(
            f"提示：--mode omni 下 --n-directional={args.n_directional} 会被忽略"
            "（omni_only=True 时全部源都是全向源）",
            file=sys.stderr,
        )

    seeds = parse_seeds(args.seeds)
    if not seeds:
        raise SystemExit("--seeds 解析为空")
    keys = (
        [a.strip() for a in args.arms.split(",") if a.strip()]
        if args.arms
        else default_arms(args.mode)
    )
    param_overrides = parse_params(args.param)

    # 臂 -> 生效覆盖（模式隐含覆盖 + --param）
    arm_overrides: list[tuple[str, dict[str, Any]]] = []
    for key in keys:
        overrides = dict(MODE_PARAM_OVERRIDES.get(args.mode, {}).get(key, {}))
        overrides.update(param_overrides)
        arm_overrides.append((key, overrides))

    for key, overrides in arm_overrides:
        build_arm(key, overrides)  # 早失败：错误臂名/参数在开头就报出来
        for note in arm_warnings(args.mode, key, overrides):
            print(f"⚠ {note}", file=sys.stderr)

    labels = [arm_label(k, o) for k, o in arm_overrides]
    out_dir = Path(args.out_dir) if args.out_dir else OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = (
        f"{args.mode}_{safe_name('+'.join(labels))}_{seeds[0]}_{seeds[-1]}"
    )
    json_path = out_dir / f"{stem}.json"
    txt_path = out_dir / f"{stem}.txt"

    print(
        f"mode={args.mode} seeds={seeds[0]}..{seeds[-1]}（{len(seeds)} 例）"
        f" 臂={len(labels)} repeat={args.repeat} "
        f"n_directional={args.n_directional} legacy_timing={args.legacy_timing}"
    )
    print(
        "臂："
        + ", ".join(
            label + ("（★注册表最终交付）" if DECISION_METHODS[key].final else "")
            for (key, _overrides), label in zip(arm_overrides, labels)
        )
    )

    started_all = time.time()
    payload_arms: list[dict[str, Any]] = []
    rendered: list[tuple[str, dict[str, Any], list[dict[str, Any]]]] = []
    for key, overrides in arm_overrides:
        label = arm_label(key, overrides)
        rows: list[dict[str, Any]] = []
        t0 = time.time()
        for seed in seeds:
            for repeat_index in range(args.repeat):
                row = run_case(
                    seed,
                    args.mode,
                    key,
                    overrides,
                    n_directional=args.n_directional,
                    legacy_timing=args.legacy_timing,
                    repeat_index=repeat_index,
                )
                rows.append(row)
                if not args.quiet:
                    avg = row["average_clear_time_s"]
                    flag = (
                        ""
                        if row["error"] is None and row["cleared_count"] == row["n_jammers"]
                        else "  <== 未全清"
                    )
                    print(
                        f"  [{label:<22}] seed={seed:<4} "
                        f"{row['cleared_count']:>3}/{row['n_jammers']:<3} "
                        f"vt={row['virtual_time_s']:9.1f} "
                        f"avg={_fmt(avg, 2):>8} "
                        f"dist={row['move_distance_m']:8.0f} "
                        f"meas={row['measure_count']:4d} "
                        f"wall={row['wall_s']:5.2f}"
                        + (f"  ERR={row['error']}" if row["error"] else "")
                        + flag,
                        flush=True,
                    )
        stats = summarise(rows)
        stats["arm"] = label
        stats["key"] = key
        stats["overrides"] = dict(overrides)
        stats["elapsed_s"] = round(time.time() - t0, 2)
        rendered.append((label, stats, rows))
        payload_arms.append({"arm": label, "summary": stats, "cases": rows})
        if not args.quiet:
            print()

    print(f"=== 汇总 (mode={args.mode}, legacy_timing={args.legacy_timing}, repeat={args.repeat}) ===")
    table = render_table(rendered)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": args.mode,
        "seeds": seeds,
        "n_directional": args.n_directional,
        "repeat": args.repeat,
        "legacy_timing": args.legacy_timing,
        "param_overrides": param_overrides,
        "arms": payload_arms,
        "timing_note": (
            "legacy_timing=true 时失败的 /clear 也按 5s 计（旧口径，仅用于复现归档数字）；"
            "默认按附件1 §2.3：未发现 3s、已清除 5s"
        ),
        "elapsed_s": round(time.time() - started_all, 2),
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    txt_path.write_text("\n".join(table) + "\n", encoding="utf-8")

    for line in table:
        print(line)
    print(f"\n-> {json_path}")
    print(f"-> {txt_path}")
    return 0


def safe_name(text: str) -> str:
    """把臂名转成文件名安全的形式（``matrix@directional=True`` -> ``matrix@directional-True``）。"""
    return "".join(ch if (ch.isalnum() or ch in "-._+@") else "-" for ch in text)


def _tag(spec: Any) -> str:
    from mathmodel2026b.versioned import method_tag

    return method_tag(spec)


if __name__ == "__main__":
    raise SystemExit(main())
