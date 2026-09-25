"""Command-line entry point for the Task-3 GA optimizer."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .optimizer import OptimizationConfig, PMOptimizer
from .visualize import render_optimization


TASK3_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = TASK3_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from project_paths import TASK2_ZONE_PARTITION_OUTPUT_DIR, TASK3_FINAL_OUTPUT_DIR, TASK6_PRODUCTION_INPUT, TASK6_PRODUCTION_OUTPUT_DIR


DEFAULT_REGIONS = TASK2_ZONE_PARTITION_OUTPUT_DIR / "qgis_direct_region_partition_result.json"
DEFAULT_TASK6_OUTPUT = TASK6_PRODUCTION_OUTPUT_DIR / "layered_plan_optimized.json"
DEFAULT_TASK6_FIGURE = TASK6_PRODUCTION_OUTPUT_DIR / "layered_plan_optimized.png"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Task 3 PM optimization within a configurable road buffer")
    result.add_argument("--task6-input", type=Path, default=TASK6_PRODUCTION_INPUT)
    result.add_argument("--task2-regions", type=Path, default=DEFAULT_REGIONS)
    result.add_argument("--output", type=Path, default=TASK3_FINAL_OUTPUT_DIR)
    result.add_argument("--task6-output", type=Path, default=DEFAULT_TASK6_OUTPUT)
    result.add_argument("--task6-visualization", type=Path, default=DEFAULT_TASK6_FIGURE)
    result.add_argument("--candidate-spacing-m", type=float, default=8.0)
    result.add_argument("--candidate-lateral-spacing-m", type=float, default=5.0)
    result.add_argument("--pm-road-access-radius-m", type=float, default=10.0)
    result.add_argument("--candidate-min-separation-m", type=float, default=3.0)
    result.add_argument("--zpm-boundary-clearance-m", type=float, default=0.5)
    result.add_argument("--candidate-limit", type=int, default=24)
    result.add_argument("--population", type=int, default=14)
    result.add_argument("--generations", type=int, default=10)
    result.add_argument("--patience", type=int, default=5)
    result.add_argument("--seed", type=int, default=20260905)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    config = OptimizationConfig(
        candidate_spacing_m=args.candidate_spacing_m,
        candidate_lateral_spacing_m=args.candidate_lateral_spacing_m,
        pm_road_access_radius_m=args.pm_road_access_radius_m,
        candidate_min_separation_m=args.candidate_min_separation_m,
        zpm_boundary_clearance_m=args.zpm_boundary_clearance_m,
        candidate_limit_per_zpm=args.candidate_limit,
        population_size=args.population,
        generations=args.generations,
        patience=args.patience,
        seed=args.seed,
    )
    try:
        optimizer = PMOptimizer(args.task6_input, args.task2_regions, config)
        result = optimizer.run()
        paths = optimizer.write_outputs(result, args.output)
        task3_figure = render_optimization(optimizer, result, args.output / "pm_optimization.png")
        args.task6_output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(paths["task6_plan"], args.task6_output)
        task6_code = PROJECT_DIR / "任务6" / "code"
        if str(task6_code) not in sys.path:
            sys.path.insert(0, str(task6_code))
        from visualize_task6_plan import render as render_task6

        render_task6(args.task6_output, args.task6_visualization)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2))
        return 1
    summary = {
        "ok": not result.optimized.hard_violations,
        "algorithm": "constraint_first_memetic_ga_task6_feedback",
        "output": str(args.output.resolve()),
        "task6_output": str(args.task6_output.resolve()),
        "visualizations": {
            "task3": str(task3_figure.resolve()),
            "task6": str(args.task6_visualization.resolve()),
        },
        "evaluations": len(result.evaluations) + 2,
        "candidate_counts": {zpm_id: len(result.candidates[zpm_id]) for zpm_id in result.zpm_ids},
        "original": result.original.metrics,
        "initial": result.initial.metrics,
        "optimized": result.optimized.metrics,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
