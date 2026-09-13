"""离线 mock 模拟器的"世界"：案例生成 + 物理 + 计时。

严格按题目附录2 与逆向出的 ``robot-protocol-v1`` 语义实现：

==================  ====================================================
规则                实现
==================  ====================================================
目标区域            半径 1800m 圆，圆心为原点，x 轴正东、y 轴正北
干扰源个数          10..16（可指定）
频道                互不相同，取自 1..20（可指定）
有效接收半径        每源独立取 1000..1500m
覆盖               面向源 360°；定向源为定向方向 ±90°
示向度误差          同一 (检测点, 源) 固定，落在 [-1°, 1°]
过强不可测向        距离 <= 5m 且落在覆盖角内 -> ``near``
光学清除            清除点与源距离 <= 20m -> ``success``（与覆盖角无关）
移动                5 m/s（按相邻 position 的直线距离计时）
频道切换            变更检测频道 1 s
测向/确认无信号      5 s
光学 + 激光          3 s + 2 s
==================  ====================================================
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from math import cos, radians, sin
from typing import Any

from ..geometry import Point, angle_difference_deg, degrees_atan2, from_polar
from ..protocol import (
    ARENA_RADIUS_M,
    BEARING_ERROR_DEG,
    CHANNEL_SWITCH_SECONDS,
    CLEAR_SECONDS,
    MAX_EFFECTIVE_RADIUS_M,
    MAX_JAMMERS,
    MAX_REAL_DURATION_S,
    MAX_VIRTUAL_DURATION_S,
    MEASURE_SECONDS,
    MIN_EFFECTIVE_RADIUS_M,
    MIN_JAMMERS,
    MOVE_SPEED_MPS,
    NEAR_RANGE_M,
    OPTICAL_RANGE_M,
    OPTICAL_SECONDS,
)


@dataclass(frozen=True, slots=True)
class Jammer:
    channel: int
    position: Point
    effective_radius_m: float
    direction_deg: float | None = None  # None 表示全向源

    @property
    def is_omni(self) -> bool:
        return self.direction_deg is None

    def covers(self, point: Point) -> bool:
        if self.direction_deg is None:
            return True
        bearing = degrees_atan2(point.y - self.position.y, point.x - self.position.x)
        # 定向源覆盖"定向方向两侧各 90°（含）"。判断的是**源->点**方向与定向方向夹角。
        return angle_difference_deg(bearing, self.direction_deg) <= 90.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "x": self.position.x,
            "y": self.position.y,
            "effective_radius_m": self.effective_radius_m,
            "direction_deg": self.direction_deg,
        }


@dataclass(slots=True)
class Case:
    seed: int
    jammers: list[Jammer]
    n_jammers: int

    def by_channel(self) -> dict[int, Jammer]:
        return {j.channel: j for j in self.jammers}

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "n_jammers": self.n_jammers,
            "jammers": [j.as_dict() for j in self.jammers],
        }


def generate_case(
    seed: int,
    n_jammers: int | None = None,
    omni_only: bool = True,
    n_directional: int | None = None,
) -> Case:
    """确定性生成一个案例（同一 seed 永远得到同一案例）。"""
    import random

    rng = random.Random(seed)
    n = n_jammers if n_jammers is not None else rng.randint(MIN_JAMMERS, MAX_JAMMERS)
    n = max(MIN_JAMMERS, min(MAX_JAMMERS, n))
    channels = rng.sample(range(1, 21), n)
    channels.sort()

    if omni_only:
        n_dir = 0
    elif n_directional is not None:
        n_dir = max(0, min(n, n_directional))
    else:
        n_dir = rng.randint(1, max(1, n // 2))
    directional_channels = set(rng.sample(channels, n_dir)) if n_dir else set()

    jammers: list[Jammer] = []
    for ch in channels:
        # 均匀分布在半径 1800m 圆内（面积均匀）
        r = ARENA_RADIUS_M * (rng.random() ** 0.5)
        theta = rng.uniform(0.0, 360.0)
        direction = rng.uniform(0.0, 360.0) if ch in directional_channels else None
        jammers.append(
            Jammer(
                channel=ch,
                position=from_polar(r, theta),
                effective_radius_m=rng.uniform(MIN_EFFECTIVE_RADIUS_M, MAX_EFFECTIVE_RADIUS_M),
                direction_deg=direction,
            )
        )
    return Case(seed=seed, jammers=jammers, n_jammers=n)


def bearing_error(seed: int, channel: int, position: Point, quant_m: float = 0.25) -> float:
    """固定电磁环境误差：只取决于 (seed, 频道, 检测点)，落在 [-1°, 1°]。

    题目明确"同一地点的误差固定，重复检测不会改变"，因此这里必须是纯函数，
    不能引入随机数发生器状态。
    """
    key = (
        f"{seed}|{channel}|{round(position.x / quant_m)}|{round(position.y / quant_m)}"
    ).encode("utf-8")
    digest = int.from_bytes(hashlib.sha256(key).digest()[:8], "big")
    frac = digest / float(1 << 64)  # [0, 1)
    return (frac * 2.0 - 1.0) * BEARING_ERROR_DEG


@dataclass(slots=True)
class MeasurementOutcome:
    kind: str  # no_signal | near | direction
    svd_deg: float | None = None
    jammer_channel: int | None = None


@dataclass(slots=True)
class ClearOutcome:
    kind: str  # success | no_target_in_range
    jammer_channel: int | None = None


@dataclass(slots=True)
class World:
    """一次测试的完整状态机（进入 /enter 后开始计时）。"""

    case: Case
    robot_id: str
    arena_id: str = "default"
    clear_requires_switch: bool = False
    #: ``True`` 时把**失败的** ``/clear`` 也按 5s 计（旧 mock 口径，仅用于复现
    #: 2026-09-13 之前归档的基准数字）。默认 ``False`` = 附件1 §2.3 的正确口径：
    #: 未发现 3s、已清除 5s。两者对同一动作轨迹满足
    #: ``T_legacy = T_correct + 2 * N_clear_failure``。
    legacy_clear_timing: bool = False
    max_virtual_duration_s: float = MAX_VIRTUAL_DURATION_S
    max_real_duration_s: float = MAX_REAL_DURATION_S

    entered: bool = False
    position: Point = field(default_factory=lambda: Point(0.0, 0.0))
    channel: int = 1
    virtual_time_s: float = 0.0
    cleared: set[int] = field(default_factory=set)
    move_distance_m: float = 0.0
    channel_switch_count: int = 0
    measure_count: int = 0
    clear_count: int = 0
    exit_reason: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    # -- 计时 -----------------------------------------------------------
    def _advance_to(self, target: Point) -> None:
        distance = self.position.distance_to(target)
        self.move_distance_m += distance
        self.virtual_time_s += distance / MOVE_SPEED_MPS
        self.position = target

    def _select_channel(self, channel: int) -> None:
        if channel != self.channel:
            self.channel_switch_count += 1
            self.virtual_time_s += CHANNEL_SWITCH_SECONDS
        self.channel = channel

    # -- 动作 -----------------------------------------------------------
    def enter(self) -> dict[str, Any]:
        self.entered = True
        self.position = Point(0.0, 0.0)
        self.channel = 1
        self.virtual_time_s = 0.0
        return {
            "accepted": True,
            "real_timestamp_ms": 0,
            "virtual_time_s": self.virtual_time_s,
            "max_virtual_duration_s": self.max_virtual_duration_s,
            "max_real_duration_s": self.max_real_duration_s,
            "remaining_real_duration_s": self.max_real_duration_s,
        }

    def measure(self, position: Point, channel: int) -> tuple[dict[str, Any], MeasurementOutcome]:
        self.measure_count += 1
        self._advance_to(position)
        self._select_channel(channel)
        self.virtual_time_s += MEASURE_SECONDS

        outcome = self._evaluate(position, channel)
        response: dict[str, Any] = {
            "accepted": True,
            "real_timestamp_ms": int(self.virtual_time_s * 1000),
            "virtual_time_s": self.virtual_time_s,
            "measure_result": outcome.kind,
        }
        if outcome.kind == "direction" and outcome.svd_deg is not None:
            response["svd_deg"] = round(outcome.svd_deg, 2)
        return response, outcome

    def _evaluate(self, position: Point, channel: int) -> MeasurementOutcome:
        jammer = self.case.by_channel().get(channel)
        if jammer is None or channel in self.cleared:
            return MeasurementOutcome("no_signal")
        distance = position.distance_to(jammer.position)
        if distance > jammer.effective_radius_m:
            return MeasurementOutcome("no_signal", jammer_channel=channel)
        if not jammer.covers(position):
            return MeasurementOutcome("no_signal", jammer_channel=channel)
        if distance <= NEAR_RANGE_M:
            return MeasurementOutcome("near", jammer_channel=channel)
        true_bearing = degrees_atan2(
            jammer.position.y - position.y, jammer.position.x - position.x
        )
        measured = (true_bearing + bearing_error(self.case.seed, channel, position)) % 360.0
        return MeasurementOutcome("direction", measured, channel)

    def clear(self, position: Point, channel: int) -> tuple[dict[str, Any], ClearOutcome]:
        self.clear_count += 1
        self._advance_to(position)
        if self.clear_requires_switch:
            self._select_channel(channel)

        jammer = self.case.by_channel().get(channel)
        hit = (
            jammer is not None
            and channel not in self.cleared
            and position.distance_to(jammer.position) <= OPTICAL_RANGE_M
        )
        # 附件1 §2.3：先光学精确定位 3s；命中才再花 2s 激光清除。
        # 未发现 = 3s，成功 = 5s；且 /clear 不切换测向机频道（不产生 1s 切换）。
        self.virtual_time_s += OPTICAL_SECONDS
        if hit or self.legacy_clear_timing:
            self.virtual_time_s += CLEAR_SECONDS

        outcome: ClearOutcome
        if hit:
            self.cleared.add(channel)
            outcome = ClearOutcome("success", channel)
        else:
            outcome = ClearOutcome("no_target_in_range")
        response = {
            "accepted": True,
            "real_timestamp_ms": int(self.virtual_time_s * 1000),
            "virtual_time_s": self.virtual_time_s,
            "clear_result": outcome.kind,
        }
        return response, outcome

    def exit(self) -> dict[str, Any]:
        self.exit_reason = "user_exit"
        return {
            "accepted": True,
            "real_timestamp_ms": int(self.virtual_time_s * 1000),
            "virtual_time_s": self.virtual_time_s,
            "exit_reason": self.exit_reason,
        }

    # -- 统计 -----------------------------------------------------------
    @property
    def cleared_count(self) -> int:
        return len(self.cleared)

    @property
    def cleared_ratio(self) -> float:
        return self.cleared_count / self.case.n_jammers if self.case.n_jammers else 0.0

    @property
    def average_clear_time_s(self) -> float | None:
        if not self.cleared:
            return None
        return self.virtual_time_s / self.cleared_count

    def summary(self) -> dict[str, Any]:
        return {
            "seed": self.case.seed,
            "n_jammers": self.case.n_jammers,
            "cleared_count": self.cleared_count,
            "cleared_ratio": self.cleared_ratio,
            "virtual_time_s": self.virtual_time_s,
            "average_clear_time_s": self.average_clear_time_s,
            "move_distance_m": self.move_distance_m,
            "measure_count": self.measure_count,
            "clear_count": self.clear_count,
            "channel_switch_count": self.channel_switch_count,
            "cleared_channels": sorted(self.cleared),
            "uncleared_channels": sorted(
                j.channel for j in self.case.jammers if j.channel not in self.cleared
            ),
            "jammers": [j.as_dict() for j in self.case.jammers],
        }
