"""NRO/PM 选址核心算法。

算法采用可解释的离散设施选址：在候选点集合中枚举设施位置，用
INFRASTRUCTURE 图上的 Dijkstra 最短路计算总线缆长度，并施加容量、连通性、
安装空间、维护条件和产权条件约束。规模较小、管网清晰的示例工程正适合此法。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, inf, isfinite
from typing import Iterable

from .graph import NetworkGraph
from .io import InputBundle
from .models import Candidate, Demand, Point, Rejection, SiteSelection, Zone, text


@dataclass
class SelectionResult:
    selections: list[SiteSelection]
    rejections: list[Rejection]
    validation: dict[str, object]


class SelectionEngine:
    def __init__(self, bundle: InputBundle) -> None:
        self.bundle = bundle
        self.graph = NetworkGraph(bundle.nodes, bundle.edges)
        self.rejections: list[Rejection] = []
        self._used_site_codes = {
            text(row.get("CODE")) for row in bundle.raw_sites if text(row.get("CODE"))
        }
        self._sequence = {"NRO": 1, "PM": 1, "PTECH": 1}
        self._prepare_assignments()

    def _prepare_assignments(self) -> None:
        """步骤 1：给需求点补齐 ZNRO/ZPM 归属和管网吸附节点。"""
        zpm_by_code = {zone.code: zone for zone in self.bundle.zpm}
        znro_codes = {zone.code for zone in self.bundle.znro}
        for demand in self.bundle.demands:
            if not demand.ref_pm_zone:
                demand.ref_pm_zone = self.bundle.zone_for_point(demand.point, self.bundle.zpm)
            if not demand.ref_pm_zone:
                raise ValueError(
                    f"需求点 {demand.code} 无法确定 ZPM；请提供 ZPM 面或辅助字段 ZPM_CODE/REF_ZPM"
                )
            if demand.ref_pm_zone not in zpm_by_code:
                raise ValueError(f"需求点 {demand.code} 引用了不存在的 ZPM {demand.ref_pm_zone}")
            if not demand.ref_nro_zone:
                demand.ref_nro_zone = self.bundle.zone_for_point(demand.point, self.bundle.znro)
            if not demand.ref_nro_zone:
                demand.ref_nro_zone = zpm_by_code[demand.ref_pm_zone].ref_nro
            if demand.ref_nro_zone not in znro_codes:
                raise ValueError(
                    f"需求点 {demand.code} 无法确定 ZNRO；请提供 ZNRO 面、ZNRO_CODE，"
                    "或在 ZPM 中填写 ZNRO_CODE/REF_NRO"
                )
            if (
                not demand.node_code
                or demand.node_code not in self.graph.nodes
                or demand.node_code not in self.graph.adjacency
            ):
                node, snap_distance = self.graph.nearest_node(demand.point)
                if not node or snap_distance > float(self.bundle.config["max_snap_distance"]):
                    raise ValueError(
                        f"需求点 {demand.code} 距管网过远（{snap_distance:.2f} > "
                        f"{self.bundle.config['max_snap_distance']}）"
                    )
                demand.node_code = node

        # 检查 ZPM 是否真的完全归属于一个 ZNRO（至少从需求归属角度）。
        for zpm_code in zpm_by_code:
            parents = {d.ref_nro_zone for d in self.bundle.demands if d.ref_pm_zone == zpm_code}
            if len(parents) > 1:
                raise ValueError(f"ZPM {zpm_code} 内需求跨越多个 ZNRO: {sorted(parents)}")
            if parents and not zpm_by_code[zpm_code].ref_nro:
                zpm_by_code[zpm_code].ref_nro = next(iter(parents))

    def _candidate_zone(self, candidate: Candidate, zone: Zone) -> bool:
        """显式辅助归属优先，其次用多边形，最后识别已有 REF 反向关系。"""
        explicit = candidate.ref_nro_zone if zone.kind == "ZNRO" else candidate.ref_pm_zone
        if explicit:
            return explicit == zone.code
        if zone.polygon:
            return self.bundle.zone_for_point(candidate.point, [zone]) == zone.code
        if candidate.is_existing:
            reference = zone.ref_nro if zone.kind == "ZNRO" else zone.ref_pm
            if reference:
                return reference == candidate.existing_site_code
        return False

    def _snap_candidate(self, candidate: Candidate) -> tuple[str, float]:
        if candidate.node_code in self.graph.nodes and candidate.node_code in self.graph.adjacency:
            return candidate.node_code, self.graph.nodes[candidate.node_code].distance(candidate.point)
        return self.graph.nearest_node(candidate.point)

    def _road_access_limit(self, facility_type: str) -> float:
        key = "pm_road_access_radius_m" if facility_type.upper() == "PM" else "nro_road_access_radius_m"
        return float(self.bundle.config.get(key, self.bundle.config["max_snap_distance"]))

    def _new_candidate(self, zone: Zone, demands: list[Demand], facility_type: str) -> Candidate:
        """只有原候选点均不可用时，才在需求关联的管网节点上规划新 PTECH。"""
        possible_nodes = sorted({d.node_code for d in demands if d.node_code in self.graph.nodes})
        if not possible_nodes:
            raise ValueError(f"{zone.code} 无可连接的需求节点，不能规划新 {facility_type}")
        best_node = min(
            possible_nodes,
            key=lambda node: sum(self.graph.distance(node, demand.node_code) for demand in demands),
        )
        point = self.graph.nodes[best_node]
        ptech_code = self._next_code("PTECH")
        return Candidate(
            code=f"NEW:{facility_type}:{zone.code}", point=point, node_code=best_node,
            source="NEW_PTECH", allowed_types={facility_type}, ptech_code=ptech_code,
            ref_nro_zone=zone.code if facility_type == "NRO" else zone.ref_nro,
            ref_pm_zone=zone.code if facility_type == "PM" else "",
            construction_cost=float(self.bundle.config["new_ptech_penalty"]),
        )

    def _next_code(self, code_type: str) -> str:
        prefix = self.bundle.config["code_prefix"][code_type]
        while True:
            value = f"{prefix}_{self._sequence[code_type]:04d}"
            self._sequence[code_type] += 1
            if value not in self._used_site_codes:
                if code_type in {"NRO", "PM"}:
                    self._used_site_codes.add(value)
                return value

    def _score_candidates(
        self,
        facility_type: str,
        zone: Zone,
        demands: list[Demand],
        capacity: int,
        parent_nro: SiteSelection | None = None,
    ) -> tuple[Candidate, float, float, str]:
        """步骤 2：硬约束过滤，步骤 3：按总线缆长度目标评分。"""
        required_capacity = ceil(sum(d.ports for d in demands) * (1 + self.bundle.config["capacity_reserve_ratio"]))
        if required_capacity > capacity:
            raise ValueError(
                f"{zone.code} 含余量后需 {required_capacity} 端口，超过 {facility_type} 容量 {capacity}"
            )

        candidates = [
            candidate for candidate in self.bundle.candidates
            if candidate.permits(facility_type) and self._candidate_zone(candidate, zone)
        ]
        scored: list[tuple[float, float, float, Candidate, str]] = []
        access_limit = self._road_access_limit(facility_type)
        for candidate in candidates:
            if candidate.is_existing and not candidate.reusable:
                self.rejections.append(Rejection(facility_type, zone.code, candidate.code, "已有站点状态不可复用"))
                continue
            if not candidate.feasible:
                failed = []
                if not candidate.install_space:
                    failed.append("无安装空间")
                if not candidate.maintainable:
                    failed.append("不便维护")
                if not candidate.property_clear:
                    failed.append("产权不明确")
                self.rejections.append(Rejection(facility_type, zone.code, candidate.code, "、".join(failed)))
                continue
            node, snap_distance = self._snap_candidate(candidate)
            if not node or snap_distance > access_limit:
                self.rejections.append(
                    Rejection(
                        facility_type,
                        zone.code,
                        candidate.code,
                        f"距管网 {snap_distance:.2f}，超过 {access_limit:.2f} 米直连光纤接入阈值",
                    )
                )
                continue
            candidate.node_code = node
            distances = [self.graph.distance(node, demand.node_code) for demand in demands]
            if any(not isfinite(value) for value in distances):
                self.rejections.append(Rejection(facility_type, zone.code, candidate.code, "无法连通全部需求点"))
                continue
            route_cost = sum(distances) + snap_distance
            if parent_nro is not None:
                parent_node, _ = self.graph.nearest_node(parent_nro.point)
                uplink = self.graph.distance(node, parent_node)
                if not isfinite(uplink):
                    self.rejections.append(Rejection(facility_type, zone.code, candidate.code, "无法连通所属 NRO"))
                    continue
                route_cost += float(self.bundle.config["uplink_weight"]) * uplink
            priority_adjustment = 0.0
            reason = "满足道路接入约束且总管网路径最短"
            if candidate.is_existing:
                priority_adjustment -= float(self.bundle.config["existing_site_bonus"])
                reason = "优先复用已有可用站点；同时满足容量、管网连通和施工条件"
            elif candidate.source == "PTECH":
                priority_adjustment -= float(self.bundle.config["ptech_bonus"])
                reason = "优先利用已有 PTECH；在可用候选中综合成本最低"
            total = route_cost + candidate.construction_cost + priority_adjustment
            scored.append((total, route_cost, candidate.construction_cost, candidate, reason))

        if not scored:
            candidate = self._new_candidate(zone, demands, facility_type)
            node = candidate.node_code
            route_cost = sum(self.graph.distance(node, demand.node_code) for demand in demands)
            if parent_nro is not None:
                parent_node, _ = self.graph.nearest_node(parent_nro.point)
                route_cost += float(self.bundle.config["uplink_weight"]) * self.graph.distance(node, parent_node)
            return candidate, route_cost, route_cost + candidate.construction_cost, "无可用既有点，按规则规划新 PTECH"

        scored.sort(key=lambda item: (item[0], item[1], item[3].code))
        total, route_cost, construction_cost, candidate, reason = scored[0]
        return candidate, route_cost, total, reason

    def _make_selection(
        self,
        facility_type: str,
        zone: Zone,
        demands: list[Demand],
        parent_nro: SiteSelection | None = None,
    ) -> SiteSelection:
        candidate, route_cost, total_score, reason = self._score_candidates(
            facility_type, zone, demands, zone.capacity, parent_nro
        )
        site_code = candidate.existing_site_code or self._next_code(facility_type)
        ref_nro = site_code if facility_type == "NRO" else (parent_nro.site_code if parent_nro else "")
        return SiteSelection(
            facility_type=facility_type, zone_code=zone.code, site_code=site_code,
            candidate_code=candidate.code, ptech_code=candidate.ptech_code,
            point=candidate.point, ref_nro=ref_nro, capacity=zone.capacity,
            demand_ports=sum(d.ports for d in demands), route_cost=route_cost,
            construction_cost=candidate.construction_cost, total_score=total_score,
            reused=candidate.is_existing, reason=reason, candidate_source=candidate.source,
        )

    def run(self) -> SelectionResult:
        selections: list[SiteSelection] = []
        nro_by_zone: dict[str, SiteSelection] = {}

        # 步骤 4：每个 ZNRO 选一个 NRO。
        for zone in sorted(self.bundle.znro, key=lambda item: item.code):
            demands = [d for d in self.bundle.demands if d.ref_nro_zone == zone.code]
            if not demands:
                raise ValueError(f"ZNRO {zone.code} 内没有需求点")
            selection = self._make_selection("NRO", zone, demands)
            selections.append(selection)
            nro_by_zone[zone.code] = selection
            zone.ref_nro = selection.site_code

        # 步骤 5：每个 ZPM 选一个 PM，并计入 PM→NRO 上联距离。
        for zone in sorted(self.bundle.zpm, key=lambda item: item.code):
            demands = [d for d in self.bundle.demands if d.ref_pm_zone == zone.code]
            if not demands:
                raise ValueError(f"ZPM {zone.code} 内没有需求点")
            parent_zone = zone.ref_nro or demands[0].ref_nro_zone
            parent_nro = nro_by_zone.get(parent_zone)
            if parent_nro is None:
                raise ValueError(f"ZPM {zone.code} 无法关联已选 NRO（ZNRO={parent_zone}）")
            selection = self._make_selection("PM", zone, demands, parent_nro)
            selections.append(selection)
            zone.ref_pm = selection.site_code
            zone.ref_nro = parent_nro.site_code

        validation = self._validate_result(selections)
        return SelectionResult(selections, self.rejections, validation)

    def _validate_result(self, selections: Iterable[SiteSelection]) -> dict[str, object]:
        """步骤 6：交付前校验一对一归属、容量、连通与编码唯一性。"""
        values = list(selections)
        errors: list[str] = []
        site_codes = [item.site_code for item in values]
        if len(site_codes) != len(set(site_codes)):
            errors.append("选址结果 SITE.CODE 不唯一")
        if len([s for s in values if s.facility_type == "NRO"]) != len(self.bundle.znro):
            errors.append("不是每个 ZNRO 都恰好对应一个 NRO")
        if len([s for s in values if s.facility_type == "PM"]) != len(self.bundle.zpm):
            errors.append("不是每个 ZPM 都恰好对应一个 PM")
        reserve = float(self.bundle.config["capacity_reserve_ratio"])
        for item in values:
            if ceil(item.demand_ports * (1 + reserve)) > item.capacity:
                errors.append(f"{item.site_code} 容量不足")
            if not isfinite(item.route_cost):
                errors.append(f"{item.site_code} 存在不可达路径")
            _, access_distance = self.graph.nearest_node(item.point)
            if access_distance > self._road_access_limit(item.facility_type):
                errors.append(
                    f"{item.site_code} 距管网 {access_distance:.2f} 米，超过"
                    f" {self._road_access_limit(item.facility_type):.2f} 米接入阈值"
                )
        return {
            "passed": not errors,
            "errors": errors,
            "counts": {
                "ZNRO": len(self.bundle.znro), "ZPM": len(self.bundle.zpm),
                "NRO_selected": sum(s.facility_type == "NRO" for s in values),
                "PM_selected": sum(s.facility_type == "PM" for s in values),
                "demand_points": len(self.bundle.demands),
            },
            "total_demand_ports": sum(d.ports for d in self.bundle.demands),
            "total_route_cost": sum(s.route_cost for s in values),
        }


