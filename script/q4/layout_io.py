"""Q4 布局的**读写与交付格式**（``LAYOUT_Q4_V17`` 片段），离线工具链共用。

为什么单独一个模块
------------------
快照里"布局文件"有四个互不相同的落点：``_joint_opt_q4.json``（机器可读）、
``_joint_opt_layout.py``（可粘贴片段）、``_strong_tsp_order.py``（只有顺序）、
以及直接写进 ``strategy_v17.py`` 的那份。四个落点各写各的格式化代码，
于是"JSON 里的点"和"粘进框架的点"有可能不一致（快照 23 点候选失败后
回写 JSON 的分支就与框架里真正的 24 点不同源）。这里只保留**一处**格式化实现：

* :func:`save_layout_json` / :func:`load_layout_json` —— ``{"m", "route_len_m", "points_in_order"}``；
* :func:`format_layout_fragment` / :func:`emit_layout` —— ``--emit`` 用的
  ``LAYOUT_Q4_V17: list[tuple[float, float]] = [...]`` 文本，**逐字符可粘贴**进
  ``framework/src/mathmodel2026b/strategy_v17.py``；坐标一律 ``%.1f``，
  与框架里现有那份（一位小数）保持同一种精度，避免"看上去一样、实际差 0.05m"。

与框架的关系
------------
``mathmodel2026b.strategy_v18``（``q4-v18``，最终交付）**继承** ``q4-v17`` 的
``LAYOUT_Q4_V17``：v18 只新增知识矩阵负例与覆盖式清除兜底，**不**改发现层布局。
因此本模块 emit 出的片段同时服务 q4-v17 与 q4-v18；改动它等于同时改两个版本，
需要重跑 :mod:`script.verify_layout_certificate` 出证书。

本模块**不**修改框架文件：``--emit`` 只打印/写到自己指定的路径，粘贴动作交给人。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent          # mathModel2026B/script/q4
REPO_ROOT = SCRIPT_DIR.parent.parent                  # mathModel2026B
sys.path.insert(0, str(REPO_ROOT / "framework" / "src"))

#: 离线工具链的默认输出根目录（不写进快照目录，也不写进 framework/）。
LOG_ROOT = REPO_ROOT / "logs" / "q4"

#: 交付布局的变量名（框架里 ``strategy_v17.LAYOUT_Q4_V17`` 用的就是它）。
LAYOUT_VAR_NAME = "LAYOUT_Q4_V17"

#: 坐标输出精度：框架里那份是一位小数，emit 必须一致。
COORD_FMT = "{:.1f}"

Point2 = tuple[float, float]


# --------------------------------------------------------------------------- 框架布局
def v9_layout() -> list[Point2]:
    """v9 两圈式 25 点布局（方法一、也是联合优化的初始解/兜底解）。"""
    from mathmodel2026b.strategy_v9 import two_tier_nodes  # noqa: PLC0415

    return [(float(x), float(y)) for x, y in two_tier_nodes(950.0, 1750.0, 12, 1900.0)]


def framework_layout() -> list[Point2]:
    """读 ``mathmodel2026b.strategy_v17.LAYOUT_Q4_V17``（交付布局的**唯一真值**）。"""
    from mathmodel2026b.strategy_v17 import LAYOUT_Q4_V17  # noqa: PLC0415

    return [(float(x), float(y)) for x, y in LAYOUT_Q4_V17]


def compare_layouts(a: Sequence[Point2], b: Sequence[Point2], tol: float = 0.05) -> dict:
    """比较两个布局（同序逐点），用于"emit 出来的片段和框架里那份是否一致"。"""
    n = min(len(a), len(b))
    diffs = [
        {
            "index": i,
            "a": [round(float(a[i][0]), 3), round(float(a[i][1]), 3)],
            "b": [round(float(b[i][0]), 3), round(float(b[i][1]), 3)],
            "max_abs_diff": round(max(abs(a[i][0] - b[i][0]), abs(a[i][1] - b[i][1])), 6),
        }
        for i in range(n)
        if max(abs(a[i][0] - b[i][0]), abs(a[i][1] - b[i][1])) > tol
    ]
    return {
        "same_length": len(a) == len(b),
        "n_a": len(a),
        "n_b": len(b),
        "max_abs_diff": round(
            max(
                (max(abs(a[i][0] - b[i][0]), abs(a[i][1] - b[i][1])) for i in range(n)),
                default=0.0,
            ),
            6,
        ),
        "n_points_over_tol": len(diffs),
        "tol_m": tol,
        "diffs": diffs[:20],
    }


# --------------------------------------------------------------------------- JSON 读写
def save_layout_json(
    path: Path | str,
    points: Sequence[Sequence[float]],
    *,
    route_len_m: float | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """写 ``{"m", "route_len_m", "points_in_order"}``（与快照 ``_joint_opt_q4.json`` 同构）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "m": len(points),
        "route_len_m": None if route_len_m is None else round(float(route_len_m), 1),
        "points_in_order": [[round(float(x), 1), round(float(y), 1)] for x, y in points],
    }
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def load_layout_json(path: Path | str) -> tuple[list[Point2], dict]:
    """读 :func:`save_layout_json` 写出的文件，返回 ``(点表, 元数据)``。"""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    pts = [(float(x), float(y)) for x, y in data["points_in_order"]]
    return pts, data


# --------------------------------------------------------------------------- emit 片段
def format_layout_fragment(
    points: Sequence[Sequence[float]],
    *,
    var_name: str = LAYOUT_VAR_NAME,
    header: Iterable[str] = (),
) -> str:
    """把点表格式化成可直接粘贴的 ``LAYOUT_Q4_V17: list[tuple[float, float]] = [...]``。"""
    lines = [f"{h}" for h in header]
    lines.append(f"{var_name}: list[tuple[float, float]] = [")
    lines.extend(f"    ({COORD_FMT.format(float(x))}, {COORD_FMT.format(float(y))})," for x, y in points)
    lines.append("]")
    return "\n".join(lines) + "\n"


def emit_layout(
    points: Sequence[Sequence[float]],
    *,
    route_len_m: float | None = None,
    var_name: str = LAYOUT_VAR_NAME,
    note: Iterable[str] = (),
    out: Path | str | None = None,
) -> str:
    """生成 ``--emit`` 文本：**带表头注释**的布局片段；``out`` 给定时同时落盘。

    表头刻意写明三件事，避免评阅者把这份片段当成"又一次采样结果"：
    1. 粘贴位置（``strategy_v17.py`` 的 ``LAYOUT_Q4_V17``）与 q4-v18 的继承关系；
    2. 访问顺序与路线长度（起点为原点，开放路径）；
    3. 应该用哪个脚本重新出**连续域**证书（本模块不做任何几何判定）。
    """
    head: list[str] = [
        "# ==== Q4 发现层布局（script/q4 离线工具链 --emit 产出）====",
        "# 用途：粘贴到 framework/src/mathmodel2026b/strategy_v17.py 的 LAYOUT_Q4_V17。",
        "#       mathmodel2026b.versioned 里的 q4-v18（最终交付）从 q4-v17 **继承**该布局，",
        "#       v18 只新增知识矩阵负例与覆盖式清除兜底，不改发现层，所以这份片段同时服务两版。",
        f"# 点数 m={len(points)}；顺序即访问顺序（起点 (0, 0) 的开放路径）"
        + (f"；离线路线 {route_len_m:.1f} m。" if route_len_m is not None else "。"),
        "# 证书：0.25° 采样扫描只是有限样本，**不是**全域证明；连续域请跑",
        "#       python3 script/verify_layout_certificate.py（四叉树充分条件，要求 unresolved=0）。",
    ]
    head.extend(f"# {line}" for line in note)
    return format_layout_fragment(points, var_name=var_name, header=head)


def layout_declared_in_framework() -> list[Point2]:
    """语义别名：交付真值就是框架里那份 ``LAYOUT_Q4_V17``。"""
    return framework_layout()


__all__ = [
    "COORD_FMT",
    "LAYOUT_VAR_NAME",
    "LOG_ROOT",
    "Point2",
    "REPO_ROOT",
    "SCRIPT_DIR",
    "compare_layouts",
    "emit_layout",
    "format_layout_fragment",
    "framework_layout",
    "layout_declared_in_framework",
    "load_layout_json",
    "save_layout_json",
    "v9_layout",
]
