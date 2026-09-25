from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QAction, QDockWidget
from .panel import Panel


class Plugin:
    def __init__(self, iface):
        self.iface, self.dock = iface, None

    def initGui(self):
        self.action = QAction("FTTH Agent demo0", self.iface.mainWindow())
        self.action.triggered.connect(self.show)
        self.iface.addPluginToMenu("FTTH demo0", self.action)
        self.iface.addToolBarIcon(self.action)

    def show(self):
        if self.dock is None:
            self.dock = QDockWidget("FTTH Agent demo0", self.iface.mainWindow())
            self.dock.setObjectName("FtthDemo0Dock")
            self.dock.setMinimumWidth(480)
            self.panel = Panel(self.iface)
            self.dock.setWidget(self.panel)
            self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.dock.show()
        self.dock.raise_()

    def unload(self):
        if self.dock:
            self.panel.shutdown()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
        self.iface.removePluginMenu("FTTH demo0", self.action)
        self.iface.removeToolBarIcon(self.action)
        self.action.deleteLater()
