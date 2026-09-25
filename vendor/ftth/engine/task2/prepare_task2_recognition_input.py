"""Build the single, auditable input JSON used by task 2.

Task 2 needs two kinds of data: the PTECH/IMB infrastructure graph used by
the ZNRO/ZPM algorithm, and recognised geographic features used by the
building-group and entrance stages.  Earlier deliveries kept the latter in a
set of GIS folders.  This command serialises the required layers into one
JSON document while keeping the established ``nodes``/``edges`` contract at
the document root, so existing partition scripts continue to work unchanged.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from task2_geometry_features import load_features


SRC_DIR = Path(__file__).resolve().parent
ZONE_DIR = SRC_DIR.parent
TASK2_DIR = ZONE_DIR.parent
ROOT_DIR = TASK2_DIR.parent

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
from project_paths import (
    RECOGNITION_BUILDING_INPUT_DIR,
    TASK1_TO_TASK2_TOPOLOGY,
    TASK2_RECOGNITION_INPUT,
)

DEFAULT_TOPOLOGY = TASK1_TO_TASK2_TOPOLOGY
DEFAULT_RECOGNITION_GPKG = RECOGNITION_BUILDING_INPUT_DIR / "regional_geo_context.gpkg"
DEFAULT_OUTPUT = TASK2_RECOGNITION_INPUT

RECOGNITION_LAYERS = {
    "building_groups": "GEO_BUILDING",
    "roads": "GEO_ROAD",
    "road_nodes": "GEO_ROAD_NODE",
    "barriers": "GEO_BARRIER",
    "cost_zones": "GEO_COST_ZONE",
    "access_candidates": "GEO_ACCESS_CANDIDATE",
}


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"Topology JSON must be an object: {path}")
    return payload


def relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR.resolve()))
    except ValueError:
        return str(path.resolve())


def coordinate_pairs(value: Any) -> list[tuple[float, float]]:
    if not isinstance(value, list):
        return []
    if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
        return [(float(value[0]), float(value[1]))]
    points: list[tuple[float, float]] = []
    for item in value:
        points.extend(coordinate_pairs(item))
    return points


def feature_bounds(features: list[dict[str, Any]]) -> dict[str, float] | None:
    points = [
        point
        for feature in features
        for point in coordinate_pairs((feature.get("geometry") or {}).get("coordinates"))
    ]
    if not points:
        return None
    xs, ys = zip(*points)
    return {
        "min_x": round(min(xs), 9),
        "min_y": round(min(ys), 9),
        "max_x": round(max(xs), 9),
        "max_y": round(max(ys), 9),
    }


def bounds_overlap(first: dict[str, float] | None, second: dict[str, float] | None) -> bool | None:
    if not first or not second:
        return None
    return not (
        first["max_x"] < second["min_x"]
        or second["max_x"] < first["min_x"]
        or first["max_y"] < second["min_y"]
        or second["max_y"] < first["min_y"]
    )


def bounds_cover(container: dict[str, float] | None, content: dict[str, float] | None) -> bool | None:
    if not container or not content:
        return None
    return (
        container["min_x"] <= content["min_x"]
        and container["min_y"] <= content["min_y"]
        and container["max_x"] >= content["max_x"]
        and container["max_y"] >= content["max_y"]
    )


def validate_topology(topology: dict[str, Any]) -> dict[str, Any]:
    nodes = topology.get("nodes")
    edges = topology.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("Topology JSON must contain list-valued nodes and edges.")

    node_ids = [str(node.get("id") or "") for node in nodes]
    if not all(node_ids) or len(node_ids) != len(set(node_ids)):
        raise ValueError("Topology nodes must have unique non-empty ids.")
    node_map = {str(node["id"]): node for node in nodes}

    missing_coordinates = [node_id for node_id, node in node_map.items() if node.get("x") is None or node.get("y") is None]
    if missing_coordinates:
        raise ValueError(f"Topology nodes missing x/y coordinates: {missing_coordinates[:5]}")
    missing_ports = [
        node_id
        for node_id, node in node_map.items()
        if str(node.get("type") or "").upper() == "IMB"
        and node.get("ports") is None
        and node.get("nb_loc_tot") is None
    ]
    if missing_ports:
        raise ValueError(f"IMB nodes missing ports or nb_loc_tot: {missing_ports[:5]}")
    unknown_edges = [
        edge
        for edge in edges
        if str(edge.get("source") or "") not in node_map or str(edge.get("target") or "") not in node_map
    ]
    if unknown_edges:
        raise ValueError(f"Topology edge has an unknown endpoint: {unknown_edges[0]}")

    imb_ports = sum(
        int(node.get("nb_loc_tot", node.get("ports", 0)))
        for node in nodes
        if str(node.get("type") or "").upper() == "IMB"
    )
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "ptech_count": sum(str(node.get("type") or "").upper() in {"PTECH", "PETCH"} for node in nodes),
        "imb_count": sum(str(node.get("type") or "").upper() == "IMB" for node in nodes),
        "total_imb_ports": imb_ports,
    }


def build_input(topology_path: Path, recognition_gpkg: Path) -> dict[str, Any]:
    topology = read_json(topology_path)
    topology_summary = validate_topology(topology)
    recognition = {
        key: load_features(recognition_gpkg, layer)
        for key, layer in RECOGNITION_LAYERS.items()
    }
    topology_bounds = (topology.get("meta") or {}).get("bounds")
    recognition_bounds = feature_bounds(recognition["building_groups"])
    overlaps = bounds_overlap(topology_bounds, recognition_bounds)
    topology_covers_recognition = bounds_cover(topology_bounds, recognition_bounds)
    scope_status = "READY" if topology_covers_recognition else "REVIEW_REQUIRED"

    meta = dict(topology.get("meta") or {})
    meta.update(
        {
            "schema_version": "task2-recognition-input-1.0",
            "prepared_at": datetime.now(timezone.utc).isoformat(),
            "topology_source": relative_path(topology_path),
            "recognition_source": relative_path(recognition_gpkg),
            "recognition_coordinate_system": "EPSG:4326",
            "recognition_calculation_crs": "EPSG:32629",
        }
    )
    payload = {
        "schema_version": "task2-recognition-input-1.0",
        "meta": meta,
        "nodes": topology["nodes"],
        "edges": topology["edges"],
        "recognition": recognition,
        "validation": {
            "status": scope_status,
            "topology": topology_summary,
            "recognition_layer_counts": {key: len(records) for key, records in recognition.items()},
            "topology_bounds": topology_bounds,
            "recognition_building_bounds": recognition_bounds,
            "topology_and_recognition_bounds_overlap": overlaps,
            "topology_covers_recognition_extent": topology_covers_recognition,
            "scope_note": (
                "The graph and recognised buildings overlap spatially. Confirm field attributes before construction use."
                if overlaps
                else "The graph and recognised buildings do not overlap. Rebuild PTECH/IMB/INFRASTRUCTURE for the recognition extent before using geometry results as production assignments."
            ),
        },
    }
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create the single task-2 input JSON from the FTTH topology and recognition GeoPackage."
    )
    parser.add_argument("--topology-json", type=Path, default=DEFAULT_TOPOLOGY)
    parser.add_argument("--recognition-gpkg", type=Path, default=DEFAULT_RECOGNITION_GPKG)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_input(args.topology_json.resolve(), args.recognition_gpkg.resolve())
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    validation = payload["validation"]
    print(f"Task-2 input JSON: {args.output_json.resolve()}")
    print(f"Topology: {validation['topology']['node_count']} nodes, {validation['topology']['edge_count']} edges")
    print(f"Recognition layers: {validation['recognition_layer_counts']}")
    print(f"Validation status: {validation['status']}")


if __name__ == "__main__":
    main()
