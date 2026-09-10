# `script/` — 自动化脚本层

这一层负责把 **框架代码**、**login-jammers 测试环境**、**线上平台** 和
**批量调参所需的日志** 串起来。它本身不含算法，只做编排、路径解析、日志汇总。

## 1. 快速开始

```bash
cd mathModel2026B

# 一键体检（离线全流程，不需要 Windows 客户端）
bash script/smoke.sh

# 带上线上连通性预检
bash script/smoke.sh --online
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
| `run.py` | 跑 **一次** 问题3 策略（`--mode mock` 离线 / `--mode live` 连真实 robot 端口） |
| `run_batch.py` | 参数网格 × 随机案例的批量调参，产出 `logs/results.jsonl` 与批次摘要 |
| `report.py` | 汇总 `results.jsonl`：表格 / 分组统计 / CSV / Markdown |
| `smoke.sh` | 一键体检整条工作流 |

### 4.1 线上腿预检

```bash
python3 script/preflight.py                 # 完整预检
python3 script/preflight.py --no-login      # 只查平台状态 + robot 端口是否已开
```

退出码：`0` 全通；`1` robot 端口未开（没有进行中的测试，属正常）；`2` 平台返回业务码
（如 `account_already_active`，说明链路与请求格式都对）；`3` 路径/网络错误。

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

把 mock 单独起在真实的 robot 端口上，就能完整演练"客户端在 2026 端口后面"的场景
——这也正是"客户端做端口映射把 robot 暴露给线上平台"时的数据面形状：

```bash
# 终端 A：把 mock 挂在 2026
PYTHONPATH=framework/src python3 -m mathmodel2026b.mock.server \
    --port 2026 --robot-id <队号> --seed 11

# 终端 B：走 live 分支
python3 script/run.py --mode live --tag live-local
```

实测：`cleared=13/13`，`average_clear_time_s=648.5`，事件流里记录的
`measure_result` / `clear_result` / `accepted` / `virtual_time_s` / `exit_reason`
都是真实线上格式，因此换成官方客户端监听同一端口即可无缝切到线上。

注意：`--mode live` 时 `n_jammers` 为 `None`（robot API 拿不到总数，
演练测试只在结束时由界面给出），所以线上只能记 `cleared_count` 与虚拟时间。
