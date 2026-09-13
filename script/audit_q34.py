#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""问题3/4 的**独立审计 + 持有集验证**（2026-09-13 复核的可执行版本）。

这个脚本的存在理由是：2026-09-13 的独立复核证明"在调参用过的种子上全清"
**不等于**"全清"。原先的 30/60 种子基准（种子 1–60）在后续种子 61–160 上
暴露出 Q4 的 97/100，而漏掉的源**全部已经拿到过测向读数**。所以审计脚本把
"未参与调参的种子区间"、"高定向占比压力场景"、"消融臂"三件事固定下来。

三种模式
--------
``mixed``            默认案例分布（``generate_case`` 的定向占比 1..⌊n/2⌋）
``all_directional``  **压力场景**：把全部源设为定向（``n_directional=16``）。
                     题面允许任意方向比例，默认生成器覆盖不到高定向占比。
``omni``             全向源（问题3）。

用法::

    # 持有集：种子 61-160，混合 / 全定向
    python script/audit_q34.py --mode mixed --start 61 --end 160
    python script/audit_q34.py --mode all_directional --start 61 --end 160

    # 全臂对照（q4-v14 / q4-v17 / q4-v18）
    python script/audit_q34.py --mode mixed --start 61 --end 160 --arms all

    # 逐层消融 q4-v18（关掉覆盖计划 / 关掉排除圆 / 二者都关）
    python script/audit_q34.py --mode all_directional --arms ablation

    # 问题3
    python script/audit_q34.py --mode omni --strategy q3-v15 --start 61 --end 160

结果写到 ``logs/audit/<mode>_<strategy>_<start>_<end>.json``，
失败案例的完整轨迹单独落盘到 ``logs/audit/failure_*.json``（含**仅供离线诊断**的
源真值），便于复现"估计误差 33m、最近试清距离 21.5m"这类根因。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "framework" / "src"))

from mathmodel2026b.mock.world import World, generate_case  # noqa: E402
from mathmodel2026b.protocol import (  # noqa: E402
    parse_clear,
    parse_enter,
    parse_exit,
    parse_measure,
)
from mathmodel2026b.state import DogState  # noqa: E402
from mathmodel2026b.versioned import DECISION_METHODS, build_method  # noqa: E402

ROBOT = "000000000000"
OUT_DIR = REPO_ROOT / "logs" / "audit"

#: 逐层消融：从 q4-v18 出发依次关掉三层机制
ABLATION_ARMS: tuple[tuple[str, dict], ...] = (
    ("q4-v18", {}),
    ("q4-v18/-regionclear", {"use_region_clear": False}),
    ("q4-v18/-exclusions", {"use_failed_clear_exclusions": False}),
    ("q4-v18/-knowledge", {"use_knowledge_matrix": False, "use_failed_clear_exclusions": False}),
    ("q4-v17", {}),
    ("q4-v14", {}),
)

ARM_CHOICES = {
    "all": ("q4-v14", "q4-v17", "q4-v18"),
    "ablation": tuple(name for name, _ in ABLATION_ARMS),
}


class DirectClient:
    """与 HTTP mock **同一条**协议解析路径，只是省掉传输开销。

    刻意不直接用 ``World`` 的返回值：审计必须走策略真正看到的解析器
    （``parse_measure`` / ``parse_clear``），否则"策略看到什么"与"模拟器发了什么"
    之间的差异会被审计脚本自己掩盖掉。
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


def run_case(seed: int, mode: str, key: str, overrides: dict, *, legacy_timing: bool = False) -> dict:
    directional = mode != "omni"
    case = generate_case(
        seed,
        omni_only=mode == "omni",
        n_directional=16 if mode == "all_directional" else None,
    )
    world = World(case, ROBOT, legacy_clear_timing=legacy_timing)
    client = DirectClient(world)
    state = DogState()
    strategy = build_method(key, **overrides)

    started = time.time()
    client.enter()
    strategy.run(client, state)
    client.exit()
    wall_s = time.time() - started

    result = world.summary()
    result.update(
        seed=seed,
        mode=mode,
        arm=key,
        overrides=overrides,
        wall_s=round(wall_s, 3),
        scan_points_visited=state.scan_points_visited,
        # 仅供离线诊断：策略**没有**看到这些真值
        truth_uncleared=[
            {
                "channel": j.channel,
                "x": round(j.position.x, 2),
                "y": round(j.position.y, 2),
                "direction_deg": j.direction_deg,
                "was_detected": bool(state.channels[j.channel].readings),
                "n_readings": len(state.channels[j.channel].readings),
            }
            for j in case.jammers
            if j.channel not in world.cleared
        ],
        diagnostics=strategy.diagnostics(state),
    )
    return result


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def summarise(rows: list[dict]) -> dict:
    avgs = [r["average_clear_time_s"] for r in rows]
    vts = [r["virtual_time_s"] for r in rows]
    dists = [r["move_distance_m"] for r in rows]
    walls = [r["wall_s"] for r in rows]
    full = [r for r in rows if r["cleared_count"] == r["n_jammers"]]
    return {
        "n_cases": len(rows),
        "full_clear_cases": len(full),
        "full_clear_rate": round(len(full) / len(rows), 4) if rows else 0.0,
        "cleared_sources": sum(r["cleared_count"] for r in rows),
        "total_sources": sum(r["n_jammers"] for r in rows),
        "avg_clear_time_median": round(statistics.median(avgs), 2) if avgs else None,
        "avg_clear_time_mean": round(statistics.mean(avgs), 2) if avgs else None,
        "virtual_time_median": round(statistics.median(vts), 1) if vts else None,
        "virtual_time_p90": round(_quantile(vts, 0.9), 1) if vts else None,
        "move_distance_median": round(statistics.median(dists), 1) if dists else None,
        "wall_median_s": round(statistics.median(walls), 3) if walls else None,
        "wall_max_s": round(max(walls), 3) if walls else None,
        "failed_seeds": [r["seed"] for r in rows if r["cleared_count"] != r["n_jammers"]],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", default="mixed", choices=("mixed", "all_directional", "omni"))
    ap.add_argument("--strategy", default=None, help="决策方法名（默认按模式给：omni→q3-v15，其余→q4-v18）")
    ap.add_argument("--arms", default=None, choices=sorted(ARM_CHOICES), help="多臂对照")
    ap.add_argument("--start", type=int, default=61)
    ap.add_argument("--end", type=int, default=160)
    ap.add_argument("--seeds", default=None, help="种子表达式，如 61-160,200（覆盖 --start/--end）")
    ap.add_argument("--legacy-timing", action="store_true",
                    help="用旧 mock 计时（失败 /clear 也按 5s）复现 2026-09-13 之前的归档数字")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    seeds = parse_seeds(args.seeds) if args.seeds else list(range(args.start, args.end + 1))
    out_dir = Path(args.out_dir) if args.out_dir else OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.arms:
        arms = [(name, dict(overrides)) for name, overrides in ABLATION_ARMS
                if not ARM_CHOICES[args.arms] or name in ARM_CHOICES[args.arms]]
        if args.arms == "all":
            arms = [(k, {}) for k in ARM_CHOICES["all"]]
    else:
        key = args.strategy or ("q3-v15" if args.mode == "omni" else "q4-v18")
        if key not in DECISION_METHODS:
            raise SystemExit(f"未知决策方法 {key!r}")
        arms = [(key, {})]

    print(f"mode={args.mode} seeds={seeds[0]}..{seeds[-1]} ({len(seeds)} 例) "
          f"legacy_timing={args.legacy_timing}")
    overall: dict[str, dict] = {}
    for key, overrides in arms:
        label = key + ("/" + ",".join(f"{k}={v}" for k, v in overrides.items()) if overrides else "")
        rows: list[dict] = []
        t0 = time.time()
        for seed in seeds:
            row = run_case(seed, args.mode, key, overrides, legacy_timing=args.legacy_timing)
            rows.append(row)
            flag = "" if row["cleared_count"] == row["n_jammers"] else "  <== 未全清"
            print(f"  {label:<28} seed={seed:4d} {row['cleared_count']:3d}/{row['n_jammers']:2d} "
                  f"vt={row['virtual_time_s']:9.1f} avg={row['average_clear_time_s']:8.2f}"
                  f"{flag}", flush=True)
            if row["cleared_count"] != row["n_jammers"]:
                (out_dir / f"failure_{args.mode}_{label.replace('/', '_').replace(',', '_')}_{seed}.json").write_text(
                    json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        stats = summarise(rows)
        stats["arm"] = label
        stats["elapsed_s"] = round(time.time() - t0, 2)
        overall[label] = stats
        payload = {"mode": args.mode, "arm": label, "overrides": overrides,
                   "legacy_timing": args.legacy_timing, "seeds": seeds,
                   "summary": stats, "cases": rows}
        path = out_dir / f"{args.mode}_{label.replace('/', '_').replace(',', '_')}_{seeds[0]}_{seeds[-1]}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  -> {path}")

    print("\n=== 汇总 ===")
    header = f"{'arm':<28}{'全清局':>9}{'清除源':>12}{'avg中位':>10}{'vt中位':>10}{'路程中位':>11}{'墙钟中位':>10}"
    print(header)
    print("-" * len(header))
    for label, s in overall.items():
        print(f"{label:<28}{s['full_clear_cases']:>4}/{s['n_cases']:<4}"
              f"{s['cleared_sources']:>6}/{s['total_sources']:<5}"
              f"{s['avg_clear_time_median']:>10}"
              f"{s['virtual_time_median']:>10}"
              f"{s['move_distance_median']:>11}"
              f"{s['wall_median_s']:>10}")
        if s["failed_seeds"]:
            print(f"{'':<28}未全清种子：{s['failed_seeds']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
