"""轻量空间函数，不强制依赖 GIS 第三方库。"""

from __future__ import annotations

from .models import Point


def point_in_ring(point: Point, ring: list[Point]) -> bool:
    """射线法判断点是否位于单个闭合环内（边界视为区内）。"""
    if len(ring) < 3:
        return False
    inside = False
    j = len(ring) - 1
    for i, pi in enumerate(ring):
        pj = ring[j]
        cross = (pi.y > point.y) != (pj.y > point.y)
        if cross:
            x_on_edge = (pj.x - pi.x) * (point.y - pi.y) / (pj.y - pi.y) + pi.x
            if point.x <= x_on_edge:
                inside = not inside
        j = i
    return inside


def point_in_polygon(point: Point, rings: list[list[Point]]) -> bool:
    """GeoJSON Polygon：首环为外边界，后续环为空洞。"""
    if not rings or not point_in_ring(point, rings[0]):
        return False
    return not any(point_in_ring(point, hole) for hole in rings[1:])
