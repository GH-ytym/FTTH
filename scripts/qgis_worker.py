"""Small QGIS-only adapter; the backend never imports this module."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor/ftth"
sys.path[:0] = [str(VENDOR), str(VENDOR / "engine")]
from task_bridge import prepare_qgis_runtime


def inspect(config_path, recognition_path=None):
    from run_task1 import run
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    result = run(config_path, validate_only=False)
    topology = json.loads(Path(result["task2_topology_json"]).read_text(encoding="utf-8"))
    nodes = topology.get("nodes", [])
    imbs = [n for n in nodes if n.get("source_layer") == "IMB"]
    roads = None
    if recognition_path:
        from qgis.core import QgsVectorLayer, QgsWkbTypes
        layer = QgsVectorLayer(str(recognition_path) + "|layername=GEO_ROAD", "GEO_ROAD", "ogr")
        if not layer.isValid():
            raise ValueError("道路 GPKG 读取失败：缺少或无法读取 GEO_ROAD 图层")
        if layer.geometryType() != QgsWkbTypes.LineGeometry or not layer.crs().isValid():
            raise ValueError("道路 GPKG 的 GEO_ROAD 必须为有有效坐标系的线图层")
        count = sum(1 for f in layer.getFeatures() if f.hasGeometry() and not f.geometry().isEmpty())
        if not count:
            raise ValueError("道路 GPKG 的 GEO_ROAD 没有非空道路几何")
        roads = {"count": count, "crs": layer.crs().authid()}
    from preflight_checks import inspect_inputs
    preflight = inspect_inputs(config, recognition_path)
    return {"status": "success", "rule_preflight": preflight, "recognition_roads": roads, "quality": result["report"], "meta": topology["meta"],
            "layers": [{"name": l["name"], "crs": l.get("crs"), "fields": [f["name"] for f in l.get("fields", [])],
                        "count": sum(n.get("source_layer") == l["name"] for n in nodes) if l["name"] != "INFRASTRUCTURE" else len(topology.get("edges", []))} for l in config["vector_layers"]],
            "nro_count": sum(n.get("source_layer") == "SITE" and str(n.get("original_type", "")).upper() == "NRO" for n in nodes),
            "oversized_imbs": [n.get("code") for n in imbs if float(n.get("ports", n.get("demand", 0))) > 48],
            "imb_count": len(imbs), "infrastructure_count": len(topology.get("edges", []))}


def main():
    _handles = prepare_qgis_runtime()  # DLL handles must stay alive until exit.
    from qgis.core import QgsApplication, QgsProject
    app = QgsApplication([], False)
    app.initQgis()
    try:
        mode, path = sys.argv[1:3]
        if mode == "inspect":
            result = inspect(Path(path), sys.argv[3] if len(sys.argv) > 3 else None)
        elif mode == "preflight":
            from preflight_checks import inspect_inputs
            result = inspect_inputs(json.loads(Path(path).read_text(encoding='utf-8-sig')), sys.argv[3] if len(sys.argv) > 3 else None)
        elif mode == "build-config":
            from input_config_builder import build_task1_config
            spec = json.loads(Path(path).read_text(encoding="utf-8"))
            result = build_task1_config(spec.get("template", VENDOR / "task1_config.json"), spec["output"], spec["sources"])
        else:
            raise ValueError("Unknown worker mode")
        print("DEMO_RESULT=" + json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    except Exception as exc:
        print("DEMO_RESULT=" + json.dumps({"status": "failed", "errors": [str(exc)]}, ensure_ascii=False), flush=True)
        return 1
    finally:
        QgsProject.instance().clear()
        app.exitQgis()


if __name__ == "__main__":
    raise SystemExit(main())
