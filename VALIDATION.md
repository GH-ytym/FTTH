# 验证记录

## 0.8.0（2026-09-29）

- 后端完整回归：205 passed、2 skipped；ruff PASS。前端状态归并测试 3 passed；TypeScript 与 Vite 生产构建 PASS。
- npm audit（包含开发依赖）：0 vulnerabilities；Vite 6.4.3、Vitest 4.1.11。
- 实际 QGIS 运行时中的 QtWebEngine 生产页面：导航、真实 HTTP 消息、刷新恢复、表格/代码复制按钮、360px 无横向溢出、危险链接/越权动作拦截、令牌不进入 DOM，PASS。源码与安装后的插件均通过；脚本 scripts/verify_web_ui.py --installed，截图 runtime/release-0.8.0/web-*.png。正文夹具用于排版，不代表实际规划结果。
- QGIS 3.44.14，Qt 5.15.13，PyQtWebEngine 5.15.7；doctor 冻结算法 64 个文件哈希 PASS。
- 原始 51IMB CSV 尝试注册被现行合同校验拒绝（IMB/SITE/PTECH/INFRASTRUCTURE 缺少合同字段）。未修改业务数据绕过校验；本次真实完整规划、地图与导出 E2E 未通过验收。
- 当前终端未配置真实模型，真实模型效果未测。中文输入法人工操作、其他平台 WebEngine 和全部交互竞态仍需补充实测。
- 插件已备份至 `runtime/plugin-backups/ftth_demo0-20260929-094518`，0.8.0 已安装到 QGIS default profile；ZIP 清单及 SHA-256 核对通过。
- 原后台 0.7.0 继续运行，未终止用户进程；升级后需重启后端与 QGIS。

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

### 2026-09-26 存储顺序与轮询性能

- 修复：对象更新由 `INSERT OR REPLACE` 改为原地 UPSERT，rowid 保持创建顺序。此前审查进度会重写所属运行，使旧运行排到新运行之后，“沿用上一版/导出上次结果”可能选中旧运行；`use_latest` 另按创建时间取最新，兼容已乱序的旧数据库。
- 修复：首条消息由工作流直接答复（规划、澄清等）时不再跳过会话标题生成；算法超时的运行错误由 `TimeoutError` 改为说明超时秒数及 `FTTH_RUN_TIMEOUT`。
- 性能：新增按会话过滤的 `Store.find` 及 `json_extract` 表达式索引、`events(session, id)` 索引，替换插件轮询路径上的全表加载。最终回复写入时同事务删除该消息的累计流式快照（已由消息对象与最终 `assistant` 事件取代），`/history` 在 SQL 层排除它们；已有数据库中的旧快照未批量清理。
- 合成数据（40 会话、600 运行、1,200 条 1,500 字流式回复）对比：事件表 220.6 MB → 5.5 MB；`/runs` 45.7 → 3.1 ms，`/display` 40.3 → 0.6 ms，`/sessions` 23.3 → 0.7 ms，`/history` 84.4 → 2.0 ms。
- 子进程控制回归改用当前 Python 解释器，不再因缺少 `D:/osgeo` 失败或跳过；POSIX 下算法进程独立成组，取消和超时连同其子进程一起终止（Windows 仍用 `taskkill /T`）。修复类用例均确认在修改前失败。

### 2026-09-26 应用基础能力

- 打包：项目可安装（hatchling），`uv run ftth` 为三平台统一入口：`serve`、`stop`、`doctor [--fix|--all|--json]`、`config show|set|unset|path`、`plugin install|package`、`check-model`。PowerShell 脚本改为这些命令的封装；原 `stop.ps1` 依赖的 `server.pid` 从未被写入，现由 `ftth stop` 凭连接令牌请求 `POST /shutdown` 优雅退出。pytest 移入 dev 依赖组；版本号以 `pyproject.toml` 为唯一来源，测试校验插件 `metadata.txt` 一致。
- 配置：默认值 < `config.toml` < `FTTH_*` 环境变量 < 命令行，`ftth config show` 显示每项来源；拒绝未知项、错误类型和写入文件的密钥。
- QGIS 定位（`backend/qgis_runtime.py`）：显式 `qgis.python` / `qgis.root` → PATH 上的 QGIS 可执行文件 → 当前及 conda 登记的含 qgis 环境 → 平台标准位置；识别 OSGeo4W / 安装包（按实际目录选择 `Python3*`、`Qt5/Qt6`，不再写死 Python312、Qt5）、macOS 应用包、conda、Linux 系统四种布局，逐个启动探测 `qgis.core`、`osgeo.ogr`、matplotlib、openpyxl。结果在进程内缓存，失败一分钟后重试，`POST /runtime/refresh` 可立即重新检测；`/health` 返回 `qgis` 状态。缺少的纯 Python 依赖由 `ftth doctor --fix` 装入数据目录 `worker-packages/`，不修改 QGIS；完整规划在启动算法前检查依赖，缺失时立即说明而不是运行到任务四才失败。
- 日志：`logs/backend.log` 滚动文件 + 控制台；按已知密钥、令牌及 Bearer / api_key / `sk-` 模式脱敏，包括异常堆栈。回复、需求识别、审查和运行失败此前只保存异常类型名，现同时记录脱敏后的堆栈；插件高频轮询的访问日志仅在 debug 级别记录。
- 数据库：`PRAGMA user_version` 管理有序迁移（v1 为 0.6.0 表结构，v2 为会话索引）；升级既有数据前自动备份为 `*.pre-schema-v<版本>-*.bak`，结构版本高于程序时拒绝打开。
- CI：`.github/workflows/ci.yml` 在 Windows、macOS、Linux 运行 `uv sync --frozen`、`pytest` 与 CLI 冒烟；尚未在 GitHub 上实际运行。
- `pytest -q`：**191 通过**（macOS）。测试与开发机的 QGIS、配置文件及 `FTTH_*` 环境变量隔离；新增布局识别、发现顺序、伪造 `qgis` 包的真实探测子进程、配置分层与校验、迁移备份与降级保护、日志脱敏（含堆栈）、配置命令、插件安装备份与替换、打包清单、版本一致性、`doctor --json`、探测进程不继承模型密钥、`/runtime/refresh`、同目录重复启动保护，以及真实子进程的 `ftth serve` → `/health` → `ftth stop` 生命周期。
- 本机 QGIS-LTR 3.44.11（macOS）实测：无任何配置时 `ftth doctor` 自动定位 QGIS.app 并指出缺少 openpyxl；`ftth doctor --fix` 安装 openpyxl 3.1.5 到 `worker-packages/` 并固定 `qgis.python`，复检全部通过（64 个冻结算法文件哈希一致）。`ftth serve` 启动后经 HTTP 登记 `vendor/ftth/sample_data`、仅分区规划成功（2.9 秒）；第二次运行中取消，该运行的 QGIS 进程由 1 个降为 0；`ftth stop` 优雅停止，日志完整。以上使用临时数据目录，未改动仓库 `runtime/`、QGIS 安装或用户级 site-packages。
- 未完成：示例数据无 NRO、本机无 51IMB 数据，未实测完整规划、导出与独立审查；插件界面在 macOS QGIS 无法加载（内置 UI 库依赖 PyObjC，QGIS.app 的签名库校验拒绝第三方编译扩展），UI 验收需在 Windows 上运行；PowerShell 封装脚本本机无 PowerShell 未能执行；未做真实模型验证。插件代码与既有 HTTP 接口字段未改动。

### 2026-09-26 0.7.0 完善

- 插件界面可在 macOS QGIS 加载：内置 PyQt-Frameless-Window / darkdetect 在缺少 PyObjC（QGIS.app 签名库校验拒绝第三方编译扩展）或 xcffib 时改用纯 Qt 实现，补丁见 `THIRD_PARTY.md`，Windows 路径未改。修改前在 QGIS-LTR 3.44.11 中 `import Cocoa` 失败，修改后导入成功。
- macOS 真实 Qt 界面验收全部通过：`verify_streaming_ui.py`（含 `--fresh-install`：按 `ftth plugin install` 装入临时配置目录并确认自动连接）、`verify_conversations.py`、`verify_review_ui.py --workflow-ui`；截图 `runtime/ui-screenshots/` 已人工检查中文渲染和布局。`conversation_ui_smoke.py` 原断言工具英文名，自 0.6.0 插件改为中文活动标签后即失效且未再列入验收，已按现行为修正。
- 验收脚本此前把偏好写入用户真实插件设置（本机因此生成了 `com.ftth.demo0.plist`，已删除）；现经 `FTTH_PLUGIN_SETTINGS` 使用临时文件，复验后用户偏好目录无新增。
- 插件连接：去掉写死的开发机令牌路径；`ftth plugin install` 写入 `connection.json`（地址与令牌路径），打包与复制均排除该文件；地址修改后保存；`/health` 的 QGIS 状态在插件底部给出 `ftth doctor` 提示；运行列表仅在变化时重建。
- 模型密钥：`ftth secret set|delete|status` 与 `ftth serve --save-key` 使用系统凭据库，环境变量优先。本机 macOS 钥匙串读写删往返正常；测试使用内存替身，子进程使用 null 后端。
- 数据库迁移 v3 清除旧版本保留的累计流式快照，迁移前备份保留原件（测试确认 4 条原始事件在备份中、升级后仅余非快照事件）。
- lint：`ruff check`（pyflakes + 语法）通过并加入 CI；修正未使用导入等 23 处，`qgis_ui_smoke.py` 中 `del` 前的闭包为有意保留（控制 exitQgis 前的销毁顺序），加注释说明。
- 版本 0.7.0（`pyproject.toml`、插件 `metadata.txt` 一致，由测试校验）；更新说明 `RELEASE-0.7.0.md`。
- 最终：`pytest -q` **196 通过**；本机真实 QGIS 上 `ftth doctor --fix`、`ftth serve`、登记、仅分区规划、取消（算法进程 1→0）、`ftth stop` 与迁移后结构版本 3 均通过；临时数据目录已清理。
- 未完成：Windows 上本版的封装脚本、界面与算法；真实 51IMB 完整规划、导出与审查；真实模型的需求识别与审查；GitHub CI 实际运行。

### 2026-09-26 0.7.0 真实模型与 Windows 验证

- 真实模型：本机 Antigravity 代理（`http://127.0.0.1:8045/v1`，OpenAI 兼容）上的 `gemini-3.8-flash`，经用户同意使用。与默认 Qwen 不是同一模型，结论不能直接外推到千问。
  - `ftth check-model`：工具调用 + 结果续答 PASS（约 7 秒）。
  - `verify_conversation.py --real-model`：四轮结构与禁止执行断言通过；逐轮核对语义正确——歧义“容量”先澄清，“NRO”短答续接原问题且不执行，按源码依据说明 ZNRO 目标簇数、不决定 PM 数、当前流程不走 15% 折减、预检不等于可行、完整规划仅支持 1 个 NRO。0.6.0 真实界面复现的“无法读取距离实现 / 无法判断多 NRO”问题未再出现。回答保存于 `runtime/conversation-real-model.json`。
  - 对话 benchmark（单次运行）：routed 15 轮结构全过、路由 15/15（含明确规划/导出正样本）；react-readonly 13 轮结构全过。26 个语义轮次由 claude-opus-5-5 按 rubric 审核（AI 审核、非人工、可见架构标签）：共同只读轮次语义通过率 routed **84.6%（11/13）**、react-readonly **69.2%（9/13）**；P50/P95 耗时 12.1/25.7 s 对 8.1/14.2 s。两者都在“坐标吸附”把单模块契约推广到全部阶段、在“引用执行”中出现内容错误（routed 编造导出物料清单并把 GA 说成路由算法；react 把 ZPM 称为中继分区）；react 另有未先澄清容量类型、把 1018−800 当作实际缺口两处失败。样本量小，只作方向参考；报告与审核意见在 `runtime/benchmarks/`。
  - 独立审查 Agent：示例数据真实 QGIS 分区运行后，真实模型读取全部 4 项证据，结构化结论与引用校验通过，指出超 48 端口楼栋未拆分的警告，分区范围正确判定不适用自动重算（`runtime/review-real-model.json`）。首次运行暴露问题：模型摘要写“审查结论为通过”，程序因确定性警告改判为“需要复核”，报告界面二者并列显示而自相矛盾。已修复：程序改判时在摘要前注明原结论、新结论与原因，模型原文保留；新增回归。复跑时模型自行给出 needs_review。
- Windows 11（经 `ssh wsl` 调用原生 PowerShell 5.1，用户授权；该机 `C:\OSGeo4W` 的 QGIS 3.44.4，并非 0.6.0 使用的 `D:\osgeo` 开发机，无 51IMB 数据）：工作树复制到 `%TEMP%` 独立目录、独立数据目录运行，结束后删除，未改动用户 QGIS 配置。
  - `uv sync --frozen`（uv 0.9.18 读取本版锁文件）、`ruff check`、`pytest -q` **195 通过、1 跳过**（POSIX 专属孙进程测试）。
  - 零配置 `ftth doctor` 自动定位 `C:\OSGeo4W`（标准位置），算法依赖齐全，64 个冻结算法哈希一致。
  - 真实 Qt 界面：`verify_streaming_ui.py --fresh-install`（自动连接）、`verify_conversations.py`、`verify_review_ui.py --workflow-ui` 全部通过——内置 UI 库补丁未影响 Windows。
  - PowerShell 封装：`start.ps1` 启动、HTTP 登记示例数据、仅分区规划成功（1.8 秒）、`stop.ps1` 优雅停止并清理状态文件；`install_plugin.ps1` 与 `-Update`（APPDATA 重定向到临时目录）完成安装、备份与 `connection.json`；`check_env.py` 封装通过。运行中取消：算法进程出现后立即取消，进程由 1 个降为 0、状态 cancelled。（首次测量有误：运行已先完成、计数包含查询进程自身，已改正后重测。）
  - 首次在新解压目录运行时 1 项测试失败、之后连续 4 轮均通过：冷启动（编译 .pyc、Defender 扫描）使耗时翻倍，三处测试的轮询上限仅 1–2 秒。已放宽为提前退出的 15 秒上限；`runtime/`、`dist/` 加入 ruff 排除（非 git 副本中 .gitignore 不生效，插件备份被扫描）。服务状态测试改为不比较 PID（Windows venv 的 python.exe 为启动器）。
- 本地最终：`pytest -q` **196 通过**，`ruff check` 通过。
- 仍未验证：真实 51IMB 完整规划、导出与完整规划的独立审查（数据只在原开发机）；千问模型本身；GitHub CI 实际运行。
