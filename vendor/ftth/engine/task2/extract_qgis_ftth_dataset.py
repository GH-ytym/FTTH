import argparse
import json
import math
import struct
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from project_paths import TASK1_TO_TASK2_TOPOLOGY


# The corrected source project has not been supplied yet.
DEFAULT_PROJECT_DIR = None

SHAPE_TYPE_POINT = 1
SHAPE_TYPE_POLYLINE = 3


def make_id(prefix, index):
    return f"{prefix}_{index:04d}"


def round_coord(value, digits=9):
    return round(float(value), digits)


def read_encoding(path):
    cpg_path = path.with_suffix(".cpg")
    if not cpg_path.exists():
        return "utf-8"
    value = cpg_path.read_text(encoding="ascii", errors="ignore").strip()
    return value or "utf-8"


def decode_text(raw, encodings):
    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode(encodings[0], errors="replace")


def parse_dbf_value(raw, field_type, decimals, encodings):
    text = decode_text(raw, encodings).strip()
    if not text:
        return None
    if field_type in {"N", "F"}:
        try:
            return float(text) if decimals else int(text)
        except ValueError:
            return text
    if field_type == "L":
        upper = text.upper()
        if upper in {"Y", "T"}:
            return True
        if upper in {"N", "F"}:
            return False
    return text


def read_dbf(path):
    data = path.read_bytes()
    if len(data) < 32:
        raise ValueError(f"Invalid DBF file: {path}")

    record_count = struct.unpack("<I", data[4:8])[0]
    header_length = struct.unpack("<H", data[8:10])[0]
    record_length = struct.unpack("<H", data[10:12])[0]
    encodings = [read_encoding(path), "utf-8", "cp1252", "latin1"]

    fields = []
    offset = 32
    while offset + 32 <= len(data) and data[offset] != 0x0D:
        raw_name = data[offset : offset + 11].split(b"\x00", 1)[0]
        name = decode_text(raw_name, encodings).strip()
        field_type = chr(data[offset + 11])
        field_length = data[offset + 16]
        decimals = data[offset + 17]
        fields.append((name, field_type, field_length, decimals))
        offset += 32

    rows = []
    position = header_length
    for _ in range(record_count):
        record = data[position : position + record_length]
        position += record_length
        if not record or record[:1] == b"*":
            continue

        cursor = 1
        row = {}
        for name, field_type, field_length, decimals in fields:
            raw = record[cursor : cursor + field_length]
            cursor += field_length
            row[name] = parse_dbf_value(raw, field_type, decimals, encodings)
        rows.append(row)

    return rows


def read_point_content(content):
    if len(content) < 20:
        raise ValueError("Invalid point record.")
    return (struct.unpack("<d", content[4:12])[0], struct.unpack("<d", content[12:20])[0])


def read_polyline_content(content):
    if len(content) < 44:
        raise ValueError("Invalid polyline record.")

    part_count = struct.unpack("<i", content[36:40])[0]
    point_count = struct.unpack("<i", content[40:44])[0]
    parts_offset = 44
    points_offset = parts_offset + part_count * 4
    parts = [
        struct.unpack("<i", content[parts_offset + index * 4 : parts_offset + index * 4 + 4])[0]
        for index in range(part_count)
    ]
    points = []
    for index in range(point_count):
        point_offset = points_offset + index * 16
        x, y = struct.unpack("<2d", content[point_offset : point_offset + 16])
        points.append((x, y))

    if part_count <= 1:
        return points

    # Most infrastructure features are single-part lines. When a multipart line
    # appears, keep the record as one path and preserve each part's vertex order.
    path = []
    for part_index, start in enumerate(parts):
        end = parts[part_index + 1] if part_index + 1 < len(parts) else len(points)
        path.extend(points[start:end])
    return path


def read_shp(path):
    data = path.read_bytes()
    if len(data) < 100:
        raise ValueError(f"Invalid SHP file: {path}")

    geometries = []
    offset = 100
    while offset + 8 <= len(data):
        _, content_length_words = struct.unpack(">2i", data[offset : offset + 8])
        offset += 8
        content_length = content_length_words * 2
        content = data[offset : offset + content_length]
        offset += content_length
        if len(content) < 4:
            continue

        shape_type = struct.unpack("<i", content[:4])[0]
        if shape_type == 0:
            geometries.append(None)
        elif shape_type == SHAPE_TYPE_POINT:
            geometries.append(read_point_content(content))
        elif shape_type == SHAPE_TYPE_POLYLINE:
            geometries.append(read_polyline_content(content))
        else:
            raise ValueError(f"Unsupported shape type {shape_type} in {path}")

    return geometries


def read_layer(shape_dir, layer_name):
    stem = shape_dir / layer_name
    rows = read_dbf(stem.with_suffix(".dbf"))
    geometries = read_shp(stem.with_suffix(".shp"))
    if len(rows) != len(geometries):
        raise ValueError(
            f"{layer_name}: DBF row count {len(rows)} does not match SHP geometry count {len(geometries)}."
        )
    return [{"properties": row, "geometry": geometry} for row, geometry in zip(rows, geometries)]


def numeric_value(row, key):
    value = row.get(key)
    if value is None or value == "":
        return None
    return float(value)


def point_xy(feature):
    row = feature["properties"]
    x = numeric_value(row, "X")
    y = numeric_value(row, "Y")
    if x is not None and y is not None:
        return (x, y)

    geometry = feature["geometry"]
    if geometry is None:
        raise ValueError(f"Feature {row.get('CODE')} has no point geometry.")
    return geometry


def add_node(nodes, code_to_id, node_type, x, y, **extra):
    node = {
        "id": make_id("n", len(nodes)),
        "type": node_type,
        "x": round_coord(x),
        "y": round_coord(y),
    }
    node.update({key: value for key, value in extra.items() if value is not None})
    nodes.append(node)

    code = node.get("code")
    if code and code not in code_to_id:
        code_to_id[code] = node["id"]
    return node["id"]


def path_distance(points):
    total = 0.0
    for index in range(1, len(points)):
        x0, y0 = points[index - 1]
        x1, y1 = points[index]
        total += math.hypot(x1 - x0, y1 - y0)
    return total


def orient_path(path, source_node, target_node):
    if not path:
        return path

    start = path[0]
    end = path[-1]
    forward_distance = math.hypot(start[0] - source_node["x"], start[1] - source_node["y"])
    forward_distance += math.hypot(end[0] - target_node["x"], end[1] - target_node["y"])
    reverse_distance = math.hypot(end[0] - source_node["x"], end[1] - source_node["y"])
    reverse_distance += math.hypot(start[0] - target_node["x"], start[1] - target_node["y"])
    if reverse_distance + 1e-12 < forward_distance:
        return list(reversed(path))
    return path


def layer_bounds(features):
    xs = []
    ys = []
    for feature in features:
        geometry = feature["geometry"]
        if geometry is None:
            continue
        if isinstance(geometry, tuple):
            xs.append(geometry[0])
            ys.append(geometry[1])
        else:
            xs.extend(point[0] for point in geometry)
            ys.extend(point[1] for point in geometry)
    if not xs or not ys:
        return None
    return {
        "min_x": round_coord(min(xs)),
        "min_y": round_coord(min(ys)),
        "max_x": round_coord(max(xs)),
        "max_y": round_coord(max(ys)),
    }


def merge_bounds(bounds_list):
    valid = [bounds for bounds in bounds_list if bounds]
    if not valid:
        return None
    return {
        "min_x": min(bounds["min_x"] for bounds in valid),
        "min_y": min(bounds["min_y"] for bounds in valid),
        "max_x": max(bounds["max_x"] for bounds in valid),
        "max_y": max(bounds["max_y"] for bounds in valid),
    }


def build_dataset(project_dir, include_site_endpoints=True):
    shape_dir = project_dir / "Shape"
    if not shape_dir.exists():
        raise FileNotFoundError(f"Shape directory not found: {shape_dir}")

    ptech_features = read_layer(shape_dir, "PTECH")
    imb_features = read_layer(shape_dir, "IMB")
    infrastructure_features = read_layer(shape_dir, "INFRASTRUCTURE")
    site_features = read_layer(shape_dir, "SITE") if include_site_endpoints else []

    nodes = []
    edges = []
    code_to_id = {}

    for feature in ptech_features:
        row = feature["properties"]
        x, y = point_xy(feature)
        add_node(
            nodes,
            code_to_id,
            "PETCH",
            x,
            y,
            code=row.get("CODE"),
            role="technical_point",
            source_layer="PTECH",
            original_type=row.get("TYPE"),
            nature=row.get("NATURE"),
        )

    for feature in imb_features:
        row = feature["properties"]
        x, y = point_xy(feature)
        ports = row.get("NB_LOC_TOT")
        add_node(
            nodes,
            code_to_id,
            "IMB",
            x,
            y,
            code=row.get("CODE"),
            ports=ports,
            nb_loc_tot=ports,
            source_layer="IMB",
        )

    endpoint_codes = {
        feature["properties"].get("ORIGINE")
        for feature in infrastructure_features
        if feature["properties"].get("ORIGINE")
    } | {
        feature["properties"].get("EXTREMITE")
        for feature in infrastructure_features
        if feature["properties"].get("EXTREMITE")
    }
    site_endpoint_count = 0
    if include_site_endpoints:
        for feature in site_features:
            row = feature["properties"]
            code = row.get("CODE")
            if not code or code not in endpoint_codes or code in code_to_id:
                continue
            x, y = point_xy(feature)
            add_node(
                nodes,
                code_to_id,
                "PETCH",
                x,
                y,
                code=code,
                role="site_endpoint",
                source_layer="SITE",
                original_type=row.get("TYPE"),
            )
            site_endpoint_count += 1

    node_map = {node["id"]: node for node in nodes}
    inferred_endpoint_count = 0
    skipped_edges = []

    for feature in infrastructure_features:
        row = feature["properties"]
        path = feature["geometry"] or []
        source_code = row.get("ORIGINE")
        target_code = row.get("EXTREMITE")

        if not path:
            skipped_edges.append({"code": row.get("CODE"), "reason": "empty geometry"})
            continue

        if source_code not in code_to_id:
            add_node(
                nodes,
                code_to_id,
                "PETCH",
                path[0][0],
                path[0][1],
                code=source_code,
                role="inferred_endpoint",
                source_layer="INFRASTRUCTURE",
            )
            node_map[nodes[-1]["id"]] = nodes[-1]
            inferred_endpoint_count += 1
        if target_code not in code_to_id:
            add_node(
                nodes,
                code_to_id,
                "PETCH",
                path[-1][0],
                path[-1][1],
                code=target_code,
                role="inferred_endpoint",
                source_layer="INFRASTRUCTURE",
            )
            node_map[nodes[-1]["id"]] = nodes[-1]
            inferred_endpoint_count += 1

        source_id = code_to_id[source_code]
        target_id = code_to_id[target_code]
        source_node = node_map[source_id]
        target_node = node_map[target_id]
        path = orient_path(path, source_node, target_node)
        rounded_path = [[round_coord(x), round_coord(y)] for x, y in path]

        length = row.get("LONGUEUR")
        edge = {
            "source": source_id,
            "target": target_id,
            "path": rounded_path,
            "code": row.get("CODE"),
            "source_code": source_code,
            "target_code": target_code,
            "source_layer": "INFRASTRUCTURE",
        }
        if length is not None:
            edge["length"] = round(float(length), 3)
        else:
            edge["length"] = round(path_distance(path), 9)
        edges.append(edge)

    bounds = merge_bounds(
        [
            layer_bounds(ptech_features),
            layer_bounds(imb_features),
            layer_bounds(infrastructure_features),
        ]
    )
    if bounds:
        width = round_coord(bounds["max_x"] - bounds["min_x"])
        height = round_coord(bounds["max_y"] - bounds["min_y"])
    else:
        width = height = 0

    meta = {
        "layout": "qgis_plan_de_recolement",
        "source_project_dir": str(project_dir),
        "coordinate_system": "source_shapefile_coordinates",
        "ptech_count": len(ptech_features),
        "imb_count": len(imb_features),
        "infrastructure_count": len(infrastructure_features),
        "site_endpoint_count": site_endpoint_count,
        "inferred_endpoint_count": inferred_endpoint_count,
        "skipped_edge_count": len(skipped_edges),
        "width": width,
        "height": height,
        "bounds": bounds,
    }
    if skipped_edges:
        meta["skipped_edges"] = skipped_edges

    return {"meta": meta, "nodes": nodes, "edges": edges}


def validate_dataset(data):
    node_ids = {node["id"] for node in data["nodes"]}
    unknown_edges = [
        edge
        for edge in data["edges"]
        if edge["source"] not in node_ids or edge["target"] not in node_ids
    ]
    if unknown_edges:
        sample = unknown_edges[0]
        raise ValueError(f"Edge has unknown endpoint: {sample}")

    missing_ports = [
        node["id"]
        for node in data["nodes"]
        if node["type"] == "IMB" and node.get("ports") is None
    ]
    if missing_ports:
        raise ValueError(f"IMB nodes missing NB_LOC_TOT/ports: {missing_ports[:5]}")


def main():
    parser = argparse.ArgumentParser(
        description="Extract IMB, PTECH, and INFRASTRUCTURE layers from the QGIS plan into ftth_dataset JSON."
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=DEFAULT_PROJECT_DIR,
        help="QGIS source project directory (required; no default is configured).",
    )
    parser.add_argument("--output-json", type=Path, default=TASK1_TO_TASK2_TOPOLOGY)
    parser.add_argument(
        "--no-site-endpoints",
        action="store_true",
        help="Do not add SITE points used by infrastructure endpoints.",
    )
    args = parser.parse_args()
    if args.project_dir is None:
        parser.error("--project-dir is required; the task-2 source data path is currently unset")

    data = build_dataset(args.project_dir, include_site_endpoints=not args.no_site_endpoints)
    validate_dataset(data)

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    with args.output_json.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"JSON saved to: {args.output_json.resolve()}")
    print(f"Nodes: {len(data['nodes'])}, Edges: {len(data['edges'])}")
    print(f"PETCH nodes: {sum(1 for node in data['nodes'] if node['type'] == 'PETCH')}")
    print(f"IMB nodes: {sum(1 for node in data['nodes'] if node['type'] == 'IMB')}")
    print(f"Site endpoint nodes: {data['meta']['site_endpoint_count']}")
    print(f"Inferred endpoint nodes: {data['meta']['inferred_endpoint_count']}")


if __name__ == "__main__":
    main()
