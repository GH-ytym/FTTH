"""Render the remote-sensing recognition delivery without GIS dependencies.

The delivery GeoPackage is a SQLite database whose geometries are GeoPackage
binary WKB.  This reader covers the geometry types used by the delivered
``GEO_*`` layers, so a reviewer can inspect the package in the standard Python
environment even when QGIS, GDAL and GeoPandas are unavailable.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import struct
import sys
from collections import Counter
from pathlib import Path
from typing import Any


TASK_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = TASK_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from project_paths import RECOGNITION_BUILDING_INPUT_DIR, task_output

DEFAULT_GPKG = RECOGNITION_BUILDING_INPUT_DIR / "regional_geo_context.gpkg"
DEFAULT_OUTPUT = task_output(6, "recognition_inspection", "recognition_delivery_overview.png")
DEFAULT_BUILDING_OUTPUT = task_output(6, "recognition_inspection", "recognized_buildings.png")
DEFAULT_CLEANED_CORE_OUTPUT = task_output(6, "recognition_inspection", "cleaned_core_layers.png")

_ENVELOPE_SIZES = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
_GEOMETRY_TYPES = {
    1: "Point",
    2: "LineString",
    3: "Polygon",
    4: "MultiPoint",
    5: "MultiLineString",
    6: "MultiPolygon",
    7: "GeometryCollection",
}


def _read_point(buffer: memoryview, offset: int, endian: str, dimensions: int) -> tuple[tuple[float, float], int]:
    values = struct.unpack_from(f"{endian}{'d' * dimensions}", buffer, offset)
    return (float(values[0]), float(values[1])), offset + 8 * dimensions


def _wkb_type(raw_type: int) -> tuple[int, int]:
    """Return ordinary WKB base type and coordinate dimension count."""
    has_z = bool(raw_type & 0x80000000)
    has_m = bool(raw_type & 0x40000000)
    ordinary_type = raw_type & 0x0FFFFFFF
    iso_dimension = ordinary_type // 1000
    base_type = ordinary_type % 1000 if iso_dimension else ordinary_type
    dimensions = 2 + int(has_z or iso_dimension in {1, 3}) + int(has_m or iso_dimension in {2, 3})
    return base_type, dimensions


def _parse_wkb(buffer: memoryview, offset: int = 0) -> tuple[dict[str, Any], int]:
    byte_order = int(buffer[offset])
    if byte_order not in {0, 1}:
        raise ValueError(f"Unsupported WKB byte order {byte_order}")
    endian = "<" if byte_order else ">"
    raw_type = struct.unpack_from(f"{endian}I", buffer, offset + 1)[0]
    base_type, dimensions = _wkb_type(raw_type)
    geometry_type = _GEOMETRY_TYPES.get(base_type)
    if not geometry_type:
        raise ValueError(f"Unsupported WKB geometry type {raw_type}")
    offset += 5
    if base_type == 1:
        point, offset = _read_point(buffer, offset, endian, dimensions)
        return {"type": geometry_type, "coordinates": point}, offset
    if base_type == 2:
        count = struct.unpack_from(f"{endian}I", buffer, offset)[0]
        offset += 4
        coordinates = []
        for _ in range(count):
            point, offset = _read_point(buffer, offset, endian, dimensions)
            coordinates.append(point)
        return {"type": geometry_type, "coordinates": coordinates}, offset
    if base_type == 3:
        ring_count = struct.unpack_from(f"{endian}I", buffer, offset)[0]
        offset += 4
        rings = []
        for _ in range(ring_count):
            point_count = struct.unpack_from(f"{endian}I", buffer, offset)[0]
            offset += 4
            ring = []
            for _ in range(point_count):
                point, offset = _read_point(buffer, offset, endian, dimensions)
                ring.append(point)
            rings.append(ring)
        return {"type": geometry_type, "coordinates": rings}, offset
    count = struct.unpack_from(f"{endian}I", buffer, offset)[0]
    offset += 4
    children = []
    for _ in range(count):
        child, offset = _parse_wkb(buffer, offset)
        children.append(child)
    if base_type == 4:
        coordinates = [child["coordinates"] for child in children]
    elif base_type == 5:
        coordinates = [child["coordinates"] for child in children]
    elif base_type == 6:
        coordinates = [child["coordinates"] for child in children]
    else:
        coordinates = children
    return {"type": geometry_type, "coordinates": coordinates}, offset


def parse_gpkg_geometry(blob: bytes | memoryview) -> dict[str, Any]:
    """Decode a GeoPackage geometry blob to a compact GeoJSON-like mapping."""
    buffer = memoryview(blob)
    if len(buffer) < 8 or bytes(buffer[:2]) != b"GP":
        raise ValueError("Expected a GeoPackage geometry blob")
    envelope_code = (int(buffer[3]) >> 1) & 0x07
    if envelope_code not in _ENVELOPE_SIZES:
        raise ValueError(f"Unsupported GeoPackage envelope code {envelope_code}")
    geometry, _ = _parse_wkb(buffer, 8 + _ENVELOPE_SIZES[envelope_code])
    return geometry


def load_layer(connection: sqlite3.Connection, layer: str) -> list[dict[str, Any]]:
    cursor = connection.execute(f'SELECT * FROM "{layer}"')
    names = [item[0] for item in cursor.description]
    records = []
    for values in cursor:
        record = dict(zip(names, values))
        record["geometry"] = parse_gpkg_geometry(record.pop("geom")) if record.get("geom") else None
        records.append(record)
    return records


def _plot_polygons(axis: Any, records: list[dict[str, Any]], *, facecolor: str, edgecolor: str, alpha: float, linewidth: float, zorder: int) -> None:
    for record in records:
        geometry = record.get("geometry") or {}
        geometry_type = geometry.get("type")
        if geometry_type not in {"Polygon", "MultiPolygon"}:
            continue
        polygons = geometry.get("coordinates") if geometry_type == "MultiPolygon" else [geometry.get("coordinates")]
        for polygon in polygons:
            if not polygon:
                continue
            for index, ring in enumerate(polygon):
                if not ring:
                    continue
                axis.fill(
                    [point[0] for point in ring],
                    [point[1] for point in ring],
                    facecolor=facecolor if index == 0 else "white",
                    edgecolor=edgecolor,
                    alpha=alpha,
                    linewidth=linewidth,
                    zorder=zorder,
                )


def _plot_lines(axis: Any, records: list[dict[str, Any]], *, color: str, linewidth: float, linestyle: str = "-", alpha: float = 1.0, zorder: int = 1) -> None:
    for record in records:
        geometry = record.get("geometry") or {}
        geometry_type = geometry.get("type")
        if geometry_type not in {"LineString", "MultiLineString"}:
            continue
        lines = geometry.get("coordinates") if geometry_type == "MultiLineString" else [geometry.get("coordinates")]
        for line in lines:
            if line:
                axis.plot([point[0] for point in line], [point[1] for point in line], color=color, linewidth=linewidth, linestyle=linestyle, alpha=alpha, zorder=zorder)


def _building_label_point(record: dict[str, Any]) -> tuple[float, float] | None:
    """Return a stable, dependency-free label position for a building footprint."""
    geometry = record.get("geometry") or {}
    geometry_type = geometry.get("type")
    if geometry_type == "Polygon":
        polygons = [geometry.get("coordinates")]
    elif geometry_type == "MultiPolygon":
        polygons = geometry.get("coordinates") or []
    else:
        return None
    exterior_points = []
    for polygon in polygons:
        if polygon and polygon[0]:
            exterior_points.extend(polygon[0])
    if not exterior_points:
        return None
    return (
        sum(point[0] for point in exterior_points) / len(exterior_points),
        sum(point[1] for point in exterior_points) / len(exterior_points),
    )


def _load_geojson_lines(path: Path) -> list[dict[str, Any]]:
    """Load line features from a GeoJSON sidecar, preserving their properties."""
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    records = []
    for feature in payload.get("features") or []:
        geometry = feature.get("geometry") or {}
        if geometry.get("type") in {"LineString", "MultiLineString"}:
            record = dict(feature.get("properties") or {})
            record["geometry"] = geometry
            records.append(record)
    return records


def render_buildings(gpkg_path: Path, output_path: Path) -> int:
    """Render only recognized building footprints and return the feature count."""
    with sqlite3.connect(gpkg_path) as connection:
        buildings = load_layer(connection, "GEO_BUILDING")

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    buildings = sorted(buildings, key=lambda item: str(item.get("building_id") or ""))
    ai_buildings = [item for item in buildings if str(item.get("source") or "").startswith("AI:")]
    review_buildings = [item for item in buildings if int(item.get("needs_review") or 0)]

    figure, axis = plt.subplots(figsize=(13, 10), constrained_layout=True)
    for record in buildings:
        needs_review = int(record.get("needs_review") or 0)
        _plot_polygons(
            axis,
            [record],
            facecolor="#bfdbfe" if str(record.get("source") or "").startswith("AI:") else "#cbd5e1",
            edgecolor="#dc2626" if needs_review else "#1d4ed8",
            alpha=0.78,
            linewidth=1.1 if needs_review else 0.75,
            zorder=2,
        )

    for index, record in enumerate(buildings, start=1):
        label_point = _building_label_point(record)
        if label_point:
            axis.text(
                label_point[0],
                label_point[1],
                f"B{index:02d}",
                ha="center",
                va="center",
                fontsize=7,
                color="#0f172a",
                bbox={"boxstyle": "round,pad=0.12", "facecolor": "white", "edgecolor": "none", "alpha": 0.72},
                zorder=3,
            )

    axis.set_title("Recognized Building Footprints", fontsize=15, pad=12)
    axis.set_xlabel("Longitude (EPSG:4326)")
    axis.set_ylabel("Latitude (EPSG:4326)")
    axis.set_aspect("equal", adjustable="box")
    axis.grid(True, alpha=0.18)
    axis.legend(
        handles=[
            Patch(facecolor="#cbd5e1", edgecolor="#1d4ed8", alpha=0.78, label=f"Recognized building ({len(buildings)})"),
            Patch(facecolor="#bfdbfe", edgecolor="#1d4ed8", alpha=0.78, label=f"AI building ({len(ai_buildings)})"),
            Patch(facecolor="#cbd5e1", edgecolor="#dc2626", alpha=0.78, label=f"Needs review ({len(review_buildings)})"),
        ],
        loc="upper left",
        fontsize=9,
        framealpha=0.94,
    )
    axis.text(
        0.99,
        0.01,
        "Labels B01-Bxx follow sorted building_id order; confirm footprints during field survey.",
        transform=axis.transAxes,
        ha="right",
        va="bottom",
        fontsize=8,
        color="#334155",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=220)
    plt.close(figure)
    return len(buildings)


def render_cleaned_core(gpkg_path: Path, output_path: Path) -> dict[str, int]:
    """Render only buildings, complete road segments, green areas and access points."""
    with sqlite3.connect(gpkg_path) as connection:
        layers = {
            name: load_layer(connection, name)
            for name in ("GEO_BUILDING", "GEO_ROAD", "GEO_GREEN", "GEO_ACCESS_CANDIDATE")
        }

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    buildings = sorted(layers["GEO_BUILDING"], key=lambda item: str(item.get("building_id") or ""))
    whole_roads_path = gpkg_path.with_name("task6_roads_whole.geojson")
    roads = _load_geojson_lines(whole_roads_path) if whole_roads_path.exists() else layers["GEO_ROAD"]
    green = layers["GEO_GREEN"]
    candidates = [item for item in layers["GEO_ACCESS_CANDIDATE"] if item.get("geometry")]
    eligible = [item for item in candidates if int(item.get("eligible") or 0)]
    ineligible = [item for item in candidates if item not in eligible]

    figure, axis = plt.subplots(figsize=(13, 10), constrained_layout=True)
    _plot_polygons(axis, green, facecolor="#86efac", edgecolor="#16a34a", alpha=0.34, linewidth=0.65, zorder=1)
    # GEO_ROAD retains full source segments; no viewport clipping is applied here.
    _plot_lines(axis, roads, color="#475569", linewidth=1.05, alpha=0.86, zorder=2)
    _plot_polygons(axis, buildings, facecolor="#bfdbfe", edgecolor="#1d4ed8", alpha=0.86, linewidth=0.95, zorder=3)

    for index, record in enumerate(buildings, start=1):
        label_point = _building_label_point(record)
        if label_point:
            axis.text(
                label_point[0],
                label_point[1],
                f"B{index:02d}",
                ha="center",
                va="center",
                fontsize=7,
                color="#0f172a",
                bbox={"boxstyle": "round,pad=0.12", "facecolor": "white", "edgecolor": "none", "alpha": 0.72},
                zorder=4,
            )

    axis.scatter(
        [item["geometry"]["coordinates"][0] for item in eligible],
        [item["geometry"]["coordinates"][1] for item in eligible],
        s=42,
        marker="o",
        color="#0f766e",
        edgecolors="white",
        linewidths=0.65,
        alpha=0.98,
        zorder=5,
    )
    axis.scatter(
        [item["geometry"]["coordinates"][0] for item in ineligible],
        [item["geometry"]["coordinates"][1] for item in ineligible],
        s=52,
        marker="x",
        color="#dc2626",
        linewidths=1.4,
        zorder=6,
    )

    counts = {
        "GEO_BUILDING": len(buildings),
        "GEO_ROAD": len(roads),
        "GEO_GREEN": len(green),
        "GEO_ACCESS_CANDIDATE": len(candidates),
    }
    axis.set_title("Cleaned Recognition Core Layers", fontsize=15, pad=12)
    axis.set_xlabel("Longitude (EPSG:4326)")
    axis.set_ylabel("Latitude (EPSG:4326)")
    axis.set_aspect("equal", adjustable="box")
    axis.margins(0.04)
    axis.grid(True, alpha=0.18)
    axis.legend(
        handles=[
            Patch(facecolor="#bfdbfe", edgecolor="#1d4ed8", alpha=0.86, label=f"Building ({counts['GEO_BUILDING']})"),
            Line2D([0], [0], color="#475569", linewidth=1.4, label=f"Complete road segment ({counts['GEO_ROAD']})"),
            Patch(facecolor="#86efac", edgecolor="#16a34a", alpha=0.48, label=f"Green area ({counts['GEO_GREEN']})"),
            Line2D([0], [0], marker="o", color="w", markerfacecolor="#0f766e", markeredgecolor="white", markersize=7, label=f"Eligible access point ({len(eligible)})"),
            Line2D([0], [0], marker="x", color="#dc2626", linestyle="None", markersize=7, label=f"Ineligible access point ({len(ineligible)})"),
        ],
        loc="upper left",
        fontsize=9,
        framealpha=0.94,
    )
    axis.text(
        0.99,
        0.01,
        "Whole-road sidecar is used when present; access candidates are absent from the latest cleaned package.",
        transform=axis.transAxes,
        ha="right",
        va="bottom",
        fontsize=8,
        color="#334155",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=220)
    plt.close(figure)
    return counts


def render(gpkg_path: Path, output_path: Path) -> dict[str, int]:
    """Render delivery layers and return feature counts for command-line output."""
    with sqlite3.connect(gpkg_path) as connection:
        layers = {name: load_layer(connection, name) for name in (
            "GEO_ADMIN", "GEO_COST_ZONE", "GEO_GREEN", "GEO_BUILDING", "GEO_ROAD", "GEO_BARRIER", "GEO_ROAD_NODE", "GEO_ACCESS_CANDIDATE"
        )}

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    figure, axis = plt.subplots(figsize=(15, 10), constrained_layout=True)
    _plot_polygons(axis, layers["GEO_ADMIN"], facecolor="none", edgecolor="#334155", alpha=1.0, linewidth=1.2, zorder=0)
    _plot_polygons(axis, layers["GEO_GREEN"], facecolor="#86efac", edgecolor="#16a34a", alpha=0.30, linewidth=0.45, zorder=1)
    prohibited = [item for item in layers["GEO_COST_ZONE"] if int(item.get("prohibited") or 0)]
    high_cost = [item for item in layers["GEO_COST_ZONE"] if not int(item.get("prohibited") or 0)]
    _plot_polygons(axis, high_cost, facecolor="#facc15", edgecolor="#ca8a04", alpha=0.20, linewidth=0.45, zorder=1)
    _plot_polygons(axis, prohibited, facecolor="#ef4444", edgecolor="#b91c1c", alpha=0.26, linewidth=0.65, zorder=2)
    ai_buildings = [item for item in layers["GEO_BUILDING"] if str(item.get("source") or "").startswith("AI:")]
    other_buildings = [item for item in layers["GEO_BUILDING"] if item not in ai_buildings]
    _plot_polygons(axis, ai_buildings, facecolor="#cbd5e1", edgecolor="#94a3b8", alpha=0.70, linewidth=0.28, zorder=3)
    _plot_polygons(axis, other_buildings, facecolor="#64748b", edgecolor="#334155", alpha=0.72, linewidth=0.35, zorder=4)
    _plot_lines(axis, layers["GEO_ROAD"], color="#475569", linewidth=2.0, alpha=0.92, zorder=5)
    railways = [item for item in layers["GEO_BARRIER"] if item.get("barrier_type") == "railway"]
    waterways = [
        item for item in layers["GEO_BARRIER"]
        if item.get("barrier_type") in {"waterway", "water"}
        and (item.get("geometry") or {}).get("type") in {"LineString", "MultiLineString"}
    ]
    water_areas = [
        item for item in layers["GEO_BARRIER"]
        if item.get("barrier_type") in {"waterway", "water"}
        and (item.get("geometry") or {}).get("type") in {"Polygon", "MultiPolygon"}
    ]
    _plot_lines(axis, railways, color="#0f172a", linewidth=1.4, linestyle="--", alpha=0.9, zorder=6)
    _plot_polygons(axis, water_areas, facecolor="#7dd3fc", edgecolor="#0891b2", alpha=0.58, linewidth=0.6, zorder=6)
    _plot_lines(axis, waterways, color="#0891b2", linewidth=2.0, alpha=0.95, zorder=6)

    nodes = [item for item in layers["GEO_ROAD_NODE"] if item.get("geometry")]
    intersections = [item for item in nodes if str(item.get("node_type") or "").lower() == "intersection"]
    endpoints = [item for item in nodes if item not in intersections]
    axis.scatter([item["geometry"]["coordinates"][0] for item in endpoints], [item["geometry"]["coordinates"][1] for item in endpoints], s=18, color="#334155", alpha=0.8, zorder=7)
    axis.scatter([item["geometry"]["coordinates"][0] for item in intersections], [item["geometry"]["coordinates"][1] for item in intersections], s=34, marker="s", color="#0f172a", zorder=8)
    candidates = [item for item in layers["GEO_ACCESS_CANDIDATE"] if item.get("geometry")]
    eligible = [item for item in candidates if int(item.get("eligible") or 0)]
    ineligible = [item for item in candidates if item not in eligible]
    axis.scatter([item["geometry"]["coordinates"][0] for item in eligible], [item["geometry"]["coordinates"][1] for item in eligible], s=17, marker="o", color="#0f766e", edgecolors="white", linewidths=0.3, alpha=0.95, zorder=9)
    axis.scatter([item["geometry"]["coordinates"][0] for item in ineligible], [item["geometry"]["coordinates"][1] for item in ineligible], s=32, marker="x", color="#dc2626", linewidths=1.0, zorder=10)

    counts = {name: len(records) for name, records in layers.items()}
    axis.set_title("Remote-Sensing Delivery Review Map", fontsize=15, pad=12)
    axis.set_xlabel("Longitude (EPSG:4326)")
    axis.set_ylabel("Latitude (EPSG:4326)")
    axis.set_aspect("equal", adjustable="box")
    axis.grid(True, alpha=0.18)
    axis.legend(
        handles=[
            Patch(facecolor="#cbd5e1", edgecolor="#94a3b8", label=f"AI building footprint ({len(ai_buildings)})"),
            Line2D([0], [0], color="#475569", linewidth=2, label=f"Candidate road segment ({counts['GEO_ROAD']})"),
            Patch(facecolor="#86efac", edgecolor="#16a34a", alpha=0.45, label=f"Green area ({counts['GEO_GREEN']})"),
            Line2D([0], [0], color="#0f172a", linewidth=1.4, linestyle="--", label=f"Rail barrier ({len(railways)})"),
            Line2D([0], [0], color="#0891b2", linewidth=2, label=f"Water / waterway barrier ({len(waterways) + len(water_areas)})"),
            Patch(facecolor="#facc15", edgecolor="#ca8a04", alpha=0.35, label=f"High-cost zone ({len(high_cost)})"),
            Patch(facecolor="#ef4444", edgecolor="#b91c1c", alpha=0.35, label=f"Prohibited zone ({len(prohibited)})"),
            Line2D([0], [0], marker="o", color="w", markerfacecolor="#0f766e", markeredgecolor="white", markersize=6, label=f"Eligible access candidate ({len(eligible)})"),
            Line2D([0], [0], marker="x", color="#dc2626", linestyle="None", markersize=6, label=f"Ineligible access candidate ({len(ineligible)})"),
            Line2D([0], [0], marker="s", color="#0f172a", linestyle="None", markersize=5, label=f"Road intersection ({len(intersections)})"),
        ],
        loc="upper left",
        fontsize=8,
        framealpha=0.94,
    )
    axis.text(
        0.99,
        0.01,
        "Candidate geographic data only. AI features and access candidates require survey review before construction.",
        transform=axis.transAxes,
        ha="right",
        va="bottom",
        fontsize=8,
        color="#334155",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=220)
    plt.close(figure)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Visualize the remote-sensing recognition delivery GeoPackage.")
    parser.add_argument("--input", type=Path, default=DEFAULT_GPKG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--mode", choices=("overview", "buildings", "cleaned_core"), default="overview")
    args = parser.parse_args()
    output_path = args.output
    if args.mode == "buildings":
        if output_path == DEFAULT_OUTPUT:
            output_path = DEFAULT_BUILDING_OUTPUT
        count = render_buildings(args.input, output_path)
        print(f"Building visualization: {output_path}")
        print(f"Buildings: {count}")
    elif args.mode == "cleaned_core":
        if output_path == DEFAULT_OUTPUT:
            output_path = DEFAULT_CLEANED_CORE_OUTPUT
        counts = render_cleaned_core(args.input, output_path)
        print(f"Cleaned core visualization: {output_path}")
        print("Layers: " + ", ".join(f"{name}={count}" for name, count in sorted(counts.items())))
    else:
        counts = render(args.input, output_path)
        print(f"Visualization: {output_path}")
        print("Layers: " + ", ".join(f"{name}={count}" for name, count in sorted(counts.items())))


if __name__ == "__main__":
    main()
