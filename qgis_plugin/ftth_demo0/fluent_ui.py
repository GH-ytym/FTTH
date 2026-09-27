"""Fluent presentation layer, embedded in the existing QGIS dock."""
import sys
import time
from pathlib import Path

from . import connection

# Use QGIS's own PyQt5. Never ship or replace its Qt runtime.
vendor = str(Path(__file__).parent / "_vendor")
if vendor not in sys.path:
    sys.path.append(vendor)

from qgis.PyQt.QtCore import Qt, pyqtSignal, QTimer
from qgis.PyQt.QtGui import QTextTable, QTextLength, QColor, QPalette, QIcon, QTextBlockFormat
from qgis.PyQt.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget, QTextBrowser, QSizePolicy, QApplication, QAbstractButton)
from qfluentwidgets import (PushButton, TransparentPushButton,
    TransparentToolButton, ToolButton, setCustomStyleSheet,
    LineEdit, TextEdit, ComboBox, SpinBox, DoubleSpinBox, CardWidget,
    ScrollArea, BodyLabel, CaptionLabel, SubtitleLabel, SegmentedWidget,
    FluentIcon as FIF, IndeterminateProgressRing, IconWidget)


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
        self.setStyleSheet("background:transparent; border:none; color:#47515c; font:13px 'Microsoft YaHei'; selection-background-color:#dbeafe;")
        self.document().setDocumentMargin(2)
        self.document().setDefaultStyleSheet("table {border-collapse:collapse;} th,td {padding:6px;border:1px solid #dce3eb;} pre,code {background:#e9edf1; font-family:Consolas;} h1,h2,h3 {color:#303a45;} a {color:#1674ce;text-decoration:none;} strong {font-weight:600;}")
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

    def setMarkdown(self, text):
        super().setMarkdown(text)
        # QTextDocument's Markdown importer does not apply paragraph CSS reliably.
        block = self.document().begin()
        while block.isValid():
            cursor = self.textCursor()
            cursor.setPosition(block.position())
            fmt = block.blockFormat()
            fmt.setLineHeight(155, QTextBlockFormat.ProportionalHeight)
            fmt.setBottomMargin(5 if block.textList() else 9)
            cursor.setBlockFormat(fmt)
            fragment = block.begin()
            while not fragment.atEnd():
                part = fragment.fragment()
                style = part.charFormat()
                font = style.font()
                if font.fixedPitch() or any(name in font.family().lower() for name in ('mono', 'consolas', 'courier')):
                    style.setFontFamily('Consolas')
                    style.setFontPointSize(9)
                    style.setBackground(QColor('#e9edf1'))
                    cursor.setPosition(part.position())
                    cursor.setPosition(part.position() + part.length(), cursor.KeepAnchor)
                    cursor.setCharFormat(style)
                fragment += 1
            block = block.next()
        for frame in self.document().rootFrame().childFrames():
            if isinstance(frame, QTextTable):
                fmt = frame.format()
                fmt.setBorderCollapse(True)
                fmt.setBorder(.5)
                fmt.setBorderBrush(QColor('#dce3eb'))
                fmt.setCellPadding(6)
                fmt.setCellSpacing(0)
                fmt.setWidth(QTextLength(QTextLength.PercentageLength, 100))
                frame.setFormat(fmt)

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
        self.workflow_cards = {}
        self.workflow_versions = {}
        self.workflow_messages = set()
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
        if message_id.startswith(('review_report_', 'completion_')):
            self.replies[message_id].badge.setText('FTTH · 结果总结')
        if at_bottom and not message_id.startswith(("completion_", "review_report_", "check_")):
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
        self.workflow_cards.clear()
        self.workflow_versions.clear()
        self.workflow_messages.clear()
        for i in reversed(range(self.items.count())):
            item = self.items.itemAt(i)
            widget = item.widget()
            if widget and widget is not self.empty:
                self.items.takeAt(i)
                widget.hide()
                widget.deleteLater()
        self.empty.show()

    def clear_workflow_card(self, key):
        old = self.workflow_cards.pop(key, None)
        if old:
            self.items.removeWidget(old)
            old.hide()
            old.deleteLater()

    def workflow_card(self, workflow, choose, cancel):
        self.empty.hide()
        bar = self.verticalScrollBar()
        at_bottom = bar.maximum() - bar.value() < 60
        key = workflow['id']
        self.clear_workflow_card(key)
        if workflow['status'] in {'completed', 'cancelled', 'failed', 'interrupted'}:
            return
        card = QWidget(self.body)
        box = QVBoxLayout(card)
        box.setContentsMargins(8, 4, 8, 4)
        label = BodyLabel(workflow.get('detail', '') if workflow['status'] in {'waiting_choice', 'blocked'} else '', card)
        label.setWordWrap(True)
        box.addWidget(label)
        label.setVisible(bool(label.text()))
        for option in workflow.get('options', []):
            action = PushButton(option['label'], card)
            action.clicked.connect(lambda checked=False, oid=option['id']: choose(oid))
            box.addWidget(action)
        if workflow['status'] not in {'completed', 'cancelled', 'failed', 'interrupted'} and not workflow.get('options'):
            action = PushButton('取消工作流', card)
            action.clicked.connect(cancel)
            box.addWidget(action)
        self.workflow_cards[key] = card
        self.items.insertWidget(self.items.count() - 1, card)
        if at_bottom:
            self.scroll_timer.start(50)

    def activity(self, key, text, status="running", elapsed=None, role="main", resume=False):
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
            detail.hide()
            row = QHBoxLayout()
            icon = IconWidget(QIcon(str(Path(__file__).parent / "icons/review-shield.svg")) if role == "review" else FIF.ROBOT if role == "main" else FIF.SETTING, widget)
            icon.setFixedSize(18, 18)
            icon.setToolTip({"main": "主 Agent", "review": "独立审查 Agent", "algorithm": "算法执行器", "service": "服务端校验与调度"}.get(role, role))
            row.addWidget(icon)
            ring = IndeterminateProgressRing(widget)
            ring.setFixedSize(16, 16)
            ring.setStrokeWidth(2)
            row.addWidget(ring)
            row.addWidget(toggle, 1)
            layout.addLayout(row)
            layout.addWidget(detail)
            state = {"widget": widget, "toggle": toggle, "ring": ring, "icon": icon, "role": role, "detail": detail, "expanded": False, "lines": [], "start": time.monotonic(), "status": "running", "elapsed": None}
            toggle.clicked.connect(lambda: self._toggle_activity(state))
            self.activities[key] = state
            timer = QTimer(widget)
            state["timer"] = timer
            timer.timeout.connect(lambda: self._activity_label(state))
            timer.start(1000)
            self.items.insertWidget(self.items.count() - 1, widget)
        state = self.activities[key]
        previous_status = state["status"]
        if previous_status != "running" and status == "running":
            if not resume:
                return  # A delayed tool event cannot reopen a completed message/run.
            state['timer'].start(1000)
            state['ring'].show()
            state['ring'].start()
        if state["role"] != role:
            state["icon"].setIcon(QIcon(str(Path(__file__).parent / "icons/review-shield.svg")) if role == "review" else FIF.ROBOT if role == "main" else FIF.SETTING)
            state["icon"].setToolTip({"main": "主 Agent", "review": "独立审查 Agent", "algorithm": "算法执行器", "service": "服务端校验与调度"}.get(role, role))
            state["role"] = role
        if status != "running" and elapsed is None:
            elapsed = time.monotonic() - state["start"]
        if text and (not state["lines"] or state["lines"][-1] != text):
            state["lines"].append(text)
        state.update(status=status, elapsed=elapsed)
        state["detail"].setText("\n".join(state["lines"]))
        # Fluent labels can change visibility when their text changes. Keep the
        # user's explicit expansion state authoritative, including replay.
        state["detail"].setVisible(state["expanded"])
        if status != "running":
            state["timer"].stop()
            state["ring"].stop()
            state["ring"].hide()
        self._activity_label(state)

    def _toggle_activity(self, state):
        state['expanded'] = not state['expanded']
        state['detail'].setVisible(state['expanded'])

    def hide_activity(self, key):
        if key in self.activities:
            state = self.activities[key]
            state['timer'].stop()
            state['ring'].stop()
            state['widget'].hide()

    def _activity_label(self, state):
        seconds = state["elapsed"] if state["elapsed"] is not None else time.monotonic() - state["start"]
        label = "执行中" if state["status"] == "running" else {"failed":"执行失败", "interrupted":"已中断", "cancelled":"已取消"}.get(state["status"], "已完成")
        current = state["lines"][-1] if state["lines"] else label
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
    settings = TransparentToolButton(FIF.SETTING, p)
    settings.setFixedSize(30, 30)
    settings.setToolTip("设置")
    settings.clicked.connect(lambda: p.tabs.setCurrentIndex(2))
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
    composer.setStyleSheet("QWidget#composer {background:#FFFFFF;border:1px solid #dfe4e9;border-radius:20px;}")
    box = QVBoxLayout(composer)
    box.setContentsMargins(10, 10, 10, 9)
    box.setSpacing(7)
    p.message = Composer(composer)
    p.message.setFixedHeight(64)
    p.message.setStyleSheet("QTextEdit {background:transparent;border:none;padding:3px;color:#243247;font:14px 'Microsoft YaHei';}")
    p.message.setPlaceholderText("描述需求，或询问当前方案…")
    p.message.returnPressed.connect(p.send)
    box.addWidget(p.message)
    row = QHBoxLayout()
    attach = TransparentToolButton(FIF.ADD, composer)
    attach.setFixedSize(30, 30)
    attach.setToolTip("添加项目数据")
    p.attach_button = attach
    attach.clicked.connect(p.show_data)
    row.addWidget(attach)
    hint = CaptionLabel("项目数据", composer)
    hint.setStyleSheet("color:#77838f;font:11px 'Microsoft YaHei';")
    row.addWidget(hint)
    row.addStretch()
    p.send_button = ToolButton(FIF.UP, composer)
    p.send_button.clicked.connect(p.send)
    p.send_button.setFixedSize(32, 32)
    p.send_button.setMinimumHeight(32)
    p.send_button.setToolTip("发送 · Enter（Shift+Enter 换行）")
    setCustomStyleSheet(p.send_button,
        "ToolButton {background:#454f58;border:0;border-radius:16px;} ToolButton:hover {background:#303b45;}",
        "ToolButton {background:#454f58;border:0;border-radius:16px;}")
    p.send_button.setIcon(FIF.UP.icon(color=QColor('white')))
    row.addWidget(p.send_button)
    box.addLayout(row)
    chat.addWidget(composer)
    p.tabs.addTab(p.chat, "对话")

    from .popups import DataPopup, HistoryPopup
    p.data_popup = DataPopup(p, business_layers)
    p.history_popup = HistoryPopup(p)
    area, page, layout = scroll_page()
    p.result_title = BodyLabel("运行结果", page)
    layout.addWidget(p.result_title)
    row = QHBoxLayout()
    row.addWidget(button("查看报告", lambda: p.open_result_report(False)))
    row.addWidget(button("审查报告", lambda: p.open_result_report(True)))
    layout.addLayout(row)
    p.report_box = TextEdit(page)
    p.report_box.setReadOnly(True)
    layout.addWidget(p.report_box, 1)
    p.tabs.addTab(area, "结果")

    area, page, layout = scroll_page()
    c, box = card(page, "连接设置", "连接本机规划服务。模型密钥由后端管理，无需填入插件。")
    box.addWidget(BodyLabel("服务地址", c))
    # Saved choices first, then what `ftth plugin install` recorded for this machine.
    url, token_path = connection.resolve(p.settings.value("url", ""), p.settings.value("token_path", ""))
    p.url = LineEdit(c)
    p.url.setText(url)
    p.url.editingFinished.connect(lambda: p.settings.setValue("url", p.url.text().strip()))
    box.addWidget(p.url)
    box.addWidget(BodyLabel("本地连接令牌", c))
    p.token = LineEdit(c)
    p.token.setEchoMode(LineEdit.Password)
    p.token.setPlaceholderText("选择 connection.token 文件（`ftth serve` 启动时会显示路径）")
    p.token.setText(connection.read_token(token_path))
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
    p.message.setStyleSheet("QTextEdit {background:transparent;border:none;padding:3px;color:#47515c;font:13px 'Microsoft YaHei';}")
    mark_page(0)
    for control in p.findChildren(QAbstractButton):
        hover_feedback(control)
    send_css = ('QToolButton, QPushButton {background:#454f58;border:0;border-radius:16px;}'
                'QToolButton:hover:enabled, QPushButton:hover:enabled {background:#303b45;border-radius:16px;}'
                'QToolButton:pressed:enabled, QPushButton:pressed:enabled {background:#202a34;border-radius:16px;}')
    setCustomStyleSheet(p.send_button, send_css, send_css)
    p.send_button.setStyleSheet(send_css)
