import argparse
import math
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from project_paths import TASK2_ZONE_PARTITION_OUTPUT_DIR

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as PlotPolygon

from znro_zpm_partition import dump_json, load_json, node_type, port_count
from znro_zpm_partition_petch_atomic import build_result as build_clustering_result


PETCH_TYPES = {"PETCH", "PTECH"}


def point_distance_sq(left, right):
    return (float(left[0]) - float(right[0])) ** 2 + (float(left[1]) - float(right[1])) ** 2


def polygon_area(points):
    if len(points) < 3:
        return 0.0
    total = 0.0
    for index, point in enumerate(points):
        nxt = points[(index + 1) % len(points)]
        total += float(point[0]) * float(nxt[1]) - float(nxt[0]) * float(point[1])
    return abs(total) / 2.0


def polygon_signed_area(points):
    if len(points) < 3:
        return 0.0
    total = 0.0
    for index, point in enumerate(points):
        nxt = points[(index + 1) % len(points)]
        total += float(point[0]) * float(nxt[1]) - float(nxt[0]) * float(point[1])
    return total / 2.0


def cross(origin, left, right):
    return (
        (float(left[0]) - float(origin[0])) * (float(right[1]) - float(origin[1]))
        - (float(left[1]) - float(origin[1])) * (float(right[0]) - float(origin[0]))
    )


def polygon_axis_bounds_dict(points):
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return {
        "min_x": min(xs),
        "min_y": min(ys),
        "max_x": max(xs),
        "max_y": max(ys),
    }


def convex_hull(points):
    unique_points = sorted({(float(point[0]), float(point[1])) for point in points})
    if len(unique_points) <= 1:
        return [[point[0], point[1]] for point in unique_points]

    lower = []
    for point in unique_points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)

    upper = []
    for point in reversed(unique_points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)

    hull = lower[:-1] + upper[:-1]
    return [[point[0], point[1]] for point in hull]


def clean_polygon(points, tolerance):
    cleaned = []
    for point in points:
        if cleaned and math.hypot(point[0] - cleaned[-1][0], point[1] - cleaned[-1][1]) <= tolerance:
            continue
        cleaned.append([float(point[0]), float(point[1])])
    if len(cleaned) > 1 and math.hypot(
        cleaned[0][0] - cleaned[-1][0],
        cleaned[0][1] - cleaned[-1][1],
    ) <= tolerance:
        cleaned.pop()
    if len(cleaned) < 3 or polygon_area(cleaned) <= tolerance * tolerance:
        return []
    return cleaned


def clip_polygon_to_bisector(polygon, site, other_site, value_tolerance, coord_tolerance):
    px, py = site
    qx, qy = other_site
    if point_distance_sq(site, other_site) <= coord_tolerance * coord_tolerance:
        return polygon

    a = 2.0 * (qx - px)
    b = 2.0 * (qy - py)
    c = qx * qx + qy * qy - px * px - py * py

    def signed_value(point):
        return a * point[0] + b * point[1] - c

    def intersection(start, end, start_value, end_value):
        denom = start_value - end_value
        if abs(denom) <= value_tolerance:
            return [float(end[0]), float(end[1])]
        ratio = start_value / denom
        return [
            float(start[0]) + ratio * (float(end[0]) - float(start[0])),
            float(start[1]) + ratio * (float(end[1]) - float(start[1])),
        ]

    output = []
    previous = polygon[-1]
    previous_value = signed_value(previous)
    previous_inside = previous_value <= value_tolerance

    for current in polygon:
        current_value = signed_value(current)
        current_inside = current_value <= value_tolerance
        if current_inside:
            if not previous_inside:
                output.append(intersection(previous, current, previous_value, current_value))
            output.append([float(current[0]), float(current[1])])
        elif previous_inside:
            output.append(intersection(previous, current, previous_value, current_value))
        previous = current
        previous_value = current_value
        previous_inside = current_inside

    return clean_polygon(output, coord_tolerance)


def clip_polygon_to_linear_halfplane(polygon, normal, threshold, keep_less, tolerance):
    """Clip a polygon to n.x <= threshold (or the opposite half-plane)."""
    if not polygon:
        return []
    nx, ny = float(normal[0]), float(normal[1])

    def signed_value(point):
        return nx * float(point[0]) + ny * float(point[1]) - float(threshold)

    def inside(value):
        return value <= tolerance if keep_less else value >= -tolerance

    def intersection(start, end, start_value, end_value):
        denominator = start_value - end_value
        if abs(denominator) <= tolerance:
            return [float(end[0]), float(end[1])]
        ratio = start_value / denominator
        return [
            float(start[0]) + ratio * (float(end[0]) - float(start[0])),
            float(start[1]) + ratio * (float(end[1]) - float(start[1])),
        ]

    output = []
    previous = polygon[-1]
    previous_value = signed_value(previous)
    previous_inside = inside(previous_value)
    for current in polygon:
        current_value = signed_value(current)
        current_inside = inside(current_value)
        if current_inside:
            if not previous_inside:
                output.append(intersection(previous, current, previous_value, current_value))
            output.append([float(current[0]), float(current[1])])
        elif previous_inside:
            output.append(intersection(previous, current, previous_value, current_value))
        previous = current
        previous_value = current_value
        previous_inside = current_inside
    return clean_polygon(output, tolerance)


def build_bounds(data, imb_records, padding_ratio):
    meta_bounds = (data or {}).get("meta", {}).get("bounds", {})
    if all(key in meta_bounds for key in ("min_x", "min_y", "max_x", "max_y")):
        min_x = float(meta_bounds["min_x"])
        min_y = float(meta_bounds["min_y"])
        max_x = float(meta_bounds["max_x"])
        max_y = float(meta_bounds["max_y"])
    else:
        xs = [float(record["x"]) for record in imb_records.values()]
        ys = [float(record["y"]) for record in imb_records.values()]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

    width = max(max_x - min_x, 0.0)
    height = max(max_y - min_y, 0.0)
    extent_scale = max(width, height)
    if extent_scale <= 0:
        coordinate_scale = max(
            [abs(value) for value in (min_x, min_y, max_x, max_y)] + [1.0]
        )
        extent_scale = coordinate_scale
    numeric_scale = max(extent_scale, 1.0)
    padding = extent_scale * padding_ratio
    return {
        "min_x": min_x - padding,
        "min_y": min_y - padding,
        "max_x": max_x + padding,
        "max_y": max_y + padding,
        "width": width + 2.0 * padding,
        "height": height + 2.0 * padding,
        "padding": padding,
        "scale": extent_scale,
        "numeric_scale": numeric_scale,
    }


def boundary_polygon(bounds):
    return [
        [bounds["min_x"], bounds["min_y"]],
        [bounds["max_x"], bounds["min_y"]],
        [bounds["max_x"], bounds["max_y"]],
        [bounds["min_x"], bounds["max_y"]],
    ]


def adjusted_sites(imb_records, bounds):
    groups = defaultdict(list)
    for imb_id, record in imb_records.items():
        key = (round(float(record["x"]), 12), round(float(record["y"]), 12))
        groups[key].append(imb_id)

    jitter = bounds["scale"] * 1e-7
    sites = []
    duplicate_coordinate_groups = []
    for key in sorted(groups):
        imb_ids = sorted(groups[key])
        if len(imb_ids) > 1:
            duplicate_coordinate_groups.append({"coordinate": list(key), "imb_ids": imb_ids})
        for offset_index, imb_id in enumerate(imb_ids):
            record = imb_records[imb_id]
            x = float(record["x"])
            y = float(record["y"])
            if len(imb_ids) > 1:
                angle = 2.0 * math.pi * offset_index / len(imb_ids)
                x += jitter * math.cos(angle)
                y += jitter * math.sin(angle)
            sites.append(
                {
                    "id": f"CELL_{imb_id}",
                    "imb_id": imb_id,
                    "x": float(record["x"]),
                    "y": float(record["y"]),
                    "site_x": x,
                    "site_y": y,
                    "owner_znro_id": record["znro_id"],
                    "owner_zpm_id": record["zpm_id"],
                    "ports": int(record["ports"]),
                }
            )
    return sites, duplicate_coordinate_groups


def build_voronoi_cells(sites, bounds):
    border = boundary_polygon(bounds)
    numeric_scale = bounds.get("numeric_scale", bounds["scale"])
    coord_tolerance = numeric_scale * 1e-10
    value_tolerance = max(numeric_scale * numeric_scale * 1e-12, 1e-18)
    cells = []
    site_points = [(site["site_x"], site["site_y"]) for site in sites]

    for index, site in enumerate(sites):
        polygon = [point[:] for point in border]
        for other_index, other_site in enumerate(site_points):
            if other_index == index:
                continue
            polygon = clip_polygon_to_bisector(
                polygon,
                site_points[index],
                other_site,
                value_tolerance,
                coord_tolerance,
            )
            if not polygon:
                break
        cells.append(
            {
                "id": site["id"],
                "site_type": "IMB",
                "imb_id": site["imb_id"],
                "site": {
                    "x": site["x"],
                    "y": site["y"],
                    "adjusted_x": site["site_x"],
                    "adjusted_y": site["site_y"],
                },
                "owner_znro_id": site["owner_znro_id"],
                "owner_zpm_id": site["owner_zpm_id"],
                "ports": site["ports"],
                "polygon": polygon,
                "area": polygon_area(polygon),
            }
        )
    return cells


def quantized_point(point, tolerance):
    return (round(float(point[0]) / tolerance), round(float(point[1]) / tolerance))


def cell_edge_key(start, end, tolerance):
    left = quantized_point(start, tolerance)
    right = quantized_point(end, tolerance)
    return tuple(sorted((left, right)))


def polygon_bounds(points):
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def bounds_are_close(left, right, tolerance):
    return not (
        left[2] < right[0] - tolerance
        or right[2] < left[0] - tolerance
        or left[3] < right[1] - tolerance
        or right[3] < left[1] - tolerance
    )


def point_segment_distance_sq(point, start, end):
    px, py = float(point[0]), float(point[1])
    sx, sy = float(start[0]), float(start[1])
    ex, ey = float(end[0]), float(end[1])
    dx = ex - sx
    dy = ey - sy
    length_sq = dx * dx + dy * dy
    if length_sq <= 0:
        return (px - sx) ** 2 + (py - sy) ** 2
    ratio = max(0.0, min(1.0, ((px - sx) * dx + (py - sy) * dy) / length_sq))
    closest_x = sx + ratio * dx
    closest_y = sy + ratio * dy
    return (px - closest_x) ** 2 + (py - closest_y) ** 2


def segment_distance_sq(left_start, left_end, right_start, right_end):
    return min(
        point_segment_distance_sq(left_start, right_start, right_end),
        point_segment_distance_sq(left_end, right_start, right_end),
        point_segment_distance_sq(right_start, left_start, left_end),
        point_segment_distance_sq(right_end, left_start, left_end),
    )


def polygons_touch(left, right, tolerance):
    tolerance_sq = tolerance * tolerance
    for left_index, left_start in enumerate(left):
        left_end = left[(left_index + 1) % len(left)]
        for right_index, right_start in enumerate(right):
            right_end = right[(right_index + 1) % len(right)]
            if segment_distance_sq(left_start, left_end, right_start, right_end) <= tolerance_sq:
                return True
    return False


def build_cell_adjacency(cells, bounds):
    tolerance = max(bounds.get("numeric_scale", bounds["scale"]) * 1e-8, 1e-12)
    edge_map = defaultdict(list)
    for cell in cells:
        polygon = cell["polygon"]
        for index, start in enumerate(polygon):
            end = polygon[(index + 1) % len(polygon)]
            if math.hypot(float(start[0]) - float(end[0]), float(start[1]) - float(end[1])) <= tolerance:
                continue
            edge_map[cell_edge_key(start, end, tolerance)].append(cell["id"])

    adjacency = defaultdict(set)
    for cell_ids in edge_map.values():
        if len(cell_ids) < 2:
            continue
        for left in cell_ids:
            for right in cell_ids:
                if left != right:
                    adjacency[left].add(right)

    cell_bounds = {cell["id"]: polygon_bounds(cell["polygon"]) for cell in cells}
    for left_index, left in enumerate(cells):
        for right in cells[left_index + 1:]:
            if right["id"] in adjacency.get(left["id"], set()):
                continue
            if not bounds_are_close(cell_bounds[left["id"]], cell_bounds[right["id"]], tolerance):
                continue
            if polygons_touch(left["polygon"], right["polygon"], tolerance):
                adjacency[left["id"]].add(right["id"])
                adjacency[right["id"]].add(left["id"])
    return {key: sorted(value) for key, value in adjacency.items()}


def connected_components(cell_ids, adjacency):
    remaining = set(cell_ids)
    components = []
    while remaining:
        start = min(remaining)
        queue = deque([start])
        remaining.remove(start)
        component = []
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbor in adjacency.get(current, []):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    queue.append(neighbor)
        components.append(sorted(component))
    components.sort(key=lambda item: (-len(item), item[0] if item else ""))
    return components


def point_on_segment(point, start, end, tolerance):
    px, py = point
    sx, sy = start
    ex, ey = end
    cross = (px - sx) * (ey - sy) - (py - sy) * (ex - sx)
    if abs(cross) > tolerance:
        return False
    dot = (px - sx) * (ex - sx) + (py - sy) * (ey - sy)
    if dot < -tolerance:
        return False
    length_sq = (ex - sx) ** 2 + (ey - sy) ** 2
    return dot <= length_sq + tolerance


def point_in_polygon(point, polygon, tolerance):
    if len(polygon) < 3:
        return False
    x, y = point
    inside = False
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        sx, sy = float(start[0]), float(start[1])
        ex, ey = float(end[0]), float(end[1])
        if point_on_segment((x, y), (sx, sy), (ex, ey), tolerance):
            return True
        if (sy > y) != (ey > y):
            cross_x = (ex - sx) * (y - sy) / (ey - sy) + sx
            if x < cross_x:
                inside = not inside
    return inside


def point_in_cell_union(point, cell_ids, cells_by_id, tolerance):
    return any(point_in_polygon(point, cells_by_id[cell_id]["polygon"], tolerance) for cell_id in cell_ids)


def point_in_polygons(point, polygons, tolerance):
    return any(point_in_polygon(point, polygon, tolerance) for polygon in polygons)


def point_in_rectangle(point, bounds, tolerance):
    x, y = point
    return (
        bounds["min_x"] - tolerance <= x <= bounds["max_x"] + tolerance
        and bounds["min_y"] - tolerance <= y <= bounds["max_y"] + tolerance
    )


def clip_polygon_axis(polygon, axis, threshold, keep_less, tolerance):
    if not polygon:
        return []

    def value(point):
        return float(point[axis])

    def inside(point):
        current = value(point)
        if keep_less:
            return current <= threshold + tolerance
        return current >= threshold - tolerance

    def intersection(start, end):
        start_value = value(start)
        end_value = value(end)
        denom = end_value - start_value
        if abs(denom) <= tolerance:
            return [float(end[0]), float(end[1])]
        ratio = (threshold - start_value) / denom
        return [
            float(start[0]) + ratio * (float(end[0]) - float(start[0])),
            float(start[1]) + ratio * (float(end[1]) - float(start[1])),
        ]

    output = []
    previous = polygon[-1]
    previous_inside = inside(previous)
    for current in polygon:
        current_inside = inside(current)
        if current_inside:
            if not previous_inside:
                output.append(intersection(previous, current))
            output.append([float(current[0]), float(current[1])])
        elif previous_inside:
            output.append(intersection(previous, current))
        previous = current
        previous_inside = current_inside
    return clean_polygon(output, tolerance)


def clip_polygon_to_rectangle(polygon, bounds, tolerance):
    clipped = polygon
    clipped = clip_polygon_axis(clipped, 0, bounds["min_x"], keep_less=False, tolerance=tolerance)
    clipped = clip_polygon_axis(clipped, 0, bounds["max_x"], keep_less=True, tolerance=tolerance)
    clipped = clip_polygon_axis(clipped, 1, bounds["min_y"], keep_less=False, tolerance=tolerance)
    clipped = clip_polygon_axis(clipped, 1, bounds["max_y"], keep_less=True, tolerance=tolerance)
    return clipped


def line_intersection(left_start, left_end, right_start, right_end, tolerance):
    x1, y1 = float(left_start[0]), float(left_start[1])
    x2, y2 = float(left_end[0]), float(left_end[1])
    x3, y3 = float(right_start[0]), float(right_start[1])
    x4, y4 = float(right_end[0]), float(right_end[1])
    denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denominator) <= tolerance:
        return [x2, y2]
    px = (
        (x1 * y2 - y1 * x2) * (x3 - x4)
        - (x1 - x2) * (x3 * y4 - y3 * x4)
    ) / denominator
    py = (
        (x1 * y2 - y1 * x2) * (y3 - y4)
        - (y1 - y2) * (x3 * y4 - y3 * x4)
    ) / denominator
    return [px, py]


def clip_polygon_to_convex_polygon(subject_polygon, clip_polygon, tolerance):
    if not subject_polygon or not clip_polygon:
        return []

    clip_orientation = 1.0 if polygon_signed_area(clip_polygon) >= 0 else -1.0

    def inside(point, edge_start, edge_end):
        edge_length = math.hypot(
            float(edge_end[0]) - float(edge_start[0]),
            float(edge_end[1]) - float(edge_start[1]),
        )
        cross_tolerance = tolerance * max(edge_length, tolerance)
        return clip_orientation * cross(edge_start, edge_end, point) >= -cross_tolerance

    output = [[float(point[0]), float(point[1])] for point in subject_polygon]
    for index, edge_start in enumerate(clip_polygon):
        edge_end = clip_polygon[(index + 1) % len(clip_polygon)]
        if not output:
            break

        current_input = output
        output = []
        previous = current_input[-1]
        previous_inside = inside(previous, edge_start, edge_end)
        for current in current_input:
            current_inside = inside(current, edge_start, edge_end)
            if current_inside:
                if not previous_inside:
                    output.append(
                        line_intersection(
                            previous,
                            current,
                            edge_start,
                            edge_end,
                            max(tolerance * tolerance, 1e-18),
                        )
                    )
                output.append([float(current[0]), float(current[1])])
            elif previous_inside:
                output.append(
                    line_intersection(
                        previous,
                        current,
                        edge_start,
                        edge_end,
                        max(tolerance * tolerance, 1e-18),
                    )
                )
            previous = current
            previous_inside = current_inside

        output = clean_polygon(output, tolerance)

    return output


def resolve_source_data_path(result_path, result, override_path):
    if override_path:
        candidate = override_path
        return candidate if candidate.exists() else None

    source_file = result.get("source_file")
    if not source_file:
        return None
    raw = Path(source_file)
    candidates = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.extend(
            [
                Path.cwd() / raw,
                result_path.parent / raw,
                result_path.parent.parent / raw,
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def is_cluster_result(payload):
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("znro"), dict)
        and isinstance(payload.get("zpm"), dict)
        and isinstance(payload["znro"].get("clusters"), list)
        and isinstance(payload["zpm"].get("clusters"), list)
    )


def is_source_dataset(payload):
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("nodes"), list)
        and isinstance(payload.get("edges"), list)
    )


def build_clustering_args(input_json, args):
    return SimpleNamespace(
        input_json=input_json,
        nro_max_cap=args.nro_max_cap,
        zpm_max_cap=args.zpm_max_cap,
        spatial_weight=args.spatial_weight,
        pipe_weight=args.pipe_weight,
        allow_extra_clusters=args.allow_extra_clusters,
        unconnect_penalty=args.unconnect_penalty,
    )


def prepare_inputs(args):
    payload = load_json(args.input_json)
    if is_cluster_result(payload):
        source_data_path = resolve_source_data_path(args.input_json, payload, args.source_data_json)
        source_data = load_json(source_data_path) if source_data_path else None
        return {
            "input_kind": "cluster_result",
            "cluster_result": payload,
            "cluster_result_reference": args.input_json,
            "source_data": source_data,
            "source_data_path": source_data_path,
            "generated_cluster_result_path": None,
        }

    if is_source_dataset(payload):
        source_data_path = args.input_json
        source_data, cluster_result = build_clustering_result(build_clustering_args(args.input_json, args))
        generated_path = args.cluster_output_json
        if generated_path:
            dump_json(cluster_result, generated_path)
        return {
            "input_kind": "source_dataset",
            "cluster_result": cluster_result,
            "cluster_result_reference": generated_path or f"generated_from:{args.input_json}",
            "source_data": source_data,
            "source_data_path": source_data_path,
            "generated_cluster_result_path": generated_path,
        }

    raise ValueError(
        "Input JSON must be either a ZNRO/ZPM clustering result or a source FTTH dataset."
    )


def flatten_layer_members(layer):
    assignment = {}
    duplicates = []
    for cluster in layer.get("clusters", []):
        cluster_id = cluster["id"]
        for member in cluster.get("members", []):
            imb_id = member["id"]
            if imb_id in assignment:
                duplicates.append(imb_id)
            assignment[imb_id] = {
                "cluster_id": cluster_id,
                "member": member,
                "cluster": cluster,
            }
    return assignment, sorted(set(duplicates))


def extract_source_imbs(data):
    if not data:
        return {}
    return {
        node["id"]: node
        for node in data.get("nodes", [])
        if node_type(node) == "IMB"
    }


def build_imb_records(result, source_data):
    znro_members, znro_duplicates = flatten_layer_members(result["znro"])
    zpm_members, zpm_duplicates = flatten_layer_members(result["zpm"])
    source_imbs = extract_source_imbs(source_data)
    all_result_ids = sorted(set(znro_members) | set(zpm_members))

    records = {}
    for imb_id in all_result_ids:
        zpm_entry = zpm_members.get(imb_id)
        znro_entry = znro_members.get(imb_id)
        source_node = source_imbs.get(imb_id, {})
        member = (zpm_entry or znro_entry or {}).get("member", {})
        x = member.get("x", source_node.get("x"))
        y = member.get("y", source_node.get("y"))
        ports = member.get("ports", source_node.get("nb_loc_tot", source_node.get("ports", 0)))
        if x is None or y is None:
            raise ValueError(f"IMB {imb_id} is missing x/y coordinates in result and source data.")
        records[imb_id] = {
            "id": imb_id,
            "x": float(x),
            "y": float(y),
            "ports": int(ports),
            "znro_id": znro_entry["cluster_id"] if znro_entry else None,
            "zpm_id": zpm_entry["cluster_id"] if zpm_entry else None,
            "petch_id": member.get("petch_id", source_node.get("petch_id")),
        }

    expected_ids = set(source_imbs) if source_imbs else set(all_result_ids)
    return records, {
        "expected_imb_ids": sorted(expected_ids),
        "znro_missing_imb_ids": sorted(expected_ids - set(znro_members)),
        "zpm_missing_imb_ids": sorted(expected_ids - set(zpm_members)),
        "znro_duplicate_imb_ids": znro_duplicates,
        "zpm_duplicate_imb_ids": zpm_duplicates,
    }


def petch_node_map(source_data):
    if not source_data:
        return {}
    return {
        node["id"]: node
        for node in source_data.get("nodes", [])
        if node_type(node) in PETCH_TYPES
    }


def nearest_member_to_centroid(cluster):
    centroid = cluster.get("centroid", {})
    cx = float(centroid.get("x", 0.0))
    cy = float(centroid.get("y", 0.0))
    members = cluster.get("members", [])
    if not members:
        return {"id": None, "x": cx, "y": cy}
    return min(
        members,
        key=lambda member: (
            (float(member["x"]) - cx) ** 2 + (float(member["y"]) - cy) ** 2,
            member["id"],
        ),
    )


def choose_facility(cluster, region_cell_ids, cells_by_id, anchors, facility_prefix, tolerance):
    centroid = cluster.get("centroid", {})
    cx = float(centroid.get("x", 0.0))
    cy = float(centroid.get("y", 0.0))

    candidates = []
    for petch_id in cluster.get("petch_ids", []):
        anchor = anchors.get(petch_id)
        if anchor:
            candidates.append(anchor)
    candidates.sort(
        key=lambda node: (
            (float(node["x"]) - cx) ** 2 + (float(node["y"]) - cy) ** 2,
            node["id"],
        )
    )

    for candidate in candidates:
        point = (float(candidate["x"]), float(candidate["y"]))
        if point_in_cell_union(point, region_cell_ids, cells_by_id, tolerance):
            return {
                "id": f"{facility_prefix}_{cluster['id']}",
                "x": point[0],
                "y": point[1],
                "placement_source": "petch_anchor",
                "source_node_id": candidate["id"],
                "contained_in_region": True,
            }

    member = nearest_member_to_centroid(cluster)
    point = (float(member["x"]), float(member["y"]))
    return {
        "id": f"{facility_prefix}_{cluster['id']}",
        "x": point[0],
        "y": point[1],
        "placement_source": "member_imb_fallback",
        "source_node_id": member.get("id"),
        "contained_in_region": point_in_cell_union(point, region_cell_ids, cells_by_id, tolerance),
    }


def summarize_components(components):
    return [
        {
            "component_index": index + 1,
            "cell_count": len(component),
            "cell_ids": component,
        }
        for index, component in enumerate(components)
    ]


def segment_buffer_polygon(points, padding):
    start, end = points
    dx = float(end[0]) - float(start[0])
    dy = float(end[1]) - float(start[1])
    length = math.hypot(dx, dy)
    if length <= 0:
        half_size = padding or 1e-9
        x, y = float(start[0]), float(start[1])
        return [
            [x - half_size, y - half_size],
            [x + half_size, y - half_size],
            [x + half_size, y + half_size],
            [x - half_size, y + half_size],
        ]

    unit = (dx / length, dy / length)
    normal = (-unit[1], unit[0])
    return [
        [
            float(start[0]) - unit[0] * padding + normal[0] * padding,
            float(start[1]) - unit[1] * padding + normal[1] * padding,
        ],
        [
            float(end[0]) + unit[0] * padding + normal[0] * padding,
            float(end[1]) + unit[1] * padding + normal[1] * padding,
        ],
        [
            float(end[0]) + unit[0] * padding - normal[0] * padding,
            float(end[1]) + unit[1] * padding - normal[1] * padding,
        ],
        [
            float(start[0]) - unit[0] * padding - normal[0] * padding,
            float(start[1]) - unit[1] * padding - normal[1] * padding,
        ],
    ]


def offset_convex_polygon(polygon, padding):
    if padding <= 0:
        return [point[:] for point in polygon]
    if polygon_signed_area(polygon) < 0:
        polygon = list(reversed(polygon))

    shifted_edges = []
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        dx = float(end[0]) - float(start[0])
        dy = float(end[1]) - float(start[1])
        length = math.hypot(dx, dy)
        if length <= 0:
            continue
        outward = (dy / length, -dx / length)
        shifted_edges.append(
            (
                [float(start[0]) + outward[0] * padding, float(start[1]) + outward[1] * padding],
                [float(end[0]) + outward[0] * padding, float(end[1]) + outward[1] * padding],
            )
        )

    if len(shifted_edges) < 3:
        return [point[:] for point in polygon]

    expanded = []
    for index, current_edge in enumerate(shifted_edges):
        previous_edge = shifted_edges[index - 1]
        expanded.append(
            line_intersection(
                previous_edge[0],
                previous_edge[1],
                current_edge[0],
                current_edge[1],
                tolerance=1e-18,
            )
        )
    return expanded


def buffered_convex_hull_polygon_from_members(members, padding):
    points = [[float(member["x"]), float(member["y"])] for member in members]
    if not points:
        raise ValueError("Cannot build a ZNRO polygon without IMB members.")

    # IMB coordinates are longitude/latitude. Project the local hull to metres
    # before buffering so the requested 20 m extension is not distorted by
    # latitude-dependent degree lengths.
    center_x = sum(point[0] for point in points) / len(points)
    center_y = sum(point[1] for point in points) / len(points)
    latitude = math.radians(center_y)
    meters_per_latitude = (
        111132.92
        - 559.82 * math.cos(2.0 * latitude)
        + 1.175 * math.cos(4.0 * latitude)
        - 0.0023 * math.cos(6.0 * latitude)
    )
    meters_per_longitude = (
        111412.84 * math.cos(latitude)
        - 93.5 * math.cos(3.0 * latitude)
        + 0.118 * math.cos(5.0 * latitude)
    )
    metric_points = [
        [
            (point[0] - center_x) * meters_per_longitude,
            (point[1] - center_y) * meters_per_latitude,
        ]
        for point in points
    ]
    metric_hull = convex_hull(metric_points)
    effective_padding_m = max(float(padding), 1e-6)
    if len(metric_hull) == 1:
        metric_polygon = segment_buffer_polygon(
            [metric_hull[0], metric_hull[0]], effective_padding_m
        )
    elif len(metric_hull) == 2:
        metric_polygon = segment_buffer_polygon(metric_hull, effective_padding_m)
    else:
        metric_polygon = offset_convex_polygon(metric_hull, effective_padding_m)

    polygon = [
        [
            center_x + point[0] / meters_per_longitude,
            center_y + point[1] / meters_per_latitude,
        ]
        for point in metric_polygon
    ]
    base_hull = [
        [
            center_x + point[0] / meters_per_longitude,
            center_y + point[1] / meters_per_latitude,
        ]
        for point in metric_hull
    ]

    return polygon, {
        "base_hull_polygon": base_hull,
        "base_hull_area": polygon_area(base_hull),
        "hull_vertex_count": len(base_hull),
        "padding_m": effective_padding_m,
        "meters_per_degree": {
            "longitude": meters_per_longitude,
            "latitude": meters_per_latitude,
        },
    }


def metric_buffered_degenerate_polygon(points, padding_m):
    """Buffer a point/line IMB hull in local metres, then return lon/lat."""
    center_x = sum(float(point[0]) for point in points) / len(points)
    center_y = sum(float(point[1]) for point in points) / len(points)
    latitude = math.radians(center_y)
    meters_per_latitude = (
        111132.92
        - 559.82 * math.cos(2.0 * latitude)
        + 1.175 * math.cos(4.0 * latitude)
        - 0.0023 * math.cos(6.0 * latitude)
    )
    meters_per_longitude = (
        111412.84 * math.cos(latitude)
        - 93.5 * math.cos(3.0 * latitude)
        + 0.118 * math.cos(5.0 * latitude)
    )
    metric_points = [
        [
            (float(point[0]) - center_x) * meters_per_longitude,
            (float(point[1]) - center_y) * meters_per_latitude,
        ]
        for point in points
    ]
    metric_polygon = segment_buffer_polygon(metric_points, max(float(padding_m), 1e-3))
    return [
        [
            center_x + point[0] / meters_per_longitude,
            center_y + point[1] / meters_per_latitude,
        ]
        for point in metric_polygon
    ]


def circular_arc(center, start_angle, end_angle, segments, clockwise=False, radius=1.0):
    """Return an arc including both tangent points, approximated by line segments."""
    if clockwise:
        while end_angle >= start_angle:
            end_angle -= 2.0 * math.pi
    else:
        while end_angle <= start_angle:
            end_angle += 2.0 * math.pi
    return [
        [
            float(center[0]) + float(radius) * math.cos(start_angle + (end_angle - start_angle) * index / segments),
            float(center[1]) + float(radius) * math.sin(start_angle + (end_angle - start_angle) * index / segments),
        ]
        for index in range(segments + 1)
    ]


def rounded_buffered_convex_hull_polygon_from_members(members, padding, corner_segments):
    """Build a rounded convex hull that strictly contains every assigned IMB.

    The polygon is the convex hull of the IMB coordinates buffered by a small
    circle.  It is represented as a dense linear ring so it remains compatible
    with the project's JSON and plotting stack without an optional GIS library.
    """
    points = [[float(member["x"]), float(member["y"])] for member in members]
    if not points:
        raise ValueError("Cannot build a smoothed ZPM polygon without IMB members.")

    hull = convex_hull(points)
    effective_padding = padding if padding > 0 else 1e-12
    arc_segments = max(int(corner_segments), 2)

    if len(hull) == 1:
        polygon = circular_arc(
            hull[0],
            0.0,
            2.0 * math.pi,
            max(arc_segments * 4, 8),
            radius=effective_padding,
        )[:-1]
    elif len(hull) == 2:
        start, end = hull
        dx = float(end[0]) - float(start[0])
        dy = float(end[1]) - float(start[1])
        length = math.hypot(dx, dy)
        if length <= 1e-18:
            polygon = circular_arc(
                start,
                0.0,
                2.0 * math.pi,
                max(arc_segments * 4, 8),
                radius=effective_padding,
            )[:-1]
        else:
            left_normal_angle = math.atan2(dx / length, -dy / length)
            right_normal_angle = math.atan2(-dx / length, dy / length)
            first = [
                float(start[0]) + math.cos(left_normal_angle) * effective_padding,
                float(start[1]) + math.sin(left_normal_angle) * effective_padding,
            ]
            second = [
                float(end[0]) + math.cos(left_normal_angle) * effective_padding,
                float(end[1]) + math.sin(left_normal_angle) * effective_padding,
            ]
            end_cap = circular_arc(
                end,
                left_normal_angle,
                right_normal_angle,
                arc_segments,
                clockwise=True,
                radius=effective_padding,
            )
            start_cap = circular_arc(
                start,
                right_normal_angle,
                left_normal_angle,
                arc_segments,
                clockwise=True,
                radius=effective_padding,
            )
            polygon = [first, second] + end_cap[1:] + [
                [
                    float(start[0]) + math.cos(right_normal_angle) * effective_padding,
                    float(start[1]) + math.sin(right_normal_angle) * effective_padding,
                ]
            ] + start_cap[1:-1]
    else:
        if polygon_signed_area(hull) < 0:
            hull = list(reversed(hull))
        polygon = []
        for index, vertex in enumerate(hull):
            previous = hull[index - 1]
            following = hull[(index + 1) % len(hull)]
            previous_dx = float(vertex[0]) - float(previous[0])
            previous_dy = float(vertex[1]) - float(previous[1])
            following_dx = float(following[0]) - float(vertex[0])
            following_dy = float(following[1]) - float(vertex[1])
            previous_length = math.hypot(previous_dx, previous_dy)
            following_length = math.hypot(following_dx, following_dy)
            if previous_length <= 1e-18 or following_length <= 1e-18:
                continue
            previous_normal_angle = math.atan2(
                -previous_dx / previous_length,
                previous_dy / previous_length,
            )
            following_normal_angle = math.atan2(
                -following_dx / following_length,
                following_dy / following_length,
            )
            arc = circular_arc(
                vertex,
                previous_normal_angle,
                following_normal_angle,
                arc_segments,
                radius=effective_padding,
            )
            if polygon:
                polygon.extend(arc[1:])
            else:
                polygon.extend(arc)

    polygon = clean_polygon(polygon, effective_padding * 1e-8)
    if len(polygon) < 3:
        polygon = segment_buffer_polygon([hull[0], hull[-1]], effective_padding)
    return polygon, {
        "base_hull_polygon": hull,
        "base_hull_area": polygon_area(hull),
        "hull_vertex_count": len(hull),
        "padding": effective_padding,
        "corner_segments": arc_segments,
        "boundary_vertex_count": len(polygon),
    }


def boundary_rings_from_polygons(polygons, tolerance):
    """Extract oriented outer rings from a polygon union's exposed edges."""
    edges = defaultdict(list)
    for polygon in polygons:
        if len(polygon) < 3:
            continue
        for index, start in enumerate(polygon):
            end = polygon[(index + 1) % len(polygon)]
            if math.hypot(float(start[0]) - float(end[0]), float(start[1]) - float(end[1])) <= tolerance:
                continue
            edges[cell_edge_key(start, end, tolerance)].append((start, end))

    exposed = [segments[0] for segments in edges.values() if len(segments) == 1]
    if not exposed:
        return []

    by_node = defaultdict(list)
    for index, (start, end) in enumerate(exposed):
        by_node[quantized_point(start, tolerance)].append(index)
        by_node[quantized_point(end, tolerance)].append(index)

    unused = set(range(len(exposed)))
    rings = []
    while unused:
        first_index = min(unused)
        first_start, first_end = exposed[first_index]
        start_node = quantized_point(first_start, tolerance)
        ring = [[float(first_start[0]), float(first_start[1])]]
        current_node = start_node
        current_index = first_index
        current_point = first_start
        while True:
            unused.discard(current_index)
            edge_start, edge_end = exposed[current_index]
            if quantized_point(edge_start, tolerance) == current_node:
                next_point = edge_end
            else:
                next_point = edge_start
            ring.append([float(next_point[0]), float(next_point[1])])
            next_node = quantized_point(next_point, tolerance)
            if next_node == start_node:
                break
            candidates = [candidate for candidate in by_node[next_node] if candidate in unused]
            if not candidates:
                break
            current_index = min(candidates)
            current_node = next_node
            current_point = next_point
        cleaned = clean_polygon(ring[:-1], tolerance)
        if len(cleaned) >= 3:
            rings.append(cleaned)
    rings.sort(key=polygon_area, reverse=True)
    return rings


def chaikin_smooth_polygon(polygon, iterations=2):
    """Round corners by repeatedly cutting each polygon corner inward."""
    smoothed = [[float(point[0]), float(point[1])] for point in polygon]
    for _ in range(max(0, int(iterations))):
        if len(smoothed) < 3:
            break
        next_polygon = []
        for index, point in enumerate(smoothed):
            following = smoothed[(index + 1) % len(smoothed)]
            next_polygon.append([
                0.75 * point[0] + 0.25 * following[0],
                0.75 * point[1] + 0.25 * following[1],
            ])
            next_polygon.append([
                0.25 * point[0] + 0.75 * following[0],
                0.25 * point[1] + 0.75 * following[1],
            ])
        smoothed = next_polygon
    return smoothed


def smoothed_polygon_from_cell_union(cluster, polygons, parent_polygon, tolerance, iterations):
    """Create one smooth, non-expanding boundary from the clipped cell union."""
    rings = boundary_rings_from_polygons(
        [item["polygon"] for item in polygons],
        tolerance,
    )
    if not rings:
        return [], {"method": "cell_union_boundary", "iterations": 0, "ring_count": 0}
    # A connected ZPM normally has one ring. For unusual disconnected input,
    # use the outer ring as a deterministic single-region fallback.
    base = rings[0]
    candidate_iterations = list(range(max(0, int(iterations)), -1, -1))
    for current_iterations in candidate_iterations:
        candidate = chaikin_smooth_polygon(base, current_iterations)
        candidate = clip_polygon_to_convex_polygon(candidate, parent_polygon, tolerance)
        if candidate and len(candidate) >= 3:
            covers = all(
                point_in_polygon((float(member["x"]), float(member["y"])), candidate, tolerance)
                for member in cluster.get("members", [])
            )
            if covers:
                return candidate, {
                    "method": "chaikin_cell_union_boundary",
                    "iterations": current_iterations,
                    "ring_count": len(rings),
                    "base_vertex_count": len(base),
                    "vertex_count": len(candidate),
                    "covers_assigned_imb_points": True,
                }
    base_covers = all(
        point_in_polygon((float(member["x"]), float(member["y"])), base, tolerance)
        for member in cluster.get("members", [])
    )
    return base, {
        "method": "cell_union_boundary_no_smoothing",
        "iterations": 0,
        "ring_count": len(rings),
        "base_vertex_count": len(base),
        "vertex_count": len(base),
        "covers_assigned_imb_points": base_covers,
    }


def minimal_imb_hull_polygon(cluster, parent_polygon, tolerance):
    """Build the lowest-edge polygon that covers the assigned IMB points."""
    members = cluster.get("members", [])
    points = [[float(member["x"]), float(member["y"])] for member in members]
    if not points:
        return [], {"method": "imb_convex_hull", "vertex_count": 0, "covers_assigned_imb_points": False}

    hull = convex_hull(points)
    if len(hull) == 1:
        # Degenerate clusters still need a visible, valid polygon for GIS.
        polygon = metric_buffered_degenerate_polygon([hull[0]], padding_m=2.0)
    elif len(hull) == 2:
        polygon = metric_buffered_degenerate_polygon(hull, padding_m=2.0)
    else:
        polygon = hull

    polygon = clip_polygon_to_convex_polygon(polygon, parent_polygon, tolerance)
    covers = bool(polygon) and all(
        point_in_polygon((float(member["x"]), float(member["y"])), polygon, tolerance)
        for member in members
    )
    return polygon, {
        "method": "imb_convex_hull",
        "base_hull_vertex_count": len(hull),
        "vertex_count": len(polygon),
        "covers_assigned_imb_points": covers,
        "nested_in_parent_znro": bool(polygon)
        and all(point_in_polygon(point, parent_polygon, tolerance) for point in polygon),
    }


def polygon_projection(polygon, axis):
    return [float(point[0]) * axis[0] + float(point[1]) * axis[1] for point in polygon]


def convex_separating_axis(left_polygon, right_polygon, tolerance):
    """Find an edge-normal axis separating two disjoint convex polygons."""
    axes = []
    for polygon in (left_polygon, right_polygon):
        for index, start in enumerate(polygon):
            end = polygon[(index + 1) % len(polygon)]
            dx = float(end[0]) - float(start[0])
            dy = float(end[1]) - float(start[1])
            length = math.hypot(dx, dy)
            if length <= tolerance:
                continue
            axes.append((-dy / length, dx / length))

    # A one-point hull has no edges, and a two-point hull only provides one
    # edge normal.  Add axes through cross-hull point pairs so degenerate
    # clusters still get a separating line when they are disjoint.
    for left_point in left_polygon:
        for right_point in right_polygon:
            dx = float(right_point[0]) - float(left_point[0])
            dy = float(right_point[1]) - float(left_point[1])
            length = math.hypot(dx, dy)
            if length <= tolerance:
                continue
            axes.append((dx / length, dy / length))
            axes.append((-dy / length, dx / length))

    best = None
    best_gap = -float("inf")
    for axis in axes:
        left_values = polygon_projection(left_polygon, axis)
        right_values = polygon_projection(right_polygon, axis)
        left_min, left_max = min(left_values), max(left_values)
        right_min, right_max = min(right_values), max(right_values)
        if left_max <= right_min + tolerance:
            gap = right_min - left_max
            if gap > best_gap:
                best = (axis, True, left_max, right_min)
                best_gap = gap
        if right_max <= left_min + tolerance:
            gap = left_min - right_max
            if gap > best_gap:
                best = (axis, False, right_max, left_min)
                best_gap = gap
    return best


def apply_zpm_boundary_extension(zpm_regions, zpm_clusters, parent_tolerance, extension_m):
    """Extend each ZPM by metres, then clip neighbours at separating lines."""
    if not zpm_regions:
        return
    cluster_by_id = {cluster["id"]: cluster for cluster in zpm_clusters}
    candidates = {}
    base_hulls = {}
    for region in zpm_regions:
        cluster = cluster_by_id[region["id"]]
        members = cluster.get("members", [])
        base_hulls[region["id"]] = convex_hull(
            [[float(member["x"]), float(member["y"])] for member in members]
        )
        expanded, metrics = buffered_convex_hull_polygon_from_members(members, extension_m)
        expanded = clip_polygon_to_convex_polygon(
            expanded,
            region["geometry"]["clip_parent_polygon"],
            parent_tolerance,
        )
        if not expanded:
            raise ValueError(f"ZPM {region['id']} has no valid expanded polygon inside its ZNRO parent.")
        candidates[region["id"]] = (expanded, metrics)

    region_ids = [region["id"] for region in zpm_regions]
    for left_index, left_id in enumerate(region_ids):
        for right_id in region_ids[left_index + 1:]:
            separation = convex_separating_axis(
                base_hulls[left_id],
                base_hulls[right_id],
                parent_tolerance,
            )
            if separation is None:
                raise ValueError(
                    f"Cannot create non-overlapping {extension_m:g} m ZPM extensions for {left_id} and {right_id}: "
                    "their assigned IMB convex hulls overlap."
                )
            axis, left_is_lower, left_max, right_min = separation
            threshold = (left_max + right_min) / 2.0
            left_polygon, left_metrics = candidates[left_id]
            right_polygon, right_metrics = candidates[right_id]
            if left_is_lower:
                left_polygon = clip_polygon_to_linear_halfplane(
                    left_polygon, axis, threshold, keep_less=True, tolerance=parent_tolerance
                )
                right_polygon = clip_polygon_to_linear_halfplane(
                    right_polygon, axis, threshold, keep_less=False, tolerance=parent_tolerance
                )
            else:
                left_polygon = clip_polygon_to_linear_halfplane(
                    left_polygon, axis, threshold, keep_less=False, tolerance=parent_tolerance
                )
                right_polygon = clip_polygon_to_linear_halfplane(
                    right_polygon, axis, threshold, keep_less=True, tolerance=parent_tolerance
                )
            if not left_polygon or not right_polygon:
                raise ValueError(f"ZPM extension clipping removed {left_id} or {right_id} geometry.")
            candidates[left_id] = (left_polygon, left_metrics)
            candidates[right_id] = (right_polygon, right_metrics)

    for region in zpm_regions:
        polygon, metrics = candidates[region["id"]]
        cluster = cluster_by_id[region["id"]]
        covers = all(
            point_in_polygon((float(member["x"]), float(member["y"])), polygon, parent_tolerance)
            for member in cluster.get("members", [])
        )
        if not covers:
            raise ValueError(f"ZPM {region['id']} extension boundary no longer covers all assigned IMBs.")
        geometry = region["geometry"]
        geometry["type"] = "disjoint_buffered_imb_convex_hull_polygon"
        geometry["polygon"] = polygon
        geometry["smoothed_polygon"] = polygon
        geometry["smoothed_area"] = polygon_area(polygon)
        geometry["boundary_extension_m"] = float(extension_m)
        geometry["base_hull_polygon"] = base_hulls[region["id"]]
        geometry["smoothed_boundary"] = {
            "method": "disjoint_buffered_imb_convex_hull",
            "extension_m": float(extension_m),
            "base_hull_vertex_count": len(base_hulls[region["id"]]),
            "vertex_count": len(polygon),
            "covers_assigned_imb_points": covers,
            "nested_in_parent_znro": all(
                point_in_polygon(point, geometry["clip_parent_polygon"], parent_tolerance)
                for point in polygon
            ),
            "meters_per_degree": metrics.get("meters_per_degree"),
        }


def point_polygon_boundary_distance(point, polygon):
    if not polygon:
        return 0.0
    return math.sqrt(
        min(
            point_segment_distance_sq(point, polygon[index], polygon[(index + 1) % len(polygon)])
            for index in range(len(polygon))
        )
    )


def point_polygon_boundary_distance_m(point, polygon):
    """Approximate a lon/lat boundary distance in metres using a local scale."""
    if not polygon:
        return 0.0
    center_lat = math.radians(sum(float(item[1]) for item in polygon) / len(polygon))
    meters_per_latitude = (
        111132.92
        - 559.82 * math.cos(2.0 * center_lat)
        + 1.175 * math.cos(4.0 * center_lat)
        - 0.0023 * math.cos(6.0 * center_lat)
    )
    meters_per_longitude = (
        111412.84 * math.cos(center_lat)
        - 93.5 * math.cos(3.0 * center_lat)
        + 0.118 * math.cos(5.0 * center_lat)
    )

    def project(value):
        return [float(value[0]) * meters_per_longitude, float(value[1]) * meters_per_latitude]

    metric_point = project(point)
    metric_polygon = [project(item) for item in polygon]
    return math.sqrt(
        min(
            point_segment_distance_sq(
                metric_point,
                metric_polygon[index],
                metric_polygon[(index + 1) % len(metric_polygon)],
            )
            for index in range(len(metric_polygon))
        )
    )


def min_member_boundary_distance(members, polygon):
    if not members:
        return 0.0
    return min(
        point_polygon_boundary_distance((float(member["x"]), float(member["y"])), polygon)
        for member in members
    )


def min_member_boundary_distance_m(members, polygon):
    if not members:
        return 0.0
    return min(
        point_polygon_boundary_distance_m((float(member["x"]), float(member["y"])), polygon)
        for member in members
    )


def build_znro_polygon_record(
    cluster,
    cell_ids,
    cells_by_id,
    facility,
    padding_m,
    zpm_ids=None,
):
    members = cluster.get("members", [])
    polygon, polygon_metrics = buffered_convex_hull_polygon_from_members(members, padding_m)
    bounds = polygon_axis_bounds_dict(polygon)
    total_area = polygon_area(polygon)
    min_boundary_distance = min_member_boundary_distance(members, polygon)
    min_boundary_distance_m = min_member_boundary_distance_m(members, polygon)
    facility["contained_in_region"] = point_in_polygon(
        (float(facility["x"]), float(facility["y"])),
        polygon,
        1e-12,
    )
    if not facility["contained_in_region"]:
        member = nearest_member_to_centroid(cluster)
        facility = {
            "id": f"NRO_{cluster['id']}",
            "x": float(member["x"]),
            "y": float(member["y"]),
            "placement_source": "member_imb_fallback_znro_polygon",
            "source_node_id": member.get("id"),
            "contained_in_region": True,
        }
    record = {
        "id": cluster["id"],
        "total_ports": cluster["total_ports"],
        "capacity_limit": cluster["capacity_limit"],
        "capacity_utilization": cluster.get(
            "capacity_utilization",
            round(cluster["total_ports"] / cluster["capacity_limit"], 4),
        ),
        "member_count": cluster.get("member_count", len(members)),
        "imb_ids": sorted(member["id"] for member in members),
        "facility": facility,
        "geometry": {
            "type": "buffered_convex_hull_polygon",
            "area": total_area,
            "polygon": polygon,
            "bounds": {
                "min_x": bounds["min_x"],
                "min_y": bounds["min_y"],
                "max_x": bounds["max_x"],
                "max_y": bounds["max_y"],
                "padding_m": padding_m,
            },
            "base_hull_polygon": polygon_metrics["base_hull_polygon"],
            "base_hull_area": polygon_metrics["base_hull_area"],
            "hull_vertex_count": polygon_metrics["hull_vertex_count"],
            "effective_padding_m": polygon_metrics["padding_m"],
            "meters_per_degree": polygon_metrics["meters_per_degree"],
            "min_imb_boundary_distance": min_boundary_distance,
            "min_imb_boundary_distance_m": min_boundary_distance_m,
            "cell_ids": cell_ids,
            "covers_assigned_imb_points": all(
                point_in_polygon((float(member["x"]), float(member["y"])), polygon, 1e-12)
                for member in members
            ),
            "imbs_strictly_inside": min_boundary_distance > 0,
            "connected_component_count": 1,
            "continuous_region": True,
        },
    }
    if zpm_ids is not None:
        record["zpm_ids"] = sorted(zpm_ids)
    return record


def build_region_record(
    cluster,
    cell_ids,
    cells_by_id,
    adjacency,
    facility,
    parent_znro_id=None,
    zpm_ids=None,
):
    components = connected_components(cell_ids, adjacency)
    total_area = sum(cells_by_id[cell_id]["area"] for cell_id in cell_ids)
    record = {
        "id": cluster["id"],
        "total_ports": cluster["total_ports"],
        "capacity_limit": cluster["capacity_limit"],
        "capacity_utilization": cluster.get(
            "capacity_utilization",
            round(cluster["total_ports"] / cluster["capacity_limit"], 4),
        ),
        "member_count": cluster.get("member_count", len(cluster.get("members", []))),
        "imb_ids": sorted(member["id"] for member in cluster.get("members", [])),
        "facility": facility,
        "geometry": {
            "type": "cell_union",
            "area": total_area,
            "polygon_count": len(cell_ids),
            "cell_ids": cell_ids,
            "connected_component_count": len(components),
            "continuous_region": len(components) <= 1,
            "connected_components": summarize_components(components),
        },
    }
    if parent_znro_id is not None:
        record["parent_znro_id"] = parent_znro_id
    if zpm_ids is not None:
        record["zpm_ids"] = sorted(zpm_ids)
    return record


def clipped_cell_polygons(cell_ids, cells_by_id, clip_polygon, tolerance):
    polygons = []
    total_area = 0.0
    for cell_id in cell_ids:
        polygon = clip_polygon_to_convex_polygon(
            cells_by_id[cell_id]["polygon"],
            clip_polygon,
            tolerance,
        )
        if not polygon:
            continue
        area = polygon_area(polygon)
        total_area += area
        polygons.append(
            {
                "cell_id": cell_id,
                "polygon": polygon,
                "area": area,
            }
        )
    return polygons, total_area


def ensure_facility_in_polygons(cluster, facility, polygons, tolerance, facility_prefix):
    point = (float(facility["x"]), float(facility["y"]))
    raw_polygons = [item["polygon"] for item in polygons]
    if point_in_polygons(point, raw_polygons, tolerance):
        facility["contained_in_region"] = True
        return facility

    member = nearest_member_to_centroid(cluster)
    fallback = {
        "id": f"{facility_prefix}_{cluster['id']}",
        "x": float(member["x"]),
        "y": float(member["y"]),
        "placement_source": "member_imb_fallback_clipped_region",
        "source_node_id": member.get("id"),
        "contained_in_region": point_in_polygons(
            (float(member["x"]), float(member["y"])),
            raw_polygons,
            tolerance,
        ),
    }
    return fallback


def build_clipped_zpm_region_record(
    cluster,
    cell_ids,
    cells_by_id,
    adjacency,
    facility,
    parent_znro_region,
    tolerance,
):
    parent_polygon = parent_znro_region["geometry"]["polygon"]
    parent_bounds = parent_znro_region["geometry"]["bounds"]
    polygons, total_area = clipped_cell_polygons(cell_ids, cells_by_id, parent_polygon, tolerance)
    facility = ensure_facility_in_polygons(cluster, facility, polygons, tolerance, "PM")
    components = connected_components(cell_ids, adjacency)
    # Use the convex hull of the assigned IMBs as the presentation boundary.
    # It has the fewest possible edges while retaining every required point;
    # the clipped cell union remains authoritative for exact ownership.
    smooth_polygon, smooth_metrics = minimal_imb_hull_polygon(
        cluster,
        parent_polygon,
        tolerance,
    )
    if not smooth_polygon:
        smooth_polygon = [point[:] for point in parent_polygon]
    smooth_area = polygon_area(smooth_polygon)
    smooth_covers_imbs = all(
        point_in_polygon((float(member["x"]), float(member["y"])), smooth_polygon, tolerance)
        for member in cluster.get("members", [])
    )
    return {
        "id": cluster["id"],
        "parent_znro_id": cluster.get("parent_znro_id"),
        "total_ports": cluster["total_ports"],
        "capacity_limit": cluster["capacity_limit"],
        "capacity_utilization": cluster.get(
            "capacity_utilization",
            round(cluster["total_ports"] / cluster["capacity_limit"], 4),
        ),
        "member_count": cluster.get("member_count", len(cluster.get("members", []))),
        "imb_ids": sorted(member["id"] for member in cluster.get("members", [])),
        "facility": facility,
        "geometry": {
            "type": "minimal_imb_convex_hull_polygon",
            "polygon": smooth_polygon,
            "smoothed_polygon": smooth_polygon,
            "smoothed_area": smooth_area,
            "smoothed_boundary": {
                "method": "imb_convex_hull",
                "smoothing_method": smooth_metrics.get("method"),
                "base_hull_vertex_count": smooth_metrics.get("base_hull_vertex_count", 0),
                "vertex_count": len(smooth_polygon),
                "covers_assigned_imb_points": smooth_covers_imbs,
                "nested_in_parent_znro": smooth_metrics.get("nested_in_parent_znro", False),
            },
            # Original cell union retained for exact ownership and diagnostics.
            "raw_geometry_type": "clipped_cell_union",
            "area": total_area,
            "polygon_count": len(polygons),
            "cell_ids": cell_ids,
            "polygons": polygons,
            "clip_parent_znro_id": parent_znro_region["id"],
            "clip_parent_polygon": parent_polygon,
            "clip_parent_bounds": parent_bounds,
            "connected_component_count": len(components),
            "continuous_region": len(components) <= 1,
            "connected_components": summarize_components(components),
        },
    }


def round_numbers(value, digits):
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, list):
        return [round_numbers(item, digits) for item in value]
    if isinstance(value, dict):
        return {key: round_numbers(item, digits) for key, item in value.items()}
    return value


def build_partition(result_path, result, source_data, source_data_path, args):
    imb_records, assignment_validation = build_imb_records(result, source_data)
    if not imb_records:
        raise ValueError("The clustering result does not contain any IMB members.")

    bounds = build_bounds(source_data, imb_records, args.boundary_padding_ratio)
    sites, duplicate_coordinate_groups = adjusted_sites(imb_records, bounds)
    cells = build_voronoi_cells(sites, bounds)
    cells_by_id = {cell["id"]: cell for cell in cells}
    adjacency = build_cell_adjacency(cells, bounds)
    facility_tolerance = max(bounds.get("numeric_scale", bounds["scale"]) * 1e-8, 1e-12)
    anchors = petch_node_map(source_data)

    znro_cells = defaultdict(list)
    zpm_cells = defaultdict(list)
    for cell in cells:
        if cell["owner_znro_id"]:
            znro_cells[cell["owner_znro_id"]].append(cell["id"])
        if cell["owner_zpm_id"]:
            zpm_cells[cell["owner_zpm_id"]].append(cell["id"])

    for owner_cells in (znro_cells, zpm_cells):
        for key in owner_cells:
            owner_cells[key].sort()

    zpm_parent = {
        cluster["id"]: cluster.get("parent_znro_id")
        for cluster in result["zpm"].get("clusters", [])
    }
    znro_to_zpm = defaultdict(list)
    for zpm_id, parent_id in zpm_parent.items():
        if parent_id:
            znro_to_zpm[parent_id].append(zpm_id)

    znro_regions = []
    for cluster in result["znro"].get("clusters", []):
        cell_ids = znro_cells.get(cluster["id"], [])
        facility = choose_facility(
            cluster,
            cell_ids,
            cells_by_id,
            anchors,
            "NRO",
            facility_tolerance,
        )
        znro_regions.append(
            build_znro_polygon_record(
                cluster,
                cell_ids,
                cells_by_id,
                facility,
                args.znro_boundary_extension_m,
                zpm_ids=znro_to_zpm.get(cluster["id"], []),
            )
        )
    znro_region_by_id = {region["id"]: region for region in znro_regions}

    zpm_regions = []
    for cluster in result["zpm"].get("clusters", []):
        cell_ids = zpm_cells.get(cluster["id"], [])
        parent_znro = znro_region_by_id.get(cluster.get("parent_znro_id"))
        if parent_znro is None:
            raise ValueError(
                f"ZPM cluster {cluster['id']} is missing a valid parent ZNRO region."
            )
        facility = choose_facility(
            cluster,
            cell_ids,
            cells_by_id,
            anchors,
            "PM",
            facility_tolerance,
        )
        zpm_regions.append(
            build_clipped_zpm_region_record(
                cluster,
                cell_ids,
                cells_by_id,
                adjacency,
                facility,
                parent_znro,
                facility_tolerance,
            )
        )

    # ZPM boundaries use the same metric extension as ZNRO by default.  The
    # pairwise clipping keeps neighbouring extended polygons disjoint while
    # preserving the exact Voronoi-cell ownership stored above.
    zpm_boundary_extension_m = getattr(args, "zpm_boundary_extension_m", None)
    if zpm_boundary_extension_m is None:
        zpm_boundary_extension_m = args.znro_boundary_extension_m
    apply_zpm_boundary_extension(
        zpm_regions,
        result["zpm"].get("clusters", []),
        facility_tolerance,
        zpm_boundary_extension_m,
    )

    validation = validate_region_partition(
        result,
        assignment_validation,
        cells,
        znro_regions,
        zpm_regions,
        znro_cells,
        zpm_cells,
        zpm_parent,
    )

    rounded_cells = [
        {
            **cell,
            "polygon": round_numbers(cell["polygon"], args.round_digits),
            "area": round(cell["area"], args.round_digits),
            "site": round_numbers(cell["site"], args.round_digits),
        }
        for cell in cells
    ]

    output = {
        "source_cluster_result": str(result_path),
        "source_data_file": str(source_data_path) if source_data_path else None,
        "algorithm": {
            "name": "Non-overlapping ZNRO/ZPM region partition",
            "method": (
                "ZNRO regions are IMB convex hulls buffered by the requested metre distance; "
                "ZPM regions use equally buffered IMB convex hulls clipped at separating lines "
                "so neighbouring ZPM polygons do not overlap; "
                "the finite Voronoi-cell unions remain available for exact ownership and are clipped to the parent ZNRO polygon."
            ),
            "non_overlap_model": (
                "ZPM cell ownership is unique and extended ZPM polygons may touch at boundaries but do not overlap. "
                "ZNRO polygons are driven by ZNRO cluster assignment."
            ),
        },
        "parameters": {
            "boundary_padding_ratio": args.boundary_padding_ratio,
            "znro_boundary_extension_m": args.znro_boundary_extension_m,
            "zpm_boundary_extension_m": zpm_boundary_extension_m,
            "round_digits": args.round_digits,
        },
        "service_boundary": {
            "type": "rectangle",
            "bounds": {
                "min_x": bounds["min_x"],
                "min_y": bounds["min_y"],
                "max_x": bounds["max_x"],
                "max_y": bounds["max_y"],
                "padding": bounds["padding"],
            },
            "polygon": boundary_polygon(bounds),
        },
        "cell_generation": {
            "cell_count": len(cells),
            "duplicate_coordinate_group_count": len(duplicate_coordinate_groups),
            "duplicate_coordinate_groups": duplicate_coordinate_groups,
        },
        "validation": validation,
        "znro_regions": znro_regions,
        "zpm_regions": zpm_regions,
        "cells": rounded_cells,
    }
    return round_numbers(output, args.round_digits)


def unique_cell_ownership(regions):
    counts = Counter(
        cell_id
        for region in regions
        for cell_id in region["geometry"].get("cell_ids", [])
    )
    duplicates = sorted(cell_id for cell_id, count in counts.items() if count > 1)
    return not duplicates, duplicates


def convex_polygon_overlap_area(left_polygon, right_polygon):
    overlap_polygon = clip_polygon_to_convex_polygon(left_polygon, right_polygon, tolerance=1e-12)
    area = polygon_area(overlap_polygon)
    # Treat machine-precision slivers left by clipping touching boundaries as
    # zero; callers use this helper for geometric non-overlap validation.
    return 0.0 if area <= polygon_area_tolerance(left_polygon, right_polygon) else area


def polygon_area_tolerance(*polygons):
    """Return a scale-aware area epsilon for floating-point polygon clipping."""
    max_coordinate = max(
        [
            abs(float(point[axis]))
            for polygon in polygons
            for point in polygon
            for axis in (0, 1)
        ]
        + [1.0]
    )
    # Coordinates are commonly lon/lat values around +/-30; clipping two
    # touching polygons can leave a machine-precision sliver around 1e-13.
    return max(1e-18, 1e-15 * max_coordinate * max_coordinate)


def znro_polygon_overlaps(znro_regions):
    overlaps = []
    for left_index, left in enumerate(znro_regions):
        left_polygon = left["geometry"].get("polygon")
        if not left_polygon:
            continue
        for right in znro_regions[left_index + 1:]:
            right_polygon = right["geometry"].get("polygon")
            if not right_polygon:
                continue
            overlap_area = convex_polygon_overlap_area(left_polygon, right_polygon)
            if overlap_area > polygon_area_tolerance(left_polygon, right_polygon):
                overlaps.append(
                    {
                        "left_znro_id": left["id"],
                        "right_znro_id": right["id"],
                        "overlap_area": overlap_area,
                    }
                )
    return overlaps


def zpm_polygon_overlaps(zpm_regions):
    """Return positive-area intersections between extended ZPM polygons."""
    overlaps = []
    for left_index, left in enumerate(zpm_regions):
        left_polygon = (
            left.get("geometry", {}).get("smoothed_polygon")
            or left.get("geometry", {}).get("polygon")
        )
        if not left_polygon:
            continue
        for right in zpm_regions[left_index + 1 :]:
            right_polygon = (
                right.get("geometry", {}).get("smoothed_polygon")
                or right.get("geometry", {}).get("polygon")
            )
            if not right_polygon:
                continue
            overlap_area = convex_polygon_overlap_area(left_polygon, right_polygon)
            if overlap_area > polygon_area_tolerance(left_polygon, right_polygon):
                overlaps.append(
                    {
                        "left_zpm_id": left["id"],
                        "right_zpm_id": right["id"],
                        "overlap_area": overlap_area,
                    }
                )
    return overlaps


def znro_boundary_violations(znro_regions):
    return [
        {
            "znro_id": region["id"],
            "min_imb_boundary_distance": region["geometry"].get("min_imb_boundary_distance", 0.0),
        }
        for region in znro_regions
        if not region["geometry"].get("imbs_strictly_inside")
    ]


def zpm_geometry_parent_violations(zpm_regions):
    violations = []
    for region in zpm_regions:
        parent_polygon = region["geometry"].get("clip_parent_polygon")
        if not parent_polygon:
            continue
        outside_points = []
        smooth_polygon = (
            region["geometry"].get("smoothed_polygon")
            or region["geometry"].get("polygon")
            or []
        )
        for point in smooth_polygon:
            if not point_in_polygon((point[0], point[1]), parent_polygon, tolerance=1e-12):
                outside_points.append(point)
        for polygon_record in region["geometry"].get("polygons", []):
            for point in polygon_record.get("polygon", []):
                if not point_in_polygon((point[0], point[1]), parent_polygon, tolerance=1e-12):
                    outside_points.append(point)
        if outside_points:
            violations.append(
                {
                    "zpm_id": region["id"],
                    "parent_znro_id": region.get("parent_znro_id"),
                    "outside_point_count": len(outside_points),
                    "sample_outside_points": outside_points[:5],
                }
            )
    return violations


def validate_region_partition(
    cluster_result,
    assignment_validation,
    cells,
    znro_regions,
    zpm_regions,
    znro_cells,
    zpm_cells,
    zpm_parent,
):
    znro_polygon_overlap_violations = znro_polygon_overlaps(znro_regions)
    zpm_polygon_overlap_violations = zpm_polygon_overlaps(zpm_regions)
    znro_edge_violations = znro_boundary_violations(znro_regions)
    zpm_geometry_violations = zpm_geometry_parent_violations(zpm_regions)
    zpm_unique, zpm_duplicate_cells = unique_cell_ownership(zpm_regions)
    all_cell_ids = {cell["id"] for cell in cells}
    znro_covered_cells = {
        cell_id
        for region in znro_regions
        for cell_id in region["geometry"].get("cell_ids", [])
    }
    zpm_covered_cells = {
        cell_id
        for region in zpm_regions
        for cell_id in region["geometry"].get("cell_ids", [])
    }

    zpm_nesting_violations = []
    for zpm_id, parent_id in zpm_parent.items():
        parent_cells = set(znro_cells.get(parent_id, []))
        child_cells = set(zpm_cells.get(zpm_id, []))
        outside = sorted(child_cells - parent_cells)
        if outside:
            zpm_nesting_violations.append(
                {
                    "zpm_id": zpm_id,
                    "parent_znro_id": parent_id,
                    "cell_ids_outside_parent": outside,
                }
            )

    znro_capacity_violations = [
        {
            "znro_id": region["id"],
            "total_ports": region["total_ports"],
            "capacity_limit": region["capacity_limit"],
        }
        for region in znro_regions
        if region["total_ports"] > region["capacity_limit"]
    ]
    zpm_capacity_violations = [
        {
            "zpm_id": region["id"],
            "total_ports": region["total_ports"],
            "capacity_limit": region["capacity_limit"],
        }
        for region in zpm_regions
        if region["total_ports"] > region["capacity_limit"]
    ]

    disconnected_znro = [
        {
            "znro_id": region["id"],
            "connected_component_count": region["geometry"]["connected_component_count"],
        }
        for region in znro_regions
        if not region["geometry"]["continuous_region"]
    ]
    disconnected_zpm = [
        {
            "zpm_id": region["id"],
            "connected_component_count": region["geometry"]["connected_component_count"],
        }
        for region in zpm_regions
        if not region["geometry"]["continuous_region"]
    ]

    nro_facility_violations = [
        region["id"]
        for region in znro_regions
        if not region["facility"].get("contained_in_region")
    ]
    pm_facility_violations = [
        region["id"]
        for region in zpm_regions
        if not region["facility"].get("contained_in_region")
    ]
    zpm_smoothed_boundary_violations = [
        region["id"]
        for region in zpm_regions
        if not region["geometry"].get("smoothed_boundary", {}).get(
            "covers_assigned_imb_points", False
        )
    ]
    znro_boundary_extension_violations = [
        region["id"]
        for region in znro_regions
        if region["geometry"].get("min_imb_boundary_distance_m", 0.0)
        + 1e-6
        < region["geometry"].get("effective_padding_m", 0.0)
    ]

    return {
        "znro_all_effective_imbs_covered": not assignment_validation["znro_missing_imb_ids"],
        "zpm_all_effective_imbs_covered": not assignment_validation["zpm_missing_imb_ids"],
        "znro_unique_imb_assignment": not assignment_validation["znro_duplicate_imb_ids"],
        "zpm_unique_imb_assignment": not assignment_validation["zpm_duplicate_imb_ids"],
        "znro_regions_non_overlapping": not znro_polygon_overlap_violations,
        "znro_imbs_strictly_inside": not znro_edge_violations,
        "zpm_regions_non_overlapping": zpm_unique and not zpm_polygon_overlap_violations,
        "znro_cells_cover_partition": znro_covered_cells == all_cell_ids,
        "zpm_cells_cover_required_imbs": zpm_covered_cells == all_cell_ids,
        "zpm_nested_in_znro": not zpm_nesting_violations and not zpm_geometry_violations,
        "zpm_smoothed_boundaries_cover_imbs": not zpm_smoothed_boundary_violations,
        "znro_boundary_extension_ok": not znro_boundary_extension_violations,
        "znro_capacity_ok": not znro_capacity_violations,
        "zpm_capacity_ok": not zpm_capacity_violations,
        "nro_facilities_in_regions": not nro_facility_violations,
        "pm_facilities_in_regions": not pm_facility_violations,
        "source_cluster_validations": {
            "znro": cluster_result.get("znro", {}).get("validation", {}),
            "zpm": cluster_result.get("zpm", {}).get("validation", {}),
        },
        "diagnostics": {
            "expected_imb_count": len(assignment_validation["expected_imb_ids"]),
            "cell_count": len(all_cell_ids),
            "znro_missing_imb_ids": assignment_validation["znro_missing_imb_ids"],
            "zpm_missing_imb_ids": assignment_validation["zpm_missing_imb_ids"],
            "znro_duplicate_imb_ids": assignment_validation["znro_duplicate_imb_ids"],
            "zpm_duplicate_imb_ids": assignment_validation["zpm_duplicate_imb_ids"],
            "znro_polygon_overlap_violations": znro_polygon_overlap_violations,
            "zpm_polygon_overlap_violations": zpm_polygon_overlap_violations,
            "znro_boundary_violations": znro_edge_violations,
            "zpm_duplicate_cell_ids": zpm_duplicate_cells,
            "zpm_nesting_violations": zpm_nesting_violations,
            "zpm_geometry_parent_violations": zpm_geometry_violations,
            "zpm_smoothed_boundary_violations": zpm_smoothed_boundary_violations,
            "znro_boundary_extension_violations": znro_boundary_extension_violations,
            "znro_capacity_violations": znro_capacity_violations,
            "zpm_capacity_violations": zpm_capacity_violations,
            "nro_facility_violations": nro_facility_violations,
            "pm_facility_violations": pm_facility_violations,
            "disconnected_znro_regions": disconnected_znro,
            "disconnected_zpm_regions": disconnected_zpm,
        },
    }


def region_boundary_segments(region, cells_by_id, bounds):
    tolerance = max(bounds["scale"] * 1e-8, 1e-12)
    edges = defaultdict(list)
    geometry_polygons = [
        item["polygon"]
        for item in region["geometry"].get("polygons", [])
        if item.get("polygon")
    ]
    if not geometry_polygons:
        geometry_polygons = [
            cells_by_id[cell_id]["polygon"]
            for cell_id in region["geometry"].get("cell_ids", [])
        ]
    for polygon in geometry_polygons:
        for index, start in enumerate(polygon):
            end = polygon[(index + 1) % len(polygon)]
            if math.hypot(float(start[0]) - float(end[0]), float(start[1]) - float(end[1])) <= tolerance:
                continue
            edges[cell_edge_key(start, end, tolerance)].append((start, end))
    return [segments[0] for segments in edges.values() if len(segments) == 1]


def palette(ids):
    colors = {}
    colormap = plt.get_cmap("tab20")
    for index, item_id in enumerate(sorted(ids)):
        colors[item_id] = colormap(index % colormap.N)
    return colors


def draw_network(ax, source_data):
    if not source_data:
        return
    node_map = {node["id"]: node for node in source_data.get("nodes", [])}
    for edge in source_data.get("edges", []):
        if edge.get("path"):
            points = edge["path"]
        else:
            source = node_map.get(edge.get("source"))
            target = node_map.get(edge.get("target"))
            if not source or not target:
                continue
            points = [[source["x"], source["y"]], [target["x"], target["y"]]]
        ax.plot(
            [point[0] for point in points],
            [point[1] for point in points],
            color="#9ca3af",
            linewidth=0.35,
            alpha=0.35,
            zorder=1,
        )


def visualize_partition(region_result, source_data, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cells = region_result["cells"]
    cells_by_id = {cell["id"]: cell for cell in cells}
    bounds = region_result["service_boundary"]["bounds"]
    plot_scale = max(bounds["max_x"] - bounds["min_x"], bounds["max_y"] - bounds["min_y"], 1e-12)
    numeric_scale = max(plot_scale, 1.0)
    draw_bounds = {
        "scale": numeric_scale,
    }
    fig, ax = plt.subplots(figsize=(12, 10), dpi=180)
    draw_network(ax, source_data)

    znro_colors = palette({region["id"] for region in region_result["znro_regions"]})
    for region in region_result["znro_regions"]:
        color = znro_colors[region["id"]]
        patch = PlotPolygon(
            region["geometry"]["polygon"],
            closed=True,
            facecolor=color,
            edgecolor="#111827",
            linewidth=1.9,
            alpha=0.12,
            zorder=2,
        )
        ax.add_patch(patch)

    zpm_colors = palette({region["id"] for region in region_result["zpm_regions"]})
    for region in region_result["zpm_regions"]:
        color = zpm_colors.get(region["id"], "#dddddd")
        for polygon_record in region["geometry"].get("polygons", []):
            patch = PlotPolygon(
                polygon_record["polygon"],
                closed=True,
                facecolor=color,
                edgecolor="none",
                alpha=0.42,
                zorder=3,
            )
            ax.add_patch(patch)

    for region in region_result["zpm_regions"]:
        smooth_polygon = (
            region["geometry"].get("smoothed_polygon")
            or region["geometry"].get("polygon")
        )
        if smooth_polygon and len(smooth_polygon) >= 3:
            closed = smooth_polygon + [smooth_polygon[0]]
            ax.plot(
                [point[0] for point in closed],
                [point[1] for point in closed],
                color="#111827",
                linewidth=1.35,
                alpha=0.95,
                zorder=5,
            )
        facility = region["facility"]
        ax.scatter(
            [facility["x"]],
            [facility["y"]],
            marker="s",
            s=16,
            color="#111827",
            linewidth=0,
            alpha=0.88,
            zorder=7,
        )
        ax.text(
            facility["x"],
            facility["y"],
            region["id"],
            fontsize=5.6,
            ha="left",
            va="top",
            color="#111827",
            alpha=0.9,
            zorder=8,
        )

    for region in region_result["znro_regions"]:
        color = znro_colors[region["id"]]
        polygon = region["geometry"]["polygon"]
        closed = polygon + [polygon[0]]
        ax.plot(
            [point[0] for point in closed],
            [point[1] for point in closed],
            color="#111827",
            linewidth=2.45,
            alpha=0.95,
            zorder=9,
        )
        ax.plot(
            [point[0] for point in closed],
            [point[1] for point in closed],
            color=color,
            linewidth=1.35,
            alpha=1.0,
            zorder=10,
        )
        facility = region["facility"]
        ax.scatter(
            [facility["x"]],
            [facility["y"]],
            marker="*",
            s=120,
            color=color,
            edgecolor="#111827",
            linewidth=0.65,
            zorder=11,
        )
        ax.text(
            facility["x"],
            facility["y"],
            region["id"],
            fontsize=7.5,
            fontweight="bold",
            ha="left",
            va="bottom",
            color="#111827",
            zorder=12,
        )

    imb_x = [cell["site"]["x"] for cell in cells]
    imb_y = [cell["site"]["y"] for cell in cells]
    ax.scatter(imb_x, imb_y, s=5, color="#111827", linewidth=0, alpha=0.6, zorder=6)

    margin = plot_scale * 0.02
    ax.set_xlim(bounds["min_x"] - margin, bounds["max_x"] + margin)
    ax.set_ylim(bounds["min_y"] - margin, bounds["max_y"] + margin)
    if (source_data or {}).get("meta", {}).get("coordinate_system") != "source_shapefile_coordinates":
        ax.invert_yaxis()
    ax.set_aspect("equal", adjustable="box")
    ax.set_title("ZNRO regions with nested ZPM regions", fontsize=11)
    ax.axis("off")
    fig.tight_layout(pad=0.4)
    fig.savefig(output_path, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)


def default_output_path(input_json, suffix):
    stem = input_json.stem
    if stem.endswith("_result"):
        stem = stem[: -len("_result")]
    return TASK2_ZONE_PARTITION_OUTPUT_DIR / f"{stem}_{suffix}"


def parse_args():
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description=(
            "Build non-overlapping ZNRO/ZPM service regions from a clustering result JSON "
            "or directly from a source FTTH dataset JSON."
        )
    )
    parser.add_argument(
        "--input-json",
        type=Path,
        default=None,
        help="Clustering result JSON, or a source FTTH dataset JSON to cluster first.",
    )
    parser.add_argument(
        "--source-data-json",
        type=Path,
        default=None,
        help="Optional source FTTH topology JSON. Defaults to source_file in the clustering result.",
    )
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--output-fig", type=Path, default=None)
    parser.add_argument(
        "--cluster-output-json",
        type=Path,
        default=None,
        help="Optional path for saving the generated clustering result when input is source data.",
    )
    parser.add_argument("--nro-max-cap", type=int, default=2048)
    parser.add_argument("--zpm-max-cap", type=int, default=576)
    parser.add_argument("--spatial-weight", type=float, default=1.0)
    parser.add_argument("--pipe-weight", type=float, default=100.0)
    parser.add_argument("--allow-extra-clusters", type=int, default=1)
    parser.add_argument("--unconnect-penalty", type=float, default=100.0)
    parser.add_argument("--boundary-padding-ratio", type=float, default=0.05)
    parser.add_argument(
        "--znro-boundary-extension-m",
        type=float,
        default=20.0,
        help="Metric extension outside the assigned IMB convex hull.",
    )
    parser.add_argument(
        "--zpm-boundary-extension-m",
        type=float,
        default=None,
        help="Metric extension for ZPM polygons; defaults to the ZNRO extension.",
    )
    parser.add_argument("--round-digits", type=int, default=9)
    parser.add_argument("--skip-fig", action="store_true")
    args = parser.parse_args()
    if args.nro_max_cap <= 0 or args.zpm_max_cap <= 0:
        parser.error("Capacity limits must be positive.")
    if args.spatial_weight < 0 or args.pipe_weight < 0:
        parser.error("Distance weights must be non-negative.")
    if args.spatial_weight + args.pipe_weight <= 0:
        parser.error("At least one distance weight must be positive.")
    if args.boundary_padding_ratio < 0:
        parser.error("--boundary-padding-ratio must be non-negative.")
    if args.znro_boundary_extension_m < 0:
        parser.error("--znro-boundary-extension-m must be non-negative.")
    if args.zpm_boundary_extension_m is not None and args.zpm_boundary_extension_m < 0:
        parser.error("--zpm-boundary-extension-m must be non-negative.")
    if args.round_digits < 0:
        parser.error("--round-digits must be non-negative.")
    if args.input_json is None:
        parser.error("--input-json is required; the task-2 data path is currently unset")
    if args.output_json is None:
        args.output_json = default_output_path(args.input_json, "region_partition_result.json")
    if args.output_fig is None:
        args.output_fig = default_output_path(args.input_json, "region_partition_result.png")
    return args


def main():
    args = parse_args()
    prepared = prepare_inputs(args)
    result = prepared["cluster_result"]
    source_data = prepared["source_data"]
    source_data_path = prepared["source_data_path"]
    region_result = build_partition(
        prepared["cluster_result_reference"],
        result,
        source_data,
        source_data_path,
        args,
    )
    dump_json(region_result, args.output_json)
    if not args.skip_fig:
        visualize_partition(region_result, source_data, args.output_fig)

    validation = region_result["validation"]
    print(f"Input kind: {prepared['input_kind']}")
    print(f"Input JSON: {args.input_json.resolve()}")
    if prepared["generated_cluster_result_path"]:
        print(f"Generated clustering result: {prepared['generated_cluster_result_path'].resolve()}")
    elif prepared["input_kind"] == "source_dataset":
        print("Generated clustering result: in memory")
    else:
        print(f"Input clustering result: {args.input_json.resolve()}")
    print(f"Source data: {source_data_path.resolve() if source_data_path else 'not found'}")
    print(f"Region JSON: {args.output_json.resolve()}")
    if not args.skip_fig:
        print(f"Visualization: {args.output_fig.resolve()}")
    print(
        f"Regions: ZNRO={len(region_result['znro_regions'])}, "
        f"ZPM={len(region_result['zpm_regions'])}, cells={len(region_result['cells'])}"
    )
    print(
        "Validation: "
        f"ZNRO coverage={validation['znro_all_effective_imbs_covered']}, "
        f"ZPM coverage={validation['zpm_all_effective_imbs_covered']}, "
        f"ZNRO non-overlap={validation['znro_regions_non_overlapping']}, "
        f"ZPM non-overlap={validation['zpm_regions_non_overlapping']}, "
        f"ZPM nesting={validation['zpm_nested_in_znro']}, "
        f"capacity={validation['znro_capacity_ok'] and validation['zpm_capacity_ok']}"
    )


if __name__ == "__main__":
    main()
