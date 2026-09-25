"""Build a small runnable Shape dataset for task 4.

The task 4 planners operate on existing IMB/BOITE/SITE/PTECH shapefiles.  The
repository does not include those production shapefiles, so this module creates
a local sample set from the current Task-6 production input while preserving
the Task-4 planning logic unchanged.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from generate_pbo import port_capacity, write_dbf, write_point_shapefile
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from project_paths import TASK4_SAMPLE_INPUT_DIR, TASK6_PRODUCTION_INPUT


TASK_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TASK_DIR.parent
DEFAULT_SOURCE_JSON = TASK6_PRODUCTION_INPUT
DEFAULT_SHAPE_DIR = TASK4_SAMPLE_INPUT_DIR

WGS84_PRJ = (
    'GEOGCS["WGS 84",DATUM["WGS_1984",'
    'SPHEROID["WGS 84",6378137,298.257223563]],'
    'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433],'
    'AUTHORITY["EPSG","4326"]]'
)

IMB_FIELDS = [
    ("fid", "N", 8, 0),
    ("CODE", "C", 32, 0),
    ("BPE_CODE", "C", 32, 0),
    ("REF_PLAQUE", "C", 20, 0),
    ("NB_LOC_RES", "N", 8, 0),
    ("NB_LOC_PRO", "N", 8, 0),
    ("NB_LOC_TOT", "N", 8, 0),
    ("TYPE_BATIM", "C", 20, 0),
    ("RACCORDEME", "C", 30, 0),
    ("TYPE_VOIE", "C", 20, 0),
    ("NOM_VOIE", "C", 40, 0),
    ("NOM_BATIME", "C", 40, 0),
    ("NUMERO_VOI", "N", 8, 0),
    ("VILLE", "C", 30, 0),
    ("CODE_POSTA", "N", 8, 0),
    ("X", "F", 18, 8),
    ("Y", "F", 18, 8),
]

BOITE_FIELDS = [
    ("fid", "N", 8, 0),
    ("CODE", "C", 32, 0),
    ("CODE_PTC", "C", 32, 0),
    ("REF_PLAQUE", "C", 20, 0),
    ("REF_NRO", "C", 32, 0),
    ("REF_PM", "C", 32, 0),
    ("TYPE", "C", 12, 0),
    ("TYPE_STRUC", "C", 20, 0),
    ("MODE_POSE", "C", 24, 0),
    ("CAPACITE", "N", 8, 0),
    ("NB_LOGEMEN", "N", 8, 0),
    ("NB_SPLICES", "N", 8, 0),
    ("NB_FIBRE_U", "N", 8, 0),
    ("FABRIQUANT", "C", 20, 0),
    ("REF_BPE", "C", 24, 0),
    ("NB_CASSETT", "N", 8, 0),
    ("CABLE_AMON", "C", 32, 0),
    ("STATUT", "C", 20, 0),
    ("PROPRIETAI", "C", 24, 0),
    ("GESTIONNAI", "C", 24, 0),
    ("ADRESSSE", "C", 80, 0),
    ("VILLE", "C", 30, 0),
    ("CODE_POSTA", "N", 8, 0),
    ("X", "F", 18, 8),
    ("Y", "F", 18, 8),
    ("COMMENT", "C", 120, 0),
]

SITE_FIELDS = [
    ("fid", "N", 8, 0),
    ("CODE", "C", 32, 0),
    ("REF_PLAQUE", "C", 20, 0),
    ("REF_NRO", "C", 32, 0),
    ("TYPE", "C", 12, 0),
    ("FABRIQUANT", "C", 20, 0),
    ("REF_PRODUI", "C", 24, 0),
    ("MODE_POSE", "C", 24, 0),
    ("STATUT", "C", 20, 0),
    ("PROPRIETAI", "C", 24, 0),
    ("GESTIONNAI", "C", 24, 0),
    ("ADRESSSE", "C", 80, 0),
    ("VILLE", "C", 30, 0),
    ("CODE_POSTA", "N", 8, 0),
    ("X", "F", 18, 8),
    ("Y", "F", 18, 8),
]

PTECH_FIELDS = [
    ("fid", "N", 8, 0),
    ("CODE", "C", 32, 0),
    ("NOM", "C", 40, 0),
    ("REF_PLAQUE", "C", 20, 0),
    ("TYPE", "C", 24, 0),
    ("NATURE", "C", 20, 0),
    ("HAUTEUR_AP", "F", 12, 2),
    ("TYPE_APPUI", "C", 20, 0),
    ("EFFORT_APP", "F", 12, 2),
    ("NB_BOITIER", "N", 8, 0),
    ("STATUT", "C", 20, 0),
    ("PROPRIETAI", "C", 24, 0),
    ("GESTIONNAI", "C", 24, 0),
    ("ADRESSSE", "C", 80, 0),
    ("VILLE", "C", 30, 0),
    ("CODE_POSTA", "N", 8, 0),
    ("X", "F", 18, 8),
    ("Y", "F", 18, 8),
    ("COMMENT", "C", 120, 0),
]


def _write_layer(
    output_dir: Path,
    name: str,
    fields: list[tuple[str, str, int, int]],
    rows: list[dict[str, Any]],
    points: list[tuple[float, float]],
) -> None:
    base = output_dir / name
    write_point_shapefile(base, points)
    write_dbf(base.with_suffix(".dbf"), fields, rows)
    base.with_suffix(".prj").write_text(WGS84_PRJ, encoding="ascii")
    base.with_suffix(".cpg").write_text("UTF-8", encoding="ascii")


def _centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _pm_code(index: int) -> str:
    return f"PM-JAD-MAR-{index:04d}"


def build_sample_shape_dir(
    output_dir: Path = DEFAULT_SHAPE_DIR,
    source_json: Path = DEFAULT_SOURCE_JSON,
) -> Path:
    if not source_json.exists():
        raise FileNotFoundError(f"Task 4 sample source JSON was not found: {source_json}")

    data = json.loads(source_json.read_text(encoding="utf-8"))
    imbs = list(data.get("imb_points") or [])
    if not imbs:
        raise ValueError(f"No imb_points were found in {source_json}")

    output_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(imbs, key=lambda item: (float(item["x"]), float(item["y"])))
    western_codes = {str(item["code"]) for item in ordered[: len(ordered) // 2]}

    points_by_pm: dict[str, list[tuple[float, float]]] = {_pm_code(1): [], _pm_code(2): []}
    imb_rows: list[dict[str, Any]] = []
    imb_points: list[tuple[float, float]] = []
    boite_rows: list[dict[str, Any]] = []
    boite_points: list[tuple[float, float]] = []

    for index, item in enumerate(imbs, start=1):
        point = (float(item["x"]), float(item["y"]))
        demand = int(item["ports"])
        if demand > 96:
            raise ValueError(f"Sample IMB demand exceeds task 4 PBO capacity: {item['code']}={demand}")
        pm_code = _pm_code(1) if str(item["code"]) in western_codes else _pm_code(2)
        points_by_pm[pm_code].append(point)
        pbo_code = f"PBO-REF-JAD-MAR-{index:04d}"
        address = f"Rue demo {index:03d} {item['code']}"
        imb_rows.append(
            {
                "fid": index,
                "CODE": item["code"],
                "BPE_CODE": pbo_code,
                "REF_PLAQUE": "JAD-MAR",
                "NB_LOC_RES": demand,
                "NB_LOC_PRO": 0,
                "NB_LOC_TOT": demand,
                "TYPE_BATIM": "BATIMENT",
                "RACCORDEME": "COLONNE MONTANTE",
                "TYPE_VOIE": "RUE",
                "NOM_VOIE": "DEMO",
                "NOM_BATIME": item["code"],
                "NUMERO_VOI": index,
                "VILLE": "ELJADIDA",
                "CODE_POSTA": 24000,
                "X": point[0],
                "Y": point[1],
            }
        )
        imb_points.append(point)
        capacity = port_capacity(demand)
        boite_rows.append(
            {
                "fid": len(boite_rows) + 1,
                "CODE": pbo_code,
                "CODE_PTC": f"PTC-REF-PBO-{index:04d}",
                "REF_PLAQUE": "JAD-MAR",
                "REF_NRO": "NRO-JAD-MAR-0001",
                "REF_PM": pm_code,
                "TYPE": "PBO",
                "TYPE_STRUC": "DISTRIBUTION",
                "MODE_POSE": "IMMEUBLE",
                "CAPACITE": capacity,
                "NB_LOGEMEN": demand,
                "NB_SPLICES": demand,
                "NB_FIBRE_U": demand,
                "FABRIQUANT": "FBR",
                "REF_BPE": f"FBR-BPO{capacity}",
                "NB_CASSETT": max(1, capacity // 12),
                "STATUT": "REFERENCE",
                "PROPRIETAI": "UNIFIBER",
                "GESTIONNAI": "UNIFIBER",
                "ADRESSSE": address,
                "VILLE": "ELJADIDA",
                "CODE_POSTA": 24000,
                "X": point[0],
                "Y": point[1],
                "COMMENT": "reference PBO for runnable task 4 sample",
            }
        )
        boite_points.append(point)

    pm_centers = {code: _centroid(points) for code, points in points_by_pm.items()}
    nro_point = _centroid(list(pm_centers.values()))
    site_rows = [
        {
            "fid": 1,
            "CODE": "NRO-JAD-MAR-0001",
            "REF_PLAQUE": "JAD-MAR",
            "REF_NRO": "NRO-JAD-MAR-0001",
            "TYPE": "NRO",
            "FABRIQUANT": "FBR",
            "STATUT": "EXISTANT",
            "PROPRIETAI": "UNIFIBER",
            "GESTIONNAI": "UNIFIBER",
            "ADRESSSE": "Task 4 sample NRO",
            "VILLE": "ELJADIDA",
            "CODE_POSTA": 24000,
            "X": nro_point[0],
            "Y": nro_point[1],
        }
    ]
    site_points = [nro_point]
    for index, code in enumerate(sorted(pm_centers), start=2):
        point = pm_centers[code]
        site_rows.append(
            {
                "fid": index,
                "CODE": code,
                "REF_PLAQUE": "JAD-MAR",
                "REF_NRO": "NRO-JAD-MAR-0001",
                "TYPE": "PM",
                "FABRIQUANT": "FBR",
                "STATUT": "REFERENCE",
                "PROPRIETAI": "UNIFIBER",
                "GESTIONNAI": "UNIFIBER",
                "ADRESSSE": f"Task 4 sample {code}",
                "VILLE": "ELJADIDA",
                "CODE_POSTA": 24000,
                "X": point[0],
                "Y": point[1],
            }
        )
        site_points.append(point)

    for index, code in enumerate(sorted(pm_centers), start=1):
        point = pm_centers[code]
        demand = sum(
            int(row["NB_LOC_TOT"])
            for row in imb_rows
            if (_pm_code(1) if row["CODE"] in western_codes else _pm_code(2)) == code
        )
        boite_rows.append(
            {
                "fid": len(boite_rows) + 1,
                "CODE": f"BPE-REF-JAD-MAR-{index:04d}",
                "CODE_PTC": f"PTC-REF-BPE-{index:04d}",
                "REF_PLAQUE": "JAD-MAR",
                "REF_NRO": "NRO-JAD-MAR-0001",
                "REF_PM": code,
                "TYPE": "BPE",
                "TYPE_STRUC": "DISTRIBUTION",
                "MODE_POSE": "CHAMBRE",
                "CAPACITE": 144,
                "NB_LOGEMEN": demand,
                "NB_SPLICES": 0,
                "NB_FIBRE_U": demand,
                "FABRIQUANT": "FBR",
                "REF_BPE": "FBR-BPE144",
                "NB_CASSETT": 12,
                "STATUT": "REFERENCE",
                "PROPRIETAI": "UNIFIBER",
                "GESTIONNAI": "UNIFIBER",
                "ADRESSSE": "Task 4 sample BPE",
                "VILLE": "ELJADIDA",
                "CODE_POSTA": 24000,
                "X": point[0],
                "Y": point[1],
                "COMMENT": "reference BPE for map comparison",
            }
        )
        boite_points.append(point)

    ptech_rows = []
    ptech_points = []
    for index, (code, point, kind) in enumerate(
        [
            ("PTC-REF-NRO-0001", nro_point, "CHAMBRE"),
            ("PTC-REF-PM-0001", pm_centers[_pm_code(1)], "CHAMBRE"),
            ("PTC-REF-PM-0002", pm_centers[_pm_code(2)], "CHAMBRE"),
        ],
        start=1,
    ):
        ptech_rows.append(
            {
                "fid": index,
                "CODE": code,
                "NOM": code,
                "REF_PLAQUE": "JAD-MAR",
                "TYPE": kind,
                "NATURE": "REFERENCE",
                "HAUTEUR_AP": 0.0,
                "TYPE_APPUI": "NA",
                "EFFORT_APP": 0.0,
                "NB_BOITIER": 1,
                "STATUT": "REFERENCE",
                "PROPRIETAI": "UNIFIBER",
                "GESTIONNAI": "UNIFIBER",
                "ADRESSSE": "Task 4 sample PTECH",
                "VILLE": "ELJADIDA",
                "CODE_POSTA": 24000,
                "X": point[0],
                "Y": point[1],
                "COMMENT": "template and reference PTECH",
            }
        )
        ptech_points.append(point)

    _write_layer(output_dir, "IMB", IMB_FIELDS, imb_rows, imb_points)
    _write_layer(output_dir, "BOITE", BOITE_FIELDS, boite_rows, boite_points)
    _write_layer(output_dir, "SITE", SITE_FIELDS, site_rows, site_points)
    _write_layer(output_dir, "PTECH", PTECH_FIELDS, ptech_rows, ptech_points)
    return output_dir


def main() -> None:
    shape_dir = build_sample_shape_dir()
    print(f"Task 4 sample shapefiles written to: {shape_dir}")


if __name__ == "__main__":
    main()
