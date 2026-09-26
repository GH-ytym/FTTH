"""Bounded, source-backed engine reference; no project files or arbitrary paths."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FULL_NRO_COUNT = 1

# Anchors select reviewed code, not model-supplied filesystem paths.
REFERENCES = {
    'distance.task2': ('vendor/ftth/engine/task2/znro_zpm_partition.py', 'def point_distance(', 'def edge_points('),
    'distance.metadata': ('vendor/ftth/engine/task2_topology_adapter.py', '            # Task-2\'s unchanged algorithm', '            "axis_order":'),
    'distance.task3': ('vendor/ftth/engine/task3/ga_pm_optimizer/geometry.py', 'def metres(', 'def interpolate('),
    'distance.task6': ('vendor/ftth/engine/task6/code/metric_crs.py', '"""Dependency-free', 'from __future__'),
    'capacity.partition': ('vendor/ftth/engine/task2/znro_zpm_partition_petch_atomic.py', '    total_ports = sum(unit["nb_loc_tot"]', '    znro = format_atomic_partition('),
    'capacity.preflight': ('backend/service.py', '        if scope == "full":\n            if summary["nro_count"]', '            if not (p['),
    'capacity.scope': ('vendor/ftth/task_bridge.py', '        if run_task3 and not run_tasks4_to6:\n            # Task 3 reserves', '        required_sources ='),
    'capacity.dispatch': ('backend/service.py', '                    if run["scope"] == "full":', '                    for key, value in run["parameters"].items():'),
}

APPLICABILITY = {
    'capacity.partition': 'NRO容量确定ZNRO目标簇数，不是PM/ZPM目标数；ZPM受自身容量和ZNRO嵌套共同限制。目标簇数不等于运行后实际结果。',
    'capacity.scope': '仅run_task3=true且run_tasks4_to6=false的单独选址分支才做15%折减。当前API的full与partition均不走此分支，不得将输入容量除以1.15当作当前流程的有效容量。',
    'capacity.dispatch': 'full同时启用run_task3和run_tasks4_to6；partition不启用这两个标记。与capacity.scope条件对照，当前API不触发该15%折减。',
}


def capabilities():
    return {
        'full_required_nro_count': FULL_NRO_COUNT,
        'validation_scope': '输入预检：参数类型/范围、数据与策略前提；不是容量分配、可行性或工程质量验收。',
        'distance': '存储坐标EPSG:4326不等于按度计距。任务二source_shapefile_coordinates标记启用地理距离；任务三GA有UTM29米制距离；任务六有local UTM米制工具。具体调用路径须读依据，不能由CRS标签推断全流程错误。',
        'capacity': 'NRO容量参与任务二ZNRO分区目标及容量限制，不是直接确定PM数量；ZPM另有容量与嵌套约束。理论所需分区数不等于必须新增的实体SITE数；完整规划只支持一个NRO。当前API的full/partition均不走单独任务三选址的15%容量折减分支。未运行不能断言分配结果或无解。',
        'reference_topics': ['distance', 'capacity'],
    }


def read_reference(topic):
    if topic not in {'distance', 'capacity'}:
        raise ValueError('可读取的主题为 distance、capacity')
    sources = []
    for identifier, (relative, start, end) in REFERENCES.items():
        if not identifier.startswith(topic + '.'):
            continue
        path = ROOT / relative
        raw = path.read_text(encoding='utf-8')
        begin = raw.find(start)
        finish = raw.find(end, begin + len(start)) if begin >= 0 else -1
        if begin < 0 or finish < 0 or finish - begin > 4500:
            sources.append({'id': identifier, 'available': False, 'reason': '源码锚点已变化，需要维护依据索引；不可引用旧结论'})
            continue
        first = raw[:begin].count('\n') + 1
        sources.append({'id': identifier, 'available': True, 'path': relative,
            'applicability': APPLICABILITY.get(identifier, '仅说明该源码位置的实现，不代表所有算法阶段或已执行结果。'),
            'line': first, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'excerpt': '\n'.join(f'{first+i}: {line}' for i, line in enumerate(raw[begin:finish].splitlines()))})
    return {'topic': topic, 'kind': 'implementation_reference_not_run_result',
        'capabilities': capabilities(), 'sources': sources,
        'limits': '源码说明实现契约，不证明本项目某批次已执行，也不是完整算法正确性证明。'}
