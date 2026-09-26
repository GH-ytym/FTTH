"""Real Qt widget acceptance; no interaction with the user's open QGIS project."""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
plugin_dir = Path(os.environ["APPDATA"]) / "QGIS/QGIS3/profiles/default/python/plugins" if os.environ.get("DEMO_TEST_INSTALLED") else ROOT / "qgis_plugin"
sys.path[:0] = [str(ROOT / "vendor/ftth"), str(plugin_dir)]
from task_bridge import prepare_qgis_runtime
handles = prepare_qgis_runtime()
from qgis.core import QgsApplication
from qgis.PyQt.QtWidgets import QApplication
from qgis.PyQt.QtCore import QCoreApplication, QEvent
app = QgsApplication([], True)
app.initQgis()
from ftth_demo0.panel import Panel
import traceback
uncaught = []
def report_exception(kind, value, tb):
    uncaught.append(str(value))
    traceback.print_exception(kind, value, tb, file=sys.__stderr__)
    sys.__stderr__.flush()
sys.excepthook = report_exception
from qgis.PyQt.QtCore import qInstallMessageHandler
qInstallMessageHandler(lambda kind, context, message: print("QT:", message, file=sys.__stderr__, flush=True))

p = Panel(None)
p.url.setText(os.environ["DEMO_TEST_URL"])
p.token.setText(os.environ["DEMO_TEST_TOKEN"])
p.resize(480, 780)
p.show()
out = ROOT / "runtime/release-0.5.0"
out.mkdir(parents=True, exist_ok=True)

def wait_for(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
        if predicate():
            return
        time.sleep(.015)
    raise AssertionError("UI condition timed out")

p.message.setText("请检查规划数据，先给我建议。")
assert not hasattr(p, "action_mode")
p.send()
wait_for(lambda: p.session and p.transcript.replies)
sid = p.session
assert not hasattr(p, "action_mode")  # First message needs no execution switch.
mid = next(iter(p.transcript.replies))
card = p.transcript.replies[mid]
wait_for(lambda: card.current_text and card.status == "running")
assert card.timer.isActive()
first = card.current_text
wait_for(lambda: len(card.current_text) > len(first) + 20 and card.status == "running")
p.grab().save(str(out / "streaming.png"))
badge = card.badge.text()
wait_for(lambda: card.badge.text() != badge)

# Re-enter the active conversation: history must restore the partial message.
p.home()
p.open_session({"id": sid, "title": "数据检查 · 流式验证"})
wait_for(lambda: not p.loading_history and mid in p.transcript.replies)
card = p.transcript.replies[mid]
assert card.current_text and card.status == "running"
wait_for(lambda: card.status == "succeeded")
assert not card.timer.isActive()
assert "已经收到的正文会恢复" in card.current_text
assert "PRIVATE_REASONING_SENTINEL" not in card.current_text
assert len(p.transcript.replies) == 1
card.copy_button.click()
assert QApplication.clipboard().text() == card.current_text
assert p.minimumSizeHint().width() <= 480
p.grab().save(str(out / "complete.png"))
print("Verified streaming, restore, final card and clipboard", flush=True)

# Use the actual Panel callback while simulating a delayed registration response.
p.timer.stop()
original_api = p.api
calls = []
p.api = lambda *args, **kwargs: calls.append((args, kwargs))
p.session, p.session_project = "original", None
callback = p.registration_callback()
p.session, p.session_project = "other", None
p.view_generation += 1
callback({"id": "registered", "name": "Original dataset"})
assert calls[-1][0][1] == "/sessions/original/project"
assert p.session == "other" and p.session_project is None
print("Verified registration isolation", flush=True)
p.api = original_api
p.shutdown()
print("Shut down panel networking", flush=True)
p.message.clearFocus()
p.close()
print("Closed panel", flush=True)
p.deleteLater()
print("Queued panel deletion", flush=True)
from qgis.PyQt.QtCore import QCoreApplication, QEvent
QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
app.processEvents()
print("Deleted panel widgets", flush=True)
app.exitQgis()
assert not uncaught, uncaught
print("PASS: actual SSE text growth, animated status, first-message natural-language request, live history recovery, one final card, copy, 480px layout, original-session registration")
