"""Real Qt acceptance for typography, progress disclosure and code objects."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'qgis_plugin'), str(ROOT / 'vendor/ftth')]
from task_bridge import prepare_qgis_runtime
handles = prepare_qgis_runtime()
from qgis.core import QgsApplication
from qgis.PyQt.QtCore import Qt, QPoint
from qgis.PyQt.QtGui import QTextCursor
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QLabel, QApplication
from ftth_demo0.panel import Panel
from ftth_demo0.presentation import CODE_OBJECT

app = QgsApplication([], False)
app.initQgis()
settings = tempfile.TemporaryDirectory(prefix='ftth-style-')
os.environ['FTTH_PLUGIN_SETTINGS'] = str(Path(settings.name) / 'settings.ini')
p = Panel(None)
p.timer.stop()
p.token.clear()
p.api = lambda *args, **kwargs: None
p.recent.hide()
p.resize(760, 960)
p.show()
text = ('我会把滚动条改成圆角滑块：默认 `#EDEDED`，悬浮和按下时 `#585A5B`；'
        '同时将 Fluent 强调色改为 `#3A83F7`。\n\n'
        '**计算与审查分别记录。** 输入数据保留快照，检查结果绑定当前批次；'
        '审查暂未完成时，不会要求重新运行算法。\n\n'
        '- **结果明确**：正文自然换行，辅助信息弱化显示。\n'
        '- **格式灵活**：需要比较时使用表格，解释因果时使用完整段落。\n\n'
        '| 分区 | PBO 额定容量 | 出线容量 |\n| --- | ---: | ---: |\n'
        '| ZPM_001 | 672 | 720 |\n| ZPM_002 | 648 | 576 |\n\n'
        '字段 `CAPACITE` 表示额定容量。\n\n'
        '```python\ncapacity = 144\nprint(capacity)\n```\n\n'
        '[Qt 文档](https://doc.qt.io/)')
p.transcript.activity('main', '理解当前请求', 'running')
long = '读取本批次报告 F:/PROJECTS/py/qgis/demo0/runtime/runs/run_example/final_layers/field_validation.json 并核对原始规则'
p.transcript.activity('main', long, 'running')
p.transcript.activity('main', '核对完成', 'succeeded', 73)
state = p.transcript.activities['main']
app.processEvents()
state['toggle'].click()
app.processEvents()
assert state['detail'].isVisible()
assert '1 分 13 秒' in state['toggle'].text()
row, detail = state['detail'].rows[1]
QTest.mouseClick(row, Qt.LeftButton, pos=QPoint(10, 10))
assert not detail.isHidden()
p.transcript.activity('main', '迟到消息', 'running')
assert state['status'] == 'succeeded' and not detail.isHidden()
QTest.mouseClick(row, Qt.LeftButton, pos=QPoint(10, 10))
assert detail.isHidden()
p.transcript.reply('reply', text[:90], 'running')
p.transcript.reply('reply', text, 'succeeded', 73)
card = p.transcript.replies['reply']
view = card.view
objects = []
block = view.document().begin()
while block.isValid():
    if os.environ.get('FTTH_STYLE_DEBUG'):
        print(repr(block.text()), block.blockFormat().properties(), block.charFormat().properties())
    iterator = block.begin()
    while not iterator.atEnd():
        part = iterator.fragment()
        if part.charFormat().objectType() == CODE_OBJECT:
            objects.append(part.position())
        iterator += 1
    block = block.next()
assert len(objects) == 4, objects
cursor = view.textCursor()
cursor.select(QTextCursor.Document)
view.setTextCursor(cursor)
copied = view.createMimeDataFromSelection().text()
assert '#EDEDED' in copied and '#3A83F7' in copied and 'CAPACITE' in copied
assert '\ufffc' not in copied and 'print(capacity)' in copied
cursor.clearSelection()
view.setTextCursor(cursor)
card.copy_button.click()
assert QApplication.clipboard().text() == text
assert not card.timer.isActive()
for width in (760, 390):
    p.resize(width, 960)
    for _ in range(12):
        app.processEvents()
    p.transcript.verticalScrollBar().setValue(0)
    app.processEvents()
    assert p.minimumSizeHint().width() <= width
    if width == 390:
        assert len(QLabel.text(row)) < len(row.text())  # long activity is elided, not wrapped
    p.grab().save(str(ROOT / f'runtime/output-style-{width}.png'))
assert view.loadResource(2, None) is None
state['toggle'].click()
assert state['detail'].isHidden()
state['toggle'].click()
assert not state['detail'].isHidden()
p.shutdown()
p.close()
app.processEvents()
app.exitQgis()
settings.cleanup()
print('PASS: 390/760px rendering, paragraphs/list/table/code/link, color chips, selection/copy, streaming, activity elision/expansion and stale-event isolation')
