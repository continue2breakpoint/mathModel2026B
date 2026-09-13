#!/usr/bin/env python3
"""列出问题3 / 问题4 此后可选择的全部**决策方法**。

数据来源是 ``mathmodel2026b.versioned.DECISION_METHODS`` —— 与 ``script/run.py``
的 ``--strategy`` 选项、``_bench_versions.py`` 的臂用的是**同一份注册表**，
因此这里列出的名字一定可以直接用来跑：

    python script/list_methods.py                    # 路线总览 + 全部方法
    python script/list_methods.py --problem 4        # 只看问题4
    python script/list_methods.py --final-only       # 只看最终交付版
    python script/list_methods.py --routes           # 只打印"可选路线总览"
    python script/list_methods.py --json             # 机器可读（喂给别的脚本）
    python script/list_methods.py --commands         # 打印可直接复制的运行命令

**每个问题只有两条可行路线**（见 ``docs/q34-version-lineage.md``）：

* 问题3：``matrix``（知识矩阵） ／ ``q3-v8``、``q3-v15``（迭代优化版）
* 问题4：``paper-q4``（论文冻结内核，对照/兜底） ／ ``q4-v14``（迭代优化版）

其余条目要么是路线内部的历代版本（消融/回顾用），要么已过时
（原始 ``q3``：线上 500~680 s/源），要么在问题4 上不满足"确保全部清除"
（整条 matrix 路线、``q4-v8``、``q4-v9``）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
FRAMEWORK_SRC = REPO_ROOT / "framework" / "src"
sys.path.insert(0, str(FRAMEWORK_SRC))

from mathmodel2026b.versioned import (  # noqa: E402
    DECISION_METHODS,
    INTERMEDIATE_ENTRIES,
    available_methods,
    method_tag,
    usable_routes,
)

#: 状态标签统一由 ``versioned.method_tag`` 提供，避免两处口径漂移。
tag_of = method_tag


def command_for(key: str, spec) -> str:
    """给出**最常用**的那条命令。

    注意不能用 ``4 in spec.problem`` 判断：``matrix`` 的 problem 是 ``(3, 4)``，
    但它作为问题3 路线A 时的正确命令**不带** ``--directional``
    （带上的话就变成问题4 语义，而它在那不达标）。因此只看"该方法是否
    以问题4 交付为目标"：仅解问题4，或本身就是问题4 的路线入口。
    """
    q4_route = spec.problem == (4,) or (
        spec.route_entry and 4 in spec.problem and 3 not in spec.problem
    )
    if q4_route:
        return f"python script/run.py --strategy {key} --directional --seed 1"
    return f"python script/run.py --strategy {key} --seed 1"


def render_routes(keys: list[str]) -> str:
    """按"问题 → 两条路线 → 中间代 → 排除项"渲染决策总览。

    这是本文件最重要的输出：每个问题**只有两条路线**（路线A 独立解法、
    路线B 迭代优化版），其余都是路线内部的历代版本（消融/回归用），
    或已过时/不满足题目硬要求而被排除的条目。
    """
    lines = ["## 可选路线总览", ""]
    for problem in (3, 4):
        pkeys = set(k for k in keys if problem in DECISION_METHODS[k].problem)
        # 路线声明来自注册表（versioned.ROUTE_ENTRIES），本文件不再自带一份
        routes = [k for k in usable_routes(problem) if k in pkeys]
        derives = [k for k in INTERMEDIATE_ENTRIES[problem] if k in pkeys]
        excluded = [k for k in keys
                    if k in pkeys and k not in routes + derives]

        lines.append(f"问题{problem}：")
        by_route: dict[str, list[str]] = {"A": [], "B": []}
        for k in routes:
            by_route["A" if DECISION_METHODS[k].separate_route else "B"].append(k)
        # 说明：路线A = 独立解法，路线B = 迭代优化版
        for label, title in (("A", "独立解法路线"), ("B", "迭代优化版路线")):
            ks = by_route[label]
            if not ks:
                continue
            lines.append(f"  ✅ 路线{label}（{title}）")
            for k in ks:
                spec = DECISION_METHODS[k]
                lines.append(f"       {k:<9} {tag_of(spec)}")
                lines.append(f"                 {spec.effect}")
        if derives:
            lines.append("  ·  路线B 的中间代（消融/回顾用，非交付）：" + "、".join(derives))
        for k in excluded:
            spec = DECISION_METHODS[k]
            if problem == 4 and spec.fails_q4_requirement:
                why = "不满足『确保全部清除』，不是可选项"
            else:
                why = "已过时 / 仅作对照，非交付选项"
            lines.append(f"  ❌ {k:<9} {why}")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_text(keys: list[str], *, verbose: bool) -> str:
    lines: list[str] = []
    for key in keys:
        spec = DECISION_METHODS[key]
        probs = "/".join(str(p) for p in spec.problem)
        lines.append(f"{key:<9} 问题{probs}  {tag_of(spec)}")
        if spec.separate_route:
            lines.append("    定位     : 另一条独立路线（与迭代优化版并列，非历代版本）")
        lines.append(f"    新增机制 : {spec.adds}")
        lines.append(f"    实测效果 : {spec.effect}")
        if spec.outdated:
            lines.append("    ⚠️ 已过时 : 不要作为交付选项，仅用于回归/消融")
        if spec.fails_q4_requirement:
            lines.append("    ⚠️ 排除   : 在问题4 上不满足『确保全部清除』")
        if spec.overrides:
            lines.append(
                "    交付参数 : "
                + ", ".join(f"{k}={v!r}" for k, v in spec.overrides.items())
            )
        if verbose and spec.note:
            lines.append(f"    备注     : {spec.note}")
        if verbose:
            lines.append(f"    运行     : {command_for(key, spec)}")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_json(keys: list[str]) -> str:
    payload = [
        {
            "key": key,
            "problem": list(DECISION_METHODS[key].problem),
            "tag": tag_of(DECISION_METHODS[key]),
            "final": DECISION_METHODS[key].final,
            "beneficial_intermediate": DECISION_METHODS[key].beneficial_intermediate,
            "separate_route": DECISION_METHODS[key].separate_route,
            "outdated": DECISION_METHODS[key].outdated,
            "fails_q4_requirement": DECISION_METHODS[key].fails_q4_requirement,
            "adds": DECISION_METHODS[key].adds,
            "effect": DECISION_METHODS[key].effect,
            "overrides": DECISION_METHODS[key].overrides,
            "note": DECISION_METHODS[key].note,
            "strategy_ref": DECISION_METHODS[key].strategy_ref,
            "params_ref": DECISION_METHODS[key].params_ref,
            "command": command_for(key, DECISION_METHODS[key]),
        }
        for key in keys
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def main() -> int:
    ap = argparse.ArgumentParser(description="列出问题3/4 可选的决策方法")
    ap.add_argument("--problem", type=int, choices=[3, 4], default=None)
    ap.add_argument("--final-only", action="store_true", help="只列最终交付版")
    ap.add_argument("--beneficial-only", action="store_true", help="只列阶段有益版")
    ap.add_argument("--routes", action="store_true", help="只打印可选路线总览")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--commands", action="store_true", help="只输出可复制命令")
    ap.add_argument("-v", "--verbose", action="store_true", help="附带备注与运行命令")
    args = ap.parse_args()

    keys = available_methods(args.problem)
    if args.final_only:
        keys = [k for k in keys if DECISION_METHODS[k].final]
    if args.beneficial_only:
        keys = [k for k in keys if DECISION_METHODS[k].beneficial_intermediate]

    if args.routes:
        print(render_routes(keys))
        return 0
    if args.commands:
        for key in keys:
            print(command_for(key, DECISION_METHODS[key]))
        return 0
    if args.json:
        print(render_json(keys))
        return 0

    print(f"# 可选决策方法（共 {len(keys)} 个；来源 mathmodel2026b.versioned）\n")
    print(render_routes(keys))
    print("\n## 逐个方法明细\n")
    print(render_text(keys, verbose=args.verbose))
    finals = [k for k in keys if DECISION_METHODS[k].final]
    if finals:
        print("\n## 推荐命令")
        for key in finals:
            print(f"  {command_for(key, DECISION_METHODS[key])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
