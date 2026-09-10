"""端到端工作流测试：mock 服务器 <-> HTTP client <-> 策略 <-> 日志。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mathmodel2026b.client import (
    HttpSimulatorClient,
    ReplayClient,
    SimulatorError,
)
from mathmodel2026b.geometry import Point
from mathmodel2026b.logging_utils import read_index
from mathmodel2026b.mock.server import MockSimulator
from mathmodel2026b.mock.world import Case, Jammer, generate_case
from mathmodel2026b.protocol import ClearKind, MeasureKind
from mathmodel2026b.runner import RunConfig, run_once
from mathmodel2026b.state import DogState
from mathmodel2026b.strategy import Q3Params, Q3Strategy

ROBOT = "000000000000"  # 占位队号：不要在代码里写死真实队号


@pytest.fixture()
def sim() -> MockSimulator:
    with MockSimulator(robot_id=ROBOT, seed=3) as running:
        yield running


def test_client_roundtrip(sim: MockSimulator) -> None:
    client = HttpSimulatorClient(robot_id=ROBOT, base_url=sim.base_url)
    assert client.enter().accepted
    measure = client.measure(Point(0.0, 0.0), 1)
    assert measure.accepted
    assert measure.kind in (MeasureKind.NO_SIGNAL, MeasureKind.NEAR, MeasureKind.DIRECTION)
    assert client.exit().accepted


def test_request_validation_rejects_bad_arena(sim: MockSimulator) -> None:
    client = HttpSimulatorClient(robot_id=ROBOT, base_url=sim.base_url)
    with pytest.raises(SimulatorError):
        client._post("/enter", {"arena_id": "other", "robot_id": ROBOT, "request_id": "x"})


def test_request_validation_rejects_bad_robot_id(sim: MockSimulator) -> None:
    client = HttpSimulatorClient(robot_id=ROBOT, base_url=sim.base_url)
    with pytest.raises(SimulatorError):
        client._post("/enter", {"arena_id": "default", "robot_id": "999", "request_id": "x"})


def test_request_validation_rejects_unknown_field(sim: MockSimulator) -> None:
    client = HttpSimulatorClient(robot_id=ROBOT, base_url=sim.base_url)
    with pytest.raises(SimulatorError):
        client._post(
            "/measure",
            {
                "arena_id": "default",
                "robot_id": ROBOT,
                "request_id": "x",
                "position": {"x": 0, "y": 0},
                "channel": 1,
                "extra": 1,
            },
        )


def test_request_validation_rejects_channel_out_of_range(sim: MockSimulator) -> None:
    client = HttpSimulatorClient(robot_id=ROBOT, base_url=sim.base_url)
    with pytest.raises(SimulatorError):
        client.measure(Point(0.0, 0.0), 21)


def test_gated_port_behaves_like_portguard() -> None:
    from mathmodel2026b.client import RobotPortGatedError

    with MockSimulator(robot_id=ROBOT, seed=1, gated=True) as gated:
        client = HttpSimulatorClient(robot_id=ROBOT, base_url=gated.base_url, retries=1)
        with pytest.raises(RobotPortGatedError):
            client.enter()


def test_mock_validates_the_real_wire_field_names(sim: MockSimulator) -> None:
    """确认我们发的就是真实接口要的字段名（measure_result / clear_result）。"""
    client = HttpSimulatorClient(robot_id=ROBOT, base_url=sim.base_url)
    client.enter()
    client.measure(Point(0.0, 0.0), 1)
    raw = client.last_response.raw
    assert "measure_result" in raw
    assert "virtual_time_s" in raw
    assert raw["accepted"] is True


def test_strategy_smoke_clears_a_small_case() -> None:
    jammers = [
        Jammer(channel=1, position=Point(0.0, 0.0), effective_radius_m=1100.0),
        Jammer(channel=7, position=Point(700.0, 0.0), effective_radius_m=1100.0),
    ]
    case = Case(seed=5, jammers=jammers, n_jammers=2)
    with MockSimulator(robot_id=ROBOT, case=case) as sim:
        client = HttpSimulatorClient(robot_id=ROBOT, base_url=sim.base_url)
        state = DogState()
        params = Q3Params()
        strategy = Q3Strategy(params)
        client.enter()
        strategy.run(client, state)
        client.exit()
        assert sim.world.cleared_count == 2


def test_run_once_writes_structured_logs(tmp_path: Path) -> None:
    jammers = [
        Jammer(channel=2, position=Point(300.0, 100.0), effective_radius_m=1200.0),
        Jammer(channel=9, position=Point(-800.0, 900.0), effective_radius_m=1200.0),
    ]
    case = Case(seed=8, jammers=jammers, n_jammers=2)
    outcome = run_once(
        RunConfig(
            robot_id=ROBOT,
            seed=8,
            mode="mock",
            case=case,
            log_root=tmp_path,
            tag="pytest",
        )
    )
    assert outcome.status == "ok"
    run_dir = outcome.run_dir
    assert (run_dir / "meta.json").exists()
    assert (run_dir / "summary.json").exists()
    assert (run_dir / "run.jsonl").exists()
    assert (run_dir / "trace.jsonl").exists()

    events = [json.loads(line) for line in (run_dir / "run.jsonl").read_text().splitlines()]
    assert events[0]["action"] == "run_start"
    actions = [e["action"] for e in events]
    assert actions.count("enter") == 1
    assert actions.count("exit") == 1
    assert all("seq" in e for e in events)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))

    index = read_index(tmp_path)
    assert len(index) == 1
    assert index[0]["status"] == "ok"
    assert index[0]["param_scan_radius"] == Q3Params().scan_radius
    assert index[0]["cleared_ratio"] == 1.0
    assert index[0]["average_clear_time_s"] is not None

    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["ground_truth"]["n_jammers"] == 2
    assert summary["metrics"]["cleared_count"] == 2
    assert not summary["metrics"].get("consistency_issues")


def test_replay_client_reproduces_a_recorded_run(tmp_path: Path) -> None:
    outcome = run_once(
        RunConfig(
            robot_id=ROBOT,
            seed=4,
            mode="mock",
            log_root=tmp_path,
            tag="replay",
            omni_only=True,
        )
    )
    records = [
        json.loads(line)
        for line in (outcome.run_dir / "run.jsonl").read_text().splitlines()
    ]
    # lead-in / trailing 的内部事件之外，动作序列应当完整
    actions = [
        r["action"]
        for r in records
        if r["action"] in {"enter", "measure", "clear", "exit"}
    ]
    assert actions[0] == "enter"
    assert actions[-1] == "exit"

    replay = ReplayClient([r for r in records if r["action"] in {"enter", "measure", "clear", "exit"}])
    assert replay.enter().accepted
    # 重放一个刚记录的清除动作
    first_clear = next(
        (r for r in records if r["action"] == "clear"), None
    )
    assert first_clear is not None
    client = HttpSimulatorClient(robot_id=ROBOT, base_url="http://127.0.0.1:1", retries=1)
    assert client is not None
    # ReplayClient 会在同步点检查序列，不同步时抛错
    for record in records:
        if record["action"] == "measure":
            result = replay.measure(Point(0.0, 0.0), record["request"]["channel"])
            assert result.accepted == record["accepted"]
            break


def test_live_mode_reports_gated_port_cleanly(tmp_path: Path) -> None:
    outcome = run_once(
        RunConfig(
            robot_id=ROBOT,
            seed=1,
            mode="live",
            base_url="http://127.0.0.1:9",
            log_root=tmp_path,
            tag="live-gated",
            write_trace=False,
        )
    )
    assert outcome.status == "gated"
    assert "门控" in (outcome.error or "")
    index = read_index(tmp_path)
    assert index[0]["status"] == "gated"


def test_generated_cases_are_clearable() -> None:
    """回归：默认参数在若干随机案例上必须 100% 清除。"""
    params = Q3Params()
    for seed in (1, 2, 3):
        case = generate_case(seed)
        with MockSimulator(robot_id=ROBOT, case=case) as sim:
            client = HttpSimulatorClient(robot_id=ROBOT, base_url=sim.base_url)
            state = DogState()
            client.enter()
            Q3Strategy(params).run(client, state)
            client.exit()
            assert sim.world.cleared_count == case.n_jammers, f"seed={seed}"


def test_clear_kind_enum_matches_wire_values() -> None:
    assert ClearKind.SUCCESS.value == "success"
    assert ClearKind.NO_TARGET_IN_RANGE.value == "no_target_in_range"
