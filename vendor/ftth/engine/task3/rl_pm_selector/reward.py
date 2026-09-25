"""代理奖励和终局奖励的可解释分解。"""

from __future__ import annotations

from statistics import median
from typing import Any

from .features import PMCandidateFeature


def robust_scale(costs: list[float], minimum: float = 1.0) -> float:
    finite = sorted(value for value in costs if value == value and value < float("inf"))
    if not finite:
        return minimum
    return max(minimum, median(finite), (finite[-1] - finite[0]) / 2.0)


def proxy_reward(chosen: PMCandidateFeature, baseline: PMCandidateFeature | None, scale: float) -> float:
    """相对确定性基线的每步奖励，成本越低越好。"""
    baseline_cost = getattr(baseline, "proxy_cost", None)
    if baseline_cost is None:
        baseline_cost = getattr(baseline, "total_score", None)
    reference = float(baseline_cost) if baseline_cost is not None and baseline_cost < float("inf") else 0.0
    return -(chosen.proxy_cost - reference) / max(scale, 1.0)


def terminal_reward(
    exact_cost: float,
    baseline_exact_cost: float | None = None,
    scale: float = 1.0,
    unresolved: int = 0,
    review_required: bool = False,
) -> float:
    baseline = exact_cost if baseline_exact_cost is None else baseline_exact_cost
    penalty = float(unresolved) * 10.0 + (100.0 if review_required else 0.0)
    return -((exact_cost - baseline) / max(scale, 1.0)) - penalty


def task6_cost_summary(result: dict[str, Any]) -> dict[str, Any]:
    validation = result.get("validation") or {}
    meta = result.get("meta") or {}
    cost = meta.get("cost", meta.get("estimated_construction_cost", 0.0))
    unresolved = validation.get("unresolved", validation.get("unresolved_access_routes", []))
    if isinstance(unresolved, int):
        unresolved_count = unresolved
    else:
        unresolved_count = len(unresolved or [])
    return {
        "cost": float(cost or 0.0),
        "unresolved": unresolved_count,
        "status": validation.get("status", "UNKNOWN"),
        "review_required": validation.get("status") == "REVIEW_REQUIRED",
    }
