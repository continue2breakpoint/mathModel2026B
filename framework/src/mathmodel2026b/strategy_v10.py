"""问题3 的 v10：单读数频道的"侧向补测"，替代 v8 的"共线猜点直接清"。

插桩（``_diag_clears.py``，10 seeds）发现 v8 的直接清有两副面孔：

    readings=2: 63 次，命中 86%（估计误差 ~13m）
    readings=1: 66 次，命中  3%（估计 = 沿方位角猜 800m，误差 ~230m）

一半的直接清是"单读数抽奖"：走到猜点 → /clear 必败（5s）→ 在猜点补测。
但猜点就在方位线上，第二条方位线与第一条**近乎平行**，交会的顺轴误差
被 1° 噪声放大到百米级，随后只能靠 30/60/120/250m 逼近环慢慢磨——
这是清源段超出理想联合 TSP 行程（~2600m/案例）的主要来源。

v10 对策：频道只有 1 条读数时**不直接清**，先到"侧向偏置点"补一条
与原方位线成 ~60-90° 分离角的读数（从 800m 猜点沿垂线向场地中心侧
偏 400m，距真值 ≤ ~570m，必在有效半径内），然后走 v8 流程
（此时有 2 条好几何读数，直接清命中 ~86%）。

    单读数: 猜点直接清(5s 必败) + 共线补测(顺轴误差 ~100m) + 逼近环(~300-600m)
    v10   : 侧向补测(400m 偏置 + 6s) + 2 读数直接清(86%)
"""
# NOTE(端口适配): 用显式两参 super(Cls, self) 而不是零参 super()。
# @dataclass(slots=True) 会为子类重建类对象，零参 super() 依赖的 __class__
# 单元格会指向**原始**类，于是 'Q3V8Params' 实例在父类的方法里调用
# super() 时会抛 TypeError（CPython 文档 dataclasses 章节明确警告）。
from __future__ import annotations

from dataclasses import dataclass

from .client import SimulatorClient
from .geometry import Point, unit_from_deg
from .protocol import ARENA_RADIUS_M
from .state import DogState
from .strategy_v8 import Q3V8Params, Q3V8Strategy


@dataclass(slots=True)
class Q3V10Params(Q3V8Params):
    #: 单读数频道是否走侧向补测（False = 完全退回 v8 行为）
    side_probe: bool = True
    #: 侧向偏置距离（米）：太小分离角不足，太大浪费行程
    side_offset_m: float = 400.0
    #: 侧向补测前先沿方位线走到 800m 猜点（保留逼近进度）
    side_probe_via_guess: bool = True


class Q3V10Strategy(Q3V8Strategy):
    """v10：单读数先侧向补测，再直接清。"""

    def __init__(self, params: Q3V10Params | None = None) -> None:
        self.params: Q3V10Params = params or Q3V10Params()
        super(Q3V10Strategy, self).__init__(self.params)
        self.side_probes = 0

    def _side_probe_point(self, ch_state) -> Point | None:
        """从最后一条读数出发：800m 猜点 + 垂直向场地中心偏 side_offset_m。"""
        if not ch_state.readings:
            return None
        apex, bearing = ch_state.readings[-1]
        u = unit_from_deg(bearing)
        est = Point(apex.x + 800.0 * u.x, apex.y + 800.0 * u.y)
        # 垂直方向两个候选，取朝场地中心的一侧（保证不出界）
        for sign in (1.0, -1.0):
            perp = Point(-u.y * sign, u.x * sign)
            q = Point(est.x + self.params.side_offset_m * perp.x,
                      est.y + self.params.side_offset_m * perp.y)
            if q.norm() <= ARENA_RADIUS_M:
                return q
        # 两个侧向都出界（极端靠边），退回猜点本身
        return est

    def _localize_and_clear(self, client: SimulatorClient, state: DogState, channel: int) -> None:
        ch_state = state.channels[channel]
        if ch_state.is_cleared:
            return
        p = self.params
        if p.try_direct_clear and p.side_probe and len(ch_state.readings) < 2:
            q = self._side_probe_point(ch_state)
            if q is not None and state.position.distance_to(q) > 1.0:
                self._move(state, q)
                self._measure(client, state, q, channel, reason="side-probe")
                self.side_probes += 1
        # 走 v8 流程：此时要么已有 >=2 条读数（直接清 86%），
        # 要么侧向点不可达（原样退回 v8 的猜点直接清 + 共线补测）。
        super(Q3V10Strategy, self)._localize_and_clear(client, state, channel)
