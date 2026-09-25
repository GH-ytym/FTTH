"""Validate the eight-layer FTTH shapefile delivery contract.

The rules are transcribed from ``图层表字段说明和数据校验规则.xlsx``.  This
module deliberately uses only the Python standard library so it can run beside
QGIS without installing GIS/AI packages into QGIS's Python environment.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


LAYER_TYPES = {
    "IMB": "Point",
    "SITE": "Point",
    "BOITE": "Point",
    "CABLE": "LineString",
    "PTECH": "Point",
    "INFRASTRUCTURE": "LineString",
    "ZNRO": "Polygon",
    "ZPM": "Polygon",
}

REQUIRED_FIELDS = {
    "IMB": [
        "CODE", "REF_PLAQUE", "REGION", "PROVINCE", "VILLE", "COMMUNE", "CODE_POSTAL",
        "NUMERO_VOIE", "TYPE_VOIE", "CODE_VOIE|NOM_VOIE", "TYPE_BATIMENT", "TYPE_CLIENT",
        "NB_LOC_RES", "NB_LOC_PRO", "NB_LOC_TOT", "RACCORDEMENT", "STATUT", "NB_ETAGE",
        "COL_MONTANTE", "SOUS_SOL", "SOUS_SOL_COMMUN", "BPE_CODE", "X", "Y",
    ],
    "SITE": [
        "CODE", "REF_PLAQUE", "REF_NRO", "TYPE", "FABRIQUANT", "REF_PRODUIT", "MODE_POSE",
        "STATUT", "PROPRIETAIRE", "GESTIONNAIRE", "ADRESSSE", "COMMUNE", "CODE_POSTAL", "X", "Y",
    ],
    "BOITE": [
        "CODE", "CODE_PTC", "REF_PLAQUE", "REF_NRO", "REF_PM", "TYPE", "TYPE_STRUCTURE",
        "MODE_POSE", "CAPACITE", "NB_LOGEMENT", "NB_SPLICES", "NB_FIBRE_UTIL", "FABRIQUANT",
        "REF_BPE", "NB_CASSETTES_MAX", "CABLE_AMONT", "STATUT", "PROPRIETAIRE", "GESTIONNAIRE",
        "ADRESSSE", "VILLE", "CODE_POSTAL", "X", "Y",
    ],
    "CABLE": [
        "CODE", "REF_PLAQUE", "REF_NRO", "REF_PM", "CODE_INFRA", "ORIGINE", "EXTREMITE",
        "TYPE_CABLE", "DIAMETRE", "MODE_POSE", "CAPACITE", "MODULO", "FABRIQUANT",
        "REF_PRODUIT", "TYPE_FIBRE", "NB_FIBRE_UTIL", "NB_FIBRE_DISP", "STATUT", "PROPRIETAIRE",
        "GESTIONNAIRE", "TYPE_PROP", "LONGUEUR",
    ],
    "PTECH": [
        "CODE", "REF_PLAQUE", "TYPE", "NATURE", "HAUTEUR_APPUI", "TYPE_APPUI", "EFFORT_APPUI",
        "NB_BOITIERS", "STATUT", "PROPRIETAIRE", "GESTIONNAIRE", "ADRESSSE", "VILLE",
        "CODE_POSTAL", "X", "Y",
    ],
    "INFRASTRUCTURE": [
        "CODE", "REF_PLAQUE", "ORIGINE", "EXTREMITE", "COMPOSITION", "TYPE", "TYPE_LOG",
        "STATUT", "PROPRIETAIRE", "GESTIONNAIRE", "LONGUEUR",
    ],
    "ZNRO": ["CODE", "REF_PLAQUE", "REF_NRO", "STATUT", "NB_PRISES"],
    "ZPM": ["CODE", "REF_PLAQUE", "REF_NRO", "REF_PM", "STATUT", "NB_PRISES"],
}

SHAPE_TYPES = {1: "Point", 3: "LineString", 5: "Polygon", 11: "Point", 13: "LineString", 15: "Polygon"}
EMPTY = {None, ""}
EPS = 1e-10


@dataclass
class Layer:
    name: str
    geometry_type: str
    fields: list[str]
    rows: list[dict[str, Any]]
    geometries: list[Any]
    crs: str = ""
    source: str = ""


def _field_candidates(logical_name: str) -> list[str]:
    return [logical_name, logical_name[:10]]


def _resolve_field(layer: Layer, logical_name: str) -> str | None:
    available = {name.upper(): name for name in layer.fields}
    for alternative in logical_name.split("|"):
        for candidate in _field_candidates(alternative):
            if candidate.upper() in available:
                return available[candidate.upper()]
    return None


def _value(layer: Layer, row: dict[str, Any], logical_name: str) -> Any:
    field = _resolve_field(layer, logical_name)
    return row.get(field) if field else None


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _number(value: Any) -> float | None:
    if value in EMPTY:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value).replace(",", "."))
    return float(match.group()) if match else None


def _issue(rule: str, message: str, *, layer: str | None = None, code: str | None = None) -> dict[str, Any]:
    return {key: value for key, value in {"rule": rule, "layer": layer, "code": code, "message": message}.items() if value is not None}


def _decode(raw: bytes, encoding: str) -> str:
    for candidate in (encoding, "utf-8", "gb18030", "cp1252"):
        try:
            return raw.decode(candidate).strip().rstrip("\x00")
        except (LookupError, UnicodeDecodeError):
            pass
    return raw.decode("latin1", errors="replace").strip().rstrip("\x00")


def _read_dbf(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    raw = path.read_bytes()
    if len(raw) < 32:
        raise ValueError(f"Invalid DBF header: {path}")
    record_count = struct.unpack_from("<I", raw, 4)[0]
    header_length, record_length = struct.unpack_from("<HH", raw, 8)
    encoding = "utf-8"
    cpg = path.with_suffix(".cpg")
    if cpg.exists():
        encoding = cpg.read_text(encoding="ascii", errors="ignore").strip() or encoding
    specs: list[tuple[str, str, int, int]] = []
    offset = 32
    while offset + 32 <= header_length and raw[offset] != 0x0D:
        name = raw[offset : offset + 11].split(b"\x00", 1)[0].decode("ascii", errors="ignore")
        kind = chr(raw[offset + 11])
        length, decimals = raw[offset + 16], raw[offset + 17]
        specs.append((name, kind, length, decimals))
        offset += 32
    rows: list[dict[str, Any]] = []
    for index in range(record_count):
        start = header_length + index * record_length
        record = raw[start : start + record_length]
        if len(record) < record_length or record[:1] == b"*":
            continue
        row: dict[str, Any] = {}
        cursor = 1
        for name, kind, length, decimals in specs:
            value_raw = record[cursor : cursor + length]
            cursor += length
            value = _decode(value_raw, encoding)
            if kind in {"N", "F"}:
                try:
                    row[name] = float(value) if decimals and value else int(value) if value else None
                except ValueError:
                    row[name] = value
            elif kind == "L":
                row[name] = value.upper() in {"Y", "T"} if value else None
            else:
                row[name] = value
        rows.append(row)
    return [item[0] for item in specs], rows


def _read_shp(path: Path) -> tuple[str, list[Any]]:
    raw = path.read_bytes()
    if len(raw) < 100:
        raise ValueError(f"Invalid shapefile header: {path}")
    header_type = struct.unpack_from("<i", raw, 32)[0]
    geometry_type = SHAPE_TYPES.get(header_type)
    if not geometry_type:
        raise ValueError(f"Unsupported shapefile type {header_type}: {path}")
    geometries: list[Any] = []
    offset = 100
    while offset + 8 <= len(raw):
        _, words = struct.unpack_from(">ii", raw, offset)
        content = raw[offset + 8 : offset + 8 + words * 2]
        offset += 8 + words * 2
        if len(content) < 4:
            continue
        shape_type = struct.unpack_from("<i", content, 0)[0]
        if shape_type == 0:
            geometries.append(None)
        elif shape_type in {1, 11, 21}:
            geometries.append(struct.unpack_from("<dd", content, 4))
        elif shape_type in {3, 5, 13, 15, 23, 25}:
            part_count, point_count = struct.unpack_from("<ii", content, 36)
            parts = list(struct.unpack_from(f"<{part_count}i", content, 44)) if part_count else []
            points_offset = 44 + part_count * 4
            points = [struct.unpack_from("<dd", content, points_offset + index * 16) for index in range(point_count)]
            boundaries = [*parts, point_count]
            geometries.append([points[boundaries[index] : boundaries[index + 1]] for index in range(part_count)])
        else:
            raise ValueError(f"Unsupported record shape type {shape_type}: {path}")
    return geometry_type, geometries


def _find_layer(shape_dir: Path, layer_name: str) -> Path:
    matches = [path for path in shape_dir.glob("*.shp") if path.stem.upper().endswith(layer_name)]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one *{layer_name}.shp, found {len(matches)}")
    return matches[0]


def load_shape_directory(shape_dir: str | Path) -> dict[str, Layer]:
    root = Path(shape_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Shape directory not found: {root}")
    layers: dict[str, Layer] = {}
    for name in LAYER_TYPES:
        shp = _find_layer(root, name)
        dbf = shp.with_suffix(".dbf")
        prj = shp.with_suffix(".prj")
        if not dbf.exists() or not prj.exists():
            raise FileNotFoundError(f"{shp.name} requires matching .dbf and .prj files")
        geometry_type, geometries = _read_shp(shp)
        fields, rows = _read_dbf(dbf)
        if len(rows) != len(geometries):
            raise ValueError(f"Geometry/attribute count mismatch for {shp.name}: {len(geometries)} != {len(rows)}")
        layers[name] = Layer(name, geometry_type, fields, rows, geometries, prj.read_text(encoding="utf-8", errors="ignore"), str(shp))
    return layers


def _point_location(point: tuple[float, float], ring: list[tuple[float, float]]) -> int:
    """Return 1 inside, 0 on boundary, -1 outside."""
    x, y = point
    inside = False
    for a, b in zip(ring, [*ring[1:], ring[0]]):
        cross = (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0])
        if abs(cross) <= EPS and min(a[0], b[0]) - EPS <= x <= max(a[0], b[0]) + EPS and min(a[1], b[1]) - EPS <= y <= max(a[1], b[1]) + EPS:
            return 0
        if (a[1] > y) != (b[1] > y):
            crossing = (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]
            if x < crossing:
                inside = not inside
    return 1 if inside else -1


def _inside_geometry(point: tuple[float, float], geometry: Any) -> bool:
    if not geometry:
        return False
    locations = [_point_location(point, ring) for ring in geometry]
    if 0 in locations:
        return True
    # Even/odd containment handles shapefile polygon holes without relying on
    # writer-specific clockwise/counter-clockwise ring orientation.
    return sum(location == 1 for location in locations) % 2 == 1


def _crs_key(value: str) -> str:
    normalized = re.sub(r"\s+", "", value).upper()
    authority = re.search(r'AUTHORITY\["EPSG","(\d+)"\]', normalized)
    if authority:
        return f"EPSG:{authority.group(1)}"
    if "WGS_1984" in normalized or "WGS84" in normalized:
        return "EPSG:4326"
    return normalized


def _orientation(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _proper_cross(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], d: tuple[float, float]) -> bool:
    first, second = _orientation(a, b, c), _orientation(a, b, d)
    third, fourth = _orientation(c, d, a), _orientation(c, d, b)
    return ((first > EPS and second < -EPS) or (first < -EPS and second > EPS)) and ((third > EPS and fourth < -EPS) or (third < -EPS and fourth > EPS))


def _polygons_overlap(first: Any, second: Any) -> bool:
    if not first or not second:
        return False
    for ring_a in first:
        for ring_b in second:
            if any(_point_location(point, ring_b) == 1 for point in ring_a[:-1] or ring_a):
                return True
            if any(_point_location(point, ring_a) == 1 for point in ring_b[:-1] or ring_b):
                return True
            midpoints_a = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(ring_a, [*ring_a[1:], ring_a[0]])]
            midpoints_b = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(ring_b, [*ring_b[1:], ring_b[0]])]
            if any(_point_location(point, ring_b) == 1 for point in midpoints_a):
                return True
            if any(_point_location(point, ring_a) == 1 for point in midpoints_b):
                return True
            for a, b in zip(ring_a, [*ring_a[1:], ring_a[0]]):
                for c, d in zip(ring_b, [*ring_b[1:], ring_b[0]]):
                    if _proper_cross(a, b, c, d):
                        return True
            centre = (sum(p[0] for p in ring_a) / len(ring_a), sum(p[1] for p in ring_a) / len(ring_a))
            if _point_location(centre, ring_b) == 1:
                return True
    return False


def _utm_distance(first: tuple[float, float], second: tuple[float, float]) -> float:
    """WGS84/local-UTM distance; sufficient for endpoint-coincidence checks."""
    lon0 = (first[0] + second[0]) / 2.0
    lat0 = (first[1] + second[1]) / 2.0
    zone = max(1, min(60, int((lon0 + 180.0) // 6.0) + 1))
    meridian = math.radians((zone - 1) * 6 - 177)
    earth_a, e2, k0 = 6378137.0, 0.0066943799901413165, 0.9996

    def project(point: tuple[float, float]) -> tuple[float, float]:
        lon, lat = map(math.radians, point)
        ep2 = e2 / (1.0 - e2)
        n = earth_a / math.sqrt(1.0 - e2 * math.sin(lat) ** 2)
        t, c = math.tan(lat) ** 2, ep2 * math.cos(lat) ** 2
        aa = math.cos(lat) * (lon - meridian)
        m = earth_a * ((1-e2/4-3*e2**2/64-5*e2**3/256)*lat-(3*e2/8+3*e2**2/32+45*e2**3/1024)*math.sin(2*lat)+(15*e2**2/256+45*e2**3/1024)*math.sin(4*lat)-(35*e2**3/3072)*math.sin(6*lat))
        x = k0*n*(aa+(1-t+c)*aa**3/6+(5-18*t+t**2+72*c-58*ep2)*aa**5/120)+500000
        y = k0*(m+n*math.tan(lat)*(aa**2/2+(5-t+9*c+4*c**2)*aa**4/24+(61-58*t+t**2+600*c-330*ep2)*aa**6/720))
        if lat0 < 0:
            y += 10000000
        return x, y

    a, b = project(first), project(second)
    return math.hypot(a[0] - b[0], a[1] - b[1])


def validate_layers(layers: dict[str, Layer], *, project_crs: str | None = None, endpoint_tolerance_m: float = 0.20) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    for name, expected_type in LAYER_TYPES.items():
        layer = layers.get(name)
        if not layer:
            errors.append(_issue("LAYER_SET", f"Missing required layer {name}", layer=name))
            continue
        counts[name] = len(layer.rows)
        if layer.geometry_type != expected_type:
            errors.append(_issue("GEOMETRY_TYPE", f"Expected {expected_type}, got {layer.geometry_type}", layer=name))
        if not layer.rows:
            errors.append(_issue("NON_EMPTY", "Layer must contain at least one feature", layer=name))
        for required in REQUIRED_FIELDS[name]:
            field = _resolve_field(layer, required)
            if not field:
                errors.append(_issue("REQUIRED_FIELD", f"Missing field {required}; 10-character shapefile alias is accepted", layer=name))
                continue
            blanks = [index + 1 for index, row in enumerate(layer.rows) if row.get(field) in EMPTY or _text(row.get(field)) == ""]
            if blanks:
                errors.append(_issue("REQUIRED_VALUE", f"Field {field} is blank in rows {blanks[:10]}", layer=name))
        if name == "IMB" and not _resolve_field(layer, "CODE_VOIE") and _resolve_field(layer, "NOM_VOIE"):
            warnings.append(_issue("WORKBOOK_AMBIGUITY", "Validation sheet asks for CODE_VOIE, while the IMB schema sheet defines NOM_VOIE; NOM_VOIE was accepted", layer=name))
        code_field = _resolve_field(layer, "CODE")
        if code_field:
            values = [_text(row.get(code_field)) for row in layer.rows]
            duplicates = sorted({value for value in values if value and values.count(value) > 1})
            if duplicates:
                errors.append(_issue("UNIQUE_CODE", f"Duplicate CODE values: {duplicates[:10]}", layer=name))

    crs_values = {_crs_key(layer.crs) for layer in layers.values() if layer.crs.strip()}
    if len(crs_values) != 1:
        errors.append(_issue("CRS一致", f"Layer .prj definitions are not identical ({len(crs_values)} variants)"))
    if project_crs and crs_values and _crs_key(project_crs) not in crs_values:
        errors.append(_issue("PROJECT_CRS", f"Layer CRS {sorted(crs_values)} does not match requested {_crs_key(project_crs)}"))
    if errors and any(issue["rule"] == "LAYER_SET" for issue in errors):
        return {"status": "failed", "contract_version": "ftth-excel-2026-09-v1", "counts": counts, "errors": errors, "warnings": warnings}

    def by_code(name: str) -> dict[str, tuple[dict[str, Any], Any]]:
        layer = layers[name]
        return {_text(_value(layer, row, "CODE")): (row, geometry) for row, geometry in zip(layer.rows, layer.geometries)}

    site, boite, cable, zpm = layers["SITE"], layers["BOITE"], layers["CABLE"], layers["ZPM"]
    site_pm = {_text(_value(site, row, "CODE")): (row, geometry) for row, geometry in zip(site.rows, site.geometries) if _text(_value(site, row, "TYPE")).upper() == "PM"}
    zpm_by_code = by_code("ZPM")
    if set(site_pm) != set(zpm_by_code):
        errors.append(_issue("SITE_ZPM_BIDIRECTIONAL", f"SITE(TYPE=PM) only={sorted(set(site_pm)-set(zpm_by_code))[:10]}, ZPM only={sorted(set(zpm_by_code)-set(site_pm))[:10]}"))

    boxes = [(row, geometry) for row, geometry in zip(boite.rows, boite.geometries) if _text(_value(boite, row, "TYPE")).upper() in {"PBO", "BPE"}]
    pbos = [(row, geometry) for row, geometry in boxes if _text(_value(boite, row, "TYPE")).upper() == "PBO"]
    distributions = [(row, geometry) for row, geometry in zip(cable.rows, cable.geometries) if _text(_value(cable, row, "TYPE_CABLE")).upper() == "DISTRIBUTION"]
    for row, _ in pbos:
        code, ref_pm = _text(_value(boite, row, "CODE")), _text(_value(boite, row, "REF_PM"))
        if ref_pm not in site_pm:
            errors.append(_issue("PBO_REF_PM", f"REF_PM {ref_pm!r} does not reference SITE(TYPE=PM)", layer="BOITE", code=code))
    for row, _ in distributions:
        code, ref_pm = _text(_value(cable, row, "CODE")), _text(_value(cable, row, "REF_PM"))
        if ref_pm not in site_pm:
            errors.append(_issue("CABLE_REF_PM", f"REF_PM {ref_pm!r} does not reference SITE(TYPE=PM)", layer="CABLE", code=code))
    for pm_code in site_pm:
        if not any(_text(_value(boite, row, "REF_PM")) == pm_code for row, _ in pbos):
            errors.append(_issue("PM_PBO_INVERSE", "PM is not referenced by any PBO", layer="SITE", code=pm_code))
        if not any(_text(_value(cable, row, "REF_PM")) == pm_code for row, _ in distributions):
            errors.append(_issue("PM_CABLE_INVERSE", "PM is not referenced by any distribution cable", layer="SITE", code=pm_code))

    endpoint_entities = {**{code: value for code, value in by_code("BOITE").items() if _text(_value(boite, value[0], "TYPE")).upper() in {"PBO", "BPE"}}, **site_pm}
    used_endpoints: set[str] = set()
    for row, geometry in distributions:
        code = _text(_value(cable, row, "CODE"))
        origin, end = _text(_value(cable, row, "ORIGINE")), _text(_value(cable, row, "EXTREMITE"))
        if origin == end:
            errors.append(_issue("CABLE_DISTINCT_ENDPOINTS", "ORIGINE and EXTREMITE must differ", layer="CABLE", code=code))
        for endpoint in (origin, end):
            if endpoint not in endpoint_entities:
                errors.append(_issue("CABLE_ENDPOINT_REFERENCE", f"Endpoint {endpoint!r} is not a PM/PBO/BPE", layer="CABLE", code=code))
            else:
                used_endpoints.add(endpoint)
        vertices = [point for part in (geometry or []) for point in part]
        if len(vertices) >= 2:
            line_ends = (vertices[0], vertices[-1])
            for endpoint in (origin, end):
                if endpoint in endpoint_entities:
                    point = endpoint_entities[endpoint][1]
                    if not isinstance(point, tuple) or min(_utm_distance(point, candidate) for candidate in line_ends) > endpoint_tolerance_m:
                        errors.append(_issue("CABLE_ENDPOINT_GEOMETRY", f"Referenced endpoint {endpoint} does not coincide with either line endpoint within {endpoint_tolerance_m} m", layer="CABLE", code=code))
        ref_pm = _text(_value(cable, row, "REF_PM"))
        if ref_pm in zpm_by_code:
            polygon = zpm_by_code[ref_pm][1]
            if any(not _inside_geometry(point, polygon) for point in vertices):
                warnings.append(_issue(
                    "CABLE_WITHIN_ZPM_REVIEW",
                    f"At least one cable vertex lies outside ZPM {ref_pm}; updated planning contract requires review but does not block a complete package",
                    layer="CABLE", code=code,
                ))
    for endpoint in endpoint_entities:
        if endpoint not in used_endpoints:
            errors.append(_issue("CABLE_ENDPOINT_INVERSE", "PM/PBO/BPE is not used by any distribution cable endpoint", code=endpoint))

    for polygon_layer_name in ("ZNRO", "ZPM"):
        layer = layers[polygon_layer_name]
        for first in range(len(layer.geometries)):
            for second in range(first + 1, len(layer.geometries)):
                if _polygons_overlap(layer.geometries[first], layer.geometries[second]):
                    errors.append(_issue("POLYGON_NON_OVERLAP", f"Features {first + 1} and {second + 1} overlap; boundary touching is allowed", layer=polygon_layer_name))
    for code, (_, point) in site_pm.items():
        if code in zpm_by_code and not _inside_geometry(point, zpm_by_code[code][1]):
            errors.append(_issue("PM_WITHIN_ZPM", "SITE PM point lies outside its same-CODE ZPM", layer="SITE", code=code))
    for row, point in pbos:
        code, ref_pm = _text(_value(boite, row, "CODE")), _text(_value(boite, row, "REF_PM"))
        if ref_pm in zpm_by_code and not _inside_geometry(point, zpm_by_code[ref_pm][1]):
            warnings.append(_issue(
                "PBO_WITHIN_ZPM_REVIEW",
                f"PBO lies outside ZPM {ref_pm}; updated planning contract requires review but does not block a complete package",
                layer="BOITE", code=code,
            ))

    for row, _ in pbos:
        code = _text(_value(boite, row, "CODE"))
        used, capacity = _number(_value(boite, row, "NB_FIBRE_UTIL")), _number(_value(boite, row, "CAPACITE"))
        if used is not None and capacity is not None and used > capacity + EPS:
            errors.append(_issue("PBO_CAPACITY", f"NB_FIBRE_UTIL {used:g} exceeds CAPACITE {capacity:g}", layer="BOITE", code=code))
    for pm_code in site_pm:
        pbo_used = sum(_number(_value(boite, row, "NB_FIBRE_UTIL")) or 0 for row, _ in pbos if _text(_value(boite, row, "REF_PM")) == pm_code)
        cable_capacity = sum(_number(_value(cable, row, "CAPACITE")) or 0 for row, _ in distributions if _text(_value(cable, row, "ORIGINE")) == pm_code)
        if pbo_used > cable_capacity + EPS:
            errors.append(_issue("PM_DISTRIBUTION_USED_CAPACITY", f"PBO used-fiber sum {pbo_used:g} exceeds outgoing distribution cable capacity {cable_capacity:g}", layer="SITE", code=pm_code))

    return {
        "status": "passed" if not errors else "failed",
        "contract_version": "ftth-excel-2026-09-v1",
        "coordinate_order": "[lon, lat]",
        "counts": counts,
        "errors": errors,
        "warnings": warnings,
    }


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def run(shape_dir: str | Path, *, project_crs: str | None = None, output: str | Path | None = None) -> dict[str, Any]:
    try:
        report = validate_layers(load_shape_directory(shape_dir), project_crs=project_crs)
    except Exception as exc:
        report = {"status": "failed", "contract_version": "ftth-excel-2026-09-v1", "errors": [_issue("INPUT", f"{type(exc).__name__}: {exc}")], "warnings": []}
    if output:
        _atomic_json(Path(output).resolve(), report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the 8-layer FTTH shapefile delivery contract")
    parser.add_argument("shape_dir", type=Path, help="Directory containing exactly one shapefile for each required layer")
    parser.add_argument("--project-crs", default="EPSG:4326")
    parser.add_argument("--output", type=Path, default=Path("delivery_validation_report.json"))
    args = parser.parse_args()
    report = run(args.shape_dir, project_crs=args.project_crs, output=args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
