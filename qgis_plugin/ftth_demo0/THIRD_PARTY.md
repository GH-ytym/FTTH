# Fluent UI components

The plugin bundles the unmodified Python sources and resources of:

| Package | Version | License |
|---|---|---|
| PyQt-Fluent-Widgets | 1.11.3 | GPLv3 |
| PyQt5-Frameless-Window | 0.8.2 | GPLv3 |
| darkdetect | 0.8.0 | BSD-3-Clause |

Original licenses and package metadata are included in `_vendor/*.dist-info`.
Source: https://github.com/zhiyiYo/PyQt-Fluent-Widgets and
https://github.com/zhiyiYo/PyQt-Frameless-Window .

The Qt/PyQt5 runtime and Windows pywin32 support come from the tested OSGeo4W
installation, not this ZIP. This plugin targets QGIS 3 / Qt5 on Windows.
Do not install the Qt6 or PySide edition into the same runtime.
SciPy and the optional acrylic/full extras are not required or bundled.

Distribution of this bundled edition must comply with the included GPLv3 terms.

To reproduce the bundled pure-Python dependencies from the project root:

```powershell
uv pip install --python .venv/Scripts/python.exe --target qgis_plugin/ftth_demo0/_vendor --no-deps PyQt-Fluent-Widgets==1.11.3 PyQt5-Frameless-Window==0.8.2 darkdetect==0.8.0
```
