"""Shared GeoJSON constraint helpers for task 5 and task 6.

The routing modules only need a small, dependency-free geometry contract:
forbidden polygons block a segment and cost polygons multiply its estimated
construction cost. Keeping this implementation at the FTTH package root
allows both task modules to import the same behavior from source checkouts.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


EPS = 1e-12


@dataclass(frozen=True)
class ConstraintZone:
    code: str
    constraint_type: str
    cost_factor: float
    polygons: tuple[tuple[tuple[float, float], ...], ...]

    def __getitem__(self, key: str) -> Any:
        """Support the mapping-style access used by the task-5 legacy code."""
        return getattr(self, key)


def _point(value: Any) -> tuple[float, float]:
    if isinstance(value, dict):
        return float(value.get("x", value.get("longitude"))), float(value.get("y", value.get("latitude")))
    return float(value[0]), float(value[1])


def _polygons(geometry: dict[str, Any] | None) -> tuple[tuple[tuple[float, float], ...], ...]:
    geometry = geometry or {}
    kind = str(geometry.get("type") or "")
    coordinates = geometry.get("coordinates") or []
    if kind == "Polygon":
        rings = coordinates[:1]
    elif kind == "MultiPolygon":
        rings = [polygon[0] for polygon in coordinates if polygon]
    else:
        rings = []
    return tuple(tuple(_point(point) for point in ring) for ring in rings if len(ring) >= 3)


def load_constraints(path: str | Path) -> list[ConstraintZone]:
    """Read a GeoJSON FeatureCollection into the routing constraint contract."""
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        raise ValueError("Constraint source must be a GeoJSON FeatureCollection")
    zones: list[ConstraintZone] = []
    for index, feature in enumerate(features, 1):
        properties = dict(feature.get("properties") or {})
        code = str(properties.get("code") or properties.get("CODE") or feature.get("id") or f"CONSTRAINT_{index:04d}")
        constraint_type = str(properties.get("constraint_type") or properties.get("CONSTRAINT_TYPE") or "forbidden").lower()
        if constraint_type not in {"forbidden", "cost"}:
            raise ValueError(f"Unsupported constraint_type for {code}: {constraint_type}")
        factor = float(properties.get("cost_factor") or properties.get("COST_FACTOR") or 1.0)
        if factor < 1.0:
            raise ValueError(f"cost_factor must be >= 1 for {code}")
        polygons = _polygons(feature.get("geometry"))
        if not polygons:
            continue
        zones.append(ConstraintZone(code, constraint_type, factor, polygons))
    return zones


def _orientation(first: tuple[float, float], second: tuple[float, float], third: tuple[float, float]) -> float:
    return (second[0] - first[0]) * (third[1] - first[1]) - (second[1] - first[1]) * (third[0] - first[0])


def _on_segment(first: tuple[float, float], second: tuple[float, float], point: tuple[float, float]) -> bool:
    return (
        min(first[0], second[0]) - EPS <= point[0] <= max(first[0], second[0]) + EPS
        and min(first[1], second[1]) - EPS <= point[1] <= max(first[1], second[1]) + EPS
    )


def _inside(point: tuple[float, float], ring: tuple[tuple[float, float], ...]) -> bool:
    x, y = point
    inside = False
    for first, second in zip(ring, (*ring[1:], ring[0])):
        if (first[1] > y) != (second[1] > y):
            cross_x = (second[0] - first[0]) * (y - first[1]) / (second[1] - first[1]) + first[0]
            if x <= cross_x:
                inside = not inside
    return inside


def _segment_intersects_ring(first: tuple[float, float], second: tuple[float, float], ring: tuple[tuple[float, float], ...]) -> bool:
    for edge_first, edge_second in zip(ring, (*ring[1:], ring[0])):
        a = _orientation(first, second, edge_first)
        b = _orientation(first, second, edge_second)
        c = _orientation(edge_first, edge_second, first)
        d = _orientation(edge_first, edge_second, second)
        if ((a > EPS and b < -EPS) or (a < -EPS and b > EPS)) and ((c > EPS and d < -EPS) or (c < -EPS and d > EPS)):
            return True
        if abs(a) <= EPS and _on_segment(first, second, edge_first):
            return True
        if abs(b) <= EPS and _on_segment(first, second, edge_second):
            return True
        if abs(c) <= EPS and _on_segment(edge_first, edge_second, first):
            return True
        if abs(d) <= EPS and _on_segment(edge_first, edge_second, second):
            return True
    return _inside(first, ring) or _inside(second, ring)


def _segment_ratio_inside(first: tuple[float, float], second: tuple[float, float], ring: tuple[tuple[float, float], ...], samples: int = 32) -> float:
    if not _segment_intersects_ring(first, second, ring):
        return 0.0
    inside_count = 0
    for index in range(samples):
        ratio = (index + 0.5) / samples
        point = (first[0] + (second[0] - first[0]) * ratio, first[1] + (second[1] - first[1]) * ratio)
        if _inside(point, ring):
            inside_count += 1
    return inside_count / samples


def assess_segment(first: tuple[float, float], second: tuple[float, float], zones: Iterable[ConstraintZone]) -> dict[str, Any]:
    """Assess one segment against forbidden and cost zones."""
    blocked_by: list[str] = []
    cost_zones: list[ConstraintZone] = []
    multiplier = 1.0
    for zone in zones:
        ratio = max((_segment_ratio_inside(first, second, ring) for ring in zone.polygons), default=0.0)
        if ratio <= 0.0:
            continue
        if zone.constraint_type == "forbidden":
            blocked_by.append(zone.code)
        else:
            cost_zones.append(zone)
            multiplier *= 1.0 + (zone.cost_factor - 1.0) * ratio
    return {
        "blocked": bool(blocked_by),
        "blocked_by": sorted(set(blocked_by)),
        "cost_zones": cost_zones,
        "cost_multiplier": multiplier,
    }
