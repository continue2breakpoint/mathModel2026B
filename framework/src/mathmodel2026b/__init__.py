"""2026 CUMCM B题：无线电干扰源的快速自动定位与清除（问题3/4 框架）。

分层：

``protocol``       robot-protocol-v1 线协议类型与解析
``geometry``       扇形交会、可行域裁剪、最小包围圆、覆盖校验
``state``          机器狗 / 逐频道状态与计数
``client``         模拟器通信（HTTP / 记录装饰器 / 回放）
``strategy``       可插拔策略（问题3 实现见 :class:`Q3Strategy`）
``strategy_v5``…``strategy_v18``
                   问题3/4 的历代策略（版本谱系见 ``docs/q34-version-lineage.md``）；
                   ``v18`` 是当前交付版（问题3/4 通用）
``knowledge_layer``
                   知识矩阵接入 v14/v17/v18 的适配层：失败清除的 20m 排除圆 +
                   **可证明的覆盖式清除**（25m 格铺满保守可行域）
``versioned``      **决策方法注册表**：把历代策略登记成可选决策方法
                   （``script/list_methods.py`` 与 ``script/run.py --strategy`` 共用）
``strategy_matrix``/``knowledge``
                   另一条独立路线：``channel × path`` 观测台账 + 在线覆盖证书
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

#: 决策方法注册表**惰性**暴露：``versioned`` 本身只登记字符串引用，
#: 但保持惰性可以避免 ``python -m mathmodel2026b.versioned`` 触发
#: runpy 的 "found in sys.modules" 警告（与 :data:`_LAZY` 同一处理）。
_LAZY_NAMES = {
    "DECISION_METHODS",
    "MethodSpec",
    "available_methods",
    "build_method",
    "describe_methods",
}

#: ``runner`` 会 import ``mock.server``；这里惰性导入，避免
#: ``python -m mathmodel2026b.mock.server`` 触发 runpy 的 sys.modules 警告。
_LAZY = {"RunConfig", "RunOutcome", "run_once"}


def __getattr__(name: str):
    if name in _LAZY:
        from . import runner

        return getattr(runner, name)
    if name in _LAZY_NAMES:
        from . import versioned

        return getattr(versioned, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "ALL_CHANNELS",
    "ARENA_ID",
    "ChannelState",
    "ChannelStatus",
    "Circle",
    "ClearKind",
    "ClearResult",
    "DECISION_METHODS",
    "DogState",
    "EnterResult",
    "ExitResult",
    "HttpSimulatorClient",
    "MeasureKind",
    "MeasureResult",
    "MethodSpec",
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
    "available_methods",
    "build_method",
    "covering_radius",
    "default_log_root",
    "describe_methods",
    "feasible_region",
    "minimum_enclosing_circle",
    "polygon_diameter",
    "q3_seven_point_cover_valid",
    "read_index",
    "regular_hexagon_scan_points",
    "run_once",
    "summarize_index",
]

__version__ = "0.4.0"
