# Actual data fields from knowledge.py; methods omitted.
@dataclass(slots=True)
class CellInfo:
    col: int
    row: int
    cell_m: float
    center_x: float
    center_y: float
    first_seen_s: float
    visits: int = 1

@dataclass(slots=True)
class CellEvidence:
    status: CellStatus
    point: Point | None = None
    bearing_deg: float | None = None
    probes: int = 0
    last_time_s: float = 0.0
    failures: int = 0

@dataclass(slots=True)
class KnowledgeMatrix:
    cell_m: float = DEFAULT_CELL_M
    channels: tuple[int, ...] = tuple(range(MIN_CHANNEL, MAX_CHANNEL + 1))
    cells: dict[int, dict[tuple[int, int], CellEvidence]] = field(default_factory=dict)
    columns: dict[tuple[int, int], CellInfo] = field(default_factory=dict)
    _visits: dict[tuple[int, int], int] = field(default_factory=dict)
    _stats: dict[int, dict[str, float]] = field(default_factory=dict)
