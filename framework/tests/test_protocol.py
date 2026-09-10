from __future__ import annotations

import pytest

from mathmodel2026b.protocol import (
    ClearKind,
    ExitReason,
    MeasureKind,
    parse_clear,
    parse_enter,
    parse_exit,
    parse_measure,
)

# 全部取自 login-jammers/analysis/ROBOT_AND_FLOW.md 第 2.3 节的真实响应形状
ENTER_OK = {
    "accepted": True,
    "real_timestamp_ms": 0,
    "virtual_time_s": 0,
    "max_virtual_duration_s": 360000,
    "max_real_duration_s": 1200,
    "remaining_real_duration_s": 1180,
}
MEASURE_DIRECTION = {
    "accepted": True,
    "real_timestamp_ms": 12345,
    "virtual_time_s": 12.345,
    "measure_result": "direction",
    "svd_deg": 123.45,
}
MEASURE_NO_SIGNAL = {
    "accepted": True,
    "real_timestamp_ms": 1,
    "virtual_time_s": 1.0,
    "measure_result": "no_signal",
}
MEASURE_NEAR = {
    "accepted": True,
    "real_timestamp_ms": 1,
    "virtual_time_s": 1.0,
    "measure_result": "near",
}
CLEAR_OK = {
    "accepted": True,
    "real_timestamp_ms": 1,
    "virtual_time_s": 2.0,
    "clear_result": "success",
}
CLEAR_MISS = {
    "accepted": True,
    "real_timestamp_ms": 1,
    "virtual_time_s": 2.0,
    "clear_result": "no_target_in_range",
}
REJECTED = {"accepted": False, "real_timestamp_ms": 100, "virtual_time_s": 0}
EXIT_OK = {
    "accepted": True,
    "real_timestamp_ms": 1,
    "virtual_time_s": 3.0,
    "exit_reason": "user_exit",
}


def test_parse_enter() -> None:
    result = parse_enter(ENTER_OK)
    assert result.accepted
    assert result.max_real_duration_s == 1200
    assert result.remaining_real_duration_s == 1180


def test_parse_measure_direction() -> None:
    result = parse_measure(MEASURE_DIRECTION)
    assert result.kind is MeasureKind.DIRECTION
    assert result.svd_deg == pytest.approx(123.45)
    assert result.detected


def test_parse_measure_other_kinds() -> None:
    assert parse_measure(MEASURE_NO_SIGNAL).kind is MeasureKind.NO_SIGNAL
    assert parse_measure(MEASURE_NEAR).kind is MeasureKind.NEAR
    assert parse_measure(MEASURE_NO_SIGNAL).svd_deg is None


def test_parse_measure_rejected() -> None:
    result = parse_measure(REJECTED)
    assert result.kind is MeasureKind.REJECTED
    assert not result.accepted


def test_parse_measure_direction_without_svd_is_an_error() -> None:
    with pytest.raises(ValueError):
        parse_measure({"accepted": True, "measure_result": "direction"})


def test_parse_clear() -> None:
    assert parse_clear(CLEAR_OK).kind is ClearKind.SUCCESS
    assert parse_clear(CLEAR_OK).cleared
    assert parse_clear(CLEAR_MISS).kind is ClearKind.NO_TARGET_IN_RANGE
    assert not parse_clear(CLEAR_MISS).cleared
    assert parse_clear(REJECTED).kind is ClearKind.REJECTED


def test_parse_exit() -> None:
    assert parse_exit(EXIT_OK).reason is ExitReason.USER_EXIT
    assert parse_exit({"accepted": True, "exit_reason": "weird"}).reason is None


def test_measure_result_validation() -> None:
    from mathmodel2026b.protocol import MeasureResult

    with pytest.raises(ValueError):
        MeasureResult(MeasureKind.DIRECTION)
    with pytest.raises(ValueError):
        MeasureResult(MeasureKind.NEAR, 10.0)
