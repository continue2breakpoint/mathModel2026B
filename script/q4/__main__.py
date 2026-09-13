"""``python3 -m script.q4 <子命令>`` 的统一入口（参数原样转交给各模块的 ``main()``）。

为什么要有它：包内四个模块各有自己的 ``main()``，但"离线工具链"作为一个整体
需要一个发现入口——评阅者/队友不该先猜哪个文件是干什么的。这里只做分发，
**不**复制任何逻辑；每个模块仍然可以单文件直跑（``python3 script/q4/joint_opt.py``）。

用法::

    python3 -m script.q4 list                 # 列出子命令与用途
    python3 -m script.q4 joint-opt --quick
    python3 -m script.q4 advance emit
    python3 -m script.q4 diagnose list
    python3 -m script.q4 design-legacy --help
    python3 -m script.q4 verify --json logs/q4/certificate.json

``emit`` 是 ``joint-opt --emit`` 的快捷方式：打印可直接粘贴进
``framework/src/mathmodel2026b/strategy_v17.py`` 的 ``LAYOUT_Q4_V17`` 片段。
``verify`` 转发到 :mod:`script.verify_layout_certificate`（布局证书验证器，
三项检查分开报告）。
"""

from __future__ import annotations

import sys

#: 子命令 → (模块, 一句话用途)
COMMANDS: dict[str, tuple[str, str]] = {
    "joint-opt": ("joint_opt", "Q4 发现层联合优化（证书 + SCP 坐标步 + 修复 + 删点主流程）"),
    "advance": ("advance", "在产出的布局上提路线 / 试删点 / 多网格微调 / 写交付片段"),
    "diagnose": ("diagnose", "在线与离线的诊断归因（子命令，见 diagnose list）"),
    "design-legacy": ("design_legacy", "**【历史归档】** v9 布局的早期设计与论证"),
    "verify": ("__verify__", "布局证书验证器（连续域四叉树 + 采样交叉验证 + 0.25° 扫描）"),
}


def _usage() -> str:
    lines = ["用法: python3 -m script.q4 <子命令> [子命令参数]", "", "子命令："]
    width = max(len(k) for k in (*COMMANDS, "emit", "list"))
    for key, (_mod, desc) in COMMANDS.items():
        lines.append(f"  {key:<{width}}  {desc}")
    lines.append(f"  {'emit':<{width}}  = joint-opt --emit：打印 LAYOUT_Q4_V17 交付片段")
    lines.append(f"  {'list':<{width}}  打印本表")
    lines.append("")
    lines.append("例：python3 -m script.q4 joint-opt --quick    /    python3 -m script.q4 verify --quick")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "list", "help"):
        print(_usage())
        return 0
    key, rest = argv[0], argv[1:]
    if key == "emit":
        key = "joint-opt"
        rest = ["--emit", *rest]
    if key not in COMMANDS:
        print(f"未知子命令 {key!r}\n", file=sys.stderr)
        print(_usage(), file=sys.stderr)
        return 2

    module_name, _desc = COMMANDS[key]
    if module_name == "__verify__":
        from pathlib import Path  # noqa: PLC0415

        script_dir = Path(__file__).resolve().parent.parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        import verify_layout_certificate  # noqa: PLC0415

        return int(verify_layout_certificate.main(rest))

    from importlib import import_module  # noqa: PLC0415

    module = import_module(f"{__package__}.{module_name}")
    return int(module.main(rest))


if __name__ == "__main__":
    raise SystemExit(main())
