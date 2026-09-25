"""Build a task-1 configuration from user-selected tabular/vector files."""

from __future__ import annotations

import copy
import csv
import json
import os
import posixpath
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from qgis.core import QgsVectorLayer


BUSINESS_LAYERS = ("IMB", "SITE", "PTECH", "INFRASTRUCTURE")
REQUIRED_LAYERS = {"IMB"}
POINT_LAYERS = {"PTECH", "SITE", "IMB"}
VECTOR_SUFFIXES = {".shp", ".gpkg", ".geojson", ".json", ".gml", ".kml", ".sqlite"}

ALIASES = {
    "CODE": ("ID", "IDENTIFIER", "OBJECT_ID"),
    "X": ("LON", "LONGITUDE", "经度"),
    "Y": ("LAT", "LATITUDE", "纬度"),
    "NB_LOC_TOT": ("PORTS", "PORT_COUNT", "CAPACITY", "住户数", "户数"),
    "WKT": ("GEOMETRY", "GEOM", "THE_GEOM"),
}


class InputConfigError(ValueError):
    pass


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _csv_headers(path: Path) -> list[str]:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                reader = csv.reader(handle)
                return [str(value).strip() for value in next(reader)]
        except UnicodeDecodeError:
            continue
        except StopIteration as exc:
            raise InputConfigError(f"CSV 没有表头：{path}") from exc
    raise InputConfigError(f"CSV 编码无法识别：{path}")


def _vector_headers(path: Path, layer_name: str) -> tuple[list[str], str]:
    uri = f"{path}|layername={layer_name}" if layer_name else str(path)
    if path.suffix.lower() in {".xls", ".xlsx"}:
        # Force OGR to treat the first worksheet row as field names. Without
        # this option mixed textual/numeric sheets can surface as Field1...
        # on some Windows QGIS/GDAL builds.
        uri += "|option:HEADERS=FORCE"
    layer = QgsVectorLayer(uri, path.stem, "ogr")
    if not layer.isValid():
        hint = f"，请填写正确的工作表/图层名（当前：{layer_name or '空'}）" if path.suffix.lower() in {".gpkg", ".sqlite", ".xls", ".xlsx"} else ""
        raise InputConfigError(f"QGIS 无法读取 {path}{hint}")
    return [field.name() for field in layer.fields()], layer.crs().authid()


def _match_field(formal: str, headers: list[str]) -> str | None:
    upper = {value.upper(): value for value in headers}
    candidates = (formal, *ALIASES.get(formal.upper(), ()))
    for candidate in candidates:
        if str(candidate).upper() in upper:
            return upper[str(candidate).upper()]
    # DBF limits names to ten characters. Accept a unique, case-insensitive
    # truncation match but never guess when two columns could match.
    prefix = formal.upper()[:10]
    truncated = [value for value in headers if value.upper() == prefix]
    return truncated[0] if len(truncated) == 1 else None


def _xlsx_first_row(path: Path, worksheet: str) -> list[str]:
    """Read XLSX column names with only the Python standard library.

    This is a compatibility fallback for GDAL builds that expose a worksheet
    as Field1... when a WKT column makes the header heuristic fail.
    """
    main_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    pkg_rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    with zipfile.ZipFile(path) as archive:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {
            item.attrib["Id"]: item.attrib["Target"]
            for item in relationships.findall(f"{{{pkg_rel_ns}}}Relationship")
        }
        sheet_path = None
        for sheet in workbook.findall(f".//{{{main_ns}}}sheet"):
            if sheet.attrib.get("name") == worksheet:
                target = targets.get(sheet.attrib.get(f"{{{rel_ns}}}id", ""))
                if target:
                    sheet_path = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
                break
        if not sheet_path:
            raise InputConfigError(f"XLSX 中找不到工作表：{worksheet}")
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in strings.findall(f"{{{main_ns}}}si"):
                shared.append("".join(node.text or "" for node in item.iter(f"{{{main_ns}}}t")))
        sheet_xml = ElementTree.fromstring(archive.read(sheet_path))
        row = sheet_xml.find(f".//{{{main_ns}}}sheetData/{{{main_ns}}}row")
        if row is None:
            raise InputConfigError(f"XLSX 工作表没有首行：{worksheet}")
        values: dict[int, str] = {}
        for cell in row.findall(f"{{{main_ns}}}c"):
            reference = cell.attrib.get("r", "")
            letters = re.match(r"[A-Z]+", reference.upper())
            if not letters:
                continue
            column = 0
            for letter in letters.group(0):
                column = column * 26 + ord(letter) - 64
            cell_type = cell.attrib.get("t")
            if cell_type == "inlineStr":
                value = "".join(node.text or "" for node in cell.iter(f"{{{main_ns}}}t"))
            else:
                raw = cell.find(f"{{{main_ns}}}v")
                value = raw.text if raw is not None and raw.text is not None else ""
                if cell_type == "s" and value:
                    value = shared[int(value)]
            values[column - 1] = str(value).strip()
        width = max(values, default=-1) + 1
        headers = [values.get(index, "") for index in range(width)]
        if not headers or any(not name for name in headers) or len(set(name.upper() for name in headers)) != len(headers):
            raise InputConfigError(f"XLSX 工作表首行必须是非空且不重复的字段名：{worksheet}")
        return headers


def _configure_fields(
    layer: dict[str, Any], headers: list[str], source_names: dict[str, str] | None = None
) -> list[str]:
    missing = []
    for field in layer["fields"]:
        matched = _match_field(str(field["name"]), headers)
        if matched:
            field["source_field"] = (source_names or {}).get(matched, matched)
        elif field.get("required"):
            missing.append(str(field["name"]))
    return missing


def build_task1_config(
    template_path: str | Path,
    output_path: str | Path,
    sources: dict[str, dict[str, str]],
    *,
    default_crs: str = "EPSG:4326",
) -> dict[str, Any]:
    """Generate one config; source data remain in their native files."""
    template = Path(template_path).resolve(strict=True)
    target = Path(output_path).expanduser().resolve()
    config = copy.deepcopy(json.loads(template.read_text(encoding="utf-8-sig")))
    by_name = {str(item["name"]).upper(): item for item in config.get("vector_layers") or []}
    errors, summary, missing_optional = [], [], []
    for name in BUSINESS_LAYERS:
        selected = sources.get(name) or {}
        raw_path = str(selected.get("path") or "").strip()
        layer = by_name[name]
        if not raw_path:
            if name in REQUIRED_LAYERS:
                errors.append("IMB 是唯一必填的原始需求图层，请选择输入文件")
                continue
            layer.update(source_type="empty", source="", crs=default_crs)
            for key in (
                "source_layer", "x_field", "y_field", "wkt_field",
                "spreadsheet_header_row", "filter_obj_type",
            ):
                layer.pop(key, None)
            for field in layer.get("fields") or []:
                field.pop("source_field", None)
            missing_optional.append(name)
            summary.append(f"{name}: 未提供，建立空的正式占位层；不伪造现状设施")
            continue
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            errors.append(f"{name} 文件不存在：{path}")
            continue
        suffix = path.suffix.lower()
        sublayer = str(selected.get("source_layer") or "").strip()
        layer["source"] = str(path)
        layer["crs"] = default_crs
        layer.pop("source_layer", None)
        if suffix == ".csv":
            headers = _csv_headers(path)
            if name in POINT_LAYERS:
                x_field = _match_field("X", headers)
                y_field = _match_field("Y", headers)
                if not x_field or not y_field:
                    errors.append(f"{name} CSV 无法识别经纬度字段；支持 X/Y、LON/LAT、LONGITUDE/LATITUDE")
                    continue
                layer.update(source_type="csv_xy", x_field=x_field, y_field=y_field)
                layer.pop("wkt_field", None)
            else:
                wkt_field = _match_field("WKT", headers)
                if not wkt_field:
                    errors.append("INFRASTRUCTURE CSV 必须包含 WKT/GEOMETRY/GEOM 字段")
                    continue
                layer.update(source_type="csv_wkt", wkt_field=wkt_field)
                layer.pop("x_field", None)
                layer.pop("y_field", None)
        elif suffix in {".xls", ".xlsx"}:
            sublayer = sublayer or name
            try:
                headers, detected_crs = _vector_headers(path, sublayer)
            except InputConfigError as exc:
                errors.append(f"{name}: {exc}")
                continue
            layer["source_layer"] = sublayer
            logical_headers = headers
            source_names: dict[str, str] | None = None
            if suffix == ".xlsx" and headers and all(re.fullmatch(r"Field\d+", value, re.IGNORECASE) for value in headers):
                logical_headers = _xlsx_first_row(path, sublayer)
                if len(logical_headers) != len(headers):
                    errors.append(
                        f"{name} 工作表首行字段数与 QGIS 读取列数不一致："
                        f"{len(logical_headers)} != {len(headers)}"
                    )
                    continue
                source_names = dict(zip(logical_headers, headers))
                layer["spreadsheet_header_row"] = True
            else:
                layer.pop("spreadsheet_header_row", None)
            if name in POINT_LAYERS:
                x_field = _match_field("X", logical_headers)
                y_field = _match_field("Y", logical_headers)
                if not x_field or not y_field:
                    errors.append(f"{name} 工作表缺少可识别的 X/Y 或经纬度字段")
                    continue
                x_field = (source_names or {}).get(x_field, x_field)
                y_field = (source_names or {}).get(y_field, y_field)
                layer.update(source_type="spreadsheet_xy", x_field=x_field, y_field=y_field)
                layer.pop("wkt_field", None)
            else:
                wkt_field = _match_field("WKT", logical_headers)
                if not wkt_field:
                    errors.append("INFRASTRUCTURE 工作表必须包含 WKT/GEOMETRY/GEOM 字段")
                    continue
                wkt_field = (source_names or {}).get(wkt_field, wkt_field)
                layer.update(source_type="spreadsheet_wkt", wkt_field=wkt_field)
                layer.pop("x_field", None)
                layer.pop("y_field", None)
        elif suffix in VECTOR_SUFFIXES:
            if suffix in {".gpkg", ".sqlite"}:
                sublayer = sublayer or name
                layer["source_layer"] = sublayer
            try:
                headers, detected_crs = _vector_headers(path, sublayer)
            except InputConfigError as exc:
                errors.append(f"{name}: {exc}")
                continue
            layer["source_type"] = "vector"
            layer.pop("x_field", None)
            layer.pop("y_field", None)
            layer.pop("wkt_field", None)
            # Geometry-native formats do not need a duplicated WKT attribute.
            for field in layer["fields"]:
                if str(field["name"]).lower() == "wkt":
                    field["required"] = False
            if detected_crs:
                layer["crs"] = detected_crs
        else:
            errors.append(f"{name} 不支持的格式：{suffix or '无扩展名'}")
            continue
        missing = _configure_fields(
            layer,
            logical_headers if suffix in {".xls", ".xlsx"} else headers,
            source_names if suffix in {".xls", ".xlsx"} else None,
        )
        if missing:
            errors.append(f"{name} 缺少合同必填字段：{', '.join(missing)}")
        else:
            matched = sum(bool(field.get("source_field")) for field in layer["fields"])
            summary.append(f"{name}: {suffix}，已映射 {matched}/{len(layer['fields'])} 个字段")

    if errors:
        raise InputConfigError("\n".join(errors))
    config["task_id"] = "task1_wizard_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    config["input_mode"] = "imb_only" if len(missing_optional) == 3 else "imb_with_optional_context"
    config["optional_layers_missing"] = missing_optional
    config["project_crs"] = default_crs
    config.setdefault("task2_topology", {})["crs"] = default_crs
    config["output"] = {
        "geopackage": "outputs/base_survey_layers.gpkg",
        "quality_report": "outputs/data_quality_report.json",
        "overwrite_file": True,
        "add_to_project": True,
        "replace_project_layers": True,
        "apply_style": True,
        "capture_screenshot": False,
        "screenshot": "outputs/工程可视化全景.png",
    }
    _atomic_json(target, config)
    return {
        "status": "success",
        "config_path": str(target),
        "input_mode": config["input_mode"],
        "missing_optional": missing_optional,
        "task2_ready": True,
        "task3_ready": not ({"PTECH", "INFRASTRUCTURE"} & set(missing_optional)),
        "sources": summary,
    }
