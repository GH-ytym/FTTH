"""Materialize the current project's surveyed layers for the Task-4 planner.

Task 4 historically consumed a checked-in Shapefile directory.  That made it
easy for the planner to silently use a different IMB set than Task 2.  This
module keeps the Shapefile implementation, but makes the hand-off explicit:
IMB comes from Task 2, SITE and PTECH come from the same Task-1 topology, and
BOITE is created empty.  Bundled data supplies DBF/CRS *schemas only*; no
bundled feature record is copied into a live run.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

from generate_pbo import write_dbf, write_point_shapefile
from inspect_shapefile import read_dbf


SCHEMA_VERSION = "task4.input_contract.v2"


def _number(value: Any, label: str) -> float:
    if value in (None, ""):
        raise ValueError(f"Task-2 IMB is missing {label}")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Task-2 IMB has invalid {label}: {value!r}") from exc


def _integer(value: Any, label: str) -> int:
    if value in (None, ""):
        raise ValueError(f"Task-2 IMB is missing {label}")
    try:
        return int(round(float(value)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Task-2 IMB has invalid {label}: {value!r}") from exc


def _imb_rows(task2_payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[tuple[float, float]]]:
    raw_nodes = task2_payload.get("nodes") or task2_payload.get("imb_points") or []
    candidates = [item for item in raw_nodes if isinstance(item, dict)]
    typed = [item for item in candidates if str(item.get("type") or item.get("TYPE") or "").upper() == "IMB"]
    nodes = typed or candidates
    rows: list[dict[str, Any]] = []
    points: list[tuple[float, float]] = []
    seen: set[str] = set()
    for index, item in enumerate(nodes, start=1):
        code = str(item.get("code") or item.get("CODE") or item.get("id") or "").strip()
        if not code:
            raise ValueError(f"Task-2 IMB #{index} has no code/id")
        if code in seen:
            raise ValueError(f"Task-2 contains duplicate IMB code: {code}")
        seen.add(code)
        point = (_number(item.get("x", item.get("X")), "x"), _number(item.get("y", item.get("Y")), "y"))
        demand = _integer(
            item.get("ports", item.get("nb_loc_tot", item.get("NB_LOC_TOT", item.get("demand")))),
            f"ports for {code}",
        )
        if demand <= 0:
            raise ValueError(f"Task-2 IMB {code} must have positive demand")
        passthrough = item.get("task4_attributes") if isinstance(item.get("task4_attributes"), dict) else {}
        rows.append(
            {
                **{str(key).upper(): value for key, value in passthrough.items()},
                "fid": index,
                "CODE": code,
                # The legacy Task-4 schema has no NODE_ID column.  Preserve
                # the Task-2 internal id in the unused BPE_CODE slot so the
                # partition adapter can resolve node-id membership without
                # changing the published Shapefile field layout.
                "BPE_CODE": str(item.get("id") or item.get("ID") or ""),
                "REF_PLAQUE": str(item.get("ref_plaque") or item.get("REF_PLAQUE") or ""),
                "NB_LOC_RES": int(item.get("nb_loc_res") or item.get("NB_LOC_RES") or demand),
                "NB_LOC_PRO": int(item.get("nb_loc_pro") or item.get("NB_LOC_PRO") or 0),
                "NB_LOC_TOT": demand,
                "TYPE_BATIM": str(item.get("type_batim") or item.get("TYPE_BATIM") or "IMB"),
                "RACCORDEME": str(item.get("raccordeme") or item.get("RACCORDEME") or ""),
                "TYPE_VOIE": str(item.get("type_voie") or item.get("TYPE_VOIE") or ""),
                "NOM_VOIE": str(item.get("nom_voie") or item.get("NOM_VOIE") or ""),
                "NOM_BATIME": str(item.get("nom_batime") or item.get("NOM_BATIME") or code),
                "NUMERO_VOI": int(item.get("numero_voi") or item.get("NUMERO_VOI") or index),
                "VILLE": str(item.get("ville") or item.get("VILLE") or ""),
                "CODE_POSTA": int(item.get("code_posta") or item.get("CODE_POSTA") or 0),
                "X": point[0],
                "Y": point[1],
            }
        )
        points.append(point)
    if not rows:
        raise ValueError("Task-2 input contains no IMB nodes")
    return rows, points


def _empty_point_shapefile(base: Path) -> None:
    """Write valid zero-feature Point SHP/SHX files.

    The unchanged Task-4 writer cannot create a zero-record shapefile, while
    BOITE must deliberately be empty so example boxes never enter planning.
    """
    header = bytearray(100)
    struct.pack_into(">i", header, 0, 9994)
    struct.pack_into(">i", header, 24, 50)
    struct.pack_into("<i", header, 28, 1000)
    struct.pack_into("<i", header, 32, 1)
    for offset in (36, 44, 52, 60, 68, 76, 84, 92):
        struct.pack_into("<d", header, offset, 0.0)
    base.with_suffix(".shp").write_bytes(header)
    base.with_suffix(".shx").write_bytes(header)


def _schema(template: Path, layer: str) -> list[tuple[str, str, int, int]]:
    source = template / f"{layer}.dbf"
    if not source.is_file():
        raise FileNotFoundError(f"Task-4 schema is missing {layer}.dbf: {source}")
    fields, _ = read_dbf(source)
    return fields


def _normalise_row(
    source: dict[str, Any],
    fields: list[tuple[str, str, int, int]],
    *,
    fid: int,
    code: str,
    point: tuple[float, float],
) -> dict[str, Any]:
    attrs = source.get("task4_attributes") if isinstance(source.get("task4_attributes"), dict) else {}
    values = {str(key).upper(): value for key, value in attrs.items()}
    # ``source['type']`` is the topology type (PETCH/IMB), not the surveyed
    # SITE/PTECH business TYPE.  Never let it overwrite the original row.
    values.setdefault("TYPE", source.get("original_type") or "")
    values.update({"FID": fid, "CODE": code, "X": point[0], "Y": point[1]})
    aliases = {
        "REF_PRODUI": ("REF_PRODUIT",),
        "PROPRIETAI": ("PROPRIETAIRE", "PROPRETAI"),
        "GESTIONNAI": ("GESTIONNAIRE",),
        "ADRESSSE": ("ADRESSE",),
        "CODE_POSTA": ("CODE_POSTAL",),
    }
    row: dict[str, Any] = {"fid": fid}
    for name, kind, _width, _decimals in fields:
        if name == "fid":
            continue
        value = values.get(name)
        if value in (None, ""):
            for alias in aliases.get(name, ()):
                if values.get(alias) not in (None, ""):
                    value = values[alias]
                    break
        if value in (None, ""):
            value = 0 if kind in {"N", "F"} else ""
        row[name] = value
    return row


def _project_rows(
    topology: dict[str, Any], layer: str, fields: list[tuple[str, str, int, int]]
) -> tuple[list[dict[str, Any]], list[tuple[float, float]]]:
    selected = [
        node for node in topology.get("nodes") or []
        if isinstance(node, dict) and str(node.get("source_layer") or "").upper() == layer
    ]
    rows: list[dict[str, Any]] = []
    points: list[tuple[float, float]] = []
    for index, node in enumerate(selected, start=1):
        code = str(node.get("code") or "").strip()
        if not code:
            raise ValueError(f"Current-project {layer} #{index} has no CODE")
        point = (_number(node.get("x"), f"{layer}.{code}.x"), _number(node.get("y"), f"{layer}.{code}.y"))
        rows.append(_normalise_row(node, fields, fid=index, code=code, point=point))
        points.append(point)
    return rows, points


def _write_layer(
    output: Path,
    template: Path,
    layer: str,
    fields: list[tuple[str, str, int, int]],
    rows: list[dict[str, Any]],
    points: list[tuple[float, float]],
) -> None:
    base = output / layer
    if points:
        write_point_shapefile(base, points)
    else:
        _empty_point_shapefile(base)
    write_dbf(base.with_suffix(".dbf"), fields, rows)
    prj = template / f"{layer}.prj"
    base.with_suffix(".prj").write_text(prj.read_text(encoding="utf-8") if prj.is_file() else "", encoding="utf-8")
    base.with_suffix(".cpg").write_text("UTF-8", encoding="ascii")


def materialize_task4_shape_dir(
    task2_input: str | Path,
    project_topology: str | Path,
    template_dir: str | Path,
    output_dir: str | Path,
) -> Path:
    """Create Task-4 inputs exclusively from the current project.

    ``template_dir`` supplies field definitions and projection text only.  Its
    feature records are never copied.  ``project_topology`` is the current
    Task-1 hand-off and is the sole source of SITE and PTECH features.
    """
    task2_path = Path(task2_input).resolve()
    topology_path = Path(project_topology).resolve()
    template = Path(template_dir).resolve()
    output = Path(output_dir).resolve()
    if not task2_path.is_file():
        raise FileNotFoundError(f"Task-2 input was not found: {task2_path}")
    if not topology_path.is_file():
        raise FileNotFoundError(f"Current-project topology was not found: {topology_path}")
    if not template.is_dir():
        raise FileNotFoundError(f"Task-4 template directory was not found: {template}")
    if output == template:
        raise ValueError("Task-4 materialized output must be separate from the template directory")

    payload = json.loads(task2_path.read_text(encoding="utf-8-sig"))
    topology = json.loads(topology_path.read_text(encoding="utf-8-sig"))
    imb_rows, imb_points = _imb_rows(payload)
    site_fields = _schema(template, "SITE")
    ptech_fields = _schema(template, "PTECH")
    boite_fields = _schema(template, "BOITE")
    site_rows, site_points = _project_rows(topology, "SITE", site_fields)
    ptech_rows, ptech_points = _project_rows(topology, "PTECH", ptech_fields)
    nro_codes = [str(row.get("CODE") or "") for row in site_rows if str(row.get("TYPE") or "").upper() == "NRO"]
    if len(nro_codes) != 1:
        raise ValueError(f"当前项目 SITE 必须且只能包含一个 TYPE=NRO；实际为 {len(nro_codes)} 个：{nro_codes}")

    output.mkdir(parents=True, exist_ok=True)
    _write_layer(output, template, "IMB", _schema(template, "IMB"), imb_rows, imb_points)
    _write_layer(output, template, "SITE", site_fields, site_rows, site_points)
    _write_layer(output, template, "PTECH", ptech_fields, ptech_rows, ptech_points)
    _write_layer(output, template, "BOITE", boite_fields, [], [])

    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "task2_input": str(task2_path),
        "project_topology": str(topology_path),
        "schema_template_dir": str(template),
        "template_usage": "schema_and_crs_only",
        "sample_feature_records_used": False,
        "coordinate_system": meta.get("coordinate_system") or "EPSG:4326",
        "axis_order": meta.get("axis_order") or "x=longitude,y=latitude",
        "imb_codes": [row["CODE"] for row in imb_rows],
        "site_codes": [row["CODE"] for row in site_rows],
        "nro_codes": nro_codes,
        "ptech_codes": [row["CODE"] for row in ptech_rows],
        "imb_count": len(imb_rows),
        "site_count": len(site_rows),
        "ptech_count": len(ptech_rows),
        "boite_count": 0,
        "total_ports": sum(int(row["NB_LOC_TOT"]) for row in imb_rows),
    }
    (output / "task4_input_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return output


__all__ = ["SCHEMA_VERSION", "materialize_task4_shape_dir"]
