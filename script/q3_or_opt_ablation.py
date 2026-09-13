#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""问题3 的 **or-opt 修正 + 覆盖式清除** 增益分解（消融对照）。

为什么单独有这么一个脚本
------------------------
`docs/review-fixes-2026-09-13.md` §5 记录了一个只在**消融**里才看得见的 bug：

    base = self._path_len(start, rest)              # 删掉片段后的短路线
    if self._path_len(start, cand) < base - 1e-9:   # 片段插回后的路线

由三角不等式，删点只会让路线变短或不变、插点只会变长或不变，因此
``len(cand) >= len(rest)`` **恒成立** —— 这个判据几乎不可能接受任何动作，
在线 or-opt 一直在空转。归档表格里 "or_opt_passes=0 与 4 逐位相同" 不是
"or-opt 中性"的证据，而是这个 bug 的指纹。

修好之后 or-opt 变成真实收益项，而 ``versioned.OR_OPT_PASSES`` 也随之由 0 改成 4。
本脚本把四件事拆开量，避免"把两个收益算到一个机制头上"：

=====================  ============================================================
臂                     含义
=====================  ============================================================
``v8``                 不加 or-opt 也不加覆盖式清除的基线
``v15-legacy``         v15 + **注册表旧默认扫描布局**（7 / 1110 / 2）+ or-opt 关
                       —— 这是 2026-09-13 之前注册表实际跑出来的配置
``v15-final-o0``       v15 + **定稿扫描布局**（6 / 1130 / 3）+ or-opt 关
                       —— 归档口径下它就是 README 里的 264.81
``v15-final-o4``       v15 + 定稿布局 + or-opt 开（只量 or-opt 的贡献）
``v18-final-o0``       v18 + 定稿布局 + or-opt 关（只量覆盖式清除的贡献）
``v18-final-o4``       v18 + 定稿布局 + or-opt 开（= 当前交付配置）
=====================  ============================================================

**两个必须分开的轴**：扫描布局定稿（6/1130/3）与注册表旧默认（7/1110/2）不是同一个
配置，差距约 4–9 s/源；把它和 or-opt 的收益混在一起会把结论算错。定稿配置的判定依据是
"归档口径 + or-opt=0 在 seed 1–30 上给出 **264.81**"，与最终工程 ``README`` 的 v15 数字
逐位一致。

用法::

    python3 script/q3_or_opt_ablation.py                       # 默认 1-30 / 1-60 / 61-160
    python3 script/q3_or_opt_ablation.py --seeds 1-30
    python3 script/q3_or_opt_ablation.py --json logs/q3_ablation.json

> ⚠️ 所有数字都取自**修正后的计时口径**（失败 ``/clear`` = 3s、成功 = 5s）。
> 归档里的 264.81 / 273.73 是旧口径，要复现它们请同时传
> ``--legacy-timing``（本脚本默认关闭）。
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
from mathmodel2026b.strategy_v18 import Q3V18Params, Q3V18Strategy  # noqa: E402
from mathmodel2026b.versioned import (  # noqa: E402
    Q3_DELIVERY_PARAMS,
    Q3_SCAN_PARAMS,
    build_method,
)

ROBOT = "000000000000"

#: Q3 交付参数（与 ``versioned.DECISION_METHODS["q3-v18"].overrides`` 同源）
_V18_BASE = {**Q3_SCAN_PARAMS, **Q3_DELIVERY_PARAMS}

SEED_SETS: tuple[tuple[int, int], ...] = ((1, 30), (1, 60), (61, 160))


def _legacy_default_arm():
    """v15 + Q3V5Params 的**字段默认**扫描布局（sides=7 / radius=1110 / limit=2）。

    注册表已把定稿布局（6/1130/3）并入交付覆盖，所以"旧默认"只能绕过注册表、
    直接构造参数对象来复现。
    """
    from mathmodel2026b.strategy_v15 import Q3V15Params, Q3V15Strategy

    return Q3V15Strategy(Q3V15Params(or_opt_passes=0, schedule_min_readings=1))


class _DirectClient:
    """与 HTTP mock 同一条协议解析路径，省掉传输开销。"""

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


def _arms() -> list[tuple[str, object]]:
    final = dict(_V18_BASE)          # 含定稿扫描布局
    return [
        ("v8 (基线)", lambda: build_method("q3-v8")),
        # 注册表旧默认扫描布局（= Q3V5Params 的字段默认值 7/1110/2）
        ("v15-legacy (7/1110/2, o0)", _legacy_default_arm),
        ("v15-final-o0 (6/1130/3, o0)",
         lambda: build_method("q3-v15", or_opt_passes=0, **Q3_SCAN_PARAMS)),
        ("v15-final-o4 (6/1130/3, o4)",
         lambda: build_method("q3-v15", or_opt_passes=4, **Q3_SCAN_PARAMS)),
        ("v18-final-o0 (覆盖清除 only)",
         lambda: Q3V18Strategy(Q3V18Params(or_opt_passes=0, **final))),
        ("v18-final-o4 (当前交付)",
         lambda: Q3V18Strategy(Q3V18Params(or_opt_passes=4, **final))),
    ]


def _run_arm(make, seeds: list[int], *, legacy_timing: bool) -> dict:
    rows: list[dict] = []
    started = time.time()
    for seed in seeds:
        case = generate_case(seed, omni_only=True)
        world = World(case, ROBOT, legacy_clear_timing=legacy_timing)
        client = _DirectClient(world)
        state = DogState()
        strategy = make()
        client.enter()
        strategy.run(client, state)
        client.exit()
        rows.append(world.summary())
    avgs = [r["average_clear_time_s"] for r in rows]
    vts = [r["virtual_time_s"] for r in rows]
    return {
        "n_cases": len(rows),
        "full_clear_cases": sum(1 for r in rows if r["cleared_count"] == r["n_jammers"]),
        "cleared_sources": sum(r["cleared_count"] for r in rows),
        "total_sources": sum(r["n_jammers"] for r in rows),
        "avg_median": round(statistics.median(avgs), 2),
        "avg_mean": round(statistics.mean(avgs), 2),
        "vt_median": round(statistics.median(vts), 1),
        "vt_total": round(sum(vts), 1),
        "elapsed_s": round(time.time() - started, 2),
    }


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


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--seeds", default=None, help="种子表达式，如 1-30,61-160（默认跑三组）")
    ap.add_argument("--legacy-timing", action="store_true",
                    help="旧 mock 计时（失败 /clear 也按 5s）；用于复现 2026-09-13 之前的归档")
    ap.add_argument("--json", default=None, help="把结果写到该 JSON 路径")
    args = ap.parse_args()

    if args.seeds:
        seeds = parse_seeds(args.seeds)
        seed_sets = [(seeds[0], seeds[-1])]
    else:
        seed_sets = list(SEED_SETS)

    print(f"计时口径：{'legacy（失败清除 5s，复现归档）' if args.legacy_timing else '附件1 §2.3（失败 3s / 成功 5s）'}")
    payload: dict = {"legacy_timing": args.legacy_timing, "seed_sets": {}}

    for lo, hi in seed_sets:
        seeds = list(range(int(lo), int(hi) + 1))
        print(f"\n===== seeds {lo}-{hi}（{len(seeds)} 例）=====")
        header = (f"{'臂':<26}{'全清':>8}{'清除源':>13}{'avg中位':>10}"
                  f"{'avg均值':>10}{'vt中位':>10}{'总vt':>12}{'用时':>8}")
        print(header)
        print("-" * len(header))
        block: dict = {}
        for label, make in _arms():
            stats = _run_arm(make, seeds, legacy_timing=args.legacy_timing)
            block[label] = stats
            print(f"{label:<26}"
                  f"{stats['full_clear_cases']:>4}/{stats['n_cases']:<3}"
                  f"{stats['cleared_sources']:>7}/{stats['total_sources']:<5}"
                  f"{stats['avg_median']:>10.2f}"
                  f"{stats['avg_mean']:>10.2f}"
                  f"{stats['vt_median']:>10.1f}"
                  f"{stats['vt_total']:>12.1f}"
                  f"{stats['elapsed_s']:>7.1f}s", flush=True)
        payload["seed_sets"][f"{lo}-{hi}"] = block

    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n-> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
