# 任务三：PM 道路约束选址与任务六反馈优化

## 1. 目标

任务三不再只对既有候选设施做简单校验，而是接收任务二的 ZPM 分区、任务四固定的 BPE/PBO 服务关系和任务六道路输入，在每个 ZPM 内、可施工道路中心线周围 10 米范围内选择一个 PM 位置。PM 可像 NRO 一样通过短段直连光纤接入道路管网。任务六负责对每组 PM 坐标生成真实管道路由，任务三根据路由长度和 ZPM 越界情况迭代优化。

任务三只改变 PM 坐标，不改变以下关系：

- 任务二确定的 IMB 到 ZPM 归属；
- 任务四确定的 IMB-PBO-BPE-PM 逻辑归属；
- ZPM 到 ZNRO/NRO 的归属；
- BPE、PBO 和建筑入口的位置。

## 2. 数据链路

```text
任务二 ZPM 分区及区域多边形
              +
任务四固定 BPE/PBO 位置和服务关系
              +
任务六道路、入口、建筑和 NRO 数据
              |
              v
任务三道路 10m 缓冲区候选 -> 初始 PM -> 任务六真实路由评估
              ^                         |
              |                         v
              +------ 约束型遗传算法 ---+
                                        |
                                        v
                      最优 PM 固定方案和最终任务六路由
```

任务四的 BPE/PBO 生成必须先于 PM 坐标优化执行。优化完成后可使用任务三输出的 `pm_fixed_plan.json` 再运行任务四，以更新其 PM 点图层，但不得重新划分 ZPM 或改变 BPE 聚类关系。

## 3. 输入

默认使用工程已有格式：

- `OutPut/Task2/zone_partition/qgis_direct_region_partition_result.json`
  - 使用 `zpm_regions[].geometry.polygons` 作为 ZPM 空间硬边界；
  - 使用 `imb_ids`、`parent_znro_id` 和容量字段校验固定归属。
- `Input/Task6/layered_planning_production_input.json`
  - 使用 `roads` 构造道路候选；
  - 使用 `pm_sites`、`nro_sites` 保留设备标识和容量；
  - 使用 `bpe_plan`、`pbo_plan`、`building_groups`、`entrances` 和 `imb_points` 运行任务六。

所有距离和候选排序在 EPSG:32629 米制坐标中完成，输出坐标继续保持工程现用的 EPSG:4326。

## 4. 道路候选点

对每个 ZPM 执行以下步骤：

1. 从开放施工且容量非零的道路提取线段。
2. 加入道路端点、路口、道路与 ZPM 边界交点。
3. 按配置间隔沿道路采样，默认每 8 米一个道路入口点。
4. 将该 ZPM 的 BPE 和原 PM 投影到道路，加入相应道路入口点。
5. 以每个道路入口点为基准，沿道路法向在两侧生成候选；默认横向间隔 5 米，包含中心线、±5 米和 ±10 米位置。
6. 删除距对应道路中心线超过 10 米的点，以及不在所属 ZPM 多多边形内的点；同时要求默认 0.5 米区内净距，避免把公共边界点同时分配给两个 ZPM。
7. 为每个候选保留道路入口坐标、道路编号和直连光纤长度，以便任务六复核并从准确入口节点路由。
8. 计算每个点到所属 BPE 的需求加权空间距离总和和最远 BPE 距离。
9. 候选点之间默认至少间隔 3 米；保留空间得分靠前的点，同时保留部分空间分散点，避免候选只集中在几何中心。

初始候选点为道路候选集合上的加权离散几何中位点：

```text
p0(z) = arg min [sum(demand(b) * distance(p, b))]
        p in buffer(legal_roads, 10m) intersect ZPM(z)
```

## 5. 遗传编码

染色体包含每个 ZPM 的一个候选索引：

```text
[candidate_index(ZPM_001), candidate_index(ZPM_002), ...]
```

一个染色体描述全网所有 PM 的联合位置。不能逐 ZPM 完全独立优化，因为多个 PM 的 NRO 上联总管可能共享道路边，任务六的联合管道路由存在跨 PM 耦合。

初始种群包含：

- 道路约束几何中位点方案；
- 原 PM 吸附到合法道路后的方案；
- 每个 ZPM 的单基因扰动方案；
- 固定随机种子生成的空间分散方案。

交叉按 ZPM 交换候选索引。变异优先移动到当前位置附近的合法道路候选，少量使用全局随机候选。每代保留精英，最后对最优个体做道路邻域局部搜索。

## 6. 任务六反馈与目标

每个未缓存的染色体都将 PM 坐标写入任务六输入副本，然后直接调用任务六 `LayeredPlanner`。道路图、固定服务关系和任务六规则保持不变。

硬约束包括：

- 每个 ZPM 恰好一个 PM；
- PM 位于所属 ZPM 内，且距可施工道路中心线不超过 10 米；
- PM 具有可达的道路入口，并通过直连光纤接入管道网络；
- PM 容量满足固定 IMB 需求；
- 任务六不存在未解决路由；
- 所有 BPE/PBO 从所属 PM 可达；
- 固定 IMB、BPE、PBO 归属在优化前后完全一致。

可行解按以下字典序比较：

1. 硬约束违反数；
2. 各 ZPM 配线树最大越界比例；
3. 配线树总越界长度；
4. 全网实际安装管道长度；
5. 任务六估算施工成本。

实际安装管道长度按如下方式计算：

- `NRO_TO_PM_MAIN` 和 `PM_TO_GROUP_ENTRY` 按道路边 ID 取并集，只计算一次共享道路；
- `GROUP_ENTRY_TO_BPE`、`BPE_TO_BPE` 和 `BPE_TO_PBO` 按任务六输出长度累加；
- NRO、PM 到各自道路入口的直接光纤不计为管道。

ZPM 越界约束针对由 PM 发出的本地配线树。`NRO_TO_PM_MAIN` 是进入 PM 的上联总管，不要求整条位于目标 ZPM 内；但记录其进入目标 ZPM 后再次离开的长度用于审计。

## 7. 输出与验收

继续生成现有任务三接口文件：

- `pm_fixed_plan.json`
- `SITE_selected.csv`
- `ZNRO_selected.csv`
- `ZPM_selected.csv`
- `PTECH_selected.csv`
- `selection_scores.csv`
- `rejected_candidates.csv`
- `validation_report.json`

新增：

- `pm_candidates.geojson`：全部参与搜索的道路 10 米缓冲区候选，含道路入口和接入距离；
- `pm_optimization_history.csv`：每代最优指标；
- `pm_evaluations.csv`：所有实际调用任务六的染色体评价；
- `pm_optimization_report.json`：配置、基准、最优解和改善率；
- `task6_optimized_input.json`：保持任务六现有格式的最终输入；
- `task6_optimized_plan.json`：任务六完整输出；
- `pm_optimization.png`：分区、道路、BPE、初始/原始/最优 PM 与收敛曲线；
- `task6_optimized_plan.png`：任务六完整路由图。

最终验收要求为：任务六 `PASS`、PM 全部位于所属 ZPM 且距可施工道路中心线不超过 10 米、每个 PM 均生成直连光纤接入记录、固定关系未改变、无容量超限、BPE/PBO 全部可达，并输出相对原始方案和初始道路缓冲区中位点方案的长度变化。
