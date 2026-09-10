from __future__ import annotations

from abc import ABC, abstractmethod

from .client import SimulatorClient
from .geometry import regular_hexagon_scan_points
from .protocol import ClearKind, MeasureKind
from .state import ChannelStatus, DogState


class Strategy(ABC):
    @abstractmethod
    def run(self, client: SimulatorClient, state: DogState) -> None: ...


class Q3BaselineStrategy(Strategy):
    def __init__(self, ring_radius: float = 1400.0) -> None:
        self.scan_points = regular_hexagon_scan_points(ring_radius)

    def run(self, client: SimulatorClient, state: DogState) -> None:
        for point in self.scan_points:
            for channel in range(1, 21):
                channel_state = state.channels[channel]
                if channel_state.status == ChannelStatus.CLEARED:
                    continue

                result = client.measure(point, channel)
                state.position = point
                state.measurement_channel = channel

                if result.kind == MeasureKind.DIRECTION:
                    channel_state.add_bearing(point, result.svd_deg)
                elif result.kind == MeasureKind.NEAR:
                    clear_result = client.clear(point, channel)
                    if clear_result.kind == ClearKind.SUCCESS:
                        channel_state.mark_cleared()
