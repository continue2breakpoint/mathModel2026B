"""实时知识矩阵：``channel × path`` 的观测台账（dataType.txt 的可执行版本）。

设计来源
--------
用户提出的 ``dataType.txt`` 形态::

    | Channel\\Path | (x1, y1) | (x2, y2) | ... |
    | - | - | - | - |
    | 1 | { "status": "find", "angle": {"lower": 2, "upper": 4} } | ... |
    | 2 | { "status": "not find", "angle": {"lower": 0, "upper": 359} } | ... |
    | 3 | { "status": "not clear", "angle": {"lower": 0, "upper": 359} } | ... |
    | 4 | { "status": "not measure" } | ... |

即：**行 = 频道（固定 20 行）**、**列 = 机器狗走过的路径点（随每次移动增长）**、
**单元格 = 该点该频道的一次观测结论**。本模块把它实现成机器狗运行期的数据结构，
并保持与 ``dataType.txt`` 的双向可读写（``loads_table`` / ``as_table``）。

为什么这个结构对本题特别合适
------------------------------
1. **频道是天然的行主键**：附录1-1 说"每个干扰源的频道互不相同"，所以
   ``channel`` 是干扰源的唯一 ID；一行 = 一个候选目标的全部证据链。
   换成"位置为行"就丢掉了这个一一对应，反而更差。
2. **列随移动增长、但受 5m 物理分辨率限制**：本题"同一地点重复检测读数不变"
   （附录2-1），所以在同一格重复测同一频道**信息量为 0**。把列按
   :data:`DEFAULT_CELL_M` 量化（并允许 5m 的同一位置合并）之后，
   "这一格还没测过" 变成 O(1) 查询，可以直接用来剪枝。
3. **负例与正例同等重要**：``find`` 给出 ±1° 扇形（正例）；
   ``not_find`` 给出"该点到源的距离 > 有效接收半径 R"（负例）。
   负例是**唯一**能直接反推 R 的证据，而 R 又决定"还有多大区域没被覆盖"。
   原来的实现把 ``no_signal`` 直接丢掉，等于把 R 的先验永远锁死在最坏值 1000m。
4. **可审计**：覆盖完备性（"所有源必定被探测过"）在这个结构上就是一句
   可计算的话——见 :mod:`mathmodel2026b.coverage`。

量化误差的处理
--------------
格子是离散的，而真实位置是连续的。本模块**只把矩阵当作决策索引**：
凡是"反向推断源位置 / 收敛到 20m"的计算，仍然走
:mod:`mathmodel2026b.state` 里的连续凸多边形可行域。矩阵负责回答
"哪里还没看过""这个频道还缺哪块面积"，几何负责回答"源在哪"。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .geometry import Point
from .protocol import (
    ARENA_RADIUS_M,
    MAX_CHANNEL,
    MAX_EFFECTIVE_RADIUS_M,
    MIN_CHANNEL,
    MIN_EFFECTIVE_RADIUS_M,
)

#: 路径列量化步长（米）。取 120m ≈ "机器狗走 24s"，远大于 5m 的重测无效尺度，
#: 又远小于最小有效接收半径 1000m，因此既不会误合并不同检测点，也不会把
#: 覆盖判定做粗。数据表 ``dataType.txt`` 里的 "(x, y)" 就是格心坐标。
DEFAULT_CELL_M: float = 120.0
#: 平面坐标绝对值上限（格坐标量化用；题目允许提交到 2e6）。
_COORD_LIMIT_M: float = 2_000_000.0


class CellStatus(StrEnum):
    """单元格取值（与 ``dataType.txt`` 的 status 字符串一一对应）。"""

    #: 该点该频道未被检测过（表里写 ``not measure``）
    NOT_MEASURED = "not_measure"
    #: 测到信号并取到示向度（表里写 ``find``）
    FIND = "find"
    #: 在该点收不到该频道信号（表里写 ``not find``）—— 强负例
    NOT_FIND = "not_find"
    #: 距离 <= 5m 且落在覆盖角内，信号过强无法测向，可直接清除
    NEAR = "near"
    #: 该频道已被清除（表里写 ``not clear`` = 还没清 / ``cleared`` = 已清）
    CLEARED = "cleared"
    #: 尝试清除但光学没命中（清除点离真值 > 20m）
    CLEAR_FAILED = "clear_failed"

    # -- 兼容 dataType.txt 的写法 --------------------------------------
    @property
    def table_text(self) -> str:
        return _STATUS_TO_TABLE[self]

    @classmethod
    def from_table_text(cls, text: str) -> "CellStatus":
        key = text.strip().lower().replace("-", "_").replace(" ", "_")
        return _TABLE_TO_STATUS[key]


_STATUS_TO_TABLE: dict[CellStatus, str] = {
    CellStatus.NOT_MEASURED: "not measure",
    CellStatus.FIND: "find",
    CellStatus.NOT_FIND: "not find",
    CellStatus.NEAR: "near",
    CellStatus.CLEARED: "cleared",
    CellStatus.CLEAR_FAILED: "not clear",
}
_TABLE_TO_STATUS: dict[str, CellStatus] = {
    "not_measure": CellStatus.NOT_MEASURED,
    "not_find": CellStatus.NOT_FIND,
    "not_clear": CellStatus.CLEAR_FAILED,
    "notfound": CellStatus.NOT_FIND,
    "find": CellStatus.FIND,
    "near": CellStatus.NEAR,
    "clear": CellStatus.CLEARED,
    "cleared": CellStatus.CLEARED,
    "clear_failed": CellStatus.CLEAR_FAILED,
}


# ---------------------------------------------------------------------------
# 格坐标 <-> 世界坐标
# ---------------------------------------------------------------------------
def _clamp_coord(value: float) -> float:
    return max(-_COORD_LIMIT_M, min(_COORD_LIMIT_M, float(value)))


def to_cell_index(value: float, cell_m: float) -> int:
    """把一维坐标量化成整数格号（负数向下取整，保证连续值映射到连续格）。"""
    return int(math.floor((_clamp_coord(value) + _COORD_LIMIT_M) / cell_m))


def from_cell_index(index: int, cell_m: float) -> float:
    """格号 -> 格心坐标。"""
    return (index + 0.5) * cell_m - _COORD_LIMIT_M


@dataclass(frozen=True, slots=True)
class Cell:
    """一个路径格（一列）。"""

    key: tuple[int, int]

    @property
    def center(self) -> Point:
        raise NotImplementedError


@dataclass(slots=True)
class CellInfo:
    """一列的元数据：格号 + 格心 + 首次到达时间。"""

    col: int
    row: int
    cell_m: float
    center_x: float
    center_y: float
    first_seen_s: float
    visits: int = 1

    @property
    def center(self) -> Point:
        return Point(self.center_x, self.center_y)

    @property
    def key(self) -> tuple[int, int]:
        return (self.col, self.row)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": f"{self.col}:{self.row}",
            "x": self.center_x,
            "y": self.center_y,
            "first_seen_s": self.first_seen_s,
            "visits": self.visits,
        }


@dataclass(slots=True)
class CellEvidence:
    """一个单元格的多次观测汇总（同一格同频道重复检测读数不变，故只留最新+计数）。

    ``point`` 是**真实检测坐标**，不是格心。这一点很关键：格边长 :data:`DEFAULT_CELL_M`
    =120m，若用格心去算示向度，1200m 处会引入约 3° 的假误差（实测把可行域从
    7m 撑到 88m，直接导致清除失败）。所以矩阵只把格子当作**索引**（"这一格测过没有"），
    几何一律用 ``point`` 的真值。
    """

    status: CellStatus
    #: 该单元格最后一次检测的**真实坐标**
    point: Point | None = None
    #: ``find`` 时的示向度（度）
    bearing_deg: float | None = None
    #: 该格该频道被检测过几次（>1 说明发生了无信息的重测，可用于诊断）
    probes: int = 0
    #: 最后一次观测的虚拟时间
    last_time_s: float = 0.0
    #: ``clear_failed`` 的失败次数
    failures: int = 0

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"status": self.status.table_text}
        if self.status is CellStatus.FIND and self.bearing_deg is not None:
            lo = (self.bearing_deg - 1.0) % 360.0
            hi = (self.bearing_deg + 1.0) % 360.0
            data["angle"] = {"lower": round(lo, 2), "upper": round(hi, 2), "svd": round(self.bearing_deg, 2)}
        elif self.status in (CellStatus.NOT_FIND, CellStatus.NEAR, CellStatus.CLEARED, CellStatus.CLEAR_FAILED):
            data["angle"] = {"lower": 0, "upper": 359}
        if self.failures:
            data["failures"] = self.failures
        return data


# ---------------------------------------------------------------------------
# 知识矩阵
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class KnowledgeMatrix:
    """``channel × path`` 观测台账。

    行主键是频道（1..20，长度固定），列是机器狗走过并被量化的路径格。
    ``.cells[channel][(col, row)] = CellEvidence``。

    另外维护两个**按频道聚合的派生量**（每次写入后增量更新，O(1) 读）：

    * :attr:`observed_cells` —— 该频道已被检测过的格数（"看过多少地方"）
    * :attr:`found_cells` / :attr:`not_find_cells` —— 正例 / 负例格数
    * :attr:`max_find_dist_m` / :attr:`min_not_find_dist_m` —— 反推 R 的两端
    """

    cell_m: float = DEFAULT_CELL_M
    channels: tuple[int, ...] = tuple(range(MIN_CHANNEL, MAX_CHANNEL + 1))
    cells: dict[int, dict[tuple[int, int], CellEvidence]] = field(default_factory=dict)
    columns: dict[tuple[int, int], CellInfo] = field(default_factory=dict)

    #: 被移动覆盖（但没有做任何检测）的格；用于"路径长度"与可视化
    _visits: dict[tuple[int, int], int] = field(default_factory=dict)
    #: 每个频道的统计缓存
    _stats: dict[int, dict[str, float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for channel in self.channels:
            self.cells.setdefault(channel, {})
            self._stats.setdefault(
                channel,
                {
                    "probes": 0.0,
                    "find": 0.0,
                    "not_find": 0.0,
                    "near": 0.0,
                    "cleared": 0.0,
                    "failures": 0.0,
                    "max_find_dist": 0.0,
                    "min_not_find_dist": math.inf,
                },
            )

    # -- 列（路径）管理 --------------------------------------------------
    def cell_key(self, point: Point) -> tuple[int, int]:
        return (to_cell_index(point.x, self.cell_m), to_cell_index(point.y, self.cell_m))

    def touch(self, point: Point, virtual_time_s: float = 0.0) -> CellInfo:
        """登记"机器狗到过这里"。重复到达只累加 visits。"""
        key = self.cell_key(point)
        info = self.columns.get(key)
        if info is None:
            info = CellInfo(
                col=key[0],
                row=key[1],
                cell_m=self.cell_m,
                center_x=from_cell_index(key[0], self.cell_m),
                center_y=from_cell_index(key[1], self.cell_m),
                first_seen_s=virtual_time_s,
            )
            self.columns[key] = info
        else:
            info.visits += 1
        self._visits[key] = self._visits.get(key, 0) + 1
        return info

    @property
    def path_length(self) -> int:
        """已登记的路径格数（= dataType.txt 的列数）。"""
        return len(self.columns)

    def column_points(self) -> list[Point]:
        return [info.center for info in sorted(self.columns.values(), key=lambda i: i.first_seen_s)]

    # -- 写入 ------------------------------------------------------------
    def record_measure(
        self,
        point: Point,
        channel: int,
        status: CellStatus,
        *,
        bearing_deg: float | None = None,
        virtual_time_s: float = 0.0,
        source_distance_m: float | None = None,
    ) -> CellEvidence:
        """记录一次检测结果。

        ``status`` 只接受 :class:`CellStatus` 中与检测相关的取值：
        ``FIND`` / ``NOT_FIND`` / ``NEAR`` / ``CLEARED``（清除后再测一定是 not_find，
        但调用方可以直接记 CLEARED 表示"该频道已收工"）。

        ``source_distance_m`` 只在**已知真值**的离线分析里传（mock / 复盘），
        用来统计 R 的可辨识性；线上不知道真值，R 只能靠
        ``max_find_dist`` / ``min_not_find_dist`` 夹逼。
        """
        info = self.touch(point, virtual_time_s)
        row = self.cells[channel]
        existing = row.get(info.key)
        if existing is None:
            existing = CellEvidence(status=status, point=Point(point.x, point.y), bearing_deg=bearing_deg)
            row[info.key] = existing
        else:
            # 同一格重复检测：读数不变（附录2-1），只更新状态与计数
            existing.status = status
            existing.point = Point(point.x, point.y)
            existing.bearing_deg = bearing_deg
        existing.probes += 1
        existing.last_time_s = virtual_time_s

        stat = self._stats[channel]
        stat["probes"] += 1
        if status is CellStatus.FIND and bearing_deg is not None:
            stat["find"] += 1
            if source_distance_m is not None:
                stat["max_find_dist"] = max(stat["max_find_dist"], source_distance_m)
        elif status is CellStatus.NOT_FIND:
            stat["not_find"] += 1
            # 该格到"当前已知的源可行域"的距离是 R 的下界证据；
            # 这里先记绝对距离，由 coverage 模块用可行域精修。
            if source_distance_m is not None:
                stat["min_not_find_dist"] = min(stat["min_not_find_dist"], source_distance_m)
        elif status is CellStatus.NEAR:
            stat["near"] += 1
        elif status is CellStatus.CLEARED:
            stat["cleared"] += 1
        return existing

    def record_clear_failed(self, point: Point, channel: int, virtual_time_s: float = 0.0) -> None:
        info = self.touch(point, virtual_time_s)
        row = self.cells[channel]
        existing = row.get(info.key)
        if existing is None:
            row[info.key] = CellEvidence(
                status=CellStatus.CLEAR_FAILED,
                point=Point(point.x, point.y),
                last_time_s=virtual_time_s,
                failures=1,
            )
        else:
            existing.failures += 1
            if existing.status is not CellStatus.CLEARED:
                existing.status = CellStatus.CLEAR_FAILED
        self._stats[channel]["failures"] += 1

    def mark_channel_cleared(self, channel: int) -> None:
        """整行标记为已清除（附录1-1：一频道一源，清掉就再也不用测这行）。"""
        self._stats[channel]["cleared"] = 1.0
        for evidence in self.cells[channel].values():
            evidence.status = CellStatus.CLEARED

    # -- 读取 ------------------------------------------------------------
    def evidence(self, channel: int, key: tuple[int, int]) -> CellEvidence | None:
        return self.cells.get(channel, {}).get(key)

    def status_at(self, point: Point, channel: int) -> CellStatus:
        info = self.columns.get(self.cell_key(point))
        if info is None:
            return CellStatus.NOT_MEASURED
        ev = self.cells.get(channel, {}).get(info.key)
        return ev.status if ev is not None else CellStatus.NOT_MEASURED

    def is_measured(self, point: Point, channel: int) -> bool:
        """该点该频道是否已经测过（含 not_find）。同格重测无信息，可直接跳过。"""
        return self.status_at(point, channel) is not CellStatus.NOT_MEASURED

    def probes(self, channel: int) -> int:
        return int(self._stats[channel]["probes"])

    def total_probes(self) -> int:
        return int(sum(s["probes"] for s in self._stats.values()))

    def found(self, channel: int) -> int:
        return int(self._stats[channel]["find"])

    def not_found(self, channel: int) -> int:
        return int(self._stats[channel]["not_find"])

    def is_cleared(self, channel: int) -> bool:
        return self._stats[channel]["cleared"] > 0

    def found_points(self, channel: int) -> list[Point]:
        """该频道所有 ``find`` 的**真实检测坐标**（用于往可行域喂读数）。"""
        out: list[Point] = []
        for ev in self.cells[channel].values():
            if ev.status is CellStatus.FIND:
                out.append(ev.point or Point(0.0, 0.0))
        return out

    def observation_points(self, channel: int, status: CellStatus) -> list[Point]:
        """某状态下该频道的所有真实检测坐标。"""
        return [
            (ev.point or Point(0.0, 0.0))
            for ev in self.cells[channel].values()
            if ev.status is status and ev.point is not None
        ]

    def detected_channels(self) -> list[int]:
        return [c for c in self.channels if self._stats[c]["find"] > 0 or self._stats[c]["near"] > 0]

    def open_channels(self) -> list[int]:
        return [c for c in self.channels if not self.is_cleared(c)]

    @property
    def cleared_channels(self) -> list[int]:
        return [c for c in self.channels if self.is_cleared(c)]

    def exclude_points(self, channel: int, radius_m: float) -> list[Point]:
        """负例点集合：这些点周围 ``radius_m`` 范围内**不可能**有该频道的源。

        对全向源严格成立（``not_find`` ⇒ 距离超过有效接收半径）。
        对定向源这是**保守**假设（反方向的 ``not_find`` 可能只是锥没照到），
        因此调用方必须传入最保守的 :data:`~mathmodel2026b.protocol.MAX_EFFECTIVE_RADIUS_M`。
        """
        return [
            ev.point
            for ev in self.cells.get(channel, {}).values()
            if ev.status is CellStatus.NOT_FIND and ev.point is not None
        ]

    #: 有效接收半径 R 的夹逼区间（米）。只依赖可观测量，线上同样可用。
    def radius_bounds(self, channel: int) -> tuple[float, float]:
        """由 ``find`` / ``not_find`` 反推 R 的上下界。

        * 上界 = 附录2-2 的 1500m（先验）。
        * 下界 = 该频道所有 ``not_find`` 格到"源可行域"的**最小**距离——
          在那些点上收不到，说明 R 比这个距离还小。
          线上没有真值，这里用"当前最优位置估计"近似；估计误差会通过
          :func:`mathmodel2026b.coverage.effective_radius_estimate` 里的
          保守折扣吸收。
        """
        stat = self._stats[channel]
        lower = float(stat["min_not_find_dist"])
        if math.isinf(lower):
            lower = MIN_EFFECTIVE_RADIUS_M
        return (max(0.0, lower), MAX_EFFECTIVE_RADIUS_M)

    def note_not_find_distance(self, channel: int, distance_m: float) -> None:
        """外部（coverage 模块）用可行域精修后回写 R 的下界证据。"""
        if distance_m < self._stats[channel]["min_not_find_dist"]:
            self._stats[channel]["min_not_find_dist"] = distance_m

    # -- 导出：dataType.txt 形态 ----------------------------------------
    def active_columns(self, arena_only: bool = True, limit: int | None = None) -> list[CellInfo]:
        infos = [i for i in self.columns.values() if i.first_seen_s >= 0]
        if arena_only:
            infos = [i for i in infos if math.hypot(i.center_x, i.center_y) <= ARENA_RADIUS_M + self.cell_m]
        infos.sort(key=lambda i: (i.first_seen_s, i.col, i.row))
        if limit is not None:
            infos = infos[:limit]
        return infos

    def as_table(
        self,
        *,
        arena_only: bool = True,
        limit_cols: int | None = 40,
        channels: Sequence[int] | None = None,
        cell_mode: str = "center",
    ) -> str:
        """导出 ``dataType.txt`` 形态的 Markdown 表（行=频道，列=路径点）。

        ``cell_mode="center"`` 时列头写格心 ``(x, y)``（量化后的路径坐标）；
        ``cell_mode="observed"`` 时只写真实观测点（去掉从未检测过的路径格，
        表更紧凑，但会丢"到过但没测"的信息）。
        """
        cols = self.active_columns(arena_only=arena_only, limit=limit_cols)
        chans = list(channels) if channels is not None else list(self.channels)
        if cell_mode == "observed":
            used = {key for c in chans for key in self.cells[c]}
            cols = [i for i in cols if i.key in used]
        head = "| Channel\\\\Path | " + " | ".join(f"({i.center_x:.0f}, {i.center_y:.0f})" for i in cols) + " |"
        sep = "| - |" + " - |" * len(cols)
        lines = [head, sep]
        for channel in chans:
            row = self.cells[channel]
            cells: list[str] = []
            for info in cols:
                ev = row.get(info.key)
                payload = ev.as_dict() if ev is not None else {"status": CellStatus.NOT_MEASURED.table_text}
                cells.append(json.dumps(payload, ensure_ascii=False, separators=(", ", ": ")))
            lines.append(f"| {channel} | " + " | ".join(cells) + " |")
        return "\n".join(lines) + "\n"

    def loads_table(self, text: str) -> int:
        """从 ``dataType.txt`` 形态的 Markdown 表回填（返回写入单元格数）。

        只解析 ``|` 行；列头解析 ``(x, y)``；单元格解析 JSON 里的 ``status``
        与 ``angle``（用角区间中心反推示向度）。
        """
        written = 0
        cols: list[tuple[float, float]] = []
        header_seen = False
        for raw in text.splitlines():
            line = raw.strip()
            if not line.startswith("|"):
                continue
            parts = [p.strip() for p in line.strip("|").split("|")]
            if not parts:
                continue
            if not header_seen:
                if parts[0].lower().startswith("channel"):
                    for part in parts[1:]:
                        coord = _parse_coord(part)
                        if coord is None:
                            return written
                        cols.append(coord)
                    header_seen = True
                    continue
                continue
            if set(parts[0]) <= {"-", " "}:
                continue
            try:
                channel = int(parts[0])
            except ValueError:
                continue
            if channel not in self.channels:
                continue
            for index, part in enumerate(parts[1:]):
                if index >= len(cols):
                    break
                status, bearing = _parse_cell(part)
                if status is None:
                    continue
                point = Point(cols[index][0], cols[index][1])
                if status is CellStatus.NOT_MEASURED:
                    self.touch(point)
                    continue
                # 表里只有格心坐标：把格心当作该格的观测点写回（数据表本身的精度上限）
                self.record_measure(point, channel, status, bearing_deg=bearing)
                written += 1
        return written

    # -- 导出：机读 -------------------------------------------------------
    def as_dict(self, *, include_cells: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema": "knowledge-matrix/v1",
            "cell_size_m": self.cell_m,
            "channels": list(self.channels),
            "n_columns": self.path_length,
            "n_probes": self.total_probes(),
            "columns": [i.as_dict() for i in self.columns.values()],
            "stats": {
                str(c): {
                    "probes": int(s["probes"]),
                    "find": int(s["find"]),
                    "not_find": int(s["not_find"]),
                    "near": int(s["near"]),
                    "cleared": bool(s["cleared"]),
                    "clear_failures": int(s["failures"]),
                    "radius_lower_bound_m": self.radius_bounds(c)[0],
                }
                for c, s in self._stats.items()
                if s["probes"] or s["cleared"]
            },
        }
        if include_cells:
            data["cells"] = {
                str(c): {f"{k[0]}:{k[1]}": v.as_dict() for k, v in rows.items()}
                for c, rows in self.cells.items()
                if rows
            }
        return data

    def summary_line(self) -> str:
        detected = self.detected_channels()
        return (
            f"matrix {self.path_length} cols × {len(self.channels)} ch, "
            f"probes={self.total_probes()}, detected={len(detected)}, "
            f"cleared={len([c for c in self.channels if self.is_cleared(c)])}"
        )


def _parse_coord(text: str) -> tuple[float, float] | None:
    cleaned = text.strip().strip("()[]")
    if not cleaned or cleaned in {"...", "…"}:
        return None
    for sep in (",", "，", " "):
        if sep in cleaned:
            a, _, b = cleaned.partition(sep)
            try:
                return (float(a), float(b.strip()))
            except ValueError:
                return None
    return None


def _parse_cell(text: str) -> tuple[CellStatus | None, float | None]:
    raw = text.strip()
    if not raw or raw in {"-", "..."}:
        return (None, None)
    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
        except ValueError:
            return (None, None)
        status_raw = str(payload.get("status", "")).strip()
        try:
            status = CellStatus.from_table_text(status_raw)
        except KeyError:
            return (None, None)
        bearing: float | None = None
        angle = payload.get("angle")
        if isinstance(angle, Mapping) and "svd" in angle:
            bearing = float(angle["svd"])
        elif status is CellStatus.FIND and isinstance(angle, Mapping):
            lo = float(angle.get("lower", 0.0))
            hi = float(angle.get("upper", 0.0))
            bearing = ((lo + hi) / 2.0) % 360.0
        return (status, bearing)
    try:
        return (CellStatus.from_table_text(raw), None)
    except KeyError:
        return (None, None)


def iter_cells(matrix: KnowledgeMatrix, channel: int) -> Iterator[tuple[CellInfo, CellEvidence]]:
    for key, ev in matrix.cells.get(channel, {}).items():
        info = matrix.columns.get(key)
        if info is not None:
            yield info, ev


def iter_evidence(observations: Iterable[CellEvidence]) -> Iterator[CellEvidence]:
    yield from observations
