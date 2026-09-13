"""问题3 / 问题4 的**版本化决策方法注册表**（"最终成品 + 阶段性有益尝试"）。

为什么有这个模块
----------------
问题 3/4 的策略经过两轮独立迭代，先后出现过十余个版本。其中一部分是**净收益**
的（保留为可选决策方法），另一部分是**负结果**（已回退，只记录在文档里）。
把"哪些版本可以选、各自的参数默认值是什么、上一代比这一代强在哪"集中到一个
注册表，可以避免三件事：

1. ``script/run.py`` 与各种 bench 脚本各自维护一份 import 与参数默认值；
2. 交付配置（例如 ``schedule_min_readings=1``）散落在调用点上，改一处漏一处；
3. 选了"未经证明"的中间代却以为在用最终版。

纳入范围（``DECISION_METHODS``）
-------------------------------
问题3（全向，加 :func:`q3_v5` 起逐代）
    ``q3``      框架基线：全频道覆盖扫描 + 按频道号收尾 + 在线最近邻
    ``q3-v5``   布局可调 + 扫描检测预算剪枝 + 可证明安全的范围上界剪枝
    ``q3-v6``   **扫描点与待清源合并成单一任务集**（最大单项杠杆）
    ``q3-v7``   任务集路由换 NN + 2-opt（带前瞻）
    ``q3-v8``   **直接清优先**：到估计点先 ``/clear``，失败才精定位
    ``q3-v15``  **最终交付**：v8 + no_signal 排除修正估计点（30/30 全清）

问题4（全向 + 定向混合）
    ``q4-v8``   v8 直接当定向策略用 —— **反例**，发现层不朝向完备会漏源
    ``q4-v9``   **阶段性有益尝试**：朝向完备的两圈式发现层（25 点/18828m）
                + 正面锥限定的精定位候选 + 可行域内部取点 + 镜像探点
    ``q4-v14``  **最终交付**：v9 + 沿射线推进（walk-the-ray）二分精定位
                + 地毯式清除 + 免重测/免重清（30/30 全清）

未纳入（负结果，见 ``docs/q34-version-lineage.md``）：v10 侧向补测、v11 延迟调度、
v12 单读数猜距、v13 顺路探针、v16 侧向补测先行、``follow_plan``、
``schedule_min_readings=2``、``ray_entry_scheduling``、``ray_first``。

参数默认值口径
--------------
注册表里的参数**就是交付配置**，不是类的默认值。两者不同的地方都在此处显式写出
并注明理由（例如 ``schedule_min_readings=1``、``or_opt_passes=0``），
以便"跑出来的数字"与"文档里的数字"始终对得上。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from .client import SimulatorClient
from .state import DogState
from .strategy import Budget, Q3Params, Q3Strategy

__all__ = [
    "DECISION_METHODS",
    "DecisionMethod",
    "MethodSpec",
    "Q3_DELIVERY_PARAMS",
    "Q4_DELIVERY_PARAMS",
    "VERSION_ORDER",
    "available_methods",
    "INTERMEDIATE_ENTRIES",
    "ROUTE_ENTRIES",
    "method_tag",
    "usable_routes",
    "build_method",
    "describe_methods",
    "q3_v5",
    "paper_q4",
    "q3_matrix",
    "q3_v6",
    "q3_v7",
    "q3_v8",
    "q3_v15",
    "q4_v8",
    "q4_v9",
    "q4_v14",
]

# --------------------------------------------------------------------------
# 交付配置常量
# --------------------------------------------------------------------------
#: v6 及以后：**攒够 1 条读数即登记为待清任务**。
#: 类的默认值是 2，但那是"攒够 2 条才调度"；实测 2 会拖到扫描环走完才清源，
#: 破坏"顺路清"的交织（30 seed 平均 314.77 vs 1 条时的 267.19）。
#: 这一条是本轮最大的单项杠杆（≈ −11%）。
SCHEDULE_MIN_READINGS = 1

#: v15：在线路由 or-opt 额外轮数。
#: 类默认 4；但 v2 归档的 30 seed 记录里"or-opt 在线版 ≈ 0"，
#: 且 v15 相对 v8 的净收益（−0.9 s/源）已由 no_signal 排除区单独解释，
#: 因此交付配置关闭 or-opt，避免把中性项当作收益项写进论文。
OR_OPT_PASSES = 0

Q3_DELIVERY_PARAMS: dict[str, Any] = {"schedule_min_readings": SCHEDULE_MIN_READINGS}
Q4_DELIVERY_PARAMS: dict[str, Any] = {}


# --------------------------------------------------------------------------
# 注册表条目
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class MethodSpec:
    """一个可选决策方法的元数据。"""

    key: str
    #: 适用的问题集合：``(3,)`` = 只能解问题3（全向）；
    #: ``(4,)`` = 只能解问题4（全向+定向混合，需 ``--directional``）；
    #: ``(3, 4)`` = 两者都能跑（例如 matrix 的代码路径两边都有，
    #: 但它在问题4 上不满足"确保全部清除"，见 :attr:`fails_q4_requirement`）。
    problem: tuple[int, ...]
    #: 参数 dataclass 的 "模块:类名"
    params_ref: str
    #: 策略类的 "模块:类名"
    strategy_ref: str
    #: 交付参数覆盖（见模块 docstring "参数默认值口径"）
    overrides: dict[str, Any]
    #: 是否为本仓库的最终交付版本
    final: bool
    #: 该版本引入的**新**机制（相对上一代）
    adds: str
    #: 相对上一代的实测效果（同 mock、同 seed；来源见 docstring）
    effect: str
    #: 是否属于"阶段性有益尝试"（非最终版但净收益为正，保留可选）
    beneficial_intermediate: bool = False
    #: 是否是该问题"路线B（迭代优化版）"的**交付入口**之一。
    #: ``final`` 只标最终版（每问题一个），但路线B 可以有两个并列入口
    #: （问题3 的 ``q3-v8`` 在本仓库 mock 口径下优于 ``q3-v15``）。
    route_entry: bool = False
    #: 已过时的基线：仅用于回归/消融，**不应作为交付选项**
    outdated: bool = False
    #: 在**问题4**上不满足"确保全部清除"，因此不是问题4 的可选项
    fails_q4_requirement: bool = False
    #: 是否为"另一条独立路线"（不是历代版本，而是并列的另一种解法）
    separate_route: bool = False
    #: 备注 / 使用约束
    note: str = ""


def _resolve_ref(ref: str):
    """把 ``"module:ClassName"`` 解析成类对象（相对 ``mathmodel2026b`` 包）。"""
    import importlib

    module_name, class_name = ref.split(":")
    module = importlib.import_module(module_name, package=__package__)
    return getattr(module, class_name)


DECISION_METHODS: dict[str, MethodSpec] = {
    # ---------------- 问题3 ----------------
    "q3": MethodSpec(
        key="q3",
        problem=(3,),
        params_ref="mathmodel2026b.strategy:Q3Params",
        strategy_ref="mathmodel2026b.strategy:Q3Strategy",
        overrides={},
        final=False,
        outdated=True,
        adds="框架基线：全频道覆盖扫描 + 在线最近邻收尾 + 逼近半径 30/60/120/250",
        effect=(
            "本仓库 mock 30 seed：332.19 s/源、30/30；**线上 5 场：542.9 / 626.1 / "
            "392.8 / 641.6 / 677.0 s/源（中位 641.6）**"
        ),
        note=(
            "⚠️ **已过时的基线，不要作为问题3 的交付选项**：线上实测 500~680 s/源，"
            "明显差于 matrix（330 量级）与迭代优化版（267~274）。"
            "保留它只为回归比较与『历代改了多少』的量化。"
        ),
    ),
    "matrix": MethodSpec(
        key="matrix",
        problem=(3, 4),
        params_ref="mathmodel2026b.strategy_matrix:MatrixParams",
        strategy_ref="mathmodel2026b.strategy_matrix:KnowledgeSearchStrategy",
        overrides={},
        final=False,
        separate_route=True,
        route_entry=True,
        fails_q4_requirement=True,
        adds=(
            "**另一条独立路线**（不是历代版本）：知识矩阵(channel×path) + "
            "在线覆盖证书 + 有效接收半径在线夹逼 + 集合覆盖缺口补测"
        ),
        effect=(
            "本仓库 mock 30 seed：330.24 s/源、30/30（对照 q3 332.19、q3-v8 267.19）；"
            "**线上 1 场：479.3 s/源、10/10 全清**"
            "（2026-09-13 11:42，case ZD4M-9UQ8-AKZS-JTG6，虚拟 4793 s、墙钟 2.1 s）"
        ),
        note=(
            "问题3 的**路线 A**，与迭代优化版并列可选。"
            "⚠️ 三点必须记住："
            "(a) matrix 线上目前只有 **1 场**（479.3 s/源），落在旧 q3 线上区间 392.8~677.0 内、"
            "低于其中位 641.6，但线上每场是随机案例，**不能**据此断言它优于迭代优化版"
            "——v8/v15 线上仍为零场，且同一 mock 上 v8 快 19%；"
            "(b) mock→线上的膨胀倍数：旧 q3 ≈ ×1.93、matrix ≈ ×1.45（主因是案例分布而非运动学，"
            "官方运动学已逐行复算确认与 mock 一致，见 docs/robot-link-windows-vm.md §3.9）；"
            "(c) 它在**问题4 上不可选**：默认 polygon 布局 12/30，"
            "配 scan_layout=axial 也只到 29/30（漏 seed 6 的退化交会几何）。"
        ),
    ),
    "q3-v5": MethodSpec(
        key="q3-v5",
        problem=(3,),
        params_ref="mathmodel2026b.strategy_v5:Q3V5Params",
        strategy_ref="mathmodel2026b.strategy_v5:Q3V5Strategy",
        overrides={},
        final=False,
        adds="布局可调（中心+n边形）+ 扫描阶段检测预算剪枝 + 可证明安全的范围上界剪枝",
        effect="单独使用 404.91 s/源（劣于基线）；机制被 v6~v8 继承后才体现价值",
        beneficial_intermediate=True,
        note=(
            "⚠️ 这一代**不要单独作为交付版**：实测比基线差。它的价值有两个——"
            "(a) 把扫描布局变成可调参数，(b) 提供 _sweep_v5/_scan_points 等 v6+ 的基座。"
        ),
    ),
    "q3-v6": MethodSpec(
        key="q3-v6",
        problem=(3,),
        params_ref="mathmodel2026b.strategy_v6:Q3V6Params",
        strategy_ref="mathmodel2026b.strategy_v6:Q3V6Strategy",
        overrides={"schedule_min_readings": SCHEDULE_MIN_READINGS},
        final=False,
        adds="**扫描点与待清源合并成单一任务集**，按最近邻在线调度",
        effect="5 seed 同 mock：312.94 s/源（基线 338.53）；30 seed 321.05",
        beneficial_intermediate=True,
        note="结构杠杆最大的一代：两段路线（扫描环 + 清源）合并后省约 5km/例。",
    ),
    "q3-v7": MethodSpec(
        key="q3-v7",
        problem=(3,),
        params_ref="mathmodel2026b.strategy_v7:Q3V7Params",
        strategy_ref="mathmodel2026b.strategy_v7:Q3V7Strategy",
        overrides={"schedule_min_readings": SCHEDULE_MIN_READINGS},
        final=False,
        adds="任务集路由换 NN + 2-opt（开放路径，固定起点），带前瞻",
        effect="5 seed 同 mock：324.03 s/源（v6 312.94）；30 seed 326.05",
        beneficial_intermediate=True,
        note=(
            "⚠️ 在**本仓库的 mock** 上这一代略逊于 v6（+11 s/源），但在上游归档的 10 seed 上"
            "是 326.05 vs 321.05 的反向结论也不稳定；保留它是因为 v8/v9/v14 都建立在"
            "这个 2-opt 路由骨架之上，且 follow_plan=False 时它与 v6 的差值在噪声带内。"
        ),
    ),
    "q3-v8": MethodSpec(
        key="q3-v8",
        problem=(3,),
        params_ref="mathmodel2026b.strategy_v8:Q3V8Params",
        strategy_ref="mathmodel2026b.strategy_v8:Q3V8Strategy",
        overrides={"schedule_min_readings": SCHEDULE_MIN_READINGS},
        final=False,
        adds="**直接清优先**：走到估计点先 /clear（失败仅 5s，与一次检测同价），失败再精定位",
        effect="本仓库 mock 30 seed：267.19 s/源、30/30 全清（与归档逐位一致，见 framework/tests/test_versioned_strategies.py::test_v8_reproduces_archived_30_seed_median）",
        beneficial_intermediate=True,
        route_entry=True,
        note=(
            "问题3 的关键解锁点：MEC>20m **不等于**估计点偏离 >20m（可行域可能是细长条），"
            "所以不必为了补读数多绕一趟。它也是问题4 全部版本（v9/v14）的基类。"
        ),
    ),
    "q3-v15": MethodSpec(
        key="q3-v15",
        problem=(3,),
        params_ref="mathmodel2026b.strategy_v15:Q3V15Params",
        strategy_ref="mathmodel2026b.strategy_v15:Q3V15Strategy",
        overrides={
            "schedule_min_readings": SCHEDULE_MIN_READINGS,
            "or_opt_passes": OR_OPT_PASSES,
        },
        final=True,
        route_entry=True,
        adds="**no_signal 排除区修正估计点**（全向源在 B 处 no_signal ⟹ 真值在 disk(B,1000) 外）",
        effect="本仓库 mock 30 seed：273.73 s/源、30/30；归档交付口径 264.81（v8 267.19）。本仓库 mock 上该机制为 −6.5 s/源的回归，见 docs/q34-version-lineage.md §2.3",
        note=(
            "本仓库问题3 最终交付版。可行域（用于 MEC≤20 判据）**不变**，只是让调度锚点"
            "与直接清的命中点更准。or-opt 默认关闭（中性项）。"
        ),
    ),
    # ---------------- 问题4 ----------------
    "paper-q4": MethodSpec(
        key="paper-q4",
        problem=(4,),
        params_ref="mathmodel2026b.strategy:Q3Params",
        strategy_ref="mathmodel2026b.strategy_q4:Q4Strategy",
        overrides={},
        final=False,
        separate_route=True,
        route_entry=True,
        adds=(
            "**另一条独立路线**：队友论文的冻结内核 —— 31 点轴向三角网格(s=950)"
            " + 楔形交会 + 双假设辨识 + 正面 NBV 枚举 + 矩形兜底清扫"
        ),
        effect="本仓库 mock 30 seed：1308.84 s/源、30/30 全清（q4-v14 为 513.66、同为 30/30）",
        note=(
            "问题4 的**路线 A**，唯一在 5 个真实画像代理上 5/5 全清的路线，"
            "也是『能证明全覆盖』的兜底（发现层局部凸包证书 + 矩形兜底证明）。"
            "⚠️ 只解问题4，必须配 --directional；问题3 **不采纳**它"
            "（mock 上 567.39 s/源，比 matrix 与迭代优化版都慢）。"
            "内核冻结，勿就地修改。"
        ),
    ),
    "q4-v8": MethodSpec(
        key="q4-v8",
        problem=(4,),
        params_ref="mathmodel2026b.strategy_v8:Q3V8Params",
        strategy_ref="mathmodel2026b.strategy_v8:Q3V8Strategy",
        overrides={"schedule_min_readings": SCHEDULE_MIN_READINGS},
        final=False,
        fails_q4_requirement=True,
        adds="把 v8（全向）直接套到定向场景",
        effect=(
            "本仓库 mock 30 seed：仅 10/30 全清（7 点环布局的朝向覆盖不完备）；"
            "其 avg 377.35 是『跳过难案例』的假优，不可与全清率分开读"
        ),
        note=(
            "⚠️ **反例臂**，只用于演示『发现层必须朝向完备』这条结论。"
            "定向源只在正面半平面内有信号，圆盘覆盖 ≠ 发现保证。"
        ),
    ),
    "q4-v9": MethodSpec(
        key="q4-v9",
        problem=(4,),
        params_ref="mathmodel2026b.strategy_v9:Q4V9Params",
        strategy_ref="mathmodel2026b.strategy_v9:Q4V9Strategy",
        overrides={"schedule_min_readings": SCHEDULE_MIN_READINGS},
        final=False,
        adds=(
            "**朝向完备的两圈式发现层**（内圈网格 s=950@r≤1750 + 外圈环 12@1900，"
            "25 点/18828m）+ 正面锥限定的精定位候选 + 可行域内部取点 + 镜像探点"
        ),
        effect="本仓库 mock 30 seed：647.31 s/源、23/30 全清（对照 q4-v8 10/30、matrix-axial 29/30）；上游归档 10 seed 546.28（v8 直接套用 793.48）",
        beneficial_intermediate=True,
        fails_q4_requirement=True,
        note=(
            "阶段性有益尝试：把发现层从轴向网格 31 点/28500m 压到 25 点/18828m，"
            "同时保持朝向完备（最坏正面距离 ≤1000m）。**但它仍会漏清**："
            "楔形退化几何下补救探点全落在源的背面。需要 v14 的沿射线推进。"
        ),
    ),
    "q4-v14": MethodSpec(
        key="q4-v14",
        problem=(4,),
        params_ref="mathmodel2026b.strategy_v14:Q4V14Params",
        strategy_ref="mathmodel2026b.strategy_v14:Q4V14Strategy",
        overrides={},
        final=True,
        route_entry=True,
        adds=(
            "**沿射线推进**（walk-the-ray）二分精定位 + 区间平移继承 + 地毯式清除"
            " + 同点免重测 + 失败点免重清"
        ),
        effect="本仓库 mock 30 seed：513.66 s/源、30/30 全清，是论文冻结内核 (1308.84 s/源、同为 30/30) 的 1/2.55；归档交付口径 500.57 s/源、30/30",
        note=(
            "本仓库问题4 最终交付版。核心正确性论证：射线上的点 q(t)=A+t·u 在 t<t_G 时"
            "必出读数、t>t_G 时定向源必 no_signal，故『步进 + no_signal 二分』在 O(log) 步内"
            "把源距压到小区间；区间 <40m 时改用与朝向无关的 clear 判据地毯式清除。"
            "默认关闭 ray_entry_scheduling 与 ray_first（两者实测回归且破坏 seed 7）。"
        ),
    ),
}

#: **每个问题的可选路线**：``问题 -> (路线A(独立解法), 路线B(迭代优化版))``。
#: 这是"哪些方法可以真正用于交付"的**唯一权威声明**，``script/list_methods.py``
#: 与 ``framework/tests/test_versioned_strategies.py`` 都消费它，避免两处口径漂移。
#:
#: 问题3：``matrix``（知识矩阵）与 ``q3-v8``/``q3-v15``（同一血脉的迭代优化版）；
#:        原始 ``q3`` 已过时（线上 500~680 s/源）。
#: 问题4：``paper-q4``（论文冻结内核）与 ``q4-v14``（迭代优化版）；
#:        整条 matrix 路线（默认 12/30、axial 29/30）与 ``q4-v8``（10/30）、
#:        ``q4-v9``（23/30）都不满足"确保全部清除"。
ROUTE_ENTRIES: dict[int, dict[str, tuple[str, ...]]] = {
    3: {"independent": ("matrix",), "iterated": ("q3-v8", "q3-v15")},
    4: {"independent": ("paper-q4",), "iterated": ("q4-v14",)},
}

#: 路线内部的中间代（净收益为正，但不作为交付入口；供消融/回顾）。
INTERMEDIATE_ENTRIES: dict[int, tuple[str, ...]] = {
    3: ("q3-v5", "q3-v6", "q3-v7"),
    4: ("q4-v9",),
}


def _validate_route_model() -> None:
    """启动时自检：``route_entry`` 标记必须与 :data:`ROUTE_ENTRIES` 一致。

    两处声明一旦漂移，"清单里能选但测试说不可选"这类不一致就会悄悄发生。
    """
    declared = {k for entries in ROUTE_ENTRIES.values() for v in entries.values() for k in v}
    flagged = {k for k, spec in DECISION_METHODS.items() if spec.route_entry}
    if declared != flagged:
        raise AssertionError(
            "路线声明漂移：ROUTE_ENTRIES=%s 与 route_entry 标记=%s 不一致"
            % (sorted(declared), sorted(flagged))
        )


def usable_routes(problem: int) -> list[str]:
    """返回某问题的全部可用路线入口（路线A + 路线B，按 :data:`VERSION_ORDER`）。"""
    entries = ROUTE_ENTRIES.get(problem, {})
    flat = set(entries.get("independent", ())) | set(entries.get("iterated", ()))
    return [k for k in VERSION_ORDER if k in flat]


_validate_route_model()

#: 推荐浏览顺序（先按问题，再按"路线 → 历代"）。
#: ``matrix`` 是问题3 的**路线 A**（并列的独立解法），放在历代版本之前。
VERSION_ORDER: tuple[str, ...] = (
    "q3",
    "matrix",
    "q3-v5",
    "q3-v6",
    "q3-v7",
    "q3-v8",
    "q3-v15",
    "q4-v8",
    "q4-v9",
    "q4-v14",
    "paper-q4",
)


# --------------------------------------------------------------------------
# 构造 / 查询
# --------------------------------------------------------------------------
def available_methods(problem: int | None = None) -> list[str]:
    """返回可用的决策方法名（按 :data:`VERSION_ORDER`）。"""
    keys = [k for k in VERSION_ORDER if k in DECISION_METHODS]
    if problem is None:
        return keys
    return [k for k in keys if problem in DECISION_METHODS[k].problem]


def build_method(key: str, **param_overrides: Any):
    """构造第 ``key`` 代策略实例（已应用交付参数覆盖 + 调用方覆盖）。

    调用方覆盖优先于交付覆盖，便于 ``--param`` 做消融。
    """
    if key not in DECISION_METHODS:
        raise KeyError(
            f"未知决策方法 {key!r}；可用：{', '.join(available_methods())}"
        )
    spec = DECISION_METHODS[key]
    params_cls = _resolve_ref(spec.params_ref)
    values = dict(spec.overrides)
    values.update(param_overrides)
    params = params_cls(**values)
    strategy_cls = _resolve_ref(spec.strategy_ref)
    return strategy_cls(params)


def method_tag(spec: "MethodSpec") -> str:
    """单个方法的状态标签（``list_methods.py`` 也用它，保证口径一致）。"""
    if spec.final:
        return "★ 最终交付"
    if spec.outdated:
        return "✗ 已过时"
    if spec.separate_route:
        return "◇ 独立路线"
    if spec.route_entry and spec.beneficial_intermediate:
        return "◆ 阶段有益（同时是路线B 入口）"
    if spec.beneficial_intermediate:
        return "◆ 阶段有益"
    return "· 对照/反例"


def describe_methods(problem: int | None = None, *, verbose: bool = False) -> str:
    """渲染决策方法清单（供 ``script/list_methods.py`` 与文档引用）。"""
    lines: list[str] = []
    for key in available_methods(problem):
        spec = DECISION_METHODS[key]
        lines.append(f"{key:<9} P{spec.problem}  {method_tag(spec)}")
        if spec.separate_route:
            lines.append("          【另一条独立路线，非历代版本】")
        lines.append(f"          新增：{spec.adds}")
        lines.append(f"          效果：{spec.effect}")
        if spec.outdated:
            lines.append("          ⚠️ 已过时：不要作为交付选项，仅用于回归/消融")
        if spec.fails_q4_requirement:
            lines.append("          ⚠️ 在问题4 上不满足『确保全部清除』，不是问题4 的可选项")
        if verbose and spec.note:
            lines.append(f"          备注：{spec.note}")
        if verbose and spec.overrides:
            lines.append(
                "          交付参数覆盖："
                + ", ".join(f"{k}={v!r}" for k, v in spec.overrides.items())
            )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 向后兼容的显式工厂（老脚本可以直接 import 这些名字）
# --------------------------------------------------------------------------
def q3_v5(params: Any | None = None):
    from .strategy_v5 import Q3V5Params, Q3V5Strategy

    return Q3V5Strategy(params or Q3V5Params())


def q3_v6(params: Any | None = None):
    from .strategy_v6 import Q3V6Params, Q3V6Strategy

    return Q3V6Strategy(params or Q3V6Params(**Q3_DELIVERY_PARAMS))


def q3_v7(params: Any | None = None):
    from .strategy_v7 import Q3V7Params, Q3V7Strategy

    return Q3V7Strategy(params or Q3V7Params(**Q3_DELIVERY_PARAMS))


def q3_v8(params: Any | None = None):
    from .strategy_v8 import Q3V8Params, Q3V8Strategy

    return Q3V8Strategy(params or Q3V8Params(**Q3_DELIVERY_PARAMS))


def q3_v15(params: Any | None = None):
    from .strategy_v15 import Q3V15Params, Q3V15Strategy

    if params is None:
        params = Q3V15Params(or_opt_passes=OR_OPT_PASSES, **Q3_DELIVERY_PARAMS)
    return Q3V15Strategy(params)


def q4_v8(params: Any | None = None):
    from .strategy_v8 import Q3V8Params, Q3V8Strategy

    return Q3V8Strategy(params or Q3V8Params(**Q3_DELIVERY_PARAMS))


def q4_v9(params: Any | None = None):
    from .strategy_v9 import Q4V9Params, Q4V9Strategy

    return Q4V9Strategy(params or Q4V9Params(**Q4_DELIVERY_PARAMS))


def q4_v14(params: Any | None = None):
    from .strategy_v14 import Q4V14Params, Q4V14Strategy

    return Q4V14Strategy(params or Q4V14Params(**Q4_DELIVERY_PARAMS))


#: ``DecisionMethod`` = ``MethodSpec`` 的别名，给外部脚本一个语义化名字。
DecisionMethod = MethodSpec


def _selftest() -> int:
    """``python -m mathmodel2026b.versioned``：每个方法都能构造出来。"""
    failures = 0
    for key in available_methods():
        try:
            strategy = build_method(key)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  FAIL {key}: {type(exc).__name__}: {exc}")
            continue
        print(f"  OK   {key:<9} -> {type(strategy).__name__}")
    print(f"\n{len(available_methods())} 个决策方法，{failures} 个构造失败")
    print("问题3 可选路线：matrix ／ q3-v8、q3-v15；问题4 可选路线：paper-q4 ／ q4-v14")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())


def q3_matrix(params: Any | None = None):
    """问题3 的**路线 A**：知识矩阵策略（``--strategy matrix`` 的同义入口）。"""
    from .strategy_matrix import KnowledgeSearchStrategy, MatrixParams

    return KnowledgeSearchStrategy(params or MatrixParams())


def paper_q4(params: Any | None = None):
    """问题4 的**路线 A**：论文冻结内核适配器（``--strategy paper-q4`` 的同义入口）。"""
    from .strategy_q4 import Q4Strategy as PaperQ4Strategy

    return PaperQ4Strategy(params)
