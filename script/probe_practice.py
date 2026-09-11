#!/usr/bin/env python3
"""探测线上平台的"练习/演练测试"接口是否可以不依赖官方 Windows 客户端直接调用。

**只读式探测**：只调用 ``/api/v1/practice-tests/authorize``。
练习测试不限次数，不会消耗正式次数。

请求体形状来自逆向的匿名结构体（analysis/typedump/types_all.txt:3476）::

    {client_request_id, problem_no, client_version, scenario_schema_version,
     ruleset_version, package_envelope_version, package_signing_public_key_b64}

响应形状（types_all.txt:3622）::

    {practice_ticket_b64, server_time}

用法::

    python3 script/probe_practice.py [--problem 3] [--json out.json]
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import uuid
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from jammers_paths import resolve  # noqa: E402

#: 内嵌构建清单里的版本字段
SCENARIO_SCHEMA_VERSION = "scenario-v1"
RULESET_VERSION = "rules-v1"
PACKAGE_ENVELOPE_VERSION = 1


def b64u_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="探测线上练习测试 authorize 接口")
    ap.add_argument("--problem", type=int, default=3, choices=[3, 4])
    ap.add_argument("--login-jammers", default=None)
    ap.add_argument("--timeout", type=float, default=25.0)
    ap.add_argument("--json", default=None, help="把结果写到该文件")
    args = ap.parse_args(argv)

    paths = resolve(args.login_jammers, None)
    sys.path.insert(0, str(paths.root / "linux-client" / "python"))
    from jammers_auth import (  # noqa: E402
        BUILD_ID,
        BUILD_MANIFEST_SHA256,
        CLIENT_VERSION,
        JammersClient,
        JammersError,
        build_device_report,
    )

    cfg = json.loads(Path(paths.default_config).read_text(encoding="utf-8"))
    team_no = cfg["team_no"]

    c = JammersClient(timeout=args.timeout)
    st = c.status()
    print(f"[probe] status new_tests_enabled={st.get('new_tests_enabled')} "
          f"deadline={st.get('test_start_deadline')} "
          f"practice_summary_upload_enabled={st.get('practice_summary_upload_enabled')}")

    login = c.login(team_no, cfg["password"], build_device_report())
    print(f"[probe] login ok team_no={login['team_no']} "
          f"p3={login['problem3_remaining_attempts']} p4={login['problem4_remaining_attempts']} "
          f"pending_upload={login['pending_upload_count']}")

    # 生成一次性的 Ed25519 密钥对（练习包签名用；本次不上传包，仅探接口）
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    priv = ed25519.Ed25519PrivateKey.generate()
    pub_raw = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    # 实测：平台要求 **base64url 无填充**；标准 base64（含 +/ 与 =）会被判 "请求内容不正确"
    pub_b64 = base64.urlsafe_b64encode(pub_raw).decode().rstrip("=")

    body = {
        "client_request_id": str(uuid.uuid4()),
        "problem_no": args.problem,
        "client_version": CLIENT_VERSION,
        "scenario_schema_version": SCENARIO_SCHEMA_VERSION,
        "ruleset_version": RULESET_VERSION,
        "package_envelope_version": PACKAGE_ENVELOPE_VERSION,
        "package_signing_public_key_b64": pub_b64,
    }
    print(f"[probe] authorize body = {json.dumps(body, ensure_ascii=False)}")

    started = time.time()
    try:
        resp = c._call("POST", "/api/v1/practice-tests/authorize", body, authorized=True)
    except JammersError as exc:
        print(f"[probe] authorize FAILED: HTTP {exc.status} code={exc.code} msg={exc.message}")
        return 2
    print(f"[probe] authorize OK ({time.time() - started:.2f}s) keys={sorted(resp)}")

    ticket_b64 = resp.get("practice_ticket_b64")
    print(f"[probe] ticket_b64 len={len(ticket_b64 or '')}")

    claims = None
    if ticket_b64:
        # 尝试当作 JWT 风格的三段式票据解析
        parts = ticket_b64.split(".")
        print(f"[probe] ticket segments: {[len(p) for p in parts]}")
        if len(parts) == 3:
            try:
                header = json.loads(b64u_decode(parts[0]))
                claims = json.loads(b64u_decode(parts[1]))
                print("[probe] ticket header:", json.dumps(header, ensure_ascii=False))
                print("[probe] ticket claims:", json.dumps(claims, ensure_ascii=False, indent=2))
            except Exception as exc:  # noqa: BLE001
                print(f"[probe] ticket 解析失败: {type(exc).__name__}: {exc}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "status": st,
                    "login": {k: v for k, v in login.items() if k != "access_token"},
                    "authorize_body": body,
                    "authorize_response_keys": sorted(resp),
                    "ticket_b64": ticket_b64,
                    "ticket_claims": claims,
                    "server_time": resp.get("server_time"),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"[probe] 写入 {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
