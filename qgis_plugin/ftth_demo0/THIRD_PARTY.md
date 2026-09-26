# Fluent UI components

The plugin bundles the Python sources and resources of:

| Package | Version | License | Local changes |
|---|---|---|---|
| PyQt-Fluent-Widgets | 1.11.3 | GPLv3 | none |
| PyQt5-Frameless-Window | 0.8.2 | GPLv3 | pure-Qt fallback, see below |
| darkdetect | 0.8.0 | BSD-3-Clause | one exception clause, see below |

Original licenses and package metadata are included in `_vendor/*.dist-info`.
Source: https://github.com/zhiyiYo/PyQt-Fluent-Widgets and
https://github.com/zhiyiYo/PyQt-Frameless-Window .

## Local modifications (marked `FTTH patch` in the source)

A signed QGIS.app on macOS refuses third-party compiled extensions such as
PyObjC, and distribution QGIS builds on Linux often lack xcffib. Upstream
imports those unconditionally, so the plugin could not load there. The plugin is
a dock widget and needs these modules to import, not native frameless windows.

- `qframelesswindow/utils/qt_utils.py` (new): window move/resize through Qt
  5.15's `QWindow.startSystemMove/startSystemResize`, the palette highlight as
  accent colour, and a no-op screen-capture filter.
- `qframelesswindow/utils/__init__.py`: on macOS and Linux, fall back to
  `qt_utils` when the native helper module raises `ImportError`.
- `qframelesswindow/__init__.py`: on macOS without PyObjC, use the generic
  Qt frameless window and no-op window effect (the Linux classes, which only
  depend on Qt once the move/resize helper is platform-selected).
- `qframelesswindow/linux/__init__.py`: take `MoveResize` from `..utils`
  (the platform selection) instead of importing `linux_utils` directly.
- `darkdetect/_mac_detect.py`: catch `ImportError` rather than only
  `ModuleNotFoundError`, so a PyObjC refused by library validation disables the
  optional theme listener instead of failing the import.

Windows code paths are unchanged.

## Runtime

The Qt/PyQt5 runtime comes from QGIS, not this ZIP. The plugin targets QGIS 3 /
Qt5; it is verified on Windows (OSGeo4W, with pywin32) and macOS (QGIS.app).
Do not install the Qt6 or PySide edition into the same runtime.
SciPy and the optional acrylic/full extras are not required or bundled.

Distribution of this bundled edition must comply with the included GPLv3 terms.

To reproduce the upstream pure-Python dependencies from the project root, then
re-apply the local modifications above:

```shell
uv pip install --target qgis_plugin/ftth_demo0/_vendor --no-deps PyQt-Fluent-Widgets==1.11.3 PyQt5-Frameless-Window==0.8.2 darkdetect==0.8.0
```
