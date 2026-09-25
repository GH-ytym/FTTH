# 任务五：基于任务六管网的分层光缆连接

任务五不再规划道路、管道或设备位置。它读取任务六已生成的管网和设备归属，按固定层级生成光缆连接清单：

```text
NRO -> PM -> BPE -> PBO -> IMB
```

同一条任务六管网可承载任意多条光缆。本任务明确不累计、扣减或校验管道容量。

## 光缆选型

- `NRO -> PM` 是主干，固定选用 `144` 芯；
- `PM -> BPE`、`BPE -> PBO`、`PBO -> IMB` 从 `24/48/72/144` 芯中选择满足需求的最小规格；
- 需求芯数取下游设备的标称容量与下游用户端口数两者较大值；
- PM 具备分光能力，`NRO -> PM` 不受 PM 汇总用户端口数限制：始终是一根 144 芯主干，不产生超限告警；
- 非主干链路需求超过 144 芯时，当前版本仍输出 144 芯并标记复核，后续可扩展为同管多缆拆分。

## 输入

默认读取：

```text
../任务6/output/layered_plan.json
```

推荐输入任务六的分层方案，它应包含：

- `fixed_assignments.nro_sites`、`fixed_assignments.pm_sites`；
- `bpe_plan`、`pbo_plan`、`imb_points`；
- `duct_routes`，并提供 `id`、`route_type`、`from_code`、`to_code`、`coordinates`、`length_m`。

任务五识别以下任务六线路作为物理管网证据：

| 光缆层级 | 任务六线路类型 |
| --- | --- |
| NRO -> PM | `NRO_TO_PM_MAIN` |
| PM -> BPE | `PM_TO_GROUP_ENTRY` + `GROUP_ENTRY_TO_BPE` + optional `BPE_TO_BPE`，或 `PM_TO_BPE` |
| BPE -> PBO | `BPE_TO_PBO` |
| PBO -> IMB | 楼内末端连接，不要求道路管网记录 |

普通任务六集成方案若缺少 NRO/PM 坐标或线路端点，任务五仍会生成逻辑连接，但将该连接标为 `REVIEW_REQUIRED`，不会虚构物理管线路由。

## 运行

在工程根目录执行：

```powershell
python .\任务5\run_task5.py .\任务5\task5_config.json
```

使用任务六分层样例：

```powershell
python .\任务5\run_task5.py .\任务5\task5_geo_config.example.json
```

## 输出

默认写入 `任务5/outputs/fiber_links/`：

- `fiber_link_plan.json`：完整设备、连接、任务六管网引用和选缆依据；
- `fiber_links.csv`：可用于清单、预算和人工复核的光缆段表；
- `fiber_link_validation.json`：层级关系和缺失管网引用；
- `fiber_links.geojson`：可直接由 QGIS GEOAgent 加载的连接图层。

## 校验

```powershell
python -m unittest discover -s .\任务5\tests -v
```

## Visualization

Generate a Task-5 review map with stage colors, cable-core line widths, device nodes, and a summary panel:

```powershell
python .\任务5\visualize_task5.py
```

The default input is `任务5/outputs/fiber_links/fiber_link_plan.json`; the PNG is written to `任务5/outputs/fiber_links/fiber_link_overview.png`. For the production plan:

```powershell
python .\任务5\visualize_task5.py --input .\任务5\outputs\fiber_links_production\fiber_link_plan.json --output .\任务5\outputs\fiber_links_production\fiber_link_overview.png
```
