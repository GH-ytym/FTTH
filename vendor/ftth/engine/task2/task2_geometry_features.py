"""Compute remote-sensing geometry features for task 2 and task 6.

The module is intentionally dependency-free.  It accepts GeoJSON/JSON or a
GeoPackage with ``GEO_BUILDING`` and ``GEO_ROAD`` layers, reads the task-2
ZNRO/ZPM region result, and writes building groups plus three entrance types:
building, building-group, and community (ZNRO).

All geometric calculations are done in EPSG:32629 metres.  The output keeps
both projected coordinates and WGS84 coordinates so task 6 can consume the
points without re-projecting them blindly.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import struct
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Polygon as PlotPolygon

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from project_paths import TASK2_ZONE_PARTITION_OUTPUT_DIR


EARTH_A = 6378137.0
EARTH_E2 = 0.0066943799901413165
UTM_K0 = 0.9996
UTM_CENTRAL_MERIDIAN = math.radians(-9.0)
EPS = 1e-9


def utm29(lon: float, lat: float) -> tuple[float, float]:
    """WGS84 longitude/latitude to EPSG:32629."""
    lon_r = math.radians(lon)
    lat_r = math.radians(lat)
    e2 = EARTH_E2
    ep2 = e2 / (1.0 - e2)
    n = EARTH_A / math.sqrt(1.0 - e2 * math.sin(lat_r) ** 2)
    t = math.tan(lat_r) ** 2
    c = ep2 * math.cos(lat_r) ** 2
    a = math.cos(lat_r) * (lon_r - UTM_CENTRAL_MERIDIAN)
    m = EARTH_A * ((1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256) * lat_r
                   - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * math.sin(2 * lat_r)
                   + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * math.sin(4 * lat_r)
                   - (35 * e2**3 / 3072) * math.sin(6 * lat_r))
    x = UTM_K0 * n * (a + (1 - t + c) * a**3 / 6
                      + (5 - 18 * t + t**2 + 72 * c - 58 * ep2) * a**5 / 120) + 500000.0
    y = UTM_K0 * (m + n * math.tan(lat_r) * (a**2 / 2
                      + (5 - t + 9 * c + 4 * c**2) * a**4 / 24
                      + (61 - 58 * t + t**2 + 600 * c - 330 * ep2) * a**6 / 720))
    return x, y


def inv_utm29(x: float, y: float) -> tuple[float, float]:
    """EPSG:32629 to WGS84 longitude/latitude."""
    e2 = EARTH_E2
    ep2 = e2 / (1.0 - e2)
    m = y / UTM_K0
    mu = m / (EARTH_A * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
            + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
            + (151 * e1**3 / 96) * math.sin(6 * mu)
            + (1097 * e1**4 / 512) * math.sin(8 * mu))
    n1 = EARTH_A / math.sqrt(1 - e2 * math.sin(phi1) ** 2)
    t1 = math.tan(phi1) ** 2
    c1 = ep2 * math.cos(phi1) ** 2
    r1 = EARTH_A * (1 - e2) / (1 - e2 * math.sin(phi1) ** 2) ** 1.5
    d = (x - 500000.0) / (n1 * UTM_K0)
    lat = phi1 - (n1 * math.tan(phi1) / r1) * (
        d**2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * ep2) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * ep2 - 3 * c1**2) * d**6 / 720
    )
    lon = UTM_CENTRAL_MERIDIAN + (
        d - (1 + 2 * t1 + c1) * d**3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * ep2 + 24 * t1**2) * d**5 / 120
    ) / math.cos(phi1)
    return math.degrees(lon), math.degrees(lat)


def _coord(point: Any) -> tuple[float, float]:
    if isinstance(point, dict):
        return float(point.get("x", point.get("longitude"))), float(point.get("y", point.get("latitude")))
    return float(point[0]), float(point[1])


def _transform(point: Any, crs: str) -> tuple[float, float]:
    x, y = _coord(point)
    normalized = str(crs or "EPSG:4326").upper().replace(" ", "")
    if normalized in {"EPSG:32629", "32629", "UTM29N"}:
        return x, y
    if normalized in {"EPSG:4326", "4326", "WGS84"}:
        return utm29(x, y)
    raise ValueError(f"Unsupported CRS {crs!r}; use EPSG:4326 or EPSG:32629")


def _wkb_reader(blob: bytes) -> bytes:
    raw = bytes(blob)
    if raw[:2] == b"GP":
        raw = raw[8:]
    return raw


def _read_wkb(raw: bytes, offset: int = 0) -> tuple[dict[str, Any], int]:
    order = raw[offset]
    offset += 1
    endian = "<" if order == 1 else ">"
    geometry_type = struct.unpack_from(endian + "I", raw, offset)[0]
    offset += 4
    base_type = geometry_type & 0xFF
    if base_type == 1:
        x, y = struct.unpack_from(endian + "dd", raw, offset)
        return {"type": "Point", "coordinates": [x, y]}, offset + 16
    if base_type == 2:
        count = struct.unpack_from(endian + "I", raw, offset)[0]
        offset += 4
        points = []
        for _ in range(count):
            points.append(list(struct.unpack_from(endian + "dd", raw, offset)))
            offset += 16
        return {"type": "LineString", "coordinates": points}, offset
    if base_type == 3:
        ring_count = struct.unpack_from(endian + "I", raw, offset)[0]
        offset += 4
        rings = []
        for _ in range(ring_count):
            count = struct.unpack_from(endian + "I", raw, offset)[0]
            offset += 4
            ring = []
            for _ in range(count):
                ring.append(list(struct.unpack_from(endian + "dd", raw, offset)))
                offset += 16
            rings.append(ring)
        return {"type": "Polygon", "coordinates": rings}, offset
    if base_type == 6:
        count = struct.unpack_from(endian + "I", raw, offset)[0]
        offset += 4
        polygons = []
        for _ in range(count):
            polygon, offset = _read_wkb(raw, offset)
            polygons.append(polygon["coordinates"])
        return {"type": "MultiPolygon", "coordinates": polygons}, offset
    if base_type == 5:
        count = struct.unpack_from(endian + "I", raw, offset)[0]
        offset += 4
        lines = []
        for _ in range(count):
            line, offset = _read_wkb(raw, offset)
            lines.append(line["coordinates"])
        return {"type": "MultiLineString", "coordinates": lines}, offset
    raise ValueError(f"Unsupported WKB geometry type {base_type}")


def _geometry_from_blob(blob: bytes) -> dict[str, Any]:
    geometry, _ = _read_wkb(_wkb_reader(blob))
    return geometry


def _load_json_features(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    # A dedicated task-2 input keeps all recognition layers under one JSON
    # document.  ``load_features`` supplies the layer key so resolve it here
    # without changing the GeoJSON/GeoPackage paths used by older callers.
    if raw.get("type") == "FeatureCollection":
        result = []
        for feature in raw.get("features") or []:
            item = dict(feature.get("properties") or {})
            item["geometry"] = feature.get("geometry")
            result.append(item)
        return result
    if raw.get("type") == "Feature":
        item = dict(raw.get("properties") or {})
        item["geometry"] = raw.get("geometry")
        return [item]
    if isinstance(raw, list):
        return [dict(item) for item in raw]
    return [dict(raw)]


def _load_gpkg(path: Path, layer: str) -> list[dict[str, Any]]:
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(f'SELECT * FROM "{layer}" ORDER BY fid').fetchall()
    except sqlite3.Error as exc:
        raise ValueError(f"Layer {layer!r} is not available in {path}") from exc
    finally:
        connection.close()
    features = []
    for row in rows:
        item = {key: row[key] for key in row.keys() if key != "geom"}
        item["geometry"] = _geometry_from_blob(row["geom"])
        features.append(item)
    return features


def load_features(path: Path, layer: str) -> list[dict[str, Any]]:
    if path.suffix.lower() in {".gpkg", ".sqlite", ".db"}:
        return _load_gpkg(path, layer)
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    recognition = raw.get("recognition") if isinstance(raw, dict) else None
    if isinstance(recognition, dict):
        section_by_layer = {"GEO_BUILDING": ("buildings", "building_groups"), "GEO_ROAD": ("roads",)}
        sections = section_by_layer.get(layer, (layer,))
        section = next((candidate for candidate in sections if isinstance(recognition.get(candidate), list)), sections[0])
        records = recognition.get(section)
        if not isinstance(records, list):
            raise ValueError(f"Recognition section {section!r} is not available in {path}")
        return [dict(record) for record in records]
    return _load_json_features(path)


def _geometry_type(geometry: dict[str, Any]) -> str:
    return str((geometry or {}).get("type") or "")


def polygon_rings(geometry: dict[str, Any]) -> list[list[list[float]]]:
    kind = _geometry_type(geometry)
    coordinates = geometry.get("coordinates") or []
    if kind == "Polygon":
        return [ring for ring in coordinates if len(ring) >= 3]
    if kind == "MultiPolygon":
        rings = []
        for polygon in coordinates:
            rings.extend(ring for ring in polygon if len(ring) >= 3)
        return rings
    return []


def line_parts(geometry: dict[str, Any]) -> list[list[list[float]]]:
    kind = _geometry_type(geometry)
    coordinates = geometry.get("coordinates") or []
    if kind == "LineString":
        return [coordinates] if len(coordinates) >= 2 else []
    if kind == "MultiLineString":
        return [line for line in coordinates if len(line) >= 2]
    if kind == "Polygon":
        return [ring for ring in coordinates if len(ring) >= 2]
    if kind == "MultiPolygon":
        return [ring for polygon in coordinates for ring in polygon if len(ring) >= 2]
    return []


def project_rings(geometry: dict[str, Any], crs: str) -> list[list[tuple[float, float]]]:
    return [[_transform(point, crs) for point in ring] for ring in polygon_rings(geometry)]


def project_lines(geometry: dict[str, Any], crs: str) -> list[list[tuple[float, float]]]:
    return [[_transform(point, crs) for point in line] for line in line_parts(geometry)]


def point_in_ring(point: tuple[float, float], ring: list[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    for first, second in zip(ring, [*ring[1:], ring[0]]):
        if (first[1] > y) != (second[1] > y):
            cross_x = (second[0] - first[0]) * (y - first[1]) / (second[1] - first[1]) + first[0]
            if x <= cross_x:
                inside = not inside
    return inside


def point_in_polygon(point: tuple[float, float], rings: list[list[tuple[float, float]]]) -> bool:
    return bool(rings and point_in_ring(point, rings[0]) and not any(point_in_ring(point, ring) for ring in rings[1:]))


def orientation(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def on_segment(a: tuple[float, float], b: tuple[float, float], p: tuple[float, float]) -> bool:
    return min(a[0], b[0]) - EPS <= p[0] <= max(a[0], b[0]) + EPS and min(a[1], b[1]) - EPS <= p[1] <= max(a[1], b[1]) + EPS


def segment_intersection(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], d: tuple[float, float]) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    denominator = (b[0] - a[0]) * (d[1] - c[1]) - (b[1] - a[1]) * (d[0] - c[0])
    if abs(denominator) <= EPS:
        if abs(orientation(a, b, c)) > EPS or abs(orientation(a, b, d)) > EPS:
            return result
        for point in (a, b, c, d):
            if on_segment(a, b, point) and on_segment(c, d, point):
                result.append(point)
        return dedupe_points(result, 0.01)
    ratio_a = ((c[0] - a[0]) * (d[1] - c[1]) - (c[1] - a[1]) * (d[0] - c[0])) / denominator
    ratio_b = ((c[0] - a[0]) * (b[1] - a[1]) - (c[1] - a[1]) * (b[0] - a[0])) / denominator
    if -EPS <= ratio_a <= 1 + EPS and -EPS <= ratio_b <= 1 + EPS:
        result.append((a[0] + ratio_a * (b[0] - a[0]), a[1] + ratio_a * (b[1] - a[1])))
    return result


def project_point_to_segment(point: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    denominator = dx * dx + dy * dy
    ratio = 0.0 if denominator <= EPS else ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / denominator
    ratio = max(0.0, min(1.0, ratio))
    return a[0] + ratio * dx, a[1] + ratio * dy


def segment_distance(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], d: tuple[float, float]) -> tuple[float, tuple[float, float], tuple[float, float]]:
    intersections = segment_intersection(a, b, c, d)
    if intersections:
        return 0.0, intersections[0], intersections[0]
    candidates = []
    for point, first, second in ((a, c, d), (b, c, d), (c, a, b), (d, a, b)):
        projected = project_point_to_segment(point, first, second)
        candidates.append((math.hypot(point[0] - projected[0], point[1] - projected[1]), point, projected))
    return min(candidates, key=lambda item: item[0])


def iter_edges(rings: Iterable[list[tuple[float, float]]]) -> Iterable[tuple[tuple[float, float], tuple[float, float]]]:
    for ring in rings:
        for index, first in enumerate(ring):
            yield first, ring[(index + 1) % len(ring)]


def nearest_polygons(left: list[list[tuple[float, float]]], right: list[list[tuple[float, float]]]) -> tuple[float, tuple[float, float], tuple[float, float]]:
    best = (float("inf"), (0.0, 0.0), (0.0, 0.0))
    for a, b in iter_edges(left):
        for c, d in iter_edges(right):
            candidate = segment_distance(a, b, c, d)
            if candidate[0] < best[0]:
                best = candidate
    return best


def nearest_polygon_to_line(rings: list[list[tuple[float, float]]], line: list[tuple[float, float]]) -> tuple[float, tuple[float, float], tuple[float, float]]:
    """Find the nearest pair without treating a road's endpoints as an edge."""
    best = (float("inf"), (0.0, 0.0), (0.0, 0.0))
    for a, b in iter_edges(rings):
        for c, d in zip(line, line[1:]):
            candidate = segment_distance(a, b, c, d)
            if candidate[0] < best[0]:
                best = candidate
    return best


def polygons_intersect(left: list[list[tuple[float, float]]], right: list[list[tuple[float, float]]]) -> bool:
    for a, b in iter_edges(left):
        for c, d in iter_edges(right):
            if segment_intersection(a, b, c, d):
                return True
    return bool(left and right and (point_in_polygon(left[0][0], right) or point_in_polygon(right[0][0], left)))


def polygon_centroid(rings: list[list[tuple[float, float]]]) -> tuple[float, float]:
    weighted = []
    for ring in rings:
        signed = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, [*ring[1:], ring[0]]))
        if abs(signed) <= EPS:
            continue
        cx = sum((a[0] + b[0]) * (a[0] * b[1] - b[0] * a[1]) for a, b in zip(ring, [*ring[1:], ring[0]])) / (3 * signed)
        cy = sum((a[1] + b[1]) * (a[0] * b[1] - b[0] * a[1]) for a, b in zip(ring, [*ring[1:], ring[0]])) / (3 * signed)
        weighted.append((signed, cx, cy))
    total = sum(item[0] for item in weighted)
    if abs(total) <= EPS:
        return rings[0][0]
    return sum(item[0] * item[1] for item in weighted) / total, sum(item[0] * item[2] for item in weighted) / total


def polygon_area(rings: list[list[tuple[float, float]]]) -> float:
    return abs(sum(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, [*ring[1:], ring[0]])) for ring in rings)) / 2.0


def convex_hull(points: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    unique = sorted(set(points))
    if len(unique) <= 2:
        return unique
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def dedupe_points(points: Iterable[tuple[float, float]], tolerance_m: float) -> list[tuple[float, float]]:
    result = []
    for point in points:
        if all(math.hypot(point[0] - existing[0], point[1] - existing[1]) > tolerance_m for existing in result):
            result.append(point)
    return result


class UnionFind:
    def __init__(self, size: int):
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        left, right = self.find(left), self.find(right)
        if left != right:
            self.parent[right] = left


def _record_id(record: dict[str, Any], fallback: str) -> str:
    for key in ("id", "building_id", "road_id", "code", "CODE", "fid"):
        if record.get(key) not in (None, ""):
            return str(record[key])
    return fallback


def _geometry(record: dict[str, Any]) -> dict[str, Any]:
    geometry = record.get("geometry")
    if isinstance(geometry, dict):
        return geometry
    if record.get("type") in {"Polygon", "MultiPolygon", "LineString", "MultiLineString"}:
        return record
    raise ValueError(f"Record {_record_id(record, 'unknown')} has no supported geometry")


def _coord_output(point_xy: tuple[float, float], digits: int = 6) -> dict[str, Any]:
    lon, lat = inv_utm29(*point_xy)
    return {
        "x": round(lon, digits),
        "y": round(lat, digits),
        "calculation_x": round(point_xy[0], 3),
        "calculation_y": round(point_xy[1], 3),
    }


def _geometry_output(geometry: dict[str, Any], crs: str) -> dict[str, Any]:
    """Preserve Polygon versus MultiPolygon in the output GeoJSON geometry."""
    kind = _geometry_type(geometry)
    coordinates = geometry.get("coordinates") or []
    def output_ring(ring):
        return [[round(inv_utm29(*_transform(point, crs))[0], 9), round(inv_utm29(*_transform(point, crs))[1], 9)] for point in ring]
    if kind == "Polygon":
        return {"type": "Polygon", "coordinates": [output_ring(ring) for ring in coordinates]}
    if kind == "MultiPolygon":
        return {"type": "MultiPolygon", "coordinates": [[output_ring(ring) for ring in polygon] for polygon in coordinates]}
    raise ValueError(f"Unsupported building geometry type {kind}")


def _region_records(partition: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    znro = partition.get("znro_regions") or []
    zpm = partition.get("zpm_regions") or []
    return znro, zpm


def _region_rings(region: dict[str, Any], partition_crs: str) -> list[list[tuple[float, float]]]:
    geometry = region.get("geometry") or {}
    if geometry.get("polygon"):
        return [[_transform(point, partition_crs) for point in geometry["polygon"]]]
    polygons = geometry.get("polygons") or []
    return [[_transform(point, partition_crs) for point in item.get("polygon", [])] for item in polygons if len(item.get("polygon", [])) >= 3]


def _region_contains(point: tuple[float, float], rings: list[list[tuple[float, float]]]) -> bool:
    """Task-2 ZPM cells are a union of separate polygons, not polygon holes."""
    return any(point_in_ring(point, ring) for ring in rings)


def _region_intersects(building_rings: list[list[tuple[float, float]]], region_rings: list[list[tuple[float, float]]]) -> bool:
    return any(polygons_intersect(building_rings, [ring]) for ring in region_rings)


def _assign_building(building_rings, zpm_regions, znro_regions, partition_crs):
    centroid = polygon_centroid(building_rings)
    candidates = []
    for region in zpm_regions:
        rings = _region_rings(region, partition_crs)
        if rings and (_region_contains(centroid, rings) or _region_intersects(building_rings, rings)):
            candidates.append(region)
    if not candidates:
        for region in znro_regions:
            rings = _region_rings(region, partition_crs)
            if rings and _region_contains(centroid, rings):
                return None, str(region.get("id")), "znro_centroid_fallback", True
        return None, None, "unassigned", True
    candidates.sort(key=lambda item: str(item.get("id")))
    selected = candidates[0]
    pm_id = str(selected.get("id"))
    znro_id = str(selected.get("parent_znro_id")) if selected.get("parent_znro_id") is not None else None
    return pm_id, znro_id, "zpm_intersection_or_centroid", len(candidates) > 1


def compute_features(
    buildings: list[dict[str, Any]],
    roads: list[dict[str, Any]],
    partition: dict[str, Any],
    *,
    building_crs: str = "EPSG:4326",
    road_crs: str = "EPSG:4326",
    partition_crs: str = "EPSG:4326",
    distance_threshold_m: float = 15.0,
    road_snap_tolerance_m: float = 2.0,
    entrance_dedup_tolerance_m: float = 1.0,
    min_group_gap_width_m: float = 0.0,
) -> dict[str, Any]:
    if distance_threshold_m < 0 or road_snap_tolerance_m < 0 or entrance_dedup_tolerance_m < 0:
        raise ValueError("distance parameters must be non-negative")
    znro_regions, zpm_regions = _region_records(partition)
    building_rows = []
    for index, record in enumerate(buildings, 1):
        source_geometry = _geometry(record)
        rings = project_rings(source_geometry, building_crs)
        if not rings:
            continue
        building_id = _record_id(record, f"BUILDING_{index:04d}")
        pm_id, znro_id, assignment_method, ambiguous = _assign_building(rings, zpm_regions, znro_regions, partition_crs)
        centroid = polygon_centroid(rings)
        location = _coord_output(centroid)
        building_rows.append({
            "id": building_id,
            "building_id": building_id,
            "pm_id": pm_id,
            "znro_id": znro_id,
            "assignment_method": assignment_method,
            "needs_review": bool(record.get("needs_review", 0)) or ambiguous,
            "source": record.get("source") or "remote_sensing",
            "area_m2": round(polygon_area(rings), 3),
            **location,
            "centroid": location,
            "geometry": _geometry_output(source_geometry, building_crs),
            "_rings": rings,
        })

    road_rows = []
    for index, record in enumerate(roads, 1):
        parts = project_lines(_geometry(record), road_crs)
        road_id = _record_id(record, f"ROAD_{index:04d}")
        for part_index, line in enumerate(parts, 1):
            if len(line) >= 2:
                road_rows.append({
                    "id": road_id,
                    "part": part_index,
                    "source": record.get("source") or "road_data",
                    "geometry_role": record.get("geometry_role") or record.get("road_role") or "centerline",
                    "line": line,
                })

    uf = UnionFind(len(building_rows))
    links = []
    for left_index, left in enumerate(building_rows):
        for right_index in range(left_index + 1, len(building_rows)):
            right = building_rows[right_index]
            if left["pm_id"] is None or left["pm_id"] != right["pm_id"]:
                continue
            distance, first, second = nearest_polygons(left["_rings"], right["_rings"])
            if distance <= distance_threshold_m:
                uf.union(left_index, right_index)
                links.append({"left": left["building_id"], "right": right["building_id"], "distance_m": round(distance, 3), "nearest_points": [_coord_output(first), _coord_output(second)]})

    components = defaultdict(list)
    for index in range(len(building_rows)):
        # A building group is defined only inside one PM/ZPM partition.
        if building_rows[index]["pm_id"] is not None:
            components[uf.find(index)].append(index)
    groups = []
    group_by_building = {}
    for number, indices in enumerate(sorted(components.values(), key=lambda item: min(building_rows[i]["building_id"] for i in item)), 1):
        first = building_rows[indices[0]]
        pm_id = first["pm_id"]
        znro_id = first["znro_id"]
        group_id = f"BG-{pm_id or 'UNASSIGNED'}-{number:03d}"
        for index in indices:
            group_by_building[building_rows[index]["building_id"]] = group_id
        points = [point for index in indices for ring in building_rows[index]["_rings"] for point in ring]
        hull = convex_hull(points)
        center = (sum(building_rows[i]["centroid"]["calculation_x"] for i in indices) / len(indices), sum(building_rows[i]["centroid"]["calculation_y"] for i in indices) / len(indices))
        location = _coord_output(center)
        groups.append({"id": group_id, "group_id": group_id, "pm_id": pm_id, "znro_id": znro_id, "building_ids": [building_rows[i]["building_id"] for i in indices], "building_count": len(indices), **location, "centroid": location, "hull": [_coord_output(point) for point in hull], "max_link_distance_m": round(max((item["distance_m"] for item in links if item["left"] in {building_rows[i]["building_id"] for i in indices} and item["right"] in {building_rows[i]["building_id"] for i in indices}), default=0.0), 3)})
    for row in building_rows:
        row["group_id"] = group_by_building.get(row["building_id"])

    group_entrances = []
    for group in groups:
        candidates = []
        member_ids = set(group["building_ids"])
        for link in links:
            if link["left"] not in member_ids or link["right"] not in member_ids:
                continue
            first = link["nearest_points"][0]
            second = link["nearest_points"][1]
            p1 = (first["calculation_x"], first["calculation_y"])
            p2 = (second["calculation_x"], second["calculation_y"])
            # Touching/overlapping outlines do not form a traversable inter-building gap.
            if link["distance_m"] <= max(min_group_gap_width_m, 0.01):
                continue
            candidates.append((p1, p2, link))
        selected_points = []
        for p1, p2, link in candidates:
            midpoint = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
            if any(math.hypot(midpoint[0] - old[0], midpoint[1] - old[1]) <= entrance_dedup_tolerance_m for old in selected_points):
                continue
            selected_points.append(midpoint)
            nearest_road = None
            for road in road_rows:
                for first, second in zip(road["line"], road["line"][1:]):
                    projected = project_point_to_segment(midpoint, first, second)
                    distance = math.hypot(midpoint[0] - projected[0], midpoint[1] - projected[1])
                    if nearest_road is None or distance < nearest_road[0]:
                        nearest_road = (distance, road["id"], projected)
            location = _coord_output(midpoint)
            group_entrances.append({"id": f"BGE-{len(group_entrances) + 1:04d}", "entrance_type": "building_group", "group_id": group["group_id"], "pm_id": group["pm_id"], "znro_id": group["znro_id"], "from_building_id": link["left"], "to_building_id": link["right"], **location, "coordinates": location, "gap_width_m": link["distance_m"], "nearest_road_id": nearest_road[1] if nearest_road else None, "road_distance_m": round(nearest_road[0], 3) if nearest_road else None, "accessible": bool(nearest_road and nearest_road[0] <= road_snap_tolerance_m), "method": "nearest_outline_midpoint", "needs_review": True})
        group["entrance_ids"] = [entry["id"] for entry in group_entrances if entry["group_id"] == group["group_id"]]
        group["representative_entrance_id"] = min(group["entrance_ids"], key=lambda entry_id: next((entry["road_distance_m"] if entry["road_distance_m"] is not None else float("inf") for entry in group_entrances if entry["id"] == entry_id), float("inf")), default=None)

    building_entrances = []
    for building in building_rows:
        candidates = []
        for road in road_rows:
            for building_a, building_b in iter_edges(building["_rings"]):
                for road_a, road_b in zip(road["line"], road["line"][1:]):
                    for point in segment_intersection(building_a, building_b, road_a, road_b):
                        candidates.append((point, "exact_boundary_intersection", 0.0, road["id"]))
        if not candidates:
            for road in road_rows:
                distance, building_point, road_point = nearest_polygon_to_line(building["_rings"], road["line"])
                if distance <= road_snap_tolerance_m:
                    candidates.append((building_point, "nearest_boundary_snap", distance, road["id"]))
        for point, method, distance, road_id in candidates:
            if any(item["road_id"] == road_id and math.hypot(point[0] - item["coordinates"]["calculation_x"], point[1] - item["coordinates"]["calculation_y"]) <= entrance_dedup_tolerance_m for item in building_entrances if item["building_id"] == building["building_id"]):
                continue
            exact = method == "exact_boundary_intersection"
            location = _coord_output(point)
            building_entrances.append({"id": f"BE-{len(building_entrances) + 1:04d}", "entrance_type": "building", "building_id": building["building_id"], "group_id": building["group_id"], "pm_id": building["pm_id"], "znro_id": building["znro_id"], "road_id": road_id, **location, "coordinates": location, "distance_m": round(distance, 3), "method": method, "eligible": exact, "needs_review": bool(building["needs_review"]) or not exact})

    community_entrances = []
    for region in znro_regions:
        region_id = str(region.get("id"))
        rings = _region_rings(region, partition_crs)
        if not rings:
            continue
        for road in road_rows:
            points = []
            for region_a, region_b in iter_edges(rings):
                for road_a, road_b in zip(road["line"], road["line"][1:]):
                    points.extend(segment_intersection(region_a, region_b, road_a, road_b))
            for point in dedupe_points(points, entrance_dedup_tolerance_m):
                location = _coord_output(point)
                community_entrances.append({"id": f"CE-{len(community_entrances) + 1:04d}", "entrance_type": "community", "community_id": region_id, "znro_id": region_id, "road_id": road["id"], **location, "coordinates": location, "method": "znro_boundary_road_intersection", "eligible": True, "needs_review": True})

    for row in building_rows:
        row.pop("_rings", None)
    for group in groups:
        group["hull"] = [{key: value for key, value in point.items() if key in {"x", "y"}} for point in group["hull"]]

    unresolved = []
    for row in building_rows:
        if row["pm_id"] is None:
            unresolved.append({"type": "building", "id": row["building_id"], "reason": "no_zpm_assignment"})
        if not any(entry["building_id"] == row["building_id"] and entry["eligible"] for entry in building_entrances):
            unresolved.append({"type": "building", "id": row["building_id"], "reason": "no_exact_building_road_intersection"})
    for region in znro_regions:
        if not any(entry["znro_id"] == str(region.get("id")) for entry in community_entrances):
            unresolved.append({"type": "community", "id": str(region.get("id")), "reason": "no_znro_road_intersection"})

    return {
        "schema_version": "task2-geometry-features-1.0",
        "meta": {"calculation_crs": "EPSG:32629", "output_crs": "EPSG:4326", "source_semantics": "remote-sensing building candidates plus supplied road network"},
        "parameters": {"building_group_distance_m": distance_threshold_m, "road_snap_tolerance_m": road_snap_tolerance_m, "entrance_dedup_tolerance_m": entrance_dedup_tolerance_m, "min_group_gap_width_m": min_group_gap_width_m},
        "summary": {"building_count": len(building_rows), "building_group_count": len(groups), "building_group_entrance_count": len(group_entrances), "building_entrance_count": len(building_entrances), "community_entrance_count": len(community_entrances), "exact_building_entrance_count": sum(item["eligible"] for item in building_entrances), "unresolved_count": len(unresolved)},
        "buildings": building_rows,
        "building_groups": groups,
        "entrances": [*building_entrances, *group_entrances, *community_entrances],
        "building_entrances": building_entrances,
        "building_group_entrances": group_entrances,
        "community_entrances": community_entrances,
        "links": links,
        "validation": {"all_pm_assigned_buildings_have_one_group": all(row["pm_id"] is None or row["group_id"] is not None for row in building_rows), "all_group_links_within_threshold": all(item["distance_m"] <= distance_threshold_m + 1e-6 for item in links), "entrance_ids_unique": len({item["id"] for item in [*building_entrances, *group_entrances, *community_entrances]}) == len([*building_entrances, *group_entrances, *community_entrances]), "unresolved": unresolved},
    }


def write_task6_views(result: dict[str, Any], output_dir: Path) -> None:
    """Write list-only JSON files accepted directly by task 6's remote sources."""
    output_dir.mkdir(parents=True, exist_ok=True)
    views = {
        "task2_buildings.json": result["buildings"],
        "task2_building_groups.json": result["building_groups"],
        "task2_entrances.json": result["entrances"],
    }
    for name, records in views.items():
        (output_dir / name).write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def visualize_features(
    result: dict[str, Any],
    roads: list[dict[str, Any]],
    partition: dict[str, Any],
    output_path: Path,
    *,
    road_crs: str = "EPSG:4326",
    partition_crs: str = "EPSG:4326",
) -> None:
    """Render a compact review map for the geometry feature calculation."""
    fig, ax = plt.subplots(figsize=(14, 10), dpi=180)
    ax.set_facecolor("#f8fafc")
    palette = plt.get_cmap("tab20")
    groups = result.get("building_groups") or []
    color_by_group = {group["group_id"]: palette(index % 20) for index, group in enumerate(groups)}

    znro_regions, zpm_regions = _region_records(partition)
    for region in znro_regions:
        for ring in _region_rings(region, partition_crs):
            ax.add_patch(PlotPolygon(ring, closed=True, fill=False, edgecolor="#b91c1c", linewidth=1.8, zorder=2))
    for region in zpm_regions:
        for ring in _region_rings(region, partition_crs):
            ax.add_patch(PlotPolygon(ring, closed=True, fill=False, edgecolor="#1d4ed8", linestyle="--", linewidth=0.9, alpha=0.9, zorder=2))

    for road in roads:
        for line in project_lines(_geometry(road), road_crs):
            xs, ys = zip(*line)
            ax.plot(xs, ys, color="#64748b", linewidth=1.1, alpha=0.9, zorder=1)

    for building in result.get("buildings") or []:
        rings = project_rings(building["geometry"], "EPSG:4326")
        color = color_by_group.get(building.get("group_id"), "#d1d5db")
        for ring in rings:
            ax.add_patch(PlotPolygon(ring, closed=True, facecolor=color, edgecolor="#475569", linewidth=0.35, alpha=0.66, zorder=3))

    for group in groups:
        hull = [_transform(point, "EPSG:4326") for point in group.get("hull") or []]
        if len(hull) >= 3:
            ax.add_patch(PlotPolygon(hull, closed=True, facecolor=color_by_group[group["group_id"]], edgecolor=color_by_group[group["group_id"]], linewidth=1.2, alpha=0.12, zorder=2.5))
        ax.scatter(group["calculation_x"], group["calculation_y"], marker="o", s=26, facecolor="#ffffff", edgecolor="#111827", linewidth=0.8, zorder=7)
        ax.annotate(group["group_id"], (group["calculation_x"], group["calculation_y"]), xytext=(4, 4), textcoords="offset points", fontsize=6.5, color="#111827", zorder=8)

    entrances = result.get("entrances") or []
    exact = [item for item in entrances if item.get("entrance_type") == "building" and item.get("eligible")]
    inferred = [item for item in entrances if item.get("entrance_type") == "building" and not item.get("eligible")]
    group_entries = [item for item in entrances if item.get("entrance_type") == "building_group"]
    community_entries = [item for item in entrances if item.get("entrance_type") == "community"]
    for items, marker, color, size in ((exact, "o", "#15803d", 24), (inferred, "^", "#d97706", 34), (group_entries, "D", "#c026d3", 32), (community_entries, "s", "#0369a1", 30)):
        if items:
            ax.scatter([item["calculation_x"] for item in items], [item["calculation_y"] for item in items], marker=marker, s=size, c=color, edgecolor="#ffffff", linewidth=0.55, zorder=9)

    handles = [
        Line2D([0], [0], color="#64748b", linewidth=1.2, label="Road"),
        Line2D([0], [0], color="#b91c1c", linewidth=1.8, label="ZNRO boundary"),
        Line2D([0], [0], color="#1d4ed8", linestyle="--", linewidth=1.0, label="ZPM boundary"),
        Patch(facecolor="#d1d5db", edgecolor="#475569", label="Unassigned building"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#15803d", markeredgecolor="#ffffff", markersize=6, label="Exact building entrance"),
        Line2D([0], [0], marker="^", color="w", markerfacecolor="#d97706", markeredgecolor="#ffffff", markersize=6, label="Inferred building candidate"),
        Line2D([0], [0], marker="D", color="w", markerfacecolor="#c026d3", markeredgecolor="#ffffff", markersize=6, label="Building-group entrance"),
        Line2D([0], [0], marker="s", color="w", markerfacecolor="#0369a1", markeredgecolor="#ffffff", markersize=6, label="Community entrance"),
    ]
    ax.legend(handles=handles, loc="upper left", fontsize=7, frameon=True, framealpha=0.94, edgecolor="#cbd5e1")
    ax.set_aspect("equal", adjustable="box")
    ax.set_title("Task 2 Remote-Sensing Geometry Features", fontsize=13, pad=10)
    ax.set_xlabel("EPSG:32629 easting (m)")
    ax.set_ylabel("EPSG:32629 northing (m)")
    ax.grid(color="#cbd5e1", linewidth=0.35, alpha=0.55)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def run_from_files(buildings_path: Path, roads_path: Path, partition_path: Path, **options: Any) -> dict[str, Any]:
    buildings = load_features(buildings_path, options.pop("building_layer", "GEO_BUILDING"))
    roads = load_features(roads_path, options.pop("road_layer", "GEO_ROAD"))
    partition = json.loads(partition_path.read_text(encoding="utf-8-sig"))
    result = compute_features(buildings, roads, partition, **options)
    result["meta"].update({"source_buildings": str(buildings_path), "source_roads": str(roads_path), "source_partition": str(partition_path)})
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute task-2 remote-sensing building groups and entrance features.")
    parser.add_argument("--buildings", type=Path, required=True, help="Building GeoJSON/JSON or GeoPackage path")
    parser.add_argument("--roads", type=Path, required=True, help="Road GeoJSON/JSON or GeoPackage path")
    parser.add_argument("--partition-json", type=Path, required=True, help="Task-2 qgis_direct_region_partition_result.json")
    parser.add_argument("--output-json", type=Path, default=TASK2_ZONE_PARTITION_OUTPUT_DIR / "task2_geometry_features.json")
    parser.add_argument("--output-fig", type=Path, default=None, help="Optional review PNG output")
    parser.add_argument("--task6-output-dir", type=Path, default=None, help="Optional directory for task-6 list-only JSON views")
    parser.add_argument("--building-layer", default="GEO_BUILDING")
    parser.add_argument("--road-layer", default="GEO_ROAD")
    parser.add_argument("--building-crs", default="EPSG:4326")
    parser.add_argument("--road-crs", default="EPSG:4326")
    parser.add_argument("--partition-crs", default="EPSG:4326")
    parser.add_argument("--distance-threshold-m", type=float, default=15.0)
    parser.add_argument("--road-snap-tolerance-m", type=float, default=2.0)
    parser.add_argument("--entrance-dedup-tolerance-m", type=float, default=1.0)
    parser.add_argument("--min-group-gap-width-m", type=float, default=0.0)
    args = parser.parse_args()
    if min(args.distance_threshold_m, args.road_snap_tolerance_m, args.entrance_dedup_tolerance_m, args.min_group_gap_width_m) < 0:
        parser.error("distance parameters must be non-negative")
    return args


def main() -> None:
    args = parse_args()
    result = run_from_files(args.buildings, args.roads, args.partition_json, building_layer=args.building_layer, road_layer=args.road_layer, building_crs=args.building_crs, road_crs=args.road_crs, partition_crs=args.partition_crs, distance_threshold_m=args.distance_threshold_m, road_snap_tolerance_m=args.road_snap_tolerance_m, entrance_dedup_tolerance_m=args.entrance_dedup_tolerance_m, min_group_gap_width_m=args.min_group_gap_width_m)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.task6_output_dir is not None:
        write_task6_views(result, args.task6_output_dir)
    if args.output_fig is not None:
        roads = load_features(args.roads, args.road_layer)
        partition = json.loads(args.partition_json.read_text(encoding="utf-8-sig"))
        visualize_features(result, roads, partition, args.output_fig, road_crs=args.road_crs, partition_crs=args.partition_crs)
    print(f"Buildings: {result['summary']['building_count']}; groups: {result['summary']['building_group_count']}; entrances: {len(result['entrances'])}")
    print(f"Result JSON: {args.output_json.resolve()}")
    if args.output_fig is not None:
        print(f"Review image: {args.output_fig.resolve()}")


if __name__ == "__main__":
    main()
