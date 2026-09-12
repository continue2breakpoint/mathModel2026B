# -*- coding: utf-8 -*-
"""2026 CUMCM B题 问题3/4 —— 自建带真值离线仿真环境（Offline Simulator）。

为什么需要它：官方"演练测试"结束后只反馈【干扰源个数、全向/定向个数】，不返回
逐源坐标、有效半径、定向方向；正式测试更不反馈真值。因此真源包含率、定位误差、
朝向估计误差、全向/定向判别正确率这些指标，只能在自建、可控、带真值的环境里评估。

本模块严格对齐《B题.pdf》正文与附件1（模拟器说明）、附件2（通信接口）的规则：
  * 区域 Ω：半径 1800 圆盘，机器狗从 (0,0) 出发，东 x 北 y，允许移动到 Ω 外；
  * 20 频道，每源独占一个固定互异频道（频道即身份，无 data association）；
  * 有效接收半径 R∈[1000,1500] 因源而异、策略不可见；
  * 示向度误差全局有界 [-1°,+1°]；【同一检测点对同一频道重复测量误差固定，换点才变】；
    返回值保留两位小数（先加内部误差再四舍五入到 0.01，量化半宽 0.005°）；
  * 全向源 360°；定向源仅在朝向 φ 两侧各 90° 的闭前半平面
    ⟨p-G,u(φ)⟩≥0 内有信号，角外无信号；
  * 动作虚拟耗时：移动 d/5（5 m/s，直线）；/measure 频道变化才 +1 切换、检测恒 +5；
    /clear 不切换频道，光学先 +3，成功再 +2（共 5），no_target 只 +3；
  * ≤5m 且在覆盖角内 => near（无示向度，可直接清）；≤20m /clear 必成功且与朝向无关；
  * 同一源只能成功清除一次。

策略代码只允许调用 enter/measure/clear/exit 这四个"接口"，不得直接读 sources 真值；
真值仅供一次测试结束后的离线评估（report_truth）。在线跑官方模拟器时，把这四个
方法原样替换为 HTTP 调用即可（策略内核与后端解耦）。
"""
import math
import random

OMEGA_R = 1800.0
N_CHANNEL = 20
R_MIN, R_MAX = 1000.0, 1500.0
SPEED = 5.0
SWITCH_S = 1.0
MEASURE_S = 5.0
CLEAR_OK_S = 5.0      # 光学 3 + 激光 2
CLEAR_MISS_S = 3.0    # 仅光学
NEAR_R = 5.0
CLEAR_R = 20.0
BEARING_BOUND = 1.0  # ±1°
REAL_BUDGET_S = 1200.0
VIRTUAL_BUDGET_S = 360000.0


class Jammer:
    __slots__ = ("channel", "g", "R", "kind", "phi", "cleared", "clear_at", "clear_tv")

    def __init__(self, channel, g, R, kind, phi=None):
        self.channel = channel          # 1..20
        self.g = g                      # 真值坐标 (x,y)
        self.R = R                      # 有效接收半径
        self.kind = kind                # 'omni' / 'dir'
        self.phi = phi                  # 定向朝向（弧度），全向为 None
        self.cleared = False
        self.clear_at = None
        self.clear_tv = None

    def in_coverage_angle(self, p):
        """点 p 是否落在该源的有效覆盖角内（距离判定由调用方负责）。"""
        if self.kind == "omni":
            return True
        ux, uy = math.cos(self.phi), math.sin(self.phi)
        return (p[0] - self.g[0]) * ux + (p[1] - self.g[1]) * uy >= -1e-9


class SimEnv:
    """离线仿真环境，接口语义与官方 HTTP 后端一一对应。"""

    def __init__(self, sources, seed_measure=0, quantize=True, bearing_extra=0.0):
        self.sources = sources                       # list[Jammer]（真值，策略禁读）
        self._by_ch = {j.channel: j for j in sources}
        self.quantize = quantize                     # svd 是否保留两位小数
        self.bearing_extra = bearing_extra           # 额外误差半宽（灵敏度：0.005 量化保守口径）
        self._rng = random.Random(seed_measure)
        self._err_fixed = {}                         # (x,y,ch)->固定误差，实现"同点误差固定"
        self.reset()

    # ---------- 生命周期 ----------
    def reset(self):
        self.pos = (0.0, 0.0)
        self.cur_channel = 1
        self.tv = 0.0
        self.real_t = 0.0
        self.n_request = 0
        self.trace = []                             # 动作流水（出时间线图用）
        for j in self.sources:
            j.cleared, j.clear_at, j.clear_tv = False, None, None
        self._err_fixed.clear()
        self.closed = False

    def enter(self):
        self.reset()
        return {"accepted": True, "virtual_time_s": 0.0,
                "remaining_real_duration_s": REAL_BUDGET_S}

    def exit(self):
        self.closed = True
        return {"accepted": True, "exit_reason": "user_exit", "virtual_time_s": self.tv}

    # ---------- 内部：移动 / 计时 ----------
    def _move(self, p):
        d = math.hypot(p[0] - self.pos[0], p[1] - self.pos[1])
        self.tv += d / SPEED
        self.real_t += 0.0                          # 离线不模拟墙钟（移动不占现实）
        self.pos = (float(p[0]), float(p[1]))
        return d

    def _fixed_error(self, ch):
        key = (round(self.pos[0], 3), round(self.pos[1], 3), ch)
        e = self._err_fixed.get(key)
        if e is None:
            bound = BEARING_BOUND + self.bearing_extra
            e = self._rng.uniform(-bound, bound)
            self._err_fixed[key] = e
        return e

    # ---------- 测量 ----------
    def measure(self, x, y, ch):
        assert 1 <= ch <= 20
        self.n_request += 1
        d_move = self._move((x, y))
        switched = 0
        if ch != self.cur_channel:
            self.tv += SWITCH_S
            self.cur_channel = ch
            switched = 1
        self.tv += MEASURE_S
        j = self._by_ch.get(ch)
        result = {"accepted": True, "virtual_time_s": self.tv}
        # 判定能否收到：未清 + 距离≤R + 在覆盖角内
        receivable = False
        dist_g = None
        if j is not None and not j.cleared:
            dist_g = math.hypot(self.pos[0] - j.g[0], self.pos[1] - j.g[1])
            if dist_g <= j.R + 1e-9 and j.in_coverage_angle(self.pos):
                receivable = True
        if not receivable:
            result["measure_result"] = "no_signal"
            self.trace.append(("measure", ch, "no_signal", self.tv, d_move, switched))
            return result
        if dist_g <= NEAR_R + 1e-9:
            result["measure_result"] = "near"      # 信号太强，无示向度
            self.trace.append(("measure", ch, "near", self.tv, d_move, switched))
            return result
        true_b = math.degrees(math.atan2(j.g[1] - self.pos[1], j.g[0] - self.pos[0])) % 360.0
        e = self._fixed_error(ch)
        svd = (true_b + e) % 360.0
        if self.quantize:
            svd = round(svd, 2)                    # 保留两位小数
        result["measure_result"] = "direction"
        result["svd_deg"] = svd
        self.trace.append(("measure", ch, "direction", self.tv, d_move, switched))
        return result

    # ---------- 清除 ----------
    def clear(self, x, y, ch):
        self.n_request += 1
        d_move = self._move((x, y))
        # 注意：/clear 不切换测向机频道、不产生切换耗时
        j = self._by_ch.get(ch)
        ok = False
        if j is not None and not j.cleared:
            dist_g = math.hypot(self.pos[0] - j.g[0], self.pos[1] - j.g[1])
            if dist_g <= CLEAR_R + 1e-9:           # 20m 内必成功，与朝向无关
                ok = True
        if ok:
            self.tv += CLEAR_OK_S
            j.cleared = True
            j.clear_at = (self.pos[0], self.pos[1])
            j.clear_tv = self.tv
            out = {"accepted": True, "clear_result": "success", "virtual_time_s": self.tv}
            self.trace.append(("clear", ch, "success", self.tv, d_move, 0))
            return out
        self.tv += CLEAR_MISS_S
        out = {"accepted": True, "clear_result": "no_target_in_range", "virtual_time_s": self.tv}
        self.trace.append(("clear", ch, "no_target", self.tv, d_move, 0))
        return out

    # ---------- 真值评估（仅供赛后离线打分，策略禁调） ----------
    def truth(self):
        return [dict(channel=j.channel, g=j.g, R=j.R, kind=j.kind, phi=j.phi,
                     cleared=j.cleared) for j in self.sources]

    def n_total(self):
        return len(self.sources)

    def n_cleared(self):
        return sum(1 for j in self.sources if j.cleared)


# ---------------- 真值场景生成 ----------------
def gen_sources(rng, n_min=10, n_max=16, directional=False, dir_ratio_hi=0.5,
                r_lo=R_MIN, r_hi=R_MAX, omega=OMEGA_R):
    """随机生成一个合法案例：源数随机、频道互异、位置在 Ω 内均匀、R∈[1000,1500]。

    directional=False：全部全向（问题3）；True：按比例混入定向源（问题4）。
    """
    n = rng.randint(n_min, n_max)
    chans = rng.sample(range(1, N_CHANNEL + 1), n)         # 互异频道
    srcs = []
    for c in chans:
        rr = math.sqrt(rng.random()) * omega              # 圆盘内均匀
        aa = rng.uniform(0, 2 * math.pi)
        g = (rr * math.cos(aa), rr * math.sin(aa))
        R = rng.uniform(r_lo, r_hi)
        if directional and rng.random() < dir_ratio_hi:
            phi = rng.uniform(0, 2 * math.pi)
            srcs.append(Jammer(c, g, R, "dir", phi))
        else:
            srcs.append(Jammer(c, g, R, "omni", None))
    return srcs


if __name__ == "__main__":
    # 最小自检：随机一个全向案例，原点对全部频道 measure 一遍并打印
    rng = random.Random(0)
    env = SimEnv(gen_sources(rng))
    env.enter()
    hit = 0
    for c in range(1, 21):
        r = env.measure(0, 0, c)
        if r["measure_result"] == "direction":
            hit += 1
    print("源数=%d，原点能收到 direction 的频道=%d，虚拟时间=%.1f" %
          (env.n_total(), hit, env.tv))
    print("单点扫全20频道理论耗时 19*1+20*5=119，实际=%.1f" % env.tv)
