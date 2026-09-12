# -*- coding: utf-8 -*-
"""问题3/4 发现层几何：检测点覆盖布局 + 遍历路径 + 定向发现完备性判据。

全向发现（问题3）：以保守检测半径 R0=1000 的全等圆盘覆盖半径 1800 的 Ω。
  采用"原点 + 正六边形外环"七点布局，环半径 ρ 的解析可行区间为
  [1122.9558, 1732.0508]（见 coverage_rho_interval 与第二轮审查 R3/第13条）；
  取略大于下界以留数值余量。遍历走【开放哈密顿路径】（附件允许任意位置 /exit，
  不必回原点）：原点→某环点→沿环依次访问，长度 6ρ（回路为 7ρ）。

定向发现（问题4）：对给定候选源 G，近距离检测点集 Q_G={q:|q-G|≤R0}，
  "任意朝向都至少有一个正面点" 当且仅当 G∈conv(Q_G)（局部凸包定理，分离定理）。
  边界朝外的定向源在 Ω 内无正面点，故检测点必须允许落在 Ω 外。
  等边三角网格（边长 s≤1000、保留与 Ω 相交闭三角形的界外顶点）是一个可证明
  定向发现完备的充分构造：G 所在三角形三顶点都在 1000 内且 G∈其凸包。
"""
import math

OMEGA_R = 1800.0
R0 = 1000.0
# 推荐环半径：理论可行区间 [1122.96,1732.05]。下端点外缘最坏距离≈999m 仅 1m 裕量、
# 过于临界，故取 1150（外缘最坏≈988.6m、留 11m 裕量），遍历路程仅比下端点多 120m；
# 定位收敛修复后该取值在常规与 R=1000 极端场景均 100% 全清且总时间最短（见 Q3 消融）。
RECOMMENDED_RHO = 1150.0


# ---------------- 问题3：七点全向覆盖 ----------------
def coverage_rho_interval(R=R0, omega=OMEGA_R):
    """原点+正六边形布局覆盖 Ω 的环半径可行区间 [rho_lo, rho_hi]（解析）。"""
    # 内衔接（r=R 处，与中心盘相切）：ρ ≤ 2R cos30
    rho_hi = 2 * R * math.cos(math.radians(30))
    # 外缘（r=omega 处）：omega^2 + ρ^2 - 2 omega ρ cos30 ≤ R^2，取下根
    b = -2 * omega * math.cos(math.radians(30))
    a, c = 1.0, omega * omega - R * R
    disc = b * b - 4 * a * c
    rho_lo = (-b - math.sqrt(disc)) / 2
    return rho_lo, rho_hi


def seven_points(rho, omega=OMEGA_R):
    """[原点] + 正六边形 6 顶点（CCW，从方位 0° 起）。"""
    pts = [(0.0, 0.0)]
    for k in range(6):
        a = math.radians(60 * k)
        pts.append((rho * math.cos(a), rho * math.sin(a)))
    return pts


def open_route_7(rho):
    """开放哈密顿路径：原点→环点0→环点1…→环点5（不回原点），返回点序与长度。"""
    ring = []
    for k in range(6):
        a = math.radians(60 * k)
        ring.append((rho * math.cos(a), rho * math.sin(a)))
    route = [(0.0, 0.0)] + ring
    L = sum(math.hypot(route[i + 1][0] - route[i][0], route[i + 1][1] - route[i][1])
            for i in range(len(route) - 1))
    return route, L


def coverage_gap(points, R=R0, omega=OMEGA_R, step=20.0):
    """数值检验：在 Ω 内稠密采样，返回任一点到最近检测点的最大距离（应≤R）。"""
    worst = 0.0
    worst_at = None
    x = -omega
    while x <= omega:
        y = -omega
        while y <= omega:
            if x * x + y * y <= omega * omega:
                dmin = min(math.hypot(x - p[0], y - p[1]) for p in points)
                if dmin > worst:
                    worst, worst_at = dmin, (x, y)
            y += step
        x += step
    return worst, worst_at


# ---------------- 问题4：三角网格 + 局部凸包 ----------------
def triangular_grid(s=R0, omega=OMEGA_R):
    """等边三角网格，保留落在 Ω 外扩 s 范围内的顶点（含界外补点）。

    任何 G∈Ω 所在三角形的三顶点到 G 距离≤s，且这些顶点到原点距离≤omega+s，
    故保留 |p|≤omega+s 的网格点即构成充分的检测点集合。
    """
    dy = s * math.sqrt(3) / 2
    pts = []
    j = int(math.ceil((omega + s) / dy)) + 1
    i_hi = int(math.ceil((omega + s) / s)) + 2
    for jj in range(-j, j + 1):
        off = (s / 2) if (jj % 2 != 0) else 0.0
        for ii in range(-i_hi, i_hi + 1):
            x = ii * s + off
            y = jj * dy
            if math.hypot(x, y) <= omega + s + 1e-9:
                pts.append((x, y))
    return pts


def axial_grid_nodes(s=R0, omega=OMEGA_R):
    """轴向三角格点（第二轮复核 R3 的精确构造）。
    q_ij=(s(i+j/2), s√3/2·j)；保留与 Ω 相交闭三角形的顶点，即 |q_ij|≤omega+s。
    相邻格点满足 (di²+di·dj+dj²)==1、间距恰为 s。"""
    h = s * math.sqrt(3) / 2
    lim = omega + s
    nodes = []
    m = int(math.ceil(lim / min(s, h))) + 1
    for i in range(-m, m + 1):
        for j in range(-m, m + 1):
            x, y = s * (i + j / 2), h * j
            if math.hypot(x, y) <= lim + 1e-9:
                nodes.append((i, j))
    return nodes


def axial_to_xy(ij, s=R0):
    i, j = ij
    return (s * (i + j / 2), s * math.sqrt(3) / 2 * j)


def _axial_neighbors(a, nodes):
    s = set(nodes)
    out = []
    for d in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, -1), (-1, 1)):
        b = (a[0] + d[0], a[1] + d[1])
        if b in s:
            out.append(b)
    return out


def hamiltonian_route_axial(s=R0, omega=OMEGA_R, start=(0, 0)):
    """对轴向格点用 Warnsdorff DFS 求从 start 出发、相邻点间距全为 s 的开放
    哈密顿路径（该固定点集上的最短遍历：长度=(N-1)s）。返回坐标序列与长度。
    第二轮复核 R3 对 s=1000 给出 31 点、30000m 的路径。"""
    nodes = axial_grid_nodes(s, omega)
    adj = {a: _axial_neighbors(a, nodes) for a in nodes}

    def dfs(path, seen):
        if len(path) == len(nodes):
            return path
        last = path[-1]
        cand = sorted((v for v in adj[last] if v not in seen),
                      key=lambda v: sum(w not in seen for w in adj[v]))
        for v in cand:
            r = dfs(path + [v], seen | {v})
            if r:
                return r
        return None

    if start not in nodes:
        start = min(nodes, key=lambda z: math.hypot(*axial_to_xy(z, s)))
    route_ij = dfs([start], {start})
    if route_ij is None:
        return None, None
    route = [axial_to_xy(z, s) for z in route_ij]
    L = sum(math.hypot(route[i+1][0]-route[i][0], route[i+1][1]-route[i][1])
            for i in range(len(route)-1))
    # 起点到原点的进入段（原点 (0,0)=轴向(0,0)，若 start 即原点则为 0）
    return route, L


def point_in_convex(p, poly):
    """点是否在凸多边形内（含边界），poly 按 CCW。"""
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        cr = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
        if cr < -1e-7:
            return False
    return True


def directional_discovery_check(points, R=R0, omega=OMEGA_R, step=40.0, n_phi=36):
    """数值检验定向发现完备性：对 Ω 内每个采样 G、若干朝向 φ，
    检查是否总存在检测点 q 满足 |q-G|≤R 且在 G 的前半平面（正面）。
    返回 (worst_case, fail_list)：worst_case 为"最坏朝向下正面近距离点数"的最小值。
    """
    worst = 10 ** 9
    fails = []
    gx = -omega
    while gx <= omega:
        gy = -omega
        while gy <= omega:
            if gx * gx + gy * gy <= omega * omega:
                near = [q for q in points if math.hypot(q[0] - gx, q[1] - gy) <= R + 1e-9]
                for t in range(n_phi):
                    phi = 2 * math.pi * t / n_phi
                    ux, uy = math.cos(phi), math.sin(phi)
                    front = [q for q in near
                             if (q[0] - gx) * ux + (q[1] - gy) * uy >= -1e-9]
                    if len(front) < worst:
                        worst = len(front)
                    if len(front) == 0:
                        fails.append((gx, gy, math.degrees(phi)))
            gy += step
        gx += step
    return worst, fails


if __name__ == "__main__":
    lo, hi = coverage_rho_interval()
    print("七点环半径可行区间: [%.4f, %.4f]" % (lo, hi))
    rho = RECOMMENDED_RHO             # 略大于下界、留余量
    print("选取 ρ=%.1f" % rho)
    pts7 = seven_points(rho)
    gap, at = coverage_gap(pts7, step=20)
    print("七点布局 Ω 内到最近检测点最大距离=%.2f m（应≤1000），最差点=%s" % (gap, at))
    route, L = open_route_7(rho)
    print("开放路径长度=%.1f m（=6ρ=%.1f），移动虚拟耗时=%.1f s" % (L, 6 * rho, L / 5))
    print("发现层纯扫描+移动（7×119 + 路程/5）=%.1f s" % (7 * 119 + L / 5))

    # 问题4：三角网格
    grid = triangular_grid(1000.0)
    print("\n三角网格(s=1000)检测点数=%d" % len(grid))
    w, fails = directional_discovery_check(grid, step=50, n_phi=24)
    print("定向发现检验：最坏正面近距离点数=%d，失败朝向数=%d" % (w, len(fails)))
    # 对照：七点布局对定向是否完备（预期边界朝外会失败）
    w7, f7 = directional_discovery_check(pts7, step=60, n_phi=24)
    print("对照 七点布局：最坏正面近距离点数=%d，失败朝向数=%d（边界朝外应>0）" % (w7, len(f7)))

    # 问题4：轴向 31 点 + 最短哈密顿路径（第二轮复核 R3）
    nodes = axial_grid_nodes(1000.0)
    hr, hL = hamiltonian_route_axial(1000.0)
    wg, fg = directional_discovery_check(hr, step=50, n_phi=24)
    print("\n轴向网格点=%d，哈密顿路径点=%d、长=%.1f m（应31/30000），定向失败=%d"
          % (len(nodes), len(hr), hL, len(fg)))
    print("完整发现虚拟时间=路程/5+31×119=%.1f s" % (hL / 5 + 31 * 119))
