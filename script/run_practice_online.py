#!/usr/bin/env python3
"""线上演练测试入口：真实 robot API（默认）与本地 mock（``--mock``）两种模式。

真实模式（默认，不启动 mock）
----------------------------
``login-jammers`` 负责平台登录；官方 ``jammers-simulator.exe`` 在客户端里开始一次
练习/正式测试后，会在 ``127.0.0.1:<robot_port>`` 打开 ``robot-protocol-v1`` 的
robot API。本脚本默认直接连接该端口，不主动 login/authorize/statistics，避免与
官方客户端已有会话冲突；统计上报由官方客户端负责。

端口默认读取 ``JAMMERS_ROBOT_PORT``（与 ``login-jammers/linux-client/python/
jammers_robot.py`` 一致，缺省 2026），也可用 ``--robot-port`` 或 ``--base-url``
覆盖。端口未开时 ``run_once`` 会返回 ``gated``，这是正常门控行为。

模拟模式（``--mock``）
----------------------
用于没有官方 Windows 客户端时验证登录 / authorize / robot-protocol-v1 / live
数据面形状：授权拿练习票据后，在 ``127.0.0.1:<robot_port>`` 临时启动
``framework.mock.server.MockSimulator``，再用 ``--mode live`` 跑策略。
**物理不是官方 simcore，结果不能当作真实演练成绩或正式测试成绩。**

用法::

    # 真实线上：先在官方客户端里开始练习测试，再运行
    python3 script/run_practice_online.py --strategy matrix

    # 模拟链路自测（临时起 mock 占住 2026）
    python3 script/run_practice_online.py --mock --strategy matrix --seed 11
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(REPO_ROOT / "framework" / "src"))

from jammers_paths import resolve  # noqa: E402
from mathmodel2026b.runner import RunConfig, run_once  # noqa: E402
from run import build_strategy_params, make_strategy_factory  # noqa: E402

SCENARIO_SCHEMA_VERSION = "scenario-v1"
RULESET_VERSION = "rules-v1"
PACKAGE_ENVELOPE_VERSION = 1
STATISTICS_SCHEMA_VERSION = "practice-run-statistics-v1"
DEFAULT_ROBOT_PORT = int(os.environ.get("JAMMERS_ROBOT_PORT", "2026"))
DEFAULT_ROBOT_URL = f"http://127.0.0.1:{DEFAULT_ROBOT_PORT}"


def b64u_nopad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def b64u_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def decode_ticket(ticket_b64: str) -> dict:
    """票据二进制格式（实测）: magic(8) | version(2) | json_len(4, BE) | json | sig(64)。"""
    raw = b64u_decode(ticket_b64)
    if raw[:8] != b"JMBTKT1\x00":
        raise ValueError(f"未知票据 magic: {raw[:8]!r}")
    version = int.from_bytes(raw[8:10], "big")
    jlen = int.from_bytes(raw[10:14], "big")
    claims = json.loads(raw[14 : 14 + jlen].decode("utf-8"))
    return {
        "raw": raw,
        "version": version,
        "claims": claims,
        "signature": raw[14 + jlen :],
        "sha256_raw_hex": hashlib.sha256(raw).hexdigest(),
        "sha256_b64_hex": hashlib.sha256(ticket_b64.encode()).hexdigest(),
    }


def _run_mock_mode(args, paths, team_no: str, cfg: dict, report: dict) -> tuple:
    """接口自测模式：authorize -> 本地 MockSimulator -> live 策略路径 -> 可选 statistics。"""
    from mathmodel2026b.mock.server import MockSimulator  # 延迟导入：真实模式不需要

    sys.path.insert(0, str(paths.root / "linux-client" / "python"))
    from jammers_auth import (  # noqa: E402
        CLIENT_VERSION,
        JammersClient,
        JammersError,
        build_device_report,
    )
    from cryptography.hazmat.primitives import serialization  # noqa: E402
    from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: E402

    client = JammersClient(timeout=25.0)
    st = client.status()
    report["server_status"] = st
    print(f"[online] status new_tests_enabled={st.get('new_tests_enabled')} "
          f"deadline={st.get('test_start_deadline')} "
          f"practice_summary_upload_enabled={st.get('practice_summary_upload_enabled')}")

    login = client.login(team_no, cfg["password"], build_device_report())
    report["login"] = {k: v for k, v in login.items() if k != "access_token"}
    print(f"[online] login ok team_no={login['team_no']} "
          f"p3={login['problem3_remaining_attempts']} p4={login['problem4_remaining_attempts']}")

    # --- 1) authorize -----------------------------------------------------
    priv = ed25519.Ed25519PrivateKey.generate()
    pub_b64 = b64u_nopad(
        priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    auth_body = {
        "client_request_id": JammersClient.new_request_id(),
        "problem_no": args.problem,
        "client_version": CLIENT_VERSION,
        "scenario_schema_version": SCENARIO_SCHEMA_VERSION,
        "ruleset_version": RULESET_VERSION,
        "package_envelope_version": PACKAGE_ENVELOPE_VERSION,
        "package_signing_public_key_b64": pub_b64,
    }
    t0 = time.time()
    try:
        auth_resp = client._call(
            "POST", "/api/v1/practice-tests/authorize", auth_body, authorized=True
        )
    except JammersError as exc:
        print(f"[online] authorize FAILED: HTTP {exc.status} {exc.code}: {exc.message}")
        return None, client, None, None, 2
    ticket_b64 = auth_resp["practice_ticket_b64"]
    ticket = decode_ticket(ticket_b64)
    report["authorize"] = {
        "request": auth_body,
        "server_time": auth_resp.get("server_time"),
        "latency_s": round(time.time() - t0, 3),
        "ticket_claims": ticket["claims"],
        "ticket_sha256_raw": ticket["sha256_raw_hex"],
        "ticket_sha256_b64": ticket["sha256_b64_hex"],
    }
    print(f"[online] authorize ok ({time.time() - t0:.2f}s) "
          f"nonce={ticket['claims']['authorization_nonce_b64']} "
          f"start_before={ticket['claims']['start_before']}")

    # --- 2) 本地跑（robot API 挂在真实端口上，策略走 live 路径） ----------
    params = build_strategy_params(args.strategy, args.directional, args.param)
    strategy_factory = make_strategy_factory(args.strategy, args.directional)
    print(f"[online] strategy={args.strategy}")

    sim = MockSimulator(
        robot_id=team_no,
        seed=args.seed,
        n_jammers=args.n_jammers,
        omni_only=not args.directional,
        port=args.robot_port,
    )
    sim.start()
    print(f"[online] robot API (本地 mock) -> {sim.base_url}")
    try:
        outcome = run_once(
            RunConfig(
                robot_id=team_no,
                seed=args.seed,
                mode="live",
                base_url=sim.base_url,
                params=params,
                tag="online-practice",
                write_trace=False,
                notes={
                    "practice_ticket_nonce": ticket["claims"]["authorization_nonce_b64"],
                    "strategy": args.strategy,
                    "directional": args.directional,
                    "simulated_simcore": True,
                },
                strategy_factory=strategy_factory,
            )
        )
    finally:
        truth = sim.world.summary()
        sim.stop()

    report["run"] = {
        "run_id": outcome.run_id,
        "status": outcome.status,
        "error": outcome.error,
        "metrics": outcome.metrics,
        "ground_truth": truth,
    }
    print(f"[online] run {outcome.run_id} status={outcome.status} "
          f"cleared={outcome.metrics['cleared_count']}/{truth['n_jammers']} "
          f"virtual_s={outcome.metrics['virtual_time_s']:.1f} "
          f"wall_s={outcome.metrics['wall_s']:.2f}")

    # --- 3) statistics ----------------------------------------------------
    stats_body = {
        "client_request_id": JammersClient.new_request_id(),
        "statistics_schema_version": STATISTICS_SCHEMA_VERSION,
        "practice_ticket_sha256": (
            ticket["sha256_raw_hex"] if args.ticket_sha == "raw" else ticket["sha256_b64_hex"]
        ),
        "problem_no": args.problem,
        "practice_run_no": 1,
        "case_code": args.case_code
        or f"local-{args.seed:04d}-{ticket['claims']['authorization_nonce_b64'][:8]}",
        "entered": True,
        "end_reason": "user_exit",
        "cleared_jammer_count": int(outcome.metrics["cleared_count"]),
        "measure_accepted_count": int(outcome.metrics.get("measure_accepted_count") or 0),
        "virtual_time_us": int(round(outcome.metrics["virtual_time_s"] * 1_000_000)),
        "program_run_duration_ms": int(round(outcome.metrics["wall_s"] * 1000)),
        "channel_switch_count": int(outcome.metrics.get("channel_switch_count") or 0),
        "clear_failure_count": int(outcome.metrics.get("clear_failure_count") or 0),
        "jammer_count": int(truth["n_jammers"]),
    }
    report["statistics_request"] = stats_body
    print(f"[online] statistics body: {json.dumps(stats_body, ensure_ascii=False)}")

    if args.submit:
        try:
            stat_resp = client._call(
                "POST", "/api/v1/practice-tests/statistics", stats_body, authorized=True
            )
        except JammersError as exc:
            print(f"[online] statistics FAILED: HTTP {exc.status} {exc.code}: {exc.message}")
            report["statistics_response"] = {
                "error": exc.code, "message": exc.message, "status": exc.status
            }
        else:
            report["statistics_response"] = stat_resp
            print(f"[online] statistics accepted -> {json.dumps(stat_resp, ensure_ascii=False)}")
    else:
        print("[online] 未上报（加 --submit 才会调用 statistics）")

    return outcome, client, ticket, truth, 0


def _run_real_mode(args, paths, team_no: str, report: dict, params, strategy_factory) -> tuple:
    """真实模式：不启动 mock，直接连接官方客户端打开的 robot API。"""
    base_url = args.base_url or f"http://127.0.0.1:{args.robot_port}"
    report["real_robot_url"] = base_url
    print(f"[online] strategy={args.strategy}")
    print(f"[online] real robot API (官方客户端) -> {base_url}")
    if args.submit:
        print("[online] 真实模式不主动上报 statistics；统计由官方客户端负责。")

    outcome = run_once(
        RunConfig(
            robot_id=team_no,
            seed=args.seed,
            mode="live",
            base_url=base_url,
            params=params,
            tag="online-practice",
            write_trace=False,
            notes={
                "strategy": args.strategy,
                "directional": args.directional,
                "simulated_simcore": False,
            },
            strategy_factory=strategy_factory,
        )
    )
    metrics = outcome.metrics or {}
    total = metrics.get("n_jammers")
    total_text = str(total) if total is not None else "?"
    report["run"] = {
        "run_id": outcome.run_id,
        "status": outcome.status,
        "error": outcome.error,
        "metrics": metrics,
        "ground_truth": None,
    }
    print(f"[online] run {outcome.run_id} status={outcome.status} "
          f"cleared={metrics.get('cleared_count')}/{total_text} "
          f"virtual_s={metrics.get('virtual_time_s')} "
          f"wall_s={metrics.get('wall_s'):.2f}")
    return outcome, None, None, None, 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="线上演练：真实 robot API（默认）或 --mock 自测")
    ap.add_argument("--problem", type=int, default=3, choices=[3, 4])
    ap.add_argument("--seed", type=int, default=11, help="仅 --mock 使用：本地案例种子")
    ap.add_argument("--n-jammers", type=int, default=None, help="仅 --mock 使用")
    ap.add_argument("--directional", action="store_true", help="问题4：含定向源")
    ap.add_argument(
        "--strategy",
        choices=["q3", "matrix"],
        default="q3",
        help="q3=旧 Q3Strategy；matrix=知识矩阵 KnowledgeSearchStrategy",
    )
    ap.add_argument(
        "--mock",
        action="store_true",
        help="临时启动本地 MockSimulator 做接口自测；真实线上演练不要加此参数",
    )
    ap.add_argument("--keep-session", action="store_true",
                    help="仅 --mock：结束后不调用 logout")
    ap.add_argument(
        "--robot-port",
        type=int,
        default=DEFAULT_ROBOT_PORT,
        help=f"官方 robot API 端口（默认 JAMMERS_ROBOT_PORT={DEFAULT_ROBOT_PORT}）",
    )
    ap.add_argument("--base-url", default=None, help="覆盖真实 robot API 的完整地址")
    ap.add_argument("--submit", action="store_true",
                    help="仅 --mock：上报 statistics；真实模式由官方客户端上报")
    ap.add_argument("--case-code", default=None, help="仅 --mock：覆盖统计上报的 case_code")
    ap.add_argument("--ticket-sha", choices=["raw", "b64"], default="raw",
                    help="仅 --mock：practice_ticket_sha256 取原始字节还是 base64 串")
    ap.add_argument("--login-jammers", default=None)
    ap.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--report", default=None, help="结果 JSON 落盘路径")
    args = ap.parse_args(argv)

    paths = resolve(args.login_jammers, None)
    cfg = json.loads(Path(paths.default_config).read_text(encoding="utf-8"))
    team_no = cfg["team_no"]

    params = build_strategy_params(args.strategy, args.directional, args.param)
    strategy_factory = make_strategy_factory(args.strategy, args.directional)

    report: dict = {"started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    exit_code = 0
    client = None
    outcome = None
    ticket = None

    if args.mock:
        outcome, client, ticket, truth, exit_code = _run_mock_mode(
            args, paths, team_no, cfg, report
        )
        if exit_code:
            return exit_code
        if not args.keep_session:
            try:
                client.logout()
                report["logout"] = "ok"
                print("[online] logout ok")
            except Exception as exc:  # noqa: BLE001 - 清理失败不应覆盖演练结论
                report["logout"] = f"{type(exc).__name__}: {exc}"
                print(f"[online] logout failed: {type(exc).__name__}: {exc}")
    else:
        outcome, _client, _ticket, _truth, exit_code = _run_real_mode(
            args, paths, team_no, report, params, strategy_factory
        )
        if exit_code:
            return exit_code

    report["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out = Path(args.report) if args.report else (
        REPO_ROOT / "logs" / f"practice_online_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
                   encoding="utf-8")
    print(f"[online] 报告写入 {out}")
    return 1 if (outcome is not None and outcome.status != "ok") else 0


if __name__ == "__main__":
    raise SystemExit(main())
