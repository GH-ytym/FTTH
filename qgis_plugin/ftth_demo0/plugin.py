from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QAction, QDockWidget
from qgis.PyQt.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton
from qgis.PyQt.QtCore import QSettings


class Plugin:
    def __init__(self, iface):
        self.iface, self.dock = iface, None

    def initGui(self):
        self.action = QAction("FTTH Agent demo0", self.iface.mainWindow())
        self.action.triggered.connect(self.show)
        self.iface.addPluginToMenu("FTTH demo0", self.action)
        self.iface.addToolBarIcon(self.action)
        self.legacy_action = QAction('FTTH：切换旧版界面', self.iface.mainWindow())
        self.legacy_action.triggered.connect(self.toggle_legacy)
        self.iface.addPluginToMenu('FTTH demo0', self.legacy_action)

    def show(self):
        if self.dock is None:
            self.dock = QDockWidget("FTTH Agent demo0", self.iface.mainWindow())
            self.dock.setObjectName("FtthDemo0Dock")
            self.dock.setMinimumWidth(360)
            if QSettings('FTTH','demo0').value('legacy_ui', False, type=bool):
                from .panel import Panel
                self.panel = Panel(self.iface)
            else:
                try:
                    from .web_panel import WebPanel
                    self.panel = WebPanel(self.iface)
                except ImportError:
                    self.panel = QWidget()
                    layout = QVBoxLayout(self.panel)
                    label = QLabel('缺少 QtWebEngine。请通过 OSGeo4W 安装 python3-pyqtwebengine，或切换旧版界面。')
                    label.setWordWrap(True);layout.addWidget(label)
                    button = QPushButton('使用旧版界面');button.clicked.connect(self.toggle_legacy);layout.addWidget(button)
                    self.panel.shutdown = lambda: None
            self.dock.setWidget(self.panel)
            self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.dock.show()
        self.dock.raise_()

    def toggle_legacy(self):
        settings=QSettings('FTTH','demo0')
        settings.setValue('legacy_ui',not settings.value('legacy_ui',False,type=bool))
        if self.dock:
            self.panel.shutdown();self.iface.removeDockWidget(self.dock);self.dock.deleteLater();self.dock=None
        self.show()

    def unload(self):
        if self.dock:
            self.panel.shutdown()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
        self.iface.removePluginMenu("FTTH demo0", self.action)
        self.iface.removeToolBarIcon(self.action)
        self.action.deleteLater()
        self.iface.removePluginMenu('FTTH demo0', self.legacy_action)
        self.legacy_action.deleteLater()
