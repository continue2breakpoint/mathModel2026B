# Extracted from geometry.py; dependencies omitted.
def minimum_enclosing_circle(points):
    pts = list(dict.fromkeys(points))
    if not pts:
        return None
    if len(pts) == 1:
        return Circle(pts[0], 0.0)
    rnd = Random(20260913)
    rnd.shuffle(pts)
    circle = None
    for i, p in enumerate(pts):
        if circle is not None and _in_circle(circle, p):
            continue
        circle = Circle(p, 0.0)
        for j in range(i):
            q = pts[j]
            if _in_circle(circle, q):
                continue
            circle = _circle_from_two(p, q)
            for k in range(j):
                r = pts[k]
                if _in_circle(circle, r):
                    continue
                cand = _circle_from_three(p, q, r)
                if cand is not None:
                    circle = cand
    return circle
