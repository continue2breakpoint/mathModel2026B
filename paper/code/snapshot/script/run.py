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
from mathmodel2026b.versioned import (  # noqa: E402
    DECISION_METHODS,
    available_methods,
    build_method,
)

#: 非版本化路线（各自独立的技术路线，不算"历代版本"）。
ROUTE_CHOICES = ("q3", "matrix", "paper-q4")
#: 版本化决策方法（"最终成品 + 阶段性有益尝试"），见 ``mathmodel2026b.versioned``。
#: 注意 ``q3`` 同时是 ROUTE_CHOICES 里的基线名，这里去重。
VERSION_CHOICES = tuple(k for k in available_methods() if k not in ROUTE_CHOICES)
STRATEGY_CHOICES = tuple(ROUTE_CHOICES) + VERSION_CHOICES

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
    """把 ``--param KEY=VALUE`` 应用到任意 dataclass 参数对象上。

    注意：不能只看 ``params.__slots__``——``@dataclass(slots=True)`` 的子类
    ``__slots__`` **只含自己声明的字段**，继承来的字段（例如
    ``Q3V15Params.schedule_min_readings`` 来自 ``Q3V8Params``）会漏掉，
    导致"明明合法的参数被判为未知"。``dataclasses.fields`` 会递归收集全部字段，
    因此优先用它。
    """
    from dataclasses import fields, is_dataclass

    if is_dataclass(params):
        valid = {field.name for field in fields(params)}
    else:
        valid = set(getattr(params, "__slots__", ()) or ())
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
    """构造策略参数对象。

    ``matrix`` 用 :class:`MatrixParams`；版本化方法（``q3-v5`` …）用各自
    ``*Params`` 并**先套上交付参数**（``versioned.DECISION_METHODS[..].overrides``），
    再叠加 ``--param`` —— 这样命令行只写"要改的那一项"，不必抄一遍交付配置。
    ``paper-q4`` 的内核自带全部几何与调度参数，框架侧只用到
    :class:`Q3Params` 里的 ``max_wall_time_s``（适配器的墙钟安全阀）与
    ``max_virtual_time_s``，因此复用默认 :class:`Q3Params`。
    """
    if strategy == "matrix":
        from mathmodel2026b.strategy_matrix import MatrixParams

        params: Any = MatrixParams(directional=directional)
    elif strategy in DECISION_METHODS:
        from mathmodel2026b.versioned import _resolve_ref  # noqa: PLC0415

        spec = DECISION_METHODS[strategy]
        params = _resolve_ref(spec.params_ref)(**spec.overrides)
    else:
        params = Q3Params()
    return apply_param_overrides(params, overrides)


def make_strategy_factory(strategy: str, directional: bool):
    """返回 ``RunConfig.strategy_factory`` 需要的可调用对象。

    runner 以 ``factory(params)`` 调用，其中 ``params`` 由
    :func:`build_strategy_params` 构造（已含交付参数 + ``--param``）。
    因此这里只需返回**策略类本身**；不要返回无参闭包，也不要在类里再改 params
    （曾因此丢掉 runner 传进来的参数，并触发 slotted dataclass 的 super() 校验错误）。
    """
    if strategy == "matrix":
        from mathmodel2026b.strategy_matrix import KnowledgeSearchStrategy, Q4Strategy

        cls = Q4Strategy if directional else KnowledgeSearchStrategy
        return cls
    if strategy == "paper-q4":
        # 队友论文内核的适配器（冻结内核见 ``paper_q4/``，勿就地修改）。
        # 该内核只解问题4（全向+定向混合），必须配 ``--directional`` 使用。
        from mathmodel2026b.strategy_q4 import Q4Strategy as PaperQ4Strategy

        return PaperQ4Strategy
    if strategy in DECISION_METHODS:
        from mathmodel2026b.versioned import _resolve_ref  # noqa: PLC0415

        return _resolve_ref(DECISION_METHODS[strategy].strategy_ref)
    from mathmodel2026b.strategy import Q3Strategy

    return Q3Strategy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行一次问题3/4 策略并记录日志")
    parser.add_argument("--mode", choices=["mock", "live"], default="mock")
    parser.add_argument(
        "--strategy",
        choices=STRATEGY_CHOICES,
        default="q3",
        help=(
            "路线：q3=框架基线 Q3Strategy；matrix=知识矩阵；paper-q4=队友论文 Q4 内核"
            "（需配 --directional）。"
            "版本化方法（见 script/list_methods.py 或 --list-methods）："
            "q3-v5/v6/v7/v8/v15=问题3 历代；q4-v8/v9/v14=问题4 历代。"
            "推荐上线组合：--strategy q3-v15（问题3）/ --strategy q4-v14 --directional（问题4）"
        ),
    )
    parser.add_argument(
        "--list-methods",
        action="store_true",
        help="打印全部可选决策方法（含新增机制与实测效果）后退出",
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
    if args.list_methods:
        from mathmodel2026b.versioned import describe_methods

        print(describe_methods(verbose=True))
        return 0
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
