"""Exercise the compact composer and rich replies using the real QGIS Qt runtime."""
import sys
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'qgis_plugin'), str(ROOT / 'vendor/ftth')]
from task_bridge import prepare_qgis_runtime
handles = prepare_qgis_runtime()
from qgis.core import QgsApplication
from qgis.PyQt.QtWidgets import QWidget
from ftth_demo0.panel import Panel

app = QgsApplication([], False)
app.initQgis()
settings_dir = tempfile.TemporaryDirectory(prefix='ftth-composer-smoke-')
os.environ['FTTH_PLUGIN_SETTINGS'] = str(Path(settings_dir.name) / 'settings.ini')
p = Panel(None)
p.timer.stop()
p.token.clear()
p.api = lambda *args, **kwargs: None
p.resize(390, 820)
p.show()
p.recent.hide()
p.load_data_form({'IMB': {'path': 'D:/example/IMB.shp'}, 'SITE': {'path': 'D:/example/SITE.shp'}}, 'D:/example/regional_geo_context.gpkg')
p.transcript.reply('style', '**规划计算已完成，存在待复核事项。**\n\n计算与独立审查分开记录；以下是本次结果的说明。\n\n- **输入数据**：使用当前会话的数据快照。\n- **待核验项**：道路施工属性仍需确认。\n\n| 分区 | 出线容量 |\n| --- | ---: |\n| ZPM_001 | 720 |\n| ZPM_002 | 576 |\n\n字段 `CAPACITE` 表示额定容量。', 'succeeded')
for _ in range(8):
    app.processEvents()
assert not hasattr(p, 'attachment_strip')
assert p.minimumSizeHint().width() <= 390, p.minimumSizeHint().width()
assert not p.findChildren(QWidget, 'sourceCard')
assert p.message.placeholderText() == '描述需求，或询问当前方案…'
p.grab().save(str(ROOT / 'runtime/composer-preview.png'))
p.show_data()
for _ in range(4):
    app.processEvents()
p.data_popup.grab().save(str(ROOT / 'runtime/data-popup-preview.png'))
assert p.data_status.isHidden()
p.data_status.setText('保存失败：offline')
assert not p.data_status.isHidden()
p.data_status.setText('已保存')
assert p.data_status.isHidden()
p.data_popup.hide()
p.clear_source('IMB')
app.processEvents()
assert not p.sources['IMB'].text() and p.data_dirty
p.save_timer.stop()
p.message.setFocus()
app.processEvents()
assert p.message.hasFocus()
assert not p.message.layer.isVisible()
p.data_dirty = False
p.shutdown()
p.close()
app.processEvents()
app.exitQgis()
settings_dir.cleanup()
print('PASS: 390px composer without cards, minimal data popup, save failure feedback, Markdown and input focus')
