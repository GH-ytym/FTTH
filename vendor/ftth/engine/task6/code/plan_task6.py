"""Task 6 entry point: the layered fixed-assignment planner only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from project_paths import TASK6_PRODUCTION_INPUT, TASK6_PRODUCTION_OUTPUT_DIR, relative_to_root

try:
    from layered_planner import plan_layered
except ImportError:  # package import
    from .layered_planner import plan_layered


TASK_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = TASK6_PRODUCTION_INPUT
DEFAULT_OUTPUT = TASK6_PRODUCTION_OUTPUT_DIR / "layered_plan.json"


def plan(input_path: str | Path) -> dict[str, Any]:
    """Run the sole Task-6 algorithm on a layered planning input."""
    source = Path(input_path)
    result = plan_layered(source)
    # Preserve provenance and any explicit data-quality assumptions in the
    # serialized result so a plan remains auditable outside the CLI session.
    try:
        input_meta = json.loads(source.read_text(encoding="utf-8-sig")).get("meta") or {}
    except (OSError, json.JSONDecodeError):
        input_meta = {}
    result.setdefault("meta", {})["source_input"] = relative_to_root(source)
    if input_meta:
        result["meta"]["input_scenario"] = input_meta.get("scenario")
        result["meta"]["input_source_status"] = input_meta.get("source_status")
        if input_meta.get("road_attribute_assumption"):
            result["meta"]["road_attribute_assumption"] = input_meta["road_attribute_assumption"]
        source_status = input_meta.get("source_status")
        if source_status == "production_assumed":
            result.setdefault("validation", {})["data_quality_status"] = "PASS_WITH_EXPLICIT_ASSUMPTION"
            result["validation"]["data_quality_notes"] = [
                "All supplied roads were treated as open-cut eligible per the user's temporary assumption.",
                "Duct capacity was treated as unlimited per the user's temporary assumption.",
            ]
        elif source_status not in (None, "production_verified"):
            result.setdefault("validation", {})["data_quality_status"] = "REVIEW_REQUIRED"
            result["validation"]["data_quality_notes"] = [
                "Route feasibility PASS is conditional on the input's temporary construction-attribute assumption.",
                "Confirm OPEN_CUT_ALLOWED and duct capacity for every recognition road before construction release.",
            ]
    return result


def write_plan(result: dict[str, Any], output_path: str | Path) -> Path:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Task 6 layered fixed-assignment network planner")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    try:
        result = plan(arguments.input)
        destination = write_plan(result, arguments.output)
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        return 1
    print(json.dumps({"output": str(destination), "validation": result.get("validation"), "meta": result.get("meta")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
