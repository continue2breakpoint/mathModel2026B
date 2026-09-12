# Actual coverage state from coverage.py.
@dataclass(slots=True)
class ChannelCoverState:
    channel: int
    region_mask: int
    covered_mask: int = 0
    plan_radius_m: float = 950.0
    n_cells: int = 0

    @property
    def uncovered_mask(self) -> int:
        return self.region_mask & ~self.covered_mask

    @property
    def uncovered_cells(self) -> int:
        return self.uncovered_mask.bit_count()

    @property
    def complete(self) -> bool:
        return self.uncovered_cells <= 0
