# 真实 QGIS 问答复现、依据工具与澄清约束

## 实测

通过 Windows computer-use 在实际 QGIS 侧栏打开“检查当前数据”，发送两个只读追问；不是脚本模型。完整记录保存在 runtime/grounding-ui-before.json，原会话 session_bf057b1f8fb94cbb。

1. 追问 source_shapefile_coordinates 代表按度计算的依据：模型承认原说法无依据，但工具没有源码信息，仍无法回答真实实现。
2. 追问补第二个 NRO 是否受支持：模型以“当前输入只有一个、校验未报错”解释无法判断，未获取后端完整规划恰好一个 NRO 的硬限制。
3. 回查原对话：尚未明确容量类型时 Intent 已填 nro_max_cap=600，同时 request_summary 说需澄清。助手长篇展开三种容量并追加执行准备问题。目标与参数不是一致的状态。

这些现象表明：对话持久化修复有效，但事实获取与语义一致性不能由历史保留自动保证。未运行的讨论，也不应该被强迫进入工程执行决策。

## 与开源实现、资料对照

- Codex（既有本地固定提交 e72da2b）：`codex-rs/core/src/tools/handlers/request_user_input.rs` 128–140 附近以 VerifiedQuestionAnswer / VerifiedAnswer 记录提问与实际回答。借鉴点是把已确认内容与待回答内容分开；不是把模型猜测当作槽位答案。
- Kimi CLI（Python 归档版本 9ab1286）：`src/kimi_cli/tools/file/read.py` 和 read.md 提供带行号、有上限的文件读取。OpenCode（696f41b）的 `packages/opencode/src/tool/read.ts` / read.txt 也保留行号、限制和错误语义。借鉴点是让模型能读证据，而非只看数据标签猜实现。FTTH 不开放任意文件路径，只开放审核过的主题与源码片段。
- [Anthropic：Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)：工具应返回与判断有关的语义信息，并使用真实任务检查工具描述、输出与调用路径。对应修复：预检明确其检查层级并提供系统能力限制，算法依据工具按主题返回源码位置、摘要与边界。
- [Anthropic：Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)：区分最终结果与完整执行轨迹，结合确定检查和人工内容判断。对应验收：既检查无运行副作用、任务状态及原始工具返回，也人工评估实际回答是否混淆坐标存储/距离计算和分区/实体局站。

源码版本和远程链接见 CONTEXT_ORCHESTRATION_RESEARCH-20260926.md。本轮未引入外部 CLI 依赖，也没有让额外 LLM 用相同信息自我投票。

## 修改

- `backend/knowledge.py`：小型实现参考索引，distance/capacity 主题、源码行号、文件摘要和有界原文。定位失败显式返回 unavailable。源码依据是实现说明，不是某次规划已运行的证据。
- `service.validate`：返回 input_preflight、feasibility_checked=false 与完整规划局站数量限制。共享 FULL_NRO_COUNT，避免文档和实际检查各写一套数字。
- 问答：提供 read_algorithm_reference，并明确旧助手回复不是事实来源；未运行不能断言分配无解；只讨论不催用户准备运行。
- Intent：增加 unresolved_fields；存在待澄清问题时统一走 clarify，清空本轮猜测参数，阻止“先填写再问”。任务层清除对应未解决的旧参数。
- `tests/test_grounding.py`：检验澄清清除猜测、源码引用有效性、工具不可读取任意路径、预检不代表可行性、两 NRO 被拒及真实 LangChain 源码工具循环。脚本模型不作为在线回答正确率证明。

## 范围

只读证据与需求解释层改造，没有修改冻结算法。实现索引有意覆盖本轮发现的容量和距离主题；不声称是全算法自动知识库。模型仍可能误读证据，需保留真实回复验收。

## 后续实测暴露的二次问题

用户重启后，在新会话 session_ebcac7b2beac4547 实际测试：模糊容量600在3.8秒内正确澄清且参数为空；NRO短答正确续接目标并调用源码工具。但回答把独立任务三的15%容量折减推广到所有流程，还把ZNRO目标称为PM目标。随后坐标提问被原样返回：自由文本question字段被模型用来复述用户问题，代码却把任意非空question都当成阻塞澄清。

已进一步修复：仅明确clarify动作或unresolved_fields可触发澄清；其他动作清除多余question。容量证据改用实际PTECH原子分区代码，同时给出ZNRO与ZPM两层计算、后端full调用标记及旧15%分支的适用条件。该轮补丁测试曾被用户中断，随后恢复验证，不把修复前在线结果说成修复后通过。

新增 `benchmarks/` 进行系统性回归和只读ReAct消融，详见其中README。是否删掉前置语义路由需要同输入的真实对比；业务事务、幂等和异步归属不能靠ReAct提示词替代。
