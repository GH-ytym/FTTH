"""Input-only rules before planning. Delivery-only fields never block raw inputs."""
import math
from collections import Counter
from pathlib import Path

VERSION = 'ftth-input-preflight-v1'


def inspect_inputs(config, recognition_path=None):
    from qgis.core import QgsVectorLayer, QgsVariantUtils
    results, crs, counts = [], {}, {}
    def record(rule, severity, description, objects=()):
        results.append({'rule_id':rule, 'severity':severity, 'description':description,
            'object_ids':[str(x) for x in list(objects)[:30]], 'object_count':len(objects)})
    roads = bool(recognition_path)
    specs = list(config.get('vector_layers', []))
    if roads:
        specs.append({'name':'GEO_ROAD', 'source':str(recognition_path), 'source_layer':'GEO_ROAD', 'source_type':'vector'})
    for spec in specs:
        name = spec['name']
        if spec.get('source_type') == 'empty':
            counts[name] = 0
            continue
        # Table coordinates and source mappings are checked by Task-1; this
        # audit does not guess vector providers for CSV/XLSX inputs.
        if spec.get('source_type') != 'vector':
            record('input.table', 'warning', name + ' 为表格数据，坐标/字段读取沿用 Task-1 检查；矢量几何检查不适用')
            continue
        required = name in {'IMB', 'SITE', 'GEO_ROAD'} or (name == 'INFRASTRUCTURE' and not roads)
        severity = 'error' if required else 'warning'
        source = spec.get('source', '')
        if not source or not Path(source).is_file():
            record('input.files', severity, name + ' 文件不存在')
            continue
        uri = source + ('|layername=' + spec['source_layer'] if spec.get('source_layer') else '')
        layer = QgsVectorLayer(uri, name, 'ogr')
        if not layer.isValid():
            record('input.readable', severity, name + ' 图层无法读取')
            continue
        counts[name] = layer.featureCount()
        if not layer.featureCount() and name in {'IMB','GEO_ROAD'}:
            record('input.nonempty', 'error', name + ' 没有可用要素')
        if not layer.featureCount() and name not in {'IMB','GEO_ROAD'}:
            continue  # SITE count/full-scope and route fallback are separate gates.
        crs[name] = layer.crs().authid()
        if not layer.crs().isValid():
            record('input.crs', severity, name + ' 坐标系无效')
        expected = 1 if name in {'INFRASTRUCTURE','GEO_ROAD'} else 0
        if layer.geometryType() != expected:
            record('input.geometry_type', severity, name + ' 几何类型不符')
        names = {f.name() for f in layer.fields()}
        mapping = {f['name']: f.get('source_field', f['name']) for f in spec.get('fields', [])}
        def field(key):
            return mapping.get(key, key) if mapping.get(key, key) in names else key[:10] if key[:10] in names else None
        code = field('CODE')
        critical = ['CODE'] if name != 'GEO_ROAD' else []
        if name == 'IMB':
            critical.append('NB_LOC_TOT')
        if name == 'SITE':
            critical.append('TYPE')
        missing = [k for k in critical if not field(k)]
        if missing:
            record('input.fields', severity, name + ' 缺少算法必要字段', missing)
        invalid, blank, codes, demand = [], [], [], []
        for f in layer.getFeatures():
            oid = str(f[code]) if code and not QgsVariantUtils.isNull(f[code]) else str(f.id())
            g = f.geometry()
            if g.isNull() or g.isEmpty() or not g.isGeosValid():
                invalid.append(oid)
            for key in critical:
                actual = field(key)
                if actual and (QgsVariantUtils.isNull(f[actual]) or not str(f[actual]).strip()):
                    blank.append(oid + ':' + key)
            if code and not QgsVariantUtils.isNull(f[code]):
                codes.append(str(f[code]))
            total = field('NB_LOC_TOT') if name == 'IMB' else None
            if total:
                try:
                    n = float(f[total])
                    if not math.isfinite(n) or n < 0 or not n.is_integer():
                        demand.append(oid)
                except (ValueError,TypeError):
                    demand.append(oid)
        if invalid:
            record('input.geometry', severity, name + ' 含空或无效几何', invalid)
        if blank:
            record('input.required', severity, name + ' 必要字段有空值', blank)
        duplicated = [v for v,n in Counter(codes).items() if n > 1]
        if duplicated:
            record('input.code_unique', severity, name + ' CODE 重复', duplicated)
        if demand:
            record('input.demand', 'error', 'IMB 需求必须为非负整数', demand)
    if len(set(crs.values())) > 1:
        record('input.crs_difference', 'warning', '输入图层 CRS 不同，需依照 Task-1 的转换结果核对；不是最终成果 CRS 一致性失败')
    errors = [r for r in results if r['severity']=='error']
    return {'version':VERSION, 'stage':'before_planning', 'status':'FAIL' if errors else 'PASS',
        'results':results, 'counts':counts, 'crs':crs,
        'deferred':['最终八图层齐全/非空','交付字段及枚举','跨成果层引用','成果区域与光缆几何','PBO与PM出线容量','QGIS桌面结果状态']}
