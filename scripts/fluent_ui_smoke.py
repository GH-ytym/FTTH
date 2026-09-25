"""Run with QGIS Python; render and exercise the Fluent dock offscreen."""
import sys
import time
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
plugin_dir = Path(os.environ["APPDATA"]) / "QGIS/QGIS3/profiles/default/python/plugins" if "--installed" in sys.argv else ROOT / "qgis_plugin"
sys.path[:0] = [str(ROOT / "vendor/ftth"), str(plugin_dir)]
from task_bridge import prepare_qgis_runtime
handles = prepare_qgis_runtime()
from qgis.core import QgsApplication
from qgis.PyQt.QtWidgets import QCheckBox, QWidget

app = QgsApplication([], True)
app.initQgis()
from ftth_demo0.panel import Panel
import ftth_demo0.panel
assert Path(ftth_demo0.panel.__file__).parent.parent == plugin_dir

p = Panel(None)
p.timer.stop()
p.resize(540, 940)
p.show()

def settle():
    for _ in range(10):
        app.processEvents()
        time.sleep(.025)

settle()
p.refresh_sessions([{"id":"s1","title":"检查 FTTH 规划数据"}, {"id":"s2","title":"调整容量并比较方案"}, {"id":"s3","title":"导出光网规划成果"}])
settle()
p.grab().save(str(ROOT / "runtime/fluent-empty.png"))
p.recent.hide()
p.back_button.show()
p.title_button.setText("检查 FTTH 规划数据")
p.transcript.append("你：使用正式基础设施完成规划，并展示结果。")
p.transcript.activity("preview", "检查项目数据\n校验输入\n提交规划", "succeeded", 24)
p.transcript.append("助手：## 规划已完成\n\n本批次已生成 **2 个 PM 分区**，QGIS 已加载成果。\n\n| 成果 | 数量 |\n|---|---|\n| 楼栋 IMB | 51 |\n| 光缆 CABLE | 113 |\n\n你可以继续调整参数、比较方案，或导出交付文件。")
p.transcript.append("QGIS：已展示 · 8 个图层")
settle()
p.grab().save(str(ROOT / "runtime/fluent-chat.png"))
assert p.grab().toImage().pixelColor(3, 3).name() == "#ffffff"
composer = p.findChild(QWidget, "composer")
assert composer.grab().toImage().pixelColor(composer.width() // 2, composer.height() - 10).name() == "#ffffff"
assert p.message.grab().toImage().pixelColor(30, 45).name() == "#ffffff"
assert p.send_button.grab().toImage().pixelColor(5, 5).name() == "#ffffff"
from qgis.PyQt.QtCore import QPoint, Qt
from qgis.PyQt.QtTest import QTest
QTest.mouseMove(p.send_button, QPoint(12, 12))
settle()
assert p.send_button.grab().toImage().pixelColor(5, 5).name() == "#f0f2f4"
QTest.mousePress(p.send_button, Qt.LeftButton, pos=QPoint(12, 12))
settle()
assert p.send_button.grab().toImage().pixelColor(5, 5).name() == "#e3e7eb"
QTest.mouseRelease(p.send_button, Qt.LeftButton, pos=QPoint(12, 12))
QTest.mouseMove(p, QPoint(2, 2))
settle()
assert p.send_button.grab().toImage().pixelColor(5, 5).name() == "#ffffff"
p.message.setFocus()
settle()
assert p.message.hasFocus()
assert not p.message.layer.isVisible()
focused = p.message.grab().toImage()
assert focused.pixelColor(focused.width() // 2, focused.height() - 2).name() == "#ffffff"
bubble = p.transcript.findChild(QWidget, "userBubble")
assert bubble.grab().toImage().pixelColor(20, 8).name() == "#e8f3fe"
p.transcript.activity("live-test", "任务六 · 管线路由")
settle()
assert p.transcript.activities["live-test"]["detail"].isVisible()
p.transcript.activity("live-test", "算法完成", "succeeded", 12.3)
assert p.transcript.activities["live-test"]["detail"].isHidden()
assert not p.transcript.activities["live-test"]["timer"].isActive()
p.transcript.activity("live-test", "迟到的工具事件")
assert p.transcript.activities["live-test"]["status"] == "succeeded"
assert p.transcript.activities["live-test"]["elapsed"] == 12.3
p.transcript.activities["live-test"]["toggle"].click()
assert not p.transcript.activities["live-test"]["detail"].isHidden()
for i, name in [(1, "data"), (2, "runs"), (3, "settings")]:
    p.tabs.setCurrentIndex(i)
    settle()
    p.grab().save(str(ROOT / ("runtime/fluent-" + name + ".png")))
    assert p.back_button.isVisible()
    before = p.session
    p.back_button.click()
    assert p.tabs.stack.currentIndex() == 0 and p.session == before
    p.tabs.setCurrentIndex(i)
assert isinstance(p.controls["ga_refine"], QCheckBox)
assert p.scope.currentData() == "full"
assert len(p.controls) == 16
assert p.message.isVisible() is False  # Settings page is currently selected.
p.tabs.setCurrentIndex(0)
settle()
assert p.message.isVisible()
p.message.setText("测试\n多行输入")
assert p.message.text() == "测试\n多行输入"
calls = []
p.api = lambda *args, **kwargs: calls.append((args, kwargs))
p.session = "test-session"
p.send()
assert calls[-1][0][2]["content"] == "测试\n多行输入"
p.controls["use_formal_infrastructure"].setChecked(True)
p.manual(False)
assert calls[-1][0][2]["parameter_overrides"]["use_formal_infrastructure"] is True
p.tabs.setCurrentIndex(0)
p.resize(480, 780)
settle()
p.grab().save(str(ROOT / "runtime/fluent-narrow.png"))
assert p.minimumSizeHint().width() <= 480, p.minimumSizeHint().width()
p.shutdown()
p.message.clearFocus()
p.close()
p.deleteLater()
from qgis.PyQt.QtCore import QCoreApplication, QEvent
QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
app.processEvents()
app.exitQgis()
print("PASS: Fluent pages, Markdown, composer, parameter controls, send/validate and 480px layout")
