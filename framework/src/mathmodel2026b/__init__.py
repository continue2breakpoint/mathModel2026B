"""2026 CUMCM B题：无线电干扰源的快速自动定位与清除（问题3/4 框架）。

分层：

``protocol``       robot-protocol-v1 线协议类型与解析
``geometry``       扇形交会、可行域裁剪、最小包围圆、覆盖校验
``state``          机器狗 / 逐频道状态与计数
``client``         模拟器通信（HTTP / 记录装饰器 / 回放）
``strategy``       可插拔策略（问题3 实现见 :class:`Q3Strategy`）
``runner``         一次完整运行的编排 + 结构化日志
``mock``           离线 mock 模拟器（无 Windows 客户端时验证工作流）
``logging_utils``  JSONL 日志格式与索引
"""

from .client import (
    HttpSimulatorClient,
    RecordingClient,
    ReplayClient,
    RobotPortGatedError,
    SimulatorClient,
    SimulatorError,
)
from .geometry import (
    Circle,
    Point,
    covering_radius,
    feasible_region,
    minimum_enclosing_circle,
    polygon_diameter,
    q3_seven_point_cover_valid,
    regular_hexagon_scan_points,
)
from .logging_utils import RunLogger, default_log_root, read_index, summarize_index
from .protocol import (
    ARENA_ID,
    ClearKind,
    ClearResult,
    EnterResult,
    ExitResult,
    MeasureKind,
    MeasureResult,
    RobotResponse,
)
from .state import ALL_CHANNELS, ChannelState, ChannelStatus, DogState
from .strategy import Q3BaselineStrategy, Q3Params, Q3Strategy, Strategy

#: ``runner`` 会 import ``mock.server``；这里惰性导入，避免
#: ``python -m mathmodel2026b.mock.server`` 触发 runpy 的 sys.modules 警告。
_LAZY = {"RunConfig", "RunOutcome", "run_once"}


def __getattr__(name: str):
    if name in _LAZY:
        from . import runner

        return getattr(runner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "ALL_CHANNELS",
    "ARENA_ID",
    "ChannelState",
    "ChannelStatus",
    "Circle",
    "ClearKind",
    "ClearResult",
    "DogState",
    "EnterResult",
    "ExitResult",
    "HttpSimulatorClient",
    "MeasureKind",
    "MeasureResult",
    "Point",
    "Q3BaselineStrategy",
    "Q3Params",
    "Q3Strategy",
    "RecordingClient",
    "ReplayClient",
    "RobotPortGatedError",
    "RobotResponse",
    "RunConfig",
    "RunLogger",
    "RunOutcome",
    "SimulatorClient",
    "SimulatorError",
    "Strategy",
    "covering_radius",
    "default_log_root",
    "feasible_region",
    "minimum_enclosing_circle",
    "polygon_diameter",
    "q3_seven_point_cover_valid",
    "read_index",
    "regular_hexagon_scan_points",
    "run_once",
    "summarize_index",
]

__version__ = "0.2.0"
