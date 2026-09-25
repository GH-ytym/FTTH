"""Run the reproducible Task-3 -> Task-4 -> Task-6 handoff end to end.

When ``--run-task3`` is selected, Task 4 first creates the fixed PBO/BPE
relationships, Task 3 optimizes PM coordinates against those relationships,
and Task 4 is rerun with the resulting ``pm_fixed_plan.json``.  The final
fixed relationships are then attached to Task 6.  A later RL policy can reuse
the same handoff by replacing ``pm_fixed_plan.json``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from task3_to_task6_adapter import (
    attach_task4_plan_to_task6_input,
    build_fixed_pm_plan,
    build_layered_task6_input,
)


TASK3_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TASK3_DIR.parent
TASK4_DIR = PROJECT_DIR / "任务4"
TASK6_DIR = PROJECT_DIR / "任务6"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
if str(TASK4_DIR) not in sys.path:
    sys.path.insert(0, str(TASK4_DIR))

from project_paths import TASK2_ZONE_PARTITION_OUTPUT_DIR


def main() -> None:
    parser = argparse.ArgumentParser(description="Run reproducible task-3 -> task-4 -> task-6 stage A")
    parser.add_argument(
        "--task3-input",
        type=Path,
        default=None,
        help="Existing Task-3 input directory (required only when --run-task3 is omitted).",
    )
    parser.add_argument("--task3-output", type=Path, required=True)
    parser.add_argument("--task4-shape-dir", type=Path, required=True)
    parser.add_argument("--task4-output", type=Path, required=True)
    parser.add_argument("--task6-base-input", type=Path, required=True)
    parser.add_argument("--task6-input-output", type=Path, required=True)
    parser.add_argument("--task6-output", type=Path, required=True)
    parser.add_argument(
        "--task2-regions",
        type=Path,
        default=TASK2_ZONE_PARTITION_OUTPUT_DIR / "qgis_direct_region_partition_result.json",
    )
    parser.add_argument(
        "--task2-partition",
        type=Path,
        default=None,
        help="Task-2 partition JSON used by Task 4 (defaults to recomputation for legacy runs).",
    )
    parser.add_argument(
        "--task2-input",
        type=Path,
        default=None,
        help="Optional Task-2 cleaned/IMB-only JSON used to materialize the Task-4 IMB layer.",
    )
    parser.add_argument(
        "--materialized-task4-shape-dir",
        type=Path,
        default=None,
        help="Destination for a Task-2-derived Task-4 input directory.",
    )
    parser.add_argument("--run-task3", action="store_true", help="Run Task-3 road-buffer-constrained GA before adapting outputs")
    args = parser.parse_args()
    if not args.run_task3 and args.task3_input is None:
        parser.error("--task3-input is required unless --run-task3 is specified")

    # A Task-2 JSON and its region result are the authoritative upstream when
    # supplied.  Reusing the region path as the partition input keeps the
    # default pipeline single-source while still allowing legacy runs to
    # omit both and let Task 4 compute a local partition.
    task2_partition = args.task2_partition
    if task2_partition is None and args.task2_input is not None and args.task2_regions.is_file():
        task2_partition = args.task2_regions

    shape_dir = args.task4_shape_dir
    if args.task2_input is not None:
        from task4_input_contract import materialize_task4_shape_dir

        materialized_dir = args.materialized_task4_shape_dir or args.task4_output.parent / "task4_input_from_task2"
        shape_dir = materialize_task4_shape_dir(args.task2_input, shape_dir, materialized_dir)

    # Task 4 must establish the fixed PBO/BPE service relationships before the
    # Task-3 optimizer evaluates candidate PM coordinates.  The initial plan
    # is kept separate from the final fixed-PM rerun below.
    initial_task4_output = args.task4_output.parent / f"{args.task4_output.name}_initial"
    if args.run_task3:
        initial_task4_output.mkdir(parents=True, exist_ok=True)
        task4_command = [
            sys.executable,
            str(TASK4_DIR / "plan_pbo_bpe.py"),
            str(shape_dir),
            str(initial_task4_output),
        ]
        if task2_partition is not None:
            task4_command.extend(["--task2-partition", str(task2_partition)])
        subprocess.run(task4_command, cwd=TASK4_DIR, check=True)

        # Keep the caller's base input immutable.  Task-3 receives a copy with
        # the freshly generated Task-4 relationships attached.
        initial_task6_input = args.task6_input_output.parent / f"{args.task6_input_output.stem}_task4_initial.json"
        initial_task6_input.parent.mkdir(parents=True, exist_ok=True)
        initial_task6_input.write_text(
            args.task6_base_input.read_text(encoding="utf-8-sig"), encoding="utf-8"
        )
        attach_task4_plan_to_task6_input(initial_task6_input, initial_task4_output)
        subprocess.run(
            [
                sys.executable,
                str(TASK3_DIR / "run_task3.py"),
                "--algorithm",
                "ga",
                "--task6-input",
                str(initial_task6_input),
                "--task2-regions",
                str(args.task2_regions),
                "--output",
                str(args.task3_output),
                "--task6-output",
                str(args.task6_output),
                "--task6-visualization",
                str(args.task6_output.with_suffix(".png")),
            ],
            cwd=TASK3_DIR,
            check=True,
        )

    fixed_pm_path = args.task3_output / "pm_fixed_plan.json"
    if args.run_task3:
        fixed_plan = json.loads(fixed_pm_path.read_text(encoding="utf-8-sig"))
    else:
        if args.task3_input is None:  # defensive; parser validation above handles this
            raise ValueError("Task-3 input directory is required for an existing output")
        fixed_plan = build_fixed_pm_plan(args.task3_input, args.task3_output, fixed_pm_path)

    args.task4_output.mkdir(parents=True, exist_ok=True)
    final_task4_command = [
        sys.executable,
        str(TASK4_DIR / "plan_pbo_bpe.py"),
        str(shape_dir),
        str(args.task4_output),
        "--fixed-pm",
        str(fixed_pm_path),
    ]
    if task2_partition is not None:
        final_task4_command.extend(["--task2-partition", str(task2_partition)])
    subprocess.run(
        final_task4_command,
        cwd=TASK4_DIR,
        check=True,
    )

    layered_input = build_layered_task6_input(
        args.task6_base_input,
        fixed_pm_path,
        args.task6_input_output,
    )
    layered_input = attach_task4_plan_to_task6_input(
        args.task6_input_output,
        args.task4_output,
    )
    remote = layered_input.get("remote_sensing") or {}
    missing_remote = [name for name in ("roads", "building_groups", "entrances") if not remote.get(name)]
    if missing_remote:
        raise ValueError(
            "Stage-A task-6 evaluation requires layered remote-sensing inputs; "
            f"missing {', '.join(missing_remote)}"
        )
    missing_fixed_relations = [name for name in ("pbo_plan", "bpe_plan") if not layered_input.get(name)]
    if missing_fixed_relations:
        raise ValueError(
            "Stage-A layered task-6 input must carry fixed task-4 relationships; "
            f"missing {', '.join(missing_fixed_relations)}"
        )

    args.task6_output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            str(TASK6_DIR / "code" / "plan_task6.py"),
            "--input",
            str(args.task6_input_output),
            "--output",
            str(args.task6_output),
        ],
        cwd=PROJECT_DIR,
        check=True,
    )
    result = json.loads(args.task6_output.read_text(encoding="utf-8"))
    print(json.dumps({
        "fixed_pm_plan": str(fixed_pm_path),
        "task4_output": str(args.task4_output),
        "task6_output": str(args.task6_output),
        "pm_count": fixed_plan["counts"]["pm"],
        "task6_status": result.get("validation", {}).get("status"),
        "task6_cost": result.get("meta", {}).get("cost") or result.get("meta", {}).get("estimated_construction_cost"),
        "unresolved": len(result.get("validation", {}).get("unresolved", result.get("validation", {}).get("unresolved_access_routes", []))),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
