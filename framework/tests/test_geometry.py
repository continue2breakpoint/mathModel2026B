from mathmodel2026b.geometry import Point, q3_seven_point_cover_valid, regular_hexagon_scan_points


def test_point_distance() -> None:
    assert Point(0, 0).distance_to(Point(3, 4)) == 5


def test_hexagon_has_seven_points() -> None:
    points = regular_hexagon_scan_points(1400)
    assert len(points) == 7
    assert points[0] == Point(0, 0)


def test_q3_ring_radius() -> None:
    assert q3_seven_point_cover_valid(1400)
    assert not q3_seven_point_cover_valid(1000)
    assert not q3_seven_point_cover_valid(1800)
