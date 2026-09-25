"""Demand-first PBO/BPE planner.

Planning order is deliberately fixed as IMB demand -> PBO placement around IMB
without point overlap -> task 2 IMB-only PM partition -> capacity-constrained
BPE spatial candidate selection inside each PM partition -> continuous
geometric-median BPE placement -> new PTECH.
Existing PTECH, INFRASTRUCTURE and CABLE layers are not read for any location
decision.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from generate_pbo import (
    PBO_IMB_OFFSET_M,
    PBO_MIN_IMB_CLEARANCE_M,
    address,
    plan_pbo_locations,
    write_dbf,
    write_point_shapefile,
)
from inspect_shapefile import read_dbf, read_points
if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_paths import TASK4_PBO_BPE_OUTPUT_DIR, TASK4_SAMPLE_INPUT_DIR


TASK_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TASK_DIR.parent
TASK2_SRC_DIR = PROJECT_DIR / "任务2" / "zone_partition" / "src"
DEFAULT_SHAPE_DIR = TASK4_SAMPLE_INPUT_DIR
DEFAULT_OUTPUT_DIR = TASK4_PBO_BPE_OUTPUT_DIR

# Includes the 96-port enclosure required by the current Task-2 sample
# (which contains 60- and 80-port IMBs).
PBO_CAPACITIES = (12, 24, 48, 96)
BPE_CAPACITY = 144
BPE_TARGET_UTILIZATION_RATIO = 0.80
BPE_TARGET_DEMAND = BPE_CAPACITY * BPE_TARGET_UTILIZATION_RATIO
BPE_CLUSTER_SEED_COUNT = 8
BPE_CLUSTER_MAX_ITERATIONS = 40
TASK2_PM_PARTITION_CAPACITY = 576


def point_rows(base: Path) -> list[tuple[dict[str, Any], tuple[float, float]]]:
    _, rows = read_dbf(base.with_suffix(".dbf"))
    points = read_points(base.with_suffix(".shp"))
    if len(rows) != len(points):
        raise ValueError(f"Geometry and attribute count differ in {base}")
    return list(zip(rows, points))


def metres(first: tuple[float, float], second: tuple[float, float]) -> float:
    latitude = math.radians((first[1] + second[1]) / 2)
    return math.hypot(
        (first[0] - second[0]) * 111320 * math.cos(latitude),
        (first[1] - second[1]) * 110540,
    )


def capacity(demand: int, levels: tuple[int, ...], equipment: str) -> int:
    for level in levels:
        if demand <= level:
            return level
    raise ValueError(f"{equipment} demand {demand} exceeds supported capacities {levels}")


def weighted_center(items: list[dict[str, Any]]) -> tuple[float, float]:
    total = sum(item["demand"] for item in items)
    return (
        sum(item["point"][0] * item["demand"] for item in items) / total,
        sum(item["point"][1] * item["demand"] for item in items) / total,
    )


def interpolate(first: tuple[float, float], second: tuple[float, float], ratio: float) -> tuple[float, float]:
    return (first[0] + (second[0] - first[0]) * ratio, first[1] + (second[1] - first[1]) * ratio)


def nearest_index(point: tuple[float, float], features: list[tuple[dict[str, Any], tuple[float, float]]]) -> int:
    return min(range(len(features)), key=lambda index: metres(point, features[index][1]))


def leaf_bpe_colour_map(bpes: list[dict[str, Any]]) -> dict[str, tuple[float, float, float]]:
    import colorsys

    ordered_leaf_bpes = sorted((bpe for bpe in bpes if bpe["level"] == "LEAF"), key=lambda item: item["code"])
    if not ordered_leaf_bpes:
        return {}
    return {
        bpe["code"]: colorsys.hsv_to_rgb((index * 0.618033988749895) % 1.0, 0.72, 0.92)
        for index, bpe in enumerate(ordered_leaf_bpes)
    }


def imb_point_for_pbo(pbo: dict[str, Any]) -> tuple[float, float]:
    return tuple(pbo.get("imb_point") or pbo["point"])


def _task2_helpers() -> tuple[Any, Any, Any, Any]:
    if not TASK2_SRC_DIR.exists():
        raise FileNotFoundError(f"Task 2 partition source directory was not found: {TASK2_SRC_DIR}")
    task2_src = str(TASK2_SRC_DIR)
    if task2_src not in sys.path:
        sys.path.insert(0, task2_src)
    from build_imb_only_dataset import build_imb_only_dataset
    from znro_zpm_partition import dump_json
    from znro_zpm_partition_petch_atomic import build_result, visualize

    return build_imb_only_dataset, build_result, dump_json, visualize


def task2_source_from_shape_imbs(
    imbs: list[tuple[dict[str, Any], tuple[float, float]]],
) -> dict[str, Any]:
    nodes = []
    for index, (imb, point) in enumerate(imbs, start=1):
        code = str(imb.get("CODE") or f"IMB_{index:04d}")
        demand = int(imb.get("NB_LOC_TOT") or 0)
        nodes.append(
            {
                "id": code,
                "code": code,
                "type": "IMB",
                "x": point[0],
                "y": point[1],
                "ports": demand,
                "nb_loc_tot": demand,
            }
        )
    return {
        "meta": {
            "layout": "task4_shape_imb_points",
            "coordinate_system": "source_shapefile_coordinates",
            "imb_count": len(nodes),
            "total_ports": sum(node["ports"] for node in nodes),
            "note": "Generated by task 4 before calling task 2 IMB-only PM partitioning.",
        },
        "nodes": nodes,
        "edges": [],
    }


def task2_pm_partition(
    imbs: list[tuple[dict[str, Any], tuple[float, float]]],
    output_dir: Path,
) -> dict[str, Any]:
    build_imb_only_dataset, build_result, dump_json, visualize = _task2_helpers()
    output_dir.mkdir(parents=True, exist_ok=True)
    source_data = task2_source_from_shape_imbs(imbs)
    imb_only_data = build_imb_only_dataset(source_data, "task4_shape_IMB")
    input_json = output_dir / "task4_imb_only_dataset.json"
    result_json = output_dir / "task2_pm_partition_result.json"
    result_png = output_dir / "task2_pm_partition_result.png"
    dump_json(imb_only_data, input_json)
    args = SimpleNamespace(
        input_json=input_json,
        nro_max_cap=2048,
        zpm_max_cap=TASK2_PM_PARTITION_CAPACITY,
        spatial_weight=1.0,
        pipe_weight=0.0,
        allow_extra_clusters=1,
        unconnect_penalty=100.0,
    )
    _, result = build_result(args)
    dump_json(result, result_json)
    visualize(imb_only_data, result["znro"], result["zpm"], result_png)
    return result


def pm_features_from_task2_partition(
    partition_result: dict[str, Any],
    nro_row: dict[str, Any],
    imbs: list[tuple[dict[str, Any], tuple[float, float]]] | None = None,
) -> tuple[
    dict[str, tuple[dict[str, Any], tuple[float, float]]],
    dict[str, str],
    dict[str, dict[str, Any]],
]:
    pm_features: dict[str, tuple[dict[str, Any], tuple[float, float]]] = {}
    partition_to_pm: dict[str, str] = {}
    partition_by_id: dict[str, dict[str, Any]] = {}
    clusters = list((partition_result.get("zpm") or {}).get("clusters") or [])
    if not clusters:
        # Region partition output is also a supported Task-2 hand-off.  It
        # carries the same fixed membership under ``zpm_regions`` and uses a
        # facility point as the PM seed.
        for region in partition_result.get("zpm_regions") or []:
            facility = region.get("facility") or {}
            centroid = facility or region.get("centroid") or {}
            clusters.append(
                {
                    "id": region.get("id"),
                    "members": [{"id": value} for value in region.get("imb_ids") or []],
                    "total_ports": region.get("total_ports", 0),
                    "capacity_limit": region.get("capacity_limit", TASK2_PM_PARTITION_CAPACITY),
                    "centroid": {"x": centroid.get("x"), "y": centroid.get("y")},
                }
            )
    if not clusters:
        raise ValueError("Task-2 partition result contains no ZPM clusters")

    # Task 2 assignment keys may be internal node ids (``n_0003``), while the
    # Task-4 Shapefile CODE is the stable business key (for example
    # ``IMB-001``).  Resolve direct aliases first and use coordinates only as
    # a deterministic fallback for older result files.
    aliases: dict[str, str] = {}
    imb_points: dict[str, tuple[float, float]] = {}
    for row, point in imbs or []:
        code = str(row.get("CODE") or "").strip()
        if not code:
            continue
        imb_points[code] = point
        for key in ("CODE", "BPE_CODE", "ID", "NODE_ID", "NODE_CODE"):
            value = str(row.get(key) or "").strip()
            if value:
                aliases[value] = code

    def canonical_member(member: Any) -> str:
        if isinstance(member, dict):
            values = [member.get(key) for key in ("code", "CODE", "id", "ID", "node_id", "NODE_ID")]
            x, y = member.get("x"), member.get("y")
        else:
            values, x, y = [member], None, None
        for value in values:
            text = str(value or "").strip()
            if text in aliases:
                return aliases[text]
            if text in imb_points:
                return text
        if x is not None and y is not None and imb_points:
            target = (float(x), float(y))
            return min(imb_points, key=lambda code: metres(target, imb_points[code]))
        return str(values[0] or "").strip()

    used_members: set[str] = set()
    known_members = set(imb_points)
    for index, cluster in enumerate(clusters, start=1):
        partition_id = str(cluster.get("id") or "").strip()
        if not partition_id:
            raise ValueError(f"Task-2 ZPM cluster #{index} has no id")
        pm_code = f"PM-T2-{index:04d}"
        centroid = cluster.get("centroid") or {}
        if centroid.get("x") is None or centroid.get("y") is None:
            members_for_center = [canonical_member(item) for item in cluster.get("members") or []]
            points_for_center = [imb_points[item] for item in members_for_center if item in imb_points]
            if not points_for_center:
                raise ValueError(f"Task-2 cluster {partition_id} has no usable centroid or members")
            centroid = {
                "x": sum(item[0] for item in points_for_center) / len(points_for_center),
                "y": sum(item[1] for item in points_for_center) / len(points_for_center),
            }
        point = (float(centroid["x"]), float(centroid["y"]))
        row = {
            "CODE": pm_code,
            "REF_PLAQUE": "JAD-MAR",
            "REF_NRO": nro_row.get("CODE") or nro_row.get("REF_NRO"),
            "TYPE": "PM",
            "FABRIQUANT": nro_row.get("FABRIQUANT"),
            "STATUT": "EN PROJET",
            "PROPRIETAI": nro_row.get("PROPRIETAI"),
            "GESTIONNAI": nro_row.get("GESTIONNAI"),
            "ADRESSSE": f"Task 2 PM partition {partition_id}",
            "VILLE": "ELJADIDA",
            "CODE_POSTA": 24000,
            "X": point[0],
            "Y": point[1],
        }
        pm_features[pm_code] = (row, point)
        partition_to_pm[partition_id] = pm_code
        normalized_members = [canonical_member(item) for item in cluster.get("members") or []]
        if known_members:
            unknown = sorted(set(normalized_members) - known_members)
            if unknown:
                raise ValueError(f"Task-2 partition {partition_id} references unknown IMB ids: {unknown[:5]}")
            duplicates = sorted(set(normalized_members) & used_members)
            if duplicates:
                raise ValueError(f"Task-2 partition assigns an IMB more than once: {duplicates[:5]}")
            used_members.update(normalized_members)
        partition_by_id[partition_id] = {**cluster, "members": [{"id": item} for item in normalized_members]}
    if known_members:
        missing = sorted(known_members - used_members)
        if missing:
            raise ValueError(f"Task-2 partition does not assign all IMBs: {missing[:5]}")
    if not pm_features:
        raise ValueError("Task 2 did not return any ZPM/PM partitions.")
    return pm_features, partition_to_pm, partition_by_id


def task2_assignment_from_partition(
    partition_result: dict[str, Any],
    imbs: list[tuple[dict[str, Any], tuple[float, float]]],
) -> dict[str, str]:
    """Return a business-code -> ZPM map from either Task-2 result schema.

    ``qgis_petch_atomic_result.json`` stores an explicit assignment keyed by
    internal node id, whereas ``qgis_direct_region_partition_result.json``
    stores member ids inside ``zpm_regions``.  Task 4 always uses the IMB
    Shapefile ``CODE`` as its stable cross-task key.
    """
    aliases: dict[str, str] = {}
    known: set[str] = set()
    for row, _ in imbs:
        code = str(row.get("CODE") or "").strip()
        if not code:
            continue
        known.add(code)
        for field in ("CODE", "BPE_CODE", "ID", "NODE_ID", "NODE_CODE"):
            value = str(row.get(field) or "").strip()
            if value:
                aliases[value] = code

    def canonical(value: Any) -> str:
        if isinstance(value, dict):
            values = [value.get(field) for field in ("code", "CODE", "id", "ID", "node_id", "NODE_ID")]
        else:
            values = [value]
        for item in values:
            text = str(item or "").strip()
            if text in aliases:
                return aliases[text]
            if text in known:
                return text
        return str(values[0] or "").strip()

    assignment: dict[str, str] = {}
    declared_zpms = {
        str(cluster.get("id") or "").strip()
        for cluster in ((partition_result.get("zpm") or {}).get("clusters") or [])
        if isinstance(cluster, dict) and str(cluster.get("id") or "").strip()
    }
    declared_zpms.update(
        str(region.get("id") or "").strip()
        for region in partition_result.get("zpm_regions") or []
        if isinstance(region, dict) and str(region.get("id") or "").strip()
    )
    raw_assignment = (partition_result.get("zpm") or {}).get("assignment")
    if isinstance(raw_assignment, dict):
        for member, zpm_id in raw_assignment.items():
            code = canonical(member)
            if code:
                if isinstance(zpm_id, dict):
                    zpm_id = zpm_id.get("id") or zpm_id.get("zpm_id") or zpm_id.get("code")
                normalized_zpm = str(zpm_id or "").strip()
                if not normalized_zpm:
                    raise ValueError(f"Task-2 assignment for IMB {code} has no ZPM id")
                previous = assignment.get(code)
                if previous and previous != normalized_zpm:
                    raise ValueError(f"Task-2 assigns IMB {code} to multiple ZPMs")
                assignment[code] = normalized_zpm
    for region in partition_result.get("zpm_regions") or []:
        zpm_id = str(region.get("id") or "").strip()
        if not zpm_id:
            continue
        for member in region.get("imb_ids") or []:
            code = canonical(member)
            if code:
                previous = assignment.get(code)
                if previous and previous != zpm_id:
                    raise ValueError(f"Task-2 assigns IMB {code} to multiple ZPMs")
                assignment[code] = zpm_id
    if known:
        missing = sorted(known - set(assignment))
        if missing:
            raise ValueError(f"Task-2 partition does not assign all IMBs: {missing[:5]}")
        unknown = sorted(set(assignment) - known)
        if unknown:
            raise ValueError(f"Task-2 partition references unknown IMBs: {unknown[:5]}")
    unknown_zpms = sorted(set(assignment.values()) - declared_zpms) if declared_zpms else []
    if unknown_zpms:
        raise ValueError(f"Task-2 assignment references unknown ZPM ids: {unknown_zpms[:5]}")
    return assignment


def load_fixed_pm_plan(path: Path) -> dict[str, Any]:
    """Load task-3/RL PM selections and validate the fixed-assignment contract."""
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    pm_sites = payload.get("pm_sites") if isinstance(payload, dict) else None
    if not isinstance(pm_sites, list) or not pm_sites:
        raise ValueError(f"Fixed PM plan must contain a non-empty pm_sites list: {path}")
    seen_codes: set[str] = set()
    for pm in pm_sites:
        code = str(pm.get("id") or pm.get("code") or "").strip()
        if not code:
            raise ValueError("Fixed PM plan contains a PM without id/code")
        if code in seen_codes:
            raise ValueError(f"Fixed PM plan contains duplicate PM code: {code}")
        seen_codes.add(code)
        if pm.get("x") is None or pm.get("y") is None:
            raise ValueError(f"Fixed PM {code} is missing x/y")
        assigned = pm.get("assigned_imb_ids") or pm.get("imb_ids")
        if not isinstance(assigned, list) or not assigned:
            raise ValueError(f"Fixed PM {code} must contain assigned_imb_ids")
    return payload


def load_task2_partition(path: Path) -> dict[str, Any]:
    """Load a Task-2 partition result with a useful, stable error message."""
    if not path.is_file():
        raise FileNotFoundError(f"Task-2 partition result was not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Task-2 partition result is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Task-2 partition result must be a JSON object: {path}")
    return payload


def validate_fixed_plan_membership(
    fixed_plan: dict[str, Any],
    task2_partition: dict[str, Any],
    imbs: list[tuple[dict[str, Any], tuple[float, float]]],
) -> None:
    """Ensure Task-3 changed PM coordinates only, never Task-2 ownership."""
    expected = task2_assignment_from_partition(task2_partition, imbs)
    aliases: dict[str, str] = {}
    for row, _ in imbs:
        code = str(row.get("CODE") or "").strip()
        if not code:
            continue
        for field in ("CODE", "BPE_CODE", "ID", "NODE_ID", "NODE_CODE"):
            value = str(row.get(field) or "").strip()
            if value:
                aliases[value] = code
    actual: dict[str, str] = {}
    for pm in fixed_plan.get("pm_sites") or []:
        partition = str(pm.get("partition_id") or pm.get("zpm_id") or "").strip()
        if not partition:
            raise ValueError("Fixed PM plan contains a PM without partition_id/zpm_id")
        for value in pm.get("assigned_imb_ids") or pm.get("imb_ids") or []:
            code = aliases.get(str(value).strip(), str(value).strip())
            previous = actual.get(code)
            if previous and previous != partition:
                raise ValueError(f"Fixed PM plan assigns IMB {code} to multiple ZPMs")
            actual[code] = partition
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        changed = sorted(code for code in set(expected) & set(actual) if expected[code] != actual[code])
        raise ValueError(
            "Task-3 fixed PM plan must preserve Task-2 IMB/ZPM membership; "
            f"missing={missing[:5]}, extra={extra[:5]}, changed={changed[:5]}"
        )


def pm_features_from_fixed_plan(
    fixed_plan: dict[str, Any],
    nro_row: dict[str, Any],
    imbs: list[tuple[dict[str, Any], tuple[float, float]]],
) -> tuple[
    dict[str, tuple[dict[str, Any], tuple[float, float]]],
    dict[str, str],
    dict[str, dict[str, Any]],
    dict[str, str],
]:
    """Build task-4 PM features and IMB assignment from fixed PM records.

    Synthetic partition ids keep task-4's output schema stable while making the
    PM relationship independent of task-2's generated ZPM numbering.
    """
    pm_features: dict[str, tuple[dict[str, Any], tuple[float, float]]] = {}
    partition_to_pm: dict[str, str] = {}
    partition_by_id: dict[str, dict[str, Any]] = {}
    assignment: dict[str, str] = {}
    known_imb_ids = {str(row.get("CODE")) for row, _ in imbs if row.get("CODE")}
    imb_aliases: dict[str, str] = {}
    for row, _ in imbs:
        code = str(row.get("CODE") or "").strip()
        if not code:
            continue
        for field in ("CODE", "BPE_CODE", "ID", "NODE_ID", "NODE_CODE"):
            value = str(row.get(field) or "").strip()
            if value:
                imb_aliases[value] = code
    for index, raw_pm in enumerate(fixed_plan["pm_sites"], start=1):
        pm_code = str(raw_pm.get("id") or raw_pm.get("code")).strip()
        partition_id = str(raw_pm.get("partition_id") or raw_pm.get("zpm_id") or f"FIXED_ZPM_{index:04d}")
        if partition_id in partition_to_pm:
            raise ValueError(f"Fixed PM plan contains duplicate partition id: {partition_id}")
        point = (float(raw_pm["x"]), float(raw_pm["y"]))
        ref_nro = str(raw_pm.get("nro_id") or raw_pm.get("nro_code") or nro_row.get("CODE") or nro_row.get("REF_NRO") or "")
        row = {
            "CODE": pm_code,
            "REF_PLAQUE": raw_pm.get("ref_plaque") or "JAD-MAR",
            "REF_NRO": ref_nro,
            "TYPE": "PM",
            "FABRIQUANT": raw_pm.get("manufacturer") or nro_row.get("FABRIQUANT"),
            "STATUT": raw_pm.get("status") or "EN PROJET",
            "PROPRIETAI": raw_pm.get("property") or nro_row.get("PROPRIETAI"),
            "GESTIONNAI": raw_pm.get("manager") or nro_row.get("GESTIONNAI"),
            "ADRESSSE": raw_pm.get("address") or f"Fixed PM {partition_id}",
            "VILLE": raw_pm.get("city") or "ELJADIDA",
            "CODE_POSTA": raw_pm.get("postal_code") or 24000,
            "X": point[0],
            "Y": point[1],
            "COMMENT": "PM fixed by task 3/RL selection",
        }
        pm_features[pm_code] = (row, point)
        partition_to_pm[partition_id] = pm_code
        members = [
            imb_aliases.get(str(value).strip(), str(value).strip())
            for value in (raw_pm.get("assigned_imb_ids") or raw_pm.get("imb_ids") or [])
        ]
        unknown = sorted(set(members) - known_imb_ids)
        if unknown:
            raise ValueError(f"Fixed PM {pm_code} references unknown IMB ids: {unknown[:5]}")
        duplicates = sorted(item for item in members if item in assignment)
        if duplicates:
            raise ValueError(f"IMB assigned to more than one fixed PM: {duplicates[:5]}")
        for imb_id in members:
            assignment[imb_id] = partition_id
        partition_by_id[partition_id] = {
            "id": partition_id,
            "capacity_limit": int(raw_pm.get("capacity") or TASK2_PM_PARTITION_CAPACITY),
            "members": [{"id": imb_id} for imb_id in members],
            "fixed_pm_code": pm_code,
        }
    missing = sorted(known_imb_ids - set(assignment))
    if missing:
        raise ValueError(f"Fixed PM plan does not assign all IMBs: {missing[:5]}")
    return pm_features, partition_to_pm, partition_by_id, assignment


def validate_bpe_clusters(bpes: list[dict[str, Any]]) -> dict[str, Any]:
    demands = [int(bpe["demand"]) for bpe in bpes]
    scores = [bpe_demand_score(demand) for demand in demands]
    capacity_violations = [
        {"BPE_CODE": bpe["code"], "DEMAND": int(bpe["demand"])}
        for bpe in bpes
        if int(bpe["demand"]) > BPE_CAPACITY
    ]
    return {
        "bpe_count": len(bpes),
        "bpe_capacity": BPE_CAPACITY,
        "capacity_constraint_mode": "hard_limit_with_soft_target_score",
        "capacity_ok": not capacity_violations,
        "total_demand": sum(demands),
        "installed_capacity": len(bpes) * BPE_CAPACITY,
        "minimum_required_bpe_count": math.ceil(sum(demands) / BPE_CAPACITY) if demands else 0,
        "max_bpe_demand": max(demands, default=0),
        "target_utilization_ratio": BPE_TARGET_UTILIZATION_RATIO,
        "target_demand": BPE_TARGET_DEMAND,
        "total_score": round(sum(scores) / len(scores), 6) if scores else 0.0,
        "cluster_scores": [
            {"BPE_CODE": bpe["code"], "DEMAND": int(bpe["demand"]), "SCORE": round(score, 6)}
            for bpe, score in zip(bpes, scores)
        ],
        "capacity_violations": capacity_violations,
    }


def validate_pbo_placements(pbos: list[dict[str, Any]]) -> dict[str, Any]:
    """Verify that each PBO is offset from all IMB points."""
    imb_points = [imb_point_for_pbo(pbo) for pbo in pbos]
    overlaps = []
    offsets = []
    for pbo in pbos:
        point = tuple(pbo["point"])
        served_imb = imb_point_for_pbo(pbo)
        offset = metres(point, served_imb)
        offsets.append(offset)
        nearest_imb = min((metres(point, imb_point) for imb_point in imb_points), default=float("inf"))
        if nearest_imb < PBO_MIN_IMB_CLEARANCE_M:
            overlaps.append(
                {
                    "PBO_CODE": pbo["code"],
                    "IMB_CODE": pbo["imb"]["CODE"],
                    "nearest_imb_distance_m": round(nearest_imb, 3),
                }
            )
    return {
        "pbo_count": len(pbos),
        "placement_rule": "IMB_ADJACENT_OFFSET_RING",
        "target_offset_m": round(offsets[0], 3) if offsets else PBO_IMB_OFFSET_M,
        "minimum_required_imb_clearance_m": PBO_MIN_IMB_CLEARANCE_M,
        "minimum_served_imb_offset_m": round(min(offsets), 3) if offsets else None,
        "maximum_served_imb_offset_m": round(max(offsets), 3) if offsets else None,
        "imb_overlap_count": len(overlaps),
        "imb_overlap_ok": not overlaps,
        "imb_overlaps": overlaps,
    }


def partition_summary_rows(pbos: list[dict[str, Any]], bpes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for partition_id in sorted({pbo["pm_partition_id"] for pbo in pbos}):
        members = [pbo for pbo in pbos if pbo["pm_partition_id"] == partition_id]
        partition_bpes = [bpe for bpe in bpes if bpe["pm_partition_id"] == partition_id]
        rows.append(
            {
                "PM_PARTITION": partition_id,
                "REF_PM": partition_bpes[0]["pm_code"],
                "BPE_CLUSTER_COUNT": len(partition_bpes),
                "BPE_CODES": ", ".join(bpe["code"] for bpe in partition_bpes),
                "IMB_COUNT": len(members),
                "PBO_COUNT": len(members),
                "DEMAND": sum(pbo["demand"] for pbo in members),
                "BPE_CAPACITY": BPE_CAPACITY,
                "TASK4_BPE_CLUSTER_RULE": f"hard limit={BPE_CAPACITY}; soft score target={BPE_TARGET_DEMAND:.1f} ({BPE_TARGET_UTILIZATION_RATIO:.0%} capacity)",
                "TASK2_CAPACITY_LIMIT": TASK2_PM_PARTITION_CAPACITY,
            }
        )
    return rows


def geometric_median_point(cluster: list[dict[str, Any]]) -> tuple[float, float]:
    """Return the continuous point minimizing total distance to the PBO set."""
    points = [pbo["point"] for pbo in cluster]
    if not points:
        raise ValueError("Cannot place a BPE for an empty cluster.")
    if len(points) == 1:
        return points[0]

    # Work in a local metre projection so the Weiszfeld updates are not made
    # in longitude/latitude degrees.
    origin_x = sum(point[0] for point in points) / len(points)
    origin_y = sum(point[1] for point in points) / len(points)
    latitude = math.radians(origin_y)
    longitude_scale = 111320.0 * math.cos(latitude)
    latitude_scale = 110540.0
    local_points = [
        ((point[0] - origin_x) * longitude_scale, (point[1] - origin_y) * latitude_scale)
        for point in points
    ]
    current = (
        sum(point[0] for point in local_points) / len(local_points),
        sum(point[1] for point in local_points) / len(local_points),
    )
    for _ in range(100):
        distances = [math.hypot(current[0] - point[0], current[1] - point[1]) for point in local_points]
        if min(distances) < 1e-8:
            return (
                origin_x + current[0] / longitude_scale,
                origin_y + current[1] / latitude_scale,
            )
        denominator = sum(1.0 / distance for distance in distances)
        updated = (
            sum(point[0] / distance for point, distance in zip(local_points, distances)) / denominator,
            sum(point[1] / distance for point, distance in zip(local_points, distances)) / denominator,
        )
        if math.hypot(updated[0] - current[0], updated[1] - current[1]) < 0.01:
            current = updated
            break
        current = updated
    return (
        origin_x + current[0] / longitude_scale,
        origin_y + current[1] / latitude_scale,
    )


def bpe_demand_score(demand: int) -> float:
    """Score a BPE by closeness to 80% of its nominal capacity."""
    deviation_ratio = abs(float(demand) - BPE_TARGET_DEMAND) / BPE_TARGET_DEMAND
    return max(0.0, 1.0 - deviation_ratio)


def cluster_spatial_cost(cluster: list[dict[str, Any]]) -> float:
    if len(cluster) <= 1:
        return 0.0
    center = weighted_center(cluster)
    return sum(metres(pbo["point"], center) for pbo in cluster) / len(cluster)


def _initial_centers(pbos: list[dict[str, Any]], cluster_count: int, seed: int) -> list[tuple[float, float]]:
    ordered_modes = (
        lambda item: (item["point"][0], item["point"][1], item["code"]),
        lambda item: (item["point"][1], item["point"][0], item["code"]),
        lambda item: (item["point"][0] + item["point"][1], item["code"]),
        lambda item: (-item["demand"], item["point"][0], item["code"]),
    )
    if seed < len(ordered_modes):
        ordered = sorted(pbos, key=ordered_modes[seed])
        return [
            ordered[round(index * (len(ordered) - 1) / max(1, cluster_count - 1))]["point"]
            for index in range(cluster_count)
        ]

    start = seed % len(pbos)
    selected = [pbos[start]]
    while len(selected) < cluster_count:
        candidate = max(
            (pbo for pbo in pbos if pbo not in selected),
            key=lambda pbo: (
                min(metres(pbo["point"], chosen["point"]) for chosen in selected),
                pbo["code"],
            ),
        )
        selected.append(candidate)
    return [pbo["point"] for pbo in selected]


def capacity_constrained_spatial_clusters(
    pbos: list[dict[str, Any]], cluster_count: int, seed: int
) -> list[list[dict[str, Any]]]:
    """Run spatial Lloyd iterations while enforcing the BPE capacity limit."""
    if not pbos:
        return []
    cluster_count = max(1, min(cluster_count, len(pbos)))
    centers = _initial_centers(pbos, cluster_count, seed)
    clusters: list[list[dict[str, Any]]] = []
    for _ in range(BPE_CLUSTER_MAX_ITERATIONS):
        clusters = [[] for _ in centers]
        loads = [0 for _ in centers]
        for pbo in sorted(pbos, key=lambda item: (-item["demand"], item["code"])):
            demand = int(pbo["demand"])
            feasible = [
                index
                for index, load in enumerate(loads)
                if load + demand <= BPE_CAPACITY
            ]
            if not feasible:
                return []
            selected = min(
                feasible,
                key=lambda index: (metres(pbo["point"], centers[index]), loads[index], index),
            )
            clusters[selected].append(pbo)
            loads[selected] += demand

        nonempty = [cluster for cluster in clusters if cluster]
        updated = [weighted_center(cluster) for cluster in nonempty]
        if len(updated) != len(centers):
            centers = updated
            if len(centers) == 1:
                break
            continue
        if all(metres(current, next_center) < 0.01 for current, next_center in zip(centers, updated)):
            break
        centers = updated
    return [
        sorted(cluster, key=lambda item: item["code"])
        for cluster in clusters
        if cluster
    ]


def select_bpe_cluster_plan(pbos: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose the highest-scoring spatial candidate satisfying BPE capacity."""
    if not pbos:
        return {"clusters": [], "score": 0.0, "raw_score": 0.0, "spatial_cost_m": 0.0, "candidate_count": 0}

    invalid_pbos = [
        {"PBO_CODE": str(pbo.get("code") or ""), "DEMAND": int(pbo.get("demand") or 0)}
        for pbo in pbos
        if int(pbo.get("demand") or 0) <= 0 or int(pbo.get("demand") or 0) > BPE_CAPACITY
    ]
    if invalid_pbos:
        raise ValueError(
            "Each PBO demand must be within 1.."
            f"{BPE_CAPACITY} for a feasible BPE plan: {invalid_pbos}"
        )

    candidates = []
    seed_count = min(BPE_CLUSTER_SEED_COUNT, max(1, len(pbos)))
    minimum_cluster_count = math.ceil(sum(int(pbo["demand"]) for pbo in pbos) / BPE_CAPACITY)
    capacity_rejected_candidate_count = 0
    for cluster_count in range(minimum_cluster_count, len(pbos) + 1):
        seen_assignments: set[tuple[tuple[str, ...], ...]] = set()
        for seed in range(seed_count):
            clusters = capacity_constrained_spatial_clusters(pbos, cluster_count, seed)
            if not clusters:
                capacity_rejected_candidate_count += 1
                continue
            signature = tuple(sorted(tuple(pbo["code"] for pbo in cluster) for cluster in clusters))
            if signature in seen_assignments:
                continue
            seen_assignments.add(signature)
            scores = [bpe_demand_score(sum(pbo["demand"] for pbo in cluster)) for cluster in clusters]
            candidates.append(
                {
                    "clusters": clusters,
                    "score": sum(scores) / len(scores),
                    "raw_score": sum(scores),
                    "spatial_cost_m": sum(cluster_spatial_cost(cluster) for cluster in clusters) / len(clusters),
                    "candidate_count": 1,
                }
            )

    if not candidates:
        raise ValueError("Could not generate any BPE clustering candidate.")
    candidates.sort(
        key=lambda candidate: (
            -candidate["score"],
            candidate["spatial_cost_m"],
            len(candidate["clusters"]),
            tuple(tuple(pbo["code"] for pbo in cluster) for cluster in candidate["clusters"]),
        )
    )
    best = candidates[0]
    best["candidate_count"] = len(candidates)
    best["cluster_count"] = len(best["clusters"])
    best["minimum_cluster_count"] = minimum_cluster_count
    best["capacity_rejected_candidate_count"] = capacity_rejected_candidate_count
    best["cluster_scores"] = [
        {
            "demand": sum(pbo["demand"] for pbo in cluster),
            "score": round(bpe_demand_score(sum(pbo["demand"] for pbo in cluster)), 6),
        }
        for cluster in best["clusters"]
    ]
    return best


def cluster_pbos(pbos: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Compatibility wrapper returning only the selected cluster members."""
    return select_bpe_cluster_plan(pbos)["clusters"]


def fixed_pm_for_imb(
    imb_point: tuple[float, float], reference_pbos: list[tuple[dict[str, Any], tuple[float, float]]]
) -> str:
    """Use the supplied complete design only for fixed PM/ZPM membership."""
    return str(reference_pbos[nearest_index(imb_point, reference_pbos)][0]["REF_PM"])


def new_ptc_code(box_type: str, index: int) -> str:
    return f"ALG-PTC-{box_type}-{index:04d}"


def plan(
    shape_dir: Path,
    partition_output_dir: Path | None = None,
    fixed_pm_path: Path | None = None,
    task2_partition_path: Path | None = None,
    *,
    pbo_offset_m: float = PBO_IMB_OFFSET_M,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if pbo_offset_m <= 0:
        raise ValueError("PBO IMB offset must be positive")
    imbs = [item for item in point_rows(shape_dir / "IMB") if int(item[0].get("NB_LOC_TOT") or 0) > 0]
    pbo_locations = plan_pbo_locations(
        [(str(imb.get("CODE") or f"IMB-{index:04d}"), point) for index, (imb, point) in enumerate(imbs, start=1)],
        offset_m=pbo_offset_m,
    )
    site_features = point_rows(shape_dir / "SITE")
    nro_features = [(row, point) for row, point in site_features if row.get("TYPE") == "NRO"]
    if len(nro_features) != 1:
        raise ValueError("Completed example must provide exactly one NRO.")

    if fixed_pm_path is not None:
        fixed_plan = load_fixed_pm_plan(Path(fixed_pm_path))
        pm_features, partition_to_pm, _, assignment = pm_features_from_fixed_plan(
            fixed_plan, nro_features[0][0], imbs
        )
        if task2_partition_path is not None:
            validate_fixed_plan_membership(
                fixed_plan,
                load_task2_partition(Path(task2_partition_path)),
                imbs,
            )
    else:
        if task2_partition_path is not None:
            partition_result = load_task2_partition(Path(task2_partition_path))
        else:
            partition_result = task2_pm_partition(
                imbs,
                partition_output_dir or DEFAULT_OUTPUT_DIR / "task2_pm_partition",
            )
        assignment = task2_assignment_from_partition(partition_result, imbs)
        pm_features, partition_to_pm, _ = pm_features_from_task2_partition(
            partition_result, nro_features[0][0], imbs
        )

    pbo_plan: list[dict[str, Any]] = []
    for index, (imb, point) in enumerate(imbs, start=1):
        imb_code = str(imb["CODE"])
        demand = int(imb["NB_LOC_TOT"])
        mode = "IMMEUBLE" if "COLONNE" in str(imb.get("RACCORDEME") or "").upper() else "ANCRAGE FACADE"
        partition_id = assignment.get(imb_code)
        if partition_id is None:
            raise ValueError(f"Task 2 PM partition did not assign IMB {imb_code}.")
        pm_code = partition_to_pm[partition_id]
        placement = pbo_locations[imb_code]
        pbo = {
            "code": f"PBO-ALG-JAD-MAR-{index:04d}", "ptc_code": new_ptc_code("PBO", index),
            "point": placement["point"], "imb_point": point, "placement": placement,
            "ptc_type": mode, "pm_code": pm_code, "nro_code": pm_features[pm_code][0]["REF_NRO"],
            "pm_partition_id": partition_id, "leaf_bpe_code": None, "demand": demand,
            "capacity": capacity(demand, PBO_CAPACITIES, "PBO"), "imb": imb,
        }
        pbo_plan.append(pbo)

    bpe_plan: list[dict[str, Any]] = []
    for partition_id in sorted(partition_to_pm):
        pm_code = partition_to_pm[partition_id]
        partition_pbos = [pbo for pbo in pbo_plan if pbo["pm_partition_id"] == partition_id]
        if not partition_pbos:
            continue
        cluster_solution = select_bpe_cluster_plan(partition_pbos)
        for cluster in cluster_solution["clusters"]:
            demand = sum(pbo["demand"] for pbo in cluster)
            index = len(bpe_plan) + 1
            center = geometric_median_point(cluster)
            bpe = {
                "code": f"BPE-ALG-JAD-MAR-{index:04d}", "ptc_code": new_ptc_code("BPE", index),
                "point": center, "pm_point": pm_features[pm_code][1],
                "pm_code": pm_code, "pm_partition_id": partition_id, "nro_code": pm_features[pm_code][0]["REF_NRO"],
                "demand": demand, "capacity": BPE_CAPACITY, "members": cluster,
                "medoid_pbo_code": None,
                "center_method": "WEISZFELD_GEOMETRIC_MEDIAN_OF_PBO_POINTS",
                "cluster_score": bpe_demand_score(demand),
                "plan_score": cluster_solution["score"],
                "candidate_count": cluster_solution["candidate_count"],
                "type_struc": "DISTRIBUTION", "level": "LEAF", "parent_code": None,
                "selection_method": "SOFT_SCORE_SPATIAL_CLUSTER_GEOMETRIC_MEDIAN",
            }
            for pbo in cluster:
                pbo["leaf_bpe_code"] = bpe["code"]
            bpe_plan.append(bpe)

    leaf_by_code = {bpe["code"]: bpe for bpe in bpe_plan if bpe["level"] == "LEAF"}
    imb_mapping: list[dict[str, Any]] = []
    for pbo in pbo_plan:
        leaf = leaf_by_code[pbo["leaf_bpe_code"]]
        imb_mapping.append(
            {"IMB_CODE": pbo["imb"]["CODE"], "IMB_DEMAND": pbo["demand"], "PM_PARTITION": pbo["pm_partition_id"],
             "PBO_CODE": pbo["code"], "PBO_CAPACITY": pbo["capacity"], "PBO_PTC": pbo["ptc_code"],
             "PBO_PTC_TYPE": pbo["ptc_type"], "REF_PM": pbo["pm_code"], "LEAF_BPE_CODE": leaf["code"],
             "LEAF_BPE_CAPACITY": leaf["capacity"], "PBO_LOCATION_SOURCE": pbo["placement"]["source"],
             "PBO_IMB_OFFSET_M": pbo["placement"]["offset_m"], "PBO_BEARING_DEG": pbo["placement"]["bearing_deg"]}
        )
    validation = validate_bpe_clusters(bpe_plan)
    if not validation["capacity_ok"]:
        raise ValueError(f"BPE capacity constraint failed: {validation['capacity_violations']}")
    placement_validation = validate_pbo_placements(pbo_plan)
    if not placement_validation["imb_overlap_ok"]:
        raise ValueError(f"PBO/IMB overlap constraint failed: {placement_validation['imb_overlaps']}")
    return pbo_plan, bpe_plan, imb_mapping


def boite_rows(pbos: list[dict[str, Any]], bpes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[tuple[float, float]]]:
    rows: list[dict[str, Any]] = []
    points: list[tuple[float, float]] = []
    for pbo in pbos:
        imb = pbo["imb"]
        rows.append(
            {"fid": len(rows) + 1, "CODE": pbo["code"], "CODE_PTC": pbo["ptc_code"], "REF_PLAQUE": imb["REF_PLAQUE"],
             "REF_NRO": pbo["nro_code"], "REF_PM": pbo["pm_code"], "TYPE": "PBO", "TYPE_STRUC": "DISTRIBUTION",
             "MODE_POSE": pbo["ptc_type"], "CAPACITE": pbo["capacity"], "NB_LOGEMEN": pbo["demand"], "NB_SPLICES": 0,
             "NB_FIBRE_U": pbo["demand"], "FABRIQUANT": "FBR", "REF_BPE": f"FBR-BPO{pbo['capacity']}", "NB_CASSETT": 4,
             "CABLE_AMON": None, "STATUT": "EN PROJET", "PROPRIETAI": "UNIFIBER", "GESTIONNAI": "UNIFIBER",
             "ADRESSSE": address(imb), "VILLE": imb["VILLE"], "CODE_POSTA": imb["CODE_POSTA"],
             "X": pbo["point"][0], "Y": pbo["point"][1],
             "COMMENT": f"IMB={imb['CODE']}; partition={pbo['pm_partition_id']}; leaf={pbo['leaf_bpe_code']}; offset_m={pbo['placement']['offset_m']}; bearing_deg={pbo['placement']['bearing_deg']}"}
        )
        points.append(pbo["point"])
    for bpe in bpes:
        rows.append(
            {"fid": len(rows) + 1, "CODE": bpe["code"], "CODE_PTC": bpe["ptc_code"], "REF_PLAQUE": "JAD-MAR",
             "REF_NRO": bpe["nro_code"], "REF_PM": bpe["pm_code"], "TYPE": "BPE", "TYPE_STRUC": bpe["type_struc"],
             "MODE_POSE": "CHAMBRE", "CAPACITE": bpe["capacity"], "NB_LOGEMEN": bpe["demand"], "NB_SPLICES": 0,
             "NB_FIBRE_U": bpe["demand"], "FABRIQUANT": "FBR", "REF_BPE": f"FBR-BPO{bpe['capacity']}", "NB_CASSETT": 4,
             "CABLE_AMON": None, "STATUT": "EN PROJET", "PROPRIETAI": "UNIFIBER", "GESTIONNAI": "UNIFIBER",
             "ADRESSSE": "FIELD_SURVEY_REQUIRED", "VILLE": "ELJADIDA", "CODE_POSTA": 24000,
             "X": bpe["point"][0], "Y": bpe["point"][1],
             "COMMENT": f"{bpe['level']}; partition={bpe.get('pm_partition_id') or 'none'}; center=GEOMETRIC_MEDIAN; score={bpe.get('cluster_score', 0.0):.6f}; {bpe.get('selection_method') or 'TASK4_RESULT'}"}
        )
        points.append(bpe["point"])
    return rows, points


def ptech_rows(pbos: list[dict[str, Any]], bpes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[tuple[float, float]]]:
    rows: list[dict[str, Any]] = []
    points: list[tuple[float, float]] = []
    for pbo in pbos:
        rows.append(
            {"fid": len(rows) + 1, "CODE": pbo["ptc_code"], "NOM": pbo["ptc_code"], "REF_PLAQUE": "JAD-MAR",
             "TYPE": pbo["ptc_type"], "NATURE": "CREATION", "HAUTEUR_AP": 0.0, "TYPE_APPUI": "NA", "EFFORT_APP": 0.0,
             "NB_BOITIER": 1, "STATUT": "EN PROJET", "PROPRIETAI": "UNIFIBER", "GESTIONNAI": "UNIFIBER",
             "ADRESSSE": address(pbo["imb"]), "VILLE": pbo["imb"]["VILLE"], "CODE_POSTA": pbo["imb"]["CODE_POSTA"],
             "X": pbo["point"][0], "Y": pbo["point"][1], "COMMENT": f"Created for {pbo['code']}"}
        )
        points.append(pbo["point"])
    for bpe in bpes:
        rows.append(
            {"fid": len(rows) + 1, "CODE": bpe["ptc_code"], "NOM": bpe["ptc_code"], "REF_PLAQUE": "JAD-MAR",
             "TYPE": "CHAMBRE", "NATURE": "CREATION", "HAUTEUR_AP": 0.0, "TYPE_APPUI": "NA", "EFFORT_APP": 0.0,
             "NB_BOITIER": 1, "STATUT": "EN PROJET", "PROPRIETAI": "UNIFIBER", "GESTIONNAI": "UNIFIBER",
             "ADRESSSE": "FIELD_SURVEY_REQUIRED", "VILLE": "ELJADIDA", "CODE_POSTA": 24000,
             "X": bpe["point"][0], "Y": bpe["point"][1], "COMMENT": f"Created for {bpe['code']}"}
        )
        points.append(bpe["point"])
    return rows, points


def site_pm_partition_rows(shape_dir: Path, bpes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[tuple[float, float]]]:
    rows: list[dict[str, Any]] = []
    points: list[tuple[float, float]] = []
    for row, point in point_rows(shape_dir / "SITE"):
        if row.get("TYPE") != "NRO":
            continue
        copied = dict(row)
        copied["fid"] = len(rows) + 1
        rows.append(copied)
        points.append(point)

    seen_pm_codes: set[str] = set()
    for bpe in bpes:
        if bpe.get("level") != "LEAF" or bpe["pm_code"] in seen_pm_codes:
            continue
        seen_pm_codes.add(bpe["pm_code"])
        point = bpe["pm_point"]
        rows.append(
            {
                "fid": len(rows) + 1,
                "CODE": bpe["pm_code"],
                "REF_PLAQUE": "JAD-MAR",
                "REF_NRO": bpe["nro_code"],
                "TYPE": "PM",
                "FABRIQUANT": "FBR",
                "STATUT": "EN PROJET",
                "PROPRIETAI": "UNIFIBER",
                "PROPRETAI": "UNIFIBER",
                "GESTIONNAI": "UNIFIBER",
                "ADRESSSE": f"Task 2 PM partition {bpe['pm_partition_id']}",
                "ADRESSE": f"Task 2 PM partition {bpe['pm_partition_id']}",
                "VILLE": "ELJADIDA",
                "COMMUNE": "ELJADIDA",
                "CODE_POSTA": 24000,
                "X": point[0],
                "Y": point[1],
            }
        )
        points.append(point)
    return rows, points


def write_outputs(shape_dir: Path, output_dir: Path, pbos: list[dict[str, Any]], bpes: list[dict[str, Any]], mappings: list[dict[str, Any]]) -> None:
    bpe_validation = validate_bpe_clusters(bpes)
    if not bpe_validation["capacity_ok"]:
        raise ValueError(f"Refusing to write over-capacity BPE output: {bpe_validation['capacity_violations']}")
    placement_validation = validate_pbo_placements(pbos)
    if not placement_validation["imb_overlap_ok"]:
        raise ValueError(f"Refusing to write overlapping PBO output: {placement_validation['imb_overlaps']}")
    output_dir.mkdir(parents=True, exist_ok=True)
    boite_fields, _ = read_dbf(shape_dir / "BOITE.dbf")
    ptech_fields, _ = read_dbf(shape_dir / "PTECH.dbf")
    site_fields, _ = read_dbf(shape_dir / "SITE.dbf")
    for name, fields, rows, points, source in (
        ("BOITE_ALGORITHM", boite_fields, *boite_rows(pbos, bpes), "BOITE"),
        ("PTECH_ALGORITHM", ptech_fields, *ptech_rows(pbos, bpes), "PTECH"),
        ("SITE_PM_PARTITION", site_fields, *site_pm_partition_rows(shape_dir, bpes), "SITE"),
    ):
        base = output_dir / name
        write_point_shapefile(base, points)
        write_dbf(base.with_suffix(".dbf"), fields, rows)
        shutil.copyfile(shape_dir / f"{source}.prj", base.with_suffix(".prj"))
        base.with_suffix(".cpg").write_text("UTF-8", encoding="ascii")

    members = []
    for bpe in bpes:
        if bpe["level"] != "LEAF":
            continue
        for pbo in bpe["members"]:
            members.append({"BPE_CODE": bpe["code"], "LEVEL": bpe["level"], "PARENT_BPE": bpe["parent_code"],
                            "PM_PARTITION": bpe["pm_partition_id"], "REF_PM": bpe["pm_code"],
                            "BPE_MEDOID_PBO": "",
                            "BPE_CENTER_METHOD": bpe["center_method"],
                            "BPE_CENTER_X": bpe["point"][0], "BPE_CENTER_Y": bpe["point"][1],
                            "PBO_CODE": pbo["code"], "IMB_CODE": pbo["imb"]["CODE"],
                            "PBO_DEMAND": pbo["demand"], "BPE_DEMAND": bpe["demand"],
                            "BPE_CAPACITY": bpe["capacity"]})
    partition_rows = partition_summary_rows(pbos, bpes)
    validation_text = json.dumps(bpe_validation, ensure_ascii=False, indent=2)
    (output_dir / "BPE_cluster_validation.json").write_text(validation_text, encoding="utf-8")
    (output_dir / "partition_validation.json").write_text(validation_text, encoding="utf-8")
    (output_dir / "PBO_placement_validation.json").write_text(
        json.dumps(placement_validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for filename, data in (
        ("IMB_PBO_BPE_mapping.csv", mappings),
        ("BPE_cluster_members.csv", members),
        ("PM_partition_summary.csv", partition_rows),
    ):
        with (output_dir / filename).open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(data[0]))
            writer.writeheader()
            writer.writerows(data)

    from openpyxl import Workbook

    workbook = Workbook()
    summary = workbook.active
    summary.title = "Summary"
    summary.append(["metric", "value"])
    for row in [
        ("planning_order", "IMB demand -> non-overlapping PBO placement around IMB -> task2 IMB-only 576-port PM partition -> hard-capacity BPE spatial candidates -> geometric-median BPE -> new PTECH"),
        ("pbo_count", len(pbos)), ("bpe_count", len(bpes)), ("new_ptech_count", len(pbos) + len(bpes)),
        ("pm_partition_count", len(partition_rows)), ("pm_partition_capacity_limit", TASK2_PM_PARTITION_CAPACITY),
        ("bpe_capacity", BPE_CAPACITY), ("bpe_target_utilization_ratio", BPE_TARGET_UTILIZATION_RATIO),
        ("bpe_target_demand", BPE_TARGET_DEMAND), ("bpe_soft_score", bpe_validation["total_score"]),
        ("bpe_capacity_constraint", f"hard limit: every BPE demand <= {BPE_CAPACITY}; demand closeness is scored softly within the feasible set"),
        ("pbo_placement_rule", placement_validation["placement_rule"]),
        ("pbo_imb_overlap_ok", placement_validation["imb_overlap_ok"]),
        ("pbo_minimum_served_imb_offset_m", placement_validation["minimum_served_imb_offset_m"]),
        ("served_ports", sum(pbo["demand"] for pbo in pbos)), ("pbo_capacity", sum(pbo["capacity"] for pbo in pbos)),
        ("installed_bpe_capacity", sum(bpe["capacity"] for bpe in bpes)), ("pbo_capacity_levels", str(PBO_CAPACITIES)),
        ("bpe_by_level", str(dict(Counter(bpe["level"] for bpe in bpes)))),
    ]:
        summary.append(row)
    pbo_sheet_rows = [
        {
            **pbo,
            "pbo_x": pbo["point"][0],
            "pbo_y": pbo["point"][1],
            "imb_x": imb_point_for_pbo(pbo)[0],
            "imb_y": imb_point_for_pbo(pbo)[1],
            "pbo_imb_offset_m": pbo["placement"]["offset_m"],
            "pbo_bearing_deg": pbo["placement"]["bearing_deg"],
        }
        for pbo in pbos
    ]
    sheets = [("PBO_plan", pbo_sheet_rows, ["code", "ptc_code", "ptc_type", "pm_code", "pm_partition_id", "leaf_bpe_code", "demand", "capacity", "pbo_x", "pbo_y", "imb_x", "imb_y", "pbo_imb_offset_m", "pbo_bearing_deg"]),
              ("BPE_plan", bpes, ["code", "ptc_code", "pm_code", "pm_partition_id", "center_method", "cluster_score", "plan_score", "candidate_count", "demand", "capacity", "type_struc", "level", "parent_code", "selection_method"]),
              ("PM_partitions", partition_rows, list(partition_rows[0])),
              ("IMB_PBO", mappings, list(mappings[0])), ("BPE_members", members, list(members[0]))]
    for title, data, headers in sheets:
        sheet = workbook.create_sheet(title)
        sheet.append(headers)
        for item in data:
            sheet.append([item.get(header) for header in headers])
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = min(80, max(14, max(len(str(cell.value or "")) for cell in column) + 2))
    summary.column_dimensions["A"].width = 28
    summary.column_dimensions["B"].width = 92
    workbook.save(output_dir / "PBO_BPE_algorithm_plan.xlsx")


def draw_comparison(shape_dir: Path, output_dir: Path, pbos: list[dict[str, Any]], bpes: list[dict[str, Any]]) -> None:
    reference = point_rows(shape_dir / "BOITE")
    reference_pbo = [item for item in reference if item[0].get("TYPE") == "PBO"]
    reference_bpe = [item for item in reference if item[0].get("TYPE") == "BPE"]
    pm = [item for item in point_rows(shape_dir / "SITE") if item[0].get("TYPE") == "PM"]
    leaf_bpes = sorted([bpe for bpe in bpes if bpe["level"] == "LEAF"], key=lambda item: item["code"])
    bpe_colours = leaf_bpe_colour_map(leaf_bpes)
    task2_pm_points = {
        bpe["pm_code"]: bpe["pm_point"]
        for bpe in bpes
        if bpe.get("level") == "LEAF" and bpe.get("pm_point")
    }
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FormatStrFormatter
    figure, axis = plt.subplots(figsize=(11, 7), constrained_layout=True)
    axis.scatter([point[0] for _, point in reference_pbo], [point[1] for _, point in reference_pbo], marker="^", color="#dc2626", s=22, alpha=0.78, label=f"Reference PBO ({len(reference_pbo)})", zorder=2)
    axis.scatter([point[0] for _, point in reference_bpe], [point[1] for _, point in reference_bpe], marker="s", color="#991b1b", s=34, alpha=0.85, label=f"Reference BPE ({len(reference_bpe)})", zorder=3)
    for index, bpe in enumerate(leaf_bpes):
        colour = bpe_colours[bpe["code"]]
        member_points = [imb_point_for_pbo(pbo) for pbo in bpe["members"]]
        axis.scatter(
            [point[0] for point in member_points],
            [point[1] for point in member_points],
            marker="o",
            color=colour,
            edgecolors="white",
            s=76,
            linewidths=1.0,
            alpha=0.84,
            label="Algorithm IMB (colored by leaf BPE)" if index == 0 else None,
            zorder=4,
        )
    axis.scatter(
        [pbo["point"][0] for pbo in pbos],
        [pbo["point"][1] for pbo in pbos],
        marker="^",
        facecolors="white",
        edgecolors="#ea580c",
        s=48,
        linewidths=1.15,
        label=f"Algorithm PBO offset from IMB ({len(pbos)})",
        zorder=5,
    )
    axis.scatter([bpe["point"][0] for bpe in bpes], [bpe["point"][1] for bpe in bpes], marker="s", facecolors="none", edgecolors="#16a34a", s=96, linewidths=1.8, label=f"Algorithm BPE ({len(bpes)})", zorder=6)
    axis.scatter([point[0] for _, point in pm], [point[1] for _, point in pm], marker="*", color="#111827", s=145, label=f"Reference PM ({len(pm)})", zorder=7)
    if task2_pm_points:
        axis.scatter([point[0] for point in task2_pm_points.values()], [point[1] for point in task2_pm_points.values()], marker="+", color="#7c3aed", s=180, linewidths=1.8, label=f"Task2 PM partitions ({len(task2_pm_points)})", zorder=8)
    axis.set_title("Task2 PM-partition-constrained PBO/BPE plan (IMBs colored by leaf BPE)")
    axis.set_xlabel("Longitude")
    axis.set_ylabel("Latitude")
    axis.xaxis.set_major_formatter(FormatStrFormatter("%.5f"))
    axis.yaxis.set_major_formatter(FormatStrFormatter("%.5f"))
    axis.set_aspect("equal", adjustable="box")
    axis.grid(True, alpha=0.22)
    axis.legend(loc="best")
    figure.savefig(output_dir / "PBO_BPE_reference_vs_algorithm.png", dpi=220)
    plt.close(figure)
    draw_imb_bpe_cluster_colors(output_dir, pbos, bpes)


def draw_imb_bpe_cluster_colors(
    output_dir: Path, pbos: list[dict[str, Any]], bpes: list[dict[str, Any]]
) -> None:
    """Draw the IMB-to-leaf-BPE assignment using one colour per BPE cluster."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FormatStrFormatter

    leaf_bpes = sorted([bpe for bpe in bpes if bpe["level"] == "LEAF"], key=lambda item: item["code"])
    bpe_colours = leaf_bpe_colour_map(leaf_bpes)
    figure, axis = plt.subplots(figsize=(13, 8))

    for bpe in leaf_bpes:
        colour = bpe_colours[bpe["code"]]
        members = bpe["members"]
        member_points = [imb_point_for_pbo(pbo) for pbo in members]
        for pbo in members:
            imb_point = imb_point_for_pbo(pbo)
            axis.plot(
                [imb_point[0], pbo["point"][0]],
                [imb_point[1], pbo["point"][1]],
                color="#ea580c",
                linestyle="--",
                linewidth=0.7,
                alpha=0.72,
                zorder=1,
            )
            axis.plot(
                [pbo["point"][0], bpe["point"][0]],
                [pbo["point"][1], bpe["point"][1]],
                color=colour,
                linewidth=0.8,
                alpha=0.38,
                zorder=1,
            )
        axis.scatter(
            [point[0] for point in member_points],
            [point[1] for point in member_points],
            marker="o",
            color=colour,
            edgecolors="white",
            linewidths=0.65,
            s=52,
            alpha=0.92,
            zorder=2,
        )
        axis.scatter(
            [pbo["point"][0] for pbo in members],
            [pbo["point"][1] for pbo in members],
            marker="^",
            facecolors="white",
            edgecolors="#ea580c",
            linewidths=0.9,
            s=48,
            zorder=3,
        )
        axis.scatter(
            [bpe["point"][0]],
            [bpe["point"][1]],
            marker="s",
            color=colour,
            edgecolors="#111827",
            linewidths=1.35,
            s=150,
            label=f"{bpe['code']} ({bpe['demand']}/{bpe['capacity']})",
            zorder=4,
        )
        axis.annotate(
            bpe["code"].rsplit("-", 1)[-1],
            bpe["point"],
            xytext=(5, 5),
            textcoords="offset points",
            fontsize=8,
            color="#111827",
            zorder=5,
        )

    axis.set_title("IMB/PBO to BPE cluster assignment (PBO offset from IMB)")
    axis.set_xlabel("Longitude")
    axis.set_ylabel("Latitude")
    axis.xaxis.set_major_formatter(FormatStrFormatter("%.5f"))
    axis.yaxis.set_major_formatter(FormatStrFormatter("%.5f"))
    axis.set_aspect("equal", adjustable="box")
    axis.grid(True, alpha=0.22)
    axis.legend(
        title="BPE (demand/capacity)",
        loc="upper left",
        bbox_to_anchor=(1.01, 1.0),
        fontsize=8,
        title_fontsize=9,
    )
    figure.tight_layout()
    figure.savefig(output_dir / "IMB_BPE_cluster_colors.png", dpi=220, bbox_inches="tight")
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan PBO/BPE first and derive the PTECH layer second.")
    parser.add_argument(
        "shape_dir",
        nargs="?",
        type=Path,
        default=DEFAULT_SHAPE_DIR,
        help=f"Completed example Shape directory (default: {DEFAULT_SHAPE_DIR})",
    )
    parser.add_argument(
        "output_dir",
        nargs="?",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--fixed-pm",
        type=Path,
        default=None,
        help="Optional task-3/RL fixed PM plan JSON; preserves its IMB assignments and coordinates.",
    )
    parser.add_argument(
        "--task2-partition",
        type=Path,
        default=None,
        help="Optional Task-2 partition JSON. When provided, do not recompute ZPM membership.",
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
    if not (args.shape_dir / "IMB.shp").exists():
        from build_sample_data import build_sample_shape_dir

        build_sample_shape_dir(args.shape_dir)
    pbos, bpes, mappings = plan(
        args.shape_dir,
        args.output_dir / "task2_pm_partition",
        fixed_pm_path=args.fixed_pm,
        task2_partition_path=args.task2_partition,
        pbo_offset_m=args.pbo_offset_m,
    )
    write_outputs(args.shape_dir, args.output_dir, pbos, bpes, mappings)
    draw_comparison(args.shape_dir, args.output_dir, pbos, bpes)
    print(f"Planned {len(pbos)} PBOs, {len(bpes)} BPEs, and {len(pbos) + len(bpes)} new PTECH points")


if __name__ == "__main__":
    main()
