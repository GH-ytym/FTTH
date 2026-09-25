# FTTH Agent demo0 0.4.0

本版修复独立审核发现的 5 个问题，并将 Qt 侧栏升级为正文流式显示。

## 用户可见变化

- 输入后出现动态状态和耗时，模型正文到达后逐段更新同一回复卡片；结束后提供完整 Markdown 和复制按钮。没有人为切割整条完成回复制造“假流式”。支持流式的供应商实时显示；不支持流式的模型仍显示动态等待状态。
- 正文通过 SQLite 事件持久化并由 Qt 每 250ms 查询；切换会话后能恢复已接收内容。模型异常时保留正文并标记未完成。reasoning_content、工具参数及原始供应商异常不会显示为回复正文。
- 输入框增加“检查与讨论 / 允许执行”。只有用户选择允许执行，本条消息才可提交规划或导出。道路及拆分工程策略须在数据页保存，本会话后续可沿用；模型无法自行批准策略。手动运行会保存用户选择的策略。切换会话恢复该会话策略。

## 修复及验证映射

| 问题 | 修复 | 验证 |
|---|---|---|
| 模型代替用户同意 | 消息执行权限及独立会话策略，提交/导出工具校验 | 恶意工具调用被拒绝；允许执行但未确认策略仍拒绝；跨会话策略不通用；撤销后拒绝继承 |
| 数据错绑会话 | 登记时捕获会话及界面代次，回调绑定原会话 | Python 回归与实际 Qt Panel 延迟回调检查 |
| 取消创建中的进程 | 创建过程屏蔽协程取消，完成创建后复核取消并回收进程 | 实际 QGIS Python 子进程取消、创建中关闭服务、超时测试 |
| 历史漏回复 | 消息快照与对应事件在同一事务保存，部分正文也持久化 | 事务回滚、历史游标与回复一致性、Qt 中途切回会话 |
| 重复服务修改状态 | 数据目录 OS 文件锁，先锁定再恢复任务；通用启动脚本端口预检 | 第二实例拒绝且原状态不变；正常关闭后可恢复任务 |
| 侧栏关闭异常 | 延迟滚动改为控件所有的 QTimer，销毁时自动清理 | Qt 流式窗口关闭与 DeferredDelete 无未捕获异常 |

验证命令：

```powershell
.venv/Scripts/python.exe -m pytest tests audit/test_review_repros.py -q
.venv/Scripts/python.exe scripts/verify_conversations.py
.venv/Scripts/python.exe scripts/verify_streaming_ui.py
.venv/Scripts/python.exe scripts/verify_streaming_ui.py --installed
.venv/Scripts/python.exe scripts/check_env.py
```

- Python 回归：41 项通过（含原测试和新增审核、SSE、授权及进程生命周期测试）。
- QGIS 3.44 / Qt5 实测：4 个页面、480px 布局、参数提交、真实历史成果 8 图层加载与显示替换。
- 流式验收使用可控延迟的合成 SSE 响应，经真实 Qwen/OpenAI 适配器、LangChain、FastAPI、Qt HTTP、真实 QWidget 全链路执行；验证正文确实在模型结束前增长、中途恢复、最终单卡片、复制与安全关闭。未使用真实模型密钥调用供应商。
- 合成流式截图：`runtime/release-0.4.0/streaming.png`、`complete.png`。
- 未改动算法 vendor；未重新声称规划质量或现场工程验收通过。

## 安装与激活

发布包为 `dist/ftth_demo0-0.4.0.zip`，同时更新 `dist/ftth_demo0.zip`。`dist/release-0.4.0.json` 记录 ZIP、后端和插件文件的 SHA-256。默认 QGIS profile 通过 `scripts/install_plugin.ps1 -Update` 更新，旧插件保存在 `runtime/plugin-backups/`。

已打开的 QGIS 仍使用内存中的旧插件，须重启 QGIS。后端也必须更新：停止旧后端，在原启动终端运行 `./scripts/start_qwen.ps1`。旧后端的模型密钥不在本次运行环境中；此次发布保留了它，没有读取进程内存、导出密钥或强制结束现有 QGIS。若原终端仍有密钥环境变量会直接沿用，否则按脚本隐藏提示重新输入。无需重建工程或重跑已有成果。

0.4.0 插件会提示旧后端版本不匹配，并阻止向旧后端提交新聊天执行请求。
