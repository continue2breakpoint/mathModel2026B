#!/usr/bin/env python3
"""Interactive heat-map dashboard for Q2 second-point selection.

Run:
    PYTHONPATH=cpp python3 script/q2_dashboard.py --port 8055

Then open http://127.0.0.1:8055 .

Decision variables vs. environmental parameters
-----------------------------------------------
The only thing we decide in Q2 is the second detection point S2.  The first
point S1, its reading theta1 and the jammer's effective reception radius rho
are *given* by the environment, exactly like the unknown source position:
they are not ours to choose, so the dashboard exposes all of them as knobs and
classifies every candidate S2 for the whole admissible range

    rho in [rho_min, rho_max] = [1000, 1500] m       (B题 附录2.2).

Zones in the S2 plane (possible source set A1 = wedge(S1,theta1+-1deg)
∩ D(S1,rho_max) ∩ D(O,1800))
--------------------------------------------------------------------------
* 一定测到 / certain     : d2(G) <= max(d1(G), rho_min) for every G in A1,
                           i.e. even the smallest possible rho suffices.
                           Boundary = level 0 of `certain_margin`.
* 一定测不到 / blind     : d2(G) > rho_max for every G in A1, i.e. no possible
                           source can be heard from S2.  These cells carry no
                           information, are not rendered, and are shaded
                           semi-transparent red.
* 概率测到 / probabilistic: everything in between; the colour/curve is the
                           probability Pr(second reading succeeds | P1 reading).
                           It equals 1 exactly on the certain zone and 0 exactly
                           on the blind zone (verified in
                           script/q2_rho_validation.py).

rho models (the uncertainty is not ours to remove, only to cover)
-----------------------------------------------------------------
* uniform : rho ~ U[rho_min, rho_max];   weight w(d) = (rho_max - d)_+ after
            conditioning on the first hit, i.e. rho >= max(rho_min, d1).
* fixed   : rho = rho_fixed is assumed known; w(d) = 1{d <= rho_fixed}.
* interval: only rho in [rho_min, rho_max] is known; w(d) = 1{d <= rho_max},
            so p_det is the 0/1 "possible" indicator and the three zones are
            the information to read off the map.

The backend uses the C++ geometry kernel and deterministic, kink-aware
Gauss-Legendre quadrature.  It is not a Monte-Carlo app.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

import numpy as np
from flask import Flask, Response, jsonify, request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cpp"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import geom_cpp  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Cannot import geom_cpp. Build it first with:\n"
        "    ./cpp/build.sh"
    ) from exc

import q2_zones as zones  # noqa: E402

try:
    import plotly.offline as poff  # type: ignore
except ImportError:  # pragma: no cover
    poff = None


DEG2RAD = math.pi / 180.0
R_TARGET = zones.R_TARGET
RHO_MIN = zones.RHO_MIN
RHO_MAX = zones.RHO_MAX
EPS_DEG = zones.EPS_DEG
CLEAR_RADIUS = 20.0
APP_VERSION = "2026-09-11-rho-zones-v5"

MODEL_UNIFORM = zones.MODEL_UNIFORM
MODEL_FIXED = zones.MODEL_FIXED
MODEL_INTERVAL = zones.MODEL_INTERVAL
RHO_MODELS = zones.RHO_MODELS

# recommended local candidates (local frame: x along theta1)
RECOMMENDED_LOCAL = [(850.0, 520.0), (850.0, -520.0)]

app = Flask(__name__)
from q2_matrix_dashboard import matrix_app
app.register_blueprint(matrix_app)


def _gauss_legendre(n: int) -> tuple[np.ndarray, np.ndarray]:
    return np.polynomial.legendre.leggauss(n)


def _unit_deg(deg: float) -> tuple[float, float]:
    a = deg * DEG2RAD
    return math.cos(a), math.sin(a)


def _wrap360(x: float) -> float:
    x = math.fmod(x, 360.0)
    return x + 360.0 if x < 0.0 else x


def _local_to_world(
    s1: tuple[float, float], theta1: float, local: tuple[float, float]
) -> tuple[float, float]:
    c, s = _unit_deg(theta1)
    return (s1[0] + local[0] * c - local[1] * s,
            s1[1] + local[0] * s + local[1] * c)


def _make_observations(
    s1: tuple[float, float],
    theta1: float,
    s2: tuple[float, float],
    theta2: float,
) -> list["geom_cpp.Observation"]:
    return [
        geom_cpp.Observation(geom_cpp.Vec2(s1[0], s1[1]), theta1, EPS_DEG, RHO_MAX),
        geom_cpp.Observation(geom_cpp.Vec2(s2[0], s2[1]), theta2, EPS_DEG, RHO_MAX),
    ]


def _build_region(
    s1: tuple[float, float],
    theta1: float,
    s2: tuple[float, float],
    theta2: float,
    clip_rho: float,
    bound_sides: int,
    disk_sides: int,
) -> "geom_cpp.ConvexRegion | None":
    """Target disk ∩ two wedges ∩ D(S1,clip_rho) ∩ D(S2,clip_rho).

    ``clip_rho`` is rho_max for the uncertain models (the necessary condition
    d <= rho <= rho_max) and rho_fixed when rho is assumed known.
    """
    region = geom_cpp.build_region(
        _make_observations(s1, theta1, s2, theta2),
        R_TARGET,
        bound_sides,
    )
    if region.is_empty():
        return None

    for center in (s1, s2):
        region = geom_cpp.clip_region_by_disk(
            region, geom_cpp.Vec2(center[0], center[1]), clip_rho, disk_sides
        )
        if region.is_empty():
            return None
    return region


def _region_metrics(
    region: "geom_cpp.ConvexRegion",
) -> tuple[float, float, float, int]:
    """Return (area, diameter, R_min, N20_lower_bound)."""
    verts = region.vertices
    n = len(verts)
    area2 = 0.0
    for i in range(n):
        p = verts[i]
        q = verts[(i + 1) % n]
        area2 += p.x * q.y - q.x * p.y
    area = 0.5 * abs(area2)

    diameter = geom_cpp.rotating_calipers_diameter(region).distance
    r_min = geom_cpp.brute_force_min_enclosing_circle(region).radius

    if r_min <= CLEAR_RADIUS + 1e-9:
        n_lb = 1
    else:
        n_lb = max(
            2,
            int(math.ceil(area / (math.pi * CLEAR_RADIUS * CLEAR_RADIUS) - 1e-9)),
            int(math.ceil(diameter / (2.0 * CLEAR_RADIUS) - 1e-9)),
        )
    return float(area), float(diameter), float(r_min), int(n_lb)


def effective_rho_bounds(
    rho_model: str, rho_min: float, rho_max: float, rho_fixed: float
) -> tuple[float, float, float]:
    """Return (rho_min_eff, rho_max_eff, clip_rho) for the given model."""
    if rho_model == MODEL_FIXED:
        return rho_fixed, rho_fixed, rho_fixed
    return rho_min, rho_max, rho_max


def evaluate_second_point(
    s1: tuple[float, float],
    theta1: float,
    s2: tuple[float, float],
    metric: str = "Rmin",
    stat: str = "mean",
    n_phi: int = 5,
    n_r: int = 4,
    integrate_delta: bool = True,
    bound_sides: int = 128,
    disk_sides: int = 128,
    rho_model: str = MODEL_UNIFORM,
    rho_min: float = RHO_MIN,
    rho_max: float = RHO_MAX,
    rho_fixed: float = RHO_MAX,
    want_metric: bool = True,
) -> dict[str, float]:
    """Deterministic quadrature for one candidate S2.

    Returns the (kink-aware) second-detection probability, the plain
    quadrature value for cross-checking, and the mean/variance of the metric
    over the posterior of the source and of the second bearing error.
    """
    if metric not in {"Rmin", "N20", "pdet"}:
        raise ValueError("metric must be 'Rmin', 'N20' or 'pdet'")
    if stat not in {"mean", "var"}:
        raise ValueError("stat must be 'mean' or 'var'")

    rho_lo, rho_hi, clip_rho = effective_rho_bounds(
        rho_model, rho_min, rho_max, rho_fixed
    )
    p_det = zones.detection_probability(
        s1, theta1, s2, model=rho_model, rho_min=rho_lo, rho_max=rho_hi,
        rho_fixed=rho_fixed, eps_deg=EPS_DEG, r_target=R_TARGET,
    )
    out = {
        "p_det": float(p_det),
        "p_det_quad": float("nan"),
        "mean": float("nan"),
        "var": float("nan"),
        "empty": 0.0,
    }
    if metric == "pdet" or not want_metric:
        return out

    xp, wp = _gauss_legendre(n_phi)
    phi_nodes = EPS_DEG * DEG2RAD * xp
    phi_weights = EPS_DEG * DEG2RAD * wp

    n_delta = 3 if integrate_delta else 1
    xd, wd = _gauss_legendre(n_delta)
    if integrate_delta:
        delta_nodes = EPS_DEG * xd
        delta_weights = EPS_DEG * wd
    else:
        delta_nodes = np.array([0.0])
        delta_weights = np.array([2.0 * EPS_DEG])

    xg, wg = _gauss_legendre(n_r)
    norm_first = 0.0
    num_det = 0.0
    weight_sum = 0.0
    value_sum = 0.0
    value2_sum = 0.0
    empty = 0

    x1, y1 = s1
    x2, y2 = s2

    for phi, w_phi in zip(phi_nodes, phi_weights):
        direction_deg = theta1 + math.degrees(float(phi))
        r_max = min(clip_rho, zones.ray_target_radius((x1, y1), direction_deg,
                                                      R_TARGET))
        if r_max <= 1e-12:
            continue
        # split at rho_lo: the detection weight has a kink there
        if r_max <= rho_lo:
            intervals = [(0.0, r_max)]
        else:
            intervals = [(0.0, rho_lo), (rho_lo, r_max)]

        for lo, hi in intervals:
            if hi - lo <= 1e-12:
                continue
            r_nodes = 0.5 * (lo + hi) + 0.5 * (hi - lo) * xg
            r_weights = 0.5 * (hi - lo) * wg
            for r, w_r in zip(r_nodes, r_weights):
                rr = float(r)
                wr = float(w_r)
                ux, uy = _unit_deg(direction_deg)
                gx = x1 + rr * ux
                gy = y1 + rr * uy
                d1 = rr
                d2 = math.hypot(gx - x2, gy - y2)

                k1 = float(zones.detection_weight(d1, rho_model, rho_lo, rho_hi,
                                                  rho_fixed))
                k12 = float(zones.detection_weight(max(d1, d2), rho_model, rho_lo,
                                                   rho_hi, rho_fixed))
                w_g = float(w_phi) * wr * rr
                norm_first += k1 * w_g
                nd = k12 * w_g
                num_det += nd
                if nd <= 0.0:
                    continue

                beta2 = _wrap360(math.degrees(math.atan2(gy - y2, gx - x2)))
                for delta, w_delta in zip(delta_nodes, delta_weights):
                    region = _build_region(
                        (x1, y1), theta1, (x2, y2), beta2 + float(delta),
                        clip_rho, bound_sides, disk_sides,
                    )
                    if region is None:
                        empty += 1
                        continue
                    _, _, r_min, n_lb = _region_metrics(region)
                    value = r_min if metric == "Rmin" else float(n_lb)
                    ww = nd * float(w_delta)
                    weight_sum += ww
                    value_sum += ww * value
                    value2_sum += ww * value * value

    out["empty"] = float(empty)
    if norm_first > 0.0:
        out["p_det_quad"] = num_det / norm_first
    if weight_sum > 0.0:
        mean = value_sum / weight_sum
        out["mean"] = float(mean)
        out["var"] = float(max(0.0, value2_sum / weight_sum - mean * mean))
    return out


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------
@app.route("/plotly.min.js")
def plotly_js() -> Response:
    if poff is None:
        return Response("alert('plotly is not installed');", mimetype="application/javascript")
    return Response(poff.get_plotlyjs(), mimetype="application/javascript")


@app.route("/version")
def version() -> Response:
    return jsonify({"app": "q2_dashboard", "version": APP_VERSION})


@app.route("/")
def index() -> Response:
    page = HTML_PAGE.replace("__VERSION__", APP_VERSION)
    return Response(
        page,
        mimetype="text/html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


def _read_rho_params(data: dict) -> tuple[str, float, float, float]:
    model = str(data.get("rho_model", MODEL_UNIFORM))
    if model not in RHO_MODELS:
        raise ValueError(f"rho_model must be one of {RHO_MODELS}")
    rho_min = float(data.get("rho_min", RHO_MIN))
    rho_max = float(data.get("rho_max", RHO_MAX))
    rho_fixed = float(data.get("rho_fixed", rho_max))
    if not (0.0 < rho_min <= rho_max):
        raise ValueError("require 0 < rho_min <= rho_max")
    rho_fixed = min(max(rho_fixed, 1.0), 3000.0)
    return model, rho_min, rho_max, rho_fixed


@app.route("/api/heatmap", methods=["POST"])
def api_heatmap() -> Response:
    data = request.get_json(force=True)
    try:
        s1 = (float(data.get("s1x", 0.0)), float(data.get("s1y", 0.0)))
        theta1 = float(data.get("theta1", 0.0))
        u_min = float(data.get("u_min", -1800.0))
        u_max = float(data.get("u_max", 1800.0))
        v_min = float(data.get("v_min", -1800.0))
        v_max = float(data.get("v_max", 1800.0))
        nx = max(5, min(121, int(data.get("nx", 27))))
        ny = max(5, min(121, int(data.get("ny", 27))))
        metric = str(data.get("metric", "Rmin"))
        stat = str(data.get("stat", "mean"))
        integrate_delta = bool(data.get("integrate_delta", True))
        n_phi = max(2, min(10, int(data.get("n_phi", 5))))
        n_r = max(2, min(12, int(data.get("n_r", 4))))
        disk_sides = max(24, min(360, int(data.get("disk_sides", 128))))
        bound_sides = max(24, min(360, int(data.get("bound_sides", 128))))
        limit_target = bool(data.get("limit_target", False))
        rho_model, rho_min, rho_max, rho_fixed = _read_rho_params(data)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"bad request: {exc}"}), 400

    if u_max <= u_min or v_max <= v_min:
        return jsonify({"error": "empty plotting range"}), 400

    u_values = np.linspace(u_min, u_max, nx)
    v_values = np.linspace(v_min, v_max, ny)
    gx, gy = np.meshgrid(u_values, v_values)          # (ny,nx)
    pts = np.column_stack([gx.ravel(), gy.ravel()])

    t0 = time.time()

    # ---- zones (vectorised, exact geometry) -------------------------------
    zm = zones.ZoneModel(s1, theta1, rho_min=rho_min, rho_max=rho_max,
                         eps_deg=EPS_DEG, r_target=R_TARGET)
    margin = zm.certain_margin(pts).reshape(ny, nx)
    gap = zm.blind_gap(pts).reshape(ny, nx)
    # blind uses >= 0: dist(S2, A1) == rho_max is a tangency whose
    # successful set has measure zero, hence p_det == 0 (see q2_zones.classify)
    zone = np.where(margin <= 1e-9, 0, np.where(gap > -1e-6, 2, 1)).astype(int)
    in_target = (gx * gx + gy * gy) <= R_TARGET * R_TARGET + 1e-6

    # ---- metric + probability field ---------------------------------------
    z = np.full((ny, nx), np.nan, dtype=float)
    pdet = np.full((ny, nx), np.nan, dtype=float)
    skipped = 0
    for iy in range(ny):
        for ix in range(nx):
            if zone[iy, ix] == 2 or (limit_target and not in_target[iy, ix]):
                skipped += 1
                continue
            s2 = (float(u_values[ix]), float(v_values[iy]))
            res = evaluate_second_point(
                s1, theta1, s2,
                metric=metric, stat=stat, n_phi=n_phi, n_r=n_r,
                integrate_delta=integrate_delta,
                bound_sides=bound_sides, disk_sides=disk_sides,
                rho_model=rho_model, rho_min=rho_min, rho_max=rho_max,
                rho_fixed=rho_fixed,
            )
            p = res["p_det"]
            if zone[iy, ix] == 0:
                p = 1.0
            pdet[iy, ix] = p
            if metric == "pdet":
                z[iy, ix] = p
            else:
                z[iy, ix] = res[stat]

    # ---- overlays ---------------------------------------------------------
    # V = ∩ D(c, rho_min) over the extreme centres of the constraint set
    # {vertices of A(rho_min)} ∪ {inner arc}; the arc adds no new extreme
    # point, so the polygon vertices alone determine the region exactly.
    certain_centers = [tuple(v) for v in zm.near_poly]
    certain_poly = zones.disks_intersection_polygon(certain_centers, rho_min)

    # Possible source set A(rho) drawn on the map: the sector W(S1,theta1,+-1deg)
    # ∩ D(S1,rho) ∩ target disk.  With rho assumed known there is a single
    # radius rho_fixed; otherwise the interval gives the two bounds rho_min and
    # rho_max (the possible set is monotone in rho, so these bracket it).
    if rho_model == MODEL_FIXED:
        source_polys = [{
            "rho": rho_fixed,
            "label": f"ρ_fixed={rho_fixed:.0f} m",
            "poly": zones.possible_set_polygon(
                s1, theta1, rho_fixed, EPS_DEG, R_TARGET),
        }]
    else:
        source_polys = [
            {"rho": rho_min, "label": f"ρ_min={rho_min:.0f} m", "poly": zm.near_poly},
            {"rho": rho_max, "label": f"ρ_max={rho_max:.0f} m", "poly": zm.outer_poly},
        ]
    source_polys = [
        {"rho": p["rho"], "label": p["label"], "poly": p["poly"].tolist()}
        for p in source_polys if p["poly"].size
    ]
    recommended = [_local_to_world(s1, theta1, loc) for loc in RECOMMENDED_LOCAL]

    def _clean(arr: np.ndarray) -> list:
        return [
            [None if not math.isfinite(float(x)) else float(x) for x in row]
            for row in arr
        ]

    cell_area = ((u_max - u_min) / max(nx - 1, 1)) * ((v_max - v_min) / max(ny - 1, 1))
    counts = {
        "certain": int(np.sum(zone == 0)),
        "probabilistic": int(np.sum(zone == 1)),
        "blind": int(np.sum(zone == 2)),
        "computed": int(np.sum(np.isfinite(z))),
        "skipped": int(skipped),
    }
    areas_km2 = {k: counts[k] * cell_area / 1e6
                 for k in ("certain", "probabilistic", "blind")}

    finite = np.isfinite(z)
    best = None
    if finite.any():
        pool = finite & (zone == 0) if (zone == 0).any() else finite
        if not pool.any():
            pool = finite
        cand = np.where(pool)
        vals = z[cand]
        k = int(np.argmin(vals))
        iy, ix = cand[0][k], cand[1][k]
        best = {
            "u": float(u_values[ix]),
            "v": float(v_values[iy]),
            "value": float(z[iy, ix]),
            "pdet": float(pdet[iy, ix]) if math.isfinite(pdet[iy, ix]) else None,
            "certain": bool(zone[iy, ix] == 0),
        }

    return jsonify({
        "u": u_values.tolist(),
        "v": v_values.tolist(),
        "z": _clean(z),
        "pdet": _clean(pdet),
        "margin": _clean(margin),
        "gap": _clean(gap),
        "zone": zone.tolist(),
        "in_target": in_target.tolist(),
        "certain_poly": certain_poly.tolist() if certain_poly.size else [],
        "source_polys": source_polys,
        "recommended": [list(p) for p in recommended],
        "metric": metric,
        "stat": stat,
        "rho": {"model": rho_model, "min": rho_min, "max": rho_max,
                "fixed": rho_fixed},
        "s1": [s1[0], s1[1]],
        "theta1": theta1,
        "counts": counts,
        "areas_km2": areas_km2,
        "best": best,
        "elapsed_s": time.time() - t0,
    })


@app.route("/api/probe", methods=["POST"])
def api_probe() -> Response:
    """Detail for one candidate S2 plus a sweep over the non-decision rho."""
    data = request.get_json(force=True)
    try:
        s1 = (float(data.get("s1x", 0.0)), float(data.get("s1y", 0.0)))
        theta1 = float(data.get("theta1", 0.0))
        s2x = float(data.get("s2x", 0.0))
        s2y = float(data.get("s2y", 0.0))
        metric = str(data.get("metric", "Rmin"))
        rho_model, rho_min, rho_max, rho_fixed = _read_rho_params(data)
        n_sweep = max(2, min(13, int(data.get("n_sweep", 7))))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"bad request: {exc}"}), 400

    s2 = (s2x, s2y)
    zm = zones.ZoneModel(s1, theta1, rho_min=rho_min, rho_max=rho_max,
                         eps_deg=EPS_DEG, r_target=R_TARGET)
    margin = float(zm.certain_margin(s2)[0])
    gap = float(zm.blind_gap(s2)[0])
    zone = zones.classify(s2, margin, gap)

    if metric == "pdet":
        res = evaluate_second_point(s1, theta1, s2, metric="pdet",
                                    rho_model=rho_model, rho_min=rho_min,
                                    rho_max=rho_max, rho_fixed=rho_fixed)
    else:
        res = evaluate_second_point(s1, theta1, s2, metric=metric, stat="mean",
                                    rho_model=rho_model, rho_min=rho_min,
                                    rho_max=rho_max, rho_fixed=rho_fixed)

    dx, dy = s2[0] - s1[0], s2[1] - s1[1]
    bearing = _wrap360(math.degrees(math.atan2(dy, dx)))
    lateral = abs(-dx * math.sin(theta1 * DEG2RAD) + dy * math.cos(theta1 * DEG2RAD))
    along = dx * math.cos(theta1 * DEG2RAD) + dy * math.sin(theta1 * DEG2RAD)
    dist = math.hypot(dx, dy)

    sweep = []
    for rho in np.linspace(rho_min, rho_max, n_sweep):
        rho_v = float(rho)
        zm_r = zones.ZoneModel(s1, theta1, rho_min=rho_v, rho_max=rho_v,
                               eps_deg=EPS_DEG, r_target=R_TARGET)
        m_r = float(zm_r.certain_margin(s2)[0])
        g_r = float(zm_r.blind_gap(s2)[0])
        p_r = zones.detection_probability(
            s1, theta1, s2, model=MODEL_FIXED, rho_min=rho_v, rho_max=rho_v,
            rho_fixed=rho_v,
        )
        if zone == "certain":
            p_r = 1.0
        elif zone == "blind":
            p_r = 0.0
        val = None
        if metric != "pdet" and g_r <= 0.0:
            out = evaluate_second_point(
                s1, theta1, s2, metric=metric, stat="mean",
                rho_model=MODEL_FIXED, rho_min=rho_v, rho_max=rho_v,
                rho_fixed=rho_v,
            )
            val = out["mean"] if math.isfinite(out["mean"]) else None
        sweep.append({
            "rho": rho_v,
            "p_det": p_r,
            "margin": m_r,
            "gap": g_r,
            "zone": zones.classify(s2, m_r, g_r),
            "value": val,
        })

    return jsonify({
        "s2": [s2x, s2y],
        "s1": [s1[0], s1[1]],
        "theta1": theta1,
        "zone": zone,
        "margin": margin,
        "gap": gap,
        "p_det": res.get("p_det"),
        "p_det_quad": res.get("p_det_quad"),
        "value": res.get("mean"),
        "var": res.get("var"),
        "metric": metric,
        "dist_s1": dist,
        "bearing_s1_to_s2": bearing,
        "local_along": along,
        "local_lateral": lateral,
        "rho": {"model": rho_model, "min": rho_min, "max": rho_max,
                "fixed": rho_fixed},
        "sweep": sweep,
    })


HTML_PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>问题2 第二检测点选择交互热图（ρ∈[1000,1500] 全覆盖）</title>
  <script src="/plotly.min.js"></script>
  <style>
    :root { color-scheme: light; }
    body { margin:0; font-family: system-ui, -apple-system, "Segoe UI", Arial, sans-serif; background:#f6f7fb; color:#1f2430; }
    .wrap { display:flex; min-height:100vh; }
    .panel { width:372px; flex:0 0 372px; padding:14px 14px 40px; background:#fff; box-shadow:2px 0 10px rgba(0,0,0,.06); overflow:auto; max-height:100vh; }
    .main { flex:1; min-width:0; padding:16px; }
    h1 { font-size:17px; margin:0 0 6px; }
    h2 { font-size:12px; margin:14px 0 4px; color:#5b6478; text-transform:uppercase; letter-spacing:.05em; border-top:1px solid #eceff5; padding-top:8px; }
    h2:first-of-type { border-top:none; }
    label { display:block; font-size:12.5px; margin:7px 0 2px; color:#333; }
    input[type=range] { width:100%; }
    .row { display:flex; gap:6px; align-items:center; flex-wrap:wrap; }
    .row > * { min-width:0; }
    .num { width:76px; padding:3px 5px; border:1px solid #ccd2df; border-radius:6px; font-size:12.5px; }
    select, button { padding:5px 7px; border:1px solid #ccd2df; border-radius:6px; background:#fff; font-size:12.5px; }
    button.primary { background:#2f6feb; border-color:#2f6feb; color:#fff; cursor:pointer; }
    button:disabled { opacity:.55; cursor:wait; }
    .hint { font-size:11.5px; color:#697386; line-height:1.5; margin-top:4px; }
    .status { font-size:12px; color:#4b5563; min-height:16px; margin-top:6px; }
    #plot { width:100%; height:82vh; min-height:620px; background:#fff; border-radius:10px; box-shadow:0 2px 12px rgba(0,0,0,.06); }
    .legend { font-size:12px; color:#4b5563; margin-top:8px; line-height:1.6; }
    .badge { display:inline-block; background:#eef2ff; color:#2f4aa8; border-radius:5px; padding:2px 6px; margin:2px 4px 2px 0; font-size:11.5px; }
    .badge.green { background:#e6f7ee; color:#15803d; }
    .badge.amber { background:#fdf6e3; color:#a16207; }
    .badge.red { background:#fdeaea; color:#b91c1c; }
    .chk { font-size:12.5px; margin:5px 0; display:flex; gap:6px; align-items:center; }
    table.sweep { width:100%; border-collapse:collapse; font-size:11.5px; margin-top:6px; }
    table.sweep th, table.sweep td { border-bottom:1px solid #eef1f6; padding:2px 3px; text-align:right; }
    table.sweep th:first-child, table.sweep td:first-child { text-align:left; }
    .zc { color:#15803d; font-weight:600; }
    .zp { color:#a16207; font-weight:600; }
    .zb { color:#b91c1c; font-weight:600; }
  </style>
</head>
<body>
<div class="wrap">
  <aside class="panel">
    <h1>问题2 第二检测点选择热图 <span style="font-size:11px;color:#8b93a7" id="ver">__VERSION__</span></h1>
    <p><a href="/matrix">进入多观测知识矩阵热图 →</a></p>
    <div class="hint">
      决策量只有 <b>S2</b>；<b>S1、theta1、有效距离 ρ</b> 都是环境给定、不可决策的量，
      因此全部做成可调旋钮，用来检验 S2 的选取在整个可行范围内都成立。
    </div>

    <h2>1. 第一观测点 S1（不可决策）</h2>
    <label>S1.x (m) <span id="s1xVal"></span></label>
    <input id="s1x" type="range" min="-1700" max="1700" step="25" value="0">
    <label>S1.y (m) <span id="s1yVal"></span></label>
    <input id="s1y" type="range" min="-1700" max="1700" step="25" value="0">
    <label>示向角 theta1 (deg) <span id="thetaVal"></span></label>
    <input id="theta1" type="range" min="0" max="359" step="1" value="0">
    <div class="hint">S1 限制在半径 1700 m 内，保证第一点位于场地圆内部。</div>

    <h2>2. 有效接收半径 ρ（不可决策，需全覆盖）</h2>
    <div class="row">
      <span style="font-size:12.5px">ρ_min</span>
      <input class="num" id="rhoMin" type="number" value="1000" min="100" max="3000" step="25">
      <span style="font-size:12.5px">ρ_max</span>
      <input class="num" id="rhoMax" type="number" value="1500" min="100" max="3000" step="25">
    </div>
    <input id="rhoMinR" type="range" min="200" max="2900" step="25" value="1000">
    <input id="rhoMaxR" type="range" min="200" max="2900" step="25" value="1500">
    <label>ρ 的认知模型</label>
    <select id="rhoModel">
      <option value="uniform" selected>只知道区间，取 ρ~U[ρ_min,ρ_max]（概率）</option>
      <option value="fixed">假设 ρ = ρ_fixed 已知（敏感性扫描）</option>
      <option value="interval">只知道区间，不设分布（0/1 可能性）</option>
    </select>
    <label>ρ_fixed (m) <span id="rhoFixedVal"></span></label>
    <input id="rhoFixed" type="range" min="1000" max="1500" step="25" value="1250">
    <div class="hint">
      题目给定 ρ∈[1000,1500] m（附录2.2），与 ρ 的具体分布无关的结论只有三区标注：
      <span class="zc">一定测到</span>（按 ρ_min 判定）、
      <span class="zb">一定测不到</span>（按 ρ_max 判定）、
      其余为<span class="zp">概率测到</span>。ρ 模型只影响期望指标与 p_det 的数值。
    </div>

    <h2>3. 颜色指标</h2>
    <label>指标（随机变量）</label>
    <select id="metric">
      <option value="Rmin">R_min：最小覆盖圆半径 (m)</option>
      <option value="N20">N20_lb：半径 20m 圆覆盖数严格下界</option>
      <option value="pdet">p_det：第二次检测成功概率</option>
    </select>
    <label>统计量（p_det 指标下忽略）</label>
    <select id="stat">
      <option value="mean">期望</option>
      <option value="var">方差</option>
    </select>
    <label>颜色映射函数</label>
    <select id="colorMode">
      <option value="log" selected>log10(1+z)，推荐</option>
      <option value="sqrt">sqrt(z)</option>
      <option value="linear">线性</option>
      <option value="rank">分位/秩着色</option>
    </select>

    <h2>4. 区域与等值线</h2>
    <label class="chk"><input id="showCertain" type="checkbox" checked> 保证测到区（ρ_min 三个 1000 m 圆盘之交）</label>
    <label class="chk"><input id="showProbZone" type="checkbox"> 概率测得区着色</label>
    <label class="chk"><input id="showBlind" type="checkbox" checked> 必然无信号区（半透明红，不渲染热值）</label>
    <label class="chk"><input id="showPdet" type="checkbox" checked> p_det 等值线</label>
    <div class="row">
      <span style="font-size:12.5px">等值线水平</span>
      <input class="num" id="pdetLevel" type="number" value="0.999" min="0.001" max="0.9999" step="0.001">
      <span style="font-size:12.5px">+0.5 线</span>
      <input id="pdetHalf" type="checkbox" checked>
    </div>
    <label class="chk"><input id="showSourceSet" type="checkbox" checked> 干扰源可能集 A1 = P1 射线 ±1° ∩ ρ</label>
    <label class="chk"><input id="showTargetCircle" type="checkbox" checked> 场地圆 x²+y²=1800²</label>
    <label class="chk"><input id="limitTarget" type="checkbox" checked> 只计算 S2 在场地圆内的格点</label>
    <label class="chk"><input id="showRec" type="checkbox" checked> 标注推荐候选 S2（局部 (850,±520)）</label>

    <h2>5. 掩膜与数值</h2>
    <label class="chk"><input id="maskAngle" type="checkbox"> 屏蔽近平行交会带（局部 |x沿−855|≤√3|y横| 之外）</label>
    <label class="chk"><input id="maskPdet" type="checkbox"> 屏蔽 p_det 低于阈值的格点</label>
    <div class="row">
      <span style="font-size:12.5px">p_det 阈值</span>
      <input class="num" id="pdetMin" type="number" value="0.99" min="0" max="1" step="0.005">
    </div>
    <label class="chk"><input id="robustScale" type="checkbox"> 线性颜色按 2%–98% 分位裁剪</label>
    <label class="chk"><input id="integrateDelta" type="checkbox" checked> 对第二次示向误差 δ2 做三点求积</label>
    <label>热图网格分辨率 <span id="resVal">27</span> × <span id="resVal2">27</span></label>
    <input id="res" type="range" min="11" max="91" step="2" value="27">

    <h2>6. 绘图范围（场地绝对坐标 x, y）</h2>
    <div class="row">
      <input class="num" id="uMin" type="number" value="-1800" step="50">
      <input class="num" id="uMax" type="number" value="1800" step="50">
    </div>
    <div class="row">
      <input class="num" id="vMin" type="number" value="-1800" step="50">
      <input class="num" id="vMax" type="number" value="1800" step="50">
    </div>
    <button id="fitTarget" style="width:100%;margin-top:6px;">绘图范围设为场地圆外接正方形</button>
    <button id="run" class="primary" style="width:100%;margin-top:6px;">重新计算热图</button>
    <div class="status" id="status"></div>
    <div id="summary" class="legend"></div>

    <h2>7. 探针：单点检验 + ρ 扫描</h2>
    <div class="hint">点击热图任意位置即可检验该 S2；ρ 扫描固定 S2、让不可决策的 ρ 走遍 [ρ_min, ρ_max]。</div>
    <div class="row">
      <input class="num" id="probeX" type="number" value="850" step="10">
      <input class="num" id="probeY" type="number" value="520" step="10">
      <button id="probeBtn">探测该点</button>
    </div>
    <div id="probeOut" class="legend"></div>
  </aside>

  <main class="main">
    <div id="plot"></div>
    <div class="legend">
      <b>颜色</b>：<span id="colorLabel"></span>。<br>
      <b class="zc">青色实线</b>：一定测到区边界（ρ=ρ_min 下仍必然成功）；
      <b>白色点线</b>：p_det 等值线；
      <b class="zp">黄色</b>：概率测得区；
      <b class="zb">半透明红</b>：必然无信号区（所有可能源点都超出 ρ_max，第二次测量无信息，不渲染热值）；
      <b>橙色虚线</b>：场地圆 x²+y²=1800²；<b>紫色点划线</b>：干扰源可能集 A1 的边界（ρ 固定时只画 ρ_fixed 一条；ρ 不确定时同时画出 ρ_min 与 ρ_max 两条，可能源集即夹在两者之间）；<b>红色星号</b>：推荐候选 S2；<b>灰色</b>：被掩膜或无法定位的格点。
    </div>
  </main>
</div>

<script>
const $ = (id) => document.getElementById(id);
const ids = ["s1x","s1y","theta1","rhoMin","rhoMax","rhoMinR","rhoMaxR","rhoModel","rhoFixed",
             "metric","stat","res","integrateDelta","uMin","uMax","vMin","vMax","showPdet","pdetLevel","pdetHalf",
             "maskAngle","maskPdet","pdetMin","robustScale","colorMode","showTargetCircle","limitTarget",
             "showCertain","showProbZone","showBlind","showSourceSet","showRec"];
let busy = false;
let lastProbe = null;
let lastData = null;

function syncRho() {
  let lo = Number($("rhoMin").value);
  let hi = Number($("rhoMax").value);
  if (!(lo > 0)) lo = 1000;
  if (!(hi > 0)) hi = 1500;
  if (lo > hi) { const t = lo; lo = hi; hi = t; }
  $("rhoMin").value = lo; $("rhoMax").value = hi;
  $("rhoMinR").value = Math.min(2900, Math.max(200, lo));
  $("rhoMaxR").value = Math.min(2900, Math.max(200, hi));
  const rf = Number($("rhoFixed").value);
  const rfClamped = Math.min(Math.max(rf, lo), hi);
  $("rhoFixed").min = lo; $("rhoFixed").max = hi;
  if (rf !== rfClamped) $("rhoFixed").value = rfClamped;
  $("rhoFixedVal").textContent = Number($("rhoFixed").value).toFixed(0) + " m";
  $("rhoFixed").disabled = ($("rhoModel").value !== "fixed");
}

function clampS1() {
  const R = 1700.0;
  let x = Number($("s1x").value);
  let y = Number($("s1y").value);
  const rr = Math.hypot(x, y);
  if (rr > R) {
    x = x * R / rr;
    y = y * R / rr;
    $("s1x").value = x.toFixed(0);
    $("s1y").value = y.toFixed(0);
  }
}

function syncLabels() {
  $("s1xVal").textContent = Number($("s1x").value).toFixed(0);
  $("s1yVal").textContent = Number($("s1y").value).toFixed(0);
  $("thetaVal").textContent = Number($("theta1").value).toFixed(0) + "°";
  $("resVal").textContent = $("res").value;
  $("resVal2").textContent = $("res").value;
  const metricName = {Rmin:"R_min (m)", N20:"N20_lb", pdet:"p_det"}[$("metric").value];
  const statName = $("stat").value === "mean" ? "期望" : "方差";
  const modeNames = {log: "log10(1+z)", sqrt: "sqrt(z)", linear: "线性", rank: "分位/秩"};
  const mode = $("colorMode").value;
  $("colorLabel").textContent =
    ($("metric").value === "pdet" ? "p_det（第二次检测成功概率）" : statName + "[" + metricName + "]，颜色=" + (modeNames[mode] || mode));
  syncRho();
}

function payload() {
  const n = Number($("res").value);
  return {
    s1x: Number($("s1x").value), s1y: Number($("s1y").value),
    theta1: Number($("theta1").value),
    u_min: Number($("uMin").value), u_max: Number($("uMax").value),
    v_min: Number($("vMin").value), v_max: Number($("vMax").value),
    nx: n, ny: n,
    metric: $("metric").value,
    stat: $("stat").value,
    integrate_delta: $("integrateDelta").checked,
    n_phi: 5, n_r: 4, disk_sides: 128, bound_sides: 128,
    limit_target: $("limitTarget").checked,
    rho_model: $("rhoModel").value,
    rho_min: Number($("rhoMin").value),
    rho_max: Number($("rhoMax").value),
    rho_fixed: Number($("rhoFixed").value)
  };
}

function fmt(x, p) {
  if (x === null || x === undefined || !Number.isFinite(x)) return "—";
  const a = Math.abs(x);
  if ((a > 0 && a < 1e-3) || a >= 1e5) return x.toExponential(2);
  return x.toFixed(p === undefined ? 2 : p);
}

function zoneClass(z) { return z === "certain" ? "zc" : (z === "blind" ? "zb" : "zp"); }
function zoneName(z) { return z === "certain" ? "一定测到" : (z === "blind" ? "一定测不到" : "概率测到"); }

async function draw() {
  if (busy) return;
  busy = true;
  $("run").disabled = true;
  $("status").textContent = "计算中…";
  try {
    const resp = await fetch("/api/heatmap", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify(payload())
    });
    const data = await resp.json();
    if (!resp.ok || data.error) throw new Error(data.error || resp.statusText);
    lastData = data;
    renderHeat(data);
  } catch (err) {
    $("status").textContent = "错误：" + err.message;
  } finally {
    busy = false;
    $("run").disabled = false;
  }
}

function renderHeat(data) {
  const thetaRad = data.theta1 * Math.PI / 180.0;
  const cosT = Math.cos(thetaRad), sinT = Math.sin(thetaRad);

  // ---- masks ----------------------------------------------------------
  const zPlot = data.z.map(r => r.slice());
  const maskAngle = $("maskAngle").checked;
  const maskPdet = $("maskPdet").checked;
  const pdetMin = Number($("pdetMin").value);
  const finiteValues = [];
  for (let iy = 0; iy < zPlot.length; iy++) {
    const v = data.v[iy];
    for (let ix = 0; ix < zPlot[iy].length; ix++) {
      const u = data.u[ix];
      let keep = Number.isFinite(zPlot[iy][ix]);
      if (keep && maskAngle) {
        const dx = u - data.s1[0], dy = v - data.s1[1];
        const localU = dx * cosT + dy * sinT;
        const localV = -dx * sinT + dy * cosT;
        if (Math.abs(localU - 855.263) > Math.sqrt(3) * Math.abs(localV)) keep = false;
      }
      if (keep && maskPdet) {
        const p = data.pdet[iy][ix];
        if (!(Number.isFinite(p) && p >= pdetMin)) keep = false;
      }
      if (!keep) zPlot[iy][ix] = null; else finiteValues.push(zPlot[iy][ix]);
    }
  }
  finiteValues.sort((a, b) => a - b);
  const quantile = (sorted, q) => {
    if (!sorted.length) return NaN;
    const pos = (sorted.length - 1) * q, lo = Math.floor(pos), hi = Math.ceil(pos);
    return lo === hi ? sorted[lo] : sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
  };

  const colorMode = $("colorMode").value;
  const forward = (v) => colorMode === "log" ? Math.log10(1.0 + Math.max(0.0, v))
                    : colorMode === "sqrt" ? Math.sqrt(Math.max(0.0, v)) : v;
  const fmtTick = (x) => !Number.isFinite(x) ? "" :
      (((Math.abs(x) > 0 && Math.abs(x) < 1e-3) || Math.abs(x) >= 1e4) ? x.toExponential(2) : Number(x.toPrecision(4)).toString());

  const uniqueValues = [];
  for (const val of finiteValues) if (!uniqueValues.length || val !== uniqueValues[uniqueValues.length-1]) uniqueValues.push(val);
  const rankOf = (value) => {
    if (uniqueValues.length <= 1) return 0.5;
    let lo = 0, hi = uniqueValues.length - 1;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (uniqueValues[mid] < value) lo = mid + 1; else hi = mid; }
    return lo / (uniqueValues.length - 1);
  };

  const tickQs = [0, 0.25, 0.5, 0.75, 1];
  const tickOriginals = tickQs.map(q => quantile(finiteValues, q));
  let zColor = zPlot.map(row => row.map(v => Number.isFinite(v) ? forward(v) : null));
  let heatZMin, heatZMax, tickVals = [], tickTexts = [];
  if (colorMode === "rank" && finiteValues.length) {
    zColor = zPlot.map(row => row.map(v => Number.isFinite(v) ? rankOf(v) : null));
    heatZMin = 0; heatZMax = 1; tickVals = tickQs; tickTexts = tickOriginals.map(fmtTick);
  } else if (finiteValues.length) {
    heatZMin = forward(finiteValues[0]); heatZMax = forward(finiteValues[finiteValues.length-1]);
    if (colorMode === "linear" && $("robustScale").checked && finiteValues.length >= 5) {
      heatZMin = quantile(finiteValues, 0.02); heatZMax = quantile(finiteValues, 0.98);
    }
    tickVals = tickOriginals.map(forward); tickTexts = tickOriginals.map(fmtTick);
    if (heatZMin === heatZMax) { heatZMin -= 1; heatZMax += 1; }
  }

  const traces = [];
  // grey base: cells with no usable value (masked / blind / empty region)
  traces.push({
    type: "heatmap", x: data.u, y: data.v,
    z: zPlot.map(row => row.map(v => Number.isFinite(v) ? 0 : 1)),
    colorscale: [[0, "#f7f7f7"], [1, "#d5d5d5"]], zmin: 0, zmax: 1,
    showscale: false, hoverinfo: "skip", name: "无效/掩膜格点"
  });
  // zone overlays
  if ($("showBlind").checked) {
    traces.push({
      type: "heatmap", x: data.u, y: data.v,
      z: data.zone.map(row => row.map(v => v === 2 ? 1 : 0)),
      colorscale: [[0, "rgba(255,255,255,0)"], [1, "rgba(214,39,40,0.45)"]],
      zmin: 0, zmax: 1, showscale: false, hoverinfo: "skip", name: "一定测不到（无信息）"
    });
  }
  if ($("showProbZone").checked) {
    traces.push({
      type: "heatmap", x: data.u, y: data.v,
      z: data.zone.map(row => row.map(v => v === 1 ? 1 : 0)),
      colorscale: [[0, "rgba(255,255,255,0)"], [1, "rgba(255,193,7,0.22)"]],
      zmin: 0, zmax: 1, showscale: false, hoverinfo: "skip", name: "概率测得区"
    });
  }
  // heat
  const heat = {
    type: "heatmap", x: data.u, y: data.v, z: zColor, customdata: zPlot,
    colorscale: "Viridis", showlegend: false,
    colorbar: {title: data.metric === "pdet" ? "p_det" : (data.stat === "mean" ? "E[" : "Var[") + data.metric + "]",
               tickmode: "array", tickvals: tickVals, ticktext: tickTexts},
    hovertemplate: "S2=(%{x:.0f}, %{y:.0f}) m<br>指标=%{customdata:.4g}<extra></extra>"
  };
  if (Number.isFinite(heatZMin)) heat.zmin = heatZMin;
  if (Number.isFinite(heatZMax)) heat.zmax = heatZMax;
  traces.push(heat);

  // p_det contours: one constraint trace per level (white dotted)
  if ($("showPdet").checked) {
    const levels = [];
    const lv = Number($("pdetLevel").value);
    if (Number.isFinite(lv) && lv > 0 && lv < 1) levels.push(lv);
    if ($("pdetHalf").checked) levels.push(0.5);
    for (const level of levels) {
      traces.push({
        type: "contour", x: data.u, y: data.v, z: data.pdet,
        showscale: false, hoverinfo: "skip",
        contours: {type: "constraint", operation: "=", value: level,
                   showlabels: true, labelfont: {size: 10, color: "#374151"}},
        line: {color: "white", width: 2, dash: "dot"},
        name: "p_det=" + level
      });
    }
  }

  // certain region boundary
  if ($("showCertain").checked && data.certain_poly.length > 2) {
    traces.push({
      type: "scatter", mode: "lines",
      x: data.certain_poly.map(p => p[0]), y: data.certain_poly.map(p => p[1]),
      line: {color: "#00b7c7", width: 3}, fill: "toself", fillcolor: "rgba(0,183,199,0.10)",
      name: "一定测到区边界（ρ_min）"
    });
  }
  // blind boundary contour: level 0 of gap = dist(S2,A1) - rho_max
  if ($("showBlind").checked) {
    traces.push({
      type: "contour", x: data.u, y: data.v, z: data.gap,
      showscale: false, hoverinfo: "skip",
      contours: {type: "constraint", operation: "=", value: 0.0},
      line: {color: "#b91c1c", width: 2.5},
      name: "一定测不到区边界（ρ_max=1500）"
    });
  }
  // source set A(rho): one dashed outline per admissible rho
  if ($("showSourceSet").checked && (data.source_polys || []).length) {
    data.source_polys.forEach((entry, i) => {
      const poly = entry.poly;
      if (!poly || poly.length < 3) return;
      const px = poly.map(p => p[0]);
      const py = poly.map(p => p[1]);
      traces.push({
        type: "scatter", mode: "lines", x: px.concat([px[0]]), y: py.concat([py[0]]),
        line: {color: i === 0 ? "#7b1fa2" : "#b39ddb", width: 1.6, dash: "dashdot"},
        name: "干扰源可能集 A1（" + entry.label + "）"
      });
    });
  }
  // target circle
  if ($("showTargetCircle").checked) {
    const cx = [], cy = [];
    for (let k = 0; k <= 360; k++) {
      const a = 2 * Math.PI * k / 360;
      cx.push(1800 * Math.cos(a)); cy.push(1800 * Math.sin(a));
    }
    traces.push({type: "scatter", mode: "lines", x: cx, y: cy,
      line: {color: "#f2a900", width: 2, dash: "dash"}, name: "场地圆 R=1800 m", hoverinfo: "skip"});
  }
  // recommended candidates
  if ($("showRec").checked && data.recommended && data.recommended.length) {
    traces.push({
      type: "scatter", mode: "markers+text",
      x: data.recommended.map(p => p[0]), y: data.recommended.map(p => p[1]),
      text: data.recommended.map(() => "推荐 S2"),
      textposition: ["top center", "bottom center"],
      marker: {symbol: "star", size: 14, color: "#d62728", line: {color: "white", width: 1}},
      name: "推荐候选 S2",
      hovertemplate: "推荐 S2=(%{x:.1f}, %{y:.1f}) m<extra></extra>"
    });
  }
  // S1 marker
  traces.push({
    type: "scatter", mode: "markers+text", x: [data.s1[0]], y: [data.s1[1]],
    text: ["S1"], textposition: "bottom right",
    marker: {symbol: "x", size: 13, color: "#111", line: {color: "white", width: 1}},
    name: "第一观测点 S1", hovertemplate: "S1=(%{x:.0f}, %{y:.0f}) m<extra></extra>"
  });
  // probe marker
  if (lastProbe) {
    traces.push({
      type: "scatter", mode: "markers", x: [lastProbe.s2[0]], y: [lastProbe.s2[1]],
      marker: {symbol: "circle-open", size: 16, color: "#111", line: {width: 2}},
      name: "探针点", hoverinfo: "skip"
    });
  }

  Plotly.react("plot", traces, {
    margin: {l:64,r:24,t:52,b:150},
    xaxis: {title: "x (m)", zeroline:true, zerolinecolor:"#cccccc", scaleanchor: "y", scaleratio: 1},
    yaxis: {title: "y (m)", zeroline:true, zerolinecolor:"#cccccc"},
    paper_bgcolor: "white", plot_bgcolor: "white", showlegend: true,
    legend: {orientation: "h", yanchor: "top", y: -0.08, xanchor: "center", x: 0.5,
             font: {size: 10.5}, tracegroupgap: 2},
    title: {text: "第二检测点位置热图（x, y 为场地绝对坐标；ρ_min=" + fmt(data.rho.min,0) + " m, ρ_max=" + fmt(data.rho.max,0) + " m, 模型=" + data.rho.model + "）", font: {size: 14}}
  }, {responsive:true, displaylogo:false});

  const c = data.counts, a = data.areas_km2;
  $("status").textContent = `完成：${data.elapsed_s.toFixed(2)} s，有效格点 ${finiteValues.length}/${data.u.length * data.v.length}`;
  const bestTxt = data.best ? `最优格点 (${data.best.u.toFixed(0)}, ${data.best.v.toFixed(0)}) = ${fmt(data.best.value,3)}${data.best.certain ? "（保证测到区）" : "（概率区）"}` : "无可用格点";
  $("summary").innerHTML =
    `<span class="badge">S1=(${data.s1[0].toFixed(0)}, ${data.s1[1].toFixed(0)}) m</span>` +
    `<span class="badge">theta1=${data.theta1.toFixed(0)}°</span>` +
    `<span class="badge green">一定测到 ${c.certain} 格 (${a.certain.toFixed(2)} km²)</span>` +
    `<span class="badge amber">概率测得 ${c.probabilistic} 格 (${a.probabilistic.toFixed(2)} km²)</span>` +
    `<span class="badge red">一定测不到 ${c.blind} 格 (${a.blind.toFixed(2)} km²)</span>` +
    `<span class="badge">跳过计算 ${c.skipped}</span><br>${bestTxt}`;
}

async function probeAt(x, y) {
  $("probeX").value = x.toFixed(1); $("probeY").value = y.toFixed(1);
  const body = payload();
  body.s2x = x; body.s2y = y; body.n_sweep = 7;
  $("probeOut").textContent = "探测中…";
  try {
    const resp = await fetch("/api/probe", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(body)});
    const d = await resp.json();
    if (!resp.ok || d.error) throw new Error(d.error || resp.statusText);
    lastProbe = d;
    const rows = d.sweep.map(s =>
      `<tr><td>${s.rho.toFixed(0)}</td><td class="${zoneClass(s.zone)}">${zoneName(s.zone)}</td>` +
      `<td>${s.p_det.toFixed(4)}</td><td>${s.value === null ? "—" : fmt(s.value,2)}</td>` +
      `<td>${fmt(s.margin,1)}</td><td>${fmt(s.gap,1)}</td></tr>`).join("");
    $("probeOut").innerHTML =
      `<div style="margin-top:6px"><b>S2=(${fmt(d.s2[0],1)}, ${fmt(d.s2[1],1)})</b> ` +
      `<span class="${zoneClass(d.zone)}">[${zoneName(d.zone)}]</span></div>` +
      `<span class="badge">到 S1 距离 ${fmt(d.dist_s1,1)} m</span>` +
      `<span class="badge">局部 (沿, 横) = (${fmt(d.local_along,1)}, ${fmt(d.local_lateral,1)})</span>` +
      `<span class="badge">p_det(${d.rho.model}) = ${fmt(d.p_det,5)}</span>` +
      `<span class="badge">p_det(普通求积) = ${fmt(d.p_det_quad,5)}</span>` +
      `<span class="badge">margin ${fmt(d.margin,1)} m</span>` +
      `<span class="badge">gap ${fmt(d.gap,1)} m</span>` +
      (d.value !== null && Number.isFinite(d.value) ? `<span class="badge">${d.metric} = ${fmt(d.value,3)}</span>` : "") +
      `<table class="sweep"><tr><th>ρ(m)</th><th>ρ 假设下的分区</th><th>p_det</th><th>${d.metric}</th><th>margin</th><th>gap</th></tr>${rows}</table>` +
      `<div class="hint">margin≤0 表示按 ρ_min 判定必然成功；gap&gt;0 表示按 ρ_max 判定必然无信号；` +
      `两者之间为概率测得。ρ 扫描正是“S2 的选取是否覆盖了不可决策的 ρ”的直接检验。</div>`;
    if (lastData) renderHeat(lastData);   // 只重画探针标记，不重算热图
  } catch (err) {
    $("probeOut").textContent = "错误：" + err.message;
  }
}

let timer = null;
function scheduleDraw() {
  clampS1(); syncLabels();
  clearTimeout(timer); timer = setTimeout(draw, 250);
}
ids.forEach(id => { const el = $(id); el.addEventListener("input", scheduleDraw); el.addEventListener("change", scheduleDraw); });
$("rhoMinR").addEventListener("input", () => { $("rhoMin").value = $("rhoMinR").value; scheduleDraw(); });
$("rhoMaxR").addEventListener("input", () => { $("rhoMax").value = $("rhoMaxR").value; scheduleDraw(); });
$("run").addEventListener("click", draw);
$("probeBtn").addEventListener("click", () => probeAt(Number($("probeX").value), Number($("probeY").value)));
$("fitTarget").addEventListener("click", () => {
  clampS1();
  $("uMin").value = -1800; $("uMax").value = 1800; $("vMin").value = -1800; $("vMax").value = 1800;
  if (Number($("res").value) < 31) $("res").value = 31;
  syncLabels(); scheduleDraw();
});
window.addEventListener("load", () => {
  clampS1(); syncLabels(); draw();
  $("plot").on("plotly_click", (ev) => {
    if (!ev || !ev.points || !ev.points.length) return;
    const p = ev.points[0];
    if (p.x === undefined || p.y === undefined) return;
    probeAt(Number(p.x), Number(p.y));
  });
});
</script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8055)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
