#!/usr/bin/env python3
"""发现 login-jammers 的位置与账号配置（**不写死任何绝对路径**）。

解析顺序（先命中先用）
----------------------
1. 函数参数 ``explicit``
2. 环境变量 ``JAMMERS_ROOT`` / ``LOGIN_JAMMERS_ROOT``
3. ``script/jammers.local.json``（本地、已 gitignore）里的 ``login_jammers_root``
4. 从本文件所在目录向上逐级查找，检查兄弟目录里是否有
   ``<dir>/linux-client/python/jammers_auth.py``
5. 再从当前工作目录向上做同样的查找

因此只要 ``login-jammers`` 与 ``mathModel2026B`` 并列放置，或者设置了
``JAMMERS_ROOT``，脚本都能自己找到，换机器/换目录都不用改代码。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

#: 用于识别 login-jammers 仓库根目录的标志文件（相对仓库根）
ROOT_MARKERS = (
    "linux-client/python/jammers_auth.py",
    "linux-client/PROTOCOL.md",
)

LOCAL_CONFIG_NAME = "jammers.local.json"

#: 框架要求的最低 Python 版本
MIN_PYTHON = (3, 11)


class PathResolutionError(RuntimeError):
    pass


def _looks_like_login_jammers(path: Path) -> bool:
    return all((path / marker).exists() for marker in ROOT_MARKERS[:1])


def local_config_path() -> Path:
    return Path(__file__).resolve().parent / LOCAL_CONFIG_NAME


def read_local_config() -> dict:
    path = local_config_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _candidate_roots(start: Path, levels: int = 6) -> list[Path]:
    """从 ``start`` 向上，收集每一级目录自身与其子目录作为候选。"""
    out: list[Path] = []
    current = start.resolve()
    for _ in range(levels):
        out.append(current)
        try:
            for child in sorted(current.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    out.append(child)
        except OSError:
            pass
        if current.parent == current:
            break
        current = current.parent
    return out


def find_login_jammers_root(explicit: str | Path | None = None) -> Path:
    tried: list[str] = []

    if explicit:
        path = Path(explicit).expanduser()
        if _looks_like_login_jammers(path):
            return path.resolve()
        tried.append(f"参数 --login-jammers: {path}")

    for env_name in ("JAMMERS_ROOT", "LOGIN_JAMMERS_ROOT"):
        raw = os.environ.get(env_name)
        if raw:
            path = Path(raw).expanduser()
            if _looks_like_login_jammers(path):
                return path.resolve()
            tried.append(f"环境变量 {env_name}={raw}")

    configured = read_local_config().get("login_jammers_root")
    if configured:
        path = Path(str(configured)).expanduser()
        if not path.is_absolute():
            path = Path(__file__).resolve().parent / path
        if _looks_like_login_jammers(path):
            return path.resolve()
        tried.append(f"{LOCAL_CONFIG_NAME}: {configured}")

    starts = [Path(__file__).resolve().parent, Path.cwd()]
    seen: set[Path] = set()
    for start in starts:
        for candidate in _candidate_roots(start):
            if candidate in seen:
                continue
            seen.add(candidate)
            if _looks_like_login_jammers(candidate):
                return candidate.resolve()

    message = [
        "找不到 login-jammers 仓库（需要包含 linux-client/python/jammers_auth.py）。",
        "请任选一种方式指定：",
        "  · 环境变量：export JAMMERS_ROOT=/path/to/login-jammers",
        f"  · 本地配置：在 {local_config_path()} 写入 "
        '{"login_jammers_root": "/path/to/login-jammers"}',
        "  · 命令行：  --login-jammers /path/to/login-jammers",
    ]
    if tried:
        message.append("已尝试：" + "; ".join(tried))
    raise PathResolutionError("\n".join(message))


def resolve_python(preferred: str | None = None) -> str:
    """挑一个满足框架要求的 Python 解释器。"""
    candidates: list[str] = []
    if preferred:
        candidates.append(preferred)
    env_python = os.environ.get("JAMMERS_PYTHON")
    if env_python:
        candidates.append(env_python)
    candidates.append(sys.executable)
    for name in ("python3.14", "python3.13", "python3.12", "python3.11", "python3"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    for candidate in candidates:
        if not candidate:
            continue
        version = _python_version(candidate)
        if version is not None and version >= MIN_PYTHON:
            return candidate
    raise PathResolutionError(
        f"找不到 >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 的 Python 解释器；"
        "可用 pyenv 安装，例如 `pyenv install 3.13.12`，"
        "或用 JAMMERS_PYTHON 指定。"
    )


def _python_version(executable: str) -> tuple[int, int] | None:
    try:
        out = subprocess.run(
            [executable, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    try:
        major, minor = out.stdout.strip().split(".")
        return int(major), int(minor)
    except ValueError:
        return None


@dataclass(frozen=True)
class JammersPaths:
    root: Path
    python_executable: str

    @property
    def client_dir(self) -> Path:
        return self.root / "linux-client"

    @property
    def auth_cli(self) -> Path:
        return self.client_dir / "python" / "jammers_auth.py"

    @property
    def robot_cli(self) -> Path:
        return self.client_dir / "python" / "jammers_robot.py"

    @property
    def node_cli(self) -> Path:
        return self.client_dir / "node" / "jammers-auth.mjs"

    @property
    def default_config(self) -> Path:
        return self.client_dir / "config.json"

    @property
    def config_example(self) -> Path:
        return self.client_dir / "config.example.json"

    @property
    def default_session(self) -> Path:
        cache = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
        return Path(cache) / "jammers" / "session.json"

    # -- 便捷方法 --------------------------------------------------------
    def auth_command(self, *args: str) -> list[str]:
        return [self.python_executable, str(self.auth_cli), *args]

    def robot_command(self, *args: str) -> list[str]:
        return [self.python_executable, str(self.robot_cli), *args]

    def read_account_config(self, path: str | Path | None = None) -> dict:
        config_path = Path(path).expanduser() if path else self.default_config
        if not config_path.exists():
            return {}
        with config_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}

    def team_no(self, path: str | Path | None = None) -> str | None:
        """从 login-jammers 的 config.json 里取队号（= robot_id），避免在代码里写死。"""
        env_team = os.environ.get("JAMMERS_TEAM_NO")
        if env_team:
            return env_team
        config = self.read_account_config(path)
        team = config.get("team_no")
        if team:
            return str(team)
        session = self.read_session()
        if session.get("team_no"):
            return str(session["team_no"])
        return None

    def read_session(self, path: str | Path | None = None) -> dict:
        session_path = Path(path).expanduser() if path else self.default_session
        if not session_path.exists():
            return {}
        try:
            with session_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}


def resolve(
    login_jammers: str | Path | None = None,
    python: str | None = None,
) -> JammersPaths:
    return JammersPaths(
        root=find_login_jammers_root(login_jammers),
        python_executable=resolve_python(python),
    )


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="解析 login-jammers 路径与环境")
    parser.add_argument("--login-jammers", default=None)
    parser.add_argument("--python", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    paths = resolve(args.login_jammers, args.python)
    info = {
        "login_jammers_root": str(paths.root),
        "python": paths.python_executable,
        "auth_cli": str(paths.auth_cli),
        "robot_cli": str(paths.robot_cli),
        "node_cli": str(paths.node_cli),
        "config": str(paths.default_config),
        "config_exists": paths.default_config.exists(),
        "session": str(paths.default_session),
        "session_exists": paths.default_session.exists(),
        "team_no": paths.team_no(),
    }
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        for key, value in info.items():
            print(f"{key:>22}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
