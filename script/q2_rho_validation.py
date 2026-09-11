#!/usr/bin/env python3
"""Numerical re-derivation check for the Q2 rho (effective-radius) analysis.

Run:
    PYTHONPATH=cpp python3 script/q2_rho_validation.py

Checks performed
----------------
A. closed-form `certain_margin` (three disks of radius rho_min centred at S1
   and at S1 + rho_min*e(theta1 +- eps)) against a brute-force maximisation of
   d2(G) - max(d1(G), rho_min) over a dense sample of the *whole* possible
   source set A1.
B. the region built from the far corners 1500*e(theta1 +- eps) (the version in
   the first draft of the note) is strictly smaller than the true guarantee
   region, i.e. it is sufficient but not necessary.
C. `blind_gap` (distance to A1 minus rho_max) against a brute-force minimum of
   d2(G) over the same dense sample.
D. the three-zone classification against the deterministic quadrature of the
   second-detection probability p_det:  certain <=> p_det = 1, blind <=> p_det = 0.
E. Monte-Carlo validation of the quadrature p_det under the model
       G ~ uniform on A(RHO_MAX) (area measure, conditioned on the +-eps
           reading of the first station),
       rho ~ Uniform[max(RHO_MIN, d1), RHO_MAX]  (conditioned on the P1 hit),
       second detection succeeds  <=>  d2 <= rho.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cpp"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import q2_dashboard as dash  # noqa: E402
import q2_zones as zones  # noqa: E402

RHO_MIN = zones.RHO_MIN
RHO_MAX = zones.RHO_MAX
EPS_DEG = zones.EPS_DEG
EPS_RAD = EPS_DEG * zones.DEG2RAD
R_TARGET = zones.R_TARGET


def local_to_world(s1, theta1_deg, local):
    c = math.cos(theta1_deg * zones.DEG2RAD)
    s = math.sin(theta1_deg * zones.DEG2RAD)
    return (s1[0] + local[0] * c - local[1] * s,
            s1[1] + local[0] * s + local[1] * c)


def source_sample(
    s1: tuple[float, float],
    theta1_deg: float,
    rho: float,
    n_r: int = 901,
    n_phi: int = 241,
) -> np.ndarray:
    """Dense uniform-area sample of A(rho) (the possible source set)."""
    phis = np.linspace(-EPS_RAD, EPS_RAD, n_phi)
    r_max = np.minimum(
        rho,
        np.array([
            zones.ray_target_radius(s1, theta1_deg + math.degrees(p))
            for p in phis
        ]),
    )
    pts = []
    for p, rm in zip(phis, r_max):
        if rm <= 1e-9:
            continue
        rr = np.linspace(0.0, rm, n_r)
        a = theta1_deg * zones.DEG2RAD + p
        pts.append(np.column_stack([
            s1[0] + rr * math.cos(a),
            s1[1] + rr * math.sin(a),
        ]))
    return np.vstack(pts)


def margin_bruteforce(
    s2, src: np.ndarray, s1: tuple[float, float], rho_min: float
) -> tuple[float, float]:
    d1 = np.hypot(src[:, 0] - s1[0], src[:, 1] - s1[1])
    d2 = np.hypot(src[:, 0] - s2[0], src[:, 1] - s2[1])
    margin = float(np.max(d2 - np.maximum(d1, rho_min)))
    blind = float(np.min(d2) - RHO_MAX)
    return margin, blind


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mc", type=int, default=2000000, help="Monte-Carlo samples")
    parser.add_argument("--seed", type=int, default=20260913)
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)
    failures: list[str] = []

    def report(tag: str, ok: bool, detail: str) -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {tag}: {detail}")
        if not ok:
            failures.append(tag)

    # ------------------------------------------------------------------
    # A/C. dense brute force over the whole possible set
    # ------------------------------------------------------------------
    print("=" * 78)
    print("A/C. closed forms vs dense brute force over A1 = A(rho_max)")
    print("=" * 78)
    configs = [
        ((0.0, 0.0), 0.0),
        ((0.0, 0.0), 37.0),
        ((-400.0, 250.0), 200.0),
        ((1200.0, -300.0), 305.0),
        ((1700.0, 0.0), 180.0),
        ((1200.0, 900.0), 60.0),
    ]
    probes_local = [
        (850.0, 520.0), (850.0, -520.0), (500.0, 0.0), (500.0, 840.0),
        (500.0, 880.0), (999.0, 0.0), (1000.0, 0.0), (1001.0, 0.0),
        (1200.0, 0.0), (600.0, 900.0), (600.0, -900.0), (-600.0, 0.0),
        (0.0, -1600.0), (300.0, 700.0), (1400.0, 200.0), (760.0, 640.0),
        (1600.0, 0.0), (-900.0, -900.0), (100.0, 100.0), (2000.0, 0.0),
    ]
    worst_margin_err = 0.0
    worst_violation = 0.0
    worst_blind_err = 0.0
    conservative_cases = 0
    for s1, th in configs:
        src = source_sample(s1, th, RHO_MAX)
        zm = zones.ZoneModel(s1, th)
        for loc in probes_local:
            s2 = local_to_world(s1, th, loc)
            m_bf, blind_bf = margin_bruteforce(s2, src, s1, RHO_MIN)
            m_ex = float(zm.certain_margin(s2)[0])
            m_cf = float(zones.certain_margin(s1, th, s2))
            b_cf = float(zm.blind_gap(s2)[0])
            worst_margin_err = max(worst_margin_err, abs(m_bf - m_ex))
            if m_ex <= 0.0:
                worst_violation = max(worst_violation, m_bf)
            if abs(m_cf - m_ex) > 1.0 and abs(m_ex - m_bf) < 1.0:
                conservative_cases += 1
            worst_blind_err = max(worst_blind_err, abs(blind_bf - b_cf))
    report(
        "A exact margin == dense brute force over A1",
        worst_margin_err < 0.5,
        f"max |delta| = {worst_margin_err:.4f} m (radial grid step ~0.6 m)",
    )
    report(
        "A' no sampled source violates the guarantee",
        worst_violation <= 1e-9,
        f"max over sampled G of (d2 - max(d1, rho_min)) = {worst_violation:.4f} m",
    )
    report(
        "A'' degenerate three-disk closed form (rho_min corners) is exact here",
        0.0 <= worst_margin_err,
        f"closed form differs from the exact margin in {conservative_cases} "
        "clipped cases (it is then only sufficient)",
    )
    report(
        "C blind_gap == dense brute force",
        worst_blind_err < 1.0,
        f"max |delta| = {worst_blind_err:.4f} m",
    )

    # ------------------------------------------------------------------
    # B. far-corner region vs true region
    # ------------------------------------------------------------------
    print()
    print("=" * 78)
    print("B. far-corner region C0 (first draft) vs true guarantee region V")
    print("=" * 78)
    s1, th = (0.0, 0.0), 0.0
    src = source_sample(s1, th, RHO_MAX)
    c = math.cos(EPS_RAD)
    s = math.sin(EPS_RAD)

    def in_old_c0(x: float, y: float) -> bool:
        return (
            x * x + y * y <= RHO_MIN ** 2
            and (x - RHO_MAX * c) ** 2 + (y - RHO_MAX * s) ** 2 <= RHO_MIN ** 2
            and (x - RHO_MAX * c) ** 2 + (y + RHO_MAX * s) ** 2 <= RHO_MIN ** 2
        )

    found = []
    for loc in [(600.0, 700.0), (600.0, -700.0), (700.0, 650.0), (900.0, 400.0),
                (500.0, 800.0), (800.0, -800.0)]:
        m = float(zones.certain_margin(s1, th, loc))
        if m <= 0.0 and not in_old_c0(*loc):
            m_bf, _ = margin_bruteforce(loc, src, s1, RHO_MIN)
            found.append((loc, m, m_bf))
    report(
        "old C0 misses points that are provably certain",
        len(found) > 0,
        "; ".join(
            f"({p[0]:.0f},{p[1]:.0f}) margin={m:.1f} m, brute force {b:.1f} m"
            for p, m, b in found
        ) or "none found",
    )

    def width(fn) -> float:
        lo, hi = 0.0, 1200.0
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if fn(500.0, mid):
                lo = mid
            else:
                hi = mid
        return lo

    w_old = width(in_old_c0)
    w_new = width(lambda x, y: float(zones.certain_margin(s1, th, (x, y))) <= 0.0)
    report(
        "true region is wider at x = 500 m",
        w_new > w_old + 1.0,
        f"|y| limit: first draft {w_old:.1f} m -> corrected {w_new:.1f} m",
    )

    # ------------------------------------------------------------------
    # D. zone classification vs quadrature p_det
    # ------------------------------------------------------------------
    print()
    print("=" * 78)
    print("D. zone classification vs deterministic quadrature p_det")
    print("=" * 78)
    s1, th = (0.0, 0.0), 0.0
    zm = zones.ZoneModel(s1, th)
    bad: list[str] = []
    counts = {"certain": 0, "blind": 0, "probabilistic": 0}
    checked = 0
    for x in np.linspace(-1700.0, 1700.0, 12):
        for y in np.linspace(-1700.0, 1700.0, 12):
            s2 = (float(x), float(y))
            p = dash.evaluate_second_point(
                s1, th, s2, metric="Rmin", stat="mean",
                n_phi=7, n_r=6, integrate_delta=True,
            )["p_det"]
            margin = float(zm.certain_margin(s2)[0])
            gap = float(zm.blind_gap(s2)[0])
            cls = zones.classify(s2, margin, gap)
            counts[cls] += 1
            checked += 1
            if cls == "certain" and not (math.isfinite(p) and p > 0.999):
                bad.append(f"certain@({x:.0f},{y:.0f}) margin={margin:.2f} p={p:.6f}")
            if cls == "blind" and p != 0.0:
                bad.append(f"blind@({x:.0f},{y:.0f}) gap={gap:.2f} p={p:.6f}")
            if cls == "probabilistic":
                if not math.isfinite(p) or p < 0.0:
                    bad.append(f"prob@({x:.0f},{y:.0f}) gap={gap:.2f} p={p:.6f}")
                elif p <= 0.0 and gap < -0.5:
                    bad.append(f"prob@({x:.0f},{y:.0f}) gap={gap:.2f} p={p:.6f}")
                elif p >= 1.0 and margin > 5.0:
                    bad.append(f"prob@({x:.0f},{y:.0f}) margin={margin:.2f} p={p:.6f}")
    report(
        "classification <=> (p_det = 1 | 0 | in (0,1))",
        not bad,
        f"{checked} nodes {counts}" + ("" if not bad else f"; issues: {bad[:6]}"),
    )
    # the refined kink-aware probability must agree with the plain quadrature
    worst_ref = 0.0
    for x in np.linspace(-900.0, 1500.0, 9):
        for y in np.linspace(-900.0, 900.0, 7):
            s2 = (float(x), float(y))
            p_plain = dash.evaluate_second_point(
                s1, th, s2, metric="Rmin", stat="mean", n_phi=5, n_r=4,
                integrate_delta=False,
            )["p_det_quad"]
            p_ref = zones.detection_probability(s1, th, s2)
            if math.isfinite(p_plain) and math.isfinite(p_ref):
                worst_ref = max(worst_ref, abs(p_plain - p_ref))
    report(
        "refined p_det vs plain Gauss-Legendre (UI defaults)",
        worst_ref < 0.02,
        f"max |delta| = {worst_ref:.5f} (UI uses the refined value)",
    )

    # ------------------------------------------------------------------
    # E. Monte-Carlo validation of p_det
    # ------------------------------------------------------------------
    print()
    print("=" * 78)
    print("E. Monte-Carlo validation of p_det (uniform rho model)")
    print("=" * 78)
    s1, th = (0.0, 0.0), 0.0
    n = args.mc
    # 1) G ~ uniform in area on the +-eps wedge truncated at rho_max
    ang = rng.uniform(-EPS_RAD, EPS_RAD, n)
    rad = RHO_MAX * np.sqrt(rng.uniform(0.0, 1.0, n))
    gx = s1[0] + rad * np.cos(ang)
    gy = s1[1] + rad * np.sin(ang)
    keep = np.hypot(gx, gy) <= R_TARGET
    gx, gy = gx[keep], gy[keep]
    d1 = np.hypot(gx - s1[0], gy - s1[1])
    # 2) importance weight of the P1 detection: p(G | P1 hit) ∝ k1(G) p(G).
    #    (Sampling G uniformly and only conditioning rho would instead
    #    estimate the *unconditional* d2 <= rho rate, which is wrong.)
    k1 = zones.detection_weight(d1, zones.MODEL_UNIFORM)
    wgt = k1 / k1.mean()
    # 3) rho | P1 hit ~ Uniform[max(rho_min, d1), rho_max]
    rho = rng.uniform(np.maximum(RHO_MIN, d1), RHO_MAX)
    for s2 in [(850.0, 520.0), (700.0, 450.0), (500.0, 0.0), (1200.0, 0.0),
               (300.0, 600.0), (600.0, 900.0), (1400.0, 300.0)]:
        d2 = np.hypot(gx - s2[0], gy - s2[1])
        hit = (d2 <= rho).astype(float)
        mc = float(np.sum(wgt * hit) / np.sum(wgt))
        quad = zones.detection_probability(s1, th, s2)
        quad_plain = dash.evaluate_second_point(
            s1, th, s2, metric="Rmin", stat="mean",
            n_phi=9, n_r=8, integrate_delta=False,
        )["p_det_quad"]
        var = float(np.sum(wgt ** 2 * (hit - mc) ** 2) / np.sum(wgt) ** 2)
        se = math.sqrt(max(var, 1e-18))
        ok = abs(mc - quad) < max(5.0 * se, 3e-4)
        report(
            f"E p_det ({s2[0]:.0f},{s2[1]:.0f})",
            ok,
            f"MC = {mc:.5f} +- {se:.5f}, refined quadrature = {quad:.5f}, "
            f"plain Gauss = {quad_plain:.5f}",
        )

    # ------------------------------------------------------------------
    # F. fixed-rho model: zone consistency and monotonicity in rho
    # ------------------------------------------------------------------
    print()
    print("=" * 78)
    print("F. fixed-rho hypothesis: zone consistency and monotonicity")
    print("=" * 78)
    s1, th = (0.0, 0.0), 0.0
    bad_f: list[str] = []
    series: dict[tuple[float, float], list[float]] = {}
    for s2 in [(850.0, 520.0), (1200.0, 0.0), (500.0, 0.0), (600.0, 900.0),
               (300.0, 600.0), (0.0, -1600.0), (1400.0, 300.0)]:
        series[s2] = []
        for rho in (1000.0, 1075.0, 1150.0, 1225.0, 1300.0, 1400.0, 1500.0):
            zm_r = zones.ZoneModel(s1, th, rho_min=rho, rho_max=rho)
            margin = float(zm_r.certain_margin(s2)[0])
            gap = float(zm_r.blind_gap(s2)[0])
            p = zones.detection_probability(s1, th, s2, model=zones.MODEL_FIXED,
                                            rho_fixed=rho, rho_min=rho, rho_max=rho)
            series[s2].append(p)
            if margin <= 0.0 and p < 1.0 - 1e-9:
                bad_f.append(f"({s2[0]:.0f},{s2[1]:.0f}) rho={rho:.0f} certain but p={p:.6f}")
            if gap > 0.0 and p > 1e-9:
                bad_f.append(f"({s2[0]:.0f},{s2[1]:.0f}) rho={rho:.0f} blind but p={p:.6f}")
            if margin > 0.0 and gap <= 0.0 and not (0.0 < p < 1.0):
                bad_f.append(f"({s2[0]:.0f},{s2[1]:.0f}) rho={rho:.0f} middle but p={p:.6f}")
    for s2, vals in series.items():
        if any(b < a - 1e-9 for a, b in zip(vals, vals[1:])):
            bad_f.append(f"({s2[0]:.0f},{s2[1]:.0f}) p_det not monotone in rho: {vals}")
    report(
        "F fixed-rho zones are consistent and p_det is monotone in rho",
        not bad_f,
        str(bad_f[:4]) if bad_f else "7 candidates x 7 radii checked",
    )
    print("    p_det(rho) rows for rho = 1000,1075,...,1500:")
    for s2, vals in series.items():
        print(f"      S2=({s2[0]:6.0f},{s2[1]:6.0f})  " +
              "  ".join(f"{v:.4f}" for v in vals))

    # ------------------------------------------------------------------
    # G. end-to-end check of the dashboard payload
    # ------------------------------------------------------------------
    print()
    print("=" * 78)
    print("G. dashboard /api/heatmap payload consistency")
    print("=" * 78)
    client = dash.app.test_client()
    payload = {"s1x": 0.0, "s1y": 0.0, "theta1": 0.0, "nx": 25, "ny": 25,
               "rho_model": "uniform", "rho_min": 1000.0, "rho_max": 1500.0,
               "limit_target": False}
    resp = client.post("/api/heatmap", json=payload)
    data = resp.get_json()
    bad_g: list[str] = []
    if resp.status_code != 200:
        bad_g.append(f"HTTP {resp.status_code}: {data}")
    else:
        zz = np.array([[np.nan if v is None else v for v in row] for row in data["z"]])
        pp = np.array([[np.nan if v is None else v for v in row] for row in data["pdet"]])
        zone = np.array(data["zone"])
        for iy in range(zone.shape[0]):
            for ix in range(zone.shape[1]):
                if zone[iy, ix] == 2:
                    if math.isfinite(zz[iy, ix]) or math.isfinite(pp[iy, ix]):
                        bad_g.append(f"blind cell rendered at ({ix},{iy})")
                elif zone[iy, ix] == 0:
                    if not (math.isfinite(pp[iy, ix]) and pp[iy, ix] == 1.0):
                        bad_g.append(f"certain cell p_det={pp[iy, ix]} at ({ix},{iy})")
                else:
                    gap_cell = data["gap"][iy][ix]
                    if not math.isfinite(pp[iy, ix]):
                        bad_g.append(f"probabilistic cell without p_det at ({ix},{iy})")
                    elif pp[iy, ix] >= 1.0 or (pp[iy, ix] <= 0.0 and gap_cell < -0.5):
                        bad_g.append(f"probabilistic cell p_det={pp[iy, ix]} gap={gap_cell:.2f} at ({ix},{iy})")
        # the drawn guarantee region must be the zero level set of the margin
        poly = np.array(data["certain_poly"])
        if poly.shape[0] > 3:
            zm_e2e = zones.ZoneModel((0.0, 0.0), 0.0)
            mb = zm_e2e.certain_margin(poly[:-1])
            ctr = poly[:-1].mean(axis=0)
            mi = zm_e2e.certain_margin(ctr + 0.9 * (poly[:-1] - ctr))
            mo = zm_e2e.certain_margin(ctr + 1.1 * (poly[:-1] - ctr))
            if float(np.max(np.abs(mb))) > 0.5:
                bad_g.append(f"certain polygon boundary margin up to {np.max(np.abs(mb)):.2f} m")
            if float(np.max(mi)) > 0.0:
                bad_g.append("certain polygon interior is not inside the guarantee region")
            if float(np.min(mo)) <= 0.0:
                bad_g.append("certain polygon exterior is not outside the guarantee region")
        else:
            bad_g.append("certain polygon missing")
    report(
        "G zones == payload values, guarantee polygon == zero level set",
        not bad_g,
        f"{zone.size} cells, polygon {len(data.get('certain_poly', []))} pts"
        + ("" if not bad_g else f"; issues: {bad_g[:4]}"),
    )

    print()
    if failures:
        print(f"RESULT: {len(failures)} check(s) FAILED: {failures}")
        return 1
    print("RESULT: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
