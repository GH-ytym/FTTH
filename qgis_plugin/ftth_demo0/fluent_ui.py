"""Fluent presentation layer, embedded in the existing QGIS dock."""
import sys
import time
from pathlib import Path

# Use QGIS's own PyQt5. Never ship or replace its Qt runtime.
vendor = str(Path(__file__).parent / "_vendor")
if vendor not in sys.path:
    sys.path.append(vendor)

from qgis.PyQt.QtCore import Qt, pyqtSignal, QTimer
from qgis.PyQt.QtGui import QTextDocument, QFont, QTextTable, QTextLength, QColor, QPalette
from qgis.PyQt.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QStackedWidget, QTextBrowser, QSizePolicy, QApplication, QAbstractButton)
from qfluentwidgets import (PushButton, PrimaryPushButton, TransparentPushButton,
    TransparentToolButton, ToolButton, setCustomStyleSheet,
    LineEdit, TextEdit, ComboBox, CheckBox, SpinBox, DoubleSpinBox, CardWidget,
    ScrollArea, BodyLabel, CaptionLabel, SubtitleLabel, TitleLabel, SegmentedWidget,
    FluentIcon as FIF)


class Pages(QWidget):
    """Small QTabWidget-compatible facade with Fluent segmented navigation."""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        self.nav = SegmentedWidget(self)
        self.stack = QStackedWidget(self)
        layout.addWidget(self.nav)
        layout.addWidget(self.stack, 1)
        self.stack.currentChanged.connect(lambda i: self.nav.setCurrentItem(str(i)))

    def addTab(self, widget, label):
        index = self.stack.addWidget(widget)
        self.nav.addItem(str(index), label, onClick=lambda: self.stack.setCurrentIndex(index))
        if index == 0:
            self.nav.setCurrentItem("0")

    def setCurrentIndex(self, index):
        self.stack.setCurrentIndex(index)


class Composer(TextEdit):
    returnPressed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(90)
        self.setAcceptRichText(False)
        self.layer.hide()  # Fluent EditLayer draws a focus underline outside QSS.

    def text(self):
        return self.toPlainText()

    def setText(self, text):
        self.setPlainText(text)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers() & Qt.ShiftModifier:
            self.returnPressed.emit()
        else:
            super().keyPressEvent(event)


class MarkdownView(QTextBrowser):
    """Non-scrolling selectable Markdown that follows the card width."""
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setFrameShape(self.NoFrame)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setStyleSheet("background:transparent; border:none; color:#243247; font:14px 'Microsoft YaHei';")
        self.document().setDefaultStyleSheet("table {border-collapse:collapse;} th,td {padding:5px;border:1px solid #dce3eb;} pre {background:#FFFFFF;} h1,h2,h3 {color:#182b48;}")
        self.setMarkdown(text)
        for frame in self.document().rootFrame().childFrames():
            if isinstance(frame, QTextTable):
                fmt = frame.format()
                fmt.setBorderCollapse(True)
                fmt.setBorder(.5)
                fmt.setBorderBrush(QColor("#dce3eb"))
                fmt.setCellPadding(6)
                fmt.setCellSpacing(0)
                fmt.setWidth(QTextLength(QTextLength.PercentageLength, 100))
                frame.setFormat(fmt)
        self.document().contentsChanged.connect(self.fit)
        QTimer.singleShot(0, self.fit)

    def loadResource(self, kind, url):
        # Model text is untrusted; do not fetch images or local files in Markdown.
        return None

    def fit(self):
        self.document().setTextWidth(max(100, self.viewport().width()))
        self.setFixedHeight(max(30, int(self.document().size().height()) + 14))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit()


class Conversation(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setStyleSheet("QScrollArea {background:transparent;border:none;}")
        self.body = QWidget()
        self.body.setStyleSheet("QWidget {background:transparent;}")
        white_surface(self.viewport())
        white_surface(self.body)
        self.items = QVBoxLayout(self.body)
        self.items.setContentsMargins(0, 0, 4, 0)
        self.items.setSpacing(14)
        self.items.addStretch()
        self.setWidget(self.body)
        self.activities = {}
        self.replies = {}
        self.scroll_timer = QTimer(self)
        self.scroll_timer.setSingleShot(True)
        self.scroll_timer.timeout.connect(self._scroll_to_bottom)
        self.empty = QWidget(self.body)
        self.items.insertWidget(0, self.empty)

    def reply(self, message_id, text="", status="running", elapsed=None):
        """One stable card per message, shared by history, streaming and final events."""
        self.empty.hide()
        bar = self.verticalScrollBar()
        at_bottom = bar.maximum() - bar.value() < 60
        if message_id not in self.replies:
            card = LiveReply(self.body)
            self.replies[message_id] = card
            self.items.insertWidget(self.items.count() - 1, card)
        self.replies[message_id].update_reply(text, status, elapsed)
        if at_bottom:
            self.scroll_timer.start(0)

    def _scroll_to_bottom(self):
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def append(self, text):
        self.empty.hide()
        bar = self.verticalScrollBar()
        at_bottom = bar.maximum() - bar.value() < 60
        role, sep, content = text.partition("：")
        if not sep:
            role, content = "助手", text
        wrapper = QWidget(self.body)
        wrapper.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        outer = QHBoxLayout(wrapper)
        outer.setContentsMargins(0, 6, 0, 6)
        bubble = QWidget(wrapper)
        bubble.setObjectName("userBubble" if role == "你" else "assistantMessage")
        if role == "你":
            bubble.setStyleSheet("QWidget#userBubble {background:#E8F3FE; border-radius:16px;}")
            outer.addStretch(1)
            outer.addWidget(bubble, 5)
        else:
            outer.addWidget(bubble)
        box = QVBoxLayout(bubble)
        box.setContentsMargins(14, 10, 14, 10) if role == "你" else box.setContentsMargins(0, 4, 0, 4)
        box.setSpacing(4)
        view = MarkdownView(content.strip(), bubble)
        if role == "你":
            view.setPlainText(content.strip())
        elif role in ("QGIS", "错误"):
            view.setPlainText(("QGIS · " if role == "QGIS" else "请求未完成：") + content.strip())
            view.setStyleSheet("background:transparent;border:none;color:#667687;font:13px 'Microsoft YaHei';")
        box.addWidget(view)
        if role == "助手":
            row = QHBoxLayout()
            copy = TransparentToolButton(FIF.COPY, bubble)
            hover_feedback(copy)
            copy.setFixedSize(28, 26)
            copy.setToolTip("复制回复")
            copy.clicked.connect(lambda: QApplication.clipboard().setText(content.strip()))
            row.addWidget(copy)
            row.addStretch()
            box.addLayout(row)
        self.items.insertWidget(self.items.count() - 1, wrapper)
        if at_bottom:
            self.scroll_timer.start(50)

    def clear(self):
        self.scroll_timer.stop()
        self.activities.clear()
        self.replies.clear()
        for i in reversed(range(self.items.count())):
            item = self.items.itemAt(i)
            widget = item.widget()
            if widget and widget is not self.empty:
                self.items.takeAt(i)
                widget.hide()
                widget.deleteLater()
        self.empty.show()

    def activity(self, key, text, status="running", elapsed=None):
        self.empty.hide()
        if key not in self.activities:
            widget = QWidget(self.body)
            widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
            layout = QVBoxLayout(widget)
            layout.setContentsMargins(0, 3, 0, 3)
            toggle = TransparentPushButton("执行中…", widget)
            toggle.setStyleSheet("QPushButton {text-align:left;border:none;background:transparent;color:#7a8592;padding:3px;}")
            hover_feedback(toggle)
            detail = BodyLabel("", widget)
            detail.setWordWrap(True)
            detail.show()
            toggle.clicked.connect(lambda: detail.setVisible(not detail.isVisible()))
            layout.addWidget(toggle)
            layout.addWidget(detail)
            state = {"toggle": toggle, "detail": detail, "lines": [], "start": time.monotonic(), "status": "running", "elapsed": None}
            self.activities[key] = state
            timer = QTimer(widget)
            state["timer"] = timer
            timer.timeout.connect(lambda: self._activity_label(state))
            timer.start(1000)
            self.items.insertWidget(self.items.count() - 1, widget)
        state = self.activities[key]
        previous_status = state["status"]
        if previous_status != "running" and status == "running":
            return  # A delayed tool event cannot reopen a completed message/run.
        if status != "running" and elapsed is None:
            elapsed = time.monotonic() - state["start"]
        if text and (not state["lines"] or state["lines"][-1] != text):
            state["lines"].append(text)
        state.update(status=status, elapsed=elapsed)
        state["detail"].setText("\n".join(state["lines"]))
        if status != "running" and previous_status == "running":
            state["detail"].hide()
        if status != "running":
            state["timer"].stop()
        self._activity_label(state)

    def _activity_label(self, state):
        seconds = state["elapsed"] if state["elapsed"] is not None else time.monotonic() - state["start"]
        label = "执行中" if state["status"] == "running" else {"failed":"执行失败", "interrupted":"已中断", "cancelled":"已取消"}.get(state["status"], "已完成")
        current = state["lines"][-1] if state["lines"] and state["status"] == "running" else label
        state["toggle"].setText(f"{current} · {seconds:.1f} 秒  ﹀")


class LiveReply(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.current_text, self.status = "", "queued"
        self.started, self.elapsed, self.frame = time.monotonic(), None, 0
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 6, 0, 6)
        box.setSpacing(8)
        self.badge = CaptionLabel(self)
        self.badge.setStyleSheet("color:#2675bf;font:12px 'Microsoft YaHei';")
        box.addWidget(self.badge)
        self.view = MarkdownView("", self)
        self.view.hide()
        box.addWidget(self.view)
        row = QHBoxLayout()
        self.copy_button = TransparentToolButton(FIF.COPY, self)
        self.copy_button.setFixedSize(28, 26)
        self.copy_button.setToolTip("复制回复")
        hover_feedback(self.copy_button)
        self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.current_text))
        row.addWidget(self.copy_button)
        row.addStretch()
        box.addLayout(row)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.timer.start(180)

    def update_reply(self, text, status, elapsed=None):
        if self.status in ("succeeded", "failed", "interrupted") and status in ("queued", "running"):
            return
        self.status, self.elapsed = status, elapsed
        if text != self.current_text:
            self.current_text = text
            self.view.setMarkdown(text)
            self.view.setVisible(bool(text))
            self.view.fit()
        self.copy_button.setVisible(bool(text) and status not in ("queued", "running"))
        if status in ("queued", "running"):
            if not self.timer.isActive():
                self.timer.start(180)
        else:
            self.timer.stop()
        self.animate()

    def animate(self):
        self.frame += 1
        if self.status in ("queued", "running"):
            action = "等待处理" if self.status == "queued" else ("正在输出" if self.current_text else "正在处理")
            dots = ("·", "··", "···")[self.frame % 3]
            self.badge.setText(f"FTTH  {dots}  {action}  ·  {time.monotonic() - self.started:.1f} 秒")
        else:
            label = {"succeeded":"回复完成", "failed":"回复未完成", "interrupted":"回复已中断"}.get(self.status, self.status)
            duration = f" · {self.elapsed:.1f} 秒" if self.elapsed is not None else ""
            self.badge.setText("FTTH  ·  " + label + duration)
            self.badge.setStyleSheet("color:#778595;font:12px 'Microsoft YaHei';")


def button(text, callback, primary=False, icon=None):
    cls = PushButton
    b = cls(icon, text) if icon else cls(text)
    b.setMinimumHeight(34)
    b.clicked.connect(callback)
    return b


def hover_feedback(control):
    control.setCursor(Qt.PointingHandCursor)
    # Append after the component's base rules, including dynamically created items.
    control.setStyleSheet(control.styleSheet() + "\n"
        "QPushButton:hover:enabled, QToolButton:hover:enabled, QCheckBox:hover:enabled {background:#F0F2F4;border-radius:6px;}"
        "QPushButton:pressed:enabled, QToolButton:pressed:enabled, QCheckBox:pressed:enabled {background:#E3E7EB;border-radius:6px;}")


def card(parent, title, description=""):
    c = CardWidget(parent)
    layout = QVBoxLayout(c)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(12)
    layout.addWidget(SubtitleLabel(title, c))
    return c, layout


def scroll_page():
    area = ScrollArea()
    area.setWidgetResizable(True)
    area.setStyleSheet("QScrollArea {border:none;background:transparent;}")
    page = QWidget()
    white_surface(area.viewport())
    white_surface(page)
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 5, 0)
    layout.setSpacing(12)
    area.setWidget(page)
    return area, page, layout


class StatusLabel(CaptionLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.hide()

    def setText(self, text):
        super().setText(text)
        self.setVisible(bool(text) and not text.startswith("后端已连接"))


def white_surface(widget):
    palette = widget.palette()
    palette.setColor(QPalette.Window, QColor("#FFFFFF"))
    palette.setColor(QPalette.Base, QColor("#FFFFFF"))
    widget.setPalette(palette)
    widget.setAutoFillBackground(True)


def build_ui(p, business_layers):
    p.setObjectName("FtthFluentPanel")
    white_surface(p)
    p.setAttribute(Qt.WA_StyledBackground, True)
    p.setStyleSheet("QWidget#FtthFluentPanel {background:#FFFFFF;} QLabel {background:transparent;}")
    p.root = QVBoxLayout(p)
    p.root.setContentsMargins(14, 10, 14, 10)
    p.root.setSpacing(8)
    heading = QHBoxLayout()
    p.back_button = TransparentToolButton(FIF.LEFT_ARROW, p)
    p.back_button.setFixedSize(28, 28)
    p.back_button.clicked.connect(p.back_to_chat)
    p.back_button.hide()
    heading.addWidget(p.back_button)
    p.title_button = TransparentPushButton("Chats", p)
    p.title_button.setStyleSheet("QPushButton {text-align:left;background:transparent;border:none;color:#56616f;}")
    p.title_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    p.title_button.clicked.connect(p.home)
    heading.addWidget(p.title_button, 1)
    p.tabs = Pages(p)
    p.tabs.nav.hide()
    p.page_buttons = []
    for index, label in ((1, "数据"), (2, "运行")):
        b = TransparentPushButton(label, p)
        b.setFixedHeight(30)
        b.clicked.connect(lambda checked=False, i=index: p.tabs.setCurrentIndex(i))
        heading.addWidget(b)
        p.page_buttons.append((index, b))
    settings = TransparentToolButton(FIF.SETTING, p)
    settings.setFixedSize(30, 30)
    settings.setToolTip("设置")
    settings.clicked.connect(lambda: p.tabs.setCurrentIndex(3))
    heading.addWidget(settings)
    new_chat = TransparentToolButton(FIF.EDIT, p)
    new_chat.setFixedSize(28, 28)
    new_chat.setToolTip("新对话")
    new_chat.clicked.connect(p.home)
    heading.addWidget(new_chat)
    def mark_page(index):
        p.back_button.setVisible(index != 0 or bool(p.session))
        for i, b in p.page_buttons:
            b.setStyleSheet("QPushButton {background:#FFFFFF;border:none;border-radius:6px;padding:4px;color:" + ("#243247;font-weight:600;" if i == index else "#667687;") + "} QPushButton:hover {background:#FFFFFF;}")
            hover_feedback(b)
    p.tabs.stack.currentChanged.connect(mark_page)
    p.root.addLayout(heading)
    p.root.addWidget(p.tabs, 1)

    p.chat = QWidget()
    chat = QVBoxLayout(p.chat)
    chat.setContentsMargins(0, 0, 0, 0)
    chat.setSpacing(10)
    p.project_label = CaptionLabel("", p.chat)
    p.project_label.hide()
    p.recent = QWidget(p.chat)
    p.recent_layout = QVBoxLayout(p.recent)
    p.recent_layout.setContentsMargins(0, 0, 0, 0)
    p.recent_layout.setSpacing(2)
    chat.addWidget(p.recent)
    p.transcript = Conversation(p.chat)
    chat.addWidget(p.transcript, 1)
    composer = QWidget(p.chat)
    composer.setObjectName("composer")
    composer.setStyleSheet("QWidget#composer {background:#FFFFFF;border:1px solid #e1e6ed;border-radius:20px;}")
    box = QVBoxLayout(composer)
    box.setContentsMargins(12, 8, 12, 8)
    box.setSpacing(0)
    p.message = Composer(composer)
    p.message.setFixedHeight(64)
    p.message.setStyleSheet("QTextEdit {background:transparent;border:none;padding:3px;color:#243247;font:14px 'Microsoft YaHei';}")
    p.message.setPlaceholderText("描述你的需求…")
    p.message.returnPressed.connect(p.send)
    box.addWidget(p.message)
    row = QHBoxLayout()
    attach = TransparentToolButton(FIF.ADD, composer)
    attach.setFixedSize(30, 30)
    attach.setToolTip("添加项目数据")
    attach.clicked.connect(lambda: p.tabs.setCurrentIndex(1))
    row.addWidget(attach)
    p.action_mode = ComboBox(composer)
    p.action_mode.addItem("检查与讨论", userData=False)
    p.action_mode.addItem("允许执行", userData=True)
    p.action_mode.setToolTip("仅在允许执行模式下，本条消息可提交规划或导出。工程策略在数据页保存。")
    row.addWidget(p.action_mode)
    row.addStretch()
    p.send_button = ToolButton(FIF.UP, composer)
    p.send_button.clicked.connect(p.send)
    p.send_button.setFixedSize(32, 32)
    p.send_button.setMinimumHeight(32)
    p.send_button.setToolTip("发送 · Enter（Shift+Enter 换行）")
    row.addWidget(p.send_button)
    box.addLayout(row)
    chat.addWidget(composer)
    p.tabs.addTab(p.chat, "对话")

    area, page, layout = scroll_page()
    c, box = card(page, "项目数据", "登记后创建独立会话；当前图层有选中要素时仅保存所选部分。")
    box.addWidget(button("导入配置 JSON", p.import_config, True, FIF.FOLDER))
    p.sources, p.sublayers = {}, {}
    labels = {"IMB": "楼栋需求", "SITE": "局站", "PTECH": "技术点位", "INFRASTRUCTURE": "基础设施"}
    for name in business_layers:
        box.addWidget(BodyLabel(f"{labels.get(name, name)} · {name}", c))
        line = LineEdit(c)
        line.setPlaceholderText("选择数据文件，或使用当前 QGIS 图层")
        p.sources[name] = line
        box.addWidget(line)
        row = QHBoxLayout()
        sub = LineEdit(c)
        sub.setPlaceholderText("图层 / 工作表名（可选）")
        p.sublayers[name] = sub
        row.addWidget(sub, 1)
        row.addWidget(button("文件", lambda checked=False, n=name: p.browse(n)))
        row.addWidget(button("当前图层", lambda checked=False, n=name: p.current_layer(n)))
        box.addLayout(row)
    p.recognition = LineEdit(c)
    p.recognition.setPlaceholderText("可选：识别道路 GeoPackage")
    box.addWidget(p.recognition)
    box.addWidget(button("选择识别结果", p.browse_recognition))
    box.addWidget(button("登记数据 · 新建会话", p.register, True, FIF.ADD))
    box.addWidget(button("检查当前会话数据", lambda: p.api("GET", p.route("project"), callback=p.report)))
    layout.addWidget(c)
    c, box = card(page, "规划设置", "仅对手动运行生效；对话中请直接说明要修改的参数。")
    p.scope = ComboBox(c)
    p.scope.addItem("完整规划", userData="full")
    p.scope.addItem("仅分区", userData="partition")
    box.addWidget(p.scope)
    p.controls = {}
    for name, label, default in [("use_formal_infrastructure", "采用正式基础设施", False), ("use_recognition_roads", "采用识别道路", False), ("split_oversized_imbs", "同意拆分超过 48 端口的楼宇", False), ("ga_refine", "启用 GA 优化", True)]:
        control = CheckBox(label, c)
        control.setChecked(default)
        p.controls[name] = control
        box.addWidget(control)
    box.addWidget(button("保存本会话工程策略", p.save_policy))
    policy_hint = CaptionLabel("道路与拆分策略保存后供对话沿用；手动运行也会保存所选策略。", c)
    policy_hint.setWordWrap(True)
    box.addWidget(policy_hint)
    numeric = [("nro_max_cap", "NRO 容量", 2048, 1, 1000000), ("zpm_max_cap", "ZPM 容量", 576, 1, 1000000),
        ("formal_duct_capacity", "管道容量", 2048, 1, 1000000), ("ga_population", "GA 种群", 14, 4, 100), ("ga_generations", "GA 代数", 10, 1, 100),
        ("candidate_paths", "候选路径数", 5, 1, 20), ("spatial_weight", "空间权重", 1.0, 0, 10000), ("pipe_weight", "管线权重", 100.0, 0, 10000),
        ("pbo_offset_m", "PBO 偏移 / 米", 5.0, 0.1, 1000), ("water_cost_factor", "水域成本系数", 2.0, 0, 1000),
        ("ptech_max_spacing_m", "PTECH 间距 / 米", 200.0, 0.1, 100000), ("terminal_snap_distance_m", "端点吸附 / 米", 80.0, 0.1, 100000)]
    advanced = QWidget(c)
    form = QFormLayout(advanced)
    form.setContentsMargins(0, 8, 0, 8)
    form.setSpacing(12)
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    for name, label, default, low, high in numeric:
        control = SpinBox(advanced) if isinstance(default, int) else DoubleSpinBox(advanced)
        control.setRange(low, high)
        control.setValue(default)
        p.controls[name] = control
        form.addRow(label, control)
    toggle = CheckBox("展开高级参数", c)
    toggle.toggled.connect(advanced.setVisible)
    box.addWidget(toggle)
    box.addWidget(advanced)
    advanced.hide()
    row = QHBoxLayout()
    row.addWidget(button("校验参数", lambda: p.manual(False)))
    row.addWidget(button("运行新方案", lambda: p.manual(True), True, FIF.PLAY))
    box.addLayout(row)
    layout.addWidget(c)
    layout.addStretch()
    p.tabs.addTab(area, "数据")

    area, page, layout = scroll_page()
    c, box = card(page, "方案与运行", "选择批次，查看报告、回到地图或导出交付成果。")
    p.runs = ComboBox(c)
    box.addWidget(p.runs)
    for actions in [[("查看报告", p.run_report), ("显示此版", p.display), ("导出此版", p.export)], [("加载此版参数", p.load_parameters), ("取消任务", p.cancel)]]:
        row = QHBoxLayout()
        for label, callback in actions:
            row.addWidget(button(label, callback))
        box.addLayout(row)
    layout.addWidget(c)
    c, box = card(page, "方案比较", "比较两个成功批次的参数变化和实际指标。")
    p.compare_to = ComboBox(c)
    box.addWidget(p.compare_to)
    box.addWidget(button("与选中方案比较", p.compare))
    layout.addWidget(c)
    c, box = card(page, "详细报告")
    p.report_box = TextEdit(c)
    p.report_box.setReadOnly(True)
    p.report_box.setMinimumHeight(300)
    p.report_box.setPlaceholderText("选择一个批次查看报告。")
    box.addWidget(p.report_box)
    layout.addWidget(c, 1)
    p.tabs.addTab(area, "运行")

    area, page, layout = scroll_page()
    c, box = card(page, "连接设置", "连接本机规划服务。模型密钥由后端管理，无需填入插件。")
    box.addWidget(BodyLabel("服务地址", c))
    p.url = LineEdit(c)
    p.url.setText("http://127.0.0.1:8765")
    box.addWidget(p.url)
    box.addWidget(BodyLabel("本地连接令牌", c))
    p.token = LineEdit(c)
    p.token.setEchoMode(LineEdit.Password)
    p.token.setPlaceholderText("选择 connection.token 文件")
    token_path = Path(p.settings.value("token_path", "F:/PROJECTS/py/qgis/demo0/runtime/connection.token"))
    if token_path.exists():
        p.token.setText(token_path.read_text(encoding="utf-8").strip())
    box.addWidget(p.token)
    box.addWidget(button("选择令牌文件", p.choose_token, icon=FIF.FOLDER))
    box.addWidget(button("测试连接", lambda: p.api("GET", "/health", callback=p.report), True))
    layout.addWidget(c)
    layout.addStretch()
    p.tabs.addTab(area, "设置")
    p.status = StatusLabel(p)
    p.status.setWordWrap(True)
    p.root.addWidget(p.status)
    for control in p.findChildren(QAbstractButton):
        if isinstance(control, (PushButton, TransparentPushButton, ToolButton)):
            css = "QPushButton, QToolButton {background:#FFFFFF; color:#243247; border:none; border-radius:6px;} QPushButton:hover, QToolButton:hover, QPushButton:pressed, QToolButton:pressed {background:#FFFFFF;}"
            setCustomStyleSheet(control, css, css)
    for control in p.findChildren(LineEdit) + p.findChildren(ComboBox) + p.findChildren(TextEdit) + p.findChildren(SpinBox) + p.findChildren(DoubleSpinBox):
        css = "QLineEdit, QTextEdit, QAbstractSpinBox, QPushButton {background:#FFFFFF; color:#243247;}"
        setCustomStyleSheet(control, css, css)
    p.message.setStyleSheet("QTextEdit {background:#FFFFFF;border:none;padding:3px;color:#243247;font:14px 'Microsoft YaHei';}")
    mark_page(0)
    for control in p.findChildren(QAbstractButton):
        hover_feedback(control)
