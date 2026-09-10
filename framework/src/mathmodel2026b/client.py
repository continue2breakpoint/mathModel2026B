from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.request import Request, urlopen
from uuid import uuid4

from .geometry import Point
from .protocol import ClearKind, ClearResult, MeasureKind, MeasureResult


class SimulatorClient(ABC):
    @abstractmethod
    def enter(self) -> dict: ...

    @abstractmethod
    def measure(self, position: Point, channel: int) -> MeasureResult: ...

    @abstractmethod
    def clear(self, position: Point, channel: int) -> ClearResult: ...

    @abstractmethod
    def exit(self) -> dict: ...


@dataclass(slots=True)
class HttpSimulatorClient(SimulatorClient):
    robot_id: str
    base_url: str = "http://127.0.0.1:2026"
    timeout: float = 10.0

    def _post(self, path: str, payload: dict) -> dict:
        req = Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _base_payload(self) -> dict:
        return {"robot_id": self.robot_id, "request_id": str(uuid4())}

    def enter(self) -> dict:
        return self._post("/enter", self._base_payload())

    def measure(self, position: Point, channel: int) -> MeasureResult:
        payload = self._base_payload() | {
            "position": {"x": position.x, "y": position.y},
            "channel": channel,
        }
        raw = self._post("/measure", payload)
        kind = raw.get("result") or raw.get("status") or raw.get("type")
        if kind == MeasureKind.DIRECTION:
            return MeasureResult(MeasureKind.DIRECTION, float(raw["svd_deg"]))
        return MeasureResult(MeasureKind(kind))

    def clear(self, position: Point, channel: int) -> ClearResult:
        payload = self._base_payload() | {
            "position": {"x": position.x, "y": position.y},
            "channel": channel,
        }
        raw = self._post("/clear", payload)
        kind = raw.get("result") or raw.get("status") or raw.get("type")
        return ClearResult(ClearKind(kind))

    def exit(self) -> dict:
        return self._post("/exit", self._base_payload())
