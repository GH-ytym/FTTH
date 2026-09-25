"""输入输出模块：读取第三阶段图层，校验字段，并写出标准属性结果。

支持 CSV 与 GeoJSON。若生产环境使用 Shapefile，可先由 QGIS/GeoPandas 转成
GeoJSON；算法字段名与原 SITE/ZNRO/ZPM/PTECH DBF 保持一致。
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .geometry import point_in_polygon
from .models import (
    Candidate,
    Demand,
    Edge,
    Point,
    Rejection,
    SiteSelection,
    Zone,
    boolean,
    integer,
    number,
    text,
    unique_codes,
)


SITE_FIELDS = [
    "fid", "CODE", "REF_PLAQUE", "REF_NRO", "TYPE", "FABRIQUANT", "REF_PRODUI",
    "MODE_POSE", "STATUT", "PROPRETAI", "GESTIONNAI", "ADRESSE", "COMMUNE",
    "CODE_POSTA", "X", "Y",
]
PTECH_FIELDS = [
    "fid", "CODE", "NOM", "REF_PLAQUE", "TYPE", "NATURE", "HAUTEUR_AP",
    "TYPE_APPUI", "EFFORT_APP", "NB_BOITIER", "STATUT", "PROPRETAI",
    "GESTIONNAI", "ADRESSE", "VILLE", "CODE_POSTA", "X", "Y", "COMMENT",
]
ZNRO_FIELDS = ["fid", "CODE", "NOM", "REF_PLAQUE", "REF_NRO", "STATUT", "NIB_PRISES", "COMMENT"]
ZPM_FIELDS = ["fid", "CODE", "NOM", "REF_PLAQUE", "REF_PM", "REF_NRO", "STATUT", "NIB_PRISES", "COMMENT"]


DEFAULT_CONFIG: dict[str, Any] = {
    "nro_capacity": 10000,
    "pm_capacity": 576,
    "capacity_reserve_ratio": 0.15,
    "max_snap_distance": 200.0,
    "nro_road_access_radius_m": 50.0,
    "pm_road_access_radius_m": 10.0,
    "uplink_weight": 1.0,
    "existing_site_bonus": 1_000_000.0,
    "ptech_bonus": 10_000.0,
    "new_ptech_penalty": 100_000.0,
    "code_prefix": {"NRO": "NRO", "PM": "PM", "PTECH": "PTC"},
    "status_usable": ["EXISTANT", "EXISTING", "IN_SERVICE", "EN_SERVICE", "已有", "在用", "规划"],
    "candidate_ptech_types": ["CHAMBRE", "APPUI", "LOCAL_TECHNIQUE", "SALLE_TECHNIQUE", "管道交汇点", "弱电间", "通信机房"],
}


def _upper_keys(row: dict[str, Any]) -> dict[str, Any]:
    return {str(key).strip().upper(): value for key, value in row.items()}


def _read_csv(path: Path) -> list[dict[str, Any]]:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                return [_upper_keys(row) for row in csv.DictReader(handle)]
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法识别 CSV 编码: {path}")


def _geojson_polygon(geometry: dict[str, Any]) -> list[list[Point]]:
    if geometry.get("type") != "Polygon":
        return []
    return [[Point(number(x), number(y)) for x, y, *_ in ring] for ring in geometry.get("coordinates", [])]


def _read_geojson(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    rows: list[dict[str, Any]] = []
    for feature in payload.get("features", []):
        row = _upper_keys(feature.get("properties") or {})
        geometry = feature.get("geometry") or {}
        if geometry.get("type") == "Point":
            coords = geometry.get("coordinates", [])
            if len(coords) >= 2:
                row["X"], row["Y"] = coords[0], coords[1]
        elif geometry.get("type") == "Polygon":
            row["__POLYGON__"] = _geojson_polygon(geometry)
        elif geometry.get("type") == "MultiPolygon":
            polygons = geometry.get("coordinates", [])
            if polygons:
                row["__POLYGON__"] = [
                    [Point(number(x), number(y)) for x, y, *_ in ring]
                    for ring in polygons[0]
                ]
        rows.append(row)
    return rows


def read_layer(input_dir: Path, name: str, required: bool = False) -> list[dict[str, Any]]:
    """按 `NAME.geojson`、`NAME.json`、`NAME.csv` 顺序查找图层。"""
    variants = {child.name.lower(): child for child in input_dir.iterdir() if child.is_file()}
    for suffix in (".geojson", ".json", ".csv"):
        path = variants.get(f"{name.lower()}{suffix}")
        if path:
            return _read_csv(path) if suffix == ".csv" else _read_geojson(path)
    if required:
        raise FileNotFoundError(f"缺少必需图层 {name}.csv 或 {name}.geojson")
    return []


def _point(row: dict[str, Any], label: str) -> Point:
    if text(row.get("X")) == "" or text(row.get("Y")) == "":
        raise ValueError(f"{label} {row.get('CODE', '')} 缺少 X/Y")
    return Point(number(row["X"]), number(row["Y"]))


def _allowed_types(row: dict[str, Any]) -> set[str]:
    raw = text(row.get("ALLOWED_TYPES") or row.get("FACILITY_TYPE") or "NRO,PM")
    return {item.strip().upper() for item in raw.replace(";", ",").split(",") if item.strip()}


@dataclass
class InputBundle:
    config: dict[str, Any]
    raw_sites: list[dict[str, Any]]
    raw_ptech: list[dict[str, Any]]
    raw_znro: list[dict[str, Any]]
    raw_zpm: list[dict[str, Any]]
    raw_imb: list[dict[str, Any]]
    raw_boite: list[dict[str, Any]]
    raw_infrastructure: list[dict[str, Any]]
    raw_candidates: list[dict[str, Any]]
    znro: list[Zone] = field(default_factory=list)
    zpm: list[Zone] = field(default_factory=list)
    demands: list[Demand] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    nodes: dict[str, Point] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, input_dir: str | Path, config_path: str | Path | None = None) -> "InputBundle":
        directory = Path(input_dir)
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        selected_config = Path(config_path) if config_path else directory / "config.json"
        if selected_config.exists():
            user_config = json.loads(selected_config.read_text(encoding="utf-8-sig"))
            for key, value in user_config.items():
                if key == "code_prefix":
                    config["code_prefix"].update(value)
                else:
                    config[key] = value
        bundle = cls(
            config=config,
            raw_sites=read_layer(directory, "SITE"),
            raw_ptech=read_layer(directory, "PTECH", required=True),
            raw_znro=read_layer(directory, "ZNRO", required=True),
            raw_zpm=read_layer(directory, "ZPM", required=True),
            raw_imb=read_layer(directory, "IMB"),
            raw_boite=read_layer(directory, "BOITE"),
            raw_infrastructure=read_layer(directory, "INFRASTRUCTURE", required=True),
            raw_candidates=read_layer(directory, "CANDIDATES"),
        )
        bundle._normalise()
        bundle._validate()
        return bundle

    def _normalise(self) -> None:
        # 步骤 1：建立服务区对象；容量为 0 时使用 config 默认值。
        self.znro = [
            Zone(
                code=text(row.get("CODE")), kind="ZNRO", ref_nro=text(row.get("REF_NRO")),
                capacity=integer(row.get("CAPACITE") or self.config["nro_capacity"]),
                polygon=row.get("__POLYGON__", []), properties=row,
            )
            for row in self.raw_znro
        ]
        self.zpm = [
            Zone(
                code=text(row.get("CODE")), kind="ZPM",
                ref_nro=text(row.get("ZNRO_CODE") or row.get("REF_ZNRO") or row.get("REF_NRO")),
                ref_pm=text(row.get("REF_PM")),
                capacity=integer(row.get("CAPACITE") or self.config["pm_capacity"]),
                polygon=row.get("__POLYGON__", []), properties=row,
            )
            for row in self.raw_zpm
        ]

        # 步骤 2：把 PTECH、已有 SITE 和 BOITE 加入管网节点字典。
        for rows in (self.raw_ptech, self.raw_sites, self.raw_boite):
            for row in rows:
                code = text(row.get("CODE"))
                if code and text(row.get("X")) and text(row.get("Y")):
                    self.nodes[code] = _point(row, "网络节点")

        # 步骤 3：把 INFRASTRUCTURE 的端点关系转换为无向图边。
        self.edges = [
            Edge(
                code=text(row.get("CODE")), origin=text(row.get("ORIGINE")),
                destination=text(row.get("EXTREMITE")), length=number(row.get("LONGUEUR")),
                status=text(row.get("STATUT")), construction_cost=number(row.get("COST") or 0),
                properties=row,
            )
            for row in self.raw_infrastructure
        ]

        # 步骤 4：若已有 PBO，就用 PBO 作需求点；否则按文档顺序用 IMB 代理。
        pbo_rows = [row for row in self.raw_boite if text(row.get("TYPE")).upper() == "PBO"]
        source_rows = pbo_rows or self.raw_imb
        source_layer = "PBO" if pbo_rows else "IMB"
        if not pbo_rows:
            self.warnings.append("未发现 BOITE.TYPE=PBO，当前以 IMB 作为第三阶段需求代理点。")
        for row in source_rows:
            ports = integer(
                row.get("NB_LOGEMEN") if source_layer == "PBO" else row.get("NB_LOC_TOT")
            )
            if source_layer == "IMB" and not ports:
                ports = integer(row.get("NB_LOC_RES")) + integer(row.get("NB_LOC_PRO"))
            self.demands.append(
                Demand(
                    code=text(row.get("CODE")), point=_point(row, source_layer), ports=ports,
                    ref_nro_zone=text(row.get("REF_ZNRO") or row.get("ZNRO_CODE")),
                    ref_pm_zone=text(row.get("REF_ZPM") or row.get("ZPM_CODE")),
                    node_code=text(row.get("NODE_CODE") or row.get("CODE_PTC")),
                    source_layer=source_layer, properties=row,
                )
            )

        # 步骤 5：建立候选点，优先级由 selector 处理：已有 SITE > PTECH > 新建点。
        usable = {text(v).upper() for v in self.config["status_usable"]}
        for row in self.raw_sites:
            site_type = text(row.get("TYPE")).upper()
            if site_type not in {"NRO", "PM"}:
                continue
            status = text(row.get("STATUT")).upper()
            reusable = not status or status in usable
            code = text(row.get("CODE"))
            self.candidates.append(
                Candidate(
                    code=f"SITE:{code}", point=_point(row, "SITE"),
                    node_code=text(row.get("CODE_PTC") or code), source="EXISTING_SITE",
                    allowed_types={site_type}, existing_site_code=code,
                    existing_site_type=site_type, ptech_code=text(row.get("CODE_PTC")),
                    ref_nro_zone=text(row.get("REF_ZNRO") or row.get("ZNRO_CODE")),
                    ref_pm_zone=text(row.get("REF_ZPM") or row.get("ZPM_CODE")),
                    reusable=reusable, properties=row,
                )
            )
        allowed_ptech = {text(v).upper() for v in self.config["candidate_ptech_types"]}
        for row in self.raw_ptech:
            ptech_type = text(row.get("TYPE")).upper()
            if allowed_ptech and ptech_type not in allowed_ptech and not boolean(row.get("IS_CANDIDATE"), False):
                continue
            code = text(row.get("CODE"))
            self.candidates.append(
                Candidate(
                    code=f"PTECH:{code}", point=_point(row, "PTECH"), node_code=code,
                    source="PTECH", allowed_types=_allowed_types(row), ptech_code=code,
                    ref_nro_zone=text(row.get("REF_ZNRO") or row.get("ZNRO_CODE")),
                    ref_pm_zone=text(row.get("REF_ZPM") or row.get("ZPM_CODE")),
                    install_space=boolean(row.get("INSTALL_SPACE"), True),
                    maintainable=boolean(row.get("MAINTAINABLE"), True),
                    property_clear=boolean(row.get("PROPERTY_CLEAR"), True), properties=row,
                )
            )
        for row in self.raw_candidates:
            code = text(row.get("CODE"))
            node_code = text(row.get("NODE_CODE") or row.get("CODE_PTC"))
            self.candidates.append(
                Candidate(
                    code=f"CANDIDATE:{code}", point=_point(row, "CANDIDATES"),
                    node_code=node_code, source=text(row.get("SOURCE") or "SURVEY_CANDIDATE"),
                    allowed_types=_allowed_types(row), ptech_code=text(row.get("CODE_PTC")),
                    ref_nro_zone=text(row.get("REF_ZNRO") or row.get("ZNRO_CODE")),
                    ref_pm_zone=text(row.get("REF_ZPM") or row.get("ZPM_CODE")),
                    install_space=boolean(row.get("INSTALL_SPACE"), True),
                    maintainable=boolean(row.get("MAINTAINABLE"), True),
                    property_clear=boolean(row.get("PROPERTY_CLEAR"), True),
                    construction_cost=number(row.get("CONSTRUCTION_COST") or 0), properties=row,
                )
            )

    def _validate(self) -> None:
        unique_codes(self.znro, "ZNRO")
        unique_codes(self.zpm, "ZPM")
        unique_codes(self.demands, "需求点")
        znro_codes = {zone.code for zone in self.znro}
        for zone in self.zpm:
            if zone.ref_nro and zone.ref_nro not in znro_codes:
                raise ValueError(f"ZPM {zone.code} 的 REF_NRO={zone.ref_nro} 未对应有效 ZNRO.CODE")
        if not self.demands:
            raise ValueError("既没有 PBO，也没有 IMB，无法形成选址需求")
        if not self.edges:
            raise ValueError("INFRASTRUCTURE 为空，无法按管网最短路选址")

    def zone_for_point(self, point: Point, zones: Iterable[Zone]) -> str:
        matches = [zone.code for zone in zones if zone.polygon and point_in_polygon(point, zone.polygon)]
        if len(matches) > 1:
            raise ValueError(f"点 ({point.x}, {point.y}) 同时落入重叠区域 {matches}")
        return matches[0] if matches else ""


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            # 输入读取时统一成大写键；输出按属性表恢复 fid 等标准大小写。
            upper = {str(key).upper(): value for key, value in row.items()}
            writer.writerow({field: upper.get(field.upper(), "") for field in fields})


def _next_fid(rows: list[dict[str, Any]]) -> int:
    ids = [integer(row.get("FID") or row.get("fid")) for row in rows]
    return max(ids or [0]) + 1


def write_outputs(
    output_dir: str | Path,
    bundle: InputBundle,
    selections: list[SiteSelection],
    rejections: list[Rejection],
    validation: dict[str, Any],
) -> None:
    """按属性表字段输出 SITE/ZNRO/ZPM/PTECH 及可审计报告。"""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)

    site_rows = [dict(row) for row in bundle.raw_sites]
    existing_codes = {text(row.get("CODE")) for row in site_rows}
    next_site_fid = _next_fid(site_rows)
    for selection in selections:
        if selection.site_code in existing_codes:
            # 复用站点仅补齐归属，不破坏已有厂商、地址、产权等字段。
            for row in site_rows:
                if text(row.get("CODE")) == selection.site_code:
                    row["REF_NRO"] = selection.ref_nro
                    row["TYPE"] = selection.facility_type
            continue
        site_rows.append({
            "fid": next_site_fid, "CODE": selection.site_code,
            "REF_PLAQUE": "", "REF_NRO": selection.ref_nro, "TYPE": selection.facility_type,
            "FABRIQUANT": "", "REF_PRODUI": "", "MODE_POSE": "",
            "STATUT": "PROJET", "PROPRETAI": "", "GESTIONNAI": "",
            "ADRESSE": "", "COMMUNE": "", "CODE_POSTA": "",
            "X": selection.point.x, "Y": selection.point.y,
        })
        next_site_fid += 1
    _write_csv(directory / "SITE_selected.csv", site_rows, SITE_FIELDS)

    # 只有无 PTECH 的自定义候选点会生成新增 PTECH；已有点保持原样。
    ptech_rows = [dict(row) for row in bundle.raw_ptech]
    existing_ptech = {text(row.get("CODE")) for row in ptech_rows}
    next_ptech_fid = _next_fid(ptech_rows)
    for selection in selections:
        if not selection.ptech_code or selection.ptech_code in existing_ptech:
            continue
        ptech_rows.append({
            "fid": next_ptech_fid, "CODE": selection.ptech_code,
            "NOM": f"{selection.facility_type} 新建安装点", "REF_PLAQUE": "",
            "TYPE": "AUTRE", "NATURE": "NEW_SITE_SUPPORT", "NB_BOITIER": 1,
            "STATUT": "PROJET", "X": selection.point.x, "Y": selection.point.y,
            "COMMENT": "第三阶段选址自动新增；施工前需现场复核。",
        })
        existing_ptech.add(selection.ptech_code)
        next_ptech_fid += 1
    _write_csv(directory / "PTECH_selected.csv", ptech_rows, PTECH_FIELDS)

    nro_by_zone = {s.zone_code: s for s in selections if s.facility_type == "NRO"}
    pm_by_zone = {s.zone_code: s for s in selections if s.facility_type == "PM"}
    znro_rows: list[dict[str, Any]] = []
    for row in bundle.raw_znro:
        copy = dict(row)
        selected = nro_by_zone.get(text(row.get("CODE")))
        if selected:
            copy["REF_NRO"] = selected.site_code
            copy["NIB_PRISES"] = selected.demand_ports
        znro_rows.append(copy)
    _write_csv(directory / "ZNRO_selected.csv", znro_rows, ZNRO_FIELDS)

    zpm_rows: list[dict[str, Any]] = []
    for row in bundle.raw_zpm:
        copy = dict(row)
        selected = pm_by_zone.get(text(row.get("CODE")))
        if selected:
            copy["REF_PM"] = selected.site_code
            copy["REF_NRO"] = selected.ref_nro
            copy["NIB_PRISES"] = selected.demand_ports
        zpm_rows.append(copy)
    _write_csv(directory / "ZPM_selected.csv", zpm_rows, ZPM_FIELDS)

    selection_rows = []
    for selection in selections:
        row = asdict(selection)
        row["X"] = selection.point.x
        row["Y"] = selection.point.y
        row.pop("point")
        selection_rows.append(row)
    _write_csv(
        directory / "selection_scores.csv", selection_rows,
        ["facility_type", "zone_code", "site_code", "candidate_code", "ptech_code",
         "X", "Y", "ref_nro", "capacity", "demand_ports", "route_cost",
         "construction_cost", "total_score", "reused", "reason", "candidate_source"],
    )
    _write_csv(
        directory / "rejected_candidates.csv", [asdict(item) for item in rejections],
        ["facility_type", "zone_code", "candidate_code", "reason"],
    )
    report = {"warnings": bundle.warnings, "validation": validation}
    (directory / "validation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


