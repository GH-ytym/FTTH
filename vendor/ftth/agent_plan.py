"""Parse and strictly validate model-proposed FTTH parameters."""

from __future__ import annotations

import json
from typing import Any


SPECS = {
    "nro_max_cap": (int, 1, 1000000),
    "zpm_max_cap": (int, 1, 1000000),
    "spatial_weight": (float, 0.0, 10000.0),
    "pipe_weight": (float, 0.0, 10000.0),
    "formal_duct_capacity": (int, 1, 1000000),
    "pbo_offset_m": (float, 0.1, 1000.0),
    "candidate_paths": (int, 1, 20),
    "water_cost_factor": (float, 0.0, 1000.0),
    "ptech_max_spacing_m": (float, 1.0, 100000.0),
    "terminal_snap_distance_m": (float, 1.0, 100000.0),
    "ga_population": (int, 4, 100),
    "ga_generations": (int, 1, 100),
}


class PlanError(ValueError):
    pass


def extract_json_object(text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    if value.startswith("```"):
        lines = value.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        value = "\n".join(lines)
    decoder = json.JSONDecoder()
    for index, char in enumerate(value):
        if char != "{":
            continue
        try:
            result, _ = decoder.raw_decode(value[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(result, dict):
            return result
    raise PlanError("模型回复中没有完整 JSON 对象")


def normalize_parameters(raw: dict[str, Any], *, include_defaults: bool = False) -> dict[str, int | float | None]:
    if not isinstance(raw, dict):
        raise PlanError("parameters 必须是 JSON 对象")
    unknown = sorted(set(raw) - set(SPECS))
    if unknown:
        raise PlanError("模型返回了非白名单参数：" + "、".join(unknown))
    result: dict[str, int | float | None] = {}
    for name, (kind, minimum, maximum) in SPECS.items():
        if name not in raw:
            if include_defaults:
                result[name] = None
            continue
        value = raw[name]
        if value is None or value == "":
            result[name] = None
            continue
        if isinstance(value, bool):
            raise PlanError(f"{name} 不能是布尔值")
        try:
            number = kind(value)
        except (TypeError, ValueError) as exc:
            raise PlanError(f"{name} 必须是{'整数' if kind is int else '数值'}") from exc
        if not minimum <= number <= maximum:
            raise PlanError(f"{name} 必须在 {minimum}～{maximum} 之间")
        result[name] = number
    spatial = result.get("spatial_weight")
    pipe = result.get("pipe_weight")
    if spatial == 0 and pipe == 0:
        raise PlanError("spatial_weight 和 pipe_weight 不能同时为 0")
    return result


def parse_plan_response(text: str) -> dict[str, Any]:
    payload = extract_json_object(text)
    parameters = normalize_parameters(payload.get("parameters", {}), include_defaults=True)

    def strings(name: str) -> list[str]:
        value = payload.get(name, [])
        if value is None:
            return []
        if not isinstance(value, list):
            raise PlanError(f"{name} 必须是数组")
        return [str(item).strip() for item in value if str(item).strip()]

    run_scope = str(payload.get("run_scope", "full")).strip().lower()
    if run_scope not in {"full", "through_task3", "through_task2"}:
        raise PlanError("run_scope 只能是 full、through_task3 或 through_task2")
    return {
        "summary": str(payload.get("summary", "")).strip(),
        "run_scope": run_scope,
        "parameters": parameters,
        "missing_information": strings("missing_information"),
        "required_confirmations": strings("required_confirmations"),
        "warnings": strings("warnings"),
    }
