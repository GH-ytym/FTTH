"""Run through verify_review_ui.py; never attaches to the user's QGIS window."""
import os
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
plugins = Path(os.environ["APPDATA"]) / "QGIS/QGIS3/profiles/default/python/plugins" if os.environ.get("DEMO_TEST_INSTALLED") else ROOT / "qgis_plugin"
sys.path[:0] = [str(ROOT / "vendor/ftth"), str(plugins)]
from task_bridge import prepare_qgis_runtime
handles = prepare_qgis_runtime()
from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QCoreApplication, QEvent
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
wait(lambda: not p.loading_history and p.runs.findData(rid) >= 0)
assert not hasattr(p, "action_mode")
# Preferences survive panel recreation, and chat/manual choices stay independent.
chat_before, manual_before = p.auto_repair.isChecked(), p.manual_auto_repair.isChecked()
p.auto_repair.setChecked(False)
p.manual_auto_repair.setChecked(True)
other = Panel(None)
assert not other.auto_repair.isChecked() and other.manual_auto_repair.isChecked()
other.shutdown()
other.deleteLater()
p.auto_repair.setChecked(chat_before)
p.manual_auto_repair.setChecked(manual_before)
p.runs.setCurrentIndex(p.runs.findData(rid))
p.request_review()
wait(lambda: any(mid.startswith("review_report_") for mid in p.transcript.replies))
mid = next(mid for mid in p.transcript.replies if mid.startswith("review_report_"))
assert "独立审查" in p.transcript.replies[mid].current_text
p.review_report()
wait(lambda: '审查结论：' in p.report_box.toPlainText())
assert '补充审查（不启动自动重算）' in p.report_box.toPlainText()
out = ROOT / "runtime/release-0.5.0"
out.mkdir(parents=True, exist_ok=True)
p.grab().save(str(out / "review-report.png"))
p.home()
p.open_session({"id": sid, "title": "独立审查验证", "project_id": "p"})
wait(lambda: not p.loading_history and mid in p.transcript.replies)
assert len([key for key in p.transcript.replies if key == mid]) == 1
assert p.minimumSizeHint().width() <= 480
p.grab().save(str(out / "review-history.png"))
p.tabs.setCurrentIndex(3)
app.processEvents()
assert p.auto_repair.isVisible() and p.minimumSizeHint().width() <= 480
p.grab().save(str(out / "rerun-preferences.png"))
p.tabs.setCurrentIndex(0)
# Exercise live actor icons and animation without waiting on a real provider.
p.transcript.activity("live-main", "校验输入")
p.review_event({"review_id": "live-review", "status": "running", "summary": {"stage": "read_evidence", "active_evidence_ids": ["task6"]}})
live = p.transcript.activities["live-review"]
assert live["icon"].toolTip() == "独立审查 Agent"
assert "管网" in live["toggle"].text() and "Agent" not in live["toggle"].text()
assert live["ring"].aniGroup.state() == 2
assert p.transcript.activities["live-main"]["icon"].toolTip() == "主 Agent"
assert live['detail'].isHidden()
same_row = live['widget']
p.review_event({"review_id": "live-review", "status": "running", "summary": {"stage": "read_evidence", "active_evidence_ids": ["task5"]}})
assert live['widget'] is same_row and live['detail'].isHidden()
assert '光缆' in live['toggle'].text()
live['toggle'].click()
assert not live['detail'].isHidden()
live['toggle'].click()
assert live['detail'].isHidden()
angle = live["ring"].startAngle
wait(lambda: live["ring"].startAngle != angle)
p.grab().save(str(out / "agent-progress.png"))
p.review_event({"review_id": "live-review", "status": "failed", "summary": {"elapsed_seconds": 3}})
assert live["ring"].aniGroup.state() == 0 and live["ring"].isHidden()
assert "失败" in live["toggle"].text()
p.transcript.activity("live-main", "完成", "succeeded")
if os.environ.get('DEMO_TEST_WORKFLOW'):
    from qgis.PyQt.QtWidgets import QAbstractButton
    import json
    from urllib.request import Request, urlopen
    def api(path):
        request = Request(os.environ['DEMO_TEST_URL'] + path, headers={'Authorization':'Bearer ' + os.environ['DEMO_TEST_TOKEN']})
        with urlopen(request) as response:
            return json.load(response)
    before = len(api(f'/sessions/{sid}/runs'))
    p.message.setText('沿用已保存策略，执行一次完整规划。')
    p.send()
    def selectable():
        return any('仅用现有正式设施继续' == b.text() for card in p.transcript.workflow_cards.values() for b in card.findChildren(QAbstractButton))
    wait(selectable)
    p.home()
    p.open_session({'id':sid,'title':'工作流恢复验证','project_id':'p'})
    wait(lambda: not p.loading_history and selectable())
    painted = time.monotonic()
    wait(lambda: time.monotonic() - painted > .3)
    p.transcript._scroll_to_bottom()
    app.processEvents()
    p.grab().save(str(out/'workflow-choice.png'))
    buttons = [b for card in p.transcript.workflow_cards.values() for b in card.findChildren(QAbstractButton) if b.text() == '仅用现有正式设施继续']
    assert buttons[0].isVisible() and buttons[0].height() > 20
    buttons[0].click()
    buttons[0].click()  # Concurrent UI requests must not create another run.
    wait(lambda: len(api(f'/sessions/{sid}/runs')) == before + 1)
    wait(lambda: not selectable())
    assert api(f'/sessions/{sid}/history')['session']['policy']['use_recognition_roads'] is False
    assert len(api(f'/sessions/{sid}/runs')) == before + 1
    latest = api(f'/sessions/{sid}/history')['workflows'][-1]
    p.workflow_event(latest)
    stale = dict(latest, status='waiting_choice', updated=latest['updated'] - 1,
        options=[{'id':'formal_only', 'label':'仅用现有正式设施继续'}])
    p.workflow_event(stale)
    assert not selectable(), 'stale response reopened resolved choices'
    assert all(state['detail'].isHidden() for state in p.transcript.activities.values())
    p.grab().save(str(out/'workflow-submitted.png'))
    print('PASS: live workflow choices, history restore, duplicate clicks, one root run')
p.shutdown()
p.close()
p.deleteLater()
QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
app.processEvents()
app.exitQgis()
assert not errors, errors
print("PASS: review report, supplement, independent status, persisted rerun preferences, 480px layout, history recovery")
