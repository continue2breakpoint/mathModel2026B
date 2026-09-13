"""robot-protocol-v1 线协议类型与解析。

字段与取值来自对 ``jammers-simulator.exe`` 的逆向（见
``login-jammers/analysis/ROBOT_AND_FLOW.md`` 第 2 节与
``login-jammers/linux-client/PROTOCOL.md``），以及前端行为日志里
``outcome`` 的取值集合：

    entered / no_signal / near / direction / success / no_target_in_range /
    user_exit / rejected

要点：
* 所有请求为 POST + ``application/json``，公共字段 ``arena_id``（必须为
  ``"default"``）、``robot_id``（必须等于登录队号）、``request_id``。
* 被接受的响应带 ``accepted: true`` 与 ``virtual_time_s``；
  被拒绝的响应为 ``{"accepted": false, ...}``。
* ``/measure`` 的结果字段是 ``measure_result``（字符串），仅在
  ``direction`` 时附带 ``svd_deg``（度，两位小数）。
* ``/clear`` 的结果字段是 ``clear_result``（字符串）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

ARENA_ID = "default"
ROBOT_PROTOCOL_VERSION = "robot-protocol-v1"

#: 附录1-1：干扰源频道取自 1..20
MIN_CHANNEL = 1
MAX_CHANNEL = 20


#: 附录2-5：调整天线朝向并取得稳定读数（或确认无信号）的时间
MEASURE_SECONDS = 5.0
#: 附录2-4：任意两个频道之间的切换时间
CHANNEL_SWITCH_SECONDS = 1.0
#: 附录2-6：机器狗移动速度
MOVE_SPEED_MPS = 5.0
#: 附录2-8：光学精确定位时间
OPTICAL_SECONDS = 3.0
#: 附录2-8：激光清除时间
CLEAR_SECONDS = 2.0
#: 附录2-8：光学探测仪精确作用距离（米）
OPTICAL_RANGE_M = 20.0
#: 附录2-9：信号过强（无法测向）的直接清除距离（米）
NEAR_RANGE_M = 5.0
#: 附录2-2：有效接收半径下界（米）
MIN_EFFECTIVE_RADIUS_M = 1000.0
#: 附录2-2：有效接收半径上界（米）
MAX_EFFECTIVE_RADIUS_M = 1500.0
#: 附录2-1：示向度误差界（度）
BEARING_ERROR_DEG = 1.0
#: 题目正文：目标区域半径（米）
ARENA_RADIUS_M = 1800.0
#: 题目正文：干扰源个数范围
MIN_JAMMERS = 10
MAX_JAMMERS = 16
#: 附录3：虚拟时间上限（秒）= 100 小时
MAX_VIRTUAL_DURATION_S = 360_000.0
#: 附录3：程序运行时间上限（秒）= 20 分钟
MAX_REAL_DURATION_S = 1200.0


class MeasureKind(StrEnum):
    """一次 ``/measure`` 的语义结果。"""

    NO_SIGNAL = "no_signal"
    NEAR = "near"
    DIRECTION = "direction"
    #: 本地合成值：服务器 ``accepted=false``（例如尚未进入场地）。
    REJECTED = "rejected"


class ClearKind(StrEnum):
    """一次 ``/clear`` 的语义结果。"""

    SUCCESS = "success"
    NO_TARGET_IN_RANGE = "no_target_in_range"
    REJECTED = "rejected"


class ExitReason(StrEnum):
    USER_EXIT = "user_exit"
    WINDOW_TIMEOUT = "window_timeout"
    PROGRAM_TIMEOUT = "program_timeout"
    VIRTUAL_TIMEOUT = "virtual_timeout"
    MANUAL_ABORT = "manual_abort"
    TECHNICAL_ERROR = "technical_error"


@dataclass(frozen=True, slots=True)
class RobotResponse:
    """未被语义化的原始响应（用于日志与 Replay）。"""

    action: str
    accepted: bool
    raw: Mapping[str, Any]
    real_timestamp_ms: int = 0
    virtual_time_s: float = 0.0

    @classmethod
    def from_wire(cls, action: str, raw: Mapping[str, Any]) -> "RobotResponse":
        return cls(
            action=action,
            accepted=bool(raw.get("accepted", False)),
            raw=dict(raw),
            real_timestamp_ms=int(raw.get("real_timestamp_ms") or 0),
            virtual_time_s=float(raw.get("virtual_time_s") or 0.0),
        )


@dataclass(frozen=True, slots=True)
class EnterResult:
    accepted: bool
    virtual_time_s: float = 0.0
    real_timestamp_ms: int = 0
    max_virtual_duration_s: float = MAX_VIRTUAL_DURATION_S
    max_real_duration_s: float = MAX_REAL_DURATION_S
    remaining_real_duration_s: float = MAX_REAL_DURATION_S


@dataclass(frozen=True, slots=True)
class MeasureResult:
    kind: MeasureKind
    svd_deg: float | None = None
    accepted: bool = True
    virtual_time_s: float = 0.0

    def __post_init__(self) -> None:
        if self.kind == MeasureKind.DIRECTION and self.svd_deg is None:
            raise ValueError("direction result requires svd_deg")
        if self.kind != MeasureKind.DIRECTION and self.svd_deg is not None:
            raise ValueError("svd_deg is only valid for direction result")

    @property
    def detected(self) -> bool:
        """是否收到该频道的信号（``direction`` 或 ``near``）。"""
        return self.kind in (MeasureKind.DIRECTION, MeasureKind.NEAR)


@dataclass(frozen=True, slots=True)
class ClearResult:
    kind: ClearKind
    accepted: bool = True
    virtual_time_s: float = 0.0

    @property
    def cleared(self) -> bool:
        return self.kind == ClearKind.SUCCESS


@dataclass(frozen=True, slots=True)
class ExitResult:
    accepted: bool
    reason: ExitReason | None = None
    virtual_time_s: float = 0.0


def _outcome(raw: Mapping[str, Any]) -> str | None:
    """取语义结果字符串；兼容 ``measure_result`` / ``clear_result`` 两种字段名。"""
    for key in ("measure_result", "clear_result", "result", "outcome"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def parse_enter(raw: Mapping[str, Any]) -> EnterResult:
    return EnterResult(
        accepted=bool(raw.get("accepted", False)),
        virtual_time_s=float(raw.get("virtual_time_s") or 0.0),
        real_timestamp_ms=int(raw.get("real_timestamp_ms") or 0),
        max_virtual_duration_s=float(
            raw.get("max_virtual_duration_s") or MAX_VIRTUAL_DURATION_S
        ),
        max_real_duration_s=float(
            raw.get("max_real_duration_s") or MAX_REAL_DURATION_S
        ),
        remaining_real_duration_s=float(
            raw.get("remaining_real_duration_s") or MAX_REAL_DURATION_S
        ),
    )


def parse_measure(raw: Mapping[str, Any]) -> MeasureResult:
    accepted = bool(raw.get("accepted", False))
    virtual = float(raw.get("virtual_time_s") or 0.0)
    if not accepted:
        return MeasureResult(MeasureKind.REJECTED, accepted=False, virtual_time_s=virtual)

    outcome = _outcome(raw)
    if outcome == MeasureKind.DIRECTION:
        svd = raw.get("svd_deg")
        if svd is None:
            raise ValueError(f"direction response without svd_deg: {dict(raw)!r}")
        return MeasureResult(
            MeasureKind.DIRECTION, float(svd), accepted=True, virtual_time_s=virtual
        )
    try:
        kind = MeasureKind(outcome) if outcome is not None else MeasureKind.NO_SIGNAL
    except ValueError:
        kind = MeasureKind.NO_SIGNAL
    if kind in (MeasureKind.REJECTED,):
        kind = MeasureKind.NO_SIGNAL
    return MeasureResult(kind, accepted=True, virtual_time_s=virtual)


def parse_clear(raw: Mapping[str, Any]) -> ClearResult:
    accepted = bool(raw.get("accepted", False))
    virtual = float(raw.get("virtual_time_s") or 0.0)
    if not accepted:
        return ClearResult(ClearKind.REJECTED, accepted=False, virtual_time_s=virtual)
    outcome = _outcome(raw)
    if outcome == ClearKind.SUCCESS:
        return ClearResult(ClearKind.SUCCESS, accepted=True, virtual_time_s=virtual)
    return ClearResult(
        ClearKind.NO_TARGET_IN_RANGE, accepted=True, virtual_time_s=virtual
    )


def parse_exit(raw: Mapping[str, Any]) -> ExitResult:
    reason_raw = raw.get("exit_reason")
    reason: ExitReason | None = None
    if isinstance(reason_raw, str):
        try:
            reason = ExitReason(reason_raw)
        except ValueError:
            reason = None
    return ExitResult(
        accepted=bool(raw.get("accepted", False)),
        reason=reason,
        virtual_time_s=float(raw.get("virtual_time_s") or 0.0),
    )
