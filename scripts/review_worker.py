"""Read-only QGIS audit of serialized run artifacts, independent of algorithm validators."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.rule_checks import Checks, LAYERS


def audit(directory):
    from qgis.PyQt.QtCore import QVariant
    from qgis.core import QgsVectorLayer, QgsGeometry, QgsPointXY, QgsVariantUtils, QgsDistanceArea, QgsProject
    directory = Path(directory).resolve()
    checks = Checks()
    distance = QgsDistanceArea()
    distance.setEllipsoid("WGS84")
    layers, rows, geometries, manifest = {}, {}, {}, []
    mappings_path = directory / "field_validation.json"
    mappings = json.loads(mappings_path.read_text(encoding="utf-8")).get("field_name_mapping", {}) if mappings_path.is_file() else {}
    missing = []
    for name, expected in LAYERS.items():
        path = directory / (name + ".shp")
        for suffix in (".shp", ".shx", ".dbf", ".prj"):
            companion = path.with_suffix(suffix)
            if not companion.is_file() or companion.is_symlink():
                missing.append(companion.name)
            else:
                manifest.append({"file": companion.name, "sha256": hashlib.sha256(companion.read_bytes()).hexdigest(), "bytes": companion.stat().st_size})
        layer = QgsVectorLayer(str(path), name, "ogr")
        if not layer.isValid():
            checks.add("1.1", "fail", f"{name} 成果图层无法读取", [name])
            continue
        layers[name] = layer
        rid = "1." + str(list(LAYERS).index(name) + 2)
        checks.add(rid, "pass" if layer.geometryType() == expected else "fail", f"{name} 几何类型与文件命名")
        checks.add("3", "pass" if layer.featureCount() > 0 else "fail", f"成果 {name} 要素数量：{layer.featureCount()}；本规则不用于原始输入兜底层")
        names = [f.name() for f in layer.fields()]
        definitions = checks.pack["fields"][name]
        mapping = {}
        for spec in definitions:
            key = spec["name"]
            actual = key if key in names else key[:10] if key[:10] in names else mappings.get(name, {}).get(key)
            if actual in names:
                mapping[key] = actual
        entries, geo, invalid = [], {}, []
        for feature in layer.getFeatures():
            entry = {key: None if QgsVariantUtils.isNull(feature[actual]) else feature[actual] for key, actual in mapping.items()}
            entry["_fid"] = feature.id()
            entries.append(entry)
            geometry = feature.geometry()
            code = str(entry.get("CODE", feature.id()))
            if geometry.isNull() or geometry.isEmpty() or not geometry.isGeosValid():
                invalid.append(code)
            else:
                geo[code] = QgsGeometry(geometry)
        rows[name], geometries[name] = entries, geo
        checks.add("qgis.geometry", "fail" if invalid else "pass", f"{name} 实际几何有效性", invalid)
        checks.fields(name, entries, names, mappings.get(name))
        integer = {QVariant.Int, QVariant.UInt, QVariant.LongLong, QVariant.ULongLong}
        kinds = {key: 'integer' if layer.fields().field(actual).type() in integer else 'real' if layer.fields().field(actual).type() == QVariant.Double else 'text' if layer.fields().field(actual).type() == QVariant.String else layer.fields().field(actual).typeName() for key, actual in mapping.items()}
        checks.field_types(name, kinds)
    checks.add("1.1", "fail" if missing else "pass", "八类 Shape 成果及 shp/shx/dbf/prj 配套文件完整性", missing)
    crs = {n: l.crs().authid() or l.crs().toWkt() for n, l in layers.items()}
    same_crs = len(set(crs.values())) == 1 and all(l.crs().isValid() for l in layers.values())
    checks.add("2", "pass" if same_crs else "fail", "后台读取成果图层 CRS 一致性；桌面项目 CRS 由独立状态工具核对", crs=crs)
    if set(layers) == set(LAYERS):
        checks.relations(rows)
    else:
        for rid in ("5.1", "5.2", "5.3", "5.4", "7.1", "7.2"):
            checks.add(rid, "unavailable", "必要成果层不可读，未执行跨层检查")
    if not same_crs:
        for rid in ("6.1", "6.2", "6.3", "6.4", "6.5", "6.6"):
            checks.add(rid, "unavailable", "CRS 不一致，未直接比较不同坐标系几何")
    else:
        for rid, name in (("6.1", "ZNRO"), ("6.2", "ZPM")):
            pairs, uncertain, areas = [], [], []
            items = list(geometries.get(name, {}).items())
            for i, (code, g) in enumerate(items):
                for other, h in items[i + 1:]:
                    if not g.boundingBox().intersects(h.boundingBox()):
                        continue
                    intersection = g.intersection(h)
                    if intersection.lastError():
                        uncertain.append(code + "/" + other)
                    elif intersection.area() > 0:
                        area = intersection.area()
                        # Floating-point slivers are not evidence of a designed overlap.
                        scale = max(g.area(), h.area(), 1e-30)
                        areas.append({"objects": [code, other], "area_crs_units_squared": area, "relative_area": area / scale})
                        distance.setSourceCrs(layers[name].crs(), QgsProject.instance().transformContext())
                        areas[-1]['area_m2'] = distance.measureArea(intersection)
                        pairs.append(code + "/" + other)
            checks.add(rid, "fail" if pairs else "unavailable" if uncertain or name not in layers else "pass", f"{name} 面积重叠检查：按原表零重叠要求，共边允许；极小交叠需确认工程容差，不能推断成因", pairs or uncertain, measured_overlaps=areas)
        for rid, name, type_field, type_value, reference in (("6.3", "SITE", "TYPE", "PM", "CODE"), ("6.4", "BOITE", "TYPE", "PBO", "REF_PM"), ("6.5", "CABLE", "TYPE_CABLE", "DISTRIBUTION", "REF_PM")):
            bad, unknown = [], []
            for row in rows.get(name, []):
                if str(row.get(type_field, "")).upper() != type_value:
                    continue
                code = str(row.get("CODE"))
                g = geometries.get(name, {}).get(code)
                zone = geometries.get("ZPM", {}).get(str(row.get(reference)))
                if g is None or zone is None:
                    unknown.append(code)
                    continue
                points = list(g.vertices())
                if any(not zone.intersects(QgsGeometry.fromPointXY(QgsPointXY(p))) for p in points):
                    bad.append(code)
            checks.add(rid, "fail" if bad else "unavailable" if unknown or name not in layers or "ZPM" not in layers else "pass", "归属范围包含全部指定点/折点（边界点计入范围）", bad or unknown)
        # Workbook specifies coincidence, no tolerance. Exact coordinates are
        # reported, but a near miss cannot become a guessed engineering error.
        same, mismatched, unknown = [], [], []
        points = {**geometries.get("BOITE", {}), **geometries.get("SITE", {})}
        for row in rows.get("CABLE", []):
            code = str(row.get("CODE"))
            if row.get("ORIGINE") == row.get("EXTREMITE"):
                same.append(code)
                continue
            g = geometries.get("CABLE", {}).get(code)
            if g is None:
                unknown.append(code)
                continue
            parts = g.asMultiPolyline() if g.isMultipart() else [g.asPolyline()]
            endpoints = [QgsGeometry.fromPointXY(p) for part in parts if part for p in (part[0], part[-1])]
            for key in ("ORIGINE", "EXTREMITE"):
                point = points.get(str(row.get(key)))
                if point is None or not endpoints:
                    unknown.append(code)
                elif all(point.distance(p) > 0 for p in endpoints):
                    mismatched.append(code)
        checks.add("6.6", "fail" if same else "pass", "光缆起终点编码不同", same)
        checks.add("6.6", "pending" if mismatched or unknown else "pass", "端点重合实测；非零偏差需确认容差，PM/SITE 与全量光缆适用范围待确认", sorted(set(mismatched + unknown)))
    report = checks.report()
    report["artifact_manifest"] = manifest
    report["qgis_runtime"] = {"loaded_layers": {n: {"count": l.featureCount(), "crs": crs[n], "provider": l.providerType()} for n,l in layers.items()}, "desktop_observed": False}
    return report


def main():
    sys.path[:0] = [str(ROOT / "vendor/ftth"), str(ROOT / "vendor/ftth/engine")]
    from task_bridge import prepare_qgis_runtime
    handles = prepare_qgis_runtime()
    from qgis.core import QgsApplication
    app = QgsApplication([], False)
    app.initQgis()
    try:
        result = audit(sys.argv[1])
        Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("DEMO_RESULT=" + json.dumps({"status": "success", "counts": result["counts"]}))
    finally:
        app.exitQgis()
        del handles

if __name__ == "__main__":
    main()
