"""QGIS menu and toolbar integration."""

from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QDockWidget

from .dialog import FtthDataContractDialog


class FtthDataContractPlugin:
    MENU = "&FTTH 数据契约"

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = Path(__file__).resolve().parent
        self.action = None
        self.dialog = None
        self.dock = None

    def initGui(self):
        self.action = QAction(
            QIcon(str(self.plugin_dir / "icon.svg")),
            "FTTH 连续规划智能体",
            self.iface.mainWindow(),
        )
        self.action.setObjectName("FtthDataContractAction")
        self.action.setToolTip("数据输入、AI参数辅助、遥感环境感知、连续规划与成果校验")
        self.action.setCheckable(True)
        self.action.triggered.connect(self.show_dialog)
        self.iface.addPluginToMenu(self.MENU, self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action is not None:
            self.iface.removePluginMenu(self.MENU, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()
            self.action = None
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.close()
            self.dock.deleteLater()
            self.dock = None
            self.dialog = None

    def show_dialog(self, checked=False):
        """Show a persistent planning sidebar instead of a blocking popup."""
        if self.dock is None:
            self.dock = QDockWidget("FTTH 连续规划智能体", self.iface.mainWindow())
            self.dock.setObjectName("FtthContinuousPlanningDock")
            self.dock.setAllowedAreas(
                Qt.DockWidgetArea.LeftDockWidgetArea
                | Qt.DockWidgetArea.RightDockWidgetArea
            )
            self.dock.setMinimumWidth(470)
            self.dialog = FtthDataContractDialog(self.iface, self.plugin_dir)
            self.dialog.setWindowFlags(Qt.WindowType.Widget)
            self.dock.setWidget(self.dialog)
            self.dock.visibilityChanged.connect(self.action.setChecked)
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        if checked is False and self.dock.isVisible():
            self.dock.hide()
        else:
            self.dock.show()
            self.dock.raise_()
