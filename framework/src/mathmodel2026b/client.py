"""模拟器通信层。

三种实现：

``HttpSimulatorClient``
    通过 HTTP+JSON 访问 ``robot-protocol-v1`` 接口。既可以是 Windows 客户端
    （``jammers-simulator.exe``）在本机暴露的 ``127.0.0.1:2026``，也可以是
    本仓库提供的离线 mock 模拟器；对上层策略完全同构。
``RecordingClient``
    装饰器，把每次请求/响应按顺序写进结构化日志，供调参与回归。
``ReplayClient``
    读取 ``RecordingClient`` 产出的 JSONL 重放，用于离线回归测试。
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Mapping
from uuid import uuid4

from .geometry import Point
from .protocol import (
    ARENA_ID,
    ClearResult,
    EnterResult,
    ExitResult,
    MeasureResult,
    RobotResponse,
    parse_clear,
    parse_enter,
    parse_exit,
    parse_measure,
)


class SimulatorError(RuntimeError):
    """与模拟器通信失败（网络层）。"""


class RobotPortGatedError(SimulatorError):
    """robot 端口被 portguard 门控关闭：客户端未运行或没有进行中的测试。"""


class SimulatorClient(ABC):
    """策略与模拟器之间的唯一接口。"""

    @abstractmethod
    def enter(self) -> EnterResult: ...

    @abstractmethod
    def measure(self, position: Point, channel: int) -> MeasureResult: ...

    @abstractmethod
    def clear(self, position: Point, channel: int) -> ClearResult: ...

    @abstractmethod
    def exit(self) -> ExitResult: ...

    def close(self) -> None:  # pragma: no cover - 可选钩子
        return None


@dataclass(slots=True)
class HttpSimulatorClient(SimulatorClient):
    robot_id: str
    base_url: str = "http://127.0.0.1:2026"
    timeout: float = 10.0
    arena_id: str = ARENA_ID
    retries: int = 2
    verbose: bool = False

    last_response: RobotResponse | None = field(default=None, init=False)
    _opener: Any = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.robot_id:
            raise ValueError("robot_id 不能为空（必须等于登录队号）")
        self.base_url = self.base_url.rstrip("/")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._opener = opener

    # -- 传输层 ---------------------------------------------------------
    def _post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                if self.verbose:
                    print(f"  -> POST {path} attempt={attempt}", flush=True)
                with self._opener.open(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                parsed = json.loads(raw.decode("utf-8") or "{}")
                if not isinstance(parsed, dict):
                    raise SimulatorError(f"{path} 返回非 JSON 对象: {parsed!r}")
                self.last_response = RobotResponse.from_wire(path, parsed)
                return parsed
            except urllib.error.HTTPError as exc:
                body = exc.read()
                try:
                    parsed = json.loads(body.decode("utf-8"))
                    if isinstance(parsed, dict) and "accepted" in parsed:
                        self.last_response = RobotResponse.from_wire(path, parsed)
                        return parsed
                except ValueError:
                    pass
                raise SimulatorError(f"HTTP {exc.code} on {path}: {body[:200]!r}") from exc
            except (ConnectionResetError, ConnectionRefusedError, socket.timeout) as exc:
                last_exc = exc
                time.sleep(0.2 * attempt)
            except urllib.error.URLError as exc:
                last_exc = exc
                time.sleep(0.2 * attempt)
        raise RobotPortGatedError(
            f"无法连接 {self.base_url}{path}（{last_exc}）。\n"
            "  · 模拟器未运行，或\n"
            "  · 尚未启动练习/正式测试（problem 3/4）——robot 端口门控未开。"
        )

    def _base_payload(self) -> dict[str, Any]:
        return {
            "arena_id": self.arena_id,
            "robot_id": self.robot_id,
            "request_id": uuid4().hex,
        }

    # -- 动作 -----------------------------------------------------------
    def enter(self) -> EnterResult:
        return parse_enter(self._post("/enter", self._base_payload()))

    def measure(self, position: Point, channel: int) -> MeasureResult:
        payload = self._base_payload() | {
            "position": {"x": float(position.x), "y": float(position.y)},
            "channel": int(channel),
        }
        return parse_measure(self._post("/measure", payload))

    def clear(self, position: Point, channel: int) -> ClearResult:
        payload = self._base_payload() | {
            "position": {"x": float(position.x), "y": float(position.y)},
            "channel": int(channel),
        }
        return parse_clear(self._post("/clear", payload))

    def exit(self) -> ExitResult:
        try:
            raw = self._post("/exit", self._base_payload())
        except RobotPortGatedError:
            # 测试已经结束（超时/中止）时端口会重新关闭，不应让退出路径炸掉。
            return ExitResult(accepted=False)
        return parse_exit(raw)


# ---------------------------------------------------------------------------
# 装饰器：记录 / 重放
# ---------------------------------------------------------------------------
CallHook = Callable[[dict[str, Any]], None]


@dataclass(slots=True)
class RecordingClient(SimulatorClient):
    """把每一次交互序列化为一条 JSON 记录交给 ``hook``。"""

    inner: SimulatorClient
    hook: CallHook

    def _emit(self, record: dict[str, Any]) -> None:
        try:
            self.hook(record)
        except Exception:  # 日志失败绝不能影响策略
            pass

    def enter(self) -> EnterResult:
        t0 = time.time()
        result = self.inner.enter()
        self._emit(
            {
                "action": "enter",
                "request": {},
                "response": _response_of(self.inner),
                "accepted": result.accepted,
                "virtual_time_s": result.virtual_time_s,
                "wall_ms": int((time.time() - t0) * 1000),
            }
        )
        return result

    def measure(self, position: Point, channel: int) -> MeasureResult:
        t0 = time.time()
        result = self.inner.measure(position, channel)
        self._emit(
            {
                "action": "measure",
                "request": {"x": position.x, "y": position.y, "channel": channel},
                "response": _response_of(self.inner),
                "accepted": result.accepted,
                "outcome": str(result.kind),
                "svd_deg": result.svd_deg,
                "virtual_time_s": result.virtual_time_s,
                "wall_ms": int((time.time() - t0) * 1000),
            }
        )
        return result

    def clear(self, position: Point, channel: int) -> ClearResult:
        t0 = time.time()
        result = self.inner.clear(position, channel)
        self._emit(
            {
                "action": "clear",
                "request": {"x": position.x, "y": position.y, "channel": channel},
                "response": _response_of(self.inner),
                "accepted": result.accepted,
                "outcome": str(result.kind),
                "virtual_time_s": result.virtual_time_s,
                "wall_ms": int((time.time() - t0) * 1000),
            }
        )
        return result

    def exit(self) -> ExitResult:
        t0 = time.time()
        result = self.inner.exit()
        self._emit(
            {
                "action": "exit",
                "request": {},
                "response": _response_of(self.inner),
                "accepted": result.accepted,
                "virtual_time_s": result.virtual_time_s,
                "wall_ms": int((time.time() - t0) * 1000),
            }
        )
        return result

    def close(self) -> None:
        self.inner.close()


def _response_of(client: SimulatorClient) -> dict[str, Any]:
    inner = getattr(client, "inner", client)
    resp = getattr(inner, "last_response", None)
    return dict(resp.raw) if resp is not None else {}


@dataclass(slots=True)
class ReplayClient(SimulatorClient):
    """按记录顺序重放一次运行，用于离线回归测试。"""

    records: list[dict[str, Any]]
    _cursor: int = field(default=0, init=False, repr=False)

    @classmethod
    def from_jsonl(cls, path: str) -> "ReplayClient":
        records: list[dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return cls(records)

    def _next(self, action: str) -> dict[str, Any]:
        if self._cursor >= len(self.records):
            raise SimulatorError(f"重放记录已耗尽，策略又发出了 {action}")
        rec = self.records[self._cursor]
        if rec.get("action") != action:
            raise SimulatorError(
                f"重放不同步：记录里是 {rec.get('action')}，策略发出 {action}"
            )
        self._cursor += 1
        return rec

    def enter(self) -> EnterResult:
        rec = self._next("enter")
        raw = rec.get("response") or {"accepted": rec.get("accepted", True)}
        return parse_enter(raw)

    def measure(self, position: Point, channel: int) -> MeasureResult:
        rec = self._next("measure")
        raw = rec.get("response") or {
            "accepted": rec.get("accepted", True),
            "measure_result": rec.get("outcome"),
            "svd_deg": rec.get("svd_deg"),
        }
        return parse_measure(raw)

    def clear(self, position: Point, channel: int) -> ClearResult:
        rec = self._next("clear")
        raw = rec.get("response") or {
            "accepted": rec.get("accepted", True),
            "clear_result": rec.get("outcome"),
        }
        return parse_clear(raw)

    def exit(self) -> ExitResult:
        rec = self._next("exit")
        raw = rec.get("response") or {"accepted": rec.get("accepted", True)}
        return parse_exit(raw)
