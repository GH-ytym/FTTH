# FTTH 0.7.0

## 改动

### 修复

- 对象更新由 `INSERT OR REPLACE` 改为原地 UPSERT，存储顺序即创建顺序。此前审查进度会重写所属运行，使旧运行排到新运行之后，“沿用上一版 / 导出上次结果”可能选中旧运行；`use_latest` 另按创建时间取最新，兼容已乱序的旧数据库。
- 首条消息由工作流直接答复（规划、澄清）时不再跳过会话标题生成。
- macOS / Linux 上取消或超时连同算法进程启动的子进程一起终止（此前只结束直接子进程）；Windows 仍用 `taskkill /T`。
- 算法超时的错误说明超时秒数与配置项，不再只显示 `TimeoutError`。
- `stop.ps1` 依赖的 `server.pid` 从未被写入，停止脚本实际不可用；现由 `ftth stop` 取代。
- 插件的令牌默认路径曾写死为开发机 `F:/PROJECTS/...`，服务地址不保存；现按安装记录自动连接，手动设置会记住。
- 独立审查中程序依确定性校验改判结论时（如模型写“通过”、程序判“需要复核”），报告摘要与结论曾自相矛盾；现在摘要前注明改判及原因，模型原文保留。由真实模型审查发现。

### 性能

- 按会话过滤的查询与表达式索引取代插件轮询路径上的全表加载。合成数据（40 会话、600 运行、1,200 条流式回复）：`/runs` 45.7 → 3.1 ms，`/display` 40.3 → 0.6 ms，`/sessions` 23.3 → 0.7 ms，`/history` 84.4 → 2.0 ms。
- 流式回复每 80 ms 保存一次累计全文，存储随回复长度平方增长；最终回复写入时清除该消息的快照，升级迁移清除旧数据库中的快照。上述合成数据的事件表 220.6 MB → 5.5 MB。

### 应用基础

- 统一命令行 `uv run ftth`，Windows、macOS、Linux 用法一致：`serve`、`stop`、`doctor [--fix]`、`config`、`secret`、`plugin install|package`、`check-model`。PowerShell 脚本保留为封装。
- 分层配置：默认值 < `config.toml` < `FTTH_*` 环境变量 < 命令行；`ftth config show` 显示每项来源，拒绝未知项、错误类型和写入文件的密钥。
- QGIS 自动定位并逐个启动验证：显式配置 → PATH 上的 QGIS → conda 登记的含 qgis 环境 → 各平台标准位置；支持 OSGeo4W / 安装包、QGIS.app、conda、Linux 发行版包。`ftth doctor` 展示全部候选及原因，`--fix` 把 openpyxl 等纯 Python 依赖装入数据目录 `worker-packages/`（不修改 QGIS）并固定检测结果。完整规划在启动算法前检查依赖。
- 模型密钥可存入系统凭据库（macOS 钥匙串、Windows 凭据管理器、Linux Secret Service）：`ftth secret set`，或 `ftth serve --save-key`。环境变量仍优先；密钥不进入文件、会话、日志或算法进程。
- 日志：`logs/backend.log` 滚动文件，密钥、令牌与 Bearer 头自动脱敏（含异常堆栈）。回复、需求识别、审查和运行失败此前只记录异常类型名，现保留脱敏堆栈。
- 数据库以 `PRAGMA user_version` 管理有序迁移，升级前自动备份为 `app.sqlite.pre-schema-v<版本>-*.bak`；结构版本高于程序时拒绝打开。
- 版本号以 `pyproject.toml` 为唯一来源；pytest、ruff 移入开发依赖；新增三平台 CI（lint + 测试 + CLI 冒烟）。

### 插件

- 插件界面可在 macOS QGIS 中使用：内置的 PyQt-Frameless-Window 与 darkdetect 在缺少 PyObjC（QGIS.app 签名校验拒绝第三方编译扩展）或 xcffib 时改用纯 Qt 实现；Windows 代码路径不变，改动见 `THIRD_PARTY.md`。
- `ftth plugin install` 写入连接记录，插件自动连接本机后端；按平台约定定位 QGIS 配置目录，更新时先备份再整体替换。
- 后端找不到 QGIS 或缺少算法依赖时，插件底部提示对应命令。
- 运行列表仅在内容变化时重建，不再每秒清空下拉框。
- UI 验收脚本使用临时偏好文件，不再改写用户的插件设置；新增 `--fresh-install` 验证安装副本与自动连接。修正 `conversation_ui_smoke.py` 中与 0.6.0 中文活动标签不一致的过期断言。

## 验证

- 自动化：macOS `pytest -q` **196 通过**；Windows 11 **195 通过、1 跳过**（POSIX 专属用例）；两端 `ruff check` 通过。测试与开发机的 QGIS、配置、环境变量及系统凭据库隔离；修复类用例均确认在修改前失败。
- macOS QGIS-LTR 3.44.11 与 Windows OSGeo4W QGIS 3.44.4（均为零配置自动定位）：`ftth doctor` 全部检查通过（64 个冻结算法文件哈希一致；macOS 经 `--fix` 补 openpyxl）；启动后经 HTTP 登记示例数据、仅分区规划成功，运行中取消后算法进程清零，优雅停止。Windows 上 `start.ps1`、`stop.ps1`、`install_plugin.ps1 [-Update]`、`check_env.py` 封装均实际运行通过。
- 两个平台的真实 Qt 界面：`verify_streaming_ui.py --fresh-install`（安装副本自动连接）、`verify_conversations.py`、`verify_review_ui.py --workflow-ui` 全部通过；macOS 截图已人工检查中文渲染与布局。
- 真实模型（本机 OpenAI 兼容代理上的 `gemini-3.8-flash`，非千问）：工具调用探测通过；多轮澄清对话语义正确；对话 benchmark 路由 15/15，共同只读轮次 AI 审核语义通过率 routed 84.6%、无路由消融 69.2%（单次运行）；独立审查读取全部证据并给出合规的结构化结论。详见 `VALIDATION.md`。
- **未验证**：真实 51IMB 完整规划、导出与完整规划的独立审查（数据只在原开发机，示例数据无 NRO）；千问模型本身；GitHub 上的 CI 运行。

## 升级

```shell
uv sync --frozen
uv run ftth doctor --fix
uv run ftth plugin install --update
```

后端首次启动时迁移数据库（先自动备份），并清除旧的流式快照；不重写对话、不重跑任务、不修改算法或业务数据。启动命令改为 `uv run ftth serve --provider qwen --check-model`（`start_qwen.ps1` 仍可用）。后端与插件都需重启至 0.7.0。
