"""任务一正式业务图层到任务二 ``nodes/edges`` JSON 的安全适配器。

本模块只读取正式 ``PTECH/IMB/INFRASTRUCTURE/SITE``，不会读取或改名
``GEO_*`` 辅助图层。普通 Python 可读取任务一 CSV；QGIS 完整运行后也可从
任务一生成的 GeoPackage 构建同一合同。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sqlite3
import struct
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


TASK2_SCHEMA_VERSION = "task2-topology-1.0"


class TopologyContractError(RuntimeError):
    """正式业务图层不能安全转换为任务二拓扑。"""


DEFAULT_CONTRACT: Dict[str, Any] = {
    "enabled": True,
    "output_json": "outputs/qgis_ftth_dataset.json",
    "crs": "EPSG:4326",
    "allow_inferred_endpoints": False,
    "layers": {
        "PTECH": "PTECH",
        "IMB": "IMB",
        "INFRASTRUCTURE": "INFRASTRUCTURE",
        "SITE": "SITE",
    },
    "fields": {
        "PTECH": {"code": "CODE", "type": "TYPE", "nature": "NATURE"},
        "IMB": {
            "code": "CODE",
            "residential": "NB_LOC_RES",
            "professional": "NB_LOC_PRO",
            "ports": "NB_LOC_TOT",
        },
        "INFRASTRUCTURE": {
            "code": "CODE",
            "source": "ORIGINE",
            "target": "EXTREMITE",
            "length": "LONGUEUR",
        },
        "SITE": {"code": "CODE", "type": "TYPE"},
    },
}

EXPECTED_GEOMETRY = {
    "PTECH": "Point",
    "IMB": "Point",
    "INFRASTRUCTURE": "LineString",
    "SITE": "Point",
}


def _merge_mapping(base: Mapping[str, Any], override: Mapping[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _merge_mapping(result[key], value)
        else:
            result[key] = value
    return result


def topology_contract(config: Mapping[str, Any]) -> Dict[str, Any]:
    raw = config.get("task2_topology") or {}
    if not isinstance(raw, Mapping):
        raise TopologyContractError("task2_topology 必须是对象")
    return _merge_mapping(DEFAULT_CONTRACT, raw)


def _layer_configs(config: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    result: Dict[str, Mapping[str, Any]] = {}
    for layer in config.get("vector_layers") or []:
        if not isinstance(layer, Mapping):
            continue
        name = str(layer.get("biz_layer_name") or layer.get("name") or "").strip()
        if name:
            result[name] = layer
    return result


def validate_topology_contract_config(config: Mapping[str, Any]) -> List[str]:
    """检查任务一配置能否保留任务二所需的正式业务语义。"""
    try:
        contract = topology_contract(config)
    except TopologyContractError as exc:
        return [str(exc)]
    if not contract.get("enabled", True):
        return []

    errors: List[str] = []
    if str(contract.get("crs") or "").upper() != "EPSG:4326":
        errors.append("task2_topology.crs 必须为 EPSG:4326（x=longitude, y=latitude）")
    if not isinstance(contract.get("output_json"), str) or not str(
        contract.get("output_json") or ""
    ).strip():
        errors.append("task2_topology.output_json 必须是非空路径")
    if not isinstance(contract.get("allow_inferred_endpoints"), bool):
        errors.append("task2_topology.allow_inferred_endpoints 必须是布尔值")

    layers = contract.get("layers")
    fields = contract.get("fields")
    if not isinstance(layers, Mapping):
        return [*errors, "task2_topology.layers 必须是对象"]
    if not isinstance(fields, Mapping):
        return [*errors, "task2_topology.fields 必须是对象"]

    configured = _layer_configs(config)
    for role in ("PTECH", "IMB", "INFRASTRUCTURE"):
        layer_name = layers.get(role)
        if not isinstance(layer_name, str) or not layer_name.strip():
            errors.append(f"task2_topology.layers.{role} 必须是非空图层名")
            continue
        layer_cfg = configured.get(layer_name)
        if layer_cfg is None:
            errors.append(f"任务二合同缺少正式图层 {layer_name}")
            continue
        if layer_cfg.get("geometry_type") != EXPECTED_GEOMETRY[role]:
            errors.append(
                f"正式 {role} 必须是 {EXPECTED_GEOMETRY[role]}，"
                f"当前为 {layer_cfg.get('geometry_type')!r}"
            )
        if str(layer_cfg.get("crs") or "").upper() != "EPSG:4326":
            errors.append(f"正式 {role} 输入必须为 EPSG:4326")
        declared = {
            str(item.get("name"))
            for item in layer_cfg.get("fields") or []
            if isinstance(item, Mapping)
        }
        role_fields = fields.get(role)
        if not isinstance(role_fields, Mapping):
            errors.append(f"task2_topology.fields.{role} 必须是对象")
            continue
        for semantic, field_name in role_fields.items():
            if not isinstance(field_name, str) or not field_name.strip():
                errors.append(f"task2_topology.fields.{role}.{semantic} 必须是非空字段名")
            elif field_name not in declared:
                errors.append(f"正式 {role} 缺少合同字段 {field_name}")

    site_name = layers.get("SITE")
    if site_name:
        site_cfg = configured.get(str(site_name))
        if site_cfg is None:
            errors.append(f"任务二合同声明了 SITE={site_name}，但任务一未配置该图层")
        elif site_cfg.get("geometry_type") != "Point":
            errors.append("正式 SITE 必须是 Point")
    return errors


def _required_text(row: Mapping[str, Any], field: str, context: str) -> str:
    value = str(row.get(field) or "").strip()
    if not value:
        raise TopologyContractError(f"{context} 缺少必填字段 {field}")
    return value


def _required_number(row: Mapping[str, Any], field: str, context: str) -> float:
    raw = row.get(field)
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise TopologyContractError(f"{context}.{field} 不是数值：{raw!r}") from exc
    if not math.isfinite(value):
        raise TopologyContractError(f"{context}.{field} 不是有限数值")
    return value


def _required_nonnegative_int(row: Mapping[str, Any], field: str, context: str) -> int:
    value = _required_number(row, field, context)
    if value < 0 or not value.is_integer():
        raise TopologyContractError(f"{context}.{field} 必须是非负整数")
    return int(value)


def _validate_lon_lat(point: Sequence[float], context: str) -> Tuple[float, float]:
    if len(point) < 2:
        raise TopologyContractError(f"{context} 坐标不足两个分量")
    lon, lat = float(point[0]), float(point[1])
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        raise TopologyContractError(
            f"{context} 不是 EPSG:4326 [lon,lat]：[{lon}, {lat}]"
        )
    return lon, lat


def _parse_linestring_wkt(raw: Any, context: str) -> List[Tuple[float, float]]:
    text = str(raw or "").strip()
    match = re.fullmatch(r"LINESTRING\s*\((.*)\)", text, flags=re.IGNORECASE)
    if not match:
        raise TopologyContractError(f"{context} 必须是二维 LINESTRING WKT")
    points: List[Tuple[float, float]] = []
    for index, token in enumerate(match.group(1).split(","), start=1):
        values = token.strip().split()
        if len(values) != 2:
            raise TopologyContractError(f"{context} 第 {index} 个坐标必须为 x y")
        try:
            point = (float(values[0]), float(values[1]))
        except ValueError as exc:
            raise TopologyContractError(f"{context} 第 {index} 个坐标不是数值") from exc
        points.append(_validate_lon_lat(point, f"{context} 第 {index} 点"))
    if len(points) < 2:
        raise TopologyContractError(f"{context} 至少需要两个坐标点")
    return points


FeatureRecord = Tuple[Dict[str, Any], str, Any]


def _read_csv_records(
    layer_cfg: Mapping[str, Any], base_dir: Path, role: str
) -> List[FeatureRecord]:
    source_type = layer_cfg.get("source_type")
    if source_type == "empty":
        return []
    if source_type not in {"csv_xy", "csv_wkt"}:
        raise TopologyContractError(
            f"validate-only 无法读取 {role} 的 {source_type!r}；请在 QGIS 完整运行后从 GeoPackage 转换"
        )
    source = Path(str(layer_cfg["source"]))
    path = source if source.is_absolute() else base_dir / source
    with path.resolve().open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None:
            raise TopologyContractError(f"{role} CSV 没有表头：{path}")
        records: List[FeatureRecord] = []
        allowed = {
            str(value).strip() for value in layer_cfg.get("filter_obj_type") or []
        }
        for line_number, raw_row in enumerate(reader, start=2):
            row = dict(raw_row)
            if allowed and str(row.get("object_type") or "").strip() not in allowed:
                continue
            context = f"{role} 第{line_number}行"
            if source_type == "csv_xy":
                x = _required_number(row, str(layer_cfg["x_field"]), context)
                y = _required_number(row, str(layer_cfg["y_field"]), context)
                records.append((row, "Point", _validate_lon_lat((x, y), context)))
            else:
                points = _parse_linestring_wkt(row.get(str(layer_cfg["wkt_field"])), context)
                records.append((row, "LineString", points))
    return records


def build_task2_topology_from_sources(
    config: Mapping[str, Any], base_dir: Path
) -> Dict[str, Any]:
    contract = topology_contract(config)
    if not contract.get("enabled", True):
        raise TopologyContractError("task2_topology 已关闭")
    errors = validate_topology_contract_config(config)
    if errors:
        raise TopologyContractError("；".join(errors))
    layer_cfgs = _layer_configs(config)
    records: Dict[str, List[FeatureRecord]] = {}
    for role, layer_name in contract["layers"].items():
        if not layer_name:
            records[role] = []
            continue
        records[role] = _read_csv_records(layer_cfgs[str(layer_name)], base_dir, role)
    return _assemble_topology(records, contract, "task1_config_sources")


def _gpkg_wkb(blob: bytes) -> bytes:
    raw = bytes(blob)
    if len(raw) < 8 or raw[:2] != b"GP":
        raise TopologyContractError("GeoPackage 几何缺少 GP 头")
    flags = raw[3]
    if flags & 0x10:
        raise TopologyContractError("GeoPackage 包含空几何")
    envelope_code = (flags >> 1) & 0x07
    envelope_sizes = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
    if envelope_code not in envelope_sizes:
        raise TopologyContractError(f"不支持的 GeoPackage envelope 标志：{envelope_code}")
    offset = 8 + envelope_sizes[envelope_code]
    if len(raw) <= offset:
        raise TopologyContractError("GeoPackage 几何正文为空")
    return raw[offset:]


def _base_wkb_type(raw_type: int) -> Tuple[int, int]:
    value = raw_type & 0x0FFFFFFF
    dimensions = 2
    if value >= 3000:
        value -= 3000
        dimensions = 4
    elif value >= 2000:
        value -= 2000
        dimensions = 3
    elif value >= 1000:
        value -= 1000
        dimensions = 3
    if dimensions != 2:
        raise TopologyContractError("任务二适配器只接受二维正式业务几何")
    return value, dimensions


def _read_wkb(raw: bytes, offset: int = 0) -> Tuple[str, Any, int]:
    if offset + 5 > len(raw):
        raise TopologyContractError("WKB 几何被截断")
    byte_order = raw[offset]
    endian = "<" if byte_order == 1 else ">" if byte_order == 0 else None
    if endian is None:
        raise TopologyContractError(f"非法 WKB 字节序：{byte_order}")
    raw_type = struct.unpack_from(endian + "I", raw, offset + 1)[0]
    geometry_type, _ = _base_wkb_type(raw_type)
    cursor = offset + 5
    if raw_type & 0x20000000:
        cursor += 4

    if geometry_type == 1:
        if cursor + 16 > len(raw):
            raise TopologyContractError("WKB Point 被截断")
        point = struct.unpack_from(endian + "dd", raw, cursor)
        return "Point", point, cursor + 16
    if geometry_type == 2:
        if cursor + 4 > len(raw):
            raise TopologyContractError("WKB LineString 被截断")
        count = struct.unpack_from(endian + "I", raw, cursor)[0]
        cursor += 4
        if count < 2 or cursor + count * 16 > len(raw):
            raise TopologyContractError("WKB LineString 坐标数量非法")
        points = [
            struct.unpack_from(endian + "dd", raw, cursor + index * 16)
            for index in range(count)
        ]
        return "LineString", points, cursor + count * 16
    raise TopologyContractError(f"任务二正式拓扑不支持 WKB 类型 {geometry_type}")


def _decode_gpkg_geometry(blob: Any) -> Tuple[str, Any]:
    geometry_type, coordinates, _ = _read_wkb(_gpkg_wkb(bytes(blob)))
    return geometry_type, coordinates


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _open_gpkg_readonly(path: Path) -> sqlite3.Connection:
    uri = "file:" + path.resolve().as_posix() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _read_gpkg_layer(
    connection: sqlite3.Connection,
    table_name: str,
    expected_geometry: str,
    required_fields: Iterable[str],
) -> List[FeatureRecord]:
    content = connection.execute(
        "SELECT srs_id FROM gpkg_contents WHERE table_name=? AND data_type='features'",
        (table_name,),
    ).fetchone()
    geometry = connection.execute(
        "SELECT column_name, geometry_type_name, srs_id FROM gpkg_geometry_columns WHERE table_name=?",
        (table_name,),
    ).fetchone()
    if content is None or geometry is None:
        raise TopologyContractError(f"GeoPackage 缺少正式图层 {table_name}")
    if int(content["srs_id"]) != 4326 or int(geometry["srs_id"]) != 4326:
        raise TopologyContractError(f"正式图层 {table_name} 必须为 EPSG:4326")
    actual_geometry = str(geometry["geometry_type_name"] or "").upper()
    if actual_geometry != expected_geometry.upper():
        raise TopologyContractError(
            f"正式图层 {table_name} 必须是 {expected_geometry}，实际为 {actual_geometry}"
        )

    columns = connection.execute(
        f"PRAGMA table_info({_quote_identifier(table_name)})"
    ).fetchall()
    column_names = {str(row[1]) for row in columns}
    missing = sorted(set(required_fields) - column_names)
    if missing:
        raise TopologyContractError(f"正式图层 {table_name} 缺少字段：{', '.join(missing)}")
    primary = next((str(row[1]) for row in columns if int(row[5]) > 0), "rowid")
    rows = connection.execute(
        f"SELECT * FROM {_quote_identifier(table_name)} ORDER BY {_quote_identifier(primary)}"
    ).fetchall()
    geom_column = str(geometry["column_name"])
    records: List[FeatureRecord] = []
    for index, row in enumerate(rows, start=1):
        if row[geom_column] is None:
            raise TopologyContractError(f"{table_name} 第 {index} 个要素几何为空")
        geometry_type, coordinates = _decode_gpkg_geometry(row[geom_column])
        if geometry_type != expected_geometry:
            raise TopologyContractError(
                f"{table_name} 第 {index} 个要素几何为 {geometry_type}，应为 {expected_geometry}"
            )
        properties = {key: row[key] for key in row.keys() if key != geom_column}
        records.append((properties, geometry_type, coordinates))
    return records


def build_task2_topology_from_gpkg(
    gpkg_path: str | Path, config: Mapping[str, Any]
) -> Dict[str, Any]:
    path = Path(gpkg_path).expanduser().resolve()
    if not path.exists():
        raise TopologyContractError(f"找不到任务一 GeoPackage：{path}")
    contract = topology_contract(config)
    if not contract.get("enabled", True):
        raise TopologyContractError("task2_topology 已关闭")
    errors = validate_topology_contract_config(config)
    if errors:
        raise TopologyContractError("；".join(errors))

    records: Dict[str, List[FeatureRecord]] = {}
    connection = _open_gpkg_readonly(path)
    try:
        for role, table_name in contract["layers"].items():
            if not table_name:
                records[role] = []
                continue
            role_fields = contract["fields"].get(role) or {}
            records[role] = _read_gpkg_layer(
                connection,
                str(table_name),
                EXPECTED_GEOMETRY[role],
                role_fields.values(),
            )
    finally:
        connection.close()
    return _assemble_topology(records, contract, path.name)


def _round_coord(value: float) -> float:
    return round(float(value), 9)


def _orient_path(
    path: Sequence[Tuple[float, float]],
    source_node: Mapping[str, Any],
    target_node: Mapping[str, Any],
) -> List[Tuple[float, float]]:
    start, end = path[0], path[-1]
    forward = math.hypot(start[0] - source_node["x"], start[1] - source_node["y"])
    forward += math.hypot(end[0] - target_node["x"], end[1] - target_node["y"])
    reverse = math.hypot(end[0] - source_node["x"], end[1] - source_node["y"])
    reverse += math.hypot(start[0] - target_node["x"], start[1] - target_node["y"])
    return list(reversed(path)) if reverse + 1e-12 < forward else list(path)


def _all_points(records: Mapping[str, Sequence[FeatureRecord]]) -> Iterable[Tuple[float, float]]:
    for values in records.values():
        for _, geometry_type, coordinates in values:
            if geometry_type == "Point":
                yield _validate_lon_lat(coordinates, "正式点图层")
            else:
                for point in coordinates:
                    yield _validate_lon_lat(point, "正式线图层")


def _assemble_topology(
    records: Mapping[str, Sequence[FeatureRecord]],
    contract: Mapping[str, Any],
    source_label: str,
) -> Dict[str, Any]:
    fields = contract["fields"]
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    code_to_id: Dict[str, str] = {}

    def business_value(row: Mapping[str, Any], *names: str) -> Any:
        """Return the first populated business-field alias without inventing data."""
        for name in names:
            value = row.get(name)
            if value not in (None, ""):
                return value
        return None

    def add_node(node_type: str, code: str, point: Sequence[float], **extra: Any) -> str:
        if code in code_to_id:
            raise TopologyContractError(f"正式业务编码跨图层重复：{code}")
        lon, lat = _validate_lon_lat(point, f"节点 {code}")
        node_id = f"n_{len(nodes):04d}"
        node = {
            "id": node_id,
            "type": node_type,
            "x": _round_coord(lon),
            "y": _round_coord(lat),
            "code": code,
            **{key: value for key, value in extra.items() if value not in (None, "")},
        }
        nodes.append(node)
        code_to_id[code] = node_id
        return node_id

    for index, (row, _, point) in enumerate(records.get("PTECH") or [], start=1):
        mapping = fields["PTECH"]
        code = _required_text(row, mapping["code"], f"PTECH[{index}]")
        add_node(
            "PETCH",
            code,
            point,
            role="technical_point",
            source_layer="PTECH",
            original_type=row.get(mapping["type"]),
            nature=row.get(mapping["nature"]),
            # Preserve the complete surveyed row for the Task-4 Shapefile
            # contract.  The topology fields above remain the stable planning
            # interface; these attributes are pass-through business data and
            # are never replaced with bundled example records.
            task4_attributes={str(key).upper(): value for key, value in row.items()},
        )

    for index, (row, _, point) in enumerate(records.get("IMB") or [], start=1):
        mapping = fields["IMB"]
        code = _required_text(row, mapping["code"], f"IMB[{index}]")
        residential = _required_nonnegative_int(
            row, mapping["residential"], f"IMB[{index}]"
        )
        professional = _required_nonnegative_int(
            row, mapping["professional"], f"IMB[{index}]"
        )
        ports = _required_nonnegative_int(row, mapping["ports"], f"IMB[{index}]")
        if residential + professional != ports:
            raise TopologyContractError(
                f"IMB[{index}] 必须满足 NB_LOC_RES + NB_LOC_PRO = NB_LOC_TOT"
            )
        add_node(
            "IMB",
            code,
            point,
            ports=ports,
            nb_loc_tot=ports,
            nb_loc_res=residential,
            nb_loc_pro=professional,
            task4_attributes={
                "BPE_CODE": business_value(row, "BPE_CODE"),
                "REF_PLAQUE": business_value(row, "REF_PLAQUE"),
                "TYPE_BATIM": business_value(row, "TYPE_BATIM", "TYPE_BATIMENT"),
                "RACCORDEME": business_value(row, "RACCORDEME", "RACCORDEMENT"),
                "TYPE_VOIE": business_value(row, "TYPE_VOIE"),
                "NOM_VOIE": business_value(row, "NOM_VOIE"),
                "NOM_BATIME": business_value(row, "NOM_BATIME", "NOM_BATIMENT"),
                "NUMERO_VOI": business_value(row, "NUMERO_VOI", "NUMERO_VOIE"),
                "VILLE": business_value(row, "VILLE", "COMMUNE"),
                "CODE_POSTA": business_value(row, "CODE_POSTA", "CODE_POSTAL"),
            },
            source_layer="IMB",
        )

    infra_mapping = fields["INFRASTRUCTURE"]
    site_count = 0
    site_mapping = fields.get("SITE") or {}
    for index, (row, _, point) in enumerate(records.get("SITE") or [], start=1):
        code = _required_text(row, site_mapping["code"], f"SITE[{index}]")
        if code in code_to_id:
            raise TopologyContractError(f"SITE[{index}] 编码与已有正式点重复：{code}")
        add_node(
            "PETCH",
            code,
            point,
            role="site_endpoint",
            source_layer="SITE",
            original_type=row.get(site_mapping.get("type")),
            task4_attributes={str(key).upper(): value for key, value in row.items()},
        )
        site_count += 1

    node_by_id = {node["id"]: node for node in nodes}
    inferred_count = 0
    for index, (row, geometry_type, raw_path) in enumerate(
        records.get("INFRASTRUCTURE") or [], start=1
    ):
        if geometry_type != "LineString":
            raise TopologyContractError(f"INFRASTRUCTURE[{index}] 必须为 LineString")
        path = [
            _validate_lon_lat(point, f"INFRASTRUCTURE[{index}] 路径点")
            for point in raw_path
        ]
        code = _required_text(row, infra_mapping["code"], f"INFRASTRUCTURE[{index}]")
        source_code = _required_text(
            row, infra_mapping["source"], f"INFRASTRUCTURE[{index}]"
        )
        target_code = _required_text(
            row, infra_mapping["target"], f"INFRASTRUCTURE[{index}]"
        )
        for endpoint_code, point in ((source_code, path[0]), (target_code, path[-1])):
            if endpoint_code in code_to_id:
                continue
            if not contract.get("allow_inferred_endpoints", False):
                raise TopologyContractError(
                    f"INFRASTRUCTURE[{index}] 端点 {endpoint_code} 未在正式 PTECH/IMB/SITE 中定义"
                )
            add_node(
                "PETCH",
                endpoint_code,
                point,
                role="inferred_endpoint",
                source_layer="INFRASTRUCTURE",
                needs_review=1,
            )
            node_by_id[nodes[-1]["id"]] = nodes[-1]
            inferred_count += 1

        source_id = code_to_id[source_code]
        target_id = code_to_id[target_code]
        oriented = _orient_path(path, node_by_id[source_id], node_by_id[target_id])
        length = _required_number(
            row, infra_mapping["length"], f"INFRASTRUCTURE[{index}]"
        )
        if length <= 0:
            raise TopologyContractError(f"INFRASTRUCTURE[{index}].LONGUEUR 必须大于 0")
        edges.append(
            {
                "source": source_id,
                "target": target_id,
                "path": [[_round_coord(x), _round_coord(y)] for x, y in oriented],
                "code": code,
                "source_code": source_code,
                "target_code": target_code,
                "source_layer": "INFRASTRUCTURE",
                "length": round(length, 3),
                "task6_attributes": {str(key).upper(): value for key, value in row.items()},
            }
        )

    points = list(_all_points(records))
    bounds = None
    if points:
        xs, ys = zip(*points)
        bounds = {
            "min_x": _round_coord(min(xs)),
            "min_y": _round_coord(min(ys)),
            "max_x": _round_coord(max(xs)),
            "max_y": _round_coord(max(ys)),
        }
    payload = {
        "schema_version": TASK2_SCHEMA_VERSION,
        "meta": {
            "layout": "task1_formal_business_layers",
            "source": source_label,
            # Task-2's unchanged algorithm uses this exact contract value to
            # select geodesic distance for longitude/latitude coordinates.
            # Keep the actual CRS separately instead of changing that semantic
            # switch to an EPSG label.
            "coordinate_system": "source_shapefile_coordinates",
            "crs": "EPSG:4326",
            "axis_order": "x=longitude,y=latitude",
            "ptech_count": len(records.get("PTECH") or []),
            "imb_count": len(records.get("IMB") or []),
            "infrastructure_count": len(records.get("INFRASTRUCTURE") or []),
            "site_endpoint_count": site_count,
            "inferred_endpoint_count": inferred_count,
            "bounds": bounds,
            "width": _round_coord(bounds["max_x"] - bounds["min_x"]) if bounds else 0,
            "height": _round_coord(bounds["max_y"] - bounds["min_y"]) if bounds else 0,
        },
        "nodes": nodes,
        "edges": edges,
    }
    validate_task2_topology_payload(payload)
    return payload


def validate_task2_topology_payload(payload: Mapping[str, Any]) -> Dict[str, int]:
    nodes = payload.get("nodes")
    edges = payload.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise TopologyContractError("任务二拓扑必须包含数组 nodes 和 edges")
    node_ids = [str(node.get("id") or "") for node in nodes if isinstance(node, Mapping)]
    if len(node_ids) != len(nodes) or not all(node_ids) or len(node_ids) != len(set(node_ids)):
        raise TopologyContractError("任务二节点 id 必须非空且唯一")
    node_id_set = set(node_ids)
    codes = [str(node.get("code") or "") for node in nodes]
    if not all(codes) or len(codes) != len(set(codes)):
        raise TopologyContractError("任务二节点 code 必须非空且唯一")
    for node in nodes:
        _validate_lon_lat((node.get("x"), node.get("y")), f"节点 {node.get('id')}")
        if str(node.get("type") or "").upper() == "IMB":
            ports = node.get("ports", node.get("nb_loc_tot"))
            if not isinstance(ports, int) or isinstance(ports, bool) or ports < 0:
                raise TopologyContractError(f"IMB 节点 {node.get('id')} 缺少非负整数 ports")
    for index, edge in enumerate(edges, start=1):
        if edge.get("source") not in node_id_set or edge.get("target") not in node_id_set:
            raise TopologyContractError(f"任务二 edge[{index}] 引用了未知节点")
        path = edge.get("path")
        if not isinstance(path, list) or len(path) < 2:
            raise TopologyContractError(f"任务二 edge[{index}].path 至少需要两个点")
        for point in path:
            _validate_lon_lat(point, f"任务二 edge[{index}] 路径")
        length = edge.get("length")
        if not isinstance(length, (int, float)) or isinstance(length, bool) or length <= 0:
            raise TopologyContractError(f"任务二 edge[{index}].length 必须大于 0")
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "ptech_count": sum(str(node.get("type") or "").upper() in {"PTECH", "PETCH"} for node in nodes),
        "imb_count": sum(str(node.get("type") or "").upper() == "IMB" for node in nodes),
        "total_imb_ports": sum(
            int(node.get("ports", node.get("nb_loc_tot", 0)))
            for node in nodes
            if str(node.get("type") or "").upper() == "IMB"
        ),
    }


def atomic_write_json(path: str | Path, payload: Mapping[str, Any]) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, target)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return target


def export_task2_topology(
    gpkg_path: str | Path,
    output_path: str | Path,
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    payload = build_task2_topology_from_gpkg(gpkg_path, config)
    summary = validate_task2_topology_payload(payload)
    target = atomic_write_json(output_path, payload)
    return {
        "status": "success",
        "schema_version": TASK2_SCHEMA_VERSION,
        "task2_topology_json": str(target),
        "summary": summary,
    }


def export_from_config(config_path: str | Path) -> Dict[str, Any]:
    path = Path(config_path).expanduser().resolve()
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise TopologyContractError("任务一配置必须是 JSON 对象")
    contract = topology_contract(config)
    gpkg = Path(str(config["output"]["geopackage"]))
    output = Path(str(contract["output_json"]))
    if not gpkg.is_absolute():
        gpkg = path.parent / gpkg
    if not output.is_absolute():
        output = path.parent / output
    return export_task2_topology(gpkg, output, config)


def main() -> int:
    parser = argparse.ArgumentParser(description="将任务一正式业务 GeoPackage 转成任务二拓扑 JSON")
    parser.add_argument("config", nargs="?", default="task1_config.json")
    args = parser.parse_args()
    try:
        result = export_from_config(args.config)
    except Exception as exc:
        result = {
            "status": "failed",
            "errors": [f"{type(exc).__name__}: {exc}"],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
