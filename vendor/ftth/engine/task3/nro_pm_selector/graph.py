"""INFRASTRUCTURE 管网图及最短路计算。"""

from __future__ import annotations

import heapq
from collections import defaultdict
from math import inf

from .models import Edge, Point


class NetworkGraph:
    def __init__(self, nodes: dict[str, Point], edges: list[Edge]) -> None:
        self.nodes = nodes
        self.edges = edges
        self.adjacency: dict[str, list[tuple[str, float, str]]] = defaultdict(list)
        for edge in edges:
            if edge.origin not in nodes or edge.destination not in nodes:
                continue
            # 目标是“总线缆长度”；若后续需经济最优，可在此叠加建设成本。
            weight = edge.length if edge.length > 0 else nodes[edge.origin].distance(nodes[edge.destination])
            self.adjacency[edge.origin].append((edge.destination, weight, edge.code))
            self.adjacency[edge.destination].append((edge.origin, weight, edge.code))
        self._distance_cache: dict[str, dict[str, float]] = {}

    def nearest_node(self, point: Point) -> tuple[str, float]:
        if not self.nodes:
            return "", inf
        # 优先吸附到至少连接一条 INFRASTRUCTURE 的节点，避免落在孤立 SITE/BOITE 上。
        connected = {code: self.nodes[code] for code in self.adjacency if code in self.nodes}
        pool = connected or self.nodes
        node, location = min(pool.items(), key=lambda item: item[1].distance(point))
        return node, location.distance(point)

    def distances_from(self, source: str) -> dict[str, float]:
        if source in self._distance_cache:
            return self._distance_cache[source]
        distances = {source: 0.0}
        queue: list[tuple[float, str]] = [(0.0, source)]
        while queue:
            current_distance, node = heapq.heappop(queue)
            if current_distance != distances.get(node):
                continue
            for neighbour, edge_length, _ in self.adjacency.get(node, []):
                candidate = current_distance + edge_length
                if candidate < distances.get(neighbour, inf):
                    distances[neighbour] = candidate
                    heapq.heappush(queue, (candidate, neighbour))
        self._distance_cache[source] = distances
        return distances

    def distance(self, source: str, destination: str) -> float:
        if not source or not destination:
            return inf
        if source == destination:
            return 0.0
        return self.distances_from(source).get(destination, inf)


