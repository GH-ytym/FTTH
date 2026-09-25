"""任务一的普通 Python 核心：配置、CSV 和字段质量检查。

该文件不导入 qgis，因此可在普通 Python 环境中独立运行和测试。
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

SUPPORTED_SOURCE_TYPES = {
    "empty",
    "csv_xy",
    "csv_wkt",
    "spreadsheet_xy",
    "spreadsheet_wkt",
    "vector",
}
SUPPORTED_GEOMETRY_TYPES = {"Point", "LineString", "Polygon"}
SUPPORTED_FIELD_TYPES = {"string", "int", "float", "bool"}


class Task1Error(RuntimeError):
    """任务一配置或数据错误。"""


def publish_staged_bundle(
    replacements: Sequence[Tuple[str | Path, str | Path]],
) -> List[str]:
    """Publish a group of staged files and restore every old target on failure."""

    pairs = [(Path(source).resolve(), Path(target).resolve()) for source, target in replacements]
    if not pairs:
        return []

    targets = [target for _, target in pairs]
    if len(set(targets)) != len(targets):
        raise Task1Error("发布清单包含重复目标路径")
    for source, target in pairs:
        if source == target:
            raise Task1Error(f"staging 与目标路径不能相同：{target}")
        if not source.is_file():
            raise Task1Error(f"staging 文件不存在：{source}")
        target.parent.mkdir(parents=True, exist_ok=True)

    backups: Dict[Path, Path] = {}
    published: set[Path] = set()
    try:
        for _, target in pairs:
            if target.exists():
                backup = target.with_name(
                    f".{target.name}.task1_backup_{uuid.uuid4().hex}"
                )
                os.replace(target, backup)
                backups[target] = backup

        for source, target in pairs:
            os.replace(source, target)
            published.add(target)
    except Exception as exc:
        rollback_errors: List[str] = []
        for _, target in reversed(pairs):
            backup = backups.get(target)
            try:
                if backup is not None and backup.exists():
                    os.replace(backup, target)
                elif target in published and target.exists():
                    target.unlink()
            except Exception as rollback_exc:
                rollback_errors.append(
                    f"{target}: {type(rollback_exc).__name__}: {rollback_exc}"
                )
        detail = ""
        if rollback_errors:
            detail = "；回滚异常：" + " | ".join(rollback_errors)
        raise Task1Error(
            f"成果整组发布失败，已尝试恢复旧成果：{type(exc).__name__}: {exc}{detail}"
        ) from exc

    warnings: List[str] = []
    for backup in backups.values():
        try:
            backup.unlink(missing_ok=True)
        except OSError as exc:
            warnings.append(f"新成果已发布，但旧备份清理失败：{backup}；{exc}")
    return warnings


def load_config(config_path: str | Path) -> tuple[Dict[str, Any], Path]:
    path = Path(config_path).expanduser().resolve()
    if not path.exists():
        raise Task1Error(f"找不到配置文件：{path}")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Task1Error(
            f"配置 JSON 格式错误：第{exc.lineno}行，第{exc.colno}列；{exc.msg}"
        ) from exc
    if not isinstance(config, dict):
        raise Task1Error("配置文件最外层必须是 JSON 对象")
    return config, path.parent


def resolve_path(base_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    return path.resolve() if path.is_absolute() else (base_dir / path).resolve()


def portable_path(base_dir: Path, path: Path) -> str:
    """报告中优先使用相对路径，避免写入本机绝对目录。"""
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def convert_value(value: Any, field_type: str) -> Any:
    """按配置字段类型转换；空值保留为 None。"""
    if _is_blank(value):
        return None

    text = str(value).strip()
    if field_type == "string":
        return text
    if field_type == "int":
        number = float(text)
        if not math.isfinite(number) or not number.is_integer():
            raise ValueError("应为整数")
        return int(number)
    if field_type == "float":
        number = float(text)
        if not math.isfinite(number):
            raise ValueError("不是有限数值")
        return number
    if field_type == "bool":
        lowered = text.lower()
        if lowered in {"1", "true", "yes", "y", "是"}:
            return True
        if lowered in {"0", "false", "no", "n", "否"}:
            return False
        raise ValueError("布尔值应为 true/false、1/0、是/否")
    raise ValueError(f"不支持的字段类型：{field_type}")


def filter_csv_rows(
    rows: Sequence[Dict[str, str]], filter_types: Sequence[str]
) -> List[Dict[str, str]]:
    """兼容原接口：按 object_type 过滤 CSV 行。"""
    allowed = {str(value).strip() for value in filter_types if str(value).strip()}
    return [row for row in rows if str(row.get("object_type", "")).strip() in allowed]


def row_matches_filter(row: Dict[str, Any], layer_cfg: Dict[str, Any]) -> bool:
    """判断一行数据是否属于当前业务图层。"""
    filter_types = layer_cfg.get("filter_obj_type")
    if not filter_types:
        return True
    allowed = {str(value).strip() for value in filter_types}
    return str(row.get("object_type", "")).strip() in allowed


def output_layer_name(layer_cfg: Dict[str, Any]) -> str:
    """优先使用业务图层名，未配置时回退到 name。"""
    value = layer_cfg.get("biz_layer_name") or layer_cfg.get("name")
    return str(value).strip()


def _validate_style(style: Any, prefix: str, errors: List[str]) -> None:
    if style is None:
        return
    if not isinstance(style, dict):
        errors.append(f"{prefix}.style 必须是对象")
        return
    for key in ("color", "fill_color", "stroke_color", "label_color"):
        if key in style and (not isinstance(style[key], str) or not style[key].strip()):
            errors.append(f"{prefix}.style.{key} 必须是非空颜色字符串")
    for key in ("size", "width", "stroke_width", "label_size"):
        if key in style and not _is_number(style[key]):
            errors.append(f"{prefix}.style.{key} 必须是数值")
    if "label_field" in style and (
        not isinstance(style["label_field"], str) or not style["label_field"].strip()
    ):
        errors.append(f"{prefix}.style.label_field 必须是非空字符串")


def validate_config(config: Dict[str, Any]) -> List[str]:
    errors: List[str] = []

    if config.get("interface_version") != "1.0":
        errors.append("interface_version 当前必须为 '1.0'")
    if config.get("operation") != "build_base_survey_layers":
        errors.append("operation 必须为 'build_base_survey_layers'")
    if not isinstance(config.get("task_id"), str) or not config.get("task_id", "").strip():
        errors.append("task_id 必须是非空字符串")
    if not isinstance(config.get("project_crs"), str) or not config.get("project_crs", "").strip():
        errors.append("project_crs 必须是非空 CRS 字符串，例如 EPSG:4326")

    output = config.get("output")
    if not isinstance(output, dict):
        errors.append("缺少 output 对象")
    else:
        for key in ("geopackage", "quality_report"):
            if not isinstance(output.get(key), str) or not output[key].strip():
                errors.append(f"output.{key} 必须是非空路径")
        for key in (
            "overwrite_file",
            "add_to_project",
            "replace_project_layers",
            "capture_screenshot",
            "apply_style",
        ):
            if key in output and not isinstance(output[key], bool):
                errors.append(f"output.{key} 必须是布尔值")
        if "screenshot" in output and (
            not isinstance(output["screenshot"], str) or not output["screenshot"].strip()
        ):
            errors.append("output.screenshot 必须是非空路径")

    vector_layers = config.get("vector_layers")
    if not isinstance(vector_layers, list) or not vector_layers:
        errors.append("vector_layers 必须是非空数组")
    else:
        names: set[str] = set()
        output_names: set[str] = set()
        source_filter_map: Dict[str, Dict[str, str]] = {}

        for index, layer in enumerate(vector_layers):
            prefix = f"vector_layers[{index}]"
            if not isinstance(layer, dict):
                errors.append(f"{prefix} 必须是对象")
                continue

            name = layer.get("name")
            if not isinstance(name, str) or not name.strip():
                errors.append(f"{prefix}.name 必须是非空字符串")
            elif name in names:
                errors.append(f"图层名称重复：{name}")
            else:
                names.add(name)

            business_name = output_layer_name(layer)
            if not business_name:
                errors.append(f"{prefix}.biz_layer_name/name 不能同时为空")
            elif business_name in output_names:
                errors.append(f"输出业务图层名称重复：{business_name}")
            else:
                output_names.add(business_name)

            source_type = layer.get("source_type")
            if source_type not in SUPPORTED_SOURCE_TYPES:
                errors.append(f"{prefix}.source_type 不支持：{source_type!r}")

            source = layer.get("source")
            if source_type != "empty" and (
                not isinstance(source, str) or not source.strip()
            ):
                errors.append(f"{prefix}.source 必须是非空路径")
            if source_type == "empty" and source not in (None, ""):
                errors.append(f"{prefix}.source_type=empty 时 source 必须为空")

            geometry_type = layer.get("geometry_type")
            if geometry_type not in SUPPORTED_GEOMETRY_TYPES:
                errors.append(f"{prefix}.geometry_type 不支持：{geometry_type!r}")

            if not isinstance(layer.get("crs"), str) or not layer.get("crs", "").strip():
                errors.append(f"{prefix}.crs 必须是非空 CRS 字符串")

            fields = layer.get("fields")
            field_names: set[str] = set()
            if not isinstance(fields, list) or not fields:
                errors.append(f"{prefix}.fields 必须是非空数组")
            else:
                for field_index, field in enumerate(fields):
                    fp = f"{prefix}.fields[{field_index}]"
                    if not isinstance(field, dict):
                        errors.append(f"{fp} 必须是对象")
                        continue
                    field_name = field.get("name")
                    if not isinstance(field_name, str) or not field_name.strip():
                        errors.append(f"{fp}.name 必须是非空字符串")
                    elif field_name in field_names:
                        errors.append(f"{prefix} 字段名重复：{field_name}")
                    else:
                        field_names.add(field_name)
                    if field.get("type") not in SUPPORTED_FIELD_TYPES:
                        errors.append(f"{fp}.type 不支持：{field.get('type')!r}")
                    if "source_field" in field and (
                        not isinstance(field["source_field"], str)
                        or not field["source_field"].strip()
                    ):
                        errors.append(f"{fp}.source_field 必须是非空字符串")
                    if "required" in field and not isinstance(field["required"], bool):
                        errors.append(f"{fp}.required 必须是布尔值")
                    for bound in ("min", "max"):
                        if bound in field and not _is_number(field[bound]):
                            errors.append(f"{fp}.{bound} 必须是数值")
                    if (
                        "min" in field
                        and "max" in field
                        and _is_number(field["min"])
                        and _is_number(field["max"])
                        and float(field["min"]) > float(field["max"])
                    ):
                        errors.append(f"{fp}.min 不能大于 max")

            id_field = layer.get("id_field")
            if id_field is not None:
                if not isinstance(id_field, str) or not id_field.strip():
                    errors.append(f"{prefix}.id_field 必须是非空字符串")
                elif field_names and id_field not in field_names:
                    errors.append(f"{prefix}.id_field={id_field!r} 未在 fields 中声明")

            filter_types = layer.get("filter_obj_type")
            if filter_types is not None:
                if (
                    not isinstance(filter_types, list)
                    or not filter_types
                    or any(not isinstance(value, str) or not value.strip() for value in filter_types)
                ):
                    errors.append(
                        f'{prefix}.filter_obj_type 必须为非空字符串数组，如 ["manhole", "pole"]'
                    )
                else:
                    normalized = [value.strip() for value in filter_types]
                    if len(normalized) != len(set(normalized)):
                        errors.append(f"{prefix}.filter_obj_type 内部存在重复值")
                    if isinstance(source, str):
                        by_type = source_filter_map.setdefault(source, {})
                        for value in normalized:
                            if value in by_type:
                                errors.append(
                                    f"同一数据源 {source!r} 的过滤值 {value!r} 同时分配给 "
                                    f"{by_type[value]} 和 {business_name}"
                                )
                            else:
                                by_type[value] = business_name

            if source_type in {"csv_xy", "spreadsheet_xy"}:
                for key in ("x_field", "y_field"):
                    if not isinstance(layer.get(key), str) or not layer.get(key, "").strip():
                        errors.append(f"{prefix} 使用 {source_type} 时必须配置 {key}")
            if source_type in {"csv_wkt", "spreadsheet_wkt"} and (
                not isinstance(layer.get("wkt_field"), str)
                or not layer.get("wkt_field", "").strip()
            ):
                errors.append(f"{prefix} 使用 {source_type} 时必须配置 wkt_field")
            if source_type in {"spreadsheet_xy", "spreadsheet_wkt"}:
                suffix = Path(str(source or "")).suffix.lower()
                if suffix not in {".xls", ".xlsx"}:
                    errors.append(
                        f"{prefix} 使用 {source_type} 时 source 必须是 .xls 或 .xlsx"
                    )
                if not isinstance(layer.get("source_layer"), str) or not layer.get(
                    "source_layer", ""
                ).strip():
                    errors.append(
                        f"{prefix} 使用 {source_type} 时必须配置 source_layer（工作表名称）"
                    )
            if source_type == "vector" and isinstance(source, str):
                suffix = Path(source).suffix.lower()
                if suffix in {".gpkg", ".sqlite", ".db"} and (
                    not isinstance(layer.get("source_layer"), str)
                    or not layer.get("source_layer", "").strip()
                ):
                    errors.append(
                        f"{prefix} 读取多层 {suffix} 时必须配置 source_layer"
                    )

            _validate_style(layer.get("style"), prefix, errors)

    rasters = config.get("rasters", [])
    if not isinstance(rasters, list):
        errors.append("rasters 必须是数组")
    else:
        raster_names: set[str] = set()
        for index, raster in enumerate(rasters):
            prefix = f"rasters[{index}]"
            if not isinstance(raster, dict):
                errors.append(f"{prefix} 必须是对象")
                continue
            name = raster.get("name")
            if not isinstance(name, str) or not name.strip():
                errors.append(f"{prefix}.name 必须是非空字符串")
            elif name in raster_names:
                errors.append(f"栅格名称重复：{name}")
            else:
                raster_names.add(name)
            if not isinstance(raster.get("source"), str) or not raster.get("source", "").strip():
                errors.append(f"{prefix}.source 必须是非空路径")
            if "add_to_project" in raster and not isinstance(raster["add_to_project"], bool):
                errors.append(f"{prefix}.add_to_project 必须是布尔值")

    try:
        from task2_topology_adapter import validate_topology_contract_config

        errors.extend(validate_topology_contract_config(config))
    except (ImportError, RuntimeError, TypeError, ValueError) as exc:
        errors.append(f"任务二拓扑合同配置检查失败：{type(exc).__name__}: {exc}")
    return errors


def _read_csv_with_lines(path: Path) -> tuple[List[str], List[Tuple[int, Dict[str, str]]]]:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as file:
                reader = csv.DictReader(file)
                if reader.fieldnames is None:
                    raise Task1Error(f"CSV 没有表头：{path}")
                rows = [(line_number, row) for line_number, row in enumerate(reader, start=2)]
                return list(reader.fieldnames), rows
        except UnicodeDecodeError:
            continue
    raise Task1Error(f"CSV 编码无法识别（支持 UTF-8/GB18030）：{path}")


def _wkt_type(wkt: str) -> str:
    match = re.match(r"^\s*([A-Za-z]+)", wkt)
    return match.group(1).upper() if match else ""


def validate_csv_layer(layer_cfg: Dict[str, Any], base_dir: Path) -> Dict[str, Any]:
    source = resolve_path(base_dir, layer_cfg["source"])
    result: Dict[str, Any] = {
        "name": layer_cfg["name"],
        "biz_layer_name": output_layer_name(layer_cfg),
        "source": portable_path(base_dir, source),
        "source_type": layer_cfg["source_type"],
        "status": "ok",
        "row_count": 0,
        "errors": [],
        "warnings": [],
    }
    if not source.exists():
        result["errors"].append(f"找不到数据文件：{portable_path(base_dir, source)}")
        result["status"] = "error"
        return result

    headers, all_rows = _read_csv_with_lines(source)
    filtered_rows = [
        (line_number, row)
        for line_number, row in all_rows
        if row_matches_filter(row, layer_cfg)
    ]
    result["row_count"] = len(filtered_rows)

    declared_fields = [field.get("source_field", field["name"]) for field in layer_cfg["fields"]]
    required_columns = {
        field.get("source_field", field["name"])
        for field in layer_cfg["fields"] if field.get("required", False)
    }
    if layer_cfg["source_type"] == "csv_xy":
        required_columns.update({layer_cfg["x_field"], layer_cfg["y_field"]})
    elif layer_cfg["source_type"] == "csv_wkt":
        required_columns.add(layer_cfg["wkt_field"])
    if layer_cfg.get("filter_obj_type"):
        required_columns.add("object_type")

    missing_columns = sorted(required_columns - set(headers))
    if missing_columns:
        result["errors"].append("CSV 缺少字段：" + ", ".join(missing_columns))

    undeclared = sorted(set(headers) - set(declared_fields))
    if undeclared:
        result["warnings"].append("CSV 存在未写入输出图层的字段：" + ", ".join(undeclared))

    id_field = layer_cfg.get("id_field")
    seen_ids: set[str] = set()
    duplicate_ids: set[str] = set()
    field_defs = {field["name"]: field for field in layer_cfg["fields"]}

    for row_number, row in filtered_rows:
        for name, field_def in field_defs.items():
            source_name = field_def.get("source_field", name)
            if source_name not in headers:
                continue
            value = row.get(source_name)
            if field_def.get("required", False) and _is_blank(value):
                result["errors"].append(f"第{row_number}行必填字段 {name} 为空")
                continue
            if _is_blank(value):
                continue
            try:
                converted = convert_value(value, field_def["type"])
            except (TypeError, ValueError) as exc:
                result["errors"].append(
                    f"第{row_number}行字段 {name} 类型错误：{value!r}；{exc}"
                )
                continue

            min_value = field_def.get("min")
            max_value = field_def.get("max")
            if converted is not None and isinstance(converted, (int, float)):
                if min_value is not None and converted < float(min_value):
                    result["errors"].append(
                        f"第{row_number}行字段 {name} 小于最小值 {min_value}"
                    )
                if max_value is not None and converted > float(max_value):
                    result["errors"].append(
                        f"第{row_number}行字段 {name} 大于最大值 {max_value}"
                    )

        id_source_field = field_defs.get(id_field, {}).get("source_field", id_field)
        if id_field and id_source_field in headers:
            value = str(row.get(id_source_field, "")).strip()
            if value:
                if value in seen_ids:
                    duplicate_ids.add(value)
                seen_ids.add(value)

        if layer_cfg["source_type"] == "csv_xy":
            x_field = layer_cfg["x_field"]
            y_field = layer_cfg["y_field"]
            if x_field in headers and y_field in headers:
                x_value = row.get(x_field)
                y_value = row.get(y_field)
                if not _is_number(x_value) or not _is_number(y_value):
                    result["errors"].append(
                        f"第{row_number}行坐标无效：x={x_value!r}, y={y_value!r}"
                    )
        elif layer_cfg["source_type"] == "csv_wkt":
            wkt_field = layer_cfg["wkt_field"]
            if wkt_field in headers:
                wkt = str(row.get(wkt_field, "")).strip()
                actual = _wkt_type(wkt)
                expected = layer_cfg["geometry_type"].upper()
                if actual != expected:
                    result["errors"].append(
                        f"第{row_number}行 WKT 类型 {actual or '空/未知'} 与 "
                        f"geometry_type={layer_cfg['geometry_type']} 不一致"
                    )

    if duplicate_ids:
        result["errors"].append(
            f"主键字段 {id_field} 存在重复值：" + ", ".join(sorted(duplicate_ids))
        )
    if not filtered_rows:
        result["warnings"].append("CSV 过滤后无有效数据行")

    if result["errors"]:
        result["status"] = "error"
    elif result["warnings"]:
        result["status"] = "warning"
    return result


def validate_non_csv_layer(layer_cfg: Dict[str, Any], base_dir: Path) -> Dict[str, Any]:
    source = resolve_path(base_dir, layer_cfg["source"])
    result: Dict[str, Any] = {
        "name": layer_cfg["name"],
        "biz_layer_name": output_layer_name(layer_cfg),
        "source": portable_path(base_dir, source),
        "source_type": layer_cfg["source_type"],
        "status": "ok",
        "row_count": None,
        "errors": [],
        "warnings": [
            (
                "Excel 工作表的字段、坐标和几何将在 QGIS/OGR 模式下进一步检查"
                if layer_cfg["source_type"] in {"spreadsheet_xy", "spreadsheet_wkt"}
                else "已有矢量数据的几何和字段类型将在 QGIS 模式下进一步检查"
            )
        ],
    }
    if not source.exists():
        result["errors"].append(f"找不到数据文件：{portable_path(base_dir, source)}")
        result["status"] = "error"
    else:
        result["status"] = "warning"
    return result


def validate_empty_layer(layer_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Report an intentionally absent optional survey layer without failure."""
    return {
        "name": layer_cfg["name"],
        "biz_layer_name": output_layer_name(layer_cfg),
        "source": None,
        "source_type": "empty",
        "status": "warning",
        "row_count": 0,
        "errors": [],
        "warnings": ["未提供可选现状数据；已保留空的正式图层结构，不生成虚假设施"],
    }


def _source_filter_coverage_warnings(
    config: Dict[str, Any], base_dir: Path
) -> List[str]:
    """检查同一 CSV 中是否存在未分配到任何业务图层的 object_type。"""
    groups: Dict[Path, set[str]] = {}
    for layer in config.get("vector_layers", []):
        values = layer.get("filter_obj_type")
        if not values or layer.get("source_type") not in {"csv_xy", "csv_wkt"}:
            continue
        source = resolve_path(base_dir, layer["source"])
        groups.setdefault(source, set()).update(str(value).strip() for value in values)

    warnings: List[str] = []
    for source, assigned in groups.items():
        if not source.exists():
            continue
        headers, rows = _read_csv_with_lines(source)
        if "object_type" not in headers:
            continue
        present = {
            str(row.get("object_type", "")).strip()
            for _, row in rows
            if str(row.get("object_type", "")).strip()
        }
        uncovered = sorted(present - assigned)
        if uncovered:
            warnings.append(
                f"数据源 {portable_path(base_dir, source)} 中存在未分配的 object_type："
                + ", ".join(uncovered)
            )
    return warnings


def build_quality_report(config: Dict[str, Any], base_dir: Path) -> Dict[str, Any]:
    config_errors = validate_config(config)
    report: Dict[str, Any] = {
        "task_id": config.get("task_id"),
        "operation": config.get("operation"),
        "status": "ok",
        "summary": {},
        "configuration_errors": config_errors,
        "configuration_warnings": [],
        "data_relation_errors": [],
        "task2_topology": {"status": "not_checked", "warnings": []},
        "rasters": [],
        "vector_layers": [],
    }

    rasters = config.get("rasters", [])
    if isinstance(rasters, list):
        for raster in rasters:
            if not isinstance(raster, dict):
                report["rasters"].append(
                    {"status": "error", "errors": ["栅格配置必须是对象"], "warnings": []}
                )
                continue
            raw_source = raster.get("source", "")
            source = resolve_path(base_dir, raw_source) if raw_source else base_dir
            item: Dict[str, Any] = {
                "name": raster.get("name"),
                "source": portable_path(base_dir, source),
                "status": "ok",
                "errors": [],
                "warnings": [],
            }
            if not raw_source:
                item["errors"].append("缺少 source")
            elif not source.exists():
                item["errors"].append(
                    f"找不到栅格文件：{portable_path(base_dir, source)}"
                )
            if item["errors"]:
                item["status"] = "error"
            report["rasters"].append(item)

    if not config_errors:
        for layer_cfg in config["vector_layers"]:
            if layer_cfg["source_type"] in {"csv_xy", "csv_wkt"}:
                item = validate_csv_layer(layer_cfg, base_dir)
            elif layer_cfg["source_type"] == "empty":
                item = validate_empty_layer(layer_cfg)
            else:
                item = validate_non_csv_layer(layer_cfg, base_dir)
            report["vector_layers"].append(item)
        report["configuration_warnings"] = _source_filter_coverage_warnings(
            config, base_dir
        )
        if not any(item.get("errors") for item in report["vector_layers"]):
            try:
                from task2_topology_adapter import (
                    TopologyContractError,
                    build_task2_topology_from_sources,
                    topology_contract,
                    validate_task2_topology_payload,
                )

                if topology_contract(config).get("enabled", True):
                    topology = build_task2_topology_from_sources(config, base_dir)
                    report["task2_topology"] = {
                        "status": "ok",
                        "summary": validate_task2_topology_payload(topology),
                        "warnings": [],
                    }
                else:
                    report["task2_topology"] = {
                        "status": "disabled",
                        "warnings": [],
                    }
            except TopologyContractError as exc:
                message = str(exc)
                if message.startswith("validate-only 无法读取"):
                    report["task2_topology"] = {
                        "status": "deferred_to_qgis",
                        "warnings": [message],
                    }
                else:
                    report["data_relation_errors"].append(message)
                    report["task2_topology"] = {
                        "status": "error",
                        "warnings": [],
                    }

    error_count = len(config_errors) + len(report["data_relation_errors"])
    warning_count = len(report["configuration_warnings"]) + len(
        report["task2_topology"].get("warnings", [])
    )
    for group in (report["rasters"], report["vector_layers"]):
        for item in group:
            error_count += len(item.get("errors", []))
            warning_count += len(item.get("warnings", []))

    report["summary"] = {
        "raster_count": len(report["rasters"]),
        "vector_layer_count": len(report["vector_layers"]),
        "error_count": error_count,
        "warning_count": warning_count,
    }
    if error_count:
        report["status"] = "error"
    elif warning_count:
        report["status"] = "warning"
    return report


def save_report(report: Dict[str, Any], output_path: Path) -> None:
    from task2_topology_adapter import atomic_write_json

    atomic_write_json(output_path, report)
