"""Filter the recognition delivery for the PM-assigned study rectangle.

The source GeoPackage is kept unchanged.  The cleaner uses the PM assignment
written by task 2 geometry features for building selection and keeps all other
recognition features whose geometry envelope intersects the requested box.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from project_paths import (
    RECOGNITION_BUILDING_INPUT_DIR,
    TASK2_ZONE_PARTITION_OUTPUT_DIR,
    TASK6_RECOGNITION_OUTPUT_DIR,
)

DEFAULT_GPKG = RECOGNITION_BUILDING_INPUT_DIR / "regional_geo_context.gpkg"
DEFAULT_PM_BUILDINGS = TASK2_ZONE_PARTITION_OUTPUT_DIR / "task2_geometry_features_recognition_probe.json"
DEFAULT_OUTPUT = TASK6_RECOGNITION_OUTPUT_DIR

# The user supplied corners are (latitude, longitude).
DEFAULT_CORNERS = ((33.226603, -8.533411), (33.222520, -8.526362))
LAYERS = (
    "GEO_BUILDING",
    "GEO_ROAD",
    "GEO_ROAD_NODE",
    "GEO_BARRIER",
    "GEO_GREEN",
    "GEO_ADMIN",
    "GEO_ACCESS_CANDIDATE",
    "GEO_COST_ZONE",
)


def _load_parser():
    module_path = ROOT / "任务6" / "code" / "visualize_recognition_delivery.py"
    spec = importlib.util.spec_from_file_location("recognition_geometry_reader", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load geometry reader: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.parse_gpkg_geometry


parse_gpkg_geometry = _load_parser()


def _numbers(value: Any) -> Iterable[tuple[float, float]]:
    if isinstance(value, tuple) and len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
        yield float(value[0]), float(value[1])
    elif isinstance(value, list):
        for item in value:
            yield from _numbers(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _numbers(item)


def geometry_bbox(geometry: dict[str, Any] | None) -> tuple[float, float, float, float] | None:
    points = list(_numbers(geometry or {}))
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def bbox_intersects(first: tuple[float, float, float, float] | None, second: tuple[float, float, float, float]) -> bool:
    if first is None:
        return False
    return first[2] >= second[0] and first[0] <= second[2] and first[3] >= second[1] and first[1] <= second[3]


def json_geometry(value: Any) -> Any:
    if isinstance(value, tuple):
        return [json_geometry(item) for item in value]
    if isinstance(value, list):
        return [json_geometry(item) for item in value]
    if isinstance(value, dict):
        return {key: json_geometry(item) for key, item in value.items()}
    return value


def load_pm_building_ids(path: Path) -> tuple[set[str], dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    records = payload.get("buildings") or []
    assignments: dict[str, str] = {}
    for record in records:
        building_id = str(record.get("building_id") or record.get("id") or "").strip()
        pm_id = str(record.get("pm_id") or "").strip()
        if building_id and pm_id and pm_id.lower() not in {"none", "null"}:
            assignments[building_id] = pm_id
    if not assignments:
        raise ValueError(f"No building records with pm_id were found in {path}")
    return set(assignments), assignments


def read_layer(connection: sqlite3.Connection, layer: str) -> tuple[list[str], list[dict[str, Any]]]:
    cursor = connection.execute(f'SELECT * FROM "{layer}" ORDER BY fid')
    names = [item[0] for item in cursor.description]
    rows = []
    for values in cursor:
        row = dict(zip(names, values))
        blob = row.get("geom")
        row["_geometry"] = parse_gpkg_geometry(blob) if blob else None
        rows.append(row)
    return names, rows


def feature_from_row(row: dict[str, Any], layer: str) -> dict[str, Any]:
    properties = {key: value for key, value in row.items() if not key.startswith("_") and key not in {"geom"}}
    if layer == "GEO_BUILDING":
        properties["id"] = properties.get("building_id")
    elif layer == "GEO_ROAD":
        properties["id"] = properties.get("road_id")
        # The recognition delivery marks construction attributes as unknown;
        # preserve the audited false/zero defaults instead of silently letting
        # task 6 assume that every candidate road is buildable.
        properties["open_cut_allowed"] = False
        properties["capacity"] = 0
        properties["construction_attributes_status"] = "unknown_default"
    elif layer == "GEO_COST_ZONE":
        properties["id"] = properties.get("zone_id")
        properties["cost_factor"] = properties.get("cost_multiplier")
    elif layer == "GEO_ACCESS_CANDIDATE":
        properties["id"] = properties.get("candidate_id")
    elif layer == "GEO_ROAD_NODE":
        properties["id"] = properties.get("node_id")
    elif layer == "GEO_BARRIER":
        properties["id"] = properties.get("barrier_id")
    elif layer == "GEO_GREEN":
        properties["id"] = properties.get("green_id")
    elif layer == "GEO_ADMIN":
        properties["id"] = properties.get("admin_id")
    return {"type": "Feature", "properties": properties, "geometry": json_geometry(row.get("_geometry"))}


def write_geojson(path: Path, features: list[dict[str, Any]]) -> None:
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def filter_package(source_gpkg: Path, output_dir: Path, pm_ids: set[str], region: tuple[float, float, float, float]) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cleaned_gpkg = output_dir / "regional_geo_context.gpkg"
    shutil.copy2(source_gpkg, cleaned_gpkg)
    selected_by_layer: dict[str, list[dict[str, Any]]] = {}
    selected_fids: dict[str, list[int]] = {}

    with sqlite3.connect(source_gpkg) as source_connection:
        for layer in LAYERS:
            _, rows = read_layer(source_connection, layer)
            selected: list[dict[str, Any]] = []
            for row in rows:
                if layer == "GEO_BUILDING":
                    keep = str(row.get("building_id") or "") in pm_ids and bbox_intersects(geometry_bbox(row.get("_geometry")), region)
                elif layer == "GEO_ACCESS_CANDIDATE":
                    keep = str(row.get("building_id") or "") in pm_ids and bbox_intersects(geometry_bbox(row.get("_geometry")), region)
                else:
                    keep = bbox_intersects(geometry_bbox(row.get("_geometry")), region)
                if keep:
                    selected.append(row)
            selected_by_layer[layer] = selected
            selected_fids[layer] = [int(row["fid"]) for row in selected]

    with sqlite3.connect(cleaned_gpkg) as connection:
        for layer, fids in selected_fids.items():
            if fids:
                placeholders = ",".join("?" for _ in fids)
                connection.execute(f'DELETE FROM "{layer}" WHERE fid NOT IN ({placeholders})', fids)
            else:
                connection.execute(f'DELETE FROM "{layer}"')
            extents = [geometry_bbox(row.get("_geometry")) for row in selected_by_layer[layer]]
            extents = [extent for extent in extents if extent]
            if extents:
                extent = (
                    min(item[0] for item in extents),
                    min(item[1] for item in extents),
                    max(item[2] for item in extents),
                    max(item[3] for item in extents),
                )
                connection.execute(
                    "UPDATE gpkg_contents SET min_x=?, min_y=?, max_x=?, max_y=?, last_change=? WHERE table_name=?",
                    (*extent, datetime.now(timezone.utc).isoformat(), layer),
                )
        connection.commit()
        connection.execute("VACUUM")

    # JSON/GeoJSON views used by task 6 and by reviewers without GIS software.
    write_geojson(output_dir / "task6_buildings.geojson", [feature_from_row(row, "GEO_BUILDING") for row in selected_by_layer["GEO_BUILDING"]])
    write_geojson(output_dir / "task6_roads.geojson", [feature_from_row(row, "GEO_ROAD") for row in selected_by_layer["GEO_ROAD"]])
    write_geojson(output_dir / "task6_cost_zones.geojson", [feature_from_row(row, "GEO_COST_ZONE") for row in selected_by_layer["GEO_COST_ZONE"]])

    source_json = source_gpkg.with_name("trunk_roads.json")
    if source_json.exists():
        payload = json.loads(source_json.read_text(encoding="utf-8-sig"))
        road_ids = {str(row.get("road_id")) for row in selected_by_layer["GEO_ROAD"]}
        roads = [row for row in payload.get("trunk_roads", []) if str(row.get("id")) in road_ids]
        (output_dir / "trunk_roads.json").write_text(json.dumps({"trunk_roads": roads}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for name, layer, id_field in (
        ("task5_candidate_nodes.csv", "GEO_ROAD_NODE", "node_id"),
        ("task5_candidate_edges.csv", "GEO_ROAD", "road_id"),
    ):
        source_csv = source_gpkg.with_name(name)
        if not source_csv.exists():
            continue
        with source_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            fieldnames = reader.fieldnames or []
        if layer == "GEO_ROAD_NODE":
            allowed = {str(row.get(id_field)) for row in selected_by_layer[layer]}
            rows = [row for row in rows if str(row.get("node_id")) in allowed]
        else:
            allowed = {str(row.get(id_field)) for row in selected_by_layer[layer]}
            rows = [row for row in rows if str(row.get("edge_id")) in allowed]
        with (output_dir / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    constraints_path = source_gpkg.with_name("planning_constraints.json")
    if constraints_path.exists():
        constraints = json.loads(constraints_path.read_text(encoding="utf-8-sig"))
        barrier_ids = {str(row.get("barrier_id")) for row in selected_by_layer["GEO_BARRIER"]}
        zone_ids = {str(row.get("zone_id")) for row in selected_by_layer["GEO_COST_ZONE"]}
        constraints["barriers"] = [row for row in constraints.get("barriers", []) if str(row.get("barrier_id")) in barrier_ids]
        constraints["no_build_and_high_cost_zones"] = [row for row in constraints.get("no_build_and_high_cost_zones", []) if str(row.get("zone_id")) in zone_ids]
        constraints["building_access_candidates"] = [row for row in constraints.get("building_access_candidates", []) if str(row.get("building_id")) in pm_ids]
        roads = selected_by_layer["GEO_ROAD"]
        nodes = selected_by_layer["GEO_ROAD_NODE"]
        zones = selected_by_layer["GEO_COST_ZONE"]
        parent = {str(row.get("node_id")): str(row.get("node_id")) for row in nodes}

        def find(node_id: str) -> str:
            while parent.get(node_id, node_id) != node_id:
                parent[node_id] = parent[parent[node_id]]
                node_id = parent[node_id]
            return node_id

        for road in roads:
            first = str(road.get("from_node") or "")
            second = str(road.get("to_node") or "")
            if first in parent and second in parent:
                root_first, root_second = find(first), find(second)
                if root_first != root_second:
                    parent[root_second] = root_first
        component_count = len({find(node_id) for node_id in parent}) if parent else 0
        constraints["road_topology_summary"] = {
            **constraints.get("road_topology_summary", {}),
            "input_road_count": len(roads),
            "source_road_count": len(roads),
            "input_segment_count": len(roads),
            "node_count": len(nodes),
            "edge_count": len(roads),
            "intersection_node_count": sum(str(row.get("node_type") or "").lower() == "intersection" for row in nodes),
            "endpoint_node_count": sum(str(row.get("node_type") or "").lower() == "endpoint" for row in nodes),
            "connected_component_count": component_count,
            "total_length_m": round(sum(float(row.get("length_m") or 0.0) for row in roads), 3),
        }
        constraints["access_candidate_summary"] = {
            **constraints.get("access_candidate_summary", {}),
            "building_count": len(pm_ids),
            "candidate_count": len(constraints["building_access_candidates"]),
            "building_without_candidate_count": len(pm_ids) - len({str(row.get("building_id")) for row in constraints["building_access_candidates"]}),
            "hard_barrier_crossing_count": 0,
        }
        constraints["constraint_summary"] = {
            **constraints.get("constraint_summary", {}),
            "barrier_count": len(constraints["barriers"]),
            "cost_zone_count": len(zones),
            "hard_barrier_count": sum(int(row.get("hard_constraint") or 0) for row in constraints["barriers"]),
            "prohibited_zone_count": sum(int(row.get("prohibited") or 0) for row in constraints["no_build_and_high_cost_zones"]),
            "high_cost_zone_count": sum(not int(row.get("prohibited") or 0) for row in constraints["no_build_and_high_cost_zones"]),
        }
        if isinstance(constraints.get("data_quality"), dict):
            constraints["data_quality"]["layer_counts"] = {layer: len(rows) for layer, rows in selected_by_layer.items()}
        constraints["cleaning"] = {"bbox_xy": list(region), "pm_building_count": len(pm_ids), "spatial_filter": "geometry_envelope_intersects"}
        (output_dir / "planning_constraints.json").write_text(json.dumps(constraints, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    areas_path = source_gpkg.with_name("planning_constraint_areas.geojson")
    if areas_path.exists():
        areas = json.loads(areas_path.read_text(encoding="utf-8-sig"))
        zone_ids = {str(row.get("zone_id")) for row in selected_by_layer["GEO_COST_ZONE"]}
        areas["features"] = [
            feature
            for feature in areas.get("features", [])
            if str((feature.get("properties") or {}).get("zone_id") or (feature.get("properties") or {}).get("code")) in zone_ids
        ]
        (output_dir / "planning_constraint_areas.geojson").write_text(json.dumps(areas, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "source_gpkg": str(source_gpkg),
        "cleaned_gpkg": str(cleaned_gpkg),
        "bbox_xy": list(region),
        "pm_building_ids": sorted(pm_ids),
        "counts": {layer: len(rows) for layer, rows in selected_by_layer.items()},
        "spatial_filter": "geometry_envelope_intersects",
        "building_filter": "pm_id_present_and_bbox_intersects",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean recognition data for PM-assigned buildings and a geographic rectangle.")
    parser.add_argument("--source-gpkg", type=Path, default=DEFAULT_GPKG)
    parser.add_argument("--pm-buildings-json", type=Path, default=DEFAULT_PM_BUILDINGS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--north-lat", type=float, default=DEFAULT_CORNERS[0][0])
    parser.add_argument("--west-lon", type=float, default=DEFAULT_CORNERS[0][1])
    parser.add_argument("--south-lat", type=float, default=DEFAULT_CORNERS[1][0])
    parser.add_argument("--east-lon", type=float, default=DEFAULT_CORNERS[1][1])
    args = parser.parse_args()
    min_x, max_x = sorted((args.west_lon, args.east_lon))
    min_y, max_y = sorted((args.south_lat, args.north_lat))
    region = (min_x, min_y, max_x, max_y)
    pm_ids, assignments = load_pm_building_ids(args.pm_buildings_json)
    result = filter_package(args.source_gpkg.resolve(), args.output_dir.resolve(), pm_ids, region)
    result["pm_assignments"] = assignments
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    (args.output_dir / "cleaning_manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
