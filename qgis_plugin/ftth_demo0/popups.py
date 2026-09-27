"""Qt5 popups anchored to the composer and recent-chat control."""
from pathlib import Path
from datetime import datetime
from qgis.PyQt.QtCore import Qt, QPoint
from qgis.PyQt.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QScrollArea, QWidget, QListWidget, QListWidgetItem
from .fluent_ui import LineEdit, BodyLabel, button


class SaveStatus(BodyLabel):
    """Keep save feedback available without a permanent footer."""
    def setText(self, text):
        super().setText(text)
        self.setVisible('失败' in text or '不能' in text or '未发送' in text)


class Popup(QFrame):
    def __init__(self, parent, on_close=None):
        super().__init__(parent, Qt.Popup)
        self.on_close = on_close
        self.setObjectName('anchoredPopup')
        self.setStyleSheet('QFrame#anchoredPopup {background:#ffffff;border:1px solid #e1e6ed;border-radius:10px;}')

    def present(self, anchor, above=False):
        self.setFixedWidth(max(280, min(480, self.parentWidget().width() - 20)))
        screen = anchor.screen().availableGeometry()
        self.setMaximumHeight(min(560, screen.height() - 30))
        self.adjustSize()
        p = anchor.mapToGlobal(QPoint(0, 0 if above else anchor.height()))
        x = max(screen.left(), min(p.x(), screen.right() - self.width()))
        y = p.y() - self.height() - 6 if above else p.y() + 6
        y = max(screen.top(), min(y, screen.bottom() - self.height()))
        self.move(x, y)
        self.show()

    def hideEvent(self, event):
        super().hideEvent(event)
        if self.on_close:
            self.on_close()


class DataPopup(Popup):
    def __init__(self, panel, names):
        super().__init__(panel, panel.flush_data)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        scroll = QScrollArea(self)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet('QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {background:#ffffff;border:0;}')
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(430)
        body = QWidget(scroll)
        body.setStyleSheet('QWidget {background:#ffffff;} QLabel {border:0;padding:0;color:#47515c;}')
        layout = QVBoxLayout(body)
        layout.setContentsMargins(2, 2, 8, 4)
        layout.setSpacing(9)
        panel.sources, panel.sublayers = {}, {}
        for name in names:
            if panel.sources:
                layout.addSpacing(12)
            layout.addWidget(BodyLabel(name, body))
            field = LineEdit(body)
            field.setPlaceholderText('选择文件')
            panel.sources[name] = field
            layout.addWidget(field)
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(button('选择', lambda checked=False, n=name: panel.browse(n)))
            row.addWidget(button('当前图层', lambda checked=False, n=name: panel.current_layer(n)))
            row.addWidget(button('清除', lambda checked=False, n=name: panel.clear_source(n)))
            layout.addLayout(row)
            sub = LineEdit(body)
            sub.setPlaceholderText('图层 / 工作表名（可选）')
            sub.hide()
            panel.sublayers[name] = sub
            layout.addWidget(sub)
            field.textChanged.connect(lambda text, s=sub, f=field: self.file_changed(text, s, f))
        layout.addSpacing(12)
        layout.addWidget(BodyLabel('道路 GPKG', body))
        panel.recognition = LineEdit(body)
        panel.recognition.setPlaceholderText('选择文件')
        layout.addWidget(panel.recognition)
        row = QHBoxLayout()
        row.addWidget(button('选择', panel.browse_recognition))
        row.addWidget(button('清除', lambda: panel.clear_source('roads')))
        layout.addLayout(row)
        panel.data_status = SaveStatus('', body)
        panel.data_status.hide()
        panel.data_status.setWordWrap(True)
        layout.addWidget(panel.data_status)
        scroll.setWidget(body)
        outer.addWidget(scroll)

    @staticmethod
    def file_changed(text, sub, field):
        sub.setVisible(Path(text).suffix.lower() in {'.gpkg', '.sqlite', '.xlsx', '.xls'})
        field.setToolTip(text)
        field.setCursorPosition(len(text))


class HistoryPopup(Popup):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        layout = QVBoxLayout(self)
        self.search = LineEdit(self)
        self.search.setPlaceholderText('搜索最近对话')
        layout.addWidget(self.search)
        self.items = QListWidget(self)
        self.items.setMinimumHeight(260)
        layout.addWidget(self.items)
        self.search.textChanged.connect(self.refresh)
        self.items.itemClicked.connect(self.selected)
        self.signature = None

    def refresh(self):
        query = self.search.text().casefold()
        rows = [s for s in self.panel.sessions_cache if query in s.get('title', '新对话').casefold()]
        signature = [(s['id'], s.get('title'), s.get('updated')) for s in rows]
        if signature == self.signature:
            return
        self.signature = signature
        position = self.items.verticalScrollBar().value()
        self.items.clear()
        for session in rows:
            stamp = datetime.fromtimestamp(session.get('updated', session.get('created', 0))).strftime('%m-%d %H:%M')
            item = QListWidgetItem(session.get('title', '新对话') + '    ' + stamp)
            item.setData(Qt.UserRole, session)
            self.items.addItem(item)
        self.items.verticalScrollBar().setValue(position)

    def selected(self, item):
        session = item.data(Qt.UserRole)
        self.hide()
        self.panel.open_session(session)
