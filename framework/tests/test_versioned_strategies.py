"""版本化决策方法（``mathmodel2026b.versioned``）的回归测试。

这一组测试钉住三件在移植过程中**真实踩到过**的事，避免再次回归：

1. **零参 ``super()`` 在 ``@dataclass(slots=True)`` 子类上会失效。**
   端口到 Python 3.13 时 ``Q3V8Params().as_dict()`` 抛
   ``TypeError: super(type, obj): obj (instance of Q3V8Params) is not an instance
   or subtype of type (Q3V5Params)``，根因是 slots 重建类对象后 ``__class__``
   单元格指向原始类。所有 ``*Strategy``/``*Params`` 方法都改用
   显式两参 ``super(Cls, self)``。
2. **``--param`` 的合法性校验不能只看 ``__slots__``。**
   slotted 子类的 ``__slots__`` 只含自己声明的字段，继承字段（如
   ``Q3V15Params.schedule_min_readings``）会被误判为"未知参数"。
3. **移植保真**：v8 在 30 seed 上的 ``avg_med`` 必须与归档数字一致（267.19），
   否则说明移植过程改动了行为。
"""

from __future__ import annotations

import importlib.util
import statistics
import sys
from pathlib import Path

import pytest

from mathmodel2026b.client import HttpSimulatorClient
from mathmodel2026b.mock.server import MockSimulator
from mathmodel2026b.mock.world import generate_case
from mathmodel2026b.state import DogState
from mathmodel2026b.versioned import (
    DECISION_METHODS,
    INTERMEDIATE_ENTRIES,
    ROUTE_ENTRIES,
    available_methods,
    build_method,
    usable_routes,
)

ROBOT = "000000000000"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(strategy, *, seed: int, directional: bool) -> dict:
    case = generate_case(seed, omni_only=not directional)
    with MockSimulator(robot_id=ROBOT, case=case) as sim:
        client = HttpSimulatorClient(robot_id=ROBOT, base_url=sim.base_url, verbose=False)
        state = DogState()
        client.enter()
        strategy.run(client, state)
        client.exit()
        summary = sim.world.summary()
    assert summary["n_jammers"] == case.n_jammers
    return summary


# --------------------------------------------------------------------------
# 1. 注册表完整性
# --------------------------------------------------------------------------
def test_registry_covers_all_documented_methods() -> None:
    assert available_methods() == [
        "q3",
        "matrix",
        "q3-v5",
        "q3-v6",
        "q3-v7",
        "q3-v8",
        "q3-v15",
        "q4-v8",
        "q4-v9",
        "q4-v14",
        "paper-q4",
    ]


def test_exactly_two_final_methods_one_per_problem() -> None:
    finals = {k: v for k, v in DECISION_METHODS.items() if v.final}
    assert set(finals) == {"q3-v15", "q4-v14"}
    assert finals["q3-v15"].problem == (3,)
    assert finals["q4-v14"].problem == (4,)


def test_problem_partition() -> None:
    # matrix 两边都能跑（problem=(3,4)），但它在问题4 上不达标（见下一个测试）
    assert available_methods(3) == [
        "q3",
        "matrix",
        "q3-v5",
        "q3-v6",
        "q3-v7",
        "q3-v8",
        "q3-v15",
    ]
    # paper-q4 只解问题4；q4-v8 是"把全向 v8 直接套到定向场景"的反例臂
    assert available_methods(4) == ["matrix", "q4-v8", "q4-v9", "q4-v14", "paper-q4"]


def test_each_problem_has_exactly_two_usable_routes() -> None:
    """路线总览的硬约束：每个问题只有两条可用路线。

    问题3：matrix ／ q3-v8、q3-v15（迭代优化版）
    问题4：paper-q4 ／ q4-v14
    排除项：原始 q3（过时）；问题4 上的 matrix、q4-v8、q4-v9（全清率不达标）
    """
    # 权威声明来自注册表，而不是本测试自己再算一遍
    assert usable_routes(3) == ["matrix", "q3-v8", "q3-v15"]
    assert usable_routes(4) == ["q4-v14", "paper-q4"]
    # route_entry 标记必须与 ROUTE_ENTRIES 完全一致（模块导入时已自检，
    # 这里再钉一次，防止有人把自检删掉）
    declared = {
        k for entries in ROUTE_ENTRIES.values() for v in entries.values() for k in v
    }
    flagged = {k for k, spec in DECISION_METHODS.items() if spec.route_entry}
    assert declared == flagged
    # 每个问题的路线数必须恰好是 2（1 独立 + 1~2 个迭代优化版入口）
    for problem in (3, 4):
        entries = ROUTE_ENTRIES[problem]
        assert len(entries["independent"]) == 1, f"问题{problem} 应有 1 条独立解法路线"
        assert 1 <= len(entries["iterated"]) <= 2, f"问题{problem} 的迭代优化版入口数异常"
    # 中间代不得出现在可用路线里
    for problem, mids in INTERMEDIATE_ENTRIES.items():
        assert not set(mids) & set(usable_routes(problem))


def test_q3_baseline_is_marked_outdated() -> None:
    """原始 q3 线上 500~680 s/源，明显差于 matrix 与迭代优化版 ⇒ 标记过时。"""
    spec = DECISION_METHODS["q3"]
    assert spec.outdated is True
    assert spec.final is False
    assert "线上" in spec.effect


def test_matrix_is_usable_for_q3_but_not_q4() -> None:
    """matrix 是问题3 的路线A；在问题4 上 12/30（axial 也只 29/30）⇒ 排除。"""
    spec = DECISION_METHODS["matrix"]
    assert spec.separate_route is True
    assert 3 in spec.problem
    assert spec.fails_q4_requirement is True


def test_paper_q4_is_a_q4_route_only() -> None:
    """论文内核只解问题4；问题3 不采纳它（mock 上 567.39，比两者都慢）。"""
    spec = DECISION_METHODS["paper-q4"]
    assert spec.problem == (4,)
    assert spec.separate_route is True
    assert 3 not in spec.problem


@pytest.mark.parametrize("key", available_methods())
def test_every_method_constructs(key: str) -> None:
    strategy = build_method(key)
    assert strategy is not None
    assert hasattr(strategy, "run")


# --------------------------------------------------------------------------
# 2. 零参 super() 回归（本组测试的由来）
# --------------------------------------------------------------------------
@pytest.mark.parametrize("key", available_methods())
def test_params_as_dict_works_for_every_method(key: str) -> None:
    """``as_dict()`` 是 runner 写 meta 时必调的方法，曾经直接抛 TypeError。"""
    strategy = build_method(key)
    data = strategy.params.as_dict()
    assert isinstance(data, dict)
    assert "max_wall_time_s" in data


def test_diagnostics_works_for_every_method() -> None:
    """``diagnostics`` 里有 ``super().diagnostics(state)``，同样受 slots 影响。"""
    state = DogState()
    for key in available_methods():
        strategy = build_method(key)
        data = strategy.diagnostics(state)
        assert isinstance(data, dict)


def test_params_subclass_instance_passes_as_parent_params() -> None:
    """回归：子类 Params 实例必须能被父类方法中的 super() 接受。"""
    from mathmodel2026b.strategy_v8 import Q3V8Params

    params = Q3V8Params(schedule_min_readings=1)
    # v5 定义 as_dict，v8 是 3 层之后的子类
    assert params.as_dict()["schedule_min_readings"] == 1
    assert params.as_dict()["scan_radius"] == 1110.0


# --------------------------------------------------------------------------
# 3. --param 校验必须覆盖继承字段
# --------------------------------------------------------------------------
def _load_run_module():
    spec = importlib.util.spec_from_file_location(
        "_run_module_under_test", REPO_ROOT / "script" / "run.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_param_override_accepts_inherited_slotted_field() -> None:
    """``schedule_min_readings`` 声明在 Q3V8Params，但必须能覆盖 Q3V15Params。"""
    run_module = _load_run_module()
    params = run_module.build_strategy_params(
        "q3-v15", False, ["schedule_min_readings=3"]
    )
    assert params.schedule_min_readings == 3
    # 交付参数必须仍然在（只被显式覆盖的那一项改动）
    assert params.or_opt_passes == 0


def test_param_override_rejects_unknown_field() -> None:
    run_module = _load_run_module()
    with pytest.raises(SystemExit):
        run_module.build_strategy_params("q3-v15", False, ["no_such_param=1"])


def test_strategy_choices_include_versioned_methods() -> None:
    run_module = _load_run_module()
    for key in available_methods():
        assert key in run_module.STRATEGY_CHOICES
    # 非版本化路线仍在
    for route in ("matrix", "paper-q4"):
        assert route in run_module.STRATEGY_CHOICES


def test_run_py_factory_returns_the_real_strategy_class() -> None:
    """工厂必须返回策略类本身（runner 会以 factory(params) 调用）。"""
    run_module = _load_run_module()
    from mathmodel2026b.strategy_v8 import Q3V8Strategy

    factory = run_module.make_strategy_factory("q3-v8", False)
    assert factory is Q3V8Strategy
    params = run_module.build_strategy_params("q3-v8", False, [])
    strategy = factory(params)
    assert strategy.params.schedule_min_readings == 1  # 交付参数生效


# --------------------------------------------------------------------------
# 4. 端到端全清（问题3 / 问题4 各取少量 seed，保证 CI 快）
# --------------------------------------------------------------------------
@pytest.mark.parametrize("key", ["q3-v8", "q3-v15"])
@pytest.mark.parametrize("seed", [1, 2])
def test_q3_methods_clear_fully(key: str, seed: int) -> None:
    summary = _run(build_method(key), seed=seed, directional=False)
    assert summary["cleared_count"] == summary["n_jammers"], f"{key} seed={seed}"


@pytest.mark.parametrize("key", ["q4-v9", "q4-v14"])
@pytest.mark.parametrize("seed", [1, 2])
def test_q4_methods_clear_fully(key: str, seed: int) -> None:
    summary = _run(build_method(key), seed=seed, directional=True)
    assert summary["cleared_count"] == summary["n_jammers"], f"{key} seed={seed}"


# --------------------------------------------------------------------------
# 5. 移植保真：v8 的 30 seed 数字必须与归档一致
# --------------------------------------------------------------------------
@pytest.mark.slow
def test_v8_reproduces_archived_30_seed_median() -> None:
    """``_bench_versions.py --mode omni --seeds 1-30`` 的 v8 行。

    归档（``Q3Q4优化_v2_.../outputs/_b30_v8.txt``，schedule_min_readings=1）：
    ``avg_med=267.19``、``dist_med=13136``、``full=30/30``。
    """
    avgs: list[float] = []
    dists: list[float] = []
    for seed in range(1, 31):
        summary = _run(build_method("q3-v8"), seed=seed, directional=False)
        assert summary["cleared_count"] == summary["n_jammers"], f"seed={seed}"
        avgs.append(summary["average_clear_time_s"])
        dists.append(summary["move_distance_m"])
    assert statistics.median(avgs) == pytest.approx(267.19, abs=0.05)
    assert statistics.median(dists) == pytest.approx(13136, abs=1.0)
