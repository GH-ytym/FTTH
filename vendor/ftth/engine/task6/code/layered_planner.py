"""Layered task-6 network planner.

This module is deliberately independent from the legacy task-6 planner.  It
implements the new contract agreed for remote-sensing inputs:

* task-2 PM/IMB assignments and task-4 service relationships are fixed;
* PM-to-building-group-entry segments are road-only; an NRO or PM may attach
  to a usable road within its configured access radius and reach that entry by
  a separate direct 144-core fibre;
* a community entry is mandatory for a main route entering a community;
* a building-group entry is mandatory before an indoor route;
* each Task-4 PBO location is used as the offset building access point for its IMB;
* BPEs may share a rooted conduit tree: only reachability from the owning PM
  is required, so adjacent BPEs may be connected directly;
* a BPE-to-PBO route must use a building entrance;
* water polygons add cost, while building polygons block main routes.

The optimizer uses K legal candidates per demand. Main trunk candidates are
selected jointly as a shared rooted tree: exact candidate enumeration is used
when the search space is bounded, with a deterministic beam fallback for large
PM counts. This keeps the contract dependency-free while optimizing the actual
unique trunk length rather than making a demand-order decision.
"""

from __future__ import annotations

import heapq
import itertools
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

try:
    from ptech_planner import plan_ptech
except ImportError:  # package import
    from .ptech_planner import plan_ptech


METRES_PER_LATITUDE = 110540.0
METRES_PER_DEGREE = 111320.0
DEFAULT_K_PATHS = 5
DEFAULT_EDGE_CAPACITY = 2048
DEFAULT_COST_PER_METRE = 1.0
ROAD_NODE_SNAP_M = 1.0
DEFAULT_NRO_ROAD_ACCESS_RADIUS_M = 50.0
DEFAULT_PM_ROAD_ACCESS_RADIUS_M = 10.0
PBO_IMB_ASSOCIATION_MAX_DISTANCE_M = 10.0
EPS = 1e-12


def _capacity(value: Any, default: int | None = DEFAULT_EDGE_CAPACITY) -> int | None:
    """Normalize finite and explicitly unlimited duct capacities."""
    if value is None or value == "":
        return default
    if isinstance(value, str) and value.strip().lower() in {"unlimited", "infinite", "inf", "none", "no_limit", "no-limit"}:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return None if number < 0 else number


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"false", "0", "no", "n", "closed", "否"}


def _first_non_empty(record: dict[str, Any], *names: str, default: Any = None) -> Any:
    """Return the first explicitly populated field from a record."""
    for name in names:
        value = record.get(name)
        if value not in (None, ""):
            return value
    return default


def _road_open_cut_allowed(road: dict[str, Any], default: bool = True) -> bool:
    return _as_bool(_first_non_empty(road, "open_cut_allowed", "OPEN_CUT_ALLOWED"), default)


def _road_capacity(road: dict[str, Any], default: int | None) -> int | None:
    return _capacity(_first_non_empty(road, "capacity", "DUCT_CAPACITY"), default)


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


def _inside(point: tuple[float, float], ring: list[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    for first, second in zip(ring, [*ring[1:], ring[0]]):
        if (first[1] > y) != (second[1] > y):
            cross_x = (second[0] - first[0]) * (y - first[1]) / (second[1] - first[1]) + first[0]
            if x <= cross_x:
                inside = not inside
    return inside


def _segment_intersects_ring(first: tuple[float, float], second: tuple[float, float], ring: list[tuple[float, float]]) -> bool:
    def orientation(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def on_segment(a: tuple[float, float], b: tuple[float, float], p: tuple[float, float]) -> bool:
        return min(a[0], b[0]) - EPS <= p[0] <= max(a[0], b[0]) + EPS and min(a[1], b[1]) - EPS <= p[1] <= max(a[1], b[1]) + EPS

    for edge_a, edge_b in zip(ring, [*ring[1:], ring[0]]):
        a = orientation(first, second, edge_a)
        b = orientation(first, second, edge_b)
        c = orientation(edge_a, edge_b, first)
        d = orientation(edge_a, edge_b, second)
        if ((a > 0 > b) or (a < 0 < b)) and ((c > 0 > d) or (c < 0 < d)):
            return True
        if abs(a) < EPS and on_segment(first, second, edge_a):
            return True
        if abs(b) < EPS and on_segment(first, second, edge_b):
            return True
        if abs(c) < EPS and on_segment(edge_a, edge_b, first):
            return True
        if abs(d) < EPS and on_segment(edge_a, edge_b, second):
            return True
    return _inside(first, ring) or _inside(second, ring)


def _segment_intersection(
    first: tuple[float, float], second: tuple[float, float], third: tuple[float, float], fourth: tuple[float, float]
) -> tuple[float, float, tuple[float, float]] | None:
    direction_a = (second[0] - first[0], second[1] - first[1])
    direction_b = (fourth[0] - third[0], fourth[1] - third[1])
    denominator = direction_a[0] * direction_b[1] - direction_a[1] * direction_b[0]
    if abs(denominator) < EPS:
        return None
    delta = (third[0] - first[0], third[1] - first[1])
    ratio_a = (delta[0] * direction_b[1] - delta[1] * direction_b[0]) / denominator
    ratio_b = (delta[0] * direction_a[1] - delta[1] * direction_a[0]) / denominator
    if -EPS <= ratio_a <= 1.0 + EPS and -EPS <= ratio_b <= 1.0 + EPS:
        return ratio_a, ratio_b, (first[0] + ratio_a * direction_a[0], first[1] + ratio_a * direction_a[1])
    return None


def _project_to_segment(point: tuple[float, float], first: tuple[float, float], second: tuple[float, float]) -> tuple[float, tuple[float, float], float]:
    dx = second[0] - first[0]
    dy = second[1] - first[1]
    denominator = dx * dx + dy * dy
    ratio = 0.0 if denominator < EPS else ((point[0] - first[0]) * dx + (point[1] - first[1]) * dy) / denominator
    ratio = max(0.0, min(1.0, ratio))
    projected = (first[0] + ratio * dx, first[1] + ratio * dy)
    return ratio, projected, _metres(point, projected)


def _geometry_rings(value: dict[str, Any]) -> list[list[tuple[float, float]]]:
    geometry = value.get("geometry") if isinstance(value.get("geometry"), dict) else value
    geometry = geometry or {}
    kind = str(geometry.get("type") or "")
    coordinates = geometry.get("coordinates") or []
    if kind == "Polygon":
        return [[_point(point) for point in ring] for ring in coordinates[:1] if len(ring) >= 3]
    if kind == "MultiPolygon":
        return [[_point(point) for point in polygon[0]] for polygon in coordinates if polygon and len(polygon[0]) >= 3]
    return []


def _load_source(value: Any, base_dir: Path) -> list[dict[str, Any]]:
    """Load inline records or GeoJSON features; paths remain configurable."""
    if value is None:
        return []
    if isinstance(value, list):
        return [dict(item) for item in value]
    if isinstance(value, dict) and "file" not in value and "path" not in value:
        return [dict(value)]
    source = value.get("file") or value.get("path") if isinstance(value, dict) else value
    path = Path(str(source))
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(raw, list):
        return [dict(item) for item in raw]
    if raw.get("type") == "FeatureCollection":
        records = []
        for feature in raw.get("features") or []:
            record = dict(feature.get("properties") or {})
            record["geometry"] = feature.get("geometry")
            records.append(record)
        return records
    return [dict(raw)]


def _load_object(value: Any, base_dir: Path) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict) and "file" in value or isinstance(value, dict) and "path" in value:
        source = value.get("file") or value.get("path")
        path = Path(str(source))
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        return json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(value, dict):
        return dict(value)
    raise ValueError("A structured partition source must be an object or a JSON path object")


def _id(record: dict[str, Any], *names: str) -> str:
    for name in names:
        if record.get(name) not in (None, ""):
            return str(record[name])
    raise ValueError(f"Record is missing an id field: {names}")


def _coords(record: dict[str, Any]) -> tuple[float, float]:
    if record.get("x") is not None or record.get("longitude") is not None:
        return _point(record)
    geometry = record.get("geometry") or {}
    if geometry.get("type") == "Point":
        return _point(geometry.get("coordinates"))
    raise ValueError(f"Point coordinates are required for record {_id(record, 'id', 'code', 'CODE')}")


def _edge_key(first: str, second: str, edge_id: str) -> str:
    return edge_id or f"{first}->{second}"


class LayeredPlanner:
    """Plan fixed PM/service assignments over remote-sensing geometry."""

    def __init__(self, data: dict[str, Any], base_dir: Path | None = None) -> None:
        self.data = data
        self.base_dir = base_dir or Path.cwd()
        self.options = data.get("planning") or {}
        self.k_paths = max(1, int(self.options.get("candidate_paths", DEFAULT_K_PATHS)))
        self.default_capacity = _capacity(self.options.get("default_edge_capacity", DEFAULT_EDGE_CAPACITY))
        self.default_cost = float(self.options.get("road_cost_per_m", DEFAULT_COST_PER_METRE))
        remote = data.get("remote_sensing") or data.get("layered_inputs") or {}
        self.nros = self._records(data, remote, "nro_sites", "nros")
        self.pms = self._records(data, remote, "pm_sites", "pms")
        self.task2_partition = _load_object(data.get("task2_partition"), self.base_dir)
        self.groups = self._records(data, remote, "building_groups", "groups")
        self.buildings = self._records(data, remote, "buildings", "building_footprints")
        self.entrances = self._records(data, remote, "entrances", "building_entrances")
        self.roads = self._records(data, remote, "roads", "road_network")
        self.water_zones = self._records(data, remote, "water_cost_zones", "water")
        self.bpes = list(data.get("bpe_plan") or data.get("bpes") or [])
        self.pbos = list(data.get("pbo_plan") or data.get("pbos") or [])
        self.imb_points = list(data.get("imb_points") or data.get("imbs") or [])
        # Task 4 places each PBO beside its served IMB. Treat that fixed PBO
        # position as the building access point for Task 6 internal routes.
        # The original entrance layer is retained only for buildings without
        # a corresponding PBO, so older inputs remain usable.
        self._pbo_building_entrances: dict[str, dict[str, Any]] = {}
        self._derive_pbo_building_entrances()
        self._apply_fixed_task2_partition()
        self._validate_required_data()
        self.graph: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.nodes: dict[str, tuple[float, float]] = {}
        self.edge_costs: dict[str, float] = {}
        self.edge_lengths: dict[str, float] = {}
        self.edge_capacities: dict[str, int | None] = {}
        # Equipment sites are not mandatory road endpoints. Each site may use
        # a nearby road point as its duct entry, with the short span carried as
        # a direct optical-fibre connection.
        self.nro_road_entries: dict[str, dict[str, Any]] = {}
        self.pm_road_entries: dict[str, dict[str, Any]] = {}
        self._build_road_graph()

    def _records(self, data: dict[str, Any], remote: dict[str, Any], *names: str) -> list[dict[str, Any]]:
        for name in names:
            if name in remote:
                return _load_source(remote[name], self.base_dir)
            if name in data:
                return _load_source(data[name], self.base_dir)
        return []

    def _validate_required_data(self) -> None:
        missing = [name for name, values in (("nro_sites", self.nros), ("pm_sites", self.pms), ("roads", self.roads)) if not values]
        if missing:
            raise ValueError("Layered task-6 input is missing: " + ", ".join(missing))
        pm_ids = {_id(item, "id", "code", "CODE") for item in self.pms}
        for pm in self.pms:
            assigned = pm.get("assigned_imb_ids") or pm.get("imb_ids") or []
            if not assigned and pm.get("zpm_id"):
                assigned = [str(item.get("id") or item.get("code")) for item in self.imb_points if str(item.get("zpm_id")) == str(pm["zpm_id"])]
            if pm.get("assigned_imb_ids") is None:
                pm["assigned_imb_ids"] = assigned
            if pm.get("id") not in pm_ids:
                raise ValueError("PM records must have stable ids")

    def _apply_fixed_task2_partition(self) -> None:
        """Attach task-2 IMB memberships to the fixed remote-sensing PM sites."""
        if not self.task2_partition or not self.pms:
            return
        zpm = self.task2_partition.get("zpm") or self.task2_partition.get("ZPM") or {}
        clusters = zpm.get("clusters") or []
        members_by_zpm: dict[str, list[str]] = {}
        capacity_by_zpm: dict[str, int] = {}
        for cluster in clusters:
            cluster_id = str(cluster.get("id") or cluster.get("code") or "")
            members_by_zpm[cluster_id] = [str(member.get("id") or member.get("code")) for member in cluster.get("members") or []]
            if cluster.get("capacity_limit") is not None:
                capacity_by_zpm[cluster_id] = int(cluster["capacity_limit"])
        assignment = zpm.get("assignment") or {}
        if isinstance(assignment, dict):
            for imb_id, zpm_id in assignment.items():
                members_by_zpm.setdefault(str(zpm_id), []).append(str(imb_id))
        for pm in self.pms:
            pm_id = str(pm.get("id") or pm.get("code") or pm.get("CODE") or "")
            zpm_id = str(pm.get("zpm_id") or pm.get("ZPM_CODE") or pm.get("zpm_code") or pm_id)
            if not pm.get("assigned_imb_ids"):
                pm["assigned_imb_ids"] = sorted(set(members_by_zpm.get(zpm_id, [])))
            if pm.get("capacity") is None and zpm_id in capacity_by_zpm:
                pm["capacity"] = capacity_by_zpm[zpm_id]

    def _add_node(self, node_id: str, point: tuple[float, float]) -> None:
        self.nodes[node_id] = point

    def _road_points(self, road: dict[str, Any]) -> list[tuple[float, float]]:
        coordinates = road.get("coordinates")
        if coordinates is None and (road.get("geometry") or {}).get("type") == "LineString":
            coordinates = road["geometry"].get("coordinates")
        return [_point(point) for point in (coordinates or [])]

    def _edge_attributes(self, first: tuple[float, float], second: tuple[float, float], road: dict[str, Any]) -> tuple[bool, float, list[str], bool]:
        blocked_by_building = any(
            _segment_intersects_ring(first, second, ring)
            for building in self.buildings
            for ring in _geometry_rings(building)
        )
        multiplier = 1.0
        water_zone_ids: list[str] = []
        for index, zone in enumerate(self.water_zones, 1):
            rings = _geometry_rings(zone)
            if not rings or not _segment_intersects_ring(first, second, rings[0]):
                continue
            hits = sum(
                _inside(
                    (first[0] + (second[0] - first[0]) * ((sample + 0.5) / 32.0), first[1] + (second[1] - first[1]) * ((sample + 0.5) / 32.0)),
                    rings[0],
                )
                for sample in range(32)
            )
            ratio = hits / 32.0
            factor = float(zone.get("cost_factor") or road.get("water_cost_factor") or self.options.get("water_cost_factor", 2.0))
            multiplier *= 1.0 + (factor - 1.0) * ratio
            water_zone_ids.append(str(zone.get("id") or zone.get("code") or f"WATER_{index}"))
        return bool(water_zone_ids), multiplier, water_zone_ids, blocked_by_building

    def _road_access_eligible(self, segment: dict[str, Any]) -> bool:
        """Return whether a road segment can be used as a site duct entry.

        Site access is a relaxed *endpoint* rule, but the entry still has to
        land on a segment where duct construction is possible.  In
        particular, a geometrically closer closed/zero-capacity segment must
        not hide a usable road that is slightly farther away within the
        configured radius.
        """
        road = segment["road"]
        if not _road_open_cut_allowed(road):
            return False
        capacity = _road_capacity(road, self.default_capacity)
        if capacity == 0:
            return False
        _, _, _, blocked_by_building = self._edge_attributes(segment["start"], segment["end"], road)
        return not blocked_by_building

    def _build_road_graph(self) -> None:
        segments: list[dict[str, Any]] = []
        for index, road in enumerate(self.roads, 1):
            points = self._road_points(road)
            if len(points) < 2:
                continue
            road_id = str(road.get("id") or road.get("code") or road.get("CODE") or f"road_{index:04d}")
            for part, (first, second) in enumerate(zip(points, points[1:]), 1):
                if _metres(first, second) < 0.01:
                    continue
                segments.append({"road": road, "road_id": road_id, "part": part, "start": first, "end": second, "cuts": [(0.0, first), (1.0, second)]})
        anchors = [*self.nros, *self.pms, *self.groups, *self.entrances]
        snap_limit = float(self.options.get("terminal_snap_distance_m", 20.0))
        nro_access_limit = float(
            self.options.get(
                "nro_road_access_radius_m",
                self.options.get("nro_max_road_distance_m", DEFAULT_NRO_ROAD_ACCESS_RADIUS_M),
            )
        )
        pm_access_limit = float(
            self.options.get("pm_road_access_radius_m", DEFAULT_PM_ROAD_ACCESS_RADIUS_M)
        )
        nro_ids = {
            _id(item, "id", "code", "CODE")
            for item in self.nros
            if item.get("id") not in (None, "") or item.get("code") not in (None, "") or item.get("CODE") not in (None, "")
        }
        pm_ids = {
            _id(item, "id", "code", "CODE")
            for item in self.pms
            if item.get("id") not in (None, "") or item.get("code") not in (None, "") or item.get("CODE") not in (None, "")
        }
        for anchor in anchors:
            try:
                anchor_point = _coords(anchor)
            except (TypeError, ValueError):
                continue
            anchor_id = None
            try:
                anchor_id = _id(anchor, "id", "code", "CODE")
            except ValueError:
                pass
            is_nro = bool(anchor_id and anchor_id in nro_ids)
            is_pm = bool(anchor_id and anchor_id in pm_ids)
            best: tuple[float, dict[str, Any], tuple[float, tuple[float, float], float]] | None = None
            for segment in segments:
                # NROs and PMs may use a nearby road projection, but
                # that road must itself be usable for duct construction.
                # Other anchors retain the normal terminal snap behavior.
                if (is_nro or is_pm) and not self._road_access_eligible(segment):
                    continue
                ratio, projected, distance = _project_to_segment(anchor_point, segment["start"], segment["end"])
                candidate = (distance, segment, (ratio, projected, distance))
                if best is None or candidate[0] < best[0]:
                    best = candidate
            allowed_distance = nro_access_limit if is_nro else pm_access_limit if is_pm else snap_limit
            if best and best[0] <= allowed_distance:
                best[1]["cuts"].append((best[2][0], best[2][1]))
                if is_nro and anchor_id:
                    self.nro_road_entries[anchor_id] = {
                        "id": f"NRO_ROAD_ENTRY::{anchor_id}",
                        "nro_id": anchor_id,
                        "road_id": best[1]["road_id"],
                        "point": best[2][1],
                        "distance_m": round(best[0], 3),
                        "max_distance_m": nro_access_limit,
                    }
                elif is_pm and anchor_id:
                    self.pm_road_entries[anchor_id] = {
                        "id": f"PM_ROAD_ENTRY::{anchor_id}",
                        "pm_id": anchor_id,
                        "road_id": best[1]["road_id"],
                        "point": best[2][1],
                        "distance_m": round(best[0], 3),
                        "max_distance_m": pm_access_limit,
                    }
        for index, first_segment in enumerate(segments):
            for second_segment in segments[index + 1 :]:
                intersection = _segment_intersection(
                    first_segment["start"], first_segment["end"], second_segment["start"], second_segment["end"]
                )
                if intersection:
                    ratio_a, ratio_b, point = intersection
                    first_segment["cuts"].append((ratio_a, point))
                    second_segment["cuts"].append((ratio_b, point))

        def node_for(point: tuple[float, float]) -> str:
            for node_id, existing in self.nodes.items():
                if _metres(existing, point) <= ROAD_NODE_SNAP_M:
                    return node_id
            node_id = f"road_node_{len(self.nodes) + 1:05d}"
            self._add_node(node_id, point)
            return node_id

        for segment in segments:
            unique_cuts: list[tuple[float, tuple[float, float]]] = []
            for ratio, point in sorted(segment["cuts"], key=lambda item: item[0]):
                if unique_cuts and abs(ratio - unique_cuts[-1][0]) < 1e-8:
                    unique_cuts[-1] = (ratio, point)
                else:
                    unique_cuts.append((ratio, point))
            for split_index, (first_cut, second_cut) in enumerate(zip(unique_cuts, unique_cuts[1:]), 1):
                first, second = first_cut[1], second_cut[1]
                if _metres(first, second) < 0.01:
                    continue
                start_id = node_for(first)
                end_id = node_for(second)
                length = _metres(first, second)
                road = segment["road"]
                water, factor, water_ids, blocked_by_building = self._edge_attributes(first, second, road)
                if blocked_by_building:
                    continue
                edge_id = f"{segment['road_id']}:{segment['part']:04d}:{split_index:03d}"
                attributes = {
                    "edge_id": edge_id,
                    "length_m": length,
                    "capacity": _road_capacity(road, self.default_capacity),
                    "cost": length * float(road.get("cost_per_m") or self.default_cost) * factor,
                    "water_crossing": water,
                    "water_zone_ids": water_ids,
                    "open_cut_allowed": _road_open_cut_allowed(road),
                    "road_id": segment["road_id"],
                }
                self.edge_costs[edge_id] = float(attributes["cost"])
                self.edge_lengths[edge_id] = float(attributes["length_m"])
                self.edge_capacities[edge_id] = attributes["capacity"]
                self.graph[start_id].append({"to": end_id, **attributes})
                self.graph[end_id].append({"to": start_id, **attributes})

        # Resolve the projected NRO entry points to the actual graph node ids
        # after all cuts/intersections have been materialized.
        for entry in [*self.nro_road_entries.values(), *self.pm_road_entries.values()]:
            point = entry["point"]
            if self.nodes:
                entry["node_id"] = self._nearest_graph_node(point)

    def _nearest_graph_node(self, point: tuple[float, float]) -> str:
        return min(self.nodes, key=lambda node_id: _metres(point, self.nodes[node_id]))

    def _shortest(self, source: str, target: str, banned_edges: set[str] | None = None) -> dict[str, Any] | None:
        banned_edges = banned_edges or set()
        queue: list[tuple[float, str]] = [(0.0, source)]
        distance = {source: 0.0}
        previous: dict[str, tuple[str, dict[str, Any]]] = {}
        while queue:
            cost, node = heapq.heappop(queue)
            if cost > distance.get(node, float("inf")) + EPS:
                continue
            if node == target:
                break
            for edge in self.graph.get(node, []):
                if edge["edge_id"] in banned_edges or not edge["open_cut_allowed"]:
                    continue
                candidate = cost + edge["cost"]
                if candidate + EPS < distance.get(edge["to"], float("inf")):
                    distance[edge["to"]] = candidate
                    previous[edge["to"]] = (node, edge)
                    heapq.heappush(queue, (candidate, edge["to"]))
        if target not in distance:
            return None
        edges: list[dict[str, Any]] = []
        nodes = [target]
        current = target
        while current != source:
            parent, edge = previous[current]
            edges.append(edge)
            nodes.append(parent)
            current = parent
        edges.reverse()
        nodes.reverse()
        return self._path_record(nodes, edges)

    def _path_record(self, nodes: list[str], edges: list[dict[str, Any]], **attributes: Any) -> dict[str, Any]:
        coordinates = [self.nodes[node] for node in nodes]
        finite_capacities = [int(edge["capacity"]) for edge in edges if edge["capacity"] is not None]
        path_capacity: int | str | None = min(finite_capacities) if finite_capacities else ("unlimited" if self.default_capacity is None else self.default_capacity)
        return {
            "nodes": nodes,
            "edge_ids": [edge["edge_id"] for edge in edges],
            "coordinates": [{"x": round(point[0], 9), "y": round(point[1], 9)} for point in coordinates],
            "length_m": round(sum(edge["length_m"] for edge in edges), 3),
            "estimated_construction_cost": round(sum(edge["cost"] for edge in edges), 3),
            "capacity": path_capacity,
            "water_zone_ids": sorted({zone for edge in edges for zone in edge.get("water_zone_ids", [])}),
            **attributes,
        }

    def _candidate_paths(self, source: tuple[float, float], target: tuple[float, float], **attributes: Any) -> list[dict[str, Any]]:
        source_node = self._nearest_graph_node(source)
        target_node = self._nearest_graph_node(target)
        paths: list[dict[str, Any]] = []
        banned: set[str] = set()
        for rank in range(self.k_paths):
            path = self._shortest(source_node, target_node, banned)
            if not path:
                break
            path.update(attributes, candidate_rank=rank + 1)
            paths.append(path)
            if path["edge_ids"]:
                banned.add(path["edge_ids"][rank % len(path["edge_ids"])])
        return paths

    def _record_map(self, records: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {_id(item, "id", "code", "CODE"): item for item in records}

    @staticmethod
    def _entrance_kind(entrance: dict[str, Any]) -> str:
        return str(entrance.get("entrance_type") or entrance.get("type") or "").strip().lower()

    def _derive_pbo_building_entrances(self) -> None:
        """Use fixed PBO coordinates as building entrances.

        Task 4 emits one PBO for each positive-demand IMB and offsets it from
        the IMB point. A Task-6 building entrance therefore does not need to
        be inferred from a footprint or a road snap. PBO records from the
        adapter can omit ``building_id``; in that case the IMB or BPE record
        supplies the association when available, while the PBO id still
        identifies the derived entrance.
        """
        if not self.pbos:
            return

        def record_id(record: dict[str, Any]) -> str | None:
            for name in ("id", "code", "CODE"):
                value = record.get(name)
                if value not in (None, ""):
                    return str(value)
            return None

        bpe_by_id = {record_id(item): item for item in self.bpes if record_id(item)}
        imb_by_id = {record_id(item): item for item in self.imb_points if record_id(item)}
        generated: list[dict[str, Any]] = []
        buildings_with_pbo: set[str] = set()
        for pbo in self.pbos:
            pbo_id = record_id(pbo)
            if not pbo_id:
                continue
            try:
                point = _coords(pbo)
            except (TypeError, ValueError):
                continue
            bpe_id = str(pbo.get("bpe_id") or pbo.get("bpe_code") or "")
            bpe = bpe_by_id.get(bpe_id)
            imb_id = str(pbo.get("imb_id") or pbo.get("imb_code") or pbo.get("IMB_ID") or "")
            imb = imb_by_id.get(imb_id)
            if not imb_id:
                # Task-4 point exports may omit the explicit IMB foreign key;
                # recover the associated IMB from the bounded PBO offset.
                for candidate_id, candidate in imb_by_id.items():
                    try:
                        if _metres(point, _coords(candidate)) <= PBO_IMB_ASSOCIATION_MAX_DISTANCE_M:
                            imb_id, imb = candidate_id, candidate
                            break
                    except (TypeError, ValueError):
                        continue
            building_id = (
                pbo.get("building_id")
                or pbo.get("BUILDING_ID")
                or (imb or {}).get("building_id")
                or (imb or {}).get("BUILDING_ID")
                or (bpe or {}).get("building_id")
                or (bpe or {}).get("BUILDING_ID")
            )
            if building_id in (None, ""):
                # Some Task-4 adapter records carry only PBO/IMB coordinates.
                # Recover the containing building when a footprint is present
                # so legacy entrance records for that building are replaced.
                for building in self.buildings:
                    if any(_inside(point, ring) for ring in _geometry_rings(building)):
                        building_id = building.get("id") or building.get("code") or building.get("CODE")
                        break
            if building_id not in (None, ""):
                building_id = str(building_id)
                buildings_with_pbo.add(building_id)
            generated.append(
                {
                    "id": pbo_id,
                    "code": pbo_id,
                    "x": point[0],
                    "y": point[1],
                    "entrance_type": "building",
                    "building_id": building_id,
                    "pbo_id": pbo_id,
                    "imb_id": imb_id or None,
                    "source": "fixed_task4_pbo_location",
                    "accessible": True,
                }
            )
            self._pbo_building_entrances[pbo_id] = generated[-1]

        if not generated:
            return
        # Replace inferred/road-snapped entrances for buildings represented by
        # a PBO, while preserving explicit entries for unrelated legacy data.
        retained = [
            entrance
            for entrance in self.entrances
            if self._entrance_kind(entrance) not in {"building", "building_entrance", "building-entry"}
            or str(entrance.get("building_id") or entrance.get("BUILDING_ID") or "") not in buildings_with_pbo
        ]
        self.entrances = [*retained, *generated]

    def _pbo_records_for_bpe(self, bpe: dict[str, Any]) -> list[dict[str, Any]]:
        """Return PBOs fixed to a BPE, including adapter member lists."""
        bpe_id = str(bpe.get("id") or bpe.get("code") or bpe.get("CODE") or "")
        member_ids = {
            str(value)
            for value in (bpe.get("member_pbo_ids") or bpe.get("pbo_ids") or [])
            if value not in (None, "")
        }
        result: list[dict[str, Any]] = []
        for pbo in self.pbos:
            pbo_id = str(pbo.get("id") or pbo.get("code") or pbo.get("CODE") or "")
            pbo_bpe_id = str(pbo.get("bpe_id") or pbo.get("bpe_code") or pbo.get("leaf_bpe_code") or "")
            if pbo_id in member_ids or pbo_bpe_id == bpe_id:
                result.append(pbo)
        return result

    def _building_entries_for_bpe(self, bpe: dict[str, Any], building_entries: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        """Prefer PBO-derived entrances, then fall back to footprint entries."""
        pbo_entries = [
            self._pbo_building_entrances[pbo_id]
            for pbo in self._pbo_records_for_bpe(bpe)
            for pbo_id in [str(pbo.get("id") or pbo.get("code") or pbo.get("CODE") or "")]
            if pbo_id in self._pbo_building_entrances
        ]
        if pbo_entries:
            return pbo_entries
        building_id = str(bpe.get("building_id") or bpe.get("BUILDING_ID") or "")
        return building_entries.get(building_id, [])

    def _gates(self, gate_type: str, owner_id: str | None = None) -> list[dict[str, Any]]:
        aliases = {
            "community": {"community", "community_entrance", "community-entry"},
            "building_group": {"building_group", "building_group_entrance", "group_entrance", "building-group-entry"},
            "building": {"building", "building_entrance", "building-entry"},
        }
        result = []
        for entrance in self.entrances:
            kind = str(entrance.get("entrance_type") or entrance.get("type") or "").lower()
            if kind not in aliases.get(gate_type, {gate_type, gate_type.replace("_", "")}):
                continue
            if owner_id is not None and str(entrance.get("owner_id") or entrance.get("group_id") or entrance.get("building_id") or entrance.get("community_id")) != str(owner_id):
                continue
            if entrance.get("accessible") is False or entrance.get("ACCESSIBLE") is False:
                continue
            result.append(entrance)
        return result

    def _route_via_gates(
        self,
        source: tuple[float, float],
        target: tuple[float, float],
        gate_type: str,
        owner_id: str | None,
        route_type: str,
        source_node: str | None = None,
        target_node: str | None = None,
        **attributes: Any,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        gates = self._gates(gate_type, owner_id)
        source_node = source_node or self._nearest_graph_node(source)
        target_node = target_node or self._nearest_graph_node(target)
        for gate in gates or [{}]:
            gate_point = _coords(gate) if gate else source
            gate_node = self._nearest_graph_node(gate_point)
            first = self._shortest(source_node, gate_node) if gate else self._shortest(source_node, target_node)
            second = self._shortest(gate_node, target_node) if gate and gate_node != target_node else None
            if not first or (gate and gate_node != target_node and not second):
                continue
            if second:
                edges = [*first["edge_ids"], *second["edge_ids"]]
                nodes = [*first["nodes"], *second["nodes"][1:]]
                edge_map = {edge["edge_id"]: edge for edge in self.graph[first["nodes"][0]]}
                # Reconstruct cost from the selected path records; duplicate graph
                # lookup is avoided by retaining the aggregate path attributes.
                finite_capacities = [int(value) for value in (first["capacity"], second["capacity"]) if value is not None and str(value).lower() != "unlimited"]
                path_capacity: int | str | None = min(finite_capacities) if finite_capacities else ("unlimited" if self.default_capacity is None else self.default_capacity)
                path = {
                    "nodes": nodes,
                    "edge_ids": edges,
                    "coordinates": [*first["coordinates"], *second["coordinates"][1:]],
                    "length_m": round(first["length_m"] + second["length_m"], 3),
                    "estimated_construction_cost": round(first["estimated_construction_cost"] + second["estimated_construction_cost"], 3),
                    "capacity": path_capacity,
                    "water_zone_ids": sorted(set(first["water_zone_ids"]) | set(second["water_zone_ids"])),
                }
            else:
                path = first
            path.update(attributes, route_type=route_type, gate_id=gate.get("id") if gate else None)
            candidates.append(path)
        candidates.sort(key=lambda path: (path["estimated_construction_cost"], path["length_m"], str(path.get("gate_id") or "")))
        return candidates[: self.k_paths]

    def _select_shared(self, demands: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        used_edges: set[str] = set()
        edge_usage: dict[str, int] = defaultdict(int)
        unresolved: list[dict[str, Any]] = []
        for demand in sorted(demands, key=lambda item: (-int(item.get("demand") or 0), str(item["id"]))):
            candidates = demand.get("candidates") or []
            feasible = [
                candidate
                for candidate in candidates
                if all(
                    self.edge_capacities.get(edge_id, self.default_capacity) is None
                    or edge_usage[edge_id] + int(demand.get("demand") or 0) <= self.edge_capacities.get(edge_id, self.default_capacity)
                    for edge_id in candidate["edge_ids"]
                )
            ]
            if not feasible:
                unresolved.append({"id": demand["id"], "reason": "no_legal_capacity_feasible_candidate"})
                continue
            def incremental(candidate: dict[str, Any]) -> tuple[float, float, int]:
                new_cost = sum(self.edge_costs.get(edge_id, 0.0) for edge_id in candidate["edge_ids"] if edge_id not in used_edges)
                return new_cost, candidate["estimated_construction_cost"], int(candidate.get("candidate_rank") or 0)
            chosen = min(feasible, key=incremental)
            selected.append(chosen)
            for edge_id in chosen["edge_ids"]:
                used_edges.add(edge_id)
                edge_usage[edge_id] += int(demand.get("demand") or 0)
        return selected, {"edge_usage": dict(edge_usage), "unresolved": unresolved}

    def _select_joint_main(self, demands: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Select main-trunk paths jointly by minimum unique edge length.

        Each demand already contains legal NRO-to-PM candidates that pass
        through a community entrance. The shared trunk is the union of the
        selected candidate edge sets. For the normal two-PM case this method
        enumerates every candidate combination and is exact over the generated
        candidate pool. For larger instances it keeps a deterministic bounded
        beam, avoiding the demand-order bias of ``_select_shared``.
        """
        unresolved: list[dict[str, Any]] = []
        active: list[dict[str, Any]] = []
        for demand in demands:
            candidates = demand.get("candidates") or []
            if not candidates:
                unresolved.append({"id": demand["id"], "reason": "no_legal_capacity_feasible_candidate"})
            else:
                active.append(demand)
        if not active:
            return [], {
                "edge_usage": {},
                "unresolved": unresolved,
                "optimization": "JOINT_SHARED_TREE_EXACT_CANDIDATE_ENUMERATION",
                "objective": "unique_edge_length_m",
                "selected_unique_length_m": 0.0,
                "selected_unique_cost": 0.0,
                "selected_route_length_sum_m": 0.0,
                "shared_length_saving_m": 0.0,
            }

        def path_edges(path: dict[str, Any]) -> tuple[str, ...]:
            return tuple(dict.fromkeys(str(edge_id) for edge_id in path.get("edge_ids") or []))

        def feasible(edge_usage: dict[str, int], edge_ids: tuple[str, ...], demand_value: int) -> bool:
            for edge_id in edge_ids:
                capacity = self.edge_capacities.get(edge_id, self.default_capacity)
                if capacity is not None and edge_usage.get(edge_id, 0) + demand_value > capacity:
                    return False
            return True

        def score(chosen: list[dict[str, Any]], used_edges: set[str]) -> tuple[float, float, float, tuple[str, ...]]:
            unique_length = sum(self.edge_lengths.get(edge_id, 0.0) for edge_id in used_edges)
            unique_cost = sum(self.edge_costs.get(edge_id, 0.0) for edge_id in used_edges)
            route_length = sum(float(path.get("length_m") or 0.0) for path in chosen)
            gate_ids = tuple(str(path.get("gate_id") or "") for path in chosen)
            return unique_length, unique_cost, route_length, gate_ids

        exact_limit = max(1, int(self.options.get("main_joint_exact_limit", 100000)))
        combination_count = 1
        for demand in active:
            combination_count *= len(demand.get("candidates") or [])
            if combination_count > exact_limit:
                break

        best_chosen: list[dict[str, Any]] | None = None
        best_used: set[str] | None = None
        best_usage: dict[str, int] | None = None
        method = "JOINT_SHARED_TREE_EXACT_CANDIDATE_ENUMERATION"

        if combination_count <= exact_limit:
            candidate_lists = [demand.get("candidates") or [] for demand in active]
            for combination in itertools.product(*candidate_lists):
                used_edges: set[str] = set()
                edge_usage: dict[str, int] = defaultdict(int)
                valid = True
                for demand, path in zip(active, combination):
                    demand_value = int(demand.get("demand") or 0)
                    edge_ids = path_edges(path)
                    if not feasible(edge_usage, edge_ids, demand_value):
                        valid = False
                        break
                    used_edges.update(edge_ids)
                    for edge_id in edge_ids:
                        edge_usage[edge_id] += demand_value
                if not valid:
                    continue
                chosen = list(combination)
                if best_chosen is None or score(chosen, used_edges) < score(best_chosen, best_used or set()):
                    best_chosen, best_used, best_usage = chosen, used_edges, dict(edge_usage)
        else:
            method = "JOINT_SHARED_TREE_BOUNDED_BEAM_SEARCH"
            beam_width = max(1, int(self.options.get("main_joint_beam_width", 2000)))
            states: list[tuple[list[dict[str, Any]], set[str], dict[str, int]]] = [([], set(), {})]
            for demand in active:
                expanded: list[tuple[list[dict[str, Any]], set[str], dict[str, int]]] = []
                demand_value = int(demand.get("demand") or 0)
                for chosen, used_edges, edge_usage in states:
                    for path in demand.get("candidates") or []:
                        edge_ids = path_edges(path)
                        if not feasible(edge_usage, edge_ids, demand_value):
                            continue
                        next_used = set(used_edges)
                        next_used.update(edge_ids)
                        next_usage = dict(edge_usage)
                        for edge_id in edge_ids:
                            next_usage[edge_id] = next_usage.get(edge_id, 0) + demand_value
                        expanded.append(([*chosen, path], next_used, next_usage))
                expanded.sort(key=lambda state: score(state[0], state[1]))
                states = expanded[:beam_width]
                if not states:
                    break
            if states:
                best_chosen, best_used, best_usage = states[0]

        if best_chosen is None:
            unresolved.extend({"id": demand["id"], "reason": "no_joint_capacity_feasible_combination"} for demand in active)
            return [], {
                "edge_usage": {},
                "unresolved": unresolved,
                "optimization": method,
                "objective": "unique_edge_length_m",
                "selected_unique_length_m": 0.0,
                "selected_unique_cost": 0.0,
                "selected_route_length_sum_m": 0.0,
                "shared_length_saving_m": 0.0,
            }

        selected_unique_length = round(sum(self.edge_lengths.get(edge_id, 0.0) for edge_id in best_used or set()), 3)
        selected_unique_cost = round(sum(self.edge_costs.get(edge_id, 0.0) for edge_id in best_used or set()), 3)
        selected_route_length_sum = round(sum(float(path.get("length_m") or 0.0) for path in best_chosen), 3)
        return best_chosen, {
            "edge_usage": best_usage or {},
            "unresolved": unresolved,
            "optimization": method,
            "objective": "unique_edge_length_m",
            "selected_unique_length_m": selected_unique_length,
            "selected_unique_cost": selected_unique_cost,
            "selected_route_length_sum_m": selected_route_length_sum,
            "shared_length_saving_m": round(selected_route_length_sum - selected_unique_length, 3),
            "candidate_combinations": combination_count,
        }

    def _group_to_bpe_route(
        self,
        group_id: str,
        group_route: dict[str, Any] | None,
        bpe: dict[str, Any],
        building_entries: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any] | None:
        """Build the optional root edge from a group entry to one BPE."""
        if not group_route:
            return None
        entries = self._building_entries_for_bpe(bpe, building_entries)
        if not entries:
            return None
        entrance = min(entries, key=lambda item: _metres(_coords(item), _coords(bpe)))
        entrance_point = _coords(entrance)
        bpe_point = _coords(bpe)
        coordinates = [
            group_route["coordinates"][-1],
            {"x": entrance_point[0], "y": entrance_point[1]},
            {"x": bpe_point[0], "y": bpe_point[1]},
        ]
        if entrance_point == bpe_point:
            coordinates.pop(2)
        if coordinates[0] == coordinates[1]:
            coordinates.pop(1)
        length = round(sum(_metres(_point(a), _point(b)) for a, b in zip(coordinates, coordinates[1:])), 3)
        return {
            "id": f"group_to_bpe:{_id(bpe, 'id', 'code', 'CODE')}",
            "route_type": "GROUP_ENTRY_TO_BPE",
            "from_code": group_id,
            "to_code": _id(bpe, "id", "code", "CODE"),
            "building_entrance_id": _id(entrance, "id", "code", "CODE"),
            "coordinates": coordinates,
            "length_m": length,
            "capacity": "unlimited" if self.default_capacity is None else self.default_capacity,
            "estimated_construction_cost": round(
                length * float(self.options.get("indoor_cost_per_m", 1.0))
                + float(self.options.get("building_entry_cost", 0.0)),
                3,
            ),
        }

    def _bpe_to_bpe_route(self, source: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
        """Build a direct indoor conduit edge between two adjacent BPEs."""
        source_id = _id(source, "id", "code", "CODE")
        target_id = _id(target, "id", "code", "CODE")
        source_point = _coords(source)
        target_point = _coords(target)
        length = round(_metres(source_point, target_point), 3)
        return {
            "id": f"bpe_to_bpe:{source_id}:{target_id}",
            "route_type": "BPE_TO_BPE",
            "from_code": source_id,
            "to_code": target_id,
            "coordinates": [
                {"x": source_point[0], "y": source_point[1]},
                {"x": target_point[0], "y": target_point[1]},
            ],
            "length_m": length,
            "capacity": "unlimited" if self.default_capacity is None else self.default_capacity,
            "estimated_construction_cost": round(
                length * float(self.options.get("indoor_cost_per_m", 1.0)),
                3,
            ),
        }

    def _build_bpe_connectivity_tree(
        self,
        bpe_map: dict[str, dict[str, Any]],
        group_entry_by_id: dict[str, dict[str, Any]],
        building_entries: dict[str, list[dict[str, Any]]],
        group_pm_by_id: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Connect every BPE to a PM root or to another reachable BPE.

        The previous constraint generated one root-to-BPE edge for every BPE.
        This is a minimum spanning forest over each PM's group entries and BPE
        points. Direct group-entry edges remain legal root attachments, while
        BPE-to-BPE edges may cross building-group boundaries within that PM.
        """
        group_pm_by_id = group_pm_by_id or {}
        by_pm: dict[str, list[tuple[str, str, dict[str, Any]]]] = defaultdict(list)
        unresolved: list[dict[str, Any]] = []
        for bpe_id, bpe in bpe_map.items():
            group_id = str(bpe.get("group_id") or bpe.get("building_group_id") or "")
            if not group_id:
                unresolved.append({"id": f"bpe:{bpe_id}", "reason": "missing_group_assignment"})
                continue
            pm_id = str(
                bpe.get("pm_id")
                or bpe.get("pm_code")
                or group_pm_by_id.get(group_id, "")
            )
            if not pm_id:
                unresolved.append({"id": f"bpe:{bpe_id}", "reason": "missing_pm_assignment"})
                continue
            by_pm[pm_id].append((bpe_id, group_id, bpe))

        selected: list[dict[str, Any]] = []
        for pm_id in sorted(by_pm):
            members = sorted(by_pm[pm_id], key=lambda item: item[0])
            root_edges = {
                bpe_id: self._group_to_bpe_route(group_id, group_entry_by_id.get(group_id), bpe, building_entries)
                for bpe_id, group_id, bpe in members
            }
            root_edges = {bpe_id: route for bpe_id, route in root_edges.items() if route}
            if not root_edges:
                unresolved.extend(
                    {"id": f"bpe:{bpe_id}", "reason": "missing_group_route_or_building_entrance"}
                    for bpe_id, _, _ in members
                )
                continue

            member_map = {bpe_id: bpe for bpe_id, _, bpe in members}
            pair_edges = {
                (left_id, right_id): self._bpe_to_bpe_route(member_map[left_id], member_map[right_id])
                for index, (left_id, _, _) in enumerate(members)
                for right_id, _, _ in members[index + 1 :]
            }
            attached: set[str] = set()
            while len(attached) < len(members):
                candidates: list[tuple[tuple[Any, ...], str, dict[str, Any]]] = []
                for bpe_id, route in root_edges.items():
                    if bpe_id not in attached:
                        candidates.append(
                            ((float(route["length_m"]), float(route["estimated_construction_cost"]), "", bpe_id), bpe_id, route)
                        )
                for (left_id, right_id), route in pair_edges.items():
                    if left_id in attached and right_id not in attached:
                        candidates.append(
                            ((float(route["length_m"]), float(route["estimated_construction_cost"]), left_id, right_id), right_id, route)
                        )
                    elif right_id in attached and left_id not in attached:
                        reverse_route = self._bpe_to_bpe_route(member_map[right_id], member_map[left_id])
                        candidates.append(
                            ((float(reverse_route["length_m"]), float(reverse_route["estimated_construction_cost"]), right_id, left_id), left_id, reverse_route)
                        )
                if not candidates:
                    unresolved.extend(
                        {"id": f"bpe:{bpe_id}", "reason": "bpe_not_reachable_from_group_entry"}
                        for bpe_id, _, _ in members
                        if bpe_id not in attached
                    )
                    break
                _, target_id, route = min(candidates, key=lambda item: item[0])
                selected.append(route)
                attached.add(target_id)

        return selected, unresolved

    def plan(self) -> dict[str, Any]:
        pm_map = self._record_map(self.pms)
        nro_map = self._record_map(self.nros)
        group_map = self._record_map(self.groups)
        bpe_map = self._record_map(self.bpes)
        pbo_map = self._record_map(self.pbos)
        main_demands: list[dict[str, Any]] = []
        pm_capacity_issues: list[dict[str, Any]] = []
        nro_access_fibers: list[dict[str, Any]] = []
        nro_access_validation: dict[str, dict[str, Any]] = {}
        nro_access_unresolved: list[dict[str, Any]] = []
        pm_access_fibers: list[dict[str, Any]] = []
        pm_access_validation: dict[str, dict[str, Any]] = {}
        pm_access_unresolved: list[dict[str, Any]] = []
        for nro_id, nro in nro_map.items():
            entry = self.nro_road_entries.get(nro_id)
            if not entry or not entry.get("node_id"):
                nro_access_validation[nro_id] = {
                    "status": "REVIEW_REQUIRED",
                    "road_entry_id": None,
                    "distance_m": None,
                    "max_distance_m": float(
                        self.options.get(
                            "nro_road_access_radius_m",
                            self.options.get("nro_max_road_distance_m", DEFAULT_NRO_ROAD_ACCESS_RADIUS_M),
                        )
                    ),
                }
                nro_access_unresolved.append(
                    {"id": f"nro:{nro_id}", "reason": "no_road_within_nro_access_radius"}
                )
                continue
            nro_point = _coords(nro)
            entry_point = entry["point"]
            direct_length = round(_metres(nro_point, entry_point), 3)
            entry_id = str(entry["id"])
            nro_access_fibers.append(
                {
                    "id": f"nro_access_fiber:{nro_id}",
                    "route_type": "NRO_TO_DUCT_ENTRY_FIBER",
                    "from_type": "NRO",
                    "from_code": nro_id,
                    "to_type": "DUCT_ENTRY",
                    "to_code": entry_id,
                    "nro_id": nro_id,
                    "road_entry_id": entry_id,
                    "road_id": entry.get("road_id"),
                    "coordinates": [
                        {"x": nro_point[0], "y": nro_point[1]},
                        {"x": entry_point[0], "y": entry_point[1]},
                    ],
                    "length_m": direct_length,
                    "fiber_cores": 144,
                    "cable_cores": 144,
                    "cable_spec": "FIBER_144_CORE",
                    "physical_path_status": "DIRECT_FIBER_TO_ROAD_ENTRY",
                }
            )
            nro_access_validation[nro_id] = {
                "status": "PASS",
                "road_entry_id": entry_id,
                "road_id": entry.get("road_id"),
                "road_node_id": entry.get("node_id"),
                "distance_m": direct_length,
                "max_distance_m": entry.get("max_distance_m"),
            }
        for pm_id, pm in pm_map.items():
            entry = self.pm_road_entries.get(pm_id)
            if not entry or not entry.get("node_id"):
                pm_access_validation[pm_id] = {
                    "status": "REVIEW_REQUIRED",
                    "road_entry_id": None,
                    "distance_m": None,
                    "max_distance_m": float(
                        self.options.get("pm_road_access_radius_m", DEFAULT_PM_ROAD_ACCESS_RADIUS_M)
                    ),
                }
                pm_access_unresolved.append(
                    {"id": f"pm:{pm_id}", "reason": "no_road_within_pm_access_radius"}
                )
                continue
            pm_point = _coords(pm)
            entry_point = entry["point"]
            direct_length = round(_metres(pm_point, entry_point), 3)
            entry_id = str(entry["id"])
            pm_access_fibers.append(
                {
                    "id": f"pm_access_fiber:{pm_id}",
                    "route_type": "PM_TO_DUCT_ENTRY_FIBER",
                    "from_type": "PM",
                    "from_code": pm_id,
                    "to_type": "DUCT_ENTRY",
                    "to_code": entry_id,
                    "pm_id": pm_id,
                    "road_entry_id": entry_id,
                    "road_id": entry.get("road_id"),
                    "coordinates": [
                        {"x": pm_point[0], "y": pm_point[1]},
                        {"x": entry_point[0], "y": entry_point[1]},
                    ],
                    "length_m": direct_length,
                    "fiber_cores": 144,
                    "cable_cores": 144,
                    "cable_spec": "FIBER_144_CORE",
                    "physical_path_status": "DIRECT_FIBER_TO_ROAD_ENTRY",
                }
            )
            pm_access_validation[pm_id] = {
                "status": "PASS",
                "road_entry_id": entry_id,
                "road_id": entry.get("road_id"),
                "road_node_id": entry.get("node_id"),
                "distance_m": direct_length,
                "max_distance_m": entry.get("max_distance_m"),
            }
        for pm_id, pm in pm_map.items():
            assigned = {str(item) for item in (pm.get("assigned_imb_ids") or [])}
            demand = sum(
                int(imb.get("ports") or imb.get("demand") or imb.get("NB_LOC_TOT") or 0)
                for imb in self.imb_points
                if {
                    str(imb[name])
                    for name in ("id", "code", "CODE")
                    if imb.get(name) not in (None, "")
                }
                & assigned
            )
            if not demand:
                demand = int(pm.get("demand") or 0)
            pm_capacity = _capacity(pm.get("capacity"), self.default_capacity)
            if pm_capacity is not None and demand > pm_capacity:
                pm_capacity_issues.append({"id": f"main:{pm_id}", "reason": "fixed_pm_capacity_exceeded", "demand": demand, "capacity": pm_capacity})
            candidates: list[dict[str, Any]] = []
            pm_entry = self.pm_road_entries.get(pm_id)
            if not pm_entry or not pm_entry.get("node_id"):
                main_demands.append({"id": f"main:{pm_id}", "demand": demand, "candidates": [], "to_code": pm_id})
                continue
            for nro_id, nro in nro_map.items():
                entry = self.nro_road_entries.get(nro_id)
                if not entry or not entry.get("node_id"):
                    continue
                for candidate in self._route_via_gates(
                    _coords(nro),
                    _coords(pm),
                    "community",
                    pm.get("community_id"),
                    "NRO_TO_PM_MAIN",
                    source_node=str(entry["node_id"]),
                    target_node=str(pm_entry["node_id"]),
                    from_code=nro_id,
                    to_code=pm_id,
                    nro_road_entry_id=entry["id"],
                    nro_road_entry_distance_m=entry["distance_m"],
                    pm_road_entry_id=pm_entry["id"],
                    pm_road_entry_distance_m=pm_entry["distance_m"],
                ):
                    candidates.append(candidate)
            main_demands.append({"id": f"main:{pm_id}", "demand": demand, "candidates": [] if pm_capacity is not None and demand > pm_capacity else candidates, "to_code": pm_id})
        main_selected, main_validation = self._select_joint_main(main_demands)

        group_demands: list[dict[str, Any]] = []
        for group_id, group in group_map.items():
            pm_id = str(group.get("pm_id") or group.get("zpm_id") or "")
            pm = pm_map.get(pm_id)
            if not pm:
                continue
            pm_entry = self.pm_road_entries.get(pm_id)
            demand = int(group.get("demand") or 0)
            group_demands.append({
                "id": f"group:{group_id}",
                "demand": demand,
                "candidates": self._route_via_gates(
                    _coords(pm),
                    _coords(group),
                    "building_group",
                    group_id,
                    "PM_TO_GROUP_ENTRY",
                    source_node=str(pm_entry["node_id"]),
                    from_code=pm_id,
                    to_code=group_id,
                    pm_road_entry_id=pm_entry["id"],
                    pm_road_entry_distance_m=pm_entry["distance_m"],
                ) if pm_entry and pm_entry.get("node_id") else [],
                "to_code": group_id,
            })
        group_selected, group_validation = self._select_shared(group_demands)

        internal_routes: list[dict[str, Any]] = []
        unresolved_internal: list[dict[str, Any]] = []
        building_entries = defaultdict(list)
        for entrance in self.entrances:
            building_entries[str(entrance.get("building_id"))].append(entrance)
        group_entry_by_id = {str(route.get("to_code")): route for route in group_selected}
        group_pm_by_id = {
            group_id: str(group.get("pm_id") or group.get("zpm_id") or "")
            for group_id, group in group_map.items()
        }
        bpe_routes, bpe_unresolved = self._build_bpe_connectivity_tree(
            bpe_map,
            group_entry_by_id,
            building_entries,
            group_pm_by_id,
        )
        internal_routes.extend(bpe_routes)
        unresolved_internal.extend(bpe_unresolved)
        for pbo_id, pbo in pbo_map.items():
            bpe_id = str(pbo.get("bpe_code") or pbo.get("bpe_id") or "")
            bpe = bpe_map.get(bpe_id)
            if not bpe:
                unresolved_internal.append({"id": f"pbo:{pbo_id}", "reason": "missing_fixed_bpe_assignment"})
                continue
            building_id = str(pbo.get("building_id") or bpe.get("building_id") or "")
            entries = building_entries.get(building_id, [])
            # The fixed PBO itself is the building access point. This keeps
            # the BPE-to-PBO link at zero indoor offset even when the source
            # entrance layer contains a different inferred point.
            entrance = self._pbo_building_entrances.get(pbo_id)
            if entrance is None and entries:
                entrance = min(entries, key=lambda item: _metres(_coords(item), _coords(pbo)))
            if entrance is None:
                unresolved_internal.append({"id": f"pbo:{pbo_id}", "reason": "missing_building_entrance"})
                continue
            bpe_point = _coords(bpe)
            entrance_point = _coords(entrance)
            pbo_point = _coords(pbo)
            coordinates = [
                {"x": bpe_point[0], "y": bpe_point[1]},
                {"x": entrance_point[0], "y": entrance_point[1]},
                {"x": pbo_point[0], "y": pbo_point[1]},
            ]
            if entrance_point == pbo_point:
                coordinates.pop(1)
            length = round(sum(_metres(_point(a), _point(b)) for a, b in zip(coordinates, coordinates[1:])), 3)
            internal_routes.append({"id": f"bpe_to_pbo:{pbo_id}", "route_type": "BPE_TO_PBO", "from_code": bpe_id, "to_code": pbo_id, "building_entrance_id": _id(entrance, "id", "code", "CODE"), "coordinates": coordinates, "length_m": length, "estimated_construction_cost": round(length * float(self.options.get("indoor_cost_per_m", 1.0)) + float(self.options.get("building_entry_cost", 0.0)), 3)})

        routes = [{"id": f"main:{index:04d}", **path} for index, path in enumerate(main_selected, 1)]
        routes.extend({"id": f"group:{index:04d}", **path} for index, path in enumerate(group_selected, 1))
        routes.extend(internal_routes)
        road_route_types = {"NRO_TO_PM_MAIN", "PM_TO_GROUP_ENTRY"}
        installed_road_edges = {
            edge_id
            for route in routes
            if route.get("route_type") in road_route_types
            for edge_id in route.get("edge_ids") or []
        }
        installed_road_cost = sum(self.edge_costs.get(edge_id, 0.0) for edge_id in installed_road_edges)
        indoor_cost = sum(
            float(route.get("estimated_construction_cost") or route.get("length_m") or 0.0)
            for route in routes
            if route.get("route_type") not in road_route_types
        )
        total_cost = installed_road_cost + indoor_cost
        unresolved = [
            *pm_capacity_issues,
            *nro_access_unresolved,
            *pm_access_unresolved,
            *main_validation["unresolved"],
            *group_validation["unresolved"],
            *unresolved_internal,
        ]

        # Validate the relaxed contract explicitly: each BPE/PBO only needs an
        # endpoint-connected path back to its owning PM/BPE.  A direct
        # PM-to-every-BPE edge is not required when BPE_TO_BPE links form a
        # shared tree.
        conduit_graph: dict[str, set[str]] = defaultdict(set)
        for route in routes:
            start = str(route.get("from_code") or route.get("from_id") or "")
            end = str(route.get("to_code") or route.get("to_id") or "")
            if start and end:
                conduit_graph[start].add(end)
                conduit_graph[end].add(start)

        def reachable(source: str, target: str) -> bool:
            if not source or not target:
                return False
            queue = [source]
            seen = {source}
            while queue:
                current = queue.pop(0)
                if current == target:
                    return True
                for neighbor in sorted(conduit_graph.get(current, ())):
                    if neighbor not in seen:
                        seen.add(neighbor)
                        queue.append(neighbor)
            return False

        bpe_reachability: dict[str, dict[str, Any]] = {}
        pbo_reachability: dict[str, dict[str, Any]] = {}
        unresolved_ids = {str(item.get("id")) for item in unresolved if item.get("id")}
        for bpe_id, bpe in bpe_map.items():
            group_id = str(bpe.get("group_id") or bpe.get("building_group_id") or "")
            group = group_map.get(group_id) or {}
            pm_id = str(bpe.get("pm_id") or bpe.get("pm_code") or group.get("pm_id") or group.get("zpm_id") or "")
            is_reachable = reachable(pm_id, bpe_id)
            bpe_reachability[bpe_id] = {"pm_id": pm_id, "reachable": is_reachable}
            if not is_reachable and f"bpe:{bpe_id}" not in unresolved_ids:
                unresolved.append({"id": f"bpe:{bpe_id}", "reason": "bpe_not_reachable_from_pm"})
                unresolved_ids.add(f"bpe:{bpe_id}")
        for pbo_id, pbo in pbo_map.items():
            bpe_id = str(pbo.get("bpe_code") or pbo.get("bpe_id") or "")
            is_reachable = reachable(bpe_id, pbo_id)
            pbo_reachability[pbo_id] = {"bpe_id": bpe_id, "reachable": is_reachable}
            if not is_reachable and f"pbo:{pbo_id}" not in unresolved_ids:
                unresolved.append({"id": f"pbo:{pbo_id}", "reason": "pbo_not_reachable_from_bpe"})
                unresolved_ids.add(f"pbo:{pbo_id}")

        service_weak_points = []
        for index, item in enumerate([*self.pbos, *self.bpes], 1):
            service_weak_points.append({
                "id": f"weak_{index:04d}",
                "code": _id(item, "id", "code", "CODE"),
                "target_code": _id(item, "id", "code", "CODE"),
                "target_type": "PBO" if item in self.pbos else "BPE",
                "point_type": "PBO_PTC" if item in self.pbos else "BPE_PTC",
                "nature": "CREATION",
                "x": _coords(item)[0],
                "y": _coords(item)[1],
                "source": "fixed_task4_service_plan",
            })
        max_ptech_spacing = float(
            self.options.get("ptech_max_spacing_m", self.options.get("max_ptech_spacing_m", 200.0))
        )
        ptech_plan, ptech_validation = plan_ptech(
            routes,
            self.bpes,
            self.pbos,
            max_spacing_m=max_ptech_spacing,
        )
        # Keep the pre-existing Task-4 service points for compatibility while
        # exposing the new, fully planned PTECH layer separately. The unified
        # weak-current layer contains both records for downstream GIS users.
        weak_points = [*service_weak_points, *ptech_plan]
        return {
            "meta": {
                "algorithm": "layered_fixed_assignment_shared_edge_search_v1",
                "optimization_status": main_validation["optimization"],
                "main_trunk_objective": main_validation["objective"],
                "main_trunk_unique_length_m": main_validation["selected_unique_length_m"],
                "main_trunk_unique_cost": main_validation["selected_unique_cost"],
                "main_trunk_route_length_sum_m": main_validation["selected_route_length_sum_m"],
                "main_trunk_shared_length_saving_m": main_validation["shared_length_saving_m"],
                "candidate_paths_per_demand": self.k_paths,
                "fixed_pm_assignment": True,
                "fixed_task4_service_relationships": True,
                "nro_road_access_rule": "ROAD_WITHIN_50M_THEN_DIRECT_144_CORE_FIBER",
                "nro_road_access_radius_m": float(
                    self.options.get(
                        "nro_road_access_radius_m",
                        self.options.get("nro_max_road_distance_m", DEFAULT_NRO_ROAD_ACCESS_RADIUS_M),
                    )
                ),
                "pm_road_access_rule": "ROAD_WITHIN_CONFIGURED_RADIUS_THEN_DIRECT_144_CORE_FIBER",
                "pm_road_access_radius_m": float(
                    self.options.get("pm_road_access_radius_m", DEFAULT_PM_ROAD_ACCESS_RADIUS_M)
                ),
                "building_entrance_rule": "PBO_OFFSET_ACCESS_POINT_FOR_IMB",
                "bpe_connectivity_rule": "PM_REACHABLE_NOT_DIRECT_PER_BPE",
                "bpe_connectivity_mode": "ROOTED_SHARED_TREE",
                "bpe_network_length_m": round(
                    sum(
                        float(route.get("length_m") or 0.0)
                        for route in routes
                        if route.get("route_type") in {"GROUP_ENTRY_TO_BPE", "BPE_TO_BPE"}
                    ),
                    3,
                ),
                "cost": round(total_cost, 3),
                "counts": {
                    "nro": len(self.nros),
                    "pm": len(self.pms),
                    "building_groups": len(self.groups),
                    "imb": len(self.imb_points),
                    "bpe": len(self.bpes),
                    "pbo": len(self.pbos),
                    "weak_current_points": len(weak_points),
                    "ptech_points": len(ptech_plan),
                    "main_trunk_segments": sum(route.get("route_type") == "NRO_TO_PM_MAIN" for route in routes),
                    "nro_access_fibers": len(nro_access_fibers),
                    "pm_access_fibers": len(pm_access_fibers),
                    "bpe_to_bpe_routes": sum(route.get("route_type") == "BPE_TO_BPE" for route in routes),
                    "duct_routes": len(routes),
                    "routes": len(routes),
                    "unresolved": len(unresolved),
                    "capacity_overloads": len(pm_capacity_issues),
                    "excluded_main_segments": 0,
                    "forbidden_zone_avoidance_routes": 0,
                    "unresolved_access_routes": len(unresolved),
                },
            },
            "routes": routes,
            "duct_routes": routes,
            "nro_road_entries": [
                {
                    "id": entry["id"],
                    "nro_id": entry["nro_id"],
                    "road_id": entry.get("road_id"),
                    "road_node_id": entry.get("node_id"),
                    "x": entry["point"][0],
                    "y": entry["point"][1],
                    "distance_m": entry["distance_m"],
                    "max_distance_m": entry["max_distance_m"],
                }
                for entry in sorted(self.nro_road_entries.values(), key=lambda item: item["nro_id"])
            ],
            # This short span is optical fibre, not a duct route.  Task 5
            # prepends it to the 144-core NRO-to-PM fibre link geometry.
            "nro_access_fibers": nro_access_fibers,
            "pm_road_entries": [
                {
                    "id": entry["id"],
                    "pm_id": entry["pm_id"],
                    "road_id": entry.get("road_id"),
                    "road_node_id": entry.get("node_id"),
                    "x": entry["point"][0],
                    "y": entry["point"][1],
                    "distance_m": entry["distance_m"],
                    "max_distance_m": entry["max_distance_m"],
                }
                for entry in sorted(self.pm_road_entries.values(), key=lambda item: item["pm_id"])
            ],
            # Like NRO access, this is fibre rather than a duct route.
            "pm_access_fibers": pm_access_fibers,
            "trunk_roads": self.roads,
            "road_nodes": [{"id": node_id, "x": point[0], "y": point[1]} for node_id, point in self.nodes.items()],
            "constraint_features": [
                {
                    "type": "Feature",
                    "properties": {
                        "code": str(zone.get("id") or zone.get("code") or f"WATER_{index:03d}"),
                        "constraint_type": "cost",
                        "cost_factor": float(zone.get("cost_factor") or self.options.get("water_cost_factor", 2.0)),
                    },
                    "geometry": zone.get("geometry"),
                }
                for index, zone in enumerate(self.water_zones, 1)
                if zone.get("geometry")
            ],
            "weak_current_points": weak_points,
            "ptech_plan": ptech_plan,
            "ptech_validation": ptech_validation,
            "building_entrances": [
                entrance
                for entrance in self.entrances
                if self._entrance_kind(entrance) in {"building", "building_entrance", "building-entry"}
            ],
            "pbo_plan": self.pbos,
            "bpe_plan": self.bpes,
            "imb_points": self.imb_points,
            "validation": {
                "status": "REVIEW_REQUIRED" if unresolved else "PASS",
                "unresolved": unresolved,
                "main_edge_usage": main_validation["edge_usage"],
                "group_edge_usage": group_validation["edge_usage"],
                "main_trunk_optimization": main_validation["optimization"],
                "main_trunk_objective": main_validation["objective"],
                "main_trunk_unique_length_m": main_validation["selected_unique_length_m"],
                "main_trunk_unique_cost": main_validation["selected_unique_cost"],
                "main_trunk_route_length_sum_m": main_validation["selected_route_length_sum_m"],
                "main_trunk_shared_length_saving_m": main_validation["shared_length_saving_m"],
                "main_candidate_combinations": main_validation.get("candidate_combinations", 0),
                "nro_road_access": nro_access_validation,
                "pm_road_access": pm_access_validation,
                "connectivity_rule": "PM_REACHABLE_NOT_DIRECT_PER_BPE",
                "bpe_reachability": bpe_reachability,
                "pbo_reachability": pbo_reachability,
                "ptech": ptech_validation,
            },
            "fixed_assignments": {
                "nro_sites": self.nros,
                "pm_sites": self.pms,
                "bpe_plan": self.bpes,
                "pbo_plan": self.pbos,
                "imb_points": self.imb_points,
                "ptech_plan": ptech_plan,
                "nro_road_entries": [
                    {
                        "id": entry["id"],
                        "nro_id": entry["nro_id"],
                        "road_id": entry.get("road_id"),
                        "road_node_id": entry.get("node_id"),
                        "x": entry["point"][0],
                        "y": entry["point"][1],
                        "distance_m": entry["distance_m"],
                        "max_distance_m": entry["max_distance_m"],
                    }
                    for entry in sorted(self.nro_road_entries.values(), key=lambda item: item["nro_id"])
                ],
                "nro_access_fibers": nro_access_fibers,
                "pm_road_entries": [
                    {
                        "id": entry["id"],
                        "pm_id": entry["pm_id"],
                        "road_id": entry.get("road_id"),
                        "road_node_id": entry.get("node_id"),
                        "x": entry["point"][0],
                        "y": entry["point"][1],
                        "distance_m": entry["distance_m"],
                        "max_distance_m": entry["max_distance_m"],
                    }
                    for entry in sorted(self.pm_road_entries.values(), key=lambda item: item["pm_id"])
                ],
                "pm_access_fibers": pm_access_fibers,
                "building_entrances": [
                    entrance
                    for entrance in self.entrances
                    if self._entrance_kind(entrance) in {"building", "building_entrance", "building-entry"}
                ],
            },
        }


def plan_layered(input_path: str | Path) -> dict[str, Any]:
    path = Path(input_path)
    return LayeredPlanner(json.loads(path.read_text(encoding="utf-8-sig")), path.parent).plan()


def plan_layered_data(data: dict[str, Any], base_dir: str | Path) -> dict[str, Any]:
    return LayeredPlanner(data, Path(base_dir)).plan()
