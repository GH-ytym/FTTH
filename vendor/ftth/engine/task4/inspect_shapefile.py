"""Minimal Shapefile/DBF inspector for the supplied planning data."""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path


def read_dbf(path: Path) -> tuple[list[tuple[str, str, int, int]], list[dict[str, object]]]:
    with path.open("rb") as handle:
        header = handle.read(32)
        record_count = struct.unpack("<I", header[4:8])[0]
        header_size = struct.unpack("<H", header[8:10])[0]
        record_size = struct.unpack("<H", header[10:12])[0]
        fields: list[tuple[str, str, int, int]] = []
        while handle.tell() < header_size - 1:
            field = handle.read(32)
            name = field[:11].split(b"\0", 1)[0].decode("latin1")
            fields.append((name, chr(field[11]), field[16], field[17]))
        handle.read(1)
        records: list[dict[str, object]] = []
        for _ in range(record_count):
            row = handle.read(record_size)
            if not row or row[0:1] == b"*":
                continue
            offset = 1
            record: dict[str, object] = {}
            for name, kind, width, decimals in fields:
                raw = row[offset : offset + width].decode("latin1").strip()
                offset += width
                if raw and set(raw) == {"*"}:
                    raw = ""
                if kind in {"N", "F"}:
                    record[name] = float(raw) if raw and decimals else (int(raw) if raw else None)
                elif kind == "L":
                    record[name] = raw.upper() in {"Y", "T"}
                else:
                    record[name] = raw or None
                if kind == "F" and raw:
                    record[name] = float(raw)
            records.append(record)
    return fields, records


def read_points(path: Path) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    with path.open("rb") as handle:
        header = handle.read(100)
        shape_type = struct.unpack("<I", header[32:36])[0]
        if shape_type != 1:
            raise ValueError(f"Only Point shapefiles are supported (shape type: {shape_type}).")
        while True:
            record_header = handle.read(8)
            if not record_header:
                break
            content_length = struct.unpack(">I", record_header[4:8])[0] * 2
            content = handle.read(content_length)
            record_shape_type = struct.unpack("<I", content[:4])[0]
            if record_shape_type == 0:
                points.append((float("nan"), float("nan")))
            elif record_shape_type == 1:
                points.append(struct.unpack("<dd", content[4:20]))
            else:
                raise ValueError(f"Unexpected record shape type: {record_shape_type}")
    return points


def main() -> None:
    base = Path(sys.argv[1]).with_suffix("")
    fields, rows = read_dbf(base.with_suffix(".dbf"))
    points = read_points(base.with_suffix(".shp"))
    print(json.dumps({"fields": fields, "records": rows, "points": points}, indent=2))


if __name__ == "__main__":
    main()
