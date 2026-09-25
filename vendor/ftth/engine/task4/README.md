# 任务四运行说明

任务四原脚本需要外部完整样例 Shape 目录，至少包含 `IMB`、`BOITE`、`SITE`、`PTECH` 四个点图层。当前仓库没有随附这些生产 Shape 文件，因此 `build_sample_data.py` 会从 `任务6/data/layered_planning_production_input.json` 生成一套本地可运行样例数据。

## 当前 BPE 规划逻辑

现在的 `plan_pbo_bpe.py` 分两层做规划：

1. 先调用任务二 IMB-only 分区，把整个片区分成若干 PM/ZPM 区域。
2. 任务二 PM/ZPM 区域容量限制为 `576` 端口，不把它强行拆成 144 端口小区。
3. 在每个 576 端口区域内部，任务四再按 BPE 容量 `144` 做空间聚类。
4. BPE 聚类设置 `144` 端口硬容量上限。程序将 PBO 分配到最近且仍有剩余容量的 BPE 候选簇；任一候选簇超出 `144` 端口即作废，并自动增加 BPE 簇数重新规划。所有可行方案中，以 `144*80%=115.2` 端口为目标进行需求评分，需求越接近该目标评分越高；同分时选择空间更紧凑的方案。
5. 每个最终 BPE 的位置使用簇内 PBO 集合的连续几何中位数（Weiszfeld 算法），即总距离最小的空间点，不限定为某个 PBO 点。

因此，任务二负责“大区域 PM/ZPM 划分”，任务四负责“区域内 BPE 容量约束空间聚类”。

### 固定 PM 接口（任务三/RL）

可以通过 `--fixed-pm <pm_fixed_plan.json>` 使用任务三或强化学习已经选好的 PM。固定模式只替换 PM 编码、坐标和区域成员关系，保留 PBO/BPE 的需求与成员关系，不再让任务四生成 `PM-T2-*` 覆盖上游结果：

```powershell
python .\plan_pbo_bpe.py .\data\completed_example .\outputs\pbo_bpe_fixed --fixed-pm <pm_fixed_plan.json>
```

固定 PM 文件至少包含 `pm_sites[]`，每条记录需要 `id`、`x`、`y`、`zpm_id/partition_id`、`assigned_imb_ids` 和 `capacity`。任务三目录的 `task3_to_task6_adapter.py` 可生成该契约。

## 一键运行

在仓库根目录执行：

```powershell
python .\任务4\run_task4.py
```

也可以单独运行：

```powershell
python .\任务4\generate_pbo.py
python .\任务4\plan_pbo_bpe.py
```

## 输出位置

- `任务4/data/completed_example/`：自动生成的 `IMB/BOITE/SITE/PTECH` 样例 Shape 数据。
- `任务4/outputs/pbo_only/`：单 PBO 生成结果、CSV、Excel 和参考对比图。
- `任务4/outputs/pbo_bpe_plan/task2_pm_partition/`：任务二 IMB-only 分区输入、分区 JSON 和分区图。
- `任务4/outputs/pbo_bpe_plan/PM_partition_summary.csv`：任务二 PM/ZPM 区域及其内部 BPE 数量。
- `任务4/outputs/pbo_bpe_plan/BPE_cluster_validation.json`：BPE 目标需求评分和候选方案校验。
- `任务4/outputs/pbo_bpe_plan/IMB_BPE_cluster_colors.png`：按叶级 BPE 服务簇着色的核查图；同色圆点 IMB 由同色方块 BPE 服务。
- `任务4/outputs/pbo_bpe_plan/SITE_PM_PARTITION.*`：原 NRO 加任务二生成的 PM 分区点。
- `任务4/outputs/pbo_bpe_plan/BOITE_ALGORITHM.*`：PBO/BPE 分层规划结果。
- `任务4/outputs/pbo_bpe_plan/PTECH_ALGORITHM.*`：由 PBO/BPE 派生的新 PTECH 点。
- `任务4/outputs/pbo_bpe_plan/PBO_BPE_algorithm_plan.xlsx`：规划汇总、PBO、BPE、PM 分区和成员关系表。

## PBO 点位规则

每个有需求的 IMB 对应一个 PBO，但 PBO 不再使用 IMB 原始坐标。系统在距服务 IMB
5 m 的候选环上选择点位，并优先选择远离其他 IMB 和已布设 PBO 的方向。PBO 与任一
IMB 的最小允许间距为 1 m；规划输出会拒绝写出不满足该约束的结果。

`IMB_PBO_BPE_mapping.csv` 记录 `PBO_LOCATION_SOURCE`、`PBO_IMB_OFFSET_M` 和
`PBO_BEARING_DEG`；`PBO_placement_validation.json` 给出全量不重合校验。单 PBO
输出的 CSV/Excel 也包含偏移距离和方位。

可通过 `generate_pbo.py` 或 `plan_pbo_bpe.py` 的 `--pbo-offset-m` 调整偏移半径；
该值必须大于 0，默认是 5 m。
