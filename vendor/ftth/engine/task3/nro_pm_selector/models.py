"""第三阶段使用的领域对象与字段转换工具。

这里保留源属性表中的法文字段名（例如 REF_NRO、PROPRETAI），避免输出时
发生二次映射；算法内部再使用可读的 Python 属性名。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import hypot
from typing import Any, Iterable


TRUE_VALUES = {"1", "true", "yes", "y", "是", "可", "满足", "有"}
FALSE_VALUES = {"0", "false", "no", "n", "否", "不可", "不满足", "无"}


def text(value: Any, default: str = "") -> str:
    """把 CSV/GeoJSON 的空值统一成干净字符串。"""
    if value is None:
        return default
    return str(value).strip()


def number(value: Any, default: float = 0.0) -> float:
    """宽容读取数值；空串使用默认值，非法文本抛出清晰错误。"""
    if value is None or text(value) == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"无法把 {value!r} 转成数值") from exc


def integer(value: Any, default: int = 0) -> int:
    return int(round(number(value, default)))


def boolean(value: Any, default: bool = True) -> bool:
    raw = text(value).lower()
    if not raw:
        return default
    if raw in TRUE_VALUES:
        return True
    if raw in FALSE_VALUES:
        return False
    raise ValueError(f"无法识别布尔值 {value!r}，请使用 是/否、true/false 或 1/0")


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def distance(self, other: "Point") -> float:
        return hypot(self.x - other.x, self.y - other.y)


@dataclass
class Zone:
    """ZNRO 或 ZPM 服务区。polygon 为空时依靠显式归属字段。"""

    code: str
    kind: str
    ref_nro: str = ""
    ref_pm: str = ""
    capacity: int = 0
    polygon: list[list[Point]] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class Demand:
    """选址需求点：优先 PBO，否则使用 IMB 作为代理。"""

    code: str
    point: Point
    ports: int
    ref_nro_zone: str = ""
    ref_pm_zone: str = ""
    node_code: str = ""
    source_layer: str = "IMB"
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class Candidate:
    """可安装 NRO/PM 的候选点。"""

    code: str
    point: Point
    node_code: str
    source: str
    allowed_types: set[str]
    existing_site_code: str = ""
    existing_site_type: str = ""
    ptech_code: str = ""
    ref_nro_zone: str = ""
    ref_pm_zone: str = ""
    install_space: bool = True
    maintainable: bool = True
    property_clear: bool = True
    reusable: bool = True
    construction_cost: float = 0.0
    properties: dict[str, Any] = field(default_factory=dict)

    def permits(self, facility_type: str) -> bool:
        return facility_type.upper() in self.allowed_types

    @property
    def is_existing(self) -> bool:
        return bool(self.existing_site_code)

    @property
    def feasible(self) -> bool:
        return self.install_space and self.maintainable and self.property_clear


@dataclass
class SiteSelection:
    facility_type: str
    zone_code: str
    site_code: str
    candidate_code: str
    ptech_code: str
    point: Point
    ref_nro: str
    capacity: int
    demand_ports: int
    route_cost: float
    construction_cost: float
    total_score: float
    reused: bool
    reason: str
    candidate_source: str


@dataclass
class Rejection:
    facility_type: str
    zone_code: str
    candidate_code: str
    reason: str


@dataclass
class Edge:
    code: str
    origin: str
    destination: str
    length: float
    status: str = ""
    construction_cost: float = 0.0
    properties: dict[str, Any] = field(default_factory=dict)


def unique_codes(items: Iterable[Any], label: str) -> None:
    """校验 CODE 唯一性，这是属性表中所有主图层的共同硬约束。"""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in items:
        code = item.code
        if not code:
            raise ValueError(f"{label} 存在空 CODE")
        if code in seen:
            duplicates.add(code)
        seen.add(code)
    if duplicates:
        raise ValueError(f"{label} CODE 不唯一: {sorted(duplicates)}")
