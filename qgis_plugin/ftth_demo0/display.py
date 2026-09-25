from pathlib import Path
from qgis.core import QgsProject, QgsVectorLayer, QgsCoordinateTransform, QgsRectangle


def show_result(iface, command):
    project = QgsProject.instance()
    run_id = command["run_id"]
    name = "FTTH demo0 " + run_id
    result = command["result"]
    final = (result.get("downstream") or {}).get("final_layers") or {}
    specs = []
    if final.get("output_dir"):
        specs = [(n, str(Path(final["output_dir"]) / (n + ".shp"))) for n in ("ZNRO", "ZPM", "INFRASTRUCTURE", "CABLE", "PTECH", "SITE", "BOITE", "IMB")]
    else:
        if result.get("geopackage"):
            specs += [(n, result["geopackage"] + "|layername=" + n) for n in ("SITE", "PTECH", "IMB", "INFRASTRUCTURE")]
        specs += list(result.get("candidate_layers", {}).items())
    if not specs:
        raise ValueError("报告没有可展示图层")
    layers = []
    bounds = QgsRectangle()
    visible = command["visible_layers"] or ["IMB", "SITE", "BOITE", "CABLE", "INFRASTRUCTURE", "ZPM"]
    for layer_name, source in specs:
        layer = QgsVectorLayer(source, layer_name, "ogr")
        if not layer.isValid():
            raise ValueError("图层加载失败：" + layer_name)
        qml = Path(__file__).parent / "styles" / (layer_name + ".qml")
        if qml.exists():
            layer.loadNamedStyle(str(qml))
        if layer.featureCount() and layer_name in visible:
            transform = QgsCoordinateTransform(layer.crs(), project.crs(), project)
            bounds.combineExtentWith(transform.transformBoundingBox(layer.extent()))
        layers.append(layer)
    old = project.layerTreeRoot().findGroup(name)
    if old:
        project.removeMapLayers([n.layerId() for n in old.findLayers()])
        project.layerTreeRoot().removeChildNode(old)
    group = project.layerTreeRoot().addGroup(name)
    for layer in layers:
        project.addMapLayer(layer, False)
        node = group.insertLayer(0, layer)
        node.setItemVisibilityChecked(layer.name() in visible)
    if command.get("zoom") and not bounds.isEmpty():
        bounds.scale(1.1)
        iface.mapCanvas().setExtent(bounds)
        iface.mapCanvas().refresh()
    return {"run_id": run_id, "layers": {l.name(): l.featureCount() for l in layers}, "group": name}
