"""Rebuild Tasks 2 -> 3 -> 4 -> 6 -> 5 as one isolated pipeline run.

Task 1 is deliberately an input boundary.  The command starts from a Task-2
recognition/topology JSON, computes the service partition, creates the initial
PBO/BPE service relation required by the PM optimizer, then reruns Task 4 with
the selected fixed PMs before planning Task 6, Task 5, and final layers.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline_contract import (
    PipelineContractError,
    imb_codes,
    imb_ids,
    imb_records,
    load_json,
    validate_imb_coverage,
    validate_partition_membership,
    write_manifest,
)

from project_paths import (
    FINAL_LAYER_OUTPUT_DIR,
    TASK2_RECOGNITION_INPUT,
    TASK3_FINAL_INPUT_DIR,
    TASK6_PRODUCTION_INPUT,
    TASK4_FINAL_INPUT_DIR,
    SCENARIO_OUTPUT_DIR,
)


ROOT = Path(__file__).resolve().parent
TASK2_PIPELINE = ROOT / "任务2" / "zone_partition" / "src" / "task2_pipeline.py"
TASK3_RUNNER = ROOT / "任务3" / "run_task3.py"
TASK3_ADAPTER = ROOT / "任务3" / "task3_to_task6_adapter.py"
TASK4_PLANNER = ROOT / "任务4" / "plan_pbo_bpe.py"
TASK6_PLANNER = ROOT / "任务6" / "code" / "plan_task6.py"
TASK6_VISUALIZER = ROOT / "任务6" / "code" / "visualize_task6_plan.py"
TASK5_RUNNER = ROOT / "任务5" / "run_task5.py"
TASK5_VISUALIZER = ROOT / "任务5" / "visualize_task5.py"
FINAL_LAYERS = ROOT / "build_final_layers.py"


def _run(command: list[str], *, cwd: Path, dry_run: bool) -> None:
    print("[pipeline] " + " ".join(str(item) for item in command))
    if not dry_run:
        subprocess.run(command, cwd=cwd, check=True)


def _node_code(node: dict[str, Any]) -> str:
    return str(node.get("code") or node.get("CODE") or node.get("id") or "").strip()


def _node_id(node: dict[str, Any]) -> str:
    return str(node.get("id") or node.get("ID") or node.get("code") or node.get("CODE") or "").strip()


def materialize_task3_input(task2_payload: dict[str, Any], partition: dict[str, Any], output_dir: Path) -> Path:
    """Write the minimal Task-3 CSV contract from this run's Task-2 result."""
    output_dir.mkdir(parents=True, exist_ok=True)
    zpm_by_imb: dict[str, str] = {}
    znro_by_zpm: dict[str, str] = {}
    for region in partition.get("zpm_regions") or []:
        zpm_id = str(region.get("id") or "")
        znro_id = str(region.get("parent_znro_id") or "")
        if not zpm_id:
            continue
        znro_by_zpm[zpm_id] = znro_id
        for internal_id in region.get("imb_ids") or []:
            zpm_by_imb[str(internal_id)] = zpm_id

    fields = ["CODE", "NB_LOC_RES", "NB_LOC_PRO", "NB_LOC_TOT", "X", "Y", "NODE_CODE", "ZNRO_CODE", "ZPM_CODE"]
    rows: list[dict[str, Any]] = []
    for node in imb_records(task2_payload):
        internal_id = _node_id(node)
        code = _node_code(node)
        zpm_id = zpm_by_imb.get(internal_id)
        if not zpm_id:
            raise PipelineContractError(f"Task-2 partition has no ZPM assignment for IMB {internal_id or code}")
        ports = int(round(float(node.get("ports") or node.get("nb_loc_tot") or node.get("NB_LOC_TOT") or 0)))
        if ports <= 0:
            raise PipelineContractError(f"Task-2 IMB {code} has no positive port demand")
        rows.append(
            {
                "CODE": code,
                "NB_LOC_RES": int(node.get("nb_loc_res") or node.get("NB_LOC_RES") or ports),
                "NB_LOC_PRO": int(node.get("nb_loc_pro") or node.get("NB_LOC_PRO") or 0),
                "NB_LOC_TOT": ports,
                "X": node.get("x", node.get("X")),
                "Y": node.get("y", node.get("Y")),
                "NODE_CODE": internal_id,
                "ZNRO_CODE": znro_by_zpm[zpm_id],
                "ZPM_CODE": zpm_id,
            }
        )
    if not rows:
        raise PipelineContractError("Task-2 result contains no IMBs for Task-3 input")
    destination = output_dir / "IMB.csv"
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return destination.parent


def _task5_config(task6_plan: Path, output_dir: Path, path: Path) -> Path:
    payload = {
        "interface_version": "task5.fiber-link.v1",
        "task_id": "task5_task6_conduit_fiber_links",
        "operation": "plan_hierarchical_fiber_links",
        "input": {"task6_plan": str(task6_plan.resolve())},
        "output": {"directory": str(output_dir.resolve())},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rebuild the isolated FTTH Tasks 2-6 pipeline")
    parser.add_argument("--task2-input", type=Path, default=TASK2_RECOGNITION_INPUT)
    parser.add_argument("--task6-base-input", type=Path, default=TASK6_PRODUCTION_INPUT)
    parser.add_argument("--task4-template", type=Path, default=TASK4_FINAL_INPUT_DIR)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Run directory; defaults to OutPut/51IMB/pipeline_run",
    )
    parser.add_argument("--task3-algorithm", choices=("ga",), default="ga")
    parser.add_argument("--dry-run", action="store_true", help="Print the exact commands without changing outputs")
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    task2_input = args.task2_input.resolve()
    task6_base = args.task6_base_input.resolve()
    task4_template = args.task4_template.resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # The checked-in scenario has one stable output location.  Callers can
    # still provide a timestamped or temporary directory for comparisons.
    run_root = (args.output_root or SCENARIO_OUTPUT_DIR / "pipeline_run").resolve()
    task2_dir = run_root / "task2"
    task3_input_dir = run_root / "task3_input"
    task3_dir = run_root / "task3"
    task4_input_dir = run_root / "task4_input"
    task4_initial_dir = run_root / "task4_initial"
    task4_final_dir = run_root / "task4_final"
    task6_dir = run_root / "task6"
    task5_dir = run_root / "task5"
    layers_dir = run_root / "final_layers"
    task2_cleaned = task2_dir / "task2_imb_only_input.json"
    task2_regions = task2_dir / "qgis_direct_region_partition_result.json"
    task3_input_dir.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        task2_cleaned.parent.mkdir(parents=True, exist_ok=True)
        commands = [
            [sys.executable, str(TASK2_PIPELINE), "--input-json", str(task2_input), "--output-dir", str(task2_dir), "--cleaned-input", str(task2_cleaned)],
            [sys.executable, str(TASK4_PLANNER), str(task4_input_dir), str(task4_initial_dir), "--task2-partition", str(task2_regions)],
            [sys.executable, str(TASK3_RUNNER), "--algorithm", args.task3_algorithm, "--task6-input", str(run_root / "task6_initial_input.json"), "--task2-regions", str(task2_regions), "--output", str(task3_dir)],
            [sys.executable, str(TASK4_PLANNER), str(task4_input_dir), str(task4_final_dir), "--fixed-pm", str(task3_dir / "pm_fixed_plan.json"), "--task2-partition", str(task2_regions)],
            [sys.executable, str(TASK6_PLANNER), "--input", str(run_root / "task6_input.json"), "--output", str(task6_dir / "layered_plan.json")],
            [sys.executable, str(TASK6_VISUALIZER), "--input", str(task6_dir / "layered_plan.json"), "--output", str(task6_dir / "layered_plan.png")],
            [sys.executable, str(TASK5_RUNNER), str(run_root / "task5_config.json")],
            [sys.executable, str(TASK5_VISUALIZER), "--input", str(task5_dir / "fiber_link_plan.json"), "--output", str(task5_dir / "fiber_link_overview.png")],
            [sys.executable, str(FINAL_LAYERS), "--task2-regions", str(task2_regions), "--task4-input", str(task4_input_dir), "--task4-output", str(task4_final_dir), "--task5-plan", str(task5_dir / "fiber_link_plan.json"), "--task6-plan", str(task6_dir / "layered_plan.json"), "--output-dir", str(layers_dir)],
        ]
        for command in commands:
            print("[pipeline] " + " ".join(str(item) for item in command))
        return {"status": "DRY_RUN", "run_root": str(run_root), "commands": commands}

    task2_source = load_json(task2_input)
    task6_source = load_json(task6_base)
    _run(
        [sys.executable, str(TASK2_PIPELINE), "--input-json", str(task2_input), "--output-dir", str(task2_dir), "--cleaned-input", str(task2_cleaned)],
        cwd=ROOT,
        dry_run=False,
    )
    task2_cleaned_payload = load_json(task2_cleaned)
    partition_payload = load_json(task2_regions)
    coverage = validate_imb_coverage(task2_source, task2_cleaned_payload, expected_label="task2-input", actual_label="task2-cleaned")
    base_coverage = validate_imb_coverage(task2_cleaned_payload, task6_source, expected_label="task2-cleaned", actual_label="task6-base-input")
    partition_coverage = validate_partition_membership(task2_cleaned_payload, partition_payload)

    # Generate the Task-3 adapter input from the just-created partition.  This
    # is the only place where ZPM membership is converted to Task-3 CSV.
    task3_input = materialize_task3_input(task2_cleaned_payload, partition_payload, task3_input_dir)
    task4_module_dir = str(ROOT / "任务4")
    if task4_module_dir not in sys.path:
        sys.path.insert(0, task4_module_dir)
    from task4_input_contract import materialize_task4_shape_dir

    materialize_task4_shape_dir(task2_cleaned, task4_template, task4_input_dir)
    _run(
        [sys.executable, str(TASK4_PLANNER), str(task4_input_dir), str(task4_initial_dir), "--task2-partition", str(task2_regions)],
        cwd=ROOT / "任务4",
        dry_run=False,
    )

    initial_task6_input = run_root / "task6_initial_input.json"
    shutil.copy2(task6_base, initial_task6_input)
    from 任务3.task3_to_task6_adapter import attach_task4_plan_to_task6_input

    attach_task4_plan_to_task6_input(initial_task6_input, task4_initial_dir)
    _run(
        [sys.executable, str(TASK3_RUNNER), "--algorithm", args.task3_algorithm, "--task6-input", str(initial_task6_input), "--task2-regions", str(task2_regions), "--output", str(task3_dir), "--task6-output", str(task3_dir / "optimizer_task6_plan.json"), "--task6-visualization", str(task3_dir / "optimizer_task6_plan.png")],
        cwd=ROOT / "任务3",
        dry_run=False,
    )

    fixed_pm = task3_dir / "pm_fixed_plan.json"
    from 任务3.task3_to_task6_adapter import build_fixed_pm_plan, build_layered_task6_input

    fixed_payload = build_fixed_pm_plan(task3_input, task3_dir, fixed_pm)
    fixed_codes = {str(item) for pm in fixed_payload.get("pm_sites", []) for item in pm.get("assigned_imb_ids", [])}
    task2_codes = imb_codes(task2_cleaned_payload)
    if fixed_codes != task2_codes:
        raise PipelineContractError(f"Task-3 fixed PM plan changed the IMB set: missing={sorted(task2_codes - fixed_codes)[:5]}, extra={sorted(fixed_codes - task2_codes)[:5]}")

    _run(
        [sys.executable, str(TASK4_PLANNER), str(task4_input_dir), str(task4_final_dir), "--fixed-pm", str(fixed_pm), "--task2-partition", str(task2_regions)],
        cwd=ROOT / "任务4",
        dry_run=False,
    )
    final_task6_input = run_root / "task6_input.json"
    build_layered_task6_input(task6_base, fixed_pm, final_task6_input)
    attach_task4_plan_to_task6_input(final_task6_input, task4_final_dir)
    _run(
        [sys.executable, str(TASK6_PLANNER), "--input", str(final_task6_input), "--output", str(task6_dir / "layered_plan.json")],
        cwd=ROOT,
        dry_run=False,
    )
    _run(
        [sys.executable, str(TASK6_VISUALIZER), "--input", str(task6_dir / "layered_plan.json"), "--output", str(task6_dir / "layered_plan.png")],
        cwd=ROOT,
        dry_run=False,
    )

    config_path = _task5_config(task6_dir / "layered_plan.json", task5_dir, run_root / "task5_config.json")
    _run([sys.executable, str(TASK5_RUNNER), str(config_path)], cwd=ROOT / "任务5", dry_run=False)
    _run(
        [sys.executable, str(TASK5_VISUALIZER), "--input", str(task5_dir / "fiber_link_plan.json"), "--output", str(task5_dir / "fiber_link_overview.png")],
        cwd=ROOT,
        dry_run=False,
    )
    _run(
        [sys.executable, str(FINAL_LAYERS), "--task2-regions", str(task2_regions), "--task4-input", str(task4_input_dir), "--task4-output", str(task4_final_dir), "--task5-plan", str(task5_dir / "fiber_link_plan.json"), "--task6-plan", str(task6_dir / "layered_plan.json"), "--output-dir", str(layers_dir)],
        cwd=ROOT,
        dry_run=False,
    )

    task6_result = load_json(task6_dir / "layered_plan.json")
    task5_plan = load_json(task5_dir / "fiber_link_plan.json")
    task5_result = load_json(task5_dir / "fiber_link_validation.json")
    final_layers_result = load_json(layers_dir / "field_validation.json")
    task5_plan_validation = task5_plan.get("validation", {})
    task5_validation_status = (
        task5_plan_validation.get("status") == "PASS"
        and task5_result.get("status") == "PASS"
    )
    if not task5_validation_status:
        raise PipelineContractError("Task-5 link plan did not pass its validation contract")
    if final_layers_result.get("status") != "PASS":
        raise PipelineContractError("Final layer field validation did not pass")
    final_layer_summary = {
        "status": final_layers_result.get("status"),
        "layers": {
            name: {
                "status": details.get("status"),
                "feature_count": details.get("feature_count"),
            }
            for name, details in (final_layers_result.get("layers") or {}).items()
            if isinstance(details, dict)
        },
    }
    manifest = {
        "schema_version": "ftth.rebuild_pipeline.v1",
        "status": "PASS",
        "run_root": str(run_root),
        "sources": {"task2_input": str(task2_input), "task6_base_input": str(task6_base), "task4_template": str(task4_template)},
        "artifacts": {"task2_cleaned": str(task2_cleaned), "task2_partition": str(task2_regions), "task3_input": str(task3_input / "IMB.csv"), "fixed_pm": str(fixed_pm), "task4_output": str(task4_final_dir), "task6_input": str(final_task6_input), "task6_plan": str(task6_dir / "layered_plan.json"), "task6_visualization": str(task6_dir / "layered_plan.png"), "task5_plan": str(task5_dir / "fiber_link_plan.json"), "task5_visualization": str(task5_dir / "fiber_link_overview.png"), "final_layers": str(layers_dir)},
        "contracts": {"task2_input_to_cleaned": coverage, "task2_to_task6_base": base_coverage, "task2_partition": partition_coverage},
        "counts": {"imb": len(task2_codes), "task3_pm": len(fixed_payload.get("pm_sites", [])), "task6_duct_routes": len(task6_result.get("duct_routes", [])), "task5_links": len(task5_plan.get("links", []))},
        "task6_validation": task6_result.get("validation", {}),
        "task5_validation": {"status": "PASS", "plan": task5_plan_validation, "artifact_status": task5_result.get("status")},
        "final_layers_validation": final_layer_summary,
    }
    write_manifest(run_root / "pipeline_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        run(args)
    except (PipelineContractError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
