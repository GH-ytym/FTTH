import json
import hashlib
import os
import uuid
import time
from pathlib import Path

from qgis.PyQt.QtCore import QTimer, QUrl, QByteArray, QSettings
from qgis.PyQt.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from qgis.PyQt.QtGui import QFont, QFontDatabase
from qgis.PyQt.QtWidgets import QWidget, QFileDialog
from qgis.core import QgsVectorFileWriter, QgsProject
BUSINESS_LAYERS = ("IMB", "SITE", "PTECH", "INFRASTRUCTURE")
from .display import show_result
from .fluent_ui import build_ui, button, hover_feedback
from .connection import backend_problem

TOOL_LABELS = {"model_response": "理解请求与组织回复", "inspect_project": "检查项目数据", "validate_inputs": "校验输入", "submit_planning": "提交规划", "get_run": "读取运行结果", "compare_runs": "比较方案", "get_reviews": "读取审查结果", "show_result": "展示地图", "export_result": "导出成果"}


def run_activity_text(status):
    return {"queued": "等待计算", "running": "执行算法", "succeeded": "计算完成", "failed": "计算失败", "cancelled": "计算已取消", "interrupted": "计算已中断"}.get(status, "更新计算状态")


def tool_activity_text(event):
    suffix = {"running": "进行中", "succeeded": "完成", "failed": "失败"}.get(event["status"], event["status"])
    return TOOL_LABELS.get(event["name"], "调用工具") + " · " + suffix


def request_id():
    return uuid.uuid4().hex


class Panel(QWidget):
    def __init__(self, iface):
        super().__init__()
        # Standalone/offscreen Qt may not discover the Windows font collection.
        font_file = Path("C:/Windows/Fonts/msyh.ttc")
        if font_file.exists():
            font_id = QFontDatabase.addApplicationFont(str(font_file))
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                self.setFont(QFont(families[0], 9))
        self.iface = iface
        # Verification runs use a throwaway file, never the user's saved preferences.
        isolated = os.environ.get("FTTH_PLUGIN_SETTINGS")
        self.settings = QSettings(isolated, QSettings.IniFormat) if isolated else QSettings("FTTH", "demo0")
        self.session = ""
        self.view_generation = 0
        self.project_id = None
        self.session_project = None
        self.creating_session = False
        self.last_sessions_refresh = 0
        self.last_health_check = 0
        self.backend_compatible = None
        self.backend_problem = ""
        self.run_entries = None
        self.sessions_cache = []
        self.show_all_sessions = False
        self.loading_history = False
        self.cursor = 0
        self.busy = set()
        self.displayed = {}
        self.net = QNetworkAccessManager(self)
        self.replies = set()
        self.data_dirty = False
        self.data_revision = 0
        self.server_revision = 0
        self.loading_data = False
        self.saving_data = False
        self.after_save = []
        self.result_target = None
        self.report_generation = 0
        self.review_versions = {}
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(500)
        self.save_timer.timeout.connect(self.flush_data)
        build_ui(self, BUSINESS_LAYERS)
        for widget in [*self.sources.values(), *self.sublayers.values(), self.recognition]:
            widget.textChanged.connect(self.data_changed)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(250)
        self.last_runs_refresh = 0
        self.run_cache = {}

    def home(self):
        if self.data_dirty or self.saving_data:
            self.flush_data(self.home)
            return
        self.load_data_form({}, "")
        self.server_revision = 0
        self.view_generation += 1
        self.run_entries = None
        self.loading_history = False
        self.session = ""
        self.session_project = None
        self.cursor = 0
        self.transcript.clear()
        self.title_button.setText("Chats")
        self.back_button.hide()
        self.recent.show()
        self.tabs.setCurrentIndex(0)
        self.last_sessions_refresh = 0
        self.poll()

    def back_to_chat(self):
        if self.tabs.stack.currentIndex() != 0:
            self.tabs.setCurrentIndex(0)
        else:
            self.home()

    def refresh_sessions(self, items):
        unchanged = self.sessions_cache == items
        self.sessions_cache = items
        self.history_popup.refresh()
        if unchanged:
            return
        current = next((s for s in items if s["id"] == self.session), None)
        if current:
            self.title_button.setText(current.get("title", "新对话"))
            self.title_button.setToolTip(current.get("title", "新对话"))
        while self.recent_layout.count():
            item = self.recent_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        from .fluent_ui import TransparentPushButton
        for session in items[:3]:
            b = TransparentPushButton(session.get("title", "新对话"), self.recent)
            b.setStyleSheet("QPushButton {text-align:left;border:none;background:transparent;color:#56616f;padding:6px;} QPushButton:hover, QPushButton:pressed {background:#FFFFFF;border-radius:6px;}")
            hover_feedback(b)
            b.clicked.connect(lambda checked=False, s=session: self.open_session(s))
            self.recent_layout.addWidget(b)
        if len(items) > 3:
            b = TransparentPushButton(f"查看全部 ({len(items)})", self.recent)
            hover_feedback(b)
            self.all_sessions_button = b
            b.clicked.connect(self.all_sessions)
            self.recent_layout.addWidget(b)

    def all_sessions(self):
        self.history_popup.refresh()
        self.history_popup.present(self.all_sessions_button)

    def open_session(self, session):
        if self.data_dirty or self.saving_data:
            self.flush_data(lambda: self.open_session(session))
            return
        self.view_generation += 1
        self.run_entries = None
        generation = self.view_generation
        self.session = session["id"]
        self.session_project = session.get("project_id")
        self.loading_data = True
        self.data_status.setText("正在读取数据草稿…")
        self.project_id = self.session_project
        self.title_button.setText(session.get("title", "新对话"))
        self.title_button.setToolTip(session.get("title", "新对话"))
        self.back_button.show()
        self.recent.hide()
        self.transcript.clear()
        self.cursor = 0
        self.loading_history = True
        self.tabs.setCurrentIndex(0)
        sid = self.session
        def loaded(value):
            if sid != self.session or generation != self.view_generation:
                return
            if 'timeline' in value:
                self.render_events(value['timeline'])
                for workflow in value.get('workflows', []):
                    self.workflow_event(workflow)
                # Snapshots recover partial text and interrupted messages when
                # the process stopped before a final message event was emitted.
                for message in value['messages']:
                    if message['id'] not in self.transcript.workflow_messages:
                        self.transcript.reply(message['id'], message.get('response', message.get('partial_response', '')),
                            message['status'], message.get('elapsed_seconds'))
                for message in value['messages']:
                    if message.get('run_id') and message.get('notification'):
                        self.add_result_link(message['id'], 'run', message['run_id'])
                self.cursor = value['cursor']
                self.loading_history = False
                return
            for m in value["messages"]:
                if not m.get("notification"):
                    self.transcript.append("你：" + m.get("content", ""))
                for event in value.get("activities", []):
                    if event.get("message_id") == m["id"]:
                        self.transcript.activity(m["id"], tool_activity_text(event), m["status"], m.get("elapsed_seconds"))
                if m.get("elapsed_seconds") is not None and not m.get('notification'):
                    self.transcript.activity(m["id"], "处理结束", m["status"], m["elapsed_seconds"])
                self.transcript.reply(m["id"], m.get("response", m.get("partial_response", "")), m["status"], m.get("elapsed_seconds"))
            for event in value.get("activities", []):
                if event["type"] == "review":
                    self.review_event(event)
                if event["type"] in ("progress", "run", "algorithm"):
                    status = event.get("status", "running")
                    if event["type"] == "algorithm":
                        text = event["label"] + (" · 开始" if status == "running" else f" · {event.get('elapsed_seconds', 0):.1f} 秒")
                        self.transcript.activity(event["run_id"], text, role="algorithm")
                    elif event["type"] == "run":
                        self.transcript.activity(event["run_id"], run_activity_text(status), "running" if status == "queued" else status, event.get("elapsed_seconds"), role="algorithm")
            self.cursor = value["cursor"]
            for workflow in value.get('workflows', []):
                self.workflow_event(workflow)
            self.loading_history = False
        self.api("GET", self.route("history"), callback=loaded)
        revision = self.data_revision
        def data_loaded(value):
            if sid != self.session or generation != self.view_generation or revision != self.data_revision:
                return
            self.load_data_form(value.get("sources", {}), value.get("recognition_path") or "")
            self.data_dirty = False
            self.server_revision = value["revision"]
            self.loading_data = False
            self.data_status.setText("已保存")
            pending = self.settings.value('pending_data/' + sid, '')
            if pending:
                draft = json.loads(pending)
                if draft['sources'] == value['sources'] and (draft.get('recognition_path') or '') == (value.get('recognition_path') or ''):
                    self.settings.remove('pending_data/' + sid)
                    return
                self.load_data_form(draft['sources'], draft.get('recognition_path') or '')
                self.server_revision = draft['revision']
                self.data_dirty = True
                self.flush_data()
        self.api("GET", self.route("data"), callback=data_loaded)

    def data_changed(self, *args):
        if self.loading_data:
            return
        self.data_dirty = True
        self.data_revision += 1
        self.data_status.setText("等待保存…")
        self.settings.setValue('pending_data/' + (self.session or 'new'), json.dumps({'revision': self.server_revision, 'sources': self.form_inputs(), 'recognition_path': self.recognition.text()}, ensure_ascii=False))
        self.settings.sync()
        self.save_timer.start()

    def load_data_form(self, inputs, recognition):
        self.loading_data = True
        for name in BUSINESS_LAYERS:
            item = inputs.get(name, {})
            self.sources[name].setText(item.get("path", ""))
            self.sublayers[name].setText(item.get("source_layer") or "")
        self.recognition.setText(recognition)
        self.loading_data = False

    def form_inputs(self):
        return {n: {"path": self.sources[n].text().strip(), "source_layer": self.sublayers[n].text().strip()}
            for n in BUSINESS_LAYERS if self.sources[n].text().strip()}

    def show_data(self):
        if self.loading_data:
            self.status.setText('数据正在读取，请稍候；失败时请重新打开对话。')
            return
        if not self.session and not self.data_dirty:
            pending = self.settings.value('pending_data/new', '')
            if pending:
                draft = json.loads(pending)
                self.load_data_form(draft['sources'], draft.get('recognition_path') or '')
                self.data_dirty = True
                self.flush_data()
        self.data_popup.present(self.attach_button, above=True)

    def clear_source(self, name):
        if name == 'roads':
            self.recognition.clear()
        else:
            self.sources[name].clear()
            self.sublayers[name].clear()
        self.flush_data()

    def flush_data(self, then=None):
        if callable(then):
            self.after_save.append(then)
        self.save_timer.stop()
        if self.saving_data:
            return
        if self.loading_data:
            self.data_status.setText("草稿尚未加载，不能使用旧数据发送请求")
            self.after_save.clear()
            return
        if not self.data_dirty:
            callbacks, self.after_save = self.after_save, []
            for callback in callbacks:
                callback()
            return
        self.saving_data = True
        self.data_status.setText("保存中…")
        def failed(error):
            self.saving_data = False
            self.after_save.clear()
            self.data_status.setText("保存失败：" + str(error) + "；未发送请求。")
        if not self.session:
            def created(session):
                self.session = session['id']
                self.server_revision = 0
                self.saving_data = False
                self.last_sessions_refresh = 0
                self.flush_data()
            self.api('POST', '/sessions', {}, created, quiet=True, on_error=failed)
            return
        revision = self.data_revision
        sid = self.session
        payload = {'revision': self.server_revision, 'sources': self.form_inputs(), 'recognition_path': self.recognition.text().strip() or None}
        def saved(value):
            self.saving_data = False
            if sid != self.session:
                return
            self.server_revision = value['revision']
            if revision == self.data_revision:
                self.settings.remove('pending_data/' + sid)
                self.settings.remove('pending_data/new')
                self.settings.sync()
            self.data_dirty = revision != self.data_revision
            if self.data_dirty:
                self.settings.setValue('pending_data/' + sid, json.dumps({'revision': self.server_revision, 'sources': self.form_inputs(), 'recognition_path': self.recognition.text()}, ensure_ascii=False))
                self.settings.sync()
            self.data_status.setText('等待保存…' if self.data_dirty else '已保存')
            self.flush_data()
        self.api('POST', self.route('data'), payload, saved, quiet=True, on_error=failed)

    def button(self, text, callback):
        return button(text, callback)

    def route(self, suffix):
        return "/sessions/" + self.session + "/" + suffix

    def api(self, method, path, payload=None, callback=None, quiet=False, on_error=None, deduplicate=True):
        request_key = (method, path)
        if deduplicate and request_key in self.busy:
            if not quiet:
                self.status.setText("相同请求正在处理，请等待完成。")
            return
        self.busy.add(request_key)
        req = QNetworkRequest(QUrl(self.url.text().rstrip("/") + path))
        req.setRawHeader(b"Authorization", ("Bearer " + self.token.text().strip()).encode())
        req.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        req.setTransferTimeout(120000)
        reply = self.net.get(req) if method == "GET" else self.net.post(req, QByteArray(json.dumps(payload or {}, ensure_ascii=False).encode()))
        self.replies.add(reply)
        def done():
            self.busy.discard(request_key)
            self.replies.discard(reply)
            try:
                raw = bytes(reply.readAll()).decode("utf-8")
                value = json.loads(raw) if raw else {}
                if reply.error() != QNetworkReply.NoError:
                    raise ValueError(str(value.get("detail", "后端连接失败")))
                if not quiet:
                    self.status.setText("后端已连接 · " + ("当前会话就绪" if self.session else "尚未登记项目"))
                if callback:
                    callback(value)
            except Exception as exc:
                if path == "/sessions":
                    self.creating_session = False
                if path.endswith("/history"):
                    self.loading_history = False
                if on_error:
                    on_error(exc)
                self.status.setText(str(exc))
                if path == "/projects":
                    self.data_status.setText("登记失败：" + str(exc))
                if not quiet:
                    self.transcript.append("错误：" + str(exc))
            finally:
                reply.deleteLater()
        reply.finished.connect(done)

    def choose_token(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 connection.token")
        if path:
            self.token.setText(Path(path).read_text(encoding="utf-8").strip())
            self.settings.setValue("token_path", path)
            self.api("GET", "/health", callback=self.report)

    def workspace(self):
        directory = Path.home() / ".ftth_demo0" / "inputs" / request_id()
        directory.mkdir(parents=True)
        return directory

    def browse(self, name):
        path, _ = QFileDialog.getOpenFileName(self, "选择 " + name, "", "GIS / 表格 (*.shp *.gpkg *.geojson *.csv *.xlsx)")
        if path:
            self.sources[name].setText(path)
            self.sublayers[name].clear()
            self.flush_data()

    def browse_recognition(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择识别结果", "", "GeoPackage (*.gpkg)")
        if path:
            self.recognition.setText(path)
            self.flush_data()

    def current_layer(self, name):
        try:
            layer = self.iface.activeLayer()
            if layer is None or not hasattr(layer, "getFeatures"):
                raise ValueError("请选择矢量图层")
            path = self.workspace() / (name + ".gpkg")
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            options.layerName = name
            options.onlySelectedFeatures = layer.selectedFeatureCount() > 0
            result = QgsVectorFileWriter.writeAsVectorFormatV3(layer, str(path), QgsProject.instance().transformContext(), options)
            if result[0] != QgsVectorFileWriter.NoError:
                raise ValueError(str(result))
            self.sources[name].setText(str(path))
            self.sublayers[name].setText(name)
            self.flush_data()
            self.status.setText("已保存所选要素快照" if options.onlySelectedFeatures else "已保存当前图层全部要素快照")
        except Exception as exc:
            self.status.setText(str(exc))


    def send(self):
        text = self.message.text().strip()
        if not text or self.creating_session:
            return
        self.flush_data(lambda: self.send_saved(text))

    def send_saved(self, text):
        if not self.session:
            self.creating_session = True
            def opened(session):
                self.creating_session = False
                self.session = session['id']
                self.server_revision = 0
                self.submit_message(self.session, text)
            self.api('POST', '/sessions', {}, opened)
        else:
            self.submit_message(self.session, text)

    def submit_message(self, sid, text):
        route = "/sessions/" + sid + "/messages"
        def accepted(value):
            if sid == self.session:
                self.recent.hide()
                self.back_button.show()
                if self.message.text().strip() == text:
                    self.message.clear()
            self.last_sessions_refresh = 0
        self.api("POST", route, {"request_id": request_id(), "content": text}, accepted)

    def add_result_link(self, mid, kind, identifier):
        card = self.transcript.replies.get(mid)
        if card is None or hasattr(card, 'result_button'):
            return
        sid = self.session
        card.result_button = button('查看结果', lambda: self.show_result_page(sid, kind, identifier))
        card.layout().addWidget(card.result_button)

    def show_result_page(self, sid, kind, identifier):
        self.result_target = (sid, kind, identifier)
        self.result_title.setText(identifier)
        self.tabs.setCurrentIndex(1)
        self.open_result_report(False)

    def open_result_report(self, review=False):
        if not self.result_target:
            return
        self.report_generation += 1
        generation = self.report_generation
        target = self.result_target
        sid, kind, identifier = target
        if kind == 'check' and review:
            self.report_box.setPlainText('本次仅检查数据，未执行独立审查')
            return
        self.report_box.setPlainText('正在读取报告…')
        def loaded(value):
            if self.result_target != target or generation != self.report_generation:
                return
            if review:
                from .review_view import render_reviews
                text = render_reviews(value)
            else:
                text = json.dumps(value, ensure_ascii=False, indent=2)
            self.report_box.setPlainText(text)
        self.api('GET', f'/sessions/{sid}/' + ('checks/' if kind == 'check' else 'runs/') + identifier + ('/reviews' if review else ''), callback=loaded, quiet=True, deduplicate=False)

    def publish_qgis_state(self):
        project = QgsProject.instance()
        for group in reversed(project.layerTreeRoot().children()):
            name = group.name()
            if not name.startswith('FTTH demo0 run_') or not hasattr(group, 'findLayers'):
                continue
            rid = name.removeprefix('FTTH demo0 ')
            if rid not in self.run_cache:
                continue
            rows = []
            for node in group.findLayers():
                layer = node.layer()
                if layer is not None:
                    rows.append({'name': layer.name(), 'valid': layer.isValid(), 'modified': layer.isModified(),
                        'crs': layer.crs().authid(), 'count': layer.featureCount(),
                        'source_hash': hashlib.sha256(os.path.normcase(os.path.abspath(layer.source().split('|')[0])).encode('utf-8')).hexdigest()})
            self.api('POST', self.route('qgis-state'), {'run_id': rid, 'project_crs': project.crs().authid(), 'layers': rows}, quiet=True)
            break

    def review_event(self, event):
        summary = event.get("summary", {})
        stamp = summary.get('updated', event.get('id', 0))
        if stamp < self.review_versions.get(event['review_id'], -1):
            return
        self.review_versions[event['review_id']] = stamp
        state = event["status"]
        label = {"queued": "排队", "running": "读取证据与独立审查", "completed": "已完成", "failed": "审查暂未完成", "interrupted": "中断", "skipped": "未执行"}.get(state, state)
        if state == "running":
            label = {"evidence": "整理审查证据", "model_review": "分析证据与形成结论", "read_evidence": "读取证据", "correct_result": "纠正审查格式或引用", "validate_result": "校验结论与证据引用", "verify": "复核证据完整性", "rule_checks": "执行工作簿与 QGIS 校验工具"}.get(summary.get("stage"), label)
            names = {"run": "运行参数", "input": "输入快照", "execution": "执行状态", "task2": "分区", "task3": "选址优化", "task4_placement": "PBO 选址", "task4_clusters": "BPE 聚类", "task4_partition": "分区归属", "task6": "管网", "task5": "光缆", "task5_artifact": "光缆校验", "handoff": "任务交接", "fields": "字段校验", "failure": "失败报告"}
            ids = summary.get("active_evidence_ids", [])
            if ids:
                label += "：" + "、".join(names.get(name, "报告") for name in ids[:2]) + (f"等 {len(ids)} 项" if len(ids) > 2 else "")
        verdict = (summary.get("result") or {}).get("verdict")
        if verdict:
            label += " · " + {"pass": "通过（非工程验收）", "fail": "未通过", "needs_review": "有待复核事项", "insufficient_evidence": "证据不足"}.get(verdict, verdict)
        self.transcript.activity(event["review_id"], label,
            "running" if state in {"queued", "running"} else state if state in {"failed", "interrupted"} else "succeeded",
            None if state in {"queued", "running"} else summary.get("elapsed_seconds"),
            role="service" if state == "running" and summary.get("stage") in {"evidence", "validate_result", "verify", "rule_checks"} else "review")

    def workflow_event(self, workflow):
        sid = workflow['session_id']
        if sid != self.session:
            return
        wid = workflow['id']
        stamp = workflow.get('updated', workflow.get('created', 0))
        if stamp <= self.transcript.workflow_versions.get(wid, -1):
            return  # Poll events and resolve responses may arrive out of order.
        self.transcript.workflow_versions[wid] = stamp
        mid = workflow['message_id']
        action = (workflow.get('intent') or {}).get('action')
        if workflow['status'] == 'interpreting':
            self.transcript.activity(mid, '理解本轮请求', 'running', role='main', resume=True)
            return
        if action in {'query', 'discuss', 'clarify'}:
            self.transcript.workflow_messages.discard(mid)
            self.transcript.clear_workflow_card(wid)
            intro = self.transcript.replies.pop('intro_' + wid, None)
            if intro:
                intro.hide()
                self.transcript.items.removeWidget(intro)
                intro.deleteLater()
            reply = self.transcript.replies.get(mid)
            if reply:
                reply.show()
            if action == 'clarify':
                self.transcript.reply(mid, workflow.get('detail') or '请补充具体需求。', 'succeeded')
                self.transcript.activity(mid, '等待补充信息', 'succeeded', role='main', resume=True)
            return
        self.transcript.workflow_messages.add(mid)
        existing = self.transcript.replies.get(mid)
        if existing:
            existing.hide()
        intro = '我先核对你的请求、项目数据和工程策略；需要你确认的内容会显示为选项。'
        if action == 'plan':
            scope = '分区规划' if workflow['intent'].get('scope') == 'partition' else '完整规划'
            intro = f'本次将进行{scope}，随后独立审查。先检查数据和工程策略；如有需要确认的事项，请在下方选择。'
        new_intro = 'intro_' + wid not in self.transcript.replies
        self.transcript.reply('intro_' + wid, intro, 'succeeded')
        intro_card = self.transcript.replies['intro_' + wid]
        intro_card.badge.setText('FTTH · 本次安排')
        intro_card.copy_button.hide()
        if new_intro and mid in self.transcript.activities:
            layout = self.transcript.items
            layout.removeWidget(intro_card)
            layout.insertWidget(layout.indexOf(self.transcript.activities[mid]['widget']), intro_card)
        labels = {'interpreting': '理解本轮请求', 'validating': '校验策略与输入', 'waiting_choice': '等待你的选择',
            'blocked': '尚未执行：需要处理阻塞', 'submitted': '任务已提交', 'running': '正在计算', 'reviewing': '正在审查',
            'completed': '本轮流程已结束', 'failed': '本轮处理未完成', 'interrupted': '工作流已中断', 'cancelled': '工作流已取消'}
        state = workflow['status']
        self.transcript.activity(mid, '规划已提交' if state in {'submitted', 'running', 'reviewing', 'completed'} and workflow.get('run_id') else labels.get(state, state),
            'running' if state in {'interpreting', 'validating'} else 'failed' if state == 'failed' else 'succeeded',
            role='main' if state == 'interpreting' else 'service', resume=True)
        if state in {'failed', 'cancelled', 'interrupted'}:
            self.transcript.reply('end_' + wid, workflow.get('detail') or labels[state], 'succeeded')
        def chosen(oid):
            self.api('POST', f'/sessions/{sid}/workflows/{wid}/resolve',
                {'request_id': request_id(), 'option_id': oid, 'version': workflow['version']}, self.workflow_event)
        def cancel(checked=False):
            self.api('POST', f'/sessions/{sid}/workflows/{wid}/cancel', {}, self.workflow_event)
        self.transcript.workflow_card(workflow, chosen, cancel)

    def report(self, value):
        self.report_box.setPlainText(json.dumps(value, ensure_ascii=False, indent=2))







    def render_events(self, items):
        for item in items:
            self.cursor = max(self.cursor, item["id"])
            if item['type'] == 'workflow':
                self.workflow_event(item['workflow'])
            if item["type"] == "user":
                self.transcript.append("你：" + item["text"])
            elif item["type"] == "session_title":
                self.title_button.setText(item["title"])
                self.title_button.setToolTip(item["title"])
            elif item["type"] == "work":
                if item['message_id'] in self.transcript.workflow_messages:
                    continue
                self.transcript.activity(item["message_id"], "理解请求" if item["status"] == "running" else "处理结束", item["status"], item.get("elapsed_seconds"))
                if item["status"] == "running":
                    self.transcript.reply(item["message_id"], status="running")
            elif item["type"] == "activity":
                if item['message_id'] in self.transcript.workflow_messages:
                    continue
                self.transcript.activity(item["message_id"], tool_activity_text(item))
            elif item["type"] == "run":
                self.transcript.activity(item["run_id"], run_activity_text(item["status"]), "running" if item["status"] in ("running", "queued") else item["status"], item.get("elapsed_seconds"), role="algorithm")
            elif item["type"] == "review":
                self.review_event(item)
            elif item['type'] == 'check':
                record = item['check']
                mid = 'check_' + record['id']
                self.transcript.reply(mid, '数据检查' + ('完成' if record['status'] == 'succeeded' else '未通过') + ' · ' + record['id'] + ' · 数据版本 ' + str(record['data_revision']), 'succeeded')
                self.add_result_link(mid, 'check', record['id'])
            elif item["type"] == "assistant":
                if item['message_id'] in self.transcript.workflow_messages:
                    continue
                # A final reply is already a message; it needs no extra
                # zero-second actor row of its own.
                self.transcript.reply(item["message_id"], str(item["text"]), item.get("status", "succeeded"), item.get("elapsed_seconds"))
                ref = item.get('result_ref')
                if ref:
                    self.add_result_link(item['message_id'], ref['kind'], ref['id'])
                elif item.get('run_id'):
                    self.add_result_link(item['message_id'], 'run', item['run_id'])
            elif item["type"] == "assistant_stream":
                self.transcript.reply(item["message_id"], item["text"], "running")
            elif item["type"] == "progress":
                pass  # Structured algorithm events carry readable phase names.
            elif item["type"] == "algorithm":
                text = item["label"] + (" · 开始" if item["status"] == "running" else f" · {item.get('elapsed_seconds', 0):.1f} 秒")
                self.transcript.activity(item["run_id"], text, role="algorithm")
            elif item["type"] == "display":
                self.transcript.append("QGIS：" + ("已展示" if item["status"] == "succeeded" else "展示失败"))

    def poll(self):
        if self.token.text() and time.monotonic() - self.last_health_check > 10:
            self.last_health_check = time.monotonic()
            def health(value):
                self.backend_compatible = value.get("execution_authority") == "user_request" and value.get("context_protocol") == "task-v1"
                if not self.backend_compatible:
                    self.status.setText("后端版本过旧：停止旧后端，再运行 `uv run ftth serve` 启动新版")
                    return
                problem = backend_problem(value.get("qgis"))
                if problem:
                    self.status.setText(problem)
                    self.status.setToolTip((value.get("qgis") or {}).get("error", ""))
                elif self.backend_problem and self.status.text() == self.backend_problem:
                    self.status.setText("")  # Cleared only if nothing newer replaced it.
                self.backend_problem = problem
            self.api("GET", "/health", callback=health, quiet=True)
        if self.token.text() and time.monotonic() - self.last_sessions_refresh > 5:
            self.last_sessions_refresh = time.monotonic()
            self.api("GET", "/sessions", callback=self.refresh_sessions, quiet=True)
        if not self.session or not self.token.text() or self.loading_history:
            return
        sid = self.session
        generation = self.view_generation
        def events(items):
            if sid != self.session or generation != self.view_generation:
                return
            self.render_events(items)
        def runs(items):
            if sid != self.session:
                return
            self.run_cache = {r["id"]: r for r in items}
        def displays(items):
            if sid != self.session:
                return
            for item in items:
                if item["id"] not in self.displayed:
                    try:
                        self.displayed[item["id"]] = {"success": True, "details": show_result(self.iface, item)}
                    except Exception as exc:
                        self.displayed[item["id"]] = {"success": False, "details": {"error": str(exc)}}
                self.api("POST", self.route("display/" + item["id"] + "/ack"), self.displayed[item["id"]], quiet=True)
        self.api("GET", self.route("events") + "?after=" + str(self.cursor), callback=events, quiet=True)
        if time.monotonic() - self.last_runs_refresh >= 1:
            self.last_runs_refresh = time.monotonic()
            self.api("GET", self.route("runs"), callback=runs, quiet=True)
            self.api("GET", self.route("display"), callback=displays, quiet=True)
            self.publish_qgis_state()

    def shutdown(self):
        self.flush_data()
        self.save_timer.stop()
        self.timer.stop()
        for reply in list(self.replies):
            reply.abort()
