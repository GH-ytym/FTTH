import sys
import os
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "qgis_plugin"), str(ROOT / "vendor/ftth"), str(ROOT / "vendor/ftth/engine")]
from task_bridge import prepare_qgis_runtime
handles = prepare_qgis_runtime()
from qgis.core import QgsApplication
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtTest import QTest
from ftth_demo0.panel import Panel
app = QgsApplication([], False); app.initQgis()
settings_dir = tempfile.TemporaryDirectory(prefix='ftth-popup-smoke-')
os.environ['FTTH_PLUGIN_SETTINGS'] = str(Path(settings_dir.name) / 'settings.ini')
p = Panel(None); p.timer.stop(); p.token.clear()
p.resize(390, 800); p.show(); app.processEvents()
p.show_data(); app.processEvents()
assert p.data_popup.isVisible()
QTest.keyClick(p.data_popup, Qt.Key_Escape); app.processEvents()
assert not p.data_popup.isVisible()
p.refresh_sessions([{"id":str(i),"title":"对话"+str(i),"created":1} for i in range(8)])
p.all_sessions(); app.processEvents(); assert p.history_popup.isVisible()
p.history_popup.search.setText('对话3'); app.processEvents(); assert p.history_popup.items.count()==1
p.history_popup.hide()
p.transcript.reply('completion_r', '计算完成 · 审查完成 · 有待复核事项', 'succeeded')
p.add_result_link('completion_r','run','r')
assert p.transcript.replies['completion_r'].result_button.text() == '查看结果'
p.session='s'; requests=[]
def api(method, path, payload=None, callback=None, quiet=False, on_error=None):
    requests.append((path,payload,callback,on_error))
p.api=api
p.sources['IMB'].setText('A.shp'); p.flush_data()
p.sources['IMB'].setText('B.shp'); p.flush_data()
assert len(requests)==1
requests[0][2]({'revision':1})
assert len(requests)==2 and requests[1][1]['sources']['IMB']['path']=='B.shp'
requests[1][2]({'revision':2}); assert not p.data_dirty
p.sources['IMB'].setText('C.shp'); sent=[]; p.flush_data(lambda:sent.append(True))
requests[2][3](ValueError('offline')); assert p.data_dirty and not sent
p.data_dirty=False
p.shutdown(); p.close(); app.processEvents(); app.exitQgis()
settings_dir.cleanup()
print('Qt popup, Escape, history search, result link and slow-save/failure checks passed')
