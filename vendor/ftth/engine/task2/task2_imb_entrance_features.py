"""Calculate entrances using recognition groups and IMB-derived buildings.

The recognised ``GEO_BUILDING`` polygons represent building groups.  They are
not individual buildings.  Individual building geometry comes from the
Voronoi cells produced by ``partition_buildings_by_imb.py`` with IMB seeds.
This module preserves that hierarchy when calculating building-group and
community entrances from a cleaned road layer.  Physical IMB-derived building
cells are retained as geometry, but building entrances are intentionally not
generated because the downstream algorithm no longer consumes them.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from task2_geometry_features import (
    _coord_output,
    _geometry,
    _geometry_output,
    _region_records,
    _region_rings,
    dedupe_points,
    iter_edges,
    load_features,
    nearest_polygon_to_line,
    polygon_area,
    polygon_centroid,
    project_lines,
    project_point_to_segment,
    project_rings,
    segment_intersection,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from project_paths import TASK2_ZONE_PARTITION_OUTPUT_DIR

TASK2_ENTRANCE_OUTPUT_DIR = TASK2_ZONE_PARTITION_OUTPUT_DIR / "imb_semantic_entrance_features"


def _id(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    raise ValueError(f"Record has no id in fields {keys}")


def _group_id(record: dict[str, Any]) -> str:
    return _id(record, "building_group_id", "group_id", "building_id", "id", "code", "CODE")


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def _imb_owners(topology: dict[str, Any], partition: dict[str, Any]) -> dict[str, dict[str, str | None]]:
    owners_by_node_id = {
        str(cell.get("imb_id")): {
            "pm_id": str(cell.get("owner_zpm_id")) if cell.get("owner_zpm_id") is not None else None,
            "znro_id": str(cell.get("owner_znro_id")) if cell.get("owner_znro_id") is not None else None,
        }
        for cell in partition.get("cells") or []
        if cell.get("imb_id") is not None
    }
    result = dict(owners_by_node_id)
    for node in topology.get("nodes") or []:
        if str(node.get("type") or "").upper() != "IMB":
            continue
        node_id = str(node.get("id") or "")
        owner = owners_by_node_id.get(node_id)
        if owner is None:
            continue
        for key in ("code", "id"):
            if node.get(key) not in (None, ""):
                result[str(node[key])] = owner
    return result


def _road_parts(roads: list[dict[str, Any]], crs: str) -> list[dict[str, Any]]:
    parts = []
    for index, record in enumerate(roads, 1):
        road_id = _id(record, "road_id", "id", "code", "CODE") if any(record.get(key) not in (None, "") for key in ("road_id", "id", "code", "CODE")) else f"ROAD_{index:04d}"
        for part_index, line in enumerate(project_lines(_geometry(record), crs), 1):
            if len(line) >= 2:
                parts.append({"id": road_id, "part": part_index, "line": line})
    return parts


def _boundary_candidates(
    rings: list[list[tuple[float, float]]],
    roads: list[dict[str, Any]],
    *,
    snap_tolerance_m: float,
    dedup_tolerance_m: float,
) -> list[dict[str, Any]]:
    candidates = []
    for road in roads:
        points = []
        for ring in rings:
            for boundary_start, boundary_end in iter_edges([ring]):
                for road_start, road_end in zip(road["line"], road["line"][1:]):
                    points.extend(segment_intersection(boundary_start, boundary_end, road_start, road_end))
        for point in dedupe_points(points, dedup_tolerance_m):
            candidates.append({"point": point, "road_id": road["id"], "distance_m": 0.0, "method": "exact_boundary_intersection", "eligible": True})

    if candidates:
        return candidates

    nearest = None
    for road in roads:
        distance, boundary_point, _ = nearest_polygon_to_line(rings, road["line"])
        if nearest is None or distance < nearest[0]:
            nearest = (distance, boundary_point, road["id"])
    if nearest is None or nearest[0] > snap_tolerance_m:
        return []
    return [{"point": nearest[1], "road_id": nearest[2], "distance_m": nearest[0], "method": "nearest_boundary_snap", "eligible": False}]


def _group_records(
    source_groups: list[dict[str, Any]],
    buildings_by_group: dict[str, list[dict[str, Any]]],
    *,
    group_crs: str,
) -> tuple[list[dict[str, Any]], dict[str, list[list[tuple[float, float]]]]]:
    groups = []
    rings_by_group = {}
    for record in source_groups:
        group_id = _group_id(record)
        rings = project_rings(_geometry(record), group_crs)
        if not rings:
            continue
        children = buildings_by_group.get(group_id, [])
        pm_ids = {child["pm_id"] for child in children if child["pm_id"] is not None}
        znro_ids = {child["znro_id"] for child in children if child["znro_id"] is not None}
        center = polygon_centroid(rings)
        location = _coord_output(center)
        groups.append({
            "id": group_id,
            "group_id": group_id,
            "source_group_id": group_id,
            "semantic_role": "recognized_building_group",
            "pm_id": next(iter(pm_ids)) if len(pm_ids) == 1 else None,
            "znro_id": next(iter(znro_ids)) if len(znro_ids) == 1 else None,
            "building_ids": [child["building_id"] for child in children],
            "building_count": len(children),
            "area_m2": round(polygon_area(rings), 3),
            **location,
            "centroid": location,
            "geometry": _geometry_output(_geometry(record), group_crs),
            "needs_review": bool(record.get("needs_review", 0)) or not children or len(pm_ids) > 1,
        })
        rings_by_group[group_id] = rings
    return groups, rings_by_group


def compute_imb_entrance_features(
    source_groups: list[dict[str, Any]],
    imb_building_cells: list[dict[str, Any]],
    roads: list[dict[str, Any]],
    topology: dict[str, Any],
    partition: dict[str, Any],
    *,
    group_crs: str = "EPSG:4326",
    building_crs: str = "EPSG:4326",
    road_crs: str = "EPSG:4326",
    partition_crs: str = "EPSG:4326",
    road_snap_tolerance_m: float = 2.0,
    entrance_dedup_tolerance_m: float = 1.0,
) -> dict[str, Any]:
    if road_snap_tolerance_m < 0 or entrance_dedup_tolerance_m < 0:
        raise ValueError("Entrance tolerances must be non-negative")
    owners = _imb_owners(topology, partition)
    buildings = []
    buildings_by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rings_by_building = {}
    for record in imb_building_cells:
        imb_id = _id(record, "imb_id", "code", "id")
        group_id = _id(record, "parent_building_group_id", "group_id")
        rings = project_rings(_geometry(record), building_crs)
        if not rings:
            continue
        owner = owners.get(imb_id, {})
        center = polygon_centroid(rings)
        location = _coord_output(center)
        building_id = f"BLDG-{imb_id}"
        building = {
            "id": building_id,
            "building_id": building_id,
            "imb_id": imb_id,
            "group_id": group_id,
            "parent_building_group_id": group_id,
            "semantic_role": "imb_partitioned_building",
            "pm_id": owner.get("pm_id"),
            "znro_id": owner.get("znro_id"),
            "ports": int(record.get("nb_loc_tot", record.get("ports", 0)) or 0),
            "area_m2": round(float(record.get("partition_area_m2") or polygon_area(rings)), 3),
            "partition_method": record.get("partition_method"),
            "assignment_method": record.get("assignment_method"),
            "needs_review": str(record.get("assignment_method") or "").endswith("review"),
            **location,
            "centroid": location,
            "geometry": _geometry_output(_geometry(record), building_crs),
        }
        buildings.append(building)
        buildings_by_group[group_id].append(building)
        rings_by_building[building_id] = rings

    groups, rings_by_group = _group_records(source_groups, buildings_by_group, group_crs=group_crs)
    roads_by_part = _road_parts(roads, road_crs)
    group_entrances = []
    for group in groups:
        candidates = _boundary_candidates(rings_by_group[group["group_id"]], roads_by_part, snap_tolerance_m=road_snap_tolerance_m, dedup_tolerance_m=entrance_dedup_tolerance_m)
        for candidate in candidates:
            location = _coord_output(candidate["point"])
            group_entrances.append({
                "id": f"BGE-{len(group_entrances) + 1:04d}",
                "entrance_type": "building_group",
                "group_id": group["group_id"],
                "pm_id": group["pm_id"],
                "znro_id": group["znro_id"],
                "road_id": candidate["road_id"],
                **location,
                "coordinates": location,
                "distance_m": round(candidate["distance_m"], 3),
                "method": candidate["method"],
                "eligible": candidate["eligible"],
                "accessible": candidate["eligible"],
                "needs_review": bool(group["needs_review"]) or not candidate["eligible"],
            })
        entries = [entry for entry in group_entrances if entry["group_id"] == group["group_id"]]
        group["entrance_ids"] = [entry["id"] for entry in entries]
        group["representative_entrance_id"] = entries[0]["id"] if entries else None

    community_entrances = []
    znro_regions, _ = _region_records(partition)
    for region in znro_regions:
        region_id = str(region.get("id"))
        rings = _region_rings(region, partition_crs)
        if not rings:
            continue
        for road in roads_by_part:
            points = []
            for ring in rings:
                for boundary_start, boundary_end in iter_edges([ring]):
                    for road_start, road_end in zip(road["line"], road["line"][1:]):
                        points.extend(segment_intersection(boundary_start, boundary_end, road_start, road_end))
            for point in dedupe_points(points, entrance_dedup_tolerance_m):
                location = _coord_output(point)
                community_entrances.append({
                    "id": f"CE-{len(community_entrances) + 1:04d}",
                    "entrance_type": "community",
                    "community_id": region_id,
                    "znro_id": region_id,
                    "road_id": road["id"],
                    **location,
                    "coordinates": location,
                    "method": "znro_boundary_road_intersection",
                    "eligible": True,
                    "needs_review": True,
                })

    unresolved = []
    for group in groups:
        if not group["building_ids"]:
            unresolved.append({"type": "building_group", "id": group["group_id"], "reason": "no_imb_building"})
        if not any(entry["group_id"] == group["group_id"] and entry["eligible"] for entry in group_entrances):
            unresolved.append({"type": "building_group", "id": group["group_id"], "reason": "no_exact_group_road_intersection"})
    for region in znro_regions:
        region_id = str(region.get("id"))
        if not any(entry["znro_id"] == region_id for entry in community_entrances):
            unresolved.append({"type": "community", "id": region_id, "reason": "no_znro_road_intersection"})

    entrances = [*group_entrances, *community_entrances]
    return {
        "schema_version": "task2-imb-entrance-features-1.1",
        "semantics": {
            "recognized_GEO_BUILDING": "building_group",
            "imb_voronoi_cell": "building",
            "community": "ZNRO_service_area",
            "building_entrance": "excluded; not required by the downstream algorithm",
        },
        "parameters": {"road_snap_tolerance_m": road_snap_tolerance_m, "entrance_dedup_tolerance_m": entrance_dedup_tolerance_m},
        "summary": {
            "recognized_building_group_count": len(groups),
            "imb_building_count": len(buildings),
            "building_group_entrance_count": len(group_entrances),
            "community_entrance_count": len(community_entrances),
            "unresolved_count": len(unresolved),
        },
        "building_groups": groups,
        "buildings": buildings,
        "entrances": entrances,
        "building_group_entrances": group_entrances,
        "community_entrances": community_entrances,
        "validation": {
            "all_imb_buildings_have_one_group": all(item["group_id"] in rings_by_group for item in buildings),
            "building_ids_unique": len({item["building_id"] for item in buildings}) == len(buildings),
            "entrance_ids_unique": len({item["id"] for item in entrances}) == len(entrances),
            "unresolved": unresolved,
        },
    }


def write_task6_views(result: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, value in {
        "task2_buildings.json": result["buildings"],
        "task2_building_groups.json": result["building_groups"],
        "task2_entrances.json": result["entrances"],
    }.items():
        (output_dir / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def visualize(result: dict[str, Any], roads: list[dict[str, Any]], output_path: Path, *, road_crs: str, focus: bool = False) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch, Polygon as PlotPolygon

    figure, axis = plt.subplots(figsize=(14, 10), dpi=180)
    for road in _road_parts(roads, road_crs):
        xs, ys = zip(*road["line"])
        axis.plot(xs, ys, color="#94a3b8", linewidth=0.7, zorder=1)
    colors = plt.get_cmap("tab20")
    colors_by_group = {
        group["group_id"]: colors(index % 20)
        for index, group in enumerate(result["building_groups"])
    }
    for index, building in enumerate(result["buildings"]):
        color = colors_by_group.get(building["group_id"], colors(index % 20))
        for ring in project_rings(building["geometry"], "EPSG:4326"):
            axis.add_patch(PlotPolygon(ring, closed=True, facecolor=color, edgecolor="#334155", linewidth=0.35, alpha=0.55, zorder=2))
    for group in result["building_groups"]:
        for ring in project_rings(group["geometry"], "EPSG:4326"):
            axis.add_patch(PlotPolygon(ring, closed=True, fill=False, edgecolor="#111827", linewidth=1.1, zorder=3))
    styles = {"building_group": ("D", "#c026d3"), "community": ("s", "#0369a1")}
    for kind, (marker, color) in styles.items():
        entries = [item for item in result["entrances"] if item["entrance_type"] == kind]
        if entries:
            axis.scatter([item["calculation_x"] for item in entries], [item["calculation_y"] for item in entries], marker=marker, s=22, color=color, edgecolor="#ffffff", linewidth=0.45, zorder=5)
    for group in result["building_groups"]:
        axis.annotate(
            group["group_id"],
            (group["calculation_x"], group["calculation_y"]),
            xytext=(4, 4),
            textcoords="offset points",
            fontsize=6.5,
            color="#111827",
            zorder=6,
        )

    if focus:
        focus_points = []
        for group in result["building_groups"]:
            for ring in project_rings(group["geometry"], "EPSG:4326"):
                focus_points.extend(ring)
        if focus_points:
            min_x = min(point[0] for point in focus_points)
            max_x = max(point[0] for point in focus_points)
            min_y = min(point[1] for point in focus_points)
            max_y = max(point[1] for point in focus_points)
            padding = max(25.0, max(max_x - min_x, max_y - min_y) * 0.12)
            axis.set_xlim(min_x - padding, max_x + padding)
            axis.set_ylim(min_y - padding, max_y + padding)

    axis.legend(
        handles=[
            Line2D([0], [0], color="#94a3b8", linewidth=1.0, label="Cleaned road"),
            Patch(facecolor="#94a3b8", edgecolor="#334155", alpha=0.55, label="IMB-derived building"),
            Line2D([0], [0], color="#111827", linewidth=1.3, label="Recognised building group"),
            Line2D([0], [0], marker="D", color="w", markerfacecolor="#c026d3", markeredgecolor="#ffffff", markersize=6, label="Building-group entrance"),
            Line2D([0], [0], marker="s", color="w", markerfacecolor="#0369a1", markeredgecolor="#ffffff", markersize=6, label="Community entrance"),
        ],
        loc="upper left",
        fontsize=7,
        frameon=True,
        framealpha=0.94,
        edgecolor="#cbd5e1",
    )
    axis.set_aspect("equal", adjustable="box")
    axis.set_title("IMB-derived buildings and recognised building-group entrances" + (" (focus)" if focus else ""))
    axis.set_xlabel("EPSG:32629 easting (m)")
    axis.set_ylabel("EPSG:32629 northing (m)")
    axis.grid(color="#cbd5e1", linewidth=0.3, alpha=0.6)
    figure.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, bbox_inches="tight")
    plt.close(figure)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute entrances from recognised building groups and IMB-partitioned buildings.")
    parser.add_argument("--building-groups", type=Path, required=True)
    parser.add_argument("--building-groups-layer", default="GEO_BUILDING")
    parser.add_argument("--imb-buildings", type=Path, required=True)
    parser.add_argument("--roads", type=Path, required=True)
    parser.add_argument("--roads-layer", default="GEO_ROAD")
    parser.add_argument("--topology-json", type=Path, required=True)
    parser.add_argument("--partition-json", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-fig", type=Path, default=None)
    parser.add_argument("--focus-fig", type=Path, default=None, help="Optional cropped review image around recognised building groups")
    parser.add_argument("--task6-output-dir", type=Path, default=None)
    parser.add_argument("--group-crs", default="EPSG:4326")
    parser.add_argument("--building-crs", default="EPSG:4326")
    parser.add_argument("--road-crs", default="EPSG:4326")
    parser.add_argument("--partition-crs", default="EPSG:4326")
    parser.add_argument("--road-snap-tolerance-m", type=float, default=2.0)
    parser.add_argument("--entrance-dedup-tolerance-m", type=float, default=1.0)
    args = parser.parse_args()
    if min(args.road_snap_tolerance_m, args.entrance_dedup_tolerance_m) < 0:
        parser.error("Entrance tolerances must be non-negative")
    return args


def main() -> None:
    args = parse_args()
    groups = load_features(args.building_groups, args.building_groups_layer)
    cells = load_features(args.imb_buildings, "unused")
    roads = load_features(args.roads, args.roads_layer)
    result = compute_imb_entrance_features(
        groups,
        cells,
        roads,
        _load_json(args.topology_json),
        _load_json(args.partition_json),
        group_crs=args.group_crs,
        building_crs=args.building_crs,
        road_crs=args.road_crs,
        partition_crs=args.partition_crs,
        road_snap_tolerance_m=args.road_snap_tolerance_m,
        entrance_dedup_tolerance_m=args.entrance_dedup_tolerance_m,
    )
    result["meta"] = {
        "source_building_groups": str(args.building_groups),
        "source_imb_buildings": str(args.imb_buildings),
        "source_roads": str(args.roads),
        "source_topology": str(args.topology_json),
        "source_partition": str(args.partition_json),
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.task6_output_dir is not None:
        write_task6_views(result, args.task6_output_dir)
    if args.output_fig is not None:
        visualize(result, roads, args.output_fig, road_crs=args.road_crs)
    if args.focus_fig is not None:
        visualize(result, roads, args.focus_fig, road_crs=args.road_crs, focus=True)
    print(f"Recognised building groups: {result['summary']['recognized_building_group_count']}; IMB buildings: {result['summary']['imb_building_count']}; entrances: {len(result['entrances'])}")
    print(f"Result JSON: {args.output_json.resolve()}")
    if args.output_fig is not None:
        print(f"Review image: {args.output_fig.resolve()}")


if __name__ == "__main__":
    main()
