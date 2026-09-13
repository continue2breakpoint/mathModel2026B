# `script/` — 自动化脚本层

这一层负责把 **框架代码**、**login-jammers 测试环境**、**线上平台** 和
**批量调参所需的日志** 串起来。它本身不含算法，只做编排、路径解析、日志汇总。

## 1. 快速开始

题目 2 的可视化已经统一成**一个编辑可视化界面**（一套外壳 + 两个工作区）：

```bash
python3 script/q2_dashboard.py --port 8055
```

* `http://127.0.0.1:8055/` —— 双点交会工作区（S1、θ1、ρ → 第二检测点 S2 热图）
* `http://127.0.0.1:8055/matrix` —— 知识矩阵工作区（Channel × Path → 下一观测点热图）

两个工作区共用顶栏切换、同一套控件与配色、同一份**辅助线图层注册表**
（场地圆、可能源区、测向楔形与 ±1° 射线、近距圈、排除盘、三区着色、p_det 等值线、最优格点）
和同一份**导入 / 导出 / 粘贴**面板（JSON、Channel × Path Markdown、拖放文件、剪贴板、互转）。
编辑台是"收纳页"：可**收起 / 半屏 / 全屏**（`E` 循环、`F` 全屏、`Esc` 收起），
状态写进 URL（`?ws=matrix&drawer=full`）。说明见
[统一编辑台](../docs/q2_unified_ui.md) 与 [知识矩阵热图](../docs/q2_knowledge_heatmap.md)。

```bash
# 真浏览器端到端测试（自动拉起 Flask + 无头 Chrome）
node script/q2_ui_e2e_test.js --shots /tmp/q2-shots

# 后端与几何单测
PYTHONPATH=cpp python3 -m unittest discover -s script -p 'test_q2_matrix*.py'
```

```bash
cd mathModel2026B

# 一键体检（离线全流程，不需要 Windows 客户端）
bash script/smoke.sh

# 带上线上连通性预检
bash script/smoke.sh --online

# 额外跑一遍题目2 界面的真浏览器端到端测试
bash script/smoke.sh --ui
```

## 2. 不写死 login-jammers 路径

`script/jammers_paths.py` 按下面的顺序自动定位 login-jammers 仓库根目录
（判定标志：存在 `linux-client/python/jammers_auth.py`）：

1. 函数参数 / 命令行 `--login-jammers <dir>`
2. 环境变量 `JAMMERS_ROOT` 或 `LOGIN_JAMMERS_ROOT`
3. `script/jammers.local.json` 里的 `login_jammers_root`
   （该文件已 gitignore，适合放机器相关的本地路径）
4. 从 `script/` 向上逐级查找，检查每一级的兄弟目录
5. 从当前工作目录向上做同样的查找

所以只要 `login-jammers` 与 `mathModel2026B` 并列放置（当前就是这样），
**零配置即可工作**；换机器只需设置一次环境变量：

```bash
export JAMMERS_ROOT=/path/to/login-jammers
export JAMMERS_PYTHON=$(which python3.13)   # 可选：指定解释器
```

自查：

```bash
python3 script/jammers_paths.py
```

队号（`robot_id`）也从 login-jammers 的 `linux-client/config.json` 里读，
不在代码里写死；可用 `--robot-id` 或 `JAMMERS_TEAM_NO` 覆盖。

## 3. 环境

框架只用标准库，要求 **Python >= 3.11**（用到 `StrEnum` / `dataclass(slots=True)`）。

```bash
# 方式 A：用系统/python.org 的 3.11+
python3 -V

# 方式 B：用 pyenv
pyenv install 3.13.12 && pyenv local 3.13.12

# 方式 C：虚拟环境（跑测试用）
python3 -m venv .venv && . .venv/bin/activate
pip install -e 'framework[dev]'
```

脚本会按 `JAMMERS_PYTHON` → `sys.executable` → `python3.14/3.13/3.12/3.11/python3`
的顺序挑一个满足版本要求的解释器。

## 4. 脚本清单

| 脚本 | 作用 |
| --- | --- |
| `jammers_paths.py` | 解析 login-jammers 路径、解释器、账号配置、会话文件 |
| `preflight.py` | 线上腿预检：`status` → `login` → `presence` → `renew`（经 login-jammers CLI） |
| `probe_practice.py` | 线上**练习测试 authorize 探针**：拿到 `practice_ticket_b64` 并解出票据 claims |
| `probe_practice_variants.py` | 请求体变体对照表，区分"请求字段不正确"与"请求内容不正确" |
| `run_practice_online.py` | **线上演练入口**：默认连接官方客户端 robot API（端口取 `JAMMERS_ROBOT_PORT`）；加 `--mock` 时才启动本地 MockSimulator 做接口自测 |
| `run.py` | 跑 **一次** 问题3 策略（`--mode mock` 离线 / `--mode live` 连真实 robot 端口） |
| `run_batch.py` | 参数网格 × 随机案例的批量调参，产出 `logs/results.jsonl` 与批次摘要 |
| `q3_time_audit.py` | **虚拟时间审计**：按决策类别（扫描/逼近/清除）拆路程与时间占比 |
| `q3_diagnose_scan.py` | **覆盖扫描质量诊断**：扫描结束后各频道可行域有多准 |
| `q3_cover_design.py` | **覆盖扫描设计离线优化**（含"每停点扫 20 频道"的代价） |
| `report.py` | 汇总 `results.jsonl`：表格 / 分组统计 / CSV / Markdown |
| `q2_dashboard.py` | **题目2 统一编辑台**：双点交会端点 + 页面与 `/assets` 路由 |
| `q2_matrix_dashboard.py` | 题目2 知识矩阵端点（与双点共用同一页面，只是默认工作区不同） |
| `q2_matrix_engine.py` | 单频道知识矩阵后验与非凸保守几何（另输出分区与辅助线几何） |
| `q2_page.py` | 统一页面外壳与静态资源服务（两个蓝图共用） |
| `q2_ui_e2e_test.js` | 题目2 界面真浏览器端到端测试（收纳页三态 / 辅助线 / 导入导出 / 互转） |
| `test_q2_matrix_engine.py` | 知识矩阵几何、后验与 HTTP API 单测 |
| `smoke.sh` | 一键体检整条工作流 |

> 线上演练的完整说明、逆向细节与"为什么不建议上报"见 [`../说明.md`](../说明.md)。

### 4.1 线上腿预检

```bash
python3 script/preflight.py                 # 完整预检
python3 script/preflight.py --no-login      # 只查平台状态 + robot 端口是否已开
```

退出码：`0` 全通；`1` robot 端口未开（没有进行中的测试，属正常）；`2` 平台返回业务码
（如 `account_already_active`，说明链路与请求格式都对）；`3` 路径/网络错误。

**会话文件**：预检会显式把 `--session-file` 指向仓库内的
`login-jammers/linux-client/jm.session.json`（该文件名已被 login-jammers 的
`.gitignore` 排除）。因为默认的 `~/.cache/jammers/session.json` 在容器/受限沙箱里
常常不可写，会出现"登录其实已成功、却因为写会话失败而报错"这种最容易被误判的结局。

实测输出：

```text
[preflight] status ok: new_tests_enabled=True deadline=2026-09-13T09:30:00.000Z
[preflight] robot 127.0.0.1:2026 -> closed
[preflight] login ok: team_no=<队号>
[preflight] 剩余正式测试次数：problem3=3 problem4=3 待上传包=0
[preflight] presence rc=0
[preflight] renew rc=0
[preflight] OK 线上链路打通：status / login / presence / renew 全部成功
```

> ⚠️ 登录成功会**占用该账号的设备会话**。如果之后要在自己的模拟器里登录，
> 先 `python3 <login-jammers>/linux-client/python/jammers_auth.py logout
> --session-file <...>/jm.session.json`，否则会撞上 `account_already_active`(409)。

### 4.2 单次运行

```bash
# 离线 mock
python3 script/run.py --seed 7

# 连真实 robot 端口（先在 Windows 客户端里点"开始练习测试"）
python3 script/run.py --mode live

# 覆盖任意策略参数
python3 script/run.py --seed 7 --param scan_radius=1200 --param travel_weight=0.0008
```

### 4.3 批量调参

```bash
# 参数网格 × 种子；--jobs 并发（每个 mock 自己占一个随机端口）
python3 script/run_batch.py --tag tune1 --seeds 1-20 \
    --grid scan_radius=1125,1200,1300,1400 \
    --grid travel_weight=0.00033,0.00083 \
    --grid localize_before_full_scan=true,false \
    --jobs 8 --require-full-clear

# 从规格文件读（便于把一组实验版本化）
python3 script/run_batch.py --spec script/specs/example.json

# 看结果
python3 script/report.py --tag tune1 --group-by param_scan_radius
python3 script/report.py --tag tune1 --csv tune1.csv
python3 script/report.py --tag tune1 --markdown
```

`--require-full-clear` 会在任何一个 run 没有清完时返回非零——
因为题面要求"确保所有干扰源被清除"，**时间只有在 100% 清除率下才有可比性**。

## 5. 日志格式

目录布局：

```text
mathModel2026B/logs/
├── results.jsonl                    # 跨 run 扁平索引（一行一个 run，直接喂 pandas）
├── preflight.jsonl                  # 每次线上预检一行
├── preflight/preflight_<ts>.json    # 预检完整报告（含每步命令与返回）
├── batches/<batch_id>.json          # 批次聚合（按参数组合的均值/标准差/最小值…）
└── runs/<run_id>/
    ├── meta.json                    # 运行配置 + 环境（python/git rev/…）+ mock 案例真值
    ├── run.jsonl                    # 逐步事件流：每次 HTTP 交互一条
    ├── trace.jsonl                  # 策略内部决策（每条测量/清除的 reason）
    └── summary.json                 # 该 run 的最终指标 + 状态快照 + 真值
```

`run_id` 形如 `20260910T154822Z_s3_tune1_fbc7ed`
（UTC 时间戳 + seed + tag + 随机后缀），同时是目录名和索引主键。

### 5.1 `results.jsonl`（做统计用的那一份）

每行是一个扁平 JSON 对象，字段分三类：

* **标识**：`run_id` `tag` `mode` `status` `error` `started_utc` `finished_utc`
* **参数**：全部以 `param_` 前缀（`param_scan_radius` `param_travel_weight` …），
  列表类参数用逗号连接，另外保留一份原始 `params_json`
* **指标**（裸名，可直接排序/分组）：

  | 字段 | 含义 |
  | --- | --- |
  | `cleared_count` / `n_jammers` / `cleared_ratio` | 清除个数 / 总数 / 清除比例 |
  | `average_clear_time_s` | **平均定位清除时间**（= 虚拟总时间 / 清除个数） |
  | `virtual_time_s` | 虚拟总时间（模拟器时钟，计分口径） |
  | `move_distance_m` | 累计路程 |
  | `measure_count` / `clear_count` / `clear_failure_count` | 交互次数 |
  | `channel_switch_count` | 切频道次数 |
  | `wall_s` | 程序运行时间（真实墙钟，受 20 分钟上限约束） |

直接用 pandas：

```python
import pandas as pd
df = pd.read_json("logs/results.jsonl", lines=True)
df[df.tag == "tune1"].groupby("param_scan_radius")["average_clear_time_s"].agg(["mean", "std", "count"])
```

### 5.2 `runs/<run_id>/run.jsonl`

每行一条事件，都带 `seq`（单调递增）、`t_wall`（epoch 秒）、`t_rel`（相对开始）。
`action` 取值：

| action | 说明 |
| --- | --- |
| `run_start` | 本次运行的配置与地址 |
| `enter` / `measure` / `clear` / `exit` | 一次 robot API 调用；含 `request`、服务器原始 `response`、`outcome`、`virtual_time_s`、`wall_ms` |
| `enter_result` | `/enter` 返回的预算（`max_real_duration_s` 等） |
| `error` | `kind` 为 `port_gated` / `simulator` / `unhandled`，含 traceback |

`response` 保留**服务器原始 JSON**，所以协议一旦变化可以事后离线复核，
也可以直接用框架里的 `ReplayClient` 重放：

```python
from mathmodel2026b.client import ReplayClient
client = ReplayClient.from_jsonl("logs/runs/<run_id>/run.jsonl")
```

## 6. 离线 mock 与真实链路的边界

| 环节 | 离线 mock | 线上真实 |
| --- | --- | --- |
| 策略 / client 代码 | 同一份 | 同一份 |
| `robot-protocol-v1` 字段校验 | 严格复刻（含 `arena_id`/`robot_id`/未知字段/频道范围） | 官方实现 |
| 物理与计时（附录2） | 按题目重建（±1° 固定误差、5m/s、1s/5s/3s+2s、20m 清除） | 官方 `internal/simcore` |
| 案例真值 | 可读（写进 `meta.json`） | 拿不到（演练结束才由界面给出总数） |
| 计分口径 | **仅供调参排序** | 以线上为准 |

因此：**mock 上只能做相对比较**（哪个参数更好），最终数值必须上线复测。
`script/preflight.py` + `run.py --mode live` 就是这条上线路径。

### 6.1 自测 live 通路（不需要 Windows 客户端）

把 mock 单独起在真实的 robot 端口上（默认 `JAMMERS_ROBOT_PORT=2026`），就能完整演练"官方客户端在 robot 端口后面"的场景
——这也正是"客户端做端口映射把 robot 暴露给线上平台"时的数据面形状：

```bash
# 终端 A：把 mock 挂在 2026
PYTHONPATH=framework/src python3 -m mathmodel2026b.mock.server \
    --port 2026 --robot-id <队号> --seed 11

# 终端 B：先确认线上会话 + 端口都就绪，再走 live 分支
python3 script/preflight.py
python3 script/run.py --mode live --tag live-local
```

实测（会话已登录 + 端口已开，即生产形状）：

```text
[preflight] robot 127.0.0.1:2026 -> open
[preflight] OK 线上链路打通：... robot 端口已开，可直接 --mode live
ok run=..._live-online_45c523 cleared=16/None avg_s=543.4 virtual_s=8694.8
```

事件流里记录的 `measure_result` / `clear_result` / `accepted` / `virtual_time_s` /
`exit_reason` 都是真实线上格式，因此换成官方客户端监听同一端口即可无缝切到线上。

注意：`--mode live` 时 `n_jammers` 为 `None`（robot API 拿不到总数，
演练测试只在结束时由界面给出），所以线上只能记 `cleared_count` 与虚拟时间。

### 6.2 正式测试前的建议流程

1. `python3 script/preflight.py` —— 确认会话、剩余次数、robot 端口状态。
2. 在模拟器里点"开始练习测试"，倒计时结束后端口才开。
3. `python3 script/run.py --mode live --tag practice-N` —— 跑一次演练，日志落在
   `logs/runs/<run_id>/`。
4. `python3 script/report.py --tag practice-N` —— 看清除率与平均定位清除时间。
5. 用 `ReplayClient` 重放这一场的 `run.jsonl`，确认本地逻辑与线上一致，
   再去点"开始正式测试"（正式测试只有 3 次机会）。

## 7. 问题3/4 的基准 · 验收 · 官方演练

这三个脚本是**唯一**的问题3/4 评估入口（原先散在 `_bench_v5.py` / `_ab_q4_v17.py` /
`_bench_ab.py` / `_bench_paper_q4.py` / `_bench_versions.py` / `最终验收_问题34.py` /
`官方演练_问题34.py` 里，各写一份 import 与交付参数）。共同约定：

* **策略一律来自注册表**。用 `mathmodel2026b.versioned` 的
  `build_method(key, **overrides)` 构造，交付参数覆盖（`schedule_min_readings`、
  `or_opt_passes` …）必然生效；脚本里**不再出现**任何参数默认值，
  只有命令行显式给的 `--param` 覆盖。
* **臂名就是注册表键**：`q3`/`matrix`/`q3-v5`…/`q3-v18`/`q4-v8`/`q4-v9`/`q4-v14`/
  `q4-v17`/`q4-v18`/`paper-q4`；`python3 script/list_methods.py` 看全部口径。
* **计时口径可切换**：默认按附件1 §2.3（未发现 `/clear` 3s、已清除 5s）；
  `--legacy-timing` 用旧 mock 口径（失败的 `/clear` 也按 5s）复现
  2026-09-13 之前的归档数字（两者满足 `T_legacy = T_correct + 2×失败清除次数`）。

### 7.1 `bench_q34.py` —— 统一 A/B 基准

```bash
# 问题3（全向）：注册表里的当前最终交付 + 历代交付 / 路线A / 基线
python3 script/bench_q34.py --mode omni --seeds 1-30

# 问题4（全向+定向混合）
python3 script/bench_q34.py --mode dir --seeds 1-30

# 只比两代 / 持有集 / 全定向压力场景 / 复现归档数字
python3 script/bench_q34.py --mode dir --seeds 1-10 --arms q4-v18,q4-v17
python3 script/bench_q34.py --mode dir --seeds 61-160 --n-directional 16
python3 script/bench_q34.py --mode omni --seeds 1-30 --legacy-timing

# 列出全部可选臂（含"负结果/不适用"标注）
python3 script/bench_q34.py --list
```

默认臂 = 该问题在注册表里的**当前最终交付** + `BASE_ARMS` 里的历代交付/路线入口/对照
（所以注册表换交付版时，表头自动跟上，不必改脚本）。`--param KEY=VALUE` 对**所有**臂生效，
用于消融；`--repeat N` 把每个 (臂, 种子) 重复 N 局。

每个臂输出：全清**种子**数/总数、清除源数/总源数、`average_clear_time_s` 的中位与均值、
`virtual_time_s` 中位、`move_distance_m` 中位、墙钟中位；结果写
`logs/bench/<mode>_<arms>_<start>_<end>.json`（含逐例明细与
`diagnostics.params.as_dict()`）与同名 `.txt`（表格）。

### 7.2 `acceptance_q34.py` —— 最终模拟测试（验收）

```bash
python3 script/acceptance_q34.py                       # 种子 1-5，Q3 + Q4 + Q4 对照臂
python3 script/acceptance_q34.py --seeds 6-10
python3 script/acceptance_q34.py --seeds 1-5 --no-baseline
python3 script/acceptance_q34.py --out 验收_20260913.txt
python3 script/acceptance_q34.py --seeds 1-5 --legacy-timing
```

默认臂：问题3 = `q3-v15`（`--q3-arm`）、问题4 = `q4-v18`（`--q4-arm`）、
对照臂 = `q4-v17`（`--baseline-arm`）。输出逐局明细 + 小结 + 与大规模基准的对照 +
结论；文本写到 `--out`（默认工作目录下的 `最终验收_问题34_结果.txt`）。
若注册表已经改认了别的"最终交付"（例如问题3 的 `q3-v18`），对应段落会打印一条
`⚠` 漂移提示并给出切换开关；每段方括号里显示该臂在注册表里的真实状态。

### 7.3 `official_drill_q34.py` —— 官方模拟器演练

```bash
# 问题3、4 各 5 局；每局都要在官方 GUI 里点【演练测试】→【开始】
python3 script/official_drill_q34.py

# 先验证链路 / 只演练一题 / 指定队号与端口
python3 script/official_drill_q34.py --repeat 1 --problem 3
python3 script/official_drill_q34.py --problem 4 --repeat 3 --robot-id <队号>
JAMMERS_ROBOT_PORT=2026 python3 script/official_drill_q34.py

# 没有官方模拟器时：用本地 mock 走完全同一条流程（含预算标定 / 计数 / 落盘）
python3 script/official_drill_q34.py --dry-run --repeat 1

# 端口关闭时只探一次、立刻给出中文排查清单
python3 script/official_drill_q34.py --wait-s 0
```

对接官方 `robot-protocol-v1`（默认 `http://127.0.0.1:2026`，端口取环境变量
`JAMMERS_ROBOT_PORT`，未设置 = 2026）。流程：轮询 `/enter` 等你点开始 →
用**注册表里的当前最终交付臂**（`final=True` 那一条，目前问题3 = `q3-v18`、
问题4 = `q4-v18`；注册表没有 `final` 时退回 `q3-v15`/`q4-v18`）跑完整局 → `/exit` →
读模拟器落盘的 `*.result.json` 取**官方真值**（源数 / 定向源数 / 案例编码），
并**校验这一局开的确实是问题3/4**（点错入口会当场告警，且不给"全清"结论）。
队号沿用 `run.py` 的口径（`--robot-id` / `JAMMERS_TEAM_NO` / login-jammers 配置），
真值目录用 `--data-dir`（默认环境变量 `JAMMERS_SIM_DATA_DIR`，未设置则自动探测
`JammersSimulatorData/behavior-logs`）。墙钟预算按 `/enter` 返回的
`remaining_real_duration_s − 30s` 标定，**不写死 1200s**。`--legacy-timing` 只对
`--dry-run` 有意义（官方计时由官方实现决定）。

---


### 7.4 ⚠️ 官方真值目录：模拟器跑在 Windows VM 时必须手工取

`--data-dir` 指向官方落盘真值的 `*.result.json` 目录。**当模拟器跑在 Windows 虚拟机里
（本仓库的现行拓扑：VM `192.168.122.161` + `portrelay`），这个目录在宿主机上不存在**，
自动探测会失败（可能命中工作区里一份过时的快照副本），于是结果行显示
`清除 15/? ?（拿不到官方源数）`、小结里"全清 0/1 局"。

**这时的清除数与虚拟时间是可信的**（来自 `/enter` 之后的真实动作流水），**只有
"源总数 / 定向源数 / 案例编码"拿不到**。真值在 VM 内：

```
C:\zW<U+200C>indowsUtility\Jammers-simulator-full\JammersSimulatorData\behavior-logs\
    practice-p4-<run_no>-<CASE-CODE>.result.json
```

（注意路径里有一个**零宽非连接符** U+200C，用 `C:\zW*indowsUtility\...` 通配最省事。）

取回方式（WinRM，见 `docs/robot-link-windows-vm.md` §4.4；工作区 `_probe2026/` 有现成助手）：

```bash
cd _probe2026
python3 wr.py ps 'Get-ChildItem "C:\zW*indowsUtility\Jammers-simulator-full\JammersSimulatorData\behavior-logs\*.result.json" | Sort-Object LastWriteTime -Descending | Select-Object -First 4 | ForEach-Object { [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($_.Name + "||" + (Get-Content $_.FullName -Raw))) }'
```

输出是 base64（避免 PS 5.1 代码页把中文/路径弄乱），在 Linux 侧解码即得
`{"problem_no":4,...,"jammer_count":15,"directional_jammer_count":11}`。
把该目录挂到宿主机（SMB / `--data-dir`），脚本就能自动读真值并给出"全清"结论。

2026-09-13 14:19–14:23 的两局真值已按此取回并归档在
`docs/data/official-drill-20260913-1420-q18.json`。

---

## 8. 问题3/4 的持有集审计与消融

`## 7` 的三个脚本回答"最终版跑多少分"；本节的两个脚本回答
**"凭什么是这个最终版、它在没调过参的案例上还行不行"**。它们的出现是因为
2026-09-13 的独立复核发现：在调参用过的种子上全清**不等于**全清 ——
`q4-v17` 在种子 1–60 上 760/760，却在未参与调参的 61–160 上只有 97/100
（`docs/review-fixes-2026-09-13.md`）。

### 8.1 `script/audit_q34.py` —— 持有集审计 / 逐层消融

```bash
# 持有集：种子 61-160，混合与全定向压力场景
python3 script/audit_q34.py --mode mixed           --arms all --seeds 61-160
python3 script/audit_q34.py --mode all_directional --arms all --seeds 61-160

# 逐层消融 q4-v18（关覆盖清除 / 关排除圆 / 关知识矩阵，对照 v17、v14）
python3 script/audit_q34.py --mode all_directional --arms ablation --seeds 61-160

# 问题3
python3 script/audit_q34.py --mode omni --strategy q3-v18 --seeds 61-160

# 复现 2026-09-13 之前归档的基准（失败 /clear 也按 5s）
python3 script/audit_q34.py --mode mixed --strategy q4-v17 --seeds 1-60 --legacy-timing
```

三种模式：`mixed`（默认分布）、`all_directional`（**压力场景**，全部源设为定向；
`generate_case` 默认只生成 1..⌊n/2⌋ 个定向源，覆盖不到高定向占比）、`omni`（问题3）。
逐案例统计落到 `logs/audit/<mode>_<arm>_<start>_<end>.json`，失败案例的完整轨迹
（含**仅供离线诊断**的源真值）单独落盘 `logs/audit/failure_*.json`，便于复现
"估计误差 33m、最近试清距离 21.5m"这类根因。

### 8.2 `script/q3_or_opt_ablation.py` —— 问题3 的三轴分解

```bash
python3 script/q3_or_opt_ablation.py                    # 1-30 / 1-60 / 61-160
python3 script/q3_or_opt_ablation.py --seeds 1-30 --json logs/q3_ablation.json
```

把问题3 的收益拆成**三个互不相干的轴**，避免把两笔账算到一个机制头上：

| 轴 | 效果 |
| --- | --- |
| 扫描布局定稿 `6/1130/3` vs 注册表旧默认 `7/1110/2` | 约 −4.5 ~ −8.6 s/源（**最大的一项**） |
| 在线 or-opt 比较基准 bug 修正 | 约 −1.9 ~ −7.1 s/源（修正前它在空转） |
| 覆盖式清除（v18 新增） | **中性**（±0.5 s/源，噪声内） |

判别"定稿布局是 6/1130/3"的依据也在这里：归档口径（`--legacy-timing`）+ or-opt 关闭时，
定稿布局在 seed 1–30 上给出 **264.81**，与最终工程 `README` 的 v15 数字**逐位一致**；
而 `7/1110/2` 给出 273.73。`versioned.Q3_SCAN_PARAMS` 就是这组参数。
