"""问题4 **发现层布局**的离线工具链（从 2026-09-13 的 Windows 最终工程快照移植）。

为什么要有这个包
----------------
``framework/src/mathmodel2026b/strategy_v17.py`` 里的 ``LAYOUT_Q4_V17``（24 个停点）
不是手写的，而是离线跑出来的：以 v9 两圈式 25 点为初始解，在"局部凸包完备性证书"
约束下交替做坐标微调 / 顺序搜索 / 删点-修复。产出它的脚本原本只存在于快照顶层
（``_joint_opt_q4.py``、``_q4_advance.py``、``_strong_tsp.py``、``_fix_layout.py`` 等），
与框架仓库脱节：既无法复跑，也无法在改布局后重新出证书。本包把这些工具收进
框架仓库，并把"布局 → 交付格式（可粘贴的 ``LAYOUT_Q4_V17`` 片段）→ 独立证书"
串成一条可复核的链路。

模块一览
--------
``layout_io``
    布局读写 + ``--emit`` 片段格式；**唯一**负责"布局 ⇄ ``LAYOUT_Q4_V17`` 文本"的模块。
``joint_opt``
    ← ``_joint_opt_q4.py``。证书（多分辨率网格 + 凸包交叉验证 + 0.25° 采样扫描）、
    割平面 soft-min 罚函数 SCP 坐标步、``repair_geom`` / ``polish_feas`` / ``knife_fix``、
    删点-修复主流程。
``advance``
    ← ``_q4_advance.py`` + ``_strong_tsp.py`` + ``_fix_layout.py`` + ``_joint_opt_layout.py``。
    强 TSP 提路线、23 点删点尝试、多网格 ``polish_feas`` 收尾、片段写出。
``diagnose``
    ← ``_diag_*.py`` / ``_q4_v14_stats.py`` / ``_q4_visit_check.py`` / ``_ab_q4_v17.py``。
    在线归因与消融诊断（子命令统一入口）。
``design_legacy``
    ← ``_design_q4_nodes.py`` / ``_design_q4_route.py`` / ``_design_q4_v2.py`` /
    ``_q4_layout_analysis.py`` / ``_q4_fail_diag.py``。**历史归档**：这些脚本产生的是
    v9 两圈式布局的早期论证，保留只为追溯数字来源，不再用于交付。

运行方式
--------
两行都可用（``script`` 目录没有 ``__init__.py``，靠命名空间包工作）::

    python3 -m script.q4 --help
    python3 -m script.q4 joint-opt --help
    python3 script/q4/joint_opt.py --help          # 单文件直跑也可以

``python3 -m script.q4 <子命令>`` 会把参数转交给对应模块的 ``main()``。
"""

from __future__ import annotations

__all__ = ["advance", "design_legacy", "diagnose", "joint_opt", "layout_io"]
