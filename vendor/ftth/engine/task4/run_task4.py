"""Run the final task-4 PBO/BPE planner.

The optional Task-2 and Task-3 arguments make this entry point reproducible:
Task-2 owns the IMB/ZPM membership, and a Task-3 fixed plan owns PM
coordinates.  Omitting them keeps the historical local-sample behaviour.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from plan_pbo_bpe import draw_comparison, plan as plan_pbo_bpe
from plan_pbo_bpe import write_outputs
from project_paths import TASK4_FINAL_INPUT_DIR, TASK4_OUTPUT_DIR


TASK_DIR = Path(__file__).resolve().parent
SHAPE_DIR = TASK4_FINAL_INPUT_DIR
OUTPUT_DIR = TASK4_OUTPUT_DIR


def run(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate the Task-4 PBO/BPE plan")
    parser.add_argument("--shape-dir", type=Path, default=SHAPE_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR / "pbo_bpe_plan")
    parser.add_argument("--fixed-pm", type=Path, default=None)
    parser.add_argument("--task2-partition", type=Path, default=None)
    parser.add_argument(
        "--task2-input",
        type=Path,
        default=None,
        help="Materialize a Task-4 IMB layer from this Task-2 JSON before planning.",
    )
    parser.add_argument(
        "--materialized-shape-dir",
        type=Path,
        default=None,
        help="Destination for the Task-2-derived Task-4 input directory.",
    )
    args = parser.parse_args(argv)

    shape_dir = args.shape_dir
    if args.task2_input is not None:
        from task4_input_contract import materialize_task4_shape_dir

        materialized = args.materialized_shape_dir or args.output_dir.parent / "task4_input_from_task2"
        shape_dir = materialize_task4_shape_dir(args.task2_input, shape_dir, materialized)

    pbo_bpe_dir = args.output_dir
    pbos, bpes, mappings = plan_pbo_bpe(
        shape_dir,
        pbo_bpe_dir / "task2_pm_partition",
        fixed_pm_path=args.fixed_pm,
        task2_partition_path=args.task2_partition,
    )
    write_outputs(shape_dir, pbo_bpe_dir, pbos, bpes, mappings)
    draw_comparison(shape_dir, pbo_bpe_dir, pbos, bpes)

    print(f"Final input shapefiles: {shape_dir}")
    print(f"PBO/BPE outputs: {pbo_bpe_dir}")
    print(f"Planned {len(pbos)} PBOs, {len(bpes)} BPEs, and {len(pbos) + len(bpes)} new PTECH points.")


if __name__ == "__main__":
    run()
