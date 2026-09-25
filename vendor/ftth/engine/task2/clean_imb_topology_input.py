"""Create the IMB-only topology input used by task 2 partitioning.

The recognition layers are preserved for the optional building and entrance
stages.  Only the topology graph is reduced: PETCH/PTECH nodes and any edges
touching them are removed, so the partition algorithm deterministically enters
its IMB-only spatial mode.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def node_type(node: dict[str, Any]) -> str:
    return str(node.get("type") or node.get("node_type") or "").strip().upper()


def clean_imb_topology(payload: dict[str, Any]) -> dict[str, Any]:
    nodes = payload.get("nodes")
    edges = payload.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("Input JSON must contain list-valued nodes and edges.")

    imb_nodes = [node for node in nodes if node_type(node) == "IMB"]
    if not imb_nodes:
        raise ValueError("Input JSON contains no IMB nodes.")
    imb_ids = {str(node.get("id")) for node in imb_nodes}
    retained_edges = [
        edge
        for edge in edges
        if str(edge.get("source")) in imb_ids and str(edge.get("target")) in imb_ids
    ]

    result = dict(payload)
    result["nodes"] = imb_nodes
    result["edges"] = retained_edges

    meta = dict(payload.get("meta") or {})
    xs = [float(node["x"]) for node in imb_nodes]
    ys = [float(node["y"]) for node in imb_nodes]
    imb_bounds = {
        "min_x": min(xs),
        "min_y": min(ys),
        "max_x": max(xs),
        "max_y": max(ys),
    }
    meta.update(
        {
            "input_mode": "imb_only_spatial",
            "topology_cleaning": "PETCH/PTECH nodes and incident edges removed; IMB nodes retained",
            "original_node_count": len(nodes),
            "original_edge_count": len(edges),
            "removed_petch_node_count": len(nodes) - len(imb_nodes),
            "removed_incident_edge_count": len(edges) - len(retained_edges),
            "ptech_count": 0,
            "imb_count": len(imb_nodes),
            "bounds": imb_bounds,
            "width": imb_bounds["max_x"] - imb_bounds["min_x"],
            "height": imb_bounds["max_y"] - imb_bounds["min_y"],
        }
    )
    result["meta"] = meta

    validation = dict(payload.get("validation") or {})
    topology_validation = dict(validation.get("topology") or {})
    topology_validation.update(
        {
            "node_count": len(imb_nodes),
            "edge_count": len(retained_edges),
            "ptech_count": 0,
            "imb_count": len(imb_nodes),
            "total_imb_ports": sum(
                int(node.get("nb_loc_tot", node.get("ports", 0)) or 0)
                for node in imb_nodes
            ),
        }
    )
    validation["topology"] = topology_validation
    validation["topology_cleaning"] = {
        "mode": "imb_only_spatial",
        "retained_imb_count": len(imb_nodes),
        "retained_edge_count": len(retained_edges),
        "removed_petch_node_count": len(nodes) - len(imb_nodes),
        "removed_incident_edge_count": len(edges) - len(retained_edges),
        "all_retained_nodes_are_imb": all(node_type(node) == "IMB" for node in imb_nodes),
    }
    result["validation"] = validation
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Remove PETCH/PTECH topology nodes and keep IMB-only input.")
    parser.add_argument("--input-json", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = json.loads(args.input_json.read_text(encoding="utf-8-sig"))
    cleaned = clean_imb_topology(payload)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    cleaning = cleaned["validation"]["topology_cleaning"]
    print(
        f"IMB-only input: {args.output_json.resolve()} | "
        f"IMB={cleaning['retained_imb_count']}, edges={cleaning['retained_edge_count']}, "
        f"removed_petch={cleaning['removed_petch_node_count']}"
    )


if __name__ == "__main__":
    main()
