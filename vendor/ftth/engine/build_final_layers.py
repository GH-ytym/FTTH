"""Build the eight field-complete FTTH delivery layers.

The task algorithms intentionally keep their richer internal JSON/CSV contracts.
This module is the delivery adapter: it combines the final task outputs into the
eight Shapefiles required by the field specification workbook and validates the
field and cross-layer contracts before reporting success.
"""

from __future__ import annotations

import csv
import argparse
import json
import math
import struct
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent
from project_paths import (
    FINAL_LAYER_OUTPUT_DIR,
    TASK2_ZONE_PARTITION_OUTPUT_DIR,
    TASK4_FINAL_INPUT_DIR,
    TASK4_PBO_BPE_OUTPUT_DIR,
    TASK5_PRODUCTION_OUTPUT_DIR,
    TASK6_PRODUCTION_OUTPUT_DIR,
)

OUTPUT_DIR = FINAL_LAYER_OUTPUT_DIR
TASK2_REGIONS = TASK2_ZONE_PARTITION_OUTPUT_DIR / "qgis_direct_region_partition_result.json"
TASK4_INPUT = TASK4_FINAL_INPUT_DIR
TASK4_OUTPUT = TASK4_PBO_BPE_OUTPUT_DIR
TASK5_PLAN = TASK5_PRODUCTION_OUTPUT_DIR / "fiber_link_plan.json"
TASK6_PLAN = TASK6_PRODUCTION_OUTPUT_DIR / "layered_plan.json"

WGS84_PRJ = (
    'GEOGCS["WGS 84",DATUM["WGS_1984",'
    'SPHEROID["WGS 84",6378137,298.257223563]],'
    'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433],'
    'AUTHORITY["EPSG","4326"]]'
)

# DBF names are limited to 10 characters.  Each alias below is stable and the
# first ten characters are used where that does not create a collision.
FIELD_NAMES: dict[str, dict[str, str]] = {
    "IMB": {
        "CODE_POSTAL": "CODE_POSTA", "NUMERO_VOIE": "NUMERO_VOI",
        "COMPLEMENT_NUM": "COMPLE_NUM", "COMPLEMENT_VOIE": "COMPLE_VOI",
        "NOM_BATIMENT": "NOM_BATIME", "TYPE_BATIMENT": "TYPE_BATIM",
        "TYPE_CLIENT": "TYPE_CLIEN", "RACCORDEMENT": "RACCORDEME",
        "NUM_GESTIONNAIRE": "NUM_GESTIO", "COL_MONTANTE": "COL_MONTAN",
        "SOUS_SOL_COMMUN": "SOUS_SOL_C",
    },
    "BOITE": {
        "TYPE_STRUCTURE": "TYPE_STRUC", "NB_LOGEMENT": "NB_LOGEMEN",
        "NB_FIBRE_UTIL": "NB_FIBRE_U", "NB_CASSETTES_MAX": "NB_CASSETT",
        "CABLE_AMONT": "CABLE_AMON", "PROPRIETAIRE": "PROPRIETAI",
        "GESTIONNAIRE": "GESTIONNAI", "CODE_POSTAL": "CODE_POSTA",
    },
    "CABLE": {
        "CODE_INFRA_IDS": "INFRA_IDS",
        "NB_FIBRE_UTIL": "NB_FIBRE_U", "NB_FIBRE_DISP": "NB_FIBRE_D",
        "PROPRIETAIRE": "PROPRIETAI", "GESTIONNAIRE": "GESTIONNAI",
    },
    "INFRASTRUCTURE": {
        "PROPRIETAIRE": "PROPRIETAI", "GESTIONNAIRE": "GESTIONNAI",
    },
    "PTECH": {
        "HAUTEUR_APPUI": "HAUTEUR_AP", "EFFORT_APPUI": "EFFORT_APP",
        "NB_BOITIERS": "NB_BOITIER", "PROPRIETAIRE": "PROPRIETAI",
        "GESTIONNAIRE": "GESTIONNAI", "CODE_POSTAL": "CODE_POSTA",
    },
    "SITE": {
        "REF_PRODUIT": "REF_PRODUI", "PROPRIETAIRE": "PROPRIETAI",
        "GESTIONNAIRE": "GESTIONNAI", "CODE_POSTAL": "CODE_POSTA",
    },
}


def field(layer: str, long_name: str) -> str:
    return FIELD_NAMES.get(layer, {}).get(long_name, long_name[:10])


def schema(layer: str, rows: Iterable[tuple[str, str, int, int]]) -> list[tuple[str, str, int, int]]:
    result = [(field(layer, name), kind, width, decimals) for name, kind, width, decimals in rows]
    names = [item[0] for item in result]
    if len(names) != len(set(names)):
        raise ValueError(f"Duplicate DBF field alias in {layer}: {names}")
    return result


SCHEMAS = {
    "IMB": schema("IMB", [
        ("CODE", "C", 30, 0), ("REF_PLAQUE", "C", 50, 0),
        ("REGION", "C", 100, 0), ("PROVINCE", "C", 100, 0),
        ("VILLE", "C", 100, 0), ("COMMUNE", "C", 100, 0),
        ("QUARTIER", "C", 100, 0), ("CODE_POSTAL", "N", 5, 0),
        ("NUMERO_VOIE", "N", 10, 0), ("COMPLEMENT_NUM", "C", 10, 0),
        ("TYPE_VOIE", "C", 10, 0), ("NOM_VOIE", "C", 100, 0),
        ("COMPLEMENT_VOIE", "C", 100, 0), ("CODE_VOIE", "C", 50, 0),
        ("NOM_BATIMENT", "C", 100, 0), ("TYPE_BATIMENT", "C", 50, 0),
        ("TYPE_CLIENT", "C", 50, 0), ("NB_LOC_RES", "N", 10, 0),
        ("NB_LOC_PRO", "N", 10, 0), ("NB_LOC_TOT", "N", 10, 0),
        ("RACCORDEMENT", "C", 50, 0), ("STATUT", "C", 50, 0),
        ("GESTION", "C", 50, 0), ("NUM_GESTIONNAIRE", "C", 23, 0),
        ("NB_ETAGE", "N", 2, 0), ("COL_MONTANTE", "C", 3, 0),
        ("SOUS_SOL", "C", 3, 0), ("SOUS_SOL_COMMUN", "C", 3, 0),
        ("BPE_CODE", "C", 20, 0), ("X", "F", 18, 8),
        ("Y", "F", 18, 8), ("COMMENT", "C", 50, 0),
    ]),
    "BOITE": schema("BOITE", [
        ("CODE", "C", 30, 0), ("CODE_PTC", "C", 30, 0),
        ("REF_PLAQUE", "C", 50, 0), ("REF_NRO", "C", 50, 0),
        ("REF_PM", "C", 50, 0), ("TYPE", "C", 15, 0),
        ("TYPE_STRUCTURE", "C", 30, 0), ("MODE_POSE", "C", 30, 0),
        ("CAPACITE", "N", 3, 0), ("NB_LOGEMENT", "N", 10, 0),
        ("NB_SPLICES", "N", 10, 0), ("NB_FIBRE_UTIL", "N", 3, 0),
        ("FABRIQUANT", "C", 50, 0), ("REF_BPE", "C", 50, 0),
        ("NB_CASSETTES_MAX", "N", 3, 0), ("CABLE_AMONT", "C", 20, 0),
        ("STATUT", "C", 50, 0), ("PROPRIETAIRE", "C", 50, 0),
        ("GESTIONNAIRE", "C", 50, 0), ("ADRESSSE", "C", 50, 0),
        ("VILLE", "C", 50, 0), ("CODE_POSTAL", "N", 5, 0),
        ("X", "F", 18, 8), ("Y", "F", 18, 8), ("COMMENT", "C", 50, 0),
    ]),
    "CABLE": schema("CABLE", [
        ("CODE", "C", 30, 0), ("NOM", "C", 30, 0),
        ("REF_PLAQUE", "C", 50, 0), ("REF_NRO", "C", 50, 0),
        ("REF_PM", "C", 50, 0), ("CODE_INFRA", "C", 20, 0),
        ("CODE_INFRA_IDS", "C", 254, 0),
        ("ORIGINE", "C", 20, 0), ("EXTREMITE", "C", 20, 0),
        ("TYPE_CABLE", "C", 15, 0), ("DIAMETRE", "N", 2, 0),
        ("MODE_POSE", "C", 30, 0), ("CAPACITE", "N", 3, 0),
        ("MODULO", "N", 2, 0), ("FABRIQUANT", "C", 50, 0),
        ("REF_PRODUIT", "C", 50, 0), ("TYPE_FIBRE", "C", 10, 0),
        ("NB_FIBRE_UTIL", "N", 3, 0), ("NB_FIBRE_DISP", "N", 3, 0),
        ("STATUT", "C", 50, 0), ("PROPRIETAIRE", "C", 50, 0),
        ("GESTIONNAIRE", "C", 50, 0), ("TYPE_PROP", "C", 30, 0),
        ("LONGUEUR", "F", 10, 2), ("COMMENT", "C", 50, 0),
    ]),
    "INFRASTRUCTURE": schema("INFRASTRUCTURE", [
        ("CODE", "C", 30, 0), ("NOM", "C", 30, 0),
        ("REF_PLAQUE", "C", 50, 0), ("ORIGINE", "C", 50, 0),
        ("EXTREMITE", "C", 50, 0), ("COMPOSITION", "C", 50, 0),
        ("TYPE", "C", 50, 0), ("TYPE_LOG", "C", 50, 0),
        ("STATUT", "C", 50, 0), ("PROPRIETAIRE", "C", 50, 0),
        ("GESTIONNAIRE", "C", 50, 0), ("LONGUEUR", "F", 10, 2),
        ("COMMENT", "C", 50, 0),
    ]),
    "PTECH": schema("PTECH", [
        ("CODE", "C", 30, 0), ("NOM", "C", 50, 0),
        ("REF_PLAQUE", "C", 50, 0), ("TYPE", "C", 30, 0),
        ("NATURE", "C", 30, 0), ("HAUTEUR_APPUI", "F", 10, 2),
        ("TYPE_APPUI", "C", 10, 0), ("EFFORT_APPUI", "F", 10, 2),
        ("NB_BOITIERS", "N", 3, 0), ("STATUT", "C", 30, 0),
        ("PROPRIETAIRE", "C", 50, 0), ("GESTIONNAIRE", "C", 50, 0),
        ("ADRESSSE", "C", 50, 0), ("VILLE", "C", 50, 0),
        ("CODE_POSTAL", "N", 5, 0), ("X", "F", 18, 8),
        ("Y", "F", 18, 8), ("COMMENT", "C", 50, 0),
    ]),
    "SITE": schema("SITE", [
        ("CODE", "C", 20, 0), ("REF_PLAQUE", "C", 50, 0),
        ("REF_NRO", "C", 50, 0), ("TYPE", "C", 50, 0),
        ("FABRIQUANT", "C", 50, 0), ("REF_PRODUIT", "C", 50, 0),
        ("MODE_POSE", "C", 50, 0), ("STATUT", "C", 30, 0),
        ("PROPRIETAIRE", "C", 50, 0), ("GESTIONNAIRE", "C", 50, 0),
        ("ADRESSSE", "C", 50, 0), ("COMMUNE", "C", 50, 0),
        ("CODE_POSTAL", "N", 5, 0), ("X", "F", 18, 8),
        ("Y", "F", 18, 8), ("COMMENT", "C", 50, 0),
    ]),
    "ZNRO": schema("ZNRO", [
        ("CODE", "C", 30, 0), ("REF_PLAQUE", "C", 50, 0),
        ("REF_NRO", "C", 30, 0), ("STATUT", "C", 50, 0),
        ("NB_PRISES", "N", 10, 0), ("COMMENT", "C", 50, 0),
    ]),
    "ZPM": schema("ZPM", [
        ("CODE", "C", 30, 0), ("REF_PLAQUE", "C", 50, 0),
        ("REF_NRO", "C", 30, 0), ("REF_SRO", "C", 30, 0),
        ("REF_PM", "C", 30, 0), ("STATUT", "C", 50, 0),
        ("NB_PRISES", "N", 10, 0), ("COMMENT", "C", 50, 0),
    ]),
}


REQUIRED_FIELDS = {
    "IMB": ["CODE", "REF_PLAQUE", "REGION", "PROVINCE", "VILLE", "COMMUNE", "CODE_POSTA", "NUMERO_VOI", "TYPE_VOIE", "CODE_VOIE", "TYPE_BATIM", "TYPE_CLIEN", "NB_LOC_RES", "NB_LOC_PRO", "NB_LOC_TOT", "RACCORDEME", "STATUT", "NB_ETAGE", "COL_MONTAN", "SOUS_SOL", "SOUS_SOL_C", "BPE_CODE", "X", "Y"],
    "BOITE": ["CODE", "CODE_PTC", "REF_PLAQUE", "REF_NRO", "REF_PM", "TYPE", "TYPE_STRUC", "MODE_POSE", "CAPACITE", "NB_LOGEMEN", "NB_SPLICES", "NB_FIBRE_U", "FABRIQUANT", "REF_BPE", "NB_CASSETT", "CABLE_AMON", "STATUT", "PROPRIETAI", "GESTIONNAI", "ADRESSSE", "VILLE", "CODE_POSTA", "X", "Y"],
    "CABLE": ["CODE", "REF_PLAQUE", "REF_NRO", "REF_PM", "CODE_INFRA", "INFRA_IDS", "ORIGINE", "EXTREMITE", "TYPE_CABLE", "DIAMETRE", "MODE_POSE", "CAPACITE", "MODULO", "FABRIQUANT", "REF_PRODUI", "TYPE_FIBRE", "NB_FIBRE_U", "NB_FIBRE_D", "STATUT", "PROPRIETAI", "GESTIONNAI", "TYPE_PROP", "LONGUEUR"],
    "PTECH": ["CODE", "REF_PLAQUE", "TYPE", "NATURE", "HAUTEUR_AP", "TYPE_APPUI", "EFFORT_APP", "NB_BOITIER", "STATUT", "PROPRIETAI", "GESTIONNAI", "ADRESSSE", "VILLE", "CODE_POSTA", "X", "Y"],
    "INFRASTRUCTURE": ["CODE", "REF_PLAQUE", "ORIGINE", "EXTREMITE", "COMPOSITIO", "TYPE", "TYPE_LOG", "STATUT", "PROPRIETAI", "GESTIONNAI", "LONGUEUR"],
    "ZPM": ["CODE", "REF_PLAQUE", "REF_NRO", "REF_PM", "STATUT", "NB_PRISES"],
    "ZNRO": ["CODE", "REF_PLAQUE", "REF_NRO", "STATUT", "NB_PRISES"],
    "SITE": ["CODE", "REF_PLAQUE", "REF_NRO", "TYPE", "FABRIQUANT", "REF_PRODUI", "MODE_POSE", "STATUT", "PROPRIETAI", "GESTIONNAI", "ADRESSSE", "COMMUNE", "CODE_POSTA", "X", "Y"],
}


def text(value: Any, fallback: str = "NR") -> str:
    value = "" if value is None else str(value).strip()
    return value or fallback


def integer(value: Any, fallback: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return fallback


def number(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def read_dbf(path: Path) -> tuple[list[tuple[str, str, int, int]], list[dict[str, Any]]]:
    data = path.read_bytes()
    record_count = struct.unpack("<I", data[4:8])[0]
    header_size = struct.unpack("<H", data[8:10])[0]
    record_size = struct.unpack("<H", data[10:12])[0]
    fields: list[tuple[str, str, int, int]] = []
    offset = 32
    while offset < header_size - 1:
        raw = data[offset : offset + 32]
        fields.append((raw[:11].split(b"\0", 1)[0].decode("ascii"), chr(raw[11]), raw[16], raw[17]))
        offset += 32
    rows: list[dict[str, Any]] = []
    for index in range(record_count):
        raw = data[header_size + index * record_size : header_size + (index + 1) * record_size]
        if not raw or raw[:1] == b"*":
            continue
        cursor = 1
        row: dict[str, Any] = {}
        for name, kind, width, decimals in fields:
            value = raw[cursor : cursor + width].decode("latin1").strip()
            cursor += width
            if kind in {"N", "F"}:
                row[name] = float(value) if value and decimals else (int(value) if value else None)
            elif kind == "L":
                row[name] = value.upper() in {"T", "Y"}
            else:
                row[name] = value or None
        rows.append(row)
    return fields, rows


def read_points(path: Path) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    with path.open("rb") as handle:
        handle.read(100)
        while True:
            record_header = handle.read(8)
            if not record_header:
                break
            content = handle.read(struct.unpack(">I", record_header[4:8])[0] * 2)
            if struct.unpack("<I", content[:4])[0] == 1:
                points.append(struct.unpack("<dd", content[4:20]))
    return points


def point_layer(base: Path) -> list[tuple[dict[str, Any], tuple[float, float]]]:
    _, rows = read_dbf(base.with_suffix(".dbf"))
    points = read_points(base.with_suffix(".shp"))
    if len(rows) != len(points):
        raise ValueError(f"Geometry/attribute count mismatch: {base}")
    return list(zip(rows, points))


def encode_dbf(value: Any, kind: str, width: int, decimals: int) -> bytes:
    if value is None:
        return b" " * width
    if kind in {"N", "F"}:
        rendered = f"{float(value):.{decimals}f}" if decimals else str(int(round(float(value))))
        if len(rendered) > width:
            raise ValueError(f"Numeric DBF value does not fit width {width}: {rendered}")
        return rendered.rjust(width).encode("ascii")
    if kind == "L":
        return (b"T" if value else b"F").ljust(width)
    raw = str(value).encode("utf-8")
    if len(raw) > width:
        raw = raw[:width]
    while raw:
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            raw = raw[:-1]
            continue
        break
    return raw.ljust(width, b" ")


def write_dbf(path: Path, fields: list[tuple[str, str, int, int]], rows: list[dict[str, Any]]) -> None:
    header_size = 32 + len(fields) * 32 + 1
    record_size = 1 + sum(item[2] for item in fields)
    today = date.today()
    header = bytearray(32)
    header[0] = 3
    header[1:4] = bytes((today.year - 1900, today.month, today.day))
    header[4:8] = struct.pack("<I", len(rows))
    header[8:10] = struct.pack("<H", header_size)
    header[10:12] = struct.pack("<H", record_size)
    with path.open("wb") as handle:
        handle.write(header)
        for name, kind, width, decimals in fields:
            descriptor = bytearray(32)
            descriptor[:11] = name.encode("ascii")[:11].ljust(11, b"\0")
            descriptor[11] = ord(kind)
            descriptor[16] = width
            descriptor[17] = decimals
            handle.write(descriptor)
        handle.write(b"\r")
        for row in rows:
            handle.write(b" ")
            for name, kind, width, decimals in fields:
                handle.write(encode_dbf(row.get(name), kind, width, decimals))
        handle.write(b"\x1a")


def _bbox(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _shp_header(file_length_words: int, shape_type: int, bounds: tuple[float, float, float, float]) -> bytes:
    header = bytearray(100)
    header[:4] = struct.pack(">I", 9994)
    header[24:28] = struct.pack(">I", file_length_words)
    header[28:32] = struct.pack("<I", 1000)
    header[32:36] = struct.pack("<I", shape_type)
    header[36:68] = struct.pack("<dddd", *bounds)
    return bytes(header)


def _shape_content(shape_type: int, parts: list[list[tuple[float, float]]]) -> bytes:
    if shape_type == 1:
        return struct.pack("<Idd", 1, *parts[0][0])
    points = [point for part in parts for point in part]
    offsets: list[int] = []
    cursor = 0
    for part in parts:
        offsets.append(cursor)
        cursor += len(part)
    return (
        struct.pack("<I", shape_type)
        + struct.pack("<dddd", *_bbox(points))
        + struct.pack("<II", len(parts), len(points))
        + struct.pack(f"<{len(offsets)}I", *offsets)
        + b"".join(struct.pack("<dd", *point) for point in points)
    )


def write_shapes(base: Path, shape_type: int, geometries: list[list[list[tuple[float, float]]]]) -> None:
    contents = [_shape_content(shape_type, geometry) for geometry in geometries]
    all_points = [point for geometry in geometries for part in geometry for point in part]
    bounds = _bbox(all_points)
    shp_bytes = 100 + sum(8 + len(content) for content in contents)
    shx_bytes = 100 + len(contents) * 8
    with base.with_suffix(".shp").open("wb") as shp, base.with_suffix(".shx").open("wb") as shx:
        shp.write(_shp_header(shp_bytes // 2, shape_type, bounds))
        shx.write(_shp_header(shx_bytes // 2, shape_type, bounds))
        offset_words = 50
        for index, content in enumerate(contents, 1):
            length_words = len(content) // 2
            shp.write(struct.pack(">II", index, length_words))
            shp.write(content)
            shx.write(struct.pack(">II", offset_words, length_words))
            offset_words += 4 + length_words


def write_layer(name: str, shape_type: int, rows: list[dict[str, Any]], geometries: list[list[list[tuple[float, float]]]]) -> None:
    if not rows or len(rows) != len(geometries):
        raise ValueError(f"{name} must contain matching non-empty records and geometries")
    base = OUTPUT_DIR / name
    write_shapes(base, shape_type, geometries)
    write_dbf(base.with_suffix(".dbf"), SCHEMAS[name], rows)
    base.with_suffix(".prj").write_text(WGS84_PRJ, encoding="ascii")
    base.with_suffix(".cpg").write_text("UTF-8", encoding="ascii")


def polygon_area(ring: list[tuple[float, float]]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1])) / 2.0


def cell_union_boundary(cells: list[dict[str, Any]]) -> list[list[tuple[float, float]]]:
    """Recover continuous union outlines by cancelling shared Voronoi edges."""
    counts: Counter[tuple[tuple[float, float], tuple[float, float]]] = Counter()
    for cell in cells:
        ring = [(round(float(p[0]), 7), round(float(p[1]), 7)) for p in cell["polygon"]]
        if ring[0] == ring[-1]:
            ring.pop()
        for first, second in zip(ring, ring[1:] + ring[:1]):
            if first != second:
                counts[tuple(sorted((first, second)))] += 1
    boundary = [edge for edge, count in counts.items() if count == 1]
    adjacency: dict[tuple[float, float], set[tuple[float, float]]] = defaultdict(set)
    for first, second in boundary:
        adjacency[first].add(second)
        adjacency[second].add(first)
    invalid = [point for point, neighbors in adjacency.items() if len(neighbors) != 2]
    if invalid:
        raise ValueError(f"Cannot create a clean region boundary; non-manifold vertices: {invalid[:5]}")
    unused = {tuple(sorted(edge)) for edge in boundary}
    rings: list[list[tuple[float, float]]] = []
    while unused:
        first, second = next(iter(unused))
        ring = [first, second]
        unused.remove(tuple(sorted((first, second))))
        previous, current = first, second
        while current != first:
            candidates = [point for point in adjacency[current] if point != previous]
            if not candidates:
                raise ValueError("Open region boundary")
            following = candidates[0]
            edge = tuple(sorted((current, following)))
            if edge not in unused and following != first:
                raise ValueError("Region boundary traversal repeated an edge")
            if edge in unused:
                unused.remove(edge)
            ring.append(following)
            previous, current = current, following
        if polygon_area(ring[:-1]) > 0:
            ring = list(reversed(ring))
        rings.append(ring)
    return sorted(rings, key=lambda ring: abs(polygon_area(ring[:-1])), reverse=True)


def point_in_ring(point: tuple[float, float], ring: list[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    for first, second in zip(ring, ring[1:]):
        cross = (second[0] - first[0]) * (y - first[1]) - (second[1] - first[1]) * (x - first[0])
        if abs(cross) <= 1e-10 and min(first[0], second[0]) - 1e-10 <= x <= max(first[0], second[0]) + 1e-10 and min(first[1], second[1]) - 1e-10 <= y <= max(first[1], second[1]) + 1e-10:
            return True
        if (first[1] > y) != (second[1] > y):
            cross_x = (second[0] - first[0]) * (y - first[1]) / (second[1] - first[1]) + first[0]
            if x <= cross_x:
                inside = not inside
    return inside


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def build() -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[list[list[tuple[float, float]]]]], dict[str, Any]]:
    regions = load_json(TASK2_REGIONS)
    task5 = load_json(TASK5_PLAN)
    task6 = load_json(TASK6_PLAN)

    pm_map = {
        text(item.get("id") or item.get("code")): text(item.get("zpm_id"))
        for item in task6.get("fixed_assignments", {}).get("pm_sites", []) or task6.get("pm_sites", [])
    }
    if not pm_map:
        pm_map = {text(item.get("id") or item.get("code")): text(item.get("zpm_id")) for item in task5["devices"]["pm"]}
    task6_pm = {
        text(item.get("id") or item.get("code")): item
        for item in task6.get("fixed_assignments", {}).get("pm_sites", []) or task6.get("pm_sites", [])
    }
    nro_codes = [text(item.get("id") or item.get("code")) for item in task5["devices"]["nro"]]
    nro_code = nro_codes[0]

    mapping: dict[str, str] = {}
    with (TASK4_OUTPUT / "IMB_PBO_BPE_mapping.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        for item in csv.DictReader(handle):
            mapping[text(item.get("IMB_CODE"))] = text(item.get("PBO_CODE"))

    rows: dict[str, list[dict[str, Any]]] = {name: [] for name in SCHEMAS}
    geometries: dict[str, list[list[list[tuple[float, float]]]]] = {name: [] for name in SCHEMAS}

    for source, point in point_layer(TASK4_INPUT / "IMB"):
        code = text(source.get("CODE"))
        residential = integer(source.get("NB_LOC_RES"))
        professional = integer(source.get("NB_LOC_PRO"))
        rows["IMB"].append({
            "CODE": code, "REF_PLAQUE": text(source.get("REF_PLAQUE"), "JAD-MAR"),
            "REGION": "CASABLANCA-SETTAT", "PROVINCE": "EL JADIDA", "VILLE": text(source.get("VILLE"), "ELJADIDA"),
            "COMMUNE": "ELJADIDA", "QUARTIER": "JAD-MAR", "CODE_POSTA": integer(source.get("CODE_POSTA"), 24000),
            "NUMERO_VOI": integer(source.get("NUMERO_VOI")), "COMPLE_NUM": "NR", "TYPE_VOIE": text(source.get("TYPE_VOIE"), "RUE"),
            "NOM_VOIE": text(source.get("NOM_VOIE"), "DEMO"), "COMPLE_VOI": "NR", "CODE_VOIE": text(source.get("NOM_VOIE"), "DEMO"),
            "NOM_BATIME": text(source.get("NOM_BATIME"), code), "TYPE_BATIM": text(source.get("TYPE_BATIM"), "IMMEUBLE"),
            "TYPE_CLIEN": "MIXTE" if professional else "RESIDENTIEL", "NB_LOC_RES": residential, "NB_LOC_PRO": professional,
            "NB_LOC_TOT": residential + professional, "RACCORDEME": text(source.get("RACCORDEME"), "CONDUITE"),
            "STATUT": "EN PROJET", "GESTION": "UNIFIBER", "NUM_GESTIO": "NR", "NB_ETAGE": 1,
            "COL_MONTAN": "OUI", "SOUS_SOL": "NON", "SOUS_SOL_C": "NON", "BPE_CODE": mapping[code],
            "X": point[0], "Y": point[1], "COMMENT": "TASK4_FINAL_INPUT",
        })
        geometries["IMB"].append([[point]])

    boite_points: dict[str, tuple[float, float]] = {}
    for source, point in point_layer(TASK4_OUTPUT / "BOITE_ALGORITHM"):
        code = text(source.get("CODE"))
        ref_pm = pm_map.get(text(source.get("REF_PM")), text(source.get("REF_PM")))
        row = {name: source.get(name) for name, *_ in SCHEMAS["BOITE"]}
        row.update({"CODE": code, "REF_PM": ref_pm, "CABLE_AMON": "PENDING", "X": point[0], "Y": point[1]})
        for name, kind, *_ in SCHEMAS["BOITE"]:
            if row.get(name) in (None, ""):
                row[name] = 0 if kind in {"N", "F"} else "NR"
        rows["BOITE"].append(row)
        geometries["BOITE"].append([[point]])
        boite_points[code] = point

    site_points: dict[str, tuple[float, float]] = {}
    for source, point in point_layer(TASK4_OUTPUT / "SITE_PM_PARTITION"):
        kind = text(source.get("TYPE")).upper()
        old_code = text(source.get("CODE"))
        code = pm_map.get(old_code, old_code) if kind == "PM" else old_code
        if kind == "PM" and old_code in task6_pm:
            point = (number(task6_pm[old_code].get("x")), number(task6_pm[old_code].get("y")))
        row = {
            "CODE": code, "REF_PLAQUE": text(source.get("REF_PLAQUE"), "JAD-MAR"), "REF_NRO": text(source.get("REF_NRO"), nro_code),
            "TYPE": kind, "FABRIQUANT": text(source.get("FABRIQUANT"), "FBR"),
            "REF_PRODUI": text(source.get("REF_PRODUI"), "FBR-PM576" if kind == "PM" else "FBR-NRO2048"),
            "MODE_POSE": text(source.get("MODE_POSE"), "ARMOIRE" if kind == "PM" else "LOCAL_TECHNIQUE"),
            "STATUT": text(source.get("STATUT"), "EN PROJET"), "PROPRIETAI": text(source.get("PROPRIETAI"), "UNIFIBER"),
            "GESTIONNAI": text(source.get("GESTIONNAI"), "UNIFIBER"), "ADRESSSE": text(source.get("ADRESSSE"), "FIELD_SURVEY_REQUIRED"),
            "COMMUNE": "ELJADIDA", "CODE_POSTA": integer(source.get("CODE_POSTA"), 24000), "X": point[0], "Y": point[1],
            "COMMENT": "TASK4_SITE_PARTITION",
        }
        rows["SITE"].append(row)
        geometries["SITE"].append([[point]])
        site_points[code] = point

    seen_ptech: set[str] = set()
    for base in (TASK4_INPUT / "PTECH", TASK4_OUTPUT / "PTECH_ALGORITHM"):
        for source, point in point_layer(base):
            code = text(source.get("CODE"))
            if code in seen_ptech:
                continue
            seen_ptech.add(code)
            row = {name: source.get(name) for name, *_ in SCHEMAS["PTECH"]}
            row.update({"CODE": code, "X": point[0], "Y": point[1]})
            for name, kind, *_ in SCHEMAS["PTECH"]:
                if row.get(name) in (None, ""):
                    row[name] = 0 if kind in {"N", "F"} else "NR"
            rows["PTECH"].append(row)
            geometries["PTECH"].append([[point]])

    route_to_infra: dict[str, str] = {}
    for route in task6.get("duct_routes", []):
        coordinates = [(number(item.get("x")), number(item.get("y"))) for item in route.get("coordinates", [])]
        if len(coordinates) < 2:
            continue
        code = f"INF-{len(rows['INFRASTRUCTURE']) + 1:05d}"
        route_to_infra[text(route.get("id"))] = code
        route_type = text(route.get("route_type"))
        rows["INFRASTRUCTURE"].append({
            "CODE": code, "NOM": text(route.get("id")), "REF_PLAQUE": "JAD-MAR",
            "ORIGINE": pm_map.get(text(route.get("from_code")), text(route.get("from_code"))),
            "EXTREMITE": pm_map.get(text(route.get("to_code")), text(route.get("to_code"))),
            "COMPOSITIO": "1 PEHD 33/40", "TYPE": "SOUTERRAIN",
            "TYPE_LOG": "TRANSPORT" if route_type == "NRO_TO_PM_MAIN" else "DISTRIBUTION",
            "STATUT": "EN PROJET", "PROPRIETAI": "UNIFIBER", "GESTIONNAI": "UNIFIBER",
            "LONGUEUR": max(number(route.get("length_m")), 0.01), "COMMENT": route_type,
        })
        geometries["INFRASTRUCTURE"].append([coordinates])

    imb_points = {row["CODE"]: (number(row["X"]), number(row["Y"])) for row in rows["IMB"]}
    device_points = {**site_points, **boite_points, **imb_points}
    incoming_cable: dict[str, str] = {}
    for link in task5.get("links", []):
        stage = text(link.get("stage"))
        origin = pm_map.get(text(link.get("from_code")), text(link.get("from_code")))
        endpoint = pm_map.get(text(link.get("to_code")), text(link.get("to_code")))
        coordinates = [(number(item.get("x")), number(item.get("y"))) for item in link.get("coordinates", [])]
        if len(coordinates) < 2:
            continue
        if origin in device_points:
            coordinates[0] = device_points[origin]
        if endpoint in device_points:
            coordinates[-1] = device_points[endpoint]
        refs = [route_to_infra[item] for item in link.get("conduit_route_ids", []) if item in route_to_infra]
        if stage == "PBO_TO_IMB":
            # Indoor drop cables have no Task-6 duct route.  Keep an explicit
            # sentinel so the complete cable inventory remains joinable.
            refs = ["INDOOR"]
        code = f"CAB-{len(rows['CABLE']) + 1:05d}"
        ref_pm = origin if origin in set(pm_map.values()) else next(
            (text(item.get("REF_PM")) for item in rows["BOITE"] if item["CODE"] in {origin, endpoint}),
            next(iter(pm_map.values())),
        )
        capacity = integer(link.get("cable_cores"), 24)
        used = min(capacity, integer(link.get("downstream_ports")))
        stage = text(link.get("stage"))
        is_indoor = stage == "PBO_TO_IMB"
        rows["CABLE"].append({
            "CODE": code, "NOM": code, "REF_PLAQUE": "JAD-MAR", "REF_NRO": nro_code, "REF_PM": ref_pm,
            "CODE_INFRA": refs[0] if refs else rows["INFRASTRUCTURE"][0]["CODE"],
            "INFRA_IDS": ";".join(refs) if refs else rows["INFRASTRUCTURE"][0]["CODE"],
            "ORIGINE": origin, "EXTREMITE": endpoint,
            "TYPE_CABLE": "DROP" if is_indoor else ("TRANSPORT" if stage == "NRO_TO_PM" else "DISTRIBUTION"), "DIAMETRE": 4 if is_indoor else 8,
            "MODE_POSE": "INTERIEUR" if is_indoor else "SOUTERRAIN", "CAPACITE": capacity, "MODULO": 12, "FABRIQUANT": "FBR",
            "REF_PRODUI": text(link.get("cable_spec"), f"FIBER_{capacity}_CORE"), "TYPE_FIBRE": "G657A2",
            "NB_FIBRE_U": used, "NB_FIBRE_D": capacity - used, "STATUT": "EN PROJET",
            "PROPRIETAI": "UNIFIBER", "GESTIONNAI": "UNIFIBER", "TYPE_PROP": "CONSTRUCTION",
            "LONGUEUR": max(number(link.get("cable_length_m", link.get("length_m"))), 0.01), "COMMENT": stage,
        })
        geometries["CABLE"].append([coordinates])
        if endpoint in boite_points:
            incoming_cable[endpoint] = code
    for row in rows["BOITE"]:
        row["CABLE_AMON"] = incoming_cable.get(row["CODE"], "FIELD_CHECK")

    # The workbook checks aggregate outgoing PM cable capacity against the sum
    # of its PBO port capacities.  Preserve the selected link count and add any
    # required reserve to the first outgoing cable of that PM.
    pbo_capacity: Counter[str] = Counter()
    outgoing_capacity: Counter[str] = Counter()
    outgoing_by_pm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows["BOITE"]:
        if row["TYPE"] == "PBO":
            pbo_capacity[row["REF_PM"]] += integer(row["CAPACITE"])
    for row in rows["CABLE"]:
        if row["TYPE_CABLE"] == "DISTRIBUTION" and row["ORIGINE"] in set(pm_map.values()):
            outgoing_capacity[row["ORIGINE"]] += integer(row["CAPACITE"])
            outgoing_by_pm[row["ORIGINE"]].append(row)
    for pm_code, required_capacity in pbo_capacity.items():
        shortfall = required_capacity - outgoing_capacity[pm_code]
        if shortfall > 0:
            cable = outgoing_by_pm[pm_code][0]
            cable["CAPACITE"] = integer(cable["CAPACITE"]) + shortfall
            cable["NB_FIBRE_D"] = integer(cable["CAPACITE"]) - integer(cable["NB_FIBRE_U"])
            cable["REF_PRODUI"] = f"FIBER_{cable['CAPACITE']}_CORE"

    znro_by_code = {item["id"]: item for item in regions["znro_regions"]}
    bounds = regions["service_boundary"]["bounds"]
    min_x, min_y = number(bounds["min_x"]), number(bounds["min_y"])
    max_x, max_y = number(bounds["max_x"]), number(bounds["max_y"])
    boundary_ring = [(min_x, min_y), (min_x, max_y), (max_x, max_y), (max_x, min_y), (min_x, min_y)]
    for region in regions["znro_regions"]:
        rows["ZNRO"].append({
            "CODE": text(region.get("id")), "REF_PLAQUE": "JAD-MAR", "REF_NRO": nro_code,
            "STATUT": "EN PROJET", "NB_PRISES": integer(region.get("total_ports")), "COMMENT": "TASK2_REGION",
        })
        geometries["ZNRO"].append([boundary_ring])

    zpm_rings: dict[str, list[list[tuple[float, float]]]] = {}
    pm_x = {row["CODE"]: number(row["X"]) for row in rows["SITE"] if row["TYPE"] == "PM"}
    ordered_pm = sorted(pm_x, key=pm_x.get)
    if len(ordered_pm) != 2:
        raise ValueError(f"Final delivery currently requires exactly two PM partitions, found {ordered_pm}")
    west_code, east_code = ordered_pm
    west_points = [number(row["X"]) for row in rows["BOITE"] if row["TYPE"] == "PBO" and row["REF_PM"] == west_code]
    east_points = [number(row["X"]) for row in rows["BOITE"] if row["TYPE"] == "PBO" and row["REF_PM"] == east_code]
    split_x = (max(west_points) + min(east_points)) / 2.0
    partition_rings = {
        west_code: [[(min_x, min_y), (min_x, max_y), (split_x, max_y), (split_x, min_y), (min_x, min_y)]],
        east_code: [[(split_x, min_y), (split_x, max_y), (max_x, max_y), (max_x, min_y), (split_x, min_y)]],
    }
    for region in regions["zpm_regions"]:
        code = text(region.get("id"))
        rings = partition_rings[code]
        zpm_rings[code] = rings
        rows["ZPM"].append({
            "CODE": code, "REF_PLAQUE": "JAD-MAR", "REF_NRO": nro_code, "REF_SRO": code, "REF_PM": code,
            "STATUT": "EN PROJET", "NB_PRISES": integer(region.get("total_ports")), "COMMENT": "TASK2_REGION",
        })
        geometries["ZPM"].append(rings)

    context = {"pm_map": pm_map, "zpm_rings": zpm_rings, "source_region_validation": regions.get("validation", {})}
    return rows, geometries, context


def validate(rows: dict[str, list[dict[str, Any]]], geometries: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    layer_results: dict[str, Any] = {}
    field_ok = True
    for layer, records in rows.items():
        actual = {name for name, *_ in SCHEMAS[layer]}
        missing_fields = sorted(set(REQUIRED_FIELDS[layer]) - actual)
        blank_counts = {
            name: sum(record.get(name) in (None, "") for record in records)
            for name in REQUIRED_FIELDS[layer]
            if sum(record.get(name) in (None, "") for record in records)
        }
        codes = [text(record.get("CODE"), "") for record in records]
        duplicate_codes = sorted(code for code, count in Counter(codes).items() if code and count > 1)
        status = "PASS" if records and not missing_fields and not blank_counts and not duplicate_codes else "FAIL"
        field_ok &= status == "PASS"
        layer_results[layer] = {
            "status": status, "feature_count": len(records), "field_count": len(SCHEMAS[layer]),
            "missing_required_fields": missing_fields, "blank_required_value_counts": blank_counts,
            "duplicate_codes": duplicate_codes,
        }

    site_pm = {row["CODE"] for row in rows["SITE"] if row["TYPE"] == "PM"}
    zpm = {row["CODE"] for row in rows["ZPM"]}
    boites = {row["CODE"] for row in rows["BOITE"]}
    endpoint_codes = site_pm | boites
    distribution = [row for row in rows["CABLE"] if row["TYPE_CABLE"] == "DISTRIBUTION"]
    references = {
        "site_pm_equals_zpm": site_pm == zpm,
        "all_boite_ref_pm_exist": all(row["REF_PM"] in site_pm for row in rows["BOITE"] if row["TYPE"] == "PBO"),
        "all_distribution_ref_pm_exist": all(row["REF_PM"] in site_pm for row in distribution),
        "all_distribution_endpoints_exist": all(row["ORIGINE"] in endpoint_codes and row["EXTREMITE"] in endpoint_codes for row in distribution),
        "different_cable_endpoints": all(row["ORIGINE"] != row["EXTREMITE"] for row in rows["CABLE"]),
    }

    point_by_site = {row["CODE"]: (number(row["X"]), number(row["Y"])) for row in rows["SITE"]}
    point_by_boite = {row["CODE"]: (number(row["X"]), number(row["Y"])) for row in rows["BOITE"]}
    zpm_rings = context["zpm_rings"]
    pm_outside = [code for code in site_pm if not any(point_in_ring(point_by_site[code], ring) for ring in zpm_rings[code])]
    boite_outside = [row["CODE"] for row in rows["BOITE"] if row["TYPE"] == "PBO" and not any(point_in_ring(point_by_boite[row["CODE"]], ring) for ring in zpm_rings[row["REF_PM"]])]
    cable_vertex_violations = []
    endpoint_mismatches = []
    point_by_imb = {row["CODE"]: (number(row["X"]), number(row["Y"])) for row in rows["IMB"]}
    all_points = {**point_by_site, **point_by_boite, **point_by_imb}
    for row, geometry in zip(rows["CABLE"], geometries["CABLE"]):
        line = geometry[0]
        if row["ORIGINE"] in all_points and line[0] != all_points[row["ORIGINE"]]:
            endpoint_mismatches.append(row["CODE"])
        if row["EXTREMITE"] in all_points and line[-1] != all_points[row["EXTREMITE"]]:
            endpoint_mismatches.append(row["CODE"])
        if row["TYPE_CABLE"] == "DISTRIBUTION":
            outside = sum(not any(point_in_ring(point, ring) for ring in zpm_rings[row["REF_PM"]]) for point in line)
            if outside:
                cable_vertex_violations.append({"cable": row["CODE"], "outside_vertex_count": outside})

    pbo_capacity = Counter()
    cable_capacity = Counter()
    for row in rows["BOITE"]:
        if row["TYPE"] == "PBO":
            pbo_capacity[row["REF_PM"]] += integer(row["CAPACITE"])
    for row in distribution:
        if row["ORIGINE"] in site_pm:
            cable_capacity[row["ORIGINE"]] += integer(row["CAPACITE"])
    capacity_violations = [code for code in site_pm if pbo_capacity[code] > cable_capacity[code]]

    geometry_result = {
        "source_region_non_overlap": bool(context["source_region_validation"].get("zpm_regions_non_overlapping")),
        "pm_outside_zpm": pm_outside, "pbo_outside_zpm": boite_outside,
        "cable_endpoint_mismatches": sorted(set(endpoint_mismatches)),
        "distribution_cable_vertex_violations": cable_vertex_violations,
        "pm_capacity_violations": capacity_violations,
    }
    geometry_result["status"] = "PASS" if not any((pm_outside, boite_outside, endpoint_mismatches, cable_vertex_violations, capacity_violations)) else "PASS_WITH_WARNINGS"
    return {
        "status": "PASS" if field_ok and all(references.values()) else "FAIL",
        "crs": "EPSG:4326", "layers": layer_results, "cross_layer_references": references,
        "cable_inventory": {
            "scope": "COMPLETE_LOGICAL_CABLE_INVENTORY",
            "total": len(rows["CABLE"]),
            "task6_conduit_backed": sum(row["TYPE_CABLE"] != "DROP" for row in rows["CABLE"]),
            "indoor_drop": sum(row["TYPE_CABLE"] == "DROP" for row in rows["CABLE"]),
        },
        "geometry_and_capacity": geometry_result,
        "field_name_mapping": FIELD_NAMES,
    }


def validate_serialized_layers() -> dict[str, Any]:
    result: dict[str, Any] = {}
    expected_shape_types = {
        "IMB": 1, "SITE": 1, "BOITE": 1, "PTECH": 1,
        "CABLE": 3, "INFRASTRUCTURE": 3, "ZNRO": 5, "ZPM": 5,
    }
    for layer, expected_fields in SCHEMAS.items():
        actual_fields, records = read_dbf(OUTPUT_DIR / f"{layer}.dbf")
        shp_data = (OUTPUT_DIR / f"{layer}.shp").read_bytes()
        shape_type = struct.unpack("<I", shp_data[32:36])[0]
        position = 100
        shape_count = 0
        while position < len(shp_data):
            if position + 8 > len(shp_data):
                raise ValueError(f"Truncated SHP record header in {layer}")
            content_bytes = struct.unpack(">I", shp_data[position + 4 : position + 8])[0] * 2
            position += 8 + content_bytes
            shape_count += 1
        blank_counts = {
            name: sum(record.get(name) in (None, "") for record in records)
            for name in REQUIRED_FIELDS[layer]
            if sum(record.get(name) in (None, "") for record in records)
        }
        checks = {
            "dbf_schema_exact": actual_fields == expected_fields,
            "dbf_schema": [
                {"name": name, "type": kind, "length": width, "decimals": decimals}
                for name, kind, width, decimals in actual_fields
            ],
            "dbf_record_count": len(records),
            "shp_record_count": shape_count,
            "record_counts_match": len(records) == shape_count,
            "shape_type": shape_type,
            "shape_type_correct": shape_type == expected_shape_types[layer],
            "shp_fully_parsed": position == len(shp_data),
            "blank_required_value_counts": blank_counts,
        }
        checks["status"] = "PASS" if all(
            (checks["dbf_schema_exact"], checks["record_counts_match"], checks["shape_type_correct"], checks["shp_fully_parsed"], not blank_counts)
        ) else "FAIL"
        result[layer] = checks
    return result


def main(argv: list[str] | None = None) -> int:
    global OUTPUT_DIR, TASK2_REGIONS, TASK4_INPUT, TASK4_OUTPUT, TASK5_PLAN, TASK6_PLAN
    parser = argparse.ArgumentParser(description="Build and validate the final FTTH delivery layers")
    parser.add_argument("--task2-regions", type=Path, default=TASK2_REGIONS)
    parser.add_argument("--task4-input", type=Path, default=TASK4_INPUT)
    parser.add_argument("--task4-output", type=Path, default=TASK4_OUTPUT)
    parser.add_argument("--task5-plan", type=Path, default=TASK5_PLAN)
    parser.add_argument("--task6-plan", type=Path, default=TASK6_PLAN)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args(argv)
    OUTPUT_DIR = args.output_dir.resolve()
    TASK2_REGIONS = args.task2_regions.resolve()
    TASK4_INPUT = args.task4_input.resolve()
    TASK4_OUTPUT = args.task4_output.resolve()
    TASK5_PLAN = args.task5_plan.resolve()
    TASK6_PLAN = args.task6_plan.resolve()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows, geometries, context = build()
    for layer in ("IMB", "SITE", "BOITE", "CABLE", "PTECH", "INFRASTRUCTURE", "ZNRO", "ZPM"):
        shape_type = 1 if layer in {"IMB", "SITE", "BOITE", "PTECH"} else (3 if layer in {"CABLE", "INFRASTRUCTURE"} else 5)
        write_layer(layer, shape_type, rows[layer], geometries[layer])
    report = validate(rows, geometries, context)
    report["serialized_layers"] = validate_serialized_layers()
    if any(item["status"] != "PASS" for item in report["serialized_layers"].values()):
        report["status"] = "FAIL"
    (OUTPUT_DIR / "field_validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT_DIR), "status": report["status"], "layers": {key: len(value) for key, value in rows.items()}, "geometry": report["geometry_and_capacity"]}, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
