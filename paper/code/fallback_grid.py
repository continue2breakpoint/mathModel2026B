"""Generate a finite optical-clear fallback; no simulator calls."""
from math import ceil, cos, radians, sin


def fallback_grid(sx, sy, bearing_deg, max_range=1500.0,
                  error_deg=1.0, cell_size=20.0):
    assert max_range > 0 and 0 < error_deg < 90
    assert 0 < cell_size <= 20
    angle = radians(bearing_deg)
    half_width = max_range * sin(radians(error_deg))
    nx = ceil(max_range / cell_size)
    ny = ceil(2 * half_width / cell_size)
    points = []
    for i in range(nx):
        x = (i + 0.5) * max_range / nx
        rows = range(ny) if i % 2 == 0 else range(ny - 1, -1, -1)
        for j in rows:
            y = -half_width + (j + 0.5) * 2 * half_width / ny
            points.append((sx + x*cos(angle) - y*sin(angle),
                           sy + x*sin(angle) + y*cos(angle)))
    return points
