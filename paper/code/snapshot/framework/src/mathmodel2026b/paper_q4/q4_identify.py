# -*- coding: utf-8 -*-
"""问题4 全向/定向类型辨识与朝向估计（确定性双假设可行集，不引入概率模型）。

记号：候选源位置 G；收到 direction 的"正面点" S+=[(p,svd)]；no_signal 点 S-=[p]。
有效半径 R 对策略不可见，但任一正面点 p 都满足 |p-G|≤R，故
    R_lo = max_{p∈S+}|p-G| 是 R 的确定下界。

【全向假设的可证伪性】全向源 360° 有信号，盘 B(G,R) 内必收到。若存在 q∈S- 满足
|q-G|≤R_lo（它确定落在盘内，因为 R≥R_lo≥|q-G|）却 no_signal，则与全向矛盾，
**确定性排除全向、判定为定向**，称 q 为"确定背面点"。

【朝向可行弧】记 u(α) 为朝向单位向量、β(p)=arg(p-G)。
  正面点 p：⟨p-G,u⟩≥0 ⇔ |wrap(α-β(p))|≤90°，即 α 落在以 β(p) 为中心、半宽 90° 的弧；
  背面点 q：⟨q-G,u⟩<0 ⇔ α 落在以 β(q)+180° 为中心、半宽 90° 的弧。
朝向可行集 = 所有这些半宽 90° 弧在圆周上的交集（一段弧 / 空 / 整圈），中点即朝向
估计、半宽即朝向不确定度。

【主动辨识构造】当没有确定背面点（全向尚未被证伪）时，在以 G 为心、半径 d<R_lo 的
圆上均布 n 个"盘内探针"。全向源在每个探针都收到（d<R_lo≤R）；定向源背面是跨度
180° 的开半平面，当 n≥3（间隔 120°<180°）时必有探针落入背面而 no_signal，从而判定
定向并收窄朝向弧。n=4（间隔 90°）更对称，本文采用。
"""
import math


def wrap180(a):
    return (a + 180.0) % 360.0 - 180.0


def bearing(a, b):
    """从 a 指向 b 的方位角（度）。"""
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 360.0


def _arc_feasible(constraints, n_scan=3600, eps=1e-9):
    """圆周半宽弧求交。constraints=[(center_deg, halfwidth_deg)]。
    返回 ('full',None,360) / ('arc',lo,width) / ('empty',None,0)。
    用边界点切分圆周、逐段中点检验，连通可行段合并。"""
    if not constraints:
        return ("full", 0.0, 360.0)
    step = 360.0 / n_scan
    ok = [True] * n_scan
    for c, hw in constraints:
        for i in range(n_scan):
            a = i * step
            if abs(wrap180(a - c)) > hw + 1e-7:
                ok[i] = False
    # 找最长连续可行段（交集若为弧，通常唯一连通；整圈另判）
    if all(ok):
        return ("full", 0.0, 360.0)
    if not any(ok):
        return ("empty", None, 0.0)
    # 环形展开找连续段
    segs, i = [], 0
    doubled = ok + ok
    start = None
    cnt = 0
    i = 0
    while i < 2 * n_scan:
        if doubled[i]:
            if start is None:
                start = i
            cnt += 1
        else:
            if start is not None:
                segs.append((start % n_scan, cnt))
                start, cnt = None, 0
        i += 1
        if start is not None and cnt >= n_scan:
            return ("full", 0.0, 360.0)
    # 取最长段
    segs.sort(key=lambda z: -z[1])
    s0, length = segs[0]
    lo = s0 * step
    width = length * step
    if width >= 360.0 - 2 * step:
        return ("full", 0.0, 360.0)
    return ("arc", lo % 360.0, width)


def _eff_halfwidth(d, delta):
    """G 仅知在估计点 δ 半径内时，测点方位的额外不确定度 arcsin(δ/d)（R11）。
    d≤δ 时方位无小角界，约束退化为整圈（不提供朝向信息）。"""
    if d <= delta + 1e-9:
        return 180.0
    return 90.0 + math.degrees(math.asin(min(1.0, delta / d)))


def halfplane_can_contain(bearings):
    """正面点方位能否被某个闭前半平面（半圆）同时包含 ⇔ 圆周存在≥180°空弧。"""
    if len(bearings) <= 1:
        return True, 360.0
    b = sorted(x % 360 for x in bearings)
    gaps = [b[i+1] - b[i] for i in range(len(b)-1)] + [b[0] + 360 - b[-1]]
    mg = max(gaps)
    return mg >= 180.0 - 1e-7, mg


def classify(G, sp, sm, delta=0.0):
    """双假设辨识。
    G：候选源位置；delta：G 的定位不确定半径(MEC r)，用于朝向保守化(R11)；
    sp=[(p,svd)] 正面点；sm=[p] no_signal 点。
    返回 dict：kind(omni/dir/ambiguous/unknown)、Rlo、弧、phi_hat/phi_half、decisive_back。
    """
    out = dict(kind="unknown", Rlo=None, arc_kind=None, lo=None, width=None,
               phi_hat=None, phi_half=None, decisive_back=[])
    if not sp:
        return out
    Rlo = max(math.hypot(p[0] - G[0], p[1] - G[1]) for p, _ in sp)
    out["Rlo"] = Rlo
    front_cons = [(bearing(G, p),
                   _eff_halfwidth(math.hypot(p[0]-G[0], p[1]-G[1]), delta))
                  for p, _ in sp]
    kind_f, lo_f, w_f = _arc_feasible(front_cons)
    out["front_arc"] = (kind_f, lo_f, w_f)
    # 确定背面点：no_signal 且"对 G 的 δ 不确定度稳健地"落在盘内。
    # 真 G∈B(G,δ)、真 R≥Rlo−δ；q 确定盘内须 |q−G_est|+δ ≤ Rlo−δ，即留 2δ 裕量，
    # 避免把全向源 R 外附近（因定位偏差看似盘内）的 no_signal 误当背面证据。
    decisive = [q for q in sm
                if math.hypot(q[0] - G[0], q[1] - G[1]) <= Rlo - 2.0*delta - 1e-9]
    out["decisive_back"] = decisive
    cons = list(front_cons)
    for q in decisive:
        d = math.hypot(q[0]-G[0], q[1]-G[1])
        cons.append((bearing(G, q) + 180.0, _eff_halfwidth(d, delta)))
    kind_a, lo, w = _arc_feasible(cons)
    out["arc_kind"], out["lo"], out["width"] = kind_a, lo, w
    can_dir, _ = halfplane_can_contain([bearing(G, p) for p, _ in sp])
    if decisive:
        # 全向被确定性反例排除 → 定向
        out["kind"] = "dir"
        if kind_a == "arc":
            out["phi_hat"] = (lo + w / 2.0) % 360.0
            out["phi_half"] = w / 2.0
        elif kind_a == "full":
            out["phi_hat"], out["phi_half"] = None, 180.0
    elif not can_dir:
        # 正面点环绕超过任一前半平面 → 定向假设为空 → 判全向（R11）
        out["kind"] = "omni"; out["phi_hat"] = out["phi_half"] = None
    else:
        # 无反例且正面点可被某半圆包含：全向/定向均可能，需主动探针
        out["kind"] = "ambiguous"
        if kind_f == "arc":
            out["phi_hat"] = (lo_f + w_f / 2.0) % 360.0
            out["phi_half"] = w_f / 2.0
    return out


def probe_points(G, Rlo, n=4, frac=0.6):
    """盘内均布主动探针：半径 d=frac*Rlo（保证 d<R_lo≤R，全向必收到），>5 防 near。"""
    d = max(20.0, min(frac * Rlo, Rlo - 30.0)) if Rlo and Rlo > 40 else 120.0
    return [(G[0] + d * math.cos(2 * math.pi * k / n),
             G[1] + d * math.sin(2 * math.pi * k / n)) for k in range(n)]


def front_direction(G, info, fallback_bearing=None):
    """返回当前最可能的正面方位（朝向弧中点），用于在正面补点以改善定位。"""
    if info.get("phi_hat") is not None:
        return info["phi_hat"]
    if fallback_bearing is not None:
        return fallback_bearing
    return 0.0


if __name__ == "__main__":
    # 单元自测：构造已知场景
    # 1) 全向源：盘内点都有信号、无 no_signal -> ambiguous（无反例，不能误判定向）
    G = (300.0, 400.0)
    sp = [((-200.0, 400.0), 0.0), ((300.0, -100.0), 90.0)]
    info = classify(G, sp, [])
    print("全向场景(无背面反例):", info["kind"], "Rlo=%.0f" % info["Rlo"])
    # 2) 定向源朝东(0°)：西侧盘内点 no_signal -> 判定向，朝向弧含 0°
    G = (0.0, 0.0)
    sp = [((500.0, 300.0), 0), ((500.0, -300.0), 0)]     # 东侧正面
    sm = [(-500.0, 0.0)]                                 # 西侧盘内无信号
    info = classify(G, sp, sm)
    print("定向朝东场景:", info["kind"], "朝向估计=%.1f±%.1f° 弧宽=%.1f 确定背面点数=%d"
          % (info["phi_hat"], info["phi_half"], info["width"], len(info["decisive_back"])))
    # 3) 探针：3 探针间隔120、背面开半平面180必含其一的数值验证
    import random
    rng = random.Random(1)
    cover_ok = True
    for _ in range(2000):
        phi = rng.uniform(0, 360)
        ux, uy = math.cos(math.radians(phi)), math.sin(math.radians(phi))
        ps = probe_points((0, 0), 500, n=3)
        # 定向：是否至少一个探针在背面开半平面
        back = any((q[0] * ux + q[1] * uy) < -1e-9 for q in ps)
        if not back:
            cover_ok = False; break
    print("3探针必落入定向背面开半平面:", cover_ok)
