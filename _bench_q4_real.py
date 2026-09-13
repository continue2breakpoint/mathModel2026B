#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用“官方真实 p4 案例画像”驱动本地 Q4 mock 回归。

问题
----
``日志4/*.jlog`` 是官方模拟器导出的**加密行为包**：正文被 gzip + AES-256-GCM
分块加密，DEK 又被 RSA-OAEP 包给服务端公钥。本地模拟器只有公钥，因此没有服务端
wrap 私钥时无法从日志直接恢复真实坐标、半径和定向方向。可以可靠提取的是：

* 每个 case 的 case_code / practice_run_no / 时间戳；
* 加密前压缩长度、帧数、key id；
* 配合官方 simulator 的 ``*.result.json`` 和 ``practice-statistics-queue.sqlite3``，
  可得到**真实案例画像**：源数、定向源数、最终清除数、虚拟时间、检测次数等。

本脚本把后一类真实画像变成可复现的本地测试环境：

1. 自动从历史官方数据中发现 p4 真实画像；
2. 用 ``case_code`` 做稳定哈希种子，生成**同源数 / 同定向源数**的本地 mock 案例；
3. 用与线上完全相同的 client / strategy 路径运行；
4. 报告本地全清率、虚拟时间、检测次数、清失败次数、路程和墙钟时间。

注意：本地案例的位置/半径/朝向仍是按题面分布生成的代理真值，不等于官方隐藏案例；
它的作用是覆盖“真实案例画像”，而不是伪造“真实坐标”。

用法
----
::

    # 直接跑硬编码的真实画像回退集
    python _bench_q4_real.py

    # 自动发现历史官方 result.json / sqlite
    python _bench_q4_real.py --discover

    # 把 日志4/ 中的未知 case 按真实画像分布生成代理案例
    python _bench_q4_real.py --jlog-dir 日志4 --all-profiles

    # 显式指定 matrix 参数
    python _bench_q4_real.py --param scan_layout=axial --param max_wall_time_s=900
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "framework" / "src"))
sys.path.insert(0, str(HERE))

from mathmodel2026b.client import HttpSimulatorClient  # noqa: E402
from mathmodel2026b.geometry import Point  # noqa: E402
from mathmodel2026b.mock.server import MockSimulator  # noqa: E402
from mathmodel2026b.mock.world import Case, generate_case  # noqa: E402
from mathmodel2026b.state import DogState  # noqa: E402
from mathmodel2026b.strategy_matrix import MatrixParams, Q4Strategy  # noqa: E402
from tools.jlog_inspect import parse_envelope  # noqa: E402

ROBOT = "000000000000"
DEFAULT_REAL_DATA_ROOT = (
    HERE.parent
    / "_scratch_b_full"
    / "B题"
    / "CUMCM2026B"
    / "Jammers-simulator-win64"
    / "Jammers-simulator"
    / "JammersSimulatorData"
)


@dataclass(slots=True)
class RealCase:
    """一条历史官方 p4 记录（画像 + 可选统计）。"""

    case_code: str
    n_jammers: int
    n_directional: int
    source: str = "fallback"
    official_cleared: int | None = None
    official_virtual_time_s: float | None = None
    official_measure_accepted: int | None = None
    official_clear_failures: int | None = None
    official_program_run_ms: int | None = None

    @property
    def n_omni(self) -> int:
        return self.n_jammers - self.n_directional

    @property
    def directional_ratio(self) -> float:
        return self.n_directional / self.n_jammers if self.n_jammers else 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class JointRouteQ4Strategy(Q4Strategy):
    """使用离线联合优化的固定检测点集/顺序，其余定位清除逻辑仍用 matrix Q4。"""

    def __init__(
        self,
        params: MatrixParams,
        layout_points: list[Point],
        layout_order: list[int],
    ) -> None:
        super().__init__(params)
        self._layout_points = layout_points
        self._layout_order = layout_order

    def _scan_route(self) -> list[Point]:  # noqa: D102 - 覆写父类
        return [self._layout_points[i] for i in self._layout_order]


#: 回退画像：来自历史官方 p4 演练的 result.json + practice-statistics-queue.sqlite3。
#: 没带坐标，因为官方日志正文加密；这里只保留可靠维度。
FALLBACK_REAL_P4: tuple[RealCase, ...] = (
    RealCase(
        "KAFK-P4MP-YF32-WHQM", 10, 10, "fallback/history",
        official_cleared=10, official_virtual_time_s=17_863.673544,
        official_measure_accepted=881, official_clear_failures=0,
        official_program_run_ms=4370,
    ),
    RealCase(
        "VZ52-TJUU-FABK-PSCW", 16, 8, "fallback/history",
        official_cleared=16, official_virtual_time_s=18_342.808149,
        official_measure_accepted=838, official_clear_failures=0,
        official_program_run_ms=4001,
    ),
    RealCase(
        "9W7G-WEAE-9PPW-JWXC", 11, 9, "fallback/history",
        official_cleared=11, official_virtual_time_s=19_540.591317,
        official_measure_accepted=913, official_clear_failures=0,
        official_program_run_ms=4758,
    ),
    RealCase(
        "9T86-MU58-WMKX-E42B", 13, 2, "fallback/history",
        official_cleared=13, official_virtual_time_s=13_671.000692,
        official_measure_accepted=656, official_clear_failures=0,
        official_program_run_ms=3111,
    ),
    RealCase(
        "BM8Z-7J83-3CGE-88WV", 15, 14, "fallback/history",
        official_cleared=15, official_virtual_time_s=25_661.383487,
        official_measure_accepted=1134, official_clear_failures=0,
        official_program_run_ms=5774,
    ),
)


def stable_seed(*parts: object, salt: str = "") -> int:
    payload = "|".join([salt, *(str(p) for p in parts)]).encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], "big") % (2**31 - 1)


def discover_real_cases(root: Path = DEFAULT_REAL_DATA_ROOT) -> list[RealCase]:
    """从历史官方 simulator 数据发现 p4 画像。

    兼容两种来源：
    * ``behavior-logs/*.result.json``：直接给 n_jammers / directional_jammer_count；
    * ``practice-statistics-queue.sqlite3``：补充 official_cleared / virtual_time /
      measure_accepted / clear_failure_count（以 result.json 的 case_code 为键）。
    """
    by_code: dict[str, RealCase] = {}
    behavior_logs = root / "behavior-logs"
    if behavior_logs.is_dir():
        for result_path in sorted(behavior_logs.glob("practice-p4-*.result.json")):
            try:
                data = json.loads(result_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if data.get("problem_no") != 4:
                continue
            code = str(data.get("case_code") or result_path.stem)
            n = int(data.get("jammer_count") or 0)
            n_dir = int(data.get("directional_jammer_count") or 0)
            if n <= 0:
                continue
            by_code[code] = RealCase(
                case_code=code,
                n_jammers=n,
                n_directional=n_dir,
                source=str(result_path),
            )

    stats_db = root / "practice-statistics-queue.sqlite3"
    if stats_db.exists():
        try:
            con = sqlite3.connect(str(stats_db))
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT * FROM practice_statistics_tasks WHERE problem_no = 4 ORDER BY practice_run_no"
            ).fetchall()
            con.close()
        except Exception:  # noqa: BLE001
            rows = []
        for row in rows:
            code = str(row["case_code"])
            rc = by_code.get(code)
            if rc is None:
                continue
            rc.official_cleared = int(row["cleared_jammer_count"])
            rc.official_virtual_time_s = float(row["virtual_time_us"]) / 1_000_000.0
            rc.official_measure_accepted = int(row["measure_accepted_count"])
            rc.official_clear_failures = int(row["clear_failure_count"])
            rc.official_program_run_ms = (
                int(row["program_run_duration_ms"])
                if row["program_run_duration_ms"] is not None
                else None
            )
    return list(by_code.values())


def cases_for_jlog_dir(jlog_dir: Path, profiles: list[RealCase], all_profiles: bool) -> list[RealCase]:
    """把加密日志映射成可复现的本地代理画像。

    优先读取同名的 ``<stem>.result.json``。没有则用 case_code 稳定地选一个历史真实
    画像；``--all-profiles`` 时每个日志×每个画像都生成一个代理案例。
    """
    out: list[RealCase] = []
    for p in sorted(jlog_dir.glob("*.jlog")):
        try:
            env = parse_envelope(p)
        except Exception:  # noqa: BLE001
            continue
        code = str(env.header_json.get("case_code") or p.stem)
        result_path = p.parent / f"{p.stem}.result.json"
        if result_path.exists():
            data = json.loads(result_path.read_text(encoding="utf-8"))
            n = int(data.get("jammer_count") or 0)
            n_dir = int(data.get("directional_jammer_count") or 0)
            if n > 0:
                out.append(RealCase(code, n, n_dir, source=str(result_path)))
                continue
        if not profiles:
            # 最保守的缺省：10~16 个源全为定向；它比混合/全向更难。
            n = 10 + stable_seed(code) % 7
            out.append(RealCase(code, n, n, source=f"jlog-proxy/all-dir:{p.name}"))
            continue
        chosen = profiles if all_profiles else [profiles[stable_seed(code) % len(profiles)]]
        for idx, profile in enumerate(chosen):
            out.append(
                RealCase(
                    case_code=code,
                    n_jammers=profile.n_jammers,
                    n_directional=profile.n_directional,
                    source=f"jlog-proxy:{p.name}~{profile.case_code}",
                )
            )
    return out


def parse_params(items: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--param 需要 KEY=VALUE 形式：{item!r}")
        key, raw = item.split("=", 1)
        low = raw.lower()
        if low in ("true", "false"):
            value: Any = low == "true"
        else:
            try:
                value = int(raw)
            except ValueError:
                try:
                    value = float(raw)
                except ValueError:
                    value = raw
        out[key] = value
    return out


def make_local_case(real: RealCase, *, salt: str, index: int) -> Case:
    seed = stable_seed(real.case_code, real.n_jammers, real.n_directional, index, salt=salt)
    return generate_case(
        seed,
        n_jammers=real.n_jammers,
        omni_only=False,
        n_directional=real.n_directional,
    )


def run_one(
    real: RealCase,
    *,
    index: int,
    strategy_name: str,
    salt: str,
    params: dict[str, Any],
    joint_layout_path: Path | None = None,
) -> dict[str, Any]:
    case = make_local_case(real, salt=salt, index=index)
    with MockSimulator(robot_id=ROBOT, case=case) as sim:
        client = HttpSimulatorClient(robot_id=ROBOT, base_url=sim.base_url, verbose=False)
        state = DogState()
        if strategy_name == "matrix":
            defaults = {"directional": True, "scan_layout": "axial"}
            defaults.update(params)
            strategy = Q4Strategy(MatrixParams(**defaults))
        elif strategy_name == "paper":
            from mathmodel2026b.strategy_q4 import Q4Strategy as PaperQ4Strategy  # noqa: PLC0415
            from mathmodel2026b.strategy import Q3Params  # noqa: PLC0415

            strategy = PaperQ4Strategy(Q3Params(**params))
        elif strategy_name == "joint":
            if joint_layout_path is None or not joint_layout_path.exists():
                raise FileNotFoundError(
                    f"joint 策略需要已生成的布局 JSON：{joint_layout_path}"
                )
            layout = json.loads(joint_layout_path.read_text(encoding="utf-8"))
            layout_points = [Point(float(x), float(y)) for x, y in layout["points"]]
            layout_order = [int(i) for i in layout["order"]]
            defaults = {"directional": True}
            defaults.update(params)
            strategy = JointRouteQ4Strategy(
                MatrixParams(**defaults), layout_points, layout_order
            )
        else:
            raise ValueError(f"未知 strategy: {strategy_name}")

        client.enter()
        started = time.perf_counter()
        error: str | None = None
        try:
            strategy.run(client, state)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        wall_time_s = time.perf_counter() - started
        try:
            client.exit()
        except Exception:  # noqa: BLE001
            pass
        summary = sim.world.summary()
        odo = strategy.odometer() if hasattr(strategy, "odometer") else {}
        return {
            "case_code": real.case_code,
            "proxy_source": real.source,
            "n_jammers": real.n_jammers,
            "n_directional": real.n_directional,
            "n_omni": real.n_omni,
            "directional_ratio": real.directional_ratio,
            "local_seed": case.seed,
            "error": error,
            "cleared": summary["cleared_count"],
            "full_clear": summary["cleared_count"] == summary["n_jammers"],
            "virtual_time_s": summary["virtual_time_s"],
            "average_clear_time_s": summary["average_clear_time_s"],
            "move_distance_m": summary["move_distance_m"],
            "measure_count": summary["measure_count"],
            "clear_count": summary["clear_count"],
            "clear_failure_count": summary["clear_count"] - summary["cleared_count"],
            "channel_switch_count": summary["channel_switch_count"],
            "wall_time_s": wall_time_s,
            "uncleared_channels": summary["uncleared_channels"],
            "odometer": odo,
            "official_cleared": real.official_cleared,
            "official_virtual_time_s": real.official_virtual_time_s,
            "official_measure_accepted": real.official_measure_accepted,
        }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if r["error"] is None]
    out: dict[str, Any] = {
        "runs": len(rows),
        "errors": len(rows) - len(ok),
        "full_clear": sum(1 for r in ok if r["full_clear"]),
    }
    if not ok:
        return out
    out.update(
        {
            "cleared_ratio_mean": statistics.fmean(r["cleared"] / r["n_jammers"] for r in ok),
            "virtual_time_median_s": statistics.median(r["virtual_time_s"] for r in ok),
            "virtual_time_max_s": max(r["virtual_time_s"] for r in ok),
            "move_distance_median_m": statistics.median(r["move_distance_m"] for r in ok),
            "measure_count_median": statistics.median(r["measure_count"] for r in ok),
            "clear_failure_median": statistics.median(r["clear_failure_count"] for r in ok),
            "wall_time_median_s": statistics.median(r["wall_time_s"] for r in ok),
        }
    )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="真实 p4 画像驱动的本地 Q4 回归")
    ap.add_argument("--discover", action="store_true", help="从历史官方 simulator 数据发现真实画像")
    ap.add_argument("--jlog-dir", type=Path, default=None, help="日志4/ 目录：为每个加密 case 生成代理画像")
    ap.add_argument("--all-profiles", action="store_true", help="每个日志×每个历史画像都跑")
    ap.add_argument("--strategy", choices=["matrix", "paper", "joint"], default="matrix")
    ap.add_argument(
        "--joint-layout",
        type=Path,
        default=HERE / "data" / "q4_joint_layout.json",
        help="joint 策略使用的离线联合优化布局 JSON",
    )
    ap.add_argument("--param", action="append", default=[], help="策略参数覆盖 key=value")
    ap.add_argument("--salt", default="q4-real-v1", help="本地 case 生成盐，改变它可换一批代理真值")
    ap.add_argument("--out", type=Path, default=HERE / "logs" / "q4_real_suite", help="输出目录")
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 个")
    ap.add_argument("--list", action="store_true", help="只打印画像，不运行")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    profiles: list[RealCase] = []
    if args.discover:
        profiles = discover_real_cases()
    if not profiles:
        profiles = list(FALLBACK_REAL_P4)

    jobs: list[RealCase] = []
    if args.jlog_dir is not None:
        jobs = cases_for_jlog_dir(args.jlog_dir, profiles, all_profiles=args.all_profiles)
    else:
        jobs = list(profiles)
    if args.limit is not None:
        jobs = jobs[: args.limit]

    if args.list:
        for i, j in enumerate(jobs):
            print(
                f"{i:02d} {j.case_code:32s} n={j.n_jammers:2d} dir={j.n_directional:2d} "
                f"ratio={j.directional_ratio:.2f} source={j.source}"
            )
        return 0

    params = parse_params(args.param)
    rows: list[dict[str, Any]] = []
    for i, real in enumerate(jobs):
        row = run_one(
            real,
            index=i,
            strategy_name=args.strategy,
            salt=args.salt,
            params=params,
            joint_layout_path=args.joint_layout,
        )
        rows.append(row)
        if not args.quiet:
            print(
                f"[{i:02d}] {real.case_code:32s} n={real.n_jammers:2d} dir={real.n_directional:2d} "
                f"clear={row['cleared']:2d}/{row['n_jammers']:<2d} "
                f"vt={row['virtual_time_s']:9.1f}s dist={row['move_distance_m']:7.0f}m "
                f"meas={row['measure_count']:4d} fail={row['clear_failure_count']:2d} "
                f"wall={row['wall_time_s']:5.1f}s"
                + (f" ERR={row['error']}" if row["error"] else ""),
                flush=True,
            )

    summary = summarize(rows)
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    json_path = args.out / f"q4_real_{stamp}.json"
    json_path.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    csv_path = args.out / f"q4_real_{stamp}.csv"
    if rows:
        keys = [
            "case_code", "n_jammers", "n_directional", "n_omni", "directional_ratio",
            "local_seed", "cleared", "full_clear", "virtual_time_s", "average_clear_time_s",
            "move_distance_m", "measure_count", "clear_count", "clear_failure_count",
            "channel_switch_count", "wall_time_s", "uncleared_channels", "error",
            "official_cleared", "official_virtual_time_s", "official_measure_accepted",
        ]
        with csv_path.open("w", newline="", encoding="utf-8") as fp:
            writer = csv.DictWriter(fp, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    print("\n===== summary =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\njson: {json_path}\ncsv : {csv_path}")
    return 0 if summary.get("errors", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
