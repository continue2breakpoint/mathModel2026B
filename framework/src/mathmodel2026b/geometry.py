from __future__ import annotations

from dataclasses import dataclass
from math import cos, hypot, pi, radians, sin


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float

    def distance_to(self, other: "Point") -> float:
        return hypot(self.x - other.x, self.y - other.y)


def unit_from_deg(angle_deg: float) -> Point:
    angle = radians(angle_deg)
    return Point(cos(angle), sin(angle))


def regular_hexagon_scan_points(radius: float = 1400.0) -> list[Point]:
    points = [Point(0.0, 0.0)]
    for k in range(6):
        angle = k * pi / 3
        points.append(Point(radius * cos(angle), radius * sin(angle)))
    return points


def q3_seven_point_cover_valid(radius: float) -> bool:
    lower = 900.0 * (3.0 ** 0.5) - 100.0 * (19.0 ** 0.5)
    upper = 1000.0 * (3.0 ** 0.5)
    return lower <= radius <= upper
