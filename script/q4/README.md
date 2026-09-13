# Q4 发现层布局离线工具链（`script/q4/`）+ Q3 参考解工具（`script/q3_floor.py`）

本目录是 2026-09-13 Windows 最终工程快照（`问题34_最终工程文件_2026-09-13/`）顶层那批
`_joint_opt_q4.py` / `_q4_advance.py` / `_diag_*.py` / `_design_q4_*.py` 离线脚本的**框架内端口**。
快照里这些脚本与框架仓库脱节：无法复跑、无法在改布局后重新出证书、也说不清哪一份是最终产物。
端口把它们收进 `mathModel2026B/script/`，并把整条链路（**跑布局 → 提路线 → 出交付片段 → 出证书**）
固定成可复跑的命令。

**本目录不修改任何框架文件**：`--emit` 只把片段打印/写到指定路径，粘贴动作由人来做。

---

## 一、交付链路（谁产出什么）

```
framework/src/mathmodel2026b/strategy_v9.py
        two_tier_nodes(950,1750,12,1900)   ← 25 点两圈式（方法一：初始解 + 兜底）
                    │
                    ▼
script/q4/joint_opt.py            联合优化（证书约束下 SCP 坐标步 + 顺序搜索 + 删点-修复）
                    │                    产物：logs/q4/joint_opt/layout.json + layout_fragment.py
                    ▼
script/q4/advance.py              强 TSP 提路线（多起点 NN + 双桥 ILS）/ 试删到 23 点 / 多网格微调
                    │                    产物：logs/q4/advance/layout.json + layout_fragment.py
                    ▼
        --emit 片段（坐标 %.1f，顺序即访问顺序）
                    │                    人工粘贴
                    ▼
framework/src/mathmodel2026b/strategy_v17.py :: LAYOUT_Q4_V17（24 点，路线 18507.5 m）
                    │
                    ├── q4-v17（冻结对照臂）
                    └── q4-v18（**最终交付**，`mathmodel2026b.versioned`）**继承**该布局：
                        只新增知识矩阵负例（失败清除 20 m 排除圆）+ 可证明的覆盖式清除
                        （25 m 格铺满保守可行域），**不改发现层**。
                    │
                    ▼
script/verify_layout_certificate.py    独立证书验证（三项检查分开报告）
                    │                    产物：--json <path>
                    ▼
连续域四叉树充分条件：v17 检查 3107 单元 / 通过 1992 / 未决 0；v9 1537 / 1084 / 0
```

## 二、工具一览

| 文件 | 做什么 | 典型耗时（本机实测） | 输出 |
|---|---|---|---|
| `script/q4/joint_opt.py` | Q4 发现层联合优化：证书（多分辨率网格 + 凸包交叉验证 + 采样扫描）、割平面 soft-min 罚函数 **SCP 坐标步**、`repair_geom` / `polish_feas` / `knife_fix` 修复、**删点-修复主流程**；另有 `--emit` 片段输出 | `--quick` **0.5 s**；`--base json --max-iter 1 --max-candidates 1 --no-brute --continuous-cert` **18 s**；全量 `--max-iter 10 --time-budget 2400`：快照口径 ≈18 min（**未**全量复跑） | `logs/q4/joint_opt/{layout.json,layout_fragment.py,joint_opt.txt}` |
| `script/q4/advance.py` | 布局到手之后的推进：`strong-tsp`（多起点 NN + 双桥 ILS，只改顺序、零证书风险）、`advance-23`（试删到 23 点）、`fix`（多网格 `polish_feas` 补洞）、`emit`（写片段）、`all` | `--quick`：strong-tsp **3.5 s**、advance-23 **7.6 s**、fix **2.2 s**、all **10.9 s**；全量 `advance-23` 含 `brute_worst(1440)` 与逐候选 `knife_fix`，分钟级 | `logs/q4/advance/…` |
| `script/q4/diagnose.py` | 在线/离线诊断归因（`list` 看全部子命令与逐项耗时）：清除失败、探针、v17 漏清种子轨迹、v14/v17 统计与 A/B、删点/微调/knife 修复诊断 | `--quick` 全部子命令合计约 70 s（最慢 `knife` 27 s，其余 ≤9 s；`list` 自带耗时表） | `logs/q4/diagnose/…` |
| `script/q4/design_legacy.py` | **历史归档**（`_design_q4_*.py` / `_q4_layout_analysis.py` / `_q4_fail_diag.py`）：v9 两圈式布局当年的设计与论证，只为追溯数字来源，**不是**交付路径 | `--quick`：nodes **0.33 s**、route **0.30 s**、v2 **0.33 s**、analysis **0.50 s**、fail-diag **1.37 s**（全量 analysis 4.4 s） | `logs/q4/design_legacy/…` |
| `script/q4/layout_io.py` | 布局 ⇄ JSON ⇄ `LAYOUT_Q4_V17` 片段（唯一一处格式化实现；`--emit` 用） | 瞬时 | — |
| `script/verify_layout_certificate.py` | **独立证书验证器**：① 连续域四叉树充分条件（主结果，要求 `unresolved == 0`）② 快照多分辨率网格 + Delaunay 凸包交叉验证 ③ 0.25° 采样扫描。三项分开报告 | 全量 **8.5 s**；`--quick` **0.6 s** | `--json <path>` |
| `script/q3_floor.py` | Q3 **上帝信息参考解**家族（`--variant god/fixed/d/f/base/route-gap/v8-stats`）+ 成本差距解剖 | `--quick` 秒级（god **1.4 s**、f **7.7 s**）；全量 30 种子：god **122 s** | `logs/q3_floor/<variant>.txt`、`--json` |

包内统一入口（等价于上面各文件的 `main()`；`script/` 无 `__init__.py`，靠命名空间包）：

```bash
cd mathModel2026B
python3 -m script.q4 list
python3 -m script.q4 joint-opt --quick
python3 -m script.q4 emit | head           # = joint-opt --emit，打印交付片段
python3 -m script.q4 verify --json logs/q4/certificate.json
python3 script/q4/joint_opt.py --help      # 单文件直跑同样可用
```

## 三、两个布局的证书结论

`python3 script/verify_layout_certificate.py --json logs/q4/certificate.json`（全量，8.5 s）：

| 布局 | 点数 | ① 检查单元 | ① 通过单元 | ① 未决 | ② 网格 worst | ② 凸包 2000 采样 | ③ 0.25° 扫描 19200 样本 |
|---|---:|---:|---:|---:|---:|---:|---:|
| v17 `LAYOUT_Q4_V17`（q4-v17 / q4-v18 继承） | 24 | **3107** | **1992** | **0** | 983.3 m（阈值 988） | 2000/2000 | 989.8 m |
| v9 `two_tier_nodes` | 25 | **1537** | **1084** | **0** | 962.5 m | 2000/2000 | 962.5 m |

① 的三组数字与 2026-09-13 独立复核 `review_20260913/geometry.json`（3107/1992/0 与 1537/1084/0）
逐个一致；③ 的 989.8 m / 962.5 m 与快照 `results/_brute_check.txt` 一致。
v17 的点集与顺序也与快照 `results/_joint_opt_q4.json` 逐点一致（同点集、同访问顺序）。

**证书能证伪**（不然它没有意义）：把 v17 的第 13 个点删掉、把某个点挪到 (1500,−1500)、
或只留 6 个点，检查 ① 立即报 `unresolved ≥ 100`（`--max-unresolved` 截断）并给出未决单元位置。
把 `--min-side` 从 0.02 m 收到 0.005 m，结果不变（3107/1992/0）：本布局没有任何单元是靠
"细分到下限就放过"蒙混过去的（最大细分深度 13 ⇒ 最小单元 1800/2¹³ ≈ 0.22 m ≫ 0.02 m）。

### 口径警告（引用复核 §2）

* **① 是数值证书，不是形式化证明**：「这是连续区域的数值证书，不是新的随机采样；
  使用浮点凸包及向内余量，没有做区间算术形式化验证。它证明的是半径 1000 m 下的覆盖，
  不宣称精确最坏值就是 989.8 m。」实现的向内余量为 `--margin`（默认 1e-7 m），
  近邻点判据用 `|q−v| ≤ 1000 − margin`、凸包包含用有符号距离 `≤ −margin`。
* **③ 是有限样本，本身不构成全域证明**：「989.8 m 是采样最大值，不能直接称作连续域
  最坏真值；0.25° 也不能自动消除盲区。」采样只能**发现**洞（证伪），不能证明没有洞。
* 证书对应题面**闭半圆盘**（`n·(q−x) ≥ 0`）的等价判据；框架检测实现用严格 `proj > 0`
  （更强）。边界朝向仍需物理裕量，证书不对该裕量负责（复核 §6.4）。
* 采样类检查的采样域必须在**目标圆（半径 1800 m）内**。快照 `_verify_layout.py` 的
  "随机暴力扫描"在半径 2000 m 的盘内取点，其 `worst=inf` 落在 1964.1 m 处（Ω 之外），
  不是布局缺陷。

## 四、本端口自带的一致性证据（都是本机跑出来的）

| 断言 | 证据 |
|---|---|
| `--emit` 的片段与框架声明**逐字节**可粘贴 | `python3 script/q4/advance.py emit --no-write` 抓出 26 行片段体，与 `strategy_v17.py:29-53` 的声明 `diff` 为空，两边 sha256 都是 `1587114b…b570` |
| 框架里的交付布局 = 快照最终布局 | 与快照 `results/_joint_opt_q4.json` 逐点、逐顺序一致（同点集同访问顺序，`m=24`，文件序路线 18507.5 m） |
| 证书①的三组数字 = 独立复核 `geometry.json` | v17 3107/1992/0、v9 1537/1084/0，逐项相同 |
| 证书①不是"永远通过"的摆设 | 删掉 v17 一个点 / 把点挪到 (1500,−1500) / 只留 6 点 ⇒ `unresolved ≥ 100` 并给出未决单元坐标；`--min-side 0.005` 结果不变（无单元靠细分下限蒙混） |
| Q3 god 变体复现归档数字 | `python3 script/q3_floor.py --variant god --seeds 1-30`（122 s）：纯源 TSP 中位 8858 m、联合 TSP 中位 9562 m、贪心近似探针数中位 8.5、**上帝信息参考解中位 190.8 s/源**（146.2–245.5）——与快照 `results/_floor_q3_g.txt` 一致；快照报告里的 ≈183.7 只是把贪心的 8.5 换成 7 |
| 其余 Q3 变体同样复现归档 | `fixed/d/f/base` 各 30/30 行与快照表一致；`v8-stats --legacy-clear-timing` 与归档 30/30 一致（含页脚） |
| 不破坏既有测试 | `cd mathModel2026B && PYTHONPATH=framework/src python3 -m pytest -q` ⇒ 退出码 0，226 个测试全绿（无 F/E；本端口新增模块不参与收集，文件名不匹配 `test_*.py`）。注意仓库根的 `pytest.ini` 里 `addopts = -q` 叠加命令行的 `-q` 会变成 `-qq`，因此**不再打印汇总行**，只有进度点 |

## 五、相对快照的**措辞与行为**改动（逐条）

| # | 快照的说法 / 行为 | 本仓库改成 | 为什么 |
|---|---|---|---|
| 1 | `README`/`docs` 把 `_floor_q3_g.py` 的 ≈183.7 s/源 称作「**上帝下界**」，并断言 185 s/源 不可达 | 一律改称「**上帝信息参考解**」；JSON 键 `god_information_reference_s_per_source`、`heuristic_upper_bound_on_god_optimum=true`、`certified_lower_bound=false`；每次运行打印快照说法不成立的原因 | 该脚本用 NN / 2-opt / or-opt（TSP）与随机局部搜索（自由覆盖点）解上帝信息问题，得到的是**可行解**：可行解成本 ≥ 上帝信息最优值 ⇒ 只是最优值的**上界**，对在线问题不构成下界。真正的下界必须来自求解到可证明最优的松弛问题（复核 §7） |
| 2 | 同族 `fixed` / `d` / `f` / `base` 也被当作「地板 / 下界」 | 全部改称「上帝信息参考解」，并注明同为启发式可行解 | 同上 |
| 3 | `god` 变体的 `greedy_probe_count` 被当成"absent 频道的**最少**探针数"（快照取 7 得 ≈183.7，脚本实测 190.8） | 改称「absent 频道**贪心近似**探针数」，不再用"最少/最小"字样 | 贪心集合覆盖只是近似，且会**高估**最小值；快照 190.8 与 183.7 的差异正是把贪心中位 8.5 换成 7 造成的 |
| 4 | `brute_worst()` 被写成「**近精确判据**」、989.8 m 被写成最坏真值 | 改称「有限采样扫描」；函数 docstring 与 `_multi_cert_ok` 明确写"采样通过 ≠ 连续域完备"，并指向 `script/verify_layout_certificate.py` | 0.25° 朝向 + 有限样本不能排除盲区（复核 §2） |
| 5 | 23 点删点失败被表述为"23 点不可行" | 只报"这些候选/这条修复路径失败"，并打印"不证明所有 23 点布局不可行" | 复核 §7：删点修复失败 ≠ 该点数的所有布局不可行 |
| 6 | 坐标搜索变长被表述为"25 点已局部最优" | 只报该次搜索未找到更短解 | 复核 §7：不证明局部最优 |
| 7 | `strategy_v17.py` 顶部注释说坐标步用 **SLSQP 硬约束** | 工具文档据实写「soft-min 罚函数 + L-BFGS-B（SLSQP 是历史文字）」 | 复核 §6.4：提示词/注释与实现不符 |
| 8 | 快照脚本把 `_*.txt` / `_*.json` 写在脚本旁边（Windows 目录） | 一律写到 `logs/q4/**`、`logs/q3_floor/**`（`.gitignore` 的 `/logs/*`） | 运行产物不进源码树；且不写进只读的快照目录 |
| 9 | 快照的布局落点有 4 份（json / 片段 / 顺序 / 框架文件），各自格式化 | 只保留 `layout_io` 一处格式化；`--emit` 输出与框架声明**逐字节**一致（已验证 diff 为空） | 防"JSON 里的点"和"粘进框架的点"不一致（快照 23 点失败分支的回写就与框架里真正的 24 点不同源） |
| 10 | 无 `--quick` / `--max-iter` 之类的廉价模式，14 个脚本只能整体跑 | 每个工具都有 `--quick`（另加 `--max-iter` / `--max-candidates` / `--time-budget` / `--limit` / `--seeds`），可秒级冒烟 | 让"改一行就能验证"成为可能 |
| 11 | mock 的 `/clear` 计时对失败也记 5 s | 工具里显式给 `--legacy-timing`（复现旧数字）并打印所用口径；默认口径以框架修正后的 `World`（失败 3 s / 成功 5 s，附件1 §2.3）为准 | 复核 §4：失败清除应为 3 s。归档数字必须标口径才能比 |

## 六、复现命令

```bash
cd mathModel2026B

# 证书（推荐入口，秒级）：两项布局、三项检查
python3 script/verify_layout_certificate.py --json logs/q4/certificate.json
python3 script/verify_layout_certificate.py --layout v17 --min-side 0.02

# 交付片段：打印/落盘，然后人工粘贴进 strategy_v17.py
python3 script/q4/joint_opt.py --emit
python3 script/q4/advance.py emit --no-write

# 冒烟（不做长时间优化）
python3 script/q4/joint_opt.py --quick
python3 script/q4/joint_opt.py --base json --in logs/q4/joint_opt/layout.json \
        --max-iter 1 --max-candidates 1 --no-brute --continuous-cert
python3 script/q4/advance.py strong-tsp --quick
python3 script/q4/advance.py advance-23 --quick
python3 script/q4/diagnose.py list
python3 script/q4/diagnose.py clears --quick
python3 script/q4/design_legacy.py v2 --quick
python3 script/q3_floor.py --variant god --quick
python3 script/q3_floor.py --variant god --seeds 1-30     # 全量（本机 122 s）

# 包入口（等价）
python3 -m script.q4 list
python3 -m script.q4 verify --json logs/q4/certificate.json
```

## 七、依赖与约定

* Python 3.13 + numpy + scipy（凸包 / Delaunay / L-BFGS-B）。
* 所有脚本：LF、UTF-8、`from __future__ import annotations`、`argparse`、
  `sys.path.insert(0, <repo>/framework/src)` + `# noqa: E402`，中文模块 docstring 说明
  "这个工具为什么存在"。
* 运行产物一律落在 `logs/` 下（已被 `.gitignore` 的 `/logs/*` 排除），不进源码树。
* 这些脚本**不**参与 `pytest`（文件名不匹配 `test_*.py`），可安全离线运行。
