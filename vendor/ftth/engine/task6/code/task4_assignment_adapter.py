"""Import fixed Task-4 PBO/BPE assignments for the Task-6 layered input."""

from __future__ import annotations

import csv
import struct
from collections import defaultdict
from pathlib import Path
from typing import Any


def _read_dbf(path: Path) -> list[dict[str, Any]]:
    with path.open("rb") as handle:
        header = handle.read(32)
        record_count = struct.unpack("<I", header[4:8])[0]
        header_size = struct.unpack("<H", header[8:10])[0]
        record_size = struct.unpack("<H", header[10:12])[0]
        fields: list[tuple[str, str, int, int]] = []
        while handle.tell() < header_size - 1:
            field = handle.read(32)
            fields.append((field[:11].split(b"\0", 1)[0].decode("latin1"), chr(field[11]), field[16], field[17]))
        handle.read(1)
        records: list[dict[str, Any]] = []
        for _ in range(record_count):
            row = handle.read(record_size)
            if not row or row[:1] == b"*":
                continue
            offset = 1
            record: dict[str, Any] = {}
            for name, kind, width, decimals in fields:
                raw = row[offset : offset + width].decode("latin1").strip()
                offset += width
                if kind in {"N", "F"}:
                    record[name] = float(raw) if raw and decimals else (int(raw) if raw else None)
                else:
                    record[name] = raw or None
            records.append(record)
    return records


def _read_point_shapes(path: Path) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    with path.open("rb") as handle:
        header = handle.read(100)
        if struct.unpack("<I", header[32:36])[0] != 1:
            raise ValueError(f"Expected Point shapefile: {path}")
        while True:
            record_header = handle.read(8)
            if not record_header:
                break
            content = handle.read(struct.unpack(">I", record_header[4:8])[0] * 2)
            shape_type = struct.unpack("<I", content[:4])[0]
            if shape_type == 1:
                points.append(struct.unpack("<dd", content[4:20]))
            elif shape_type == 0:
                points.append((float("nan"), float("nan")))
            else:
                raise ValueError(f"Unexpected shape type {shape_type}: {path}")
    return points


def _imb_code_map(input_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in input_data.get("imb_points") or []:
        if not isinstance(item, dict):
            continue
        for key in ("id", "code", "CODE"):
            value = str(item.get(key) or "")
            if value:
                result[value] = item
    return result


def load_task4_plan(task4_output: str | Path, input_data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read Task-4 point layers and retain their fixed PBO/BPE relationships."""
    output_dir = Path(task4_output)
    base = output_dir / "BOITE_ALGORITHM"
    required = [base.with_suffix(".dbf"), base.with_suffix(".shp"), output_dir / "IMB_PBO_BPE_mapping.csv", output_dir / "BPE_cluster_members.csv"]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Task-4 assignment output is incomplete: {missing}")
    rows, points = _read_dbf(base.with_suffix(".dbf")), _read_point_shapes(base.with_suffix(".shp"))
    if len(rows) != len(points):
        raise ValueError("Task-4 BOITE DBF and SHP record counts differ")
    with (output_dir / "IMB_PBO_BPE_mapping.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        pbo_to_imb = {str(row.get("PBO_CODE") or ""): str(row.get("IMB_CODE") or "") for row in csv.DictReader(handle) if row.get("PBO_CODE") and row.get("IMB_CODE")}
    members_by_bpe: dict[str, list[str]] = defaultdict(list)
    with (output_dir / "BPE_cluster_members.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            bpe_code, pbo_code = str(row.get("BPE_CODE") or ""), str(row.get("PBO_CODE") or "")
            if bpe_code and pbo_code:
                members_by_bpe[bpe_code].append(pbo_code)
    pbo_to_bpe = {pbo_code: bpe_code for bpe_code, pbo_codes in members_by_bpe.items() for pbo_code in pbo_codes}
    imbs = _imb_code_map(input_data)
    # Task 4 publishes the partition key in REF_PM for historical reasons;
    # Task 6 uses the selected PM site code.  Resolve the stable ZPM -> PM
    # relation carried by the current Task-3 hand-off at this boundary.
    pm_by_zpm = {
        str(item.get("zpm_id") or item.get("partition_id") or ""): str(item.get("id") or item.get("code") or "")
        for item in (input_data.get("pm_sites") or [])
        if item.get("zpm_id") and (item.get("id") or item.get("code"))
    }
    # Task-4 publishes only REF_PM in its historical DBF schema.  Task-6 and
    # the Task-3 optimizer require both sides of the relation, so resolve the
    # selected PM back to its authoritative ZPM id at this hand-off.
    zpm_by_pm = {pm_code: zpm_code for zpm_code, pm_code in pm_by_zpm.items()}
    pbos: list[dict[str, Any]] = []
    bpes: list[dict[str, Any]] = []
    for row, coordinates in zip(rows, points):
        code, box_type = str(row.get("CODE") or ""), str(row.get("TYPE") or "").upper()
        if not code or box_type not in {"PBO", "BPE"}:
            continue
        record = {
            "id": code,
            "code": code,
            "x": coordinates[0],
            "y": coordinates[1],
            "capacity": int(row.get("CAPACITE") or 0),
            "demand": int(row.get("NB_LOGEMEN") or 0),
            "pm_id": pm_by_zpm.get(str(row.get("REF_PM") or ""), row.get("REF_PM")),
            "nro_id": row.get("REF_NRO"),
            "source": "task4_fixed_assignment",
        }
        record["zpm_id"] = zpm_by_pm.get(str(record["pm_id"]))
        if not record["zpm_id"]:
            raise ValueError(
                f"Task-4 {box_type} {code} references PM without a Task-6 ZPM relation: {record['pm_id']}"
            )
        if box_type == "PBO":
            imb_id = pbo_to_imb.get(code)
            record.update({"bpe_id": pbo_to_bpe.get(code), "imb_id": imb_id})
            if imb_id in imbs:
                record["imb_ports"] = int(imbs[imb_id].get("ports") or imbs[imb_id].get("demand") or 0)
            pbos.append(record)
        else:
            record["member_pbo_ids"] = sorted(members_by_bpe.get(code, []))
            bpes.append(record)
    if not pbos or not bpes:
        raise ValueError("Task-4 output must contain PBO and BPE records")
    return sorted(pbos, key=lambda item: item["id"]), sorted(bpes, key=lambda item: item["id"])
