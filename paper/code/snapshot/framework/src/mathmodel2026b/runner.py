"""一次"进入场地 -> 执行策略 -> 退出"的完整运行编排（含日志与指标）。

``mode="mock"``
    在进程内起一个 :class:`~mathmodel2026b.mock.server.MockSimulator`，
    直接拿到案例真值，可以离线批量调参。
``mode="live"``
    连到真实 robot 端口（Windows 客户端 ``jammers-simulator.exe`` 已开始练习/正式
    测试后暴露的 ``127.0.0.1:2026``）。此时拿不到真值，只能记录可观测量。
两种模式下策略与 client 走的是同一条代码路径，因此 mock 上调好的参数可以直接搬到线上。
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from .client import (
    HttpSimulatorClient,
    RecordingClient,
    RobotPortGatedError,
    SimulatorClient,
    SimulatorError,
)
from .logging_utils import (
    RunLogger,
    default_log_root,
    environment_info,
    flatten_index_record,
    new_run_id,
    utc_now_iso,
)
from .mock.server import MockSimulator
from .mock.world import Case
from .state import DogState
from .strategy import Q3Params, Strategy, Q3Strategy

DEFAULT_ROBOT_URL = "http://127.0.0.1:2026"


@dataclass(slots=True)
class RunConfig:
    robot_id: str
    seed: int = 1
    mode: str = "mock"  # mock | live
    base_url: str | None = None
    n_jammers: int | None = None
    omni_only: bool = True
    case: Case | None = None
    params: Q3Params = field(default_factory=Q3Params)
    log_root: Path = field(default_factory=default_log_root)
    tag: str = "q3"
    write_trace: bool = True
    verbose: bool = False
    notes: dict[str, Any] = field(default_factory=dict)
    strategy_factory: Callable[[Q3Params], Strategy] | None = None

    def resolved_params(self) -> Q3Params:
        return self.params


@dataclass(slots=True)
class RunOutcome:
    run_id: str
    status: str  # ok | error | gated | timeout
    metrics: dict[str, Any]
    run_dir: Path
    error: str | None = None
    ground_truth: dict[str, Any] | None = None


def run_once(config: RunConfig) -> RunOutcome:
    params = config.resolved_params()
    logger = RunLogger(
        run_id=new_run_id(tag=config.tag, seed=config.seed),
        log_root=Path(config.log_root),
        tag=config.tag,
        write_trace=config.write_trace,
    )
    started_utc = utc_now_iso()
    started = time.time()
    status = "ok"
    error: str | None = None
    state = DogState()
    metrics: dict[str, Any] = {}
    truth: dict[str, Any] | None = None
    sim: MockSimulator | None = None
    strategy: Strategy | None = None
    client: SimulatorClient | None = None

    try:
        if config.mode == "mock":
            sim = MockSimulator(
                robot_id=config.robot_id,
                seed=config.seed,
                n_jammers=config.n_jammers,
                omni_only=config.omni_only,
                case=config.case,
                verbose=config.verbose,
            )
            sim.start()
            base_url = sim.base_url
            truth = sim.world.case.as_dict()
        elif config.mode == "live":
            base_url = config.base_url or DEFAULT_ROBOT_URL
        else:
            raise ValueError(f"未知 mode: {config.mode!r}")

        logger.write_meta(
            {
                "run_id": logger.run_id,
                "tag": config.tag,
                "mode": config.mode,
                "started_utc": started_utc,
                "robot_id": config.robot_id,
                "seed": config.seed,
                "n_jammers": config.n_jammers,
                "omni_only": config.omni_only,
                "base_url": base_url,
                "params": params.as_dict(),
                "notes": config.notes,
                "environment": environment_info(),
                "ground_truth": truth,
            }
        )
        logger.event(
            {
                "action": "run_start",
                "run_id": logger.run_id,
                "mode": config.mode,
                "robot_id": config.robot_id,
                "base_url": base_url,
                "seed": config.seed,
                "params": params.as_dict(),
            }
        )

        raw_client = HttpSimulatorClient(
            robot_id=config.robot_id, base_url=base_url, verbose=config.verbose
        )
        client = RecordingClient(raw_client, hook=logger.event)

        factory = config.strategy_factory or (lambda p: Q3Strategy(p))
        strategy = factory(params)

        enter = client.enter()
        logger.event(
            {
                "action": "enter_result",
                "accepted": enter.accepted,
                "max_virtual_duration_s": enter.max_virtual_duration_s,
                "max_real_duration_s": enter.max_real_duration_s,
                "remaining_real_duration_s": enter.remaining_real_duration_s,
            }
        )
        if not enter.accepted:
            status = "error"
            error = "enter 未被接受"
        else:
            state.enter_virtual_time_s = enter.virtual_time_s
            strategy.run(client, state)
    except RobotPortGatedError as exc:
        status = "gated"
        error = str(exc)
        logger.event({"action": "error", "kind": "port_gated", "message": str(exc)})
    except SimulatorError as exc:
        status = "error"
        error = str(exc)
        logger.event({"action": "error", "kind": "simulator", "message": str(exc)})
    except Exception as exc:  # noqa: BLE001 - 兜底，保证日志一定落盘
        status = "error"
        error = f"{type(exc).__name__}: {exc}"
        logger.event(
            {
                "action": "error",
                "kind": "unhandled",
                "message": error,
                "traceback": traceback.format_exc(),
            }
        )
    finally:
        # 无论成败都要尝试 /exit（除非端口已经关了）
        try:
            if client is not None:
                client.exit()
        except Exception:  # noqa: BLE001
            pass
        wall_s = time.time() - started

        # 策略内部决策轨迹落盘（便于逐腿分析路程构成）
        if strategy is not None:
            for index, record in enumerate(getattr(strategy, "trace", []) or []):
                logger.trace.write({"seq": index, **record})

        if sim is not None:
            authoritative = sim.world.summary()
            metrics = _metrics_from_world(authoritative, state, wall_s)
            truth = sim.world.case.as_dict()
            consistency = _consistency(state, sim.world)
            if consistency:
                metrics["consistency_issues"] = consistency
            sim.stop()
        else:
            metrics = _metrics_from_state(state, wall_s)

        if status == "ok" and params.max_wall_time_s and wall_s >= params.max_wall_time_s:
            status = "timeout"

        try:
            summary = {
                "run_id": logger.run_id,
                "tag": config.tag,
                "mode": config.mode,
                "status": status,
                "error": error,
                "started_utc": started_utc,
                "finished_utc": utc_now_iso(),
                "wall_s": wall_s,
                "metrics": metrics,
                "params": params.as_dict(),
                "state": state.snapshot(),
                "ground_truth": truth,
                "strategy_trace_len": len(getattr(strategy, "trace", []) or []),
            }
            logger.write_summary(summary)
            logger.append_index(
                flatten_index_record(
                    run_id=logger.run_id,
                    tag=config.tag,
                    mode=config.mode,
                    status=status,
                    started_utc=started_utc,
                    finished_utc=utc_now_iso(),
                    params={**params.as_dict(), "seed": config.seed, "mode": config.mode},
                    metrics=metrics,
                    error=error,
                    notes=config.notes,
                )
            )
        except Exception as exc:  # noqa: BLE001
            error = error or f"写日志失败: {exc}"
        finally:
            logger.close()

    return RunOutcome(
        run_id=logger.run_id,
        status=status,
        metrics=metrics,
        run_dir=logger.run_dir,
        error=error,
        ground_truth=truth,
    )


def _metrics_from_world(
    world_summary: Mapping[str, Any], state: DogState, wall_s: float
) -> dict[str, Any]:
    return {
        "cleared_count": world_summary["cleared_count"],
        "n_jammers": world_summary["n_jammers"],
        "cleared_ratio": world_summary["cleared_ratio"],
        "virtual_time_s": world_summary["virtual_time_s"],
        "average_clear_time_s": world_summary["average_clear_time_s"],
        "move_distance_m": world_summary["move_distance_m"],
        "measure_count": world_summary["measure_count"],
        "measure_accepted_count": state.measure_accepted_count,
        "clear_count": world_summary["clear_count"],
        "clear_success_count": state.clear_success_count,
        "clear_failure_count": state.clear_failure_count,
        "channel_switch_count": world_summary["channel_switch_count"],
        "scan_points_visited": state.scan_points_visited,
        "wall_s": wall_s,
        "cleared_channels": world_summary["cleared_channels"],
        "uncleared_channels": world_summary["uncleared_channels"],
        "state_virtual_time_s": state.virtual_time_s,
        "state_measure_count": state.measure_count,
        "state_move_distance_m": state.move_distance_m,
    }


def _metrics_from_state(state: DogState, wall_s: float) -> dict[str, Any]:
    cleared = len(state.cleared_channels)
    return {
        "cleared_count": cleared,
        # 线上演练测试只会在结束时由界面给出总数，robot API 拿不到
        "n_jammers": None,
        "cleared_ratio": None,
        "virtual_time_s": state.virtual_time_s,
        "average_clear_time_s": (state.virtual_time_s / cleared) if cleared else None,
        "move_distance_m": state.move_distance_m,
        "measure_count": state.measure_count,
        "measure_accepted_count": state.measure_accepted_count,
        "clear_count": state.clear_count,
        "clear_success_count": state.clear_success_count,
        "clear_failure_count": state.clear_failure_count,
        "channel_switch_count": state.channel_switch_count,
        "scan_points_visited": state.scan_points_visited,
        "wall_s": wall_s,
        "cleared_channels": state.cleared_channels,
        "uncleared_channels": state.open_channels,
    }


def _consistency(state: DogState, world: Any) -> list[str]:
    """本地计数与模拟器计数不一致时给出告警（用于发现协议/逻辑偏差）。"""
    issues: list[str] = []
    if state.measure_count != world.measure_count:
        issues.append(f"measure: state={state.measure_count} world={world.measure_count}")
    if state.clear_count != world.clear_count:
        issues.append(f"clear: state={state.clear_count} world={world.clear_count}")
    if state.channel_switch_count != world.channel_switch_count:
        issues.append(
            f"switch: state={state.channel_switch_count} world={world.channel_switch_count}"
        )
    if abs(state.move_distance_m - world.move_distance_m) > 1e-6:
        issues.append(
            f"move: state={state.move_distance_m:.3f} world={world.move_distance_m:.3f}"
        )
    return issues


def new_robot_id(tag: str = "mock") -> str:
    return f"{tag}-{uuid4().hex[:8]}"
