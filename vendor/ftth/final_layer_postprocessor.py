"""Plugin-owned corrections and audits for the frozen FTTH final-layer builder.

The supplied Task 2-6 sources stay byte-identical.  This adapter replaces the
builder's rectangular display placeholders with the actual Task-2 service-area
geometry and performs a graph-level cable connectivity audit before a run is
made available to QGIS or the delivery exporter.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

try:
    from .engine.delivery_validator import _read_dbf, _read_shp
except ImportError:  # pragma: no cover - direct worker import fallback
    from engine.delivery_validator import _read_dbf, _read_shp


def _number(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"区域坐标不是有限数值：{value!r}")
    return result


def _closed_clockwise_ring(points: Any, polygon_area) -> list[tuple[float, float]]:
    ring: list[tuple[float, float]] = []
    for value in points or []:
        if isinstance(value, dict):
            point = (_number(value.get("x", value.get("X"))), _number(value.get("y", value.get("Y"))))
        else:
            point = (_number(value[0]), _number(value[1]))
        if not ring or point != ring[-1]:
            ring.append(point)
    if len(set(ring)) < 3:
        raise ValueError("任务二区域边界至少需要3个不同坐标点")
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    if polygon_area(ring[:-1]) > 0:
        ring.reverse()
    return ring


def _region_rings(region: dict[str, Any], module) -> tuple[list[list[tuple[float, float]]], str]:
    geometry = region.get("geometry") or {}
    for key in ("smoothed_polygon", "polygon"):
        if geometry.get(key):
            return [_closed_clockwise_ring(geometry[key], module.polygon_area)], f"task2.geometry.{key}"
    cells = [item for item in geometry.get("polygons") or [] if len(item.get("polygon") or []) >= 3]
    if cells:
        rings = module.cell_union_boundary(cells)
        if not rings:
            raise ValueError(f"任务二区域 {region.get('id')} 的单元面无法合并")
        return rings, "task2.geometry.polygons_union"
    raise ValueError(f"任务二区域 {region.get('id')} 没有可输出的真实边界")


def _layer(output_dir: Path, name: str) -> tuple[list[dict[str, Any]], list[Any]]:
    _fields, rows = _read_dbf(output_dir / f"{name}.dbf")
    _kind, geometries = _read_shp(output_dir / f"{name}.shp")
    return rows, geometries


def _metres(first: tuple[float, float], second: tuple[float, float]) -> float:
    latitude = math.radians((first[1] + second[1]) / 2.0)
    return math.hypot(
        (first[0] - second[0]) * 111320.0 * math.cos(latitude),
        (first[1] - second[1]) * 110540.0,
    )


def _cable_topology(output_dir: Path, tolerance_m: float = 0.20) -> dict[str, Any]:
    site_rows, site_geometries = _layer(output_dir, "SITE")
    box_rows, box_geometries = _layer(output_dir, "BOITE")
    imb_rows, imb_geometries = _layer(output_dir, "IMB")
    cable_rows, cable_geometries = _layer(output_dir, "CABLE")
    points = {
        str(row.get("CODE") or ""): tuple(geometry)
        for rows, geometries in (
            (site_rows, site_geometries),
            (box_rows, box_geometries),
            (imb_rows, imb_geometries),
        )
        for row, geometry in zip(rows, geometries)
        if row.get("CODE") and isinstance(geometry, tuple)
    }
    roots = {
        str(row.get("CODE") or "")
        for row in site_rows
        if str(row.get("TYPE") or "").upper() == "NRO"
    }
    graph: dict[str, set[str]] = defaultdict(set)
    unknown_endpoints: list[dict[str, str]] = []
    coordinate_gaps: list[dict[str, Any]] = []
    invalid_line_parts: list[str] = []
    edges: list[tuple[str, str, str]] = []
    for row, geometry in zip(cable_rows, cable_geometries):
        code = str(row.get("CODE") or "")
        origin = str(row.get("ORIGINE") or "")
        endpoint = str(row.get("EXTREMITE") or "")
        edges.append((code, origin, endpoint))
        graph[origin].add(endpoint)
        graph[endpoint].add(origin)
        if len(geometry or []) != 1 or len((geometry or [[]])[0]) < 2:
            invalid_line_parts.append(code)
            continue
        line = geometry[0]
        for side, device, line_point in (("start", origin, line[0]), ("end", endpoint, line[-1])):
            if device not in points:
                unknown_endpoints.append({"cable": code, "side": side, "device": device})
                continue
            distance = _metres(tuple(line_point), points[device])
            if distance > tolerance_m:
                coordinate_gaps.append({
                    "cable": code,
                    "side": side,
                    "device": device,
                    "distance_m": round(distance, 3),
                })
    reachable: set[str] = set()
    queue = deque(code for code in roots if code in graph)
    while queue:
        code = queue.popleft()
        if code in reachable:
            continue
        reachable.add(code)
        queue.extend(graph[code] - reachable)
    unreachable_cables = sorted(
        code for code, origin, endpoint in edges if origin not in reachable or endpoint not in reachable
    )
    blockers = unknown_endpoints or coordinate_gaps or invalid_line_parts or unreachable_cables or not roots
    return {
        "status": "FAIL" if blockers else "PASS",
        "tolerance_m": tolerance_m,
        "cable_count": len(cable_rows),
        "nro_roots": sorted(roots),
        "reachable_node_count": len(reachable),
        "unknown_endpoints": unknown_endpoints,
        "coordinate_gaps": coordinate_gaps,
        "invalid_or_multipart_cables": sorted(invalid_line_parts),
        "unreachable_cables": unreachable_cables,
    }


def apply_final_layer_corrections(
    module,
    output_dir: str | Path,
    task2_regions_path: str | Path,
    task5_plan_path: str | Path,
    report: dict[str, Any],
) -> dict[str, Any]:
    """Restore algorithm outputs, replace placeholders, then refresh audits."""
    output = Path(output_dir).resolve()
    regions = module.load_json(Path(task2_regions_path).resolve())
    task5 = module.load_json(Path(task5_plan_path).resolve())
    module.OUTPUT_DIR = output
    transfer: dict[str, Any] = {"source": "task2_region_partition", "layers": {}}
    zpm_rings: dict[str, list[list[tuple[float, float]]]] = {}
    for layer_name, region_key in (("ZNRO", "znro_regions"), ("ZPM", "zpm_regions")):
        rows, _old_geometries = _layer(output, layer_name)
        by_code = {str(item.get("id") or ""): item for item in regions.get(region_key) or []}
        geometries: list[list[list[tuple[float, float]]]] = []
        methods: dict[str, str] = {}
        vertices: dict[str, int] = {}
        for row in rows:
            code = str(row.get("CODE") or "")
            if code not in by_code:
                raise ValueError(f"最终 {layer_name} {code} 在任务二区域结果中不存在")
            rings, method = _region_rings(by_code[code], module)
            geometries.append(rings)
            methods[code] = method
            vertices[code] = sum(len(ring) for ring in rings)
            if layer_name == "ZPM":
                zpm_rings[code] = rings
        if set(by_code) != {str(row.get("CODE") or "") for row in rows}:
            raise ValueError(f"任务二与最终 {layer_name} 区域代码不一致")
        module.write_layer(layer_name, 5, rows, geometries)
        transfer["layers"][layer_name] = {
            "feature_count": len(rows),
            "geometry_methods": methods,
            "vertex_counts": vertices,
        }

    site_rows, site_geometries = _layer(output, "SITE")
    box_rows, box_geometries = _layer(output, "BOITE")
    cable_rows, cable_geometries = _layer(output, "CABLE")
    source_links = [link for link in task5.get("links") or [] if len(link.get("coordinates") or []) >= 2]
    if len(source_links) != len(cable_rows):
        raise ValueError(
            f"任务五有效光缆与最终CABLE数量不一致：任务五={len(source_links)}，最终={len(cable_rows)}"
        )
    sizing_changes = []
    allowed_capacities = {24, 48, 72, 144}
    for row, link in zip(cable_rows, source_links):
        stage = str(link.get("stage") or "")
        if str(row.get("COMMENT") or "") != stage:
            raise ValueError(f"任务五与最终CABLE顺序不一致：{row.get('CODE')} / {link.get('id')}")
        capacity = int(link.get("cable_cores") or 0)
        if capacity not in allowed_capacities:
            raise ValueError(f"任务五返回非标准光缆容量：{link.get('id')}={capacity}")
        previous = int(row.get("CAPACITE") or 0)
        if previous != capacity:
            sizing_changes.append({
                "cable": row.get("CODE"),
                "task5_link": link.get("id"),
                "adapter_value_removed": previous,
                "task5_capacity_restored": capacity,
            })
        used = min(capacity, int(link.get("downstream_ports") or 0))
        row["CAPACITE"] = capacity
        row["REF_PRODUI"] = str(link.get("cable_spec") or f"FIBER_{capacity}_CORE")
        row["NB_FIBRE_U"] = used
        row["NB_FIBRE_D"] = capacity - used
    module.write_layer("CABLE", 3, cable_rows, cable_geometries)
    report["cable_sizing_transfer"] = {
        "source": "task5.fiber_link_plan.links",
        "allowed_standard_capacities": sorted(allowed_capacities),
        "cable_count": len(cable_rows),
        "restored_adapter_overrides": sizing_changes,
        "nonstandard_capacity_count": 0,
    }
    pm_points = {
        str(row.get("CODE") or ""): tuple(point)
        for row, point in zip(site_rows, site_geometries)
        if str(row.get("TYPE") or "").upper() == "PM"
    }
    pm_outside = sorted(
        code for code, point in pm_points.items()
        if code not in zpm_rings or not any(module.point_in_ring(point, ring) for ring in zpm_rings[code])
    )
    pbo_outside = sorted(
        str(row.get("CODE") or "")
        for row, point in zip(box_rows, box_geometries)
        if str(row.get("TYPE") or "").upper() == "PBO"
        and (
            str(row.get("REF_PM") or "") not in zpm_rings
            or not any(module.point_in_ring(tuple(point), ring) for ring in zpm_rings[str(row.get("REF_PM") or "")])
        )
    )
    cable_vertex_violations = []
    for row, parts in zip(cable_rows, cable_geometries):
        if str(row.get("TYPE_CABLE") or "").upper() != "DISTRIBUTION":
            continue
        ref_pm = str(row.get("REF_PM") or "")
        rings = zpm_rings.get(ref_pm) or []
        outside = sum(
            not any(module.point_in_ring(tuple(point), ring) for ring in rings)
            for part in parts or [] for point in part
        )
        if outside:
            cable_vertex_violations.append({"cable": row.get("CODE"), "outside_vertex_count": outside})

    topology = _cable_topology(output)
    geometry = report.setdefault("geometry_and_capacity", {})
    task5_shortfalls = [
        str(link.get("id") or "")
        for link in source_links if bool(link.get("cable_capacity_shortfall"))
    ]
    geometry.update({
        "region_geometry_source": "task2_region_partition",
        "pm_outside_zpm": pm_outside,
        "pbo_outside_zpm": pbo_outside,
        "cable_endpoint_mismatches": sorted({item["cable"] for item in topology["coordinate_gaps"]}),
        "distribution_cable_vertex_violations": cable_vertex_violations,
        "pm_capacity_violations": task5_shortfalls,
        "capacity_validation_basis": "task5_per_link_downstream_ports_or_device_capacity",
    })
    warnings = (
        pm_outside
        or pbo_outside
        or topology["coordinate_gaps"]
        or cable_vertex_violations
        or task5_shortfalls
    )
    geometry["status"] = "PASS_WITH_WARNINGS" if warnings else "PASS"
    report["region_geometry_transfer"] = transfer
    report["cable_topology"] = topology
    report.setdefault("cross_layer_references", {})["cable_network_connected_to_nro"] = topology["status"] == "PASS"
    report["serialized_layers"] = module.validate_serialized_layers()
    serialized_ok = all(item.get("status") == "PASS" for item in report["serialized_layers"].values())
    if topology["status"] != "PASS" or not serialized_ok:
        report["status"] = "FAIL"
    return report
