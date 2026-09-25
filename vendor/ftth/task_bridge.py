"""Task 1 -> Task 2 bridge, executed in an isolated QGIS Python worker.

No precomputed Task 2 input is selected. A whole run is staged and published
as a new directory only after both algorithms and their checks succeed.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import os
import runpy
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


RESULT_PREFIX = "FTTH_BRIDGE_RESULT="
REGION_CHECKS = (
    "znro_all_effective_imbs_covered", "zpm_all_effective_imbs_covered",
    "znro_unique_imb_assignment", "zpm_unique_imb_assignment",
    "znro_regions_non_overlapping", "zpm_regions_non_overlapping",
    "znro_imbs_strictly_inside", "znro_cells_cover_partition",
    "zpm_cells_cover_required_imbs", "zpm_nested_in_znro",
    "znro_capacity_ok", "zpm_capacity_ok",
    "nro_facilities_in_regions", "pm_facilities_in_regions",
)
ALGORITHM_FILES = (
    "task2_pipeline.py", "znro_zpm_partition.py",
    "znro_zpm_partition_petch_atomic.py", "znro_zpm_region_partition.py",
    "building_group_partition.py", "task2_geometry_features.py",
    "prepare_task2_recognition_input.py", "clean_imb_topology_input.py",
)
TASK3_CAPACITY_RESERVE_RATIO = 0.15


def runtime_paths():
    plugin = Path(__file__).resolve().parent
    if (plugin / "engine").is_dir():
        return plugin / "engine", plugin / "engine" / "task2"
    root = plugin.parents[1]
    return root / "任务一_基础数据与场勘图层构建", root / "任务2" / "zone_partition" / "src"


def task3_runtime_path() -> Path:
    plugin = Path(__file__).resolve().parent
    packaged = plugin / "engine" / "task3"
    if packaged.is_dir():
        return packaged
    return plugin.parents[1] / "任务3"


def default_worker_python() -> str:
    """Never mistake qgis-bin.exe for a Python interpreter."""
    candidates = (Path(sys.prefix) / "python.exe", Path(sys.executable))
    for candidate in candidates:
        if candidate.is_file() and candidate.name.lower().startswith("python"):
            return str(candidate)
    return ""


def prepare_qgis_runtime():
    """Configure only this process; never install or replace runtime files.

    Eager loading avoids Windows ICU-forwarder / QGIS DLL load-order failures
    seen when starting Python directly rather than through the QGIS launcher.
    Keep the returned handles alive until the worker exits.
    """
    handles = []
    libraries = []
    if os.name == "nt" and os.environ.get("QGIS_PREFIX_PATH"):
        import ctypes

        # Some launchers inherit protected or stale directories from another
        # Windows account. qgis.__init__ registers every existing PATH item as
        # a DLL directory, and one inaccessible item can abort the import.
        safe_path = []
        for value in os.environ.get("PATH", "").split(os.pathsep):
            value = value.strip()
            if not value:
                continue
            try:
                if not Path(value).is_dir():
                    continue
                probe = os.add_dll_directory(value)
                probe.close()
            except (OSError, PermissionError):
                continue
            safe_path.append(value)
        os.environ["PATH"] = os.pathsep.join(safe_path)

        prefix = Path(os.environ["QGIS_PREFIX_PATH"])
        install = prefix.parent.parent
        system = Path(os.environ["SystemRoot"]) / "System32"
        for name in ("icu.dll", "icuuc.dll", "icuin.dll"):
            if (system / name).is_file():
                libraries.append(ctypes.WinDLL(str(system / name)))
        for directory in (install / "bin", prefix / "bin", install / "apps" / "qt6" / "bin",
                          install / "apps" / "Qt5" / "bin", Path(sys.prefix)):
            if directory.is_dir():
                handles.append(os.add_dll_directory(str(directory)))
        libraries.append(ctypes.WinDLL(str(prefix / "bin" / "qgis_core.dll")))
        gui_library = prefix / "bin" / "qgis_gui.dll"
        if gui_library.is_file():
            libraries.append(ctypes.WinDLL(str(gui_library)))
    return handles, libraries


def read_json(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON 顶层必须是对象：{path}")
    return data


def write_json(path, data):
    """Even diagnostic/config writes are atomic."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_frozen_algorithms(plugin_dir: Path) -> dict:
    """Fail before execution if any packaged Task 2-6 source changed."""
    manifest_path = plugin_dir / "algorithm_integrity_manifest.json"
    manifest = read_json(manifest_path)
    failures = []
    for item in manifest.get("files") or []:
        relative = str(item.get("path") or "")
        source = (plugin_dir / relative).resolve()
        try:
            source.relative_to(plugin_dir.resolve())
        except ValueError:
            failures.append(f"越界路径:{relative}")
            continue
        if not source.is_file():
            failures.append(f"缺失:{relative}")
        elif sha256_file(source) != str(item.get("sha256") or "").lower():
            failures.append(f"哈希不一致:{relative}")
    if failures:
        raise ValueError("算法2-6完整性校验失败：" + "；".join(failures[:20]))
    return {"status": "PASS", "file_count": len(manifest.get("files") or [])}


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _validate_recognition_input(payload: dict) -> dict:
    validation = payload.get("validation") or {}
    if validation.get("topology_and_recognition_bounds_overlap") is not True:
        raise ValueError(
            "本次 IMB 范围与识别地理要素范围不相交；拒绝混用不同区域或历史识别成果"
        )
    topology_bounds = validation.get("topology_bounds") or (payload.get("meta") or {}).get("bounds")
    if not topology_bounds:
        raise ValueError("任务一拓扑缺少 bounds，无法核验识别成果是否属于同一区域")
    center_lon = (float(topology_bounds["min_x"]) + float(topology_bounds["max_x"])) / 2.0
    center_lat = (float(topology_bounds["min_y"]) + float(topology_bounds["max_y"])) / 2.0
    if not (-12.0 <= center_lon < -6.0 and 0.0 <= center_lat <= 84.0):
        raise ValueError(
            "当前任务二识别几何阶段固定使用 EPSG:32629，仅接受 UTM 29N 范围内的本次数据；"
            "其他区域必须先将任务二计算 CRS 参数化，不能在错误米制坐标系下继续"
        )
    recognition = payload.get("recognition") or {}
    counts = {name: len(records or []) for name, records in recognition.items()}
    if not counts.get("roads"):
        raise ValueError("连续物理规划至少需要非空 GEO_ROAD；GEO_BUILDING 可由 OSM/本地矢量补充，正式需求仍以 IMB 为准")
    unsafe_ai = []
    unsafe_access = []
    for layer_name, records in recognition.items():
        for index, record in enumerate(records or []):
            source_text = " ".join(
                str(record.get(key) or "")
                for key in ("source", "source_kind", "candidate_origin")
            ).lower()
            if ("ai" in source_text or "remote_sensing" in source_text) and int(
                record.get("needs_review") or 0
            ) != 1:
                unsafe_ai.append(f"{layer_name}[{index}]")
            if layer_name == "access_candidates" and int(
                record.get("needs_review") or 0
            ) != 1:
                unsafe_access.append(f"{layer_name}[{index}]")
            if (
                layer_name == "access_candidates"
                and ("ai" in source_text or "remote_sensing" in source_text)
                and str(record.get("review_status") or "") != "待场勘"
            ):
                unsafe_access.append(f"{layer_name}[{index}]")
    if unsafe_ai:
        raise ValueError("AI 要素缺少 needs_review=1：" + ", ".join(unsafe_ai[:10]))
    if unsafe_access:
        raise ValueError(
            "接入候选必须 needs_review=1；AI 建筑接入候选还必须 review_status=待场勘："
            + ", ".join(unsafe_access[:10])
        )
    counts["_scope"] = {
        "overlap": True,
        "fully_covered": validation.get("topology_covers_recognition_extent") is True,
        "status": (
            "READY" if validation.get("topology_covers_recognition_extent") is True
            else "REVIEW_REQUIRED_PARTIAL_EXTENT"
        ),
    }
    return counts


def _planning_topology_with_recognition_roads(payload: dict) -> dict:
    """Create an internal Task-3 candidate graph without relabelling GEO_* layers.

    The returned nodes are explicitly marked PLANNING_ROAD_NODE and only exist
    inside this run's adapter input.  The source GeoPackage remains GEO_ROAD /
    GEO_ROAD_NODE and is never published as surveyed PTECH/INFRASTRUCTURE.
    """
    result = copy.deepcopy(payload)
    recognition = result.get("recognition") or {}
    road_nodes = recognition.get("road_nodes") or []
    roads = recognition.get("roads") or []
    if not road_nodes or not roads:
        raise ValueError("仅 IMB 模式继续任务三时，需要同一区域的 GEO_ROAD 与 GEO_ROAD_NODE 候选网络")

    nodes = result.setdefault("nodes", [])
    edges = result.setdefault("edges", [])
    existing_ids = {str(item.get("id") or "") for item in nodes}
    id_map: dict[str, str] = {}
    code_map: dict[str, str] = {}
    added_nodes = 0
    for index, item in enumerate(road_nodes, 1):
        source_id = str(item.get("node_id") or item.get("id") or f"RNODE-{index:05d}")
        geometry = item.get("geometry") or {}
        coordinates = geometry.get("coordinates") or []
        if len(coordinates) < 2:
            continue
        node_id = f"planning-road-node::{source_id}"
        code = f"PRN-{index:05d}"
        id_map[source_id] = node_id
        code_map[node_id] = code
        if node_id in existing_ids:
            continue
        nodes.append({
            "id": node_id,
            "type": "PETCH",
            "x": float(coordinates[0]),
            "y": float(coordinates[1]),
            "code": code,
            "role": "planning_route_candidate",
            "source_layer": "PLANNING_ROAD_NODE",
            "original_type": "ROAD_NODE_CANDIDATE",
            "candidate_origin": "GEO_ROAD_NODE",
            "needs_review": 1,
        })
        existing_ids.add(node_id)
        added_nodes += 1

    added_edges = 0
    for index, item in enumerate(roads, 1):
        source_id = id_map.get(str(item.get("from_node") or ""))
        target_id = id_map.get(str(item.get("to_node") or ""))
        coordinates = ((item.get("geometry") or {}).get("coordinates") or [])
        if source_id not in existing_ids or target_id not in existing_ids or len(coordinates) < 2:
            continue
        edges.append({
            "source": source_id,
            "target": target_id,
            "path": coordinates,
            "code": str(item.get("road_id") or item.get("id") or f"GEO-ROAD-{index:05d}"),
            "source_code": code_map[source_id],
            "target_code": code_map[target_id],
            "source_layer": "PLANNING_ROAD",
            "length": float(item.get("length_m") or 0.0),
            "candidate_origin": "GEO_ROAD",
            "needs_review": 1,
        })
        added_edges += 1
    if not added_nodes or not added_edges:
        raise ValueError("识别道路没有形成可供任务三使用的端点—边拓扑")
    result.setdefault("meta", {})["planning_candidate_graph"] = {
        "node_count": added_nodes,
        "edge_count": added_edges,
        "formal_business_layer": False,
        "review_required": True,
    }
    return result


def _point_distance_m(first: dict, second: dict) -> float:
    lon1, lat1 = math.radians(float(first["x"])), math.radians(float(first["y"]))
    lon2, lat2 = math.radians(float(second["x"])), math.radians(float(second["y"]))
    x = (lon2 - lon1) * math.cos((lat1 + lat2) / 2.0)
    y = lat2 - lat1
    return 6371008.8 * math.hypot(x, y)


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _prepare_task3_input(
    topology: dict,
    clusters: dict,
    regions: dict,
    output_dir: Path,
    *,
    split_oversized_imbs: bool = False,
    max_service_unit_ports: int = 48,
    physical_nro_capacity: int = 2048,
    physical_zpm_capacity: int = 576,
    max_snap_distance_m: float = 80.0,
) -> dict:
    """Convert the current Task-2 result into Task-3's explicit CSV contract."""
    nodes = {str(item["id"]): item for item in topology.get("nodes") or []}
    original_imbs = [item for item in nodes.values() if str(item.get("type") or "").upper() == "IMB"]
    technical_sources = {"PTECH", "SITE", "PLANNING_ROAD_NODE"}
    technical = [item for item in nodes.values() if str(item.get("source_layer") or "").upper() in technical_sources]
    znro_assignment = {str(key): str(value) for key, value in (clusters.get("znro", {}).get("assignment") or {}).items()}
    zpm_assignment = {str(key): str(value) for key, value in (clusters.get("zpm", {}).get("assignment") or {}).items()}
    missing = sorted(item["id"] for item in original_imbs if item["id"] not in znro_assignment or item["id"] not in zpm_assignment)
    if missing:
        raise ValueError(f"任务二分区没有覆盖任务三所需 IMB：{missing[:10]}")

    imbs = []
    unit_to_imb: dict[str, str] = {}
    for item in original_imbs:
        original_id = str(item["id"])
        original_code = str(item.get("code") or original_id)
        total = int(item.get("ports") or item.get("nb_loc_tot") or 0)
        residential = int(item.get("nb_loc_res", total) or 0)
        professional = int(item.get("nb_loc_pro", max(0, total - residential)) or 0)
        if residential + professional != total:
            raise ValueError(f"IMB {original_code} 住宅与专业需求之和不等于总需求")
        part_count = (
            max(1, math.ceil(total / max_service_unit_ports))
            if split_oversized_imbs
            else 1
        )
        remaining_total = total
        remaining_residential = residential
        remaining_professional = professional
        for part in range(1, part_count + 1):
            demand = min(max_service_unit_ports, remaining_total) if part_count > 1 else remaining_total
            unit_residential = min(remaining_residential, demand)
            unit_professional = min(remaining_professional, demand - unit_residential)
            unit_code = original_code if part_count == 1 else f"{original_code}__U{part:02d}"
            unit = dict(item)
            unit.update({
                "id": unit_code,
                "code": unit_code,
                "ports": demand,
                "nb_loc_tot": demand,
                "nb_loc_res": unit_residential,
                "nb_loc_pro": unit_professional,
                "parent_imb": original_code,
                "source_node_id": original_id,
            })
            imbs.append(unit)
            unit_to_imb[unit_code] = original_code
            remaining_total -= demand
            remaining_residential -= unit_residential
            remaining_professional -= unit_professional
        if remaining_total or remaining_residential or remaining_professional:
            raise ValueError(f"IMB {original_code} 服务单元拆分未守恒")

    def nearest_imb(point_node: dict) -> dict:
        return min(
            original_imbs,
            key=lambda item: (float(item["x"]) - float(point_node["x"])) ** 2
            + (float(item["y"]) - float(point_node["y"])) ** 2,
        )

    adjacent_ptech: dict[str, str] = {}
    for edge in topology.get("edges") or []:
        source_id, target_id = str(edge.get("source") or ""), str(edge.get("target") or "")
        if source_id in nodes and target_id in nodes:
            if str(nodes[source_id].get("type") or "").upper() == "IMB" and str(nodes[target_id].get("source_layer") or "").upper() in technical_sources:
                adjacent_ptech[source_id] = str(nodes[target_id].get("code") or target_id)
            if str(nodes[target_id].get("type") or "").upper() == "IMB" and str(nodes[source_id].get("source_layer") or "").upper() in technical_sources:
                adjacent_ptech[target_id] = str(nodes[source_id].get("code") or source_id)

    for item in original_imbs:
        source_id = str(item["id"])
        if source_id in adjacent_ptech or not technical:
            continue
        nearest = min(technical, key=lambda candidate: _point_distance_m(item, candidate))
        distance = _point_distance_m(item, nearest)
        if distance <= float(max_snap_distance_m):
            adjacent_ptech[source_id] = str(nearest.get("code") or nearest["id"])

    imb_rows = []
    for item in imbs:
        node_id = str(item["id"])
        source_node_id = str(item.get("source_node_id") or node_id)
        task4_attributes = dict(item.get("task4_attributes") or {})
        imb_rows.append({
            **task4_attributes,
            "CODE": str(item.get("code") or node_id),
            "PARENT_IMB": str(item.get("parent_imb") or item.get("code") or node_id),
            "NB_LOC_RES": int(item.get("nb_loc_res") or 0),
            "NB_LOC_PRO": int(item.get("nb_loc_pro") or 0),
            "NB_LOC_TOT": int(item.get("ports") or item.get("nb_loc_tot") or 0),
            "X": item["x"], "Y": item["y"],
            "NODE_CODE": adjacent_ptech.get(source_node_id, ""),
            "ZNRO_CODE": znro_assignment[source_node_id],
            "ZPM_CODE": zpm_assignment[source_node_id],
        })
    if any(not row["NODE_CODE"] for row in imb_rows):
        raise ValueError(f"存在距离候选网络超过 {float(max_snap_distance_m):g} m 的 IMB，无法为任务三建立吸附点")

    ptech_rows, site_rows = [], []
    for item in technical:
        owner = nearest_imb(item)
        is_planning_candidate = str(item.get("source_layer") or "").upper() == "PLANNING_ROAD_NODE"
        common = {
            "CODE": str(item.get("code") or item["id"]),
            "TYPE": str(item.get("original_type") or ("PM" if item.get("source_layer") == "SITE" else "CHAMBRE")),
            "STATUT": "PROJET" if is_planning_candidate else "EXISTANT", "X": item["x"], "Y": item["y"],
            "ZNRO_CODE": znro_assignment[str(owner["id"])],
            "ZPM_CODE": zpm_assignment[str(owner["id"])],
        }
        if str(item.get("source_layer") or "").upper() == "SITE":
            site_rows.append({**common, "REF_NRO": common["ZNRO_CODE"], "CODE_PTC": common["CODE"]})
        else:
            ptech_rows.append({
                **common, "NATURE": str(item.get("nature") or "CHAMBRE"),
                "ALLOWED_TYPES": "NRO,PM", "INSTALL_SPACE": "是",
                "MAINTAINABLE": "是", "PROPERTY_CLEAR": "是",
            })

    network_codes = {str(item.get("code") or item["id"]) for item in technical}
    infrastructure_rows = []
    for edge in topology.get("edges") or []:
        origin = str(edge.get("source_code") or "")
        destination = str(edge.get("target_code") or "")
        if origin in network_codes and destination in network_codes:
            infrastructure_rows.append({
                "CODE": str(edge.get("code") or f"EDGE_{len(infrastructure_rows) + 1:04d}"),
                "ORIGINE": origin, "EXTREMITE": destination,
                "LONGUEUR": float(edge.get("length") or 0.0),
                "TYPE": "PLANNING_ROAD" if str(edge.get("source_layer") or "").upper() == "PLANNING_ROAD" else "INFRASTRUCTURE",
                "STATUT": "PROJET" if str(edge.get("source_layer") or "").upper() == "PLANNING_ROAD" else "EXISTANT",
            })
    if not infrastructure_rows:
        raise ValueError("任务三至少需要一条正式 INFRASTRUCTURE 或本批次规划道路候选边")

    znro_rows = [{
        "CODE": str(item["id"]), "REF_NRO": str(item["id"]),
        "STATUT": "PROJET", "CAPACITE": int(physical_nro_capacity),
        "NIB_PRISES": int(item.get("total_ports") or 0),
    } for item in regions.get("znro_regions") or []]
    zpm_rows = [{
        "CODE": str(item["id"]), "REF_NRO": str(item.get("parent_znro_id") or ""),
        "REF_PM": "", "STATUT": "PROJET",
        "CAPACITE": int(physical_zpm_capacity),
        "NIB_PRISES": int(item.get("total_ports") or 0),
    } for item in regions.get("zpm_regions") or []]
    if not znro_rows or not zpm_rows:
        raise ValueError("任务二没有生成任务三所需 ZNRO/ZPM 分区")

    tables = {
        "IMB.csv": (imb_rows, ["CODE", "NB_LOC_RES", "NB_LOC_PRO", "NB_LOC_TOT", "X", "Y", "NODE_CODE", "ZNRO_CODE", "ZPM_CODE"]),
        "PTECH.csv": (ptech_rows, ["CODE", "TYPE", "NATURE", "STATUT", "X", "Y", "ZNRO_CODE", "ZPM_CODE", "ALLOWED_TYPES", "INSTALL_SPACE", "MAINTAINABLE", "PROPERTY_CLEAR"]),
        "SITE.csv": (site_rows, ["CODE", "REF_NRO", "TYPE", "STATUT", "CODE_PTC", "X", "Y", "ZNRO_CODE", "ZPM_CODE"]),
        "INFRASTRUCTURE.csv": (infrastructure_rows, ["CODE", "ORIGINE", "EXTREMITE", "TYPE", "STATUT", "LONGUEUR"]),
        "ZNRO.csv": (znro_rows, ["CODE", "REF_NRO", "STATUT", "CAPACITE", "NIB_PRISES"]),
        "ZPM.csv": (zpm_rows, ["CODE", "REF_NRO", "REF_PM", "STATUT", "CAPACITE", "NIB_PRISES"]),
    }
    for filename, (rows, fields) in tables.items():
        if filename == "IMB.csv":
            extra_fields = [
                "PARENT_IMB", "BPE_CODE", "REF_PLAQUE", "TYPE_BATIM",
                "RACCORDEME", "TYPE_VOIE", "NOM_VOIE", "NOM_BATIME",
                "NUMERO_VOI", "VILLE", "CODE_POSTA",
            ]
            fields = [*fields, *extra_fields]
        _write_csv(output_dir / filename, rows, fields)
    write_json(
        output_dir / "service_unit_map.json",
        {
            "schema_version": "fh.service-units.v1",
            "max_ports": max_service_unit_ports,
            "split_enabled": bool(split_oversized_imbs),
            "unit_to_imb": unit_to_imb,
        },
    )
    return {"IMB": len(imb_rows), "original_IMB": len(original_imbs), "PTECH": len(ptech_rows), "SITE": len(site_rows),
            "INFRASTRUCTURE": len(infrastructure_rows), "ZNRO": len(znro_rows), "ZPM": len(zpm_rows),
            "service_unit_map": str(output_dir / "service_unit_map.json")}


def _run_task3(input_dir: Path, output_dir: Path, fixed_pm_path: Path) -> dict:
    runtime = task3_runtime_path().resolve()
    if not (runtime / "nro_pm_selector" / "selector.py").is_file():
        raise FileNotFoundError(f"任务三运行包缺失：{runtime}")
    if str(runtime) not in sys.path:
        sys.path.insert(0, str(runtime))
    from nro_pm_selector.io import InputBundle, write_outputs
    from nro_pm_selector.selector import SelectionEngine
    from task3_to_task6_adapter import build_fixed_pm_plan

    bundle = InputBundle.load(input_dir)
    selection = SelectionEngine(bundle).run()
    if selection.validation.get("passed") is not True:
        raise ValueError(f"任务三选址校验失败：{selection.validation.get('errors')}")
    write_outputs(output_dir, bundle, selection.selections, selection.rejections, selection.validation)
    fixed = build_fixed_pm_plan(input_dir, output_dir, fixed_pm_path)
    return {
        "status": "success", "mode": "deterministic_baseline",
        "validation": selection.validation, "fixed_pm_counts": fixed.get("counts"),
        "input_dir": str(input_dir), "output_dir": str(output_dir),
        "fixed_pm_plan": str(fixed_pm_path),
    }


def _downstream_readiness(
    task2_payload: dict,
    task3_result: dict | None,
    *,
    split_oversized_imbs: bool = False,
    use_formal_infrastructure: bool = False,
    use_recognition_roads: bool = False,
) -> dict:
    imbs = [item for item in task2_payload.get("nodes") or [] if str(item.get("type") or "").upper() == "IMB"]
    oversized = [{
        "id": str(item.get("code") or item.get("id")),
        "ports": int(item.get("ports") or item.get("nb_loc_tot") or 0),
    } for item in imbs if int(item.get("ports") or item.get("nb_loc_tot") or 0) > 48]
    roads = (task2_payload.get("recognition") or {}).get("roads") or []
    missing_road_attributes = []
    for item in roads:
        identifier = str(item.get("road_id") or item.get("id") or item.get("code") or "")
        missing = [key for key in ("OPEN_CUT_ALLOWED", "DUCT_CAPACITY") if item.get(key) in (None, "")]
        if missing:
            missing_road_attributes.append({"id": identifier, "missing": missing})
    task4_blockers = []
    if oversized and not split_oversized_imbs:
        task4_blockers.append({
            "code": "TASK4_SINGLE_PBO_CAPACITY_EXCEEDED",
            "message": "任务四当前是一栋 IMB 对应一个 PBO，支持容量仅 12/24/48；必须先确认多 PBO 拆分规则。",
            "records": oversized,
        })
    task6_blockers = [{
        "code": "TASK4_PLAN_NOT_AVAILABLE",
        "message": "任务六需要任务四确认后的 BPE/PBO 固定关系。",
    }]
    if not use_formal_infrastructure and not use_recognition_roads:
        task6_blockers.append({"code": "ROUTABLE_SOURCE_UNCONFIRMED", "message": "尚未显式确认任务六使用的道路/管道来源。"})
    if use_recognition_roads and not roads:
        task6_blockers.append({"code": "GEO_ROAD_MISSING", "message": "已选择识别道路，但本批次没有 GEO_ROAD。"})
    recognition_assumptions = []
    if use_recognition_roads and missing_road_attributes:
        recognition_assumptions.append({
            "code": "GEO_ROAD_PLANNING_ASSUMPTION_CONFIRMED",
            "message": "用户已确认把缺少施工属性的识别道路用于本批次新建候选方案；控制器使用界面容量值，成果保持待复核。",
            "records": missing_road_attributes,
        })
    return {
        "schema_version": "ftth.downstream-readiness.v1",
        "overall_status": "READY_FOR_TASK4" if task3_result and not task4_blockers else "BLOCKED_BEFORE_TASK4",
        "task3": {"status": "COMPLETED" if task3_result else "NOT_RUN", "result": task3_result},
        "task4": {"status": "BLOCKED" if task4_blockers else "READY", "blockers": task4_blockers},
        "task6": {"status": "BLOCKED", "blockers": task6_blockers},
        "task5": {"status": "WAITING_FOR_TASK6", "blockers": [{"code": "TASK6_PLAN_NOT_AVAILABLE", "message": "任务五只能读取本批次任务六管网方案。"}]},
        "assumptions_applied": recognition_assumptions,
        "user_confirmations": {
            "split_oversized_imbs": bool(split_oversized_imbs),
            "use_formal_infrastructure": bool(use_formal_infrastructure),
            "use_recognition_roads": bool(use_recognition_roads),
        },
        "note": "本报告不把就绪检查冒充算法结果；识别道路只有在用户勾选确认后才作为新建候选网络使用。",
    }


def _staged_config(config_path, stage):
    config = copy.deepcopy(read_json(config_path))
    topology = config.setdefault("task2_topology", {})
    if topology.get("enabled", True) is not True:
        raise ValueError("任务一已关闭 task2_topology；请开启后再运行连接。")
    # Moving the config must not change how its relative source paths resolve.
    for layer in [*config.get("vector_layers", []), *config.get("rasters", [])]:
        raw_source = str(layer.get("source") or "").strip()
        if not raw_source:
            # IMB-only configurations intentionally carry empty formal
            # context layers. Do not turn "" into the config directory.
            layer["source"] = ""
            continue
        source = Path(raw_source).expanduser()
        layer["source"] = str(source.resolve() if source.is_absolute() else (config_path.parent / source).resolve())
    topology.update(enabled=True, output_json="base/qgis_ftth_dataset.json")
    config.setdefault("output", {}).update(
        geopackage="base/base_survey_layers.gpkg",
        quality_report="base/data_quality_report.json",
        add_to_project=False, apply_style=False, capture_screenshot=False,
    )
    target = stage / "task1_config.json"
    write_json(target, config)
    return target


def _run_task1(config_path):
    from run_task1 import run

    return run(config_path, validate_only=False)


def _run_task2(input_json, output_dir, source_dir, parameters):
    """Call the frozen task-2 entry points through their published CLI contract.

    The original ``task2_pipeline.py`` is retained byte-for-byte.  Its folder
    constants describe the author's Chinese project tree, so the portable QGIS
    package assembles the same command arguments here instead of editing it.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    def command(script_name, *arguments):
        return [sys.executable, str(source_dir / script_name), *(str(value) for value in arguments)]

    cluster_json = output_dir / "qgis_petch_atomic_result.json"
    cleaned_input = output_dir / "task2_imb_only_input.json"
    commands = [command(
        "clean_imb_topology_input.py",
        "--input-json", input_json,
        "--output-json", cleaned_input,
    ), command(
        "znro_zpm_partition_petch_atomic.py",
        "--input-json", cleaned_input,
        "--output-json", cluster_json,
        "--output-fig", output_dir / "qgis_petch_atomic_result.png",
        "--nro-max-cap", parameters["nro_max_cap"],
        "--zpm-max-cap", parameters["zpm_max_cap"],
        "--spatial-weight", parameters["spatial_weight"],
        "--pipe-weight", parameters["pipe_weight"],
    ), command(
        "znro_zpm_region_partition.py",
        "--input-json", cluster_json,
        "--source-data-json", cleaned_input,
        "--output-json", output_dir / "qgis_direct_region_partition_result.json",
        "--output-fig", output_dir / "qgis_direct_region_partition_result.png",
        "--nro-max-cap", parameters["nro_max_cap"],
        "--zpm-max-cap", parameters["zpm_max_cap"],
        "--spatial-weight", parameters["spatial_weight"],
        "--pipe-weight", parameters["pipe_weight"],
    )]
    if parameters.get("building_groups_from_input"):
        commands.append(command(
            "building_group_partition.py",
            "--input-json", input_json,
            "--distance-threshold-m", parameters.get("building_threshold_m", 15.0),
            "--output-json", output_dir / "building_groups.json",
            "--output-csv", output_dir / "building_group_members.csv",
            "--output-fig", output_dir / "building_groups.png",
        ))
    if parameters.get("geometry_features_from_input"):
        commands.append(command(
            "task2_geometry_features.py",
            "--buildings", input_json,
            "--roads", input_json,
            "--partition-json", output_dir / "qgis_direct_region_partition_result.json",
            "--output-json", output_dir / "task2_geometry_features.json",
            "--output-fig", output_dir / "task2_geometry_features.png",
            "--task6-output-dir", output_dir,
            "--building-crs", parameters.get("geometry_building_crs", "EPSG:4326"),
            "--road-crs", parameters.get("geometry_road_crs", "EPSG:4326"),
            "--partition-crs", parameters.get("geometry_partition_crs", "EPSG:4326"),
            "--distance-threshold-m", parameters.get("geometry_distance_threshold_m", 15.0),
            "--road-snap-tolerance-m", parameters.get("geometry_road_snap_tolerance_m", 2.0),
        ))
    for command in commands:
        script = Path(command[1]).resolve()
        if script.parent != source_dir.resolve() or script.name not in ALGORITHM_FILES:
            raise ValueError(f"连接只允许任务二分区入口：{script}")
        print(f"[bridge] {script.name} 输入：{input_json}", flush=True)
        previous = sys.argv
        try:
            sys.argv = command[1:]
            runpy.run_path(str(script), run_name="__main__")
        except SystemExit as exc:
            if exc.code not in (None, 0):
                raise RuntimeError(f"任务二退出：{script.name} ({exc.code})") from exc
        finally:
            sys.argv = previous


def _check_results(cluster, regions, input_path, cluster_path, summary):
    if Path(cluster.get("source_file", "")).resolve() != input_path.resolve():
        raise ValueError("任务二读取的不是本次任务一输出。")
    if Path(regions.get("source_data_file") or "").resolve() != input_path.resolve():
        raise ValueError("区域面阶段的拓扑来源与本次任务一输出不一致。")
    if Path(regions.get("source_cluster_result") or "").resolve() != cluster_path.resolve():
        raise ValueError("区域面阶段未使用本次分区结果。")
    for key, expected in (("node_count", summary["node_count"]), ("edge_count", summary["edge_count"]),
                          ("imb_count", summary["imb_count"]), ("total_ports", summary["total_imb_ports"])):
        if cluster.get("input_summary", {}).get(key) != expected:
            raise ValueError(f"任务二输入统计不一致：{key}")
    failures = []
    for layer in ("znro", "zpm"):
        required = ["full_coverage", "unique_assignment", "non_overlapping_by_imb", "capacity_ok"]
        if layer == "zpm":
            required += ["nested_in_znro", "petch_atomicity_ok"]
        checks = cluster.get(layer, {}).get("validation", {})
        failures += [f"{layer}.{key}" for key in required if checks.get(key) is not True]
    failures += [key for key in REGION_CHECKS if regions.get("validation", {}).get(key) is not True]
    if failures:
        raise ValueError("任务二结构校验未通过：" + ", ".join(failures))


def region_geojson(result, name):
    """Serialize existing polygon coordinates only; do not buffer or infer areas."""
    features = []
    for region in result.get(f"{name.lower()}_regions", []):
        geometry = region["geometry"]
        polygons = ([geometry["polygon"]] if geometry.get("polygon")
                    else [part["polygon"] for part in geometry.get("polygons", [])])
        if not polygons:
            raise ValueError(f"{name} 区域面为空")
        for part_index, polygon in enumerate(polygons, start=1):
            ring = [list(point) for point in polygon]
            if len(ring) < 3:
                raise ValueError(f"{name} 区域面坐标不足")
            for point in ring:
                if (len(point) != 2 or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in point)
                        or not -180 <= point[0] <= 180 or not -90 <= point[1] <= 90):
                    raise ValueError(f"{name} 区域面不是有效的 [lon, lat] 坐标")
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            # Adjacent Voronoi cells share edges and would make an invalid
            # MultiPolygon. Preserve them as separate Polygon features with
            # explicit region membership, without changing/unioning geometry.
            properties = {
                "CODE": f"{region['id']}__part_{part_index}",
                "region_id": region["id"],
                "part_index": part_index,
                "parent_znro_id": region.get("parent_znro_id"),
                "region_total_ports": region["total_ports"],
                "CAPACITE": region.get("capacity_limit"),
                "STATUT": "PROJET",
                "needs_review": 1,
                "source": "task2:planning",
                "review_status": "待复核",
            }
            if name.upper() == "ZNRO":
                properties["REF_NRO"] = region["id"]
            else:
                properties["REF_NRO"] = region.get("parent_znro_id")
                properties["REF_ZNRO"] = region.get("parent_znro_id")
            features.append({
                "type": "Feature",
                "properties": properties,
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            })
    if not features:
        raise ValueError(f"{name} 无分区结果")
    return {"type": "FeatureCollection", "features": features}


def _relocate(value, stage, target):
    """Keep recorded output paths valid after the staging directory is renamed."""
    if isinstance(value, dict):
        return {key: _relocate(item, stage, target) for key, item in value.items()}
    if isinstance(value, list):
        return [_relocate(item, stage, target) for item in value]
    if isinstance(value, str) and (value == str(stage) or value.startswith(str(stage) + os.sep)):
        return str(target) + value[len(str(stage)):]
    return value


def run_connected(
    config_path,
    output_root=None,
    *,
    recognition_gpkg=None,
    run_task3=False,
    run_tasks4_to6=False,
    split_oversized_imbs=False,
    use_formal_infrastructure=False,
    use_recognition_roads=False,
    formal_duct_capacity=2048,
    pbo_offset_m=5.0,
    candidate_paths=5,
    water_cost_factor=2.0,
    ptech_max_spacing_m=200.0,
    terminal_snap_distance_m=80.0,
    ga_refine=True,
    ga_population=14,
    ga_generations=10,
    nro_max_cap=2048,
    zpm_max_cap=576,
    spatial_weight=1.0,
    pipe_weight=100.0,
):
    """Return success/failed; never replace any existing task outputs."""
    stage = None
    phase = "preflight"
    previous_path = list(sys.path)
    try:
        config_path = Path(config_path).expanduser().resolve(strict=True)
        recognition_gpkg = (
            Path(recognition_gpkg).expanduser().resolve(strict=True)
            if recognition_gpkg
            else None
        )
        if run_tasks4_to6 and not run_task3:
            raise ValueError("运行任务四至六前必须勾选并完成任务三")
        if run_tasks4_to6 and not split_oversized_imbs:
            # Whether splitting is actually needed is checked after Task 1;
            # requiring the explicit policy here keeps the downstream run auditable.
            raise ValueError("运行任务四至六必须显式确认超过48端口的IMB按服务单元拆分")
        # Whether a formal network exists is known only after Task 1 has built
        # the current project's topology.  Defer the road-source check so a
        # supplied INFRASTRUCTURE layer is used automatically rather than being
        # accidentally ignored by an unchecked UI option.
        physical_parameters = dict(nro_max_cap=nro_max_cap, zpm_max_cap=zpm_max_cap,
                                   spatial_weight=spatial_weight, pipe_weight=pipe_weight)
        if (any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in (nro_max_cap, zpm_max_cap))
                or any(not math.isfinite(value) or value < 0 for value in (spatial_weight, pipe_weight))
                or spatial_weight + pipe_weight <= 0):
            raise ValueError("容量须为正整数，距离权重须为非负有限数且不能同时为零。")
        engine, task2 = runtime_paths()
        algorithm_integrity = verify_frozen_algorithms(Path(__file__).resolve().parent)
        parameters = dict(physical_parameters)
        if run_task3 and not run_tasks4_to6:
            # Task 3 reserves 15% inside its unchanged selector.  Partitioning
            # therefore uses the maximum assignable demand while Task 3 still
            # receives the physical equipment capacities selected by the user.
            parameters["nro_max_cap"] = max(1, math.floor(nro_max_cap / (1 + TASK3_CAPACITY_RESERVE_RATIO)))
            parameters["zpm_max_cap"] = max(1, math.floor(zpm_max_cap / (1 + TASK3_CAPACITY_RESERVE_RATIO)))
        required_sources = [engine / "run_task1.py", *(task2 / name for name in ALGORITHM_FILES)]
        if run_task3:
            task3_runtime = task3_runtime_path()
            required_sources.extend(
                [
                    task3_runtime / "task3_to_task6_adapter.py",
                    task3_runtime / "nro_pm_selector" / "selector.py",
                    task3_runtime / "nro_pm_selector" / "io.py",
                ]
            )
        for path in required_sources:
            if not path.is_file():
                raise FileNotFoundError(f"连接所需源码缺失：{path}")
        sys.path[:0] = [str(engine), str(task2)]
        from task2_topology_adapter import validate_task2_topology_payload

        # Reject disabled topology before creating staging or invoking Task 1.
        if read_json(config_path).get("task2_topology", {}).get("enabled", True) is not True:
            raise ValueError("任务一已关闭 task2_topology；请开启后再运行连接。")
        root = Path(output_root).expanduser().resolve() if output_root else config_path.parent / "outputs" / "connected_runs"
        root.mkdir(parents=True, exist_ok=True)
        run_id = datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ_") + uuid.uuid4().hex
        final = root / run_id
        stage = Path(tempfile.mkdtemp(prefix=".staging_", dir=root)).resolve()
        staged_config = _staged_config(config_path, stage)
        phase = "task1"
        print("[bridge] 生成本次基础图层及拓扑 JSON…", flush=True)
        base = _run_task1(staged_config)
        if base.get("status") != "success":
            raise ValueError(f"任务一失败：{base.get('errors', base)}")
        input_json = stage / "base" / "qgis_ftth_dataset.json"
        if Path(base.get("task2_topology_json") or "").resolve() != input_json:
            raise ValueError("任务一没有返回本次 staging 拓扑 JSON；拒绝回退到历史文件。")
        topology = read_json(input_json)
        summary = validate_task2_topology_payload(topology)
        if not summary["imb_count"]:
            raise ValueError("连接至少需要非空 IMB 原始需求图层。")
        if topology.get("edges"):
            use_formal_infrastructure = True
        # Task 1's source label refers to its internal, already-renamed GPKG
        # staging file. Normalize only this provenance field before Task 2
        # reads the JSON so the recorded hash remains byte-for-byte auditable.
        topology.setdefault("meta", {})["source"] = str(final / "base" / "base_survey_layers.gpkg")
        write_json(input_json, topology)
        base_digest = sha256_file(input_json)
        task2_input = input_json
        recognition_counts = None
        recognition_source = recognition_gpkg
        task2_payload = topology
        if recognition_source is not None:
            phase = "task2_input"
            print("[bridge] 合成本次拓扑与本次识别输入…", flush=True)
            from prepare_task2_recognition_input import (
                bounds_cover,
                bounds_overlap,
                build_input,
                feature_bounds,
            )

            combined = build_input(input_json, recognition_source)
            # The approved handoff model is road-only.  In an IMB-led project,
            # road geometry is sufficient for scope matching; GEO_BUILDING is
            # optional context and must never replace the formal IMB demand.
            if not (combined.get("recognition") or {}).get("building_groups"):
                recognition_geometry = []
                for values in (combined.get("recognition") or {}).values():
                    recognition_geometry.extend(values or [])
                geometry_bounds = feature_bounds(recognition_geometry)
                topology_bounds = (combined.get("validation") or {}).get("topology_bounds")
                combined.setdefault("validation", {})["recognition_geometry_bounds"] = geometry_bounds
                combined["validation"]["topology_and_recognition_bounds_overlap"] = bounds_overlap(
                    topology_bounds, geometry_bounds
                )
                combined["validation"]["topology_covers_recognition_extent"] = bounds_cover(
                    topology_bounds, geometry_bounds
                )
            combined.setdefault("meta", {})["topology_source"] = str(
                final / "base" / "qgis_ftth_dataset.json"
            )
            combined["meta"]["recognition_source"] = (
                str(Path(_relocate(str(recognition_source), stage, final)))
                if _inside(recognition_source, stage)
                else str(recognition_source)
            )
            recognition_counts = _validate_recognition_input(combined)
            task2_payload = combined
            task2_input = stage / "task2" / "input" / "task2_recognition_input.json"
            write_json(task2_input, combined)
            parameters.update(
                building_groups_from_input=True,
                geometry_features_from_input=True,
            )
        digest = sha256_file(task2_input)
        phase = "task2"
        task2_output = stage / "task2"
        _run_task2(task2_input, task2_output, task2, parameters)
        phase = "validation"
        if sha256_file(task2_input) != digest:
            raise ValueError("任务二运行期间本次输入 JSON 发生变化。")
        cluster_path = task2_output / "qgis_petch_atomic_result.json"
        region_path = task2_output / "qgis_direct_region_partition_result.json"
        cluster, regions = read_json(cluster_path), read_json(region_path)
        cleaned_task2_input = task2_output / "task2_imb_only_input.json"
        cleaned_summary = validate_task2_topology_payload(read_json(cleaned_task2_input))
        _check_results(cluster, regions, cleaned_task2_input, cluster_path, cleaned_summary)
        for name in ("ZNRO", "ZPM"):
            write_json(task2_output / f"{name}_candidate.geojson", region_geojson(regions, name))
        required_outputs = [
            stage / "base" / "base_survey_layers.gpkg",
            task2_output / "qgis_petch_atomic_result.png",
            task2_output / "qgis_direct_region_partition_result.png",
        ]
        if recognition_source is not None:
            required_outputs.extend(
                [
                    task2_output / "building_groups.json",
                    task2_output / "task2_geometry_features.json",
                    task2_output / "task2_geometry_features.png",
                ]
            )
        for path in required_outputs:
            if not path.is_file() or not path.stat().st_size:
                raise ValueError(f"成果缺失：{path}")
        json_paths = [
            cluster_path,
            region_path,
            stage / "base" / "data_quality_report.json",
        ]
        if recognition_source is not None:
            json_paths.extend(
                [
                    task2_output / "building_groups.json",
                    task2_output / "task2_geometry_features.json",
                ]
            )
        for path in json_paths:
            write_json(path, _relocate(read_json(path), stage, final))

        task3_result = None
        if run_task3 and not run_tasks4_to6:
            phase = "task3"
            print("[bridge] 将本次任务二结果转换为任务三输入并运行确定性选址…", flush=True)
            task3_payload = task2_payload
            current_summary = validate_task2_topology_payload(task3_payload)
            if not current_summary["ptech_count"] or not current_summary["edge_count"]:
                if recognition_source is None or not use_recognition_roads:
                    raise ValueError(
                        "任务三需要正式 PTECH+INFRASTRUCTURE，或勾选本批次识别道路作为"
                        "待复核的新建候选网络；只有 IMB 且没有路径来源时不能继续。"
                    )
                task3_payload = _planning_topology_with_recognition_roads(task2_payload)
            task3_input_dir = stage / "task3" / "input"
            task3_output_dir = stage / "task3" / "output"
            fixed_pm_path = stage / "task3" / "pm_fixed_plan.json"
            task3_input_counts = _prepare_task3_input(
                task3_payload,
                cluster,
                regions,
                task3_input_dir,
                split_oversized_imbs=split_oversized_imbs,
                physical_nro_capacity=nro_max_cap,
                physical_zpm_capacity=zpm_max_cap,
                max_snap_distance_m=terminal_snap_distance_m,
            )
            task3_result = _run_task3(task3_input_dir, task3_output_dir, fixed_pm_path)
            task3_result["input_counts"] = task3_input_counts
            task3_required_outputs = [
                task3_output_dir / "SITE_selected.csv",
                task3_output_dir / "PTECH_selected.csv",
                task3_output_dir / "ZNRO_selected.csv",
                task3_output_dir / "ZPM_selected.csv",
                task3_output_dir / "selection_scores.csv",
                task3_output_dir / "validation_report.json",
                fixed_pm_path,
            ]
            for path in task3_required_outputs:
                if not path.is_file() or not path.stat().st_size:
                    raise ValueError(f"任务三成果缺失：{path}")
            for path in (task3_output_dir / "validation_report.json", fixed_pm_path):
                write_json(path, _relocate(read_json(path), stage, final))
            task3_result = _relocate(task3_result, stage, final)

        downstream_report = _downstream_readiness(
            task2_payload,
            task3_result,
            split_oversized_imbs=split_oversized_imbs,
            use_formal_infrastructure=use_formal_infrastructure,
            use_recognition_roads=use_recognition_roads,
        )
        downstream_result = None
        if run_tasks4_to6:
            phase = "tasks4_to6"
            print("[bridge] 启动更新算法连续控制器…", flush=True)
            try:
                from .downstream_controller import run_downstream
            except ImportError:
                from downstream_controller import run_downstream

            downstream_result = run_downstream(
                stage,
                topology=topology,
                task2_payload=task2_payload,
                cluster=cluster,
                use_formal_infrastructure=use_formal_infrastructure,
                use_recognition_roads=use_recognition_roads,
                formal_duct_capacity=int(formal_duct_capacity),
                pbo_offset_m=float(pbo_offset_m),
                candidate_paths=int(candidate_paths),
                water_cost_factor=float(water_cost_factor),
                ptech_max_spacing_m=float(ptech_max_spacing_m),
                terminal_snap_distance_m=float(terminal_snap_distance_m),
                ga_refine=bool(ga_refine),
                ga_population=int(ga_population),
                ga_generations=int(ga_generations),
            )
            task3_result = downstream_result["task3"]
            downstream_report = {
                "schema_version": "ftth.downstream-readiness.v2.0",
                "overall_status": "COMPLETED_THROUGH_TASK5",
                "task3": {"status": "COMPLETED", "result": downstream_result["task3"]},
                "task4": {"status": "COMPLETED", "result": downstream_result["task4"]},
                "task6": {"status": "COMPLETED", "result": downstream_result["task6"]},
                "task3_ga_refinement": {
                    "status": "COMPLETED" if downstream_result.get("task3_ga_refinement") else "NOT_REQUESTED",
                    "result": downstream_result.get("task3_ga_refinement"),
                },
                "task5": {"status": "COMPLETED", "result": downstream_result["task5"]},
                "assumptions_applied": [],
                "user_confirmations": downstream_result["user_confirmations"],
                "note": "按 FTTH(4) run_pipeline 顺序执行任务四初算、任务三优化、任务四定稿、任务六、任务五和最终八图层；全部只读取本批次 staging 成果。",
            }
            # 下游算法会把输入来源写进自己的 JSON。发布前统一把临时
            # staging 路径改成最终批次目录，避免换机或重启后审计路径失效。
            for path in (
                stage / "task6" / "base_scene.json",
                stage / "task6" / "base_input_audit.json",
                stage / "task6" / "task6_input.json",
                stage / "task6" / "layered_plan.json",
                stage / "task5" / "fiber_link_plan.json",
                stage / "pipeline_handoff_validation.json",
                stage / "final_layers" / "field_validation.json",
            ):
                if path.is_file():
                    write_json(path, _relocate(read_json(path), stage, final))
            downstream_result = _relocate(downstream_result, stage, final)
            downstream_report = _relocate(downstream_report, stage, final)
        downstream_path = stage / "downstream_readiness.json"
        write_json(downstream_path, _relocate(downstream_report, stage, final))

        manifest_path = stage / "run_manifest.json"
        manifest_artifacts = {
            "task1_geopackage": stage / "base" / "base_survey_layers.gpkg",
            "task1_topology": input_json,
            "task2_input": task2_input,
            "task2_clusters": cluster_path,
            "task2_regions": region_path,
            "znro_candidate": task2_output / "ZNRO_candidate.geojson",
            "zpm_candidate": task2_output / "ZPM_candidate.geojson",
            "downstream_readiness": downstream_path,
        }
        if recognition_source is not None:
            manifest_artifacts.update(
                recognition_geopackage=recognition_source,
                task2_building_groups=task2_output / "building_groups.json",
                task2_geometry_features=task2_output / "task2_geometry_features.json",
            )
        if task3_result is not None:
            manifest_artifacts.update(
                task3_fixed_pm=stage / "task3" / "pm_fixed_plan.json",
                task3_validation=stage / "task3" / "output" / "validation_report.json",
                task3_sites=stage / "task3" / "output" / "SITE_selected.csv",
                task3_ptech=stage / "task3" / "output" / "PTECH_selected.csv",
                task3_service_units=stage / "task3" / "input" / "service_unit_map.json",
            )
        if downstream_result is not None:
            manifest_artifacts.update(
                task4_boite=stage / "task4" / "output" / "BOITE_ALGORITHM.shp",
                task4_mapping=stage / "task4" / "output" / "IMB_PBO_BPE_mapping.csv",
                task6_input=stage / "task6" / "task6_input.json",
                task6_plan=stage / "task6" / "layered_plan.json",
                task6_routes=stage / "task6" / "DUCT_ROUTE.geojson",
                task6_visualization=stage / "task6" / "layered_plan.png",
                task5_plan=stage / "task5" / "fiber_link_plan.json",
                task5_links=stage / "task5" / "fiber_links.csv",
                task5_geojson=stage / "task5" / "fiber_links.geojson",
                task5_visualization=stage / "task5" / "fiber_link_overview.png",
                pipeline_handoff_validation=stage / "pipeline_handoff_validation.json",
                final_layer_validation=stage / "final_layers" / "field_validation.json",
                final_infrastructure=stage / "final_layers" / "INFRASTRUCTURE.shp",
            )
            if downstream_result.get("task3_ga_refinement"):
                manifest_artifacts.update(
                    task3_ga_report=stage / "task3" / "output" / "pm_optimization_report.json",
                    task3_ga_figure=stage / "task3" / "output" / "pm_optimization.png",
                )
        manifest = {
            "schema_version": "ftth.connected-run.v2.0",
            "status": "success",
            "data_status": "REVIEW_REQUIRED" if recognition_source is not None else "FORMAL_INPUT_WITH_PLANNING_CANDIDATES",
            "run_id": run_id,
            "source_config": str(config_path),
            "source_recognition_geopackage": str(recognition_source) if recognition_source else None,
            "scope_validation": (
                read_json(task2_input).get("validation") if recognition_source is not None else None
            ),
            "artifacts": {
                name: {
                    "path": _relocate(str(path), stage, final),
                    "sha256": sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
                for name, path in manifest_artifacts.items()
            },
        }
        write_json(manifest_path, manifest)
        release_status = "NOT_REQUESTED"
        if downstream_result is not None:
            task6_status = ((downstream_result.get("task6") or {}).get("validation") or {}).get("status")
            task5_status = ((downstream_result.get("task5") or {}).get("validation") or {}).get("status")
            release_status = (
                "READY_FOR_5_6_VALIDATION"
                if task6_status == "PASS" and task5_status == "PASS"
                else "REVIEW_REQUIRED_BEFORE_5_6_VALIDATION"
            )
        result = {
            "status": "success", "bridge_version": "2.4.2",
            "release_status": release_status,
            "algorithm_integrity": algorithm_integrity,
            "scope": (
                "task1_recognition_to_task5"
                if recognition_source is not None and run_tasks4_to6
                else "task1_to_task5"
                if run_tasks4_to6
                else "task1_recognition_to_task3"
                if recognition_source is not None and run_task3
                else "task1_to_task3"
                if run_task3
                else "task1_recognition_to_task2"
                if recognition_source is not None
                else "task1_to_task2"
            ),
            "output_dir": str(final), "source_config": str(config_path),
            "geopackage": str(stage / "base" / "base_survey_layers.gpkg"),
            "task1_topology_json": str(input_json), "task2_input_json": str(task2_input),
            "task1_topology_sha256": base_digest,
            "input_sha256": digest, "input_summary": summary,
            "parameters": {
                "physical_capacity": physical_parameters,
                "task2_effective_demand_capacity": {
                    "nro_max_cap": parameters["nro_max_cap"],
                    "zpm_max_cap": parameters["zpm_max_cap"],
                    "task3_reserve_ratio": (
                        TASK3_CAPACITY_RESERVE_RATIO
                        if run_task3 and not run_tasks4_to6
                        else 0.0
                    ),
                },
                "task2_options": {
                    key: value for key, value in parameters.items()
                    if key not in {"nro_max_cap", "zpm_max_cap"}
                },
            },
            "task2_cluster_json": str(cluster_path), "task2_region_json": str(region_path),
            "candidate_layers": {name: str(task2_output / f"{name}_candidate.geojson") for name in ("ZNRO", "ZPM")},
            "region_counts": {name: len(regions[f"{name.lower()}_regions"]) for name in ("ZNRO", "ZPM")},
            "checks": regions["validation"],
            "recognition": (
                {
                    "geopackage": str(recognition_source),
                    "layer_counts": recognition_counts,
                    "execution": "precomputed_result_ingestion",
                    "status": "REVIEW_REQUIRED",
                }
                if recognition_source is not None
                else None
            ),
            "task3": task3_result,
            "downstream": downstream_result,
            "downstream_readiness": {
                "path": str(downstream_path),
                "report": downstream_report,
            },
            "manifest": str(manifest_path),
            "report": str(stage / "connection_report.json"),
            "warnings": [
                *base.get("runtime_warnings", []),
                (
                    "任务四、任务六、任务五已使用本批次成果连续运行；施工前仍须复核候选设施和线路。"
                    if run_tasks4_to6
                    else "任务三确定性选址已使用本批次任务二结果运行；任务四至六是否可运行以 downstream_readiness.json 为准。"
                    if run_task3
                    else "任务三未运行；可勾选任务三后用本批次任务二结果继续，任务四至六不得读取历史默认结果。"
                ),
                "分区与选址均为算法规划候选，需人工复核；结构检查通过不代表业务验收通过。",
            ],
        }
        result = _relocate(result, stage, final)
        write_json(stage / "connection_report.json", result)
        phase = "publish"
        if final.exists():
            raise FileExistsError(f"新成果目录已存在，拒绝覆盖：{final}")
        os.replace(stage, final)
        return result
    except Exception as exc:
        failed = {"status": "failed", "phase": phase, "errors": [f"{type(exc).__name__}: {exc}"],
                  "published": False, "staging_dir": str(stage) if stage else None}
        if stage and stage.is_dir():
            try:
                write_json(stage / "failure_report.json", failed)
            except Exception as report_error:
                failed["errors"].append(f"失败报告写入异常：{report_error}")
        return failed
    finally:
        sys.path[:] = previous_path


def main():
    parser = argparse.ArgumentParser(
        description="任务一基础图层 + 可选本次识别 → 任务二 → 任务三 → 任务四 → 任务六 → 任务五"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--recognition-gpkg", type=Path, help="已完成识别的区域 GeoPackage")
    parser.add_argument(
        "--run-task3",
        action="store_true",
        help="使用本次任务二结果运行任务三确定性选址",
    )
    parser.add_argument("--run-tasks4-to6", action="store_true", help="继续运行任务四、任务六和任务五")
    parser.add_argument("--split-oversized-imbs", action="store_true", help="用户确认把超过48端口的IMB拆成可追溯服务单元")
    parser.add_argument("--use-formal-infrastructure", action="store_true", help="用户确认本批次正式INFRASTRUCTURE可用于路由")
    parser.add_argument("--use-recognition-roads", action="store_true", help="纳入已补齐施工属性的本批次GEO_ROAD")
    parser.add_argument("--nro-max-cap", type=int, default=2048)
    parser.add_argument("--zpm-max-cap", type=int, default=576)
    parser.add_argument("--spatial-weight", type=float, default=1.0)
    parser.add_argument("--pipe-weight", type=float, default=100.0)
    parser.add_argument("--formal-duct-capacity", type=int, default=2048)
    parser.add_argument("--pbo-offset-m", type=float, default=5.0)
    parser.add_argument("--candidate-paths", type=int, default=5)
    parser.add_argument("--water-cost-factor", type=float, default=2.0)
    parser.add_argument("--ptech-max-spacing-m", type=float, default=200.0)
    parser.add_argument("--terminal-snap-distance-m", type=float, default=80.0)
    parser.add_argument("--no-ga-refine", action="store_true", help="关闭任务六反馈驱动的任务三 GA 优化")
    parser.add_argument("--ga-population", type=int, default=14)
    parser.add_argument("--ga-generations", type=int, default=10)
    args = parser.parse_args()
    application = None
    runtime_handles = None
    try:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["MPLBACKEND"] = "Agg"
        runtime_handles = prepare_qgis_runtime()
        from qgis.core import QgsApplication, QgsProject

        application = QgsApplication([], False)
        application.initQgis()
        result = run_connected(
            args.config,
            args.output_root,
            recognition_gpkg=args.recognition_gpkg,
            run_task3=args.run_task3,
            run_tasks4_to6=args.run_tasks4_to6,
            split_oversized_imbs=args.split_oversized_imbs,
            use_formal_infrastructure=args.use_formal_infrastructure,
            use_recognition_roads=args.use_recognition_roads,
            nro_max_cap=args.nro_max_cap,
            zpm_max_cap=args.zpm_max_cap,
            spatial_weight=args.spatial_weight,
            pipe_weight=args.pipe_weight,
            formal_duct_capacity=args.formal_duct_capacity,
            pbo_offset_m=args.pbo_offset_m,
            candidate_paths=args.candidate_paths,
            water_cost_factor=args.water_cost_factor,
            ptech_max_spacing_m=args.ptech_max_spacing_m,
            terminal_snap_distance_m=args.terminal_snap_distance_m,
            ga_refine=not args.no_ga_refine,
            ga_population=args.ga_population,
            ga_generations=args.ga_generations,
        )
        QgsProject.instance().clear()
    except Exception as exc:
        result = {"status": "failed", "phase": "worker_startup", "errors": [f"{type(exc).__name__}: {exc}"],
                  "hint": "请选择与当前 QGIS 匹配、可导入 qgis 和 matplotlib 的 Python；不要选择 qgis-bin.exe。"}
    finally:
        if application is not None:
            application.exitQgis()
    print(RESULT_PREFIX + json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
