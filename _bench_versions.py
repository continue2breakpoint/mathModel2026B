#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""版本化策略 A/B：把问题3/问题4 的每一代策略跑在**完全相同**的 mock 案例上。

设计原则
--------
1. **只用 mathModel2026B 自己的 mock**（``mock.world.generate_case`` +
   ``mock.server.MockSimulator``），不借助任何外部仓库的 ``sim_env``。
   因此本文件的数字可以在任何机器上逐位复现，且与 ``_bench_paper_q4.py`` 同源。
2. 一个 seed 对应一个案例，所有臂共用 ⇒ 差异只能归因到策略本身。
3. 看**全清场数**这种稳健量，而不是单场小数后几位。

与 ``_bench_paper_q4.py`` 的分工
--------------------------------
``_bench_paper_q4.py``  比"三条不同技术路线"：框架 q3 / 知识矩阵 / 论文内核。
``_bench_versions.py``  比"同一条路线的历代版本"：v5→v8→v15（问题3）、
                        v8→v9→v14（问题4），用于确认移植没有性能回归，
                        并给出每一代相对上一代的净收益。

用法::

    # 问题3：历代全向策略
    python3 _bench_versions.py --mode omni --seeds 1-30

    # 问题4：历代定向策略（含论文冻结内核作对照）
    python3 _bench_versions.py --mode dir --seeds 1-30

    # 只跑某几代
    python3 _bench_versions.py --mode dir --seeds 1-10 --only v9,v14,paper-q4

    # 列出所有已注册的臂
    python3 _bench_versions.py --list
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "framework" / "src"))

from mathmodel2026b.client import HttpSimulatorClient  # noqa: E402
from mathmodel2026b.mock.server import MockSimulator  # noqa: E402
from mathmodel2026b.mock.world import generate_case  # noqa: E402
from mathmodel2026b.state import DogState  # noqa: E402
from mathmodel2026b.strategy import Q3Params, Q3Strategy  # noqa: E402

# 在模块层导入每个臂，让"少了哪个模块"在 ``--list`` 时就报出来，
# 而不是跑完 30 个 seed 才在 lambda 里炸。
from mathmodel2026b import joint_layout as _joint_layout  # noqa: E402,F401
from mathmodel2026b import strategy_q4 as _strategy_q4  # noqa: E402,F401
from mathmodel2026b import strategy_v5 as _v5  # noqa: E402,F401
from mathmodel2026b import strategy_v6 as _v6  # noqa: E402,F401
from mathmodel2026b import strategy_v7 as _v7  # noqa: E402,F401
from mathmodel2026b import strategy_v8 as _v8  # noqa: E402,F401
from mathmodel2026b import strategy_v9 as _v9  # noqa: E402,F401
from mathmodel2026b import strategy_v14 as _v14  # noqa: E402,F401
from mathmodel2026b import strategy_v15 as _v15  # noqa: E402,F401

ROBOT = "000000000000"  # 占位队号


# --------------------------------------------------------------------------
# 臂注册表：名字 -> (适用的 mode, 构造函数)
# --------------------------------------------------------------------------
def _q3_v5():
    from mathmodel2026b.strategy_v5 import Q3V5Params, Q3V5Strategy

    return Q3V5Strategy(Q3V5Params())


def _q3_v6():
    from mathmodel2026b.strategy_v6 import Q3V6Params, Q3V6Strategy

    return Q3V6Strategy(Q3V6Params())


def _q3_v7():
    from mathmodel2026b.strategy_v7 import Q3V7Params, Q3V7Strategy

    return Q3V7Strategy(Q3V7Params())


def _q3_v8():
    from mathmodel2026b.strategy_v8 import Q3V8Params, Q3V8Strategy

    # 交付配置：1 条读数即登记为待清任务（3 条读数才调度会破坏 interleaving）
    return Q3V8Strategy(Q3V8Params(schedule_min_readings=1))


def _q3_v15():
    from mathmodel2026b.strategy_v15 import Q3V15Params, Q3V15Strategy

    # 交付配置：no_signal 排除修正估计点；or-opt 关闭（实测 60 seed 中性，
    # 另见 docs/q34-version-lineage.md §2.3）
    return Q3V15Strategy(Q3V15Params(schedule_min_readings=1, or_opt_passes=0))


def _q4_v8():
    """v8 当作定向策略直接套用（反例：发现层不朝向完备，会漏源）。"""
    from mathmodel2026b.strategy_v8 import Q3V8Params, Q3V8Strategy

    return Q3V8Strategy(Q3V8Params(schedule_min_readings=1))


def _q4_v9():
    from mathmodel2026b.strategy_v9 import Q4V9Params, Q4V9Strategy

    return Q4V9Strategy(Q4V9Params(schedule_min_readings=1))


def _q4_v14():
    from mathmodel2026b.strategy_v14 import Q4V14Params, Q4V14Strategy

    return Q4V14Strategy(Q4V14Params())


def _matrix_polygon():
    from mathmodel2026b.strategy_matrix import MatrixParams
    from mathmodel2026b.strategy_matrix import Q4Strategy as MatrixQ4

    return MatrixQ4(MatrixParams(directional=True))


def _matrix_axial():
    from mathmodel2026b.strategy_matrix import MatrixParams
    from mathmodel2026b.strategy_matrix import Q4Strategy as MatrixQ4

    return MatrixQ4(MatrixParams(directional=True, scan_layout="axial"))


def _paper_q4():
    from mathmodel2026b.strategy_q4 import Q4Strategy as PaperQ4

    return PaperQ4()


def _joint_layout():
    """离线布局 + 矩阵定位的联合路线臂。

    ``JointRouteQ4Strategy`` 在 ``_bench_q4_real.py`` 里定义，且**需要一份已生成
    的布局 JSON**（``data/q4_joint_layout.json``，由 ``joint_layout.JointLayoutOptimizer``
    产出）。这里按需反向导入，缺文件就给出一条可读的错误说明，而不是 ImportError。
    """
    import json

    from mathmodel2026b.geometry import Point

    q4_real = _load_q4_real_module()
    layout_path = HERE / "data" / "q4_joint_layout.json"
    if not layout_path.exists():
        raise FileNotFoundError(
            f"joint 臂需要离线布局 {layout_path}；先跑 "
            "`python3 -c \"from mathmodel2026b.joint_layout import JointLayoutOptimizer, "
            "JointLayoutParams; import json; r=JointLayoutOptimizer(JointLayoutParams()).run(); "
            "open('data/q4_joint_layout.json','w').write(json.dumps(r.as_dict()))\"`"
        )
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    points = [Point(float(x), float(y)) for x, y in layout["points"]]
    order = [int(i) for i in layout["order"]]
    return q4_real.JointRouteQ4Strategy(
        _matrix_params_directional(), points, order
    )


def _matrix_params_directional():
    from mathmodel2026b.strategy_matrix import MatrixParams

    return MatrixParams(directional=True)


def _load_q4_real_module():
    """把同目录的 ``_bench_q4_real.py`` 当模块加载（它本身可直接执行）。"""
    import importlib.util

    if "_bench_q4_real" in sys.modules:
        return sys.modules["_bench_q4_real"]
    spec = importlib.util.spec_from_file_location(
        "_bench_q4_real", HERE / "_bench_q4_real.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError("无法加载 _bench_q4_real.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_bench_q4_real"] = module
    spec.loader.exec_module(module)
    return module


#: ``mode`` 为 ``omni``（问题3）/ ``dir``（问题4）/ ``both``。
ARMS: dict[str, tuple[str, object]] = {
    # 框架基线
    "q3": ("omni", lambda: Q3Strategy(Q3Params())),
    # 问题3 历代
    "v5": ("omni", _q3_v5),
    "v6": ("omni", _q3_v6),
    "v7": ("omni", _q3_v7),
    "v8": ("omni", _q3_v8),
    "v15": ("omni", _q3_v15),
    # 问题4 历代
    "q4-v8": ("dir", _q4_v8),
    "v9": ("dir", _q4_v9),
    "v14": ("dir", _q4_v14),
    # 问题4 其他路线（对照）
    "matrix-polygon": ("dir", _matrix_polygon),
    "matrix-axial": ("dir", _matrix_axial),
    "joint": ("dir", _joint_layout),
    "paper-q4": ("dir", _paper_q4),
}

#: 每个 mode 的默认臂顺序（从最早到最新）。
DEFAULT_ARMS = {
    "omni": ["q3", "v5", "v6", "v7", "v8", "v15"],
    "dir": ["q4-v8", "v9", "v14", "matrix-axial", "joint", "paper-q4"],
}


def parse_seeds(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.extend(range(int(lo), int(hi) + 1))
        elif part:
            out.append(int(part))
    return sorted(set(out))


def run_one(
    seed: int,
    which: str,
    *,
    directional: bool,
    n_directional: int | None = None,
) -> dict:
    case = generate_case(seed, omni_only=not directional, n_directional=n_directional)
    with MockSimulator(robot_id=ROBOT, case=case) as sim:
        client = HttpSimulatorClient(robot_id=ROBOT, base_url=sim.base_url, verbose=False)
        state = DogState()
        strategy = ARMS[which][1]()
        client.enter()
        started = time.perf_counter()
        error = None
        try:
            strategy.run(client, state)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        wall = time.perf_counter() - started
        try:
            client.exit()
        except Exception:  # noqa: BLE001
            pass
        summary = sim.world.summary()
        return {
            "seed": seed,
            "error": error,
            "cleared": summary["cleared_count"],
            "n": summary["n_jammers"],
            "vt": summary["virtual_time_s"],
            "avg": summary["average_clear_time_s"],
            "dist": summary["move_distance_m"],
            "meas": summary["measure_count"],
            "wall": wall,
            "uncleared": summary["uncleared_channels"],
        }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["omni", "dir"], default="omni")
    ap.add_argument("--seeds", default="1-30")
    ap.add_argument("--n-directional", type=int, default=None)
    ap.add_argument("--only", default=None, help="逗号分隔的臂名；默认按 DEFAULT_ARMS")
    ap.add_argument("--list", action="store_true", help="列出所有臂后退出")
    ap.add_argument("--quiet", action="store_true", help="只打印汇总表")
    args = ap.parse_args()

    if args.list:
        print(f"{'arm':<16}{'mode':<8}说明")
        for name, (mode, _) in ARMS.items():
            print(f"{name:<16}{mode:<8}")
        return 0

    seeds = parse_seeds(args.seeds)
    directional = args.mode == "dir"
    names = args.only.split(",") if args.only else DEFAULT_ARMS[args.mode]
    for name in names:
        if name not in ARMS:
            raise SystemExit(f"未知臂：{name}（可用：{', '.join(ARMS)}）")

    results: dict[str, list[dict]] = {}
    for which in names:
        rows: list[dict] = []
        for seed in seeds:
            row = run_one(
                seed, which, directional=directional, n_directional=args.n_directional
            )
            rows.append(row)
            if not args.quiet:
                print(
                    f"  [{which:14s}] seed={seed:<3} clear={row['cleared']:>2}/{row['n']:<2} "
                    f"vt={row['vt']:8.0f} avg={(row['avg'] or 0):7.1f} "
                    f"dist={row['dist']:7.0f} meas={row['meas']:4d} wall={row['wall']:5.2f}"
                    + (f"  ERR={row['error']}" if row["error"] else ""),
                    flush=True,
                )
        results[which] = rows

    print(f"\n############ summary ({args.mode}, seeds={len(seeds)}, "
          f"n_directional={args.n_directional}) ############")
    print(f"{'arm':<15}{'full':>7}{'ratioMin':>10}{'vt_med':>10}{'avg_med':>10}"
          f"{'dist_med':>11}{'meas_med':>10}{'wall_med':>10}")
    for which, rows in results.items():
        ok = [r for r in rows if r["error"] is None]
        if not ok:
            print(f"{which:<15}  全部异常")
            continue
        full = f"{sum(1 for r in ok if r['cleared'] == r['n'])}/{len(ok)}"
        avgs = [r["avg"] for r in ok if r["avg"]]
        print(
            f"{which:<15}{full:>7}"
            f"{min(r['cleared'] / r['n'] for r in ok):>10.3f}"
            f"{statistics.median(r['vt'] for r in ok):>10.0f}"
            f"{(statistics.median(avgs) if avgs else float('nan')):>10.2f}"
            f"{statistics.median(r['dist'] for r in ok):>11.0f}"
            f"{statistics.median(r['meas'] for r in ok):>10.1f}"
            f"{statistics.median(r['wall'] for r in ok):>10.2f}"
        )

    for which, rows in results.items():
        bad = [r for r in rows if r["error"] is None and r["cleared"] < r["n"]]
        if bad:
            print(f"  {which} 未全清：" + ", ".join(
                f"{r['seed']}({r['cleared']}/{r['n']})" for r in bad))
        errs = [r for r in rows if r["error"]]
        if errs:
            print(f"  {which} 异常：" + "; ".join(
                f"seed{r['seed']}: {r['error']}" for r in errs[:4]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
