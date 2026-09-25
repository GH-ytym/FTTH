"""Compatibility-preserving orchestration for the task-2 modules.

The existing task-2 scripts contain the established algorithms and remain the
source of truth.  This file only separates the workflow into explicit stages
and invokes those scripts with their existing command-line contracts.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Sequence


SRC_DIR = Path(__file__).resolve().parent
ZONE_DIR = SRC_DIR.parent
TASK2_DIR = ZONE_DIR.parent
ROOT_DIR = TASK2_DIR.parent
FORMAL_SRC_DIR = ZONE_DIR / "src"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
from project_paths import (
    TASK2_CLEANED_INPUT,
    TASK2_RECOGNITION_INPUT,
    TASK2_ZONE_PARTITION_OUTPUT_DIR,
)

DEFAULT_INPUT = TASK2_RECOGNITION_INPUT
DEFAULT_OUTPUT_DIR = TASK2_ZONE_PARTITION_OUTPUT_DIR


def _command(script: Path, *arguments: object) -> list[str]:
    return [sys.executable, str(script), *(str(argument) for argument in arguments)]


def _run(command: Sequence[str], *, dry_run: bool) -> None:
    print("[task2] " + " ".join(command))
    if not dry_run:
        subprocess.run(list(command), cwd=SRC_DIR, check=True)


def build_commands(
    *,
    input_json: Path,
    output_dir: Path,
    source_project_dir: Path | None = None,
    build_imb_model: bool = False,
    building_gpkg: Path | None = None,
    building_groups_from_input: bool = False,
    building_threshold_m: float = 15.0,
    geometry_features_gpkg: Path | None = None,
    geometry_features_buildings: Path | None = None,
    geometry_features_roads: Path | None = None,
    geometry_features_from_input: bool = False,
    imb_entrance_features_from_input: bool = False,
    imb_building_snap_tolerance_m: float = 5.0,
    geometry_distance_threshold_m: float = 15.0,
    geometry_road_snap_tolerance_m: float = 2.0,
    geometry_building_crs: str = "EPSG:4326",
    geometry_road_crs: str = "EPSG:4326",
    geometry_partition_crs: str = "EPSG:4326",
    znro_boundary_extension_m: float = 20.0,
    zpm_boundary_extension_m: float | None = None,
    nro_max_cap: int = 2048,
    zpm_max_cap: int = 576,
    spatial_weight: float = 1.0,
    pipe_weight: float = 100.0,
    cleaned_input: Path | None = None,
) -> list[list[str]]:
    """Return stage commands without changing any algorithm implementation."""
    output_dir.mkdir(parents=True, exist_ok=True)
    commands: list[list[str]] = []

    if source_project_dir is not None:
        commands.append(
            _command(
                FORMAL_SRC_DIR / "extract_qgis_ftth_dataset.py",
                "--project-dir",
                source_project_dir,
                "--output-json",
                input_json,
            )
        )

    if build_imb_model:
        commands.append(
            _command(
                FORMAL_SRC_DIR / "extract_imb_pm_model.py",
                "--input-json",
                input_json,
                "--output-json",
                output_dir / "imb_pm_model.json",
            )
        )

    # Partitioning is intentionally IMB-only. Keep the full input for the
    # optional recognition stages, but pass the cleaned topology to ZNRO/ZPM.
    # Keep every derived file inside this invocation's output directory by
    # default.  A caller may still provide an explicit compatibility path,
    # but no stage implicitly reads another run's files.
    partition_input_json = cleaned_input or (output_dir / "task2_imb_only_input.json")
    partition_input_json.parent.mkdir(parents=True, exist_ok=True)
    commands.append(
        _command(
            SRC_DIR / "clean_imb_topology_input.py",
            "--input-json",
            input_json,
            "--output-json",
            partition_input_json,
        )
    )

    cluster_json = output_dir / "qgis_petch_atomic_result.json"
    cluster_png = output_dir / "qgis_petch_atomic_result.png"
    commands.append(
        _command(
            SRC_DIR / "znro_zpm_partition_petch_atomic.py",
            "--input-json",
            partition_input_json,
            "--output-json",
            cluster_json,
            "--output-fig",
            cluster_png,
            "--nro-max-cap",
            nro_max_cap,
            "--zpm-max-cap",
            zpm_max_cap,
            "--spatial-weight",
            spatial_weight,
            "--pipe-weight",
            pipe_weight,
        )
    )

    region_json = output_dir / "qgis_direct_region_partition_result.json"
    region_png = output_dir / "qgis_direct_region_partition_result.png"
    commands.append(
        _command(
            SRC_DIR / "znro_zpm_region_partition.py",
            "--input-json",
            cluster_json,
            "--source-data-json",
            partition_input_json,
            "--output-json",
            region_json,
            "--output-fig",
            region_png,
            "--nro-max-cap",
            nro_max_cap,
            "--zpm-max-cap",
            zpm_max_cap,
            "--spatial-weight",
            spatial_weight,
            "--pipe-weight",
            pipe_weight,
            "--znro-boundary-extension-m",
            znro_boundary_extension_m,
        )
    )
    if zpm_boundary_extension_m is not None:
        commands[-1].extend(["--zpm-boundary-extension-m", str(zpm_boundary_extension_m)])

    if building_gpkg is not None or building_groups_from_input:
        building_input_arguments: list[object]
        if building_groups_from_input:
            building_input_arguments = ["--input-json", input_json]
        else:
            building_input_arguments = ["--input-gpkg", building_gpkg]
        commands.append(
            _command(
                SRC_DIR / "building_group_partition.py",
                *building_input_arguments,
                "--distance-threshold-m",
                building_threshold_m,
                "--output-json",
                output_dir / "building_groups.json",
                "--output-csv",
                output_dir / "building_group_members.csv",
                "--output-fig",
                output_dir / "building_groups.png",
            )
        )

    if geometry_features_from_input or geometry_features_gpkg is not None or geometry_features_buildings is not None or geometry_features_roads is not None:
        if geometry_features_from_input:
            geometry_buildings = input_json
            geometry_roads = input_json
        elif geometry_features_gpkg is not None:
            geometry_buildings = geometry_features_gpkg
            geometry_roads = geometry_features_gpkg
        elif geometry_features_buildings is not None and geometry_features_roads is not None:
            geometry_buildings = geometry_features_buildings
            geometry_roads = geometry_features_roads
        else:
            raise ValueError("geometry feature calculation needs a GeoPackage or both buildings and roads sources")
        commands.append(
            _command(
                FORMAL_SRC_DIR / "task2_geometry_features.py",
                "--buildings", geometry_buildings,
                "--roads", geometry_roads,
                "--partition-json", region_json,
                "--output-json", output_dir / "task2_geometry_features.json",
                "--output-fig", output_dir / "task2_geometry_features.png",
                "--task6-output-dir", output_dir,
                "--building-crs", geometry_building_crs,
                "--road-crs", geometry_road_crs,
                "--partition-crs", geometry_partition_crs,
                "--distance-threshold-m", geometry_distance_threshold_m,
                "--road-snap-tolerance-m", geometry_road_snap_tolerance_m,
            )
        )

    if imb_entrance_features_from_input:
        imb_buildings_geojson = output_dir / "imb_buildings.geojson"
        commands.append(
            _command(
                ROOT_DIR / "任务6" / "code" / "preparation" / "partition_buildings_by_imb.py",
                "--groups", input_json,
                "--imb", input_json,
                "--output", imb_buildings_geojson,
                "--report", output_dir / "imb_buildings_report.json",
                "--visualization", output_dir / "imb_buildings_overview.png",
                "--snap-tolerance-m", imb_building_snap_tolerance_m,
            )
        )
        commands.append(
            _command(
                FORMAL_SRC_DIR / "task2_imb_entrance_features.py",
                "--building-groups", input_json,
                "--imb-buildings", imb_buildings_geojson,
                "--roads", input_json,
                "--topology-json", input_json,
                "--partition-json", region_json,
                "--output-json", output_dir / "task2_imb_entrance_features.json",
                "--output-fig", output_dir / "task2_imb_entrance_features.png",
                "--task6-output-dir", output_dir,
            )
        )

    return commands


def run_pipeline(**options: object) -> None:
    dry_run = bool(options.get("dry_run", False))
    command_options = {key: value for key, value in options.items() if key != "dry_run"}
    commands = build_commands(**command_options)
    for command in commands:
        _run(command, dry_run=dry_run)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run task 2 as separated data, partition, region and building-group stages."
    )
    parser.add_argument(
        "--input-json",
        type=Path,
        default=DEFAULT_INPUT,
        help="Dedicated task-2 input JSON.",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--cleaned-input",
        type=Path,
        default=None,
        help="Optional explicit IMB-only hand-off path; defaults to <output-dir>/task2_imb_only_input.json.",
    )
    parser.add_argument("--source-project-dir", type=Path, default=None)
    parser.add_argument("--build-imb-model", action="store_true")
    parser.add_argument("--building-gpkg", type=Path, default=None)
    parser.add_argument("--building-groups-from-input", action="store_true", help="Read recognition.buildings from --input-json")
    parser.add_argument("--building-threshold-m", type=float, default=15.0)
    parser.add_argument("--geometry-features-gpkg", type=Path, default=None, help="GeoPackage with GEO_BUILDING/GEO_ROAD layers")
    parser.add_argument("--geometry-features-buildings", type=Path, default=None, help="Building GeoJSON/JSON source")
    parser.add_argument("--geometry-features-roads", type=Path, default=None, help="Road GeoJSON/JSON source")
    parser.add_argument("--geometry-features-from-input", action="store_true", help="Read recognition.buildings and recognition.roads from --input-json")
    parser.add_argument("--imb-entrance-features-from-input", action="store_true", help="Treat recognition.building_groups as groups and derive buildings from IMB partitions")
    parser.add_argument("--imb-building-snap-tolerance-m", type=float, default=5.0)
    parser.add_argument("--geometry-distance-threshold-m", type=float, default=15.0)
    parser.add_argument("--geometry-road-snap-tolerance-m", type=float, default=2.0)
    parser.add_argument("--geometry-building-crs", default="EPSG:4326")
    parser.add_argument("--geometry-road-crs", default="EPSG:4326")
    parser.add_argument("--geometry-partition-crs", default="EPSG:4326")
    parser.add_argument("--znro-boundary-extension-m", type=float, default=20.0)
    parser.add_argument(
        "--zpm-boundary-extension-m",
        type=float,
        default=None,
        help="ZPM metric extension; defaults to the ZNRO extension.",
    )
    parser.add_argument("--nro-max-cap", type=int, default=2048)
    parser.add_argument("--zpm-max-cap", type=int, default=576)
    parser.add_argument("--spatial-weight", type=float, default=1.0)
    parser.add_argument("--pipe-weight", type=float, default=100.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.nro_max_cap <= 0 or args.zpm_max_cap <= 0:
        parser.error("capacity limits must be positive")
    if args.spatial_weight < 0 or args.pipe_weight < 0:
        parser.error("distance weights must be non-negative")
    if args.spatial_weight + args.pipe_weight <= 0:
        parser.error("at least one distance weight must be positive")
    if args.building_threshold_m < 0:
        parser.error("building threshold must be non-negative")
    if args.geometry_distance_threshold_m < 0 or args.geometry_road_snap_tolerance_m < 0:
        parser.error("geometry feature distances must be non-negative")
    if args.znro_boundary_extension_m < 0:
        parser.error("ZNRO boundary extension must be non-negative")
    if args.zpm_boundary_extension_m is not None and args.zpm_boundary_extension_m < 0:
        parser.error("ZPM boundary extension must be non-negative")
    if args.imb_building_snap_tolerance_m < 0:
        parser.error("IMB building snap tolerance must be non-negative")
    if bool(args.geometry_features_buildings) != bool(args.geometry_features_roads):
        parser.error("provide both --geometry-features-buildings and --geometry-features-roads")
    if args.building_gpkg and args.building_groups_from_input:
        parser.error("choose --building-gpkg or --building-groups-from-input, not both")
    if args.geometry_features_from_input and args.imb_entrance_features_from_input:
        parser.error("choose one input geometry mode")
    return args


def main() -> None:
    args = parse_args()
    run_pipeline(
        input_json=args.input_json.resolve(),
        output_dir=args.output_dir.resolve(),
        cleaned_input=args.cleaned_input.resolve() if args.cleaned_input else None,
        source_project_dir=args.source_project_dir.resolve() if args.source_project_dir else None,
        build_imb_model=args.build_imb_model,
        building_gpkg=args.building_gpkg.resolve() if args.building_gpkg else None,
        building_groups_from_input=args.building_groups_from_input,
        building_threshold_m=args.building_threshold_m,
        geometry_features_gpkg=args.geometry_features_gpkg.resolve() if args.geometry_features_gpkg else None,
        geometry_features_buildings=args.geometry_features_buildings.resolve() if args.geometry_features_buildings else None,
        geometry_features_roads=args.geometry_features_roads.resolve() if args.geometry_features_roads else None,
        geometry_features_from_input=args.geometry_features_from_input,
        imb_entrance_features_from_input=args.imb_entrance_features_from_input,
        imb_building_snap_tolerance_m=args.imb_building_snap_tolerance_m,
        geometry_distance_threshold_m=args.geometry_distance_threshold_m,
        geometry_road_snap_tolerance_m=args.geometry_road_snap_tolerance_m,
        geometry_building_crs=args.geometry_building_crs,
        geometry_road_crs=args.geometry_road_crs,
        geometry_partition_crs=args.geometry_partition_crs,
        znro_boundary_extension_m=args.znro_boundary_extension_m,
        zpm_boundary_extension_m=args.zpm_boundary_extension_m,
        nro_max_cap=args.nro_max_cap,
        zpm_max_cap=args.zpm_max_cap,
        spatial_weight=args.spatial_weight,
        pipe_weight=args.pipe_weight,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
