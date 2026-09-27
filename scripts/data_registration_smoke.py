"""Real Qt/QGIS data-form regression; run with a throwaway FTTH_PLUGIN_SETTINGS.

Arguments: directory containing IMB/SITE/PTECH/INFRASTRUCTURE.shp, roads GPKG.
No live backend requests or planning jobs are made.
"""
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
def api(method, path, payload=None, callback=None, quiet=False, on_error=None):
    calls.append((method,path,payload,callback))
p.api=api
p.session='test-session'
assert not hasattr(p, 'import_config') and not hasattr(p, 'register')
for name in p.sources:
    p.sources[name].setText(str(Path(source) / (name + '.shp')))
p.recognition.setText(roads)
p.message.setText('开始完整规划');p.send()
assert len(calls)==1 and calls[0][1].endswith('/data')
payload=calls[0][2]
assert payload['recognition_path']==roads and set(payload['sources'])=={'IMB','SITE','PTECH','INFRASTRUCTURE'}
assert 'config_path' not in payload
calls[0][3]({'revision':1})
assert len(calls)==2 and calls[1][1].endswith('/messages')
p.shutdown();p.close();app.processEvents()
print('PASS: choose raw sources -> autosave draft -> send after acknowledgement, without config JSON')
