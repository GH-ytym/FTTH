# FTTH 对话 benchmark v1

目的：用真实多轮任务评价上下文衔接、事实依据、只读边界及前置路由的收益和代价。它不是 pytest 通过数量的别名，也不是通用模型排行榜。

## 两种被测架构

- `routed`：实际应用的需求识别/持久目标 + LangChain ReAct 风格问答工具循环。
- `react-readonly`：仅在 benchmark 中绕过前置语义路由和目标解释，完整聊天直接进入同一个只读模型—工具循环。相同问答提示、能力说明、工具、历史投影、模型配置和工具结果。由于没有前置路由，不给它伪造“路由准确率”。执行类正样本标为不适用。

这是一项消融对照，不是宣称纯 ReAct 已可替换生产执行编排；只读试验无法衡量规划提交、取消、审查、修复的完整生命周期。

## 用例

`dialogue_v1.json` 分成回归、迁移表达和执行路由三个组：容量澄清→NRO→800、换话题、坐标存储与计距、15%余量适用范围、两层分区与多局站限制、输入预检、无项目、引用执行命令、假设导出、纠正错误助手历史、明确规划和导出。迁移表达是小样本补充，不是严格保密的 held-out 测试集。

每个 case 使用独立临时 SQLite、合成项目和会话，不访问正在运行的后端或真实工程。规划、导出、显示、修复及后台任务入口被拦截并记录为失败；正向执行请求只评估 Intent，不提交计算。不会改变真实数据，不会启动 QGIS 算法。输出中不记录密钥、端点或模型私有推理。

## 运行

在 demo0 目录、配置好模型的终端运行；此命令直接读本地最新代码，不依赖后端重启：

```powershell
.\.venv\Scripts\python.exe -X utf8 benchmarks/run_dialogue.py --real-model --architecture routed --repeat 3 --output runtime/benchmarks/routed.json
.\.venv\Scripts\python.exe -X utf8 benchmarks/run_dialogue.py --real-model --architecture react-readonly --repeat 3 --output runtime/benchmarks/react.json
```

可用 `--case reserve_scope` 等仅运行指定案例，重复 `--case` 可以选多个。先单次小规模运行，再扩大重复次数以控制调用量。真实模型会产生正常供应商调用费用；未配置时直接退出，不降级为假模型。

离线检查评测管线：

```powershell
.\.venv\Scripts\python.exe -X utf8 benchmarks/run_dialogue.py --smoke --output runtime/benchmarks/smoke.json
```

Smoke 模型故意总将意图识别为 query，用于验证评分器能抓住错路由；输出不是回答正确率，禁止拿 smoke 与真实模型做效果比较。

## 评分与对比

机器检查和人工语义分开报告：

- 共同检查：回复完成、没有副作用尝试、没有原样复述问题。
- 路由专属检查：动作与参数是否符合用例；不能用“全部拒绝执行”赢得测试，另有明确规划/导出的正样本。
- 语义：逐轮按 `rubric` 检查回答与 `tool_trace`。是否正确续接数值、是否区分源码分支、是否把理论下限说成实际成果、是否读懂实现，是人工审核项；不以出现“NRO/600”等关键词就算正确。
- 性能：逐轮耗时、P50/P95、实际问答模型次数、工具调用和可见工具轨迹。前置识别调用未纳入 answer_model_calls；token/费用未采集，不能据此计算成本节省。

审核者在输出 JSON 的每条需要语义判断的 row 中填写：

```json
"semantic_grade": {"pass": false, "reviewer": "审核者标识", "reason": "将仅适用于独立选址的15%折减错误套到完整规划"}
```

只有该报告所有待审核轮次完成审核，才计算语义通过率；否则显示 null 和未审核数量。建议隐藏架构标签交叉审核，并保留失败回答。模型自动裁判可作为下一步辅助，但 v1 不把同一模型自评当真值。

```powershell
.\.venv\Scripts\python.exe -X utf8 benchmarks/compare.py runtime/benchmarks/routed.json runtime/benchmarks/react.json
```

比较器校验数据集摘要、相同只读用例、轮次和重复次数；执行路由正样本不进入两架构的共同分母。模型配置分别展示，比较时应保持一致。它没有自动判断哪套架构胜出，也不把未审核结果宣称为成功。

## 研究依据与架构决策

数据集 v1.1 修正了 `positive_export` 的前置数据：该意图识别用例现在预置同会话的成功完整规划记录。记录是合成元数据，没有计算成果或可导出文件，且仅允许用于 `intent_only` 测试。旧 v1 没有历史批次却要求识别为导出，因此模型询问缺失批次不应直接算作理解失败。原始报告保持不变，新旧数据集不能直接合并比较。

[ReAct 论文](https://arxiv.org/abs/2210.03629)讨论交替推理与外部动作。当前问答使用的 [LangChain create_agent](https://docs.langchain.com/oss/python/langchain/agents) 已提供模型—工具循环。因此，选择不是“workflow或者ReAct”二选一：可让模型自主查证、澄清和回答，仍由程序处理状态、权限、幂等、取消及异步完成。

[Anthropic 的 agent eval 指南](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)强调同时查看执行轨迹和结果并组合验证方式。本基准据此保留实际回答/工具轨迹，分开机器契约与语义审核。当前先保留生产前置路由，加入无路由只读消融；取得可比实测前不宣称新架构更好。
