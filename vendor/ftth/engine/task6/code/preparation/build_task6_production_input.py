"""Assemble the current Task-2/3/4/recognition outputs into a Task-6 input.

The recognition road export deliberately contains unknown construction
attributes.  This adapter keeps those source values in ``source_*`` fields and
uses the explicit Task-6 execution assumptions (all roads open-cut eligible,
unlimited duct capacity) so the heuristic planner can be executed now.
"""

from __future__ import annotations

import csv
import json
import struct
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from project_paths import (
    RECOGNITION_BUILDING_INPUT_DIR,
    TASK2_ZONE_PARTITION_INPUT_DIR,
    TASK2_ZONE_PARTITION_OUTPUT_DIR,
    TASK4_OUTPUT_DIR,
    TASK6_INPUT_PREPARATION_OUTPUT_DIR,
    TASK6_PRODUCTION_INPUT,
    TASK6_WHOLE_ROADS_OUTPUT_DIR,
    relative_path,
)

TASK2_INPUT = TASK2_ZONE_PARTITION_INPUT_DIR / "task2_cleaned_input.json"
TASK2_FEATURES = TASK2_ZONE_PARTITION_OUTPUT_DIR / "recognition_input_entrance_features" / "task2_geometry_features.json"
ROADS = TASK6_WHOLE_ROADS_OUTPUT_DIR / "trunk_roads_whole.json"
BUILDINGS = TASK6_WHOLE_ROADS_OUTPUT_DIR / "task6_buildings.geojson"
IMB_BUILDINGS = RECOGNITION_BUILDING_INPUT_DIR / "imb_partition" / "imb_buildings.geojson"
COST_ZONES = TASK6_WHOLE_ROADS_OUTPUT_DIR / "task6_cost_zones.geojson"
PARTITION = TASK4_OUTPUT_DIR / "pbo_bpe_plan" / "task2_pm_partition" / "task2_pm_partition_result.json"
PM_SITES = TASK4_OUTPUT_DIR / "pbo_bpe_plan" / "SITE_PM_PARTITION.dbf"
PBO_BASE = TASK4_OUTPUT_DIR / "pbo_bpe_plan" / "BOITE_ALGORITHM"
TASK4_DIR = TASK4_OUTPUT_DIR / "pbo_bpe_plan"
TASK4_IMB_INPUT = TASK4_DIR / "task2_pm_partition" / "task4_imb_only_dataset.json"
PM_PARTITION_SUMMARY = TASK4_DIR / "PM_partition_summary.csv"
IMB_ASSIGNMENT = TASK4_DIR / "IMB_PBO_BPE_mapping.csv"
OUTPUT = TASK6_PRODUCTION_INPUT
INPUT_BASE_DIR = OUTPUT.parent

TASK2_SRC = ROOT / "任务2" / "zone_partition" / "src"
sys.path.insert(0, str(TASK2_SRC))
from task2_geometry_features import _coord_output, nearest_polygons, polygon_centroid, project_rings


def read_dbf(path: Path) -> list[dict[str, Any]]:
    data = path.read_bytes()
    record_count = struct.unpack("<I", data[4:8])[0]
    header_size = struct.unpack("<H", data[8:10])[0]
    record_size = struct.unpack("<H", data[10:12])[0]
    fields: list[tuple[str, str, int, int]] = []
    offset = 32
    while offset < header_size - 1:
        field = data[offset : offset + 32]
        fields.append((field[:11].split(b"\0", 1)[0].decode("latin1"), chr(field[11]), field[16], field[17]))
        offset += 32
    rows: list[dict[str, Any]] = []
    for index in range(record_count):
        row = data[header_size + index * record_size : header_size + (index + 1) * record_size]
        if not row or row[:1] == b"*":
            continue
        cursor = 1
        parsed: dict[str, Any] = {}
        for name, kind, width, decimals in fields:
            raw = row[cursor : cursor + width].decode("latin1").strip()
            cursor += width
            if kind in {"N", "F"}:
                parsed[name] = float(raw) if raw and decimals else (int(raw) if raw else None)
            else:
                parsed[name] = raw or None
        rows.append(parsed)
    return rows


def read_points(path: Path) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    with path.with_suffix(".shp").open("rb") as handle:
        handle.read(100)
        while True:
            header = handle.read(8)
            if not header:
                break
            size = struct.unpack(">I", header[4:8])[0] * 2
            content = handle.read(size)
            if struct.unpack("<I", content[:4])[0] == 1:
                points.append(struct.unpack("<dd", content[4:20]))
    return points


def load_geojson(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return [
        {**(feature.get("properties") or {}), "geometry": feature.get("geometry")}
        for feature in payload.get("features", [])
    ]


def _record_point(record: dict[str, Any]) -> tuple[float, float]:
    return float(record.get("x")), float(record.get("y"))


def build_imb_only_groups(
    imb_buildings: list[dict[str, Any]],
    imb_by_code: dict[str, dict[str, Any]],
    zpm_to_pm: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build operational groups from IMB cells, keeping one group per PM.

    The source recognised parent groups can straddle the fixed ZPM boundary.
    Task 6 needs a single fixed PM owner for every group, so IMB cells are
    grouped by their authoritative Task-2 PM assignment.  Any shared edge or
    gap up to the existing 15 m grouping threshold becomes an eligible group
    entrance under the relaxed business rule.
    """
    by_pm: dict[str, list[dict[str, Any]]] = {}
    pm_to_zpm = {pm_id: zpm_id for zpm_id, pm_id in zpm_to_pm.items()}
    for record in imb_buildings:
        imb_id = str(record.get("imb_id") or record.get("code") or record.get("id") or "")
        source = imb_by_code.get(imb_id, {})
        zpm_id = str(source.get("zpm_id") or "")
        pm_id = str(source.get("pm_id") or "")
        if not pm_id and zpm_id:
            pm_id = zpm_to_pm.get(zpm_id, "")
        if not zpm_id and pm_id:
            zpm_id = pm_to_zpm.get(pm_id, "")
        if not pm_id:
            continue
        building = dict(record)
        building.update(
            {
                "id": f"BLDG-{imb_id}",
                "building_id": f"BLDG-{imb_id}",
                "imb_id": imb_id,
                "group_id": None,
                "pm_id": pm_id,
                "zpm_id": zpm_id,
                "semantic_role": "imb_partitioned_building",
                "ports": int(source.get("ports") or record.get("ports") or record.get("nb_loc_tot") or 0),
            }
        )
        by_pm.setdefault(pm_id, []).append(building)

    groups: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    for group_number, (pm_id, members) in enumerate(sorted(by_pm.items()), 1):
        zpm_id = str(members[0].get("zpm_id") or "")
        group_id = f"BG-{zpm_id}-{group_number:03d}"
        for member in members:
            member["group_id"] = group_id
        center_x = sum(float(member["x"]) for member in members) / len(members)
        center_y = sum(float(member["y"]) for member in members) / len(members)
        group = {
            "id": group_id,
            "group_id": group_id,
            "pm_id": pm_id,
            "zpm_id": zpm_id,
            "znro_id": "ZNRO_001",
            "x": round(center_x, 9),
            "y": round(center_y, 9),
            "demand": sum(int(member.get("ports") or 0) for member in members),
            "building_ids": [member["building_id"] for member in members],
            "building_count": len(members),
            "semantic_role": "imb_only_operational_group",
            "entrance_rule": "shared_edge_or_inter_building_gap",
        }
        groups.append(group)

        # Keep all distinct pairwise shared-edge/gap candidates.  The planner
        # itself ranks these candidates by road cost and takes K alternatives.
        projected = {member["building_id"]: project_rings(member["geometry"], "EPSG:4326") for member in members}
        for left_index, left in enumerate(members):
            for right in members[left_index + 1 :]:
                distance, left_point, right_point = nearest_polygons(projected[left["building_id"]], projected[right["building_id"]])
                if distance > 15.0:
                    continue
                midpoint = ((left_point[0] + right_point[0]) / 2.0, (left_point[1] + right_point[1]) / 2.0)
                location = _coord_output(midpoint)
                entries.append(
                    {
                        "id": f"GE-{group_id}-{len([item for item in entries if item['group_id'] == group_id]) + 1:04d}",
                        "entrance_type": "building_group",
                        "group_id": group_id,
                        "pm_id": pm_id,
                        "zpm_id": zpm_id,
                        "from_building_id": left["building_id"],
                        "to_building_id": right["building_id"],
                        "x": location["x"],
                        "y": location["y"],
                        "calculation_x": location["calculation_x"],
                        "calculation_y": location["calculation_y"],
                        "gap_width_m": round(distance, 3),
                        "method": "shared_edge_or_inter_building_gap",
                        "eligible": True,
                        "accessible": True,
                        "needs_review": False,
                    }
                )
        group_entries = [item for item in entries if item["group_id"] == group_id]
        group["entrance_ids"] = [item["id"] for item in group_entries]
        group["representative_entrance_id"] = group_entries[0]["id"] if group_entries else None
    return groups, entries


def main() -> int:
    task2 = json.loads(TASK2_INPUT.read_text(encoding="utf-8-sig"))
    features = json.loads(TASK2_FEATURES.read_text(encoding="utf-8-sig"))
    roads_source = json.loads(ROADS.read_text(encoding="utf-8-sig"))["trunk_roads"]
    # Keep source uncertainty while making the execution assumption explicit.
    roads = []
    for road in roads_source:
        item = dict(road)
        item.update(
            {
                "source_open_cut_allowed": item.get("OPEN_CUT_ALLOWED"),
                "source_duct_capacity": item.get("DUCT_CAPACITY"),
                "open_cut_allowed": True,
                "OPEN_CUT_ALLOWED": True,
                "capacity": "unlimited",
                "DUCT_CAPACITY": "unlimited",
                "cost_per_m": float(item.get("planning_cost_multiplier") or item.get("cost_factor") or 1.0),
                "construction_assumption": "user_explicit_assumption",
            }
        )
        roads.append(item)

    # Fixed PM locations are the Task-4 partition outputs; the DBF stores X/Y.
    # The summary is the authoritative PM/ZPM relation. Do not infer a ZPM
    # from a PM code suffix because fixed Task-3 plans may use arbitrary ids.
    with PM_PARTITION_SUMMARY.open("r", encoding="utf-8-sig", newline="") as handle:
        partition_summary = list(csv.DictReader(handle))
    zpm_to_pm = {
        str(row.get("PM_PARTITION") or ""): str(row.get("REF_PM") or "")
        for row in partition_summary
        if row.get("PM_PARTITION") and row.get("REF_PM")
    }
    pm_to_zpm = {pm_id: zpm_id for zpm_id, pm_id in zpm_to_pm.items()}
    if not zpm_to_pm or len(pm_to_zpm) != len(zpm_to_pm):
        raise ValueError("Task-4 PM partition summary has missing or duplicate PM/ZPM relations")
    pm_rows = read_dbf(PM_SITES)
    pm_sites = []
    nro_sites = []
    for row in pm_rows:
        kind = str(row.get("TYPE") or "").upper()
        record = {
            "id": str(row.get("CODE")),
            "code": str(row.get("CODE")),
            "x": float(row.get("X")),
            "y": float(row.get("Y")),
            "capacity": 576 if kind == "PM" else 2048,
            "source": "task4_fixed_site_partition",
        }
        if kind == "NRO":
            nro_sites.append(record)
        elif kind == "PM":
            zpm = pm_to_zpm.get(record["id"])
            if not zpm:
                raise ValueError(f"Task-4 PM {record['id']} has no PM_PARTITION summary relation")
            record.update({"zpm_id": zpm, "partition_id": zpm, "nro_id": nro_sites[0]["id"] if nro_sites else None, "community_id": "ZNRO_001"})
            pm_sites.append(record)
    if not nro_sites:
        raise ValueError("Task-4 site output contains no NRO")
    # User-supplied Task-6 NRO location (input was given as latitude, longitude).
    nro_sites[0].update({"x": -8.5317504, "y": 33.2237864, "source": "user_fixed_task6_location"})

    partition = json.loads(PARTITION.read_text(encoding="utf-8-sig"))
    zpm = partition.get("zpm") or {}
    with IMB_ASSIGNMENT.open("r", encoding="utf-8-sig", newline="") as handle:
        mapping_rows = list(csv.DictReader(handle))
    fixed_assignment = {
        str(row.get("IMB_CODE") or ""): str(row.get("PM_PARTITION") or "")
        for row in mapping_rows
        if row.get("IMB_CODE") and row.get("PM_PARTITION")
    }
    if not fixed_assignment:
        raise ValueError("Task-4 IMB/PBO/BPE mapping contains no fixed IMB assignments")
    unknown_partitions = sorted(set(fixed_assignment.values()) - set(zpm_to_pm))
    if unknown_partitions:
        raise ValueError(f"Task-4 IMB mapping references unknown PM partitions: {unknown_partitions}")
    zpm["assignment"] = fixed_assignment

    imb_source_path = TASK4_IMB_INPUT if TASK4_IMB_INPUT.is_file() else TASK2_INPUT
    source_nodes = json.loads(imb_source_path.read_text(encoding="utf-8-sig")).get("nodes", [])
    source_imbs = [node for node in source_nodes if str(node.get("type") or "IMB").upper() == "IMB"]
    imb_nodes = [
        {
            "id": str(node.get("id") or node.get("code")),
            "code": str(node.get("code") or node.get("id")),
            "x": float(node["x"]),
            "y": float(node["y"]),
            "ports": int(node.get("ports") or node.get("nb_loc_tot") or 0),
            "pm_id": zpm_to_pm.get(str((zpm.get("assignment") or {}).get(str(node.get("code") or node.get("id"))))) ,
        }
        for node in source_imbs
        if str(node.get("code") or node.get("id")) in fixed_assignment
    ]
    imb_by_code = {item["code"]: item for item in imb_nodes}
    assignment = zpm.get("assignment") or {}
    if set(imb_by_code) != set(assignment):
        missing = sorted(set(assignment) - set(imb_by_code))
        extra = sorted(set(imb_by_code) - set(assignment))
        raise ValueError(f"Task-4 assignment and IMB source differ: missing={missing[:5]}, extra={extra[:5]}")
    for pm in pm_sites:
        assigned_codes = sorted(code for code, zpm_id in assignment.items() if str(zpm_id) == str(pm["zpm_id"]))
        pm["assigned_imb_ids"] = assigned_codes
        pm["demand"] = sum(int(imb_by_code.get(code, {}).get("ports") or 0) for code in assigned_codes)

    clusters_by_id = {str(cluster.get("id") or ""): cluster for cluster in zpm.get("clusters") or []}
    fixed_clusters = []
    for zpm_id in sorted(zpm_to_pm):
        members = [imb_by_code[code] for code, owner in assignment.items() if owner == zpm_id]
        cluster = dict(clusters_by_id.get(zpm_id) or {})
        total_ports = sum(int(member.get("ports") or 0) for member in members)
        cluster.update(
            {
                "id": zpm_id,
                "member_count": len(members),
                "total_ports": total_ports,
                "capacity_limit": int(cluster.get("capacity_limit") or 576),
                "members": [
                    {"id": member["id"], "x": member["x"], "y": member["y"], "ports": member["ports"]}
                    for member in members
                ],
                "fixed_pm_code": zpm_to_pm[zpm_id],
            }
        )
        fixed_clusters.append(cluster)
    zpm["clusters"] = fixed_clusters

    bpe_rows = list(csv.DictReader((TASK4_DIR / "BPE_cluster_members.csv").open("r", encoding="utf-8-sig", newline="")))
    bpe_summary: dict[str, dict[str, Any]] = {}
    for row in bpe_rows:
        bpe_id = str(row.get("BPE_CODE") or "")
        if bpe_id and bpe_id not in bpe_summary:
            bpe_summary[bpe_id] = row

    # Use only IMB-partitioned buildings. Parent recognition groups can cross
    # the fixed ZPM boundary, so rebuild operational groups by PM ownership.
    imb_buildings = load_geojson(IMB_BUILDINGS)
    imb_source_by_code = {str(item.get("code") or item.get("imb_id") or item.get("id")): item for item in imb_nodes}
    groups, group_entries = build_imb_only_groups(imb_buildings, imb_source_by_code, zpm_to_pm)
    community_entries = [dict(item) for item in features.get("community_entrances", [])]
    if not community_entries:
        community_entries = [dict(item) for item in features.get("entrances", []) if str(item.get("entrance_type")) == "community"]
    entrances = community_entries + group_entries

    # Build fixed Task-4 BPE/PBO records from the authoritative CSV and DBF.
    pbo_rows = read_dbf(PBO_BASE.with_suffix(".dbf"))
    pbo_points = read_points(PBO_BASE)
    pbo_by_code = {str(row.get("CODE")): (row, point) for row, point in zip(pbo_rows, pbo_points) if str(row.get("TYPE") or "").upper() == "PBO"}
    bpe_plan: list[dict[str, Any]] = []
    seen_bpes: set[str] = set()
    pbo_plan: list[dict[str, Any]] = []
    for row in bpe_rows:
        bpe_id = str(row.get("BPE_CODE") or "")
        if not bpe_id:
            continue
        pm_id = str(row.get("REF_PM") or "")
        group = next((g for g in groups if g["pm_id"] == pm_id), None)
        if not group:
            continue
        if bpe_id not in seen_bpes:
            seen_bpes.add(bpe_id)
            bpe_plan.append({"id": bpe_id, "code": bpe_id, "x": float(row["BPE_CENTER_X"]), "y": float(row["BPE_CENTER_Y"]), "pm_id": pm_id, "zpm_id": group["zpm_id"], "group_id": group["id"], "demand": int(row.get("BPE_DEMAND") or 0), "capacity": int(row.get("BPE_CAPACITY") or 144), "member_pbo_ids": []})
        pbo_id = str(row.get("PBO_CODE") or "")
        if pbo_id in pbo_by_code:
            pbo_row, point = pbo_by_code[pbo_id]
            imb_code = str(row.get("IMB_CODE") or "")
            pbo_plan.append({"id": pbo_id, "code": pbo_id, "x": point[0], "y": point[1], "pm_id": pm_id, "zpm_id": group["zpm_id"], "bpe_id": bpe_id, "bpe_code": bpe_id, "building_id": f"BLDG-{imb_code}", "imb_id": imb_code, "demand": int(pbo_row.get("NB_LOGEMEN") or 0), "capacity": int(pbo_row.get("CAPACITE") or 0)})
            next(item for item in bpe_plan if item["id"] == bpe_id)["member_pbo_ids"].append(pbo_id)

    buildings = imb_buildings
    group_id_by_pm = {group["pm_id"]: group["id"] for group in groups}
    for building in buildings:
        imb_code = str(building.get("imb_id") or building.get("code") or building.get("id") or "")
        source = imb_source_by_code.get(imb_code, {})
        pm_id = str(source.get("pm_id") or "")
        building.update({"id": f"BLDG-{imb_code}", "building_id": f"BLDG-{imb_code}", "imb_id": imb_code, "pm_id": pm_id, "group_id": group_id_by_pm.get(pm_id), "ports": int(source.get("ports") or building.get("ports") or building.get("nb_loc_tot") or 0)})
    cost_zones = load_geojson(COST_ZONES)
    for zone in cost_zones:
        zone["id"] = zone.get("id") or zone.get("zone_id") or zone.get("code")
        zone["cost_factor"] = float(zone.get("cost_factor") or zone.get("cost_multiplier") or 2.0)

    # Include all IMBs from the task-2 graph, retaining the authoritative codes.
    task2_imb_by_code = {str(node.get("code")): node for node in task2.get("nodes", []) if node.get("type") == "IMB"}
    for item in imb_nodes:
        original = task2_imb_by_code.get(item["code"], {})
        item["ports"] = int(original.get("ports") or item["ports"] or 0)

    payload = {
        "meta": {"task": "task6_layered_fixed_assignment_plan", "coordinate_system": "EPSG:4326", "calculation_crs": "EPSG:32629", "scenario": "task2_task4_recognition_outputs_with_explicit_user_assumptions", "source_status": "production_assumed", "road_attribute_assumption": "OPEN_CUT_ALLOWED=true and duct capacity=unlimited for all roads", "nro_location_assumption": "NRO x=-8.5317504, y=33.2237864", "building_scope": "IMB_ONLY", "building_group_entrance_rule": "shared_edge_or_inter_building_gap", "sources": {"task2_input": relative_path(TASK2_INPUT, INPUT_BASE_DIR), "task2_features": relative_path(TASK2_FEATURES, INPUT_BASE_DIR), "recognition_roads": relative_path(ROADS, INPUT_BASE_DIR), "imb_buildings": relative_path(IMB_BUILDINGS, INPUT_BASE_DIR), "task4_partition": relative_path(PARTITION, INPUT_BASE_DIR), "task4_sites": relative_path(PM_SITES, INPUT_BASE_DIR), "task4_boite": relative_path(PBO_BASE.with_suffix('.dbf'), INPUT_BASE_DIR)}},
        "planning": {"algorithm": "layered_fixed_assignment_shared_edge_search", "main_trunk_optimization": "joint_shared_tree_exact_candidate_enumeration", "candidate_paths": 5, "default_edge_capacity": "unlimited", "road_cost_per_m": 1.0, "water_cost_factor": 2.0, "terminal_snap_distance_m": 80.0, "nro_road_access_radius_m": 50.0},
        "task2_partition": {"zpm": zpm},
        "nro_sites": nro_sites,
        "pm_sites": pm_sites,
        "building_groups": groups,
        "buildings": buildings,
        "entrances": entrances,
        "roads": roads,
        "water_cost_zones": cost_zones,
        "bpe_plan": bpe_plan,
        "pbo_plan": pbo_plan,
        "imb_points": imb_nodes,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    readiness = {
        "status": "READY_WITH_EXPLICIT_ASSUMPTIONS",
        "input": relative_path(OUTPUT, ROOT),
        "checks": {
            "task2_imb_partition": {"status": "PASS_FOR_FIXED_IMB_SET", "imb_count": len(imb_nodes), "zpm_count": len(zpm.get("clusters") or [])},
            "task3_fixed_sites": {"status": "PASS", "nro_count": len(nro_sites), "pm_count": len(pm_sites)},
            "task4_fixed_service_relations": {"status": "PASS", "bpe_count": len(bpe_plan), "pbo_count": len(pbo_plan), "unique_pbo_count": len({item["id"] for item in pbo_plan})},
            "recognition_roads": {"status": "PASS_WITH_EXPLICIT_ASSUMPTION", "count": len(roads), "unknown_source_attributes": len(roads), "execution_assumption": "open_cut_allowed=true; capacity=unlimited"},
            "entrances": {"status": "PASS_RELAXED_RULE", "community_entries": sum(1 for item in entrances if item.get("entrance_type") == "community"), "group_entries": len(group_entries), "pbo_derived_building_entries": len(pbo_plan), "rule": "shared edge or inter-building gap"},
            "recognition_building_scope": {"status": "PASS_IMB_ONLY", "legacy_recognition_candidates": len(features.get("buildings") or []), "building_footprints_used": len(buildings), "imb_buildings_used": len(buildings)},
        },
        "remaining_data": [],
        "assumptions": [
            "All supplied roads are treated as open-cut eligible.",
            "Duct capacity is unlimited for this run.",
            "Only the 51 IMB-partitioned buildings are included.",
            "Any shared edge or inter-building gap up to 15 m is an eligible building-group entrance.",
        ],
    }
    report_path = TASK6_INPUT_PREPARATION_OUTPUT_DIR / "task6_data_readiness_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(readiness, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "nro": len(nro_sites), "pm": len(pm_sites), "groups": len(groups), "roads": len(roads), "buildings": len(buildings), "bpes": len(bpe_plan), "pbos": len(pbo_plan), "imbs": len(imb_nodes), "entrances": len(entrances)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
