# 2026 CUMCM B题：无线电干扰源快速定位与清除

轻量 Python 3.14 框架，面向问题3/4。

设计目标：
- 不过度工程化；
- 模拟器通信、状态管理、几何计算、策略解耦；
- Q3/Q4 策略可插拔；
- 后续抓取演练判例后可加入 ReplayClient 离线重放；
- 先保证正确性与可测试性，再优化总虚拟时间。

## 目录

```text
src/mathmodel2026b/
├── client.py
├── geometry.py
├── protocol.py
├── state.py
└── strategy.py
scripts/run_q3.py
tests/
docs/
```

## 环境

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

当前 baseline：圆心 + 半径 1400m 正六边形六顶点，共 7 个扫描点。
