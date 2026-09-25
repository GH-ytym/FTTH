"""Review visualization for road-buffer-constrained PM optimization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .geometry import extract_zpm_polygons, point
from .optimizer import OptimizationResult, PMOptimizer


def _road_points(road: dict[str, Any]) -> list[tuple[float, float]]:
    coordinates = road.get("coordinates")
    if coordinates is None and isinstance(road.get("geometry"), dict):
        coordinates = road["geometry"].get("coordinates")
    return [point(item) for item in (coordinates or [])]


def render_optimization(
    optimizer: PMOptimizer,
    result: OptimizationResult,
    output_path: str | Path,
) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch, Polygon as PlotPolygon

    destination = Path(output_path)
    figure = plt.figure(figsize=(15, 9), constrained_layout=True)
    grid = figure.add_gridspec(2, 3, width_ratios=(1.15, 1.15, 0.8), height_ratios=(1, 1))
    axis = figure.add_subplot(grid[:, :2])
    convergence = figure.add_subplot(grid[0, 2])
    summary_axis = figure.add_subplot(grid[1, 2])
    palette = ["#2563eb", "#16a34a", "#ca8a04", "#dc2626", "#7c3aed", "#0891b2"]

    for road in optimizer.roads:
        coordinates = _road_points(road)
        if len(coordinates) >= 2:
            axis.plot(
                [item[0] for item in coordinates],
                [item[1] for item in coordinates],
                color="#a8a29e",
                linewidth=1.2,
                alpha=0.6,
                zorder=1,
            )

    for index, zpm_id in enumerate(result.zpm_ids):
        color = palette[index % len(palette)]
        for ring in extract_zpm_polygons(optimizer.regions[zpm_id]):
            axis.add_patch(
                PlotPolygon(ring, closed=True, facecolor=color, edgecolor=color, linewidth=0.35, alpha=0.08, zorder=0)
            )
        candidates = result.candidates[zpm_id]
        axis.scatter(
            [item.x for item in candidates],
            [item.y for item in candidates],
            s=13,
            marker="o",
            facecolors="none",
            edgecolors=color,
            linewidths=0.55,
            alpha=0.75,
            zorder=3,
        )

    route_styles = {
        "NRO_TO_PM_MAIN": ("#1d4ed8", 2.6, 0.82),
        "PM_TO_GROUP_ENTRY": ("#15803d", 2.1, 0.88),
        "GROUP_ENTRY_TO_BPE": ("#0f766e", 1.1, 0.62),
        "BPE_TO_BPE": ("#0891b2", 1.0, 0.62),
        "BPE_TO_PBO": ("#f97316", 0.45, 0.28),
    }
    for route in result.task6_plan.get("duct_routes") or []:
        style = route_styles.get(str(route.get("route_type") or ""))
        coordinates = [point(item) for item in route.get("coordinates") or []]
        if style and len(coordinates) >= 2:
            axis.plot(
                [item[0] for item in coordinates],
                [item[1] for item in coordinates],
                color=style[0],
                linewidth=style[1],
                alpha=style[2],
                zorder=4,
            )

    axis.scatter(
        [float(item["x"]) for item in optimizer.bpes],
        [float(item["y"]) for item in optimizer.bpes],
        marker="D",
        s=46,
        color="#b91c1c",
        edgecolors="white",
        linewidths=0.6,
        zorder=7,
    )
    for bpe in optimizer.bpes:
        axis.annotate(_short_id(str(bpe.get("id") or bpe.get("code") or "")), (bpe["x"], bpe["y"]), xytext=(4, 4), textcoords="offset points", fontsize=6.5, color="#7f1d1d")

    optimized_by_zpm = {
        zpm_id: result.candidates[zpm_id][gene]
        for zpm_id, gene in zip(result.zpm_ids, result.optimized.chromosome)
    }
    original_by_zpm = {
        str(pm.get("zpm_id") or pm.get("partition_id")): pm for pm in optimizer.pms
    }
    for zpm_id in result.zpm_ids:
        original = original_by_zpm[zpm_id]
        initial = result.candidates[zpm_id][0]
        optimized = optimized_by_zpm[zpm_id]
        axis.plot([original["x"], optimized.x], [original["y"], optimized.y], color="#111827", linewidth=0.8, linestyle="--", alpha=0.75, zorder=6)
        axis.scatter([original["x"]], [original["y"]], marker="s", s=76, facecolors="white", edgecolors="#111827", linewidths=1.3, zorder=8)
        axis.scatter([initial.x], [initial.y], marker="*", s=145, color="#f59e0b", edgecolors="#713f12", linewidths=0.55, zorder=9)
        axis.scatter([optimized.x], [optimized.y], marker="^", s=105, color="#111827", edgecolors="white", linewidths=0.7, zorder=10)
        axis.annotate(zpm_id, optimized.coordinate, xytext=(6, -11), textcoords="offset points", fontsize=8, fontweight="bold", color="#111827")

    nro_x = [float(item["x"]) for item in optimizer.nros]
    nro_y = [float(item["y"]) for item in optimizer.nros]
    axis.scatter(nro_x, nro_y, marker="s", s=105, color="#111827", edgecolors="white", linewidths=0.8, zorder=10)
    axis.set_title("Task 3 PM placement within 10 m of roads with Task-6 feedback", fontsize=13)
    axis.set_xlabel("Longitude (EPSG:4326)")
    axis.set_ylabel("Latitude (EPSG:4326)")
    axis.set_aspect("equal", adjustable="box")
    axis.grid(True, alpha=0.16)
    axis.legend(
        handles=[
            Patch(facecolor="#2563eb", alpha=0.1, edgecolor="#2563eb", label="Task-2 ZPM regions"),
            Line2D([0], [0], color="#a8a29e", linewidth=1.4, label="Road network"),
            Line2D([0], [0], color="#1d4ed8", linewidth=2.6, label="NRO-PM shared main"),
            Line2D([0], [0], color="#15803d", linewidth=2.1, label="PM distribution route"),
            Line2D([0], [0], marker="D", color="w", markerfacecolor="#b91c1c", markersize=7, label="BPE"),
            Line2D([0], [0], marker="s", color="#111827", markerfacecolor="white", markersize=7, linestyle="None", label="Original PM"),
            Line2D([0], [0], marker="*", color="#713f12", markerfacecolor="#f59e0b", markersize=10, linestyle="None", label="Initial road-buffer median"),
            Line2D([0], [0], marker="^", color="w", markerfacecolor="#111827", markersize=8, linestyle="None", label="Optimized PM"),
        ],
        loc="upper left",
        fontsize=7.5,
        framealpha=0.94,
    )

    generations = [int(item["generation"]) for item in result.history]
    lengths = [float(item["installed_duct_length_m"]) for item in result.history]
    outside = [float(item["outside_zpm_length_m"]) for item in result.history]
    convergence.plot(generations, lengths, marker="o", markersize=3.5, color="#1d4ed8", linewidth=1.5, label="Installed duct")
    convergence.set_xlabel("Generation")
    convergence.set_ylabel("Installed duct (m)", color="#1d4ed8")
    convergence.tick_params(axis="y", labelcolor="#1d4ed8")
    convergence.grid(True, alpha=0.2)
    outside_axis = convergence.twinx()
    outside_axis.plot(generations, outside, marker="s", markersize=3, color="#dc2626", linewidth=1.2, label="Outside ZPM")
    outside_axis.set_ylabel("Outside ZPM (m)", color="#dc2626")
    outside_axis.tick_params(axis="y", labelcolor="#dc2626")
    convergence.set_title("GA convergence", fontsize=10)

    original = result.original.metrics
    initial = result.initial.metrics
    optimized = result.optimized.metrics
    rows = [
        ("Task-6 status", str(optimized.get("task6_status"))),
        ("Task-6 evaluations", str(len(result.evaluations) + 2)),
        ("Original installed duct", f"{original.get('installed_duct_length_m', 0):.2f} m"),
        ("Initial road-buffer median", f"{initial.get('installed_duct_length_m', 0):.2f} m"),
        ("Optimized installed duct", f"{optimized.get('installed_duct_length_m', 0):.2f} m"),
        ("Optimized road union", f"{optimized.get('road_unique_length_m', 0):.2f} m"),
        ("Outside ZPM", f"{optimized.get('outside_zpm_length_m', 0):.2f} m"),
        ("Max outside ratio", f"{100 * optimized.get('max_outside_zpm_ratio', 0):.2f}%"),
        ("Construction cost", f"{optimized.get('construction_cost', 0):.2f}"),
    ]
    summary_axis.axis("off")
    table = summary_axis.table(cellText=rows, colLabels=["Metric", "Value"], loc="center", cellLoc="left", colLoc="left")
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1.0, 1.55)
    for (row, _), cell in table.get_celld().items():
        cell.set_edgecolor("#d4d4d8")
        if row == 0:
            cell.set_facecolor("#e5e7eb")
            cell.set_text_props(weight="bold")
    summary_axis.set_title("Final verification", fontsize=10, pad=4)

    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination, dpi=200, bbox_inches="tight")
    plt.close(figure)
    return destination


def _short_id(value: str) -> str:
    return value.rsplit("-", 1)[-1] if value else "BPE"


def render_from_files(report_path: str | Path, output_path: str | Path) -> Path:
    """Render a compact convergence-only fallback from a serialized report."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    report = json.loads(Path(report_path).read_text(encoding="utf-8-sig"))
    history = report.get("history") or []
    destination = Path(output_path)
    figure, axis = plt.subplots(figsize=(8, 5), constrained_layout=True)
    axis.plot([item["generation"] for item in history], [item["installed_duct_length_m"] for item in history], marker="o")
    axis.set_title("Task 3 PM optimization convergence")
    axis.set_xlabel("Generation")
    axis.set_ylabel("Installed duct length (m)")
    axis.grid(True, alpha=0.25)
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination, dpi=180)
    plt.close(figure)
    return destination
