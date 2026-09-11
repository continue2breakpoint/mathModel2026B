#!/usr/bin/env python3
"""线上腿的连通性预检：通过 login-jammers 的 CLI 完成 status / login / presence / renew。

这一步验证的是"我们 -> login-jammers CLI -> 线上平台"这条链路，以及
"真实 robot 端口是否已经开启"（Windows 客户端开始练习/正式测试后才会开）。

设计要点
--------
* **不写死 login-jammers 路径**：见 :mod:`jammers_paths` 的解析顺序。
* **不打印密码**：日志里只记录配置文件路径，命令行中的口令一律替换为 ``***``。
* 结果写成结构化 JSON（``logs/preflight/<run_id>.json``）并追加到
  ``logs/preflight.jsonl``，便于批量跑的时候回看。

用法::

    python script/preflight.py                     # 找路径 + status + login + presence + renew
    python script/preflight.py --no-login          # 只查平台状态与 robot 端口
    python script/preflight.py --config /path/config.json
    JAMMERS_ROOT=/path/to/login-jammers python script/preflight.py

退出码：0 全部成功；1 robot 端口未开（不算失败）；2 平台/business 错误；3 路径或网络错误。
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jammers_paths import (  # noqa: E402
    PathResolutionError,
    JammersPaths,
    resolve,
)

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
LOG_ROOT = REPO_ROOT / "logs"
PREFLIGHT_DIR = LOG_ROOT / "preflight"
PREFLIGHT_INDEX = LOG_ROOT / "preflight.jsonl"

#: 这些业务码不代表"链路不通"，而是"链路通、账号状态如此"
NOTABLE_BUSINESS_CODES = {
    "invalid_credentials",
    "account_already_active",
    "account_cooldown",
    "identity_mismatch",
}


def _redact(cmd: list[str]) -> list[str]:
    out: list[str] = []
    skip_next = False
    for item in cmd:
        if skip_next:
            out.append("***")
            skip_next = False
            continue
        out.append(item)
        if item in ("--password", "--old-password", "--new-password", "--confirm-password"):
            skip_next = True
    return out


def run_cli(cmd: list[str], timeout: float = 60.0) -> dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {
            "command": _redact(cmd),
            "returncode": None,
            "stdout": "",
            "stderr": f"超时（>{timeout}s）",
            "parsed": None,
            "duration_s": round(time.time() - started, 3),
        }
    parsed: Any = None
    for stream in (proc.stdout, proc.stderr):
        text = (stream or "").strip()
        if not text:
            continue
        try:
            parsed = json.loads(text)
            break
        except ValueError:
            continue
    return {
        "command": _redact(cmd),
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "parsed": parsed,
        "duration_s": round(time.time() - started, 3),
    }


def probe_robot_port(host: str = "127.0.0.1", port: int = 2026, timeout: float = 1.5) -> dict:
    """只做 TCP 连接探测：能连上说明客户端在跑且有活动测试（门控已开）。"""
    started = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {
                "host": host,
                "port": port,
                "state": "open",
                "latency_ms": round((time.time() - started) * 1000, 2),
            }
    except (ConnectionRefusedError, socket.timeout, OSError) as exc:
        return {
            "host": host,
            "port": port,
            "state": "closed",
            "error": f"{type(exc).__name__}: {exc}",
        }


def classify_login(step: dict[str, Any]) -> str:
    parsed = step.get("parsed")
    if step.get("returncode") == 0 and isinstance(parsed, dict) and parsed.get("access_token"):
        return "ok"
    if isinstance(parsed, dict) and parsed.get("code") in NOTABLE_BUSINESS_CODES:
        return "business"
    return "failed"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="线上腿预检（经 login-jammers CLI）")
    parser.add_argument("--login-jammers", default=None, help="login-jammers 仓库根目录")
    parser.add_argument("--python", default=None, help="用于跑 login-jammers CLI 的解释器")
    parser.add_argument("--config", default=None, help="账号配置 JSON（默认 linux-client/config.json）")
    parser.add_argument("--session-file", default=None)
    parser.add_argument("--no-login", action="store_true", help="只做 status 与端口探测")
    parser.add_argument("--skip-renew", action="store_true")
    parser.add_argument("--robot-port", type=int, default=2026)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--log-root", default=None)
    args = parser.parse_args(argv)

    log_dir = Path(args.log_root) / "preflight" if args.log_root else PREFLIGHT_DIR
    index_path = (Path(args.log_root) / "preflight.jsonl") if args.log_root else PREFLIGHT_INDEX

    report: dict[str, Any] = {
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "steps": {},
        "ok": False,
        "conclusion": "",
    }

    try:
        paths: JammersPaths = resolve(args.login_jammers, args.python)
    except PathResolutionError as exc:
        print(f"[preflight] 路径解析失败：\n{exc}", file=sys.stderr)
        return 3

    report["paths"] = {
        "login_jammers_root": str(paths.root),
        "python": paths.python_executable,
        "auth_cli": str(paths.auth_cli),
        "robot_cli": str(paths.robot_cli),
        "config": str(Path(args.config).expanduser() if args.config else paths.default_config),
        "config_exists": (Path(args.config).expanduser() if args.config else paths.default_config).exists(),
    }
    print(f"[preflight] login-jammers = {paths.root}")
    print(f"[preflight] python        = {paths.python_executable}")

    # 1) 平台状态
    status = run_cli(paths.auth_command("status"), timeout=args.timeout)
    report["steps"]["status"] = status
    server_status = status.get("parsed") if isinstance(status.get("parsed"), dict) else None
    if status["returncode"] != 0 or server_status is None:
        report["conclusion"] = "无法访问线上平台 /api/v1/status"
        print(f"[preflight] FAIL {report['conclusion']}", file=sys.stderr)
        _write_report(report, log_dir, index_path)
        return 3
    print(
        f"[preflight] status ok: new_tests_enabled={server_status.get('new_tests_enabled')} "
        f"deadline={server_status.get('test_start_deadline')} "
        f"server_time={server_status.get('server_time')}"
    )
    report["server_status"] = server_status

    # 2) robot 端口探测（Windows 客户端是否已开测）
    port_info = probe_robot_port(port=args.robot_port)
    report["steps"]["robot_port"] = port_info
    print(f"[preflight] robot 127.0.0.1:{args.robot_port} -> {port_info['state']}")

    if args.no_login:
        report["ok"] = True
        report["conclusion"] = "仅做了平台状态与 robot 端口探测"
        _write_report(report, log_dir, index_path)
        return 0 if port_info["state"] == "open" else 1

    # 3) 登录
    session_file = (
        Path(args.session_file).expanduser() if args.session_file else paths.default_session
    )
    report["paths"]["session"] = str(session_file)
    session_args = ["--session-file", str(session_file)]

    login_cmd = paths.auth_command("login")
    config_path = Path(args.config).expanduser() if args.config else paths.default_config
    if config_path.exists():
        login_cmd += ["--config", str(config_path)]
    else:
        report["conclusion"] = (
            f"缺少账号配置 {config_path}（可复制 {paths.config_example} 后填写）"
        )
        print(f"[preflight] FAIL {report['conclusion']}", file=sys.stderr)
        _write_report(report, log_dir, index_path)
        return 3
    # 始终显式指定会话文件：默认路径可能落在不可写的位置（容器/受限沙箱），
    # 否则会出现"登录其实已成功、却因为写会话失败而报错"这种最容易被误判的结局。
    login_cmd += session_args

    login = run_cli(login_cmd, timeout=args.timeout)
    report["steps"]["login"] = login
    verdict = classify_login(login)
    report["login_verdict"] = verdict
    if verdict != "ok":
        code = (login.get("parsed") or {}).get("code") if isinstance(login.get("parsed"), dict) else None
        if verdict == "business":
            report["conclusion"] = (
                f"平台可达且请求格式被接受，但登录被业务规则拒绝：{code}"
            )
            print(f"[preflight] PARTIAL {report['conclusion']}")
            report["ok"] = True
            _write_report(report, log_dir, index_path)
            return 2
        report["conclusion"] = "登录失败（网络或协议错误）"
        print(f"[preflight] FAIL {report['conclusion']}: {login.get('stderr')}", file=sys.stderr)
        _write_report(report, log_dir, index_path)
        return 3

    session_args = ["--session-file", str(session_file)]
    print(f"[preflight] login ok: team_no={login['parsed'].get('team_no')}")
    report["team_no"] = login["parsed"].get("team_no")
    print(
        f"[preflight] 剩余正式测试次数：problem3={login['parsed'].get('problem3_remaining_attempts')} "
        f"problem4={login['parsed'].get('problem4_remaining_attempts')} "
        f"待上传包={login['parsed'].get('pending_upload_count')}"
    )

    # 4) presence / renew
    presence = run_cli(paths.auth_command("presence", *session_args), timeout=args.timeout)
    report["steps"]["presence"] = presence
    print(f"[preflight] presence rc={presence['returncode']}")

    if not args.skip_renew:
        renew = run_cli(paths.auth_command("renew", *session_args), timeout=args.timeout)
        report["steps"]["renew"] = renew
        print(f"[preflight] renew rc={renew['returncode']}")

    report["ok"] = True
    if port_info["state"] == "open":
        report["conclusion"] = "线上链路打通：status / login / presence / renew 全部成功；robot 端口已开，可直接 --mode live"
    else:
        report["conclusion"] = (
            "线上链路打通：status / login / presence / renew 全部成功；"
            f"但 robot 端口 {args.robot_port} 未开（尚无进行中的练习/正式测试），"
            "--mode live 需要先在模拟器里开始测试"
        )
    print(f"[preflight] OK {report['conclusion']}")
    _write_report(report, log_dir, index_path)
    return 0


def _write_report(report: dict[str, Any], log_dir: Path, index_path: Path) -> None:
    report["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = log_dir / f"preflight_{stamp}.json"
    with path.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "finished_utc": report["finished_utc"],
                    "ok": report.get("ok"),
                    "login_verdict": report.get("login_verdict"),
                    "team_no": report.get("team_no"),
                    "robot_port": (report["steps"].get("robot_port") or {}).get("state"),
                    "new_tests_enabled": (report.get("server_status") or {}).get("new_tests_enabled"),
                    "conclusion": report.get("conclusion"),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    print(f"[preflight] 报告写入 {path}")
    print(f"[preflight] 索引追加 {index_path}")


if __name__ == "__main__":
    raise SystemExit(main())
