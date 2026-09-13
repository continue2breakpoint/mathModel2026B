# Q4 决策方法整理与全清/虚拟时间测试

日期：2026-09-13  
测试环境：本地 `MockSimulator`，5 个历史官方 p4 真实画像生成的同源数/同定向源数代理案例。  
测试脚本：`_bench_q4_real.py`  
原始结果：`logs/q4_methods_compare/`

---

## 0. 结论先行

当前仓库里 Q4 实际有三条可运行的决策路线：

| 路线 | 代码入口 | 核心思想 | 全清优先？ |
|---|---|---|---|
| paper 冻结内核 | `strategy_q4.py` + `paper_q4/` | 轴向发现 + 双假设辨识 + 正面 NBV + 矩形兜底 | 是，最稳 |
| matrix Q4 | `strategy_matrix.py` | 知识矩阵 + 置信集 + 朝向覆盖证书 + 联合可行性剪枝 | 部分依赖 `scan_layout=axial` |
| joint layout Q4 | `joint_layout.py` + 自定义 `_scan_route` | 局部凸包证书约束下联合优化点集与路径 | 是，但定位阶段仍复用 matrix |

本次 5 案例代理测试的汇总：

| 方法 | 全清 | 虚拟时间中位数/s | 路程中位数/m | 检测次数中位数 | 清失败中位数 | 墙钟中位数/s |
|---|---:|---:|---:|---:|---:|---:|
| `paper` | **5/5** | 20262 | 76123 | 877 | 0 | 0.8 |
| `matrix` + `polygon` | 1/5 | 7447 | 30696 | 215 | 0 | 4.3 |
| `matrix` + `axial` | 2/5 | 12213 | 50692 | 379 | 3 | 7.8 |
| `joint layout` + matrix | 3/5 | **10220** | **36187** | **360** | 1 | 7.5 |

注意：`joint layout` 只优化发现层的检测点和访问顺序；定位/清除仍由 matrix Q4 完成，
所以高定向比例下的退化交会几何仍可能漏清。它在全清率、虚拟时间、路程上均优于
`matrix + axial`，但尚未达到 `paper` 的 5/5。

---

## 1. paper 冻结内核

代码：`framework/src/mathmodel2026b/paper_q4/`

### 1.1 发现层

- 使用等边三角网格 `axial_grid_nodes(grid_s=950)`；
- 31 个停点基本覆盖目标圆域；
- 对任意候选源位置，三个近邻格点形成包含该位置的三角形；
- 由 `heading_cover_condition` 的同一原理保证：任意朝向都有至少一个格点落在源的前半平面。

决策依据：**确定性轴向网格 + 局部凸包朝向覆盖**。

### 1.2 定位层

- 一次 `direction` 读数：以检测点为顶点、±1° 的楔形；
- 多个楔形求交，得到包含真值的定位多边形；
- 用最小包围圆 MEC 衡量定位精度；
- 选择 `front_nbv`：在朝向弧的正面一侧枚举补点，要求新读数能改善交会几何。

决策依据：**楔形交会 + NBV 枚举 + MEC 收敛**。

### 1.3 辨识层

- 双假设：全向 / 定向；
- 若盘内出现确定背面点，则排除全向；
- 朝向可行弧由所有正面点和背面点约束求交；
- 盘内 4 个均布探针用于主动区分全向/定向；
- 用 36 点圆周探针精测朝向边界。

决策依据：**确定性双假设 + 圆弧区间求交 + 主动探针**。

### 1.4 清除/兜底

- MEC ≤20m 直接 `/clear`；
- 清不掉时使用 `fallback_rect_sweep`：
  以最后一条有效示向度射线为轴，在 `[0,1500]×[-30,30]` 矩形内按 20m 间距蛇形清除；
- 证明：任意源到某个矩形网格点的距离 <20m，且清除与朝向无关，因此有限步必清。

---

## 2. matrix Q4

代码：`framework/src/mathmodel2026b/strategy_matrix.py`

### 2.1 知识矩阵

- 行：频道 1..20；
- 列：检测点；
- 单元格：`direction / no_signal / near / cleared / clear_failed`；
- 仅作为原始证据台账，不直接当作“源是否在某处”的结论。

### 2.2 置信集

每个未清除频道维护：

- `region`：由正向示向度楔形求交得到的凸可行域；
- `radius_lo_m` / `radius_hi_m`：有效接收半径安全上下界；
- `plan_radius_m`：规划使用的安全下界；
- `near`、`cleared` 等状态。

### 2.3 定向联合可行性

`no_signal` 在定向场景下有两种解释：

\[
\|s-x\|>R
\quad\text{或}\quad
(s-x)\cdot u(\phi)<0.
\]

因此 `directional=True` 时关闭“负例圆盘排除”，改用
`coverage.plausible_directional_mask()`：

- 同一朝向 \(u\) 和同一半径 \(R\) 必须解释全部观测；
- 距离约束、正例半平面约束、负例背面约束联合求交；
- 只删除**全向和定向都无法解释**的位置。

决策依据：**隐变量集合可行性剪枝**，不是贝叶斯后验。

### 2.4 发现完备性证书

对每个候选位置 \(x\)，设

\[
S_x=P\cap\overline B(x,1000).
\]

充分条件：

\[
x\in\operatorname{conv}(S_x).
\]

matrix Q4 用：

- `heading_cover_condition` 做几何证明；
- `heading_cover_layout` 做三角网格构造；
- `CoverageTracker` 把覆盖量从“圆盘覆盖”升级为

\[
\text{disk}(s,\text{plan\_radius})\cap\text{heading\_covered\_cells}.
\]

当某频道 `region_mask \ covered_mask = ∅` 时，认为该频道不可能再藏着未发现的源。

### 2.5 定位/清除决策

- 发现后：可行域楔形求交；
- 交会角 <20°：先做 `_transverse_probe()`，在垂直方向取新点；
- 逼近：`_approach_target` 弦模式 / 最近点模式；
- MEC ≤20m：直接清除；
- 逼近中 `no_signal`：不再继续深入，改做横向补测；
- 收尾清不掉：`_revisit_last_bearing()` 回到最后一条有效示向度附近重测；
- 证书不完全：`_gap_fill` 贪心集合覆盖，再回退六边形重扫。

决策依据：**确定性几何集合 + 覆盖证书 + 启发式调度**。

---

## 3. joint layout Q4（方法二原型）

代码：`framework/src/mathmodel2026b/joint_layout.py`

### 3.1 硬约束

完整局部凸包证书：

\[
\forall x\in\Omega,\quad
x\in\operatorname{conv}(P\cap\overline B(x,1000)).
\]

连续域通过“正方形单元 + 充分条件”证明：

- 对单元 \(C\)，取 \(S_C=\{p_i:\max_{v\in C}\|p_i-v\|\le1000-\eta\}\)；
- 若顶点都在 `conv(S_C)`，则整个单元被证书覆盖；
- 不通过就递归细分；
- `ok=True` 是严格证明，`ok=False` 只表示证书无法证明。

### 3.2 联合优化

- 变量：检测点坐标 \(q_i\)、访问顺序 \(\pi\)；
- 初始解：method-1 轴向网格 + Hamiltonian 开放链；
- 操作：
  - 删除冗余点；
  - 点坐标朝质心/路径邻居/随机方向微调；
  - 固定点集时 2-opt 调整顺序；
- 每个候选移动在严格模式下都复核连续证书；
- 目标：

\[
\min \frac{L}{5}+c_{\text{measure}}m.
\]

### 3.3 SOCP 扩展

`joint_layout.socp_refine()` 提供固定加权组合的 SOCP 精调接口，依赖 `cvxpy`，
当前环境未安装，因此本次测试使用坐标下降版本。

### 3.4 本次离线布局结果

| 项 | 值 |
|---|---:|
| 初始路径 | 28500.0m |
| 联合微调后路径 | 26997.2m |
| 下降 | 5.3% |
| 连续证书 | 通过 |
| 布局文件 | `data/q4_joint_layout.json` |

---

## 4. 代理案例测试明细

真实画像来自历史官方数据：

| case_code | n | 定向 | 定向比例 | official 全清 | official vt/s |
|---|---:|---:|---:|---:|---:|
| KAFK-P4MP-YF32-WHQM | 10 | 10 | 1.00 | 10/10 | 17863.7 |
| VZ52-TJUU-FABK-PSCW | 16 | 8 | 0.50 | 16/16 | 18342.8 |
| 9W7G-WEAE-9PPW-JWXC | 11 | 9 | 0.82 | 11/11 | 19540.6 |
| 9T86-MU58-WMKX-E42B | 13 | 2 | 0.15 | 13/13 | 13671.0 |
| BM8Z-7J83-3CGE-88WV | 15 | 14 | 0.93 | 15/15 | 25661.4 |

本地代理真值由 `case_code` 稳定哈希生成，因此只能做相对比较，不能替代官方线上复测。

复现命令：

```bash
cd mathModel2026B

# paper
python _bench_q4_real.py --discover --strategy paper --out logs/q4_methods_compare/paper

# matrix + polygon
python _bench_q4_real.py --discover --strategy matrix --param scan_layout=polygon \
  --out logs/q4_methods_compare/matrix_polygon

# matrix + axial
python _bench_q4_real.py --discover --strategy matrix --param scan_layout=axial \
  --out logs/q4_methods_compare/matrix_axial

# joint layout
python _bench_q4_real.py --discover --strategy joint \
  --out logs/q4_methods_compare/joint
```

---

## 5. 当前推荐

1. **正式硬约束优先**：paper 冻结内核 5/5 全清，是当前唯一在 5 个真实画像代理上都全清的路线。
2. **matrix 只适合作为快速路径**：`axial` 比 `polygon` 可靠，但高定向比例下仍会因退化交会几何漏清。
3. **joint layout 已有工程价值**：在不降低完备性证书的前提下，把路径从 28500m 降到 26997m，5 案例代理上全清 3/5、虚拟时间中位数 10220s，优于 matrix axial 的 2/5 和 12213s。
4. **下一步**：把 joint layout 的离线布局接到 `MatrixParams.scan_layout="joint"`，再针对高定向退化几何改造横向补测/兜底；在未跑通更多真实画像前，不建议让 matrix/joint 取代 paper 兜底。
