# coding:utf-8
import sys

if sys.platform == "win32":
    from .win32_utils import WindowsMoveResize as MoveResize
    from .win32_utils import getSystemAccentColor
    from .win32_utils import WindowsScreenCaptureFilter as ScreenCaptureFilter
elif sys.platform == "darwin":
    try:
        from .mac_utils import MacMoveResize as MoveResize
        from .mac_utils import getSystemAccentColor
        from .mac_utils import MacScreenCaptureFilter as ScreenCaptureFilter
    except ImportError:  # FTTH patch: PyObjC missing or refused (signed QGIS.app)
        from .qt_utils import QtMoveResize as MoveResize
        from .qt_utils import getSystemAccentColor
        from .qt_utils import QtScreenCaptureFilter as ScreenCaptureFilter
else:
    try:
        from .linux_utils import LinuxMoveResize as MoveResize
        from .linux_utils import getSystemAccentColor
        from .linux_utils import LinuxScreenCaptureFilter as ScreenCaptureFilter
    except ImportError:  # FTTH patch: xcffib / QtX11Extras unavailable
        from .qt_utils import QtMoveResize as MoveResize
        from .qt_utils import getSystemAccentColor
        from .qt_utils import QtScreenCaptureFilter as ScreenCaptureFilter


def startSystemMove(window, globalPos):
    """ resize window

    Parameters
    ----------
    window: QWidget
        window

    globalPos: QPoint
        the global point of mouse release event
    """
    MoveResize.startSystemMove(window, globalPos)


def starSystemResize(window, globalPos, edges):
    """ resize window

    Parameters
    ----------
    window: QWidget
        window

    globalPos: QPoint
        the global point of mouse release event

    edges: `Qt.Edges`
        window edges
    """
    MoveResize.starSystemResize(window, globalPos, edges)
