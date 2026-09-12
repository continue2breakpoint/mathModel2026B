# `paper_q4/` —— 冻结的论文内核（勿就地修改）

本目录是**队友论文求解代码的逐字节副本**，不是我们自己的实现。适配层在
`mathmodel2026b/strategy_q4.py`，本目录只作为"可执行的黑盒内核"被调用。

## 来源

| 项 | 值 |
| --- | --- |
| 来源分支 | `origin/b-full-upload` |
| 来源提交 | `c3a38fd`（"B题: 全量上传题目材料、模拟器、论文与求解代码"） |
| 来源路径 | `B题/workbuddy/strategy_q4_port/paper_q4/`（与 `B题/` 根目录同名文件字节相同） |
| 引入分支 | `feat/adopt-paper-q4`（基线 `c865d10`） |

## 文件清单与 SHA256（`framework/tests/test_paper_q4_port.py` 会校验）

```
7d785556d5685232cea870cc4e76e3192390fb0af25ca1e35a043bd352803cf9  问题1_求解.py
dafbfff8782a008b2db5ca9e9550b95984753b1ea709b2b047edd433f9bfaaad  coverage.py
f8e50e8309a0a66b96577758b9539ca8fe65217231cba69bb18c3fe6c30d4a2d  q3_strategy.py
310fad6569a69afaf7b038361cc42ccd04b134103459d974effb80004f9bb1c4  q4_identify.py
d981736c421d54595a664fe12d1769d3b4e5b5b23dcab2e90b4c36eb04035e5a  q4_strategy.py
638bc2f0332fec795e5974261fc05adec89b2372b98e8aa2f4340b168314e1f5  sim_env.py
```

## 为什么冻结

这些文件的行为**已在官方模拟器上经过 5 场 live 演练（65 源含 43 定向，100% 一次清除）**。
任何就地修改都会让"演练验证过的行为"与"仓库里的行为"脱钩，证书失效。
需要改逻辑时请**另开新文件或改适配层**，并在 `docs/paper-q4-adoption.md` 记录动机与实测。

## 各文件职责

| 文件 | 作用 |
| --- | --- |
| `q4_strategy.py` | **入口**：`run_q4(env, grid_s=950.0)`。发现层（轴向三角网格 31 点 + Warnsdorff 哈密顿路径）→ 定位层（正面点楔形交会 + NBV）→ 辨识层（双假设 + 盘内探针）→ 清除层（MEC≤20m、朝向精测） |
| `q4_identify.py` | `classify()` 确定性双假设类型辨识（全向/定向/ambiguous）+ `_arc_feasible()` 圆周半宽弧求交得朝向可行弧 + `probe_points()` 盘内 4 探针。**docstring 明确写"不引入概率模型"** |
| `coverage.py` | `hamiltonian_route_axial()` 轴向三角网格 + Warnsdorff DFS 哈密顿路径；`coverage_rho_interval()` 七点环半径解析可行区间 `[1122.9558, 1732.0508]`；`directional_discovery_check()` 定向发现完备性数值检验 |
| `q3_strategy.py` | 被 `q4_strategy` 复用的库（`robust_center` 射线交点中位数、`select_nbv`、`fallback_rect_sweep` 304 点保证兜底、`ChannelState`），同时内含问题3 的 `run_q3()` |
| `问题1_求解.py` | 问题1 几何算子：`localization_polygon()` 半平面交、`welzl_min_enclosing_circle()`、`polygon_diameter_calipers()` 旋转卡壳等 |
| `sim_env.py` | 内核自带的离线仿真器（常数表 + `gen_sources`）。**接入框架后不会被调用**，仅作为内核常量来源（`CLEAR_R`、`R_MIN`）与其自测脚本的依赖 |

## 导入机制（重要）

`strategy_q4.py` 在模块导入时把本目录插入 `sys.path`，内核用**绝对导入**互相引用
（`import q4_strategy`、`from 问题1_求解 import ...`）。因此：

* 本目录**不是** Python 包（没有 `__init__.py`，也不要加）；
* `问题1_求解.py` 是**中文模块名**，Python 3 支持，但要求文件系统用 UTF-8/UTF-16
  （Linux/macOS/Windows 均满足）。改名会破坏逐字节一致性，故保留；
* 内核里的 `import coverage` 会命中本目录的 `coverage.py` 而**不是**框架的
  `mathmodel2026b/coverage.py`——框架侧一律用相对导入（`from .coverage import ...`），
  两边不会互相污染。

## 已知需要留意的地方（不在本分支修）

1. **内核与线上参数不一致**：`run_q4` 默认 `grid_s=950`，而 `coverage.py` 与论文正文
   示例用 `s=1000`（31 点 / 30000 m）；`RECOMMENDED_RHO=1150` 而 live 日志里出现 `1200`。
2. **本目录的 `q4_strategy.py` 不含 P0（发现途中顺路清）**。P0 只存在于来源分支的
   `B题/workbuddy/ablation_v1/q4_strategy_with_P0.py`（与本文件差 10 行），且归档产物
   复现不出报告里宣称的数字。本分支刻意采用**论文正本**（无 P0）。
3. 内核的半径用法（`R_lo = max|p−G|` 作 R 的安全下界）方向正确，与
   `docs/method-review-next-steps.md` §2.1 的结论一致；但我们对框架自身 `coverage.py`
   里半径界的批评**不适用于**本内核，两者是不同的实现。

## 合规提示

本目录代码的作者是**本队队友**（其论文求解代码）。引入前请确认一份归属说明：
来源上传里 `B题/_gitee_study/外部仓库学习与交叉验证笔记.md` 声明"他人未发表竞赛代码，
不得复制其代码/文字进论文或支撑材料"，而该笔记指向的 `continue2breakpoint/math-model2026-b`
正是我们自己的仓库，存在归属表述矛盾。若确认是同队协作代码，本目录可正常入库；
若确属他人代码，则**只可用于内部对比实验，不得进入论文或支撑材料**。
