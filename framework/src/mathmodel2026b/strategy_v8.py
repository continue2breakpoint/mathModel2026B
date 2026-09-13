"""问题3 的 v8：在 v7 的合并调度上，先"直接清"再"精定位"。

观察（``_diag_v6b.py`` 实测）：两次扫描读数（检测点相距约 1km、误差仅 ±1°）交会
出来的估计位置，与真值的偏差**多数在 0~30m**，而清除判据是"清除点距真值 ≤20m"。
也就是说相当一部分源根本不需要再精定位：走到估计点直接 ``/clear`` 就能命中。

基线（以及 v5~v7）的 ``_localize_and_clear`` 是"先看最小包围圆 MEC ≤20m 才清，
否则先去别处补一条读数"——但 **MEC>20m 不等于估计点偏离 >20m**（可行域可能是
一条细长条，长轴几十米而质心几乎在真值上）。于是白白多走一趟"补读数"的路
（每源最多 250m 位移 + 一次 5s 检测）。

v8 改成：
    1) 走到估计点，直接 ``/clear``（失败也只花 5s，与一次检测同价）；
    2) 失败才就地补一条读数，再走基线的精定位流程。
命中率按实测估计误差分布约 6 成，期望省下"每源一次绕行 + 一次检测"。
"""

# NOTE(端口适配): 用显式两参 super(Cls, self) 而不是零参 super()。
# @dataclass(slots=True) 会为子类重建类对象，零参 super() 依赖的 __class__
# 单元格会指向**原始**类，于是 'Q3V8Params' 实例在 Q3V5Params 的方法里调用
# super() 时会抛 TypeError（CPython 文档 dataclasses 章节明确警告）。
from __future__ import annotations

from dataclasses import dataclass

from .client import SimulatorClient
from .state import DogState
from .strategy_v7 import Q3V7Params, Q3V7Strategy


@dataclass(slots=True)
class Q3V8Params(Q3V7Params):
    #: 到估计点先直接试一次 /clear（失败仅损失 5s，与一次检测同价）
    try_direct_clear: bool = True
    #: 扫描时按距离上界剪枝（可证明安全，见 Q3V5Params.prune_by_range）
    prune_by_range: bool = True


class Q3V8Strategy(Q3V7Strategy):
    """v8：直接清优先，失败再精定位。"""

    def __init__(self, params: Q3V8Params | None = None) -> None:
        self.params: Q3V8Params = params or Q3V8Params()
        super(Q3V8Strategy, self).__init__(self.params)

    def _localize_and_clear(self, client: SimulatorClient, state: DogState, channel: int) -> None:
        ch_state = state.channels[channel]
        if ch_state.is_cleared:
            return
        if self.params.try_direct_clear:
            est = self._estimate(ch_state)
            if est is not None:
                self._move(state, est)
                if self._clear(client, state, est, channel, reason="direct"):
                    self.direct_hits = getattr(self, "direct_hits", 0) + 1
                    return
                self.direct_miss = getattr(self, "direct_miss", 0) + 1
                # 就地补一条读数，让可行域收缩后再交给常规精定位流程
                self._measure(client, state, est, channel, reason="direct-fail")
        # 扩展点（默认空操作）：v18 在这里插入可证明的覆盖式清除
        if self._after_direct_clear_failed(client, state, channel):
            return
        super(Q3V8Strategy, self)._localize_and_clear(client, state, channel)
