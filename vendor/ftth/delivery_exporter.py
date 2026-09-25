"""Assemble and safely publish a traceable FTTH delivery package."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .engine.delivery_validator import _read_dbf, _read_shp, run as validate_delivery
    from .shapefile_writer import write_layer
except ImportError:  # direct CLI execution
    from engine.delivery_validator import _read_dbf, _read_shp, run as validate_delivery
    from shapefile_writer import write_layer


RESULT_PREFIX = "FTTH_EXPORT_RESULT="


def _read_json(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    value = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是对象：{source}")
    return value


def _read_csv(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with source.open("r", encoding=encoding, newline="") as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法读取 CSV：{source}")


def _upper(row: dict[str, Any]) -> dict[str, Any]:
    return {str(key or "").strip().upper(): value for key, value in row.items()}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = fields or list(rows[0]) if rows else (fields or [])
    if not columns:
        raise ValueError(f"CSV 没有字段：{path.name}")
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _c(name: str, width: int = 40) -> tuple[str, str, int, int]: return (name[:10], "C", width, 0)
def _n(name: str, width: int = 10) -> tuple[str, str, int, int]: return (name[:10], "N", width, 0)
def _f(name: str) -> tuple[str, str, int, int]: return (name[:10], "F", 18, 8)


SCHEMAS = {
    "IMB": [_c("CODE",32),_c("REF_PLAQUE",20),_c("REGION"),_c("PROVINCE"),_c("VILLE"),_c("COMMUNE"),_c("CODE_POSTAL",16),_c("NUMERO_VOIE",16),_c("TYPE_VOIE"),_c("NOM_VOIE"),_c("TYPE_BATIMENT"),_c("TYPE_CLIENT"),_n("NB_LOC_RES"),_n("NB_LOC_PRO"),_n("NB_LOC_TOT"),_c("RACCORDEMENT"),_c("STATUT"),_n("NB_ETAGE"),_c("COL_MONTANTE"),_c("SOUS_SOL"),_c("SOUS_SOL_COMMUN"),_c("BPE_CODE",80),_f("X"),_f("Y")],
    "SITE": [_c("CODE",32),_c("REF_PLAQUE",20),_c("REF_NRO",32),_c("TYPE",12),_c("FABRIQUANT"),_c("REF_PRODUIT"),_c("MODE_POSE"),_c("STATUT"),_c("PROPRIETAIRE"),_c("GESTIONNAIRE"),_c("ADRESSSE",80),_c("COMMUNE"),_c("CODE_POSTAL",16),_f("X"),_f("Y")],
    "BOITE": [_c("CODE",32),_c("CODE_PTC",32),_c("REF_PLAQUE",20),_c("REF_NRO",32),_c("REF_PM",32),_c("TYPE",12),_c("TYPE_STRUCTURE"),_c("MODE_POSE"),_n("CAPACITE"),_n("NB_LOGEMENT"),_n("NB_SPLICES"),_n("NB_FIBRE_UTIL"),_c("FABRIQUANT"),_c("REF_BPE"),_n("NB_CASSETTES_MAX"),_c("CABLE_AMONT",32),_c("STATUT"),_c("PROPRIETAIRE"),_c("GESTIONNAIRE"),_c("ADRESSSE",80),_c("VILLE"),_c("CODE_POSTAL",16),_f("X"),_f("Y")],
    "CABLE": [_c("CODE",64),_c("REF_PLAQUE",20),_c("REF_NRO",32),_c("REF_PM",32),_c("CODE_INFRA",64),_c("ORIGINE",40),_c("EXTREMITE",40),_c("TYPE_CABLE"),_f("DIAMETRE"),_c("MODE_POSE"),_n("CAPACITE"),_n("MODULO"),_c("FABRIQUANT"),_c("REF_PRODUIT"),_c("TYPE_FIBRE"),_n("NB_FIBRE_UTIL"),_n("NB_FIBRE_DISP"),_c("STATUT"),_c("PROPRIETAIRE"),_c("GESTIONNAIRE"),_c("TYPE_PROP"),_f("LONGUEUR")],
    "PTECH": [_c("CODE",32),_c("REF_PLAQUE",20),_c("TYPE"),_c("NATURE"),_f("HAUTEUR_APPUI"),_c("TYPE_APPUI"),_f("EFFORT_APPUI"),_n("NB_BOITIERS"),_c("STATUT"),_c("PROPRIETAIRE"),_c("GESTIONNAIRE"),_c("ADRESSSE",80),_c("VILLE"),_c("CODE_POSTAL",16),_f("X"),_f("Y")],
    "INFRASTRUCTURE": [_c("CODE",32),_c("REF_PLAQUE",20),_c("ORIGINE",32),_c("EXTREMITE",32),_c("COMPOSITION"),_c("TYPE"),_c("TYPE_LOG"),_c("STATUT"),_c("PROPRIETAIRE"),_c("GESTIONNAIRE"),_f("LONGUEUR")],
    "ZNRO": [_c("CODE",32),_c("REF_PLAQUE",20),_c("REF_NRO",32),_c("STATUT"),_n("NB_PRISES")],
    "ZPM": [_c("CODE",32),_c("REF_PLAQUE",20),_c("REF_NRO",32),_c("REF_PM",32),_c("STATUT"),_n("NB_PRISES")],
}


ALIASES = {"CODE_POSTA":"CODE_POSTAL","NUMERO_VOI":"NUMERO_VOIE","TYPE_BATIM":"TYPE_BATIMENT","RACCORDEME":"RACCORDEMENT","TYPE_STRUC":"TYPE_STRUCTURE","NB_LOGEMEN":"NB_LOGEMENT","NB_FIBRE_U":"NB_FIBRE_UTIL","NB_CASSETT":"NB_CASSETTES_MAX","CABLE_AMON":"CABLE_AMONT","PROPRIETAI":"PROPRIETAIRE","PROPRETAI":"PROPRIETAIRE","GESTIONNAI":"GESTIONNAIRE","REF_PRODUI":"REF_PRODUIT","HAUTEUR_AP":"HAUTEUR_APPUI","EFFORT_APP":"EFFORT_APPUI","NB_BOITIER":"NB_BOITIERS","NIB_PRISES":"NB_PRISES"}


def _logical(row: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key, value in _upper(row).items(): result[ALIASES.get(key, key)] = value
    return result


def _row(schema: list[tuple[str,str,int,int]], source: dict[str, Any], defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    values = {**(defaults or {}), **_logical(source)}
    result = {}
    for name, kind, _width, _decimals in schema:
        value = values.get(name)
        if value in (None, ""):
            value = 0 if kind in {"N", "F"} else "A_VERIFIER"
        result[name] = value
    return result


def _wkt_line(value: Any) -> list[tuple[float,float]]:
    match = re.search(r"LINESTRING\s*\((.+)\)", str(value or ""), re.I)
    if not match: raise ValueError(f"仅支持LINESTRING WKT：{value}")
    return [tuple(float(number) for number in item.strip().split()[:2]) for item in match.group(1).split(",")]


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    unique = sorted(set((float(x), float(y)) for x, y in points))
    if len(unique) < 3:
        raise ValueError("服务区包络至少需要3个不同坐标点")

    def cross(origin, first, second):
        return (first[0] - origin[0]) * (second[1] - origin[1]) - (first[1] - origin[1]) * (second[0] - origin[0])

    lower = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    hull = lower[:-1] + upper[:-1]
    if hull[0] != hull[-1]:
        hull.append(hull[0])
    return hull


def _shape_records(base: Path) -> tuple[list[dict[str, Any]], list[Any]]:
    _fields, rows = _read_dbf(base.with_suffix(".dbf"))
    _kind, geometries = _read_shp(base.with_suffix(".shp"))
    return rows, geometries


def _load_pipeline_final_layers(run_dir: Path, shape_dir: Path) -> dict[str, Any] | None:
    """Reuse the exact FTTH(4) ``build_final_layers.py`` result when present."""
    source = run_dir / "final_layers"
    validation_path = source / "field_validation.json"
    if not validation_path.is_file():
        return None
    validation = _read_json(validation_path)
    if validation.get("status") != "PASS":
        raise ValueError("连续批次的 FTTH(4) 八图层校验未通过")
    rows: dict[str, list[dict[str, Any]]] = {}
    geometries: dict[str, list[Any]] = {}
    for name in SCHEMAS:
        base = source / name
        required = [base.with_suffix(suffix) for suffix in (".shp", ".dbf", ".shx", ".prj", ".cpg")]
        missing = [str(path) for path in required if not path.is_file() or not path.stat().st_size]
        if missing:
            raise ValueError(f"FTTH(4) 最终图层 {name} 不完整：{missing}")
        for path in required:
            shutil.copy2(path, shape_dir / path.name)
        rows[name], geometries[name] = _shape_records(shape_dir / name)
    task6 = _read_json(run_dir / "task6" / "layered_plan.json")
    routes = task6.get("duct_routes") or []
    valid_routes = [route for route in routes if len(route.get("coordinates") or []) >= 2]
    skipped_routes = [
        {
            "id": route.get("id"),
            "route_type": route.get("route_type"),
            "from_code": route.get("from_code"),
            "to_code": route.get("to_code"),
            "coordinate_count": len(route.get("coordinates") or []),
            "length_m": route.get("length_m"),
        }
        for route in routes if len(route.get("coordinates") or []) < 2
    ]
    route_count = len(valid_routes)
    if len(rows["INFRASTRUCTURE"]) != route_count or route_count <= 0:
        raise ValueError(
            f"INFRASTRUCTURE 来源校验失败：图层={len(rows['INFRASTRUCTURE'])}，任务六 duct_routes={route_count}"
        )
    mapping_rows = [_upper(row) for row in _read_csv(run_dir / "task4" / "output" / "IMB_PBO_BPE_mapping.csv")]
    unit_map_path = run_dir / "task3" / "input" / "service_unit_map.json"
    unit_map = _read_json(unit_map_path).get("unit_to_imb") if unit_map_path.is_file() else {}
    return {
        "rows": rows,
        "geometries": geometries,
        "mapping_rows": mapping_rows,
        "unit_map": unit_map or {},
        "fiber": _read_json(run_dir / "task5" / "fiber_link_plan.json"),
        "task6": task6,
        "topology": _read_json(run_dir / "base" / "qgis_ftth_dataset.json"),
        "service_area_adjustments": [],
        "capacity_adjustments": [],
        "formal_layer_source": "ftth4_build_final_layers",
        "task6_route_filter": {
            "raw_count": len(routes),
            "valid_linestring_count": route_count,
            "skipped_zero_length_or_invalid": skipped_routes,
        },
    }


def _ensure_pm_distribution_capacity(
    box_rows: list[dict[str, Any]], cable_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Size exported PM feeder cables for their assigned PBO capacity.

    This is a delivery-contract transformation, matching the updated upstream
    ``build_final_layers.py``.  It does not edit Task-4/5 algorithm outputs.
    """
    required: dict[str, int] = {}
    outgoing: dict[str, list[dict[str, Any]]] = {}
    for row in box_rows:
        if str(row.get("TYPE") or "").upper() != "PBO":
            continue
        pm = str(row.get("REF_PM") or "")
        required[pm] = required.get(pm, 0) + int(float(row.get("CAPACITE") or 0))
    for row in cable_rows:
        if str(row.get("TYPE_CABLE") or "").upper() != "DISTRIBUTION":
            continue
        outgoing.setdefault(str(row.get("ORIGINE") or ""), []).append(row)

    adjustments = []
    for pm, required_capacity in sorted(required.items()):
        cables = outgoing.get(pm) or []
        actual = sum(int(float(row.get("CAPACITE") or 0)) for row in cables)
        shortfall = required_capacity - actual
        if shortfall <= 0 or not cables:
            continue
        cable = cables[0]
        old_capacity = int(float(cable.get("CAPACITE") or 0))
        used = int(float(cable.get("NB_FIBRE_U") or 0))
        cable["CAPACITE"] = old_capacity + shortfall
        cable["NB_FIBRE_D"] = max(0, int(cable["CAPACITE"]) - used)
        cable["REF_PRODUI"] = f"FIBER_{cable['CAPACITE']}_CORE"
        adjustments.append({
            "rule": "PM_DISTRIBUTION_CAPACITY",
            "pm_code": pm,
            "cable_code": cable.get("CODE"),
            "old_capacity": old_capacity,
            "new_capacity": cable["CAPACITE"],
            "required_pbo_capacity": required_capacity,
            "reason": "updated_final_layer_contract",
        })
    return adjustments


def _source_path(run_dir: Path, report: dict[str, Any], declared: str | Path) -> Path:
    path = Path(declared)
    if path.is_file():
        return path
    old_root = Path(str(report.get("output_dir") or run_dir))
    try:
        relative = path.relative_to(old_root)
    except ValueError:
        return path
    return run_dir / relative


def _verify_source_run(run_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    report_path, manifest_path = run_dir / "connection_report.json", run_dir / "run_manifest.json"
    if not report_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("所选目录不是完整的连续运行批次，缺少 connection_report.json 或 run_manifest.json")
    report, manifest = _read_json(report_path), _read_json(manifest_path)
    if report.get("status") != "success" or not report.get("downstream"):
        raise ValueError("只有成功完成任务四、六、五的批次才能导出")
    downstream = report["downstream"]
    statuses = {
        "task4": (downstream.get("task4") or {}).get("status"),
        "task6": ((downstream.get("task6") or {}).get("validation") or {}).get("status"),
        "task5": ((downstream.get("task5") or {}).get("validation") or {}).get("status"),
    }
    if statuses != {"task4": "success", "task6": "PASS", "task5": "PASS"}:
        raise ValueError(f"下游校验未全部通过：{statuses}")
    failures = []
    for name, item in (manifest.get("artifacts") or {}).items():
        path = _source_path(run_dir, report, item.get("path", ""))
        if not path.is_file():
            failures.append(f"{name}:missing")
        elif item.get("sha256") and _sha256(path) != str(item["sha256"]).lower():
            failures.append(f"{name}:sha256")
    if failures:
        raise ValueError("源批次清单校验失败：" + "；".join(failures[:20]))
    return report, manifest


def _coordinates(value: Any) -> list[tuple[float, float]]:
    result = []
    for point in value or []:
        if isinstance(point, dict):
            result.append((float(point.get("x", point.get("X"))), float(point.get("y", point.get("Y")))))
        else:
            result.append((float(point[0]), float(point[1])))
    return result


def _build_formal_layers(run_dir: Path, shape_dir: Path) -> dict[str, Any]:
    supplied = _load_pipeline_final_layers(run_dir, shape_dir)
    if supplied is not None:
        return supplied
    topology = _read_json(run_dir / "base" / "qgis_ftth_dataset.json")
    mapping_rows = [_upper(row) for row in _read_csv(run_dir / "task4" / "output" / "IMB_PBO_BPE_mapping.csv")]
    unit_map = _read_json(run_dir / "task3" / "input" / "service_unit_map.json").get("unit_to_imb") or {}
    bpes: dict[str, list[str]] = {}
    for item in mapping_rows:
        unit = str(item.get("IMB_CODE") or "")
        parent = str(unit_map.get(unit, unit))
        bpes.setdefault(parent, []).append(str(item.get("LEAF_BPE_CODE") or ""))

    imb_rows, imb_geoms = [], []
    for node in topology.get("nodes") or []:
        if str(node.get("type") or "").upper() != "IMB":
            continue
        attrs = dict(node.get("task4_attributes") or {})
        code = str(node.get("code") or node.get("id") or "")
        source = {
            **attrs,
            "CODE": code,
            "NB_LOC_RES": node.get("nb_loc_res", node.get("ports", 0)),
            "NB_LOC_PRO": node.get("nb_loc_pro", 0),
            "NB_LOC_TOT": node.get("ports", 0),
            "BPE_CODE": ";".join(sorted(set(filter(None, bpes.get(code, []))))) or "A_VERIFIER",
            "X": node.get("x"),
            "Y": node.get("y"),
        }
        imb_rows.append(_row(SCHEMAS["IMB"], source))
        imb_geoms.append((float(node["x"]), float(node["y"])))
    write_layer(shape_dir / "IMB", "Point", SCHEMAS["IMB"], imb_rows, imb_geoms)

    znro_selected = [_logical(row) for row in _read_csv(run_dir / "task3" / "output" / "ZNRO_selected.csv")]
    zpm_selected = [_logical(row) for row in _read_csv(run_dir / "task3" / "output" / "ZPM_selected.csv")]
    sites = [_logical(row) for row in _read_csv(run_dir / "task3" / "output" / "SITE_selected.csv")]
    sites = [row for row in sites if str(row.get("TYPE") or "").upper() in {"NRO", "PM"}]
    task6 = _read_json(run_dir / "task6" / "layered_plan.json")
    optimized_pm = {
        str(item.get("id") or item.get("code")): item
        for item in (
            (task6.get("fixed_assignments") or {}).get("pm_sites")
            or task6.get("pm_sites")
            or []
        )
    }
    for row in sites:
        if str(row.get("TYPE") or "").upper() != "PM":
            continue
        candidate = optimized_pm.get(str(row.get("CODE") or ""))
        if candidate and candidate.get("x") is not None and candidate.get("y") is not None:
            row["X"], row["Y"] = candidate["x"], candidate["y"]
    nro_code = next((str(row.get("CODE")) for row in sites if str(row.get("TYPE")).upper() == "NRO"), "A_VERIFIER")
    site_rows = [
        _row(SCHEMAS["SITE"], row, {
            "REF_PLAQUE": "A_VERIFIER", "REF_NRO": row.get("REF_NRO") or nro_code,
            "FABRIQUANT": "A_VERIFIER", "REF_PRODUIT": "A_VERIFIER", "MODE_POSE": "A_VERIFIER",
            "PROPRIETAIRE": "A_VERIFIER", "GESTIONNAIRE": "A_VERIFIER", "ADRESSSE": "A_VERIFIER",
            "COMMUNE": row.get("COMMUNE") or row.get("VILLE") or "A_VERIFIER", "CODE_POSTAL": "A_VERIFIER",
        })
        for row in sites
    ]
    site_geoms = [(float(row["X"]), float(row["Y"])) for row in sites]
    write_layer(shape_dir / "SITE", "Point", SCHEMAS["SITE"], site_rows, site_geoms)

    box_source, box_geoms = _shape_records(run_dir / "task4" / "output" / "BOITE_ALGORITHM")
    box_rows = [_row(SCHEMAS["BOITE"], row, {"CABLE_AMONT": "A_VERIFIER"}) for row in box_source]
    write_layer(shape_dir / "BOITE", "Point", SCHEMAS["BOITE"], box_rows, box_geoms)

    task3_ptech = run_dir / "task3" / "input" / "PTECH.csv"
    if task3_ptech.is_file():
        ptech_values_source = [_logical(row) for row in _read_csv(task3_ptech)]
    else:
        source_rows, source_points = _shape_records(run_dir / "task4" / "input_shapes" / "PTECH")
        ptech_values_source = []
        for row, point in zip(source_rows, source_points):
            logical = _logical(row)
            logical["X"], logical["Y"] = point
            ptech_values_source.append(logical)
    ptech_by_code = {
        str(row.get("CODE")): row
        for row in ptech_values_source
        if row.get("CODE")
    }
    for item in task6.get("ptech_plan") or []:
        logical = _logical(item)
        code = str(logical.get("CODE") or logical.get("ID") or "")
        if code:
            logical["CODE"] = code
            ptech_by_code[code] = logical
    ptech_values = list(ptech_by_code.values())
    ptech_rows = [
        _row(SCHEMAS["PTECH"], row, {
            "REF_PLAQUE": "A_VERIFIER", "TYPE": row.get("POINT_TYPE") or "AUTRE", "NATURE": "CREATION",
            "STATUT": "PROJET", "PROPRIETAIRE": "A_VERIFIER", "GESTIONNAIRE": "A_VERIFIER",
            "ADRESSSE": "A_VERIFIER", "VILLE": "A_VERIFIER", "CODE_POSTAL": "A_VERIFIER",
        })
        for row in ptech_values
    ]
    ptech_geoms = [(float(row.get("X")), float(row.get("Y"))) for row in ptech_values]
    write_layer(shape_dir / "PTECH", "Point", SCHEMAS["PTECH"], ptech_rows, ptech_geoms)

    infrastructure = []
    infra_geoms = []
    route_to_infra: dict[str, str] = {}
    for index, route in enumerate(task6.get("duct_routes") or [], 1):
        geometry = _coordinates(route.get("coordinates"))
        if len(geometry) < 2:
            continue
        code = f"INF-{index:05d}"
        route_to_infra[str(route.get("id") or "")] = code
        route_type = str(route.get("route_type") or "")
        infrastructure.append({
            "CODE": code,
            "REF_PLAQUE": "A_VERIFIER", "ORIGINE": route.get("from_code") or "A_VERIFIER",
            "EXTREMITE": route.get("to_code") or "A_VERIFIER", "COMPOSITION": "CONDUITE",
            "TYPE": "SOUTERRAIN", "TYPE_LOG": "TRANSPORT" if route_type == "NRO_TO_PM_MAIN" else "DISTRIBUTION",
            "STATUT": "PROJET", "PROPRIETAIRE": "A_VERIFIER",
            "GESTIONNAIRE": "A_VERIFIER", "LONGUEUR": route.get("length_m") or 0.01,
        })
        infra_geoms.append(geometry)
    infra_rows = [_row(SCHEMAS["INFRASTRUCTURE"], row) for row in infrastructure]
    write_layer(shape_dir / "INFRASTRUCTURE", "LineString", SCHEMAS["INFRASTRUCTURE"], infra_rows, infra_geoms)

    regions = _read_json(run_dir / "task2" / "qgis_direct_region_partition_result.json")
    znro_refs = {str(row.get("CODE")): row for row in znro_selected}
    zpm_refs = {str(row.get("CODE")): row for row in zpm_selected}
    znro_rows, znro_geoms = [], []
    for region in regions.get("znro_regions") or []:
        selected = znro_refs.get(str(region.get("id")), {})
        znro_rows.append(_row(SCHEMAS["ZNRO"], {
            "CODE": region.get("id"), "REF_PLAQUE": "A_VERIFIER",
            "REF_NRO": selected.get("REF_NRO") or nro_code, "STATUT": "PROJET",
            "NB_PRISES": region.get("total_ports"),
        }))
        znro_geoms.append([region["geometry"]["polygon"]])
    write_layer(shape_dir / "ZNRO", "Polygon", SCHEMAS["ZNRO"], znro_rows, znro_geoms)
    zpm_rows, zpm_geoms = [], []
    for region in regions.get("zpm_regions") or []:
        selected = zpm_refs.get(str(region.get("id")), {})
        pm_code = str(selected.get("REF_PM") or region.get("id"))
        zpm_rows.append(_row(SCHEMAS["ZPM"], {
            "CODE": pm_code, "REF_PLAQUE": "A_VERIFIER", "REF_NRO": selected.get("REF_NRO") or nro_code,
            "REF_PM": pm_code, "STATUT": "PROJET", "NB_PRISES": region.get("total_ports"),
        }))
        zpm_geoms.append([item["polygon"] for item in region.get("geometry", {}).get("polygons") or []])
    write_layer(shape_dir / "ZPM", "Polygon", SCHEMAS["ZPM"], zpm_rows, zpm_geoms)

    fiber = _read_json(run_dir / "task5" / "fiber_link_plan.json")
    devices = fiber.get("devices") or {}
    bpe_pm = {str(row.get("id")): str(row.get("pm_id") or "") for row in devices.get("bpe") or []}
    pbo_pm = {str(row.get("id")): str(row.get("pm_id") or "") for row in devices.get("pbo") or []}
    cable_rows, cable_geoms = [], []
    for link in fiber.get("links") or []:
        coordinates = _coordinates(link.get("coordinates"))
        if len(coordinates) < 2:
            continue
        stage = str(link.get("stage") or "")
        origin, end = str(link.get("from_code") or ""), str(link.get("to_code") or "")
        pm_code = end if stage == "NRO_TO_PM" else origin if stage == "PM_TO_BPE" else bpe_pm.get(origin) if stage == "BPE_TO_PBO" else pbo_pm.get(origin)
        cable_type = "DISTRIBUTION" if stage in {"PM_TO_BPE", "BPE_TO_PBO"} else "TRANSPORT" if stage == "NRO_TO_PM" else "BRANCHEMENT"
        capacity = int(link.get("cable_cores") or 24)
        used = min(capacity, int(link.get("sizing_requirement_cores") or 1))
        cable_rows.append(_row(SCHEMAS["CABLE"], {
            "CODE": link.get("id"), "REF_PLAQUE": "A_VERIFIER", "REF_NRO": nro_code,
            "REF_PM": pm_code or "A_VERIFIER", "CODE_INFRA": (
                ";".join(
                    route_to_infra[value]
                    for value in link.get("conduit_route_ids") or []
                    if value in route_to_infra
                )
                or ("INDOOR" if stage == "PBO_TO_IMB" else "DIRECT_REVIEW")
            ),
            "ORIGINE": origin, "EXTREMITE": end, "TYPE_CABLE": cable_type, "DIAMETRE": 1,
            "MODE_POSE": "CONDUITE", "CAPACITE": capacity, "MODULO": capacity,
            "FABRIQUANT": "A_VERIFIER", "REF_PRODUIT": link.get("cable_spec") or "A_VERIFIER",
            "TYPE_FIBRE": "MONOMODE", "NB_FIBRE_UTIL": used, "NB_FIBRE_DISP": capacity - used,
            "STATUT": "PROJET", "PROPRIETAIRE": "A_VERIFIER", "GESTIONNAIRE": "A_VERIFIER",
            "TYPE_PROP": "A_VERIFIER", "LONGUEUR": link.get("length_m") or 0.01,
        }))
        cable_geoms.append(coordinates)
    capacity_adjustments = _ensure_pm_distribution_capacity(box_rows, cable_rows)
    write_layer(shape_dir / "CABLE", "LineString", SCHEMAS["CABLE"], cable_rows, cable_geoms)

    # Do not redraw or enlarge Task-2 service areas during export.  Earlier
    # versions built convex hulls around downstream routes, which made adjacent
    # ZPMs overlap and visually diverge from the algorithm output.  The exact
    # non-overlapping Task-2 polygons are preserved; if a later facility falls
    # outside, the delivery validator reports it instead of silently changing
    # the planning boundary.
    adjustments = []

    return {
        "rows": {"IMB": imb_rows, "SITE": site_rows, "BOITE": box_rows, "CABLE": cable_rows, "PTECH": ptech_rows, "INFRASTRUCTURE": infra_rows, "ZNRO": znro_rows, "ZPM": zpm_rows},
        "geometries": {"IMB": imb_geoms, "SITE": site_geoms, "BOITE": box_geoms, "CABLE": cable_geoms, "PTECH": ptech_geoms, "INFRASTRUCTURE": infra_geoms, "ZNRO": znro_geoms, "ZPM": zpm_geoms},
        "mapping_rows": mapping_rows,
        "unit_map": unit_map,
        "fiber": fiber,
        "task6": task6,
        "topology": topology,
        "service_area_adjustments": adjustments,
        "capacity_adjustments": capacity_adjustments,
    }


def _export_tables(stage: Path, data: dict[str, Any]) -> dict[str, str]:
    tables = stage / "tables"
    equipment = []
    for layer in ("SITE", "BOITE", "PTECH"):
        for row in data["rows"][layer]:
            equipment.append({
                "LAYER": layer, "CODE": row.get("CODE"), "TYPE": row.get("TYPE"),
                "CAPACITY": row.get("CAPACITE", ""), "STATUS": row.get("STATUT"),
                "X": row.get("X"), "Y": row.get("Y"),
            })
    _write_csv(tables / "equipment_list.csv", equipment, ["LAYER", "CODE", "TYPE", "CAPACITY", "STATUS", "X", "Y"])
    _write_csv(tables / "line_list.csv", data["rows"]["CABLE"], [field[0] for field in SCHEMAS["CABLE"]])
    service_rows = []
    for row in data["mapping_rows"]:
        unit = str(row.get("IMB_CODE") or "")
        service_rows.append({
            "PARENT_IMB": data["unit_map"].get(unit, unit), "SERVICE_UNIT": unit,
            "PBO_CODE": row.get("PBO_CODE"), "BPE_CODE": row.get("LEAF_BPE_CODE"), "PM_CODE": row.get("REF_PM"),
        })
    _write_csv(tables / "service_relationships.csv", service_rows, ["PARENT_IMB", "SERVICE_UNIT", "PBO_CODE", "BPE_CODE", "PM_CODE"])
    summary = [{"ITEM": f"LAYER_{name}", "VALUE": len(rows)} for name, rows in data["rows"].items()]
    summary.extend([
        {"ITEM": "TOTAL_PORTS", "VALUE": sum(int(float(row.get("NB_LOC_TOT") or 0)) for row in data["rows"]["IMB"])},
        {"ITEM": "TASK6_ROUTES", "VALUE": len(data["task6"].get("duct_routes") or data["task6"].get("routes") or [])},
        {"ITEM": "TASK5_LINKS", "VALUE": len(data["fiber"].get("links") or [])},
    ])
    _write_csv(tables / "statistics.csv", summary, ["ITEM", "VALUE"])
    source_xlsx = data.get("source_run") / "task4" / "output" / "PBO_BPE_algorithm_plan.xlsx"
    if source_xlsx.is_file():
        shutil.copy2(source_xlsx, tables / source_xlsx.name)
    return {"equipment": str(tables / "equipment_list.csv"), "lines": str(tables / "line_list.csv"), "services": str(tables / "service_relationships.csv"), "statistics": str(tables / "statistics.csv")}


def _export_road_nodes(stage: Path, topology: dict[str, Any]) -> Path:
    unique: dict[tuple[float, float], str] = {}
    features = []
    for edge in topology.get("edges") or []:
        for point in _coordinates(edge.get("path") or edge.get("coordinates")):
            key = (round(point[0], 9), round(point[1], 9))
            if key not in unique:
                unique[key] = f"ROAD_NODE_{len(unique) + 1:05d}"
    for (x, y), code in unique.items():
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [x, y]}, "properties": {"CODE": code, "SOURCE": "TASK1_INFRASTRUCTURE_VERTEX"}})
    target = stage / "auxiliary" / "road_nodes.geojson"
    _write_json(target, {"type": "FeatureCollection", "features": features})
    return target


def _export_gpkg(shape_dir: Path, target: Path) -> None:
    try:
        from osgeo import ogr
    except ImportError as exc:
        raise RuntimeError("当前 QGIS Python 缺少 GDAL/OGR，无法导出 GeoPackage") from exc
    ogr.UseExceptions()
    driver = ogr.GetDriverByName("GPKG")
    if driver is None:
        raise RuntimeError("GDAL 不支持 GPKG")
    dataset = driver.CreateDataSource(str(target))
    if dataset is None:
        raise RuntimeError(f"无法创建 GeoPackage：{target}")
    try:
        for name in SCHEMAS:
            source = ogr.Open(str(shape_dir / f"{name}.shp"), 0)
            if source is None:
                raise RuntimeError(f"无法打开导出图层：{name}")
            layer = source.GetLayer(0)
            if dataset.CopyLayer(layer, name, ["OVERWRITE=YES"]) is None:
                raise RuntimeError(f"无法写入 GPKG 图层：{name}")
            source = None
    finally:
        dataset = None
    check = ogr.Open(str(target), 0)
    if check is None or check.GetLayerCount() != len(SCHEMAS):
        raise RuntimeError("GeoPackage 图层数量校验失败")
    check = None


def _render_plan(stage: Path, data: dict[str, Any], *, png: bool, pdf: bool) -> list[str]:
    if not png and not pdf:
        return []
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    maps = stage / "maps"
    maps.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(11.69, 8.27))
    for geometry in data["geometries"]["ZPM"]:
        for ring in geometry:
            if ring:
                axis.fill([p[0] for p in ring], [p[1] for p in ring], color="#b9e3f5", alpha=0.28, zorder=0)
    for line in data["geometries"]["INFRASTRUCTURE"]:
        axis.plot([p[0] for p in line], [p[1] for p in line], color="#7a7a7a", linewidth=2.2, zorder=1)
    for line in data["geometries"]["CABLE"]:
        axis.plot([p[0] for p in line], [p[1] for p in line], color="#1769aa", linewidth=1.3, alpha=0.9, zorder=2)
    for layer, marker, color, size in (("IMB", "o", "#f2b705", 24), ("SITE", "s", "#e11414", 44), ("BOITE", "^", "#7a3db8", 28), ("PTECH", ".", "#111111", 13)):
        points = data["geometries"][layer]
        axis.scatter([p[0] for p in points], [p[1] for p in points], marker=marker, c=color, s=size, zorder=3)
    axis.set_title("FTTH Planning Overview")
    axis.set_xlabel("Longitude")
    axis.set_ylabel("Latitude")
    axis.grid(True, linewidth=0.3, alpha=0.4)
    axis.set_aspect("equal", adjustable="datalim")
    axis.annotate("N", xy=(0.965, 0.94), xytext=(0.965, 0.84), xycoords="axes fraction", ha="center", arrowprops={"arrowstyle": "-|>", "linewidth": 1.5})
    axis.legend(handles=[
        Line2D([0], [0], color="#7a7a7a", lw=2.2, label="Infrastructure"),
        Line2D([0], [0], color="#1769aa", lw=1.3, label="Cable"),
        Line2D([0], [0], marker="s", color="w", markerfacecolor="#e11414", label="NRO / PM"),
        Line2D([0], [0], marker="^", color="w", markerfacecolor="#7a3db8", label="PBO / BPE"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#f2b705", label="IMB"),
    ], loc="best")
    outputs = []
    if png:
        path = maps / "FTTH_planning_overview.png"
        figure.savefig(path, dpi=220, bbox_inches="tight")
        outputs.append(str(path))
    if pdf:
        path = maps / "FTTH_planning_overview.pdf"
        figure.savefig(path, bbox_inches="tight")
        outputs.append(str(path))
    plt.close(figure)
    return outputs


def export_delivery(
    run_dir: str | Path,
    output_root: str | Path,
    *,
    export_gpkg: bool = True,
    export_png: bool = True,
    export_pdf: bool = True,
    project_crs: str = "EPSG:4326",
) -> dict[str, Any]:
    source = Path(run_dir).expanduser().resolve()
    destination = Path(output_root).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".staging_delivery_", dir=destination))
    run_id = source.name
    final = destination / f"FTTH_delivery_{run_id}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    try:
        report, source_manifest = _verify_source_run(source)
        shape_dir = stage / "shape"
        shape_dir.mkdir(parents=True, exist_ok=True)
        data = _build_formal_layers(source, shape_dir)
        data["source_run"] = source
        validation_path = stage / "reports" / "delivery_validation_report.json"
        validation = validate_delivery(shape_dir, project_crs=project_crs, output=validation_path)
        if validation.get("status") != "passed":
            failure = {"status": "failed", "phase": "formal_contract_validation", "source_run": str(source), "validation": validation, "staging_dir": str(stage)}
            _write_json(stage / "failure_report.json", failure)
            return failure
        tables = _export_tables(stage, data)
        road_nodes = _export_road_nodes(stage, data["topology"])
        gpkg_path = stage / "gis" / "FTTH_delivery.gpkg"
        if export_gpkg:
            gpkg_path.parent.mkdir(parents=True, exist_ok=True)
            _export_gpkg(shape_dir, gpkg_path)
        maps = _render_plan(stage, data, png=export_png, pdf=export_pdf)
        for name in ("qgis_petch_atomic_result.png", "qgis_direct_region_partition_result.png"):
            source_map = source / "task2" / name
            if source_map.is_file():
                target_map = stage / "maps" / name
                target_map.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_map, target_map)
                maps.append(str(target_map))
        traceability = {
            "schema_version": "ftth.delivery-traceability.v1",
            "plugin_version": "2.4.2",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_run": str(source),
            "source_run_id": run_id,
            "source_bridge_version": report.get("bridge_version"),
            "source_manifest_sha256": _sha256(source / "run_manifest.json"),
            "source_artifacts": source_manifest.get("artifacts") or {},
            "user_confirmations": (report.get("downstream") or {}).get("user_confirmations") or {},
            "export_options": {"shapefile": True, "csv_tables": True, "gpkg": export_gpkg, "png": export_png, "pdf": export_pdf},
            "formal_validation": validation,
            "service_area_adjustments": data.get("service_area_adjustments") or [],
            "capacity_adjustments": data.get("capacity_adjustments") or [],
            "task6_route_filter": data.get("task6_route_filter") or {},
        }
        _write_json(stage / "traceability.json", traceability)
        (stage / "成果说明.txt").write_text(
            "本目录由 FTTH 连续规划智能体 v2.4.2 从单一连续运行批次校验并生成。\n"
            "shape 为八个正式合同图层；gis 为统一 GeoPackage；tables 为设备、线路、服务关系和统计表；\n"
            "auxiliary 为道路节点；maps 为规划图和标准图纸素材；reports 为正式合同校验报告。\n"
            "规划成果用于复核，施工发布前仍须确认现场条件和 A_VERIFIER 字段。\n",
            encoding="utf-8",
        )
        required = [validation_path, road_nodes, stage / "traceability.json", *[Path(value) for value in tables.values()]]
        required.extend(shape_dir / f"{name}{suffix}" for name in SCHEMAS for suffix in (".shp", ".dbf", ".shx", ".prj", ".cpg"))
        if export_gpkg:
            required.append(gpkg_path)
        required.extend(Path(value) for value in maps)
        missing = [str(path) for path in required if not path.is_file() or not path.stat().st_size]
        if missing:
            raise ValueError("关键导出文件缺失：" + "；".join(missing[:20]))
        files = []
        for path in sorted(item for item in stage.rglob("*") if item.is_file() and item.name != "delivery_manifest.json"):
            files.append({"path": path.relative_to(stage).as_posix(), "size_bytes": path.stat().st_size, "sha256": _sha256(path)})
        manifest = {
            "schema_version": "ftth.delivery-package.v1",
            "status": "published",
            "source_run_id": run_id,
            "validation_status": validation["status"],
            "file_count": len(files),
            "files": files,
        }
        _write_json(stage / "delivery_manifest.json", manifest)
        os.replace(stage, final)
        return {
            "status": "success", "version": "2.4.2", "source_run": str(source), "output_dir": str(final),
            "shape_dir": str(final / "shape"), "geopackage": str(final / "gis" / "FTTH_delivery.gpkg") if export_gpkg else None,
            "tables_dir": str(final / "tables"), "maps_dir": str(final / "maps"),
            "validation_report": str(final / "reports" / "delivery_validation_report.json"),
            "manifest": str(final / "delivery_manifest.json"), "traceability": str(final / "traceability.json"),
            "validation": validation,
            "warnings": [
                *(validation.get("warnings") or []),
                *([{
                    "rule": "TASK6_ZERO_LENGTH_ROUTE_IGNORED",
                    "message": f"任务六有 {len((data.get('task6_route_filter') or {}).get('skipped_zero_length_or_invalid') or [])} 条零长度或无效逻辑连接未写入 INFRASTRUCTURE。",
                }] if (data.get("task6_route_filter") or {}).get("skipped_zero_length_or_invalid") else []),
            ],
            "task6_route_filter": data.get("task6_route_filter") or {},
            "file_count": len(files),
            "files": [{**item, "path": str(final / Path(item["path"]))} for item in files],
        }
    except Exception as exc:
        failure = {"status": "failed", "phase": "delivery_export", "source_run": str(source), "errors": [f"{type(exc).__name__}: {exc}"], "staging_dir": str(stage)}
        _write_json(stage / "failure_report.json", failure)
        return failure


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and export a complete FTTH delivery package from one connected run")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--no-gpkg", action="store_true")
    parser.add_argument("--no-png", action="store_true")
    parser.add_argument("--no-pdf", action="store_true")
    parser.add_argument("--project-crs", default="EPSG:4326")
    args = parser.parse_args()
    # The plugin launches the selected QGIS python.exe directly through
    # QProcess.  On Windows that process does not inherit the launcher BAT's
    # DLL search setup, so initialise the same read-only QGIS runtime context
    # used by task_bridge before importing GDAL/OGR during GPKG export.
    runtime_handles = None
    result = None
    try:
        try:
            from .task_bridge import prepare_qgis_runtime
        except ImportError:
            from task_bridge import prepare_qgis_runtime
        runtime_handles = prepare_qgis_runtime()
        result = export_delivery(
            args.run_dir, args.output_root,
            export_gpkg=not args.no_gpkg, export_png=not args.no_png,
            export_pdf=not args.no_pdf, project_crs=args.project_crs,
        )
    except Exception as exc:
        result = {
            "status": "failed", "phase": "worker_startup",
            "errors": [f"{type(exc).__name__}: {exc}"],
        }
    finally:
        # Keep add_dll_directory handles and eagerly loaded libraries alive
        # until all writers and drivers have finished.
        runtime_handles = None
    print(RESULT_PREFIX + json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
