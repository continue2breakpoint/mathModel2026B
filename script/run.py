#!/usr/bin/env python3
"""跑**一次**问题3 策略，并写入结构化日志。

robot_id（= 登录队号）默认从 login-jammers 的账号配置里读，避免写死队号；
也可以用 ``--robot-id`` 或环境变量 ``JAMMERS_TEAM_NO`` 覆盖。

用法::

    # 离线 mock（默认）：不需要 Windows 客户端
    python script/run.py --seed 7

    # 连真实 robot 端口（客户端已开始练习/正式测试）
    python script/run.py --mode live

    # 调参
    python script/run.py --seed 7 --param scan_radius=1500 --param travel_weight=0.0008

日志位置：``mathModel2026B/logs/runs/<run_id>/``，索引 ``mathModel2026B/logs/results.jsonl``。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
FRAMEWORK_SRC = REPO_ROOT / "framework" / "src"
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(FRAMEWORK_SRC))

from jammers_paths import PathResolutionError, resolve  # noqa: E402
from mathmodel2026b.logging_utils import default_log_root  # noqa: E402
from mathmodel2026b.runner import RunConfig, run_once  # noqa: E402
from mathmodel2026b.strategy import Q3Params  # noqa: E402

STRATEGY_CHOICES = ("q3", "matrix")

#: 离线 mock 用的占位队号。**不要在这里写死真实队号**——平台公告明确要求
#: 提交代码时隐去队号，改为命令行参数/配置文件提供。
PLACEHOLDER_TEAM_NO = "000000000000"

DEFAULT_ROBOT_PORT = int(os.environ.get("JAMMERS_ROBOT_PORT", "2026"))
DEFAULT_ROBOT_URL = f"http://127.0.0.1:{DEFAULT_ROBOT_PORT}"


def coerce(value: str):
    low = value.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    if "," in value:
        parts = [coerce(v) for v in value.split(",") if v != ""]
        if all(isinstance(p, (int, float)) and not isinstance(p, bool) for p in parts):
            return tuple(parts)
    return value


def apply_param_overrides(params: Any, overrides: list[str]) -> Any:
    """把 ``--param KEY=VALUE`` 应用到任意 slotted dataclass 参数对象上。"""
    valid = set(getattr(params, "__slots__", ()) or ())
    if not valid:
        from dataclasses import fields

        valid = {field.name for field in fields(params)}
    for item in overrides:
        if "=" not in item:
            raise SystemExit(f"--param 需要 KEY=VALUE 形式：{item!r}")
        key, raw = item.split("=", 1)
        key = key.strip()
        if key not in valid:
            raise SystemExit(
                f"未知参数 {key!r}；可用：{', '.join(sorted(valid))}"
            )
        value = coerce(raw)
        current = getattr(params, key)
        # 保持元组字段的元素类型（scan_channels / approach_radii）
        if isinstance(current, tuple) and isinstance(value, (int, float)):
            value = (value,)
        setattr(params, key, value)
    return params


def build_strategy_params(strategy: str, directional: bool, overrides: list[str]) -> Any:
    """构造策略参数对象（``q3`` 或 ``matrix``）。"""
    if strategy == "matrix":
        from mathmodel2026b.strategy_matrix import MatrixParams

        params: Any = MatrixParams(directional=directional)
    else:
        params = Q3Params()
    return apply_param_overrides(params, overrides)


def make_strategy_factory(strategy: str, directional: bool):
    """返回 ``RunConfig.strategy_factory`` 需要的可调用对象。"""
    if strategy == "matrix":
        from mathmodel2026b.strategy_matrix import KnowledgeSearchStrategy, Q4Strategy

        cls = Q4Strategy if directional else KnowledgeSearchStrategy
        return cls
    from mathmodel2026b.strategy import Q3Strategy

    return Q3Strategy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行一次问题3/4 策略并记录日志")
    parser.add_argument("--mode", choices=["mock", "live"], default="mock")
    parser.add_argument(
        "--strategy",
        choices=STRATEGY_CHOICES,
        default="q3",
        help="q3=旧 Q3Strategy（兼容默认）；matrix=知识矩阵 KnowledgeSearchStrategy",
    )
    parser.add_argument("--robot-id", default=None, help="默认取 login-jammers 配置里的队号")
    parser.add_argument("--login-jammers", default=None, help="login-jammers 仓库根目录")
    parser.add_argument("--base-url", default=None, help=f"robot 端口，默认 {DEFAULT_ROBOT_URL}")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--n-jammers", type=int, default=None)
    parser.add_argument("--directional", action="store_true", help="含定向源（问题4）")
    parser.add_argument("--log-root", default=None)
    parser.add_argument("--tag", default="q3")
    parser.add_argument("--no-trace", action="store_true")
    parser.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="只打印一行摘要")
    return parser


def resolve_robot_id(explicit: str | None, login_jammers: str | None) -> str:
    """队号来源：命令行 > login-jammers 配置/会话 > 占位队号（仅 mock 有意义）。"""
    if explicit:
        return explicit
    try:
        paths = resolve(login_jammers, None)
    except PathResolutionError:
        return PLACEHOLDER_TEAM_NO
    return paths.team_no() or PLACEHOLDER_TEAM_NO


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    params = build_strategy_params(args.strategy, args.directional, args.param)
    strategy_factory = make_strategy_factory(args.strategy, args.directional)
    robot_id = resolve_robot_id(args.robot_id, args.login_jammers)
    if args.mode == "live" and robot_id == PLACEHOLDER_TEAM_NO:
        print(
            "live 模式必须提供真实队号（robot_id 必须等于登录队号）：\n"
            "  · --robot-id <队号>\n"
            "  · 或设置 JAMMERS_TEAM_NO\n"
            "  · 或在 login-jammers/linux-client/config.json 里填 team_no",
            file=sys.stderr,
        )
        return 2

    config = RunConfig(
        robot_id=robot_id,
        seed=args.seed,
        mode=args.mode,
        base_url=args.base_url or (DEFAULT_ROBOT_URL if args.mode == "live" else None),
        n_jammers=args.n_jammers,
        omni_only=not args.directional,
        params=params,
        log_root=Path(args.log_root) if args.log_root else default_log_root(),
        tag=args.tag,
        write_trace=not args.no_trace,
        verbose=args.verbose,
        notes={"strategy": args.strategy, "directional": args.directional},
        strategy_factory=strategy_factory,
    )
    outcome = run_once(config)
    metrics = outcome.metrics
    if args.quiet:
        print(
            f"{outcome.status} run={outcome.run_id} "
            f"cleared={metrics.get('cleared_count')}/{metrics.get('n_jammers')} "
            f"avg_s={metrics.get('average_clear_time_s')} "
            f"virtual_s={metrics.get('virtual_time_s')}"
        )
    else:
        print(
            json.dumps(
                {
                    "run_id": outcome.run_id,
                    "status": outcome.status,
                    "error": outcome.error,
                    "run_dir": str(outcome.run_dir),
                    "metrics": metrics,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
    return 0 if outcome.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
