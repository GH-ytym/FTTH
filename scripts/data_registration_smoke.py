"""Real Qt/QGIS data-form regression; run with a throwaway FTTH_PLUGIN_SETTINGS.

Arguments: directory containing IMB/SITE/PTECH/INFRASTRUCTURE.shp, roads GPKG.
No live backend requests or planning jobs are made.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "vendor/ftth"), str(ROOT / "qgis_plugin")]
from task_bridge import prepare_qgis_runtime

handles = prepare_qgis_runtime()
from qgis.core import QgsApplication
from ftth_demo0.panel import Panel

app = QgsApplication([], True)
app.initQgis()
source, roads = sys.argv[1:3]
p = Panel(None)
p.timer.stop()
calls = []
p.api = lambda method, path, payload=None, callback=None, quiet=False: calls.append((method, path, payload))
def no_local_config():
    raise AssertionError("Registration must not create a plugin-side configuration")
p.workspace = no_local_config
assert not hasattr(p, "import_config")
for name in p.sources:
    p.sources[name].setText(str(Path(source) / (name + ".shp")))
assert p.data_dirty
p.recognition.setText(roads)
p.message.setText("开始完整规划")
p.send()
assert not calls, "Unsaved inputs must not submit against the old project"
p.register()
assert len(calls) == 1 and calls[0][1] == "/projects", p.data_status.text()
payload = calls[0][2]
assert payload["recognition_path"] == roads
assert "config_path" not in payload
assert set(payload["sources"]) == {"IMB", "SITE", "PTECH", "INFRASTRUCTURE"}
print("PAYLOAD=" + json.dumps(payload, ensure_ascii=False))
print("PASS: select individual files -> prevent stale submit -> register paths without local JSON")
p.deleteLater()
app.processEvents()
