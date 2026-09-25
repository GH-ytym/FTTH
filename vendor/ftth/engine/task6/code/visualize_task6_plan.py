"""Render a Task-6 layered planning result for review."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from project_paths import TASK6_PRODUCTION_OUTPUT_DIR


TASK_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = TASK6_PRODUCTION_OUTPUT_DIR / "layered_plan.json"
DEFAULT_OUTPUT = TASK6_PRODUCTION_OUTPUT_DIR / "layered_plan.png"
ROUTE_STYLES = {
    "NRO_TO_PM_MAIN": ("#1d4ed8", 3.0, "NRO to PM main"),
    "PM_TO_GROUP_ENTRY": ("#7c3aed", 2.0, "PM to group entry"),
    "GROUP_ENTRY_TO_BPE": ("#15803d", 1.5, "Group entry to BPE"),
    "BPE_TO_BPE": ("#0891b2", 1.4, "Shared BPE conduit"),
    "BPE_TO_PBO": ("#ea580c", 1.2, "BPE to PBO"),
}
ACCESS_STYLES = {
    "nro_access_fibers": ("#111827", 1.6, "NRO direct fibre access"),
    "pm_access_fibers": ("#d97706", 1.6, "PM direct fibre access"),
}


def _points(value: Any) -> tuple[list[float], list[float]]:
    coordinates = value.get("coordinates") or []
    return [float(item["x"]) for item in coordinates], [float(item["y"]) for item in coordinates]


def render(input_path: str | Path, output_path: str | Path) -> Path:
    source, destination = Path(input_path), Path(output_path)
    data = json.loads(source.read_text(encoding="utf-8-sig"))
    algorithm = str(data.get("meta", {}).get("algorithm") or "")
    if not algorithm.startswith("layered_"):
        raise ValueError("Task 6 visualizer accepts only a layered planning result")

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    figure, axis = plt.subplots(figsize=(13, 8), constrained_layout=True)
    for road in data.get("trunk_roads") or []:
        xs, ys = _points(road)
        if xs:
            axis.plot(xs, ys, color="#a1a1aa", linewidth=2.8, alpha=0.65, zorder=1)
    seen_types: set[str] = set()
    for route in data.get("duct_routes") or []:
        route_type = str(route.get("route_type") or "")
        style = ROUTE_STYLES.get(route_type)
        if not style:
            continue
        xs, ys = _points(route)
        if xs:
            axis.plot(xs, ys, color=style[0], linewidth=style[1], alpha=0.9, zorder=3)
            seen_types.add(route_type)
    seen_access: set[str] = set()
    for key, style in ACCESS_STYLES.items():
        for access in data.get(key) or []:
            xs, ys = _points(access)
            if xs:
                axis.plot(xs, ys, color=style[0], linewidth=style[1], linestyle="--", alpha=0.95, zorder=4)
                seen_access.add(key)

    fixed = data.get("fixed_assignments") or {}
    device_styles = {
        "nro_sites": ("#111827", "s", 72, "NRO"),
        "pm_sites": ("#1d4ed8", "^", 62, "PM"),
    }
    for key, (color, marker, size, label) in device_styles.items():
        records = fixed.get(key) or []
        xs = [item["x"] for item in records if "x" in item and "y" in item]
        ys = [item["y"] for item in records if "x" in item and "y" in item]
        if xs:
            axis.scatter(xs, ys, marker=marker, s=size, color=color, edgecolors="white", linewidths=0.7, zorder=7, label=label)
    for key, color, marker, size, label in (("bpe_plan", "#dc2626", "D", 55, "BPE"), ("pbo_plan", "#0f766e", "o", 32, "PBO"), ("imb_points", "#71717a", ".", 30, "IMB")):
        records = data.get(key) or []
        xs = [item["x"] for item in records if "x" in item and "y" in item]
        ys = [item["y"] for item in records if "x" in item and "y" in item]
        if xs:
            axis.scatter(xs, ys, marker=marker, s=size, color=color, zorder=6, label=label)

    ptech = data.get("ptech_plan") or []
    for subtype, color, marker, label in (
        ("CHAMBRE", "#b91c1c", "s", "PTECH CHAMBRE"),
        ("AUTRE", "#f59e0b", "x", "PTECH AUTRE"),
    ):
        records = [item for item in ptech if str(item.get("subtype") or item.get("point_type") or "").upper() == subtype]
        xs = [item["x"] for item in records if "x" in item and "y" in item]
        ys = [item["y"] for item in records if "x" in item and "y" in item]
        if xs:
            axis.scatter(xs, ys, marker=marker, s=30 if subtype == "CHAMBRE" else 36, color=color, edgecolors="white" if subtype == "CHAMBRE" else None, linewidths=0.45, zorder=8, label=label)

    route_legend = [Line2D([0], [0], color=ROUTE_STYLES[item][0], linewidth=ROUTE_STYLES[item][1], label=ROUTE_STYLES[item][2]) for item in sorted(seen_types)]
    route_legend.extend(
        Line2D(
            [0], [0], color=ACCESS_STYLES[item][0], linewidth=ACCESS_STYLES[item][1],
            linestyle="--", label=ACCESS_STYLES[item][2]
        )
        for item in sorted(seen_access)
    )
    handles, labels = axis.get_legend_handles_labels()
    axis.legend([*route_legend, *handles], [item.get_label() for item in route_legend] + labels, loc="best", fontsize=8)
    status = data.get("validation", {}).get("status", "UNKNOWN")
    counts = data.get("meta", {}).get("counts", {})
    axis.set_title(f"Task 6 layered fixed-assignment plan ({status})")
    axis.set_aspect("equal", adjustable="box")
    axis.grid(True, alpha=0.2)
    axis.text(0.99, 0.01, f"NRO {counts.get('nro', 0)} | PM {counts.get('pm', 0)} | BPE {counts.get('bpe', 0)} | PBO {counts.get('pbo', 0)}", transform=axis.transAxes, ha="right", va="bottom", fontsize=8)
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination, dpi=180)
    plt.close(figure)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Visualize a Task-6 layered plan")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    try:
        destination = render(arguments.input, arguments.output)
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        return 1
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
