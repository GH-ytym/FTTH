"""Group recognized building footprints into nearby building groups.

The recognition delivery is an auxiliary GeoPackage in EPSG:4326.  This
module deliberately keeps it separate from the formal IMB/ZNRO/ZPM layers:
it produces a reviewable building-group candidate layer for task 2.

The default method is single-linkage clustering on the minimum distance
between building outlines.  Polygon intersections therefore belong to the
same group, and a chain of buildings is retained as one connected group.
Distances are calculated in WGS84 UTM zone 29N (EPSG:32629).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import struct
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as PlotPolygon

from task2_geometry_features import load_features

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from project_paths import TASK2_ZONE_PARTITION_OUTPUT_DIR


EARTH_A = 6378137.0
EARTH_E2 = 0.0066943799901413165
UTM_K0 = 0.9996
UTM_CENTRAL_MERIDIAN = math.radians(-9.0)  # zone 29


def _wkb_reader(blob):
    """Return a cursor over the WKB payload in a GeoPackage geometry blob."""
    if blob is None:
        raise ValueError("Building has a null geometry")
    raw = bytes(blob)
    if raw[:2] == b"GP":
        raw = raw[8:]  # GeoPackage header: magic, version/flags, srs id
    return raw


def _read_wkb(raw, offset=0):
    order = raw[offset]
    offset += 1
    endian = "<" if order == 1 else ">"
    geometry_type = struct.unpack_from(endian + "I", raw, offset)[0]
    offset += 4
    # OGC type codes may contain Z/M/SRID flags.  The delivery uses 2D data.
    base_type = geometry_type & 0xFF
    if base_type == 1:  # Point
        x, y = struct.unpack_from(endian + "dd", raw, offset)
        return {"type": "Point", "coordinates": (x, y)}, offset + 16
    if base_type == 2:  # LineString
        count = struct.unpack_from(endian + "I", raw, offset)[0]
        offset += 4
        points = []
        for _ in range(count):
            points.append(struct.unpack_from(endian + "dd", raw, offset))
            offset += 16
        return {"type": "LineString", "coordinates": points}, offset
    if base_type == 3:  # Polygon
        ring_count = struct.unpack_from(endian + "I", raw, offset)[0]
        offset += 4
        rings = []
        for _ in range(ring_count):
            count = struct.unpack_from(endian + "I", raw, offset)[0]
            offset += 4
            ring = []
            for _ in range(count):
                ring.append(struct.unpack_from(endian + "dd", raw, offset))
                offset += 16
            rings.append(ring)
        return {"type": "Polygon", "coordinates": rings}, offset
    if base_type == 6:  # MultiPolygon
        count = struct.unpack_from(endian + "I", raw, offset)[0]
        offset += 4
        polygons = []
        for _ in range(count):
            polygon, offset = _read_wkb(raw, offset)
            if polygon["type"] != "Polygon":
                raise ValueError("Unexpected member in MultiPolygon")
            polygons.append(polygon["coordinates"])
        return {"type": "MultiPolygon", "coordinates": polygons}, offset
    raise ValueError("Unsupported WKB geometry type: %s" % base_type)


def parse_geometry(blob):
    geometry, _ = _read_wkb(_wkb_reader(blob))
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiPolygon":
        return geometry["coordinates"]
    raise ValueError("GEO_BUILDING geometry must be Polygon or MultiPolygon")


def utm29(lon, lat):
    """WGS84 longitude/latitude to EPSG:32629, without a pyproj dependency."""
    lon = math.radians(lon)
    lat = math.radians(lat)
    e2 = EARTH_E2
    ep2 = e2 / (1.0 - e2)
    n = EARTH_A / math.sqrt(1.0 - e2 * math.sin(lat) ** 2)
    t = math.tan(lat) ** 2
    c = ep2 * math.cos(lat) ** 2
    a = math.cos(lat) * (lon - UTM_CENTRAL_MERIDIAN)
    m = EARTH_A * ((1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256) * lat
                   - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * math.sin(2 * lat)
                   + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * math.sin(4 * lat)
                   - (35 * e2**3 / 3072) * math.sin(6 * lat))
    x = UTM_K0 * n * (a + (1 - t + c) * a**3 / 6
                      + (5 - 18 * t + t**2 + 72 * c - 58 * ep2) * a**5 / 120) + 500000.0
    y = UTM_K0 * (m + n * math.tan(lat) * (a**2 / 2
                      + (5 - t + 9 * c + 4 * c**2) * a**4 / 24
                      + (61 - 58 * t + t**2 + 600 * c - 330 * ep2) * a**6 / 720))
    return x, y


def ring_area(ring):
    return abs(sum(ring[i][0] * ring[(i + 1) % len(ring)][1]
                   - ring[(i + 1) % len(ring)][0] * ring[i][1]
                   for i in range(len(ring))) / 2.0)


def polygon_centroid(rings):
    outer = rings[0]
    signed = sum(outer[i][0] * outer[(i + 1) % len(outer)][1]
                 - outer[(i + 1) % len(outer)][0] * outer[i][1]
                 for i in range(len(outer)))
    if abs(signed) < 1e-12:
        return tuple(map(float, outer[0]))
    cx = sum((outer[i][0] + outer[(i + 1) % len(outer)][0]) *
             (outer[i][0] * outer[(i + 1) % len(outer)][1]
              - outer[(i + 1) % len(outer)][0] * outer[i][1])
             for i in range(len(outer))) / (3.0 * signed)
    cy = sum((outer[i][1] + outer[(i + 1) % len(outer)][1]) *
             (outer[i][0] * outer[(i + 1) % len(outer)][1]
              - outer[(i + 1) % len(outer)][0] * outer[i][1])
             for i in range(len(outer))) / (3.0 * signed)
    return cx, cy


def point_segment_distance(point, start, end):
    px, py = point
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return math.hypot(px - sx, py - sy)
    t = max(0.0, min(1.0, ((px - sx) * dx + (py - sy) * dy) / length2))
    return math.hypot(px - (sx + t * dx), py - (sy + t * dy))


def orientation(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_intersect(a, b, c, d):
    eps = 1e-8
    o1, o2, o3, o4 = orientation(a, b, c), orientation(a, b, d), orientation(c, d, a), orientation(c, d, b)
    if ((o1 > eps and o2 < -eps) or (o1 < -eps and o2 > eps)) and ((o3 > eps and o4 < -eps) or (o3 < -eps and o4 > eps)):
        return True
    return (abs(o1) <= eps and point_segment_distance(c, a, b) <= eps or
            abs(o2) <= eps and point_segment_distance(d, a, b) <= eps or
            abs(o3) <= eps and point_segment_distance(a, c, d) <= eps or
            abs(o4) <= eps and point_segment_distance(b, c, d) <= eps)


def point_in_ring(point, ring):
    inside = False
    x, y = point
    for i, start in enumerate(ring):
        end = ring[(i + 1) % len(ring)]
        if (start[1] > y) != (end[1] > y):
            at_x = (end[0] - start[0]) * (y - start[1]) / (end[1] - start[1]) + start[0]
            if x < at_x:
                inside = not inside
    return inside


def outline_distance(left_polygons, right_polygons):
    best = float("inf")
    for left in left_polygons:
        left_ring = left[0]
        for right in right_polygons:
            right_ring = right[0]
            minx = max(min(p[0] for p in left_ring), min(p[0] for p in right_ring))
            maxx = min(max(p[0] for p in left_ring), max(p[0] for p in right_ring))
            miny = max(min(p[1] for p in left_ring), min(p[1] for p in right_ring))
            maxy = min(max(p[1] for p in left_ring), max(p[1] for p in right_ring))
            if minx <= maxx and miny <= maxy:
                for i, a in enumerate(left_ring):
                    b = left_ring[(i + 1) % len(left_ring)]
                    for j, c in enumerate(right_ring):
                        d = right_ring[(j + 1) % len(right_ring)]
                        if segments_intersect(a, b, c, d):
                            return 0.0
                        best = min(best, point_segment_distance(a, c, d), point_segment_distance(c, a, b))
                if point_in_ring(left_ring[0], right_ring) or point_in_ring(right_ring[0], left_ring):
                    return 0.0
            else:
                # Bounding boxes are disjoint; this lower bound avoids most edge work.
                gap = math.hypot(max(minx - maxx, 0.0), max(miny - maxy, 0.0))
                best = min(best, gap)
    return best


def convex_hull(points):
    points = sorted(set((float(x), float(y)) for x, y in points))
    if len(points) <= 2:
        return points
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower = []
    for point in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


class UnionFind:
    def __init__(self, size):
        self.parent = list(range(size))

    def find(self, item):
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left, right):
        left, right = self.find(left), self.find(right)
        if left != right:
            self.parent[right] = left


def _polygon_parts(geometry):
    kind = str((geometry or {}).get("type") or "")
    coordinates = (geometry or {}).get("coordinates") or []
    if kind == "Polygon":
        return [coordinates]
    if kind == "MultiPolygon":
        return coordinates
    raise ValueError("GEO_BUILDING geometry must be Polygon or MultiPolygon")


def _prepare_building(row, lonlat_polygons):
    projected_polygons = [[[utm29(x, y) for x, y in ring] for ring in polygon] for polygon in lonlat_polygons]
    all_points = [point for polygon in projected_polygons for ring in polygon for point in ring]
    centroid_ll = polygon_centroid(lonlat_polygons[0])
    centroid_xy = utm29(*centroid_ll)
    area = float(row.get("area_m2") or 0.0)
    if area <= 0:
        area = sum(ring_area(ring) for polygon in projected_polygons for ring in polygon)
    tags = row.get("tags_json") or "{}"
    try:
        confidence = json.loads(tags).get("mean_confidence") if isinstance(tags, str) else None
    except json.JSONDecodeError:
        confidence = None
    return {
        "building_id": row.get("building_id"),
        "building": row.get("building"),
        "source": row.get("source"),
        "needs_review": int(row.get("needs_review") or 0),
        "reliability": row.get("reliability"),
        "area_m2": round(area, 3),
        "confidence": confidence,
        "polygons_wgs84": lonlat_polygons,
        "polygons_utm": projected_polygons,
        "centroid_wgs84": {"x": round(centroid_ll[0], 8), "y": round(centroid_ll[1], 8)},
        "centroid_utm": {"x": round(centroid_xy[0], 3), "y": round(centroid_xy[1], 3)},
        "bbox_utm": {
            "min_x": round(min(p[0] for p in all_points), 3), "min_y": round(min(p[1] for p in all_points), 3),
            "max_x": round(max(p[0] for p in all_points), 3), "max_y": round(max(p[1] for p in all_points), 3),
        },
    }


def read_buildings(gpkg_path):
    connection = sqlite3.connect(str(gpkg_path))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT * FROM GEO_BUILDING ORDER BY fid").fetchall()
    finally:
        connection.close()
    buildings = [_prepare_building(dict(row), parse_geometry(row["geom"])) for row in rows]
    if not buildings:
        raise ValueError("GEO_BUILDING is empty")
    return buildings


def read_buildings_json(input_path):
    records = load_features(input_path, "GEO_BUILDING")
    buildings = [_prepare_building(record, _polygon_parts(record.get("geometry"))) for record in records]
    if not buildings:
        raise ValueError("recognition.buildings is empty")
    return buildings


def cluster_buildings(buildings, threshold_m):
    uf = UnionFind(len(buildings))
    links = []
    for i in range(len(buildings)):
        for j in range(i + 1, len(buildings)):
            distance = outline_distance(buildings[i]["polygons_utm"], buildings[j]["polygons_utm"])
            if distance <= threshold_m:
                uf.union(i, j)
                links.append({"left": buildings[i]["building_id"], "right": buildings[j]["building_id"], "distance_m": round(distance, 3)})
    groups = defaultdict(list)
    for index in range(len(buildings)):
        groups[uf.find(index)].append(index)
    clusters = []
    assignment = {}
    for number, indices in enumerate(sorted(groups.values(), key=lambda item: min(buildings[i]["building_id"] for i in item)), 1):
        points = [point for i in indices for polygon in buildings[i]["polygons_utm"] for ring in polygon for point in ring]
        cluster_id = "BGRP-%03d" % number
        for index in indices:
            assignment[buildings[index]["building_id"]] = cluster_id
        clusters.append({
            "cluster_id": cluster_id,
            "building_ids": [buildings[i]["building_id"] for i in indices],
            "building_count": len(indices),
            "total_area_m2": round(sum(buildings[i]["area_m2"] for i in indices), 3),
            "centroid_utm": {"x": round(sum(buildings[i]["centroid_utm"]["x"] for i in indices) / len(indices), 3), "y": round(sum(buildings[i]["centroid_utm"]["y"] for i in indices) / len(indices), 3)},
            "hull_utm": [[round(x, 3), round(y, 3)] for x, y in convex_hull(points)],
            "max_link_distance_m": round(max((link["distance_m"] for link in links if link["left"] in {buildings[i]["building_id"] for i in indices} and link["right"] in {buildings[i]["building_id"] for i in indices}), default=0.0), 3),
        })
    for building in buildings:
        building["cluster_id"] = assignment[building["building_id"]]
    return clusters, links


def visualize(buildings, clusters, links, output_path, threshold_m):
    colors = plt.get_cmap("tab20")
    color_by_id = {cluster["cluster_id"]: colors(i % 20) for i, cluster in enumerate(clusters)}
    fig, ax = plt.subplots(figsize=(12, 10), dpi=180)
    for link in links:
        left = next(b for b in buildings if b["building_id"] == link["left"])
        right = next(b for b in buildings if b["building_id"] == link["right"])
        ax.plot([left["centroid_utm"]["x"], right["centroid_utm"]["x"]], [left["centroid_utm"]["y"], right["centroid_utm"]["y"]], color="#9ca3af", linewidth=0.35, alpha=0.45, zorder=1)
    for building in buildings:
        color = color_by_id[building["cluster_id"]]
        for polygon in building["polygons_utm"]:
            ax.add_patch(PlotPolygon(polygon[0], closed=True, facecolor=color, edgecolor="#202020", linewidth=0.45, alpha=0.72, zorder=2))
        ax.scatter([building["centroid_utm"]["x"]], [building["centroid_utm"]["y"]], s=5, color="#111827", zorder=3)
    for cluster in clusters:
        hull = cluster["hull_utm"]
        if len(hull) >= 3:
            ax.add_patch(PlotPolygon(hull, closed=True, facecolor=color_by_id[cluster["cluster_id"]], edgecolor=color_by_id[cluster["cluster_id"]], linewidth=1.6, alpha=0.08, zorder=0))
        center = cluster["centroid_utm"]
        ax.text(center["x"], center["y"], "%s\n%d buildings" % (cluster["cluster_id"], cluster["building_count"]), ha="center", va="center", fontsize=7, bbox={"facecolor": "white", "edgecolor": color_by_id[cluster["cluster_id"]], "pad": 2, "alpha": 0.85}, zorder=4)
    ax.set_aspect("equal", adjustable="box")
    ax.set_title("Recognized building groups (outline gap <= %.1f m)" % threshold_m)
    ax.set_xlabel("EPSG:32629 easting (m)")
    ax.set_ylabel("EPSG:32629 northing (m)")
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def parse_args():
    output_dir = TASK2_ZONE_PARTITION_OUTPUT_DIR
    parser = argparse.ArgumentParser(description="Group nearby recognized GEO_BUILDING footprints.")
    parser.add_argument("--input-gpkg", type=Path, default=None, help="Input GeoPackage path.")
    parser.add_argument("--input-json", type=Path, default=None, help="Dedicated task-2 input JSON containing recognition.buildings.")
    parser.add_argument("--distance-threshold-m", type=float, default=15.0, help="Maximum outline-to-outline gap for one graph edge (default: 15 m).")
    parser.add_argument("--output-json", type=Path, default=output_dir / "building_groups.json")
    parser.add_argument("--output-csv", type=Path, default=output_dir / "building_group_members.csv")
    parser.add_argument("--output-fig", type=Path, default=output_dir / "building_groups.png")
    return parser.parse_args()


def main():
    args = parse_args()
    if bool(args.input_gpkg) == bool(args.input_json):
        raise SystemExit("provide exactly one of --input-gpkg or --input-json")
    if args.distance_threshold_m < 0:
        raise SystemExit("--distance-threshold-m must be non-negative")
    input_path = args.input_json or args.input_gpkg
    buildings = read_buildings_json(args.input_json) if args.input_json else read_buildings(args.input_gpkg)
    clusters, links = cluster_buildings(buildings, args.distance_threshold_m)
    assigned = [b["building_id"] for b in buildings]
    result = {
        "schema_version": "task2-building-groups-1.0",
        "source_file": str(input_path),
        "source_layer": "GEO_BUILDING",
        "source_semantics": "AI:remote_sensing candidate footprints; needs_review is preserved",
        "calculation_crs": "EPSG:32629",
        "output_crs": "EPSG:4326",
        "algorithm": {"method": "single_linkage_connected_components", "distance_metric": "minimum_polygon_outline_distance", "distance_threshold_m": args.distance_threshold_m, "transitive_grouping": True},
        "summary": {"building_count": len(buildings), "cluster_count": len(clusters), "linked_pair_count": len(links), "singleton_cluster_count": sum(c["building_count"] == 1 for c in clusters), "covered_building_count": len(set(assigned))},
        "validation": {"all_buildings_assigned_once": len(assigned) == len(set(assigned)) == len(buildings), "cluster_ids_unique": len({c["cluster_id"] for c in clusters}) == len(clusters), "max_cluster_link_distance_within_threshold": all(c["max_link_distance_m"] <= args.distance_threshold_m + 1e-6 for c in clusters)},
        "clusters": clusters,
        "buildings": [{k: v for k, v in b.items() if k not in {"polygons_utm", "bbox_utm"}} for b in buildings],
        "links": links,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["building_id", "cluster_id", "area_m2", "centroid_x", "centroid_y", "needs_review", "confidence"])
        for building in buildings:
            writer.writerow([building["building_id"], building["cluster_id"], building["area_m2"], building["centroid_wgs84"]["x"], building["centroid_wgs84"]["y"], building["needs_review"], building["confidence"]])
    visualize(buildings, clusters, links, args.output_fig, args.distance_threshold_m)
    print("Buildings: %d; groups: %d; links: %d" % (len(buildings), len(clusters), len(links)))
    print("Result JSON: %s" % args.output_json.resolve())
    print("Member CSV: %s" % args.output_csv.resolve())
    print("Visualization: %s" % args.output_fig.resolve())


if __name__ == "__main__":
    main()
