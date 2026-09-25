"""PM 候选点的可审计特征和动作掩码输入。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from copy import deepcopy
from math import ceil, inf, isfinite
from typing import Any

from nro_pm_selector.io import InputBundle
from nro_pm_selector.models import Candidate, Demand, SiteSelection, Zone, text
from nro_pm_selector.selector import SelectionEngine, SelectionResult


@dataclass
class PMCandidateFeature:
    zpm_id: str
    index: int
    candidate_code: str
    site_code: str
    ptech_code: str
    node_id: str
    x: float
    y: float
    source: str
    feasible: bool
    reason: str
    route_cost: float
    uplink_cost: float
    construction_cost: float
    reuse_bonus: float
    proxy_cost: float
    demand_ports: int
    capacity: int
    reused: bool
    node_degree: int
    synthetic: bool = False

    def numeric(self) -> list[float]:
        """固定顺序的归一化前特征，供轻量策略使用。"""
        return [
            self.route_cost, self.uplink_cost, self.construction_cost,
            self.reuse_bonus, float(self.demand_ports), float(self.capacity),
            float(self.node_degree), 1.0 if self.reused else 0.0,
        ]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PMFeatureTable:
    """所有 ZPM 的固定候选数组。"""

    by_zpm: dict[str, list[PMCandidateFeature]]
    baseline: SelectionResult

    @property
    def zpm_ids(self) -> list[str]:
        return sorted(self.by_zpm)

    @property
    def max_candidates(self) -> int:
        return max((len(items) for items in self.by_zpm.values()), default=0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "task3.pm_candidate_features.v1",
            "zpm": {key: [item.to_dict() for item in value] for key, value in self.by_zpm.items()},
            "max_candidates": self.max_candidates,
        }


def _demands(bundle: InputBundle, zpm: Zone) -> list[Demand]:
    return [item for item in bundle.demands if item.ref_pm_zone == zpm.code]


def _evaluate(
    engine: SelectionEngine,
    candidate: Candidate,
    zpm: Zone,
    demands: list[Demand],
    parent_nro: SiteSelection,
    index: int,
) -> PMCandidateFeature:
    capacity = zpm.capacity
    required = ceil(sum(item.ports for item in demands) * (1 + float(engine.bundle.config["capacity_reserve_ratio"])))
    reason = ""
    feasible = True
    route_cost = uplink_cost = inf
    node = candidate.node_code
    if not candidate.permits("PM"):
        feasible, reason = False, "ALLOWED_TYPES 不含 PM"
    elif candidate.is_existing and not candidate.reusable:
        feasible, reason = False, "已有站点状态不可复用"
    elif not candidate.feasible:
        failed = []
        if not candidate.install_space:
            failed.append("无安装空间")
        if not candidate.maintainable:
            failed.append("不便维护")
        if not candidate.property_clear:
            failed.append("产权不明确")
        feasible, reason = False, "、".join(failed)
    elif required > capacity:
        feasible, reason = False, f"含余量需 {required} 端口，超过容量 {capacity}"
    else:
        node, snap = engine._snap_candidate(candidate)
        access_limit = engine._road_access_limit("PM")
        if not node or snap > access_limit:
            feasible, reason = False, f"距管网 {snap:.2f}，超过 {access_limit:.2f} 米直连光纤接入阈值"
        else:
            candidate.node_code = node
            distances = [engine.graph.distance(node, demand.node_code) for demand in demands]
            parent_node, parent_snap = engine.graph.nearest_node(parent_nro.point)
            uplink = engine.graph.distance(node, parent_node)
            if any(not isfinite(value) for value in distances):
                feasible, reason = False, "无法连通全部需求点"
            elif not isfinite(uplink):
                feasible, reason = False, "无法连通所属 NRO"
            else:
                route_cost = sum(distances) + snap
                uplink_cost = uplink
                route_cost += float(engine.bundle.config["uplink_weight"]) * uplink
                reason = "满足约束"
    reuse_bonus = 0.0
    if candidate.is_existing:
        reuse_bonus = float(engine.bundle.config["existing_site_bonus"])
    elif candidate.source == "PTECH":
        reuse_bonus = float(engine.bundle.config["ptech_bonus"])
    proxy_cost = route_cost + candidate.construction_cost - reuse_bonus if feasible else inf
    return PMCandidateFeature(
        zpm_id=zpm.code, index=index, candidate_code=candidate.code,
        site_code=candidate.existing_site_code, ptech_code=candidate.ptech_code,
        node_id=node, x=candidate.point.x, y=candidate.point.y, source=candidate.source,
        feasible=feasible, reason=reason, route_cost=route_cost,
        uplink_cost=uplink_cost, construction_cost=candidate.construction_cost,
        reuse_bonus=reuse_bonus, proxy_cost=proxy_cost,
        demand_ports=sum(item.ports for item in demands), capacity=capacity,
        reused=candidate.is_existing, node_degree=len(engine.graph.adjacency.get(node, [])),
    )


def build_feature_table(bundle: InputBundle, baseline: SelectionResult | None = None) -> PMFeatureTable:
    """使用与确定性选择器相同的图和约束生成 PM 特征。"""
    # 选择器会在运行时回填 Zone.ref_*；特征抽取必须是只读操作，避免
    # 后续同一进程再次训练/推理时看到被回填的 SITE.CODE。
    working_bundle = deepcopy(bundle)
    engine = SelectionEngine(working_bundle)
    baseline = baseline or engine.run()
    nro_by_zone = {
        item.zone_code: item for item in baseline.selections if item.facility_type == "NRO"
    }
    table: dict[str, list[PMCandidateFeature]] = {}
    for zpm in sorted(working_bundle.zpm, key=lambda item: item.code):
        demands = _demands(working_bundle, zpm)
        # SelectionEngine 回填后会把 zone.ref_nro 改成 NRO SITE.CODE；需求对象
        # 保留原始 ZNRO 归属，优先用它避免混淆两个编码空间。
        parent_zone = (demands[0].ref_nro_zone if demands else "") or zpm.ref_nro
        parent = nro_by_zone.get(parent_zone)
        if parent is None and zpm.ref_nro:
            parent = next((item for item in nro_by_zone.values() if item.site_code == zpm.ref_nro), None)
        if parent is None:
            raise ValueError(f"ZPM {zpm.code} 无法关联确定性 NRO")
        candidates = [
            candidate for candidate in working_bundle.candidates
            if engine._candidate_zone(candidate, zpm)
        ]
        features = [_evaluate(engine, candidate, zpm, demands, parent, index)
                    for index, candidate in enumerate(candidates)]
        # 始终保留一个新建动作作为动态冲突回退：正常有合法既有候选时
        # 它保持 masked；若候选已被前序 ZPM 占用，则环境会开放它。
        synthetic = engine._new_candidate(zpm, demands, "PM")
        synthetic_feature = _evaluate(engine, synthetic, zpm, demands, parent, len(features))
        synthetic_feature.synthetic = True
        synthetic_feature.feasible = not any(item.feasible for item in features)
        synthetic_feature.reason = "无可用既有点，按规则新建 PTECH"
        if not isfinite(synthetic_feature.proxy_cost):
            synthetic_feature.route_cost = sum(
                engine.graph.distance(synthetic.node_code, demand.node_code) for demand in demands
            )
            parent_node, _ = engine.graph.nearest_node(parent.point)
            synthetic_feature.uplink_cost = engine.graph.distance(synthetic.node_code, parent_node)
            synthetic_feature.proxy_cost = (
                synthetic_feature.route_cost
                + float(engine.bundle.config["uplink_weight"]) * synthetic_feature.uplink_cost
                + synthetic.construction_cost
            )
        features.append(synthetic_feature)
        table[zpm.code] = features
    return PMFeatureTable(table, baseline)


def write_feature_table(table: PMFeatureTable, path: str) -> None:
    import json
    from pathlib import Path
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(table.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
