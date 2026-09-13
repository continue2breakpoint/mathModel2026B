#!/usr/bin/env python3
"""批量调参：对参数网格 × 随机案例种子跑大量 run，结果汇总进 results.jsonl。

用法::

    # 两条曲线的笛卡尔积 × 种子 1..10
    python script/run_batch.py --grid scan_radius=1250,1400,1550 \
        --grid travel_weight=0.00033,0.00083 --seeds 1-10 --tag tune1

    # 从 JSON 规格文件读（便于版本化保存一组实验）
    python script/run_batch.py --spec script/specs/example.json

    # 反复跑同一配置换 seed（评估鲁棒性 / 清除率）
    python script/run_batch.py --seeds 1-50 --repeat 1 --jobs 8

规格文件格式::

    {
      "tag": "tune1",
      "seeds": "1-10",
      "grid": {"scan_radius": [1250, 1400, 1550]},
      "fixed": {"localize_before_full_scan": false},
      "mode": "mock"
    }

产物
----
* 每个 run 一个目录：``logs/runs/<run_id>/``
* 扁平索引：``logs/results.jsonl``（一行一个 run，直接 pandas 读）
* 批次摘要：``logs/batches/<batch_id>.json``（按参数组合聚合的均值/标准差/清除率）

退出码：0 表示所有 run 都 ok（清除率未要求满分，见 ``--require-full-clear``）。
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(REPO_ROOT / "framework" / "src"))

from jammers_paths import PathResolutionError, resolve  # noqa: E402
from mathmodel2026b.logging_utils import default_log_root  # noqa: E402
from mathmodel2026b.runner import RunConfig, run_once  # noqa: E402
from mathmodel2026b.strategy import Q3Params  # noqa: E402
from run import (  # noqa: E402
    PLACEHOLDER_TEAM_NO,
    apply_param_overrides,
    build_strategy_params,
    coerce,
    make_strategy_factory,
)


@dataclass(slots=True)
class BatchSpec:
    tag: str = "batch"
    mode: str = "mock"
    seeds: list[int] = field(default_factory=lambda: [1])
    grid: dict[str, list[Any]] = field(default_factory=dict)
    fixed: dict[str, Any] = field(default_factory=dict)
    n_jammers: int | None = None
    directional: bool = False
    repeat: int = 1
    jobs: int = 1
    notes: dict[str, Any] = field(default_factory=dict)
    #: 要横评的策略（"决策方法"名，见 ``mathmodel2026b.versioned`` 与
    #: ``script/list_methods.py``）。留空 = 只跑框架基线 ``q3``。
    #: 每个名字会与 grid 做笛卡尔积，因此可以在**同一批 seed** 上比较
    #: ``q3-v8`` 与 ``q3-v15`` 这类历代策略。
    strategies: list[str] = field(default_factory=list)

    def combinations(self) -> list[dict[str, Any]]:
        base: list[dict[str, Any]]
        if not self.grid:
            base = [dict(self.fixed)]
        else:
            keys = sorted(self.grid)
            base = []
            for values in itertools.product(*(self.grid[k] for k in keys)):
                combo = dict(self.fixed)
                combo.update(dict(zip(keys, values)))
                base.append(combo)
        names = self.strategies or ["q3"]
        out: list[dict[str, Any]] = []
        for name in names:
            for combo in base:
                item = dict(combo)
                item["__strategy__"] = name
                out.append(item)
        return out


def parse_seeds(spec: str) -> list[int]:
    seeds: list[int] = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            seeds.extend(range(int(lo), int(hi) + 1))
        else:
            seeds.append(int(part))
    return sorted(set(seeds))


def load_spec(args: argparse.Namespace) -> BatchSpec:
    spec = BatchSpec()
    if args.spec:
        with open(args.spec, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        spec.tag = raw.get("tag", spec.tag)
        spec.mode = raw.get("mode", spec.mode)
        spec.seeds = parse_seeds(raw["seeds"]) if "seeds" in raw else spec.seeds
        spec.grid = {k: list(v) for k, v in (raw.get("grid") or {}).items()}
        spec.fixed = dict(raw.get("fixed") or {})
        spec.n_jammers = raw.get("n_jammers")
        spec.directional = bool(raw.get("directional", False))
        spec.repeat = int(raw.get("repeat", 1))
        spec.jobs = int(raw.get("jobs", 1))
        spec.notes = dict(raw.get("notes") or {})
        strategies = raw.get("strategies")
        if strategies:
            spec.strategies = (
                [strategies] if isinstance(strategies, str) else [str(s) for s in strategies]
            )

    if args.tag:
        spec.tag = args.tag
    if args.mode:
        spec.mode = args.mode
    if args.seeds:
        spec.seeds = parse_seeds(args.seeds)
    for item in args.grid:
        if "=" not in item:
            raise SystemExit(f"--grid 需要 KEY=v1,v2 形式：{item!r}")
        key, values = item.split("=", 1)
        spec.grid[key.strip()] = [coerce(v) for v in values.split(",") if v != ""]
    for item in args.fixed:
        if "=" not in item:
            raise SystemExit(f"--fixed 需要 KEY=VALUE 形式：{item!r}")
        key, value = item.split("=", 1)
        spec.fixed[key.strip()] = coerce(value)
    if args.n_jammers is not None:
        spec.n_jammers = args.n_jammers
    if args.directional:
        spec.directional = True
    if args.repeat is not None:
        spec.repeat = args.repeat
    if args.jobs is not None:
        spec.jobs = args.jobs
    if getattr(args, "strategies", None):
        spec.strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    return spec


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="批量调参（参数网格 × 随机案例）")
    parser.add_argument("--spec", default=None, help="JSON 规格文件")
    parser.add_argument("--tag", default=None)
    parser.add_argument("--mode", choices=["mock", "live"], default=None)
    parser.add_argument("--seeds", default=None, help="如 1-20 或 1,5,9")
    parser.add_argument("--grid", action="append", default=[], metavar="KEY=v1,v2")
    parser.add_argument("--fixed", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--n-jammers", type=int, default=None)
    parser.add_argument("--directional", action="store_true")
    parser.add_argument("--repeat", type=int, default=None)
    parser.add_argument("--jobs", type=int, default=None, help="并发 run 数（每个 mock 独立端口）")
    parser.add_argument("--log-root", default=None)
    parser.add_argument("--login-jammers", default=None)
    parser.add_argument("--robot-id", default=None)
    parser.add_argument("--require-full-clear", action="store_true", help="有未清除的 run 就返回非零")
    parser.add_argument(
        "--strategies",
        default=None,
        help=(
            "逗号分隔的决策方法名（见 script/list_methods.py），与参数网格做笛卡尔积。"
            "用于在同一批 seed 上横评历代策略，例如 --strategies q3-v8,q3-v15"
        ),
    )
    parser.add_argument("--max-wall-s", type=float, default=None, help="单个 run 的 wall 时间上限")
    parser.add_argument("--quiet", action="store_true")
    return parser


def _override_pairs(combo: dict[str, Any]) -> list[str]:
    """把参数字典转成 ``KEY=VALUE`` 列表，跳过 ``__strategy__`` 这类内部键。"""
    return [
        f"{k}={_to_text(v)}" for k, v in combo.items() if not str(k).startswith("__")
    ]


def _run_one(
    combo: dict[str, Any],
    seed: int,
    *,
    tag: str,
    mode: str,
    robot_id: str,
    log_root: Path,
    n_jammers: int | None,
    directional: bool,
    max_wall_s: float | None,
    batch_id: str,
):
    params = Q3Params()
    apply_param_overrides(params, _override_pairs(combo))
    if max_wall_s is not None:
        params.max_wall_time_s = max_wall_s
    strategy_name = combo.get("__strategy__", "q3")
    strategy_factory = None
    if strategy_name != "q3":
        # 复用 run.py 的构造/工厂，保证"批次里的参数覆盖口径"与单跑完全一致
        # （包括版本化方法的**交付参数**，例如 schedule_min_readings=1）。
        params = build_strategy_params(
            strategy_name, directional, _override_pairs(combo)
        )
        if max_wall_s is not None:
            params.max_wall_time_s = max_wall_s
        strategy_factory = make_strategy_factory(strategy_name, directional)
    config = RunConfig(
        robot_id=robot_id,
        seed=seed,
        mode=mode,
        n_jammers=n_jammers,
        omni_only=not directional,
        params=params,
        log_root=log_root,
        tag=tag,
        write_trace=False,
        notes={"batch_id": batch_id, "combo": combo, "seed": seed},
        strategy_factory=strategy_factory,
    )
    return run_once(config)


def _to_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def aggregate(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    ok = [r for r in rows if r.get("status") == "ok"]
    ratios = [r["cleared_ratio"] for r in ok if r.get("cleared_ratio") is not None]
    avgs = [r["average_clear_time_s"] for r in ok if r.get("average_clear_time_s")]
    virt = [r["virtual_time_s"] for r in ok if r.get("virtual_time_s") is not None]
    move = [r["move_distance_m"] for r in ok if r.get("move_distance_m") is not None]

    def _stat(values: list[float]) -> dict[str, float] | None:
        if not values:
            return None
        return {
            "mean": statistics.fmean(values),
            "std": statistics.pstdev(values) if len(values) > 1 else 0.0,
            "min": min(values),
            "max": max(values),
            "median": statistics.median(values),
        }

    return {
        "runs": len(rows),
        "ok_runs": len(ok),
        "failed_runs": len(rows) - len(ok),
        "full_clear_runs": sum(1 for r in ratios if math.isclose(r, 1.0)),
        "cleared_ratio": _stat(ratios),
        "average_clear_time_s": _stat(avgs),
        "virtual_time_s": _stat(virt),
        "move_distance_m": _stat(move),
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    spec = load_spec(args)

    robot_id = args.robot_id
    if not robot_id:
        try:
            robot_id = resolve(args.login_jammers, None).team_no() or PLACEHOLDER_TEAM_NO
        except PathResolutionError:
            robot_id = PLACEHOLDER_TEAM_NO

    log_root = Path(args.log_root) if args.log_root else default_log_root()
    batch_id = f"{spec.tag}_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    combos = spec.combinations()
    total = len(combos) * len(spec.seeds) * max(1, spec.repeat)

    print(
        f"[batch] id={batch_id} mode={spec.mode} combos={len(combos)} "
        f"seeds={len(spec.seeds)} repeat={spec.repeat} total_runs={total} jobs={spec.jobs}"
    )
    if not args.quiet:
        for combo in combos:
            print(f"[batch]   combo: {json.dumps(combo, ensure_ascii=False, sort_keys=True)}")

    results: list[dict[str, Any]] = []
    started = time.time()
    tasks = [
        (combo, seed)
        for combo in combos
        for seed in spec.seeds
        for _ in range(max(1, spec.repeat))
    ]

    def work(task):
        combo, seed = task
        return combo, seed, _run_one(
            combo,
            seed,
            tag=spec.tag,
            mode=spec.mode,
            robot_id=robot_id,
            log_root=log_root,
            n_jammers=spec.n_jammers,
            directional=spec.directional,
            max_wall_s=args.max_wall_s,
            batch_id=batch_id,
        )

    if spec.jobs and spec.jobs > 1:
        with ThreadPoolExecutor(max_workers=spec.jobs) as pool:
            futures = [pool.submit(work, t) for t in tasks]
            for index, future in enumerate(as_completed(futures), 1):
                combo, seed, outcome = future.result()
                results.append({"combo": combo, "seed": seed, "outcome": outcome})
                _progress(index, total, seed, outcome)
    else:
        for index, task in enumerate(tasks, 1):
            combo, seed, outcome = work(task)
            results.append({"combo": combo, "seed": seed, "outcome": outcome})
            _progress(index, total, seed, outcome)

    elapsed = time.time() - started
    by_combo: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        key = json.dumps(item["combo"], sort_keys=True, default=str)
        by_combo.setdefault(key, []).append(_index_like(item["outcome"], item["combo"]))

    summary = {
        "batch_id": batch_id,
        "tag": spec.tag,
        "mode": spec.mode,
        "robot_id": robot_id,
        "seeds": spec.seeds,
        "repeat": spec.repeat,
        "grid": spec.grid,
        "fixed": spec.fixed,
        "n_jammers": spec.n_jammers,
        "directional": spec.directional,
        "total_runs": len(results),
        "elapsed_s": round(elapsed, 3),
        "log_root": str(log_root),
        "index": str(log_root / "results.jsonl"),
        "overall": aggregate([_index_like(r["outcome"], r["combo"]) for r in results]),
        "by_combo": {
            key: aggregate(rows) for key, rows in sorted(by_combo.items())
        },
    }

    batches_dir = log_root / "batches"
    batches_dir.mkdir(parents=True, exist_ok=True)
    summary_path = batches_dir / f"{batch_id}.json"
    with summary_path.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2, default=str)
        fh.write("\n")

    print(f"[batch] 完成 {len(results)} 个 run，用时 {elapsed:.1f}s")
    _print_summary(summary, sorted(spec.grid))
    print(f"[batch] 批次摘要：{summary_path}")
    print(f"[batch] 索引：{log_root / 'results.jsonl'}")

    overall = summary["overall"]
    if overall["failed_runs"]:
        return 1
    if args.require_full_clear and overall["full_clear_runs"] != overall["ok_runs"]:
        return 1
    return 0


def _index_like(outcome, combo: dict[str, Any]) -> dict[str, Any]:
    metrics = outcome.metrics or {}
    row = {
        "run_id": outcome.run_id,
        "status": outcome.status,
        "error": outcome.error,
        **{f"param_{k}": v for k, v in combo.items()},
    }
    row.update({k: metrics.get(k) for k in (
        "cleared_count",
        "n_jammers",
        "cleared_ratio",
        "virtual_time_s",
        "average_clear_time_s",
        "move_distance_m",
    )})
    return row


def _progress(index: int, total: int, seed: int, outcome) -> None:
    metrics = outcome.metrics or {}
    print(
        f"[batch] {index:>5}/{total} seed={seed:<4} {outcome.status:<6} "
        f"cleared={metrics.get('cleared_count')}/{metrics.get('n_jammers')} "
        f"avg_s={_fmt(metrics.get('average_clear_time_s'))} "
        f"virt_s={_fmt(metrics.get('virtual_time_s'))}",
        flush=True,
    )


def _fmt(value: Any) -> str:
    return "-" if value is None else f"{value:.1f}"


def _print_summary(summary: dict[str, Any], grid_keys: list[str] | None = None) -> None:
    grid_keys = grid_keys or []

    def label(key: str) -> str:
        try:
            combo = json.loads(key)
        except ValueError:
            return key[:56]
        if not grid_keys:
            return key[:56]

        def short(value: Any) -> str:
            if isinstance(value, bool):
                return "T" if value else "F"
            if isinstance(value, float):
                return f"{value:.4g}"
            return str(value)

        return " ".join(
            f"{k}={short(combo.get(k))}" for k in grid_keys if k in combo
        )[:56]

    header = f"{'combo':<56} {'runs':>5} {'full':>5} {'ratio':>7} {'avg_s':>9} {'virt_s':>10}"
    print(header)
    print("-" * len(header))
    for key, agg in summary["by_combo"].items():
        ratio = agg["cleared_ratio"]
        avg = agg["average_clear_time_s"]
        virt = agg["virtual_time_s"]
        print(
            f"{label(key):<56} {agg['ok_runs']:>5} {agg['full_clear_runs']:>5} "
            f"{(ratio['mean'] if ratio else float('nan')):>7.3f} "
            f"{(avg['mean'] if avg else float('nan')):>9.1f} "
            f"{(virt['mean'] if virt else float('nan')):>10.1f}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
