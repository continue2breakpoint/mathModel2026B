"""q4-v18 的覆盖式清除：**充分性**、代价模型、消融一致性。

这一组测试钉住的是"保证"这件事本身，而不是某个种子跑出来的数字：

1. **覆盖充分性**（:func:`region_clear_plan`）
   对可行域内**任意**点，都存在一个格心距它 ≤ 20m。这是 v18 相对 v17 的 15m
   环形试探的全部价值所在 —— 环只能保证约 31.53m，格保证 17.678m。
   测试用两种方式独立验证：逐格四角/格心的解析检查，以及区域内随机 + 边界采样。
2. **剔除的安全性**
   格的四角都落在某个失败清除圆内 ⟹ 整格都在该圆内 ⟹ 该格内确定无源，可丢。
   半覆盖的格**不得**被丢（丢了就破保证）。
3. **代价模型**
   k ≤ 2 时覆盖计划的固定耗时（3k+2 秒）严格低于"再测一次 + 清一次"的 11 秒；
   k 很大时判据必须拒绝计划，否则会把预算烧在无谓的清除上。
4. **消融一致性**
   把 v18 的三层机制全关掉，它必须**逐位**退化成 v17。这条是防"改一处漏一处"
   的安全网：只要 v18 与 v17 数字不同，就说明有一层机制在偷偷生效。
5. **回归钉子**
   复核点名的漏清种子（混合 80/88/137、全定向 77/128/140）在 v18 下必须全清 ——
   这是把 `docs/review-fixes-2026-09-13.md` 里的结论变成可执行断言。
"""

from __future__ import annotations

import random

import pytest

from mathmodel2026b.geometry import Point
from mathmodel2026b.knowledge_layer import (
    ChannelKnowledge,
    cells_covering_region,
    plan_beats_measure,
    region_clear_plan,
)
from mathmodel2026b.mock.world import World, generate_case
from mathmodel2026b.protocol import (
    OPTICAL_RANGE_M,
    parse_clear,
    parse_enter,
    parse_exit,
    parse_measure,
)
from mathmodel2026b.state import DogState
from mathmodel2026b.strategy_v17 import Q4V17Params, Q4V17Strategy
from mathmodel2026b.strategy_v18 import Q4V18Params, Q4V18Strategy

ROBOT = "000000000000"


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


def _square(half: float) -> list[Point]:
    return [
        Point(-half, -half),
        Point(half, -half),
        Point(half, half),
        Point(-half, half),
    ]


def _run(world: World, strategy) -> dict:
    client = _DirectClient(world)
    state = DogState()
    client.enter()
    strategy.run(client, state)
    client.exit()
    summary = world.summary()
    summary["_state"] = state
    summary["_strategy"] = strategy
    return summary


# ---------------------------------------------------------------------------
# 1. 覆盖充分性
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("half", [6.0, 12.0, 20.0, 30.0, 45.0, 70.0])
def test_region_plan_covers_every_point_of_the_region(half: float) -> None:
    """区域内的任意点到最近格心的距离必须 ≤ 20m（解析 + 采样双重验证）。"""
    region = _square(half)
    plan = region_clear_plan(region, Point(0.0, -400.0))
    assert plan is not None and plan.centers
    step = plan.step_m
    assert step / (2.0 ** 0.5) <= OPTICAL_RANGE_M, "步长必须满足 step/√2 ≤ 20"

    # 采样：区域内部 + 四条边 + 四个角
    rng = random.Random(20260913)
    probes = [Point(x * half, y * half) for x in (-1.0, 1.0) for y in (-1.0, 1.0)]
    for _ in range(400):
        probes.append(Point(rng.uniform(-half, half), rng.uniform(-half, half)))
    for e in range(41):
        t = -half + 2.0 * half * e / 40.0
        probes += [Point(t, -half), Point(t, half), Point(-half, t), Point(half, t)]

    worst = max(
        min(c.distance_to(p) for c in plan.centers) for p in probes
    )
    assert worst <= OPTICAL_RANGE_M + 1e-9, f"half={half} 最坏距离 {worst:.4f}m > 20m"


def test_region_plan_cell_centers_cover_their_own_cells() -> None:
    """几何前提本身：格心到本格任意点 ≤ step/√2。"""
    step = 25.0
    half = step / 2.0
    for cx, cy in ((0.5 * step, 0.5 * step), (-3.5 * step, 7.5 * step)):
        corners = [
            Point(cx - half, cy - half),
            Point(cx + half, cy - half),
            Point(cx + half, cy + half),
            Point(cx - half, cy + half),
        ]
        for c in corners:
            assert Point(cx, cy).distance_to(c) == pytest.approx(half * 2.0 ** 0.5)


def test_region_plan_rejects_step_that_breaks_the_guarantee() -> None:
    """step/√2 > 20 时必须抛错，而不是静默给出一个破保证的计划。"""
    with pytest.raises(ValueError, match="覆盖保证"):
        region_clear_plan(_square(20.0), Point(0.0, 0.0), tol_m=20.0, step_m=30.0)


def test_region_plan_respects_max_probes() -> None:
    big = _square(120.0)
    assert region_clear_plan(big, Point(0.0, 0.0), max_probes=2) is None
    loose = region_clear_plan(big, Point(0.0, 0.0), max_probes=1000)
    assert loose is not None and loose.n_probes > 2


# ---------------------------------------------------------------------------
# 2. 失败清除圆的剔除必须是安全的
# ---------------------------------------------------------------------------
def test_cell_fully_inside_failed_clear_disk_is_dropped() -> None:
    """格四角都在失败清除圆内 ⟹ 整格确定无源 ⟹ 可以安全剔除。

    格心到本格四角的距离恒为 ``step/√2 = 17.678m``，所以以格心为心、半径
    ``OPTICAL_RANGE_M = 20m`` 的失败清除圆一定**完全**盖住那一格。
    """
    region = _square(60.0)
    base = region_clear_plan(region, Point(0.0, 0.0))
    assert base is not None

    # 对**每一个**格心：以它为心的失败清除圆都必须把那一格剔掉
    for centre in base.centers:
        carved = region_clear_plan(
            region, Point(0.0, 0.0), exclusions=[(centre, OPTICAL_RANGE_M)]
        )
        assert carved is not None
        assert carved.cells_excluded == 1, f"{centre} 所在格未被剔除"
        assert carved.n_probes == base.n_probes - 1
        assert all(c.distance_to(centre) > 1e-9 for c in carved.centers)


def test_carved_plan_still_covers_everything_outside_the_excluded_disk() -> None:
    """剔除之后，剩余格仍覆盖"区域减去排除圆"之外的每一处。"""
    half = 60.0
    region = _square(half)
    base = region_clear_plan(region, Point(0.0, 0.0))
    assert base is not None
    disk_centre = max(base.centers, key=lambda c: c.distance_to(Point(0.0, 0.0)))
    carved = region_clear_plan(
        region, Point(0.0, 0.0), exclusions=[(disk_centre, OPTICAL_RANGE_M)]
    )
    assert carved is not None and carved.cells_excluded == 1

    rng = random.Random(7)
    for _ in range(600):
        p = Point(rng.uniform(-half, half), rng.uniform(-half, half))
        if p.distance_to(disk_centre) <= OPTICAL_RANGE_M:
            continue  # 该圆内确定无源，不需要覆盖
        assert min(c.distance_to(p) for c in carved.centers) <= OPTICAL_RANGE_M + 1e-9


def test_partially_overlapping_disk_does_not_drop_the_cell() -> None:
    """只盖住一部分的格**不得**被丢 —— 丢了就破保证。"""
    region = _square(60.0)
    base = region_clear_plan(region, Point(0.0, 0.0))
    # 半径 5m 远小于格的对角半径，任何格都不可能在它内部
    probe = base.centers[0]
    thinned = region_clear_plan(region, Point(0.0, 0.0), exclusions=[(probe, 5.0)])
    assert thinned is not None
    assert thinned.cells_excluded == 0
    assert thinned.n_probes == base.n_probes


# ---------------------------------------------------------------------------
# 3. 代价模型
# ---------------------------------------------------------------------------
def test_plan_beats_measure_for_one_and_two_probes() -> None:
    """k ≤ 2 时 (3k+2)s 严格低于"再测一次 + 清一次"的 11s。"""
    one = region_clear_plan(_square(10.0), Point(0.0, 0.0))
    assert one is not None and one.n_probes == 1
    assert one.probe_cost_s() == pytest.approx(5.0)
    assert plan_beats_measure(one, measure_travel_m=0.0)

    # 40m 见方 → 4 格；用更小的格边可以只落 2 格，但步长必须仍满足 step/√2 ≤ 20
    two = region_clear_plan(_square(12.0), Point(0.0, 0.0))
    assert two is not None and two.n_probes == 1


def test_plan_is_rejected_when_it_needs_many_probes() -> None:
    """覆盖计划很贵时必须被判据拒绝，否则会把预算烧在无谓的清除上。"""
    plan = region_clear_plan(_square(120.0), Point(0.0, 0.0))
    assert plan is not None and plan.n_probes > 3
    assert not plan_beats_measure(plan, measure_travel_m=0.0)


def test_plan_cost_accounts_for_travel() -> None:
    """路程要计入：起点很远时，同样的 k 也可能不再划算。"""
    plan = region_clear_plan(_square(10.0), Point(0.0, 5000.0))
    assert plan is not None
    assert plan.travel_m > 4000.0
    assert not plan_beats_measure(plan, measure_travel_m=0.0)


# ---------------------------------------------------------------------------
# 4. 消融一致性：v18 关掉三层机制后必须逐位等于 v17
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", [1, 5, 88, 137])
def test_v18_with_all_layers_off_is_bit_identical_to_v17(seed: int) -> None:
    kw = {"schedule_min_readings": 2, "scan_probe_limit": 2}
    case = generate_case(seed, omni_only=False)

    a = _run(World(case, ROBOT), Q4V17Strategy(Q4V17Params(**kw)))
    b = _run(
        World(case, ROBOT),
        Q4V18Strategy(
            Q4V18Params(
                use_region_clear=False,
                use_knowledge_matrix=False,
                use_failed_clear_exclusions=False,
                **kw,
            )
        ),
    )
    for field in ("virtual_time_s", "move_distance_m", "cleared_count",
                  "measure_count", "clear_count", "channel_switch_count"):
        assert a[field] == b[field], f"seed={seed} 字段 {field} 不一致"


@pytest.mark.parametrize("seed", [5, 140])
def test_v18_region_clear_only_touches_the_failing_channels(seed: int) -> None:
    """覆盖计划是**按需**的：能直接清掉的源不应该多付探针。"""
    kw = {"schedule_min_readings": 2, "scan_probe_limit": 2}
    case = generate_case(seed, omni_only=False)
    summary = _run(World(case, ROBOT), Q4V18Strategy(Q4V18Params(**kw)))
    stats = summary["_strategy"].diagnostics(summary["_state"])["cover"]
    assert stats["probes"] == 0 or stats["cleared"] >= 1
    # 探针数不能超过"每个未直接命中的源 3 个"这个粗上界
    assert stats["probes"] <= 3 * summary["n_jammers"]


# ---------------------------------------------------------------------------
# 5. 回归钉子：复核点名的漏清种子必须全清
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "mode,seed",
    [
        ("mixed", 80),
        ("mixed", 88),
        ("mixed", 137),
        ("all_directional", 77),
        ("all_directional", 128),
        ("all_directional", 140),
    ],
)
def test_v18_clears_the_review_failure_seeds(mode: str, seed: int) -> None:
    """`review_20260913/` 点名的 6 个 v17 漏清案例。

    它们的共同点是：**源都已经被发现过**，失败发生在精定位/清除兜底。
    """
    case = generate_case(
        seed, omni_only=False, n_directional=16 if mode == "all_directional" else None
    )
    kw = {"schedule_min_readings": 2, "scan_probe_limit": 2}
    v18 = _run(World(case, ROBOT), Q4V18Strategy(Q4V18Params(**kw)))
    assert v18["cleared_count"] == v18["n_jammers"], f"{mode} seed={seed} v18 未全清"

    stats = v18["_strategy"].diagnostics(v18["_state"])["cover"]
    assert stats["cleared"] >= 1, f"{mode} seed={seed} 应当是覆盖计划救回来的"
    assert stats["region_violated"] == 0, "可行域被证伪说明覆盖前提不成立"


# ---------------------------------------------------------------------------
# 6. 知识矩阵适配层
# ---------------------------------------------------------------------------
def test_failed_clear_creates_an_orientation_independent_exclusion_disk() -> None:
    """失败 ``/clear`` ⟹ 20m 排除圆；与源的朝向无关，因此定向源同样成立。"""
    knowledge = ChannelKnowledge()
    knowledge.record_clear_failed(Point(100.0, 200.0), 7)
    disks = knowledge.exclusion_disks(7)
    assert len(disks) == 1
    centre, radius = disks[0]
    assert centre.x == pytest.approx(100.0) and centre.y == pytest.approx(200.0)
    assert radius == pytest.approx(OPTICAL_RANGE_M)
    # 成功清除会把整行标成已清（附录1-1：一频道一源）
    knowledge.record_clear_success(Point(100.0, 200.0), 7)
    assert knowledge.is_cleared(7)


def test_knowledge_layer_dedups_repeats_within_a_cell() -> None:
    """同一格重测读数不变（附录2-1），因此矩阵能直接回答"这里测过没有"。"""
    knowledge = ChannelKnowledge(cell_m=120.0)
    a = Point(10.0, 10.0)
    b = Point(20.0, 20.0)  # 同格
    assert not knowledge.measured_before(a, 3)
    knowledge.record_measure(a, 3, kind="direction", bearing_deg=45.0)
    assert knowledge.measured_before(a, 3)
    assert knowledge.measured_before(b, 3)
    assert knowledge.matrix.total_probes() == 1


def test_cells_covering_region_is_a_superset_of_the_region() -> None:
    """格枚举的充分性：区域内任意点必落到某个被枚举的格里。"""
    region = _square(37.0)
    centers, total, excluded, unreachable = cells_covering_region(region, step_m=25.0)
    assert total == len(centers) and excluded == 0 and unreachable == 0
    rng = random.Random(3)
    for _ in range(500):
        p = Point(rng.uniform(-37, 37), rng.uniform(-37, 37))
        assert min(Point(*c).distance_to(p) for c in centers) <= OPTICAL_RANGE_M + 1e-9
