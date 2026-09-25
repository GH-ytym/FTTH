"""Task 5: hierarchical fiber links over the conduits planned by Task 6.

Task 5 accepts a Task-6 plan and turns its fixed service hierarchy into cable
links.  It never reserves conduit capacity: more than one cable may use the
same Task-6 conduit route by design.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from project_paths import TASK5_PRODUCTION_OUTPUT_DIR, relative_to_root


CABLE_SIZES = (24, 48, 72, 144)
LINK_STAGES = ("NRO_TO_PM", "PM_TO_BPE", "BPE_TO_PBO", "PBO_TO_IMB")
ROUTE_TYPES = {
    "NRO_TO_PM": {"NRO_TO_PM_MAIN"},
    # Task 6 may use a shared BPE conduit tree.  A PM-to-BPE physical path can
    # therefore traverse one or more BPE_TO_BPE links before reaching the
    # requested BPE; route search is intentionally endpoint-connectivity based.
    "PM_TO_BPE": {"PM_TO_BPE", "PM_TO_GROUP_ENTRY", "GROUP_ENTRY_TO_BPE", "BPE_TO_BPE"},
    "BPE_TO_PBO": {"BPE_TO_PBO"},
}


class Task5Error(ValueError):
    """Raised when a Task-6 delivery does not satisfy the link contract."""


def load_json(path: str | Path) -> tuple[dict[str, Any], Path]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise Task5Error(f"Input JSON was not found: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise Task5Error(f"Invalid JSON in {source}: {exc}") from exc
    if not isinstance(payload, dict):
        raise Task5Error("The input JSON root must be an object")
    return payload, source.parent


def resolve_path(base_dir: Path, raw_path: str | Path) -> Path:
    path = Path(raw_path).expanduser()
    return path.resolve() if path.is_absolute() else (base_dir / path).resolve()


def _records(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def record_id(record: dict[str, Any]) -> str:
    for key in ("id", "code", "CODE", "name"):
        value = _text(record.get(key))
        if value:
            return value
    return ""


def first_text(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _text(record.get(key))
        if value:
            return value
    return ""


def number(record: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = record.get(key)
        if value in (None, ""):
            continue
        try:
            return max(0, int(round(float(value))))
        except (TypeError, ValueError):
            continue
    return 0


def point(record: dict[str, Any]) -> dict[str, float] | None:
    try:
        return {"x": float(record["x"]), "y": float(record["y"])}
    except (KeyError, TypeError, ValueError):
        return None


def _distance_m(first: dict[str, float], second: dict[str, float]) -> float:
    """Approximate short WGS84 separation in metres."""
    latitude = math.radians((first["y"] + second["y"]) / 2.0)
    east = (second["x"] - first["x"]) * 111320.0 * math.cos(latitude)
    north = (second["y"] - first["y"]) * 110540.0
    return math.hypot(east, north)


def _polyline_length_m(value: Any) -> float:
    coordinates = points(value)
    return sum(_distance_m(first, second) for first, second in zip(coordinates, coordinates[1:]))


def points(value: Any) -> list[dict[str, float]]:
    result: list[dict[str, float]] = []
    if not isinstance(value, list):
        return result
    for item in value:
        if isinstance(item, dict):
            item_point = point(item)
            if item_point:
                result.append(item_point)
    return result


def choose_cable_size(required_cores: int, *, main_trunk: bool = False) -> int:
    """Use 144 cores for NRO-PM and the smallest allowed cable elsewhere."""
    if main_trunk:
        return 144
    for cable_size in CABLE_SIZES:
        if cable_size >= max(1, required_cores):
            return cable_size
    return 144


def _add_unique(records: dict[str, dict[str, Any]], record: dict[str, Any]) -> None:
    identifier = record_id(record)
    if identifier and identifier not in records:
        records[identifier] = dict(record)


def _aliases(records: dict[str, dict[str, Any]]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for identifier, record in records.items():
        aliases[identifier] = identifier
        for key in ("id", "code", "CODE", "name", "source_id"):
            alias = _text(record.get(key))
            if alias:
                aliases.setdefault(alias, identifier)
    return aliases


def _resolve(aliases: dict[str, str], reference: str) -> str:
    return aliases.get(reference, reference)


def _same_point(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_point, second_point = point(first), point(second)
    return bool(first_point and second_point and first_point == second_point)


def _append_coordinates(existing: list[dict[str, float]], incoming: list[dict[str, float]]) -> list[dict[str, float]]:
    if not incoming:
        return existing
    if existing and existing[-1] == incoming[0]:
        return [*existing, *incoming[1:]]
    return [*existing, *incoming]


def _prepend_nro_access(
    path: dict[str, Any] | None,
    access_records: list[dict[str, Any]],
    nro_id: str,
) -> dict[str, Any] | None:
    """Prepend the direct NRO-to-road-entry fibre span to a duct path.

    Task 6 keeps this span out of ``duct_routes`` because it is not a duct. It
    is nevertheless part of the physical 144-core NRO-to-PM link emitted by
    Task 5, so its geometry and length are folded into the link while the
    conduit route ids remain unchanged.
    """
    if not path:
        return path
    access = next(
        (
            item
            for item in access_records
            if first_text(item, "nro_id", "from_code", "from_id") == nro_id
        ),
        None,
    )
    if not access:
        return path
    access_points = points(access.get("coordinates"))
    duct_points = points(path.get("coordinates"))
    if access_points and duct_points:
        path["coordinates"] = _append_coordinates(access_points, duct_points)
    try:
        access_length = float(access.get("length_m") or 0.0)
    except (TypeError, ValueError):
        access_length = 0.0
    path["length_m"] = round(float(path.get("length_m") or 0.0) + access_length, 3)
    path["nro_access_fiber_id"] = first_text(access, "id") or f"nro_access_fiber:{nro_id}"
    path["nro_access_length_m"] = round(access_length, 3)
    path["nro_road_entry_id"] = first_text(access, "road_entry_id", "to_code")
    path["nro_access_status"] = "DIRECT_144_CORE_FIBER"
    return path


def _attach_pm_access(
    path: dict[str, Any] | None,
    access_records: list[dict[str, Any]],
    pm_id: str,
    *,
    prepend: bool,
) -> dict[str, Any] | None:
    """Join the direct PM-to-road-entry fibre span to a conduit path."""
    if not path:
        return path
    access = next(
        (
            item
            for item in access_records
            if first_text(item, "pm_id", "from_code", "from_id") == pm_id
        ),
        None,
    )
    if not access:
        return path
    access_points = points(access.get("coordinates"))
    duct_points = points(path.get("coordinates"))
    if access_points and duct_points:
        path["coordinates"] = (
            _append_coordinates(access_points, duct_points)
            if prepend
            else _append_coordinates(duct_points, list(reversed(access_points)))
        )
    try:
        access_length = float(access.get("length_m") or 0.0)
    except (TypeError, ValueError):
        access_length = 0.0
    path["length_m"] = round(float(path.get("length_m") or 0.0) + access_length, 3)
    path["pm_access_fiber_id"] = first_text(access, "id") or f"pm_access_fiber:{pm_id}"
    path["pm_access_length_m"] = round(access_length, 3)
    path["pm_road_entry_id"] = first_text(access, "road_entry_id", "to_code")
    path["pm_access_status"] = "DIRECT_144_CORE_FIBER"
    return path


def _route_path(routes: list[dict[str, Any]], source_id: str, target_id: str, allowed_types: set[str]) -> dict[str, Any] | None:
    """Find an endpoint-connected Task-6 conduit chain.

    Only route records with explicit from/to codes can be used as evidence.
    This prevents accidental use of unassigned main-road segments.
    """
    graph: dict[str, list[tuple[str, int, bool]]] = defaultdict(list)
    for index, route in enumerate(routes):
        if first_text(route, "route_type") not in allowed_types:
            continue
        start = first_text(route, "from_code", "from_id")
        end = first_text(route, "to_code", "to_id")
        if start and end:
            graph[start].append((end, index, False))
            graph[end].append((start, index, True))

    queue: deque[str] = deque([source_id])
    seen = {source_id}
    previous: dict[str, tuple[str, int, bool]] = {}
    while queue:
        current = queue.popleft()
        if current == target_id:
            break
        for neighbor, route_index, reverse in sorted(graph.get(current, []), key=lambda item: (item[0], item[1])):
            if neighbor not in seen:
                seen.add(neighbor)
                previous[neighbor] = (current, route_index, reverse)
                queue.append(neighbor)
    if target_id not in seen:
        return None

    chain: list[tuple[int, bool]] = []
    cursor = target_id
    while cursor != source_id:
        predecessor, route_index, reverse = previous[cursor]
        chain.append((route_index, reverse))
        cursor = predecessor
    chain.reverse()

    route_ids: list[str] = []
    route_types: list[str] = []
    coordinates: list[dict[str, float]] = []
    length_m = 0.0
    for route_index, reverse in chain:
        route = routes[route_index]
        route_ids.append(first_text(route, "id") or f"task6_route_{route_index + 1}")
        route_types.append(first_text(route, "route_type"))
        segment = points(route.get("coordinates"))
        if reverse:
            segment.reverse()
        coordinates = _append_coordinates(coordinates, segment)
        try:
            length_m += float(route.get("length_m") or 0.0)
        except (TypeError, ValueError):
            pass
    return {
        "conduit_route_ids": route_ids,
        "conduit_route_types": route_types,
        "coordinates": coordinates,
        "length_m": round(length_m, 3),
        "conduit_length_m": round(length_m, 3),
    }


def _issue(code: str, message: str, *, link_id: str = "", severity: str = "warning") -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message, "link_id": link_id}


def _materialize_devices(plan: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    fixed = plan.get("fixed_assignments") if isinstance(plan.get("fixed_assignments"), dict) else {}
    devices = {kind: {} for kind in ("NRO", "PM", "BPE", "PBO", "IMB")}
    for record in [*_records(fixed.get("nro_sites")), *_records(plan.get("nro_sites"))]:
        _add_unique(devices["NRO"], record)
    for record in [*_records(fixed.get("pm_sites")), *_records(plan.get("pm_sites"))]:
        _add_unique(devices["PM"], record)
    for record in _records(plan.get("bpe_plan")):
        _add_unique(devices["BPE"], record)
    for record in _records(plan.get("pbo_plan")):
        _add_unique(devices["PBO"], record)
    for source_record in _records(plan.get("imb_points")):
        # The public IMB key is the building code used by Task 4 and the
        # mapping tables.  Preserve Task 6's internal node id only as an
        # audit attribute so every downstream reference resolves directly.
        record = dict(source_record)
        code = first_text(record, "code", "CODE")
        source_id = first_text(record, "id")
        if code:
            if source_id and source_id != code:
                record["source_id"] = source_id
            record["id"] = code
        _add_unique(devices["IMB"], record)

    for route in _records(plan.get("duct_routes")):
        if first_text(route, "route_type") != "NRO_TO_PM_MAIN":
            continue
        nro_id, pm_id = first_text(route, "from_code"), first_text(route, "to_code")
        if nro_id and nro_id not in devices["NRO"]:
            devices["NRO"][nro_id] = {"id": nro_id, "source": "task6_route_endpoint"}
        if pm_id and pm_id not in devices["PM"]:
            devices["PM"][pm_id] = {"id": pm_id, "source": "task6_route_endpoint"}
    for record in [*devices["BPE"].values(), *devices["PBO"].values()]:
        identifier = first_text(record, "nro_id", "nro_code")
        if identifier and identifier not in devices["NRO"]:
            devices["NRO"][identifier] = {"id": identifier, "source": "task4_assignment"}
    for record in [*devices["BPE"].values(), *devices["PBO"].values()]:
        identifier = first_text(record, "pm_id", "pm_code")
        if identifier and identifier not in devices["PM"] and identifier not in devices["NRO"]:
            devices["PM"][identifier] = {"id": identifier, "source": "task4_assignment"}
    return devices


def _link_record(stage: str, source_type: str, source_id: str, target_type: str, target_id: str, target: dict[str, Any], downstream_ports: int, path: dict[str, Any] | None) -> dict[str, Any]:
    target_capacity = number(target, "capacity", "CAPACITY")
    sizing_requirement = max(downstream_ports, target_capacity)
    is_main = stage == "NRO_TO_PM"
    if stage == "PBO_TO_IMB":
        physical_path_status = "INDOOR_LINK"
        path = {"conduit_route_ids": [], "conduit_route_types": [], "coordinates": [], "length_m": 0.0, "conduit_length_m": 0.0}
    elif path:
        physical_path_status = "MATCHED_TASK6_CONDUIT"
    else:
        physical_path_status = "REVIEW_REQUIRED"
        path = {"conduit_route_ids": [], "conduit_route_types": [], "coordinates": [], "length_m": 0.0, "conduit_length_m": 0.0}
    cable_cores = choose_cable_size(sizing_requirement, main_trunk=is_main)
    return {
        "id": f"FIBER::{stage}::{source_id}::{target_id}",
        "stage": stage,
        "from_type": source_type,
        "from_code": source_id,
        "to_type": target_type,
        "to_code": target_id,
        "downstream_ports": downstream_ports,
        "downstream_device_capacity": target_capacity or None,
        "sizing_requirement_cores": sizing_requirement,
        "cable_cores": cable_cores,
        "cable_spec": f"FIBER_{cable_cores}_CORE",
        # PM performs optical splitting.  Its upstream trunk is always one
        # 144-core cable and is not limited by the aggregate PM service ports.
        "cable_capacity_shortfall": not is_main and sizing_requirement > max(CABLE_SIZES),
        "cable_selection_rule": "NRO_TO_PM_FIXED_144" if is_main else "SMALLEST_OF_24_48_72_144_FOR_DOWNSTREAM_CAPACITY_OR_PORTS",
        "splitter_required": is_main,
        "capacity_basis": "PM_SPLITTER_UPSTREAM_144_CORE" if is_main else "DOWNSTREAM_PORTS_OR_DEVICE_CAPACITY",
        "effective_upstream_cores": cable_cores if is_main else sizing_requirement,
        "physical_path_status": physical_path_status,
        **path,
    }


def _finalize_link_lengths(link: dict[str, Any]) -> None:
    """Expose separate cable and conduit quantities while retaining length_m."""
    conduit_length = float(link.get("conduit_length_m") or 0.0)
    cable_length = float(link.get("length_m") or 0.0)
    if link.get("stage") == "PBO_TO_IMB" or not cable_length:
        cable_length = _polyline_length_m(link.get("coordinates"))
    link["cable_length_m"] = round(cable_length, 3)
    link["conduit_length_m"] = round(conduit_length, 3)
    link["length_m"] = round(cable_length, 3)


def _add_direct_geometry(link: dict[str, Any], source: dict[str, Any], target: dict[str, Any]) -> None:
    if link["coordinates"]:
        return
    source_point, target_point = point(source), point(target)
    if source_point and target_point:
        link["coordinates"] = [source_point, target_point]


def build_fiber_links(plan: dict[str, Any]) -> dict[str, Any]:
    devices = _materialize_devices(plan)
    aliases = {kind: _aliases(records) for kind, records in devices.items()}
    imb_aliases = aliases["IMB"]
    for pm in devices["PM"].values():
        assigned = pm.get("assigned_imb_ids")
        if isinstance(assigned, list):
            pm["assigned_imb_ids"] = [_resolve(imb_aliases, _text(value)) for value in assigned]
    for pbo in devices["PBO"].values():
        for key in ("imb_id", "imb_code", "assigned_imb_id", "target_imb_id"):
            if _text(pbo.get(key)):
                pbo[key] = _resolve(imb_aliases, _text(pbo[key]))
    routes = _records(plan.get("duct_routes"))
    access_records = _records(plan.get("nro_access_fibers"))
    pm_access_records = _records(plan.get("pm_access_fibers"))
    if not access_records:
        fixed = plan.get("fixed_assignments") if isinstance(plan.get("fixed_assignments"), dict) else {}
        access_records = _records(fixed.get("nro_access_fibers"))
    if not pm_access_records:
        fixed = plan.get("fixed_assignments") if isinstance(plan.get("fixed_assignments"), dict) else {}
        pm_access_records = _records(fixed.get("pm_access_fibers"))
    issues: list[dict[str, str]] = []
    links: list[dict[str, Any]] = []

    pbo_to_imb: dict[str, str] = {}
    pbo_to_bpe: dict[str, str] = {}
    bpe_to_pbos: dict[str, list[str]] = defaultdict(list)
    for pbo_id, pbo in devices["PBO"].items():
        imb_ref = first_text(pbo, "imb_id", "imb_code", "assigned_imb_id", "target_imb_id")
        bpe_ref = first_text(pbo, "bpe_id", "bpe_code", "parent_bpe_id")
        if imb_ref:
            pbo_to_imb[pbo_id] = _resolve(aliases["IMB"], imb_ref)
        else:
            building_id = first_text(pbo, "building_id", "BUILDING_ID")
            candidates = [
                imb_id for imb_id, imb in devices["IMB"].items()
                if building_id and building_id == first_text(imb, "building_id", "BUILDING_ID")
            ]
            if len(candidates) != 1:
                candidates = [imb_id for imb_id, imb in devices["IMB"].items() if _same_point(pbo, imb)]
            if len(candidates) == 1:
                pbo_to_imb[pbo_id] = candidates[0]
        if bpe_ref:
            bpe_id = _resolve(aliases["BPE"], bpe_ref)
            pbo_to_bpe[pbo_id] = bpe_id
            bpe_to_pbos[bpe_id].append(pbo_id)

    pm_by_imb: dict[str, str] = {}
    for pm_id, pm in devices["PM"].items():
        for imb_ref in pm.get("assigned_imb_ids") or []:
            identifier = _resolve(aliases["IMB"], _text(imb_ref))
            if identifier:
                pm_by_imb[identifier] = pm_id
    for imb_id, imb in devices["IMB"].items():
        pm_ref = first_text(imb, "pm_id", "pm_code")
        if pm_ref:
            pm_by_imb[imb_id] = _resolve(aliases["PM"], pm_ref)

    bpe_to_pm: dict[str, str] = {}
    bpe_to_nro: dict[str, str] = {}
    for bpe_id, bpe in devices["BPE"].items():
        pm_ref = first_text(bpe, "pm_id", "pm_code")
        nro_ref = first_text(bpe, "nro_id", "nro_code")
        inferred_pm_ids = {
            pm_by_imb[pbo_to_imb[pbo_id]]
            for pbo_id in bpe_to_pbos.get(bpe_id, [])
            if pbo_id in pbo_to_imb and pbo_to_imb[pbo_id] in pm_by_imb
        }
        if not pm_ref and len(inferred_pm_ids) == 1:
            pm_ref = next(iter(inferred_pm_ids))
        if pm_ref:
            pm_id = _resolve(aliases["PM"], pm_ref)
            if pm_id in devices["PM"]:
                bpe_to_pm[bpe_id] = pm_id
        if nro_ref:
            bpe_to_nro[bpe_id] = _resolve(aliases["NRO"], nro_ref)

    for bpe_id in devices["BPE"]:
        if bpe_id in bpe_to_pm:
            continue
        candidates = [
            pm_id for pm_id in devices["PM"]
            if _route_path(routes, pm_id, bpe_id, ROUTE_TYPES["PM_TO_BPE"])
        ]
        if len(candidates) == 1:
            bpe_to_pm[bpe_id] = candidates[0]

    route_nro_by_pm: dict[str, str] = {}
    for route in routes:
        if first_text(route, "route_type") == "NRO_TO_PM_MAIN":
            nro_ref, pm_ref = first_text(route, "from_code"), first_text(route, "to_code")
            if nro_ref and pm_ref:
                route_nro_by_pm[_resolve(aliases["PM"], pm_ref)] = _resolve(aliases["NRO"], nro_ref)

    pm_to_nro: dict[str, str] = {}
    for pm_id, pm in devices["PM"].items():
        nro_ref = first_text(pm, "nro_id", "nro_code", "parent_nro_id")
        nro_id = _resolve(aliases["NRO"], nro_ref) if nro_ref else route_nro_by_pm.get(pm_id, "")
        if not nro_id:
            inferred_nros = {bpe_to_nro[bpe_id] for bpe_id, owner_pm in bpe_to_pm.items() if owner_pm == pm_id and bpe_id in bpe_to_nro}
            if len(inferred_nros) == 1:
                nro_id = next(iter(inferred_nros))
        if nro_id:
            pm_to_nro[pm_id] = nro_id

    pbo_ports: dict[str, int] = {}
    for pbo_id, pbo in devices["PBO"].items():
        imb = devices["IMB"].get(pbo_to_imb.get(pbo_id, ""), {})
        pbo_ports[pbo_id] = max(number(pbo, "demand", "ports", "NB_LOC_TOT"), number(imb, "ports", "demand", "NB_LOC_TOT"))
    bpe_ports = {bpe_id: sum(pbo_ports[pbo_id] for pbo_id in pbo_ids) for bpe_id, pbo_ids in bpe_to_pbos.items()}
    pm_ports = {pm_id: sum(bpe_ports.get(bpe_id, number(devices["BPE"][bpe_id], "demand")) for bpe_id, owner_pm in bpe_to_pm.items() if owner_pm == pm_id) for pm_id in devices["PM"]}

    for pm_id, pm in sorted(devices["PM"].items()):
        nro_id = pm_to_nro.get(pm_id, "")
        if not nro_id:
            issues.append(_issue("missing_nro_assignment", f"PM {pm_id} has no NRO assignment"))
            continue
        nro = devices["NRO"].get(nro_id, {"id": nro_id})
        main_path = _route_path(routes, nro_id, pm_id, ROUTE_TYPES["NRO_TO_PM"])
        main_path = _prepend_nro_access(main_path, access_records, nro_id)
        main_path = _attach_pm_access(main_path, pm_access_records, pm_id, prepend=False)
        link = _link_record("NRO_TO_PM", "NRO", nro_id, "PM", pm_id, pm, pm_ports.get(pm_id, number(pm, "demand")), main_path)
        _add_direct_geometry(link, nro, pm)
        _finalize_link_lengths(link)
        links.append(link)

    for bpe_id, bpe in sorted(devices["BPE"].items()):
        pm_id = bpe_to_pm.get(bpe_id, "")
        if not pm_id:
            issues.append(_issue("missing_pm_assignment", f"BPE {bpe_id} has no unique PM assignment"))
            continue
        pm = devices["PM"].get(pm_id, {"id": pm_id})
        distribution_path = _route_path(routes, pm_id, bpe_id, ROUTE_TYPES["PM_TO_BPE"])
        distribution_path = _attach_pm_access(
            distribution_path, pm_access_records, pm_id, prepend=True
        )
        link = _link_record("PM_TO_BPE", "PM", pm_id, "BPE", bpe_id, bpe, bpe_ports.get(bpe_id, number(bpe, "demand")), distribution_path)
        _add_direct_geometry(link, pm, bpe)
        _finalize_link_lengths(link)
        links.append(link)

    for pbo_id, pbo in sorted(devices["PBO"].items()):
        bpe_id = pbo_to_bpe.get(pbo_id, "")
        if not bpe_id:
            issues.append(_issue("missing_bpe_assignment", f"PBO {pbo_id} has no BPE assignment"))
        else:
            bpe = devices["BPE"].get(bpe_id, {"id": bpe_id})
            link = _link_record("BPE_TO_PBO", "BPE", bpe_id, "PBO", pbo_id, pbo, pbo_ports.get(pbo_id, number(pbo, "demand")), _route_path(routes, bpe_id, pbo_id, ROUTE_TYPES["BPE_TO_PBO"]))
            _add_direct_geometry(link, bpe, pbo)
            _finalize_link_lengths(link)
            links.append(link)

        imb_id = pbo_to_imb.get(pbo_id, "")
        imb = devices["IMB"].get(imb_id)
        if not imb:
            issues.append(_issue("missing_imb_assignment", f"PBO {pbo_id} has no resolvable IMB assignment"))
            continue
        link = _link_record("PBO_TO_IMB", "PBO", pbo_id, "IMB", imb_id, imb, pbo_ports.get(pbo_id, number(imb, "ports", "demand")), None)
        _add_direct_geometry(link, pbo, imb)
        _finalize_link_lengths(link)
        links.append(link)

    for link in links:
        if link["physical_path_status"] == "REVIEW_REQUIRED":
            issues.append(_issue("missing_task6_conduit_path", f"{link['id']} has no matching Task-6 conduit path", link_id=link["id"]))
        if link["cable_capacity_shortfall"]:
            issues.append(_issue("cable_size_limit", f"{link['id']} needs {link['sizing_requirement_cores']} cores, above the configured 144-core maximum", link_id=link["id"]))

    stage_counts = Counter(link["stage"] for link in links)
    cable_counts = Counter(link["cable_cores"] for link in links)
    imb_id_contract = {
        "canonical_key": "code",
        "all_device_ids_equal_codes": all(item.get("id") == item.get("code") for item in devices["IMB"].values()),
        "all_link_imb_references_resolve": all(
            link.get("to_code") in devices["IMB"]
            for link in links if link.get("to_type") == "IMB"
        ),
    }
    return {
        "schema_version": "task5.fiber-link-plan.v1",
        "meta": {
            "algorithm": "task6_conduit_hierarchical_fiber_links",
            "hierarchy": list(LINK_STAGES),
            "conduit_capacity_accounting": "DISABLED_BY_REQUIREMENT",
            "cable_sizes": list(CABLE_SIZES),
            "main_trunk_cable_cores": 144,
            "nro_access_rule": "DIRECT_144_CORE_FIBER_TO_ROAD_ENTRY",
            "nro_access_fiber_records": len(access_records),
            "pm_access_rule": "DIRECT_144_CORE_FIBER_TO_ROAD_ENTRY_WITHIN_10M",
            "pm_access_fiber_records": len(pm_access_records),
            "length_contract": "length_m=cable_length_m; conduit_length_m excludes direct access and indoor spans",
            "imb_id_contract": imb_id_contract,
        },
        "devices": {kind.lower(): sorted(records.values(), key=record_id) for kind, records in devices.items()},
        "links": links,
        "validation": {
            "status": "REVIEW_REQUIRED" if issues else "PASS",
            "expected_links_by_stage": {
                "NRO_TO_PM": len(pm_to_nro), "PM_TO_BPE": len(bpe_to_pm),
                "BPE_TO_PBO": len(pbo_to_bpe), "PBO_TO_IMB": len(pbo_to_imb),
            },
            "planned_links_by_stage": {stage: stage_counts.get(stage, 0) for stage in LINK_STAGES},
            "cables_by_cores": {str(size): cable_counts.get(size, 0) for size in CABLE_SIZES},
            "review_required_link_ids": [link["id"] for link in links if link["physical_path_status"] == "REVIEW_REQUIRED"],
            "issues": issues,
        },
    }


def _feature_collection(links: Iterable[dict[str, Any]]) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    for link in links:
        coordinates = [[item["x"], item["y"]] for item in link.get("coordinates", [])]
        geometry = {"type": "LineString", "coordinates": coordinates} if len(coordinates) >= 2 else None
        properties = {key: value for key, value in link.items() if key != "coordinates"}
        features.append({"type": "Feature", "properties": properties, "geometry": geometry})
    return {"type": "FeatureCollection", "features": features}


def write_outputs(result: dict[str, Any], output_dir: Path, source_plan: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    plan_path = output_dir / "fiber_link_plan.json"
    links_path = output_dir / "fiber_links.csv"
    validation_path = output_dir / "fiber_link_validation.json"
    geojson_path = output_dir / "fiber_links.geojson"
    result["meta"]["source_task6_plan"] = relative_to_root(source_plan)
    plan_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = [
        "id", "stage", "from_type", "from_code", "to_type", "to_code",
        "downstream_ports", "downstream_device_capacity", "sizing_requirement_cores",
        "cable_cores", "cable_spec", "cable_capacity_shortfall", "cable_selection_rule",
        "splitter_required", "capacity_basis", "effective_upstream_cores",
        "physical_path_status", "length_m", "cable_length_m", "conduit_length_m",
        "coordinates", "conduit_route_ids", "conduit_route_types",
        "nro_access_fiber_id", "nro_access_length_m", "nro_access_status", "nro_road_entry_id",
        "pm_access_fiber_id", "pm_access_length_m", "pm_access_status", "pm_road_entry_id",
    ]
    with links_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for link in result["links"]:
            row = {key: link.get(key, "") for key in fields}
            row["coordinates"] = json.dumps(link.get("coordinates", []), ensure_ascii=False, separators=(",", ":"))
            row["conduit_route_ids"] = ";".join(link["conduit_route_ids"])
            row["conduit_route_types"] = ";".join(link["conduit_route_types"])
            writer.writerow(row)
    validation_path.write_text(json.dumps(result["validation"], ensure_ascii=False, indent=2), encoding="utf-8")
    geojson_path.write_text(json.dumps(_feature_collection(result["links"]), ensure_ascii=False, indent=2), encoding="utf-8")
    return {"fiber_link_plan": str(plan_path), "fiber_links": str(links_path), "validation": str(validation_path), "qgis_geojson": str(geojson_path)}


def execute(config: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    if config.get("interface_version") != "task5.fiber-link.v1":
        raise Task5Error("interface_version must be 'task5.fiber-link.v1'")
    if config.get("operation") != "plan_hierarchical_fiber_links":
        raise Task5Error("operation must be 'plan_hierarchical_fiber_links'")
    input_config = config.get("input")
    if not isinstance(input_config, dict) or not input_config.get("task6_plan"):
        raise Task5Error("input.task6_plan is required")
    source_plan = resolve_path(base_dir, str(input_config["task6_plan"]))
    plan, _ = load_json(source_plan)
    result = build_fiber_links(plan)
    output_config = config.get("output") if isinstance(config.get("output"), dict) else {}
    output_dir = resolve_path(base_dir, str(output_config.get("directory", str(TASK5_PRODUCTION_OUTPUT_DIR))))
    result["output_paths"] = write_outputs(result, output_dir, source_plan)
    return result
