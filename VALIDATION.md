# 验证记录

## 0.5.0（2026-09-26）

- 最终回归：**95 passed, 1 skipped**。跳过项是当前 Windows 环境没有创建符号链接的权限；目录越界、文件哈希篡改及输入快照变更检查均已执行。
- 新增覆盖：真实 LangChain 审查工具循环、结构纠正与累计生成预算、模型超时／错误隔离、无模型降级、空报告不能通过、伪造证据引用、注入内容不能获得执行工具、跨会话访问、并发一次修复、策略撤销、后续规划取代、排队修复授权复查、取消和重启、补审幂等及结果比较。
- QGIS 3.44.14 环境和 64 个冻结算法文件 SHA-256：PASS。`vendor/` 未修改。
- 真实 51IMB + 真实算法 + HTTP API + 脚本化审查模型 + 正式导出：PASS，运行 `run_54e1c95de74843dc`；验收记录 `runtime/release-0.5.0/e2e-bf9d829a/acceptance.json`。最终证据合同改动后，再对同一批真实成果补审通过，未再次规划。
- 默认 profile 安装后的实际 QGIS/Qt 验收：审查报告、补审、独立状态、执行／检查模式开关、历史恢复及 480px 布局 PASS；原有 SSE 流式回复、计时、复制及会话切换回归 PASS。
- 插件更新前备份：`runtime/plugin-backups/ftth_demo0-20260926-101925`。未终止用户已打开的 QGIS 或旧后端，需重启加载新版。
- 当前终端没有真实模型配置，**未执行真实千问／其他供应商审查效果验证**。脚本化模型验证不等于真实审查准确性验收。

以下为历史版本记录，不代表当前测试数量。

## 2026-09-25 历史记录

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
# 0.6.0 固定工作流验收（2026-09-26）

- 自动化回归 118 通过、1 跳过（Windows 链接权限）。覆盖缺失意图、执行权限、并发选择、参数变体防重、事务失败回滚、旧选项、撤销授权、输入篡改、模型超时与取消、数据库迁移及重启不重放。
- `verify_review_ui.py --workflow-ui`：真实 QGIS Qt 选择卡片、恢复、重复点击、审查步骤与动画通过。
- `verify_streaming_ui.py`：真实 SSE 正文增长、历史恢复、复制及窄屏通过。
- `verify_reviews.py --workflow`：真实 51 IMB 数据规划、假模型独立审查和正式导出通过，记录 `runtime/release-0.6.0/e2e-7c9d84ae/acceptance.json`。
- `check_env.py`：QGIS 3.44.14，冻结算法 64 文件哈希 PASS。
- 真实 Qwen 需求识别及审查尚未验收：当前执行进程无模型凭据。可在已配置模型的终端运行 `verify_reviews.py --workflow --real-model`，不得以假模型结果替代。


### 2026-09-26 聊天进度展示修订

- 同一次主流程、算法运行和审查各复用状态行，步骤文本原位更新，日志按用户展开状态显示；不再生成通知专用的零秒 Agent 状态行。
- 工作流开场显示本次安排，计算完成通过状态行呈现，审查结束合并说明计算结果、审查分级、优先复核项及重算状态。补审继续单独通知。
- 历史按事件顺序恢复，保留消息快照用于中断和流式恢复；按工作流更新时间拒绝旧响应，避免已提交后重新出现选择卡片。
- 固定报告/假模型自动回归与真实 QGIS Qt 界面验证覆盖：默认折叠、同一行更新、手动展开、选择卡片恢复、乱序响应、重复点击、普通聊天流式和复制。真实在线模型未新增验证；算法与自动修复权限未修改。

### 2026-09-26 自然语言执行授权

- 移除聊天执行模式开关。LLM 识别本轮动作，服务端记录用户原文和动作授权，并验证消息、会话、项目及工作流状态；规划授权不能用于导出，历史记录和后台通知不能提供新授权。旧 `allow_actions` 字段仅兼容接收并忽略。
- 设置页保存对话自动重算偏好，手动运行单独记忆。关闭自动重算仍完成独立审查；启用时沿用一次额度和搜索参数白名单。
- `python -m pytest -q`：140 通过、1 跳过（Windows 链接权限）。覆盖无模式的规划和导出、旧字段不能授权或拒绝执行、查询/讨论/澄清分支无执行权、授权篡改、关闭重算仍审查、一次重算限制及原工程策略校验。
- `verify_review_ui.py --workflow-ui` 和 `verify_streaming_ui.py` 通过。真实 QGIS Qt 验证无执行模式的发送、持久化偏好、历史恢复、选择卡片、重复点击及 480px 布局；已查看 `runtime/release-0.5.0/rerun-preferences.png` 和 `workflow-submitted.png`。
- `verify_reviews.py --workflow` 通过：真实 51 IMB 数据完成规划、独立审查和导出，规划与导出均从不含 `allow_actions` 的聊天消息提交。记录：`runtime/release-0.6.0/e2e-61972dd9/acceptance.json`。
- 上述模型环节使用脚本模型，验证工作流和授权边界，不作为自然语言识别准确率证明。当前终端没有真实模型配置；在线模型验证仍可在配置后运行 `verify_reviews.py --workflow --real-model`。

### 2026-09-26 上下文与主任务协调改造

- 完整回归：155 通过、1 跳过（Windows 链接权限）。新增覆盖现场四轮对话、澄清绕过模型 checkpoint 的旧历史、旧工具轨迹隔离、目标修订、只读限制、切换话题、晚到审查、重算归属原子提交及失败回滚、取消、重启、历史分页隔离、保护澄清链及消息更新不改变轮次顺序。
- `verify_review_ui.py --workflow-ui` 和 `verify_streaming_ui.py` 通过真实 QGIS Qt/SSE 验证；问答与澄清不再留下执行卡片。命令使用 `python -X utf8`，避免 PowerShell 默认 GBK 无法输出第三方组件提示中的 emoji。
- `verify_reviews.py --workflow` 通过真实 51 IMB 规划与导出，并核对两个主任务、算法/导出/审查 worker 的父任务及版本。记录：`runtime/release-0.6.0/e2e-918603cb/acceptance.json`。模型为脚本模型。
- `verify_conversation.py --real-model` 已提供隔离真实模型验收入口；当前终端无模型配置，预检退出，未宣称在线模型语义验收通过。
- 健康检查新增 `context_protocol=task-v1`，插件能提示旧后端需要重启。源码调研、固定提交与方案依据见 `audit/CONTEXT_ORCHESTRATION_RESEARCH-20260926.md`。
- 已重新打包并安装到 QGIS default profile，168 个文件哈希与源码一致；`verify_review_ui.py --workflow-ui --installed` 通过。原插件备份：`runtime/plugin-backups/ftth_demo0-20260926-144454`。用户正在运行的后端和 QGIS 未自动重启。

### 2026-09-26 真实 UI 语义复现与源码依据

- 使用 Windows computer-use 实际向 QGIS 侧栏发送两轮问题，在线模型复现：无法读取距离计算实现、从未报错推断无法判断多 NRO 支持。原文保存在 `runtime/grounding-ui-before.json`。本轮未提交规划、导出或数据修改。
- 修复加入按主题读取源码证据的工具、预检与可行性区分、完整规划一个 NRO 的能力说明，以及待澄清问题清除猜测参数的规则。
- 完整回归 160 通过、1 跳过；新增 `tests/test_grounding.py` 验证上述接口契约与实际 LangChain 工具分派。真实模型修复后的验收待后端重启后完成，不能由脚本模型代替。
- 调研与实现记录：`audit/GROUNDING_UI_RESEARCH-20260926.md`。

### 2026-09-26 二次语义修正与 benchmark

- 已完成非阻塞question字段误触发澄清、ZNRO/ZPM混淆及15%分支范围说明的修正。完整回归 **167通过、1跳过**。
- 新增12场景15轮的 `benchmarks/dialogue_v1.json`，现有路由+ReAct与只读ReAct消融均跑通离线smoke。脚本模型故意错分动作，路由评分确实捕获失败；所有语义评分保持未审核，没有把结构通过计为回答正确。
- 评测器测试覆盖副作用拦截、无项目场景、实际工具轨迹、执行正样本的只读消融跳过、数据集/轮次一致性以及未完成语义审核不计算通过率。报告记录后端源码摘要、模型公开配置、依赖版本及耗时。
- 真实benchmark预检因当前终端没有模型配置退出；尚无两架构的真实效果或成本优劣结论。命令、逐轮审核标准与对比方法见 `benchmarks/README.md`。
- 已重新打包。上述最后两处后端修正仍需重启后端加载，QGIS插件本轮没有新增改动；修复后在线语义验收仍待完成。
