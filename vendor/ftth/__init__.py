"""QGIS plugin entry point."""


def classFactory(iface):
    from .plugin import FtthDataContractPlugin

    return FtthDataContractPlugin(iface)
