"""离线 mock 模拟器（无 Windows 客户端时验证整条工作流）。

这里用惰性导入：``server`` 会 import ``world``，而 ``runner`` 又 import 本包；
如果在本文件里直接 eager import，用 ``python -m mathmodel2026b.mock.server``
启动时会触发 runpy 的 "found in sys.modules" 警告。
"""

from typing import Any

from .world import Case, Jammer, World, bearing_error, generate_case

__all__ = [
    "Case",
    "Jammer",
    "MockSimulator",
    "World",
    "bearing_error",
    "generate_case",
    "run_standalone",
    "validate",
]


def __getattr__(name: str) -> Any:
    if name in {"MockSimulator", "run_standalone", "validate"}:
        from . import server

        return getattr(server, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

