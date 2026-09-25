"""Qt HTTP integration against the isolated backend created by verify_conversations.py."""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "vendor/ftth"), str(ROOT / "qgis_plugin")]
from task_bridge import prepare_qgis_runtime
handles = prepare_qgis_runtime()
from qgis.core import QgsApplication
app = QgsApplication([], True)
app.initQgis()
from ftth_demo0.panel import Panel
from ftth_demo0.fluent_ui import MarkdownView
p = Panel(None)
p.url.setText(os.environ["DEMO_TEST_URL"])
p.token.setText(os.environ["DEMO_TEST_TOKEN"])
p.resize(480, 780)
p.show()

def wait_for(predicate):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.025)
    raise AssertionError("UI condition timed out")

assert not p.session
assert p.message.isVisible()
p.message.setText("检查项目数据")
p.send()
wait_for(lambda: p.session and p.cursor > 0 and not p.message.text())
first = p.session
wait_for(lambda: any("有3个需求点" in w.toPlainText() for w in p.transcript.findChildren(MarkdownView)))
assert any(w.toPlainText() == "检查项目数据" for w in p.transcript.findChildren(MarkdownView))
wait_for(lambda: bool(p.sessions_cache))
entry = next(s for s in p.sessions_cache if s["id"] == first)
assert entry["title"] == "检查项目数据"
assert p.back_button.isVisible()
p.home()
assert not p.session
assert p.recent.isVisible() and p.message.isVisible()
p.open_session(entry)
wait_for(lambda: not p.loading_history)
assert any(w.toPlainText() == "检查项目数据" for w in p.transcript.findChildren(MarkdownView))
assert p.transcript.activities
activity = next(iter(p.transcript.activities.values()))
activity["toggle"].click()
wait_for(lambda: activity["detail"].isVisible())
assert "inspect_project" in activity["detail"].text()
p.home()
p.message.setText("新的规划问题")
p.send()
wait_for(lambda: p.session and p.session != first and not p.message.text())
assert not any(w.toPlainText() == "检查项目数据" for w in p.transcript.findChildren(MarkdownView))
p.shutdown()
print("PASS: first-message session creation, title, user/assistant history, activity expansion, session isolation, persistent composer")
