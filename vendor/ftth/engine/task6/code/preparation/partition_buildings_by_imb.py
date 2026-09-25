"""Partition recognized building-group polygons by IMB seeds.

The recognition ``GEO_BUILDING`` layer is treated as a set of containing
building-group polygons.  IMB records provide the building seeds.  For every
group, a Voronoi partition is clipped to the group polygon, so cell interiors
are disjoint and every cell is contained by its parent group.

The implementation deliberately has no GIS dependency.  It accepts GeoJSON,
the project's task-2 ``nodes[]`` JSON, and GeoPackage inputs containing a
named layer.  Calculations use EPSG:32629 when input coordinates are WGS84.
It also writes a review overview with group boundaries, partition cells, IMB
seeds, and unmatched IMBs.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import struct
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


EPS = 1e-9
EARTH_A = 6378137.0
EARTH_E2 = 0.0066943799901413165
UTM_K0 = 0.9996
UTM_CENTRAL_MERIDIAN = math.radians(-9.0)


def utm29(lon: float, lat: float) -> tuple[float, float]:
    """Convert WGS84 longitude/latitude to EPSG:32629 metres."""
    lon_r, lat_r = math.radians(lon), math.radians(lat)
    ep2 = EARTH_E2 / (1.0 - EARTH_E2)
    n = EARTH_A / math.sqrt(1.0 - EARTH_E2 * math.sin(lat_r) ** 2)
    t = math.tan(lat_r) ** 2
    c = ep2 * math.cos(lat_r) ** 2
    a = math.cos(lat_r) * (lon_r - UTM_CENTRAL_MERIDIAN)
    m = EARTH_A * ((1 - EARTH_E2 / 4 - 3 * EARTH_E2**2 / 64 - 5 * EARTH_E2**3 / 256) * lat_r
                   - (3 * EARTH_E2 / 8 + 3 * EARTH_E2**2 / 32 + 45 * EARTH_E2**3 / 1024) * math.sin(2 * lat_r)
                   + (15 * EARTH_E2**2 / 256 + 45 * EARTH_E2**3 / 1024) * math.sin(4 * lat_r)
                   - (35 * EARTH_E2**3 / 3072) * math.sin(6 * lat_r))
    x = UTM_K0 * n * (a + (1 - t + c) * a**3 / 6
                      + (5 - 18 * t + t**2 + 72 * c - 58 * ep2) * a**5 / 120) + 500000.0
    y = UTM_K0 * (m + n * math.tan(lat_r) * (a**2 / 2
                      + (5 - t + 9 * c + 4 * c**2) * a**4 / 24
                      + (61 - 58 * t + t**2 + 600 * c - 330 * ep2) * a**6 / 720))
    return x, y


def inv_utm29(x: float, y: float) -> tuple[float, float]:
    """Convert EPSG:32629 metres to WGS84 longitude/latitude."""
    ep2 = EARTH_E2 / (1.0 - EARTH_E2)
    m = y / UTM_K0
    mu = m / (EARTH_A * (1 - EARTH_E2 / 4 - 3 * EARTH_E2**2 / 64 - 5 * EARTH_E2**3 / 256))
    e1 = (1 - math.sqrt(1 - EARTH_E2)) / (1 + math.sqrt(1 - EARTH_E2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
            + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
            + (151 * e1**3 / 96) * math.sin(6 * mu)
            + (1097 * e1**4 / 512) * math.sin(8 * mu))
    n1 = EARTH_A / math.sqrt(1 - EARTH_E2 * math.sin(phi1) ** 2)
    t1 = math.tan(phi1) ** 2
    c1 = ep2 * math.cos(phi1) ** 2
    r1 = EARTH_A * (1 - EARTH_E2) / (1 - EARTH_E2 * math.sin(phi1) ** 2) ** 1.5
    d = (x - 500000.0) / (n1 * UTM_K0)
    lat = phi1 - (n1 * math.tan(phi1) / r1) * (d**2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * ep2) * d**4 / 24 + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * ep2 - 3 * c1**2) * d**6 / 720)
    lon = UTM_CENTRAL_MERIDIAN + (d - (1 + 2 * t1 + c1) * d**3 / 6 + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * ep2 + 24 * t1**2) * d**5 / 120) / math.cos(phi1)
    return math.degrees(lon), math.degrees(lat)


def _wkb_reader(blob: bytes) -> bytes:
    raw = bytes(blob)
    return raw[8:] if raw[:2] == b"GP" else raw


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
        points = [list(struct.unpack_from(endian + "dd", raw, offset + i * 16)) for i in range(count)]
        return {"type": "LineString", "coordinates": points}, offset + count * 16
    if base_type == 3:
        ring_count = struct.unpack_from(endian + "I", raw, offset)[0]
        offset += 4
        rings = []
        for _ in range(ring_count):
            count = struct.unpack_from(endian + "I", raw, offset)[0]
            offset += 4
            rings.append([list(struct.unpack_from(endian + "dd", raw, offset + i * 16)) for i in range(count)])
            offset += count * 16
        return {"type": "Polygon", "coordinates": rings}, offset
    if base_type in {5, 6}:
        count = struct.unpack_from(endian + "I", raw, offset)[0]
        offset += 4
        values = []
        for _ in range(count):
            geometry, offset = _read_wkb(raw, offset)
            values.append(geometry["coordinates"])
        return ({"type": "MultiLineString" if base_type == 5 else "MultiPolygon", "coordinates": values}, offset)
    raise ValueError(f"Unsupported WKB geometry type {base_type}")


def _geometry_from_blob(blob: bytes) -> dict[str, Any]:
    return _read_wkb(_wkb_reader(blob))[0]


def _load_gpkg(path: Path, layer: str) -> list[dict[str, Any]]:
    with sqlite3.connect(str(path)) as connection:
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(f'SELECT * FROM "{layer}" ORDER BY fid').fetchall()
        except sqlite3.Error as exc:
            raise ValueError(f"Layer {layer!r} is not available in {path}") from exc
    result = []
    for row in rows:
        item = {key: row[key] for key in row.keys() if key not in {"geom"}}
        if row["geom"]:
            item["geometry"] = _geometry_from_blob(row["geom"])
        result.append(item)
    return result


def _load_source(path: Path, layer: str | None = None, *, only_imb_nodes: bool = False) -> list[dict[str, Any]]:
    if path.suffix.lower() in {".gpkg", ".sqlite", ".db"}:
        if not layer:
            raise ValueError(f"A layer name is required for GeoPackage input: {path}")
        return _load_gpkg(path, layer)
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, dict) and payload.get("type") == "FeatureCollection":
        return [{**dict(feature.get("properties") or {}), "geometry": feature.get("geometry")} for feature in payload.get("features") or []]
    if isinstance(payload, dict) and isinstance(payload.get("features"), list):
        return [{**dict(feature.get("properties") or {}), "geometry": feature.get("geometry")} for feature in payload["features"]]
    if isinstance(payload, dict) and isinstance(payload.get("nodes"), list):
        nodes = [dict(item) for item in payload["nodes"]]
        if only_imb_nodes:
            return [item for item in nodes if str(item.get("type") or "").upper() == "IMB"]
        recognition = payload.get("recognition")
        if isinstance(recognition, dict):
            groups = recognition.get("building_groups", recognition.get("buildings"))
            if isinstance(groups, list):
                return [dict(item) for item in groups]
        return nodes
    if isinstance(payload, dict) and isinstance(payload.get("buildings"), list):
        return [dict(item) for item in payload["buildings"]]
    if isinstance(payload, list):
        return [dict(item) for item in payload]
    raise ValueError(f"Unsupported structured input: {path}")


def _point(value: Any) -> tuple[float, float]:
    if isinstance(value, dict):
        return float(value.get("x", value.get("longitude", value.get("X")))), float(value.get("y", value.get("latitude", value.get("Y"))))
    return float(value[0]), float(value[1])


def _rings(geometry: dict[str, Any] | None) -> list[list[tuple[float, float]]]:
    geometry = geometry or {}
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if kind == "Polygon":
        # Holes are intentionally excluded: the recognition group contract
        # uses simple containing footprints.  They cannot be represented by a
        # clipped cell without a polygon-difference operation.
        return [[_point(point) for point in coordinates[0]]] if coordinates and len(coordinates[0]) >= 3 else []
    if kind == "MultiPolygon":
        return [[_point(point) for point in polygon[0]] for polygon in coordinates if polygon and len(polygon[0]) >= 3]
    return []


def _group_parts(geometry: dict[str, Any] | None) -> list[dict[str, list[list[tuple[float, float]]]]]:
    """Return parent polygon parts while preserving their interior holes."""
    geometry = geometry or {}
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    source_polygons = [coordinates] if kind == "Polygon" else coordinates if kind == "MultiPolygon" else []
    parts = []
    for polygon in source_polygons:
        if not polygon or len(polygon[0]) < 3:
            continue
        parts.append({
            "outer": [_point(point) for point in polygon[0]],
            "holes": [[_point(point) for point in ring] for ring in polygon[1:] if len(ring) >= 3],
        })
    return parts


def _geometry_of(record: dict[str, Any]) -> dict[str, Any] | None:
    geometry = record.get("geometry")
    if isinstance(geometry, dict):
        return geometry
    return None


def _seed(record: dict[str, Any]) -> tuple[float, float]:
    for x_key, y_key in (("x", "y"), ("X", "Y"), ("longitude", "latitude")):
        if record.get(x_key) not in (None, "") and record.get(y_key) not in (None, ""):
            return float(record[x_key]), float(record[y_key])
    geometry = _geometry_of(record)
    if geometry and geometry.get("type") == "Point":
        return _point(geometry["coordinates"])
    rings = _rings(geometry)
    if rings:
        points = [point for ring in rings[:1] for point in ring]
        return (sum(point[0] for point in points) / len(points), sum(point[1] for point in points) / len(points))
    return _point(record)


def _id(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        if record.get(key) not in (None, ""):
            return str(record[key]).strip()
    raise ValueError(f"Record is missing an id field: {keys}")


def _group_id(record: dict[str, Any]) -> str:
    return _id(record, "group_id", "building_group_id", "parent_group_id", "id", "code", "CODE", "building_id")


def _area(ring: list[tuple[float, float]]) -> float:
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, [*ring[1:], ring[0]]))) / 2.0


def _inside(point: tuple[float, float], ring: list[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    for first, second in zip(ring, [*ring[1:], ring[0]]):
        cross = (second[0] - first[0]) * (y - first[1]) - (second[1] - first[1]) * (x - first[0])
        if abs(cross) <= 1e-8 and min(first[0], second[0]) - 1e-8 <= x <= max(first[0], second[0]) + 1e-8 and min(first[1], second[1]) - 1e-8 <= y <= max(first[1], second[1]) + 1e-8:
            return True
        if (first[1] > y) != (second[1] > y):
            at_x = (second[0] - first[0]) * (y - first[1]) / (second[1] - first[1]) + first[0]
            if x <= at_x:
                inside = not inside
    return inside


def _clip_half_plane(polygon: list[tuple[float, float]], first: tuple[float, float], second: tuple[float, float]) -> list[tuple[float, float]]:
    """Keep points closer to ``first`` than ``second``."""
    if not polygon:
        return []
    # 2*(second-first).x*x + 2*(second-first).y*y <= |second|^2-|first|^2
    dx, dy = second[0] - first[0], second[1] - first[1]
    limit = second[0] ** 2 + second[1] ** 2 - first[0] ** 2 - first[1] ** 2

    def value(point: tuple[float, float]) -> float:
        return 2.0 * (dx * point[0] + dy * point[1]) - limit

    output: list[tuple[float, float]] = []
    previous = polygon[-1]
    previous_value = value(previous)
    previous_inside = previous_value <= EPS
    for current in polygon:
        current_value = value(current)
        current_inside = current_value <= EPS
        if current_inside != previous_inside:
            denominator = previous_value - current_value
            ratio = previous_value / denominator if abs(denominator) > EPS else 0.0
            output.append((previous[0] + (current[0] - previous[0]) * ratio, previous[1] + (current[1] - previous[1]) * ratio))
        if current_inside:
            output.append(current)
        previous, previous_value, previous_inside = current, current_value, current_inside
    return output


def _dedupe_ring(ring: list[tuple[float, float]]) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for point in ring:
        if not result or math.hypot(point[0] - result[-1][0], point[1] - result[-1][1]) > 1e-7:
            result.append(point)
    if len(result) > 1 and math.hypot(result[0][0] - result[-1][0], result[0][1] - result[-1][1]) <= 1e-7:
        result.pop()
    return result if len(result) >= 3 and _area(result) > 1e-8 else []


def _segments_cross_interiors(first: tuple[float, float], second: tuple[float, float], third: tuple[float, float], fourth: tuple[float, float]) -> bool:
    def orientation(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    values = (orientation(first, second, third), orientation(first, second, fourth), orientation(third, fourth, first), orientation(third, fourth, second))
    return values[0] * values[1] < -EPS and values[2] * values[3] < -EPS


def _point_on_ring(point: tuple[float, float], ring: list[tuple[float, float]]) -> bool:
    for first, second in zip(ring, [*ring[1:], ring[0]]):
        cross = (second[0] - first[0]) * (point[1] - first[1]) - (second[1] - first[1]) * (point[0] - first[0])
        if abs(cross) <= EPS and min(first[0], second[0]) - EPS <= point[0] <= max(first[0], second[0]) + EPS and min(first[1], second[1]) - EPS <= point[1] <= max(first[1], second[1]) + EPS:
            return True
    return False


def _nearest_ring_boundary(point: tuple[float, float], ring: list[tuple[float, float]]) -> tuple[float, tuple[float, float]]:
    best = (float("inf"), ring[0])
    for first, second in zip(ring, [*ring[1:], ring[0]]):
        dx, dy = second[0] - first[0], second[1] - first[1]
        length2 = dx * dx + dy * dy
        ratio = 0.0 if length2 <= EPS else max(0.0, min(1.0, ((point[0] - first[0]) * dx + (point[1] - first[1]) * dy) / length2))
        candidate = (first[0] + ratio * dx, first[1] + ratio * dy)
        distance = math.hypot(point[0] - candidate[0], point[1] - candidate[1])
        if distance < best[0]:
            best = (distance, candidate)
    return best


def _strictly_inside(point: tuple[float, float], ring: list[tuple[float, float]]) -> bool:
    return not _point_on_ring(point, ring) and _inside(point, ring)


def _group_polygons_overlap(first: list[list[tuple[float, float]]], second: list[list[tuple[float, float]]]) -> bool:
    for left in first:
        for right in second:
            for index, point in enumerate(left):
                if _strictly_inside(point, right):
                    return True
                for j, other in enumerate(right):
                    if _segments_cross_interiors(point, left[(index + 1) % len(left)], other, right[(j + 1) % len(right)]):
                        return True
            if right and _strictly_inside(right[0], left):
                return True
    return False


def _project(point: tuple[float, float], crs: str) -> tuple[float, float]:
    normalized = str(crs).upper().replace(" ", "")
    return utm29(*point) if normalized in {"EPSG:4326", "4326", "WGS84"} else point


def _unproject(point: tuple[float, float], crs: str) -> tuple[float, float]:
    normalized = str(crs).upper().replace(" ", "")
    return inv_utm29(*point) if normalized in {"EPSG:4326", "4326", "WGS84"} else point


def _output_geometry(parts: list[dict[str, list[list[tuple[float, float]]]]]) -> dict[str, Any] | None:
    converted = []
    for part in parts:
        outer = [[round(point[0], 9), round(point[1], 9)] for point in part["outer"]]
        if len(outer) < 3:
            continue
        rings = [outer + [outer[0]]]
        for hole in part["holes"]:
            converted_hole = [[round(point[0], 9), round(point[1], 9)] for point in hole]
            if len(converted_hole) >= 3:
                rings.append(converted_hole + [converted_hole[0]])
        converted.append(rings)
    if not converted:
        return None
    if len(converted) == 1:
        return {"type": "Polygon", "coordinates": converted[0]}
    return {"type": "MultiPolygon", "coordinates": converted}


def partition(
    groups: list[dict[str, Any]],
    imbs: list[dict[str, Any]],
    *,
    crs: str = "EPSG:4326",
    allow_nearest_group: bool = False,
    assignment_snap_tolerance_m: float = 0.0,
) -> dict[str, Any]:
    if not groups:
        raise ValueError("No building-group records were found")
    if not imbs:
        raise ValueError("No IMB records were found")
    group_data = []
    for index, group in enumerate(groups, 1):
        group_geometry = _geometry_of(group)
        parts = _group_parts(group_geometry)
        if not parts:
            raise ValueError(f"Building group {_group_id(group)} has no polygon geometry")
        projected_parts = [{"outer": [_project(point, crs) for point in part["outer"]], "holes": [[_project(point, crs) for point in hole] for hole in part["holes"]]} for part in parts]
        group_data.append({"record": group, "id": _group_id(group), "parts": projected_parts, "rings": [part["outer"] for part in projected_parts]})
    for index, group in enumerate(group_data):
        for other in group_data[index + 1 :]:
            if _group_polygons_overlap(group["rings"], other["rings"]):
                raise ValueError(f"Building groups {group['id']} and {other['id']} overlap; resolve parent group geometry before partitioning")

    seeds = []
    for index, imb in enumerate(imbs, 1):
        imb_id = _id(imb, "imb_id", "code", "CODE", "id")
        point = _project(_seed(imb), crs)
        declared = str(imb.get("group_id") or imb.get("building_group_id") or imb.get("parent_group_id") or "").strip()
        eligible_groups = [item for item in group_data if item["id"] == declared] if declared else group_data
        matches = [(item, component_index) for item in eligible_groups for component_index, part in enumerate(item["parts"], 1) if _inside(point, part["outer"]) and not any(_inside(point, hole) for hole in part["holes"])]
        assignment_method = "declared_group" if declared and matches else "spatial_contains"
        snap_distance_m = None
        if not matches and allow_nearest_group and eligible_groups:
            matches = [min(
                ((item, component_index) for item in eligible_groups for component_index, ring in enumerate(item["rings"], 1)),
                key=lambda candidate: min(math.hypot(point[0] - vertex[0], point[1] - vertex[1]) for vertex in candidate[0]["rings"][candidate[1] - 1]),
            )]
            assignment_method = "nearest_group_review_required"
        if not matches and assignment_snap_tolerance_m > 0 and eligible_groups:
            snap_candidates = []
            for item in eligible_groups:
                for component_index, part in enumerate(item["parts"], 1):
                    distance, snapped_point = _nearest_ring_boundary(point, part["outer"])
                    snap_candidates.append((distance, item, component_index, snapped_point))
            snap_distance_m, selected_group, component_index, snapped_point = min(snap_candidates, key=lambda item: item[0])
            if snap_distance_m <= assignment_snap_tolerance_m + EPS:
                matches = [(selected_group, component_index)]
                point = snapped_point
                assignment_method = "nearest_boundary_snap_review"
            else:
                snap_distance_m = None
        if not matches:
            seeds.append({"id": imb_id, "record": imb, "point": point, "group": None})
            continue
        selected_group, component_index = matches[0]
        seeds.append({"id": imb_id, "record": imb, "point": point, "group": selected_group, "component_index": component_index, "assignment_method": assignment_method, "spatial_snap_distance_m": snap_distance_m})

    by_group: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    unresolved = []
    for seed in seeds:
        if seed["group"] is None:
            unresolved.append({"imb_id": seed["id"], "reason": "no_containing_building_group"})
        else:
            by_group[(seed["group"]["id"], int(seed["component_index"]))].append(seed)

    features = []
    group_reports = []
    for group in group_data:
        for component_index, part in enumerate(group["parts"], 1):
            ring = part["outer"]
            group_seeds = sorted(by_group.get((group["id"], component_index), []), key=lambda item: item["id"])
            if not group_seeds:
                group_reports.append({"group_id": group["id"], "component_index": component_index, "imb_count": 0, "status": "NO_IMB"})
                continue
            cells: dict[str, dict[str, list[list[tuple[float, float]]]]] = {}
            seen_points: dict[tuple[int, int], str] = {}
            for seed in group_seeds:
                key = (round(seed["point"][0] * 1000), round(seed["point"][1] * 1000))
                if key in seen_points:
                    raise ValueError(f"IMB seeds {seen_points[key]} and {seed['id']} share a location in group {group['id']}; Voronoi cells would overlap")
                seen_points[key] = seed["id"]
            for seed in group_seeds:
                cell = ring[:]
                for other in group_seeds:
                    if other["id"] != seed["id"]:
                        cell = _clip_half_plane(cell, seed["point"], other["point"])
                        if not cell:
                            break
                cell = _dedupe_ring(cell)
                if cell:
                    holes = []
                    for hole in part["holes"]:
                        hole_cell = hole[:]
                        for other in group_seeds:
                            if other["id"] != seed["id"]:
                                hole_cell = _clip_half_plane(hole_cell, seed["point"], other["point"])
                                if not hole_cell:
                                    break
                        hole_cell = _dedupe_ring(hole_cell)
                        if hole_cell:
                            holes.append(hole_cell)
                    cells[seed["id"]] = {"outer": cell, "holes": holes}
            for seed in group_seeds:
                cell = cells.get(seed["id"])
                if not cell:
                    unresolved.append({"imb_id": seed["id"], "reason": "empty_partition_cell"})
                    continue
                part_ll = {
                    "outer": [_unproject(point, crs) for point in cell["outer"]],
                    "holes": [[_unproject(point, crs) for point in hole] for hole in cell["holes"]],
                }
                geometry = _output_geometry([part_ll])
                properties = {key: value for key, value in seed["record"].items() if key != "geometry"}
                partition_area_m2 = _area(cell["outer"]) - sum(_area(hole) for hole in cell["holes"])
                partition_seed = _unproject(seed["point"], crs)
                properties.update({"id": seed["id"], "imb_id": seed["id"], "group_id": group["id"], "parent_building_group_id": group["id"], "parent_component_index": component_index, "assignment_method": seed["assignment_method"], "spatial_snap_distance_m": round(seed["spatial_snap_distance_m"], 3) if seed["spatial_snap_distance_m"] is not None else None, "partition_seed_x": round(partition_seed[0], 9), "partition_seed_y": round(partition_seed[1], 9), "partition_method": "voronoi_clipped_to_group", "partition_area_m2": round(partition_area_m2, 3)})
                features.append({"type": "Feature", "properties": properties, "geometry": geometry})
            group_reports.append({"group_id": group["id"], "component_index": component_index, "imb_count": len(group_seeds), "partitioned_count": len(cells), "status": "PASS" if len(cells) == len(group_seeds) else "REVIEW_REQUIRED"})

    output = {
        "schema_version": "building-partition-by-imb.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "crs": crs,
        "algorithm": "voronoi_cells_clipped_to_building_group",
        "assignment_snap_tolerance_m": assignment_snap_tolerance_m,
        "guarantees": {"within_parent_group": True, "interior_disjointness": True, "boundary_touching_allowed": True},
        "summary": {"group_count": len(group_data), "imb_count": len(imbs), "partitioned_count": len(features), "snapped_count": sum(feature["properties"].get("assignment_method") == "nearest_boundary_snap_review" for feature in features), "unresolved_count": len(unresolved)},
        "snapped_imb_ids": [str(feature["properties"]["imb_id"]) for feature in features if feature["properties"].get("assignment_method") == "nearest_boundary_snap_review"],
        "groups": group_reports,
        "unresolved": unresolved,
        "features": {"type": "FeatureCollection", "features": features},
    }
    return output


def visualize_partition(groups: list[dict[str, Any]], imbs: list[dict[str, Any]], result: dict[str, Any], output_path: Path, *, crs: str, focus: bool = False) -> None:
    """Draw the geometry used for the partition and its unresolved IMBs."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch, Polygon as PlotPolygon

    figure, axis = plt.subplots(figsize=(14, 11), dpi=200)
    features = result["features"]["features"]
    used_groups = {str(feature["properties"]["group_id"]) for feature in features}

    for group in groups:
        group_id = _group_id(group)
        linewidth = 1.0 if group_id in used_groups else 0.35
        color = "#1f2937" if group_id in used_groups else "#94a3b8"
        for part in _group_parts(_geometry_of(group)):
            outer = [_project(point, crs) for point in part["outer"]]
            axis.add_patch(PlotPolygon(outer, closed=True, fill=False, edgecolor=color, linewidth=linewidth, zorder=1))
            for hole in part["holes"]:
                hole_xy = [_project(point, crs) for point in hole]
                axis.add_patch(PlotPolygon(hole_xy, closed=True, facecolor="#ffffff", edgecolor=color, linewidth=0.35, zorder=1.1))

    palette = plt.get_cmap("tab20")
    for index, feature in enumerate(sorted(features, key=lambda item: str(item["properties"]["imb_id"]))):
        color = palette(index % 20)
        for part in _group_parts(feature["geometry"]):
            outer = [_project(point, crs) for point in part["outer"]]
            axis.add_patch(PlotPolygon(outer, closed=True, facecolor=color, edgecolor="#334155", linewidth=0.45, alpha=0.63, zorder=2))
            for hole in part["holes"]:
                hole_xy = [_project(point, crs) for point in hole]
                axis.add_patch(PlotPolygon(hole_xy, closed=True, facecolor="#ffffff", edgecolor="#334155", linewidth=0.3, zorder=2.1))

    partitioned_ids = {str(feature["properties"]["imb_id"]) for feature in features}
    snapped_features = {str(feature["properties"]["imb_id"]): feature for feature in features if feature["properties"].get("assignment_method") == "nearest_boundary_snap_review"}
    unmatched_ids = {str(item["imb_id"]) for item in result["unresolved"]}
    for imb in imbs:
        imb_id = _id(imb, "imb_id", "code", "CODE", "id")
        point = _project(_seed(imb), crs)
        if imb_id in unmatched_ids:
            axis.scatter([point[0]], [point[1]], marker="x", s=34, c="#dc2626", linewidths=1.25, zorder=5)
            axis.annotate(imb_id, point, xytext=(4, 4), textcoords="offset points", fontsize=6.5, color="#991b1b", zorder=6)
        elif imb_id in snapped_features:
            seed = snapped_features[imb_id]["properties"]
            snapped_point = _project((seed["partition_seed_x"], seed["partition_seed_y"]), crs)
            axis.plot([point[0], snapped_point[0]], [point[1], snapped_point[1]], color="#d97706", linewidth=0.7, zorder=4)
            axis.scatter([point[0]], [point[1]], marker="x", s=28, c="#d97706", linewidths=1.1, zorder=5)
            axis.scatter([snapped_point[0]], [snapped_point[1]], marker="D", s=19, facecolor="#f59e0b", edgecolor="#92400e", linewidths=0.45, zorder=5.1)
            axis.annotate(imb_id, point, xytext=(4, 4), textcoords="offset points", fontsize=6.5, color="#92400e", zorder=6)
        elif imb_id in partitioned_ids:
            axis.scatter([point[0]], [point[1]], marker="o", s=12, facecolor="#ffffff", edgecolor="#111827", linewidths=0.45, zorder=4)

    if focus:
        focus_points = []
        for feature in features:
            for part in _group_parts(feature["geometry"]):
                focus_points.extend(_project(point, crs) for point in part["outer"])
        for imb in imbs:
            imb_id = _id(imb, "imb_id", "code", "CODE", "id")
            if imb_id in unmatched_ids:
                focus_points.append(_project(_seed(imb), crs))
        if focus_points:
            min_x, max_x = min(point[0] for point in focus_points), max(point[0] for point in focus_points)
            min_y, max_y = min(point[1] for point in focus_points), max(point[1] for point in focus_points)
            padding = max(20.0, max(max_x - min_x, max_y - min_y) * 0.08)
            axis.set_xlim(min_x - padding, max_x + padding)
            axis.set_ylim(min_y - padding, max_y + padding)

    axis.legend(handles=[
        Line2D([0], [0], color="#1f2937", linewidth=1.0, label="Building group boundary"),
        Patch(facecolor=palette(0), edgecolor="#334155", alpha=0.63, label="IMB partition cell"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#ffffff", markeredgecolor="#111827", markersize=5, label="Assigned IMB seed"),
        Line2D([0], [0], marker="D", color="w", markerfacecolor="#f59e0b", markeredgecolor="#92400e", markersize=5, label="Snapped IMB seed (review)"),
        Line2D([0], [0], marker="x", color="#dc2626", markersize=6, label="Unmatched IMB"),
    ], loc="best", fontsize=8, frameon=True, framealpha=0.94)
    axis.set_title("IMB-constrained building partition detail" if focus else "IMB-constrained building partition overview", fontsize=13, pad=10)
    axis.set_xlabel("EPSG:32629 easting (m)")
    axis.set_ylabel("EPSG:32629 northing (m)")
    axis.set_aspect("equal", adjustable="box")
    axis.grid(color="#cbd5e1", linewidth=0.35, alpha=0.65)
    figure.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, bbox_inches="tight")
    plt.close(figure)


def main() -> int:
    parser = argparse.ArgumentParser(description="Partition building-group polygons using IMB seeds.")
    parser.add_argument("--groups", type=Path, required=True, help="GEO_BUILDING group polygons (GeoPackage/GeoJSON).")
    parser.add_argument("--groups-layer", default="GEO_BUILDING", help="GeoPackage layer name for --groups.")
    parser.add_argument("--imb", type=Path, required=True, help="IMB layer (GeoPackage/GeoJSON/task-2 nodes JSON).")
    parser.add_argument("--imb-layer", default="IMB", help="GeoPackage layer name for --imb.")
    parser.add_argument("--output", type=Path, required=True, help="Output GeoJSON FeatureCollection.")
    parser.add_argument("--report", type=Path, default=None, help="Optional JSON validation report.")
    parser.add_argument("--visualization", type=Path, default=None, help="Optional output PNG review map.")
    parser.add_argument("--crs", default="EPSG:4326", help="Input/output CRS; EPSG:4326 or EPSG:32629.")
    parser.add_argument("--snap-tolerance-m", type=float, default=0.0, help="Maximum distance in metres for a controlled nearest-boundary IMB snap.")
    parser.add_argument("--allow-nearest-group", action="store_true", help="Assign an IMB outside all groups to the nearest group and mark it for review.")
    args = parser.parse_args()
    groups = _load_source(args.groups, args.groups_layer if args.groups.suffix.lower() in {".gpkg", ".sqlite", ".db"} else None)
    imbs = _load_source(args.imb, args.imb_layer if args.imb.suffix.lower() in {".gpkg", ".sqlite", ".db"} else None, only_imb_nodes=True)
    if args.snap_tolerance_m < 0:
        parser.error("--snap-tolerance-m must be non-negative")
    result = partition(groups, imbs, crs=args.crs, allow_nearest_group=args.allow_nearest_group, assignment_snap_tolerance_m=args.snap_tolerance_m)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result["features"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path = args.report or args.output.with_name(args.output.stem + "_report.json")
    visualization_path = args.visualization or args.output.with_name(args.output.stem + "_overview.png")
    visualize_partition(groups, imbs, result, visualization_path, crs=args.crs)
    stem = visualization_path.stem[:-9] if visualization_path.stem.endswith("_overview") else visualization_path.stem
    detail_path = visualization_path.with_name(stem + "_detail.png")
    visualize_partition(groups, imbs, result, detail_path, crs=args.crs, focus=True)
    report = {key: value for key, value in result.items() if key != "features"}
    report["visualizations"] = {"overview": str(visualization_path), "detail": str(detail_path)}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "report": str(report_path.resolve()), "visualizations": {"overview": str(visualization_path.resolve()), "detail": str(detail_path.resolve())}, "summary": result["summary"]}, ensure_ascii=False, indent=2))
    return 0 if not result["unresolved"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
