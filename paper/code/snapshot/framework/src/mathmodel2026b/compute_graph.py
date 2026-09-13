"""Typed, pluggable compute graph for the Q1 localization-region pipeline.

Design goals
------------
* Artifacts are small immutable dataclasses that can be mapped 1:1 to C structs.
* Every node declares ``input_types`` / ``output_type``.
* Several milestone paths are provided:
    A. wedges -> half-planes -> convex region -> diameter -> diameter-circle check
    B. wedges -> half-planes -> convex region -> Welzl min-circle -> 20m clear check
    C. observations -> linearized/predicted centre -> local region -> Welzl check
* Incremental update is first-class: ``RegionState`` keeps the previous
  half-plane set and polygon, so adding one observation only clips one more wedge.
* Complex parts (exact half-plane intersection, rotating calipers, multi-disk
  cover, observation planning) are behind interchangeable node classes.

This module is deliberately dependency-free except for the standard library.
Replace any node by a faster C/C++ implementation without changing the graph.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from math import atan2, cos, degrees, hypot, pi, radians, sin, sqrt
from random import Random
from typing import Any, ClassVar, Mapping, Sequence, Union

Vec2 = tuple[float, float]


# ---------------------------------------------------------------------------
# Milestone artifacts: keep these flat, plain and C-friendly.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Observation:
    point: Vec2
    bearing_deg: float
    half_width_deg: float = 1.0
    max_range_m: float = 1500.0


@dataclass(frozen=True, slots=True)
class Wedge:
    apex: Vec2
    bearing_deg: float
    half_width_deg: float = 1.0


@dataclass(frozen=True, slots=True)
class HalfPlane:
    # a*x + b*y + c >= 0
    a: float
    b: float
    c: float


@dataclass(frozen=True, slots=True)
class ConvexRegion:
    vertices: tuple[Vec2, ...]  # CCW for a bounded polygon
    bounded: bool = True
    empty: bool = False
    version: int = 0  # bumped by incremental builders for cache invalidation

    @property
    def is_empty(self) -> bool:
        return self.empty or len(self.vertices) < 3

    @staticmethod
    def empty_region(version: int = 0) -> "ConvexRegion":
        return ConvexRegion((), bounded=True, empty=True, version=version)


@dataclass(frozen=True, slots=True)
class DiameterWitness:
    a: Vec2
    b: Vec2
    distance: float


@dataclass(frozen=True, slots=True)
class DiameterCache:
    """Cache for Width/Diameter under one polygon version."""

    version: int
    witness: DiameterWitness
    antipodal_pairs: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class MinCircleResult:
    center: Vec2
    radius: float
    basis: tuple[Vec2, ...]

    @property
    def basis_size(self) -> int:
        return len(self.basis)

    @property
    def basis_type(self) -> str:
        if self.basis_size <= 1:
            return "one_point"
        if self.basis_size == 2:
            return "two_point"
        return "three_point"


@dataclass(frozen=True, slots=True)
class CoverageDecision:
    covered: bool
    radius: float
    center: Vec2
    kind: str  # "diameter_circle" | "clear_20m" | "multi_disk"
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CircleCoverPlan:
    radius: float
    centers: tuple[Vec2, ...]
    covered_all: bool
    exact: bool = False
    remaining_vertices: tuple[Vec2, ...] = ()


@dataclass(frozen=True, slots=True)
class CenterEstimate:
    center: Vec2
    residual_rms_deg: float


@dataclass(frozen=True, slots=True)
class RegionState:
    half_planes: tuple[HalfPlane, ...]
    region: ConvexRegion
    version: int = 0


@dataclass(frozen=True, slots=True)
class ObservationSuggestion:
    point: Vec2
    score: float
    reason: str


# ---------------------------------------------------------------------------
# Tiny typed DAG.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class NodeRef:
    node: str
    port: str = "out"


@dataclass(frozen=True, slots=True)
class GraphInput:
    name: str


PortRef = Union[NodeRef, GraphInput]


class Node(ABC):
    input_types: ClassVar[Mapping[str, type]]
    output_type: ClassVar[type]

    @abstractmethod
    def compute(self, **kwargs: Any) -> Any:
        raise NotImplementedError


class ComputeGraph:
    """A minimal explicit dataflow graph.

    Nodes are pure functions.  A node may also carry immutable configuration,
    so the same graph can be instantiated for different strategies.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, tuple[Node, dict[str, PortRef]]] = {}

    def add(self, node_id: str, node: Node, inputs: Mapping[str, PortRef]) -> "ComputeGraph":
        if node_id in self._nodes:
            raise KeyError(f"duplicate node id: {node_id}")
        self._nodes[node_id] = (node, dict(inputs))
        return self

    def describe(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for node_id, (node, inputs) in self._nodes.items():
            rows.append(
                {
                    "id": node_id,
                    "op": type(node).__name__,
                    "inputs": {
                        port: (
                            {"external": ref.name}
                            if isinstance(ref, GraphInput)
                            else {"node": ref.node, "port": ref.port}
                        )
                        for port, ref in inputs.items()
                    },
                    "output_type": getattr(node.output_type, "__name__", str(node.output_type)),
                }
            )
        return rows

    def run(self, **feed: Any) -> dict[str, Any]:
        cache: dict[str, Any] = {}
        visiting: set[str] = set()

        def eval_ref(ref: PortRef) -> Any:
            if isinstance(ref, GraphInput):
                try:
                    return feed[ref.name]
                except KeyError as exc:
                    raise KeyError(f"missing graph input {ref.name!r}") from exc
            return eval_node(ref.node)

        def eval_node(node_id: str) -> Any:
            if node_id in cache:
                return cache[node_id]
            if node_id in visiting:
                raise ValueError(f"cycle detected at {node_id}")
            visiting.add(node_id)
            node, inputs = self._nodes[node_id]
            kwargs = {port: eval_ref(ref) for port, ref in inputs.items()}
            # Lightweight runtime type checks; keep them cheap.
            for port, expected in node.input_types.items():
                if port in kwargs and not isinstance(kwargs[port], expected):
                    raise TypeError(
                        f"{type(node).__name__}.{port}: expected {expected}, "
                        f"got {type(kwargs[port])}"
                    )
            out = node.compute(**kwargs)
            if not isinstance(out, node.output_type):
                raise TypeError(
                    f"{type(node).__name__} output: expected {node.output_type}, "
                    f"got {type(out)}"
                )
            visiting.remove(node_id)
            cache[node_id] = out
            return out

        for node_id in self._nodes:
            eval_node(node_id)
        return cache


# ---------------------------------------------------------------------------
# Geometry helpers.
# ---------------------------------------------------------------------------
def _dist(a: Vec2, b: Vec2) -> float:
    return hypot(a[0] - b[0], a[1] - b[1])


def _unit(deg: float) -> Vec2:
    r = radians(deg)
    return cos(r), sin(r)


def _polygon_circle(center: Vec2, radius: float, sides: int = 64) -> list[Vec2]:
    # Circumscribed polygon: contains the true disk.
    r = radius / cos(pi / sides)
    return [
        (
            center[0] + r * cos(2.0 * pi * k / sides),
            center[1] + r * sin(2.0 * pi * k / sides),
        )
        for k in range(sides)
    ]


def _clip_half_plane(poly: Sequence[Vec2], hp: HalfPlane, eps: float = 1e-10) -> list[Vec2]:
    if not poly:
        return []
    out: list[Vec2] = []
    n = len(poly)
    for i in range(n):
        cur = poly[i]
        nxt = poly[(i + 1) % n]
        dc = hp.a * cur[0] + hp.b * cur[1] + hp.c
        dn = hp.a * nxt[0] + hp.b * nxt[1] + hp.c
        if dc >= -eps:
            out.append(cur)
        if (dc > eps and dn < -eps) or (dc < -eps and dn > eps):
            t = dc / (dc - dn)
            out.append(
                (
                    cur[0] + (nxt[0] - cur[0]) * t,
                    cur[1] + (nxt[1] - cur[1]) * t,
                )
            )
    return out


def _wedge_half_planes(wedge: Wedge) -> tuple[HalfPlane, HalfPlane]:
    px, py = wedge.apex
    eps = wedge.half_width_deg
    # inside: cross(u(theta+eps), Q-P) <= 0 and cross(u(theta-eps), Q-P) >= 0
    ux, uy = _unit(wedge.bearing_deg + eps)
    h1 = HalfPlane(a=uy, b=-ux, c=-uy * px + ux * py)
    ux, uy = _unit(wedge.bearing_deg - eps)
    h2 = HalfPlane(a=-uy, b=ux, c=uy * px - ux * py)
    return h1, h2


def _circle_from_two(a: Vec2, b: Vec2) -> tuple[Vec2, float]:
    c = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    return c, _dist(c, a)


def _circle_from_three(a: Vec2, b: Vec2, c: Vec2) -> tuple[Vec2, float] | None:
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    ux = (
        (ax * ax + ay * ay) * (by - cy)
        + (bx * bx + by * by) * (cy - ay)
        + (cx * cx + cy * cy) * (ay - by)
    ) / d
    uy = (
        (ax * ax + ay * ay) * (cx - bx)
        + (bx * bx + by * by) * (ax - cx)
        + (cx * cx + cy * cy) * (bx - ax)
    ) / d
    center = (ux, uy)
    return center, _dist(center, a)


def _in_circle(center: Vec2, radius: float, p: Vec2, eps: float = 1e-9) -> bool:
    return _dist(center, p) <= radius + eps


# ---------------------------------------------------------------------------
# Node implementations.
# ---------------------------------------------------------------------------
class MakeWedgesNode(Node):
    input_types = {"observations": list}
    output_type = list

    def compute(self, observations: list[Observation]) -> list[Wedge]:
        return [
            Wedge(o.point, o.bearing_deg, o.half_width_deg)
            for o in observations
        ]


class WedgesToHalfPlanesNode(Node):
    input_types = {"wedges": list}
    output_type = list

    def compute(self, wedges: list[Wedge]) -> list[HalfPlane]:
        out: list[HalfPlane] = []
        for w in wedges:
            out.extend(_wedge_half_planes(w))
        return out


class ClipHalfPlanesNode(Node):
    input_types = {"half_planes": list}
    output_type = ConvexRegion

    def __init__(self, bound_radius: float = 1800.0, sides: int = 64) -> None:
        self.bound_radius = bound_radius
        self.sides = sides

    def compute(self, half_planes: list[HalfPlane]) -> ConvexRegion:
        poly = _polygon_circle((0.0, 0.0), self.bound_radius, self.sides)
        for hp in half_planes:
            poly = _clip_half_plane(poly, hp)
            if len(poly) < 3:
                return ConvexRegion.empty_region()
        return ConvexRegion(tuple(poly), bounded=True)


class IncrementalWedgeClipNode(Node):
    """Add one observation to a previous ``RegionState``.

    Two modes:
    * ``clip_previous``: start from the previous polygon and clip only the new
      wedge.  Cheap, but can accumulate numerical error.
    * ``rebuild``: replay all half-planes from the stored set.  Slower but safer.
    """

    input_types = {"state": RegionState, "observation": Observation}
    output_type = RegionState

    def __init__(self, mode: str = "clip_previous", bound_radius: float = 1800.0, sides: int = 64) -> None:
        if mode not in {"clip_previous", "rebuild"}:
            raise ValueError(mode)
        self.mode = mode
        self.bound_radius = bound_radius
        self.sides = sides

    def compute(self, state: RegionState, observation: Observation) -> RegionState:
        new_hps = _wedge_half_planes(
            Wedge(observation.point, observation.bearing_deg, observation.half_width_deg)
        )
        all_hps = state.half_planes + new_hps
        next_version = state.version + 1
        if state.region.is_empty:
            region = ConvexRegion.empty_region(version=next_version)
        elif self.mode == "clip_previous":
            poly = list(state.region.vertices)
            for hp in new_hps:
                poly = _clip_half_plane(poly, hp)
                if len(poly) < 3:
                    region = ConvexRegion.empty_region(version=next_version)
                    break
            else:
                region = ConvexRegion(tuple(poly), bounded=state.region.bounded, version=next_version)
        else:
            base = ClipHalfPlanesNode(self.bound_radius, self.sides).compute(list(all_hps))
            region = ConvexRegion(base.vertices, bounded=base.bounded, empty=base.empty, version=next_version)
        return RegionState(all_hps, region, next_version)


class ExhaustiveDiameterNode(Node):
    """O(m^2) reference implementation; replace by rotating calipers in C++."""

    input_types = {"region": ConvexRegion}
    output_type = DiameterWitness

    def compute(self, region: ConvexRegion) -> DiameterWitness:
        verts = region.vertices
        best = DiameterWitness((0.0, 0.0), (0.0, 0.0), -1.0)
        for i in range(len(verts)):
            for j in range(i + 1, len(verts)):
                d = _dist(verts[i], verts[j])
                if d > best.distance:
                    best = DiameterWitness(verts[i], verts[j], d)
        return best


class RotatingCalipersDiameterNode(Node):
    """O(m) diameter by rotating calipers.

    Geometric fact: for a convex body K,
        diam(K) = max_{||u||=1} (h_K(u) + h_K(-u)),
    i.e. the maximum distance between parallel supporting lines.
    The support points at the maximizing orientation form a diameter pair.
    The implementation below checks vertex distances of antipodal pairs.
    """

    input_types = {"region": ConvexRegion}
    output_type = DiameterWitness

    def compute(self, region: ConvexRegion) -> DiameterWitness:
        pts = list(region.vertices)
        n = len(pts)
        if n == 0:
            return DiameterWitness((0.0, 0.0), (0.0, 0.0), 0.0)
        if n == 1:
            return DiameterWitness(pts[0], pts[0], 0.0)
        if n == 2:
            return DiameterWitness(pts[0], pts[1], _dist(pts[0], pts[1]))

        def cross(o: Vec2, a: Vec2, b: Vec2) -> float:
            return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

        # Initial antipodal vertex for edge (n-1, 0).
        k = 1
        while abs(cross(pts[n - 1], pts[0], pts[(k + 1) % n])) > abs(
            cross(pts[n - 1], pts[0], pts[k])
        ):
            k = (k + 1) % n

        best_a, best_b, best_d = pts[0], pts[k], _dist(pts[0], pts[k])
        i, j = 0, k
        guard = 0
        while i < n and guard < 4 * n:
            guard += 1
            # Advance the antipodal pointer for edge i -> i+1.
            while abs(
                cross(pts[i], pts[(i + 1) % n], pts[(j + 1) % n])
            ) > abs(cross(pts[i], pts[(i + 1) % n], pts[j])):
                j = (j + 1) % n
            for a_idx, b_idx in (
                (i, j),
                ((i + 1) % n, j),
                (i, (j + 1) % n),
                ((i + 1) % n, (j + 1) % n),
            ):
                d = _dist(pts[a_idx], pts[b_idx])
                if d > best_d:
                    best_a, best_b, best_d = pts[a_idx], pts[b_idx], d
            i += 1
        return DiameterWitness(best_a, best_b, best_d)


class WelzlMinCircleNode(Node):
    input_types = {"region": ConvexRegion}
    output_type = MinCircleResult

    def __init__(self, seed: int = 20260913) -> None:
        self.seed = seed

    def _welzl(self, pts: list[Vec2]) -> tuple[Vec2, float, tuple[Vec2, ...]]:
        if not pts:
            return (0.0, 0.0), 0.0, ()
        rnd = Random(self.seed)
        rnd.shuffle(pts)
        center, radius, basis = pts[0], 0.0, (pts[0],)
        for i, p in enumerate(pts):
            if _in_circle(center, radius, p):
                continue
            center, radius, basis = p, 0.0, (p,)
            for j in range(i):
                q = pts[j]
                if _in_circle(center, radius, q):
                    continue
                center, radius = _circle_from_two(p, q)
                basis = (p, q)
                for k in range(j):
                    r = pts[k]
                    if _in_circle(center, radius, r):
                        continue
                    circ = _circle_from_three(p, q, r)
                    if circ is None:
                        continue
                    center, radius = circ
                    basis = (p, q, r)
        return center, radius, basis

    def compute(self, region: ConvexRegion) -> MinCircleResult:
        # Deduplicate while preserving a stable order.
        pts = list(dict.fromkeys(region.vertices))
        center, radius, basis = self._welzl(pts)
        return MinCircleResult(center=center, radius=radius, basis=basis)


class DiameterCoverageNode(Node):
    input_types = {"region": ConvexRegion, "diameter": DiameterWitness}
    output_type = CoverageDecision

    def compute(self, region: ConvexRegion, diameter: DiameterWitness) -> CoverageDecision:
        ax, ay = diameter.a
        bx, by = diameter.b
        for vx, vy in region.vertices:
            if (vx - ax) * (vx - bx) + (vy - ay) * (vy - by) > 1e-9:
                center = ((ax + bx) / 2.0, (ay + by) / 2.0)
                return CoverageDecision(
                    covered=False,
                    radius=diameter.distance / 2.0,
                    center=center,
                    kind="diameter_circle",
                    reason="a polygon vertex is outside the disk with the diameter as diameter",
                )
        center = ((ax + bx) / 2.0, (ay + by) / 2.0)
        return CoverageDecision(
            covered=True,
            radius=diameter.distance / 2.0,
            center=center,
            kind="diameter_circle",
            reason="all vertices lie in the diameter disk",
        )


class ClearabilityNode(Node):
    input_types = {"min_circle": MinCircleResult}
    output_type = CoverageDecision

    def __init__(self, tolerance_m: float = 20.0) -> None:
        self.tolerance_m = tolerance_m

    def compute(self, min_circle: MinCircleResult) -> CoverageDecision:
        return CoverageDecision(
            covered=min_circle.radius <= self.tolerance_m + 1e-9,
            radius=min_circle.radius,
            center=min_circle.center,
            kind="clear_20m",
            reason=f"min enclosing radius <= {self.tolerance_m:g}m",
        )


class PredictedCenterNode(Node):
    """Least-squares intersection of the centre rays.

    This is the cheap "directly use the 2-degree ray" milestone.
    """

    input_types = {"observations": list}
    output_type = CenterEstimate

    def compute(self, observations: list[Observation]) -> CenterEstimate:
        aa = ab = bb = x = y = 0.0
        for o in observations:
            ux, uy = _unit(o.bearing_deg)
            # line normal
            nx, ny = -uy, ux
            px, py = o.point
            proj = nx * px + ny * py
            aa += nx * nx
            ab += nx * ny
            bb += ny * ny
            x += nx * proj
            y += ny * proj
        det = aa * bb - ab * ab
        if abs(det) < 1e-12:
            cx = sum(o.point[0] for o in observations) / max(1, len(observations))
            cy = sum(o.point[1] for o in observations) / max(1, len(observations))
        else:
            cx = (x * bb - y * ab) / det
            cy = (y * aa - x * ab) / det
        # RMS angular residual.
        ss = 0.0
        for o in observations:
            dx = cx - o.point[0]
            dy = cy - o.point[1]
            angle = degrees(atan2(dy, dx)) % 360.0
            d = abs((angle - o.bearing_deg + 180.0) % 360.0 - 180.0)
            ss += d * d
        rms = sqrt(ss / max(1, len(observations)))
        return CenterEstimate((cx, cy), rms)


class LinearizedRegionNode(Node):
    """Small-angle strip approximation around a predicted centre.

    This is intentionally a fast approximate path.  Use it for warm starts,
    filtering and early planning; do not use it as a proof of coverage.
    """

    input_types = {"observations": list, "center": CenterEstimate}
    output_type = ConvexRegion

    def __init__(self, bound_radius: float = 1800.0, sides: int = 64) -> None:
        self.bound_radius = bound_radius
        self.sides = sides

    def compute(self, observations: list[Observation], center: CenterEstimate) -> ConvexRegion:
        cx, cy = center.center
        hps: list[HalfPlane] = []
        for o in observations:
            px, py = o.point
            dx, dy = cx - px, cy - py
            d = hypot(dx, dy)
            if d < 1e-9:
                continue
            ux, uy = _unit(o.bearing_deg)
            # signed residual from centre ray, in degrees
            center_ang = degrees(atan2(dy, dx)) % 360.0
            delta = ((center_ang - o.bearing_deg + 180.0) % 360.0 - 180.0)
            nperp = (-uy, ux)
            # nperp . (Q - center) + delta*d  <= eps*d
            # => nperp.Q - nperp.center + delta*d - eps*d <= 0
            eps_rad = radians(o.half_width_deg)
            delta_rad = radians(delta)
            # half-plane in standard form a*x+b*y+c >= 0
            # -(nperp.Q - nperp.center + (delta_rad-eps_rad)*d) >= 0
            a = -nperp[0]
            b = -nperp[1]
            c = nperp[0] * cx + nperp[1] * cy - (delta_rad - eps_rad) * d
            hps.append(HalfPlane(a, b, c))
            # (nperp.Q - nperp.center + (delta_rad+eps_rad)*d) >= 0
            hps.append(
                HalfPlane(
                    nperp[0],
                    nperp[1],
                    -nperp[0] * cx - nperp[1] * cy + (delta_rad + eps_rad) * d,
                )
            )
        return ClipHalfPlanesNode(self.bound_radius, self.sides).compute(hps)


class GreedyMultiCircleCoverNode(Node):
    """Greedy sampled covering by fixed-radius disks.

    This is a placeholder for a true minimum-disk-cover solver.  It is useful
    to produce the plan type and to decide whether another observation is
    necessary.
    """

    input_types = {"region": ConvexRegion}
    output_type = CircleCoverPlan

    def __init__(self, radius: float = 20.0, max_disks: int = 100) -> None:
        self.radius = radius
        self.max_disks = max_disks

    def compute(self, region: ConvexRegion) -> CircleCoverPlan:
        verts = list(region.vertices)
        samples = list(verts)
        for i in range(len(verts)):
            a = verts[i]
            b = verts[(i + 1) % len(verts)]
            samples.append(((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0))
        uncovered = set(range(len(samples)))
        centers: list[Vec2] = []
        while uncovered and len(centers) < self.max_disks:
            best_center = samples[next(iter(uncovered))]
            best_cover = {i for i in uncovered if _dist(best_center, samples[i]) <= self.radius + 1e-9}
            # Search beyond samples too: candidate centres at samples and midpoints.
            candidates = samples
            for c in candidates:
                cover = {i for i in uncovered if _dist(c, samples[i]) <= self.radius + 1e-9}
                if len(cover) > len(best_cover):
                    best_center, best_cover = c, cover
            if not best_cover:
                break
            centers.append(best_center)
            uncovered -= best_cover
        remaining = tuple(samples[i] for i in uncovered)
        return CircleCoverPlan(
            radius=self.radius,
            centers=tuple(centers),
            covered_all=not uncovered,
            exact=False,
            remaining_vertices=remaining,
        )


class ObservationPlannerNode(Node):
    """Interface for the next-observation planner.

    The current implementation returns candidate directions around the current
    region; replace with an information-gain / geometric-dilution optimizer.
    """

    input_types = {"region": ConvexRegion, "plan": CircleCoverPlan}
    output_type = list

    def __init__(self, reach_m: float = 1500.0, samples: int = 8) -> None:
        self.reach_m = reach_m
        self.samples = samples

    def compute(self, region: ConvexRegion, plan: CircleCoverPlan) -> list[ObservationSuggestion]:
        if region.is_empty:
            return []
        cx = sum(v[0] for v in region.vertices) / len(region.vertices)
        cy = sum(v[1] for v in region.vertices) / len(region.vertices)
        out: list[ObservationSuggestion] = []
        for k in range(self.samples):
            ang = 2.0 * pi * k / self.samples
            out.append(
                ObservationSuggestion(
                    point=(cx + self.reach_m * cos(ang), cy + self.reach_m * sin(ang)),
                    score=float(len(plan.centers)),
                    reason="heuristic ring around current region; replace by info-gain planner",
                )
            )
        return out


# ---------------------------------------------------------------------------
# Graph factories: show alternative paths explicitly.
# ---------------------------------------------------------------------------
def build_diameter_path(bound_radius: float = 1800.0) -> ComputeGraph:
    g = ComputeGraph()
    g.add("wedges", MakeWedgesNode(), {"observations": GraphInput("observations")})
    g.add("half_planes", WedgesToHalfPlanesNode(), {"wedges": NodeRef("wedges")})
    g.add("region", ClipHalfPlanesNode(bound_radius=bound_radius), {"half_planes": NodeRef("half_planes")})
    g.add("diameter", RotatingCalipersDiameterNode(), {"region": NodeRef("region")})
    g.add("decision", DiameterCoverageNode(), {"region": NodeRef("region"), "diameter": NodeRef("diameter")})
    return g


def build_welzl_path(bound_radius: float = 1800.0, tolerance_m: float = 20.0) -> ComputeGraph:
    g = ComputeGraph()
    g.add("wedges", MakeWedgesNode(), {"observations": GraphInput("observations")})
    g.add("half_planes", WedgesToHalfPlanesNode(), {"wedges": NodeRef("wedges")})
    g.add("region", ClipHalfPlanesNode(bound_radius=bound_radius), {"half_planes": NodeRef("half_planes")})
    g.add("min_circle", WelzlMinCircleNode(), {"region": NodeRef("region")})
    g.add("decision", ClearabilityNode(tolerance_m=tolerance_m), {"min_circle": NodeRef("min_circle")})
    g.add("multi_cover", GreedyMultiCircleCoverNode(radius=tolerance_m), {"region": NodeRef("region")})
    return g


def build_predicted_path(bound_radius: float = 1800.0, tolerance_m: float = 20.0) -> ComputeGraph:
    g = ComputeGraph()
    g.add("center", PredictedCenterNode(), {"observations": GraphInput("observations")})
    g.add("region", LinearizedRegionNode(bound_radius=bound_radius), {"observations": GraphInput("observations"), "center": NodeRef("center")})
    g.add("min_circle", WelzlMinCircleNode(), {"region": NodeRef("region")})
    g.add("decision", ClearabilityNode(tolerance_m=tolerance_m), {"min_circle": NodeRef("min_circle")})
    return g


def make_initial_state(bound_radius: float = 1800.0, sides: int = 64) -> RegionState:
    region = ConvexRegion(tuple(_polygon_circle((0.0, 0.0), bound_radius, sides)))
    return RegionState(half_planes=(), region=region, version=0)


__all__ = [
    "Vec2",
    "Observation",
    "Wedge",
    "HalfPlane",
    "ConvexRegion",
    "DiameterWitness",
    "MinCircleResult",
    "CoverageDecision",
    "CircleCoverPlan",
    "CenterEstimate",
    "RegionState",
    "ObservationSuggestion",
    "Node",
    "ComputeGraph",
    "NodeRef",
    "GraphInput",
    "MakeWedgesNode",
    "WedgesToHalfPlanesNode",
    "ClipHalfPlanesNode",
    "IncrementalWedgeClipNode",
    "DiameterCache",
    "ExhaustiveDiameterNode",
    "RotatingCalipersDiameterNode",
    "WelzlMinCircleNode",
    "DiameterCoverageNode",
    "ClearabilityNode",
    "PredictedCenterNode",
    "LinearizedRegionNode",
    "GreedyMultiCircleCoverNode",
    "ObservationPlannerNode",
    "build_diameter_path",
    "build_welzl_path",
    "build_predicted_path",
    "make_initial_state",
]
