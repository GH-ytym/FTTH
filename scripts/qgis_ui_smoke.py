"""Exercise the actual plugin widgets, map layer loader and renderer with PyQGIS."""
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "vendor/ftth"), str(ROOT / "qgis_plugin")]
from task_bridge import prepare_qgis_runtime
handles = prepare_qgis_runtime()
from qgis.core import QgsApplication, QgsProject, QgsCoordinateReferenceSystem, QgsMapSettings, QgsMapRendererParallelJob
from qgis.gui import QgsMapCanvas
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtGui import QFontDatabase, QFont
from qgis.PyQt.QtWidgets import QMainWindow


def main():
    app = QgsApplication([], True)
    app.initQgis()
    for font in ("msyh.ttc", "arial.ttf"):
        font_id = QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + font)
    app.setFont(QFont("Microsoft YaHei", 9))
    from ftth_demo0.panel import Panel
    from ftth_demo0.display import show_result
    project = QgsProject.instance()
    project.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    canvas = QgsMapCanvas()
    window = QMainWindow()
    class Iface:
        def mapCanvas(self):
            return canvas
        def mainWindow(self):
            return window
    iface = Iface()
    panel = Panel(iface)
    panel.timer.stop()
    panel.resize(500, 900)
    panel.show()
    app.processEvents()
    panel.grab().save(str(ROOT / "runtime/sidebar.png"))
    run = json.loads((ROOT / "runtime/smoke_planning.json").read_text(encoding="utf-8"))
    command = {"run_id": run["id"], "result": run["result"], "visible_layers": [], "zoom": True}
    details = show_result(iface, command)
    assert len(project.mapLayers()) == 8
    assert details["layers"]["IMB"] == 51
    assert details["layers"]["CABLE"] == 113
    # Duplicate delivery must replace its own group, not duplicate layers.
    show_result(iface, command)
    assert len(project.mapLayers()) == 8
    command["visible_layers"] = ["INFRASTRUCTURE"]
    show_result(iface, command)
    group = project.layerTreeRoot().findGroup("FTTH demo0 " + run["id"])
    assert [n.name() for n in group.findLayers() if n.itemVisibilityChecked()] == ["INFRASTRUCTURE"]
    command["visible_layers"] = []
    show_result(iface, command)
    group = project.layerTreeRoot().findGroup("FTTH demo0 " + run["id"])
    settings = QgsMapSettings()
    settings.setLayers([n.layer() for n in group.findLayers() if n.itemVisibilityChecked()])
    settings.setDestinationCrs(project.crs())
    settings.setExtent(canvas.extent())
    settings.setOutputSize(QSize(1400, 950))
    settings.setBackgroundColor(QColor("white"))
    job = QgsMapRendererParallelJob(settings)
    job.start()
    job.waitForFinished()
    assert job.renderedImage().save(str(ROOT / "runtime/qgis_map.png"))
    project.setFileName(str(ROOT / "runtime/51imb_demo.qgz"))
    assert project.write()
    if "--http" in sys.argv:
        session = json.loads((ROOT / "runtime/smoke_session.json").read_text(encoding="utf-8"))["session_id"]
        token = (ROOT / "runtime/connection.token").read_text(encoding="utf-8").strip()
        def http(path, payload=None):
            req = urllib.request.Request("http://127.0.0.1:8765" + path, data=json.dumps(payload).encode() if payload is not None else None,
                headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.load(response)
        panel.session = session
        panel.token.setText(token)
        pending = http(f"/sessions/{session}/display", {"run_id": run["id"]})
        deadline = time.monotonic() + 20
        receipt = None
        while time.monotonic() < deadline:
            panel.poll()
            app.processEvents()
            time.sleep(0.05)
            events = http(f"/sessions/{session}/events?after=0")
            receipt = next((e for e in events if e.get("command_id") == pending["command_id"]), None)
            if receipt:
                break
        assert receipt and receipt["status"] == "succeeded", receipt
        details["http_display_receipt"] = receipt
        panel.grab().save(str(ROOT / "runtime/sidebar_connected.png"))
    (ROOT / "runtime/qgis_ui_report.json").write_text(json.dumps({"status": "PASS", **details}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(details), flush=True)
    panel.shutdown()
    panel.close()
    project.clear()
    del job, panel, canvas, window
    app.processEvents()
    app.exitQgis()


if __name__ == "__main__":
    main()
