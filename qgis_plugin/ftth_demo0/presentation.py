"""Compact Qt5 conversation primitives; no web engine or remote resources."""
import re
from qgis.PyQt.QtCore import Qt, QObject, QSizeF, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont, QFontMetricsF, QTextFormat, QTextObjectInterface, QPainter
from qgis.PyQt.QtWidgets import QLabel, QWidget, QVBoxLayout, QSizePolicy

CODE_OBJECT = QTextFormat.UserObject + 41
CODE_TEXT = QTextFormat.UserProperty + 41


class InlineCode(QObject, QTextObjectInterface):
    """Selectable inline object; the view expands its source when copying."""
    def font(self):
        font = QFont('Consolas')
        font.setPixelSize(12)
        return font

    def intrinsicSize(self, document, pos, fmt):
        text = fmt.property(CODE_TEXT) or ''
        extra = 18 if re.fullmatch(r'#[0-9a-fA-F]{6}', text) else 0
        return QSizeF(QFontMetricsF(self.font()).horizontalAdvance(text) + 10 + extra, 20)

    def drawObject(self, painter, rect, document, pos, fmt):
        text = fmt.property(CODE_TEXT) or ''
        swatch = bool(re.fullmatch(r'#[0-9a-fA-F]{6}', text))
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        chip = rect.adjusted(0, 2, -18 if swatch else 0, -1)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#ededed'))
        painter.drawRoundedRect(chip, 4, 4)
        painter.setFont(self.font())
        painter.setPen(QColor('#303336'))
        painter.drawText(chip.adjusted(5, 0, -5, 0), Qt.AlignVCenter | Qt.AlignLeft, text)
        if swatch:
            color_rect = rect.adjusted(rect.width() - 13, 5, -1, -3)
            painter.setPen(QColor('#d8dadd'))
            painter.setBrush(QColor(text))
            painter.drawRoundedRect(color_rect, 3, 3)
        painter.restore()


class ElidedLabel(QLabel):
    clicked = pyqtSignal()

    def __init__(self, text='', parent=None):
        super().__init__(parent)
        self.full_text = ''
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setFixedHeight(25)
        self.setStyleSheet('color:#8b8d91;font:12px "Microsoft YaHei";background:transparent;border:0;')
        self.setCursor(Qt.PointingHandCursor)
        self.setText(text)

    def setText(self, text):
        self.full_text = text
        self.setToolTip(text)
        self.setAccessibleName(text)
        self.refresh()

    def text(self):
        return self.full_text

    def refresh(self):
        super().setText(self.fontMetrics().elidedText(self.full_text, Qt.ElideRight, max(20, self.width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class ActivityDetails(QWidget):
    """Single-line operations; click one to reveal its full selectable text."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.content = ''
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(24, 2, 0, 6)
        self.box.setSpacing(0)

    def text(self):
        return self.content

    def setText(self, text):
        self.content = text
        lines = [line for line in text.splitlines() if line.strip()]
        # Append-only progress preserves expansion and selection of existing rows.
        for index, line in enumerate(lines):
            if index < len(self.rows):
                continue
            compact = ElidedLabel('›  ' + line, self)
            full = QLabel(line, self)
            full.setWordWrap(True)
            full.setTextFormat(Qt.PlainText)
            full.setTextInteractionFlags(Qt.TextSelectableByMouse)
            full.setStyleSheet('color:#777b80;font:12px "Microsoft YaHei";padding:3px 0 8px 12px;border:0;')
            full.hide()
            compact.clicked.connect(lambda label=full: label.setVisible(label.isHidden()))
            self.box.addWidget(compact)
            self.box.addWidget(full)
            self.rows.append((compact, full))


def duration(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 60} 分 {seconds % 60} 秒' if seconds >= 60 else f'{seconds} 秒'
