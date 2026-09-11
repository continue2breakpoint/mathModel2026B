#!/usr/bin/env python3
"""Q3 虚拟时间审计：把总时间按"决策类别"拆开，回答"时间到底花在哪"。

题面口径：虚拟时间 = 移动(5m/s) + 频道切换(1s) + 检测(5s) + 光学定位(3s)+清除(2s)。
本脚本在 mock 上跑若干 seed（开启 trace），按 trace 里每条 measure/clear 的
``reason`` 把**移动距离**归因到决策类别，再折算成时间占比。

用法::

    python3 script/q3_time_audit.py --seeds 1-20
    python3 script/q3_time_audit.py --seeds 1-20 --param scan_radius=1125
"""

from __future__ import annotations

import argparse
import json
import statistics as stats
import sys
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(REPO_ROOT / "framework" / "src"))

from jammers_paths import resolve  # noqa: E402
from mathmodel2026b.logging_utils import default_log_root  # noqa: E402
from mathmodel2026b.runner import RunConfig, run_once  # noqa: E402
from mathmodel2026b.strategy import Q3Params  # noqa: E402

#: reason -> 决策类别
def classify(kind: str, reason: str) -> str:
    if kind == "clear":
        return {
            "near": "clear@near",
            "localized": "clear@localized",
            "last-resort": "clear@last-resort",
        }.get(reason, f"clear@{reason or 'other'}")
    if reason.startswith("scan#"):
        return "measure@cover-scan"
    if reason == "rescan":
        return "measure@rescan"
    if reason in ("near",):
        return "measure@near"
    if reason == "post-fail":
        return "measure@post-fail"
    if reason == "refine-fallback":
        return "measure@refine-fallback"
    if reason.startswith("refine#"):
        return "measure@refine"
    return f"measure@{reason or 'other'}"


def parse_seeds(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Q3 虚拟时间审计")
    ap.add_argument("--seeds", default="1-20")
    ap.add_argument("--n-jammers", type=int, default=None)
    ap.add_argument("--directional", action="store_true")
    ap.add_argument("--param", action="append", default=[])
    ap.add_argument("--log-root", default=None)
    args = ap.parse_args(argv)

    params = Q3Params()
    for item in args.param:
        key, raw = item.split("=", 1)
        cur = getattr(params, key)
        if isinstance(cur, tuple):
            value: object = (float(raw),)
        else:
            try:
                value = int(raw)
            except ValueError:
                value = float(raw)
        setattr(params, key, value)

    seeds = parse_seeds(args.seeds)
    dist_by_cat: dict[str, list[float]] = defaultdict(list)
    cnt_by_cat: dict[str, list[int]] = defaultdict(list)
    virt, dist, avg, nj = [], [], [], []
    per_channel_dist: list[float] = []
    unreachable = 0

    for seed in seeds:
        outcome = run_once(
            RunConfig(
                robot_id="000000000000",
                seed=seed,
                mode="mock",
                n_jammers=args.n_jammers,
                omni_only=not args.directional,
                params=params,
                log_root=Path(args.log_root) if args.log_root else default_log_root(),
                tag="time-audit",
                write_trace=True,
            )
        )
        if outcome.status != "ok" or (outcome.metrics.get("cleared_ratio") or 0) < 0.999:
            unreachable += 1
        m = outcome.metrics
        virt.append(m["virtual_time_s"])
        dist.append(m["move_distance_m"])
        avg.append(m["average_clear_time_s"])
        nj.append(m["n_jammers"])

        trace_path = outcome.run_dir / "trace.jsonl"
        events = [json.loads(l) for l in trace_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        cur_x = cur_y = 0.0
        local: dict[str, float] = defaultdict(float)
        local_cnt: dict[str, int] = defaultdict(int)
        ch_dist: dict[int, float] = defaultdict(float)
        for ev in events:
            dx, dy = ev["x"] - cur_x, ev["y"] - cur_y
            d = (dx * dx + dy * dy) ** 0.5
            cur_x, cur_y = ev["x"], ev["y"]
            cat = classify(ev["kind"], ev.get("reason", ""))
            local[cat] += d
            local_cnt[cat] += 1
            if ev["kind"] in ("measure", "clear"):
                ch_dist[ev["channel"]] += d
        for cat, d in local.items():
            dist_by_cat[cat].append(d)
        for cat, c in local_cnt.items():
            cnt_by_cat[cat].append(c)
        per_channel_dist.extend(ch_dist.values())

    def med(xs: list[float]) -> float:
        return stats.median(xs) if xs else 0.0

    tot_dist = med(dist)
    tot_virt = med(virt)
    print(f"seeds={len(seeds)}  未全清除={unreachable}")
    print(f"  虚拟时间中位数 = {tot_virt:.0f} s    路程中位数 = {tot_dist:.0f} m "
          f"({tot_dist / 5:.0f} s 移动)")
    print(f"  平均定位清除时间中位数 = {med(avg):.1f} s   源数中位数 = {med(nj):.0f}")
    print()
    print(f"{'决策类别':<26}{'路程(m)':>10}{'占比':>8}{'折算时间(s)':>12}{'事件数':>8}")
    print("-" * 66)
    for cat in sorted(dist_by_cat, key=lambda c: -med(dist_by_cat[c])):
        d = med(dist_by_cat[cat])
        c = med(cnt_by_cat.get(cat, [0]))
        print(f"{cat:<26}{d:>10.0f}{100 * d / tot_dist if tot_dist else 0:>7.1f}%"
              f"{d / 5:>12.0f}{c:>8.0f}")
    print("-" * 66)
    print(f"{'合计':<26}{tot_dist:>10.0f}{100.0:>7.1f}%{tot_dist / 5:>12.0f}")
    print()
    other_t = tot_virt - tot_dist / 5
    print(f"非移动时间中位数 = {other_t:.0f} s（检测 5s×n + 切频道 1s×n + 清除 5s×n）")
    print(f"每个源的平均路程（含扫描分摊）≈ {tot_dist / med(nj):.0f} m" if med(nj) else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
