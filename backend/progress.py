"""Map observed bridge log lines to algorithm labels, never inferred progress."""
STEPS = (
    ("生成本次基础图层", "task1", "任务一 · 数据与拓扑"),
    ("合成本次拓扑", "task2_input", "任务二 · 合并路由输入"),
    ("clean_imb_topology_input.py", "task2_clean", "任务二 · 清理楼栋拓扑"),
    ("znro_zpm_partition_petch_atomic.py", "task2_partition", "任务二 · NRO / PM 分区"),
    ("znro_zpm_region_partition.py", "task2_region", "任务二 · 区域划分"),
    ("building_group_partition.py", "task2_group", "任务二 · 楼栋分组"),
    ("task2_geometry_features.py", "task2_geometry", "任务二 · 几何特征"),
    ("确定性选址", "task3_initial", "任务三 · 初始选址"),
    ("任务四初算", "task4_initial", "任务四 · PBO / BPE 初算"),
    ("任务三：", "task3_ga", "任务三 · GA 优化 PM"),
    ("任务四定稿", "task4_final", "任务四 · PBO / BPE 定稿"),
    ("任务六：", "task6", "任务六 · 管线路由"),
    ("任务五：", "task5", "任务五 · 光缆拓扑"),
    ("数据合同复核", "validation", "检查算法间的数据衔接"),
    ("最终图层", "layers", "生成八类成果图层"),
)


def algorithm_step(line):
    if not line.startswith("[bridge]"):
        return None
    for marker, key, label in STEPS:
        if marker in line:
            return {"stage": key, "label": label}
    return None
