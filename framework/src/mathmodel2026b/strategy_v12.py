"""问题3 的 v12：单读数猜距调优。

v8 对单读数频道用"沿方位线 800m"当估计点。但单读数源几乎都处在检测停点的
"独占区"（别的停点看不见它）——对六边形@1130 布局，独占区在场地边缘的
六个弓形里，源到停点的距离典型 ~950-1200m。猜 800m 系统性偏近：

    猜点离真值越远 → 共线补测的视差角越小 → 顺轴误差被 1° 噪声放大越多。

v12 把猜距做成参数 ``single_reading_guess_m``（默认 1000），
并允许"有第二读数来源就照常"的 v8 流程不变。
"""
# NOTE(端口适配): 用显式两参 super(Cls, self) 而不是零参 super()。
# @dataclass(slots=True) 会为子类重建类对象，零参 super() 依赖的 __class__
# 单元格会指向**原始**类，于是 'Q3V8Params' 实例在父类的方法里调用
# super() 时会抛 TypeError（CPython 文档 dataclasses 章节明确警告）。
from __future__ import annotations

from dataclasses import dataclass

from .geometry import Point, polygon_centroid, unit_from_deg
from .state import ChannelState, DogState
from .strategy_v8 import Q3V8Params, Q3V8Strategy


@dataclass(slots=True)
class Q3V12Params(Q3V8Params):
    #: 单读数频道的猜距（米）
    single_reading_guess_m: float = 1000.0


class Q3V12Strategy(Q3V8Strategy):
    """v12：单读数猜距 1000（贴近独占区源的真实距离分布）。"""

    def __init__(self, params: Q3V12Params | None = None) -> None:
        self.params: Q3V12Params = params or Q3V12Params()
        super(Q3V12Strategy, self).__init__(self.params)

    def _estimate(self, ch_state: ChannelState) -> Point | None:
        if len(ch_state.readings) == 1:
            # 恰好 1 条读数：楔形质心 ≈ 800m，但独占区源真实距离 ~950-1200m。
            # 直接沿方位线取 guess_m。
            apex, bearing = ch_state.readings[-1]
            d = self.params.single_reading_guess_m
            return Point(
                apex.x + d * unit_from_deg(bearing).x,
                apex.y + d * unit_from_deg(bearing).y,
            )
        return super(Q3V12Strategy, self)._estimate(ch_state)
