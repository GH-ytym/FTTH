"""Render a Task-5 hierarchical fiber-link plan for engineering review.

The renderer is intentionally dependency-light: it reads the JSON plan emitted by
``run_task5.py`` and uses matplotlib only for the final PNG. Missing endpoint
coordinates (for example, an NRO materialized from a route endpoint) are inferred
from the first/last coordinate of its connected link.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from project_paths import TASK5_PRODUCTION_OUTPUT_DIR


TASK_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = TASK5_PRODUCTION_OUTPUT_DIR / "fiber_link_plan.json"
DEFAULT_OUTPUT = TASK5_PRODUCTION_OUTPUT_DIR / "fiber_link_overview.png"

STAGE_STYLES = {
    "NRO_TO_PM": ("#1d4ed8", "NRO to PM"),
    "PM_TO_BPE": ("#7c3aed", "PM to BPE"),
    "BPE_TO_PBO": ("#ea580c", "BPE to PBO"),
    "PBO_TO_IMB": ("#64748b", "PBO to IMB"),
}
DEVICE_STYLES = {
    "nro": ("#111827", "s", 70, "NRO"),
    "pm": ("#1d4ed8", "^", 64, "PM"),
    "bpe": ("#dc2626", "D", 48, "BPE"),
    "pbo": ("#0f766e", "o", 28, "PBO"),
    "imb": ("#475569", ".", 22, "IMB"),
}


def _coordinates(value: Any) -> list[tuple[float, float]]:
    if not isinstance(value, list):
        return []
    points: list[tuple[float, float]] = []
    for item in value:
        try:
            if isinstance(item, dict):
                points.append((float(item["x"]), float(item["y"])))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                points.append((float(item[0]), float(item[1])))
        except (KeyError, TypeError, ValueError):
            continue
    return points


def _record_id(record: dict[str, Any]) -> str:
    for key in ("id", "code", "name"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return ""


def _device_points(plan: dict[str, Any]) -> dict[str, dict[str, tuple[float, float]]]:
    devices: dict[str, dict[str, tuple[float, float]]] = {kind: {} for kind in DEVICE_STYLES}
    raw_devices = plan.get("devices") if isinstance(plan.get("devices"), dict) else {}
    for kind in DEVICE_STYLES:
        for record in raw_devices.get(kind, []) or []:
            if not isinstance(record, dict):
                continue
            try:
                devices[kind][_record_id(record)] = (float(record["x"]), float(record["y"]))
            except (KeyError, TypeError, ValueError):
                pass

    # Route endpoints are authoritative when a device record has no coordinates.
    for link in plan.get("links") or []:
        if not isinstance(link, dict):
            continue
        coords = _coordinates(link.get("coordinates"))
        if len(coords) < 2:
            continue
        for endpoint_key, index in (("from_code", 0), ("to_code", -1)):
            endpoint = str(link.get(endpoint_key) or "").strip()
            endpoint_type = str(link.get("from_type" if endpoint_key == "from_code" else "to_type") or "").lower()
            if endpoint and endpoint_type in devices and endpoint not in devices[endpoint_type]:
                devices[endpoint_type][endpoint] = coords[index]
    return devices


def _line_width(cable_cores: Any) -> float:
    try:
        cores = float(cable_cores)
    except (TypeError, ValueError):
        cores = 24.0
    return 1.2 + min(3.0, max(0.0, cores / 72.0))


def render(input_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    source = Path(input_path)
    destination = Path(output_path)
    plan = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(plan, dict) or not isinstance(plan.get("links"), list):
        raise ValueError("Task-5 plan must be an object containing a links list")

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    links = [item for item in plan["links"] if isinstance(item, dict)]
    devices = _device_points(plan)
    status = str((plan.get("validation") or {}).get("status") or "UNKNOWN")
    stage_counts = Counter(str(link.get("stage") or "UNKNOWN") for link in links)
    core_counts = Counter(str(link.get("cable_cores") or "UNKNOWN") for link in links)
    stage_lengths: defaultdict[str, float] = defaultdict(float)
    for link in links:
        try:
            stage_lengths[str(link.get("stage") or "UNKNOWN")] += float(link.get("length_m") or 0.0)
        except (TypeError, ValueError):
            continue

    figure = plt.figure(figsize=(15, 9), constrained_layout=True)
    grid = figure.add_gridspec(1, 2, width_ratios=(4.8, 1.35))
    axis = figure.add_subplot(grid[0, 0])
    summary = figure.add_subplot(grid[0, 1])

    seen_stages: set[str] = set()
    for link in links:
        coords = _coordinates(link.get("coordinates"))
        if len(coords) < 2:
            continue
        stage = str(link.get("stage") or "UNKNOWN")
        color, _ = STAGE_STYLES.get(stage, ("#94a3b8", stage))
        linestyle = "--" if str(link.get("physical_path_status") or "") == "INDOOR_LINK" else "-"
        axis.plot(
            [point[0] for point in coords],
            [point[1] for point in coords],
            color=color,
            linewidth=_line_width(link.get("cable_cores")),
            linestyle=linestyle,
            alpha=0.82 if linestyle == "-" else 0.58,
            zorder=3,
        )
        seen_stages.add(stage)

    for kind, (color, marker, size, label) in DEVICE_STYLES.items():
        records = devices[kind]
        if not records:
            continue
        xs, ys = zip(*records.values())
        axis.scatter(xs, ys, marker=marker, s=size, color=color, edgecolors="white", linewidths=0.65, zorder=7, label=f"{label} ({len(records)})")
        # Labels are limited to the higher-level devices to keep dense IMB/PBO maps readable.
        if kind in {"nro", "pm", "bpe"}:
            for identifier, (x, y) in records.items():
                axis.annotate(identifier, (x, y), xytext=(4, 4), textcoords="offset points", fontsize=7, color="#0f172a", zorder=8)

    route_legend = [
        Line2D([0], [0], color=STAGE_STYLES[stage][0], linewidth=2.4, label=STAGE_STYLES[stage][1])
        for stage in sorted(seen_stages)
        if stage in STAGE_STYLES
    ]
    handles, labels = axis.get_legend_handles_labels()
    axis.legend([*route_legend, *handles], [item.get_label() for item in route_legend] + labels, loc="best", fontsize=8, framealpha=0.94)
    axis.set_title(f"Task 5 hierarchical fiber links ({status})", fontsize=15, pad=12)
    axis.set_xlabel("Longitude / X")
    axis.set_ylabel("Latitude / Y")
    axis.set_aspect("equal", adjustable="box")
    axis.margins(0.05)
    axis.grid(True, alpha=0.18)

    summary.axis("off")
    summary.set_title("Plan summary", loc="left", fontsize=13, pad=8)
    summary.text(0.0, 0.93, f"Validation: {status}", transform=summary.transAxes, fontsize=11, fontweight="bold", color="#15803d" if status == "PASS" else "#b91c1c")
    summary.text(0.0, 0.86, f"Total links: {len(links)}", transform=summary.transAxes, fontsize=10)
    summary.text(0.0, 0.81, f"Mapped links: {sum(1 for link in links if link.get('physical_path_status') == 'MATCHED_TASK6_CONDUIT')}", transform=summary.transAxes, fontsize=10)
    summary.text(0.0, 0.76, f"Review links: {sum(1 for link in links if link.get('physical_path_status') == 'REVIEW_REQUIRED')}", transform=summary.transAxes, fontsize=10)
    summary.text(0.0, 0.68, "Links by stage", transform=summary.transAxes, fontsize=10, fontweight="bold")
    y = 0.63
    for stage in ("NRO_TO_PM", "PM_TO_BPE", "BPE_TO_PBO", "PBO_TO_IMB"):
        if stage_counts.get(stage):
            label = STAGE_STYLES.get(stage, ("", stage))[1]
            summary.text(0.0, y, f"{label}: {stage_counts[stage]}", transform=summary.transAxes, fontsize=9)
            summary.text(0.98, y, f"{stage_lengths.get(stage, 0.0):.1f} m", transform=summary.transAxes, fontsize=9, ha="right")
            y -= 0.045
    summary.text(0.0, y - 0.02, "Cable cores", transform=summary.transAxes, fontsize=10, fontweight="bold")
    y -= 0.07
    for cores in ("24", "48", "72", "144"):
        if core_counts.get(cores):
            summary.text(0.0, y, f"{cores}-core: {core_counts[cores]}", transform=summary.transAxes, fontsize=9)
            y -= 0.045
    summary.text(0.0, 0.04, "Solid = Task-6 conduit; dashed = indoor link", transform=summary.transAxes, fontsize=8, color="#475569")

    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination, dpi=220)
    plt.close(figure)
    return {
        "status": status,
        "links": len(links),
        "stage_counts": dict(stage_counts),
        "core_counts": dict(core_counts),
        "output": str(destination),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Visualize a Task-5 hierarchical fiber-link plan")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    try:
        stats = render(arguments.input, arguments.output)
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        return 1
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
