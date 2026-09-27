# FTTH Agent for QGIS

基于 **QGIS 桌面插件 + 主 Agent + 独立审查 Agent + 独立算法进程** 的 FTTH 光纤接入网规划工作空间。通过中文对话检查数据、提交规划、调整参数、比较方案，在 QGIS 中展示和导出成果。

当前版本 **0.7.0**（后端与插件同版本，更新说明见 [RELEASE-0.7.0.md](RELEASE-0.7.0.md)）。面向同机、单用户的 Windows 与 macOS 环境，仍处于实验阶段。

## 功能

- **对话规划**：LangChain 工具调用与 LangGraph 会话记忆，支持 Qwen 和支持 tool calling 的 OpenAI 兼容接口。
- **实时回复**：正文流式显示、动态状态与耗时、Markdown 和复制，切换会话后恢复已收到的正文。
- **自然语言执行**：明确要求规划或导出即可执行，无需切换模式；LLM 识别本轮意图，后端验证动作授权、数据及工程策略。
- **可追溯运行**：输入快照与 SHA-256 校验、参数继承、取消、报告和方案比较。
- **地图与交付**：成果按批次显示，收到 QGIS 回执后确认展示；完整规划可导出 GPKG、SHP、表格和规划图。
- **运行保护**：数据目录单实例锁、独立子进程（取消时连同其子进程终止）、超时处理；会话标题过滤拒答和无效输出。
- **应用基础**：统一命令行 `ftth`（启停、自检修复、配置、密钥、插件安装）；自动定位并验证 QGIS；密钥存系统凭据库；脱敏日志；数据库版本化迁移与自动备份。

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
| 操作系统 | Windows、macOS；Linux 上后端与测试可运行，插件界面与算法未验证 |
| QGIS | QGIS 3 / Qt5，含 PyQGIS、GDAL、matplotlib；OSGeo4W / QGIS 安装包、QGIS.app、conda-forge `qgis` 或发行版包 |
| 后端 Python | 3.12，由 [uv](https://docs.astral.sh/uv/) 按 `uv.lock` 安装 |
| 模型（可选） | 支持工具调用的接口及对应 API Key |

验证记录：Windows OSGeo4W QGIS 3.44.14 完成真实 51IMB 完整规划、导出与界面验收（0.6.0）；0.7.0 在 macOS QGIS-LTR 3.44.11 与另一台 Windows 的 OSGeo4W QGIS 3.44.4 上均零配置完成环境自检、数据登记、仅分区规划、取消、启停及全部 Qt 界面验收，并用真实模型（OpenAI 兼容接口上的 gemini-3.8-flash）完成对话、benchmark 与独立审查验证。详见 [验证记录](VALIDATION.md)。

插件内置的 PyQt-Frameless-Window 与 darkdetect 带有本地补丁：QGIS.app 的签名库校验会拒绝 PyObjC 等第三方编译扩展，补丁在缺少原生依赖时改用纯 Qt 实现，Windows 代码路径不变，见 [THIRD_PARTY.md](qgis_plugin/ftth_demo0/THIRD_PARTY.md)。

## 快速启动

Windows 日常启动：复制 `.env.example` 为仓库根目录的 `.env`，填写其中的 `FTTH_API_KEY`，然后双击 `start_backend.cmd`。后续直接双击即可，无需每次输入命令或 Key。停止时按启动窗口的 Ctrl+C，或双击 `stop_backend.cmd`。

`.env` 已被 Git 忽略；只修改本机 `.env`，不要把真实 Key 填进会提交的 `.env.example`。模板沿用本项目的千问模型和接口默认值，可按所用服务修改。脚本按文本读取配置，不执行其中的命令；配置文件中明确列出的项覆盖终端环境变量。未填写 Key 时会提示补充，模型连通性可另用 `scripts/start_qwen.ps1` 的启动前检查。原有 `uv run ftth serve` 命令不自动读取 `.env`。

所有平台使用同一个命令行入口 `ftth`（`uv run ftth --help` 查看全部命令）。

### 1. 获取代码并检查环境

```shell
git clone https://github.com/GH-ytym/FTTH.git
cd FTTH
uv sync --frozen
uv run ftth doctor
```

`ftth doctor` 自动定位 QGIS，逐项报告：QGIS 版本与来源、算法依赖、64 个冻结算法文件哈希、配置来源、数据库结构版本、模型与端口。发现问题时运行：

```shell
uv run ftth doctor --fix
```

它把缺失的纯 Python 依赖（如任务四需要的 openpyxl）装到数据目录的 `worker-packages/`，**不修改 QGIS 安装**，并把检测到的 QGIS 固定写入配置，之后启动不再重复探测。

QGIS 的定位顺序：配置项 `qgis.python`（解释器）或 `qgis.root`（安装目录）→ PATH 上的 `qgis_process` / `qgis` → 当前及 conda 登记的含 qgis 的环境 → 各平台标准安装位置（Windows 的 Program Files `QGIS*` 与各盘 `OSGeo*`，macOS 的 `QGIS*.app`，Linux 的发行版包）。每个候选都会实际启动一次，确认 `qgis.core` 等模块可导入后才被采用；`ftth doctor --all` 列出全部候选的探测结果。自动定位不到时显式指定：

```shell
uv run ftth config set qgis.root "D:\osgeo"                                   # Windows 安装目录
uv run ftth config set qgis.python /Applications/QGIS-LTR.app/Contents/MacOS/python
```

### 2. 启动后端

使用千问：

```shell
uv run ftth serve --provider qwen --check-model
```

密钥按以下顺序取用：环境变量 `FTTH_API_KEY` / `DASHSCOPE_API_KEY` → 系统凭据库 → 启动时隐藏输入（加 `--save-key` 则存入凭据库，下次无需输入）。也可预先保存：`uv run ftth secret set`；`ftth secret status` 只显示是否已保存，从不显示密钥。密钥不会写入任何文件、会话或日志。`--check-model` 先用合成数据做一次真实工具调用探测，失败则不启动；探测及后续模型调用可能产生供应商费用。默认模型为 `qwen3.8-flash`、`https://maas.qianwenaiapi.com/compatible-mode/v1`、Chat Completions 流式协议。参考：[千问模型文档](https://platform.qianwenai.com/docs/developer-guides/getting-started/latest-model)。

```shell
uv run ftth serve --provider qwen --no-thinking        # 关闭模型思考
uv run ftth serve --provider qwen --api responses      # Responses API，需要供应商支持，不会自动回退
uv run ftth serve                                      # 无模型，或按配置/环境变量使用其他兼容接口
uv run ftth serve --provider openai-compatible --base-url http://127.0.0.1:8045/v1 --model gemini-3.8-flash
                                                       # 任意支持工具调用的 OpenAI 兼容接口（本机代理、Ollama、LM Studio 等）
```

服务默认运行在 `http://127.0.0.1:8765`。前台按 `Ctrl+C` 停止，或在另一个终端运行 `uv run ftth stop`（凭连接令牌请求后端优雅退出，会终止进行中的算法进程）。无模型时仍可手动检查、规划、展示和导出。

Windows 下原有脚本仍可用，现为上述命令的封装：`scripts\start_qwen.ps1 [-NoThinking] [-Api responses]`、`scripts\start.ps1`、`scripts\stop.ps1`、`scripts\install_plugin.ps1 [-Update]`。

### 3. 安装插件

```shell
uv run ftth plugin install            # 安装到默认 QGIS 用户配置
uv run ftth plugin install --update   # 已安装时先备份到数据目录 plugin-backups/，再整体替换
uv run ftth plugin package            # 生成 dist/ftth_demo0.zip，可在 QGIS“从 ZIP 安装”
```

QGIS 配置目录按平台约定定位（Windows `%APPDATA%\QGIS\QGIS3\profiles`，macOS `~/Library/Application Support/QGIS/QGIS3/profiles`，Linux `~/.local/share/QGIS/QGIS3/profiles`），可用 `--profile`、`--profiles-dir` 指定。**ZIP 只包含桌面插件，后端仍须单独启动。** 安装后重启 QGIS，启用 **FTTH Agent demo0**。

### 4. 连接服务

用 `ftth plugin install` 安装的插件会自动连接本机后端：安装时在插件目录写入 `connection.json`（后端地址与令牌文件路径，不含令牌本身）。从 ZIP 安装或连接其他后端时，在插件设置页填写：

- 地址：`http://127.0.0.1:8765`（修改后会记住）
- 令牌：选择数据目录下的 `connection.token`（默认 `runtime/connection.token`，`ftth serve` 启动时会打印路径）

手动选择优先于安装时的记录。后端找不到 QGIS 或缺少算法依赖时，插件底部会提示对应的 `ftth doctor` 命令。令牌及 SQLite 数据库会自动创建，不包含在 Git 仓库中。

## 从数据到完整规划

1. 数据页选择 IMB 及可用的 SITE、PTECH、INFRASTRUCTURE。支持 SHP、GPKG、GeoJSON、CSV、XLSX；GPKG/XLSX 可填写图层或工作表名。
2. 单独选择道路 GPKG（优先路由来源）；未提供时使用有数据的 INFRA 兜底。无需准备或导入 JSON 配置，字段识别与算法配置由后端自动生成。
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

新增 `GET /sessions/{sid}/workflows/{wid}`、`POST .../resolve`（`request_id`、`option_id`、`version`）及 `POST .../cancel`。历史接口含工作流快照，事件接口含 `workflow` 事件。旧客户端仍可调用原接口；`allow_actions` 仅兼容接收并忽略，不能授予或否决执行权限，建议同步更新插件。授权来自本轮意图及用户原文，绑定会话、项目和动作；后台通知不能授权。自动重算省略时关闭。数据库结构升级前会自动生成 `app.sqlite.pre-schema-v<版本>-*.bak`；历史任务不会自动恢复或重跑。

验证固定工作流、真实 QGIS 算法和导出（默认假模型）：

```shell
uv run python -X utf8 scripts/verify_reviews.py --workflow
```

在已配置 Qwen 环境变量的终端增加 `--real-model`，额外验证真实模型对否定、引用、假设、取消和实际规划请求的识别。真实模型验证未完成前，不应把假模型测试当作模型理解能力验收。详见 [0.6.0 验收记录](RELEASE-0.6.0.md)。

### 独立审查与一次重算

每次规划结束后，后台自动收集当前批次的校验报告，由独立上下文的审查 Agent 给出结论、问题对象、证据编号和修复建议。计算状态、程序校验和 AI 审查分别显示；AI 审查不替代工程验收，也不改变原有导出门槛。

设置页的“对话运行偏好”提供“搜索失败时自动重算一次”（默认勾选）；手动运行有独立开关。两项偏好分别记忆，每次发送或运行时保存本次选择。关闭后仍进行独立审查，只是不自动重算；旧客户端省略 `allow_auto_repair` 时关闭。

自动重算只在完整规划具有齐全结构化证据、问题属于候选路径或组合搜索失败时执行。只允许增加 `candidate_paths`、`ga_population`、`ga_generations`（至多两倍并受现有上限约束），或开启 `ga_refine`。不修改需求、容量、距离、工程策略、输入文件、坐标或拓扑。新建一次子运行后再审查，不再自动重跑。授权变化、后续规划、取消或输入改变都会阻止尚未开始的修复。

“运行 → 审查报告”显示结论与证据；“补充审查”只复核、不重新获得修复额度；“取消工作流”同时停止该根任务的待执行修复和审查，并可终止正在运行的修复子任务。历史成果保持原样，不批量补审。证据快照位于 `runtime/reviews/<review_id>/evidence.json`，审查状态、结论和修复关联持久化在 SQLite。

部分冻结算法会在写出结构化诊断前抛异常；这些任务明确显示证据缺失，不从错误字符串推断修复操作。模型未配置、超时或输出无效时保留算法结果和程序校验，不启动自动重算。AI 审查属于应用层工具与上下文隔离，不是操作系统沙箱。

接口新增 `GET/POST /sessions/{sid}/runs/{rid}/reviews`；POST 接收 `request_id` 并保证同请求幂等。消息与手动规划请求新增布尔字段 `allow_auto_repair`，默认 `false`。主 Agent 只能通过 `get_reviews` 读取审查，审查 Agent 只可按证据 ID 读取本次登记的报告。

### 配置

配置按优先级从低到高叠加：内置默认值 < 配置文件 < `FTTH_*` 环境变量 < `ftth serve` 命令行参数。配置文件为 TOML，默认位于数据目录的 `config.toml`（可用 `FTTH_CONFIG` 指定），通过命令维护：

```shell
uv run ftth config show                   # 每一项的生效值及来源（default / file / env / cli）
uv run ftth config set run.timeout 7200
uv run ftth config unset run.timeout
```

未知配置项、错误类型和密钥写入配置文件都会被拒绝；修改后重启后端生效。

| 配置项 | 环境变量 | 用途 / 默认值 |
|---|---|---|
| `qgis.python` | `FTTH_QGIS_PYTHON` | QGIS 的 Python 解释器；默认自动定位 |
| `qgis.root` | `FTTH_QGIS_ROOT` | QGIS 安装目录（OSGeo4W 根、`QGIS*.app` 或 conda 环境）；默认自动定位 |
| `model.provider` | `FTTH_PROVIDER` | `qwen` 或 `openai-compatible`；设置了 `DASHSCOPE_API_KEY` 时默认 `qwen` |
| `model.base_url` | `FTTH_BASE_URL` | 模型接口地址，包含 `/v1` 等前缀 |
| `model.name` | `FTTH_MODEL` | 模型标识 |
| `model.api` | `FTTH_MODEL_API` | `chat`（默认）或 `responses` |
| `model.thinking` | `FTTH_ENABLE_THINKING` | `true` / `false`，默认 `true` |
| `run.timeout` | `FTTH_RUN_TIMEOUT` | 算法超时秒数，默认 `3600` |
| `server.host` / `server.port` | `FTTH_HOST` / `FTTH_PORT` | 监听地址，默认 `127.0.0.1:8765` |
| `log.level` | `FTTH_LOG_LEVEL` | `debug` / `info`（默认）/ `warning` / `error` |
| — | `FTTH_DATA_DIR` | 数据目录，默认仓库下 `runtime`；也可用 `--data-dir` |
| — | `FTTH_API_KEY` | 模型密钥；优先于系统凭据库（`ftth secret set`），不可写入配置文件；Qwen 也支持 `DASHSCOPE_API_KEY` |

独立审查模型沿用主配置，可用以下环境变量单独指定：

| 环境变量 | 用途 / 默认值 |
|---|---|
| `FTTH_REVIEW_MODEL` | 独立审查模型；默认沿用主模型 |
| `FTTH_REVIEW_PROVIDER` | 独立审查提供商；默认沿用主配置 |
| `FTTH_REVIEW_BASE_URL` | 独立审查接口；默认沿用主配置 |
| `FTTH_REVIEW_API_KEY` | 审查凭据（也可 `ftth secret set review`）；修改接口地址时必须显式提供，不跨地址继承主密钥 |
| `FTTH_REVIEW_MODEL_API` | `chat` 或 `responses`；默认沿用主配置 |

审查关闭扩展思考，限制为 180 秒、6 次批量证据查询、总计 4,096 生成 token 和最多一次结构纠正。提供商未返回 token 用量时采用保守 UTF-8 字节计费限制。证据读取上限为每文件 8 MiB、每项模型可见内容 24,000 字符；截断或必需报告缺失不能判为通过，也不启动修复。

### 数据目录

| 路径 | 内容 |
|---|---|
| `app.sqlite` | 会话、任务、事件；结构版本记录在 `PRAGMA user_version`，升级前自动备份，拒绝用旧版后端打开新版数据库 |
| `checkpoints.sqlite` | 模型上下文 |
| `config.toml` | 持久配置（不含密钥） |
| `connection.token` | 插件连接令牌 |
| `logs/backend.log` | 滚动日志（5 MB × 5）；密钥、令牌自动脱敏，失败时保留堆栈以便排查 |
| `worker-packages/` | 应用为算法进程补充的纯 Python 依赖 |
| `server.json` | `ftth serve` 运行时的地址与进程号，供 `ftth stop`、`ftth doctor` 使用 |
| `projects/`、`runs/`、`reviews/` | 输入快照、运行成果与日志、审查证据 |

模型会接收对话及工具返回的项目摘要、参数和报告信息，请根据项目数据要求选择服务。本服务面向本机单用户，本地令牌持有人可以操作服务，不提供多租户隔离。

## 测试与开发

```shell
# 回归使用脚本化模型 / 合成 SSE，不调用真实模型服务，也不依赖本机 QGIS
uv run pytest -q
uv run ruff check        # pyflakes 级正确性检查；不改变项目格式

# QGIS 环境、算法依赖与算法完整性
uv run ftth doctor

# 临时后端 + 本机 QGIS 的实际 Qt 控件验收（Windows、macOS）
uv run python scripts/verify_conversations.py
uv run python scripts/verify_streaming_ui.py
uv run python -X utf8 scripts/verify_review_ui.py [--workflow-ui]

# 按 `ftth plugin install` 装入临时配置目录，验证安装副本及自动连接
uv run python scripts/verify_streaming_ui.py --fresh-install

# 验证已安装在 QGIS 配置中的插件（默认配置目录，或 --profiles-dir 指定）
uv run python scripts/verify_streaming_ui.py --installed
uv run python -X utf8 scripts/verify_review_ui.py --installed

# 真实本地 51IMB 数据和算法 + 脚本化审查模型；使用独立数据目录
uv run python -X utf8 scripts/verify_reviews.py --imb /path/to/IMB.shp --site /path/to/SITE.shp --roads /path/to/roads.gpkg

# 显式使用当前终端配置的真实审查模型；没有配置会直接退出
uv run python -X utf8 scripts/verify_reviews.py --real-model
```

测试对开发机上的 QGIS、配置文件和 `FTTH_*` 环境变量完全隔离：QGIS 布局用伪造目录验证，探测链路用伪造的 `qgis` 包跑真实子进程，子进程控制回归（进度解析、取消、超时、关闭回收）使用当前 Python 解释器。模型密钥读写使用内存替身，不触及系统凭据库。CI（`.github/workflows/ci.yml`）在 Windows、macOS、Linux 上运行 lint 与同一套测试。UI 验收及真实算法验证仍需本机 QGIS；验收脚本使用临时插件偏好文件（`FTTH_PLUGIN_SETTINGS`），不改动用户的插件设置，截图保存在 `runtime/ui-screenshots/`。`scripts/smoke_51imb.py` 依赖仓库同级 `FTTH(4)/FTTH/Input/51IMB/Task1/final_data`，不是克隆即用的测试；运行前先停止同数据目录后端。`qgis_ui_smoke.py` 依赖已有 smoke 成果。

运行成功、文件完整和图层数量一致不等于工程质量验收，仍需独立检查成本、容量、接通率及现场约束。

## 目录

```text
backend/                     FastAPI、Agent、队列、存储；cli.py（ftth 命令）、settings.py（分层配置）、qgis_runtime.py（QGIS 定位与探测）
qgis_plugin/ftth_demo0/       Qt 侧栏、地图显示、样式及 GUI 依赖
vendor/ftth/                 原算法、适配模块、配置、完整性清单
scripts/                    集成验证脚本及 Windows 兼容封装（ps1）
tests/                      单元与回归测试
audit/                      审核记录和对应回归测试
runtime/                    本机数据与成果（Git 忽略）
dist/                       本机构建的插件包（Git 忽略）
```

开发记录：[更新说明](RELEASE-0.7.0.md)、[验证记录](VALIDATION.md)、[审核报告](audit/REVIEW.md)。这些文档包含历史开发环境记录，以当前代码及重新运行的测试为准。

## 停止、升级与常见问题

上下文改造采用应用 SQLite 统一保存完整问答和当前任务；目标、约束、待澄清问题与任务版本独立保存。算法、导出和审查结果绑定原任务及版本，不接管新问题。源码调研和方案取舍见 [上下文与编排调研](audit/CONTEXT_ORCHESTRATION_RESEARCH-20260926.md)。升级需同时重启后端和 QGIS；仅替换插件文件不会更新已运行的后端。

真实模型对话回归可在已配置模型的终端执行 `uv run python -X utf8 scripts/verify_conversation.py --real-model`。脚本在隔离的合成项目重放“容量600 → NRO → 完整问题”，禁止实际计算，保存回答到 `runtime/conversation-real-model.json`，仍需人工核对语义正确性。

系统性评测见 [对话 benchmark](benchmarks/README.md)：12 个场景、15 轮，支持现有路由 + ReAct 与无前置路由的只读 ReAct 对照，保存回答、工具证据、源码摘要及耗时。机器契约检查与人工语义评分分开；离线 smoke 不作为模型效果。生产问答本身已使用 LangChain 模型—工具循环，事务、幂等及异步任务状态仍由服务端管理。

- **停止后端**：前台终端按 `Ctrl+C`，或 `uv run ftth stop`。
- **端口被占用**：先 `uv run ftth stop`，或用 `--port` / `server.port` 更换。同一数据目录只允许一个服务实例。
- **找不到 QGIS / 算法依赖缺失**：运行 `uv run ftth doctor` 查看定位过程，`--fix` 补齐依赖，或 `ftth config set qgis.python ...` 指定。已运行的后端可调用 `POST /runtime/refresh` 重新检测，无需重启。
- **排查失败原因**：查看数据目录下 `logs/backend.log`；`ftth serve --log-level debug` 记录每个 HTTP 请求。
- **后端版本不匹配**：同时更新后端与插件，重启后端和 QGIS。
- **没有流式正文**：检查模型配置。等待模型/工具时显示动态状态，正文到达后逐段更新，不展示私有推理。
- **找不到 51IMB 配置**：这是开发机运行产物，不在源码仓库；需登记本机数据。
- **服务重启**：未完成任务标记为 `interrupted`，不自动恢复算法中间步骤或重提计算。

## 第三方组件

保留 PyQt-Fluent-Widgets、PyQt5-Frameless-Window、darkdetect 的源码、资源及许可证，详见 [THIRD_PARTY.md](qgis_plugin/ftth_demo0/THIRD_PARTY.md)。Qt/PyQt 运行时由 QGIS 提供。第三方组件分别适用原有许可；仓库未为项目原创代码另行声明许可证。
