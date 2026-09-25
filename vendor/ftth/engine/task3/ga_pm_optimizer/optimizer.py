"""Constraint-first genetic PM placement with the Task-6 planner as evaluator."""

from __future__ import annotations

import copy
import csv
import json
import math
import random
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable

from .geometry import (
    Point,
    extract_zpm_polygons,
    interpolate,
    metres,
    offset_perpendicular,
    point,
    point_in_polygons,
    point_with_clearance_in_polygons,
    polygon_intersection_ratios,
    polyline_outside_length,
    project_ratio,
    utm29,
)


TASK3_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = TASK3_DIR.parent
TASK6_CODE_DIR = PROJECT_DIR / "任务6" / "code"
if str(TASK6_CODE_DIR) not in sys.path:
    sys.path.insert(0, str(TASK6_CODE_DIR))

from layered_planner import LayeredPlanner  # noqa: E402


ROAD_ROUTE_TYPES = {"NRO_TO_PM_MAIN", "PM_TO_GROUP_ENTRY"}
LOCAL_ROUTE_TYPES = {"PM_TO_GROUP_ENTRY", "GROUP_ENTRY_TO_BPE", "BPE_TO_BPE", "BPE_TO_PBO"}


@dataclass(frozen=True)
class OptimizationConfig:
    candidate_spacing_m: float = 8.0
    candidate_lateral_spacing_m: float = 5.0
    pm_road_access_radius_m: float = 10.0
    candidate_min_separation_m: float = 3.0
    zpm_boundary_clearance_m: float = 0.5
    candidate_limit_per_zpm: int = 24
    diversity_fraction: float = 0.3
    population_size: int = 14
    generations: int = 10
    elite_count: int = 2
    tournament_size: int = 3
    crossover_rate: float = 0.85
    mutation_rate: float = 0.3
    local_neighbor_count: int = 5
    patience: int = 5
    seed: int = 1433223

    def validate(self) -> None:
        if self.candidate_spacing_m <= 0:
            raise ValueError("candidate_spacing_m must be positive")
        if self.candidate_lateral_spacing_m <= 0:
            raise ValueError("candidate_lateral_spacing_m must be positive")
        if self.pm_road_access_radius_m < 0:
            raise ValueError("pm_road_access_radius_m must be non-negative")
        if self.candidate_min_separation_m < 0 or self.zpm_boundary_clearance_m < 0:
            raise ValueError("candidate separation and ZPM clearance must be non-negative")
        if self.candidate_limit_per_zpm < 2:
            raise ValueError("candidate_limit_per_zpm must be at least 2")
        if self.population_size < 4:
            raise ValueError("population_size must be at least 4")
        if self.generations < 1:
            raise ValueError("generations must be positive")
        if not 0 <= self.diversity_fraction < 1:
            raise ValueError("diversity_fraction must be in [0, 1)")
        if not 0 <= self.crossover_rate <= 1 or not 0 <= self.mutation_rate <= 1:
            raise ValueError("crossover_rate and mutation_rate must be in [0, 1]")


@dataclass(frozen=True)
class PMCandidate:
    id: str
    zpm_id: str
    x: float
    y: float
    road_id: str
    road_part: int
    segment_ratio: float
    road_entry_x: float
    road_entry_y: float
    road_access_distance_m: float
    spatial_score_m: float
    max_bpe_distance_m: float
    rank: int
    sources: tuple[str, ...]

    @property
    def coordinate(self) -> Point:
        return self.x, self.y

    @property
    def road_entry_coordinate(self) -> Point:
        return self.road_entry_x, self.road_entry_y

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["sources"] = list(self.sources)
        return payload


@dataclass
class Evaluation:
    chromosome: tuple[int, ...]
    candidate_ids: tuple[str, ...]
    metrics: dict[str, Any]
    hard_violations: list[str]
    fitness: tuple[float, ...]
    error: str | None = None

    def to_dict(self, zpm_ids: Iterable[str]) -> dict[str, Any]:
        genes = {zpm_id: candidate_id for zpm_id, candidate_id in zip(zpm_ids, self.candidate_ids)}
        return {
            "chromosome": list(self.chromosome),
            "genes": genes,
            "fitness": list(self.fitness),
            "hard_violations": self.hard_violations,
            "metrics": _json_numbers(self.metrics),
            "error": self.error,
        }


@dataclass
class OptimizationResult:
    zpm_ids: list[str]
    candidates: dict[str, list[PMCandidate]]
    candidate_generation: dict[str, Any]
    config: OptimizationConfig
    original: Evaluation
    initial: Evaluation
    optimized: Evaluation
    history: list[dict[str, Any]]
    evaluations: list[Evaluation]
    optimized_input: dict[str, Any]
    task6_plan: dict[str, Any]


def _json_numbers(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_numbers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_numbers(item) for item in value]
    return value


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_numbers(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _record_id(record: dict[str, Any]) -> str:
    for name in ("id", "code", "CODE"):
        if record.get(name) not in (None, ""):
            return str(record[name])
    raise ValueError("Record has no id/code")


def _road_points(road: dict[str, Any]) -> list[Point]:
    coordinates = road.get("coordinates")
    if coordinates is None and isinstance(road.get("geometry"), dict):
        coordinates = road["geometry"].get("coordinates")
    return [point(item) for item in (coordinates or [])]


def _road_usable(road: dict[str, Any]) -> bool:
    opened = road.get("open_cut_allowed", road.get("OPEN_CUT_ALLOWED", True))
    if str(opened).strip().lower() in {"false", "0", "no", "closed"}:
        return False
    capacity = road.get("capacity", road.get("DUCT_CAPACITY", "unlimited"))
    return str(capacity).strip().lower() not in {"0", "closed", "none"}


def _route_edge_lengths(route: dict[str, Any]) -> dict[str, float]:
    edge_ids = [str(item) for item in route.get("edge_ids") or []]
    coordinates = [point(item) for item in route.get("coordinates") or []]
    if len(coordinates) == len(edge_ids) + 1:
        return {
            edge_id: metres(first, second)
            for edge_id, first, second in zip(edge_ids, coordinates, coordinates[1:])
        }
    if not edge_ids:
        return {}
    average = float(route.get("length_m") or 0.0) / len(edge_ids)
    return {edge_id: average for edge_id in edge_ids}


class PMOptimizer:
    def __init__(
        self,
        task6_input_path: str | Path,
        task2_regions_path: str | Path,
        config: OptimizationConfig | None = None,
    ) -> None:
        self.task6_input_path = Path(task6_input_path).resolve()
        self.task2_regions_path = Path(task2_regions_path).resolve()
        self.config = config or OptimizationConfig()
        self.config.validate()
        self.base_data = _read_json(self.task6_input_path)
        self.region_data = _read_json(self.task2_regions_path)
        self.regions = {
            str(region["id"]): region for region in self.region_data.get("zpm_regions") or []
        }
        self.polygons = {
            zpm_id: extract_zpm_polygons(region) for zpm_id, region in self.regions.items()
        }
        self.pms = list(self.base_data.get("pm_sites") or [])
        self.nros = list(self.base_data.get("nro_sites") or [])
        self.bpes = list(self.base_data.get("bpe_plan") or [])
        self.pbos = list(self.base_data.get("pbo_plan") or [])
        self.groups = list(self.base_data.get("building_groups") or [])
        self.roads = list(self.base_data.get("roads") or [])
        self.imbs = list(self.base_data.get("imb_points") or [])
        self.pm_by_id = {_record_id(pm): pm for pm in self.pms}
        self.zpm_ids = sorted(str(pm.get("zpm_id") or pm.get("partition_id") or "") for pm in self.pms)
        self.pm_id_by_zpm = {
            str(pm.get("zpm_id") or pm.get("partition_id")): _record_id(pm) for pm in self.pms
        }
        self.rng = random.Random(self.config.seed)
        self.cache: dict[tuple[int, ...], Evaluation] = {}
        self.evaluation_order: list[Evaluation] = []
        self._validate_inputs()
        self.candidates, self.candidate_generation = self._generate_candidates()
        self.neighbors = self._candidate_neighbors()

    def _validate_inputs(self) -> None:
        if not self.pms or not self.nros or not self.bpes or not self.roads:
            raise ValueError("Task-6 input must contain PM, NRO, BPE and road records")
        if len(set(self.zpm_ids)) != len(self.zpm_ids) or "" in self.zpm_ids:
            raise ValueError("Each PM must have one unique zpm_id/partition_id")
        missing_regions = sorted(set(self.zpm_ids) - set(self.regions))
        if missing_regions:
            raise ValueError(f"Task-2 region result is missing ZPM polygons: {missing_regions}")
        empty_regions = sorted(zpm_id for zpm_id in self.zpm_ids if not self.polygons[zpm_id])
        if empty_regions:
            raise ValueError(f"ZPM regions have no usable polygons: {empty_regions}")
        known_pm_ids = set(self.pm_by_id)
        invalid_bpes = [
            _record_id(bpe)
            for bpe in self.bpes
            if str(bpe.get("pm_id") or "") not in known_pm_ids
            or str(bpe.get("zpm_id") or "") not in self.regions
        ]
        if invalid_bpes:
            raise ValueError(f"BPE records have invalid fixed PM/ZPM relations: {invalid_bpes[:5]}")
        imb_aliases: dict[str, str] = {}
        for imb in self.imbs:
            canonical = _record_id(imb)
            for name in ("id", "code", "CODE"):
                alias = imb.get(name)
                if alias not in (None, ""):
                    imb_aliases[str(alias)] = canonical
        assigned_raw = [str(item) for pm in self.pms for item in (pm.get("assigned_imb_ids") or [])]
        unknown_assigned = sorted(set(assigned_raw) - set(imb_aliases))
        if unknown_assigned:
            raise ValueError(f"Task-6 PM assignments reference unknown IMBs: {unknown_assigned[:5]}")
        assigned = [imb_aliases[item] for item in assigned_raw]
        if len(assigned) != len(set(assigned)):
            raise ValueError("Task-6 fixed PM assignments contain duplicate IMB ids")
        expected = {_record_id(imb) for imb in self.imbs}
        if expected and set(assigned) != expected:
            raise ValueError("Task-6 PM assignments do not cover the same IMB set as imb_points")

    def _special_projection_ratios(
        self,
        first: Point,
        second: Point,
        bpes: list[dict[str, Any]],
        pm: dict[str, Any],
    ) -> dict[float, set[str]]:
        result: dict[float, set[str]] = {}
        for bpe in bpes:
            ratio = round(project_ratio(point(bpe), first, second), 12)
            result.setdefault(ratio, set()).add("bpe_projection")
        ratio = round(project_ratio(point(pm), first, second), 12)
        result.setdefault(ratio, set()).add("original_pm_projection")
        return result

    def _generate_candidates(self) -> tuple[dict[str, list[PMCandidate]], dict[str, Any]]:
        raw_by_zpm: dict[str, dict[tuple[int, int], dict[str, Any]]] = {
            zpm_id: {} for zpm_id in self.zpm_ids
        }
        road_segment_count = 0
        for road_index, road in enumerate(self.roads, 1):
            if not _road_usable(road):
                continue
            road_id = str(road.get("id") or road.get("code") or road.get("CODE") or f"ROAD_{road_index:04d}")
            coordinates = _road_points(road)
            for road_part, (first, second) in enumerate(zip(coordinates, coordinates[1:]), 1):
                length = metres(first, second)
                if length < 0.05:
                    continue
                road_segment_count += 1
                regular_count = max(1, math.ceil(length / self.config.candidate_spacing_m))
                regular = {round(index / regular_count, 12) for index in range(regular_count + 1)}
                for zpm_id in self.zpm_ids:
                    polygons = self.polygons[zpm_id]
                    bpes = [bpe for bpe in self.bpes if str(bpe.get("zpm_id") or "") == zpm_id]
                    pm = self.pm_by_id[self.pm_id_by_zpm[zpm_id]]
                    sources: dict[float, set[str]] = {ratio: {"regular_road_sample"} for ratio in regular}
                    for ratio in polygon_intersection_ratios(first, second, polygons):
                        sources.setdefault(round(ratio, 12), set()).add("zpm_boundary_intersection")
                    for ratio, labels in self._special_projection_ratios(first, second, bpes, pm).items():
                        sources.setdefault(ratio, set()).update(labels)
                    ordered = sorted(sources)
                    for start, end in zip(ordered, ordered[1:]):
                        midpoint = round((start + end) / 2.0, 12)
                        sources.setdefault(midpoint, set()).add("inside_interval_midpoint")
                    for ratio, labels in sources.items():
                        road_entry = interpolate(first, second, ratio)
                        lateral_offsets = {0.0}
                        offset = self.config.candidate_lateral_spacing_m
                        while offset < self.config.pm_road_access_radius_m - 1e-9:
                            lateral_offsets.update((-offset, offset))
                            offset += self.config.candidate_lateral_spacing_m
                        if self.config.pm_road_access_radius_m > 0:
                            lateral_offsets.update(
                                (-self.config.pm_road_access_radius_m, self.config.pm_road_access_radius_m)
                            )
                        for lateral_offset in sorted(lateral_offsets):
                            candidate_point = offset_perpendicular(first, second, ratio, lateral_offset)
                            if not point_with_clearance_in_polygons(
                                candidate_point, polygons, self.config.zpm_boundary_clearance_m
                            ):
                                continue
                            access_distance = metres(candidate_point, road_entry)
                            if access_distance > self.config.pm_road_access_radius_m + 1e-6:
                                continue
                            metric = utm29(*candidate_point)
                            key = round(metric[0] * 10), round(metric[1] * 10)
                            candidate_labels = set(labels)
                            candidate_labels.add(
                                "road_centerline" if abs(lateral_offset) <= 1e-9 else "road_buffer_offset"
                            )
                            existing = raw_by_zpm[zpm_id].get(key)
                            if existing is None or access_distance < existing["road_access_distance_m"]:
                                raw_by_zpm[zpm_id][key] = {
                                    "point": candidate_point,
                                    "road_entry": road_entry,
                                    "road_access_distance_m": access_distance,
                                    "road_id": road_id,
                                    "road_part": road_part,
                                    "ratio": ratio,
                                    "sources": candidate_labels,
                                }
                            else:
                                existing["sources"].update(candidate_labels)

        selected_by_zpm: dict[str, list[PMCandidate]] = {}
        generation_report: dict[str, Any] = {"road_segment_count": road_segment_count, "zpm": {}}
        for zpm_id in self.zpm_ids:
            bpes = [bpe for bpe in self.bpes if str(bpe.get("zpm_id") or "") == zpm_id]
            if not bpes:
                raise ValueError(f"ZPM {zpm_id} has no BPE demand points")
            total_weight = sum(max(1.0, float(bpe.get("demand") or 1.0)) for bpe in bpes)
            ranked: list[PMCandidate] = []
            for index, item in enumerate(raw_by_zpm[zpm_id].values(), 1):
                candidate_point = item["point"]
                distances = [metres(candidate_point, point(bpe)) for bpe in bpes]
                score = sum(
                    max(1.0, float(bpe.get("demand") or 1.0)) * distance
                    for bpe, distance in zip(bpes, distances)
                ) / total_weight
                ranked.append(
                    PMCandidate(
                        id=f"{zpm_id}-RAW-{index:05d}",
                        zpm_id=zpm_id,
                        x=candidate_point[0],
                        y=candidate_point[1],
                        road_id=item["road_id"],
                        road_part=int(item["road_part"]),
                        segment_ratio=float(item["ratio"]),
                        road_entry_x=float(item["road_entry"][0]),
                        road_entry_y=float(item["road_entry"][1]),
                        road_access_distance_m=round(float(item["road_access_distance_m"]), 3),
                        spatial_score_m=round(score, 3),
                        max_bpe_distance_m=round(max(distances), 3),
                        rank=0,
                        sources=tuple(sorted(item["sources"])),
                    )
                )
            if not ranked:
                raise ValueError(f"ZPM {zpm_id} has no legal candidate within the road-access buffer")
            ranked.sort(key=lambda item: (item.spatial_score_m, item.max_bpe_distance_m, item.road_id, item.x, item.y))
            limit = min(self.config.candidate_limit_per_zpm, len(ranked))
            core_count = max(1, round(limit * (1.0 - self.config.diversity_fraction)))
            selected: list[PMCandidate] = []
            for candidate in ranked:
                if all(
                    metres(candidate.coordinate, other.coordinate) >= self.config.candidate_min_separation_m
                    for other in selected
                ):
                    selected.append(candidate)
                if len(selected) >= core_count:
                    break
            remaining = [candidate for candidate in ranked if candidate not in selected]
            while len(selected) < limit and remaining:
                choice = max(
                    remaining,
                    key=lambda item: (
                        min(metres(item.coordinate, other.coordinate) for other in selected),
                        -item.spatial_score_m,
                    ),
                )
                selected.append(choice)
                remaining = [
                    candidate
                    for candidate in remaining
                    if candidate != choice
                    and metres(candidate.coordinate, choice.coordinate) >= self.config.candidate_min_separation_m
                ]
            selected.sort(key=lambda item: (item.spatial_score_m, item.max_bpe_distance_m, item.road_id))
            selected = [
                replace(item, id=f"PM-CAND-{zpm_id}-{index:03d}", rank=index)
                for index, item in enumerate(selected, 1)
            ]
            selected_by_zpm[zpm_id] = selected
            generation_report["zpm"][zpm_id] = {
                "raw_candidate_count": len(ranked),
                "selected_candidate_count": len(selected),
                "bpe_count": len(bpes),
                "pm_road_access_radius_m": self.config.pm_road_access_radius_m,
                "initial_candidate_id": selected[0].id,
                "initial_spatial_score_m": selected[0].spatial_score_m,
            }
        return selected_by_zpm, generation_report

    def _candidate_neighbors(self) -> dict[str, dict[int, list[int]]]:
        result: dict[str, dict[int, list[int]]] = {}
        for zpm_id, candidates in self.candidates.items():
            result[zpm_id] = {}
            for index, candidate in enumerate(candidates):
                ordered = sorted(
                    (other_index for other_index in range(len(candidates)) if other_index != index),
                    key=lambda other_index: metres(candidate.coordinate, candidates[other_index].coordinate),
                )
                result[zpm_id][index] = ordered[: self.config.local_neighbor_count]
        return result

    def _candidate_ids(self, chromosome: tuple[int, ...]) -> tuple[str, ...]:
        return tuple(
            self.candidates[zpm_id][gene].id for zpm_id, gene in zip(self.zpm_ids, chromosome)
        )

    def _data_for_chromosome(self, chromosome: tuple[int, ...]) -> dict[str, Any]:
        data = copy.deepcopy(self.base_data)
        selected = {
            self.pm_id_by_zpm[zpm_id]: self.candidates[zpm_id][gene]
            for zpm_id, gene in zip(self.zpm_ids, chromosome)
        }

        def update_records(records: list[dict[str, Any]]) -> None:
            for pm in records:
                pm_id = _record_id(pm)
                candidate = selected.get(pm_id)
                if candidate is None:
                    continue
                pm.update(
                    {
                        "x": candidate.x,
                        "y": candidate.y,
                        "road_id": candidate.road_id,
                        "road_entry_x": candidate.road_entry_x,
                        "road_entry_y": candidate.road_entry_y,
                        "road_access_distance_m": candidate.road_access_distance_m,
                        "task3_candidate_id": candidate.id,
                        "source": "task3_road_buffer_constrained_ga",
                    }
                )

        update_records(data.get("pm_sites") or [])
        remote = data.get("remote_sensing") or {}
        update_records(remote.get("pm_sites") or [])
        data.setdefault("meta", {})["task3_pm_optimization"] = {
            "algorithm": "constraint_first_memetic_ga_task6_feedback",
            "candidate_ids": list(self._candidate_ids(chromosome)),
            "task2_regions": str(self.task2_regions_path),
        }
        planning = data.setdefault("planning", {})
        planning["pm_placement_mode"] = "task3_road_buffer_constrained_ga"
        planning["pm_road_access_radius_m"] = self.config.pm_road_access_radius_m
        return data

    def _route_owner_maps(self) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
        group_to_pm = {_record_id(group): str(group.get("pm_id") or "") for group in self.groups}
        bpe_to_pm = {_record_id(bpe): str(bpe.get("pm_id") or "") for bpe in self.bpes}
        pbo_to_pm = {
            _record_id(pbo): str(pbo.get("pm_id") or bpe_to_pm.get(str(pbo.get("bpe_id") or pbo.get("bpe_code") or ""), ""))
            for pbo in self.pbos
        }
        return group_to_pm, bpe_to_pm, pbo_to_pm

    def _route_owner(
        self,
        route: dict[str, Any],
        group_to_pm: dict[str, str],
        bpe_to_pm: dict[str, str],
        pbo_to_pm: dict[str, str],
    ) -> str:
        route_type = str(route.get("route_type") or "")
        source = str(route.get("from_code") or "")
        target = str(route.get("to_code") or "")
        if route_type == "PM_TO_GROUP_ENTRY":
            return source
        if route_type == "GROUP_ENTRY_TO_BPE":
            return group_to_pm.get(source, bpe_to_pm.get(target, ""))
        if route_type in {"BPE_TO_BPE", "BPE_TO_PBO"}:
            return bpe_to_pm.get(source, pbo_to_pm.get(target, ""))
        return ""

    def _main_reentry_length(self, route: dict[str, Any], polygons: list[list[Point]]) -> float:
        coordinates = [point(item) for item in route.get("coordinates") or []]
        entered = False
        reexit = 0.0
        for first, second in zip(coordinates, coordinates[1:]):
            ratios = polygon_intersection_ratios(first, second, polygons)
            length = metres(first, second)
            for start, end in zip(ratios, ratios[1:]):
                if end - start <= 1e-12:
                    continue
                inside = point_in_polygons(interpolate(first, second, (start + end) / 2.0), polygons)
                if inside:
                    entered = True
                elif entered:
                    reexit += length * (end - start)
        return reexit

    def _metrics(self, plan: dict[str, Any], data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        validation = plan.get("validation") or {}
        unresolved = list(validation.get("unresolved") or validation.get("unresolved_access_routes") or [])
        hard_violations = [f"unresolved:{item.get('id') or item.get('reason') or index}" for index, item in enumerate(unresolved, 1)]
        for name in ("bpe_reachability", "pbo_reachability"):
            for item_id, record in (validation.get(name) or {}).items():
                if not record.get("reachable"):
                    hard_violations.append(f"{name}:{item_id}")

        pm_records = {_record_id(pm): pm for pm in data.get("pm_sites") or []}
        for zpm_id, pm_id in self.pm_id_by_zpm.items():
            pm = pm_records.get(pm_id)
            if pm is None:
                hard_violations.append(f"missing_pm:{pm_id}")
            elif not point_with_clearance_in_polygons(
                point(pm), self.polygons[zpm_id], self.config.zpm_boundary_clearance_m
            ):
                hard_violations.append(f"pm_outside_zpm:{pm_id}:{zpm_id}")

        pm_access_validation = validation.get("pm_road_access") or {}
        for pm_id in self.pm_by_id:
            access = pm_access_validation.get(pm_id) or {}
            distance = access.get("distance_m")
            if (
                access.get("status") != "PASS"
                or distance is None
                or float(distance) > self.config.pm_road_access_radius_m + 1e-6
            ):
                hard_violations.append(f"pm_road_access:{pm_id}")

        routes = list(plan.get("duct_routes") or plan.get("routes") or [])
        edge_lengths: dict[str, float] = {}
        nonroad_length = 0.0
        for route in routes:
            if str(route.get("route_type") or "") in ROAD_ROUTE_TYPES:
                for edge_id, length in _route_edge_lengths(route).items():
                    edge_lengths.setdefault(edge_id, length)
            else:
                nonroad_length += float(route.get("length_m") or 0.0)
        road_unique_length = sum(edge_lengths.values())

        group_to_pm, bpe_to_pm, pbo_to_pm = self._route_owner_maps()
        zone_metrics: dict[str, dict[str, float]] = {
            zpm_id: {"local_length_m": 0.0, "outside_length_m": 0.0, "outside_ratio": 0.0, "main_reexit_length_m": 0.0}
            for zpm_id in self.zpm_ids
        }
        zpm_by_pm = {pm_id: zpm_id for zpm_id, pm_id in self.pm_id_by_zpm.items()}
        for route in routes:
            route_type = str(route.get("route_type") or "")
            if route_type in LOCAL_ROUTE_TYPES:
                owner = self._route_owner(route, group_to_pm, bpe_to_pm, pbo_to_pm)
                zpm_id = zpm_by_pm.get(owner)
                if zpm_id:
                    total, outside = polyline_outside_length(route.get("coordinates") or [], self.polygons[zpm_id])
                    zone_metrics[zpm_id]["local_length_m"] += total
                    zone_metrics[zpm_id]["outside_length_m"] += outside
            elif route_type == "NRO_TO_PM_MAIN":
                zpm_id = zpm_by_pm.get(str(route.get("to_code") or ""))
                if zpm_id:
                    zone_metrics[zpm_id]["main_reexit_length_m"] += self._main_reentry_length(route, self.polygons[zpm_id])

        for record in zone_metrics.values():
            total = record["local_length_m"]
            record["outside_ratio"] = record["outside_length_m"] / total if total > 1e-9 else 0.0
            for key in list(record):
                record[key] = round(record[key], 6)

        outside_total = sum(record["outside_length_m"] for record in zone_metrics.values())
        pm_access_fibers = list(plan.get("pm_access_fibers") or [])
        pm_access_lengths = [float(item.get("length_m") or 0.0) for item in pm_access_fibers]
        metrics = {
            "task6_status": validation.get("status", "UNKNOWN"),
            "unresolved_count": len(unresolved),
            "hard_violation_count": len(hard_violations),
            "main_trunk_unique_length_m": float((plan.get("meta") or {}).get("main_trunk_unique_length_m") or 0.0),
            "road_unique_length_m": round(road_unique_length, 6),
            "nonroad_route_length_m": round(nonroad_length, 6),
            "installed_duct_length_m": round(road_unique_length + nonroad_length, 6),
            "pm_access_fiber_length_m": round(sum(pm_access_lengths), 6),
            "max_pm_road_access_distance_m": round(max(pm_access_lengths, default=0.0), 6),
            "construction_cost": float((plan.get("meta") or {}).get("cost") or 0.0),
            "outside_zpm_length_m": round(outside_total, 6),
            "max_outside_zpm_ratio": round(max((record["outside_ratio"] for record in zone_metrics.values()), default=0.0), 9),
            "main_reexit_length_m": round(sum(record["main_reexit_length_m"] for record in zone_metrics.values()), 6),
            "zpm_route_containment": zone_metrics,
        }
        return metrics, hard_violations

    @staticmethod
    def _fitness(metrics: dict[str, Any], hard_violations: list[str]) -> tuple[float, ...]:
        return (
            float(bool(hard_violations)),
            float(len(hard_violations)),
            float(metrics.get("max_outside_zpm_ratio", math.inf)),
            float(metrics.get("outside_zpm_length_m", math.inf)),
            float(metrics.get("installed_duct_length_m", math.inf)),
            float(metrics.get("construction_cost", math.inf)),
        )

    def _evaluate_data(
        self,
        data: dict[str, Any],
        chromosome: tuple[int, ...],
        candidate_ids: tuple[str, ...],
    ) -> tuple[Evaluation, dict[str, Any] | None]:
        try:
            plan = LayeredPlanner(data, base_dir=self.task6_input_path.parent).plan()
            metrics, hard_violations = self._metrics(plan, data)
            evaluation = Evaluation(
                chromosome=chromosome,
                candidate_ids=candidate_ids,
                metrics=metrics,
                hard_violations=hard_violations,
                fitness=self._fitness(metrics, hard_violations),
            )
            return evaluation, plan
        except Exception as exc:
            metrics = {
                "task6_status": "ERROR",
                "unresolved_count": 1,
                "hard_violation_count": 1,
                "max_outside_zpm_ratio": math.inf,
                "outside_zpm_length_m": math.inf,
                "installed_duct_length_m": math.inf,
                "construction_cost": math.inf,
            }
            evaluation = Evaluation(
                chromosome=chromosome,
                candidate_ids=candidate_ids,
                metrics=metrics,
                hard_violations=["task6_evaluation_error"],
                fitness=self._fitness(metrics, ["task6_evaluation_error"]),
                error=f"{type(exc).__name__}: {exc}",
            )
            return evaluation, None

    def evaluate(self, chromosome: tuple[int, ...]) -> Evaluation:
        if chromosome in self.cache:
            return self.cache[chromosome]
        data = self._data_for_chromosome(chromosome)
        evaluation, _ = self._evaluate_data(data, chromosome, self._candidate_ids(chromosome))
        self.cache[chromosome] = evaluation
        self.evaluation_order.append(evaluation)
        return evaluation

    def _initial_population(self, initial: tuple[int, ...], original_snapped: tuple[int, ...]) -> list[tuple[int, ...]]:
        population = [initial]
        if original_snapped != initial:
            population.append(original_snapped)
        for zpm_index, zpm_id in enumerate(self.zpm_ids):
            for gene in range(1, min(4, len(self.candidates[zpm_id]))):
                value = list(initial)
                value[zpm_index] = gene
                population.append(tuple(value))
        space_size = math.prod(len(self.candidates[zpm_id]) for zpm_id in self.zpm_ids)
        seen = set(population)
        while len(population) < min(self.config.population_size, space_size):
            chromosome = tuple(self.rng.randrange(len(self.candidates[zpm_id])) for zpm_id in self.zpm_ids)
            if chromosome not in seen:
                seen.add(chromosome)
                population.append(chromosome)
        return population

    def _tournament(self, ranked: list[Evaluation]) -> tuple[int, ...]:
        pool = [self.rng.choice(ranked) for _ in range(self.config.tournament_size)]
        return min(pool, key=lambda item: item.fitness).chromosome

    def _crossover(self, first: tuple[int, ...], second: tuple[int, ...]) -> tuple[int, ...]:
        if self.rng.random() > self.config.crossover_rate:
            return first
        return tuple(left if self.rng.random() < 0.5 else right for left, right in zip(first, second))

    def _mutate(self, chromosome: tuple[int, ...]) -> tuple[int, ...]:
        value = list(chromosome)
        changed = False
        for index, zpm_id in enumerate(self.zpm_ids):
            if self.rng.random() >= self.config.mutation_rate:
                continue
            nearby = self.neighbors[zpm_id][value[index]]
            if nearby and self.rng.random() < 0.8:
                value[index] = self.rng.choice(nearby)
            else:
                value[index] = self.rng.randrange(len(self.candidates[zpm_id]))
            changed = True
        if not changed and self.rng.random() < self.config.mutation_rate:
            index = self.rng.randrange(len(value))
            zpm_id = self.zpm_ids[index]
            value[index] = self.rng.randrange(len(self.candidates[zpm_id]))
        return tuple(value)

    def _next_population(self, ranked: list[Evaluation]) -> list[tuple[int, ...]]:
        target = self.config.population_size
        next_population = [item.chromosome for item in ranked[: self.config.elite_count]]
        seen = set(next_population)
        attempts = 0
        while len(next_population) < target and attempts < target * 30:
            attempts += 1
            child = self._crossover(self._tournament(ranked), self._tournament(ranked))
            child = self._mutate(child)
            if child not in seen:
                seen.add(child)
                next_population.append(child)
        while len(next_population) < target:
            child = tuple(self.rng.randrange(len(self.candidates[zpm_id])) for zpm_id in self.zpm_ids)
            if child not in seen:
                seen.add(child)
                next_population.append(child)
        return next_population

    def _history_row(self, generation: int, best: Evaluation, population: list[Evaluation]) -> dict[str, Any]:
        feasible = sum(not item.hard_violations for item in population)
        return {
            "generation": generation,
            "evaluations": len(self.evaluation_order),
            "population_size": len(population),
            "feasible_individuals": feasible,
            "best_candidate_ids": "|".join(best.candidate_ids),
            "hard_violation_count": len(best.hard_violations),
            "max_outside_zpm_ratio": best.metrics.get("max_outside_zpm_ratio"),
            "outside_zpm_length_m": best.metrics.get("outside_zpm_length_m"),
            "installed_duct_length_m": best.metrics.get("installed_duct_length_m"),
            "construction_cost": best.metrics.get("construction_cost"),
        }

    def _local_search(self, best: Evaluation) -> Evaluation:
        current = best
        improved = True
        while improved:
            improved = False
            for index, zpm_id in enumerate(self.zpm_ids):
                for gene in self.neighbors[zpm_id][current.chromosome[index]]:
                    value = list(current.chromosome)
                    value[index] = gene
                    candidate = self.evaluate(tuple(value))
                    if candidate.fitness < current.fitness:
                        current = candidate
                        improved = True
                        break
                if improved:
                    break
        return current

    def run(self) -> OptimizationResult:
        initial_chromosome = tuple(0 for _ in self.zpm_ids)
        original_snapped = tuple(
            min(
                range(len(self.candidates[zpm_id])),
                key=lambda index: metres(
                    point(self.pm_by_id[self.pm_id_by_zpm[zpm_id]]),
                    self.candidates[zpm_id][index].coordinate,
                ),
            )
            for zpm_id in self.zpm_ids
        )
        original, _ = self._evaluate_data(
            copy.deepcopy(self.base_data),
            tuple(-1 for _ in self.zpm_ids),
            tuple(f"ORIGINAL::{self.pm_id_by_zpm[zpm_id]}" for zpm_id in self.zpm_ids),
        )
        population = self._initial_population(initial_chromosome, original_snapped)
        history: list[dict[str, Any]] = []
        overall_best: Evaluation | None = None
        stagnant = 0
        for generation in range(self.config.generations + 1):
            ranked = sorted((self.evaluate(chromosome) for chromosome in population), key=lambda item: item.fitness)
            best = ranked[0]
            history.append(self._history_row(generation, best, ranked))
            if overall_best is None or best.fitness < overall_best.fitness:
                overall_best = best
                stagnant = 0
            else:
                stagnant += 1
            if generation >= self.config.generations or stagnant >= self.config.patience:
                break
            population = self._next_population(ranked)
        assert overall_best is not None
        optimized = self._local_search(overall_best)
        if optimized.fitness < overall_best.fitness:
            history.append(self._history_row(len(history), optimized, [optimized]))
        optimized_input = self._data_for_chromosome(optimized.chromosome)
        final_evaluation, final_plan = self._evaluate_data(
            optimized_input, optimized.chromosome, optimized.candidate_ids
        )
        if final_plan is None or final_evaluation.fitness != optimized.fitness:
            raise RuntimeError("Final Task-6 verification did not reproduce the selected GA evaluation")
        initial = self.evaluate(initial_chromosome)
        final_plan.setdefault("meta", {})["pm_optimization"] = {
            "algorithm": "constraint_first_memetic_ga_task6_feedback",
            "seed": self.config.seed,
            "evaluation_count": len(self.evaluation_order),
            "candidate_ids": list(optimized.candidate_ids),
            "road_unique_length_m": optimized.metrics["road_unique_length_m"],
            "installed_duct_length_m": optimized.metrics["installed_duct_length_m"],
            "outside_zpm_length_m": optimized.metrics["outside_zpm_length_m"],
            "pm_access_fiber_length_m": optimized.metrics["pm_access_fiber_length_m"],
            "max_pm_road_access_distance_m": optimized.metrics["max_pm_road_access_distance_m"],
        }
        final_plan.setdefault("validation", {})["pm_site_constraints"] = {
            "status": "PASS" if not optimized.hard_violations else "FAIL",
            "hard_violations": optimized.hard_violations,
            "all_pm_inside_assigned_zpm": not any(item.startswith("pm_outside_zpm") for item in optimized.hard_violations),
            "all_pm_within_road_access_radius": not any(
                item.startswith("pm_road_access") for item in optimized.hard_violations
            ),
            "pm_road_access_radius_m": self.config.pm_road_access_radius_m,
            "max_pm_road_access_distance_m": optimized.metrics["max_pm_road_access_distance_m"],
            "zpm_route_containment": optimized.metrics["zpm_route_containment"],
        }
        return OptimizationResult(
            zpm_ids=self.zpm_ids,
            candidates=self.candidates,
            candidate_generation=self.candidate_generation,
            config=self.config,
            original=original,
            initial=initial,
            optimized=optimized,
            history=history,
            evaluations=list(self.evaluation_order),
            optimized_input=optimized_input,
            task6_plan=final_plan,
        )

    def write_outputs(self, result: OptimizationResult, output_dir: str | Path) -> dict[str, Path]:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        paths = {
            "fixed_plan": output / "pm_fixed_plan.json",
            "candidates": output / "pm_candidates.geojson",
            "history": output / "pm_optimization_history.csv",
            "evaluations": output / "pm_evaluations.csv",
            "report": output / "pm_optimization_report.json",
            "task6_input": output / "task6_optimized_input.json",
            "task6_plan": output / "task6_optimized_plan.json",
            "validation": output / "validation_report.json",
        }
        selected = {
            zpm_id: result.candidates[zpm_id][gene]
            for zpm_id, gene in zip(result.zpm_ids, result.optimized.chromosome)
        }
        initial = {zpm_id: result.candidates[zpm_id][0] for zpm_id in result.zpm_ids}
        optimized_pms = {_record_id(pm): pm for pm in result.optimized_input.get("pm_sites") or []}
        pm_sites = []
        for zpm_id in result.zpm_ids:
            pm_id = self.pm_id_by_zpm[zpm_id]
            pm = copy.deepcopy(optimized_pms[pm_id])
            candidate = selected[zpm_id]
            pm.update(
                {
                    "id": pm_id,
                    "code": pm_id,
                    "node_id": candidate.id,
                    "zpm_id": zpm_id,
                    "partition_id": zpm_id,
                    "candidate_id": candidate.id,
                    "road_id": candidate.road_id,
                    "road_entry_x": candidate.road_entry_x,
                    "road_entry_y": candidate.road_entry_y,
                    "road_access_distance_m": candidate.road_access_distance_m,
                    "source": "task3_road_buffer_constrained_ga",
                }
            )
            pm_sites.append(pm)
        fixed_plan = {
            "schema_version": "task3.fixed_pm.v1",
            "source": {
                "task2_regions": str(self.task2_regions_path),
                "task6_base_input": str(self.task6_input_path),
            },
            "nro_sites": copy.deepcopy(self.nros),
            "pm_sites": pm_sites,
            "counts": {
                "nro": len(self.nros),
                "pm": len(pm_sites),
                "imb": len({_record_id(imb) for imb in self.imbs}),
                "total_ports": sum(int(imb.get("ports") or imb.get("demand") or 0) for imb in self.imbs),
            },
            "selection_mode": "road_buffer_constrained_memetic_ga_task6_feedback",
            "optimization": {
                "candidate_ids": list(result.optimized.candidate_ids),
                "fitness": list(result.optimized.fitness),
                "metrics": result.optimized.metrics,
            },
        }
        _write_json(paths["fixed_plan"], fixed_plan)
        _write_json(paths["task6_input"], result.optimized_input)
        _write_json(paths["task6_plan"], result.task6_plan)

        features = []
        optimized_ids = set(result.optimized.candidate_ids)
        initial_ids = {candidate.id for candidate in initial.values()}
        for zpm_id in result.zpm_ids:
            for candidate in result.candidates[zpm_id]:
                properties = candidate.to_dict()
                properties.update(
                    {
                        "selected_initial": candidate.id in initial_ids,
                        "selected_optimized": candidate.id in optimized_ids,
                    }
                )
                properties.pop("x")
                properties.pop("y")
                features.append(
                    {
                        "type": "Feature",
                        "properties": properties,
                        "geometry": {"type": "Point", "coordinates": [candidate.x, candidate.y]},
                    }
                )
        _write_json(paths["candidates"], {"type": "FeatureCollection", "features": features})
        _write_csv(paths["history"], result.history, list(result.history[0]))
        evaluation_rows = []
        for sequence, evaluation in enumerate(result.evaluations, 1):
            evaluation_rows.append(
                {
                    "sequence": sequence,
                    "chromosome": "|".join(str(item) for item in evaluation.chromosome),
                    "candidate_ids": "|".join(evaluation.candidate_ids),
                    "task6_status": evaluation.metrics.get("task6_status"),
                    "hard_violation_count": len(evaluation.hard_violations),
                    "max_outside_zpm_ratio": evaluation.metrics.get("max_outside_zpm_ratio"),
                    "outside_zpm_length_m": evaluation.metrics.get("outside_zpm_length_m"),
                    "installed_duct_length_m": evaluation.metrics.get("installed_duct_length_m"),
                    "road_unique_length_m": evaluation.metrics.get("road_unique_length_m"),
                    "pm_access_fiber_length_m": evaluation.metrics.get("pm_access_fiber_length_m"),
                    "max_pm_road_access_distance_m": evaluation.metrics.get("max_pm_road_access_distance_m"),
                    "construction_cost": evaluation.metrics.get("construction_cost"),
                    "error": evaluation.error or "",
                }
            )
        _write_csv(paths["evaluations"], evaluation_rows, list(evaluation_rows[0]))

        def change(reference: Evaluation, metric: str) -> float | None:
            before = float(reference.metrics.get(metric) or 0.0)
            after = float(result.optimized.metrics.get(metric) or 0.0)
            return round(after - before, 6) if math.isfinite(before) and math.isfinite(after) else None

        report = {
            "schema_version": "task3.pm_optimization.v1",
            "algorithm": "constraint_first_memetic_ga_task6_feedback",
            "configuration": asdict(result.config),
            "data_contract": {
                "task2_regions": str(self.task2_regions_path),
                "task6_base_input": str(self.task6_input_path),
                "fixed_assignments": True,
            },
            "candidate_generation": result.candidate_generation,
            "original": result.original.to_dict(result.zpm_ids),
            "initial_road_median": result.initial.to_dict(result.zpm_ids),
            "optimized": result.optimized.to_dict(result.zpm_ids),
            "changes": {
                "versus_original_installed_duct_length_m": change(result.original, "installed_duct_length_m"),
                "versus_original_outside_zpm_length_m": change(result.original, "outside_zpm_length_m"),
                "versus_initial_installed_duct_length_m": change(result.initial, "installed_duct_length_m"),
                "versus_initial_outside_zpm_length_m": change(result.initial, "outside_zpm_length_m"),
            },
            "generations_completed": len(result.history) - 1,
            "task6_evaluation_count": len(result.evaluations) + 2,
            "history": result.history,
        }
        _write_json(paths["report"], report)
        validation = {
            "warnings": [],
            "validation": {
                "passed": not result.optimized.hard_violations,
                "task6_status": result.optimized.metrics.get("task6_status"),
                "all_pm_inside_assigned_zpm": not any(
                    item.startswith("pm_outside_zpm") for item in result.optimized.hard_violations
                ),
                "fixed_assignment_preserved": True,
                "hard_violations": result.optimized.hard_violations,
                "candidate_counts": {
                    zpm_id: len(result.candidates[zpm_id]) for zpm_id in result.zpm_ids
                },
                "metrics": result.optimized.metrics,
            },
        }
        _write_json(paths["validation"], validation)
        self._write_compatible_tables(output, result, pm_sites, selected)
        return paths

    def _write_compatible_tables(
        self,
        output: Path,
        result: OptimizationResult,
        pm_sites: list[dict[str, Any]],
        selected: dict[str, PMCandidate],
    ) -> None:
        site_fields = [
            "fid", "CODE", "REF_PLAQUE", "REF_NRO", "TYPE", "FABRIQUANT", "REF_PRODUI",
            "MODE_POSE", "STATUT", "PROPRETAI", "GESTIONNAI", "ADRESSE", "COMMUNE",
            "CODE_POSTA", "X", "Y",
        ]
        site_rows = []
        for index, nro in enumerate(self.nros, 1):
            nro_id = _record_id(nro)
            site_rows.append(
                {"fid": index, "CODE": nro_id, "REF_NRO": nro_id, "TYPE": "NRO", "STATUT": nro.get("status") or "EXISTANT", "X": nro.get("x"), "Y": nro.get("y")}
            )
        for pm in pm_sites:
            site_rows.append(
                {"fid": len(site_rows) + 1, "CODE": _record_id(pm), "REF_NRO": pm.get("nro_id"), "TYPE": "PM", "MODE_POSE": "CHAMBRE", "STATUT": pm.get("status") or "PROJET", "X": pm["x"], "Y": pm["y"]}
            )
        _write_csv(output / "SITE_selected.csv", site_rows, site_fields)

        zpm_fields = ["fid", "CODE", "NOM", "REF_PLAQUE", "REF_PM", "REF_NRO", "STATUT", "NIB_PRISES", "COMMENT"]
        zpm_rows = []
        for index, zpm_id in enumerate(result.zpm_ids, 1):
            pm = next(item for item in pm_sites if str(item.get("zpm_id")) == zpm_id)
            zpm_rows.append(
                {
                    "fid": index,
                    "CODE": zpm_id,
                    "NOM": zpm_id,
                    "REF_PM": _record_id(pm),
                    "REF_NRO": pm.get("nro_id"),
                    "STATUT": "PROJET",
                    "NIB_PRISES": pm.get("demand") or self.regions[zpm_id].get("total_ports") or 0,
                    "COMMENT": f"candidate={selected[zpm_id].id}; road={selected[zpm_id].road_id}",
                }
            )
        _write_csv(output / "ZPM_selected.csv", zpm_rows, zpm_fields)

        znro_fields = ["fid", "CODE", "NOM", "REF_PLAQUE", "REF_NRO", "STATUT", "NIB_PRISES", "COMMENT"]
        parent_ids = sorted({str(self.regions[zpm_id].get("parent_znro_id") or "") for zpm_id in result.zpm_ids})
        total_ports = sum(int(imb.get("ports") or imb.get("demand") or 0) for imb in self.imbs)
        znro_rows = []
        for index, parent_id in enumerate(parent_ids, 1):
            nro = self.nros[min(index - 1, len(self.nros) - 1)]
            znro_rows.append({"fid": index, "CODE": parent_id, "NOM": parent_id, "REF_NRO": _record_id(nro), "STATUT": "PROJET", "NIB_PRISES": total_ports})
        _write_csv(output / "ZNRO_selected.csv", znro_rows, znro_fields)

        ptech_fields = [
            "fid", "CODE", "NOM", "REF_PLAQUE", "TYPE", "NATURE", "HAUTEUR_AP", "TYPE_APPUI",
            "EFFORT_APP", "NB_BOITIER", "STATUT", "PROPRETAI", "GESTIONNAI", "ADRESSE", "VILLE",
            "CODE_POSTA", "X", "Y", "COMMENT",
        ]
        ptech_rows = [
            {"fid": index, "CODE": candidate.id, "NOM": f"PM support {zpm_id}", "TYPE": "CHAMBRE", "NATURE": "TASK3_GA_PM_SUPPORT", "NB_BOITIER": 1, "STATUT": "PROJET", "X": candidate.x, "Y": candidate.y, "COMMENT": f"road={candidate.road_id}"}
            for index, (zpm_id, candidate) in enumerate(selected.items(), 1)
        ]
        _write_csv(output / "PTECH_selected.csv", ptech_rows, ptech_fields)

        score_fields = [
            "facility_type", "zone_code", "site_code", "candidate_code", "ptech_code", "X", "Y",
            "ref_nro", "capacity", "demand_ports", "route_cost", "construction_cost", "total_score",
            "reused", "reason", "candidate_source",
        ]
        score_rows = []
        for zpm_id, candidate in selected.items():
            pm = next(item for item in pm_sites if str(item.get("zpm_id")) == zpm_id)
            zone_metric = result.optimized.metrics["zpm_route_containment"][zpm_id]
            score_rows.append(
                {
                    "facility_type": "PM", "zone_code": zpm_id, "site_code": _record_id(pm),
                    "candidate_code": candidate.id, "ptech_code": candidate.id, "X": candidate.x, "Y": candidate.y,
                    "ref_nro": pm.get("nro_id"), "capacity": pm.get("capacity"), "demand_ports": pm.get("demand"),
                    "route_cost": zone_metric["local_length_m"], "construction_cost": 0.0,
                    "total_score": candidate.spatial_score_m, "reused": False,
                    "reason": "Selected by constraint-first memetic GA using Task-6 route feedback",
                    "candidate_source": "ROAD_SAMPLE_IN_TASK2_ZPM",
                }
            )
        _write_csv(output / "selection_scores.csv", score_rows, score_fields)
        _write_csv(output / "rejected_candidates.csv", [], ["facility_type", "zone_code", "candidate_code", "reason"])
