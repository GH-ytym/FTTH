import argparse
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from project_paths import TASK2_ZONE_PARTITION_OUTPUT_DIR

from znro_zpm_partition import (
    EPS,
    capacity_constrained_agglomerative,
    dump_json,
    edge_points,
    extract_imb_nodes,
    load_json,
    node_type,
    normalize_distance_matrix,
    path_length,
    pipe_distance_matrix,
    point_distance,
    port_count,
    sorted_cluster_states,
    uses_geographic_coordinates,
    visualize,
    znro_merge_groups,
)


PETCH_TYPES = {"PETCH", "PTECH"}


def is_imb_only_dataset(data):
    nodes = data.get("nodes", [])
    return bool(nodes) and all(node_type(node) == "IMB" for node in nodes)


def build_petch_atomic_units(data, node_map):
    """Build indivisible IMB groups from their directly connected PETCH anchors."""
    imb_to_candidates = defaultdict(list)
    geographic_coordinates = uses_geographic_coordinates(data)
    for edge in data.get("edges", []):
        source = edge["source"]
        target = edge["target"]
        source_type = node_type(node_map[source])
        target_type = node_type(node_map[target])
        if source_type == "IMB" and target_type in PETCH_TYPES:
            imb_to_candidates[source].append(
                (target, path_length(edge, node_map, geographic_coordinates))
            )
        elif target_type == "IMB" and source_type in PETCH_TYPES:
            imb_to_candidates[target].append(
                (source, path_length(edge, node_map, geographic_coordinates))
            )

    groups = defaultdict(list)
    anchors = {}
    ambiguous_attachments = []
    orphan_imbs = []
    for imb in extract_imb_nodes(data):
        candidates = sorted(imb_to_candidates[imb["id"]], key=lambda item: (item[1], item[0]))
        if not candidates:
            group_id = f"ORPHAN::{imb['id']}"
            groups[group_id].append(imb)
            anchors[group_id] = imb
            orphan_imbs.append(imb["id"])
            continue

        petch_id = candidates[0][0]
        groups[petch_id].append(imb)
        anchors[petch_id] = node_map[petch_id]
        candidate_ids = sorted({candidate[0] for candidate in candidates})
        if len(candidate_ids) > 1:
            ambiguous_attachments.append(
                {
                    "imb_id": imb["id"],
                    "candidate_petch_ids": candidate_ids,
                    "selected_petch_id": petch_id,
                    "selected_edge_length": round(candidates[0][1], 3),
                }
            )

    units = []
    anchor_nodes = []
    for group_id in sorted(groups):
        members = sorted(groups[group_id], key=lambda node: node["id"])
        total_ports = sum(port_count(member) for member in members)
        if total_ports <= 0:
            raise ValueError(f"PETCH atomic group {group_id} has non-positive port count.")
        anchor = anchors[group_id]
        units.append(
            {
                "id": f"PETCH_GROUP::{group_id}",
                "x": anchor["x"],
                "y": anchor["y"],
                "nb_loc_tot": total_ports,
                "anchor_id": anchor["id"],
                "petch_id": None if group_id.startswith("ORPHAN::") else group_id,
                "imb_members": members,
            }
        )
        anchor_nodes.append(anchor)

    return units, anchor_nodes, {
        "atomic_unit_count": len(units),
        "direct_petch_group_count": sum(1 for unit in units if unit["petch_id"] is not None),
        "orphan_imb_ids": orphan_imbs,
        "ambiguous_attachments": ambiguous_attachments,
        "max_atomic_group_ports": max(unit["nb_loc_tot"] for unit in units),
        "atomic_group_size_distribution": dict(
            sorted(Counter(len(unit["imb_members"]) for unit in units).items())
        ),
    }


def spatial_distance_matrix(anchor_nodes, geographic_coordinates):
    count = len(anchor_nodes)
    matrix = [[0.0 for _ in range(count)] for _ in range(count)]
    for left in range(count):
        for right in range(left + 1, count):
            distance = point_distance(
                anchor_nodes[left], anchor_nodes[right], geographic_coordinates
            )
            matrix[left][right] = distance
            matrix[right][left] = distance
    return matrix


def combined_distance_matrix(spatial_matrix, pipe_matrix, spatial_weight, pipe_weight):
    normalized_spatial, spatial_scale = normalize_distance_matrix(spatial_matrix)
    normalized_pipe, pipe_scale = normalize_distance_matrix(pipe_matrix)
    total_weight = spatial_weight + pipe_weight
    if total_weight <= EPS:
        raise ValueError("At least one of spatial_weight or pipe_weight must be positive.")
    count = len(spatial_matrix)
    matrix = [
        [
            (spatial_weight * normalized_spatial[left][right]
             + pipe_weight * normalized_pipe[left][right])
            / total_weight
            for right in range(count)
        ]
        for left in range(count)
    ]
    return matrix, spatial_scale, pipe_scale


def cluster_centroid(cluster, units):
    members = [imb for unit_index in cluster.members for imb in units[unit_index]["imb_members"]]
    return {
        "x": round(sum(float(member["x"]) for member in members) / len(members), 6),
        "y": round(sum(float(member["y"]) for member in members) / len(members), 6),
    }


def format_atomic_partition(
    name,
    distance_mode,
    partition,
    units,
    capacity_limit,
    allow_extra_clusters,
    parent_field_name=None,
):
    clusters = []
    imb_assignment = {}
    unit_assignment = {}
    for display_index, cluster in enumerate(sorted_cluster_states(partition["clusters"], units), start=1):
        cluster_id = f"{name}_{display_index:03d}"
        members = []
        petch_ids = []
        for unit_index in sorted(cluster.members, key=lambda index: units[index]["id"]):
            unit = units[unit_index]
            unit_assignment[unit["id"]] = cluster_id
            if unit["petch_id"] is not None:
                petch_ids.append(unit["petch_id"])
            for imb in unit["imb_members"]:
                imb_assignment[imb["id"]] = cluster_id
                members.append(
                    {
                        "id": imb["id"],
                        "x": imb["x"],
                        "y": imb["y"],
                        "ports": port_count(imb),
                        "petch_id": unit["petch_id"],
                        "atomic_unit_id": unit["id"],
                    }
                )
        members.sort(key=lambda member: (float(member["y"]), float(member["x"]), member["id"]))
        record = {
            "id": cluster_id,
            "internal_key": cluster.key,
            "member_count": len(members),
            "atomic_group_count": len(cluster.members),
            "petch_ids": sorted(petch_ids),
            "total_ports": cluster.total_ports,
            "capacity_limit": capacity_limit,
            "capacity_utilization": round(cluster.total_ports / capacity_limit, 4),
            "centroid": cluster_centroid(cluster, units),
            "members": members,
        }
        if parent_field_name and cluster.merge_group is not None:
            record[parent_field_name] = cluster.merge_group
        clusters.append(record)

    all_imbs = [imb for unit in units for imb in unit["imb_members"]]
    assigned_ids = [member["id"] for cluster in clusters for member in cluster["members"]]
    counts = Counter(assigned_ids)
    expected_ids = {imb["id"] for imb in all_imbs}
    missing = sorted(expected_ids - set(assigned_ids))
    duplicates = sorted(imb_id for imb_id, count in counts.items() if count > 1)
    capacity_violations = [
        {"cluster_id": cluster["id"], "total_ports": cluster["total_ports"]}
        for cluster in clusters
        if cluster["total_ports"] > capacity_limit
    ]
    validation = {
        "full_coverage": not missing,
        "unique_assignment": not duplicates,
        "non_overlapping_by_imb": not duplicates,
        "capacity_ok": not capacity_violations,
        "cluster_count_ok": len(clusters) <= partition["target_cluster_num"] + allow_extra_clusters,
        "expected_imb_count": len(all_imbs),
        "covered_imb_count": len(set(assigned_ids)),
        "missing_imb_ids": missing,
        "duplicate_imb_ids": duplicates,
        "capacity_violations": capacity_violations,
        "allowed_cluster_num": partition["target_cluster_num"] + allow_extra_clusters,
    }
    return {
        "name": name,
        "distance_mode": distance_mode,
        "target_cluster_num": partition["target_cluster_num"],
        "actual_cluster_num": len(clusters),
        "total_ports": partition["total_ports"],
        "capacity_limit": capacity_limit,
        "stop_reason": partition["stop_reason"],
        "merge_count": len(partition["merges"]),
        "cluster_reduction_count": len(partition["cluster_reductions"]),
        "cluster_reductions": partition["cluster_reductions"],
        "refinement_move_count": len(partition["refinement_moves"]),
        "validation": validation,
        "assignment": imb_assignment,
        "atomic_unit_assignment": unit_assignment,
        "clusters": clusters,
    }


def validate_petch_atomicity(units, zpm_partition):
    violations = []
    assignment = zpm_partition["assignment"]
    for unit in units:
        if unit["petch_id"] is None:
            continue
        assigned_zpms = sorted({assignment[imb["id"]] for imb in unit["imb_members"]})
        if len(assigned_zpms) != 1:
            violations.append(
                {
                    "petch_id": unit["petch_id"],
                    "imb_ids": [imb["id"] for imb in unit["imb_members"]],
                    "zpm_ids": assigned_zpms,
                }
            )
    zpm_partition["validation"]["petch_atomicity_ok"] = not violations
    zpm_partition["validation"]["petch_atomicity_violations"] = violations


def validate_zpm_nesting(zpm_partition, znro_partition):
    znro_assignment = znro_partition["assignment"]
    violations = []
    for cluster in zpm_partition["clusters"]:
        member_znros = {znro_assignment[member["id"]] for member in cluster["members"]}
        if member_znros != {cluster.get("parent_znro_id")}:
            violations.append(
                {
                    "zpm_id": cluster["id"],
                    "parent_znro_id": cluster.get("parent_znro_id"),
                    "member_znro_ids": sorted(member_znros),
                }
            )
    zpm_partition["validation"]["nested_in_znro"] = not violations
    zpm_partition["validation"]["znro_nesting_violations"] = violations


def enforce_constraints(partition, name, require_nesting=False, require_petch_atomicity=False):
    validation = partition["validation"]
    required = ["full_coverage", "unique_assignment", "non_overlapping_by_imb", "capacity_ok"]
    if require_nesting:
        required.append("nested_in_znro")
    if require_petch_atomicity:
        required.append("petch_atomicity_ok")
    failed = [key for key in required if not validation.get(key, False)]
    if failed:
        raise ValueError(f"{name} constraints failed: {failed}")


def build_result(args):
    data = load_json(args.input_json)
    node_map = {node["id"]: node for node in data.get("nodes", [])}
    imb_only_mode = is_imb_only_dataset(data)
    units, anchor_nodes, atomic_summary = build_petch_atomic_units(data, node_map)
    over_capacity_units = [
        {"unit_id": unit["id"], "ports": unit["nb_loc_tot"]}
        for unit in units
        if unit["nb_loc_tot"] > args.zpm_max_cap
    ]
    if over_capacity_units:
        raise ValueError(f"PETCH atomic groups exceed ZPM capacity: {over_capacity_units}")

    geographic_coordinates = uses_geographic_coordinates(data)
    if imb_only_mode:
        spatial_mode = "geodesic_imb" if geographic_coordinates else "euclidean_imb"
    else:
        spatial_mode = "geodesic_petch_anchor" if geographic_coordinates else "euclidean_petch_anchor"
    spatial_matrix = spatial_distance_matrix(anchor_nodes, geographic_coordinates)
    normalized_spatial, spatial_scale = normalize_distance_matrix(spatial_matrix)
    if imb_only_mode:
        pipe_matrix = [[0.0 for _ in anchor_nodes] for _ in anchor_nodes]
        weighted_matrix = normalized_spatial
        pipe_scale = 0.0
        disconnected_pairs = 0
        synthetic_attachment_count = 0
        zpm_distance_mode = "spatial_only_imb"
        effective_spatial_weight = 1.0
        effective_pipe_weight = 0.0
    else:
        pipe_matrix, disconnected_pairs, synthetic_attachment_count = pipe_distance_matrix(
            data,
            anchor_nodes,
            node_map,
            args.unconnect_penalty,
        )
        weighted_matrix, spatial_scale, pipe_scale = combined_distance_matrix(
            spatial_matrix,
            pipe_matrix,
            args.spatial_weight,
            args.pipe_weight,
        )
        zpm_distance_mode = "weighted_petch_anchor_spatial_pipe"
        effective_spatial_weight = args.spatial_weight
        effective_pipe_weight = args.pipe_weight
    total_ports = sum(unit["nb_loc_tot"] for unit in units)
    znro_target = math.ceil(total_ports / args.nro_max_cap)
    znro_raw = capacity_constrained_agglomerative(
        units,
        spatial_matrix,
        args.nro_max_cap,
        target_cluster_num=znro_target,
        spatial_matrix=spatial_matrix,
        allow_refinement=True,
    )
    znro_labels, _ = znro_merge_groups(znro_raw["clusters"], units)
    zpm_target_from_capacity = math.ceil(total_ports / args.zpm_max_cap)
    zpm_target_from_znro = sum(
        math.ceil(cluster.total_ports / args.zpm_max_cap)
        for cluster in znro_raw["clusters"]
    )
    zpm_target = max(zpm_target_from_capacity, zpm_target_from_znro)
    zpm_raw = capacity_constrained_agglomerative(
        units,
        weighted_matrix,
        args.zpm_max_cap,
        target_cluster_num=zpm_target,
        merge_group_labels=znro_labels,
        minimize_cluster_count=True,
        spatial_matrix=normalized_spatial,
        allow_refinement=True,
    )

    znro = format_atomic_partition(
        "ZNRO", spatial_mode, znro_raw, units, args.nro_max_cap, args.allow_extra_clusters
    )
    zpm = format_atomic_partition(
        "ZPM",
        zpm_distance_mode,
        zpm_raw,
        units,
        args.zpm_max_cap,
        args.allow_extra_clusters,
        parent_field_name="parent_znro_id",
    )
    zpm["distance_weights"] = {
        "spatial_weight": effective_spatial_weight,
        "pipe_weight": effective_pipe_weight,
        "requested_spatial_weight": args.spatial_weight,
        "requested_pipe_weight": args.pipe_weight,
        "normalized_spatial_weight": round(
            effective_spatial_weight / (effective_spatial_weight + effective_pipe_weight),
            6,
        ),
        "normalized_pipe_weight": round(
            effective_pipe_weight / (effective_spatial_weight + effective_pipe_weight),
            6,
        ),
        "spatial_distance_scale": round(spatial_scale, 3),
        "pipe_network_distance_scale": round(pipe_scale, 3),
        "pipe_network_disabled": imb_only_mode,
    }
    validate_zpm_nesting(zpm, znro)
    validate_petch_atomicity(units, zpm)
    enforce_constraints(znro, "ZNRO")
    enforce_constraints(zpm, "ZPM", require_nesting=True, require_petch_atomicity=True)

    result = {
        "source_file": str(args.input_json),
        "algorithm": {
            "name": (
                "IMB-only spatial ZNRO/ZPM partitioning"
                if imb_only_mode
                else "PETCH-atomic ZNRO/ZPM partitioning"
            ),
            "input_mode": "imb_only_spatial" if imb_only_mode else "petch_atomic",
            "atomic_rule": (
                "Each IMB is an independent atomic unit because the input has no PETCH/PTECH layer."
                if imb_only_mode
                else "All IMBs directly connected to the same PETCH are one indivisible unit for both ZNRO and ZPM."
            ),
            "zpm_cluster_priority": "minimum feasible cluster count before distance refinement",
        },
        "parameters": {
            "nro_max_cap": args.nro_max_cap,
            "zpm_max_cap": args.zpm_max_cap,
            "spatial_weight": args.spatial_weight,
            "pipe_weight": args.pipe_weight,
            "allow_extra_clusters": args.allow_extra_clusters,
        },
        "input_summary": {
            "node_count": len(data.get("nodes", [])),
            "edge_count": len(data.get("edges", [])),
            "imb_count": sum(len(unit["imb_members"]) for unit in units),
            "total_ports": total_ports,
            "imb_only_layer": imb_only_mode,
            "spatial_distance_mode": spatial_mode,
            "pipe_disconnected_pair_count": disconnected_pairs,
            "synthetic_imb_attachment_count": synthetic_attachment_count,
        },
        "petch_atomicity": atomic_summary,
        "znro": znro,
        "zpm": zpm,
    }
    return data, result


def parse_args():
    output_dir = TASK2_ZONE_PARTITION_OUTPUT_DIR
    parser = argparse.ArgumentParser(
        description="PETCH-atomic ZNRO/ZPM partitioning for GIS-converted FTTH data."
    )
    parser.add_argument(
        "--input-json",
        type=Path,
        default=None,
        help="Source FTTH dataset JSON (required; no default is configured).",
    )
    parser.add_argument("--output-json", type=Path, default=output_dir / "qgis_petch_atomic_result.json")
    parser.add_argument("--output-fig", type=Path, default=output_dir / "qgis_petch_atomic_result.png")
    parser.add_argument("--nro-max-cap", type=int, default=2048)
    parser.add_argument("--zpm-max-cap", type=int, default=576)
    parser.add_argument("--spatial-weight", type=float, default=1.0)
    parser.add_argument("--pipe-weight", type=float, default=100.0)
    parser.add_argument("--allow-extra-clusters", type=int, default=1)
    parser.add_argument("--unconnect-penalty", type=float, default=100.0)
    args = parser.parse_args()
    if args.nro_max_cap <= 0 or args.zpm_max_cap <= 0:
        parser.error("Capacity limits must be positive.")
    if args.spatial_weight < 0 or args.pipe_weight < 0:
        parser.error("Distance weights must be non-negative.")
    if args.spatial_weight + args.pipe_weight <= EPS:
        parser.error("At least one distance weight must be positive.")
    return args


def main():
    args = parse_args()
    if args.input_json is None:
        raise SystemExit("--input-json is required; the task-2 data path is currently unset")
    data, result = build_result(args)
    dump_json(result, args.output_json)
    visualize(data, result["znro"], result["zpm"], args.output_fig)
    print(f"Input: {args.input_json.resolve()}")
    print(f"Result JSON: {args.output_json.resolve()}")
    print(f"Visualization: {args.output_fig.resolve()}")
    if result["input_summary"].get("imb_only_layer"):
        print("Input mode: IMB-only spatial clustering; pipe/PETCH distance is ignored.")
    print(
        f"PETCH atomic units={result['petch_atomicity']['atomic_unit_count']}, "
        f"size_distribution={result['petch_atomicity']['atomic_group_size_distribution']}"
    )
    print(
        f"ZNRO: target={result['znro']['target_cluster_num']}, "
        f"actual={result['znro']['actual_cluster_num']}, "
        f"valid={result['znro']['validation']}"
    )
    print(
        f"ZPM: target={result['zpm']['target_cluster_num']}, "
        f"actual={result['zpm']['actual_cluster_num']}, "
        f"valid={result['zpm']['validation']}"
    )


if __name__ == "__main__":
    main()
