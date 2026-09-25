"""Small QGIS-only adapter; the backend never imports this module."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor/ftth"
sys.path[:0] = [str(VENDOR), str(VENDOR / "engine")]
from task_bridge import prepare_qgis_runtime


def inspect(config_path):
    from run_task1 import run
    from task2_topology_adapter import build_task2_topology_from_sources
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    result = run(config_path, validate_only=False)
    topology = json.loads(Path(result["task2_topology_json"]).read_text(encoding="utf-8"))
    nodes = topology.get("nodes", [])
    imbs = [n for n in nodes if n.get("source_layer") == "IMB"]
    return {"status": "success", "quality": result["report"], "meta": topology["meta"],
            "layers": [{"name": l["name"], "crs": l.get("crs"), "fields": [f["name"] for f in l.get("fields", [])],
                        "count": sum(n.get("source_layer") == l["name"] for n in nodes) if l["name"] != "INFRASTRUCTURE" else len(topology.get("edges", []))} for l in config["vector_layers"]],
            "nro_count": sum(n.get("source_layer") == "SITE" and str(n.get("original_type", "")).upper() == "NRO" for n in nodes),
            "oversized_imbs": [n.get("code") for n in imbs if float(n.get("ports", n.get("demand", 0))) > 48],
            "imb_count": len(imbs), "infrastructure_count": len(topology.get("edges", []))}


def main():
    handles = prepare_qgis_runtime()
    from qgis.core import QgsApplication, QgsProject
    app = QgsApplication([], False)
    app.initQgis()
    try:
        mode, path = sys.argv[1:3]
        if mode == "inspect":
            result = inspect(Path(path))
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
