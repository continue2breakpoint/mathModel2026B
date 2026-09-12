# Extracted from geometry.py; dependencies omitted.
def _clip_half_plane(poly, a, b, c, eps=1e-09):
    if not poly:
        return []
    out = []
    n = len(poly)
    for i in range(n):
        cur = poly[i]
        nxt = poly[(i + 1) % n]
        d_cur = a * cur.x + b * cur.y + c
        d_nxt = a * nxt.x + b * nxt.y + c
        if d_cur >= -eps:
            out.append(cur)
        if d_cur > eps and d_nxt < -eps or (d_cur < -eps and d_nxt > eps):
            t = d_cur / (d_cur - d_nxt)
            out.append(Point(cur.x + (nxt.x - cur.x) * t, cur.y + (nxt.y - cur.y) * t))
    return out

def clip_by_bearing(poly, apex, bearing_deg, half_width_deg=BEARING_ERROR_DEG):
    left = unit_from_deg(bearing_deg + half_width_deg)
    right = unit_from_deg(bearing_deg - half_width_deg)
    poly = _clip_half_plane(poly, -right.y, right.x, right.y * apex.x - right.x * apex.y)
    poly = _clip_half_plane(poly, left.y, -left.x, left.x * apex.y - left.y * apex.x)
    return poly

def polygon_diameter(poly):
    best = 0.0
    n = len(poly)
    for i in range(n):
        for j in range(i + 1, n):
            d = poly[i].distance_to(poly[j])
            if d > best:
                best = d
    return best
