# `mathModel2026B/docs/` 文档索引

日期：2026-09-13
适用代码：`mathModel2026B/framework/src/mathmodel2026b/`

本目录是 **B 题（问题 1–4）技术文档的唯一权威位置**。版本/路线类结论以
`framework/src/mathmodel2026b/versioned.py`（`ROUTE_ENTRIES` / `INTERMEDIATE_ENTRIES` /
`NEGATIVE_ENTRIES`）为准；本文档与注册表冲突时以注册表为准。

> **阅读顺序建议**：先看 `q34-version-lineage.md`（版本谱系与选型），
> 再看 `method-review-next-steps.md` + `review-response-2026-09-13.md`（复核与修正），
> 最后按需读各专题文档。
>
> ⚠️ 本目录同时存在**两代计时口径**：2026-09-13 之前生成的基准把失败的 `/clear`
> 按 5s 计（附件1 §2.3 的正确值是 **3s**，且 `/clear` 不切换测向机频道）。
> 差异为 `2 s × 每案例失败清除次数`，详见 `q34-version-lineage.md` §3.6。

---

## 一、题目与建模

| 文档 | 一句话说明 |
| --- | --- |
| `q1_probability_analysis.md` | 问题1：单点/多点检测的发现概率模型、蒙特卡洛口径与结论。 |
| `q2_second_point_strategy.md` | 问题2：第二检测点的几何与策略推导（含覆盖数下界、稳健区域分析）。 |
| `q2_knowledge_heatmap.md` | 问题2：单频道知识矩阵 → 下一观测点热图的模型口径（在线页面 `/matrix` 的说明）。 |
| `q2_unified_ui.md` | 问题2 统一编辑台（双点交会页与矩阵热图页合并的收纳页、辅助线与导入导出）。 |
| `knowledge-matrix.md` | 上游知识矩阵（channel×path）与在线覆盖证书、有效接收半径在线夹逼的口径与实测。 |
| `param-calibration.md` | 参数口径统一建议：`grid_s=950` vs 论文 `s=1000`、`rho=1150` vs live `1200` 的实测对比与结论。 |
| `p0-dispute-resolution.md` | P0（顺路清）复现争议澄清：归档缺基线臂与 import 断裂的原因、补齐的同种子 A/B。 |
| `provenance-confirmation.md` | `paper_q4/` 代码来源合规确认（同队协作产出的声明草稿）。 |

## 二、Q3（全向）

| 文档 | 一句话说明 |
| --- | --- |
| `q34-version-lineage.md` §2 | Q3 两条可选路线（`matrix` / `q3-v8`+`q3-v15`）与逐代数据、v15 的两个实测事实、注册表现状。 |
| `q3-floor-and-v10-v13-20260913.md` | Q3 参考解口径更正（183.7 是**上帝信息参考解**、不是下界）+ v10~v13 负结果记录。 |
| `q3-v15-v16-q4-v14-session-20260913.md` | 会话记录：Q3 v15 定稿、v16 明确否决、Q4 v14 收敛与 350/185 的可达性论证（口径已限定）。 |

## 三、Q4（全向 + 定向混合）

| 文档 | 一句话说明 |
| --- | --- |
| `q34-version-lineage.md` §3 | Q4 两条可选路线（`paper-q4` / `q4-v18`）、逐代数据、v17→v18 降级与交付版新增三层、计时口径更正。 |
| `q4-method2-joint-opt-20260913.md` | 方法二完整报告：局部凸包判据、证书工具链、联合优化机器、24 点布局、A/B 三臂表、负结果清单。 |
| `q4-decision-methods.md` | Q4 三条可运行决策路线的同框架对比（5 个真实画像代理案例的全清与虚拟时间）。 |
| `q4-joint-layout.md` | Q4 检测层：局部凸包证书与联合优化的方法二原型（`joint_layout.py`）与 SOCP 讨论。 |
| `q4-real-log-analysis.md` | 问题4 官方日志分析与本地"真实画像"回归（`日志4/*.jlog` 的可解性边界）。 |

## 四、版本谱系与决策方法选择

| 文档 | 一句话说明 |
| --- | --- |
| `q34-version-lineage.md` | **主文档**：Q3/Q4 版本谱系、路线声明、负结果清单、决策表与口径约定；§2.4 与 §3.5/§3.6 为 2026-09-13 新增。 |
| `q4-semicircle-hit-method-prompt.md` | Q4 半圆命中法（方法二联合优化）的**研究设想存档** + 复核给出的四处论证更正（紧性论证不成立、固定顺序不使原问题成为 SOCP、闭半圆边界统一为 ≥0、计时公式写错）。 |

> 代码侧的可执行版本：`python script/list_methods.py -v`（输出带"★ 最终交付 / ◆ 阶段有益 /
> ✗ 负结果（勿选）"标签），清单数据来自 `versioned.py` 注册表。

## 五、复核与评审

| 文档 | 一句话说明 |
| --- | --- |
| `method-review-next-steps.md` | 上一轮方法复核意见与后续方向（覆盖判据、半径上下界、布局与路由的改进空间）。 |
| `review-response-2026-09-13.md` | 对 `method-review-next-steps.md` 的逐条响应：确认 / 修正 / 保留意见与对应代码改动。 |
| `review-fixes-2026-09-13.md` | ⭐ **本轮修复的权威记录**（必读）：v17→v18 的三层新机制与充分性论证、两处实现 bug（Q3 or-opt 空转 / mock 计时）、口径更正、全部实测表（520 局持有集）与复现命令。`q3-v18`/`q4-v18` 为什么存在、为什么可信，唯一依据在这篇。 |
| `paper-q4-adoption.md` | 队友论文冻结内核（`paper_q4/`）的采纳说明、A/B 依据与不采纳/待议部分。 |

> 外部独立复核材料在工作区根的 `review_20260913/审阅结论.md`（不在本目录），
> 其复现日志（`mixed.log`、`all_directional*.log`、`q3*.log`）是 `q34-version-lineage.md`
> §3.5 表格数字的来源。

## 六、工程链路与运维

| 文档 | 一句话说明 |
| --- | --- |
| `robot-link-windows-vm.md` | Windows VM + 官方模拟器（`jammers-simulator.exe`）的对接链路、协议核对、逐场线上实测与运动学复算。 |
| `data/official-drill-20260913-1420-q18.json` | **2026-09-13 官方模拟器演练 2 局（`q3-v18` 14/14、`q4-v18` 15/15）的官方真值与成绩记录** —— 真值取自官方 `*.result.json`，非 mock。 |
| `data/online-p3-matrix-20260913-requestlog.tsv` | 2026-09-13 11:42 官方线上 1 场（`matrix`，问题3）的请求流水，供运动学/口径复算用。 |

---

## 附：文件名约定

- 带日期后缀（`-20260913` / `-2026-09-13`）的文件是**某次会话的记录**，
  正文按当时口径写成，可能包含此后被修正的结论——**每份此类文档顶部若有
  `⚠️ 口径更正` 段，以该段为准**。
- 不带日期的文件是**随仓库持续维护的口径文档**，随代码演进更新。
- 全部文档为 UTF-8 + LF。
