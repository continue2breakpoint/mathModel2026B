"""Single-channel Q2 posterior and conservative, nonconvex geometry.

No Monte Carlo: triangle Gauss quadrature for source position, exact integration
of the shared uniform range at each node, measured-bearing bin quadrature.
All disk approximations contain the true possible set (positive: outer polygon;
negative: remove an inner polygon). Increase sides/order/angle_bins to converge.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cpp"))
import geom_cpp as geom


def finite(value):
    v = float(value)
    if not math.isfinite(v):
        raise ValueError("坐标、角度和参数必须为有限数")
    return v


def normalize_matrix(data, channel=1):
    """Accept our lossless column schema or the project's Markdown matrix."""
    if isinstance(data, str):
        if data.lstrip().startswith('{'):
            data = json.loads(data)
        else:
            rows = [r.strip().strip('|').split('|') for r in data.splitlines()
                    if r.strip().startswith('|')]
            if len(rows) < 3:
                raise ValueError("需要 JSON 或 Channel × Path Markdown 表")
            points = [json.loads('[' + p.strip().strip('()[]') + ']') for p in rows[0][1:]]
            row = next((r for r in rows[2:] if r[0].strip() == str(channel)), None)
            if row is None or len(row) - 1 != len(points):
                raise ValueError("找不到所选频道或列数不一致")
            data = {"channel": channel, "columns": [dict(json.loads(c), point=p)
                    for p, c in zip(points, row[1:])]}
    if not isinstance(data, dict) or data.get('schema', 'q2-channel/v1') != 'q2-channel/v1':
        raise ValueError("请使用 q2-channel/v1 JSON 或知识矩阵 Markdown 表；旧版矩阵 JSON 不含真实观测坐标")
    columns = data.get('columns')
    if not isinstance(columns, list) or not 1 <= len(columns) <= 40:
        raise ValueError("观测列数应为 1–40")
    result = []
    seen = {}
    for c in columns:
        p = c['point']
        if len(p) != 2:
            raise ValueError("point 需要 [x,y]")
        point = [finite(v) for v in p]
        status = str(c['status']).strip().replace(' ', '_')
        if status not in ('find', 'not_find', 'not_measure', 'near'):
            raise ValueError(f"不支持状态 {status}：本页针对尚未清除的全向源")
        out = dict(point=point, status=status)
        if status == 'find':
            angle = c.get('angle', {})
            bearing = c.get('bearing_deg', angle.get('svd'))
            if bearing is None and 'lower' in angle and 'upper' in angle:
                lo, hi = finite(angle['lower']), finite(angle['upper'])
                width = (hi - lo) % 360
                if abs(width - 2) > 1e-6:
                    raise ValueError("find 角区间必须为 2°，或提供 bearing_deg")
                bearing = lo + width / 2
            out['bearing_deg'] = finite(bearing) % 360
        if status != 'not_measure':
            key = tuple(point)
            if key in seen and seen[key] != out:
                raise ValueError("同一地点的重复观测相互矛盾")
            seen[key] = out
        result.append(out)
    channel = int(data.get('channel', channel))
    if not 1 <= channel <= 20:
        raise ValueError("频道号应为 1–20")
    return dict(schema='q2-channel/v1', channel=channel, columns=result)


def parts_to_lists(parts, digits=4):
    """Polygon pieces as plain [[x, y], ...] lists for the JSON API."""
    return [[[round(float(v), digits) for v in point] for point in poly]
            for poly in parts if len(poly) >= 3]


def ray_exit(point, direction_deg, radius=1800.0):
    """Far intersection of a ray leaving ``point`` with the site circle.

    Used to draw the two +-1 deg measurement rays of a find observation: the
    wedge itself is unbounded, so the drawing needs an explicit far end.
    """
    ux, uy = math.cos(math.radians(direction_deg)), math.sin(math.radians(direction_deg))
    ox, oy = float(point[0]), float(point[1])
    b = ox * ux + oy * uy
    c = ox * ox + oy * oy - radius * radius
    disc = b * b - c
    if disc <= 0.0:
        return None
    t = -b + math.sqrt(disc)
    if t <= 0.0:
        return None
    return [ox + t * ux, oy + t * uy]


def clip(poly, normal, offset):
    """Convex polygon intersected with normal . x <= offset."""
    if len(poly) < 3:
        return []
    out = []
    a = poly[-1]
    da = np.dot(a, normal) - offset
    for b in poly:
        db = np.dot(b, normal) - offset
        if (da <= 0) != (db <= 0):
            out.append(a + (b - a) * (da / (da - db)))
        if db <= 0:
            out.append(b)
        a, da = b, db
    return out if len(out) >= 3 else []


def disk(parts, point, radius, sides, exclude=False):
    """Intersect outer disk polygon / subtract inner disk polygon, retaining pieces."""
    p = np.asarray(point)
    angle = np.arange(sides) * (2 * math.pi / sides)
    normals = np.column_stack([np.cos(angle), np.sin(angle)])
    apothem = radius * math.cos(math.pi / sides) if exclude else radius
    result = []
    for poly in parts:
        # Exact coarse checks avoid splitting polygons far from this disk.
        arr = np.asarray(poly)
        if exclude and np.linalg.norm(np.maximum(np.maximum(arr.min(0)-p, p-arr.max(0)), 0)) > radius:
            result.append(poly)
            continue
        remaining = poly
        for n in normals:
            off = np.dot(n, p) + apothem
            if exclude:
                outside = clip(remaining, -n, -off)
                if outside:
                    result.append(outside)
            remaining = clip(remaining, n, off)
            if not remaining:
                break
        if not exclude and remaining:
            result.append(remaining)
    return result


def wedge(parts, point, bearing):
    for a, sign in ((bearing - 1, -1), (bearing + 1, 1)):
        t = math.radians(a)
        n = sign * np.array([-math.sin(t), math.cos(t)])
        parts = [q for p in parts if (q := clip(p, n, np.dot(n, point)))]
    return parts


def metrics(parts):
    vertices = [v for p in parts for v in p]
    if not vertices:
        raise ValueError("可能区域为空")
    circle = geom.welzl_min_enclosing_circle_points([geom.Vec2(*v) for v in vertices])
    area = sum(abs(sum(float(a[0]*b[1]-a[1]*b[0]) for a,b in zip(p, p[1:]+p[:1]))) / 2 for p in parts)
    # The LENGTH of the projected union, not its diameter, is <= 40*N.
    # This remains valid for holes and disconnected components, and is
    # independent of how a region is split into convex pieces.
    angles = np.arange(180) * math.pi / 180
    directions = np.array([np.cos(angles), np.sin(angles)])
    projections = [np.asarray(p) @ directions for p in parts]
    starts = np.array([p.min(0) for p in projections])
    ends = np.array([p.max(0) for p in projections])
    order = starts.argsort(axis=0)
    starts, ends = np.take_along_axis(starts,order,axis=0), np.take_along_axis(ends,order,axis=0)
    right, lengths = ends[0].copy(), ends[0]-starts[0]
    for left, end in zip(starts[1:], ends[1:]):
        lengths += np.maximum(0, end-np.maximum(right,left))
        right = np.maximum(right,end)
    projection_lb = float(lengths.max())
    r = circle.radius
    n = 1 if r <= 20 else max(2, math.ceil(area/(math.pi*400)-1e-9),
                            math.ceil(projection_lb/40-1e-9))
    return {'Rmin': r, 'N20': n, 'area': area}


class MatrixEngine:
    def __init__(self, matrix, *, rho_min=1000, rho_max=1500, rho_model='uniform', rho_fixed=1250,
                 sides=96, order=3, angle_bins=48):
        self.matrix = normalize_matrix(matrix)
        self.lo, self.hi = finite(rho_min), finite(rho_max)
        if not 0 < self.lo <= self.hi <= 3000:
            raise ValueError("需要 0 < rho_min ≤ rho_max ≤ 3000")
        if rho_model not in ('uniform', 'fixed'):
            raise ValueError("期望/方差需要概率模型：请选择均匀先验或固定 ρ")
        self.fixed = rho_model == 'fixed' or self.lo == self.hi
        if self.fixed:
            self.lo = self.hi = finite(rho_fixed) if rho_model == 'fixed' else self.lo
            if not 0 < self.lo <= 3000:
                raise ValueError("固定 ρ 应在 (0,3000] 内")
        self.sides, self.order, self.bins = int(sides), int(order), int(angle_bins)
        if not (24 <= self.sides <= 360 and 2 <= self.order <= 10 and 8 <= self.bins <= 360):
            raise ValueError("精度范围：sides 24–360，order 2–10，angle_bins 8–360")
        self.observations = sorted({tuple(c['point']): c for c in self.matrix['columns'] if c['status'] != 'not_measure'}.values(),
                                   key=lambda c: tuple(c['point']))
        square = [np.array(v, dtype=float) for v in [(-2000,-2000),(2000,-2000),(2000,2000),(-2000,2000)]]
        self.site = disk([square], [0,0], 1800, self.sides)
        self.parts = list(self.site)
        # Apply narrow positive constraints before carving holes.
        for c in self.observations:
            if c['status'] == 'find':
                self.parts = wedge(self.parts, c['point'], c['bearing_deg'])
                self.parts = disk(self.parts, c['point'], self.hi, self.sides)
            elif c['status'] == 'near':
                self.parts = disk(self.parts, c['point'], min(5, self.hi), self.sides)
        for c in self.observations:
            if c['status'] in ('find', 'not_find'):
                self.parts = disk(self.parts, c['point'], 5 if c['status']=='find' else self.lo, self.sides, True)
        if not self.parts:
            raise ValueError("观测矛盾：可能区域为空")
        self.baseline = metrics(self.parts)
        self.points, self.area_weights = self._quadrature()
        self.lower, self.upper = self.bounds(self.points)
        weights = self.area_weights * self._mass(self.lower, self.upper)
        total = weights.sum()
        if total <= 1e-20:
            raise ValueError("后验积分为零：观测可能矛盾或有效区域过窄，请提高积分精度核对")
        self.weights = weights / total
        positive = self.weights > 0
        self.points, self.weights = self.points[positive], self.weights[positive]
        self.lower, self.upper = self.lower[positive], self.upper[positive]

    def bounds(self, points):
        lower, upper = np.full(len(points), self.lo), np.full(len(points), self.hi)
        valid = np.linalg.norm(points, axis=1) <= 1800
        for c in self.observations:
            d = np.linalg.norm(points - c['point'], axis=1)
            if c['status'] == 'not_find':
                if self.fixed:
                    valid &= d > self.hi
                else:
                    upper = np.minimum(upper, d)
            else:
                lower = np.maximum(lower, d)
                if c['status'] == 'near':
                    valid &= d <= 5
                else:
                    a = np.degrees(np.arctan2(points[:,1]-c['point'][1], points[:,0]-c['point'][0]))
                    valid &= (np.abs((a-c['bearing_deg']+180)%360-180) <= 1+1e-9) & (d > 5)
        upper = np.where(valid, upper, -1)
        return lower, upper

    def _mass(self, lower, upper):
        return (lower <= upper).astype(float) if self.fixed else np.maximum(0, upper-lower)

    @staticmethod
    def zone(pdet):
        """Detection zone of one candidate, read off p_det itself.

        certain  : every posterior node is detectable (d < U for all of them),
                   so the next reading succeeds with probability 1 under the
                   conservative endpoint model -- the matrix counterpart of the
                   double-point "一定测到" region (there it is judged at rho_min,
                   here at U(G) = min(b, min_j |G-M_j|)).
        blind    : no posterior node is detectable; the reading carries no
                   information and the cell is not rendered.
        probabilistic: everything in between.
        """
        if pdet >= 1.0 - 1e-9:
            return 'certain'
        if pdet <= 1e-12:
            return 'blind'
        return 'probabilistic'

    def observation_regions(self):
        """Own constraint geometry of every observation, for the overlay layer.

        Each entry is the region *that observation alone* allows (or excludes),
        not the intersection: it is what a user needs in order to see why the
        current possible set looks the way it does.
        """
        out = []
        for c in self.observations:
            entry = {'point': [float(v) for v in c['point']], 'status': c['status']}
            point = c['point']
            if c['status'] == 'find':
                parts = wedge(self.site, point, c['bearing_deg'])
                parts = disk(parts, point, self.hi, self.sides)
                parts = disk(parts, point, 5.0, self.sides, True)
                entry['bearing_deg'] = float(c['bearing_deg'])
                entry['poly'] = parts_to_lists(parts)
                entry['near_radius'] = 5.0
                entry['rays'] = []
                for delta in (-1.0, 1.0):
                    end = ray_exit(point, c['bearing_deg'] + delta)
                    if end is not None:
                        entry['rays'].append([[float(point[0]), float(point[1])], end])
            elif c['status'] == 'near':
                entry['poly'] = parts_to_lists(disk(self.site, point, min(5.0, self.hi), self.sides))
                entry['near_radius'] = min(5.0, self.hi)
            else:  # not_find: this disk is excluded, not allowed
                entry['poly'] = parts_to_lists(disk(self.site, point, self.lo, self.sides))
                entry['exclude_radius'] = self.lo
            out.append(entry)
        return out

    def _quadrature(self):
        nodes, weights = np.polynomial.legendre.leggauss(self.order)
        nodes, weights = (nodes+1)/2, weights/2
        points, mass = [], []
        for p in self.parts:
            # Fan about centroid keeps long, narrow regions resolved in both axes.
            center = np.mean(p, axis=0)
            for a,b in zip(p, p[1:]+p[:1]):
                va, vb = a-center, b-center
                det = abs(float(va[0]*vb[1]-va[1]*vb[0]))
                if det < 1e-12:
                    continue
                for u,wu in zip(nodes, weights):
                    for v,wv in zip(nodes, weights):
                        points.append(center + u*va + (1-u)*v*vb)
                        mass.append(det*(1-u)*wu*wv)
        return np.asarray(points), np.asarray(mass)

    def evaluate(self, point, metric='Rmin', condition='all'):
        if metric not in ('Rmin','N20','pdet') or condition not in ('all','detected'):
            raise ValueError("未知指标或统计条件")
        point = np.asarray([finite(x) for x in point])
        if point.shape != (2,):
            raise ValueError("候选点需要 [x,y]")
        for c in self.observations:
            if np.linalg.norm(point-c['point']) < 1e-8:
                detected = c['status'] != 'not_find'
                if metric == 'pdet':
                    return dict(pdet=float(detected),mean=float(detected),variance=0.0,
                                repeated=True,zone=self.zone(float(detected)))
                return dict(pdet=float(detected), mean=self.baseline[metric] if detected or condition=='all' else None,
                            variance=0.0 if detected or condition=='all' else None, repeated=True,
                            zone=self.zone(float(detected)))
        d = np.linalg.norm(self.points-point, axis=1)
        prob = self._mass(np.maximum(self.lower,d),self.upper)/self._mass(self.lower,self.upper)
        hit_weights = self.weights*prob
        pdet = min(1.0, max(0.0, float(hit_weights.sum())))
        zone = self.zone(pdet)
        if metric == 'pdet':
            return dict(pdet=pdet, mean=pdet, variance=pdet*(1-pdet), repeated=False, zone=zone)
        outcomes = []
        miss = max(0.0,1-pdet)
        if condition == 'all' and miss > 1e-12:
            parts = disk(self.parts, point, self.lo, self.sides, True)
            outcomes.append((miss, metrics(parts)[metric]))
        near = d <= 5
        p_near = float(hit_weights[near].sum())
        if p_near > 1e-12:
            outcomes.append((p_near, metrics(disk(self.parts, point, min(5,self.hi),self.sides))[metric]))
        active = (hit_weights > 0) & ~near
        if active.any():
            beta = np.degrees(np.arctan2(self.points[active,1]-point[1],self.points[active,0]-point[0])) % 360
            hw = hit_weights[active]
            # Cut the circle in its largest empty gap to avoid a 0°/360° seam.
            ordered = np.sort(beta)
            gaps = np.diff(np.r_[ordered, ordered[0]+360])
            start = ordered[(int(np.argmax(gaps))+1)%len(ordered)]
            beta = (beta-start)%360+start
            edges = np.linspace(beta.min()-1,beta.max()+1,self.bins+1)
            base = disk(self.parts, point, self.hi, self.sides)
            base = disk(base, point, 5, self.sides, True)
            for a,b in zip(edges[:-1],edges[1:]):
                overlap = np.maximum(0,np.minimum(beta+1,b)-np.maximum(beta-1,a))/2
                mass = float(hw@overlap)
                if mass > 1e-14:
                    # Weighted overlap midpoint stays in occupied bearing support.
                    theta = float(hw @ (overlap*(np.minimum(beta+1,b)+np.maximum(beta-1,a))/2)/mass)
                    parts = wedge(base, point, theta)
                    if not parts:
                        raise ValueError("角度积分未解析到可行区域，请提高 angle_bins")
                    outcomes.append((mass, metrics(parts)[metric]))
        total = sum(w for w,v in outcomes)
        if total < 1e-12:
            return dict(pdet=pdet, mean=None, variance=None, repeated=False, zone=zone)
        mean = sum(w*v for w,v in outcomes)/total
        variance = sum(w*(v-mean)**2 for w,v in outcomes)/total
        return dict(pdet=pdet, mean=mean, variance=variance, repeated=False, zone=zone)
