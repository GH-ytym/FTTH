"""任务三 PM 选址环境：固定 NRO，按 ZPM 逐步选择 PM。"""

from __future__ import annotations

import random
from dataclasses import dataclass
from math import inf
from typing import Any

from nro_pm_selector.io import InputBundle
from nro_pm_selector.models import Candidate, Point, Rejection, SiteSelection
from nro_pm_selector.selector import SelectionResult

from .features import PMCandidateFeature, PMFeatureTable, build_feature_table
from .reward import proxy_reward, robust_scale


@dataclass
class RLSelectionResult:
    selections: list[SiteSelection]
    rejections: list[Rejection]
    validation: dict[str, Any]
    trajectory: list[dict[str, Any]]
    mode: str = "rl"


class PMSelectionEnv:
    """无 gym 依赖的轻量环境，接口与 gym 的 reset/step 约定兼容。"""

    def __init__(self, bundle: InputBundle, feature_table: PMFeatureTable | None = None, seed: int = 0) -> None:
        self.bundle = bundle
        self.table = feature_table or build_feature_table(bundle)
        self.baseline = self.table.baseline
        self.rng = random.Random(seed)
        self._baseline_by_zpm = {
            item.zone_code: item for item in self.baseline.selections if item.facility_type == "PM"
        }
        self._features_by_code = {
            item.candidate_code: item for items in self.table.by_zpm.values() for item in items
        }
        self.reset(seed)

    def reset(self, seed: int | None = None) -> dict[str, Any]:
        if seed is not None:
            self.rng.seed(seed)
        self.position = 0
        self.selected: list[PMCandidateFeature] = []
        self.trajectory: list[dict[str, Any]] = []
        return self.observation()

    @property
    def zpm_ids(self) -> list[str]:
        return self.table.zpm_ids

    def current_features(self) -> list[PMCandidateFeature]:
        if self.position >= len(self.zpm_ids):
            return []
        return self.table.by_zpm[self.zpm_ids[self.position]]

    def action_mask(self) -> list[bool]:
        features = self.current_features()
        used = {item.candidate_code for item in self.selected if not item.synthetic}
        legal = [item.feasible and item.candidate_code not in used for item in features]
        if any(legal):
            return legal
        # synthetic action is only legal as a fallback and is never shared.
        return [item.synthetic for item in features]

    def observation(self) -> dict[str, Any]:
        features = self.current_features()
        return {
            "zpm_id": self.zpm_ids[self.position] if self.position < len(self.zpm_ids) else None,
            "step": self.position,
            "total_steps": len(self.zpm_ids),
            "progress": self.position / max(len(self.zpm_ids), 1),
            "candidate_features": [item.to_dict() for item in features],
            "action_mask": self.action_mask(),
            "selected_candidate_codes": [item.candidate_code for item in self.selected],
            "baseline_cost": sum(item.total_score for item in self.baseline.selections),
        }

    def step(self, action: int) -> tuple[dict[str, Any], float, bool, dict[str, Any]]:
        features = self.current_features()
        mask = self.action_mask()
        if not features:
            return self.observation(), 0.0, True, {"error": "environment already terminated"}
        if action < 0 or action >= len(features) or not mask[action]:
            raise ValueError(f"非法或被屏蔽的 PM 动作: {action}; mask={mask}")
        chosen = features[action]
        baseline = self._baseline_by_zpm.get(chosen.zpm_id)
        costs = [item.proxy_cost for item in features]
        scale = robust_scale(costs)
        reward = proxy_reward(chosen, baseline, scale)
        record = {
            "step": self.position, "zpm_id": chosen.zpm_id, "action": action,
            "candidate_code": chosen.candidate_code, "proxy_cost": chosen.proxy_cost,
            "baseline_proxy_cost": getattr(baseline, "proxy_cost", getattr(baseline, "total_score", None)) if baseline else None, "reward": reward,
        }
        self.selected.append(chosen)
        self.trajectory.append(record)
        self.position += 1
        done = self.position >= len(self.zpm_ids)
        info = {"chosen": chosen.to_dict(), "trajectory_record": record}
        return self.observation(), reward, done, info

    def deterministic_actions(self) -> list[int]:
        actions = []
        used: set[str] = set()
        for zpm_id in self.zpm_ids:
            features = self.table.by_zpm[zpm_id]
            legal = [item for item in features if item.feasible and item.candidate_code not in used]
            baseline = self._baseline_by_zpm.get(zpm_id)
            choice = next((item for item in legal if item.site_code == (baseline.site_code if baseline else "")), None)
            if choice is None and baseline:
                choice = next((item for item in legal if item.candidate_code == baseline.candidate_code), None)
            if choice is None and legal:
                choice = min(legal, key=lambda item: (item.proxy_cost, item.candidate_code))
            if choice is None:
                choice = next((item for item in features if item.synthetic), None)
            if choice is None:
                raise ValueError(f"ZPM {zpm_id} 没有可用 PM 动作")
            actions.append(choice.index)
            used.add(choice.candidate_code)
        return actions

    def result(self, mode: str = "rl") -> RLSelectionResult:
        if len(self.selected) != len(self.zpm_ids):
            raise ValueError("episode 尚未完成，不能导出 PM 选择")
        engine = self._engine_for_result()
        nro = [item for item in self.baseline.selections if item.facility_type == "NRO"]
        pm_by_zone = {item.zpm_id: item for item in self.selected}
        selections = list(nro)
        for zpm in sorted(self.bundle.zpm, key=lambda item: item.code):
            feature = pm_by_zone[zpm.code]
            baseline = self._baseline_by_zpm[zpm.code]
            candidate = self._candidate_for_feature(feature)
            site_code = candidate.existing_site_code or baseline.site_code
            ref_nro = baseline.ref_nro
            selections.append(SiteSelection(
                facility_type="PM", zone_code=zpm.code, site_code=site_code,
                candidate_code=feature.candidate_code, ptech_code=candidate.ptech_code,
                point=candidate.point, ref_nro=ref_nro, capacity=feature.capacity,
                demand_ports=feature.demand_ports, route_cost=feature.route_cost,
                construction_cost=feature.construction_cost, total_score=feature.proxy_cost,
                reused=candidate.is_existing, reason=f"RL 选择：{feature.reason}",
                candidate_source=candidate.source,
            ))
        validation = engine._validate_result(selections)
        rejections = [
            Rejection("PM", feature.zpm_id, feature.candidate_code, feature.reason)
            for items in self.table.by_zpm.values() for feature in items if not feature.feasible
        ]
        return RLSelectionResult(selections, rejections, validation, list(self.trajectory), mode)

    def _engine_for_result(self):
        from nro_pm_selector.selector import SelectionEngine
        return SelectionEngine(self.bundle)

    def _candidate_for_feature(self, feature: PMCandidateFeature) -> Candidate:
        if feature.synthetic:
            # 特征抽取时已经为每个 ZPM 分配了稳定的 PTECH 编码；导出时不能
            # 重新起号，否则多个新建动作会得到相同编码。
            return Candidate(
                code=feature.candidate_code, point=Point(feature.x, feature.y),
                node_code=feature.node_id, source="NEW_PTECH", allowed_types={"PM"},
                ptech_code=feature.ptech_code, construction_cost=feature.construction_cost,
            )
        return next(
            candidate for candidate in self.bundle.candidates if candidate.code == feature.candidate_code
        )
