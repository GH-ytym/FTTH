"""把 RL episode 结果导出为任务三和任务六可用契约。"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from nro_pm_selector.io import InputBundle, write_outputs

from .environment import PMSelectionEnv, RLSelectionResult
from .features import write_feature_table
from .policy import MaskedPolicy


def run_policy(
    bundle: InputBundle,
    policy: MaskedPolicy,
    greedy: bool = True,
    fallback: bool = True,
) -> RLSelectionResult:
    env = PMSelectionEnv(bundle, seed=0)
    observation = env.reset()
    while env.position < len(env.zpm_ids):
        try:
            action, _ = policy.select(observation, greedy=greedy)
            observation, _, done, _ = env.step(action)
        except (ValueError, IndexError):
            if not fallback:
                raise
            # 环境掩码失效或模型损坏时，整个场景回退到确定性专家轨迹。
            env.reset()
            for action in env.deterministic_actions():
                observation, _, done, _ = env.step(action)
            return env.result(mode="deterministic_fallback")
    result = env.result(mode="rl")
    if not result.validation.get("passed") and fallback:
        env.reset()
        for action in env.deterministic_actions():
            env.step(action)
        return env.result(mode="deterministic_fallback")
    return result


def export_result(
    bundle: InputBundle,
    result: RLSelectionResult,
    output_dir: str | Path,
    feature_table: Any | None = None,
) -> None:
    destination = Path(output_dir)
    write_outputs(destination, bundle, result.selections, result.rejections, result.validation)
    metadata = {
        "schema_version": "task3.rl_selection.v1",
        "mode": result.mode,
        "trajectory": result.trajectory,
        "validation": result.validation,
    }
    (destination / "rl_selection_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if feature_table is not None:
        write_feature_table(feature_table, str(destination / "pm_candidate_features.json"))


def export_fixed_plan(
    task3_input: str | Path,
    task3_output: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    """复用现有适配器，将 RL 输出转换为 task3.fixed_pm.v1。"""
    from task3_to_task6_adapter import build_fixed_pm_plan
    return build_fixed_pm_plan(task3_input, task3_output, output_path)
