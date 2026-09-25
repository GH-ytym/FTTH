"""任务三统一命令行入口，默认执行道路约束遗传优化。

示例：
    python run_task3.py
    python run_task3.py --algorithm deterministic --input example_input --output example_output
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from nro_pm_selector.cli import main as deterministic_main
from nro_pm_selector.io import InputBundle


TASK3_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TASK3_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from project_paths import TASK3_FINAL_INPUT_DIR, TASK3_FINAL_OUTPUT_DIR


DEFAULT_INPUT = TASK3_FINAL_INPUT_DIR
DEFAULT_OUTPUT = TASK3_FINAL_OUTPUT_DIR


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--algorithm", choices=("ga", "deterministic", "rl", "rl_with_fallback"), default="ga")
    parser.add_argument("--model")
    parsed, remaining = parser.parse_known_args(argv)
    if parsed.algorithm == "ga":
        from ga_pm_optimizer.cli import main as ga_main

        return ga_main(list(remaining))
    if parsed.algorithm == "deterministic":
        remaining = list(remaining)
        if not any(item == "--input" or item.startswith("--input=") for item in remaining):
            remaining.extend(["--input", str(DEFAULT_INPUT)])
        if not any(item == "--output" or item.startswith("--output=") for item in remaining):
            remaining.extend(["--output", str(DEFAULT_OUTPUT)])
        return deterministic_main(remaining)
    if not parsed.model:
        parser.error("--algorithm rl 或 rl_with_fallback 必须提供 --model")
    output_parser = argparse.ArgumentParser(description="任务三 RL PM 选址")
    output_parser.add_argument("--input", default=str(DEFAULT_INPUT))
    output_parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    output_parser.add_argument("--config")
    args = output_parser.parse_args(remaining)
    from rl_pm_selector.export import export_result, run_policy
    from rl_pm_selector.features import build_feature_table
    from rl_pm_selector.policy import MaskedPolicy
    bundle = InputBundle.load(args.input, args.config)
    table = build_feature_table(bundle)
    result = run_policy(bundle, MaskedPolicy.load(parsed.model), greedy=True, fallback=parsed.algorithm == "rl_with_fallback")
    if not result.validation["passed"] and parsed.algorithm == "rl":
        print(json.dumps({"ok": False, "error": "RL 结果未通过任务三校验"}, ensure_ascii=False))
        return 2
    export_result(bundle, result, args.output, table)
    print(json.dumps({"ok": result.validation["passed"], "algorithm": parsed.algorithm, "actual_mode": result.mode, "output": args.output, "validation": result.validation}, ensure_ascii=False, indent=2))
    return 0 if result.validation["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
