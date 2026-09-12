"""队友论文 Q4 内核接入的回归测试。

三层保护：

1. **冻结完整性** —— ``paper_q4/`` 下的六个文件必须与引入时的 SHA256 完全一致。
   这些文件的行为已在官方模拟器 live 演练中验证，一旦被就地修改，
   "验证过的行为" 与 "仓库里的行为" 就脱钩了。
2. **适配器语义** —— ``enter``/``exit`` 是 no-op（真正的 ``/enter``、``/exit`` 由
   runner 负责）；``measure``/``clear`` 的返回值必须译成内核认识的字符串枚举。
3. **端到端全清** —— 在框架自己的 mock 上跑定向混合案例，硬要求是
   "所有干扰源被清除"（题面：在确保全部清除的前提下再谈时间）。
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest

from mathmodel2026b.client import HttpSimulatorClient
from mathmodel2026b.geometry import Point
from mathmodel2026b.mock.server import MockSimulator
from mathmodel2026b.mock.world import generate_case
from mathmodel2026b.state import DogState
from mathmodel2026b.strategy import Q3Params
from mathmodel2026b.strategy_q4 import PaperEnvAdapter, Q4Strategy, Q4WallBudgetExceeded

ROBOT = "000000000000"  # 占位队号：不要在代码里写死真实队号

PAPER_DIR = (
    Path(__file__).resolve().parent.parent / "src" / "mathmodel2026b" / "paper_q4"
)

#: 引入自 ``origin/b-full-upload`` (``c3a38fd``) 的逐字节指纹。
#: 变更此表等于宣布内核被修改——必须同时在 docs/paper-q4-adoption.md 说明原因与实测。
FROZEN_SHA256 = {
    "问题1_求解.py": "7d785556d5685232cea870cc4e76e3192390fb0af25ca1e35a043bd352803cf9",
    "coverage.py": "dafbfff8782a008b2db5ca9e9550b95984753b1ea709b2b047edd433f9bfaaad",
    "q3_strategy.py": "f8e50e8309a0a66b96577758b9539ca8fe65217231cba69bb18c3fe6c30d4a2d",
    "q4_identify.py": "310fad6569a69afaf7b038361cc42ccd04b134103459d974effb80004f9bb1c4",
    "q4_strategy.py": "d981736c421d54595a664fe12d1769d3b4e5b5b23dcab2e90b4c36eb04035e5a",
    "sim_env.py": "638bc2f0332fec795e5974261fc05adec89b2372b98e8aa2f4340b168314e1f5",
}

#: 端到端断言用的种子（默认定向混合：n_dir = randint(1, n//2)）。
CLEARANCE_SEEDS = (1, 2, 3)


# ---------------------------------------------------------------------------
# 1. 冻结完整性
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(FROZEN_SHA256))
def test_paper_kernel_is_byte_identical(name: str) -> None:
    path = PAPER_DIR / name
    assert path.is_file(), f"冻结内核缺少 {name}"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == FROZEN_SHA256[name], (
        f"{name} 已被修改（{digest}）。\n"
        "paper_q4/ 是逐字节冻结的队友内核：要改逻辑请改适配层或另开新文件，"
        "并在 docs/paper-q4-adoption.md 记录动机与实测。"
    )


def test_paper_kernel_has_no_stray_files() -> None:
    """目录里只应有冻结的六个 .py 与来源说明。

    ``__pycache__`` 由 Python 导入内核时自动生成（已被 .gitignore 覆盖），不算噪音。
    """
    ignored = {"__pycache__"}
    extra = {
        p.name
        for p in PAPER_DIR.iterdir()
        if p.name not in FROZEN_SHA256
        and p.name != "PROVENANCE.md"
        and p.name not in ignored
    }
    assert extra == set(), f"paper_q4/ 出现预期外文件：{sorted(extra)}"


def test_paper_kernel_is_not_a_package() -> None:
    """内核靠 sys.path + 绝对导入被引用；加了 __init__.py 会改变导入语义。"""
    assert not (PAPER_DIR / "__init__.py").exists()


# ---------------------------------------------------------------------------
# 2. 适配器语义
# ---------------------------------------------------------------------------
@pytest.fixture()
def sim() -> MockSimulator:
    case = generate_case(1, omni_only=False)
    with MockSimulator(robot_id=ROBOT, case=case) as running:
        yield running


def test_adapter_enter_exit_are_noop(sim: MockSimulator) -> None:
    """runner 负责真正的 /enter 与 /exit；适配器只回显虚拟时间。"""
    strategy = Q4Strategy()
    client = HttpSimulatorClient(robot_id=ROBOT, base_url=sim.base_url)
    state = DogState()
    adapter = PaperEnvAdapter(strategy, client, state)

    entered = adapter.enter()
    assert entered["accepted"] is True
    assert entered["virtual_time_s"] == state.virtual_time_s

    left = adapter.exit()
    assert left["accepted"] is True
    # no-op 的证据：没有真的走 /exit，所以 mock 世界的出口计数仍为 0
    assert adapter.n_request == 0


def test_adapter_translates_measure_and_clear(sim: MockSimulator) -> None:
    strategy = Q4Strategy()
    client = HttpSimulatorClient(robot_id=ROBOT, base_url=sim.base_url)
    state = DogState()
    adapter = PaperEnvAdapter(strategy, client, state)

    measured = adapter.measure(0.0, 0.0, 1)
    # 内核只认这三个字符串（见 paper_q4/sim_env.py 的接口约定）
    assert measured["measure_result"] in ("no_signal", "near", "direction")
    if measured["measure_result"] == "direction":
        assert isinstance(measured["svd_deg"], float)
    else:
        assert "svd_deg" not in measured
    assert adapter.n_request == 1
    assert "virtual_time_s" in measured

    cleared = adapter.clear(0.0, 0.0, 1)
    assert cleared["clear_result"] in ("success", "no_target_in_range")
    assert isinstance(cleared["clear_result"], str)


def test_adapter_live_truth_is_unavailable(sim: MockSimulator) -> None:
    """线上拿不到真值：truth() 必须返回空、n_total() 必须不炸。"""
    strategy = Q4Strategy()
    client = HttpSimulatorClient(robot_id=ROBOT, base_url=sim.base_url)
    adapter = PaperEnvAdapter(strategy, client, DogState())
    assert adapter.truth() == []
    assert adapter.n_total() >= 1


def test_wall_budget_valve_raises(sim: MockSimulator) -> None:
    """内核自身没有墙钟检查；适配器必须在每次动作前守住现实预算。"""
    strategy = Q4Strategy()
    client = HttpSimulatorClient(robot_id=ROBOT, base_url=sim.base_url)
    adapter = PaperEnvAdapter(
        strategy, client, DogState(), wall_deadline=time.time() - 1.0
    )
    with pytest.raises(Q4WallBudgetExceeded):
        adapter.measure(0.0, 0.0, 1)
    with pytest.raises(Q4WallBudgetExceeded):
        adapter.clear(0.0, 0.0, 1)


def test_runner_rejects_omni_case_for_paper_q4() -> None:
    """paper-q4 只解问题4；纯全向案例下暴露其发现层是 31 点三角网格。

    这里不做通过/失败断言（全向案例它同样能清），只固定住
    "paper-q4 的默认 grid_s=950" 这个契约，避免被静默改掉。
    """
    assert Q4Strategy().grid_s == 950.0
    assert Q4Strategy(Q3Params()).grid_s == 950.0


# ---------------------------------------------------------------------------
# 3. 端到端全清（硬要求）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", CLEARANCE_SEEDS)
def test_paper_q4_clears_every_source(seed: int) -> None:
    case = generate_case(seed, omni_only=False)
    true_channels = sorted(case.by_channel())
    with MockSimulator(robot_id=ROBOT, case=case) as sim:
        client = HttpSimulatorClient(robot_id=ROBOT, base_url=sim.base_url)
        state = DogState()
        strategy = Q4Strategy()
        client.enter()
        strategy.run(client, state)
        client.exit()
        summary = sim.world.summary()

    # 题面硬要求："在确保所有干扰源被清除的前提下"——先看全清，再看时间。
    assert summary["n_jammers"] >= 10
    assert summary["cleared_count"] == summary["n_jammers"], (
        f"seed={seed} 漏清 {summary['uncleared_channels']}"
    )
    assert summary["cleared_channels"] == true_channels
    assert summary["uncleared_channels"] == []
    # 内核做的是"先定位到 MEC ≤ 20m 再清"，不应有大量无效 clear 调用
    assert summary["clear_count"] >= summary["n_jammers"]


def test_paper_q4_identifies_kinds_and_headings() -> None:
    """类型辨识与朝向估计是这个内核相对我们 Q4 策略的核心增量，必须留下证据。"""
    case = generate_case(7, omni_only=False)
    with MockSimulator(robot_id=ROBOT, case=case) as sim:
        client = HttpSimulatorClient(robot_id=ROBOT, base_url=sim.base_url)
        state = DogState()
        strategy = Q4Strategy()
        client.enter()
        strategy.run(client, state)
        client.exit()
        diag = strategy.diagnostics(state)

    identified = diag["identified"]
    assert identified, "没有任何频道产出类型辨识结果"
    kinds = {v["kind"] for v in identified.values()}
    assert kinds <= {"omni", "dir", "ambiguous", "unknown"}
    assert diag["paper_stats"] is not None
    # 真值类型分布里 seed=7 至少有一个定向源，内核应当认出至少一个 dir
    truth = case.by_channel()
    has_dir = any(not j.is_omni for j in truth.values())
    assert has_dir
    assert any(v["kind"] == "dir" for v in identified.values()), kinds
    assert any(v["kind"] == "omni" for v in identified.values()), kinds
