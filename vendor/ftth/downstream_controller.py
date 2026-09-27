"""Batch-scoped FTTH(4) Task-2-to-6 pipeline controller.

The controller consumes only artifacts produced inside the current staged run.
It never reads bundled example outputs.  Potentially unsafe business choices are
explicit arguments recorded in the returned audit document.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable


def _engine_root() -> Path:
    return Path(__file__).resolve().parent / "engine"


@contextmanager
def _import_path(path: Path):
    text = str(path.resolve())
    added = text not in sys.path
    if added:
        sys.path.insert(0, text)
    try:
        yield
    finally:
        if added and text in sys.path:
            sys.path.remove(text)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载算法模块：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是对象：{path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _read_csv(path: Path) -> list[dict[str, Any]]:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                return [
                    {str(key or "").strip().upper(): value for key, value in row.items()}
                    for row in csv.DictReader(handle)
                ]
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法读取 CSV：{path}")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


ALIASES = {
    "TYPE_BATIM": ("TYPE_BATIMENT",),
    "RACCORDEME": ("RACCORDEMENT",),
    "NOM_BATIME": ("NOM_BATIMENT",),
    "NUMERO_VOI": ("NUMERO_VOIE",),
    "CODE_POSTA": ("CODE_POSTAL",),
    "PROPRIETAI": ("PROPRIETAIRE", "PROPRETAI"),
    "GESTIONNAI": ("GESTIONNAIRE",),
    "REF_PRODUI": ("REF_PRODUIT",),
    "ADRESSSE": ("ADRESSE",),
}


def _shape_row(source: dict[str, Any], fields: list[tuple[str, str, int, int]], fid: int) -> dict[str, Any]:
    values = {str(key).upper(): value for key, value in source.items()}
    result: dict[str, Any] = {"fid": fid}
    for name, kind, _width, _decimals in fields:
        if name == "fid":
            continue
        value = values.get(name)
        if value in (None, ""):
            for alias in ALIASES.get(name, ()):
                if values.get(alias) not in (None, ""):
                    value = values[alias]
                    break
        if value in (None, ""):
            value = 0 if kind in {"N", "F"} else "A_VERIFIER"
        result[name] = value
    return result


def run_task4(
    stage: Path,
    task2_input: Path,
    project_topology: Path,
    *,
    fixed_pm_path: Path | None,
    output_name: str,
    pbo_offset_m: float,
) -> dict[str, Any]:
    """Materialize this run's IMB/SITE/PTECH and execute one Task-4 pass."""
    runtime = _engine_root() / "task4"
    shape_dir = stage / "task4" / "input_shapes"
    output_dir = stage / "task4" / output_name
    partition_path = stage / "task2" / "qgis_direct_region_partition_result.json"

    task2_runtime = _engine_root() / "task2"
    with _import_path(task2_runtime), _import_path(runtime):
        template = runtime / "data" / "completed_example"
        contract = _load(runtime / "task4_input_contract.py", f"ftth_v220_task4_contract_{output_name}")
        if not shape_dir.is_dir():
            contract.materialize_task4_shape_dir(task2_input, project_topology, template, shape_dir)
        planner = _load(runtime / "plan_pbo_bpe.py", f"ftth_v220_task4_planner_{output_name}")
        pbos, bpes, mappings = planner.plan(
            shape_dir,
            stage / "task4" / "partition",
            fixed_pm_path=fixed_pm_path,
            task2_partition_path=partition_path,
            pbo_offset_m=float(pbo_offset_m),
        )
        planner.write_outputs(shape_dir, output_dir, pbos, bpes, mappings)

    required = [
        output_dir / "BOITE_ALGORITHM.shp",
        output_dir / "PTECH_ALGORITHM.shp",
        output_dir / "SITE_PM_PARTITION.shp",
        output_dir / "IMB_PBO_BPE_mapping.csv",
        output_dir / "BPE_cluster_members.csv",
        output_dir / "BPE_cluster_validation.json",
        output_dir / "PBO_placement_validation.json",
    ]
    missing = [str(path) for path in required if not path.is_file() or not path.stat().st_size]
    if missing:
        raise ValueError(f"任务四成果不完整：{missing}")
    return {
        "status": "success",
        "pass": output_name,
        "input_shape_dir": str(shape_dir),
        "output_dir": str(output_dir),
        "pbo_count": len(pbos),
        "bpe_count": len(bpes),
        "pbo_offset_m": float(pbo_offset_m),
        "layers": [
            {"group": "04 设备规划", "name": "PBO与BPE", "path": str(output_dir / "BOITE_ALGORITHM.shp")},
            {"group": "04 设备规划", "name": "任务四新增PTECH", "path": str(output_dir / "PTECH_ALGORITHM.shp")},
            {"group": "04 设备规划", "name": "NRO与PM", "path": str(output_dir / "SITE_PM_PARTITION.shp")},
        ],
    }


def materialize_task3_input(
    task2_payload: dict[str, Any],
    regions: dict[str, Any],
    output_dir: Path,
) -> Path:
    """Create the Task-3 CSV contract from this batch's Task-2 membership."""
    output_dir.mkdir(parents=True, exist_ok=True)
    zpm_by_imb: dict[str, str] = {}
    znro_by_zpm: dict[str, str] = {}
    for region in regions.get("zpm_regions") or []:
        zpm_id = str(region.get("id") or "")
        if not zpm_id:
            continue
        znro_by_zpm[zpm_id] = str(region.get("parent_znro_id") or "")
        for value in region.get("imb_ids") or []:
            zpm_by_imb[str(value)] = zpm_id
    rows = []
    candidates = [item for item in task2_payload.get("nodes") or task2_payload.get("imb_points") or [] if isinstance(item, dict)]
    typed = [item for item in candidates if str(item.get("type") or item.get("TYPE") or "").upper() == "IMB"]
    for node in typed or candidates:
        internal_id = str(node.get("id") or node.get("ID") or node.get("code") or node.get("CODE") or "")
        code = str(node.get("code") or node.get("CODE") or internal_id)
        zpm_id = zpm_by_imb.get(internal_id)
        if not zpm_id:
            raise ValueError(f"任务二分区未给 IMB {code} 分配 ZPM")
        ports = int(round(float(node.get("ports") or node.get("nb_loc_tot") or node.get("NB_LOC_TOT") or 0)))
        if ports <= 0:
            raise ValueError(f"IMB {code} 没有正数端口需求")
        rows.append({
            "CODE": code,
            "NB_LOC_RES": int(node.get("nb_loc_res") or node.get("NB_LOC_RES") or ports),
            "NB_LOC_PRO": int(node.get("nb_loc_pro") or node.get("NB_LOC_PRO") or 0),
            "NB_LOC_TOT": ports,
            "X": node.get("x", node.get("X")),
            "Y": node.get("y", node.get("Y")),
            "NODE_CODE": internal_id,
            "ZNRO_CODE": znro_by_zpm.get(zpm_id, ""),
            "ZPM_CODE": zpm_id,
        })
    if not rows:
        raise ValueError("任务二结果没有可传给任务三的 IMB")
    _write_csv(
        output_dir / "IMB.csv",
        rows,
        ["CODE", "NB_LOC_RES", "NB_LOC_PRO", "NB_LOC_TOT", "X", "Y", "NODE_CODE", "ZNRO_CODE", "ZPM_CODE"],
    )
    _write_json(
        output_dir / "service_unit_map.json",
        {"schema_version": "ftth.service-unit-map.v1", "unit_to_imb": {row["CODE"]: row["CODE"] for row in rows}},
    )
    return output_dir


def _initial_fixed_plan_from_task4(stage: Path, task4_output: Path) -> Path:
    """Represent Task-4's initial PMs in the fixed-PM adapter contract."""
    runtime = _engine_root() / "task4"
    with _import_path(runtime):
        planner = _load(runtime / "plan_pbo_bpe.py", "ftth_v220_task4_initial_sites")
        sites = planner.point_rows(task4_output / "SITE_PM_PARTITION")
    rows = _read_csv(stage / "task3" / "input" / "IMB.csv")
    summary = _read_csv(task4_output / "PM_partition_summary.csv")
    by_code = {str(row.get("CODE") or ""): (row, point) for row, point in sites}
    nro_sites = [
        {"id": str(row.get("CODE")), "code": str(row.get("CODE")), "x": point[0], "y": point[1], "capacity": 2048, "source": "task4_initial"}
        for row, point in sites if str(row.get("TYPE") or "").upper() == "NRO"
    ]
    if not nro_sites:
        raise ValueError("任务四初算未生成 NRO")
    pm_sites = []
    for item in summary:
        zpm_id = str(item.get("PM_PARTITION") or "")
        pm_id = str(item.get("REF_PM") or "")
        if pm_id not in by_code:
            raise ValueError(f"任务四初算缺少 PM 点：{pm_id}")
        _row, point = by_code[pm_id]
        members = [row for row in rows if str(row.get("ZPM_CODE") or "") == zpm_id]
        pm_sites.append({
            "id": pm_id,
            "code": pm_id,
            "x": point[0],
            "y": point[1],
            "node_id": pm_id,
            "zpm_id": zpm_id,
            "partition_id": zpm_id,
            "nro_id": nro_sites[0]["id"],
            "assigned_imb_ids": [row["CODE"] for row in members],
            "capacity": 576,
            "demand": sum(int(float(row["NB_LOC_TOT"])) for row in members),
            "source": "task4_initial_partition",
        })
    target = stage / "task3" / "initial_pm_plan.json"
    _write_json(target, {"schema_version": "task3.fixed_pm.v1", "nro_sites": nro_sites, "pm_sites": pm_sites, "selection_mode": "task4_initial"})
    return target


def _normalise_recognition_roads(
    records: Iterable[dict[str, Any]],
    *,
    confirmed_default_capacity: int,
) -> tuple[list[dict[str, Any]], int]:
    roads: list[dict[str, Any]] = []
    assumed_count = 0
    for index, raw in enumerate(records, 1):
        road = dict(raw)
        identifier = str(road.get("road_id") or road.get("id") or road.get("code") or f"GEO_ROAD_{index:04d}")
        open_cut = road.get("OPEN_CUT_ALLOWED", road.get("open_cut_allowed"))
        capacity = road.get("DUCT_CAPACITY", road.get("capacity"))
        attributes_missing = open_cut in (None, "") or capacity in (None, "")
        if open_cut in (None, ""):
            open_cut = True
        if capacity in (None, ""):
            capacity = int(confirmed_default_capacity)
        if attributes_missing:
            assumed_count += 1
        coordinates = road.get("coordinates") or ((road.get("geometry") or {}).get("coordinates") or [])
        if coordinates and isinstance(coordinates[0], (list, tuple)):
            coordinates = [
                {"x": float(point[0]), "y": float(point[1])}
                for point in coordinates
                if len(point) >= 2
            ]
        road.update({
            "id": identifier,
            "coordinates": coordinates,
            "open_cut_allowed": open_cut,
            "capacity": capacity,
            "construction_attributes_status": (
                "USER_CONFIRMED_PLANNING_ASSUMPTION"
                if attributes_missing
                else "SOURCE_ATTRIBUTES"
            ),
        })
        roads.append(road)
    return roads, assumed_count


def _consolidate_recognition_roads(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adapt the supplied whole-road preparation rule to loaded GPKG records."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for index, raw in enumerate(records, 1):
        row = dict(raw)
        road_id = str(row.get("road_id") or row.get("id") or f"GEO_ROAD_{index:04d}")
        parent = str(row.get("parent_road_id") or road_id.split(":", 1)[0])
        geometry = row.get("geometry") or {}
        coordinates = row.get("coordinates") or geometry.get("coordinates") or []
        if coordinates and isinstance(coordinates[0], dict):
            points = [(float(item["x"]), float(item["y"])) for item in coordinates]
        else:
            points = [(float(item[0]), float(item[1])) for item in coordinates if len(item) >= 2]
        if len(points) < 2:
            continue
        row.update({"_road_id": road_id, "_parent": parent, "_points": points})
        groups.setdefault(parent, []).append(row)

    consolidated: list[dict[str, Any]] = []
    for parent in sorted(groups):
        rows = groups[parent]
        segments = []
        adjacency: dict[str, list[int]] = {}
        for row in rows:
            points = row["_points"]
            first = str(row.get("from_node") or repr(points[0]))
            last = str(row.get("to_node") or repr(points[-1]))
            item = {"row": row, "points": points, "first": first, "last": last}
            segment_index = len(segments)
            segments.append(item)
            adjacency.setdefault(first, []).append(segment_index)
            adjacency.setdefault(last, []).append(segment_index)
        remaining = set(range(len(segments)))
        chains: list[list[tuple[int, bool]]] = []
        while remaining:
            endpoints = [
                index for index in remaining
                if len(adjacency[segments[index]["first"]]) != 2
                or len(adjacency[segments[index]["last"]]) != 2
            ]
            current = min(endpoints or remaining, key=lambda index: segments[index]["row"]["_road_id"])
            node = segments[current]["first"]
            if not endpoints and len(adjacency[node]) == 2:
                node = min(segments[current]["first"], segments[current]["last"])
            chain: list[tuple[int, bool]] = []
            while current in remaining:
                remaining.remove(current)
                segment = segments[current]
                forward = node == segment["first"]
                chain.append((current, forward))
                node = segment["last"] if forward else segment["first"]
                options = [index for index in adjacency[node] if index in remaining]
                if not options:
                    break
                current = min(options, key=lambda index: segments[index]["row"]["_road_id"])
            chains.append(chain)
        for part_index, chain in enumerate(chains, 1):
            points: list[tuple[float, float]] = []
            for segment_index, forward in chain:
                values = segments[segment_index]["points"]
                values = values if forward else list(reversed(values))
                points.extend(values[1:] if points and points[-1] == values[0] else values)
            source = segments[chain[0][0]]["row"]
            consolidated.append({
                **{key: value for key, value in source.items() if not key.startswith("_") and key not in {"geometry", "coordinates", "road_id", "id"}},
                "id": parent if len(chains) == 1 else f"{parent}:part{part_index:03d}",
                "parent_road_id": parent,
                "coordinates": [{"x": point[0], "y": point[1]} for point in points],
                "segment_count": len(chain),
                "length_m": round(sum(float(segments[index]["row"].get("length_m") or 0.0) for index, _ in chain), 3),
            })
    return consolidated


def _roads_in_imb_scope(
    records: Iterable[dict[str, Any]],
    task2_payload: dict[str, Any],
    *,
    buffer_m: float,
) -> list[dict[str, Any]]:
    """Apply the supplied geographic-envelope cleaning before consolidation."""
    nodes = [item for item in task2_payload.get("nodes") or task2_payload.get("imb_points") or [] if isinstance(item, dict)]
    imbs = [item for item in nodes if str(item.get("type") or item.get("TYPE") or "").upper() == "IMB"] or nodes
    points = [
        (float(item.get("x", item.get("X"))), float(item.get("y", item.get("Y"))))
        for item in imbs if item.get("x", item.get("X")) is not None and item.get("y", item.get("Y")) is not None
    ]
    if not points:
        raise ValueError("无法依据 IMB 范围筛选识别道路")
    center_lat = sum(point[1] for point in points) / len(points)
    lat_margin = float(buffer_m) / 111320.0
    lon_margin = float(buffer_m) / max(1.0, 111320.0 * abs(math.cos(math.radians(center_lat))))
    min_x = min(point[0] for point in points) - lon_margin
    max_x = max(point[0] for point in points) + lon_margin
    min_y = min(point[1] for point in points) - lat_margin
    max_y = max(point[1] for point in points) + lat_margin
    selected = []
    for record in records:
        geometry = record.get("geometry") or {}
        coordinates = record.get("coordinates") or geometry.get("coordinates") or []
        if coordinates and isinstance(coordinates[0], dict):
            values = [(float(item["x"]), float(item["y"])) for item in coordinates]
        else:
            values = [(float(item[0]), float(item[1])) for item in coordinates if len(item) >= 2]
        if values and not (
            max(point[0] for point in values) < min_x
            or min(point[0] for point in values) > max_x
            or max(point[1] for point in values) < min_y
            or min(point[1] for point in values) > max_y
        ):
            selected.append(record)
    if not selected:
        raise ValueError("识别道路与 IMB 规划范围不相交")
    return selected


def _task6_base(
    stage: Path,
    topology: dict[str, Any],
    task2_payload: dict[str, Any],
    cluster: dict[str, Any],
    fixed_pm_path: Path,
    *,
    use_formal_infrastructure: bool,
    use_recognition_roads: bool,
    formal_duct_capacity: int,
    candidate_paths: int,
    water_cost_factor: float,
    ptech_max_spacing_m: float,
    terminal_snap_distance_m: float,
) -> Path:
    imbs = _read_csv(stage / "task3" / "input" / "IMB.csv")
    use_formal_infrastructure = use_formal_infrastructure and not use_recognition_roads

    roads: list[dict[str, Any]] = []
    confirmations: list[dict[str, Any]] = []
    if use_formal_infrastructure:
        for index, edge in enumerate(topology.get("edges") or [], 1):
            coordinates = edge.get("path") or []
            if len(coordinates) < 2:
                continue
            source_attributes = edge.get("task6_attributes") if isinstance(edge.get("task6_attributes"), dict) else {}
            capacity_value = source_attributes.get("DUCT_CAPACITY", source_attributes.get("CAPACITY", formal_duct_capacity))
            try:
                capacity_value = int(float(capacity_value))
            except (TypeError, ValueError):
                capacity_value = int(formal_duct_capacity)
            roads.append({
                "id": str(edge.get("code") or f"FORMAL_INFRA_{index:04d}"),
                "coordinates": coordinates,
                "open_cut_allowed": True,
                "capacity": capacity_value,
                "source": "formal_task1_infrastructure",
                "construction_attributes_status": "USER_CONFIRMED_FOR_THIS_RUN",
                "source_attributes": source_attributes,
            })
        confirmations.append({
            "policy": "FORMAL_INFRASTRUCTURE_ROUTABLE",
            "open_cut_allowed": True,
            "duct_capacity": int(formal_duct_capacity),
            "scope": "本批次任务一 INFRASTRUCTURE",
        })
    recognition = task2_payload.get("recognition") or {}
    if use_recognition_roads:
        scoped_roads = _roads_in_imb_scope(
            recognition.get("roads") or [],
            task2_payload,
            buffer_m=max(200.0, float(terminal_snap_distance_m) * 2.0),
        )
        recognition_roads = _consolidate_recognition_roads(scoped_roads)
        recognised, assumed_count = _normalise_recognition_roads(
            recognition_roads,
            confirmed_default_capacity=int(formal_duct_capacity),
        )
        if not recognised:
            raise ValueError("已选择使用识别道路，但本批次没有可用 GEO_ROAD")
        roads.extend(recognised)
        confirmations.append({
            "policy": "USE_RECOGNITION_ROAD_AS_REVIEW_REQUIRED_NEW_BUILD_CANDIDATE",
            "count": len(recognised),
            "source_segment_count": len(recognition.get("roads") or []),
            "scoped_segment_count": len(scoped_roads),
            "assumed_attribute_count": assumed_count,
            "open_cut_allowed": True,
            "duct_capacity": int(formal_duct_capacity),
            "review_required": True,
        })
    if not roads:
        raise ValueError("任务六没有经用户确认的道路来源")

    reference_audit = {
        "status": "PASS",
        "mode": "current_project_only",
        "reason": "runtime_example_scene_disabled",
        "sample_feature_records_used": False,
        "formal_infrastructure_input_count": len(topology.get("edges") or []),
        "formal_infrastructure_used": bool(use_formal_infrastructure),
        "recognition_road_segment_input_count": len((task2_payload.get("recognition") or {}).get("roads") or []),
        "recognition_roads_used": bool(use_recognition_roads),
        "task6_road_count": len(roads),
    }
    service_map_path = stage / "task3" / "input" / "service_unit_map.json"
    service_map = _read_json(service_map_path).get("unit_to_imb") if service_map_path.is_file() else {}
    fixed_pm = _read_json(fixed_pm_path)
    imbs_by_code = {row["CODE"]: row for row in imbs}
    building_groups: list[dict[str, Any]] = []
    buildings: list[dict[str, Any]] = []
    entrances: list[dict[str, Any]] = []
    assignment_by_imb: dict[str, dict[str, str]] = {}
    # 任务六的分层路由必须具有 PM -> 建筑组 -> BPE 的明确关系。
    # 当没有识别建筑面时，以任务三已经确认的 ZPM 服务单元构造逻辑建筑组；
    # 这是数据关系适配，不会伪造建筑面或覆盖 GEO_* 辅助图层。
    for pm in fixed_pm.get("pm_sites") or []:
        pm_id = str(pm.get("id") or pm.get("code") or "")
        zpm_id = str(pm.get("zpm_id") or pm.get("partition_id") or pm_id)
        member_ids = [str(value) for value in pm.get("assigned_imb_ids") or []]
        members = [imbs_by_code[value] for value in member_ids if value in imbs_by_code]
        if not pm_id or not members:
            continue
        group_id = f"BG-{zpm_id}"
        group_x = sum(float(row["X"]) for row in members) / len(members)
        group_y = sum(float(row["Y"]) for row in members) / len(members)
        demand = sum(int(float(row["NB_LOC_TOT"])) for row in members)
        building_groups.append({
            "id": group_id,
            "group_id": group_id,
            "x": group_x,
            "y": group_y,
            "pm_id": pm_id,
            "zpm_id": zpm_id,
            "demand": demand,
            "source": "task3_fixed_zpm_service_group",
        })
        entrances.append({
            "id": f"GE-{group_id}",
            "x": group_x,
            "y": group_y,
            "entrance_type": "building_group",
            "group_id": group_id,
            "accessible": True,
            "source": "task3_service_group_centroid",
        })
        for row in members:
            imb_id = row["CODE"]
            building_id = f"BLDG-{imb_id}"
            assignment_by_imb[imb_id] = {
                "pm_id": pm_id,
                "zpm_id": zpm_id,
                "group_id": group_id,
                "building_id": building_id,
            }
            buildings.append({
                "id": building_id,
                "building_id": building_id,
                "imb_id": imb_id,
                "x": float(row["X"]),
                "y": float(row["Y"]),
                "group_id": group_id,
                "pm_id": pm_id,
                "ports": int(float(row["NB_LOC_TOT"])),
                "source": "task3_service_unit_point",
            })
    payload = {
        "meta": {
            "task": "task6_current_batch_input",
            "scenario": "task1_to_task6_current_batch_with_explicit_confirmations",
            "coordinate_system": (topology.get("meta") or {}).get("coordinate_system", "EPSG:4326"),
            "source_status": "user_confirmed_current_batch",
            "road_confirmations": confirmations,
            "service_unit_map": str(service_map_path) if service_map_path.is_file() else None,
        },
        "planning": {
            "algorithm": "layered_fixed_assignment_shared_edge_search",
            "candidate_paths": int(candidate_paths),
            "default_edge_capacity": int(formal_duct_capacity),
            "road_cost_per_m": 1.0,
            "water_cost_factor": float(water_cost_factor),
            "ptech_max_spacing_m": float(ptech_max_spacing_m),
            "terminal_snap_distance_m": float(terminal_snap_distance_m),
        },
        "task2_partition": {"zpm": cluster.get("zpm") or {}},
        "remote_sensing": {
            "roads": roads,
            "building_groups": building_groups,
            "buildings": buildings,
            "entrances": [
                *entrances,
                *(list(recognition.get("access_candidates") or []) if use_recognition_roads else []),
            ],
            "water_cost_zones": list(recognition.get("cost_zones") or []) if use_recognition_roads else [],
        },
        "imb_points": [
            {
                "id": row["CODE"],
                "code": row["CODE"],
                "x": float(row["X"]),
                "y": float(row["Y"]),
                "ports": int(float(row["NB_LOC_TOT"])),
                "zpm_id": row.get("ZPM_CODE"),
                "parent_imb": (service_map or {}).get(row["CODE"], row["CODE"]),
                **assignment_by_imb.get(row["CODE"], {}),
            }
            for row in imbs
        ],
    }
    target = stage / "task6" / "base_scene.json"
    _write_json(target, payload)
    _write_json(stage / "task6" / "base_input_audit.json", reference_audit)
    return target


def _feature_collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def _write_task6_map_layers(plan: dict[str, Any], output_dir: Path) -> list[dict[str, str]]:
    routes = []
    for record in plan.get("duct_routes") or []:
        coordinates = [[item["x"], item["y"]] for item in record.get("coordinates") or [] if item.get("x") is not None and item.get("y") is not None]
        if len(coordinates) >= 2:
            routes.append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coordinates},
                "properties": {key: value for key, value in record.items() if key not in {"coordinates", "nodes"}},
            })
    ptech = []
    for record in plan.get("ptech_plan") or []:
        if record.get("x") is None or record.get("y") is None:
            continue
        ptech.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [record["x"], record["y"]]},
            "properties": {key: value for key, value in record.items() if key not in {"x", "y", "X", "Y"}},
        })
    route_path = output_dir / "DUCT_ROUTE.geojson"
    ptech_path = output_dir / "PTECH_TASK6.geojson"
    _write_json(route_path, _feature_collection(routes))
    _write_json(ptech_path, _feature_collection(ptech))
    return [
        {"group": "05 管线路由", "name": "NRO-PM-BPE-PBO分层管路", "path": str(route_path)},
        {"group": "05 管线路由", "name": "任务六新增PTECH", "path": str(ptech_path)},
    ]


def _normalise_task6_plan_coordinates(plan: dict[str, Any]) -> None:
    """Normalise generated plan coordinates for the unchanged visualizer.

    Formal Task-1 INFRASTRUCTURE reaches Task 6 as ``[x, y]`` pairs, while
    recognition roads use ``{"x": x, "y": y}``.  The planner accepts both,
    but its supplied visualizer accepts only the latter.  This is an output
    representation adapter; it does not alter graph topology or routing.
    """
    for key in ("trunk_roads", "duct_routes", "nro_access_fibers", "pm_access_fibers"):
        for record in plan.get(key) or []:
            converted = []
            for point in record.get("coordinates") or []:
                if isinstance(point, dict):
                    converted.append(point)
                elif isinstance(point, (list, tuple)) and len(point) >= 2:
                    converted.append({"x": float(point[0]), "y": float(point[1])})
                else:
                    raise ValueError(f"任务六 {key} 包含无法识别的坐标：{point!r}")
            record["coordinates"] = converted


def _enrich_task6_relationships(input_path: Path) -> None:
    """Complete cross-task IDs in the plugin adapter, outside frozen algorithms."""
    payload = _read_json(input_path)
    remote = payload.get("remote_sensing") or {}
    # The supplied Task-6 planner accepts the nested remote_sensing contract,
    # while the supplied Task-3 GA feedback optimizer reads the same records
    # from top-level keys.  Publish both views without renaming or modifying
    # any feature so the two unchanged algorithms can share one batch input.
    for key in ("roads", "building_groups", "buildings", "entrances", "water_cost_zones"):
        if not payload.get(key) and remote.get(key):
            payload[key] = remote[key]
    payload.setdefault("meta", {})["task3_ga_compatibility_view"] = {
        "source": "remote_sensing",
        "keys": [
            key for key in ("roads", "building_groups", "buildings", "entrances", "water_cost_zones")
            if payload.get(key)
        ],
    }
    groups = payload.get("building_groups") or remote.get("building_groups") or []
    group_by_pm = {
        str(item.get("pm_id") or item.get("pm_code")): str(item.get("id") or item.get("group_id"))
        for item in groups if item.get("pm_id") or item.get("pm_code")
    }
    pm_to_zpm = {
        str(item.get("id") or item.get("code")): str(item.get("zpm_id") or item.get("partition_id"))
        for item in payload.get("pm_sites") or []
        if item.get("id") or item.get("code")
    }
    imb_by_id = {
        str(item.get("id") or item.get("code") or item.get("CODE")): item
        for item in payload.get("imb_points") or []
        if item.get("id") or item.get("code") or item.get("CODE")
    }
    for item in payload.get("bpe_plan") or []:
        pm_id = str(item.get("pm_id") or "")
        group_id = group_by_pm.get(pm_id)
        if group_id:
            item["group_id"] = group_id
            item["building_group_id"] = group_id
        if not item.get("zpm_id") and pm_to_zpm.get(pm_id):
            item["zpm_id"] = pm_to_zpm[pm_id]
    for item in payload.get("pbo_plan") or []:
        imb = imb_by_id.get(str(item.get("imb_id") or ""), {})
        if imb.get("building_id"):
            item["building_id"] = imb["building_id"]
        group_id = imb.get("group_id") or group_by_pm.get(str(item.get("pm_id") or ""))
        if group_id:
            item["group_id"] = group_id
        pm_id = str(item.get("pm_id") or "")
        if not item.get("zpm_id") and pm_to_zpm.get(pm_id):
            item["zpm_id"] = pm_to_zpm[pm_id]
    _write_json(input_path, payload)


def run_task6(
    stage: Path,
    topology: dict[str, Any],
    task2_payload: dict[str, Any],
    cluster: dict[str, Any],
    fixed_pm_path: Path,
    task4_output: Path,
    **options: Any,
) -> dict[str, Any]:
    runtime = _engine_root() / "task6"
    base = _task6_base(stage, topology, task2_payload, cluster, fixed_pm_path, **options)
    input_path = stage / "task6" / "task6_input.json"
    output_path = stage / "task6" / "layered_plan.json"
    task3_runtime = _engine_root() / "task3"
    with _import_path(task3_runtime):
        adapter = _load(task3_runtime / "task3_to_task6_adapter.py", "ftth_v180_task3_task6_adapter")
        # The original adapter is kept byte-identical and therefore retains
        # its author's Chinese project path constants. Point only the loaded
        # module object at the packaged runtime; no task-3 source is edited.
        adapter.TASK4_DIR = _engine_root() / "task4"
        adapter.TASK6_DIR = runtime
        adapter.build_layered_task6_input(base, fixed_pm_path, input_path)
        adapter.attach_task4_plan_to_task6_input(input_path, task4_output)
        _enrich_task6_relationships(input_path)
    with _import_path(runtime / "code"):
        planner = _load(runtime / "code" / "plan_task6.py", "ftth_v180_task6_planner")
        plan = planner.plan(input_path)
        _normalise_task6_plan_coordinates(plan)
        input_meta = (_read_json(input_path).get("meta") or {})
        if input_meta.get("road_confirmations"):
            plan.setdefault("meta", {})["road_confirmations"] = input_meta["road_confirmations"]
        planner.write_plan(plan, output_path)
        visualizer = _load(runtime / "code" / "visualize_task6_plan.py", "ftth_v240_task6_visualizer")
        visualization = visualizer.render(output_path, output_path.parent / "layered_plan.png")
    validation = plan.get("validation") or {}
    layers = _write_task6_map_layers(plan, output_path.parent)
    return {
        "status": "success",
        "input": str(input_path),
        "output": str(output_path),
        "validation": validation,
        "route_count": len(plan.get("duct_routes") or []),
        "ptech_count": len(plan.get("ptech_plan") or []),
        "layers": layers,
        "visualization": str(visualization),
        "warnings": list(validation.get("unresolved") or []),
    }


def run_task3_ga_optimization(
    stage: Path,
    *,
    topology: dict[str, Any],
    task2_payload: dict[str, Any],
    cluster: dict[str, Any],
    initial_fixed_pm: Path,
    task4_initial_output: Path,
    population: int,
    generations: int,
    task6_options: dict[str, Any],
) -> dict[str, Any]:
    """Run Task 3 after Task 4 initial service relationships, as FTTH(4) specifies."""
    task3_runtime = _engine_root() / "task3"
    task6_runtime = _engine_root() / "task6"
    task6_code = task6_runtime / "code"
    regions = stage / "task2" / "qgis_direct_region_partition_result.json"
    base = _task6_base(stage, topology, task2_payload, cluster, initial_fixed_pm, **task6_options)
    initial_input = stage / "task6" / "task6_initial_input.json"
    output_dir = stage / "task3" / "output"
    with _import_path(task3_runtime), _import_path(task6_code):
        adapter = _load(task3_runtime / "task3_to_task6_adapter.py", "ftth_v220_task3_initial_adapter")
        adapter.TASK4_DIR = _engine_root() / "task4"
        adapter.TASK6_DIR = task6_runtime
        adapter.build_layered_task6_input(base, initial_fixed_pm, initial_input)
        adapter.attach_task4_plan_to_task6_input(initial_input, task4_initial_output)
        _enrich_task6_relationships(initial_input)
        from ga_pm_optimizer.optimizer import OptimizationConfig, PMOptimizer
        from ga_pm_optimizer.visualize import render_optimization

        config = OptimizationConfig(
            population_size=int(population),
            generations=int(generations),
            patience=max(2, min(int(generations), 5)),
        )
        optimizer = PMOptimizer(initial_input, regions, config)
        result = optimizer.run()
        if result.optimized.hard_violations:
            raise ValueError("任务三 GA 未通过硬约束：" + "；".join(result.optimized.hard_violations[:20]))
        paths = optimizer.write_outputs(result, output_dir)
        figure = render_optimization(optimizer, result, output_dir / "pm_optimization.png")
        fixed_pm = stage / "task3" / "pm_fixed_plan.json"
        fixed_payload = adapter.build_fixed_pm_plan(stage / "task3" / "input", output_dir, fixed_pm)
    expected = {row["CODE"] for row in _read_csv(stage / "task3" / "input" / "IMB.csv")}
    actual = {str(value) for pm in fixed_payload.get("pm_sites") or [] for value in pm.get("assigned_imb_ids") or []}
    if actual != expected:
        raise ValueError(f"任务三改变了 IMB 集合：missing={sorted(expected - actual)[:5]} extra={sorted(actual - expected)[:5]}")
    return {
        "status": "success",
        "algorithm": "constraint_first_memetic_ga_task6_feedback",
        "population": int(population),
        "generations": int(generations),
        "fixed_pm": str(fixed_pm),
        "pm_count": len(fixed_payload.get("pm_sites") or []),
        "optimized_metrics": result.optimized.metrics,
        "candidate_counts": {zpm_id: len(result.candidates[zpm_id]) for zpm_id in result.zpm_ids},
        "output_dir": str(output_dir),
        "figure": str(figure),
        "artifacts": {name: str(path) for name, path in paths.items()},
    }


def run_task5(stage: Path, task6_plan: Path) -> dict[str, Any]:
    runtime = _engine_root() / "task5"
    output_dir = stage / "task5"
    config = {
        "interface_version": "task5.fiber-link.v1",
        "task_id": stage.name,
        "operation": "plan_hierarchical_fiber_links",
        "input": {"task6_plan": str(task6_plan)},
        "output": {"directory": str(output_dir)},
    }
    with _import_path(runtime):
        module = _load(runtime / "task5_core.py", "ftth_v140_task5")
        plan = module.execute(config, stage)
        visualizer = _load(runtime / "visualize_task5.py", "ftth_v240_task5_visualizer")
        visualization = visualizer.render(output_dir / "fiber_link_plan.json", output_dir / "fiber_link_overview.png")
    paths = plan.get("output_paths") or {}
    required = [Path(paths[key]) for key in ("fiber_link_plan", "fiber_links", "validation", "qgis_geojson")]
    missing = [str(path) for path in required if not path.is_file() or not path.stat().st_size]
    if missing:
        raise ValueError(f"任务五成果不完整：{missing}")
    validation = plan.get("validation") or {}
    return {
        "status": "success",
        "output_paths": paths,
        "validation": validation,
        "link_count": len(plan.get("links") or []),
        "visualization": str(visualization.get("output") or output_dir / "fiber_link_overview.png"),
        "layers": [{"group": "06 光缆", "name": "NRO-PM-BPE-PBO-IMB光缆", "path": paths["qgis_geojson"]}],
        "warnings": list(validation.get("issues") or []),
    }


def _imb_contract(payload: dict[str, Any]) -> dict[str, int]:
    candidates = [row for row in payload.get("nodes") or payload.get("imb_points") or [] if isinstance(row, dict)]
    typed = [row for row in candidates if str(row.get("type") or row.get("TYPE") or "").upper() == "IMB"]
    return {
        str(row.get("code") or row.get("CODE") or row.get("id") or row.get("ID")): int(
            round(float(row.get("ports") or row.get("nb_loc_tot") or row.get("NB_LOC_TOT") or 0))
        )
        for row in typed or candidates
    }


def validate_pipeline_handoffs(stage: Path, topology: dict[str, Any]) -> dict[str, Any]:
    """Prove that every stage consumed one current-project data lineage."""
    cleaned = _read_json(stage / "task2" / "task2_imb_only_input.json")
    cluster_result = _read_json(stage / "task2" / "qgis_petch_atomic_result.json")
    regions = _read_json(stage / "task2" / "qgis_direct_region_partition_result.json")
    fixed = _read_json(stage / "task3" / "pm_fixed_plan.json")
    task6_input = _read_json(stage / "task6" / "task6_input.json")
    task6_plan = _read_json(stage / "task6" / "layered_plan.json")
    task5_plan = _read_json(stage / "task5" / "fiber_link_plan.json")
    task4_manifest = _read_json(stage / "task4" / "input_shapes" / "task4_input_manifest.json")
    task6_audit = _read_json(stage / "task6" / "base_input_audit.json")
    source = _imb_contract(topology)
    task2 = _imb_contract(cleaned)
    task3_rows = _read_csv(stage / "task3" / "input" / "IMB.csv")
    task3 = {str(row["CODE"]): int(float(row["NB_LOC_TOT"])) for row in task3_rows}
    fixed_codes = {
        str(code)
        for pm in fixed.get("pm_sites") or []
        for code in pm.get("assigned_imb_ids") or []
    }
    task4_codes = {
        str(row.get("IMB_CODE") or "")
        for row in _read_csv(stage / "task4" / "output" / "IMB_PBO_BPE_mapping.csv")
    }
    task6_codes = set(_imb_contract(task6_input))
    source_site_codes = {
        str(row.get("code") or "") for row in topology.get("nodes") or []
        if str(row.get("source_layer") or "").upper() == "SITE"
    }
    source_nro_codes = {
        str(row.get("code") or "") for row in topology.get("nodes") or []
        if str(row.get("source_layer") or "").upper() == "SITE"
        and str(row.get("original_type") or (row.get("task4_attributes") or {}).get("TYPE") or "").upper() == "NRO"
    }
    source_ptech_codes = {
        str(row.get("code") or "") for row in topology.get("nodes") or []
        if str(row.get("source_layer") or "").upper() == "PTECH"
    }
    fixed_nro_codes = {
        str(row.get("code") or row.get("id") or "") for row in fixed.get("nro_sites") or []
    }
    partition_codes = {
        str(code)
        for region in regions.get("zpm_regions") or []
        for code in region.get("imb_codes") or []
    }
    if not partition_codes:
        internal_to_code = {
            str(row.get("id") or row.get("code")): str(row.get("code") or row.get("id"))
            for row in cleaned.get("nodes") or []
        }
        partition_codes = {
            internal_to_code.get(str(value), str(value))
            for region in regions.get("zpm_regions") or []
            for value in region.get("imb_ids") or []
        }
    checks = {
        "base_to_task2_codes_and_demand": source == task2,
        "task2_to_partition_membership": set(task2) == partition_codes,
        "task2_to_task3_codes_and_demand": task2 == task3,
        "task3_fixed_pm_coverage": set(task3) == fixed_codes,
        "fixed_pm_count_matches_zpm_count": len(fixed.get("pm_sites") or []) == len(regions.get("zpm_regions") or []),
        "task3_to_task4_mapping": set(task3) == task4_codes,
        "task4_to_task6_imb_coverage": set(task4_codes) == task6_codes,
        "task6_has_planned_infrastructure": len(task6_plan.get("duct_routes") or []) > 0,
        "task5_reads_task6_plan": str((task5_plan.get("meta") or {}).get("source_task6_plan") or (task5_plan.get("source") or {}).get("task6_plan") or "") != "",
        "task5_validation_pass": (task5_plan.get("validation") or {}).get("status") == "PASS",
        "task2_uses_geodesic_source_coordinates": (
            (cleaned.get("meta") or {}).get("coordinate_system") == "source_shapefile_coordinates"
            and (cluster_result.get("input_summary") or {}).get("spatial_distance_mode") in {"geodesic", "geodesic_imb"}
        ),
        "task4_uses_schema_only_no_example_features": (
            task4_manifest.get("template_usage") == "schema_and_crs_only"
            and task4_manifest.get("sample_feature_records_used") is False
        ),
        "current_site_materialized": source_site_codes == set(task4_manifest.get("site_codes") or []),
        "current_nro_reaches_task3": source_nro_codes == fixed_nro_codes,
        "current_ptech_materialized": source_ptech_codes == set(task4_manifest.get("ptech_codes") or []),
        "task6_uses_current_project_only": (
            task6_audit.get("mode") == "current_project_only"
            and task6_audit.get("sample_feature_records_used") is False
        ),
        "all_formal_infrastructure_applied_when_present": (
            not topology.get("edges")
            or bool(task6_audit.get("formal_infrastructure_used"))
        ),
    }
    failures = [name for name, passed in checks.items() if not passed]
    report = {
        "schema_version": "ftth.pipeline-handoff.v2",
        "status": "PASS" if not failures else "FAIL",
        "checks": checks,
        "failures": failures,
        "counts": {
            "base_imb": len(source),
            "task2_imb": len(task2),
            "zpm": len(regions.get("zpm_regions") or []),
            "task3_pm": len(fixed.get("pm_sites") or []),
            "task4_pbo_mapping": len(task4_codes),
            "task6_imb": len(task6_codes),
            "task6_duct_routes": len(task6_plan.get("duct_routes") or []),
            "task5_links": len(task5_plan.get("links") or []),
            "base_site": len(source_site_codes),
            "base_ptech": len(source_ptech_codes),
            "base_infrastructure": len(topology.get("edges") or []),
        },
        "input_lineage": {
            "site_codes": sorted(source_site_codes),
            "nro_codes": sorted(source_nro_codes),
            "ptech_codes": sorted(source_ptech_codes),
            "task4_manifest": task4_manifest,
        },
        "task6_base_input": task6_audit,
    }
    _write_json(stage / "pipeline_handoff_validation.json", report)
    if failures:
        raise ValueError("任务2—6数据传递合同失败：" + "、".join(failures))
    return report


def run_final_layers(stage: Path, topology: dict[str, Any]) -> dict[str, Any]:
    """Call the supplied FTTH(4) final-layer adapter with this batch's paths."""
    runtime = _engine_root()
    output_dir = stage / "final_layers"
    args = [
        "--task2-regions", str(stage / "task2" / "qgis_direct_region_partition_result.json"),
        "--task4-input", str(stage / "task4" / "input_shapes"),
        "--task4-output", str(stage / "task4" / "output"),
        "--task5-plan", str(stage / "task5" / "fiber_link_plan.json"),
        "--task6-plan", str(stage / "task6" / "layered_plan.json"),
        "--output-dir", str(output_dir),
    ]
    with _import_path(runtime):
        module = _load(runtime / "build_final_layers.py", "ftth_v240_final_layers")
        code = module.main(args)
    report_path = output_dir / "field_validation.json"
    if code != 0 or not report_path.is_file():
        raise ValueError("FTTH(4) 最终八图层构建或校验失败")
    report = _read_json(report_path)
    try:
        from .final_layer_postprocessor import apply_final_layer_corrections
    except ImportError:  # pragma: no cover - direct worker import fallback
        from final_layer_postprocessor import apply_final_layer_corrections
    report = apply_final_layer_corrections(
        module,
        output_dir,
        stage / "task2" / "qgis_direct_region_partition_result.json",
        stage / "task5" / "fiber_link_plan.json",
        report,
    )
    _write_json(report_path, report)
    if report.get("status") != "PASS":
        raise ValueError("FTTH(4) 最终八图层合同未通过")
    counts = {
        name: int((report.get("layers") or {}).get(name, {}).get("feature_count") or 0)
        for name in ("IMB", "SITE", "BOITE", "CABLE", "PTECH", "INFRASTRUCTURE", "ZNRO", "ZPM")
    }
    task6_routes = sum(
        len(route.get("coordinates") or []) >= 2
        for route in _read_json(stage / "task6" / "layered_plan.json").get("duct_routes") or []
    )
    if counts["INFRASTRUCTURE"] != task6_routes or counts["INFRASTRUCTURE"] <= 0:
        raise ValueError(
            f"INFRASTRUCTURE 必须来自任务六 duct_routes：图层={counts['INFRASTRUCTURE']}，任务六={task6_routes}"
        )
    source_nro = {
        str(row.get("code") or ""): (float(row.get("x")), float(row.get("y")))
        for row in topology.get("nodes") or []
        if str(row.get("source_layer") or "").upper() == "SITE"
        and str(row.get("original_type") or (row.get("task4_attributes") or {}).get("TYPE") or "").upper() == "NRO"
    }
    final_site = {
        str(row.get("CODE") or ""): (float(point[0]), float(point[1]))
        for row, point in module.point_layer(output_dir / "SITE")
        if str(row.get("TYPE") or "").upper() == "NRO"
    }
    source_ptech = {
        str(row.get("code") or "") for row in topology.get("nodes") or []
        if str(row.get("source_layer") or "").upper() == "PTECH"
    }
    final_ptech = {str(row.get("CODE") or "") for row, _point in module.point_layer(output_dir / "PTECH")}
    if source_nro != final_site:
        raise ValueError(f"最终 SITE 未原样继承当前项目 NRO：输入={source_nro}，输出={final_site}")
    missing_ptech = sorted(source_ptech - final_ptech)
    if missing_ptech:
        raise ValueError(f"最终 PTECH 漏掉当前项目输入：{missing_ptech[:20]}")
    return {
        "status": "success",
        "output_dir": str(output_dir),
        "validation": str(report_path),
        "counts": counts,
        "infrastructure_source": "task6.duct_routes",
        "site_nro_source": "current_project.SITE",
        "preserved_input_ptech_count": len(source_ptech),
    }


def run_downstream(
    stage: Path,
    *,
    topology: dict[str, Any],
    task2_payload: dict[str, Any],
    cluster: dict[str, Any],
    use_formal_infrastructure: bool,
    use_recognition_roads: bool,
    formal_duct_capacity: int,
    pbo_offset_m: float,
    candidate_paths: int,
    water_cost_factor: float,
    ptech_max_spacing_m: float,
    terminal_snap_distance_m: float,
    ga_refine: bool = True,
    ga_population: int = 8,
    ga_generations: int = 4,
) -> dict[str, Any]:
    """Run the FTTH(4) sequence on artifacts from the current batch only."""
    requested_formal_infrastructure = bool(use_formal_infrastructure)
    # Recognition roads take precedence; formal infrastructure is a fallback,
    # never silently added to a road-only routing policy.
    use_formal_infrastructure = requested_formal_infrastructure and not use_recognition_roads
    if not use_formal_infrastructure and not use_recognition_roads:
        raise ValueError("任务六至少要显式选择一种经确认的道路来源")
    if formal_duct_capacity <= 0:
        raise ValueError("确认管道容量必须是正整数")
    if pbo_offset_m <= 0 or ptech_max_spacing_m <= 0 or terminal_snap_distance_m <= 0:
        raise ValueError("PBO偏移、PTECH间距和吸附距离必须为正数")
    if candidate_paths < 1 or not 0 <= water_cost_factor <= 1000:
        raise ValueError("候选路径数至少为1，水域成本系数必须在0到1000之间")
    if ga_refine and (ga_population < 4 or ga_generations < 1):
        raise ValueError("GA 种群至少为4，迭代代数至少为1")

    cleaned_task2 = stage / "task2" / "task2_imb_only_input.json"
    project_topology = stage / "base" / "qgis_ftth_dataset.json"
    if not cleaned_task2.is_file():
        raise FileNotFoundError(f"新任务二未生成 IMB-only 数据契约：{cleaned_task2}")
    cleaned_payload = _read_json(cleaned_task2)
    regions = _read_json(stage / "task2" / "qgis_direct_region_partition_result.json")
    materialize_task3_input(cleaned_payload, regions, stage / "task3" / "input")

    print("[bridge] 任务四初算：建立本批次 PBO/BPE 服务关系…", flush=True)
    task4_initial = run_task4(
        stage,
        cleaned_task2,
        project_topology,
        fixed_pm_path=None,
        output_name="initial",
        pbo_offset_m=pbo_offset_m,
    )
    initial_fixed_pm = _initial_fixed_plan_from_task4(stage, Path(task4_initial["output_dir"]))
    task6_options = dict(
        use_formal_infrastructure=use_formal_infrastructure,
        use_recognition_roads=use_recognition_roads,
        formal_duct_capacity=formal_duct_capacity,
        candidate_paths=candidate_paths,
        water_cost_factor=water_cost_factor,
        ptech_max_spacing_m=ptech_max_spacing_m,
        terminal_snap_distance_m=terminal_snap_distance_m,
    )
    task3_result = None
    fixed_pm_path = initial_fixed_pm
    if ga_refine:
        print(
            f"[bridge] 任务三：依据任务四关系和道路反馈优化 PM（种群 {int(ga_population)}，迭代 {int(ga_generations)}）…",
            flush=True,
        )
        task3_result = run_task3_ga_optimization(
            stage,
            topology=topology,
            task2_payload=task2_payload,
            cluster=cluster,
            initial_fixed_pm=initial_fixed_pm,
            task4_initial_output=Path(task4_initial["output_dir"]),
            population=int(ga_population),
            generations=int(ga_generations),
            task6_options=task6_options,
        )
        fixed_pm_path = Path(task3_result["fixed_pm"])
    else:
        initial = _read_json(initial_fixed_pm)
        task3_result = {
            "status": "success",
            "algorithm": "task4_initial_pm_without_ga",
            "fixed_pm": str(initial_fixed_pm),
            "pm_count": len(initial.get("pm_sites") or []),
        }

    print("[bridge] 任务四定稿：锁定任务三 PM 后重算 PBO/BPE…", flush=True)
    task4 = run_task4(
        stage,
        cleaned_task2,
        project_topology,
        fixed_pm_path=fixed_pm_path,
        output_name="output",
        pbo_offset_m=pbo_offset_m,
    )
    print("[bridge] 任务六：使用定稿服务关系生成分层管线路由…", flush=True)
    task6 = run_task6(
        stage,
        topology,
        task2_payload,
        cluster,
        fixed_pm_path,
        Path(task4["output_dir"]),
        **task6_options,
    )
    print("[bridge] 任务五：依据任务六方案生成完整光缆拓扑…", flush=True)
    task5 = run_task5(stage, Path(task6["output"]))
    print("[bridge] 数据合同复核：逐级核对任务2—6的IMB、PM、PBO、管路和光缆…", flush=True)
    handoff_validation = validate_pipeline_handoffs(stage, topology)
    print("[bridge] 最终图层：调用 FTTH(4) build_final_layers 生成八图层…", flush=True)
    final_layers = run_final_layers(stage, topology)
    layers = [*task4["layers"], *task6["layers"], *task5["layers"]]
    return {
        "status": "success",
        "sequence": [
            "task4_initial",
            "task3_ga_optimization" if ga_refine else "task3_initial_pm",
            "task4_final",
            "task6",
            "task5",
            "final_layers",
        ],
        "task4_initial": task4_initial,
        "task3": task3_result,
        "task4": task4,
        "task6": task6,
        "task3_ga_refinement": task3_result if ga_refine else None,
        "task5": task5,
        "handoff_validation": handoff_validation,
        "final_layers": final_layers,
        "layers": layers,
        "user_confirmations": {
            "oversized_imb_split": "service_units_max_48",
            "use_formal_infrastructure": bool(use_formal_infrastructure),
            "formal_infrastructure_requested": requested_formal_infrastructure,
            "formal_infrastructure_auto_applied": bool(topology.get("edges") or []),
            "use_recognition_roads": bool(use_recognition_roads),
            "formal_duct_capacity": int(formal_duct_capacity),
            "ga_refine": bool(ga_refine),
            "ga_population": int(ga_population),
            "ga_generations": int(ga_generations),
        },
    }
