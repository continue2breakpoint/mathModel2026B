# 2026 CUMCM B题：无线电干扰源快速定位与清除

轻量 Python 框架（**仅标准库**，Python >= 3.11），面向问题3/4。

设计目标：

- 不过度工程化，但每一项都能对上题面/协议里的具体条款；
- 模拟器通信、状态管理、几何计算、策略解耦；
- Q3/Q4 策略可插拔，参数集中在 `Q3Params` 便于批量调参；
- 每一步交互都落结构化日志，可离线重放做回归；
- 先保证正确性与可测试性，再优化总虚拟时间。

## 目录

```text
src/mathmodel2026b/
├── protocol.py       # robot-protocol-v1 线协议类型与解析 + 题面物理常量
├── geometry.py       # 扇形交会、可行域裁剪、最小包围圆、覆盖半径校验
├── state.py          # 机器狗 / 逐频道状态与计数
├── client.py         # SimulatorClient(HTTP) / RecordingClient / ReplayClient
├── strategy.py       # 可插拔策略（Q3Strategy + Q3Params）
├── runner.py         # 一次完整运行的编排（enter -> run -> exit -> metrics）
├── logging_utils.py  # JSONL 日志格式与跨 run 索引
└── mock/
    ├── world.py      # 案例生成 + 附录2 物理/计时模型
    └── server.py     # 本地 HTTP mock，严格复刻 robot-protocol-v1 校验
scripts/run_q3.py
tests/
docs/
```

## 环境

```bash
python3.13 -m venv .venv          # 3.11+ 均可
source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

或直接用仓库的脚本层（会自动挑解释器）：

```bash
cd ..
bash script/smoke.sh
```

## 快速跑一次

```bash
# 离线 mock（不需要 Windows 客户端）
python3 scripts/run_q3.py --mode mock --seed 7

# 连真实 robot 端口（客户端已开始练习/正式测试）
python3 scripts/run_q3.py --mode live --robot-id <队号>
```

日志写在 `../logs/`，格式见 [`../script/README.md`](../script/README.md)。

## 算法要点（问题3）

1. **覆盖**：全向源有效接收半径 >= 1000m。取圆心 + 半径 `r` 正六边形六顶点共 7 点为
   检测点。只要"覆盖半径"（区域内任一点到最近检测点的最大距离）<= 1000m，任何干扰源
   都至少被一个检测点收到。解析下界 `r >= 900√3 − 100√19 ≈ 1122.96m`，
   默认取 **1200m**（覆盖半径 968.9m，留约 31m 余量）。
   用 `geometry.covering_radius` 可离线复核任意半径。
   覆盖扫描的设计本身已接近最优——见 [`docs/q3-strategy.md`](docs/q3-strategy.md)。
2. **定位**：一次示向度读数给出以检测点为顶点、角宽 2° 的扇形。多条扇形求交得到
   **必定包含真值**的凸多边形。`direction` 读数还蕴含"距离 <= 1500m"这一**凸**约束，
   能把细长区域收成有界区域——这是可行域收缩里最划算的一步。
3. **误差不是噪声**：同一地点重复检测读数不变（附录2-1），所以**不做平均**，只做
   集合求交；重复在同一地点测量不产生任何信息。
4. **清除判据**：可行域的最小包围圆半径 <= 20m（光学作用距离）时，瞄准圆心执行
   `/clear` 必定命中。距离 `d` 处的横向不确定度约 `d·tan(1°) ≈ d·0.01745`，
   所以近距测向收敛极快——这就是"先远距交会缩到几十米，再逼近到 20m 内"的原因。
5. **顺序比几何更值钱**：一频道一源（附录1-1），所以"清除所有源"本质是一个 TSP。
   当前用**在线最近邻**（每清完一个源，用最新估计点重挑最近的下一个）串成路线；
   配合更小的逼近半径 `(30,60,120,250)` 与"离源多近"打分项，
   在 200 个随机案例上把**平均定位清除时间从 558.4s 降到 349.8s**（清除率 1.000）。

## 与真实链路的边界

`mock` 严格复刻了 **协议字段校验**（`arena_id`/`robot_id`/`request_id`/未知字段/
频道范围/坐标范围）和 **附录2 的物理与计时**，但没有官方 `internal/simcore` 的实现细节。
因此 mock 上的数值**只适合做相对排序**（哪个参数更好），最终数值必须上线复测。
两者对策略与 client 走的是同一份代码，切换只需 `--mode live`。
线上演练测试的全流程（含"完全不依赖官方 Windows 客户端"的接口路径）见
[`../说明.md`](../说明.md)。

## 已知限制

- 问题4（定向源）在 `mock.world` 里已支持（`--directional`），但策略尚未针对
  定向源的"覆盖角只有 ±90°"做专门处理，`Q3Strategy` 直接跑会明显掉清除率。
- `ReplayClient` 只能按记录顺序重放，不做请求校验。
