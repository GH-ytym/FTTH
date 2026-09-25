"""Command-line entry point for Task 5 fiber-link planning."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from task5_core import execute, load_json


TASK_DIR = Path(__file__).resolve().parent


def run(config_path: str | Path) -> dict[str, Any]:
    config, base_dir = load_json(config_path)
    result = execute(config, base_dir)
    print(json.dumps({"validation": result["validation"], "outputs": result["output_paths"]}, ensure_ascii=False, indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Task 5: hierarchical fiber links on Task-6 conduits")
    parser.add_argument("config", nargs="?", type=Path, default=TASK_DIR / "task5_config.json")
    arguments = parser.parse_args()
    try:
        run(arguments.config)
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
