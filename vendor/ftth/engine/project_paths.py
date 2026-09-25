"""Shared repository paths for the cross-task FTTH workflow.

Keeping these locations in one small module makes the hand-off between tasks
independent of the caller's current working directory.
"""

from pathlib import Path
from typing import Union
import os


ROOT_DIR = Path(__file__).resolve().parent
INPUT_DIR = ROOT_DIR / "Input"
OUTPUT_DIR = ROOT_DIR / "OutPut"
SCENARIO_NAME = "51IMB"
SCENARIO_INPUT_DIR = INPUT_DIR / SCENARIO_NAME
SCENARIO_OUTPUT_DIR = OUTPUT_DIR / SCENARIO_NAME
FINAL_LAYER_OUTPUT_DIR = SCENARIO_OUTPUT_DIR / "final_layers"


def task_input(task: Union[int, str], *parts: str) -> Path:
    return SCENARIO_INPUT_DIR / f"Task{task}" / Path(*parts)


def task_output(task: Union[int, str], *parts: str) -> Path:
    return SCENARIO_OUTPUT_DIR / f"Task{task}" / Path(*parts)


def relative_to_root(path: Union[str, Path]) -> str:
    """Return a portable project-relative path when ``path`` is in this repo."""
    resolved = Path(path).expanduser().resolve()
    try:
        return resolved.relative_to(ROOT_DIR).as_posix()
    except ValueError:
        return str(resolved)


def relative_path(path: Union[str, Path], base_dir: Union[str, Path]) -> str:
    """Return a POSIX relative path suitable for a JSON/config hand-off."""
    return Path(os.path.relpath(Path(path).resolve(), Path(base_dir).resolve())).as_posix()


# External recognition deliveries are system inputs.  They must not be mixed
# with the derived Task-1/2/6 planning products under ``OutPut``.
RECOGNITION_INPUT_DIR = SCENARIO_INPUT_DIR / "Recognition"
RECOGNITION_BUILDING_INPUT_DIR = RECOGNITION_INPUT_DIR / "building_delivery"
RECOGNITION_ROAD_INPUT_DIR = RECOGNITION_INPUT_DIR / "road_delivery"

# Stable module hand-off locations.  Import these instead of deriving paths
# from the caller's working directory.
TASK1_FINAL_INPUT_DIR = task_input(1, "final_data")
# Backwards-compatible symbol for callers that still import the old name.
TASK1_SAMPLE_INPUT_DIR = TASK1_FINAL_INPUT_DIR
TASK1_BASE_SURVEY_OUTPUT_DIR = task_output(1, "base_survey")
TASK1_TO_TASK2_TOPOLOGY = TASK1_BASE_SURVEY_OUTPUT_DIR / "qgis_ftth_dataset.json"

TASK2_ZONE_PARTITION_INPUT_DIR = task_input(2, "zone_partition_data")
TASK2_ZONE_PARTITION_OUTPUT_DIR = task_output(2, "zone_partition")
TASK2_RECOGNITION_INPUT = TASK2_ZONE_PARTITION_INPUT_DIR / "task2_recognition_input.json"
TASK2_CLEANED_INPUT = TASK2_ZONE_PARTITION_INPUT_DIR / "task2_cleaned_input.json"
TASK2_GEOMETRY_FEATURES_OUTPUT_DIR = TASK2_ZONE_PARTITION_OUTPUT_DIR / "recognition_input_entrance_features"

TASK3_FINAL_INPUT_DIR = task_input(3, "final_data")
TASK3_FINAL_OUTPUT_DIR = task_output(3, "final_output")
# Backwards-compatible symbols for the command-line entry point.
TASK3_SAMPLE_INPUT_DIR = TASK3_FINAL_INPUT_DIR
TASK3_SAMPLE_OUTPUT_DIR = TASK3_FINAL_OUTPUT_DIR
TASK3_OUTPUT_DIR = task_output(3)

TASK4_FINAL_INPUT_DIR = task_input(4, "final_data")
TASK4_SAMPLE_INPUT_DIR = TASK4_FINAL_INPUT_DIR
TASK4_OUTPUT_DIR = task_output(4)
TASK4_PBO_ONLY_OUTPUT_DIR = task_output(4, "pbo_only")
TASK4_PBO_BPE_OUTPUT_DIR = task_output(4, "pbo_bpe_plan")
TASK4_PM_PARTITION_OUTPUT_DIR = TASK4_PBO_BPE_OUTPUT_DIR / "task2_pm_partition"

TASK5_PRODUCTION_OUTPUT_DIR = task_output(5, "production")
TASK5_OUTPUT_DIR = task_output(5)

TASK6_PRODUCTION_INPUT = task_input(6, "layered_planning_production_input.json")
TASK6_PRODUCTION_OUTPUT_DIR = task_output(6, "production")
TASK6_RECOGNITION_OUTPUT_DIR = task_output(6, "recognition_cleaned")
TASK6_WHOLE_ROADS_OUTPUT_DIR = task_output(6, "recognition_whole_roads")
TASK6_INPUT_PREPARATION_OUTPUT_DIR = task_output(6, "input_preparation")

__all__ = [
    "ROOT_DIR",
    "INPUT_DIR",
    "OUTPUT_DIR",
    "SCENARIO_NAME",
    "SCENARIO_INPUT_DIR",
    "SCENARIO_OUTPUT_DIR",
    "FINAL_LAYER_OUTPUT_DIR",
    "task_input",
    "task_output",
    "relative_to_root",
    "relative_path",
    "RECOGNITION_INPUT_DIR",
    "RECOGNITION_BUILDING_INPUT_DIR",
    "RECOGNITION_ROAD_INPUT_DIR",
    "TASK1_FINAL_INPUT_DIR",
    "TASK1_SAMPLE_INPUT_DIR",
    "TASK1_BASE_SURVEY_OUTPUT_DIR",
    "TASK1_TO_TASK2_TOPOLOGY",
    "TASK2_ZONE_PARTITION_INPUT_DIR",
    "TASK2_ZONE_PARTITION_OUTPUT_DIR",
    "TASK2_RECOGNITION_INPUT",
    "TASK2_CLEANED_INPUT",
    "TASK2_GEOMETRY_FEATURES_OUTPUT_DIR",
    "TASK3_FINAL_INPUT_DIR",
    "TASK3_FINAL_OUTPUT_DIR",
    "TASK3_SAMPLE_INPUT_DIR",
    "TASK3_SAMPLE_OUTPUT_DIR",
    "TASK3_OUTPUT_DIR",
    "TASK4_FINAL_INPUT_DIR",
    "TASK4_SAMPLE_INPUT_DIR",
    "TASK4_OUTPUT_DIR",
    "TASK4_PBO_ONLY_OUTPUT_DIR",
    "TASK4_PBO_BPE_OUTPUT_DIR",
    "TASK4_PM_PARTITION_OUTPUT_DIR",
    "TASK5_PRODUCTION_OUTPUT_DIR",
    "TASK5_OUTPUT_DIR",
    "TASK6_PRODUCTION_INPUT",
    "TASK6_PRODUCTION_OUTPUT_DIR",
    "TASK6_RECOGNITION_OUTPUT_DIR",
    "TASK6_WHOLE_ROADS_OUTPUT_DIR",
    "TASK6_INPUT_PREPARATION_OUTPUT_DIR",
]
