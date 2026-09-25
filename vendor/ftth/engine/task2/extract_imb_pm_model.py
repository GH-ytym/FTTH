"""Extract a compact IMB-PM model from GIS-converted FTTH network JSON.

The output contains only IMBs, PM/PETCH anchor nodes, and PM-to-PM pipe links.
Every IMB is explicitly attached to one direct PM anchor so the partitioning
algorithm can enforce that all IMBs under one PM remain in one ZPM.
"""

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from project_paths import TASK2_ZONE_PARTITION_OUTPUT_DIR


PETCH_TYPES = {"PETCH", "PTECH"}


def load_json(path):
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def dump_json(data, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def node_type(node):
    return str(node.get("type", "")).upper()


def port_count(node):
    if "nb_loc_tot" in node:
        return int(node["nb_loc_tot"])
    if "ports" in node:
        return int(node["ports"])
    raise ValueError(f"IMB {node.get('id')} is missing nb_loc_tot/ports.")


def edge_length(edge, node_map):
    if "length" in edge:
        return float(edge["length"])
    source = node_map[edge["source"]]
    target = node_map[edge["target"]]
    return math.hypot(float(source["x"]) - float(target["x"]), float(source["y"]) - float(target["y"]))


def select_pm_attachments(raw_data, node_map):
    """Attach each IMB to its shortest direct PETCH/PTECH edge."""
    candidates = defaultdict(list)
    for edge in raw_data.get("edges", []):
        source = edge["source"]
        target = edge["target"]
        source_type = node_type(node_map[source])
        target_type = node_type(node_map[target])
        length = edge_length(edge, node_map)
        if source_type == "IMB" and target_type in PETCH_TYPES:
            candidates[source].append((target, length))
        elif target_type == "IMB" and source_type in PETCH_TYPES:
            candidates[target].append((source, length))

    attachment = {}
    ambiguous = []
    missing = []
    for node in node_map.values():
        if node_type(node) != "IMB":
            continue
        options = sorted(candidates[node["id"]], key=lambda item: (item[1], item[0]))
        if not options:
            missing.append(node["id"])
            continue
        attachment[node["id"]] = options[0][0]
        distinct_pm_ids = sorted({pm_id for pm_id, _ in options})
        if len(distinct_pm_ids) > 1:
            ambiguous.append(
                {
                    "imb_id": node["id"],
                    "candidate_pm_ids": distinct_pm_ids,
                    "selected_pm_id": options[0][0],
                    "selected_edge_length": round(options[0][1], 3),
                }
            )
    if missing:
        raise ValueError(
            "Every IMB must have a direct PM/PETCH attachment; missing=" + str(missing)
        )
    return attachment, ambiguous


def build_model(raw_data):
    node_map = {node["id"]: node for node in raw_data.get("nodes", [])}
    attachments, ambiguous = select_pm_attachments(raw_data, node_map)
    pm_nodes = []
    imb_nodes = []
    pm_ids = set()

    for node in raw_data.get("nodes", []):
        if node_type(node) in PETCH_TYPES:
            pm_ids.add(node["id"])
            pm_nodes.append(
                {
                    "id": node["id"],
                    "code": node.get("code"),
                    "x": node["x"],
                    "y": node["y"],
                    "role": node.get("role"),
                }
            )
        elif node_type(node) == "IMB":
            imb_nodes.append(
                {
                    "id": node["id"],
                    "code": node.get("code"),
                    "x": node["x"],
                    "y": node["y"],
                    "ports": port_count(node),
                    "pm_id": attachments[node["id"]],
                }
            )

    pm_links = []
    for edge in raw_data.get("edges", []):
        source = edge["source"]
        target = edge["target"]
        if source not in pm_ids or target not in pm_ids:
            continue
        pm_links.append(
            {
                "source": source,
                "target": target,
                "length": round(edge_length(edge, node_map), 3),
                "code": edge.get("code"),
            }
        )

    if not pm_links:
        raise ValueError("No PM-to-PM pipe links found in the GIS network.")

    meta = raw_data.get("meta", {})
    return {
        "format": "imb_pm_partition_model/v1",
        "meta": {
            "coordinate_system": meta.get("coordinate_system", "unknown"),
            "bounds": meta.get("bounds"),
            "source_layout": meta.get("layout"),
        },
        "pm_nodes": sorted(pm_nodes, key=lambda node: node["id"]),
        "imb_nodes": sorted(imb_nodes, key=lambda node: node["id"]),
        "pm_links": pm_links,
        "extraction_summary": {
            "pm_count": len(pm_nodes),
            "imb_count": len(imb_nodes),
            "total_ports": sum(node["ports"] for node in imb_nodes),
            "pm_link_count": len(pm_links),
            "ambiguous_attachment_count": len(ambiguous),
            "ambiguous_attachments": ambiguous,
        },
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract IMB-PM model JSON from GIS-converted FTTH JSON."
    )
    parser.add_argument(
        "--input-json",
        type=Path,
        default=None,
        help="Source FTTH dataset JSON (required; no default is configured).",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=TASK2_ZONE_PARTITION_OUTPUT_DIR / "imb_pm_model.json",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.input_json is None:
        raise SystemExit("--input-json is required; the task-2 data path is currently unset")
    model = build_model(load_json(args.input_json))
    dump_json(model, args.output_json)
    print(f"Input GIS JSON: {args.input_json.resolve()}")
    print(f"IMB-PM model JSON: {args.output_json.resolve()}")
    print(f"Summary: {model['extraction_summary']}")


if __name__ == "__main__":
    main()
