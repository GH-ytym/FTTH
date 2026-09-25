import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from project_paths import TASK2_ZONE_PARTITION_OUTPUT_DIR

from znro_zpm_partition import dump_json, load_json, node_type, port_count


def coordinate_bounds(nodes):
    xs = [float(node["x"]) for node in nodes]
    ys = [float(node["y"]) for node in nodes]
    return {
        "min_x": round(min(xs), 9),
        "min_y": round(min(ys), 9),
        "max_x": round(max(xs), 9),
        "max_y": round(max(ys), 9),
    }


def build_imb_only_dataset(source_data, source_file=None):
    imbs = [
        {**node, "type": "IMB"}
        for node in source_data.get("nodes", [])
        if node_type(node) == "IMB"
    ]
    if not imbs:
        raise ValueError("Input JSON does not contain any IMB nodes.")

    for node in imbs:
        port_count(node)

    bounds = coordinate_bounds(imbs)
    meta = source_data.get("meta", {})
    output_meta = {
        "layout": "imb_only_layer",
        "source_file": str(source_file) if source_file else None,
        "source_layout": meta.get("layout"),
        "coordinate_system": meta.get("coordinate_system", "unknown"),
        "source_node_count": len(source_data.get("nodes", [])),
        "source_edge_count": len(source_data.get("edges", [])),
        "imb_count": len(imbs),
        "total_ports": sum(port_count(node) for node in imbs),
        "width": round(bounds["max_x"] - bounds["min_x"], 9),
        "height": round(bounds["max_y"] - bounds["min_y"], 9),
        "bounds": bounds,
        "note": "Only IMB layer nodes are kept. Clustering uses the existing NRO/ZPM capacity parameters.",
    }
    for key in ("zones", "cluster_boxes"):
        if key in meta:
            output_meta[key] = meta[key]

    return {
        "meta": output_meta,
        "nodes": sorted(imbs, key=lambda node: node["id"]),
        "edges": [],
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description="Create a source dataset that contains only the IMB layer."
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
        default=TASK2_ZONE_PARTITION_OUTPUT_DIR / "qgis_imb_only_dataset.json",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.input_json is None:
        raise SystemExit("--input-json is required; the task-2 data path is currently unset")
    data = build_imb_only_dataset(load_json(args.input_json), args.input_json)
    dump_json(data, args.output_json)
    print(f"Input JSON: {args.input_json.resolve()}")
    print(f"IMB-only JSON: {args.output_json.resolve()}")
    print(
        f"IMBs: {data['meta']['imb_count']}, "
        f"total_ports: {data['meta']['total_ports']}, edges: {len(data['edges'])}"
    )


if __name__ == "__main__":
    main()
