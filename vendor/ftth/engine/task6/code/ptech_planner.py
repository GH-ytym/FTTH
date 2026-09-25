"""Weak-current PTECH point planning for Task 6 duct routes.

The planner works on the final route polylines so it remains independent from
the route optimizer.  CHAMBRE points are placed at real bends and at evenly
spaced interior positions on each straight run (maximum 200 m spacing). BPE
coordinates receive CHAMBRE and PBO coordinates receive AUTRE.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

METRES_PER_LATITUDE = 110540.0
METRES_PER_DEGREE = 111320.0
EPS = 1e-10


def _point(value: Any) -> tuple[float, float]:
    if isinstance(value, dict):
        return float(value.get("x", value.get("longitude"))), float(value.get("y", value.get("latitude")))
    return float(value[0]), float(value[1])


def _metres(first: tuple[float, float], second: tuple[float, float]) -> float:
    latitude = math.radians((first[1] + second[1]) / 2.0)
    return math.hypot(
        (first[0] - second[0]) * METRES_PER_DEGREE * math.cos(latitude),
        (first[1] - second[1]) * METRES_PER_LATITUDE,
    )


def _same(first: tuple[float, float], second: tuple[float, float], tolerance_m: float = 0.01) -> bool:
    return _metres(first, second) <= tolerance_m


def _direction_cross(
    previous: tuple[float, float], current: tuple[float, float], following: tuple[float, float]
) -> float:
    # Scale longitude to metres before testing collinearity.
    latitude = math.radians((previous[1] + current[1] + following[1]) / 3.0)
    scale_x = METRES_PER_DEGREE * math.cos(latitude)
    ax = (current[0] - previous[0]) * scale_x
    ay = (current[1] - previous[1]) * METRES_PER_LATITUDE
    bx = (following[0] - current[0]) * scale_x
    by = (following[1] - current[1]) * METRES_PER_LATITUDE
    return ax * by - ay * bx


def _clean_coordinates(route: dict[str, Any]) -> list[tuple[float, float]]:
    coordinates = [_point(item) for item in route.get("coordinates") or []]
    cleaned: list[tuple[float, float]] = []
    for point in coordinates:
        if not cleaned or not _same(cleaned[-1], point):
            cleaned.append(point)
    return cleaned


def _subtype_record(
    point: tuple[float, float],
    subtype: str,
    sequence: int,
    *,
    source_route: dict[str, Any] | None = None,
    source: str = "task6_ptech_planner",
    bpe_id: str | None = None,
) -> dict[str, Any]:
    route_id = str((source_route or {}).get("id") or "")
    target = str((source_route or {}).get("to_code") or "")
    code = f"PTECH-{sequence:05d}"
    return {
        "id": code,
        "code": code,
        "type": "PTECH",
        "ptech_type": "PTECH",
        "entity_type": "PTECH",
        # Task 3's PTECH schema stores the engineering subtype in TYPE. Keep
        # lower-case aliases as well for JSON/GIS consumers that use an
        # explicit ``subtype`` field.
        "TYPE": subtype,
        "subtype": subtype,
        "sub_type": subtype,
        "ptech_subtype": subtype,
        "point_type": subtype,
        "nature": "CREATION",
        "NATURE": "CREATION",
        "NOM": f"Task6 {subtype} {code}",
        "STATUT": "PROJET",
        "x": round(point[0], 9),
        "y": round(point[1], 9),
        "source": source,
        "source_route_id": route_id or None,
        "target_code": target or None,
        "bpe_id": bpe_id,
    }


def plan_ptech(
    routes: Iterable[dict[str, Any]],
    bpes: Iterable[dict[str, Any]],
    pbos: Iterable[dict[str, Any]] | None = None,
    *,
    max_spacing_m: float = 200.0,
    route_types: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return planned PTECH records and auditable spacing statistics.

    Only interior points of straight runs are generated for spacing. A bend is
    identified by a non-collinear direction change; graph split points on a
    straight road therefore do not create redundant chambers.
    """
    if max_spacing_m <= 0:
        raise ValueError("max_spacing_m must be positive")
    selected_types = route_types or {
        "NRO_TO_PM_MAIN",
        "PM_TO_GROUP_ENTRY",
        "GROUP_ENTRY_TO_BPE",
        "BPE_TO_BPE",
        "BPE_TO_PBO",
    }
    bpe_records = list(bpes)
    pbo_records = list(pbos or [])
    records: list[dict[str, Any]] = []
    # Keep subtype-specific colocated records distinct, while avoiding duplicate
    # chambers produced by shared route endpoints or overlapping route pieces.
    seen: set[tuple[str, int, int]] = set()
    sequence = 1
    stats: dict[str, Any] = {
        "max_spacing_m": float(max_spacing_m),
        "route_types": sorted(selected_types),
        "routes_processed": 0,
        "bend_chambres": 0,
        "interval_chambres": 0,
        "bpe_chambres": 0,
        "pbo_autre": 0,
        "max_interval_m": 0.0,
    }

    def add(point: tuple[float, float], subtype: str, route: dict[str, Any] | None = None, *, bpe_id: str | None = None, source: str = "task6_ptech_planner") -> bool:
        nonlocal sequence
        key = (subtype, round(point[0] * 1e9), round(point[1] * 1e9))
        if key in seen:
            # A BPE may coincide with a bend or an interval point. Preserve
            # the single physical point while enriching it with the BPE link.
            if bpe_id:
                for existing in records:
                    if existing.get("subtype") == subtype and _same((existing["x"], existing["y"]), point):
                        existing["bpe_id"] = bpe_id
                        existing["source"] = source
                        break
            return False
        seen.add(key)
        records.append(_subtype_record(point, subtype, sequence, source_route=route, source=source, bpe_id=bpe_id))
        sequence += 1
        return True

    for route in routes:
        if str(route.get("route_type") or "") not in selected_types:
            continue
        points = _clean_coordinates(route)
        if len(points) < 2:
            continue
        stats["routes_processed"] += 1
        # Collapse collinear graph splits into straight runs while retaining all
        # genuine bend vertices.
        runs: list[list[tuple[float, float]]] = [[points[0]]]
        for index in range(1, len(points) - 1):
            previous, current, following = points[index - 1 : index + 2]
            if abs(_direction_cross(previous, current, following)) > 0.01:
                runs[-1].append(current)
                runs.append([current])
        runs[-1].append(points[-1])
        for run_index, run in enumerate(runs):
            if run_index > 0:
                bend = run[0]
                if add(bend, "CHAMBRE", route):
                    stats["bend_chambres"] += 1
            length = sum(_metres(first, second) for first, second in zip(run, run[1:]))
            intervals = max(1, math.ceil(length / max_spacing_m))
            if intervals > 1:
                travelled = 0.0
                targets = [length * index / intervals for index in range(1, intervals)]
                target_index = 0
                for first, second in zip(run, run[1:]):
                    segment_length = _metres(first, second)
                    while target_index < len(targets) and targets[target_index] < travelled + segment_length - EPS:
                        ratio = (targets[target_index] - travelled) / segment_length if segment_length else 0.0
                        point = (first[0] + ratio * (second[0] - first[0]), first[1] + ratio * (second[1] - first[1]))
                        if add(point, "CHAMBRE", route):
                            stats["interval_chambres"] += 1
                        target_index += 1
                    travelled += segment_length
            stats["max_interval_m"] = max(stats["max_interval_m"], length / intervals if intervals else 0.0)

    for bpe in bpe_records:
        try:
            point = _point(bpe)
        except (TypeError, ValueError):
            continue
        bpe_id = str(bpe.get("id") or bpe.get("code") or bpe.get("CODE") or "") or None
        add(point, "CHAMBRE", bpe_id=bpe_id, source="bpe_colocated_chambre")
        if bpe_id:
            for existing in reversed(records):
                if existing.get("subtype") == "CHAMBRE" and _same((existing["x"], existing["y"]), point):
                    existing["target_type"] = "BPE"
                    existing["target_code"] = bpe_id
                    break
        stats["bpe_chambres"] += 1

    for pbo in pbo_records:
        try:
            point = _point(pbo)
        except (TypeError, ValueError):
            continue
        pbo_id = str(pbo.get("id") or pbo.get("code") or pbo.get("CODE") or "") or None
        add(point, "AUTRE", source="pbo_colocated_autre")
        # Keep the PBO association explicit for GIS consumers.
        if pbo_id:
            for existing in reversed(records):
                if existing.get("subtype") == "AUTRE" and _same((existing["x"], existing["y"]), point):
                    existing["pbo_id"] = pbo_id
                    existing["target_type"] = "PBO"
                    existing["target_code"] = pbo_id
                    break
        stats["pbo_autre"] += 1

    stats["total"] = len(records)
    stats["spacing_valid"] = stats["max_interval_m"] <= max_spacing_m + 1e-6
    stats["bpe_chambre_count"] = len(bpe_records)
    stats["pbo_autre_count"] = len(pbo_records)
    stats["bpe_colocation_valid"] = stats["bpe_chambres"] == len(bpe_records)
    stats["pbo_colocation_valid"] = stats["pbo_autre"] == len(pbo_records)
    stats["subtype_counts"] = {
        "CHAMBRE": sum(item.get("subtype") == "CHAMBRE" for item in records),
        "AUTRE": sum(item.get("subtype") == "AUTRE" for item in records),
    }
    return records, stats
