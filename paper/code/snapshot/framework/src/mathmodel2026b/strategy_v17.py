"""问题4 的 v17：发现层换成【方法二】联合优化布局（坐标+顺序交替优化）。

布局由 ``_joint_opt_q4.py`` 离线产出：以 v9 的两圈式 25 点为初始解，在
局部凸包判据（``∀x ∈ Ω: x ∈ conv{q_i : |q_i − x| ≤ 1000}``）的约束下，交替执行
  A. 固定顺序的坐标微调
  B. 固定坐标的 2-opt + or-opt 顺序调整
  C. 删除-修复（删点后把邻点拉去补洞）
每轮结束重新校验判据，不通过则回退。

⚠️ **代码与文字必须对齐（2026-09-13 复核 §六.3 / §六.5）**：``_joint_opt_q4.py``
实际用的是 **soft-min 罚函数 + L-BFGS-B + 几何修复（``repair_geom``）+ 可行性微调
（``polish_feas``）+ 顺序搜索**，**不是**精确 SOCP，也不是 SLSQP。而且"固定访问顺序"
**不会**让原问题自动变成 SOCP —— 半径内的点集随坐标改变，"存在某个正面点"带析取
结构，同时优化坐标与凸组合权重会产生双线性项。可以把单元顶点的见证集合与凸组合
权重**固定**下来构造保守的 SOCP 子问题，但那是另一件要单独做并重验证书的事。
本段此前的旧描述（"信任域 SCP 坐标微调（SLSQP，witness 半圆盘二阶锥约束）"）只是
**研究设想**，不能当作已实现算法的说明。

在线机制（调度、直接清、沿射线推进、免重测备忘）全部继承 v14。
"""
# NOTE(端口适配): 用显式两参 super(Cls, self) 而不是零参 super()。
# @dataclass(slots=True) 会为子类重建类对象，零参 super() 依赖的 __class__
# 单元格会指向**原始**类，于是 'Q4V14Params' 实例在父类的方法里调用
# super() 时会抛 TypeError（CPython 文档 dataclasses 章节明确警告）。
from __future__ import annotations

import math
from dataclasses import dataclass

from .geometry import ARENA_RADIUS_M, Point
from .strategy_v14 import Q4V14Params, Q4V14Strategy

#: 联合优化产出的检测点（已按最优访问顺序排列）；
#: 由 _joint_opt_layout.py 生成后粘贴，空表时回退 v9 两圈式布局。
#: m=24，离线路线 18507.5m（v9 25 点 18828m）。
#:
#: ⚠️ **证书口径（2026-09-13 复核 §二）**：原先写作"完备性经三分辨率网格证书 +
#: Delaunay 凸包 2000 采样 + 0.25° 暴力扫描（19200 样本，worst=989.8m）"。
#: 那句表述有问题 —— ``_joint_opt_q4.py`` 的 ``brute_worst()`` 是**随机位置 +
#: 有限边界点 + 有限朝向的采样**，``989.8m`` 是**采样最大值**，不能直接称作连续域
#: 的最坏真值；0.25° 的离散化也不能自动消除盲区；而"闭集覆盖 + 紧性 ⇒ 有限检测点"
#: 这一步本身不成立（每个 x 能选有限见证 ≠ 能为全域选统一有限点集）。
#:
#: 复核给出的替代**充分条件**（已并入 ``script/verify_layout_certificate.py``）：
#: 把目标圆的包围正方形递归四分；对每个与目标圆相交的正方形 C，取"到 C 的**所有
#: 顶点**距离都 < 1000m"的检测点集合 S_C，验证 C 的所有顶点都在 conv(S_C) 内。
#: 由范数凸性与凸包凸性，C 内每个位置都满足局部凸包判据，**因而无需对朝向离散采样**。
#: 复核结果：本布局与 v9 的 25 点布局**都通过**，未决单元 0。
#: 这是**数值**证书（浮点凸包 + 向内余量），不是区间算术的形式化验证。
LAYOUT_Q4_V17: list[tuple[float, float]] = [
    (74.8, 29.7),
    (480.4, -745.5),
    (-401.8, -781.5),
    (-901.7, 19.8),
    (-453.4, 768.2),
    (-903.7, 1585.9),
    (-1662.3, 1016.2),
    (-1345.0, 758.9),
    (-1820.9, 12.9),
    (-1379.9, -759.1),
    (-1674.8, -1006.7),
    (-895.3, -1593.3),
    (-16.3, -1565.4),
    (75.0, -1970.9),
    (938.7, -1565.4),
    (1725.4, -934.5),
    (1345.0, -742.7),
    (1003.8, 97.7),
    (1900.8, 35.5),
    (1616.0, 890.0),
    (1014.6, 1586.5),
    (577.6, 871.6),
    (39.0, 1565.4),
    (60.6, 1964.9),
]


@dataclass(slots=True)
class Q4V17Params(Q4V14Params):
    #: 使用联合优化布局（False 则回退 v9 两圈式，用于 A/B 消融）
    use_opt_layout: bool = True
    #: 直接清失败后的邻域环形试探半径（米，0 = 关闭）。
    #: 清除判据是"距真实源 ≤20m"，估计点误差 20~40m 时环形试探可兜底。
    clear_search_radius_m: float = 15.0
    #: 环形试探点数（0 = 关闭）
    clear_search_points: int = 6


class Q4V17Strategy(Q4V14Strategy):
    """v17 = v14 在线机制 + 方法二联合优化发现层布局 + 环形兜底。"""

    def __init__(self, params: Q4V17Params | None = None) -> None:
        self.params: Q4V17Params = params or Q4V17Params()
        super(Q4V17Strategy, self).__init__(self.params)

    def _scan_points(self) -> list[Point]:
        if self.params.use_opt_layout and LAYOUT_Q4_V17:
            # 布局本身就是按最优访问顺序给出的
            return [Point(float(x), float(y)) for (x, y) in LAYOUT_Q4_V17]
        return super(Q4V17Strategy, self)._scan_points()

    def _localize_and_clear(self, client, state, channel: int) -> None:
        """继承 v14 机制；仍失败时在估计点周围做小环形试探。

        动因：v14 的直接清要求估计点距真值 ≤20m（题面判据）。24 点布局
        下个别种子估计误差 ~23m 就会失败（seed 25 信道 2）；环形试探用
        少量移动/清除代价换取清除率，代价只在失败时支付。
        """
        super(Q4V17Strategy, self)._localize_and_clear(client, state, channel)
        p = self.params
        ch_state = state.channels[channel]
        if ch_state.is_cleared or p.clear_search_points <= 0 or p.clear_search_radius_m <= 0:
            return
        est = self._estimate(ch_state)
        if est is None:
            return
        r = float(p.clear_search_radius_m)
        for k in range(int(p.clear_search_points)):
            theta = 2.0 * math.pi * k / int(p.clear_search_points)
            cand = Point(est.x + r * math.cos(theta), est.y + r * math.sin(theta))
            if cand.norm() > ARENA_RADIUS_M * 1.35:
                continue
            self._move(state, cand)
            if self._clear(client, state, cand, channel, reason="ring"):
                return
