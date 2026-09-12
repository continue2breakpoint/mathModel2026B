# -*- coding: utf-8 -*-
"""问题3 策略内核（与后端解耦）：信念状态机 + Q1 几何定位 + 下一最佳观测点
(NBV) + 完备性终止。后端 env 可以是：
  * sim_env.SimEnv（离线带真值仿真，用于验证与统计）；
  * 一个把 measure/clear 映射到官方 HTTP 的同名包装类（正式测试时）。

两种调度模式（互为消融对照）：
  two_stage   : 先沿开放路径完整发现扫描，再逐频道精定位清除（基线，逻辑清晰）；
  interleaved : 发现途中某频道一旦 r_MEC≤20 即就近顺路清除（优化版，省移动）。
完备性终止：七点检测盘并集覆盖 Ω，故全向源必被某点收到；某频道在全部发现点均
no_signal（且从未收到方向）即获"无源证书"判 ABSENT。所有频道进入 CLEARED/ABSENT
才 /exit，可证明不漏清。
"""
import math
import sys
import os
import time as _time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from 问题1_求解 import localization_polygon, welzl_min_enclosing_circle  # noqa
from sim_env import CLEAR_R, R_MIN  # noqa

CLEAR_R = 20.0
MAX_REFINE = 5


class ChannelState:
    __slots__ = ("c", "dirs", "no_sig", "state", "mec", "first_tv", "clear_tv", "n_refine")

    def __init__(self, c):
        self.c = c
        self.dirs = []          # [(pos, svd_deg)]
        self.no_sig = []        # [pos]
        self.state = "unknown"  # unknown/signal/cleared/absent
        self.mec = None         # (cx,cy,r)
        self.first_tv = None
        self.clear_tv = None
        self.n_refine = 0

    def region(self):
        """用 Q1 算子算定位多边形与最小覆盖圆。"""
        if len(self.dirs) < 2:
            return None, None
        obs = [(p[0], p[1], th) for p, th in self.dirs]
        poly = localization_polygon(obs)
        if len(poly) < 3:
            return poly, None
        cx, cy, r = welzl_min_enclosing_circle(poly)
        self.mec = (cx, cy, r)
        return poly, self.mec


def circular_order(chans, cur):
    """从与当前频道 cur 最近的频道开始环形排序，最小化频道切换次数。"""
    chans = sorted(chans)
    if not chans:
        return []
    k = min(range(len(chans)), key=lambda i: abs(chans[i] - cur))
    return chans[k:] + chans[:k]


def _bearing(src, q):
    return math.degrees(math.atan2(src[1] - q[1], src[0] - q[0])) % 360.0


def _ray_intersect(p1, th1, p2, th2):
    a1, a2 = math.radians(th1), math.radians(th2)
    d1 = (math.cos(a1), math.sin(a1))
    d2 = (math.cos(a2), math.sin(a2))
    det = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(det) < math.sin(math.radians(8)):   # 近平行，剔除
        return None
    t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / det
    q = (p1[0] + t * d1[0], p1[1] + t * d1[1])
    if not (5 < t < 2600):                      # 合理截距
        return None
    return q


def robust_center(st):
    """源位置的稳健工作估计：非近平行射线两两交点的坐标中位数
    （比坏几何下巨大的 MEC 圆心更可靠）；仅一条方向时沿方位前进 650。"""
    if len(st.dirs) == 1:
        (px, py), th = st.dirs[0]
        a = math.radians(th)
        return (px + 500 * math.cos(a), py + 500 * math.sin(a))
    xs, ys = [], []
    for i in range(len(st.dirs)):
        for j in range(i + 1, len(st.dirs)):
            q = _ray_intersect(st.dirs[i][0], st.dirs[i][1],
                               st.dirs[j][0], st.dirs[j][1])
            if q:
                xs.append(q[0]); ys.append(q[1])
    if not xs:
        (px, py), th = st.dirs[0]
        a = math.radians(th)
        return (px + 500 * math.cos(a), py + 500 * math.sin(a))
    xs.sort(); ys.sort()
    return (xs[len(xs) // 2], ys[len(ys) // 2])


def _gamma_at(g, p, q):
    v1 = (p[0] - g[0], p[1] - g[1]); v2 = (q[0] - g[0], q[1] - g[1])
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if n1 < 1e-9 or n2 < 1e-9:
        return 180.0
    return math.degrees(math.acos(max(-1, min(1,
        (v1[0]*v2[0]+v1[1]*v2[1])/(n1*n2)))))


def _feasible(q, used, nosig, d_used=120, d_nosig=200):
    if abs(q[0]) > 2e6 or abs(q[1]) > 2e6:
        return False
    if any(math.hypot(q[0]-u[0], q[1]-u[1]) < d_used for u in used):
        return False
    if any(math.hypot(q[0]-z[0], q[1]-z[1]) < d_nosig for z in nosig):
        return False
    return True


def select_nbv(st, prev_r=None, no_sig_strike=0):
    """下一最佳观测点。工作中心选取：定位圆已可靠(MEC r≤300)时以 MEC 圆心为心
    （它比射线交点中位数更准），几何差时才用稳健交点中心。候选点硬约束：
    不重复已访问点、避开已记录的 no_signal 点、对整个定位区域保证收到(d+r≤950)、
    交会角尽量接近 90°。任何情况下都不返回已访问点本身。
    """
    used = [p for p, _ in st.dirs]
    nosig = st.no_sig
    # —— 仅 1 条方向：沿方位保证第 2 点收到（步长 minimax，且避开已访问/无信号点）——
    if len(st.dirs) == 1:
        (px, py), th = st.dirs[0]
        a = math.radians(th)
        for d in ((400, 500, 600, 300, 700) if no_sig_strike else (500, 400, 600, 700, 300)):
            q = (px + d*math.cos(a), py + d*math.sin(a))
            if _feasible(q, used, nosig):
                return q
        return (px + 500*math.cos(a), py + 500*math.sin(a))

    _, mec = st.region()
    if mec is not None and mec[2] <= 300:          # 几何好：以 MEC 圆心为心
        g, rcur = (mec[0], mec[1]), mec[2]
    else:                                          # 几何差：稳健交点中心 + 垂直交会
        g, rcur = robust_center(st), (mec[2] if mec else 1e9)

    def score_candidate(q):
        obs = [(p[0], p[1], th) for p, th in st.dirs] + [(q[0], q[1], _bearing(g, q))]
        poly = localization_polygon(obs)
        if len(poly) < 3:
            return None
        _, _, rr = welzl_min_enclosing_circle(poly)
        gam = min((_gamma_at(g, u, q) for u in used), default=90)
        return rr + abs(gam - 90) * 1.5

    # 几何差时优先加入"最近观测点两侧垂直"候选（Q2 近 90° 交会）
    seeds = []
    if mec is None or mec[2] > 300:
        p_last, th_last = min(st.dirs, key=lambda z: math.hypot(z[0][0]-g[0], z[0][1]-g[1]))
        al = math.radians(th_last); nx, ny = -math.sin(al), math.cos(al)
        for d in (450, 600, 300):
            seeds += [(p_last[0]+d*nx, p_last[1]+d*ny), (p_last[0]-d*nx, p_last[1]-d*ny)]

    # 以 g 为心枚举方位×半径，保证 d+r_cur≤950（对整个定位区域收到）
    radii = [d for d in (300, 450, 600, 750, 880) if d + min(rcur, 900) <= 950]
    if no_sig_strike:
        radii = [d for d in radii if d <= 450] or radii[:1]
    if not radii:
        radii = [300]
    for d in radii:
        for k in range(72):
            a = 2*math.pi*k/72
            seeds.append((g[0]+d*math.cos(a), g[1]+d*math.sin(a)))

    best, best_s = None, None
    for q in seeds:
        if not _feasible(q, used, nosig):
            continue
        s = score_candidate(q)
        if s is None:
            continue
        if best_s is None or s < best_s:
            best_s, best = s, q
    if best is not None:
        return best

    # 兜底：放宽排斥距离，仍保证不返回已访问点本身
    for d in (300, 500, 700):
        for k in range(36):
            a = 2*math.pi*k/36
            q = (g[0]+d*math.cos(a), g[1]+d*math.sin(a))
            if _feasible(q, used, nosig, d_used=30, d_nosig=80):
                return q
    p0 = st.dirs[-1][0]
    return (p0[0]+250, p0[1])   # 确定不与 p0 重合的新点


def _try_clear(env, st, pos=None):
    """移动到 MEC 圆心（或指定点）清除；返回是否成功。"""
    if pos is None:
        _, mec = st.region()
        if mec is None:
            return False
        pos = (mec[0], mec[1])
    resp = env.clear(pos[0], pos[1], st.c)
    if resp.get("clear_result") == "success":
        st.state = "cleared"
        st.clear_tv = resp["virtual_time_s"]
        return True
    return False


def fallback_rect_sweep(env, st):
    """R9 有限兜底（不漏清终止证书）：以最近示向度 β 的测点 p 为原点、β 为 x 轴，
    源必在矩形 [0,1500]×[-30,30]（tan1°·1500≈26.2<30）内；x=0,20,…,1500、
    y=-30,-10,10,30 蛇形共 304 点，任一点到矩形内点 ≤√(10²+10²)<20，≤20m clear
    必成功，成功即停。仅在常规交会收敛失败/动作额度将尽时启用。"""
    if not st.dirs:
        return False
    (px, py), beta = st.dirs[-1]
    cb, sb = math.cos(math.radians(beta)), math.sin(math.radians(beta))
    for ix, x in enumerate(range(0, 1501, 20)):
        ys = (-30.0, -10.0, 10.0, 30.0) if ix % 2 == 0 else (30.0, 10.0, -10.0, -30.0)
        for y in ys:
            q = (px + x*cb - y*sb, py + x*sb + y*cb)
            if _try_clear(env, st, pos=q):
                return True
    return False


def run_q3(env, route, mode="two_stage", verbose=False, max_refine=MAX_REFINE):
    t0 = _time.perf_counter()
    enter = env.enter()
    states = {c: ChannelState(c) for c in range(1, 21)}
    discovery_pts = [p for p in route]
    log = (lambda *a: None) if not verbose else (lambda *a: print(*a, flush=True))

    def undecided():
        return [c for c in range(1, 21) if states[c].state in ("unknown", "signal")]

    # ---------- 阶段1：发现扫描（开放路径，逐点扫未定频道） ----------
    for pos in discovery_pts:
        active = undecided()
        if not active:
            break
        for c in circular_order(active, env.cur_channel):
            st = states[c]
            resp = env.measure(pos[0], pos[1], c)
            mr = resp.get("measure_result")
            if st.first_tv is None:
                st.first_tv = resp["virtual_time_s"]
            if mr == "direction":
                st.dirs.append((pos, resp["svd_deg"]))
                st.state = "signal"
                if mode == "interleaved":
                    _, mec = st.region()
                    if mec is not None and mec[2] <= CLEAR_R:
                        if _try_clear(env, st):
                            log("  ch%d 途中顺路清除 r=%.1f" % (c, mec[2]))
            elif mr == "near":
                # 当前点就在 5m 内，直接清（不额外移动）
                if _try_clear(env, st, pos=pos):
                    log("  ch%d near 直接清除" % c)
            else:
                st.no_sig.append(pos)

    # ---------- 无源证书：发现盘并集覆盖 Ω，全 no_signal 即 ABSENT ----------
    for c, st in states.items():
        if st.state == "unknown" and len(st.no_sig) >= len(discovery_pts):
            st.state = "absent"

    # ---------- 阶段2：逐频道精定位与清除 ----------
    for c in range(1, 21):
        st = states[c]
        if st.state in ("cleared", "absent"):
            continue
        guard, prev_r, strike = 0, None, 0
        while st.state == "signal" and guard < 12:
            guard += 1
            _, mec = st.region()
            if mec is None:
                q = select_nbv(st, no_sig_strike=strike)
                if q is None:
                    break
                resp = env.measure(q[0], q[1], c)
                if resp.get("measure_result") == "direction":
                    st.dirs.append((q, resp["svd_deg"])); strike = 0
                elif resp.get("measure_result") == "near":
                    if _try_clear(env, st, pos=q):
                        break
                else:
                    st.no_sig.append(q); strike += 1
                continue
            r = mec[2]
            if r <= CLEAR_R:
                if _try_clear(env, st):
                    log("  ch%d MEC r=%.1f≤20 一次清除成功" % (c, r))
                    break
                # no_target 兜底：到圆心补测更新信念再定位
                resp = env.measure(mec[0], mec[1], c)
                if resp.get("measure_result") == "direction":
                    st.dirs.append(((mec[0], mec[1]), resp["svd_deg"]))
                elif resp.get("measure_result") == "near":
                    _try_clear(env, st, pos=(mec[0], mec[1]))
                    break
            else:
                st.n_refine += 1
                worse = (prev_r is not None and r > prev_r * 1.05)
                q = select_nbv(st, prev_r=r, no_sig_strike=strike + (1 if worse else 0))
                prev_r = r
                resp = env.measure(q[0], q[1], c)
                mr = resp.get("measure_result")
                if mr == "direction":
                    st.dirs.append((q, resp["svd_deg"])); strike = 0
                elif mr == "near":
                    if _try_clear(env, st, pos=q):
                        break
                else:
                    st.no_sig.append(q); strike += 1   # 记忆 no_signal，NBV 避开
        if st.state == "signal":     # 兜底：MEC 圆心→稳健估计→R9 矩形蛇形（保证必清）
            if not _try_clear(env, st):
                gc = robust_center(st)
                if not _try_clear(env, st, pos=gc):
                    fallback_rect_sweep(env, st)

    exit_resp = env.exit()
    wall = _time.perf_counter() - t0

    # ---------- 统计 ----------
    cleared = [c for c, s in states.items() if s.state == "cleared"]
    absent = [c for c, s in states.items() if s.state == "absent"]
    n_total = env.n_total()
    n_clear = len(cleared)
    tv = exit_resp["virtual_time_s"]
    stat = dict(n_total=n_total, n_cleared=n_clear, n_absent=len(absent),
                cleared_ratio=n_clear / n_total if n_total else 0.0,
                virtual_time_s=tv, mean_locate_clear_s=tv / n_clear if n_clear else None,
                wall_s=wall, n_request=env.n_request,
                refine=[states[c].n_refine for c in cleared])

    # ---------- 离线真值核对（在线后端无 truth，自动跳过） ----------
    truth = getattr(env, "truth", None)
    if callable(truth):
        real = {j["channel"] for j in env.truth() if not j["cleared"] is None}
        true_ch = {j["channel"] for j in env.truth()}
        stat["truth_channels"] = sorted(true_ch)
        stat["missed"] = sorted(true_ch - set(cleared))           # 漏清（有源没清掉）
        stat["false_absent"] = sorted(true_ch & set(absent))      # 把有源误判无源
        stat["all_cleared"] = len(stat["missed"]) == 0
    return stat, states


if __name__ == "__main__":
    import random
    from sim_env import SimEnv, gen_sources
    from coverage import seven_points, open_route_7, RECOMMENDED_RHO

    rng = random.Random(2026)
    rho = RECOMMENDED_RHO
    route, L = open_route_7(rho)
    print("开放路径 %d 点、长 %.1f m" % (len(route), L))
    for mode in ("two_stage", "interleaved"):
        srcs = gen_sources(random.Random(2026))   # 同一案例对比两模式
        env = SimEnv(srcs, seed_measure=11)
        stat, _ = run_q3(env, route, mode=mode, verbose=False)
        print("\n[%s] 源数=%d 清除=%d 比例=%.3f 漏清=%s 误判无源=%s"
              % (mode, stat["n_total"], stat["n_cleared"], stat["cleared_ratio"],
                 stat.get("missed"), stat.get("false_absent")))
        print("       虚拟总时间=%.1f s 平均定位清除=%.1f s/个 请求=%d 现实墙钟=%.3f s"
              % (stat["virtual_time_s"], stat["mean_locate_clear_s"],
                 stat["n_request"], stat["wall_s"]))
