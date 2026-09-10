from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MeasureKind(StrEnum):
    NO_SIGNAL = "no_signal"
    NEAR = "near"
    DIRECTION = "direction"


class ClearKind(StrEnum):
    SUCCESS = "success"
    NO_TARGET = "no_target_in_range"


@dataclass(frozen=True, slots=True)
class MeasureResult:
    kind: MeasureKind
    svd_deg: float | None = None

    def __post_init__(self) -> None:
        if self.kind == MeasureKind.DIRECTION and self.svd_deg is None:
            raise ValueError("direction result requires svd_deg")
        if self.kind != MeasureKind.DIRECTION and self.svd_deg is not None:
            raise ValueError("svd_deg is only valid for direction result")


@dataclass(frozen=True, slots=True)
class ClearResult:
    kind: ClearKind
