"""Generate BOITE/PBO features from an IMB point layer without GIS dependencies.

The planner creates one PBO for every IMB with positive demand.  A PBO is placed
on a small, deterministic ring around its served IMB rather than on the IMB
coordinate itself.  The selected ring position maximizes clearance from other
IMBs and already placed PBOs, while capacity is rounded up to the next
supported cassette size.
"""

from __future__ import annotations

import argparse
import csv
import math
import shutil
import struct
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from inspect_shapefile import read_dbf, read_points
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from project_paths import TASK4_PBO_ONLY_OUTPUT_DIR, TASK4_SAMPLE_INPUT_DIR


TASK_DIR = Path(__file__).resolve().parent
DEFAULT_SHAPE_DIR = TASK4_SAMPLE_INPUT_DIR
DEFAULT_OUTPUT_DIR = TASK4_PBO_ONLY_OUTPUT_DIR
PBO_IMB_OFFSET_M = 5.0
PBO_MIN_IMB_CLEARANCE_M = 1.0
PBO_CANDIDATE_BEARINGS_DEG = tuple(range(0, 360, 30))


def port_capacity(demand: int) -> int:
    """Select the smallest supported PBO capacity for the required ports."""
    # The current Task-2 sample contains IMBs with 60 and 80 ports.  Keep the
    # legacy sizes and add the next standard 96-port enclosure so those
    # records remain one-to-one with their PBO instead of being silently
    # dropped or requiring an unstable split policy.
    for capacity in (12, 24, 48, 96):
        if demand <= capacity:
            return capacity
    raise ValueError(f"PBO demand {demand} exceeds the supported 96-port capacity.")


def pose_mode(raccordement: object) -> str:
    value = str(raccordement or "").upper()
    if "COLONNE" in value:
        return "IMMEUBLE"
    if "FACADE" in value:
        return "ANCRAGE FACADE"
    if "SOUTERRAIN" in value:
        return "SOUTERRAIN"
    return "AUTRE"


def address(imb: dict[str, Any]) -> str:
    parts = [
        str(imb.get("TYPE_VOIE") or "").strip(),
        str(imb.get("NOM_VOIE") or "").strip(),
        str(imb.get("NOM_BATIME") or "").strip(),
    ]
    number = imb.get("NUMERO_VOI")
    if number not in (None, 0):
        parts.insert(0, str(number))
    return " ".join(part for part in parts if part)[:50]


def _offset_point(point: tuple[float, float], bearing_deg: float, distance_m: float) -> tuple[float, float]:
    """Offset a WGS84 point by metres using a local equirectangular scale."""
    latitude = math.radians(point[1])
    longitude_scale = 111320.0 * max(0.1, math.cos(latitude))
    east_m = math.sin(math.radians(bearing_deg)) * distance_m
    north_m = math.cos(math.radians(bearing_deg)) * distance_m
    return point[0] + east_m / longitude_scale, point[1] + north_m / 110540.0


def _nearest_distance(point: tuple[float, float], others: list[tuple[float, float]]) -> float:
    return min((distance_meters(point, other) for other in others), default=float("inf"))


def place_pbo_around_imb(
    imb_point: tuple[float, float],
    other_imb_points: list[tuple[float, float]] | None = None,
    existing_pbo_points: list[tuple[float, float]] | None = None,
    *,
    offset_m: float = PBO_IMB_OFFSET_M,
    phase: int = 0,
) -> dict[str, Any]:
    """Select a non-overlapping PBO point on a fixed-radius candidate ring."""
    if offset_m <= 0:
        raise ValueError("PBO IMB offset must be positive")
    other_imb_points = list(other_imb_points or [])
    existing_pbo_points = list(existing_pbo_points or [])
    candidates = []
    for order, bearing in enumerate(PBO_CANDIDATE_BEARINGS_DEG):
        point = _offset_point(imb_point, bearing, offset_m)
        imb_clearance = _nearest_distance(point, other_imb_points)
        pbo_clearance = _nearest_distance(point, existing_pbo_points)
        candidates.append(
            {
                "point": point,
                "bearing_deg": bearing,
                "offset_m": round(distance_meters(point, imb_point), 3),
                "nearest_other_imb_m": round(imb_clearance, 3) if math.isfinite(imb_clearance) else None,
                "nearest_existing_pbo_m": round(pbo_clearance, 3) if math.isfinite(pbo_clearance) else None,
                "order": (order - phase) % len(PBO_CANDIDATE_BEARINGS_DEG),
            }
        )
    feasible = [candidate for candidate in candidates if candidate["nearest_other_imb_m"] is None or candidate["nearest_other_imb_m"] >= PBO_MIN_IMB_CLEARANCE_M]
    if not feasible:
        feasible = candidates
    selected = max(
        feasible,
        key=lambda candidate: (
            candidate["nearest_other_imb_m"] if candidate["nearest_other_imb_m"] is not None else float("inf"),
            candidate["nearest_existing_pbo_m"] if candidate["nearest_existing_pbo_m"] is not None else float("inf"),
            -candidate["order"],
        ),
    )
    return {key: value for key, value in selected.items() if key != "order"}


def plan_pbo_locations(
    imb_locations: list[tuple[str, tuple[float, float]]],
    *,
    offset_m: float = PBO_IMB_OFFSET_M,
) -> dict[str, dict[str, Any]]:
    """Place every PBO around its IMB with deterministic spacing checks."""
    locations: dict[str, dict[str, Any]] = {}
    placed_points: list[tuple[float, float]] = []
    all_points = [point for _, point in imb_locations]
    for index, (imb_code, imb_point) in enumerate(imb_locations):
        other_points = [point for other_index, (_, point) in enumerate(imb_locations) if other_index != index]
        placement = place_pbo_around_imb(
            imb_point,
            other_points,
            placed_points,
            offset_m=offset_m,
            phase=sum((index + 1) * ord(char) for char in str(imb_code)) % len(PBO_CANDIDATE_BEARINGS_DEG),
        )
        placement["imb_point"] = imb_point
        placement["source"] = "IMB_ADJACENT_OFFSET_RING"
        locations[str(imb_code)] = placement
        placed_points.append(placement["point"])
    return locations


def find_nro(site_dbf: Path) -> dict[str, dict[str, Any]]:
    if not site_dbf.exists():
        return {}
    _, sites = read_dbf(site_dbf)
    return {
        str(row.get("REF_PLAQUE") or ""): row
        for row in sites
        if str(row.get("TYPE") or "").upper() == "NRO"
    }


def dbf_value(value: Any, kind: str, width: int, decimals: int) -> bytes:
    if value is None:
        return b" " * width
    if kind == "C":
        return str(value).encode("utf-8")[:width].ljust(width, b" ")
    if kind in {"N", "F"}:
        if decimals:
            text = f"{float(value):.{decimals}f}"
        else:
            text = str(int(value))
        return text.encode("ascii")[-width:].rjust(width, b" ")
    if kind == "L":
        return (b"T" if value else b"F").ljust(width, b" ")
    return str(value).encode("utf-8")[:width].ljust(width, b" ")


def write_dbf(path: Path, fields: list[tuple[str, str, int, int]], rows: list[dict[str, Any]]) -> None:
    header_size = 32 + len(fields) * 32 + 1
    record_size = 1 + sum(field[2] for field in fields)
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
            field = bytearray(32)
            field[:11] = name.encode("ascii")[:11].ljust(11, b"\0")
            field[11] = ord(kind)
            field[16] = width
            field[17] = decimals
            handle.write(field)
        handle.write(b"\r")
        for row in rows:
            handle.write(b" ")
            for name, kind, width, decimals in fields:
                handle.write(dbf_value(row.get(name), kind, width, decimals))
        handle.write(b"\x1a")


def shp_header(file_length_words: int, points: list[tuple[float, float]]) -> bytes:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    header = bytearray(100)
    header[:4] = struct.pack(">I", 9994)
    header[24:28] = struct.pack(">I", file_length_words)
    header[28:32] = struct.pack("<I", 1000)
    header[32:36] = struct.pack("<I", 1)
    header[36:68] = struct.pack("<dddd", min(xs), min(ys), max(xs), max(ys))
    return header


def write_point_shapefile(base: Path, points: list[tuple[float, float]]) -> None:
    record_count = len(points)
    shp_length_words = 50 + record_count * 14
    shx_length_words = 50 + record_count * 4
    with base.with_suffix(".shp").open("wb") as shp:
        shp.write(shp_header(shp_length_words, points))
        for index, point in enumerate(points, start=1):
            shp.write(struct.pack(">II", index, 10))
            shp.write(struct.pack("<Idd", 1, point[0], point[1]))
    with base.with_suffix(".shx").open("wb") as shx:
        shx.write(shp_header(shx_length_words, points))
        for index in range(record_count):
            shx.write(struct.pack(">II", 50 + index * 14, 10))


def plan(
    input_base: Path,
    boite_base: Path,
    site_dbf: Path,
    output_dir: Path,
    *,
    pbo_offset_m: float = PBO_IMB_OFFSET_M,
) -> tuple[list[dict[str, Any]], list[tuple[float, float]]]:
    fields, imbs = read_dbf(input_base.with_suffix(".dbf"))
    points = read_points(input_base.with_suffix(".shp"))
    if len(imbs) != len(points):
        raise ValueError("IMB geometry count and attribute count do not match.")
    boite_fields, _ = read_dbf(boite_base.with_suffix(".dbf"))
    field_names = {field[0] for field in boite_fields}
    required = {"CODE", "TYPE", "CAPACITE", "NB_LOGEMEN", "NB_FIBRE_U", "X", "Y"}
    if not required.issubset(field_names):
        raise ValueError("BOITE template does not contain the required fields.")

    nros = find_nro(site_dbf)
    fallback_nro = next(iter(nros.values()), {})
    selected_imbs = [
        (str(imb.get("CODE") or f"IMB-{index:04d}"), point)
        for index, (imb, point) in enumerate(zip(imbs, points), start=1)
        if int(imb.get("NB_LOC_TOT") or 0) > 0
    ]
    placements = plan_pbo_locations(selected_imbs, offset_m=pbo_offset_m)
    rows: list[dict[str, Any]] = []
    selected_points: list[tuple[float, float]] = []
    codes: set[str] = set()
    for index, (imb, point) in enumerate(zip(imbs, points), start=1):
        demand = int(imb.get("NB_LOC_TOT") or 0)
        if demand <= 0:
            continue
        plaque = str(imb.get("REF_PLAQUE") or "")
        nro = nros.get(plaque, fallback_nro)
        code = str(imb.get("BPE_CODE") or f"PBO-{plaque}-{index:04d}")
        if code in codes:
            raise ValueError(f"Duplicate PBO code: {code}")
        codes.add(code)
        placement = placements[str(imb.get("CODE") or f"IMB-{index:04d}")]
        capacity = port_capacity(demand)
        row = {
            "fid": len(rows) + 1,
            "CODE": code,
            "CODE_PTC": None,
            "REF_PLAQUE": plaque or None,
            "REF_NRO": nro.get("CODE"),
            "REF_PM": None,
            "TYPE": "PBO",
            "TYPE_STRUC": imb.get("TYPE_BATIM") or "BATIMENT",
            "MODE_POSE": pose_mode(imb.get("RACCORDEME")),
            "CAPACITE": capacity,
            "NB_LOGEMEN": demand,
            "NB_SPLICES": demand,
            "NB_FIBRE_U": demand,
            "FABRIQUANT": nro.get("FABRIQUANT"),
            "REF_BPE": f"PBO-{capacity}F",
            "NB_CASSETT": capacity // 12,
            "CABLE_AMON": None,
            "STATUT": imb.get("STATUT") or "EN PROJET",
            "PROPRIETAI": nro.get("PROPRIETAI"),
            "GESTIONNAI": nro.get("GESTIONNAI"),
            "ADRESSSE": address(imb),
            "VILLE": imb.get("VILLE"),
            "CODE_POSTA": imb.get("CODE_POSTA"),
            "X": placement["point"][0],
            "Y": placement["point"][1],
            "PBO_OFFSET_M": placement["offset_m"],
            "PBO_BEARING_DEG": placement["bearing_deg"],
            "COMMENT": f"IMB={imb.get('CODE')}; ports={demand}; cap={capacity}; offset_m={placement['offset_m']}; bearing_deg={placement['bearing_deg']}",
        }
        rows.append(row)
        selected_points.append(placement["point"])

    output_dir.mkdir(parents=True, exist_ok=True)
    output_base = output_dir / "BOITE_PBO"
    write_point_shapefile(output_base, selected_points)
    write_dbf(output_base.with_suffix(".dbf"), boite_fields, rows)
    shutil.copyfile(boite_base.with_suffix(".prj"), output_base.with_suffix(".prj"))
    output_base.with_suffix(".cpg").write_text("UTF-8", encoding="ascii")
    return rows, selected_points


def write_tables(output_dir: Path, rows: list[dict[str, Any]]) -> None:
    # The exact template field list is recovered from the generated DBF.
    fields, _ = read_dbf(output_dir / "BOITE_PBO.dbf")
    names = [field[0] for field in fields]
    names.extend(name for name in ("PBO_OFFSET_M", "PBO_BEARING_DEG") if any(row.get(name) is not None for row in rows))
    with (output_dir / "PBO_selection_table.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=names)
        writer.writeheader()
        writer.writerows(rows)

    try:
        from openpyxl import Workbook
    except ImportError:
        return
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "PBO"
    sheet.append(names)
    for row in rows:
        sheet.append([row.get(name) for name in names])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column in sheet.columns:
        letter = column[0].column_letter
        sheet.column_dimensions[letter].width = min(26, max(12, max(len(str(cell.value or "")) for cell in column) + 2))

    summary = workbook.create_sheet("Summary")
    total_demand = sum(int(row["NB_LOGEMEN"]) for row in rows)
    total_capacity = sum(int(row["CAPACITE"]) for row in rows)
    summary.append(["metric", "value"])
    summary.append(["pbo_count", len(rows)])
    summary.append(["served_ports", total_demand])
    summary.append(["planned_capacity", total_capacity])
    summary.append(["spare_ports", total_capacity - total_demand])
    summary.append(["capacity_rule", "ceil(NB_LOC_TOT / 12) * 12"])
    offsets = [row.get("PBO_OFFSET_M") for row in rows if row.get("PBO_OFFSET_M") is not None]
    summary.append(["placement_rule", "one PBO per positive-demand IMB on a non-overlapping offset ring"])
    summary.append(["pbo_imb_offset_m", min(offsets) if offsets else PBO_IMB_OFFSET_M])
    summary.append(["ref_pm", "blank: no PM record in supplied SITE layer"])
    summary.column_dimensions["A"].width = 22
    summary.column_dimensions["B"].width = 58
    workbook.save(output_dir / "PBO_selection_table.xlsx")


def distance_meters(first: tuple[float, float], second: tuple[float, float]) -> float:
    """Approximate short-distance WGS84 point separation in metres."""
    latitude = math.radians((first[1] + second[1]) / 2)
    longitude_delta = (first[0] - second[0]) * 111320 * math.cos(latitude)
    latitude_delta = (first[1] - second[1]) * 110540
    return math.hypot(longitude_delta, latitude_delta)


def point_records(base: Path) -> list[tuple[dict[str, Any], tuple[float, float]]]:
    _, rows = read_dbf(base.with_suffix(".dbf"))
    points = read_points(base.with_suffix(".shp"))
    if len(rows) != len(points):
        raise ValueError(f"Geometry/attribute count mismatch in {base}")
    return list(zip(rows, points))


def write_reference_comparison(
    imb_base: Path, auto_base: Path, reference_base: Path, output_dir: Path
) -> dict[str, Any]:
    """Compare generated PBOs to a completed BOITE design and plot all points."""
    imbs = [(row, point) for row, point in point_records(imb_base) if int(row.get("NB_LOC_TOT") or 0) > 0]
    auto = [(row, point) for row, point in point_records(auto_base) if row.get("TYPE") == "PBO"]
    reference = [(row, point) for row, point in point_records(reference_base) if row.get("TYPE") == "PBO"]
    if not auto or not reference:
        raise ValueError("Both the generated and reference BOITE layers must contain PBO features.")

    reference_by_code = {str(row.get("CODE")): (row, point) for row, point in reference}
    grouped_reference: list[list[tuple[dict[str, Any], tuple[float, float], float]]] = [[] for _ in auto]
    for reference_row, reference_point in reference:
        auto_index = min(
            range(len(auto)), key=lambda index: distance_meters(reference_point, auto[index][1])
        )
        grouped_reference[auto_index].append(
            (reference_row, reference_point, distance_meters(reference_point, auto[auto_index][1]))
        )

    comparison_rows: list[dict[str, Any]] = []
    exact_distances: list[float] = []
    for index, (auto_row, auto_point) in enumerate(auto):
        group = grouped_reference[index]
        exact_reference = reference_by_code.get(str(auto_row.get("CODE")))
        exact_distance = (
            distance_meters(auto_point, exact_reference[1]) if exact_reference is not None else None
        )
        if exact_distance is not None:
            exact_distances.append(exact_distance)
        reference_demand = sum(int(row.get("NB_LOGEMEN") or 0) for row, _, _ in group)
        reference_capacity = sum(int(row.get("CAPACITE") or 0) for row, _, _ in group)
        comparison_rows.append(
            {
                "AUTO_CODE": auto_row.get("CODE"),
                "AUTO_DEMAND": auto_row.get("NB_LOGEMEN"),
                "AUTO_CAPACITY": auto_row.get("CAPACITE"),
                "AUTO_X": auto_point[0],
                "AUTO_Y": auto_point[1],
                "REFERENCE_PBO_COUNT": len(group),
                "REFERENCE_CODES": ", ".join(str(row.get("CODE")) for row, _, _ in group),
                "REFERENCE_DEMAND": reference_demand,
                "REFERENCE_CAPACITY": reference_capacity,
                "DEMAND_DELTA": int(auto_row.get("NB_LOGEMEN") or 0) - reference_demand,
                "CAPACITY_DELTA": int(auto_row.get("CAPACITE") or 0) - reference_capacity,
                "EXACT_CODE_DISTANCE_M": exact_distance,
                "MAX_REFERENCE_DISTANCE_M": max((distance for _, _, distance in group), default=None),
            }
        )

    invalid_reference = [
        row for row, _ in reference if int(row.get("CAPACITE") or 0) < int(row.get("NB_LOGEMEN") or 0)
    ]
    summary = {
        "auto_pbo_count": len(auto),
        "reference_pbo_count": len(reference),
        "exact_code_matches": len(exact_distances),
        "reference_only_pbos": len(reference) - len(exact_distances),
        "exact_distance_min_m": min(exact_distances),
        "exact_distance_mean_m": sum(exact_distances) / len(exact_distances),
        "exact_distance_max_m": max(exact_distances),
        "reference_pbo_per_auto": dict(sorted(Counter(len(group) for group in grouped_reference).items())),
        "demand_delta_total": sum(int(row["DEMAND_DELTA"]) for row in comparison_rows),
        "capacity_delta_total": sum(int(row["CAPACITY_DELTA"]) for row in comparison_rows),
        "invalid_reference_pbos": len(invalid_reference),
    }

    csv_fields = list(comparison_rows[0])
    with (output_dir / "PBO_reference_comparison.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields)
        writer.writeheader()
        writer.writerows(comparison_rows)

    from openpyxl import Workbook

    workbook = Workbook()
    details = workbook.active
    details.title = "By_auto_PBO"
    details.append(csv_fields)
    for row in comparison_rows:
        details.append([row[field] for field in csv_fields])
    details.freeze_panes = "A2"
    details.auto_filter.ref = details.dimensions
    for column in details.columns:
        details.column_dimensions[column[0].column_letter].width = min(
            50, max(14, max(len(str(cell.value or "")) for cell in column) + 2)
        )
    overview = workbook.create_sheet("Summary")
    overview.append(["metric", "value"])
    for name, value in summary.items():
        overview.append([name, str(value) if isinstance(value, dict) else value])
    invalid_sheet = workbook.create_sheet("Reference_capacity_errors")
    invalid_headers = ["CODE", "CAPACITE", "NB_LOGEMEN", "NB_FIBRE_U", "REF_PM"]
    invalid_sheet.append(invalid_headers)
    for row in invalid_reference:
        invalid_sheet.append([row.get(name) for name in invalid_headers])
    overview.column_dimensions["A"].width = 31
    overview.column_dimensions["B"].width = 45
    workbook.save(output_dir / "PBO_reference_comparison.xlsx")

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FormatStrFormatter

    figure, axis = plt.subplots(figsize=(12, 6), constrained_layout=True)
    axis.scatter(
        [point[0] for _, point in imbs], [point[1] for _, point in imbs],
        marker="x", color="#1f2937", s=32, linewidths=1.1, label=f"IMB ({len(imbs)})", zorder=2,
    )
    for group, (_, auto_point) in zip(grouped_reference, auto):
        for _, reference_point, _ in group:
            axis.plot(
                [auto_point[0], reference_point[0]], [auto_point[1], reference_point[1]],
                color="#94a3b8", linewidth=0.55, alpha=0.65, zorder=1,
            )
    axis.scatter(
        [point[0] for _, point in reference], [point[1] for _, point in reference],
        marker="^", color="#dc2626", s=24, alpha=0.8, label=f"Reference PBO ({len(reference)})", zorder=3,
    )
    axis.scatter(
        [point[0] for _, point in auto], [point[1] for _, point in auto],
        marker="o", facecolors="none", edgecolors="#2563eb", s=82, linewidths=1.4,
        label=f"Auto PBO ({len(auto)})", zorder=4,
    )
    axis.set_title("PBO location comparison")
    axis.set_xlabel("Longitude")
    axis.set_ylabel("Latitude")
    axis.set_aspect("equal", adjustable="box")
    axis.xaxis.set_major_formatter(FormatStrFormatter("%.5f"))
    axis.yaxis.set_major_formatter(FormatStrFormatter("%.5f"))
    axis.grid(True, alpha=0.25)
    axis.legend(loc="best")
    figure.savefig(output_dir / "PBO_location_comparison.png", dpi=220)
    plt.close(figure)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate BOITE/PBO layer from IMB points.")
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=DEFAULT_SHAPE_DIR / "IMB.shp",
        help=f"Input IMB .shp path (default: {DEFAULT_SHAPE_DIR / 'IMB.shp'})",
    )
    parser.add_argument(
        "boite_template",
        nargs="?",
        type=Path,
        default=DEFAULT_SHAPE_DIR / "BOITE.shp",
        help=f"BOITE template .shp path (default: {DEFAULT_SHAPE_DIR / 'BOITE.shp'})",
    )
    parser.add_argument(
        "site",
        nargs="?",
        type=Path,
        default=DEFAULT_SHAPE_DIR / "SITE.shp",
        help=f"SITE .shp path, used to populate NRO attributes (default: {DEFAULT_SHAPE_DIR / 'SITE.shp'})",
    )
    parser.add_argument(
        "output_dir",
        nargs="?",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for generated outputs (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--reference-boite", type=Path,
        help="Optional completed BOITE .shp for point-map and attribute comparison",
    )
    parser.add_argument(
        "--pbo-offset-m",
        type=float,
        default=PBO_IMB_OFFSET_M,
        help=f"Offset PBO from its served IMB in metres (default: {PBO_IMB_OFFSET_M})",
    )
    args = parser.parse_args()
    if args.pbo_offset_m <= 0:
        parser.error("--pbo-offset-m must be positive")
    if not args.input.exists():
        from build_sample_data import build_sample_shape_dir

        build_sample_shape_dir(DEFAULT_SHAPE_DIR)
    rows, _ = plan(
        args.input.with_suffix(""),
        args.boite_template.with_suffix(""),
        args.site.with_suffix(".dbf"),
        args.output_dir,
        pbo_offset_m=args.pbo_offset_m,
    )
    write_tables(args.output_dir, rows)
    total_demand = sum(int(row["NB_LOGEMEN"]) for row in rows)
    total_capacity = sum(int(row["CAPACITE"]) for row in rows)
    print(f"Generated {len(rows)} PBOs; demand={total_demand}; capacity={total_capacity}; spare={total_capacity-total_demand}")
    if args.reference_boite:
        summary = write_reference_comparison(
            args.input.with_suffix(""), args.output_dir / "BOITE_PBO", args.reference_boite.with_suffix(""), args.output_dir
        )
        print(
            "Reference comparison: "
            f"reference_pbos={summary['reference_pbo_count']}; "
            f"max_exact_distance_m={summary['exact_distance_max_m']:.2f}; "
            f"invalid_reference_pbos={summary['invalid_reference_pbos']}"
        )


if __name__ == "__main__":
    main()
