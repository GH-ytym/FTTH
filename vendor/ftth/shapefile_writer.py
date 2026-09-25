"""Minimal dependency-free Point/Polyline/Polygon Shapefile writer."""

from __future__ import annotations

import struct
from datetime import date
from pathlib import Path
from typing import Any


WGS84_PRJ = ('GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
             'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433],AUTHORITY["EPSG","4326"]]')
SHAPE_TYPES = {"Point": 1, "LineString": 3, "Polygon": 5}


def _encoded(value: Any, width: int) -> bytes:
    text = "" if value is None else str(value)
    raw = text.encode("utf-8")
    while len(raw) > width and text:
        text = text[:-1]
        raw = text.encode("utf-8")
    return raw.ljust(width, b" ")


def _dbf_value(value: Any, kind: str, width: int, decimals: int) -> bytes:
    if value in (None, ""):
        return b" " * width
    if kind == "C":
        return _encoded(value, width)
    if kind in {"N", "F"}:
        try:
            text = f"{float(value):.{decimals}f}" if decimals else str(int(round(float(value))))
        except (TypeError, ValueError):
            text = "0"
        return text.encode("ascii")[-width:].rjust(width, b" ")
    if kind == "L":
        return (b"T" if bool(value) else b"F").ljust(width, b" ")
    return _encoded(value, width)


def _write_dbf(path: Path, fields: list[tuple[str, str, int, int]], rows: list[dict[str, Any]]) -> None:
    header_size = 32 + len(fields) * 32 + 1
    record_size = 1 + sum(field[2] for field in fields)
    today = date.today()
    header = bytearray(32)
    header[0] = 3
    header[1:4] = bytes((today.year - 1900, today.month, today.day))
    header[4:8] = struct.pack("<I", len(rows))
    header[8:10] = struct.pack("<H", header_size)
    header[10:12] = struct.pack("<H", record_size)
    with path.open("wb") as stream:
        stream.write(header)
        for name, kind, width, decimals in fields:
            field = bytearray(32)
            field[:11] = name.encode("ascii")[:10].ljust(11, b"\0")
            field[11] = ord(kind)
            field[16], field[17] = width, decimals
            stream.write(field)
        stream.write(b"\r")
        for row in rows:
            stream.write(b" ")
            for name, kind, width, decimals in fields:
                stream.write(_dbf_value(row.get(name), kind, width, decimals))
        stream.write(b"\x1a")


def _parts(geometry_type: str, geometry: Any) -> list[list[tuple[float, float]]]:
    if geometry_type == "Point":
        return [[(float(geometry[0]), float(geometry[1]))]]
    raw = geometry if geometry_type == "Polygon" else [geometry]
    result = [[(float(point[0]), float(point[1])) for point in part] for part in raw if len(part) >= 2]
    if geometry_type == "Polygon":
        for part in result:
            if part[0] != part[-1]:
                part.append(part[0])
    return result


def _all_points(geometry_type: str, geometries: list[Any]) -> list[tuple[float, float]]:
    return [point for geometry in geometries for part in _parts(geometry_type, geometry) for point in part]


def _header(length_words: int, shape_type: int, bounds: tuple[float, float, float, float]) -> bytes:
    return struct.pack(">i", 9994) + bytes(20) + struct.pack(">i", length_words) + struct.pack(
        "<ii8d", 1000, shape_type, *bounds, 0.0, 0.0, 0.0, 0.0
    )


def _content(geometry_type: str, geometry: Any) -> bytes:
    shape_type = SHAPE_TYPES[geometry_type]
    parts = _parts(geometry_type, geometry)
    points = [point for part in parts for point in part]
    if geometry_type == "Point":
        return struct.pack("<idd", shape_type, *points[0])
    xs, ys = [point[0] for point in points], [point[1] for point in points]
    indices, offset = [], 0
    for part in parts:
        indices.append(offset)
        offset += len(part)
    return (struct.pack("<i4d2i", shape_type, min(xs), min(ys), max(xs), max(ys), len(parts), len(points))
            + struct.pack(f"<{len(indices)}i", *indices)
            + b"".join(struct.pack("<dd", *point) for point in points))


def _write_shapes(base: Path, geometry_type: str, geometries: list[Any]) -> None:
    shape_type = SHAPE_TYPES[geometry_type]
    points = _all_points(geometry_type, geometries)
    if not points:
        raise ValueError(f"{base.name}没有几何要素")
    bounds = (min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points))
    contents = [_content(geometry_type, geometry) for geometry in geometries]
    shp_words = 50 + sum(4 + len(content) // 2 for content in contents)
    shx_words = 50 + 4 * len(contents)
    with base.with_suffix(".shp").open("wb") as shp, base.with_suffix(".shx").open("wb") as shx:
        shp.write(_header(shp_words, shape_type, bounds))
        shx.write(_header(shx_words, shape_type, bounds))
        offset = 50
        for index, content in enumerate(contents, 1):
            words = len(content) // 2
            shp.write(struct.pack(">ii", index, words))
            shp.write(content)
            shx.write(struct.pack(">ii", offset, words))
            offset += 4 + words


def write_layer(base: str | Path, geometry_type: str, fields: list[tuple[str, str, int, int]], rows: list[dict[str, Any]], geometries: list[Any], *, prj: str = WGS84_PRJ) -> Path:
    destination = Path(base)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if len(rows) != len(geometries):
        raise ValueError(f"{destination.name}属性与几何数量不一致")
    _write_shapes(destination, geometry_type, geometries)
    _write_dbf(destination.with_suffix(".dbf"), fields, rows)
    destination.with_suffix(".prj").write_text(prj, encoding="ascii")
    destination.with_suffix(".cpg").write_text("UTF-8", encoding="ascii")
    return destination.with_suffix(".shp")
