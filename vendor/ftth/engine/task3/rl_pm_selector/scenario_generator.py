"""可复现的小规模场景扰动与 train/validation/test 划分。"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any


def split_scenarios(items: list[Any], seed: int = 0, train_ratio: float = 0.6, validation_ratio: float = 0.2) -> dict[str, list[Any]]:
    if train_ratio < 0 or validation_ratio < 0 or train_ratio + validation_ratio > 1:
        raise ValueError("train_ratio 和 validation_ratio 必须在 [0,1] 且和不超过 1")
    shuffled = list(items)
    random.Random(seed).shuffle(shuffled)
    train_end = int(len(shuffled) * train_ratio)
    validation_end = train_end + int(len(shuffled) * validation_ratio)
    return {"train": shuffled[:train_end], "validation": shuffled[train_end:validation_end], "test": shuffled[validation_end:]}


def generate_scenario_manifest(input_dir: str | Path, output_path: str | Path, count: int = 10, seed: int = 0, demand_jitter: float = 0.1) -> list[dict[str, Any]]:
    """只生成扰动参数清单，不修改原始 GIS 数据，便于审计和重放。"""
    source = str(Path(input_dir).resolve())
    rng = random.Random(seed)
    scenarios = []
    for index in range(max(0, count)):
        scenarios.append({
            "id": f"scenario_{index + 1:04d}", "source_input": source,
            "seed": rng.randrange(1_000_000_000), "demand_scale": 1.0 + rng.uniform(-demand_jitter, demand_jitter),
            "candidate_jitter": rng.uniform(0.0, 1.0),
        })
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps({"schema_version": "task3.scenarios.v1", "scenarios": scenarios}, ensure_ascii=False, indent=2), encoding="utf-8")
    return scenarios
