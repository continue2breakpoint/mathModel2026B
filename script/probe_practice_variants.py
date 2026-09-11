#!/usr/bin/env python3
"""对 ``/api/v1/practice-tests/authorize`` 做变体探测，定位"请求内容不正确"到底卡在哪个字段。

只登录一次，然后依次发送若干候选请求体，打印服务器返回的 code/message。
练习测试不限次数，且这些请求都在 authorize 阶段（不会进入测试），不消耗正式次数。
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from jammers_paths import resolve  # noqa: E402


def main() -> int:
    paths = resolve(None, None)
    sys.path.insert(0, str(paths.root / "linux-client" / "python"))
    from jammers_auth import (  # noqa: E402
        BUILD_ID,
        BUILD_MANIFEST_SHA256,
        CLIENT_VERSION,
        JammersClient,
        JammersError,
        build_device_report,
    )
    from cryptography.hazmat.primitives import serialization  # noqa: E402
    from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: E402

    cfg = json.loads(Path(paths.default_config).read_text(encoding="utf-8"))
    c = JammersClient(timeout=25.0)
    login = c.login(cfg["team_no"], cfg["password"], build_device_report())
    print(f"[probe] login ok team_no={login['team_no']}")

    priv = ed25519.Ed25519PrivateKey.generate()
    raw = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    spki = priv.public_key().public_bytes(
        encoding=serialization.Encoding.DER, format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    pub_raw_b64 = base64.b64encode(raw).decode()
    pub_spki_b64 = base64.b64encode(spki).decode()
    pub_raw_urlsafe = base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def body(**over):
        b = {
            "client_request_id": JammersClient.new_request_id(),
            "problem_no": 3,
            "client_version": CLIENT_VERSION,
            "scenario_schema_version": "scenario-v1",
            "ruleset_version": "rules-v1",
            "package_envelope_version": 1,
            "package_signing_public_key_b64": pub_raw_b64,
        }
        b.update(over)
        return b

    variants: list[tuple[str, dict]] = [
        ("baseline", body()),
        ("ruleset=simulation-rules-v1", body(ruleset_version="simulation-rules-v1")),
        ("scenario=scenario-v2", body(scenario_schema_version="scenario-v2")),
        ("problem_no=4", body(problem_no=4)),
        ("envelope_version=2", body(package_envelope_version=2)),
        ("pubkey=spki-der", body(package_signing_public_key_b64=pub_spki_b64)),
        ("pubkey=urlsafe-nopad", body(package_signing_public_key_b64=pub_raw_urlsafe)),
        ("+team_no/device_digest", body(team_no=login["team_no"],
                                        device_digest=login["device_digest"],
                                        test_start_deadline=login["test_start_deadline"])),
        ("+case_envelope_version", body(case_envelope_version=1)),
        ("+build_id/manifest", body(build_id=BUILD_ID,
                                    build_manifest_sha256=BUILD_MANIFEST_SHA256)),
        ("no scenario/ruleset", {k: v for k, v in body().items()
                                 if k not in ("scenario_schema_version", "ruleset_version")}),
        ("no envelope_version", {k: v for k, v in body().items()
                                 if k != "package_envelope_version"}),
    ]

    for name, b in variants:
        try:
            resp = c._call("POST", "/api/v1/practice-tests/authorize", b, authorized=True)
        except JammersError as exc:
            print(f"  {name:28s} -> HTTP {exc.status} {exc.code}: {exc.message}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {name:28s} -> {type(exc).__name__}: {exc}")
        else:
            print(f"  {name:28s} -> OK keys={sorted(resp)}")
            print("      " + json.dumps(resp, ensure_ascii=False)[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
