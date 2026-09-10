from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .geometry import Point


class ChannelStatus(StrEnum):
    UNKNOWN = "unknown"
    DETECTED = "detected"
    LOCALIZING = "localizing"
    CLEARED = "cleared"


@dataclass(slots=True)
class ChannelState:
    channel: int
    status: ChannelStatus = ChannelStatus.UNKNOWN
    bearings: list[tuple[Point, float]] = field(default_factory=list)

    def add_bearing(self, point: Point, angle_deg: float) -> None:
        self.bearings.append((point, angle_deg))
        self.status = (
            ChannelStatus.DETECTED
            if len(self.bearings) == 1
            else ChannelStatus.LOCALIZING
        )

    def mark_cleared(self) -> None:
        self.status = ChannelStatus.CLEARED


@dataclass(slots=True)
class DogState:
    position: Point = Point(0.0, 0.0)
    measurement_channel: int = 1
    channels: dict[int, ChannelState] = field(
        default_factory=lambda: {i: ChannelState(i) for i in range(1, 21)}
    )
