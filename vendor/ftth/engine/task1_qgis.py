"""任务一的 QGIS 4.x 图层构建与可视化实现。"""

from __future__ import annotations

import csv
import gc
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List

from qgis.PyQt.QtCore import QMetaType
from qgis.PyQt.QtGui import QColor, QFont, QFontDatabase
from qgis.PyQt.QtWidgets import QApplication
from qgis.core import (
    NULL,
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsFillSymbol,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsSingleSymbolRenderer,
    QgsSymbol,
    QgsTextFormat,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
)
from qgis.gui import QgsMapCanvas

from task1_core import (
    convert_value,
    output_layer_name,
    publish_staged_bundle,
    resolve_path,
    row_matches_filter,
)

TYPE_MAP = {
    "string": QMetaType.Type.QString,
    "int": QMetaType.Type.Int,
    "float": QMetaType.Type.Double,
    "bool": QMetaType.Type.Bool,
}

DEFAULT_LAYER_STYLES: Dict[str, Dict[str, Any]] = {
    "IMB": {
        "color": "#000000",
        "shape": "square",
        "size": 2.4,
        "outline_color": "#FFFFFF",
        "outline_width": 0.4,
        "label_field": "CODE",
        "label_font": "Open Sans",
        "label_size": 10,
        "label_color": "#323232",
        "label_bold": True,
        "label_italic": True,
    },
    "INFRASTRUCTURE": {
        "color": "#E71340",
        "width": 0.66,
    },
    "PTECH": {
        "color": "#DB1E2A",
        "shape": "diamond",
        "size": 4.4,
        "outline_color": "#801119",
        "outline_width": 0.4,
        "label_field": "CODE",
        "label_font": "Open Sans",
        "label_size": 10,
        "label_color": "#323232",
        "label_bold": True,
        "label_italic": True,
    },
    "SITE": {
        "color": "#A47158",
        "shape": "circle",
        "size": 2.0,
        "outline_color": "#232323",
        "outline_width": 0.0,
    },
}


class QgisBuildError(RuntimeError):
    """QGIS 图层构建失败。"""


def _new_memory_layer(layer_cfg: Dict[str, Any]) -> QgsVectorLayer:
    uri = f"{layer_cfg['geometry_type']}?crs={layer_cfg.get('crs', 'EPSG:4326')}"
    layer = QgsVectorLayer(uri, output_layer_name(layer_cfg), "memory")
    if not layer.isValid():
        raise QgisBuildError(f"无法创建内存图层：{output_layer_name(layer_cfg)}")

    fields = [
        QgsField(str(field["name"]), TYPE_MAP[field["type"]])
        for field in layer_cfg["fields"]
    ]
    provider = layer.dataProvider()
    if not provider.addAttributes(fields):
        raise QgisBuildError(f"无法添加字段：{output_layer_name(layer_cfg)}")
    layer.updateFields()
    return layer


def _attributes(row: Dict[str, Any], layer_cfg: Dict[str, Any]) -> List[Any]:
    values: List[Any] = []
    for field in layer_cfg["fields"]:
        try:
            source_name = field.get("source_field", field["name"])
            values.append(convert_value(row.get(source_name), field["type"]))
        except (TypeError, ValueError) as exc:
            raise QgisBuildError(
                f"图层 {output_layer_name(layer_cfg)} 字段 {field['name']} 转换失败：{exc}"
            ) from exc
    return values


def _read_csv(path: Path) -> Iterable[Dict[str, str]]:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as file:
                reader = csv.DictReader(file)
                if reader.fieldnames is None:
                    raise QgisBuildError(f"CSV 没有表头：{path}")
                rows = list(reader)
            yield from rows
            return
        except UnicodeDecodeError:
            continue
    raise QgisBuildError(f"CSV 编码无法识别（支持 UTF-8/GB18030）：{path}")


def _plain_spreadsheet_value(value: Any) -> Any:
    try:
        if value is None or value == NULL:
            return None
    except (TypeError, ValueError):
        pass
    return value


def _read_spreadsheet(
    layer_cfg: Dict[str, Any], base_dir: Path
) -> Iterable[Dict[str, Any]]:
    """Read one explicitly named XLS/XLSX worksheet through QGIS/OGR.

    The workbook remains an attribute table. Geometry is built only from the
    configured X/Y or WKT columns, so no spreadsheet cell is mistaken for a
    formal GIS geometry.
    """
    source = resolve_path(base_dir, layer_cfg["source"])
    worksheet = str(layer_cfg.get("source_layer") or "").strip()
    uri = f"{source}|layername={worksheet}|option:HEADERS=FORCE"
    table = QgsVectorLayer(uri, f"{output_layer_name(layer_cfg)}_source_table", "ogr")
    if not table.isValid():
        raise QgisBuildError(
            f"无法读取 Excel 工作表 {worksheet!r}：{source}；"
            "请确认 QGIS/GDAL 已启用 XLS/XLSX 驱动，且工作表名称完全一致"
        )
    headers = [field.name() for field in table.fields()]
    required = {
        field.get("source_field", field["name"])
        for field in layer_cfg["fields"] if field.get("required", False)
    }
    if layer_cfg["source_type"] == "spreadsheet_xy":
        required.update({layer_cfg["x_field"], layer_cfg["y_field"]})
    else:
        required.add(layer_cfg["wkt_field"])
    if layer_cfg.get("filter_obj_type"):
        required.add("object_type")
    missing = sorted(required - set(headers))
    if missing:
        raise QgisBuildError(
            f"Excel 工作表 {worksheet!r} 缺少字段：{', '.join(missing)}"
        )
    seen_ids: set[str] = set()
    id_field = str(layer_cfg.get("id_field") or "").strip()
    for row_number, feature in enumerate(table.getFeatures(), start=2):
        if layer_cfg.get("spreadsheet_header_row") and row_number == 2:
            continue
        row = {
            name: _plain_spreadsheet_value(feature[name])
            for name in headers
        }
        if not row_matches_filter(row, layer_cfg):
            continue
        for field in layer_cfg["fields"]:
            source_name = field.get("source_field", field["name"])
            if field.get("required", False) and (
                row.get(source_name) is None
                or str(row.get(source_name)).strip() == ""
            ):
                raise QgisBuildError(
                    f"Excel 工作表 {worksheet!r} 第{row_number}行必填字段 "
                    f"{field['name']} 为空"
                )
        if id_field:
            id_source = next(
                (field.get("source_field", field["name"]) for field in layer_cfg["fields"] if field["name"] == id_field),
                id_field,
            )
            identifier = str(row.get(id_source) or "").strip()
            if identifier and identifier in seen_ids:
                raise QgisBuildError(
                    f"Excel 工作表 {worksheet!r} 主键 {id_field} 重复：{identifier}"
                )
            if identifier:
                seen_ids.add(identifier)
        yield row


def import_csv_xy(layer_cfg: Dict[str, Any], base_dir: Path) -> QgsVectorLayer:
    layer = _new_memory_layer(layer_cfg)
    source = resolve_path(base_dir, layer_cfg["source"])
    features: List[QgsFeature] = []

    for row in _read_csv(source):
        if not row_matches_filter(row, layer_cfg):
            continue
        try:
            x = float(row[layer_cfg["x_field"]])
            y = float(row[layer_cfg["y_field"]])
        except (KeyError, TypeError, ValueError) as exc:
            raise QgisBuildError(
                f"图层 {output_layer_name(layer_cfg)} 存在无法读取的坐标：{exc}"
            ) from exc

        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        feature.setAttributes(_attributes(row, layer_cfg))
        features.append(feature)

    if features and not layer.dataProvider().addFeatures(features):
        raise QgisBuildError(f"写入要素失败：{output_layer_name(layer_cfg)}")
    layer.updateExtents()
    return layer


def _expected_geometry_type(name: str) -> Qgis.GeometryType:
    mapping = {
        "Point": Qgis.GeometryType.Point,
        "LineString": Qgis.GeometryType.Line,
        "Polygon": Qgis.GeometryType.Polygon,
    }
    return mapping[name]


def import_csv_wkt(layer_cfg: Dict[str, Any], base_dir: Path) -> QgsVectorLayer:
    layer = _new_memory_layer(layer_cfg)
    source = resolve_path(base_dir, layer_cfg["source"])
    features: List[QgsFeature] = []
    expected_type = _expected_geometry_type(layer_cfg["geometry_type"])

    for row in _read_csv(source):
        if not row_matches_filter(row, layer_cfg):
            continue
        raw_wkt = str(row.get(layer_cfg["wkt_field"], "")).strip()
        geometry = QgsGeometry.fromWkt(raw_wkt)
        if geometry.isNull() or geometry.isEmpty():
            raise QgisBuildError(f"无效 WKT：{raw_wkt!r}")
        actual_type = QgsWkbTypes.geometryType(geometry.wkbType())
        if actual_type != expected_type:
            raise QgisBuildError(
                f"图层 {output_layer_name(layer_cfg)} 的 WKT 几何类型与配置不一致"
            )

        feature = QgsFeature(layer.fields())
        feature.setGeometry(geometry)
        feature.setAttributes(_attributes(row, layer_cfg))
        features.append(feature)

    if features and not layer.dataProvider().addFeatures(features):
        raise QgisBuildError(f"写入要素失败：{output_layer_name(layer_cfg)}")
    layer.updateExtents()
    return layer


def import_spreadsheet_xy(
    layer_cfg: Dict[str, Any], base_dir: Path
) -> QgsVectorLayer:
    layer = _new_memory_layer(layer_cfg)
    features: List[QgsFeature] = []
    for row in _read_spreadsheet(layer_cfg, base_dir):
        try:
            x = float(row[layer_cfg["x_field"]])
            y = float(row[layer_cfg["y_field"]])
        except (KeyError, TypeError, ValueError) as exc:
            raise QgisBuildError(
                f"图层 {output_layer_name(layer_cfg)} 存在无法读取的 Excel 坐标：{exc}"
            ) from exc
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        feature.setAttributes(_attributes(row, layer_cfg))
        features.append(feature)
    if features and not layer.dataProvider().addFeatures(features):
        raise QgisBuildError(f"写入要素失败：{output_layer_name(layer_cfg)}")
    layer.updateExtents()
    return layer


def import_spreadsheet_wkt(
    layer_cfg: Dict[str, Any], base_dir: Path
) -> QgsVectorLayer:
    layer = _new_memory_layer(layer_cfg)
    features: List[QgsFeature] = []
    expected_type = _expected_geometry_type(layer_cfg["geometry_type"])
    for row in _read_spreadsheet(layer_cfg, base_dir):
        raw_wkt = str(row.get(layer_cfg["wkt_field"], "")).strip()
        geometry = QgsGeometry.fromWkt(raw_wkt)
        if geometry.isNull() or geometry.isEmpty():
            raise QgisBuildError(f"无效 Excel WKT：{raw_wkt!r}")
        if QgsWkbTypes.geometryType(geometry.wkbType()) != expected_type:
            raise QgisBuildError(
                f"图层 {output_layer_name(layer_cfg)} 的 Excel WKT 几何类型与配置不一致"
            )
        feature = QgsFeature(layer.fields())
        feature.setGeometry(geometry)
        feature.setAttributes(_attributes(row, layer_cfg))
        features.append(feature)
    if features and not layer.dataProvider().addFeatures(features):
        raise QgisBuildError(f"写入要素失败：{output_layer_name(layer_cfg)}")
    layer.updateExtents()
    return layer


def load_vector(layer_cfg: Dict[str, Any], base_dir: Path) -> QgsVectorLayer:
    source = resolve_path(base_dir, layer_cfg["source"])
    source_layer = str(layer_cfg.get("source_layer") or "").strip()
    uri = f"{source}|layername={source_layer}" if source_layer else str(source)
    layer = QgsVectorLayer(uri, output_layer_name(layer_cfg), "ogr")
    if not layer.isValid():
        raise QgisBuildError(f"无法加载矢量文件：{source}")

    existing_fields = {field.name() for field in layer.fields()}
    required_fields = {
        field.get("source_field", field["name"])
        for field in layer_cfg["fields"] if field.get("required", False)
    }
    missing = sorted(required_fields - existing_fields)
    if missing:
        raise QgisBuildError(
            f"图层 {output_layer_name(layer_cfg)} 缺少必填字段：{', '.join(missing)}"
        )

    expected_type = _expected_geometry_type(layer_cfg["geometry_type"])
    if layer.geometryType() != expected_type:
        raise QgisBuildError(
            f"图层 {output_layer_name(layer_cfg)} 的实际几何类型与配置不一致"
        )
    configured_crs = str(layer_cfg.get("crs") or "").strip()
    actual_crs = layer.crs().authid()
    if configured_crs and actual_crs and configured_crs.upper() != actual_crs.upper():
        raise QgisBuildError(
            f"图层 {output_layer_name(layer_cfg)} 实际坐标系 {actual_crs} 与配置 {configured_crs} 不一致"
        )

    # Copy into the formal schema so source aliases and DBF-truncated names
    # become stable contract field names in the generated GeoPackage.
    output = _new_memory_layer(layer_cfg)
    features: List[QgsFeature] = []
    headers = [field.name() for field in layer.fields()]
    for source_feature in layer.getFeatures():
        row = {name: _plain_spreadsheet_value(source_feature[name]) for name in headers}
        if not row_matches_filter(row, layer_cfg):
            continue
        geometry = source_feature.geometry()
        if geometry.isMultipart():
            if expected_type == Qgis.GeometryType.Point:
                parts = geometry.asMultiPoint()
                if len(parts) != 1:
                    raise QgisBuildError(f"图层 {output_layer_name(layer_cfg)} 包含多点要素；请先拆分并分配唯一 CODE")
                geometry = QgsGeometry.fromPointXY(parts[0])
            elif expected_type == Qgis.GeometryType.Line:
                parts = geometry.asMultiPolyline()
                if len(parts) != 1:
                    raise QgisBuildError(f"图层 {output_layer_name(layer_cfg)} 包含不连续多线；请先拆分并分配唯一 CODE")
                geometry = QgsGeometry.fromPolylineXY(parts[0])
            else:
                parts = geometry.asMultiPolygon()
                if len(parts) != 1:
                    raise QgisBuildError(f"图层 {output_layer_name(layer_cfg)} 包含多面要素；请先拆分并分配唯一 CODE")
                geometry = QgsGeometry.fromPolygonXY(parts[0])
        feature = QgsFeature(output.fields())
        feature.setGeometry(geometry)
        feature.setAttributes(_attributes(row, layer_cfg))
        features.append(feature)
    if features and not output.dataProvider().addFeatures(features):
        raise QgisBuildError(f"写入矢量要素失败：{output_layer_name(layer_cfg)}")
    output.updateExtents()
    return output


def save_layer_to_gpkg(
    layer: QgsVectorLayer,
    gpkg_path: Path,
    layer_name: str,
    overwrite_layer: bool = True,
) -> None:
    gpkg_path.parent.mkdir(parents=True, exist_ok=True)
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = layer_name
    options.fileEncoding = "UTF-8"
    options.actionOnExistingFile = (
        QgsVectorFileWriter.CreateOrOverwriteLayer
        if gpkg_path.exists() and overwrite_layer
        else QgsVectorFileWriter.CreateOrOverwriteFile
    )

    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer,
        str(gpkg_path),
        QgsProject.instance().transformContext(),
        options,
    )
    error_code = result[0] if isinstance(result, tuple) else result
    if error_code != QgsVectorFileWriter.NoError:
        details = result[1:] if isinstance(result, tuple) else ""
        raise QgisBuildError(f"保存 GeoPackage 失败：{layer_name}；{details}")


def _remove_existing_project_layers(names: Iterable[str]) -> None:
    project = QgsProject.instance()
    for name in names:
        for layer in list(project.mapLayersByName(name)):
            project.removeMapLayer(layer.id())


def _merged_style(layer_cfg: Dict[str, Any]) -> Dict[str, Any]:
    name = output_layer_name(layer_cfg)
    style = dict(DEFAULT_LAYER_STYLES.get(name, {}))
    style.update(layer_cfg.get("style") or {})
    return style


def _color(value: Any, default: str) -> QColor:
    color = QColor(str(value or default))
    return color if color.isValid() else QColor(default)


def _label_font(style: Dict[str, Any]) -> QFont:
    requested = str(style.get("label_font", "")).strip()
    if requested:
        return QFont(requested)
    for path in (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ):
        if not path.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        if families:
            return QFont(families[0])
    return QFont("sans-serif")


def _ensure_single_symbol(layer: QgsVectorLayer) -> QgsSymbol:
    renderer = layer.renderer()
    symbol = renderer.symbol() if hasattr(renderer, "symbol") else None
    if symbol is None:
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        if symbol is None:
            raise QgisBuildError(f"无法为图层创建默认符号：{layer.name()}")
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    return symbol


def auto_set_style(
    layer_dict: Dict[str, QgsVectorLayer],
    layer_configs: Iterable[Dict[str, Any]],
) -> None:
    """按 JSON 配置批量设置图层配色和标注。"""
    config_by_name = {output_layer_name(cfg): cfg for cfg in layer_configs}

    for layer_name, layer in layer_dict.items():
        layer_cfg = config_by_name.get(layer_name)
        if layer_cfg is None:
            continue
        style = _merged_style(layer_cfg)
        if not style:
            continue

        geometry_type = layer.geometryType()

        if geometry_type == Qgis.GeometryType.Polygon:
            symbol = QgsFillSymbol.createSimple({
                "color": str(style.get("fill_color", "#FFFAC8")),
                "outline_color": str(style.get("stroke_color", "#3C3C3C")),
                "outline_width": str(style.get("stroke_width", 0.6)),
            })
        elif geometry_type == Qgis.GeometryType.Line:
            symbol = QgsLineSymbol.createSimple({
                "color": str(style.get("color", "#5A5A5A")),
                "width": str(style.get("width", 1.3)),
            })
        elif geometry_type == Qgis.GeometryType.Point:
            symbol = QgsMarkerSymbol.createSimple({
                "name": str(style.get("shape", "circle")),
                "color": str(style.get("color", "#111111")),
                "size": str(style.get("size", 5.5)),
                "outline_color": str(style.get("outline_color", "#232323")),
                "outline_width": str(style.get("outline_width", 0.0)),
            })
        else:
            symbol = _ensure_single_symbol(layer)
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))

        label_field = str(style.get("label_field", "")).strip()
        if label_field and layer.fields().indexOf(label_field) >= 0:
            label_settings = QgsPalLayerSettings()
            label_settings.fieldName = label_field
            label_settings.enabled = True

            text_format = QgsTextFormat()
            font = _label_font(style)
            font.setBold(bool(style.get("label_bold", False)))
            font.setItalic(bool(style.get("label_italic", False)))
            text_format.setFont(font)
            text_format.setSize(float(style.get("label_size", 8)))
            text_format.setColor(_color(style.get("label_color"), "#202020"))
            label_settings.setFormat(text_format)

            layer.setLabeling(QgsVectorLayerSimpleLabeling(label_settings))
            layer.setLabelsEnabled(True)

        layer.triggerRepaint()


def capture_canvas_img(save_path: Path) -> Path:
    """保存当前 QGIS 工程全景图；优先复用主界面画布。"""
    save_path.parent.mkdir(parents=True, exist_ok=True)
    project = QgsProject.instance()
    layers = list(project.mapLayers().values())
    if not layers:
        raise QgisBuildError("当前工程没有可截图的图层")

    canvas = None
    try:
        from qgis.utils import iface

        if iface is not None:
            canvas = iface.mapCanvas()
    except (ImportError, AttributeError):
        canvas = None

    temporary_canvas = canvas is None
    if temporary_canvas:
        canvas = QgsMapCanvas()
        canvas.resize(1600, 900)
        canvas.setLayers(layers)

    canvas.zoomToFullExtent()
    canvas.refresh()
    QApplication.processEvents()
    QApplication.processEvents()
    canvas.saveAsImage(str(save_path), None, "PNG")
    QApplication.processEvents()

    if not save_path.exists() or save_path.stat().st_size == 0:
        raise QgisBuildError(f"截图未成功写入：{save_path}")

    if temporary_canvas:
        canvas.deleteLater()
    return save_path


def build_qgis_layers(config: Dict[str, Any], base_dir: Path) -> Dict[str, Any]:
    output_cfg = config["output"]
    gpkg_path = resolve_path(base_dir, output_cfg["geopackage"])
    gpkg_path.parent.mkdir(parents=True, exist_ok=True)
    if gpkg_path.exists() and not output_cfg.get("overwrite_file", True):
        raise QgisBuildError(f"目标 GeoPackage 已存在且 overwrite_file=false：{gpkg_path}")

    project = QgsProject.instance()
    project_crs = QgsCoordinateReferenceSystem(config["project_crs"])
    if not project_crs.isValid():
        raise QgisBuildError(f"无效 project_crs：{config['project_crs']}")
    project.setCrs(project_crs)

    # 先验证全部输入并在私有 staging 目录生成完整成果；任何失败均不覆盖旧成果。
    loaded_rasters: List[str] = []
    raster_layers: List[tuple[Dict[str, Any], QgsRasterLayer]] = []
    for raster_cfg in config.get("rasters", []):
        source = resolve_path(base_dir, raster_cfg["source"])
        raster = QgsRasterLayer(str(source), raster_cfg["name"])
        if not raster.isValid():
            raise QgisBuildError(f"无法加载栅格：{source}")
        raster_layers.append((raster_cfg, raster))
        loaded_rasters.append(raster_cfg["name"])

    created_layers: List[str] = []
    feature_counts: Dict[str, int] = {}
    layer_dict: Dict[str, QgsVectorLayer] = {}
    runtime_warnings: List[str] = []
    source_layers: Dict[str, QgsVectorLayer] = {}
    staging_dir = Path(
        tempfile.mkdtemp(prefix=".task1_staging_", dir=str(gpkg_path.parent))
    )
    staged_gpkg = staging_dir / gpkg_path.name
    topology_path = None
    staged_topology = None
    topology_summary: Dict[str, Any] = {}

    try:
        for layer_cfg in config["vector_layers"]:
            source_type = layer_cfg["source_type"]
            if source_type == "csv_xy":
                layer = import_csv_xy(layer_cfg, base_dir)
            elif source_type == "csv_wkt":
                layer = import_csv_wkt(layer_cfg, base_dir)
            elif source_type == "spreadsheet_xy":
                layer = import_spreadsheet_xy(layer_cfg, base_dir)
            elif source_type == "spreadsheet_wkt":
                layer = import_spreadsheet_wkt(layer_cfg, base_dir)
            elif source_type == "vector":
                layer = load_vector(layer_cfg, base_dir)
            elif source_type == "empty":
                layer = _new_memory_layer(layer_cfg)
            else:
                raise QgisBuildError(f"不支持的 source_type：{source_type}")

            layer_name = output_layer_name(layer_cfg)
            save_layer_to_gpkg(layer, staged_gpkg, layer_name)
            feature_counts[layer_name] = int(layer.featureCount())
            source_layers[layer_name] = layer
            created_layers.append(layer_name)

        from task2_topology_adapter import (
            atomic_write_json,
            build_task2_topology_from_gpkg,
            topology_contract,
            validate_task2_topology_payload,
        )

        contract = topology_contract(config)
        if contract.get("enabled", True):
            topology_path = resolve_path(base_dir, str(contract["output_json"]))
            topology_path.parent.mkdir(parents=True, exist_ok=True)
            topology_handle, topology_staging_name = tempfile.mkstemp(
                prefix=f".{topology_path.name}.task1_staging_",
                suffix=".tmp",
                dir=str(topology_path.parent),
            )
            os.close(topology_handle)
            staged_topology = Path(topology_staging_name)
            topology_payload = build_task2_topology_from_gpkg(staged_gpkg, config)
            topology_summary = validate_task2_topology_payload(topology_payload)
            atomic_write_json(staged_topology, topology_payload)

        # 在发布前用 OGR 逐层回读 staging 成果。
        for layer_name in created_layers:
            probe = QgsVectorLayer(
                f"{staged_gpkg}|layername={layer_name}", layer_name, "ogr"
            )
            if not probe.isValid() or int(probe.featureCount()) != feature_counts[layer_name]:
                raise QgisBuildError(f"staging GeoPackage 回读失败：{layer_name}")
            del probe
        gc.collect()

        managed_names = [
            *(str(raster.get("name", "")) for raster in config.get("rasters", [])),
            *created_layers,
        ]
        if output_cfg.get("replace_project_layers", True):
            _remove_existing_project_layers(name for name in managed_names if name)
            QApplication.processEvents()

        publication = [(staged_gpkg, gpkg_path)]
        if topology_path is not None and staged_topology is not None:
            publication.append((staged_topology, topology_path))
        runtime_warnings.extend(publish_staged_bundle(publication))

        # 发布后再加入工程：参考示例建立稳定图层组与绘制顺序。
        for raster_cfg, raster in raster_layers:
            if raster_cfg.get("add_to_project", True):
                project.addMapLayer(raster)
        loaded_project_layers: Dict[str, QgsVectorLayer] = {}
        for layer_name in created_layers:
            if output_cfg.get("add_to_project", True):
                saved_layer = QgsVectorLayer(
                    f"{gpkg_path}|layername={layer_name}", layer_name, "ogr"
                )
                if saved_layer.isValid():
                    loaded_project_layers[layer_name] = saved_layer
                    layer_dict[layer_name] = saved_layer
                else:
                    runtime_warnings.append(
                        f"成果已生成，但未能加入当前 QGIS 工程：{layer_name}"
                    )
                    layer_dict[layer_name] = source_layers[layer_name]
            else:
                layer_dict[layer_name] = source_layers[layer_name]
        if loaded_project_layers:
            root = project.layerTreeRoot()
            group = root.findGroup("FTTH 基础图层") or root.addGroup("FTTH 基础图层")
            for layer_name in ("SITE", "PTECH", "IMB", "INFRASTRUCTURE"):
                saved_layer = loaded_project_layers.get(layer_name)
                if saved_layer is None:
                    continue
                project.addMapLayer(saved_layer, False)
                group.addLayer(saved_layer)
    finally:
        if staged_topology is not None:
            staged_topology.unlink(missing_ok=True)
        shutil.rmtree(staging_dir, ignore_errors=True)

    return {
        "geopackage": str(gpkg_path),
        "task2_topology_json": str(topology_path) if topology_path is not None else "",
        "task2_topology_summary": topology_summary,
        "created_vector_layers": created_layers,
        "feature_counts": feature_counts,
        "loaded_rasters": loaded_rasters,
        "runtime_warnings": runtime_warnings,
        "_layer_dict": layer_dict,
    }
