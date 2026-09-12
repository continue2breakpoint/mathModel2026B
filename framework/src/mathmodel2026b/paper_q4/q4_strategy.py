# -*- coding: utf-8 -*-
"""问题4 策略内核（与后端解耦）：在问题3基础上增加"全向/定向辨识 + 朝向估计"。

流程（每频道信念状态）：
  发现：等边三角网格(s=1000,31点,定向发现完备)沿贪心最近邻开放路径扫描，
        并集覆盖 Ω 且对任意朝向存在正面近距离点，故全部网格点 no_signal ⇔ 无源(ABSENT)；
  定位：用正面点示向度做 Q1 楔形交会，NBV 补在"当前朝向弧正面一侧"以保证收到、交会良好；
  辨识：定位圆足够可靠后 classify——ambiguous 布 4 个盘内均布探针（全收到=全向，
        出现盘内 no_signal=定向）；定向再在朝向弧边界补探针把朝向收窄到目标半宽；
  清除：MEC r≤20 即清（≤20m 与朝向无关），类型/朝向作为结果一并输出。
"""
import math
import os
import sys
import time as _time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from 问题1_求解 import localization_polygon, welzl_min_enclosing_circle  # noqa
from sim_env import CLEAR_R
from coverage import (triangular_grid, hamiltonian_route_axial, axial_grid_nodes,
                      axial_to_xy)
from q3_strategy import (robust_center, _gamma_at, _bearing, _feasible,
                         circular_order, ChannelState)
from q4_identify import classify, probe_points, bearing as bear

CLEAR_R = 20.0
PHI_TARGET_HALF = 15.0     # 朝向半宽收敛目标（度）


class Q4State(ChannelState):
    __slots__ = ("kind", "phi_hat", "phi_half", "probed", "ring_tries")

    def __init__(self, c):
        super().__init__(c)
        self.kind = "unknown"     # unknown/ambiguous/omni/dir
        self.phi_hat = None
        self.phi_half = None
        self.probed = []
        self.ring_tries = 0       # 整环探测轮数硬预算，防止不收敛

    def region(self):
        if len(self.dirs) < 2:
            return None, None
        obs = [(p[0], p[1], th) for p, th in self.dirs]
        poly = localization_polygon(obs)
        if len(poly) < 3:
            return poly, None
        cx, cy, r = welzl_min_enclosing_circle(poly)
        self.mec = (cx, cy, r)
        return poly, self.mec


def nearest_neighbor_route(points, start=(0.0, 0.0)):
    """从 start 出发的贪心最近邻开放路径（发现遍历）。"""
    remain = list(points)
    route, cur = [], start
    while remain:
        j = min(range(len(remain)), key=lambda i: math.hypot(remain[i][0]-cur[0], remain[i][1]-cur[1]))
        cur = remain.pop(j)
        route.append(cur)
    L = math.hypot(route[0][0]-start[0], route[0][1]-start[1])
    L += sum(math.hypot(route[i+1][0]-route[i][0], route[i+1][1]-route[i][1])
             for i in range(len(route)-1))
    return route, L


def front_nbv(st, g, rcur, no_sig_strike=0):
    """在源正面一侧枚举补点（保证收到且交会良好）；朝向未知时退回全向枚举。"""
    used = st.dirs and [p for p, _ in st.dirs] or []
    nosig = st.no_sig
    phi_lo, phi_hi = None, None
    if st.phi_hat is not None:
        c = st.phi_hat
        phi_lo, phi_hi = math.radians(c - 70), math.radians(c + 70)   # 正面内部
    best, best_s = None, None
    radii = [d for d in (300, 450, 600, 750) if d + min(rcur, 850) <= 950] or [300]
    if no_sig_strike:
        radii = [d for d in radii if d <= 450] or radii[:1]
    ang = [2*math.pi*k/72 for k in range(72)]
    for d in radii:
        for a in ang:
            if phi_lo is not None:
                w = (a - math.radians(st.phi_hat) + math.pi) % (2*math.pi) - math.pi
                if abs(w) > math.radians(70):
                    continue
            q = (g[0]+d*math.cos(a), g[1]+d*math.sin(a))
            if not _feasible(q, used, nosig):
                continue
            obs = [(p[0], p[1], th) for p, th in st.dirs] + [(q[0], q[1], _bearing(g, q))]
            poly = localization_polygon(obs)
            if len(poly) < 3:
                continue
            _, _, rr = welzl_min_enclosing_circle(poly)
            gam = min((_gamma_at(g, u, q) for u in used), default=90)
            s = rr + abs(gam-90)*1.5
            if best_s is None or s < best_s:
                best_s, best = s, q
    if best:
        return best
    # 兜底：沿最近正面点方位前进
    if st.dirs:
        (px, py), th = st.dirs[-1]
        al = math.radians(th)
        return (px+400*math.cos(al), py+400*math.sin(al))
    return g


def _ring_probe(env, st, c, g, radius, n=12):
    """以 g 为心、radius 为半径整环均布探测，用于打破"朝向初估错→正面补点全落背面"
    的死锁：环上落在真实正面的点会 signal，同时改善定位与朝向。返回新增正面点数。"""
    used = [p for p, _ in st.dirs]
    added = 0
    for k in range(n):
        a = 2*math.pi*k/n
        q = (g[0]+radius*math.cos(a), g[1]+radius*math.sin(a))
        if not _feasible(q, used, st.no_sig, 40, 40):
            continue
        mr, _ = _measure_into(env, st, q, c)
        if mr in ("direction", "near"):
            added += 1
    return added


def _measure_into(env, st, q, c):
    """在 q 测量并更新信念，返回 measure_result。"""
    resp = env.measure(q[0], q[1], c)
    mr = resp.get("measure_result")
    if mr == "direction":
        st.dirs.append((q, resp["svd_deg"])); st.state = "signal"
    elif mr == "no_signal":
        st.no_sig.append(q)
    return mr, resp


def run_q4(env, grid_s=950.0, verbose=False, max_iter=60):
    t0 = _time.perf_counter()
    env.enter()
    states = {c: Q4State(c) for c in range(1, 21)}
    grid = axial_grid_nodes(grid_s)
    discovery, Ld = hamiltonian_route_axial(grid_s)   # 31点、相邻1000、长30000（R3最短）
    log = (lambda *a: None) if not verbose else (lambda *a: print(*a, flush=True))

    def undecided():
        return [c for c in range(1, 21) if states[c].state in ("unknown", "signal")]

    # ---------- 发现：轴向31点定向完备扫描 ----------
    # 检测5s只增虚拟时间(上限360000s)、不占现实1200s墙钟(只含HTTP往返+计算)，
    # 故已发现(signal)频道在顺路点继续观测，自然积累多方向正面交会，提升定位/朝向精度。
    for pos in discovery:
        active = undecided()
        if not active:
            break
        for c in circular_order(active, env.cur_channel):
            st = states[c]
            mr, resp = _measure_into(env, st, pos, c)
            if mr == "near":
                _clear(env, st, pos)
    for c, st in states.items():
        if st.state == "unknown" and len(st.no_sig) >= len(discovery):
            st.state = "absent"; st.kind = "absent"

    # ---------- 定位 + 辨识 + 清除 ----------
    for c in range(1, 21):
        st = states[c]
        if st.state in ("cleared", "absent"):
            continue
        it, strike = 0, 0
        while st.state == "signal" and it < max_iter:
            it += 1
            _, mec = st.region()
            if mec is None:
                # 仅一条示向度：以沿射线上的点为心做整环探测，环点多方位→交会角好
                # （沿同射线补点会共线、坏几何）；两圈(远/近)兼顾保证收到
                if len(st.dirs) == 1:
                    if st.ring_tries >= 4:
                        break     # 环探测预算用尽，交末尾 R9 兜底保证清除
                    (px, py), th = st.dirs[0]; al = math.radians(th)
                    added = 0
                    # 第一圈：以发现点自身为心（治"发现点已很近、沿射线会越过源到背面"）
                    added += _ring_probe(env, st, c, (px, py), 300.0, n=6)
                    # 第二圈：沿指向源射线推进后为心（治"发现点距源约 R、较远"）
                    if len(st.dirs) < 3:
                        step = 700.0 + 200.0*min(strike, 2)
                        base = (px+step*math.cos(al), py+step*math.sin(al))
                        added += _ring_probe(env, st, c, base, 350.0, n=6)
                    st.ring_tries += 1
                    if added == 0:
                        strike += 1
                continue
            g, r = (mec[0], mec[1]), mec[2]
            # 位置足够可靠后做辨识（朝向半宽叠加定位不确定度 r）
            if r < 250 and st.kind in ("unknown", "ambiguous"):
                _identify(env, st, c, g, delta=r)
            # 交会几何是否充分：MEC 收敛即 G 已准（≥3 方向，或两点交会但 MEC 极小）
            geo_ok = r <= 20.0 and (len(st.dirs) >= 3 or r <= 10.0)
            if st.kind == "dir":
                if not geo_ok:
                    if strike >= 2 and st.ring_tries < 4:   # 连续落空：整环探测重找正面
                        rad = [300.0, 500.0, 750.0][min(strike//2-1, 2)]
                        added = _ring_probe(env, st, c, g, min(rad, max(150, 900-r)), n=12)
                        st.ring_tries += 1
                        strike = 0 if added else strike + 1
                        continue
                    q = front_nbv(st, g, r, strike)       # 正面补点，夯实定位
                    mr, _ = _measure_into(env, st, q, c)
                    if mr == "near" and _refine_and_clear(env, st, c, q, 5):
                        break
                    strike += (1 if mr == "no_signal" else 0)
                    continue
                if st.phi_half is None or st.phi_half > PHI_TARGET_HALF:
                    ph, hf, ok = _refine_heading(env, st, c, g, r)
                    if ok:
                        st.phi_hat, st.phi_half = ph, hf
                    else:   # 探针落空说明 G 仍不准，回到正面补点
                        q = front_nbv(st, g, r, strike)
                        mr, _ = _measure_into(env, st, q, c)
                        if mr == "near" and _refine_and_clear(env, st, c, q, 5): break
                        continue
                if r <= CLEAR_R and st.phi_half is not None and st.phi_half <= PHI_TARGET_HALF*1.4:
                    if _clear(env, st, g):
                        log("ch%d dir 清除 phi=%.1f±%.1f" % (c, st.phi_hat, st.phi_half)); break
            # 全向源：定位收敛即清
            if st.kind == "omni" and r <= CLEAR_R:
                if _clear(env, st, g):
                    log("ch%d omni 清除" % c); break
                mr, _ = _measure_into(env, st, g, c)
                if mr == "near" and _clear(env, st, g): break
                continue
            # 其余情况（unknown/ambiguous 或定向尚未收敛）：正面补点
            q = front_nbv(st, g, r, strike)
            mr, _ = _measure_into(env, st, q, c)
            if mr == "near":
                _identify(env, st, c, q, delta=5.0)
                if _refine_and_clear(env, st, c, q, 5): break
            elif mr == "no_signal":
                strike += 1
            else:
                strike = 0
        if st.state == "signal":     # R9 有限兜底：先精测朝向+快清，失败则矩形蛇形保证必清
            _, mec = st.region()
            g = (mec[0], mec[1]) if mec else robust_center(st)
            if st.kind in ("unknown", "ambiguous"):
                _identify(env, st, c, g, delta=(mec[2] if mec else 300.0))
            if not _refine_and_clear(env, st, c, g, (mec[2] if mec else 30.0)):
                fallback_rect_sweep(env, st, c)

    exit_resp = env.exit()
    return _stats(env, states, exit_resp, t0, Ld, len(discovery)), states


def _clear(env, st, pos):
    resp = env.clear(pos[0], pos[1], st.c)
    if resp.get("clear_result") == "success":
        st.state = "cleared"; st.clear_tv = resp["virtual_time_s"]; return True
    return False


def _refine_and_clear(env, st, c, G, r=20.0):
    """统一清除入口：定向源清除前若朝向尚未精测，先用当前（已较准的）G 圆周精测再清；
    near 场景 G 误差≤5m，圆周探针可直接得到可靠朝向，不依赖多方向交会。"""
    if st.kind == "dir" and (st.phi_half is None or st.phi_half > PHI_TARGET_HALF*1.4):
        ph, hf, ok = _refine_heading(env, st, c, G, max(r, 2.0))
        if ok:
            st.phi_hat, st.phi_half = ph, hf
    return _clear(env, st, G)


def fallback_rect_sweep(env, st, c):
    """R9 有限兜底（保证不漏清的终止证书）：以最近一次示向度 β 的测点 p 为原点、
    β 为 x 轴，源必在矩形 [0,1500]×[-30,30]（tan1°·1500≈26.2<30）内；按
    x=0,20,…,1500、y=-30,-10,10,30 蛇形布 clear 点（共304），任一点到矩形内点
    ≤√(10²+10²)<20，≤20m clear 与朝向无关必成功，成功即停。"""
    if not st.dirs:
        return False
    (px, py), beta = st.dirs[-1]
    cb, sb = math.cos(math.radians(beta)), math.sin(math.radians(beta))
    xs = list(range(0, 1501, 20))
    for ix, x in enumerate(xs):
        ys = (-30.0, -10.0, 10.0, 30.0) if ix % 2 == 0 else (30.0, 10.0, -10.0, -30.0)
        for y in ys:
            q = (px + x*cb - y*sb, py + x*sb + y*cb)
            if _clear(env, st, q):
                return True
    return False


def _identify(env, st, c, G, delta=0.0):
    """双假设辨识；ambiguous 时布盘内探针直至分出全向/定向。delta=G 定位不确定半径。"""
    info = classify(G, st.dirs, st.no_sig, delta)
    Rlo = info["Rlo"]
    if info["kind"] in ("dir", "omni"):
        st.kind = info["kind"]; st.phi_hat, st.phi_half = info["phi_hat"], info["phi_half"]
        return
    # ambiguous：盘内 4 探针（间隔90°，定向背面开半平面必含其一；全向则环绕判 omni）
    for q in probe_points(G, Rlo, n=4):
        if not _feasible(q, [p for p, _ in st.dirs], st.no_sig, d_used=30, d_nosig=30):
            continue
        mr, _ = _measure_into(env, st, q, c)
        if mr == "near":
            continue
        info = classify(G, st.dirs, st.no_sig, delta)
        if info["kind"] in ("dir", "omni"):
            st.kind = info["kind"]
            st.phi_hat, st.phi_half = info["phi_hat"], info["phi_half"]
            return
    # 探针盘内全收到且正面点环绕 → 全向
    st.kind = "omni"; st.phi_hat, st.phi_half = None, None


def _refine_heading(env, st, c, G, r, n=36, d=None):
    """定位到 G 足够准(MEC 半径 r)后，在以 G 为心、半径 d 的圆周均布 n 点直接测
    前/后分界：有信号方位的连续段即前半平面，其最小包围弧中点为朝向；
    半宽 = 角间隔/2 + G 定位误差引起的方位偏差 arcsin(r/d) + 正面弧不足180°的余量。"""
    Rlo = max([math.hypot(p[0]-G[0], p[1]-G[1]) for p, _ in st.dirs], default=400.0)
    if d is None:
        d = min(400.0, max(200.0, 0.5 * Rlo))
    used = [p for p, _ in st.dirs]
    sig = []
    for k in range(n):
        al = math.radians(360.0 * k / n)
        q = (G[0] + d*math.cos(al), G[1] + d*math.sin(al))
        # 规则圆周探针相邻间距 2d sin(180/n)>100m，不会重复同误差点，全部实测
        if min((math.hypot(q[0]-p[0], q[1]-p[1]) for p in used), default=1e9) < 8.0:
            continue
        mr, _ = _measure_into(env, st, q, c)
        if mr in ("direction", "near"):
            sig.append(360.0 * k / n)
    if len(sig) < 2:
        return st.phi_hat, st.phi_half, False
    s = sorted(sig)
    gaps = [s[i+1]-s[i] for i in range(len(s)-1)] + [s[0]+360-s[-1]]
    kg = max(range(len(gaps)), key=lambda i: gaps[i])
    # 最大空弧（背面）之后为正面弧起点
    start = s[(kg+1) % len(s)]
    span = 360.0 - gaps[kg]
    # 健康判据：正面应约半个圆周（点数≈n/2、背面空弧≈180°）；否则 G 不准、探针落空
    ok = (len(sig) >= n//3) and (120.0 <= gaps[kg] <= 240.0)
    phi_hat = (start + span/2.0) % 360.0
    half = (360.0/n)/2.0 + math.degrees(math.asin(min(1.0, r/d))) + max(0.0, (180.0-span)/2.0)
    return phi_hat, half, ok


def _stats(env, states, exit_resp, t0, Ld, n_grid):
    cleared = [c for c, s in states.items() if s.state == "cleared"]
    absent = [c for c, s in states.items() if s.state == "absent"]
    n_total = env.n_total()
    tv = exit_resp["virtual_time_s"]
    truth = env.truth()
    # 离线评估：类型辨识正确率、朝向误差、漏清/误判
    type_ok = phi_err = dir_n = 0
    phi_errs = []
    true_ch = {j["channel"]: j for j in truth}
    for c, st in states.items():
        j = true_ch.get(c)
        if j is None:
            continue
        pred = "dir" if st.kind == "dir" else ("omni" if st.kind == "omni" else None)
        if pred == j["kind"]:
            type_ok += 1
        if j["kind"] == "dir" and st.phi_hat is not None:
            dir_n += 1
            e = abs((st.phi_hat - math.degrees(j["phi"]) + 180) % 360 - 180)
            phi_errs.append(e)
    missed = sorted(set(true_ch) - set(cleared))
    false_absent = sorted(set(true_ch) & set(absent))
    return dict(n_total=n_total, n_cleared=len(cleared), n_absent=len(absent),
                cleared_ratio=len(cleared)/n_total if n_total else 0,
                virtual_time_s=tv, mean_locate_clear_s=tv/len(cleared) if cleared else None,
                wall_s=_time.perf_counter()-t0, n_request=env.n_request,
                type_correct=type_ok, type_acc=type_ok/n_total if n_total else 0,
                phi_err_mean=(sum(phi_errs)/len(phi_errs) if phi_errs else None),
                phi_err_max=(max(phi_errs) if phi_errs else None), n_dir=dir_n,
                discovery_len=Ld, n_grid=n_grid,
                missed=missed, false_absent=false_absent,
                all_cleared=len(missed) == 0,
                kinds={c: (states[c].kind, states[c].phi_hat) for c in cleared})


if __name__ == "__main__":
    import random
    from sim_env import SimEnv, gen_sources
    rng = random.Random(2026)
    srcs = gen_sources(random.Random(2026), directional=True, dir_ratio_hi=0.5)
    nd = sum(1 for s in srcs if s.kind == "dir")
    env = SimEnv(srcs, seed_measure=11)
    stat, states = run_q4(env, verbose=False)
    print("源数=%d(定向%d) 清除=%d 类型辨识正确=%d(%.1f%%) 漏清=%s 误判=%s"
          % (stat["n_total"], nd, stat["n_cleared"], stat["type_correct"],
             100*stat["type_acc"], stat["missed"], stat["false_absent"]))
    print("网格点=%d 发现路径=%.0fm 虚拟总时间=%.1f 平均=%.1f 请求=%d"
          % (stat["n_grid"], stat["discovery_len"], stat["virtual_time_s"],
             stat["mean_locate_clear_s"], stat["n_request"]))
    print("定向朝向误差 均值=%.2f° 最大=%.2f°（n=%d）"
          % (stat["phi_err_mean"] or -1, stat["phi_err_max"] or -1, stat["n_dir"]))
