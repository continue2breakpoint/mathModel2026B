#!/usr/bin/env python3
"""列出问题3 / 问题4 此后可选择的全部**决策方法**。

数据来源是 ``mathmodel2026b.versioned.DECISION_METHODS`` —— 与 ``script/run.py``
的 ``--strategy`` 选项、``_bench_versions.py`` 的臂用的是**同一份注册表**，
因此这里列出的名字一定可以直接用来跑：

    python script/list_methods.py                    # 全部方法（人读）
    python script/list_methods.py --problem 4        # 只看问题4
    python script/list_methods.py --final-only       # 只看最终交付版
    python script/list_methods.py --json             # 机器可读（喂给别的脚本）
    python script/list_methods.py --commands         # 打印可直接复制的运行命令

相关文档：``docs/q34-version-lineage.md``（版本谱系与负结果清单）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(FRAMEWORK_SRC := REPO_ROOT / "framework" / "src"))

from mathmodel2026b.versioned import (  # noqa: E402
    DECISION_METHODS,
    available_methods,
)

TAG_FINAL = "★ 最终交付"
TAG_BENEFICIAL = "◆ 阶段有益"
TAG_CONTROL = "· 对照/反例"


def tag_of(spec) -> str:
    if spec.final:
        return TAG_FINAL
    if spec.beneficial_intermediate:
        return TAG_BENEFICIAL
    return TAG_CONTROL


def command_for(key: str, spec) -> str:
    if spec.problem == 4:
        return f"python script/run.py --strategy {key} --directional --seed 1"
    return f"python script/run.py --strategy {key} --seed 1"


def render_text(keys: list[str], *, verbose: bool) -> str:
    lines: list[str] = []
    for key in keys:
        spec = DECISION_METHODS[key]
        lines.append(f"{key:<9} 问题{spec.problem}  {tag_of(spec)}")
        lines.append(f"    新增机制 : {spec.adds}")
        lines.append(f"    实测效果 : {spec.effect}")
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
            "problem": DECISION_METHODS[key].problem,
            "tag": tag_of(DECISION_METHODS[key]),
            "final": DECISION_METHODS[key].final,
            "beneficial_intermediate": DECISION_METHODS[key].beneficial_intermediate,
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
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--commands", action="store_true", help="只输出可复制命令")
    ap.add_argument("-v", "--verbose", action="store_true", help="附带备注与运行命令")
    args = ap.parse_args()

    keys = available_methods(args.problem)
    if args.final_only:
        keys = [k for k in keys if DECISION_METHODS[k].final]
    if args.beneficial_only:
        keys = [k for k in keys if DECISION_METHODS[k].beneficial_intermediate]

    if args.commands:
        for key in keys:
            print(command_for(key, DECISION_METHODS[key]))
        return 0
    if args.json:
        print(render_json(keys))
        return 0

    print(f"# 可选决策方法（共 {len(keys)} 个；来源 mathmodel2026b.versioned）\n")
    print(render_text(keys, verbose=args.verbose))
    final = [k for k in keys if DECISION_METHODS[k].final]
    if final:
        print("\n推荐组合：")
        for key in final:
            print(f"  {command_for(key, DECISION_METHODS[key])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
