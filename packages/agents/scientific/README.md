# Scientific Agent

科学顾问。

实现 ScientificPort 边界（[接口与契约](../../../docs/current/CONTRACTS.md#scientific)）：`ScientificAgent.run(ScientificTurnRequest)` 返回四态 `ScientificTurnResult`。

入口请求用一条 `ScientificTurnRequest.instruction` 表达当前研究回合的语义；`required_evidence_kinds`、数据集、授权工件、答案、工作结果和预算保持结构化，分别承担机器可检查的控制和状态职责。

- `request_work`：当前 `ScientificAssessment` + 语义化 `WorkRequestDraft`；
- `needs_user_input`：带 assessment 的用户问题（模型通过 ask_user 提议）；
- `completed`：最终 `ScientificOpinion`（模型通过 finish 提议）；
- `failed`：带原始诊断的模块失败。

finish 输入只含 opinion；Runtime 的完成摘要直接取 opinion.statement，不要求模型再写 summary。ask_user 复用 Runtime 的 text/requested_fields 输入，仅增加 assessment；需要用户知道的背景写在 text 中，不另写隐藏的 reason。requested_fields 使用 contracts 的 AnswerFieldName 机器键约束，Scientific 不另写一套规则。

允许：只读 Artifact、文献检索、科学推理。禁止：输出 WorkflowProposal/Patch、选择 capability/依赖/物理环境、修改 TaskStatus、调用其他子 Agent、把建议描述成已执行事实。

复用共享 AgentLoop；Session 属于 Run，因此 `task_id/attempt_number` 可为空。Tool 集：`read_artifact`（allowlist）、`literature_search`（注入 backend/registration port）、`request_work`、`ask_user`（带 assessment）、`finish`。finalizer 交叉检查 evidence 引用、派生 `observed_artifact_ids`；failed/blocked Task 由 Validator 从 Run 对账，并要求 Scientific 通过 `limitations` 说明其影响。模块失败通过 `failed` 返回分支保留真实错误，不是第四种模型控制动作。

## 证据怎样进入上下文

`context.build_context` 通过 `interpreter.render_work_brief` 整理执行结果，通过共享 `workspace_context(state)` 呈现成功读取的工件正文。后者不传 EnvironmentBinding，也不赋予 Scientific 文件写入、环境准备或执行工具。

工件片段从已有 Session events 按来源与行范围提取，近期优先选入、按原始事件顺序展示；artifact_reads 的导航框必需，正文由共享 Composer 按剩余额度分配。缺少的中部细节可以通过 `read_artifact(start_line, end_line)` 取回；不维护另一份 read_artifact_summaries 缓存。已观察 ID 只说明过去访问过，不表示完整正文仍可见，更不自动证明一项科学结论。

原生 Agent 与 CLI 默认输入上限为 128000 tokens，显式配置和模型容量仍是硬上限。原生工具历史按共享 Runtime 的检查点机制压缩；完整请求仍放不下时明确失败，不静默丢弃必需信息。原始事件、冻结工件和 full trace 保留，不新增 Scientific 专有的阅读笔记或记忆机制。完整规则见 [上下文](../../../docs/current/CONTEXT.md#scientific)。

## 外部检索失败时

文献检索、证据阅读与科学判断是本 Agent 自有工作。Prompt 要求 timeout/HTTP 429 经工具已有重试仍失败时，不通过 request_work 派代码/实验任务绕路；需要用户补充材料、作决定或确认服务恢复时用现有 ask_user。它是职责指引，不是确定性保证；正常检索与真实 LLM 故障注入验收见 `docs/history/reviews/SCIENTIFIC_CONTEXT_ACCEPTANCE.md`。
