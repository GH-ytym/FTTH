"""无第三方框架的 masked policy gradient 实现。

该实现采用候选特征线性评分和 PPO 风格的裁剪更新，目标是让训练、推理和
审计在标准库环境中都可运行。策略不是坐标生成器，所有非法动作仍由环境掩码拦截。
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .environment import PMSelectionEnv


FEATURE_NAMES = (
    "route_cost", "uplink_cost", "construction_cost", "reuse_bonus",
    "demand_ports", "capacity", "node_degree", "reused",
)


def _softmax(values: list[float]) -> list[float]:
    if not values:
        return []
    maximum = max(values)
    exps = [math.exp(max(-60.0, value - maximum)) for value in values]
    total = sum(exps)
    return [value / total for value in exps]


class MaskedPolicy:
    """候选级别策略；以 ZPM 和候选编码保存独立偏置。"""

    def __init__(self, learning_rate: float = 0.05, temperature: float = 1.0, seed: int = 0) -> None:
        self.learning_rate = learning_rate
        self.temperature = max(temperature, 1e-6)
        self.rng = random.Random(seed)
        self.bias: dict[str, float] = {}
        self.weights = {name: 0.0 for name in FEATURE_NAMES}
        self.updates = 0

    @staticmethod
    def _features(item: dict[str, Any]) -> list[float]:
        return [
            float(item.get("route_cost") or 0.0), float(item.get("uplink_cost") or 0.0),
            float(item.get("construction_cost") or 0.0), float(item.get("reuse_bonus") or 0.0),
            float(item.get("demand_ports") or 0.0), float(item.get("capacity") or 0.0),
            float(item.get("node_degree") or 0.0), 1.0 if item.get("reused") else 0.0,
        ]

    def score(self, item: dict[str, Any]) -> float:
        code = str(item.get("candidate_code") or "")
        values = self._features(item)
        # proxy_cost 提供稳定的未训练先验，学习到的 bias/weights 只微调它。
        prior = -float(item.get("proxy_cost") or 0.0) / 100.0
        return prior + self.bias.get(code, 0.0) + sum(
            self.weights[name] * value for name, value in zip(FEATURE_NAMES, values)
        )

    def action_probabilities(self, observation: dict[str, Any]) -> list[float]:
        candidates = observation.get("candidate_features") or []
        mask = observation.get("action_mask") or [True] * len(candidates)
        logits = [self.score(item) / self.temperature if allowed else -float("inf")
                  for item, allowed in zip(candidates, mask)]
        finite = [value for value in logits if math.isfinite(value)]
        if not finite:
            return [0.0] * len(logits)
        return _softmax(logits)

    def select(self, observation: dict[str, Any], greedy: bool = False) -> tuple[int, float]:
        probabilities = self.action_probabilities(observation)
        legal = [index for index, value in enumerate(probabilities) if value > 0.0]
        if not legal:
            raise ValueError("当前状态没有合法 PM 动作")
        if greedy:
            action = max(legal, key=lambda index: (probabilities[index], -index))
        else:
            draw = self.rng.random()
            cumulative = 0.0
            action = legal[-1]
            for index in legal:
                cumulative += probabilities[index]
                if draw <= cumulative:
                    action = index
                    break
        return action, probabilities[action]

    def behavior_clone(self, env: PMSelectionEnv) -> None:
        """将确定性枚举选择作为专家轨迹，提高首轮推理稳定性。"""
        observation = env.reset()
        for expected in env.deterministic_actions():
            candidates = observation["candidate_features"]
            for index, item in enumerate(candidates):
                if index == expected:
                    code = str(item.get("candidate_code"))
                    self.bias[code] = self.bias.get(code, 0.0) + 1.0
                    break
            observation, _, done, _ = env.step(expected)
            if done:
                break

    def update(self, transitions: list[dict[str, Any]], clip_epsilon: float = 0.2) -> float:
        """PPO 风格的单轮裁剪策略梯度更新，返回平均损失。"""
        if not transitions:
            return 0.0
        total_loss = 0.0
        for transition in transitions:
            observation = transition["observation"]
            action = int(transition["action"])
            advantage = float(transition.get("advantage", transition.get("reward", 0.0)))
            old_probability = max(float(transition.get("old_probability", 1e-8)), 1e-8)
            probabilities = self.action_probabilities(observation)
            if action >= len(probabilities) or probabilities[action] <= 0.0:
                continue
            ratio = probabilities[action] / old_probability
            clipped_ratio = max(1.0 - clip_epsilon, min(1.0 + clip_epsilon, ratio))
            objective = min(ratio * advantage, clipped_ratio * advantage)
            # 对 logits 做一个稳定的 softmax 梯度更新。
            items = observation.get("candidate_features") or []
            for index, item in enumerate(items):
                if index >= len(probabilities) or probabilities[index] <= 0.0:
                    continue
                direction = (1.0 if index == action else 0.0) - probabilities[index]
                scale = self.learning_rate * advantage * direction
                code = str(item.get("candidate_code") or "")
                self.bias[code] = self.bias.get(code, 0.0) + scale
            total_loss -= objective
            self.updates += 1
        return total_loss / max(len(transitions), 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "task3.masked_policy.v1",
            "learning_rate": self.learning_rate, "temperature": self.temperature,
            "bias": self.bias, "weights": self.weights, "updates": self.updates,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any], seed: int = 0) -> "MaskedPolicy":
        policy = cls(float(payload.get("learning_rate", 0.05)), float(payload.get("temperature", 1.0)), seed)
        policy.bias = {str(key): float(value) for key, value in (payload.get("bias") or {}).items()}
        policy.weights.update({str(key): float(value) for key, value in (payload.get("weights") or {}).items()})
        policy.updates = int(payload.get("updates", 0))
        return policy

    def save(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path, seed: int = 0) -> "MaskedPolicy":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8-sig")), seed)


@dataclass
class TrainingResult:
    policy: MaskedPolicy
    history: list[dict[str, Any]]


class MaskedPPOTrainer:
    def __init__(self, env: PMSelectionEnv, policy: MaskedPolicy | None = None, seed: int = 0) -> None:
        self.env = env
        self.policy = policy or MaskedPolicy(seed=seed)
        self.rng = random.Random(seed)

    def train(self, episodes: int = 50, behavior_clone: bool = True) -> TrainingResult:
        if behavior_clone:
            self.policy.behavior_clone(self.env)
        history: list[dict[str, Any]] = []
        for episode in range(max(0, episodes)):
            observation = self.env.reset(self.rng.randrange(1_000_000_000))
            transitions: list[dict[str, Any]] = []
            rewards: list[float] = []
            done = False
            while not done:
                action, probability = self.policy.select(observation)
                next_observation, reward, done, info = self.env.step(action)
                transitions.append({"observation": observation, "action": action, "old_probability": probability, "reward": reward})
                rewards.append(reward)
                observation = next_observation
            # episode 内折扣回报，最后一步权重最高。
            running = 0.0
            for transition, reward in reversed(list(zip(transitions, rewards))):
                running = reward + 0.98 * running
                transition["advantage"] = running
            loss = self.policy.update(transitions)
            history.append({"episode": episode + 1, "reward": sum(rewards), "loss": loss, "actions": [item["action"] for item in transitions]})
        return TrainingResult(self.policy, history)
