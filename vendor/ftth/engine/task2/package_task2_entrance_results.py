"""Package the cleaned Task 2 input and its entrance result into one JSON.

The package deliberately keeps input and derived output under separate keys:
recognised polygons are ``building_groups`` while IMB-partitioned cells are
``buildings``.  This prevents the two project concepts from being conflated
when the result is handed to another task or reviewed by an operator.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SRC_DIR = Path(__file__).resolve().parent
ZONE_DIR = SRC_DIR.parent
ROOT_DIR = ZONE_DIR.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
from project_paths import TASK2_CLEANED_INPUT, TASK2_ZONE_PARTITION_OUTPUT_DIR

TASK2_ENTRANCE_OUTPUT_DIR = TASK2_ZONE_PARTITION_OUTPUT_DIR / "imb_semantic_entrance_features"
DEFAULT_INPUT = TASK2_CLEANED_INPUT
DEFAULT_ENTRANCE_OUTPUT = TASK2_ENTRANCE_OUTPUT_DIR / "task2_imb_entrance_features.json"
DEFAULT_OUTPUT = TASK2_ENTRANCE_OUTPUT_DIR / "task2_cleaned_input_with_entrances.json"


def load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def _require_list(payload: dict[str, Any], key: str, source: Path) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{source} must contain a list-valued {key!r}")
    return value


def _cleaned_input_check(input_data: dict[str, Any]) -> dict[str, Any]:
    recognition = input_data.get("recognition")
    if not isinstance(recognition, dict):
        raise ValueError("Input package has no recognition section")
    groups = recognition.get("building_groups")
    roads = recognition.get("roads")
    if not isinstance(groups, list) or not isinstance(roads, list):
        raise ValueError("Cleaned input requires recognition.building_groups and recognition.roads lists")
    validation = input_data.get("validation") or {}
    counts = validation.get("recognition_layer_counts") or {}
    return {
        "building_group_count": len(groups),
        "road_count": len(roads),
        "access_candidate_count": len(recognition.get("access_candidates") or []),
        "declared_layer_counts": counts,
        "cleaned_only": len(groups) == 6 and len(roads) == 397 and not recognition.get("access_candidates"),
    }


def build_package(input_data: dict[str, Any], entrance_data: dict[str, Any], *, input_path: Path, entrance_path: Path) -> dict[str, Any]:
    input_check = _cleaned_input_check(input_data)
    for key in ("building_groups", "buildings", "entrances", "building_group_entrances", "community_entrances"):
        _require_list(entrance_data, key, entrance_path)

    # Strip the retired building-entrance output even when this packager is
    # pointed at an older result file.  The combined package is the downstream
    # input contract, so stale entries must not leak into it.
    entrance_features = dict(entrance_data)
    entrance_features.pop("building_entrances", None)
    entrance_features["entrances"] = [
        item for item in entrance_features["entrances"]
        if str(item.get("entrance_type") or item.get("type") or "").lower() != "building"
    ]
    entrance_features["summary"] = dict(entrance_features.get("summary") or {})
    entrance_features["summary"].pop("building_entrance_count", None)
    entrance_features["summary"].pop("exact_building_entrance_count", None)
    entrance_features["validation"] = dict(entrance_features.get("validation") or {})
    entrance_features["validation"]["building_entrance_data_removed"] = True
    entrance_features["validation"]["unresolved"] = [
        item for item in entrance_features["validation"].get("unresolved") or []
        if str(item.get("type") or "").lower() != "building"
    ]
    entrance_features["summary"]["unresolved_count"] = len(entrance_features["validation"]["unresolved"])
    entrance_features["schema_version"] = "task2-imb-entrance-features-1.2"

    entrance_summary = entrance_features.get("summary") or {}
    input_validation = input_data.get("validation") or {}
    entrance_validation = entrance_features.get("validation") or {}
    unresolved = list(entrance_validation.get("unresolved") or [])
    status = "PASS" if input_validation.get("status") == "PASS" and not unresolved else "REVIEW_REQUIRED"

    return {
        "schema_version": "task2-cleaned-input-with-entrances-1.0",
        "package": {
            "role": "cleaned_task2_input_and_entrance_result",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "input_file": str(input_path),
            "entrance_result_file": str(entrance_path),
            "cleaned_only": input_check["cleaned_only"],
            "semantics": {
                "recognition.building_groups": "recognised containing building-group polygons",
                "entrance_features.buildings": "physical buildings derived from IMB Voronoi cells",
                "entrance_features.building_groups": "the same recognised building-group polygons with derived assignments",
                "entrance_features.community_entrances": "ZNRO service-area boundary entrances",
                "entrance_features.building_entrances": "removed; not required by the downstream algorithm",
            },
        },
        "input": input_data,
        "entrance_features": entrance_features,
        "summary": {
            "input": input_check,
            "entrance": entrance_summary,
            "status": status,
            "unresolved_count": len(unresolved),
        },
        "validation": {
            "status": status,
            "input_status": input_validation.get("status"),
            "input_cleaned_only": input_check["cleaned_only"],
            "building_group_count_matches_input": len(entrance_data["building_groups"]) == input_check["building_group_count"],
            "imb_building_count": len(entrance_data["buildings"]),
            "entrance_ids_unique": bool(entrance_validation.get("entrance_ids_unique", False)),
            "building_entrance_data_removed": "building_entrances" not in entrance_features and not any(str(item.get("entrance_type") or "").lower() == "building" for item in entrance_features["entrances"]),
            "unresolved": unresolved,
        },
        "visualizations": {
            "entrance_overview": str(entrance_path.with_name("task2_imb_entrance_features.png")),
            "entrance_focus": str(entrance_path.with_name("task2_imb_entrance_features_focus.png")),
            "imb_buildings_overview": str(entrance_path.with_name("imb_buildings_overview.png")),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Combine cleaned Task 2 input and entrance output into one JSON package.")
    parser.add_argument("--input-json", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--entrance-json", type=Path, default=DEFAULT_ENTRANCE_OUTPUT)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    package = build_package(
        load_object(args.input_json.resolve()),
        load_object(args.entrance_json.resolve()),
        input_path=args.input_json.resolve(),
        entrance_path=args.entrance_json.resolve(),
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Combined package: {args.output_json.resolve()}")
    print(json.dumps(package["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
