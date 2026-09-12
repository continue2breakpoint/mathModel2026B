#!/usr/bin/env python3
"""Q2 unified editing + visualisation console (双点交会 workspace + HTTP API).

Run:
    PYTHONPATH=cpp python3 script/q2_dashboard.py --port 8055

Then open http://127.0.0.1:8055/ (双点交会) or /matrix (知识矩阵).
Both routes return the same unified page (see q2_page.py and
../docs/q2_unified_ui.md): one shell, one set of controls, one auxiliary-line
registry and one import/export panel; only the default workspace differs.

This module owns the 双点交会 model and its HTTP endpoints.  The page itself is
static (script/q2_assets/), so changing the UI never means editing Python.

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
import q2_page  # noqa: E402

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
APP_VERSION = q2_page.VERSION

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


@app.route("/favicon.ico")
def favicon() -> Response:
    """浏览器默认会来要图标，直接回 204，避免控制台里出现 404 噪声。"""
    return Response(status=204)


@app.route("/")
def index() -> Response:
    """统一编辑台，默认落在双点交会工作区（/matrix 落知识矩阵工作区）。"""
    return q2_page.page(default_ws="doublet", version=APP_VERSION)


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
    # Bounding box of the possible source set: the UI uses it to zoom the map
    # onto A1 (same field name as the knowledge-matrix endpoint).
    if source_polys:
        vertices = np.asarray([v for p in source_polys for v in p["poly"]], dtype=float)
        source_bounds = [float(vertices[:, 0].min()), float(vertices[:, 0].max()),
                         float(vertices[:, 1].min()), float(vertices[:, 1].max())]
    else:
        source_bounds = [s1[0], s1[1], s1[0], s1[1]]
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
        "source_bounds": source_bounds,
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


@app.route("/assets/<path:name>")
def assets(name: str) -> Response:
    return q2_page.asset(name)



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
