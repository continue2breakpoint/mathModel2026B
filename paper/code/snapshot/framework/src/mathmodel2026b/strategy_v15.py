"""问题3 的 v15：v8 之上两个结构改进。

1. **no_signal 排除估计**（正确性）：全向源在点 B 处 no_signal ⟹ |B−G| > R ≥ 1000
   （附录2-2 有效接收半径下界），即真值必在 disk(B, 1000) **之外**。基线的可行域
   只用 direction 读数（楔形 ∩ 1500m 圆盘 ∪ 场域），完全没用这批排除信息——
   1 读数信道的楔形质心可偏离真值数百米，调度与直接清都跟着错。

   v15 记录每频道的 no_signal 检测点，估计时在保守可行域上网格采样、剔除
   落入排除圆盘的样本、取幸存样本质心。可行域本身（保守、用于 MEC<=20 判据）
   不变，只是**估计点**更准：调度锚点更准（少走冤枉路）、直接清命中率更高
   （v8 实测 direct 命中 100%，失败 0.54 次/源，每次失败 = 5s + 绕路）。

2. **在线规划 or-opt**：v7 的任务路由只有 2-opt（段反转）；地板脚本用的是
   2-opt + or-opt（段搬家）。v15 给在线重规划补上 or-opt（段长 1-3 搬到
   别处），对"清源任务插进扫描点之间"的交织更敏感。
"""

# NOTE(端口适配): 用显式两参 super(Cls, self) 而不是零参 super()。
# @dataclass(slots=True) 会为子类重建类对象，零参 super() 依赖的 __class__
# 单元格会指向**原始**类，于是 'Q3V8Params' 实例在 Q3V5Params 的方法里调用
# super() 时会抛 TypeError（CPython 文档 dataclasses 章节明确警告）。
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .client import SimulatorClient
from .geometry import Point
from .state import DogState
from .strategy_v5 import _point_poly_min_dist
from .strategy_v8 import Q3V8Params, Q3V8Strategy


@dataclass(slots=True)
class Q3V15Params(Q3V8Params):
    #: 用 no_signal 排除圆盘修正估计点
    use_no_signal_exclusions: bool = True
    #: 排除半径 = 有效接收半径下界（附录2-2）
    exclusion_radius_m: float = 1000.0
    #: 在线规划 or-opt 轮数
    or_opt_passes: int = 4


class Q3V15Strategy(Q3V8Strategy):
    """v15：no_signal 排除估计 + 在线路由 or-opt。"""

    def __init__(self, params: Q3V15Params | None = None) -> None:
        self.params: Q3V15Params = params or Q3V15Params()
        super(Q3V15Strategy, self).__init__(self.params)
        #: channel -> no_signal 检测点列表
        self._no_signal_pts: dict[int, list[Point]] = {}
        #: (channel, gx, gy) 已记录的 no_signal 点（0.5m 网格去重）
        self._no_signal_keys: set[tuple[int, int, int]] = set()
        #: (channel, 读数数, 排除点数) -> 估计点
        self._est_cache: dict[tuple[int, int, int], Point | None] = {}

    # -- 记录 no_signal -----------------------------------------------------
    def _measure(self, client, state, point, channel, reason=""):
        result = super(Q3V15Strategy, self)._measure(client, state, point, channel, reason=reason)
        if self.params.use_no_signal_exclusions and result.kind.value == "no_signal":
            key = (channel, round(point.x * 2.0), round(point.y * 2.0))
            if key not in self._no_signal_keys:
                self._no_signal_keys.add(key)
                self._no_signal_pts.setdefault(channel, []).append(point)
        return result

    # -- 排除修正的估计 ------------------------------------------------------
    def _estimate(self, ch_state) -> Point | None:
        excl = self._no_signal_pts.get(ch_state.channel) or []
        if not excl or not ch_state.readings:
            return super(Q3V15Strategy, self)._estimate(ch_state)
        cache_key = (ch_state.channel, len(ch_state.readings), len(excl))
        if cache_key in self._est_cache:
            return self._est_cache[cache_key]

        region = self._region(ch_state)
        est: Point | None = None
        if len(region) >= 3:
            xs = [p.x for p in region]
            ys = [p.y for p in region]
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            step = max(25.0, max(x1 - x0, y1 - y0) / 64.0)
            r = self.params.exclusion_radius_m
            keep_x: list[float] = []
            keep_y: list[float] = []
            j = 0
            y = y0
            while y <= y1 + 1e-9:
                x = x0
                while x <= x1 + 1e-9:
                    q = Point(x, y)
                    if _point_poly_min_dist(q, region) <= 1e-9:
                        ok = True
                        for e in excl:
                            dx = q.x - e.x
                            dy = q.y - e.y
                            if dx * dx + dy * dy <= r * r:
                                ok = False
                                break
                        if ok:
                            keep_x.append(q.x)
                            keep_y.append(q.y)
                    x += step
                y += step
                j += 1
            if keep_x:
                est = Point(sum(keep_x) / len(keep_x), sum(keep_y) / len(keep_y))
        if est is None:
            est = super(Q3V15Strategy, self)._estimate(ch_state)
        self._est_cache[cache_key] = est
        return est

    # -- 在线路由 or-opt -----------------------------------------------------
    @staticmethod
    def _path_len(start: Point, route: list[tuple[str, int, Point]]) -> float:
        if not route:
            return 0.0
        total = start.distance_to(route[0][2])
        for i in range(len(route) - 1):
            total += route[i][2].distance_to(route[i + 1][2])
        return total

    def _two_opt(self, start, route):
        """2-opt（父类）+ or-opt（搬移长度 1~3 的连续片段）。

        ⚠️ 修正记录（2026-09-13 独立复核 + 本仓库隔离实验）
        ---------------------------------------------------
        原实现把候选路线 ``cand``（片段**插回后**的路线）与 ``rest``（片段
        **删除后**的短路线）比较::

            base = self._path_len(start, rest)          # ← 错的基准
            if self._path_len(start, cand) < base - 1e-9:

        欧氏距离满足三角不等式，删点只会让路线变短或不变、插点只会变长或不变，
        故 ``len(cand) >= len(rest)`` 恒成立 —— 这个判据**几乎不可能接受任何动作**，
        or-opt 等于空转（``v15`` 相对 ``v8`` 的净收益只能归因于 ``no_signal``
        排除区，不能归因于 or-opt）。正确的基准是**本轮搬移前的完整 route**。

        修正后（``or_opt_passes`` > 0 时）种子 61–160 实测：仍 100/100 局全清，
        逐局平均源耗时中位 279.88 → 273.07 s/源，配对总时间平均 −30.89 s/局
        （36 局变快、15 局变慢，非逐案支配）。见 ``docs/review-fixes-2026-09-13.md``。
        """
        route = super(Q3V15Strategy, self)._two_opt(start, route)
        for _ in range(self.params.or_opt_passes):
            improved = False
            n = len(route)
            for seg in (1, 2, 3):
                for i in range(n - seg + 1):
                    piece = route[i : i + seg]
                    rest = route[:i] + route[i + seg :]
                    if not rest:
                        continue
                    base = self._path_len(start, route)  # 搬移前的完整路线
                    for k in range(len(rest) + 1):
                        cand = rest[:k] + piece + rest[k:]
                        if self._path_len(start, cand) < base - 1e-9:
                            route = cand
                            improved = True
                            break
                    if improved:
                        break
                if improved:
                    break
            if not improved:
                break
        return route

    # -- 诊断 ---------------------------------------------------------------
    def diagnostics(self, state: DogState) -> dict[str, Any]:
        data = super(Q3V15Strategy, self).diagnostics(state)
        data["no_signal_pts"] = {c: len(v) for c, v in self._no_signal_pts.items()}
        return data
