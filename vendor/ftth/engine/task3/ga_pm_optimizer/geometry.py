"""Dependency-free metric and polygon operations used by Task 3."""

from __future__ import annotations

import math
from typing import Any, Iterable


EARTH_A = 6378137.0
EARTH_E2 = 0.0066943799901413165
UTM_K0 = 0.9996
UTM_CENTRAL_MERIDIAN = math.radians(-9.0)
METRES_PER_DEGREE = 111320.0
EPS = 1e-10

Point = tuple[float, float]


def point(value: Any) -> Point:
    if isinstance(value, dict):
        return float(value.get("x", value.get("longitude"))), float(value.get("y", value.get("latitude")))
    return float(value[0]), float(value[1])


def utm29(lon: float, lat: float) -> Point:
    """Convert WGS84 longitude/latitude to EPSG:32629 metres."""
    lon_r = math.radians(lon)
    lat_r = math.radians(lat)
    ep2 = EARTH_E2 / (1.0 - EARTH_E2)
    n = EARTH_A / math.sqrt(1.0 - EARTH_E2 * math.sin(lat_r) ** 2)
    t = math.tan(lat_r) ** 2
    c = ep2 * math.cos(lat_r) ** 2
    a = math.cos(lat_r) * (lon_r - UTM_CENTRAL_MERIDIAN)
    m = EARTH_A * (
        (1 - EARTH_E2 / 4 - 3 * EARTH_E2**2 / 64 - 5 * EARTH_E2**3 / 256) * lat_r
        - (3 * EARTH_E2 / 8 + 3 * EARTH_E2**2 / 32 + 45 * EARTH_E2**3 / 1024) * math.sin(2 * lat_r)
        + (15 * EARTH_E2**2 / 256 + 45 * EARTH_E2**3 / 1024) * math.sin(4 * lat_r)
        - (35 * EARTH_E2**3 / 3072) * math.sin(6 * lat_r)
    )
    x = UTM_K0 * n * (
        a
        + (1 - t + c) * a**3 / 6
        + (5 - 18 * t + t**2 + 72 * c - 58 * ep2) * a**5 / 120
    ) + 500000.0
    y = UTM_K0 * (
        m
        + n
        * math.tan(lat_r)
        * (
            a**2 / 2
            + (5 - t + 9 * c + 4 * c**2) * a**4 / 24
            + (61 - 58 * t + t**2 + 600 * c - 330 * ep2) * a**6 / 720
        )
    )
    return x, y


def metres(first: Point, second: Point) -> float:
    first_m = utm29(*first)
    second_m = utm29(*second)
    return math.hypot(first_m[0] - second_m[0], first_m[1] - second_m[1])


def interpolate(first: Point, second: Point, ratio: float) -> Point:
    return first[0] + ratio * (second[0] - first[0]), first[1] + ratio * (second[1] - first[1])


def offset_perpendicular(first: Point, second: Point, ratio: float, offset_m: float) -> Point:
    """Offset a segment point to its left by a signed distance in metres."""
    base = interpolate(first, second, ratio)
    latitude = math.radians(base[1])
    longitude_scale = METRES_PER_DEGREE * max(0.1, math.cos(latitude))
    latitude_scale = 110540.0
    dx = (second[0] - first[0]) * longitude_scale
    dy = (second[1] - first[1]) * latitude_scale
    length = math.hypot(dx, dy)
    if length <= EPS or abs(offset_m) <= EPS:
        return base
    offset_x = -dy / length * offset_m
    offset_y = dx / length * offset_m
    candidate = base[0] + offset_x / longitude_scale, base[1] + offset_y / latitude_scale
    actual_distance = metres(base, candidate)
    if actual_distance <= EPS:
        return base
    correction = abs(offset_m) / actual_distance
    return (
        base[0] + offset_x * correction / longitude_scale,
        base[1] + offset_y * correction / latitude_scale,
    )


def project_ratio(target: Point, first: Point, second: Point) -> float:
    target_m, first_m, second_m = utm29(*target), utm29(*first), utm29(*second)
    dx, dy = second_m[0] - first_m[0], second_m[1] - first_m[1]
    denominator = dx * dx + dy * dy
    if denominator <= EPS:
        return 0.0
    ratio = ((target_m[0] - first_m[0]) * dx + (target_m[1] - first_m[1]) * dy) / denominator
    return max(0.0, min(1.0, ratio))


def point_on_segment(target: Point, first: Point, second: Point, tolerance: float = 1e-9) -> bool:
    cross = (second[0] - first[0]) * (target[1] - first[1]) - (second[1] - first[1]) * (target[0] - first[0])
    scale = max(1.0, abs(second[0] - first[0]), abs(second[1] - first[1]))
    if abs(cross) > tolerance * scale:
        return False
    return (
        min(first[0], second[0]) - tolerance <= target[0] <= max(first[0], second[0]) + tolerance
        and min(first[1], second[1]) - tolerance <= target[1] <= max(first[1], second[1]) + tolerance
    )


def point_in_ring(target: Point, ring: list[Point]) -> bool:
    if len(ring) < 3:
        return False
    inside = False
    previous = ring[-1]
    for current in ring:
        if point_on_segment(target, previous, current):
            return True
        if (current[1] > target[1]) != (previous[1] > target[1]):
            cross_x = (previous[0] - current[0]) * (target[1] - current[1]) / (previous[1] - current[1]) + current[0]
            if target[0] < cross_x:
                inside = not inside
        previous = current
    return inside


def point_in_polygons(target: Point, polygons: Iterable[list[Point]]) -> bool:
    return any(point_in_ring(target, ring) for ring in polygons)


def point_with_clearance_in_polygons(
    target: Point,
    polygons: Iterable[list[Point]],
    clearance_m: float,
) -> bool:
    """Require a point and a small four-direction neighbourhood inside a polygon union."""
    polygon_list = list(polygons)
    if not point_in_polygons(target, polygon_list):
        return False
    if clearance_m <= 0:
        return True
    longitude_scale = 111320.0 * max(0.1, math.cos(math.radians(target[1])))
    latitude_scale = 110540.0
    offsets = (
        (clearance_m / longitude_scale, 0.0),
        (-clearance_m / longitude_scale, 0.0),
        (0.0, clearance_m / latitude_scale),
        (0.0, -clearance_m / latitude_scale),
    )
    return all(
        point_in_polygons((target[0] + dx, target[1] + dy), polygon_list)
        for dx, dy in offsets
    )


def segment_intersection_ratio(first: Point, second: Point, third: Point, fourth: Point) -> float | None:
    ax, ay = second[0] - first[0], second[1] - first[1]
    bx, by = fourth[0] - third[0], fourth[1] - third[1]
    denominator = ax * by - ay * bx
    if abs(denominator) <= EPS:
        return None
    dx, dy = third[0] - first[0], third[1] - first[1]
    ratio_a = (dx * by - dy * bx) / denominator
    ratio_b = (dx * ay - dy * ax) / denominator
    if -EPS <= ratio_a <= 1.0 + EPS and -EPS <= ratio_b <= 1.0 + EPS:
        return max(0.0, min(1.0, ratio_a))
    return None


def polygon_intersection_ratios(first: Point, second: Point, polygons: Iterable[list[Point]]) -> list[float]:
    ratios = {0.0, 1.0}
    for ring in polygons:
        for start, end in zip(ring, [*ring[1:], ring[0]]):
            ratio = segment_intersection_ratio(first, second, start, end)
            if ratio is not None:
                ratios.add(round(ratio, 12))
    return sorted(ratios)


def outside_length(first: Point, second: Point, polygons: Iterable[list[Point]]) -> float:
    polygon_list = list(polygons)
    ratios = polygon_intersection_ratios(first, second, polygon_list)
    length = metres(first, second)
    outside = 0.0
    for start, end in zip(ratios, ratios[1:]):
        if end - start <= EPS:
            continue
        midpoint = interpolate(first, second, (start + end) / 2.0)
        if not point_in_polygons(midpoint, polygon_list):
            outside += length * (end - start)
    return outside


def polyline_outside_length(coordinates: list[Any], polygons: Iterable[list[Point]]) -> tuple[float, float]:
    points = [point(item) for item in coordinates]
    polygon_list = list(polygons)
    total = 0.0
    outside = 0.0
    for first, second in zip(points, points[1:]):
        segment_length = metres(first, second)
        total += segment_length
        outside += outside_length(first, second, polygon_list)
    return total, outside


def extract_zpm_polygons(region: dict[str, Any]) -> list[list[Point]]:
    records = (region.get("geometry") or {}).get("polygons") or []
    polygons = []
    for record in records:
        raw = record.get("polygon") if isinstance(record, dict) else record
        ring = [point(item) for item in (raw or [])]
        if len(ring) >= 3:
            polygons.append(ring)
    return polygons
