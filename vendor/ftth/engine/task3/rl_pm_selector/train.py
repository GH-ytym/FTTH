"""训练任务三轻量 Masked PPO 策略。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from nro_pm_selector.io import InputBundle

from .environment import PMSelectionEnv
from .features import build_feature_table, write_feature_table
from .policy import MaskedPPOTrainer, MaskedPolicy


def train(input_dir: str | Path, model_path: str | Path, episodes: int = 50, seed: int = 0, feature_path: str | Path | None = None) -> dict:
    bundle = InputBundle.load(input_dir)
    table = build_feature_table(bundle)
    env = PMSelectionEnv(bundle, table, seed=seed)
    trainer = MaskedPPOTrainer(env, MaskedPolicy(seed=seed), seed=seed)
    result = trainer.train(episodes=episodes, behavior_clone=True)
    result.policy.save(model_path)
    if feature_path:
        write_feature_table(table, str(feature_path))
    history_path = Path(model_path).with_suffix(".history.json")
    history_path.write_text(json.dumps({"schema_version": "task3.training.v1", "history": result.history}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"model": str(model_path), "episodes": episodes, "updates": result.policy.updates, "history": str(history_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train task-3 masked PM policy")
    parser.add_argument("--input", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--episodes", type=int, default=50)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--features")
    args = parser.parse_args(argv)
    print(json.dumps(train(args.input, args.model, args.episodes, args.seed, args.features), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
