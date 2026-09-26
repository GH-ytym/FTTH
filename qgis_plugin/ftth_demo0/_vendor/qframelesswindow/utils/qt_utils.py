# coding:utf-8
"""FTTH patch: pure-Qt fallback for hosts without the native helpers.

A signed QGIS.app refuses third-party compiled extensions such as PyObjC, and
distribution QGIS builds often lack xcffib. The plugin is a dock widget, so it
needs these names to import, not native frameless-window behaviour.
"""
from PyQt5.QtCore import QObject
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QApplication, QWidget


class QtMoveResize:
    """ Window move/resize through Qt's own platform integration (Qt 5.15+) """

    @staticmethod
    def startSystemMove(window, globalPos):
        handle = window.windowHandle()
        if handle is not None:
            handle.startSystemMove()

    @staticmethod
    def starSystemResize(window, globalPos, edges):
        handle = window.windowHandle()
        if handle is not None and edges:
            handle.startSystemResize(edges)


def getSystemAccentColor():
    """ The platform palette's highlight colour stands in for the accent colour """
    if QApplication.instance() is None:
        return QColor(0, 120, 212)
    return QApplication.palette().highlight().color()


class QtScreenCaptureFilter(QObject):
    """ Screen-capture exclusion is unavailable without native APIs; no-op """

    def __init__(self, parent: QWidget):
        super().__init__(parent)

    def setScreenCaptureEnabled(self, enabled: bool):
        pass
