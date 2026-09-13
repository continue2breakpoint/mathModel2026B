"""机器狗状态与逐频道集合估计。

关键点
------
同一地点对同一干扰源的示向度误差是**固定**的（附录2-1），重复检测不改变读数，
所以不能靠重复采样取平均来消噪；只能靠"不同检测点"的扇形求交收缩可行域。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .geometry import Circle, Point, feasible_region, polygon_centroid, region_clear_circle
from .protocol import (
    CHANNEL_SWITCH_SECONDS,
    MAX_CHANNEL,
    MIN_CHANNEL,
    OPTICAL_RANGE_M,
)

ALL_CHANNELS: tuple[int, ...] = tuple(range(MIN_CHANNEL, MAX_CHANNEL + 1))


class ChannelStatus(StrEnum):
    UNKNOWN = "unknown"
    DETECTED = "detected"
    LOCALIZING = "localizing"
    READY = "ready"
    CLEARED = "cleared"


@dataclass(slots=True)
class ChannelState:
    channel: int
    status: ChannelStatus = ChannelStatus.UNKNOWN
    readings: list[tuple[Point, float]] = field(default_factory=list)
    cleared_at: Point | None = None
    clear_attempts: int = 0
    clear_failures: int = 0
    #: 该频道被实际探测（含 no_signal）的次数，用于判断"覆盖扫描是否已探到本频道"
    probe_count: int = 0

    # -- 读数 -----------------------------------------------------------
    def add_bearing(self, point: Point, angle_deg: float) -> None:
        self.readings.append((point, angle_deg))
        if self.status in (ChannelStatus.UNKNOWN,):
            self.status = ChannelStatus.DETECTED
        elif self.status is ChannelStatus.DETECTED and len(self.readings) >= 2:
            self.status = ChannelStatus.LOCALIZING

    def mark_near(self) -> None:
        """在有效范围内但距离过近（<=5m），可直接光学定位清除。"""
        self.status = ChannelStatus.READY

    def mark_cleared(self, position: Point) -> None:
        self.status = ChannelStatus.CLEARED
        self.cleared_at = position

    def mark_clear_failed(self) -> None:
        self.clear_attempts += 1
        self.clear_failures += 1
        if self.status is ChannelStatus.CLEARED:
            self.status = ChannelStatus.LOCALIZING

    # -- 可行域 ---------------------------------------------------------
    @property
    def distinct_points(self) -> int:
        seen: set[tuple[float, float]] = set()
        for p, _ in self.readings:
            seen.add((round(p.x, 6), round(p.y, 6)))
        return len(seen)

    def region(self, radius: float | None = None) -> list[Point]:
        if radius is None:
            return feasible_region(self.readings)
        return feasible_region(self.readings, radius)

    def estimate(self) -> Point | None:
        return polygon_centroid(self.region())

    def clear_circle(self, tolerance_m: float = OPTICAL_RANGE_M) -> Circle | None:
        return region_clear_circle(self.region(), tolerance_m)

    def diameter(self) -> float:
        from .geometry import polygon_diameter

        return polygon_diameter(self.region())

    @property
    def is_cleared(self) -> bool:
        return self.status is ChannelStatus.CLEARED

    @property
    def is_open(self) -> bool:
        """是否还需要继续处理（未清除）。"""
        return self.status is not ChannelStatus.CLEARED


@dataclass(slots=True)
class DogState:
    position: Point = Point(0.0, 0.0)
    measurement_channel: int = MIN_CHANNEL
    channels: dict[int, ChannelState] = field(
        default_factory=lambda: {c: ChannelState(c) for c in ALL_CHANNELS}
    )

    # -- 计数（用于统计与调参）-------------------------------------------
    measure_count: int = 0
    measure_accepted_count: int = 0
    clear_count: int = 0
    clear_success_count: int = 0
    clear_failure_count: int = 0
    channel_switch_count: int = 0
    move_distance_m: float = 0.0
    scan_points_visited: int = 0
    virtual_time_s: float = 0.0
    enter_virtual_time_s: float = 0.0

    # -- 移动 / 频道 -----------------------------------------------------
    def move_to(self, target: Point) -> float:
        """按直线移动，返回移动距离（米）。"""
        distance = self.position.distance_to(target)
        self.move_distance_m += distance
        self.position = target
        return distance

    def select_channel(self, channel: int) -> None:
        if channel != self.measurement_channel:
            self.channel_switch_count += 1
        self.measurement_channel = channel

    def observe(self, virtual_time_s: float) -> None:
        if virtual_time_s > 0:
            self.virtual_time_s = virtual_time_s

    # -- 查询 -----------------------------------------------------------
    @property
    def cleared_channels(self) -> list[int]:
        return [c for c, st in self.channels.items() if st.is_cleared]

    @property
    def open_channels(self) -> list[int]:
        return [c for c, st in self.channels.items() if st.is_open]

    @property
    def detected_channels(self) -> list[int]:
        return [c for c, st in self.channels.items() if st.readings or st.status in (
            ChannelStatus.READY, ChannelStatus.CLEARED
        )]

    @property
    def uncleared_detected(self) -> list[int]:
        return [c for c in self.detected_channels if not self.channels[c].is_cleared]

    def snapshot(self) -> dict:
        return {
            "position": [self.position.x, self.position.y],
            "measurement_channel": self.measurement_channel,
            "virtual_time_s": self.virtual_time_s,
            "measure_count": self.measure_count,
            "measure_accepted_count": self.measure_accepted_count,
            "clear_count": self.clear_count,
            "clear_success_count": self.clear_success_count,
            "clear_failure_count": self.clear_failure_count,
            "channel_switch_count": self.channel_switch_count,
            "move_distance_m": self.move_distance_m,
            "cleared_channels": self.cleared_channels,
            "detected_channels": self.detected_channels,
            "channels": {
                c: {
                    "status": str(st.status),
                    "readings": len(st.readings),
                    "distinct_points": st.distinct_points,
                }
                for c, st in self.channels.items()
                if st.status is not ChannelStatus.UNKNOWN
            },
        }


def switch_cost(from_channel: int, to_channel: int) -> float:
    """附录2-4：切换频道耗时 1 秒（同频道不切换）。"""
    return 0.0 if from_channel == to_channel else CHANNEL_SWITCH_SECONDS
