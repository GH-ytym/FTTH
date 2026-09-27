"""Run through verify_review_ui.py; never attaches to the user's QGIS window."""
import os
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
# DEMO_TEST_PLUGINS points at an installed copy; otherwise test the source tree.
plugins = Path(os.environ.get("DEMO_TEST_PLUGINS") or ROOT / "qgis_plugin")
sys.path[:0] = [str(ROOT / "vendor/ftth"), str(plugins)]
from task_bridge import prepare_qgis_runtime
handles = prepare_qgis_runtime()
from qgis.core import QgsApplication
app = QgsApplication([], True)
app.initQgis()
from ftth_demo0.panel import Panel
errors = []
def exception(kind, value, tb):
    errors.append(str(value))
    traceback.print_exception(kind, value, tb)
sys.excepthook = exception
p = Panel(None)
p.url.setText(os.environ["DEMO_TEST_URL"])
p.token.setText(os.environ["DEMO_TEST_TOKEN"])
p.resize(480, 850)
p.show()

def wait(predicate):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.015)
    raise AssertionError("Qt review UI timed out")

sid, rid = os.environ["DEMO_TEST_SESSION"], os.environ["DEMO_TEST_RUN"]
p.open_session({"id": sid, "title": "独立审查验证", "project_id": "p"})
wait(lambda: not p.loading_history and not p.loading_data)
assert not hasattr(p, 'auto_repair') and not hasattr(p, 'runs')
p.api('POST', f'/sessions/{sid}/runs/{rid}/reviews', {'request_id':'ui-tool-chain'})
mid = 'completion_' + rid
wait(lambda: mid in p.transcript.replies and '审查完成' in p.transcript.replies[mid].current_text)
assert p.transcript.replies[mid].result_button.text() == '查看结果'
assert p.tabs.stack.currentIndex() == 0
p.transcript.replies[mid].result_button.click()
p.open_result_report(True)
wait(lambda: '审查结论：' in p.report_box.toPlainText())
assert p.tabs.stack.currentIndex() == 1
out = ROOT / 'runtime/ui-screenshots'; out.mkdir(parents=True, exist_ok=True)
p.grab().save(str(out / 'review-tools-report.png'))
p.home(); p.open_session({'id':sid,'title':'审查工具链验证','project_id':'p'})
wait(lambda: not p.loading_history and mid in p.transcript.replies)
assert hasattr(p.transcript.replies[mid], 'result_button')
p.show_data(); app.processEvents(); assert p.data_popup.isVisible()
p.data_popup.grab().save(str(out / 'data-popup.png'))
p.data_popup.hide()
assert not errors, errors
p.shutdown(); p.close(); app.processEvents()
print('PASS: real HTTP review tools, durable run result button, two-report navigation, no page jump')
