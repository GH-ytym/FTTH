# 任务六：分层固定归属管网规划

任务六仅保留一个算法：`layered_fixed_assignment_shared_edge_search`。

它固定任务二的 PM/IMB 分区、任务三的 NRO/PM 选址和任务四的 BPE/PBO 归属，在遥感道路、建筑群和入口约束下生成可施工的分层管网：

```text
NRO -> PM -> 建筑群入口 -> BPE -> PBO
```

任务五读取本任务输出的 `duct_routes`，继续生成 `NRO -> PM -> BPE -> PBO -> IMB` 的光缆清单与芯数选型。

## 输入契约

以 `data/layered_planning_input.example.json` 为生产数据模板；遥感数据可内嵌或引用相对 JSON/GeoJSON 路径。运行示例使用可复现的小数据集 `data/layered_planning_simulated_input.json`。

必要数据包括：

- NRO、PM 和 PM 固定服务的 IMB；
- 道路、建筑、建筑群、社区入口、建筑群入口和建筑入口；
- 固定 BPE/PBO 关系；
- 可选的任务二 ZPM 分区与水域成本区。

## 算法流程

1. 构建道路拓扑，并在折点和交叉点切分为图边；
2. 删除穿越建筑或禁止施工的道路边，对水域等成本区增加施工代价；
3. 为 `NRO -> PM`、`PM -> 建筑群入口` 生成多条合法候选路径；
4. 主干段对所有 PM 联合选择候选路径：在候选组合可控时穷举求解最小唯一铺设长度，规模较大时使用有界 beam search；
5. 以建筑群入口为根生成 BPE 共享连通树，允许相邻 BPE 之间使用 `BPE_TO_BPE`，再生成 `BPE -> PBO` 路线；
6. 运行 PTECH 子功能单元：在所有主干及入区管道路由的每个拐点设置 `CHAMBRE`，直线段按不超过 200 m 的均匀间距补点；在 BPE 坐标生成 `CHAMBRE`，在 PBO 坐标生成 `AUTRE`；
7. 输出道路节点、管网段、弱电技术点、固定归属和未解决问题。

主干优化目标为 `unique_edge_length_m`，以实际只需铺设一次的道路边长度为第一目标、施工成本为第二目标；输出会明确标记 `PASS` 或 `REVIEW_REQUIRED`。

## 运行

```powershell
python .\任务6\code\plan_task6.py
python .\任务6\code\visualize_task6_plan.py
```

指定生产输入和成果路径：

```powershell
python .\任务6\code\plan_task6.py --input <layered-input.json> --output <layered-plan.json>
python .\任务6\code\visualize_task6_plan.py --input <layered-plan.json> --output <layered-plan.png>
```

## 本次生产运行假设

`code/preparation/build_task6_production_input.py` 生成的生产输入已按当前业务补充执行：NRO 固定为
`(-8.5317504, 33.2237864)`（经度、纬度），仅纳入 51 个有 IMB 的建筑；所有识别道路按
可开挖处理，管道容量按 `unlimited` 处理。建筑群按固定 PM 重新组织，任意同一群内
两个 IMB 建筑的共用边或不超过既有 15 m 群组阈值的空隙均可作为建筑群入口，入口不再
要求与道路边界精确相交。

上述道路属性和容量是本次运行的显式临时假设，规划结果会在 `validation.data_quality_status`
中标记为 `PASS_WITH_EXPLICIT_ASSUMPTION`。

## 输出

- `routes` / `duct_routes`：分层管网路径（包含可复用的 `BPE_TO_BPE` 共享段）；
- `fixed_assignments`：PM、BPE、PBO、IMB 固定服务关系；
- `road_nodes`、`weak_current_points`：供 QGIS GEOAgent 加载的点位；
- `ptech_plan`：PTECH 规划成果，字段 `subtype` 为 `CHAMBRE` 或 `AUTRE`，补齐 Excel PTECH 必填字段，并保留 `source_route_id`、BPE/PBO 归属和坐标；所有规划点默认 `needs_review=1`、`review_status=待场勘`；
- `ptech_validation` / `validation.ptech`：最大间距、拐点点位、BPE/PBO 同坐标点和数量统计；
- `validation`：容量、入口、路径、PTECH 与未解决问题校验。

## PTECH 子功能单元

实现位于 [`code/ptech_planner.py`](code/ptech_planner.py)，输入任务六已选出的 `duct_routes`、`bpe_plan` 和 `pbo_plan`，输出独立的 `ptech_plan`。`ptech_max_spacing_m` 可在 `planning` 中配置，默认 200 m；距离和插值统一在数据中心所在的局部 UTM 中计算，输出仍为 EPSG:4326 `[经度, 纬度]`。`planning.ptech_field_defaults` 可提供 `REF_PLAQUE`、产权、管理单位、城市等项目默认值；未提供时使用待核查占位并保持场勘标记。地图脚本会以方形标记显示 `CHAMBRE`，以叉号显示 `AUTRE`，便于核对拐点、均匀间距以及 BPE/PBO 同坐标点。

## Building entrance rule

Task 4 fixes every PBO on an offset ring beside its served IMB point. Task 6
uses that fixed PBO coordinate directly as the building access point. For a BPE,
the closest PBO assigned to that BPE is used as the group-entry-to-BPE access
point. The BPE-to-PBO route therefore ends at the PBO without adding a second,
inferred building entrance point. Source building entrance records remain a
compatibility fallback only when no matching PBO location is available.

## Shared BPE conduit rule

The BPE layer is planned as a rooted shared conduit tree. A group entry is a
legal root attachment, and adjacent BPEs may be joined with `BPE_TO_BPE`
routes. Every BPE and PBO must remain reachable from its owning PM, but a
separate PM-to-every-BPE conduit is not required. This permits a shorter shared
network while preserving the endpoint relationships consumed by Task 5.

## Data Preparation

Task-6-specific recognition cleaning and input assembly scripts are in
[`code/preparation`](code/preparation/README.md). Run the preparation stages
before the planner only when their upstream source data changes.
