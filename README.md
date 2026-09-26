# FTTH Agent for QGIS

基于 **QGIS 桌面插件 + 主 Agent + 独立审查 Agent + 独立算法进程** 的 FTTH 光纤接入网规划工作空间。通过中文对话检查数据、提交规划、调整参数、比较方案，在 QGIS 中展示和导出成果。

当前插件版本 **0.6.0**。面向同机、单用户 Windows 环境，仍处于实验阶段。

## 功能

- **对话规划**：LangChain 工具调用与 LangGraph 会话记忆，支持 Qwen 和支持 tool calling 的 OpenAI 兼容接口。
- **实时回复**：正文流式显示、动态状态与耗时、Markdown 和复制，切换会话后恢复已收到的正文。
- **自然语言执行**：明确要求规划或导出即可执行，无需切换模式；LLM 识别本轮意图，后端验证动作授权、数据及工程策略。
- **可追溯运行**：输入快照与 SHA-256 校验、参数继承、取消、报告和方案比较。
- **地图与交付**：成果按批次显示，收到 QGIS 回执后确认展示；完整规划可导出 GPKG、SHP、表格和规划图。
- **运行保护**：数据目录单实例锁、独立子进程、超时与取消处理；会话标题过滤拒答和无效输出。

## 架构

```text
QGIS 插件（Qt5 / Fluent UI）
         │ 本机 HTTP + Bearer Token
         ▼
FastAPI 后端（Python 3.12）
  ├─ 主 Agent：识别需求、只读问答
  ├─ 固定工作流：校验、规划、比较、展示、导出
  ├─ 独立审查 Agent：只读审查与受限修复建议
  ├─ SQLite：会话、事件、任务、模型上下文
  └─ 单任务队列 → QGIS Python 独立进程 → FTTH 算法
```

算法流程：**任务一 → 任务二 → 任务四初算 → 任务三 GA → 任务四定稿 → 任务六 → 任务五 → 最终八图层**。仅分区模式在任务二后结束。

后端不导入 PyQGIS；插件复用 QGIS 自带 Qt/Python，不在 QGIS 环境中安装 LangChain。

## 环境要求

| 组件 | 要求 |
|---|---|
| 操作系统 | Windows；未验证 Linux、macOS、Qt6 |
| QGIS | QGIS 3 / Qt5；开发环境验证版本为 OSGeo4W QGIS 3.44.14 |
| QGIS 路径 | 默认 `D:\osgeo`，通过 `FTTH_QGIS_ROOT` 修改 |
| 后端 Python | 3.12，通过 uv 管理依赖 |
| 包管理器 | [uv](https://docs.astral.sh/uv/) |
| 模型（可选） | 支持工具调用的接口及对应 API Key |

当前 QGIS 适配使用 OSGeo4W 的 `bin/python.exe`、`apps/Python312` 和 Qt5 目录布局。修改根目录不会自动适配其他 Python/Qt 主版本。

## 快速启动

以下命令在 PowerShell 中执行。

### 1. 获取代码并检查环境

```powershell
git clone https://github.com/GH-ytym/FTTH.git
cd FTTH
uv sync --frozen

# QGIS 不在默认路径时设置
# $env:FTTH_QGIS_ROOT = 'D:\osgeo'

.\.venv\Scripts\python.exe scripts/check_env.py
```

检查在临时目录运行，验证 PyQGIS、matplotlib、openpyxl 和算法完整性，不修改已有会话数据。

### 2. 启动后端

使用千问：

```powershell
.\scripts\start_qwen.ps1
```

按隐藏输入提示填写 API Key；已有 `FTTH_API_KEY` 或 `DASHSCOPE_API_KEY` 时沿用。脚本先用合成数据进行一次真实工具调用探测，再启动服务；探测及后续模型调用可能产生供应商费用。

默认配置为 `qwen3.8-flash`、`https://maas.qianwenaiapi.com/compatible-mode/v1`、Chat Completions 流式协议。参考：[千问模型文档](https://platform.qianwenai.com/docs/developer-guides/getting-started/latest-model)。

```powershell
# 关闭模型思考
.\scripts\start_qwen.ps1 -NoThinking

# 使用 Responses API，需要供应商支持，不会自动回退协议
.\scripts\start_qwen.ps1 -Api responses
```

无模型或使用其他兼容接口时，运行通用入口：

```powershell
# 兼容模型需先设置 FTTH_BASE_URL、FTTH_MODEL、FTTH_API_KEY
.\scripts\start.ps1
```

服务默认运行在 `http://127.0.0.1:8765`，请保持终端打开。无模型时仍可手动检查、规划、展示和导出。

### 3. 安装插件

安装到默认 QGIS 用户配置：

```powershell
.\scripts\install_plugin.ps1
```

更新已安装插件（先自动备份）：

```powershell
.\scripts\install_plugin.ps1 -Update
```

也可生成 ZIP，在 QGIS 中选择“插件 → 管理并安装插件 → 从 ZIP 安装”：

```powershell
.\.venv\Scripts\python.exe scripts/package.py
```

输出为 `dist/ftth_demo0.zip` 和带版本号的 ZIP。**ZIP 只包含桌面插件，后端仍须单独启动。** 安装后重启 QGIS，启用 **FTTH Agent demo0**。

### 4. 连接服务

插件侧栏设置：

- 地址：`http://127.0.0.1:8765`
- 令牌：选择仓库下的 `runtime/connection.token`

令牌及 SQLite 数据库会自动创建，不包含在 Git 仓库中。

## 从数据到完整规划

1. 数据页选择 IMB 及可用的 SITE、PTECH、INFRASTRUCTURE。支持 SHP、GPKG、GeoJSON、CSV、XLSX；GPKG/XLSX 可填写图层或工作表名。
2. 也可“导入配置 JSON”使用已有字段映射。源文件必须在本机存在；相对路径按 JSON 所在目录解析。
3. 登记输入快照并绑定会话。“当前图层”按钮有选中要素时保存所选部分，否则保存整层。
4. 勾选道路来源及拆分策略，点击 **保存本会话工程策略**。
5. 直接发送“开始完整规划”或“导出上次结果”；讨论、假设和否定请求进入问答，无需切换模式。

完整规划：

> 使用当前会话已登记的数据，按已保存的工程策略和默认参数执行完整规划。先检查并校验，通过后运行完整算法流程，生成并展示最终八个图层。缺项或失败就停止并说明，不要补造数据。完成后汇报结果与警告，以 QGIS 回执确认展示状态。

参数修改：

> 沿用刚才规划，只把 ZPM 容量改为 600，其他参数不变，重新运行并比较两版结果。

规划成功后导出：

> 将刚才成功的完整规划导出为完整交付包。

后台完成通知仅解释结果，不会自行开启下一次计算或导出；交付导出需在规划成功后单独发起。

### 数据与算法约束

- 完整规划需要且只能有一个 NRO，并需选择实际存在的道路来源。
- 完整规划须明确确认超 48 端口 IMB 拆分策略；实际只拆分超限楼栋。
- 现有算法最终导出要求恰好两个 PM 分区，未修改这一限制。
- 字段不一致时需提供明确映射，不补造缺失工程属性。
- `vendor/ftth/sample_data` 是随代码附带的样例，不保证满足完整规划的全部条件。
- 开发机的 `runtime/51imb-config.json`、真实 51IMB 源数据及运行成果**不随仓库分发**。克隆后请登记自己的数据，不要引用开发机绝对路径。

## 配置与数据保存

### 0.6.0 固定执行流程

主模型先输出严格的需求结构；规划、导出和展示由程序工作流执行，普通问答只获得只读工具。工作流单独记录“识别、校验、等待选择、提交、计算、审查、结束”，聊天回复生成完成不再代表规划完成。

新规划继承已保存工程策略；继承旧运行时保留数值参数，但策略以当前保存版本为准。数据冲突在聊天中显示选择卡片，例如启用识别道路却未登记道路时，可选择“仅用现有正式设施继续”；只有用户选择后才保存该变更。选择卡片可恢复，过期、撤销授权、取消或被后续请求取代的卡片不能执行。

每条规划请求只能创建一个根运行。参数变化不能绕过数据库唯一动作记录；运行、幂等键及提交事件同事务写入。自动重算仍最多一次，补审不获得新的修复额度。

完整历史保留在数据库，模型默认只看近期上下文和精简运行摘要，报告按登记 ID 分页读取。审查通知区分错误、警告与说明；实际修复资格由后端判定。

新增 `GET /sessions/{sid}/workflows/{wid}`、`POST .../resolve`（`request_id`、`option_id`、`version`）及 `POST .../cancel`。历史接口含工作流快照，事件接口含 `workflow` 事件。旧客户端仍可调用原接口；`allow_actions` 仅兼容接收并忽略，不能授予或否决执行权限，建议同步更新插件。授权来自本轮意图及用户原文，绑定会话、项目和动作；后台通知不能授权。自动重算省略时关闭。第一次升级数据库前会生成 `*.pre-0.6.0-*.bak`；历史任务不会自动恢复或重跑。

验证固定工作流、真实 QGIS 算法和导出（默认假模型）：

```powershell
.venv\Scripts\python.exe -X utf8 scripts/verify_reviews.py --workflow
```

在已配置 Qwen 环境变量的终端增加 `--real-model`，额外验证真实模型对否定、引用、假设、取消和实际规划请求的识别。真实模型验证未完成前，不应把假模型测试当作模型理解能力验收。详见 [0.6.0 验收记录](RELEASE-0.6.0.md)。

### 独立审查与一次重算

每次规划结束后，后台自动收集当前批次的校验报告，由独立上下文的审查 Agent 给出结论、问题对象、证据编号和修复建议。计算状态、程序校验和 AI 审查分别显示；AI 审查不替代工程验收，也不改变原有导出门槛。

设置页的“对话运行偏好”提供“搜索失败时自动重算一次”（默认勾选）；手动运行有独立开关。两项偏好分别记忆，每次发送或运行时保存本次选择。关闭后仍进行独立审查，只是不自动重算；旧客户端省略 `allow_auto_repair` 时关闭。

自动重算只在完整规划具有齐全结构化证据、问题属于候选路径或组合搜索失败时执行。只允许增加 `candidate_paths`、`ga_population`、`ga_generations`（至多两倍并受现有上限约束），或开启 `ga_refine`。不修改需求、容量、距离、工程策略、输入文件、坐标或拓扑。新建一次子运行后再审查，不再自动重跑。授权变化、后续规划、取消或输入改变都会阻止尚未开始的修复。

“运行 → 审查报告”显示结论与证据；“补充审查”只复核、不重新获得修复额度；“取消工作流”同时停止该根任务的待执行修复和审查，并可终止正在运行的修复子任务。历史成果保持原样，不批量补审。证据快照位于 `runtime/reviews/<review_id>/evidence.json`，审查状态、结论和修复关联持久化在 SQLite。

部分冻结算法会在写出结构化诊断前抛异常；这些任务明确显示证据缺失，不从错误字符串推断修复操作。模型未配置、超时或输出无效时保留算法结果和程序校验，不启动自动重算。AI 审查属于应用层工具与上下文隔离，不是操作系统沙箱。

接口新增 `GET/POST /sessions/{sid}/runs/{rid}/reviews`；POST 接收 `request_id` 并保证同请求幂等。消息与手动规划请求新增布尔字段 `allow_auto_repair`，默认 `false`。主 Agent 只能通过 `get_reviews` 读取审查，审查 Agent 只可按证据 ID 读取本次登记的报告。

| 环境变量 | 用途 / 默认值 |
|---|---|
| `FTTH_QGIS_ROOT` | QGIS 根目录，默认 `D:/osgeo` |
| `FTTH_DATA_DIR` | 数据目录，默认仓库下 `runtime` |
| `FTTH_PROVIDER` | `qwen` 或 `openai-compatible` |
| `FTTH_BASE_URL` | 模型接口地址，包含 `/v1` 等前缀 |
| `FTTH_MODEL` | 模型标识 |
| `FTTH_API_KEY` | 模型密钥；Qwen 也支持 `DASHSCOPE_API_KEY` |
| `FTTH_MODEL_API` | `chat`（默认）或 `responses` |
| `FTTH_ENABLE_THINKING` | `true` / `false`，默认 `true` |
| `FTTH_RUN_TIMEOUT` | 算法超时秒数，默认 `3600` |
| `FTTH_REVIEW_MODEL` | 独立审查模型；默认沿用主模型 |
| `FTTH_REVIEW_PROVIDER` | 独立审查提供商；默认沿用主配置 |
| `FTTH_REVIEW_BASE_URL` | 独立审查接口；默认沿用主配置 |
| `FTTH_REVIEW_API_KEY` | 审查凭据；修改接口地址时必须显式设置，不跨地址继承主密钥 |
| `FTTH_REVIEW_MODEL_API` | `chat` 或 `responses`；默认沿用主配置 |

审查关闭扩展思考，限制为 180 秒、6 次批量证据查询、总计 4,096 生成 token 和最多一次结构纠正。提供商未返回 token 用量时采用保守 UTF-8 字节计费限制。证据读取上限为每文件 8 MiB、每项模型可见内容 24,000 字符；截断或必需报告缺失不能判为通过，也不启动修复。

`start_qwen.ps1` 指定 Qwen 端点与模型；自定义模型/端点应设置环境变量后运行 `start.ps1`。脚本不会自动加载 `.env` 文件。

`runtime/app.sqlite` 保存会话、任务和事件，`runtime/checkpoints.sqlite` 保存模型上下文。项目快照、日志和成果也保存在 `runtime`。修改模型配置后需重启后端。

模型会接收对话及工具返回的项目摘要、参数和报告信息，请根据项目数据要求选择服务。本服务面向本机单用户，本地令牌持有人可以操作服务，不提供多租户隔离。

## 测试与开发

```powershell
# 回归使用脚本化模型 / 合成 SSE，不调用真实模型服务
.\.venv\Scripts\python.exe -m pytest -q

# QGIS 环境与算法完整性
.\.venv\Scripts\python.exe scripts/check_env.py

# 临时后端 + 实际 Qt 控件验收
.\.venv\Scripts\python.exe scripts/verify_conversations.py
.\.venv\Scripts\python.exe scripts/verify_streaming_ui.py
.\.venv\Scripts\python.exe -X utf8 scripts/verify_review_ui.py

# 验证已安装的插件
.\.venv\Scripts\python.exe scripts/verify_streaming_ui.py --installed
.\.venv\Scripts\python.exe -X utf8 scripts/verify_review_ui.py --installed

# 真实本地 51IMB 数据和算法 + 脚本化审查模型；使用独立数据目录
.\.venv\Scripts\python.exe -X utf8 scripts/verify_reviews.py --config runtime/51imb-config.json

# 显式使用当前终端配置的真实审查模型；没有配置会直接退出
.\.venv\Scripts\python.exe -X utf8 scripts/verify_reviews.py --real-model
```

部分子进程回归需要本机 QGIS，UI 验收也需要 QGIS 环境。`scripts/smoke_51imb.py` 依赖仓库同级 `FTTH(4)/FTTH/Input/51IMB/Task1/final_data`，不是克隆即用的测试；运行前先停止同数据目录后端。`qgis_ui_smoke.py` 依赖已有 smoke 成果。

运行成功、文件完整和图层数量一致不等于工程质量验收，仍需独立检查成本、容量、接通率及现场约束。

## 目录

```text
backend/                     FastAPI、Agent、队列、存储、标题生成
qgis_plugin/ftth_demo0/       Qt 侧栏、地图显示、样式及 GUI 依赖
vendor/ftth/                 原算法、适配模块、配置、完整性清单
scripts/                    启动、安装、打包及集成验证
tests/                      单元与回归测试
audit/                      审核记录和对应回归测试
runtime/                    本机数据与成果（Git 忽略）
dist/                       本机构建的插件包（Git 忽略）
```

开发记录：[更新说明](RELEASE-0.6.0.md)、[验证记录](VALIDATION.md)、[审核报告](audit/REVIEW.md)。这些文档包含历史开发环境记录，以当前代码及重新运行的测试为准。

## 停止、升级与常见问题

上下文改造采用应用 SQLite 统一保存完整问答和当前任务；目标、约束、待澄清问题与任务版本独立保存。算法、导出和审查结果绑定原任务及版本，不接管新问题。源码调研和方案取舍见 [上下文与编排调研](audit/CONTEXT_ORCHESTRATION_RESEARCH-20260926.md)。升级需同时重启后端和 QGIS；仅替换插件文件不会更新已运行的后端。

真实模型对话回归可在已配置模型的终端执行 `.\.venv\Scripts\python.exe -X utf8 scripts/verify_conversation.py --real-model`。脚本在隔离的合成项目重放“容量600 → NRO → 完整问题”，禁止实际计算，保存回答到 `runtime/conversation-real-model.json`，仍需人工核对语义正确性。

系统性评测见 [对话 benchmark](benchmarks/README.md)：12 个场景、15 轮，支持现有路由 + ReAct 与无前置路由的只读 ReAct 对照，保存回答、工具证据、源码摘要及耗时。机器契约检查与人工语义评分分开；离线 smoke 不作为模型效果。生产问答本身已使用 LangChain 模型—工具循环，事务、幂等及异步任务状态仍由服务端管理。

- **停止后端**：前台终端按 `Ctrl+C`；有 `runtime/server.pid` 的受管理后台实例可用 `scripts/stop.ps1`。
- **端口被占用**：先停止旧后端。同一数据目录只允许一个服务实例。
- **后端版本不匹配**：同时更新后端与插件，重启后端和 QGIS。
- **没有流式正文**：检查模型配置。等待模型/工具时显示动态状态，正文到达后逐段更新，不展示私有推理。
- **找不到 51IMB 配置**：这是开发机运行产物，不在源码仓库；需登记本机数据。
- **服务重启**：未完成任务标记为 `interrupted`，不自动恢复算法中间步骤或重提计算。

## 第三方组件

保留 PyQt-Fluent-Widgets、PyQt5-Frameless-Window、darkdetect 的源码、资源及许可证，详见 [THIRD_PARTY.md](qgis_plugin/ftth_demo0/THIRD_PARTY.md)。Qt/PyQt 运行时由 QGIS 提供。第三方组件分别适用原有许可；仓库未为项目原创代码另行声明许可证。
