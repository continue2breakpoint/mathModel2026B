#!/usr/bin/env python3
"""线上"演练/练习测试"全流程（不依赖官方 Windows 客户端）。

为什么可以这样做
----------------
逆向结果（``login-jammers/analysis/typedump/types_all.txt``）表明练习测试的
**案例是在客户端本地生成的**——``/api/v1/practice-tests/authorize`` 返回的票据里
只有队伍/设备/构建身份字段，没有任何案例数据。服务器收到的只有客户端事后上报的
统计值（``/api/v1/practice-tests/statistics``）。当前 ``/api/v1/status`` 还显示
``practice_summary_upload_enabled=false``，因此行为包也不必上传。

于是整条链路是：

    1. login                                   （会话 + device_digest）
    2. authorize  -> practice_ticket           （签发给本队的练习票据）
    3. 本地跑案例（本脚本用 framework 的 mock world + 我们的 Q3 策略），
       并且**把 robot API 挂在真实的 127.0.0.1:2026 端口上**，
       策略侧走的是与线上完全相同的 ``--mode live`` 代码路径
    4. statistics -> 把这一场的统计值上报

> ⚠️ 物理不是官方的：第 3 步的模拟核心是我们自己的 ``mock.world``，
> 官方 ``internal/simcore`` 仍在 ``jammers-simulator.exe`` 里。
> **因此这条链路的产物只能证明"线上接口链路通"，不能当作官方演练成绩使用。**
> 正式测试（``formal-tests/*``）的赛题包是服务器加密下发的，必须有官方客户端才能解密。

用法::

    python3 script/run_practice_online.py --seed 11 --submit
    python3 script/run_practice_online.py --seed 11            # 只 authorize + 本地跑，不上报
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(REPO_ROOT / "framework" / "src"))

from jammers_paths import resolve  # noqa: E402
from mathmodel2026b.mock.server import MockSimulator  # noqa: E402
from mathmodel2026b.runner import RunConfig, run_once  # noqa: E402
from mathmodel2026b.strategy import Q3Params  # noqa: E402

SCENARIO_SCHEMA_VERSION = "scenario-v1"
RULESET_VERSION = "rules-v1"
PACKAGE_ENVELOPE_VERSION = 1
STATISTICS_SCHEMA_VERSION = "practice-run-statistics-v1"
DEFAULT_ROBOT_PORT = 2026


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="线上演练测试全流程")
    ap.add_argument("--problem", type=int, default=3, choices=[3, 4])
    ap.add_argument("--seed", type=int, default=11, help="本地案例种子")
    ap.add_argument("--n-jammers", type=int, default=None)
    ap.add_argument("--directional", action="store_true", help="问题4：含定向源")
    ap.add_argument("--robot-port", type=int, default=DEFAULT_ROBOT_PORT)
    ap.add_argument("--submit", action="store_true", help="上报 statistics（默认只本地跑）")
    ap.add_argument("--case-code", default=None, help="覆盖上报用的 case_code")
    ap.add_argument("--ticket-sha", choices=["raw", "b64"], default="raw",
                    help="practice_ticket_sha256 取票据原始字节还是 base64 串的 sha256")
    ap.add_argument("--login-jammers", default=None)
    ap.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--report", default=None, help="结果 JSON 落盘路径")
    args = ap.parse_args(argv)

    paths = resolve(args.login_jammers, None)
    sys.path.insert(0, str(paths.root / "linux-client" / "python"))
    from jammers_auth import (  # noqa: E402
        CLIENT_VERSION,
        JammersClient,
        JammersError,
        build_device_report,
    )
    from cryptography.hazmat.primitives import serialization  # noqa: E402
    from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: E402

    cfg = json.loads(Path(paths.default_config).read_text(encoding="utf-8"))
    team_no = cfg["team_no"]

    report: dict = {"started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
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
        return 2
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
    params = Q3Params()
    for item in args.param:
        key, raw = item.split("=", 1)
        cur = getattr(params, key)
        if isinstance(cur, tuple):
            value: object = (float(raw),)
        else:
            try:
                value = int(raw)
            except ValueError:
                value = float(raw)
        setattr(params, key, value)

    sim = MockSimulator(
        robot_id=team_no,
        seed=args.seed,
        n_jammers=args.n_jammers,
        omni_only=not args.directional,
        port=args.robot_port,
    )
    sim.start()
    print(f"[online] robot API (本地 simcore) -> {sim.base_url}")
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
                notes={"practice_ticket_nonce": ticket["claims"]["authorization_nonce_b64"]},
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
        "case_code": args.case_code or f"local-{args.seed:04d}-{ticket['claims']['authorization_nonce_b64'][:8]}",
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

    report["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out = Path(args.report) if args.report else (
        REPO_ROOT / "logs" / f"practice_online_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
                   encoding="utf-8")
    print(f"[online] 报告写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
