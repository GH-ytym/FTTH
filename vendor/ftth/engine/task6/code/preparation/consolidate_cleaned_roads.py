"""Remove entrance candidates and consolidate cleaned road sub-segments."""

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
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from project_paths import TASK6_RECOGNITION_OUTPUT_DIR, TASK6_WHOLE_ROADS_OUTPUT_DIR

DEFAULT_SOURCE = TASK6_RECOGNITION_OUTPUT_DIR
DEFAULT_OUTPUT = TASK6_WHOLE_ROADS_OUTPUT_DIR


def _load_cleaner_helpers():
    module_path = ROOT / "任务6" / "code" / "preparation" / "clean_recognition_data.py"
    spec = importlib.util.spec_from_file_location("recognition_cleaner", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.parse_gpkg_geometry, module.geometry_bbox, module.json_geometry


parse_gpkg_geometry, geometry_bbox, json_geometry = _load_cleaner_helpers()


def read_layer(connection: sqlite3.Connection, layer: str) -> list[dict[str, Any]]:
    cursor = connection.execute(f'SELECT * FROM "{layer}" ORDER BY fid')
    names = [item[0] for item in cursor.description]
    records = []
    for values in cursor:
        row = dict(zip(names, values))
        row["_geometry"] = parse_gpkg_geometry(row["geom"]) if row.get("geom") else None
        records.append(row)
    return records


def endpoint_key(value: Any) -> str:
    if value not in (None, ""):
        return str(value)
    return ""


def segment_points(row: dict[str, Any]) -> list[tuple[float, float]]:
    geometry = row.get("_geometry") or {}
    if geometry.get("type") != "LineString":
        return []
    return [tuple(point[:2]) for point in geometry.get("coordinates") or []]


def consolidate_group(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segments = []
    for row in rows:
        points = segment_points(row)
        if len(points) < 2:
            continue
        first = endpoint_key(row.get("from_node")) or repr(points[0])
        last = endpoint_key(row.get("to_node")) or repr(points[-1])
        segments.append({"row": row, "points": points, "first": first, "last": last})
    if not segments:
        return []

    adjacency: dict[str, list[int]] = {}
    for index, segment in enumerate(segments):
        adjacency.setdefault(segment["first"], []).append(index)
        adjacency.setdefault(segment["last"], []).append(index)

    remaining = set(range(len(segments)))
    chains: list[list[int]] = []
    while remaining:
        candidates = [index for index in remaining if len(adjacency[segments[index]["first"]]) != 2 or len(adjacency[segments[index]["last"]]) != 2]
        start = min(candidates or list(remaining), key=lambda index: str(segments[index]["row"].get("road_id")))
        current_segment = start
        current_node = segments[current_segment]["first"]
        if len(adjacency[current_node]) == 2 and not candidates:
            current_node = min(segments[current_segment]["first"], segments[current_segment]["last"])
        chain: list[int] = []
        while current_segment in remaining:
            remaining.remove(current_segment)
            segment = segments[current_segment]
            chain.append(current_segment)
            next_node = segment["last"] if current_node == segment["first"] else segment["first"]
            options = [index for index in adjacency[next_node] if index in remaining]
            if not options:
                break
            current_node = next_node
            current_segment = min(options, key=lambda index: str(segments[index]["row"].get("road_id")))
        chains.append(chain)

    output = []
    parent_id = str(rows[0].get("parent_road_id") or rows[0].get("road_id"))
    for part_index, chain in enumerate(chains, 1):
        points: list[tuple[float, float]] = []
        first_row = segments[chain[0]]["row"]
        current_node = segments[chain[0]]["first"]
        for segment_index in chain:
            segment = segments[segment_index]
            segment_points_value = segment["points"]
            if current_node != segment["first"]:
                segment_points_value = list(reversed(segment_points_value))
            if points and points[-1] == segment_points_value[0]:
                points.extend(segment_points_value[1:])
            else:
                points.extend(segment_points_value)
            current_node = segment["last"] if current_node == segment["first"] else segment["first"]
        record = {
            "id": parent_id if len(chains) == 1 else f"{parent_id}:part{part_index:03d}",
            "parent_road_id": parent_id,
            "name": first_row.get("name") or "",
            "highway": first_row.get("highway") or "",
            "road_class": first_row.get("road_class") or "",
            "surface": first_row.get("surface") or "",
            "bridge": first_row.get("bridge") or "",
            "tunnel": first_row.get("tunnel") or "",
            "oneway": first_row.get("oneway") or "",
            "length_m": round(sum(float(segments[index]["row"].get("length_m") or 0.0) for index in chain), 3),
            "segment_count": len(chain),
            "coordinates": [{"x": round(point[0], 9), "y": round(point[1], 9)} for point in points],
            # JSON consumers are case-sensitive while PowerShell and several
            # GIS readers are not.  Publish one canonical spelling per field;
            # source_* fields below preserve the original survey values.
            "open_cut_allowed": False,
            "duct_capacity": 0,
            "source": first_row.get("source") or "OSM",
            "needs_review": first_row.get("needs_review"),
            "construction_attributes_status": "unknown_default",
        }
        output.append(record)
    return output


def write_geojson(path: Path, records: list[dict[str, Any]]) -> None:
    features = []
    for record in records:
        properties = {key: value for key, value in record.items() if key != "coordinates"}
        geometry = {"type": "LineString", "coordinates": [[point["x"], point["y"]] for point in record["coordinates"]]}
        features.append({"type": "Feature", "properties": properties, "geometry": geometry})
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove entrance points and consolidate cleaned recognition roads.")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    source_dir = args.source_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source_gpkg = source_dir / "regional_geo_context.gpkg"
    output_gpkg = output_dir / "regional_geo_context.gpkg"
    shutil.copy2(source_gpkg, output_gpkg)

    with sqlite3.connect(source_gpkg) as source_connection:
        road_rows = read_layer(source_connection, "GEO_ROAD")
        all_rows = {layer: read_layer(source_connection, layer) for layer in ("GEO_BUILDING", "GEO_ROAD_NODE", "GEO_BARRIER", "GEO_GREEN", "GEO_ADMIN", "GEO_COST_ZONE")}

    consolidated: list[dict[str, Any]] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in road_rows:
        groups.setdefault(str(row.get("parent_road_id") or row.get("road_id")), []).append(row)
    for parent_id in sorted(groups):
        consolidated.extend(consolidate_group(groups[parent_id]))

    # Remove all entrance candidate rows from the copied GeoPackage. The
    # segmented GEO_ROAD table remains authoritative for GIS topology; whole
    # road records are exported separately below.
    with sqlite3.connect(output_gpkg) as connection:
        connection.execute('DELETE FROM "GEO_ACCESS_CANDIDATE"')
        connection.commit()
        connection.execute("VACUUM")

    for name in ("task6_buildings.geojson", "task6_cost_zones.geojson"):
        source = source_dir / name
        if source.exists():
            shutil.copy2(source, output_dir / name)
    write_geojson(output_dir / "task6_roads_whole.geojson", consolidated)
    (output_dir / "trunk_roads_whole.json").write_text(json.dumps({"trunk_roads": consolidated}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    parent_records = []
    for parent_id in sorted({record["parent_road_id"] for record in consolidated}):
        parts = [record for record in consolidated if record["parent_road_id"] == parent_id]
        first = parts[0]
        parent_records.append(
            {
                "id": parent_id,
                "parent_road_id": parent_id,
                "name": first.get("name", ""),
                "highway": first.get("highway", ""),
                "road_class": first.get("road_class", ""),
                "length_m": round(sum(float(part.get("length_m") or 0.0) for part in parts), 3),
                "segment_count": sum(int(part.get("segment_count") or 0) for part in parts),
                "part_count": len(parts),
                "parts": [part["coordinates"] for part in parts],
                "open_cut_allowed": False,
                "duct_capacity": 0,
                "construction_attributes_status": "unknown_default",
            }
        )
    (output_dir / "trunk_roads_parent.json").write_text(json.dumps({"trunk_roads": parent_records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (output_dir / "roads_whole.csv").open("w", encoding="utf-8", newline="") as handle:
        fields = ["id", "parent_road_id", "highway", "road_class", "length_m", "segment_count", "open_cut_allowed", "duct_capacity", "construction_attributes_status"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: record.get(field) for field in fields} for record in consolidated)

    # Preserve only building/group views; entrance point views are deliberately
    # omitted from this second-stage package.
    for name in ("task2_buildings.json", "task2_building_groups.json"):
        source = source_dir / name
        if source.exists():
            payload = json.loads(source.read_text(encoding="utf-8-sig"))
            if name == "task2_building_groups.json":
                for record in payload:
                    record.pop("entrance_ids", None)
                    record.pop("representative_entrance_id", None)
                (output_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            else:
                shutil.copy2(source, output_dir / name)

    constraints_source = source_dir / "planning_constraints.json"
    if constraints_source.exists():
        constraints = json.loads(constraints_source.read_text(encoding="utf-8-sig"))
        constraints["building_access_candidates"] = []
        constraints["access_candidate_summary"] = {**constraints.get("access_candidate_summary", {}), "candidate_count": 0, "building_count": 0, "building_without_candidate_count": 0, "hard_barrier_crossing_count": 0}
        constraints["cleaning"] = {**constraints.get("cleaning", {}), "entrance_points_removed": True, "road_output": "trunk_roads_whole.json/task6_roads_whole.geojson"}
        (output_dir / "planning_constraints.json").write_text(json.dumps(constraints, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(source_dir),
        "output_dir": str(output_dir),
        "entrance_points_removed": True,
        "removed_layer_feature_count": {"GEO_ACCESS_CANDIDATE": 6},
        "segmented_road_count": len(road_rows),
        "parent_road_count": len(groups),
        "whole_road_record_count": len(consolidated),
        "whole_road_grouping": "parent_road_id_with_contiguous_chain_split",
        "road_attributes_preserved": {"open_cut_allowed": False, "duct_capacity": 0, "status": "unknown_default"},
        "outputs": ["regional_geo_context.gpkg", "task6_roads_whole.geojson", "trunk_roads_whole.json", "trunk_roads_parent.json", "roads_whole.csv", "task6_buildings.geojson", "task6_cost_zones.geojson", "task2_buildings.json", "task2_building_groups.json", "planning_constraints.json"],
    }
    (output_dir / "cleaning_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
