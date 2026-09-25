# 验证记录（2026-09-25）

## Qwen 接入补充

- 已适配 Qwen3.8-Flash 与用户指定的千问兼容端点；支持 `DASHSCOPE_API_KEY`，增加隐藏输入启动脚本。
- 当前完整测试为 **11 项通过**，新增密钥环境隔离、模拟HTTP流式工具调用及reasoning_content回传、Responses请求配置与思考开关测试。
- Qwen Chat Completions 默认启用思考和流式传输；通用LangChain消息适配器丢失的reasoning_content由Qwen子类保留，最终界面回复不包含该字段。
- Responses分支目前只验证请求构造，没有真实供应商调用。Chat Completions的工具循环使用模拟HTTP响应验证，未冒充真实模型验证。
- 本次没有在环境中读取到实际API密钥，因此未发起真实Qwen请求。`scripts/start_qwen.ps1` 输入密钥后会执行真实只读工具探测，再启动服务。

## 已验证

- `pytest -q`：8 项通过。覆盖严格参数、缺NRO与路由缺项、会话隔离、重复提交、参数继承、重启中断、显示回执、快照变更、真实子进程取消及超时、真实LangChain工具循环和SQLite会话记忆。
- `check_env.py`：QGIS 3.44.14，matplotlib和openpyxl可导入，64个算法清单文件SHA-256校验通过。
- 使用原工程51IMB的Task1真实CSV，经本项目API登记快照，完成任务一、二、四初算、三GA、四定稿、六、五和最终八图层。
- 第一版 `run_4e38b6b0d8874be9` 成功，完整交付导出成功；参数ZPM容量576、GA种群14/代数10，使用正式基础设施。
- 第二版 `run_c1455d11f6db4bf2` 通过API继承第一版，将ZPM容量改为600、GA代数改为4，成功；其余参数沿用。
- 两版比较得到真实的参数差异与图层/线路数量。数量相同不意味着几何或质量相同，未声称优化有效。
- 新插件Panel通过Qt异步HTTP接收后端显示命令，调用真实PyQGIS加载器，向后端回传成功。重复显示不产生重复图层，只显示管线的指令通过验证。
- PyQGIS实际渲染地图并保存QGIS工程，检查过地图和侧栏图片。
- `dist/ftth_demo0.zip` 已打包，顶层只有独立插件目录，不包含后端、密钥或缓存。

## 第一版成果数量

| 图层 | 要素数 |
|---|---:|
| IMB | 51 |
| SITE | 3 |
| BOITE | 60 |
| CABLE | 113 |
| PTECH | 201 |
| INFRASTRUCTURE | 63 |
| ZNRO | 1 |
| ZPM | 2 |

任务六的 `duct_routes` 数量为64；最终INFRASTRUCTURE图层为63个要素，两者属于不同输出口径。

## 输出位置

- `runtime/smoke_session.json`：测试会话与项目引用。
- `runtime/smoke_planning.json`、`smoke_followup.json`、`smoke_export.json`：批次报告。
- `runtime/comparison.json`：两版比较。
- `runtime/qgis_ui_report.json`：图层数与HTTP加载回执。
- `runtime/qgis_map.png`、`sidebar_connected.png`：实际渲染截图。
- `runtime/51imb_demo.qgz`：可打开查看的QGIS工程。
- `runtime/runs/`：各批次原始日志与成果。

## 尚未验证与已知限制

- 没有配置真实模型端点或密钥。测试中的模型是脚本化模型，确实驱动LangChain工具循环，但不证明真实模型的理解能力或供应商兼容性。
- QGIS窗口控件和地图加载在独立无窗口Qt测试进程中验证，尚未人工逐项点击桌面插件操作流程。
- 原小样例缺NRO、最终导出要求恰好两个PM的核心算法限制未改动。
- 原插件文件向导采用较完整的字段合同；51IMB字段较少，回归脚本生成了仅映射真实字段的专用配置。普通用户可通过“导入已有输入配置”使用它，没有填造地址或设施。
- 后端与插件首版为同机单用户部署。多用户、分布式队列、原始影像识别和算法断点恢复不在本版范围。
- 规划与合同检查通过不等于算法准确性或现场业务验收通过。
# Fluent UI 0.2.0 verification

- Bundled QFluentWidgets 1.11.3 with Frameless Window 0.8.2 and darkdetect 0.8.0; reused OSGeo4W Qt5, no replacement Qt runtime.
- Rendered and visually inspected chat, data, runs and settings, including 480px narrow layout; screenshots in `runtime/fluent-*.png` (synthetic UI examples).
- Real PyQGIS widget checks cover multiline composition, Markdown, all 16 parameter controls, send and manual validation payloads.
- `qgis_ui_smoke.py --http` passed against the running backend: actual async Qt HTTP polling, eight map layers and successful display acknowledgement.
- Backend regression: 26 passed. Plugin ZIP includes dependencies and license metadata; default profile updated with backup in `runtime/plugin-backups`.
- User must restart the already-open QGIS process to load the updated plugin. The interactive desktop was not restarted automatically to preserve its map state.
