"""运行一次问题3 策略。

示例::

    # 离线 mock（无 Windows 客户端也能跑通整条链路）
    python scripts/run_q3.py --mode mock --seed 7 --param scan_radius=1400

    # 连真实 robot 端口（客户端已开始练习/正式测试）
    python scripts/run_q3.py --mode live --robot-id <队号>

日志默认写到 ``<mathModel2026B>/logs/``，跨 run 索引为 ``logs/results.jsonl``。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mathmodel2026b.logging_utils import default_log_root  # noqa: E402
from mathmodel2026b.runner import RunConfig, run_once  # noqa: E402
from mathmodel2026b.strategy import Q3Params  # noqa: E402


def _coerce(value: str):
    low = value.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            continue
    if "," in value:
        parts = [_coerce(v) for v in value.split(",") if v != ""]
        if all(isinstance(p, (int, float)) and not isinstance(p, bool) for p in parts):
            return tuple(parts)
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行问题3 策略并记录结构化日志")
    parser.add_argument("--mode", choices=["mock", "live"], default="mock")
    parser.add_argument("--robot-id", default=None, help="必须等于登录队号")
    parser.add_argument("--base-url", default=None, help="robot 端口，默认 http://127.0.0.1:2026")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--n-jammers", type=int, default=None)
    parser.add_argument("--directional", action="store_true", help="含定向源（问题4）")
    parser.add_argument("--log-root", default=None)
    parser.add_argument("--tag", default="q3")
    parser.add_argument("--no-trace", action="store_true", help="不写策略决策轨迹")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="覆盖 Q3Params 字段，可重复，例如 --param scan_radius=1500",
    )
    return parser


def apply_params(params: Q3Params, overrides: list[str]) -> Q3Params:
    valid = set(Q3Params.__slots__)
    for item in overrides:
        if "=" not in item:
            raise SystemExit(f"--param 需要 KEY=VALUE 形式：{item!r}")
        key, raw = item.split("=", 1)
        key = key.strip()
        if key not in valid:
            raise SystemExit(f"未知参数 {key!r}；可用：{', '.join(sorted(valid))}")
        setattr(params, key, _coerce(raw))
    return params


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    params = apply_params(Q3Params(), args.param)
    robot_id = args.robot_id
    if robot_id is None:
        robot_id = "000000000000" if args.mode == "mock" else None
        if robot_id is None:
            print("live 模式必须提供 --robot-id（= 登录队号）", file=sys.stderr)
            return 2

    config = RunConfig(
        robot_id=robot_id,
        seed=args.seed,
        mode=args.mode,
        base_url=args.base_url,
        n_jammers=args.n_jammers,
        omni_only=not args.directional,
        params=params,
        log_root=Path(args.log_root) if args.log_root else default_log_root(),
        tag=args.tag,
        write_trace=not args.no_trace,
        verbose=args.verbose,
    )
    outcome = run_once(config)
    print(json.dumps(
        {
            "run_id": outcome.run_id,
            "status": outcome.status,
            "error": outcome.error,
            "run_dir": str(outcome.run_dir),
            "metrics": outcome.metrics,
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    ))
    return 0 if outcome.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
