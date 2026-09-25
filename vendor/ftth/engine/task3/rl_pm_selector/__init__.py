"""可复现的 PM 选址强化学习工具。

该包把任务三的确定性选址器作为硬约束和基线，策略只负责在每个 ZPM
的候选动作中选择一个 PM。没有第三方 RL 框架也可以运行，便于在 QGIS
或纯 Python 环境中复核结果。
"""

from .environment import PMSelectionEnv, RLSelectionResult
from .features import PMCandidateFeature, PMFeatureTable, build_feature_table
from .policy import MaskedPPOTrainer, MaskedPolicy

__all__ = [
    "PMSelectionEnv", "RLSelectionResult", "PMCandidateFeature", "PMFeatureTable",
    "build_feature_table", "MaskedPolicy", "MaskedPPOTrainer",
]
