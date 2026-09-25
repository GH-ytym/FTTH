"""Adapters for the stage-A task-3 -> task-4 -> task-6 baseline.

The adapter deliberately carries fixed PM assignments forward. It does not
repartition ZPMs or recluster PBO/BPEs, which makes it suitable for replacing
the deterministic PM choices with a later RL policy.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any


TASK3_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TASK3_DIR.parent
TASK4_DIR = PROJECT_DIR / "任务4"
TASK6_DIR = PROJECT_DIR / "任务6"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _number(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    return float(value)


def _read_csv(path: Path) -> list[dict[str, Any]]:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                return [
                    {str(key).strip().upper(): value for key, value in row.items()}
                    for row in csv.DictReader(handle)
                ]
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Unable to decode CSV: {path}")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_fixed_pm_plan(
    task3_input_dir: str | Path,
    task3_output_dir: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    """Convert task-3 CSV results to the shared fixed-PM contract.

    IMB membership is read from the original task-3 input because the normal
    selected ZPM output contains aggregate counts, not member IDs.
    """
    input_dir = Path(task3_input_dir)
    output_dir = Path(task3_output_dir)
    imb_rows = _read_csv(input_dir / "IMB.csv")
    site_rows = _read_csv(output_dir / "SITE_selected.csv")
    zpm_rows = _read_csv(output_dir / "ZPM_selected.csv")
    score_path = output_dir / "selection_scores.csv"
    score_rows = _read_csv(score_path) if score_path.exists() else []
    # selection_scores.csv is written with lower-case field names by task 3;
    # _read_csv upper-cases them for a stable adapter contract.
    score_by_site = {
        _text(row.get("SITE_CODE")): row
        for row in score_rows
        if _text(row.get("SITE_CODE"))
    }

    sites_by_code = {_text(row.get("CODE")): row for row in site_rows if _text(row.get("CODE"))}
    pms_by_code = {
        code: row for code, row in sites_by_code.items() if _text(row.get("TYPE")).upper() == "PM"
    }
    if not pms_by_code:
        raise ValueError(f"No PM records found in {output_dir / 'SITE_selected.csv'}")

    members_by_zpm: dict[str, list[str]] = {}
    for row in imb_rows:
        imb_code = _text(row.get("CODE"))
        zpm_code = _text(row.get("ZPM_CODE") or row.get("REF_ZPM"))
        if not imb_code or not zpm_code:
            raise ValueError("Every task-3 IMB must contain CODE and ZPM_CODE/REF_ZPM")
        members_by_zpm.setdefault(zpm_code, []).append(imb_code)

    pm_sites: list[dict[str, Any]] = []
    assigned: set[str] = set()
    for zpm in sorted(zpm_rows, key=lambda row: _text(row.get("CODE"))):
        zpm_code = _text(zpm.get("CODE"))
        pm_code = _text(zpm.get("REF_PM"))
        if not zpm_code or not pm_code:
            raise ValueError(f"ZPM {zpm_code or '<empty>'} has no selected REF_PM")
        pm = pms_by_code.get(pm_code)
        if pm is None:
            raise ValueError(f"ZPM {zpm_code} references missing PM {pm_code}")
        members = members_by_zpm.get(zpm_code, [])
        if not members:
            raise ValueError(f"ZPM {zpm_code} has no IMB members")
        duplicate_members = sorted(set(members) & assigned)
        if duplicate_members:
            raise ValueError(f"IMB assigned to multiple ZPMs: {duplicate_members[:5]}")
        assigned.update(members)
        score = score_by_site.get(pm_code, {})
        pm_sites.append(
            {
                "id": pm_code,
                "code": pm_code,
                "x": _number(pm.get("X")),
                "y": _number(pm.get("Y")),
                "node_id": _text(score.get("PTECH_CODE") or pm.get("CODE_PTC") or pm_code),
                "zpm_id": zpm_code,
                "partition_id": zpm_code,
                "nro_id": _text(zpm.get("REF_NRO") or pm.get("REF_NRO")),
                "assigned_imb_ids": members,
                "capacity": int(round(_number(score.get("CAPACITY") or zpm.get("CAPACITE"), 576))),
                "demand": int(round(_number(zpm.get("NIB_PRISES"), 0))),
                "status": _text(pm.get("STATUT") or "PROJET"),
                "property": _text(pm.get("PROPRETAI") or pm.get("PROPRIETAI")),
                "source": "task3_selected_site",
            }
        )

    expected = {_text(row.get("CODE")) for row in imb_rows if _text(row.get("CODE"))}
    missing = sorted(expected - assigned)
    if missing:
        raise ValueError(f"Task-3 ZPM output does not assign all IMBs: {missing[:5]}")

    nro_sites = []
    for row in site_rows:
        if _text(row.get("TYPE")).upper() != "NRO":
            continue
        score = score_by_site.get(_text(row.get("CODE")), {})
        nro_sites.append(
            {
                "id": _text(row.get("CODE")),
                "code": _text(row.get("CODE")),
                "x": _number(row.get("X")),
                "y": _number(row.get("Y")),
                "capacity": int(round(_number(score.get("CAPACITY") or row.get("CAPACITE"), 2048))),
                "source": "task3_selected_site",
            }
        )
    if not nro_sites:
        raise ValueError("No NRO records found in selected SITE output")

    payload = {
        "schema_version": "task3.fixed_pm.v1",
        "source": {
            "task3_input": str((input_dir / "IMB.csv").resolve()),
            "task3_output": str(output_dir.resolve()),
        },
        "nro_sites": nro_sites,
        "pm_sites": pm_sites,
        "counts": {
            "nro": len(nro_sites),
            "pm": len(pm_sites),
            "imb": len(assigned),
            "total_ports": sum(int(round(_number(row.get("NB_LOC_TOT")))) for row in imb_rows),
        },
        "selection_mode": "deterministic_task3_baseline",
    }
    _write_json(Path(output_path), payload)
    return payload


def build_layered_task6_input(
    base_task6_input: str | Path,
    fixed_pm_plan: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    """Create a task-6 layered input carrying selected NRO/PM records."""
    base_path = Path(base_task6_input)
    fixed_path = Path(fixed_pm_plan)
    base = json.loads(base_path.read_text(encoding="utf-8-sig"))
    fixed = json.loads(fixed_path.read_text(encoding="utf-8-sig"))
    if not fixed.get("pm_sites"):
        raise ValueError("Fixed PM plan has no pm_sites")

    # ``id`` is the internal topology node id in the production Task-6
    # input (for example ``n_0141``), while Task-3/4 assignments use the
    # stable business code (for example ``JAD-MAR-BAT-0001``).  The business
    # code is the cross-task key and must be preferred here.
    base_imb_ids = {
        str(item.get("code") or item.get("CODE") or item.get("id"))
        for item in (base.get("imb_points") or [])
        if item.get("id") or item.get("code") or item.get("CODE")
    }
    fixed_imb_ids = {
        str(imb_id)
        for pm in fixed["pm_sites"]
        for imb_id in (pm.get("assigned_imb_ids") or pm.get("imb_ids") or [])
    }
    if base_imb_ids and base_imb_ids != fixed_imb_ids:
        missing = sorted(base_imb_ids - fixed_imb_ids)
        extra = sorted(fixed_imb_ids - base_imb_ids)
        raise ValueError(
            "Task-3 fixed PM and task-6 input must describe the same IMB set; "
            f"missing={missing[:5]}, extra={extra[:5]}"
        )

    remote = dict(base.get("remote_sensing") or {})
    remote["nro_sites"] = fixed.get("nro_sites", [])
    remote["pm_sites"] = fixed["pm_sites"]
    if base.get("trunk_roads"):
        remote["roads"] = base["trunk_roads"]

    result = dict(base)
    result["meta"] = {
        **dict(base.get("meta") or {}),
        "task": "task3_fixed_pm_task6_input",
        "fixed_pm_plan": str(fixed_path.resolve()),
    }
    result["planning"] = {
        **dict(base.get("planning") or {}),
        "algorithm": "layered_fixed_assignment_shared_edge_search",
    }
    result["remote_sensing"] = remote
    result["nro_sites"] = fixed.get("nro_sites", [])
    result["pm_sites"] = fixed["pm_sites"]
    _write_json(Path(output_path), result)
    return result


def attach_task4_plan_to_task6_input(
    task6_input_path: str | Path,
    task4_output_dir: str | Path,
) -> dict[str, Any]:
    """Replace task-6 placeholder PBO/BPE records with task-4 output."""
    task6_path = Path(task6_input_path)
    task4_output = Path(task4_output_dir)
    input_data = json.loads(task6_path.read_text(encoding="utf-8-sig"))
    code_dir = TASK6_DIR / "code"
    if str(code_dir) not in sys.path:
        sys.path.insert(0, str(code_dir))
    from task4_assignment_adapter import load_task4_plan

    pbo_plan, bpe_plan = load_task4_plan(task4_output, input_data)
    existing_pbo = {
        str(item.get("id") or item.get("code")): item
        for item in input_data.get("pbo_plan") or []
        if item.get("id") or item.get("code")
    }
    existing_bpe = {
        str(item.get("id") or item.get("code")): item
        for item in input_data.get("bpe_plan") or []
        if item.get("id") or item.get("code")
    }

    def merge_relationships(
        loaded: dict[str, Any], existing: dict[str, Any], keys: tuple[str, ...]
    ) -> dict[str, Any]:
        merged = dict(loaded)
        for key in keys:
            if existing.get(key) not in (None, ""):
                merged[key] = existing[key]
        return merged

    input_data["pbo_plan"] = [
        merge_relationships(
            item,
            existing_pbo.get(str(item["code"]), {}),
            ("building_id", "group_id", "bpe_id", "bpe_code"),
        )
        for item in pbo_plan
    ]
    input_data["bpe_plan"] = [
        merge_relationships(
            item,
            existing_bpe.get(str(item["code"]), {}),
            ("building_id", "group_id", "building_group_id", "parent_code"),
        )
        for item in bpe_plan
    ]
    input_data.setdefault("meta", {})["fixed_task4_output"] = str(task4_output.resolve())
    _write_json(task6_path, input_data)
    return input_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Bridge task-3 selected PMs to task-6 fixed-assignment input")
    parser.add_argument("--task3-input", type=Path, required=True)
    parser.add_argument("--task3-output", type=Path, required=True)
    parser.add_argument("--fixed-pm-output", type=Path, required=True)
    parser.add_argument("--task6-input", type=Path)
    parser.add_argument("--task6-input-output", type=Path)
    args = parser.parse_args()

    plan = build_fixed_pm_plan(args.task3_input, args.task3_output, args.fixed_pm_output)
    print(json.dumps({"fixed_pm_output": str(args.fixed_pm_output), "counts": plan["counts"]}, ensure_ascii=False, indent=2))
    if args.task6_input or args.task6_input_output:
        if not args.task6_input or not args.task6_input_output:
            parser.error("--task6-input and --task6-input-output must be provided together")
        layered = build_layered_task6_input(args.task6_input, args.fixed_pm_output, args.task6_input_output)
        print(json.dumps({"task6_input_output": str(args.task6_input_output), "algorithm": layered["planning"]["algorithm"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
