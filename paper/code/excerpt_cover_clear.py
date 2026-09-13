def cells_covering_region(
    region: Sequence[Point],
    *,
    step_m: float = DEFAULT_CELL_STEP_M,
    exclusions: Iterable[tuple[Point, float]] = (),
    reach_factor: float = 1.35,
) -> tuple[list[tuple[float, float]], int, int, int]:

    if len(region) < 3:
        return [], 0, 0, 0

    xs = [v.x for v in region]
    ys = [v.y for v in region]
    half = step_m / 2.0

    ox = min(xs)
    oy = min(ys)
    nx = max(1, math.ceil((max(xs) - ox) / step_m)) if max(xs) > ox else 1
    ny = max(1, math.ceil((max(ys) - oy) / step_m)) if max(ys) > oy else 1

    disks = list(exclusions)
    centers: list[tuple[float, float]] = []
    total = excluded = unreachable = 0

    for i in range(nx):
        cx = ox + (i + 0.5) * step_m
        for j in range(ny):
            cy = oy + (j + 0.5) * step_m
            if not _square_intersects_polygon(cx, cy, half, region):
                continue
            total += 1
            if any(_cell_fully_inside_disk(cx, cy, half, d) for d in disks):
                excluded += 1
                continue
            if not _is_within_reach(Point(cx, cy), reach_factor):
                unreachable += 1
                continue
            centers.append((cx, cy))
    return centers, total, excluded, unreachable

def region_clear_plan(
    region: Sequence[Point],
    start: Point,
    *,
    tol_m: float = OPTICAL_RANGE_M,
    step_m: float | None = None,
    exclusions: Iterable[tuple[Point, float]] = (),
    max_probes: int | None = None,
    reach_factor: float = 1.35,
) -> RegionClearPlan | None:

    if len(region) < 3:
        return None
    if step_m is None:
        step_m = min(DEFAULT_CELL_STEP_M, tol_m * math.sqrt(2.0))
    if step_m / math.sqrt(2.0) > tol_m + _EPS:
        raise ValueError(
            f"step_m={step_m} 不满足 step/√2 <= tol_m={tol_m}，覆盖保证不成立"
        )

    cells, total, excluded, unreachable = cells_covering_region(
        region, step_m=step_m, exclusions=exclusions, reach_factor=reach_factor
    )
    if not cells:
        return None
    if max_probes is not None and len(cells) > max_probes:
        return None

    ordered, travel = _nearest_neighbour_order(cells, start)
    return RegionClearPlan(
        centers=ordered,
        radius_m=float(tol_m),
        step_m=float(step_m),
        cells_total=total,
        cells_excluded=excluded,
        cells_unreachable=unreachable,
        travel_m=travel,
        guaranteed=unreachable == 0,
    )

def plan_beats_measure(
    plan: RegionClearPlan,
    *,
    measure_travel_m: float,
    switch: bool = True,
    speed_mps: float = MOVE_SPEED_MPS,
) -> bool:

    if plan is None or not plan.centers:
        return False
    switch_s = CHANNEL_SWITCH_SECONDS if switch else 0.0
    measure_side = (
        measure_travel_m / speed_mps
        + MEASURE_SECONDS
        + switch_s
        + OPTICAL_SECONDS
        + CLEAR_SECONDS
    )
    return plan.total_cost_s(speed_mps) <= measure_side + _EPS
