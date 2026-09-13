"""离线 mock 模拟器：在本地 HTTP 端口上实现 ``robot-protocol-v1``。

用途
----
1. 在没有 Windows 客户端（``jammers-simulator.exe``）的机器上验证整条工作流：
   策略 -> client -> HTTP -> world，与真实链路同构，只换掉实现。
2. 批量调参：同一 seed 可复现，跑完能直接从 ``world`` 取真值算指标。

它同时**严格复刻真实接口的字段校验**（arena_id / robot_id / request_id /
position / channel / 未知字段），因此也能反过来验证我们的 client 是否发对了请求。

注意：这是 mock，不是官方的 ``internal/simcore``。真实计分必须以线上模拟器为准。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from math import isfinite
from typing import Any, Callable
from urllib.parse import urlparse

from ..geometry import Point
from ..protocol import ARENA_ID, MAX_CHANNEL, MIN_CHANNEL
from .world import Case, World, generate_case

ALLOWED_FIELDS = {
    "/enter": {"arena_id", "robot_id", "request_id"},
    "/measure": {"arena_id", "robot_id", "request_id", "position", "channel"},
    "/clear": {"arena_id", "robot_id", "request_id", "position", "channel"},
    "/exit": {"arena_id", "robot_id", "request_id"},
}
POSITION_MAX_ABS = 2_000_000.0


class ValidationError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _is_identifier(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if len(value.encode("utf-8")) > 256:
        return False
    return not any(ord(c) < 0x20 or ord(c) == 0x7F for c in value)


def validate(path: str, body: Any, world: World) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ValidationError("请求体根值必须是 JSON 对象")
    allowed = ALLOWED_FIELDS[path]
    for key in body:
        if key not in allowed:
            raise ValidationError(f"未知字段 {key}")
    arena = body.get("arena_id")
    if arena != ARENA_ID:
        raise ValidationError("arena_id 必须是 default")
    robot = body.get("robot_id")
    if robot != world.robot_id:
        raise ValidationError("robot_id 与当前登录队号不一致")
    if not _is_identifier(body.get("request_id")):
        raise ValidationError("request_id 无效")

    if path in ("/measure", "/clear"):
        if path == "/measure" or "position" in body:
            position = body.get("position")
            if path == "/measure" and position is None:
                raise ValidationError("缺少字段 position")
            if position is not None:
                if not isinstance(position, dict):
                    raise ValidationError("position 必须是 JSON 对象")
                for key in position:
                    if key not in ("x", "y"):
                        raise ValidationError(f"未知字段 {key}")
                if "x" not in position or "y" not in position:
                    raise ValidationError("position 必须同时包含 x 与 y")
                for axis in ("x", "y"):
                    value = position[axis]
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        raise ValidationError("position 分量必须是数字")
                    if not isfinite(float(value)) or abs(float(value)) > POSITION_MAX_ABS:
                        raise ValidationError("position 分量超出允许范围")
        channel = body.get("channel")
        if path == "/clear" and channel is None:
            raise ValidationError("缺少字段 channel")
        if channel is not None:
            if isinstance(channel, bool) or not isinstance(channel, int):
                raise ValidationError("channel 必须是整数")
            if not (MIN_CHANNEL <= channel <= MAX_CHANNEL):
                raise ValidationError("channel 必须在 1..20")
    return body


class _Handler(BaseHTTPRequestHandler):
    server_version = "jammers-mock/1.0"
    protocol_version = "HTTP/1.1"

    #: 由 :func:`_make_handler` 注入，每个 MockSimulator 实例一份，避免并发串台
    sim: "MockSimulator"

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        if self.sim.verbose:
            print(f"[mock] {fmt % args}", flush=True)

    # -- 辅助 -----------------------------------------------------------
    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return None
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        sim = self.sim
        if path == "/admin/case":
            self._send_json(200, sim.world.case.as_dict())
            return
        if path == "/admin/summary":
            self._send_json(200, sim.world.summary())
            return
        if path == "/admin/state":
            self._send_json(
                200,
                {
                    "entered": sim.world.entered,
                    "virtual_time_s": sim.world.virtual_time_s,
                    "position": [sim.world.position.x, sim.world.position.y],
                    "channel": sim.world.channel,
                    "cleared": sorted(sim.world.cleared),
                },
            )
            return
        self._send_json(405, {"error": {"code": "method_not_allowed", "message": "只接受 POST"}})

    def do_POST(self) -> None:  # noqa: N802
        sim = self.sim
        path = urlparse(self.path).path
        if path not in ALLOWED_FIELDS:
            self._send_json(404, {"error": {"code": "not_found", "message": "未知路径"}})
            return

        # portguard：测试已结束（或未开始）时直接断开连接
        if sim.gated or sim.world.exit_reason is not None:
            self.close_connection = True
            try:
                self.connection.close()
            except OSError:
                pass
            return

        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if ctype != "application/json":
            self._send_json(
                415,
                {"error": {"code": "unsupported_media_type", "message": "Content-Type 必须是 application/json"}},
            )
            return

        try:
            body = self._read_body()
        except (ValueError, UnicodeDecodeError):
            self._send_json(
                400, {"error": {"code": "invalid_request", "message": "JSON 解析失败"}}
            )
            return

        try:
            payload = validate(path, body, sim.world)
        except ValidationError as exc:
            self._send_json(
                exc.status, {"error": {"code": "invalid_request", "message": str(exc)}}
            )
            return

        sim.record_request(path, dict(payload))

        if path == "/enter":
            response = sim.world.enter()
        elif path == "/measure":
            position = Point(
                float(payload["position"]["x"]), float(payload["position"]["y"])
            )
            response, _ = sim.world.measure(position, int(payload["channel"]))
        elif path == "/clear":
            position = payload.get("position")
            if position is None:
                position = {"x": sim.world.position.x, "y": sim.world.position.y}
            point = Point(float(position["x"]), float(position["y"]))
            response, _ = sim.world.clear(point, int(payload["channel"]))
        else:
            response = sim.world.exit()

        self._send_json(200, response)


def _make_handler(sim: "MockSimulator") -> type[_Handler]:
    """为每个模拟器实例生成独立的 handler 类。

    如果直接把 ``sim`` 挂在共享的 ``_Handler`` 上，同一个进程里并发跑多个
    mock 就会互相串台（handler 会看到错误的 world）。
    """
    return type("_BoundHandler", (_Handler,), {"sim": sim})


class MockSimulator:
    """进程内 mock 模拟器；``with MockSimulator(...) as sim:`` 即可使用。"""

    def __init__(
        self,
        robot_id: str,
        seed: int = 1,
        n_jammers: int | None = None,
        omni_only: bool = True,
        case: Case | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        gated: bool = False,
        verbose: bool = False,
        clear_requires_switch: bool = False,
        legacy_clear_timing: bool = False,
    ) -> None:
        resolved = case if case is not None else generate_case(
            seed, n_jammers=n_jammers, omni_only=omni_only
        )
        self.world = World(
            case=resolved,
            robot_id=robot_id,
            clear_requires_switch=clear_requires_switch,
            legacy_clear_timing=legacy_clear_timing,
        )
        self.gated = gated
        self.verbose = verbose
        self.requests: list[dict[str, Any]] = []
        self._httpd = ThreadingHTTPServer((host, port), _make_handler(self))
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="mock-simulator", daemon=True
        )

    # -- 生命周期 --------------------------------------------------------
    @property
    def host(self) -> str:
        return self._httpd.server_address[0]

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> "MockSimulator":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def __enter__(self) -> "MockSimulator":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # -- 观测 ------------------------------------------------------------
    def record_request(self, path: str, payload: dict[str, Any]) -> None:
        self.requests.append({"path": path, "payload": payload})


def run_standalone(
    port: int = 2026,
    robot_id: str = "000000000000",
    seed: int = 1,
    n_jammers: int | None = None,
    omni_only: bool = True,
    gated: bool = False,
    verbose: bool = True,
) -> None:
    """把 mock 当作独立进程跑在固定端口上（模拟"真实客户端已开测"的场景）。"""
    sim = MockSimulator(
        robot_id=robot_id,
        seed=seed,
        n_jammers=n_jammers,
        omni_only=omni_only,
        port=port,
        gated=gated,
        verbose=verbose,
    )
    for jammer in sim.world.case.jammers:
        print(
            f"  ch{jammer.channel:>2} ({jammer.position.x:8.2f},{jammer.position.y:8.2f}) "
            f"R={jammer.effective_radius_m:7.1f} dir={jammer.direction_deg}",
            flush=True,
        )
    print(f"[mock] listening on {sim.base_url} (seed={sim.world.case.seed})", flush=True)
    try:
        sim._thread.start()
        sim._thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        sim.stop()


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="离线 mock 模拟器 (robot-protocol-v1)")
    parser.add_argument("--port", type=int, default=2026)
    parser.add_argument("--robot-id", default="000000000000", help="必须等于登录队号")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--n-jammers", type=int, default=None)
    parser.add_argument("--directional", action="store_true", help="包含定向源（问题4）")
    parser.add_argument("--gated", action="store_true", help="模拟端口门控关闭")
    parser.add_argument("-v", "--verbose", action="store_true", help="打印每个请求")
    parser.add_argument(
        "--clear-requires-switch",
        action="store_true",
        help="假设 /clear 也需要先切频道（默认不需要，见 world.py 说明）",
    )
    args = parser.parse_args(argv)
    run_standalone(
        port=args.port,
        robot_id=args.robot_id,
        seed=args.seed,
        n_jammers=args.n_jammers,
        omni_only=not args.directional,
        gated=args.gated,
        verbose=args.verbose,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
