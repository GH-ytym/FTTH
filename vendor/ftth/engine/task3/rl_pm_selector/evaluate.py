"""确定性基线、RL 策略和回退结果的统一评估入口。"""

from __future__ import annotations

import json
import argparse
from copy import deepcopy
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from nro_pm_selector.io import InputBundle, write_outputs
from nro_pm_selector.selector import SelectionEngine

from .environment import PMSelectionEnv
from .export import run_policy
from .policy import MaskedPolicy
from .reward import task6_cost_summary


def _selection_summary(result: Any) -> dict[str, Any]:
    validation = result.validation
    return {
        "mode": getattr(result, "mode", "deterministic"),
        "passed": bool(validation.get("passed")),
        "total_route_cost": float(validation.get("total_route_cost", 0.0)),
        "selected_pm": sum(item.facility_type == "PM" for item in result.selections),
        "new_ptech": sum(item.candidate_source == "NEW_PTECH" for item in result.selections),
    }


def evaluate(bundle: InputBundle, policy: MaskedPolicy | None = None, episodes: int = 1) -> dict[str, Any]:
    # 基线选择会回填 Zone.ref_*；评估后还要在同一输入上运行 RL，故隔离副本。
    baseline = SelectionEngine(deepcopy(bundle)).run()
    report: dict[str, Any] = {"baseline": _selection_summary(baseline), "episodes": []}
    if policy is None:
        return report
    for episode in range(max(1, episodes)):
        result = run_policy(bundle, policy, greedy=True, fallback=True)
        summary = _selection_summary(result)
        summary["episode"] = episode + 1
        summary["fallback"] = result.mode == "deterministic_fallback"
        report["episodes"].append(summary)
    rl_costs = [item["total_route_cost"] for item in report["episodes"]]
    report["mean_route_cost"] = sum(rl_costs) / len(rl_costs) if rl_costs else None
    report["delta_vs_baseline"] = (
        report["mean_route_cost"] - report["baseline"]["total_route_cost"]
        if report["mean_route_cost"] is not None else None
    )
    return report


def evaluate_task6(
    task6_input: str | Path,
    output_path: str | Path | None = None,
    task4_output: str | Path | None = None,
) -> dict[str, Any]:
    """调用任务六规划器，提取稳定的终局成本指标。"""
    input_path = Path(task6_input)
    if output_path:
        output = Path(output_path)
    else:
        fd, temporary_name = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        output = Path(temporary_name)
    task6_dir = Path(__file__).resolve().parents[1].parent / "任务6"
    command = [sys.executable, str(task6_dir / "code" / "plan_task6.py"), "--input", str(input_path), "--output", str(output)]
    subprocess.run(command, cwd=task6_dir.parent, check=True)
    return task6_cost_summary(json.loads(output.read_text(encoding="utf-8")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare deterministic and RL PM selection")
    parser.add_argument("--input", required=True)
    parser.add_argument("--model")
    parser.add_argument("--episodes", type=int, default=1)
    parser.add_argument("--task6-input")
    parser.add_argument("--task6-output")
    args = parser.parse_args(argv)
    bundle = InputBundle.load(args.input)
    policy = MaskedPolicy.load(args.model) if args.model else None
    report = evaluate(bundle, policy, args.episodes)
    if args.task6_input:
        report["task6"] = evaluate_task6(args.task6_input, args.task6_output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
