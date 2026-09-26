# 上下文、目标与异步编排改造调研（2026-09-26）

## 现场证据与问题范围

实际故障有两层：短答“NRO”没有与助手提出的容量类型问题关联；用户补全“讨论 NRO 容量600，不执行”后，意图已经识别为 discuss，回答仍重复旧的数据检查。应用数据库保存了完整四轮，但模型 checkpoint 中缺少中间两轮澄清。

代码核对：需求识别原先只有 recent_requests（用户单边）；clarify 分支提前返回，不进入问答 checkpoint；问答不使用已识别目标；执行/查询各自掌握一部分上下文。另有解释阶段展示执行卡片、异步审查结果与当前目标缺乏明确关联的问题。仅追加提示词不能解决状态所有权。

## 源码样本（只读调研，未运行其安装器）

源码在 runtime/agent-research；此目录被 Git 忽略。下列提交固定此次观察的版本，不能泛指所有版本行为。

| 项目 | 提交 | 阅读位置与观察 |
|---|---|---|
| Codex CLI | e72da2b53805894878023d01949a25a082e0a5cb | ContextManager 将模型窗口、保留事实、用户输入修订和历史修订分开；目标单独保存并注入。子 agent 结果携带父线程/父轮次来源，终态通知经控制层路由，不自动变成用户新指令。 |
| Kimi CLI（Python 归档版本） | 9ab1286b8fe4e6bcd116949a27ce5e0ac3389c82 | Context 从 JSONL 恢复完整消息；子 agent 有独立 context、实例 ID、运行状态与最近任务 ID，不能并发恢复同一实例；压缩提示优先当前任务及未完成事项。归档代码仅作实现参考，不建议安装或作为新依赖。 |
| OpenCode | 696f41bc8e7586657375d53390925fc54c25d34c | task 工具创建有 parentID 的子会话，恢复使用 task_id；工具权限单独推导；后台结果带任务身份和 synthetic 标记；压缩保留近期完整轮次并区分摘要与普通消息。 |

源码入口：

- [Codex 历史及独立修订](https://github.com/openai/codex/blob/e72da2b53805894878023d01949a25a082e0a5cb/codex-rs/core/src/context_manager/history.rs#L90)、[工具消息配对](https://github.com/openai/codex/blob/e72da2b53805894878023d01949a25a082e0a5cb/codex-rs/core/src/context_manager/normalize.rs#L25)、[目标注入](https://github.com/openai/codex/blob/e72da2b53805894878023d01949a25a082e0a5cb/codex-rs/ext/goal/src/steering.rs#L56)、[子任务完成投递](https://github.com/openai/codex/blob/e72da2b53805894878023d01949a25a082e0a5cb/codex-rs/core/src/agent/control/completion.rs#L27)。
- [Kimi Context](https://github.com/MoonshotAI/kimi-cli/blob/9ab1286b8fe4e6bcd116949a27ce5e0ac3389c82/src/kimi_cli/soul/context.py#L18)、[子任务模型](https://github.com/MoonshotAI/kimi-cli/blob/9ab1286b8fe4e6bcd116949a27ce5e0ac3389c82/src/kimi_cli/subagents/models.py#L10)、[runner](https://github.com/MoonshotAI/kimi-cli/blob/9ab1286b8fe4e6bcd116949a27ce5e0ac3389c82/src/kimi_cli/subagents/runner.py#L220)、[压缩优先级](https://github.com/MoonshotAI/kimi-cli/blob/9ab1286b8fe4e6bcd116949a27ce5e0ac3389c82/src/kimi_cli/prompts/compact.md)。
- [OpenCode 任务创建与回传](https://github.com/anomalyco/opencode/blob/696f41bc8e7586657375d53390925fc54c25d34c/packages/opencode/src/tool/task.ts#L136)、[保留近期轮次](https://github.com/anomalyco/opencode/blob/696f41bc8e7586657375d53390925fc54c25d34c/packages/opencode/src/session/compaction.ts#L223)。

## 官方文档交叉核对

1. [LangChain Subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)：持续管理上下文的 supervisor 与一次分类 router 不同；输入、输出和会话状态应显式设计。FTTH 的固定算法与审查职责已知，不需要增加自由协商 agent。
2. [LangChain Context engineering](https://docs.langchain.com/oss/python/langchain/context-engineering)：模型实际看到的内容需要显式投影；对象存在数据库或运行时上下文中，不等于模型知道。
3. [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) 和 [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)：持久状态和中断恢复需要稳定关联身份；恢复可重入节点，外部副作用仍须幂等。不能把 checkpoint 当作规划事务已经完成的证明。
4. [OpenAI Agents SDK Context](https://openai.github.io/openai-agents-python/context/) 与 [State ownership](https://openai.github.io/openai-agents-python/running_agents/)：本地依赖和 LLM 上下文不同；选择一个会话历史所有者，避免叠加两套 continuation。这里保留应用 SQLite 为权威记录，checkpoint 仅保存单次模型工具循环。
5. [OpenAI orchestration](https://openai.github.io/openai-agents-python/multi_agent/)：LLM 适合语义路由，代码适合确定步骤与约束。保留模型理解请求与程序执行校验的分工。
6. [Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)：有限窗口须保留高价值状态、近期证据和未完成目标。不能把“只讨论”压缩掉，也不应把完整工具轨迹广播给所有角色。
7. [Anthropic multi-agent research](https://www.anthropic.com/engineering/multi-agent-research-system)：委派需清楚说明目标、边界和返回形式；多 agent 的收益来自可分解任务，并非对话失忆的自动解药。

## 方案比较与选择（本项目判断）

| 方案 | 取舍 | 结论 |
|---|---|---|
| 仅补充历史提示词 | 可缓解短答，但没有目标修订、异步归属或恢复约束 | 不作为完整方案 |
| 全部 agent 共用完整工具历史 | 容易旧工具结果干扰新问题，审查失去上下文隔离 | 不采用 |
| agent 自由互相移交对话 | 用户不知道谁负责；状态和取消处理更复杂 | 不适合固定 FTTH 流程 |
| 更换为 Codex/Kimi/OpenCode 运行时 | 系统/供应商和工具差异大，不能直接解决项目状态语义 | 不引入其代码/依赖 |
| 一个持久主任务协调层 + 受限执行器和审查 agent | 完整会话统一来源，目标明确，子任务输入输出可核对 | 采用 |

## 改造契约

- **Conversation**：应用保存完整用户/助手轮次，包括澄清；两个模型阶段读取相同来源。旧工具轨迹不跨轮复用，当前轮内工具调用与结果配对保留。
- **Task**：独立保存目标、模式、约束、待回答问题、来源消息、参数及修订。用户补充条件延续任务；明确新需求创建新目标；“只讨论”不会因短答变为执行。
- **Workflow**：本条请求的可执行流程，绑定 task_id + task_revision 和用户原文。SQLite 原子动作记录继续保证最多一个根运行；一个只包 prepare 的图不承担额外职责。
- **Worker**：算法和审查具有独立身份、父任务/修订、输入批次和状态。执行器不是 LLM；审查 agent 仅拿固定任务摘要与本批次证据，没有规划或导出工具。
- **Result**：带原任务/修订回传。晚到或重复结果保留为历史事实，不能覆盖新的目标、回退终态或把旧批次置为当前活跃批次。
- **Context budget**：近期完整问答 + 当前目标快照 + 来源引用；旧消息可按需读取。任务字段不因普通历史裁剪消失；不可凭模型摘要授予执行权。
- **Recovery**：原始会话不重写；新结构增量保存。重启保留待澄清目标，运行中的子任务明确中断，不自动重放计算。
- **UI**：聊天澄清作为普通回复；无运行的问答不展示执行安排或取消计算按钮。任务和各 worker 状态分别呈现。

## 验证要求

短答与完整问题均回答当前目标；多轮澄清保留只讨论/600；切换话题不强制延续；应用重启后可恢复澄清；跨会话隔离；晚到审查不会改变新目标；重复事件不回退终态；重算子运行优先于根运行旧回调；取消及重启不重放；只读目标不能授权规划/导出；旧客户端与旧历史可读取。脚本模型用于验证契约，真实模型另行检查语义识别和回答相关性，不混淆两类证据。

## 落地结果与验收边界

`backend/coordinator.py` 实现统一会话投影、持久任务修订、受保护的澄清链、历史按需读取、worker 状态与恢复。`workflow.py` 负责模型理解及确定执行路由；移除只包含单个 prepare 节点的形式化图。`agent.py` 每轮重建用户/助手历史，使用已识别目标，保留当前工具循环；`service.py` 原子绑定重算归属并检查任务版本；`review.py` 只接收固定任务快照与本次证据。没有引入第三方 CLI 的运行时或源码依赖。

155 项回归通过、1 项跳过。真实 QGIS Qt/SSE 界面、51 IMB 规划及导出通过，模型部分仍为脚本模型。详见 `VALIDATION.md` 和 `runtime/release-0.6.0/e2e-918603cb/acceptance.json`。真实模型入口为 `scripts/verify_conversation.py --real-model`；当前终端无配置，因此不把模型输入断言当作实际语义准确率。

这是单机应用内的受控编排，不是通用多 agent 平台。继续/新建目标仍依赖 LLM 语义判断；程序负责状态归属及副作用边界。没有声称消除所有模型误解，也未增加自动长期摘要或算法断点恢复。应用保留原始问答，可按消息 ID 读取；预算不足会明确失败，不静默删除正在澄清的问题。
