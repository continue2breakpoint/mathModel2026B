#!/usr/bin/env python3
"""Interactive heat-map dashboard for Q2 second-point selection.

Run:
    PYTHONPATH=cpp python3 script/q2_dashboard.py --port 8055

Then open http://127.0.0.1:8055 .

The browser controls the first observation point S1=(S1.x,S1.y) and its
measured bearing theta1.  theta1 is the angle counter-clockwise from the
positive x-axis to the direction from S1 to the source.

The heat-map axes (u,v) are the absolute target-frame coordinates of the
candidate second point:

    u = S2.x,   v = S2.y,   origin = target-disk centre,

not coordinates relative to S1.  Internally the angular quality mask still
uses the local coordinates (u_local,v_local) = ((S2-S1)·e(theta1),
(S2-S1)·e_perp(theta1)).

The colour can be either

    * E[R_min] or Var(R_min), where R_min is the minimum enclosing circle
      radius of the feasible region after the second measurement; or
    * E[N20_lb] or Var(N20_lb), where N20_lb is the rigorous lower bound on
      the number of radius-20 m disks needed to cover the feasible region.

The backend uses the C++ geometry kernel and deterministic Gauss-Legendre
quadrature.  It is not a Monte-Carlo app.
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

try:
    import geom_cpp  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Cannot import geom_cpp. Build it first with:\n"
        "    ./cpp/build.sh"
    ) from exc

try:
    import plotly.offline as poff  # type: ignore
except ImportError:  # pragma: no cover
    poff = None


DEG2RAD = math.pi / 180.0
R_TARGET = 1800.0
RHO_MIN = 1000.0
RHO_MAX = 1500.0
EPS_DEG = 1.0
EPS_RAD = EPS_DEG * DEG2RAD
CLEAR_RADIUS = 20.0
APP_VERSION = "2026-09-11-log-color-v3"

app = Flask(__name__)


def _gauss_legendre(n: int) -> tuple[np.ndarray, np.ndarray]:
    return np.polynomial.legendre.leggauss(n)


def _unit_deg(deg: float) -> tuple[float, float]:
    a = deg * DEG2RAD
    return math.cos(a), math.sin(a)


def _wrap360(x: float) -> float:
    x = math.fmod(x, 360.0)
    return x + 360.0 if x < 0.0 else x


def _ray_target_radius(s1: tuple[float, float], direction_deg: float) -> float:
    """Distance from S1 to the target-disk boundary along direction_deg."""
    ux, uy = _unit_deg(direction_deg)
    bx = 2.0 * (s1[0] * ux + s1[1] * uy)
    c = s1[0] * s1[0] + s1[1] * s1[1] - R_TARGET * R_TARGET
    disc = bx * bx - 4.0 * c
    if disc <= 0.0:
        return 0.0
    return (-bx + math.sqrt(disc)) / 2.0


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
    bound_sides: int,
    disk_sides: int,
) -> "geom_cpp.ConvexRegion | None":
    """Target disk ∩ two wedges ∩ D(S1,1500) ∩ D(S2,1500)."""
    region = geom_cpp.build_region(
        _make_observations(s1, theta1, s2, theta2),
        R_TARGET,
        bound_sides,
    )
    if region.is_empty():
        return None

    region = geom_cpp.clip_region_by_disk(
        region,
        geom_cpp.Vec2(s1[0], s1[1]),
        RHO_MAX,
        disk_sides,
    )
    if region.is_empty():
        return None

    region = geom_cpp.clip_region_by_disk(
        region,
        geom_cpp.Vec2(s2[0], s2[1]),
        RHO_MAX,
        disk_sides,
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
) -> dict[str, float]:
    """Deterministic quadrature for one candidate S2.

    Returns p_det, mean and variance of the selected metric.
    """
    if metric not in {"Rmin", "N20"}:
        raise ValueError("metric must be 'Rmin' or 'N20'")
    if stat not in {"mean", "var"}:
        raise ValueError("stat must be 'mean' or 'var'")

    # Quadrature nodes in phi (source direction relative to theta1).
    xp, wp = _gauss_legendre(n_phi)
    phi_nodes = EPS_RAD * xp
    phi_weights = EPS_RAD * wp

    # Quadrature nodes for delta2.
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

    x1, y1 = s1
    x2, y2 = s2

    for phi, w_phi in zip(phi_nodes, phi_weights):
        ph = float(phi)
        direction_deg = theta1 + math.degrees(ph)
        r_max = min(RHO_MAX, _ray_target_radius((x1, y1), direction_deg))
        if r_max <= 1e-12:
            continue

        # Split the radial integral at r=1000 because K1 has a kink there.
        if r_max <= RHO_MIN:
            intervals = [(0.0, r_max)]
        else:
            intervals = [(0.0, RHO_MIN), (RHO_MIN, r_max)]

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

                k1 = max(0.0, RHO_MAX - max(RHO_MIN, d1))
                k12 = max(0.0, RHO_MAX - max(RHO_MIN, d1, d2))
                w_g = float(w_phi) * wr * rr
                norm_first += k1 * w_g
                nd = k12 * w_g
                num_det += nd
                if nd <= 0.0:
                    continue

                beta2 = math.degrees(math.atan2(gy - y2, gx - x2))
                beta2 = _wrap360(beta2)

                for delta, w_delta in zip(delta_nodes, delta_weights):
                    region = _build_region(
                        (x1, y1),
                        theta1,
                        (x2, y2),
                        beta2 + float(delta),
                        bound_sides,
                        disk_sides,
                    )
                    if region is None:
                        continue
                    _, _, r_min, n_lb = _region_metrics(region)
                    value = r_min if metric == "Rmin" else float(n_lb)
                    ww = nd * float(w_delta)
                    weight_sum += ww
                    value_sum += ww * value
                    value2_sum += ww * value * value

    if norm_first <= 0.0:
        return {"p_det": float("nan"), "mean": float("nan"), "var": float("nan")}
    p_det = num_det / norm_first
    if weight_sum <= 0.0:
        return {"p_det": p_det, "mean": float("nan"), "var": float("nan")}

    mean = value_sum / weight_sum
    var = max(0.0, value2_sum / weight_sum - mean * mean)
    return {"p_det": float(p_det), "mean": float(mean), "var": float(var)}


def _grid_to_global(u: float, v: float) -> tuple[float, float]:
    """Heat-map grid coordinates are already absolute target-frame (x,y)."""
    return u, v


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
    # Never cache the dashboard shell; when the Python file is edited the
    # user only needs to restart the Flask process and refresh the page.
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
        nx = max(5, min(81, int(data.get("nx", 27))))
        ny = max(5, min(81, int(data.get("ny", 27))))
        metric = str(data.get("metric", "Rmin"))
        stat = str(data.get("stat", "mean"))
        integrate_delta = bool(data.get("integrate_delta", True))
        n_phi = max(2, min(10, int(data.get("n_phi", 5))))
        n_r = max(2, min(8, int(data.get("n_r", 4))))
        disk_sides = max(24, min(360, int(data.get("disk_sides", 128))))
        bound_sides = max(24, min(360, int(data.get("bound_sides", 128))))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"bad request: {exc}"}), 400

    u_values = np.linspace(u_min, u_max, nx)
    v_values = np.linspace(v_min, v_max, ny)
    z = np.full((ny, nx), np.nan, dtype=float)
    pdet = np.full((ny, nx), np.nan, dtype=float)

    t0 = time.time()
    for iy, v in enumerate(v_values):
        for ix, u in enumerate(u_values):
            s2 = _grid_to_global(float(u), float(v))
            result = evaluate_second_point(
                s1,
                theta1,
                s2,
                metric=metric,
                stat=stat,
                n_phi=n_phi,
                n_r=n_r,
                integrate_delta=integrate_delta,
                bound_sides=bound_sides,
                disk_sides=disk_sides,
            )
            z[iy, ix] = result[stat]
            pdet[iy, ix] = result["p_det"]

    # JSON must not contain NaN/Infinity.  Use null (None) for unavailable
    # cells; Plotly renders null as gaps.
    z_out = [
        [None if not math.isfinite(float(v)) else float(v) for v in row]
        for row in z
    ]
    pdet_out = [
        [None if not math.isfinite(float(v)) else float(v) for v in row]
        for row in pdet
    ]

    return jsonify(
        {
            "u": u_values.tolist(),
            "v": v_values.tolist(),
            "z": z_out,
            "pdet": pdet_out,
            "metric": metric,
            "stat": stat,
            "elapsed_s": time.time() - t0,
            "s1": [s1[0], s1[1]],
            "theta1": theta1,
        }
    )


HTML_PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>问题2 第二检测点选择交互热图</title>
  <script src="/plotly.min.js"></script>
  <style>
    :root { color-scheme: light; }
    body { margin:0; font-family: system-ui, -apple-system, "Segoe UI", Arial, sans-serif; background:#f6f7fb; color:#1f2430; }
    .wrap { display:flex; min-height:100vh; }
    .panel { width:340px; flex:0 0 340px; padding:16px; background:#fff; box-shadow:2px 0 10px rgba(0,0,0,.06); overflow:auto; }
    .main { flex:1; min-width:0; padding:16px; }
    h1 { font-size:18px; margin:0 0 8px; }
    h2 { font-size:13px; margin:16px 0 6px; color:#5b6478; text-transform:uppercase; letter-spacing:.04em; }
    label { display:block; font-size:13px; margin:9px 0 3px; color:#333; }
    input[type=range] { width:100%; }
    .row { display:flex; gap:8px; align-items:center; }
    .row > * { min-width:0; }
    .num { width:82px; padding:4px 6px; border:1px solid #ccd2df; border-radius:6px; font-size:13px; }
    select, button { padding:6px 8px; border:1px solid #ccd2df; border-radius:6px; background:#fff; font-size:13px; }
    button.primary { background:#2f6feb; border-color:#2f6feb; color:#fff; cursor:pointer; }
    button:disabled { opacity:.55; cursor:wait; }
    .hint { font-size:12px; color:#697386; line-height:1.45; }
    .status { font-size:12px; color:#4b5563; min-height:18px; margin-top:8px; }
    #plot { width:100%; height:78vh; min-height:520px; background:#fff; border-radius:10px; box-shadow:0 2px 12px rgba(0,0,0,.06); }
    .legend { font-size:12px; color:#4b5563; margin-top:6px; }
    .badge { display:inline-block; background:#eef2ff; color:#2f4aa8; border-radius:5px; padding:2px 6px; margin:2px 4px 2px 0; font-size:12px; }
  </style>
</head>
<body>
<div class="wrap">
  <aside class="panel">
    <h1>问题2 第二检测点选择热图</h1>
    <div class="hint">
      <b>S1=(S1.x, S1.y)</b>：第一观测点的绝对场地坐标，单位 m，原点为场地圆心。<br>
      <b>theta1</b>：x 轴正向逆时针转到 <b>S1→干扰源</b> 方向的示向角，单位 deg。<br>
      <b>(u,v)=(S2.x, S2.y)</b>：第二检测点的绝对场地坐标，单位 m。
    </div>

    <h2>第一观测点 S1</h2>
    <label>S1.x (m) <span id="s1xVal"></span></label>
    <input id="s1x" type="range" min="-1700" max="1700" step="25" value="0">
    <label>S1.y (m) <span id="s1yVal"></span></label>
    <input id="s1y" type="range" min="-1700" max="1700" step="25" value="0">
    <label>示向角 theta1 (deg) <span id="thetaVal"></span></label>
    <input id="theta1" type="range" min="0" max="360" step="1" value="0">
    <div class="hint">S1 会被限制在半径 1700 m 内，以保证第一点位于场地圆内部。</div>

    <h2>热图颜色指标</h2>
    <label>指标（随机变量）</label>
    <select id="metric">
      <option value="Rmin">R_min：最小覆盖圆半径 (m)</option>
      <option value="N20">N20_lb：半径 20m 圆覆盖数严格下界</option>
    </select>
    <label>统计量</label>
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

    <h2>数值求积设置</h2>
    <label>热图网格分辨率 <span id="resVal">27</span> × 27</label>
    <input id="res" type="range" min="13" max="51" step="2" value="27">
    <label><input id="integrateDelta" type="checkbox" checked> 对第二次示向误差 δ2 做三点求积</label>
    <div class="hint">不勾选时只在 δ2=0 处评估，速度更快，精度略低。</div>

    <h2>绘图范围（绝对坐标 u,v）</h2>
    <div class="row">
      <input class="num" id="uMin" type="number" value="-1800" step="50">
      <input class="num" id="uMax" type="number" value="1800" step="50">
    </div>
    <div class="row">
      <input class="num" id="vMin" type="number" value="-1800" step="50">
      <input class="num" id="vMax" type="number" value="1800" step="50">
    </div>
    <div class="hint">u=S2.x，v=S2.y，单位 m；默认范围为场地圆外接正方形。</div>

    <h2>1800m 场地圆</h2>
    <label><input id="showTargetCircle" type="checkbox" checked> 叠加场地圆 u²+v²=1800²</label>
    <label><input id="limitTarget" type="checkbox"> 只显示 S2 在场地圆内的格点</label>
    <button id="fitTarget" style="width:100%;margin-top:6px;">将绘图范围设为场地圆外接正方形</button>
    <div class="hint">场地圆：圆心 (0,0)，半径 1800 m。</div>

    <label style="margin-top:12px;"><input id="showPdet" type="checkbox" checked> 叠加第二次检测成功概率 p_det=0.999 等值线</label>
    <label><input id="maskAngle" type="checkbox"> 屏蔽近平行交会区（局部坐标下 |u_loc-855|≤√3|v_loc|）</label>
    <div class="hint">u_loc=(S2-S1)·e(theta1)，v_loc=(S2-S1)·e_perp(theta1)。</div>
    <label style="margin-top:8px;"><input id="maskPdet" type="checkbox"> 屏蔽 p_det 小于阈值</label>
    <input class="num" id="pdetMin" type="number" value="0.99" min="0" max="1" step="0.005">
    <label><input id="robustScale" type="checkbox"> 线性颜色下按 2%–98% 分位裁剪</label>
    <button id="run" class="primary" style="width:100%;margin-top:12px;">重新计算热图</button>
    <div class="status" id="status"></div>
    <div id="summary" class="legend"></div>
  </aside>

  <main class="main">
    <div id="plot"></div>
    <div class="legend">
      颜色：<span id="colorLabel"></span>。<br>
      白色虚线：第二次检测成功概率 p_det=0.999 等值线；
      橙色虚线：场地圆 u²+v²=1800²；
      红色星号：局部推荐坐标 (850, ±520) 转换到场地绝对坐标后的 S2。<br>
      灰色格点表示被掩膜、p_det 过低，或该第二点在 1500 m 内无法覆盖任何可能源点。
    </div>
  </main>
</div>

<script>
const $ = (id) => document.getElementById(id);
const ids = ["s1x","s1y","theta1","metric","stat","res","integrateDelta","uMin","uMax","vMin","vMax","showPdet","maskAngle","maskPdet","pdetMin","robustScale","colorMode","showTargetCircle","limitTarget"];
let busy = false;
let lastPayload = null;

function clampS1() {
  // Keep S1 inside the target disk with a small margin; otherwise the
  // radial parameterization from S1 to the target boundary would need two
  // intersection roots instead of one.
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
  const metricName = $("metric").value === "Rmin" ? "R_min (m)" : "N20_lb";
  const statName = $("stat").value === "mean" ? "期望" : "方差";
  const modeName = $("colorMode").value;
  const modeNames = {log: "log10(1+z)", sqrt: "sqrt(z)", linear: "线性", rank: "分位/秩"};
  $("colorLabel").textContent = statName + "[" + metricName + "]，颜色=" + (modeNames[modeName] || modeName);
}

function payload() {
  const n = Number($("res").value);
  return {
    s1x: Number($("s1x").value),
    s1y: Number($("s1y").value),
    theta1: Number($("theta1").value),
    u_min: Number($("uMin").value),
    u_max: Number($("uMax").value),
    v_min: Number($("vMin").value),
    v_max: Number($("vMax").value),
    nx: n,
    ny: n,
    metric: $("metric").value,
    stat: $("stat").value,
    integrate_delta: $("integrateDelta").checked,
    n_phi: 5,
    n_r: 4,
    disk_sides: 128,
    bound_sides: 128
  };
}

async function draw() {
  if (busy) return;
  lastPayload = payload();
  busy = true;
  $("run").disabled = true;
  $("status").textContent = "计算中…";
  try {
    const resp = await fetch("/api/heatmap", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify(lastPayload)
    });
    const data = await resp.json();
    if (!resp.ok || data.error) throw new Error(data.error || resp.statusText);

    const thetaRad = data.theta1 * Math.PI / 180.0;
    const cosT = Math.cos(thetaRad);
    const sinT = Math.sin(thetaRad);
    const targetRadius = 1800.0;

    // Copy and mask the heat-map cells.  The two masks remove the
    // explosive but useless near-collinear configurations and candidate
    // points with a low probability of a successful second measurement.
    const zPlot = data.z.map(row => row.slice());
    const maskAngle = $("maskAngle").checked;
    const maskPdet = $("maskPdet").checked;
    const pdetMin = Number($("pdetMin").value);
    const finiteValues = [];
    for (let iy = 0; iy < zPlot.length; iy++) {
      const v = data.v[iy];
      for (let ix = 0; ix < zPlot[iy].length; ix++) {
        const u = data.u[ix];
        const p = data.pdet[iy][ix];
        let keep = Number.isFinite(zPlot[iy][ix]);
        if (maskAngle) {
          const dx = u - data.s1[0];
          const dy = v - data.s1[1];
          const localU = dx * cosT + dy * sinT;
          const localV = -dx * sinT + dy * cosT;
          if (Math.abs(localU - 855.263) > Math.sqrt(3) * Math.abs(localV)) keep = false;
        }
        if (maskPdet && !(Number.isFinite(p) && p >= pdetMin)) keep = false;
        if ($("limitTarget").checked && keep) {
          if (u * u + v * v > targetRadius * targetRadius + 1e-6) keep = false;
        }
        if (!keep) {
          zPlot[iy][ix] = null;
        } else {
          finiteValues.push(zPlot[iy][ix]);
        }
      }
    }
    finiteValues.sort((a, b) => a - b);

    function quantile(sorted, q) {
      if (!sorted.length) return NaN;
      const pos = (sorted.length - 1) * q;
      const lo = Math.floor(pos);
      const hi = Math.ceil(pos);
      if (lo === hi) return sorted[lo];
      return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
    }

    const colorMode = $("colorMode").value;
    const colorModeNames = {log: "log10(1+z)", sqrt: "sqrt(z)", linear: "线性", rank: "分位/秩"};
    function forward(v) {
      if (colorMode === "log") return Math.log10(1.0 + Math.max(0.0, v));
      if (colorMode === "sqrt") return Math.sqrt(Math.max(0.0, v));
      return v;
    }
    function fmtTick(x) {
      if (!Number.isFinite(x)) return "";
      const a = Math.abs(x);
      if ((a > 0 && a < 1e-3) || a >= 1e4) return x.toExponential(2);
      return Number(x.toPrecision(4)).toString();
    }

    const uniqueValues = [];
    for (const val of finiteValues) {
      if (!uniqueValues.length || val !== uniqueValues[uniqueValues.length - 1]) uniqueValues.push(val);
    }
    function rankOf(value) {
      if (uniqueValues.length <= 1) return 0.5;
      let lo = 0;
      let hi = uniqueValues.length - 1;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (uniqueValues[mid] < value) lo = mid + 1; else hi = mid;
      }
      return lo / (uniqueValues.length - 1);
    }

    const tickQuantiles = [0, 0.25, 0.5, 0.75, 1];
    const tickOriginals = tickQuantiles.map(q => quantile(finiteValues, q));
    let zColor = zPlot.map(row => row.map(v => Number.isFinite(v) ? forward(v) : null));
    let heatZMin;
    let heatZMax;
    let tickVals = [];
    let tickTexts = [];

    if (colorMode === "rank" && finiteValues.length) {
      zColor = zPlot.map(row => row.map(v => Number.isFinite(v) ? rankOf(v) : null));
      heatZMin = 0.0;
      heatZMax = 1.0;
      tickVals = tickQuantiles;
      tickTexts = tickOriginals.map(fmtTick);
    } else if (finiteValues.length) {
      heatZMin = forward(finiteValues[0]);
      heatZMax = forward(finiteValues[finiteValues.length - 1]);
      if (colorMode === "linear" && $("robustScale").checked && finiteValues.length >= 5) {
        heatZMin = quantile(finiteValues, 0.02);
        heatZMax = quantile(finiteValues, 0.98);
      }
      tickVals = tickOriginals.map(forward);
      tickTexts = tickOriginals.map(fmtTick);
      if (heatZMin === heatZMax) {
        heatZMin -= 1.0;
        heatZMax += 1.0;
      }
    }

    const maskZ = zPlot.map(row => row.map(v => Number.isFinite(v) ? 0 : 1));
    const maskTrace = {
      type: "heatmap",
      x: data.u,
      y: data.v,
      z: maskZ,
      colorscale: [[0, "#f7f7f7"], [1, "#d0d0d0"]],
      zmin: 0,
      zmax: 1,
      showscale: false,
      hoverinfo: "skip"
    };

    const metricName = $("metric").value === "Rmin" ? "R_min (m)" : "N20_lb";
    const statName = $("stat").value === "mean" ? "期望" : "方差";
    const colorTitle = statName + "[" + metricName + "]";

    const heatTrace = {
      type: "heatmap",
      x: data.u,
      y: data.v,
      z: zColor,
      customdata: zPlot,
      colorscale: "Viridis",
      showlegend: false,
      colorbar: {
        title: colorTitle,
        tickmode: "array",
        tickvals: tickVals,
        ticktext: tickTexts
      },
      hovertemplate: "S2.x=%{x:.0f} m<br>S2.y=%{y:.0f} m<br>指标=%{customdata:.4g}<extra></extra>"
    };
    if (Number.isFinite(heatZMin)) heatTrace.zmin = heatZMin;
    if (Number.isFinite(heatZMax)) heatTrace.zmax = heatZMax;

    // Recommended local candidates (850, ±520) converted to absolute
    // target-frame coordinates.
    const rec1x = data.s1[0] + 850.0 * cosT - 520.0 * sinT;
    const rec1y = data.s1[1] + 850.0 * sinT + 520.0 * cosT;
    const rec2x = data.s1[0] + 850.0 * cosT + 520.0 * sinT;
    const rec2y = data.s1[1] + 850.0 * sinT - 520.0 * cosT;

    const traces = [maskTrace, heatTrace];
    traces.push({
      type: "scatter",
      mode: "markers+text",
      x: [rec1x, rec2x],
      y: [rec1y, rec2y],
      text: ["推荐 S2", "推荐 S2"],
      textposition: ["top center", "bottom center"],
      marker: {symbol: "star", size: 14, color: "#d62728", line: {color: "white", width: 1}},
      name: "推荐候选 S2",
      hovertemplate: "推荐 S2.x=%{x:.1f} m<br>S2.y=%{y:.1f} m<extra></extra>"
    });

    if ($("showTargetCircle").checked) {
      const circleX = [];
      const circleY = [];
      const nCircle = 361;
      for (let k = 0; k <= nCircle; k++) {
        const a = 2.0 * Math.PI * k / nCircle;
        circleX.push(targetRadius * Math.cos(a));
        circleY.push(targetRadius * Math.sin(a));
      }
      traces.push({
        type: "scatter",
        mode: "lines",
        x: circleX,
        y: circleY,
        line: {color: "#f2a900", width: 2, dash: "dash"},
        name: "场地圆 R=1800 m",
        hoverinfo: "skip"
      });
    }

    const pdetFinite = data.pdet.flat().every(Number.isFinite);
    if ($("showPdet").checked && pdetFinite) {
      traces.push({
        type: "contour",
        x: data.u,
        y: data.v,
        z: data.pdet,
        showscale: false,
        contours: {coloring: "none", showlabels: true, start: 0.999, end: 1.0, size: 0.001},
        line: {color: "white", width: 2, dash: "dot"},
        name: "p_det=0.999",
        showlegend: true,
        hoverinfo: "skip"
      });
    }

    Plotly.react("plot", traces, {
      margin: {l:60,r:20,t:55,b:55},
      xaxis: {title: "u = S2.x  (m)", zeroline:true, zerolinecolor:"#cccccc"},
      yaxis: {title: "v = S2.y  (m)", zeroline:true, zerolinecolor:"#cccccc"},
      paper_bgcolor: "white",
      plot_bgcolor: "white",
      showlegend: true,
      legend: {orientation: "h", yanchor: "bottom", y: 1.02, xanchor: "left", x: 0},
      title: "第二检测点绝对场地坐标热图"
    }, {responsive:true, displaylogo:false});

    const finiteP = data.pdet.flat().filter(Number.isFinite);
    const pmin = finiteP.length ? Math.min(...finiteP) : NaN;
    const pmean = finiteP.length ? finiteP.reduce((a, b) => a + b, 0) / finiteP.length : NaN;
    const scaleText = Number.isFinite(heatZMin)
      ? `着色=${colorModeNames[colorMode] || colorMode}，色标 ${fmtTick(tickOriginals[0])} … ${fmtTick(tickOriginals[tickOriginals.length - 1])}`
      : "无色标";
    $("status").textContent =
      `完成：${data.elapsed_s.toFixed(2)} s，有效格点 ${finiteValues.length}/${zPlot.length * zPlot[0].length}，${scaleText}`;
    $("summary").innerHTML =
      `<span class="badge">S1=(${data.s1[0].toFixed(0)}, ${data.s1[1].toFixed(0)}) m</span>` +
      `<span class="badge">theta1=${data.theta1.toFixed(0)}°</span>` +
      `<span class="badge">p_det min=${Number.isFinite(pmin) ? pmin.toFixed(4) : "nan"}</span>` +
      `<span class="badge">p_det mean=${Number.isFinite(pmean) ? pmean.toFixed(4) : "nan"}</span>`;
  } catch (err) {
    $("status").textContent = "错误：" + err.message;
  } finally {
    busy = false;
    $("run").disabled = false;
  }
}

let timer = null;
function scheduleDraw() {
  clampS1();
  syncLabels();
  clearTimeout(timer);
  timer = setTimeout(draw, 250);
}

ids.forEach(id => {
  const el = $(id);
  el.addEventListener("input", scheduleDraw);
  el.addEventListener("change", scheduleDraw);
});
$("run").addEventListener("click", draw);
$("fitTarget").addEventListener("click", () => {
  clampS1();
  $("uMin").value = -1800;
  $("uMax").value = 1800;
  $("vMin").value = -1800;
  $("vMax").value = 1800;
  if (Number($("res").value) < 31) $("res").value = 31;
  syncLabels();
  scheduleDraw();
});
window.addEventListener("load", () => { clampS1(); syncLabels(); draw(); });
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
