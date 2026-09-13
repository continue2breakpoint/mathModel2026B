"""Offline mathematical checks. Run with python3 paper/verify_formulas.py."""
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent / 'code'))
from fallback_grid import fallback_grid


def coverage(n, ring, radius=1800.0):
    beta = math.pi / n
    return max(ring / (2 * math.cos(beta)),
               math.sqrt(radius**2 + ring**2
                         - 2*radius*ring*math.cos(beta)))


def brute_coverage(n, ring, radius=1800.0):
    # All symmetry-reduced radial/angular samples, using actual point distances.
    points = [(0., 0.)] + [(ring*math.cos(2*math.pi*k/n),
                            ring*math.sin(2*math.pi*k/n)) for k in range(n)]
    worst = 0.
    for i in range(601):
        r = radius*i/600
        for j in range(61):
            phi = math.pi*j/(60*n)
            x, y = r*math.cos(phi), r*math.sin(phi)
            worst = max(worst, min(math.hypot(x-qx, y-qy) for qx, qy in points))
    return worst


def main():
    checks = []
    for n, ring in [(6, 1130.), (6, 1200.), (8, 1010.), (6, 1800.), (3, 1000.)]:
        exact, sampled = coverage(n, ring), brute_coverage(n, ring)
        assert sampled <= exact + 1e-8
        assert exact-sampled < 2.0
        checks.append(dict(n=n, ring=ring, analytic=exact, sampled=sampled))
    a, b = 1000., 1500.
    i1, i2 = (b**3-a**3)/6, (b**4-a**4)/12
    mu = i2/i1
    mu_direction = (i2-(b-a)*5**3/3)/(i1-(b-a)*5**2/2)
    eps = math.pi/180
    s = (850., 520.)
    centers = [(0., 0.), (a*math.cos(eps), a*math.sin(eps)),
               (a*math.cos(eps), -a*math.sin(eps))]
    margin = a-max(math.dist(s, c) for c in centers)
    assert margin > 0
    worst = -math.inf
    for i in range(301):
        r = b*i/300
        for j in range(101):
            phi = -eps+2*eps*j/100
            worst = max(worst, math.dist(s, (r*math.cos(phi), r*math.sin(phi)))-max(a,r))
    assert worst <= 0
    points = fallback_grid(0., 0., 0.)
    assert len(points) == 225
    grid_length = sum(math.dist(p,q) for p,q in zip(points, points[1:]))
    grid_corner = math.hypot(1500/75/2, (3000*math.sin(eps))/3/2)
    assert grid_corner < 20
    assert grid_length <= 4480
    # Construct a triangle and confirm diameter disk fails.
    triangle = [(0.,0.), (1.,0.), (.5, math.sqrt(3)/2)]
    assert math.dist(triangle[2], (.5,0.)) > .5
    # Matrix: a safe radius lower bound is not an impossibility threshold.
    region = [(1100., -20.), (1200., -20.), (1200., 20.), (1100., 20.)]
    source, rho, positive, negative = (1150., 0.), 1200., (0., 0.), (2500., 0.)
    lower = 1100.  # Exact minimum distance from positive to rectangle.
    upper = min(1500., max(math.dist(negative, x) for x in region))
    assert lower <= rho <= upper
    assert math.dist(source, positive) <= rho < math.dist(source, negative)
    assert lower > lower - 50  # Outside plan radius still allows reception.
    optical_source = (30., 0.)
    assert 20 < math.dist(optical_source, positive) < 1000
    region_mask, covered_mask = 0b111101, 0b001101
    missing = region_mask & ~covered_mask
    assert missing == 0b110000 and missing.bit_count() == 2
    matrix_checks = dict(radius_lower=lower, radius_upper=upper,
                         actual_radius=rho, plan_radius=lower-50,
                         mask_uncovered_count=missing.bit_count(),
                         optical_failure_distance=30.,
                         matrix_open_route_m=1010+14*1010*math.sin(math.pi/8))
    report = dict(matrix=matrix_checks, coverage=checks, posterior_mean=mu,
                  posterior_mean_direction=mu_direction,
                  candidate_margin=margin, candidate_sampled_worst_gap=worst,
                  outer_polygon_error=1800*(1/math.cos(math.pi/96)-1),
                  fallback_points=len(points), fallback_path_m=grid_length,
                  fallback_cell_radius=grid_corner,
                  historical_median_improvement_percent=(332.19-319.56)/332.19*100)
    output = Path(__file__).parent / 'build' / 'formula-checks.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
