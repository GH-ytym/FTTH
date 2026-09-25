import json
import uuid
import time
from pathlib import Path

from qgis.PyQt.QtCore import QTimer, QUrl, QByteArray, QSettings
from qgis.PyQt.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from qgis.PyQt.QtGui import QFont, QFontDatabase
from qgis.PyQt.QtWidgets import QWidget, QFileDialog, QCheckBox
from qgis.core import QgsVectorFileWriter, QgsProject
from .input_config_builder import build_task1_config, BUSINESS_LAYERS
from .display import show_result
from .fluent_ui import build_ui, button, hover_feedback


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
        self.settings = QSettings("FTTH", "demo0")
        self.session = ""
        self.view_generation = 0
        self.project_id = None
        self.session_project = None
        self.creating_session = False
        self.last_sessions_refresh = 0
        self.last_health_check = 0
        self.backend_compatible = None
        self.sessions_cache = []
        self.show_all_sessions = False
        self.loading_history = False
        self.cursor = 0
        self.busy = set()
        self.displayed = {}
        self.net = QNetworkAccessManager(self)
        self.replies = set()
        build_ui(self, BUSINESS_LAYERS)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(250)
        self.last_runs_refresh = 0
        self.run_cache = {}

    def home(self):
        self.view_generation += 1
        self.loading_history = False
        self.action_mode.setCurrentIndex(0)
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
        self.sessions_cache = items
        current = next((s for s in items if s["id"] == self.session), None)
        if current:
            self.title_button.setText(current.get("title", "新对话"))
            self.title_button.setToolTip(current.get("title", "新对话"))
        while self.recent_layout.count():
            item = self.recent_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        from .fluent_ui import TransparentPushButton
        for session in items if self.show_all_sessions else items[:3]:
            b = TransparentPushButton(session.get("title", "新对话"), self.recent)
            b.setStyleSheet("QPushButton {text-align:left;border:none;background:transparent;color:#56616f;padding:6px;} QPushButton:hover, QPushButton:pressed {background:#FFFFFF;border-radius:6px;}")
            hover_feedback(b)
            b.clicked.connect(lambda checked=False, s=session: self.open_session(s))
            self.recent_layout.addWidget(b)
        if len(items) > 3 and not self.show_all_sessions:
            b = TransparentPushButton(f"查看全部 ({len(items)})", self.recent)
            hover_feedback(b)
            b.clicked.connect(self.all_sessions)
            self.recent_layout.addWidget(b)

    def all_sessions(self):
        self.show_all_sessions = True
        self.refresh_sessions(self.sessions_cache)

    def open_session(self, session):
        self.view_generation += 1
        generation = self.view_generation
        self.action_mode.setCurrentIndex(0)
        self.session = session["id"]
        self.session_project = session.get("project_id")
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
            self.apply_policy(value["session"].get("policy", {}))
            for m in value["messages"]:
                if not m.get("notification"):
                    self.transcript.append("你：" + m.get("content", ""))
                for event in value.get("activities", []):
                    if event.get("message_id") == m["id"]:
                        self.transcript.activity(m["id"], event["name"] + " · " + event["status"], m["status"], m.get("elapsed_seconds"))
                if m.get("elapsed_seconds") is not None:
                    self.transcript.activity(m["id"], "处理结束", m["status"], m["elapsed_seconds"])
                self.transcript.reply(m["id"], m.get("response", m.get("partial_response", "")), m["status"], m.get("elapsed_seconds"))
            for event in value.get("activities", []):
                if event["type"] in ("progress", "run", "algorithm"):
                    status = event.get("status", "running")
                    if event["type"] == "algorithm":
                        text = event["label"] + (" · 开始" if status == "running" else f" · {event.get('elapsed_seconds', 0):.1f} 秒")
                        self.transcript.activity(event["run_id"], text)
                    elif event["type"] == "run":
                        self.transcript.activity(event["run_id"], "算法任务 · " + status, "running" if status == "queued" else status, event.get("elapsed_seconds"))
            self.cursor = value["cursor"]
            self.loading_history = False
        self.api("GET", self.route("history"), callback=loaded)

    def registered_project(self, project, origin_sid, origin_project, generation):
        if origin_sid and not origin_project:
            def bound(value):
                if origin_sid == self.session and generation == self.view_generation:
                    self.project_id = project["id"]
                    self.session_project = project["id"]
                    self.tabs.setCurrentIndex(0)
                    self.status.setText("项目数据已登记")
            self.api("POST", "/sessions/" + origin_sid + "/project", {"project_id": project["id"]}, bound)
        else:
            # Keep a registered project reachable even if the user navigated away.
            def created(session):
                self.last_sessions_refresh = 0
                if generation == self.view_generation:
                    self.open_session(session)
                    self.status.setText("项目数据已登记")
            self.api("POST", "/sessions", {"project_id": project["id"]}, created)

    def registration_callback(self):
        sid, project, generation = self.session, self.session_project, self.view_generation
        return lambda value: self.registered_project(value, sid, project, generation)

    def apply_policy(self, policy):
        for name in ("use_formal_infrastructure", "use_recognition_roads", "split_oversized_imbs"):
            self.controls[name].setChecked(policy.get(name, False))

    def save_policy(self):
        if not self.session or not self.session_project:
            self.status.setText("请先为当前会话登记数据")
            return
        sid = self.session
        payload = {n: self.controls[n].isChecked() for n in ("use_formal_infrastructure", "use_recognition_roads", "split_oversized_imbs")}
        self.api("POST", self.route("policy"), payload,
            lambda value: self.status.setText("本会话工程策略已保存") if sid == self.session else None)

    def button(self, text, callback):
        return button(text, callback)

    def route(self, suffix):
        return "/sessions/" + self.session + "/" + suffix

    def api(self, method, path, payload=None, callback=None, quiet=False):
        request_key = (method, path)
        if request_key in self.busy:
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
                self.status.setText(str(exc))
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

    def browse_recognition(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择识别结果", "", "GeoPackage (*.gpkg)")
        if path:
            self.recognition.setText(path)

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
            self.status.setText("已保存所选要素快照" if options.onlySelectedFeatures else "已保存当前图层全部要素快照")
        except Exception as exc:
            self.status.setText(str(exc))

    def register(self):
        try:
            path = self.workspace() / "config.json"
            build_task1_config(Path(__file__).parent / "task1_config.json", path,
                {n: {"path": self.sources[n].text().strip(), "source_layer": self.sublayers[n].text().strip()} for n in BUSINESS_LAYERS})
            self.api("POST", "/projects", {"name": QgsProject.instance().title() or "QGIS项目", "config_path": str(path), "recognition_path": self.recognition.text().strip() or None}, self.registration_callback())
        except Exception as exc:
            self.status.setText(str(exc))

    def import_config(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入有明确字段映射的输入配置", "", "JSON (*.json)")
        if not path:
            return
        self.api("POST", "/projects", {"name": Path(path).stem, "config_path": path, "recognition_path": self.recognition.text().strip() or None}, self.registration_callback())

    def send(self):
        text = self.message.text().strip()
        if not text or self.creating_session:
            return
        if self.backend_compatible is False:
            self.status.setText("后端需要升级到 0.4.0：停止旧后端，再在原终端运行 scripts/start_qwen.ps1")
            return
        allow_actions = bool(self.action_mode.currentData())
        if not self.session:
            self.creating_session = True
            generation = self.view_generation
            def opened(session):
                self.creating_session = False
                if generation == self.view_generation:
                    self.open_session(session)
                    self.action_mode.setCurrentIndex(1 if allow_actions else 0)
                self.submit_message(session["id"], text, allow_actions)
            self.api("POST", "/sessions", {"project_id": self.project_id}, opened)
            return
        self.submit_message(self.session, text, allow_actions)

    def submit_message(self, sid, text, allow_actions):
        route = "/sessions/" + sid + "/messages"
        if ("POST", route) in self.busy:
            return
        def accepted(value):
            if sid == self.session and self.message.text().strip() == text:
                self.message.clear()
            self.last_sessions_refresh = 0
        self.api("POST", route, {"request_id": request_id(), "content": text,
            "allow_actions": allow_actions}, accepted)

    def manual(self, execute):
        if not self.session:
            self.status.setText("请先登记数据")
            return
        values = {n: c.isChecked() if isinstance(c, QCheckBox) else c.value() for n, c in self.controls.items()}
        self.api("POST", self.route("runs" if execute else "validate"), {"request_id": request_id(), "scope": self.scope.currentData(), "parameter_overrides": values}, self.report)

    def report(self, value):
        self.report_box.setPlainText(json.dumps(value, ensure_ascii=False, indent=2))
        self.tabs.setCurrentIndex(2)

    def run_report(self):
        if self.runs.currentData():
            self.api("GET", self.route("runs/" + self.runs.currentData()), callback=self.report)

    def load_parameters(self):
        run = self.run_cache.get(self.runs.currentData(), {})
        for name, value in run.get("parameters", {}).items():
            control = self.controls[name]
            control.setChecked(value) if isinstance(control, QCheckBox) else control.setValue(value)
        self.scope.setCurrentIndex(max(0, self.scope.findData(run.get("scope"))))
        self.tabs.setCurrentIndex(1)

    def display(self):
        if self.runs.currentData():
            self.api("POST", self.route("display"), {"run_id": self.runs.currentData()}, self.report)

    def cancel(self):
        if self.runs.currentData():
            self.api("POST", self.route("runs/" + self.runs.currentData() + "/cancel"), callback=self.report)

    def export(self):
        if self.runs.currentData():
            self.api("POST", self.route("exports"), {"request_id": request_id(), "run_id": self.runs.currentData()}, self.report)

    def compare(self):
        if self.runs.currentData() and self.compare_to.currentData():
            self.api("GET", self.route("compare") + "?left=" + self.runs.currentData() + "&right=" + self.compare_to.currentData(), callback=self.report)

    def poll(self):
        if self.token.text() and time.monotonic() - self.last_health_check > 10:
            self.last_health_check = time.monotonic()
            def health(value):
                self.backend_compatible = value.get("version") == "0.4.0"
                if not self.backend_compatible:
                    self.status.setText("后端需要升级到 0.4.0：停止旧后端，再在原终端运行 scripts/start_qwen.ps1")
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
            for item in items:
                self.cursor = max(self.cursor, item["id"])
                if item["type"] == "user":
                    self.transcript.append("你：" + item["text"])
                elif item["type"] == "session_title":
                    self.title_button.setText(item["title"])
                    self.title_button.setToolTip(item["title"])
                elif item["type"] == "work":
                    self.transcript.activity(item["message_id"], "正在理解请求，检查可用数据与工具。" if item["status"] == "running" else "处理结束", item["status"], item.get("elapsed_seconds"))
                    if item["status"] == "running":
                        self.transcript.reply(item["message_id"], status="running")
                elif item["type"] == "activity":
                    names = {"inspect_project":"检查项目数据", "validate_inputs":"校验输入", "submit_planning":"提交规划", "get_run":"读取运行结果", "compare_runs":"比较方案", "show_result":"展示地图", "export_result":"导出成果"}
                    self.transcript.activity(item["message_id"], names.get(item["name"], item["name"]) + (" · 开始" if item["status"] == "running" else " · " + item["status"]))
                elif item["type"] == "run":
                    self.transcript.activity(item["run_id"], "算法任务 · " + item["status"], "running" if item["status"] in ("running", "queued") else item["status"], item.get("elapsed_seconds"))
                elif item["type"] == "assistant":
                    self.transcript.activity(item["message_id"], "回复已生成", item.get("status", "succeeded"), item.get("elapsed_seconds"))
                    self.transcript.reply(item["message_id"], str(item["text"]), item.get("status", "succeeded"), item.get("elapsed_seconds"))
                elif item["type"] == "assistant_stream":
                    self.transcript.reply(item["message_id"], item["text"], "running")
                elif item["type"] == "progress":
                    pass  # Structured algorithm events carry readable phase names.
                elif item["type"] == "algorithm":
                    text = item["label"] + (" · 开始" if item["status"] == "running" else f" · {item.get('elapsed_seconds', 0):.1f} 秒")
                    self.transcript.activity(item["run_id"], text)
                elif item["type"] == "display":
                    self.transcript.append("QGIS：" + ("已展示" if item["status"] == "succeeded" else "展示失败"))
        def runs(items):
            if sid != self.session:
                return
            self.run_cache = {r["id"]: r for r in items}
            for combo in (self.runs, self.compare_to):
                previous = combo.currentData()
                combo.clear()
                for r in reversed(items):
                    combo.addItem(r["id"] + " · " + {"queued":"排队中", "running":"运行中", "succeeded":"已完成", "failed":"失败", "cancelled":"已取消", "interrupted":"已中断"}.get(r["status"], r["status"]), userData=r["id"])
                if previous:
                    combo.setCurrentIndex(max(0, combo.findData(previous)))
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

    def shutdown(self):
        self.timer.stop()
        for reply in list(self.replies):
            reply.abort()
