import argparse
import heapq
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from project_paths import TASK2_ZONE_PARTITION_OUTPUT_DIR

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


EPS = 1e-9
DEFAULT_UNCONNECT_PENALTY = 100.0
EARTH_RADIUS_METERS = 6_371_008.8
DEFAULT_REDUCTION_DISTANCE_WEIGHT = 1.0
DEFAULT_REDUCTION_SPATIAL_PENALTY_WEIGHT = 0.35
DEFAULT_REDUCTION_BALANCE_PENALTY_WEIGHT = 0.25
DEFAULT_REDUCTION_MAX_STATES = 500_000
PETCH_TYPES = {"PETCH", "PTECH"}


@dataclass
class ClusterState:
    key: int
    members: list
    total_ports: int
    merge_group: str = None


def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(data, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def node_type(node):
    return str(node.get("type", "")).upper()


def is_imb_only_dataset(data):
    nodes = data.get("nodes", [])
    return bool(nodes) and all(node_type(node) == "IMB" for node in nodes)


def port_count(node):
    if "nb_loc_tot" in node:
        return int(node["nb_loc_tot"])
    if "ports" in node:
        return int(node["ports"])
    raise ValueError(f"IMB node {node.get('id')} is missing ports/nb_loc_tot.")


def geographic_distance(x0, y0, x1, y1):
    longitude_delta = math.radians(float(x1) - float(x0))
    latitude_delta = math.radians(float(y1) - float(y0))
    latitude0 = math.radians(float(y0))
    latitude1 = math.radians(float(y1))
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(latitude0) * math.cos(latitude1) * math.sin(longitude_delta / 2) ** 2
    )
    return 2 * EARTH_RADIUS_METERS * math.asin(math.sqrt(haversine))


def point_distance(a, b, geographic_coordinates=False):
    if geographic_coordinates:
        return geographic_distance(a["x"], a["y"], b["x"], b["y"])
    return math.hypot(float(a["x"]) - float(b["x"]), float(a["y"]) - float(b["y"]))


def coordinates_distance(a, b, geographic_coordinates=False):
    if geographic_coordinates:
        return geographic_distance(a[0], a[1], b[0], b[1])
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def uses_geographic_coordinates(data):
    return data.get("meta", {}).get("coordinate_system") == "source_shapefile_coordinates"


def path_length(edge, node_map, geographic_coordinates=False):
    if "length" in edge:
        return float(edge["length"])
    source = node_map[edge["source"]]
    target = node_map[edge["target"]]
    points = edge.get("path") or [[source["x"], source["y"]], [target["x"], target["y"]]]
    total = 0.0
    for i in range(1, len(points)):
        total += coordinates_distance(points[i - 1], points[i], geographic_coordinates)
    return total


def edge_points(edge, node_map):
    if edge.get("path"):
        return edge["path"]
    source = node_map[edge["source"]]
    target = node_map[edge["target"]]
    return [[source["x"], source["y"]], [target["x"], target["y"]]]


def extract_imb_nodes(data):
    imbs = [node for node in data.get("nodes", []) if node_type(node) == "IMB"]
    if not imbs:
        raise ValueError("No IMB nodes found in input JSON.")
    for node in imbs:
        port_count(node)
    return imbs


def euclidean_distance_matrix(imbs, geographic_coordinates=False):
    count = len(imbs)
    matrix = [[0.0 for _ in range(count)] for _ in range(count)]
    for i in range(count):
        for j in range(i + 1, count):
            distance = point_distance(imbs[i], imbs[j], geographic_coordinates)
            matrix[i][j] = distance
            matrix[j][i] = distance
    return matrix


def build_graph(data, node_map):
    adjacency = defaultdict(list)
    geographic_coordinates = uses_geographic_coordinates(data)
    for node in data.get("nodes", []):
        adjacency[node["id"]]
    for edge in data.get("edges", []):
        source = edge["source"]
        target = edge["target"]
        if source not in node_map or target not in node_map:
            raise ValueError(f"Edge has unknown endpoint: {source} -> {target}")
        weight = path_length(edge, node_map, geographic_coordinates)
        adjacency[source].append((target, weight))
        adjacency[target].append((source, weight))

    synthetic_attachment_count = 0
    petch_nodes = [node for node in node_map.values() if node_type(node) in PETCH_TYPES]
    for node in node_map.values():
        if node_type(node) != "IMB" or adjacency[node["id"]]:
            continue
        if not petch_nodes:
            continue
        nearest = min(
            petch_nodes,
            key=lambda candidate: point_distance(node, candidate, geographic_coordinates),
        )
        weight = point_distance(node, nearest, geographic_coordinates)
        adjacency[node["id"]].append((nearest["id"], weight))
        adjacency[nearest["id"]].append((node["id"], weight))
        synthetic_attachment_count += 1
    return adjacency, synthetic_attachment_count


def dijkstra(adjacency, source):
    distances = {source: 0.0}
    queue = [(0.0, source)]
    while queue:
        current_distance, current = heapq.heappop(queue)
        if current_distance != distances[current]:
            continue
        for neighbor, weight in adjacency[current]:
            candidate = current_distance + weight
            if candidate < distances.get(neighbor, float("inf")):
                distances[neighbor] = candidate
                heapq.heappush(queue, (candidate, neighbor))
    return distances


def pipe_distance_matrix(data, imbs, node_map, unconnect_penalty):
    adjacency, synthetic_attachment_count = build_graph(data, node_map)
    count = len(imbs)
    matrix = [[0.0 for _ in range(count)] for _ in range(count)]
    max_finite = 0.0
    all_distances = []
    for source in imbs:
        distances = dijkstra(adjacency, source["id"])
        all_distances.append(distances)
        for target in imbs:
            value = distances.get(target["id"], float("inf"))
            if math.isfinite(value):
                max_finite = max(max_finite, value)
    penalty = max(max_finite, 1.0) * unconnect_penalty
    disconnected_pairs = 0
    imb_index = {node["id"]: index for index, node in enumerate(imbs)}
    for source_index, distances in enumerate(all_distances):
        for target in imbs:
            target_index = imb_index[target["id"]]
            value = distances.get(target["id"], float("inf"))
            if not math.isfinite(value):
                value = penalty
                disconnected_pairs += 1
            matrix[source_index][target_index] = value
    return matrix, disconnected_pairs, synthetic_attachment_count


def normalize_distance_matrix(matrix):
    scale = max(max(row) for row in matrix)
    if scale <= EPS:
        scale = 1.0
    normalized = [[value / scale for value in row] for row in matrix]
    return normalized, scale


def weighted_distance_matrix(spatial_matrix, pipe_matrix, spatial_weight):
    normalized_spatial, spatial_scale = normalize_distance_matrix(spatial_matrix)
    normalized_pipe, pipe_scale = normalize_distance_matrix(pipe_matrix)
    pipe_weight = 1.0 - spatial_weight
    count = len(spatial_matrix)
    matrix = [
        [
            spatial_weight * normalized_spatial[i][j]
            + pipe_weight * normalized_pipe[i][j]
            for j in range(count)
        ]
        for i in range(count)
    ]
    return matrix, spatial_scale, pipe_scale


def centroid(members, imbs):
    if not members:
        return {"x": None, "y": None}
    x = sum(float(imbs[index]["x"]) for index in members) / len(members)
    y = sum(float(imbs[index]["y"]) for index in members) / len(members)
    return {"x": round(x, 3), "y": round(y, 3)}


def cluster_average_distance(member_index, cluster_members, matrix):
    others = [index for index in cluster_members if index != member_index]
    if not others:
        return 0.0
    return sum(matrix[member_index][other] for other in others) / len(others)


def refine_enclaves(clusters, imbs, ports, matrix, capacity_limit, ratio, max_iterations):
    moves = []
    for iteration in range(max_iterations):
        moved = False
        ordered_clusters = sorted(clusters.values(), key=lambda item: item.key)
        for source_cluster in ordered_clusters:
            if source_cluster.key not in clusters or len(source_cluster.members) <= 1:
                continue
            for member in list(source_cluster.members):
                current_distance = cluster_average_distance(member, source_cluster.members, matrix)
                if current_distance <= EPS:
                    continue
                best_target = None
                best_distance = current_distance
                for target_cluster in sorted(clusters.values(), key=lambda item: item.key):
                    if target_cluster.key == source_cluster.key:
                        continue
                    if (
                        source_cluster.merge_group is not None
                        and target_cluster.merge_group != source_cluster.merge_group
                    ):
                        continue
                    if target_cluster.total_ports + ports[member] > capacity_limit:
                        continue
                    candidate_distance = (
                        sum(matrix[member][other] for other in target_cluster.members)
                        / len(target_cluster.members)
                    )
                    if candidate_distance + EPS < best_distance * ratio:
                        best_distance = candidate_distance
                        best_target = target_cluster
                if best_target is None:
                    continue
                source_cluster.members.remove(member)
                source_cluster.total_ports -= ports[member]
                best_target.members.append(member)
                best_target.total_ports += ports[member]
                moves.append(
                    {
                        "iteration": iteration + 1,
                        "imb_id": imbs[member]["id"],
                        "from_cluster_key": source_cluster.key,
                        "to_cluster_key": best_target.key,
                        "ports": ports[member],
                        "distance_before": round(current_distance, 3),
                        "distance_after": round(best_distance, 3),
                    }
                )
                moved = True
        if not moved:
            break
    return moves


def member_to_cluster_distance(member, cluster, matrix):
    return sum(matrix[member][other] for other in cluster.members) / len(cluster.members)


def cluster_cross_distance(left, right, matrix, ports):
    weighted_total = 0.0
    weight_sum = 0
    for left_member in left.members:
        for right_member in right.members:
            weight = ports[left_member] * ports[right_member]
            weighted_total += weight * matrix[left_member][right_member]
            weight_sum += weight
    return weighted_total / weight_sum if weight_sum else 0.0


def post_reduction_balance_penalty(clusters, capacity_limit, removed_key, added_ports):
    utilizations = []
    for cluster in clusters.values():
        if cluster.key == removed_key:
            continue
        load = cluster.total_ports + added_ports.get(cluster.key, 0)
        utilizations.append(load / capacity_limit)
    if not utilizations:
        return 0.0
    mean_utilization = sum(utilizations) / len(utilizations)
    return sum((value - mean_utilization) ** 2 for value in utilizations) / len(utilizations)


def reduction_total_cost(
    distance_cost,
    spatial_penalty,
    balance_penalty,
    distance_weight,
    spatial_penalty_weight,
    balance_penalty_weight,
):
    return (
        distance_weight * distance_cost
        + spatial_penalty_weight * spatial_penalty
        + balance_penalty_weight * balance_penalty
    )


def whole_merge_candidate(
    left,
    right,
    clusters,
    ports,
    matrix,
    spatial_matrix,
    capacity_limit,
    distance_weight,
    spatial_penalty_weight,
    balance_penalty_weight,
):
    if left.merge_group != right.merge_group:
        return None
    if left.total_ports + right.total_ports > capacity_limit:
        return None

    target, source = (left, right) if left.key < right.key else (right, left)
    distance_cost = cluster_cross_distance(left, right, matrix, ports)
    spatial_penalty = cluster_cross_distance(left, right, spatial_matrix, ports)
    balance_penalty = post_reduction_balance_penalty(
        clusters,
        capacity_limit,
        source.key,
        {target.key: source.total_ports},
    )
    total_cost = reduction_total_cost(
        distance_cost,
        spatial_penalty,
        balance_penalty,
        distance_weight,
        spatial_penalty_weight,
        balance_penalty_weight,
    )
    moves = [
        (
            member,
            target.key,
            member_to_cluster_distance(member, target, matrix),
            member_to_cluster_distance(member, target, spatial_matrix),
        )
        for member in source.members
    ]
    return {
        "strategy": "whole_cluster_merge",
        "source_cluster_key": source.key,
        "target_cluster_key": target.key,
        "source_total_ports": source.total_ports,
        "moves": moves,
        "distance_cost": distance_cost,
        "spatial_penalty": spatial_penalty,
        "balance_penalty": balance_penalty,
        "total_cost": total_cost,
        "search_state_count": 0,
        "search_complete": True,
        "solver": "whole_cluster_cost_comparison",
    }


def exact_split_migration_candidate(
    source,
    targets,
    clusters,
    ports,
    matrix,
    spatial_matrix,
    capacity_limit,
    distance_weight,
    spatial_penalty_weight,
    balance_penalty_weight,
    max_states,
):
    if len(targets) < 2 or len(targets) > 3:
        return None

    targets = sorted(targets, key=lambda target: target.key)
    initial_capacity = tuple(
        capacity_limit - target.total_ports for target in targets
    )
    if sum(initial_capacity) < source.total_ports:
        return None

    members = sorted(source.members, key=lambda member: (-ports[member], member))
    distance_lookup = {}
    for member in members:
        options = {}
        for target in targets:
            if initial_capacity[targets.index(target)] < ports[member]:
                continue
            options[target.key] = (
                member_to_cluster_distance(member, target, matrix),
                member_to_cluster_distance(member, target, spatial_matrix),
            )
        if not options:
            return None
        distance_lookup[member] = options

    prefix_port_sum = [0]
    for member in members:
        prefix_port_sum.append(prefix_port_sum[-1] + ports[member])
    tracked_target_count = len(targets) - 1
    total_initial_capacity = sum(initial_capacity)
    memo = {}
    choice = {}
    state_count = 0
    interrupted = False

    def remaining_capacity(position, tracked_capacity):
        final_capacity = total_initial_capacity - prefix_port_sum[position] - sum(tracked_capacity)
        return tracked_capacity + (final_capacity,)

    def solve(position, tracked_capacity):
        nonlocal state_count, interrupted
        if interrupted:
            return None
        state_key = (position, tracked_capacity)
        if state_key in memo:
            return memo[state_key]
        if state_count >= max_states:
            interrupted = True
            return None
        state_count += 1

        available = remaining_capacity(position, tracked_capacity)
        if min(available) < 0:
            memo[state_key] = float("inf")
            return memo[state_key]
        if position == len(members):
            added_ports = {
                targets[index].key: initial_capacity[index] - available[index]
                for index in range(len(targets))
            }
            balance_penalty = post_reduction_balance_penalty(
                clusters,
                capacity_limit,
                source.key,
                added_ports,
            )
            memo[state_key] = balance_penalty_weight * balance_penalty
            return memo[state_key]

        member = members[position]
        member_ports = ports[member]
        best_cost = float("inf")
        best_target_index = None
        for target_index, target in enumerate(targets):
            if available[target_index] < member_ports:
                continue
            distance, spatial_distance = distance_lookup[member][target.key]
            next_available = list(available)
            next_available[target_index] -= member_ports
            next_tracked_capacity = tuple(next_available[:tracked_target_count])
            remaining_cost = solve(position + 1, next_tracked_capacity)
            if remaining_cost is None:
                return None
            local_cost = member_ports * (
                distance_weight * distance + spatial_penalty_weight * spatial_distance
            ) / source.total_ports
            total_cost = local_cost + remaining_cost
            if total_cost + EPS < best_cost:
                best_cost = total_cost
                best_target_index = target_index
        memo[state_key] = best_cost
        if best_target_index is not None:
            choice[state_key] = best_target_index
        return best_cost

    total_cost = solve(0, initial_capacity[:tracked_target_count])
    if interrupted or total_cost is None or not math.isfinite(total_cost):
        return None

    assignments = []
    position = 0
    tracked_capacity = initial_capacity[:tracked_target_count]
    distance_sum = 0.0
    spatial_sum = 0.0
    added_ports = defaultdict(int)
    while position < len(members):
        state_key = (position, tracked_capacity)
        target_index = choice[state_key]
        target = targets[target_index]
        member = members[position]
        distance, spatial_distance = distance_lookup[member][target.key]
        assignments.append((member, target.key, distance, spatial_distance))
        distance_sum += ports[member] * distance
        spatial_sum += ports[member] * spatial_distance
        added_ports[target.key] += ports[member]
        available = list(remaining_capacity(position, tracked_capacity))
        available[target_index] -= ports[member]
        tracked_capacity = tuple(available[:tracked_target_count])
        position += 1

    balance_penalty = post_reduction_balance_penalty(
        clusters,
        capacity_limit,
        source.key,
        added_ports,
    )
    return {
        "strategy": "split_migration",
        "source_cluster_key": source.key,
        "source_total_ports": source.total_ports,
        "moves": assignments,
        "distance_cost": distance_sum / source.total_ports,
        "spatial_penalty": spatial_sum / source.total_ports,
        "balance_penalty": balance_penalty,
        "total_cost": total_cost,
        "search_state_count": state_count,
        "search_complete": True,
        "solver": "exact_dynamic_programming",
    }


def bounded_split_migration_candidate(
    source,
    targets,
    clusters,
    ports,
    matrix,
    spatial_matrix,
    capacity_limit,
    distance_weight,
    spatial_penalty_weight,
    balance_penalty_weight,
    max_states,
):
    available_capacity = {
        target.key: capacity_limit - target.total_ports for target in targets
    }
    if sum(available_capacity.values()) < source.total_ports:
        return None

    options_by_member = {}
    for member in source.members:
        member_options = []
        for target in targets:
            if available_capacity[target.key] < ports[member]:
                continue
            distance = member_to_cluster_distance(member, target, matrix)
            spatial_distance = member_to_cluster_distance(member, target, spatial_matrix)
            local_cost = distance_weight * distance + spatial_penalty_weight * spatial_distance
            member_options.append((target, distance, spatial_distance, local_cost))
        if not member_options:
            return None
        options_by_member[member] = sorted(
            member_options,
            key=lambda item: (item[3], item[0].key),
        )

    members = sorted(
        source.members,
        key=lambda member: (-ports[member], len(options_by_member[member]), member),
    )
    remaining_port_sum = [0 for _ in range(len(members) + 1)]
    remaining_max_port = [0 for _ in range(len(members) + 1)]
    minimum_cost_suffix = [0.0 for _ in range(len(members) + 1)]
    for position in range(len(members) - 1, -1, -1):
        member = members[position]
        member_ports = ports[member]
        minimum_local_cost = options_by_member[member][0][3]
        remaining_port_sum[position] = remaining_port_sum[position + 1] + member_ports
        remaining_max_port[position] = max(remaining_max_port[position + 1], member_ports)
        minimum_cost_suffix[position] = (
            minimum_cost_suffix[position + 1]
            + member_ports * minimum_local_cost / source.total_ports
        )

    assignments = []
    best_candidate = None
    best_signature = None
    state_count = 0
    search_complete = True

    def search(position, distance_sum, spatial_sum):
        nonlocal best_candidate, best_signature, state_count, search_complete
        if state_count >= max_states:
            search_complete = False
            return
        state_count += 1

        partial_cost = (
            distance_weight * distance_sum + spatial_penalty_weight * spatial_sum
        ) / source.total_ports
        if best_candidate is not None and partial_cost + minimum_cost_suffix[position] >= best_candidate["total_cost"] - EPS:
            return
        if position == len(members):
            added_ports = defaultdict(int)
            for member, target_key, _, _ in assignments:
                added_ports[target_key] += ports[member]
            balance_penalty = post_reduction_balance_penalty(
                clusters,
                capacity_limit,
                source.key,
                added_ports,
            )
            distance_cost = distance_sum / source.total_ports
            spatial_penalty = spatial_sum / source.total_ports
            total_cost = reduction_total_cost(
                distance_cost,
                spatial_penalty,
                balance_penalty,
                distance_weight,
                spatial_penalty_weight,
                balance_penalty_weight,
            )
            signature = tuple(target_key for _, target_key, _, _ in assignments)
            if (
                best_candidate is None
                or total_cost + EPS < best_candidate["total_cost"]
                or (
                    abs(total_cost - best_candidate["total_cost"]) <= EPS
                    and signature < best_signature
                )
            ):
                best_signature = signature
                best_candidate = {
                    "strategy": "split_migration",
                    "source_cluster_key": source.key,
                    "source_total_ports": source.total_ports,
                    "moves": list(assignments),
                    "distance_cost": distance_cost,
                    "spatial_penalty": spatial_penalty,
                    "balance_penalty": balance_penalty,
                    "total_cost": total_cost,
                }
            return

        member = members[position]
        member_ports = ports[member]
        for target, distance, spatial_distance, _ in options_by_member[member]:
            if available_capacity[target.key] < member_ports:
                continue
            available_capacity[target.key] -= member_ports
            remaining_capacities = list(available_capacity.values())
            can_fit_remaining = (
                sum(remaining_capacities) >= remaining_port_sum[position + 1]
                and (
                    position + 1 == len(members)
                    or max(remaining_capacities) >= remaining_max_port[position + 1]
                )
            )
            if can_fit_remaining:
                assignments.append((member, target.key, distance, spatial_distance))
                search(
                    position + 1,
                    distance_sum + member_ports * distance,
                    spatial_sum + member_ports * spatial_distance,
                )
                assignments.pop()
            available_capacity[target.key] += member_ports

    search(0, 0.0, 0.0)
    if best_candidate is None:
        return None
    best_candidate["search_state_count"] = state_count
    best_candidate["search_complete"] = search_complete
    best_candidate["solver"] = "bounded_branch_and_bound"
    return best_candidate


def optimal_split_migration_candidate(
    source,
    targets,
    clusters,
    ports,
    matrix,
    spatial_matrix,
    capacity_limit,
    distance_weight,
    spatial_penalty_weight,
    balance_penalty_weight,
    max_states,
):
    exact_candidate = exact_split_migration_candidate(
        source,
        targets,
        clusters,
        ports,
        matrix,
        spatial_matrix,
        capacity_limit,
        distance_weight,
        spatial_penalty_weight,
        balance_penalty_weight,
        max_states,
    )
    if exact_candidate is not None:
        return exact_candidate
    return bounded_split_migration_candidate(
        source,
        targets,
        clusters,
        ports,
        matrix,
        spatial_matrix,
        capacity_limit,
        distance_weight,
        spatial_penalty_weight,
        balance_penalty_weight,
        max_states,
    )


def select_reduction_candidate(
    clusters,
    ports,
    matrix,
    spatial_matrix,
    capacity_limit,
    distance_weight,
    spatial_penalty_weight,
    balance_penalty_weight,
    max_states,
):
    ordered_clusters = sorted(clusters.values(), key=lambda cluster: cluster.key)
    whole_merge_candidates = []
    for index, left in enumerate(ordered_clusters):
        for right in ordered_clusters[index + 1 :]:
            candidate = whole_merge_candidate(
                left,
                right,
                clusters,
                ports,
                matrix,
                spatial_matrix,
                capacity_limit,
                distance_weight,
                spatial_penalty_weight,
                balance_penalty_weight,
            )
            if candidate is not None:
                whole_merge_candidates.append(candidate)
    if whole_merge_candidates:
        selected = min(
            whole_merge_candidates,
            key=lambda candidate: (
                candidate["total_cost"],
                candidate["source_cluster_key"],
                candidate["target_cluster_key"],
            ),
        )
        selected["candidate_count"] = len(whole_merge_candidates)
        return selected

    split_candidates = []
    for source in ordered_clusters:
        targets = [
            target
            for target in ordered_clusters
            if target.key != source.key and target.merge_group == source.merge_group
        ]
        candidate = optimal_split_migration_candidate(
            source,
            targets,
            clusters,
            ports,
            matrix,
            spatial_matrix,
            capacity_limit,
            distance_weight,
            spatial_penalty_weight,
            balance_penalty_weight,
            max_states,
        )
        if candidate is not None:
            split_candidates.append(candidate)
    if not split_candidates:
        return None
    selected = min(
        split_candidates,
        key=lambda candidate: (candidate["total_cost"], candidate["source_cluster_key"]),
    )
    selected["candidate_count"] = len(split_candidates)
    return selected


def apply_reduction_candidate(clusters, candidate, imbs, ports):
    source = clusters[candidate["source_cluster_key"]]
    target_key = candidate.get("target_cluster_key")
    moved_members = []
    for member, move_target_key, distance, spatial_distance in candidate["moves"]:
        target = clusters[move_target_key]
        target.members.append(member)
        target.total_ports += ports[member]
        moved_members.append(
            {
                "imb_id": imbs[member]["id"],
                "ports": ports[member],
                "to_cluster_key": move_target_key,
                "distance_to_target": round(distance, 6),
                "spatial_distance_to_target": round(spatial_distance, 6),
            }
        )
    del clusters[source.key]
    return {
        "strategy": candidate["strategy"],
        "selection_basis": (
            "lowest total cost among globally feasible candidates"
            if candidate["search_complete"]
            else "lowest total cost among searched feasible candidates"
        ),
        "removed_cluster_key": source.key,
        "removed_cluster_ports": source.total_ports,
        "target_cluster_key": target_key,
        "candidate_count": candidate["candidate_count"],
        "cost": {
            "total": round(candidate["total_cost"], 6),
            "migration_distance": round(candidate["distance_cost"], 6),
            "spatial_dispersion": round(candidate["spatial_penalty"], 6),
            "capacity_balance": round(candidate["balance_penalty"], 6),
        },
        "search_state_count": candidate["search_state_count"],
        "search_complete": candidate["search_complete"],
        "solver": candidate["solver"],
        "moved_members": moved_members,
    }


def reduce_cluster_count(
    clusters,
    imbs,
    ports,
    matrix,
    spatial_matrix,
    capacity_limit,
    target_cluster_num,
    distance_weight,
    spatial_penalty_weight,
    balance_penalty_weight,
    max_states,
):
    reductions = []
    while len(clusters) > target_cluster_num:
        candidate = select_reduction_candidate(
            clusters,
            ports,
            matrix,
            spatial_matrix,
            capacity_limit,
            distance_weight,
            spatial_penalty_weight,
            balance_penalty_weight,
            max_states,
        )
        if candidate is None:
            break
        reductions.append(apply_reduction_candidate(clusters, candidate, imbs, ports))
    return reductions


def capacity_constrained_agglomerative(
    imbs,
    matrix,
    capacity_limit,
    target_cluster_num=None,
    merge_group_labels=None,
    allow_refinement=True,
    enclave_ratio=0.60,
    max_refine_iterations=50,
    minimize_cluster_count=False,
    spatial_matrix=None,
    reduction_distance_weight=DEFAULT_REDUCTION_DISTANCE_WEIGHT,
    reduction_spatial_penalty_weight=DEFAULT_REDUCTION_SPATIAL_PENALTY_WEIGHT,
    reduction_balance_penalty_weight=DEFAULT_REDUCTION_BALANCE_PENALTY_WEIGHT,
    reduction_max_states=DEFAULT_REDUCTION_MAX_STATES,
):
    ports = [port_count(node) for node in imbs]
    total_ports = sum(ports)
    if target_cluster_num is None:
        target_cluster_num = math.ceil(total_ports / capacity_limit)
    over_capacity_nodes = [
        {"id": imbs[index]["id"], "ports": value}
        for index, value in enumerate(ports)
        if value > capacity_limit
    ]
    if over_capacity_nodes:
        raise ValueError(f"Single IMB nodes exceed capacity: {over_capacity_nodes}")

    if merge_group_labels is not None and len(merge_group_labels) != len(imbs):
        raise ValueError("merge_group_labels length must match IMB count.")

    clusters = {}
    for index in range(len(imbs)):
        merge_group = merge_group_labels[index] if merge_group_labels is not None else None
        clusters[index] = ClusterState(
            key=index,
            members=[index],
            total_ports=ports[index],
            merge_group=merge_group,
        )
    distances = {}
    for i in range(len(imbs)):
        for j in range(i + 1, len(imbs)):
            distances[(i, j)] = matrix[i][j]

    next_key = len(imbs)
    merges = []
    stop_reason = "target_reached"
    while len(clusters) > target_cluster_num:
        active_keys = sorted(clusters)
        best_pair = None
        best_distance = float("inf")
        for i, left_key in enumerate(active_keys):
            left = clusters[left_key]
            for right_key in active_keys[i + 1 :]:
                right = clusters[right_key]
                if (
                    merge_group_labels is not None
                    and left.merge_group != right.merge_group
                ):
                    continue
                if left.total_ports + right.total_ports > capacity_limit:
                    continue
                pair = (left_key, right_key) if left_key < right_key else (right_key, left_key)
                distance = distances[pair]
                if distance + EPS < best_distance:
                    best_distance = distance
                    best_pair = (left_key, right_key)
        if best_pair is None:
            stop_reason = "no_capacity_legal_pair"
            break
        left_key, right_key = best_pair
        left = clusters[left_key]
        right = clusters[right_key]
        new_members = left.members + right.members
        new_cluster = ClusterState(
            key=next_key,
            members=new_members,
            total_ports=left.total_ports + right.total_ports,
            merge_group=left.merge_group,
        )
        merge_record = {
            "step": len(merges) + 1,
            "left_key": left_key,
            "right_key": right_key,
            "new_key": next_key,
            "distance": round(best_distance, 3),
            "member_count": len(new_members),
            "total_ports": new_cluster.total_ports,
        }

        for other_key in active_keys:
            if other_key in (left_key, right_key):
                continue
            other = clusters[other_key]
            left_pair = (left_key, other_key) if left_key < other_key else (other_key, left_key)
            right_pair = (right_key, other_key) if right_key < other_key else (other_key, right_key)
            new_distance = (
                len(left.members) * distances[left_pair]
                + len(right.members) * distances[right_pair]
            ) / (len(left.members) + len(right.members))
            new_pair = (other_key, next_key) if other_key < next_key else (next_key, other_key)
            distances[new_pair] = new_distance

        obsolete_keys = []
        for pair in distances:
            if left_key in pair or right_key in pair:
                obsolete_keys.append(pair)
        for pair in obsolete_keys:
            del distances[pair]

        del clusters[left_key]
        del clusters[right_key]
        clusters[next_key] = new_cluster
        merges.append(merge_record)
        next_key += 1

    cluster_reductions = []
    if minimize_cluster_count:
        reduction_spatial_matrix = spatial_matrix
        if reduction_spatial_matrix is None:
            reduction_spatial_matrix, _ = normalize_distance_matrix(matrix)
        cluster_reductions = reduce_cluster_count(
            clusters,
            imbs,
            ports,
            matrix,
            reduction_spatial_matrix,
            capacity_limit,
            target_cluster_num,
            reduction_distance_weight,
            reduction_spatial_penalty_weight,
            reduction_balance_penalty_weight,
            reduction_max_states,
        )
        if len(clusters) <= target_cluster_num and cluster_reductions:
            stop_reason = "target_reached_after_cost_optimized_rebalancing"

    refinement_moves = []
    if allow_refinement:
        refinement_moves = refine_enclaves(
            clusters,
            imbs,
            ports,
            matrix,
            capacity_limit,
            enclave_ratio,
            max_refine_iterations,
        )

    return {
        "clusters": list(clusters.values()),
        "target_cluster_num": target_cluster_num,
        "total_ports": total_ports,
        "merges": merges,
        "stop_reason": stop_reason,
        "cluster_reductions": cluster_reductions,
        "refinement_moves": refinement_moves,
    }


def sorted_cluster_states(clusters, imbs):
    return sorted(
        clusters,
        key=lambda cluster: (
            centroid(cluster.members, imbs)["y"],
            centroid(cluster.members, imbs)["x"],
            cluster.key,
        ),
    )


def partition_distance_metrics(clusters, spatial_matrix, pipe_matrix, weighted_matrix):
    pair_count = 0
    spatial_total = 0.0
    pipe_total = 0.0
    weighted_total = 0.0
    for cluster in clusters:
        for offset, left in enumerate(cluster.members):
            for right in cluster.members[offset + 1 :]:
                pair_count += 1
                spatial_total += spatial_matrix[left][right]
                pipe_total += pipe_matrix[left][right]
                weighted_total += weighted_matrix[left][right]
    if pair_count == 0:
        return {
            "within_cluster_pair_count": 0,
            "mean_spatial_distance": 0.0,
            "mean_pipe_network_distance": 0.0,
            "mean_weighted_distance": 0.0,
        }
    return {
        "within_cluster_pair_count": pair_count,
        "mean_spatial_distance": round(spatial_total / pair_count, 3),
        "mean_pipe_network_distance": round(pipe_total / pair_count, 3),
        "mean_weighted_distance": round(weighted_total / pair_count, 6),
    }


def format_cluster_members(cluster, imbs):
    members = []
    for index in sorted(cluster.members, key=lambda i: (float(imbs[i]["y"]), float(imbs[i]["x"]), imbs[i]["id"])):
        node = imbs[index]
        item = {
            "id": node["id"],
            "x": node["x"],
            "y": node["y"],
            "ports": port_count(node),
        }
        for key in ("zone", "cluster", "row", "building", "member"):
            if key in node:
                item[key] = node[key]
        members.append(item)
    return members


def format_partition(
    name,
    distance_mode,
    partition,
    imbs,
    capacity_limit,
    allow_extra_clusters,
    parent_field_name=None,
):
    clusters = []
    assignment = {}
    for display_index, cluster in enumerate(sorted_cluster_states(partition["clusters"], imbs), start=1):
        cluster_id = f"{name}_{display_index:03d}"
        for member_index in cluster.members:
            assignment[imbs[member_index]["id"]] = cluster_id
        cluster_record = {
            "id": cluster_id,
            "internal_key": cluster.key,
            "member_count": len(cluster.members),
            "total_ports": cluster.total_ports,
            "capacity_limit": capacity_limit,
            "capacity_utilization": round(cluster.total_ports / capacity_limit, 4),
            "centroid": centroid(cluster.members, imbs),
            "members": format_cluster_members(cluster, imbs),
        }
        if parent_field_name and cluster.merge_group is not None:
            cluster_record[parent_field_name] = cluster.merge_group
        clusters.append(cluster_record)
    validation = validate_partition(
        imbs,
        clusters,
        assignment,
        capacity_limit,
        partition["target_cluster_num"],
        allow_extra_clusters,
    )
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
        "refinement_moves": partition["refinement_moves"],
        "validation": validation,
        "assignment": assignment,
        "clusters": clusters,
    }


def znro_merge_groups(znro_clusters, imbs):
    labels_by_index = [None for _ in imbs]
    labels_by_imb_id = {}
    for display_index, cluster in enumerate(sorted_cluster_states(znro_clusters, imbs), start=1):
        znro_id = f"ZNRO_{display_index:03d}"
        for member_index in cluster.members:
            labels_by_index[member_index] = znro_id
            labels_by_imb_id[imbs[member_index]["id"]] = znro_id
    return labels_by_index, labels_by_imb_id


def validate_zpm_within_znro(zpm_partition, znro_assignment):
    violations = []
    for cluster in zpm_partition["clusters"]:
        parent_ids = sorted(
            {
                znro_assignment.get(member["id"], "<missing>")
                for member in cluster["members"]
            }
        )
        expected_parent = cluster.get("parent_znro_id")
        if len(parent_ids) != 1 or expected_parent != parent_ids[0]:
            violations.append(
                {
                    "zpm_id": cluster["id"],
                    "parent_znro_id": expected_parent,
                    "member_znro_ids": parent_ids,
                }
            )
    zpm_partition["validation"]["nested_in_znro"] = not violations
    zpm_partition["validation"]["znro_nesting_violations"] = violations


def enforce_required_partition_constraints(partition, partition_name, require_znro_nesting=False):
    validation = partition["validation"]
    errors = []
    if not validation["capacity_ok"]:
        errors.append(
            f"{partition_name} capacity constraint failed; "
            f"violations={validation['capacity_violations']}"
        )
    if not validation["full_coverage"]:
        errors.append(
            f"{partition_name} clusters do not cover all IMB nodes; "
            f"missing={validation['missing_imb_ids']}"
        )
    if not validation["unique_assignment"]:
        errors.append(
            f"{partition_name} assignment is not unique; "
            f"duplicates={validation['duplicate_imb_ids']}"
        )
    if not validation["non_overlapping_by_imb"]:
        errors.append(
            f"{partition_name} clusters overlap at IMB nodes; "
            f"overlaps={validation['overlap_imb_ids']}"
        )
    if require_znro_nesting and not validation.get("nested_in_znro", False):
        errors.append(
            "ZPM clusters are not nested inside ZNRO clusters; "
            f"violations={validation.get('znro_nesting_violations', [])}"
        )
    if errors:
        raise ValueError("Required ZPM constraints failed: " + " | ".join(errors))


def validate_partition(imbs, clusters, assignment, capacity_limit, target_cluster_num, allow_extra_clusters):
    all_ids = [node["id"] for node in imbs]
    assigned_ids = []
    for cluster in clusters:
        assigned_ids.extend(member["id"] for member in cluster["members"])
    counts = Counter(assigned_ids)
    missing = sorted(set(all_ids) - set(assigned_ids))
    duplicates = sorted(node_id for node_id, count in counts.items() if count > 1)
    capacity_violations = [
        {
            "cluster_id": cluster["id"],
            "total_ports": cluster["total_ports"],
            "capacity_limit": capacity_limit,
        }
        for cluster in clusters
        if cluster["total_ports"] > capacity_limit
    ]
    return {
        "full_coverage": not missing,
        "unique_assignment": not duplicates and len(assignment) == len(all_ids),
        "non_overlapping_by_imb": not duplicates,
        "capacity_ok": not capacity_violations,
        "cluster_count_ok": len(clusters) <= target_cluster_num + allow_extra_clusters,
        "expected_imb_count": len(all_ids),
        "covered_imb_count": len(set(assigned_ids)),
        "assigned_record_count": len(assigned_ids),
        "missing_imb_ids": missing,
        "duplicate_imb_ids": duplicates,
        "overlap_imb_ids": duplicates,
        "capacity_violations": capacity_violations,
        "allowed_cluster_num": target_cluster_num + allow_extra_clusters,
    }


def cluster_bounding_box(xs, ys, reference_width, reference_height):
    left, right = min(xs), max(xs)
    top, bottom = min(ys), max(ys)
    horizontal_padding = max(reference_width * 0.005, (right - left) * 0.025, EPS)
    vertical_padding = max(reference_height * 0.005, (bottom - top) * 0.025, EPS)
    return (
        left - horizontal_padding,
        top - vertical_padding,
        (right - left) + 2 * horizontal_padding,
        (bottom - top) + 2 * vertical_padding,
    )


def plot_extent(data, nodes):
    meta = data.get("meta", {})
    xs = [float(node["x"]) for node in nodes]
    ys = [float(node["y"]) for node in nodes]
    node_left, node_right = min(xs), max(xs)
    node_top, node_bottom = min(ys), max(ys)
    bounds = meta.get("bounds", {})

    if bounds and all(key in bounds for key in ("min_x", "max_x", "min_y", "max_y")):
        left = float(bounds["min_x"])
        right = float(bounds["max_x"])
        top = float(bounds["min_y"])
        bottom = float(bounds["max_y"])
    else:
        width = meta.get("width")
        height = meta.get("height")
        if (
            width
            and height
            and node_left >= 0
            and node_top >= 0
            and node_right <= float(width)
            and node_bottom <= float(height)
        ):
            left, right = 0.0, float(width)
            top, bottom = 0.0, float(height)
        else:
            left, right = node_left, node_right
            top, bottom = node_top, node_bottom

    width = max(right - left, EPS)
    height = max(bottom - top, EPS)
    return left, right, top, bottom, width, height


def partition_colors(cluster_count):
    # High-contrast colors keep adjacent ZPM assignments visually distinct.
    palette = [
        "#0072B2",
        "#D55E00",
        "#009E73",
        "#CC79A7",
        "#E69F00",
        "#56B4E9",
        "#F0E442",
        "#332288",
        "#44AA99",
        "#AA4499",
        "#999933",
        "#882255",
    ]
    return [palette[index % len(palette)] for index in range(cluster_count)]


def draw_partition(ax, data, partition, title):
    nodes = data["nodes"]
    node_map = {node["id"]: node for node in nodes}
    left, right, top, bottom, plot_width, plot_height = plot_extent(data, nodes)
    for zone in data.get("meta", {}).get("zones", []):
        box = zone.get("box", zone)
        rect = plt.Rectangle(
            (box["left"], box["top"]),
            box["right"] - box["left"],
            box["bottom"] - box["top"],
            edgecolor="#d0d0d0",
            facecolor="none",
            linestyle="--",
            linewidth=0.8,
            zorder=0,
        )
        ax.add_patch(rect)

    for edge in data.get("edges", []):
        points = edge_points(edge, node_map)
        ax.plot(
            [point[0] for point in points],
            [point[1] for point in points],
            color="#b5b5b5",
            linewidth=0.55,
            alpha=0.55,
            zorder=1,
        )

    petch_nodes = [node for node in nodes if node_type(node) in {"PETCH", "PTECH"}]
    ax.scatter(
        [node["x"] for node in petch_nodes],
        [node["y"] for node in petch_nodes],
        s=7,
        c="#6f6f6f",
        alpha=0.65,
        label="PETCH",
        zorder=2,
    )

    colors = partition_colors(len(partition["clusters"]))
    label_positions = []
    label_spacing = max(plot_width, plot_height) * 0.06
    label_offset = plot_height * 0.025
    for index, cluster in enumerate(partition["clusters"]):
        color = colors[index % len(colors)]
        xs = [member["x"] for member in cluster["members"]]
        ys = [member["y"] for member in cluster["members"]]
        box_left, box_top, width, height = cluster_bounding_box(
            xs,
            ys,
            plot_width,
            plot_height,
        )
        ax.add_patch(
            plt.Rectangle(
                (box_left, box_top),
                width,
                height,
                edgecolor="none",
                facecolor=color,
                alpha=0.10,
                zorder=3,
            )
        )
        ax.add_patch(
            plt.Rectangle(
                (box_left, box_top),
                width,
                height,
                edgecolor=color,
                facecolor="none",
                linestyle="--",
                linewidth=1.25,
                alpha=0.95,
                zorder=4,
            )
        )
        ax.scatter(
            xs,
            ys,
            s=30 if partition["name"] == "ZPM" else 25,
            color=color,
            edgecolors="white",
            linewidths=0.45,
            zorder=5,
        )
        center = cluster["centroid"]
        label_x = center["x"]
        label_y = center["y"]
        for attempt in range(10):
            if all(
                math.hypot(label_x - x, label_y - y) > label_spacing
                for x, y in label_positions
            ):
                break
            direction = 1 if attempt % 2 == 0 else -1
            label_y = center["y"] + direction * label_offset * (1 + 0.5 * attempt)
        label_positions.append((label_x, label_y))
        if math.hypot(label_x - center["x"], label_y - center["y"]) > EPS:
            ax.plot(
                [center["x"], label_x],
                [center["y"], label_y],
                color=color,
                linewidth=0.6,
                alpha=0.65,
                zorder=5,
            )
        ax.text(
            label_x,
            label_y,
            f"{cluster['id']}\n{cluster['total_ports']}p",
            ha="center",
            va="center",
            fontsize=7,
            color="#222222",
            bbox={
                "boxstyle": "round,pad=0.22",
                "facecolor": "white",
                "edgecolor": color,
                "linewidth": 0.8,
                "alpha": 0.86,
            },
            zorder=6,
        )

    horizontal_margin = plot_width * 0.05
    vertical_margin = plot_height * 0.05
    ax.set_xlim(left - horizontal_margin, right + horizontal_margin)
    if data.get("meta", {}).get("coordinate_system") == "source_shapefile_coordinates":
        ax.set_ylim(top - vertical_margin, bottom + vertical_margin)
    else:
        ax.set_ylim(bottom + vertical_margin, top - vertical_margin)
    ax.set_aspect("equal", adjustable="box")
    ax.set_title(title, fontsize=11)
    ax.axis("off")


def visualize(data, znro_partition, zpm_partition, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 2, figsize=(14, 11), dpi=180)
    weights = zpm_partition.get("distance_weights", {})
    spatial_weight = weights.get("spatial_weight", 0.0)
    pipe_weight = weights.get("pipe_weight", 1.0)
    draw_partition(
        axes[0],
        data,
        znro_partition,
        f"ZNRO {znro_partition['distance_mode']}: {znro_partition['actual_cluster_num']} clusters",
    )
    draw_partition(
        axes[1],
        data,
        zpm_partition,
        (
            f"ZPM weighted: S={spatial_weight:.2f}, P={pipe_weight:.2f} "
            f"({zpm_partition['actual_cluster_num']} clusters)"
        ),
    )
    fig.tight_layout(pad=1.0)
    fig.savefig(output_path, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)


def build_partitions(args):
    data = load_json(args.input_json)
    imbs = extract_imb_nodes(data)
    node_map = {node["id"]: node for node in data.get("nodes", [])}
    imb_only_mode = is_imb_only_dataset(data)
    capacity_limit = args.nro_max_cap
    zpm_capacity_limit = args.zpm_max_cap
    total_ports = sum(port_count(node) for node in imbs)
    target_cluster_num = args.target_cluster_num or math.ceil(total_ports / capacity_limit)
    geographic_coordinates = uses_geographic_coordinates(data)
    if imb_only_mode:
        spatial_distance_mode = "geodesic_imb" if geographic_coordinates else "euclidean_imb"
    else:
        spatial_distance_mode = "geodesic" if geographic_coordinates else "euclidean"

    euclidean_matrix = euclidean_distance_matrix(
        imbs,
        geographic_coordinates=geographic_coordinates,
    )
    normalized_spatial_matrix, spatial_distance_scale = normalize_distance_matrix(euclidean_matrix)
    if imb_only_mode:
        pipe_matrix = [[0.0 for _ in imbs] for _ in imbs]
        zpm_matrix = normalized_spatial_matrix
        pipe_distance_scale = 0.0
        disconnected_pairs = 0
        synthetic_attachment_count = 0
        zpm_distance_mode = "spatial_only_imb"
        effective_spatial_weight = 1.0
        effective_pipe_weight = 0.0
    else:
        pipe_matrix, disconnected_pairs, synthetic_attachment_count = pipe_distance_matrix(
            data,
            imbs,
            node_map,
            args.unconnect_penalty,
        )
        zpm_matrix, spatial_distance_scale, pipe_distance_scale = weighted_distance_matrix(
            euclidean_matrix,
            pipe_matrix,
            args.zpm_spatial_weight,
        )
        zpm_distance_mode = "weighted_spatial_pipe"
        effective_spatial_weight = args.zpm_spatial_weight
        effective_pipe_weight = 1.0 - args.zpm_spatial_weight

    znro_raw = capacity_constrained_agglomerative(
        imbs,
        euclidean_matrix,
        capacity_limit,
        target_cluster_num=target_cluster_num,
        allow_refinement=not args.disable_refinement,
        enclave_ratio=args.enclave_ratio,
        max_refine_iterations=args.max_refine_iterations,
    )
    znro_group_labels, znro_assignment = znro_merge_groups(znro_raw["clusters"], imbs)

    zpm_target_from_capacity = args.zpm_target_cluster_num or math.ceil(total_ports / zpm_capacity_limit)
    zpm_min_target_due_to_znro = sum(
        math.ceil(cluster.total_ports / zpm_capacity_limit)
        for cluster in znro_raw["clusters"]
    )
    zpm_target_cluster_num = max(zpm_target_from_capacity, zpm_min_target_due_to_znro)
    zpm_raw = capacity_constrained_agglomerative(
        imbs,
        zpm_matrix,
        zpm_capacity_limit,
        target_cluster_num=zpm_target_cluster_num,
        merge_group_labels=znro_group_labels,
        allow_refinement=not args.disable_refinement,
        enclave_ratio=args.enclave_ratio,
        max_refine_iterations=args.max_refine_iterations,
        minimize_cluster_count=True,
        spatial_matrix=normalized_spatial_matrix,
        reduction_distance_weight=args.reduction_distance_weight,
        reduction_spatial_penalty_weight=args.reduction_spatial_penalty_weight,
        reduction_balance_penalty_weight=args.reduction_balance_penalty_weight,
        reduction_max_states=args.reduction_max_states,
    )

    znro = format_partition(
        "ZNRO",
        spatial_distance_mode,
        znro_raw,
        imbs,
        capacity_limit,
        args.allow_extra_clusters,
    )
    znro["spatial_continuity"] = {
        "constraint_type": "soft_objective",
        "method": f"{spatial_distance_mode} average-linkage agglomeration with enclave refinement",
        "boundary_geometry_checked": False,
    }
    zpm = format_partition(
        "ZPM",
        zpm_distance_mode,
        zpm_raw,
        imbs,
        zpm_capacity_limit,
        args.allow_extra_clusters,
        parent_field_name="parent_znro_id",
    )
    zpm["distance_weights"] = {
        "spatial_weight": effective_spatial_weight,
        "pipe_weight": round(effective_pipe_weight, 6),
        "requested_spatial_weight": args.zpm_spatial_weight,
        "requested_pipe_weight": round(1.0 - args.zpm_spatial_weight, 6),
        "normalization": "each distance matrix divided by its global maximum",
        "spatial_distance_scale": round(spatial_distance_scale, 3),
        "pipe_network_distance_scale": round(pipe_distance_scale, 3),
        "pipe_network_disabled": imb_only_mode,
    }
    zpm["distance_metrics"] = partition_distance_metrics(
        zpm_raw["clusters"],
        euclidean_matrix,
        pipe_matrix,
        zpm_matrix,
    )
    validate_zpm_within_znro(zpm, znro_assignment)
    enforce_required_partition_constraints(znro, "ZNRO")
    enforce_required_partition_constraints(zpm, "ZPM", require_znro_nesting=True)
    result = {
        "source_file": str(args.input_json),
        "algorithm": {
            "input_mode": "imb_only_spatial" if imb_only_mode else "network_weighted",
            "zpm_distance_mode": zpm_distance_mode,
        },
        "parameters": {
            "nro_max_cap": capacity_limit,
            "nro_design_capacity": capacity_limit,
            "znro_target_cluster_num": target_cluster_num,
            "znro_target_cluster_num_source": "argument" if args.target_cluster_num else "ceil(total_ports / nro_max_cap)",
            "zpm_max_cap": zpm_capacity_limit,
            "pm_design_capacity": zpm_capacity_limit,
            "zpm_spatial_weight": effective_spatial_weight,
            "zpm_pipe_weight": round(effective_pipe_weight, 6),
            "requested_zpm_spatial_weight": args.zpm_spatial_weight,
            "requested_zpm_pipe_weight": round(1.0 - args.zpm_spatial_weight, 6),
            "zpm_target_cluster_num": zpm_target_cluster_num,
            "zpm_target_cluster_num_source": (
                "max(argument, sum(ceil(znro_ports / zpm_max_cap)))"
                if args.zpm_target_cluster_num
                else "max(ceil(total_ports / zpm_max_cap), sum(ceil(znro_ports / zpm_max_cap)))"
            ),
            "zpm_target_from_capacity": zpm_target_from_capacity,
            "zpm_min_target_due_to_znro": zpm_min_target_due_to_znro,
            "zpm_nested_in_znro": True,
            "zpm_full_coverage_all_imbs": True,
            "zpm_minimize_cluster_count": True,
            "zpm_reduction_strategy": "whole_merge_then_cost_optimized_split_migration",
            "reduction_distance_weight": args.reduction_distance_weight,
            "reduction_spatial_penalty_weight": args.reduction_spatial_penalty_weight,
            "reduction_balance_penalty_weight": args.reduction_balance_penalty_weight,
            "reduction_max_states": args.reduction_max_states,
            "partition_overlap_definition": "An overlap is an IMB assigned to more than one cluster of the same layer.",
            "allow_extra_clusters": args.allow_extra_clusters,
            "unconnect_penalty": args.unconnect_penalty,
            "enclave_refinement_enabled": not args.disable_refinement,
            "enclave_ratio": args.enclave_ratio,
            "max_refine_iterations": args.max_refine_iterations,
        },
        "input_summary": {
            "node_count": len(data.get("nodes", [])),
            "edge_count": len(data.get("edges", [])),
            "imb_count": len(imbs),
            "total_ports": total_ports,
            "imb_only_layer": imb_only_mode,
            "spatial_distance_mode": spatial_distance_mode,
            "pipe_disconnected_pair_count": disconnected_pairs,
            "synthetic_imb_attachment_count": synthetic_attachment_count,
        },
        "znro": znro,
        "zpm": zpm,
    }
    return data, result


def parse_args():
    default_output_dir = TASK2_ZONE_PARTITION_OUTPUT_DIR
    parser = argparse.ArgumentParser(description="Partition IMB nodes into ZNRO and ZPM clusters.")
    parser.add_argument(
        "--input-json",
        type=Path,
        default=None,
        help="Source FTTH dataset JSON (required; no default is configured).",
    )
    parser.add_argument("--output-json", type=Path, default=default_output_dir / "qgis_min_zpm_result.json")
    parser.add_argument("--output-fig", type=Path, default=default_output_dir / "qgis_min_zpm_result.png")
    parser.add_argument("--nro-max-cap", type=int, default=2048)
    parser.add_argument(
        "--zpm-max-cap",
        type=int,
        default=512,
        help="Maximum number of ports allowed in one ZPM (default: 512).",
    )
    parser.add_argument(
        "--zpm-spatial-weight",
        type=float,
        default=0.1,
        help="Weight of normalized spatial distance in ZPM clustering, from 0 to 1 (default: 0.15; pipe weight: 0.85).",
    )
    parser.add_argument("--target-cluster-num", type=int, default=None)
    parser.add_argument("--zpm-target-cluster-num", type=int, default=None)
    parser.add_argument("--allow-extra-clusters", type=int, default=1)
    parser.add_argument("--unconnect-penalty", type=float, default=DEFAULT_UNCONNECT_PENALTY)
    parser.add_argument("--disable-refinement", action="store_true")
    parser.add_argument("--enclave-ratio", type=float, default=0.60)
    parser.add_argument("--max-refine-iterations", type=int, default=50)
    parser.add_argument(
        "--reduction-distance-weight",
        type=float,
        default=DEFAULT_REDUCTION_DISTANCE_WEIGHT,
        help="Weight of weighted-distance cost when reducing ZPM clusters.",
    )
    parser.add_argument(
        "--reduction-spatial-penalty-weight",
        type=float,
        default=DEFAULT_REDUCTION_SPATIAL_PENALTY_WEIGHT,
        help="Weight of spatial-dispersion penalty when reducing ZPM clusters.",
    )
    parser.add_argument(
        "--reduction-balance-penalty-weight",
        type=float,
        default=DEFAULT_REDUCTION_BALANCE_PENALTY_WEIGHT,
        help="Weight of port-utilization balance penalty when reducing ZPM clusters.",
    )
    parser.add_argument(
        "--reduction-max-states",
        type=int,
        default=DEFAULT_REDUCTION_MAX_STATES,
        help="Maximum branch-and-bound states evaluated for one split-migration candidate.",
    )
    args = parser.parse_args()
    if not 0.0 <= args.zpm_spatial_weight <= 1.0:
        parser.error("--zpm-spatial-weight must be between 0 and 1.")
    if any(
        weight < 0
        for weight in (
            args.reduction_distance_weight,
            args.reduction_spatial_penalty_weight,
            args.reduction_balance_penalty_weight,
        )
    ):
        parser.error("Cluster-reduction weights must be non-negative.")
    if args.reduction_max_states < 1:
        parser.error("--reduction-max-states must be at least 1.")
    if args.input_json is None:
        parser.error("--input-json is required; the task-2 data path is currently unset")
    return args


def main():
    args = parse_args()
    data, result = build_partitions(args)
    dump_json(result, args.output_json)
    visualize(data, result["znro"], result["zpm"], args.output_fig)
    print(f"Input: {args.input_json.resolve()}")
    print(f"Result JSON: {args.output_json.resolve()}")
    print(f"Visualization: {args.output_fig.resolve()}")
    print(
        "ZNRO: "
        f"target={result['znro']['target_cluster_num']}, "
        f"actual={result['znro']['actual_cluster_num']}, "
        f"valid={result['znro']['validation']}"
    )
    print(
        "ZPM: "
        f"target={result['zpm']['target_cluster_num']}, "
        f"actual={result['zpm']['actual_cluster_num']}, "
        f"valid={result['zpm']['validation']}"
    )


if __name__ == "__main__":
    main()
