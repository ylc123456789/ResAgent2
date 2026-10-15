# runtime

三个专业 Agent 共享的运行底座。

共享机制：

- AgentDefinition；
- AgentLoop；
- provider-neutral LLM client protocol、OpenAI-compatible 原生工具调用和 ScriptedLLMClient；
- 有总 token 预算的 Context Composer，以及显式配置的 ModelProfile；
- Tool registry/dispatcher；
- PermissionPolicy；
- action/observation/error/compaction event 和 session 快照持久化协议；
- 确定性 completion check；
- 共享 execution_budget、请求发送前用量接口、总截止时间及结构化错误（step 仅为动作序号）；
- `needs_user_input` 信号。

runtime 提供机制，不包含科研、代码修改或实验策略，也不包含 ResAgent Workflow Scheduler。

workspace、process、Git、Artifact 读取等具体操作位于 `resagent2_components`，通用模型 Tool 位于 `resagent2_capabilities`。runtime 不依赖二者，只保留
Agentic Loop、上下文、LLM client、Session、Tool 协议与控制类 Tool。

`ModelProfile` 只描述一个已注入模型的上下文窗口、输出预留和安全余量；
`AgentDefinition.max_context_tokens` 描述当前模块自己的输入上限。Loop 使用两者
计算实际预算，并把 Agent 原生请求的 `messages + tools` 完整序列化结果，以及 Compiler 结构化输出的 schema 分别计入各自模型容量。模型能力来自组合根配置，runtime
不查询供应商，也不维护模型名称表。

ContextComposer 完整组织固定契约、任务需求和工件目录；Loop 统一计量原生消息、工具 schema 与协议历史，只有较早完整 assistant/tool 回合可以摘要。正文和执行反馈只在原生回执，不再构造可伸缩材料工作集。固定契约指纹在恢复时校验 prompt/tools 不变。业务问答直接通过请求字段完整交付；权限批准继续原调用，不累积为任务需求，也不创建问答工件。细节见 [CONTEXT](../../docs/current/CONTEXT.md)。

非循环调用方可用 `PromptLLMClient(client, system_prompt=..., max_context_tokens=...)`：传普通 prompt 和结果 schema，共用 Composer/模型容量/trace/attempt 计量，不需要 Session、Tool 或 AgentLoop。CLI 与 E2E 的 Compiler 使用它；Interpreter 用固定代码组织材料，不调用模型。runtime 不认识编译或解释业务。

`ModelRequestClient.request(body)`供托管搜索等组件发送一次独立的模型JSON请求。调用方构造和解释供应商格式；Runtime只负责HTTP上限、发送前共享用量登记、总截止时间和私有trace，不增加Agent、Session或重试。full保留请求及可取得的JSON响应，metadata保留内容hash；HTTP失败只保留状态和Retry-After，认证header不进trace，当前key即使被回显也会遮蔽。`response_valid`只说明返回了JSON对象，业务成功由调用组件校验。

文件/Git/进程/Artifact、环境、仓库 materialization、数据集、硬件和领域策略
均不属于 runtime。

## 关键控制顺序

```text
固定使用原生工具调用
  → 从 Tool.input_model 派生完整 tools schema，并固定 Session 契约指纹
  → 完整构建 Context，必要时压缩旧工具回合
  → 每轮取得 1–8 个原生候选动作
  → action schema 校验
  → Tool 是否属于 Profile
  → Tool input schema 校验与 PermissionPolicy：allow / ask / deny
  → 原生批次整体预检，再串行执行并逐项生成 ToolObservation
  → 保存状态快照
  → CompletionCheck
```

`AgentAction.arguments` 保持通用对象，同一 Loop 复用不同 Tool 集；工具名及输入校验只由实际 ToolRegistry 决定。所有 Agent 客户端，包括 ScriptedLLMClient，都必须实现原生 `next_tool_call`；ScriptedLLMClient 接收 ToolCallTurn，不接收正文 JSON 动作。Compiler 通过独立 PromptLLMClient.next_action 请求唯一 route_tasks 原生调用，传输客户端保留供显式 JSON 调用方使用的独立 next_action 接口。两者共用既有传输、上下文容量与 Run 用量，Agent 的协议历史只属于 Session。

PermissionDecision 的 outcome 只有 allow / ask / deny。Runtime 负责应用结果与暂停恢复，Capabilities 的 OperationPermissionPolicy 适配 Components 的固定规则与执行边界；工具实际操作前仍校验边界。deny 作为有界可恢复反馈，让模型可改用合法工具。ask 保存 ActionSnapshot 与 Session.pending_action，通过直接 PendingQuestion/RecordedAnswer 确认；批准绑定当前动作、参数、上下文及作用域，派发前先持久消费。不能用回答扩权或复用已消费批准。

原生回复每轮接受 1–8 个 tool calls，按数组顺序串行执行；整批先校验参数和权限，逐个执行前仍复核权限及超时。`finish`、`ask_user`、`request_work`、`reply_to_task`、`abandon_work` 必须单独调用；工具说明、通用规则与拒绝反馈共用 `CONTROL_TOOLS` 定义。零个、超量或混入控制工具的批次整体拒绝，反馈点名实际控制工具并说明整批未执行、应单独重试，assistant `content` 不作为备用动作。中途失败时保留已执行结果，取消剩余调用，不回滚或自动重放。8 是共享的单批安全上限，不是任务步数预算。

每次模型 HTTP 尝试发送前从当前共享执行预算生成第三部分运行事实：整个 Run 的调用上限、已用、剩余和剩余执行时间。明确覆盖主 Agent、Compiler、子 Agent、后续委派及最终交付，不是当前任务的专属额度；独立调用标注 invocation 范围。重试重新生成和计量请求，失败及已发送的取消调用不退款。该投影不持久化到原生历史，不改变限额或分配策略。

Session 创建时固定非空 `tool_protocol_key`：OpenAICompatibleClient 使用不含 API key 的协议、endpoint、model 身份 hash；当前串行检查点协议为 `openai-compatible-tools/v2`。其他原生客户端必须提供非空、稳定且能标识协议配置的 `tool_session_key`。恢复不允许协议/endpoint/model 切换，没有隐式迁移。

`tool_turns` 保存 assistant 的 `content`、`reasoning_content`、原始 `tool_calls` 和按调用 ID 配对的 `tool_results`。整批先保存，每个工具派发前记录 `executing_call_id`，结果和执行标记的清除一起保存。工件冻结先于回执保存时，可沿同一 operation key 恢复已登记工件，再保存明确的中断回执；不重跑工具。当时正在执行且无法恢复结果的调用记为 unknown outcome；其余缺回执调用记为未执行。这是进程重启 checkpoint，不保证掉电持久化，也不是跨外部副作用的 exactly-once 事务。

下一轮原生请求发送检查点摘要、近期已配对的 assistant/tool 协议消息，以及重新构建的业务 Context；不会累积旧的完整业务 prompt。较早完整交互可在输入压力下总结，原始 tool_turns/events 不删除。Session 文件始终按目录 `0700`、文件 `0600` 保存，和 trace 档位无关。`reasoning_content` 只为同一 Session 的供应商协议续传，不进入 memory、事件证据或业务完成判据。

原生工具参数解析失败以标准 `json.JSONDecodeError` 交还 Loop，不在客户端原样重试。AgentLoop 把简短原因放入对应历史回执，同 Session/Attempt 纠正；无法形成合法调用的协议错误保存为历史协议反馈。它们与 schema 错误、未知工具共用连续失败上限、调用预算和超时，不再增加第四部分反馈段。网络/响应封装故障仍按原有策略有界重试，每次实际尝试都入账。非循环调用方经 PromptLLMClient 收到相同异常，自行使用其既有纠错边界；适配器不增加隐藏重试。

Tool 不直接修改 AgentState，只返回 memory_updates 等结构化结果，由 AgentLoop 应用。所有 Agent 共用 FinishTool，提交 FinishCandidate(status, report, artifacts)，status 复用 completed/failed。CompletionDecision.complete 表示候选事实检查通过，随后由 Loop 接通现有 AgentResult.status；模型声明失败使用 AGENT_REPORTED_FAILURE，与真实技术故障区分。候选被拒绝则沿原反馈继续同一 Session；Scientific 只接受完成的合法意见，不以 failed 绕过 Run 交付要求。

AgentLoop 统一返回 `AgentResult`。业务提问和工作请求先保存稳定的 ControlSignal、来源调用与问题/工作 ID，再保存原生接收回执，暂停等待系统处理。业务回答通过 RecordedAnswer 进入完整任务对话；工作结果通过 WorkResult 导航进入完整目录，不改写旧接收回执，也不生成控制工件或汇总报告。权限批准保存原操作并暂不生成该操作回执；恢复后复核并直接继续原调用，最终保存唯一执行或拒绝回执。只有获得 `permissions.request_work` 的 Scientific 调用可以派工。

终态 `AgentResult` 与 Session 的 completed/failed 状态在同一快照保存。契约与作用域校验通过后的终态重入返回原结果、当次用量为零，不再调用模型或重跑工具；专业 Agent 的确定性后处理仍沿原入口执行。

`full` trace 还会保存 provider 明确返回的 `raw_reasoning_text` 与 `raw_tool_calls`（若有）；`metadata` 对请求、响应、动作和 tool calls 的内容只保存 hash。metadata 不保存这些原文，不代表 Session 不保存续传所需的 `tool_turns`；Session 与 trace 是两个独立持久化边界。

## 预算与最小压缩

当前 RunBudget/TaskBudget 只含 max_llm_calls 和 timeout_seconds；任务数与每任务尝试数由 ExecutionLimits 单独限制。Controller/Scheduler 为执行绑定 [execution_budget](src/resagent2_runtime/budget.py)，注入持久 RunUsagePort；嵌套调用只能收紧调用上限和截止时间，共享同一个 UsagePort。独立 Agent 调用可使用同一机制的内存用量，不具有跨进程 Run 持久化保证。

每次实际模型 HTTP 请求先 charge，再发送，最后 complete 为 succeeded/failed/unknown；重试、格式纠正和摘要都占用同一余额。Run 原子保存失败不发送，中断后的 unknown 不退款。AgentResult.llm_calls 与 Session 计数是用量差额投影，不再二次扣费；last_attempts 只是诊断。invoke_model 为最小客户端预扣一次，manages_usage 客户端须自行通过同一接口逐次登记传输。该占用不冒充供应商精确账单，也不是跨 Run/Session/Provider 的事务。

[http.py](src/resagent2_runtime/http.py) 使用 httpx 与可取消的总超时覆盖完整响应读取；每次 HTTP/重试取操作超时与当前截止时间的较小值。Components 的进程与环境操作沿用该截止时间并在超时后终止进程树。显式等待用户的时间由 Run 扣除，安装、下载及普通停机不扣除；恢复不重置余额。step 只记动作时序，没有隐藏的 50 步/50 次上限。

HTTP 取消后不等待默认 executor 的 DNS 线程。系统解析可能在后台结束，但不会恢复已取消的 HTTP 请求；本地取消不保证供应商停止计算或计费。

普通 HTTP 调用保持原行为。可选 `send_request(public_only=True)` 在同一超时范围内异步解析并检查全部目标地址，只连接已验证的公网 IP，保留原域名的 Host/TLS SNI 和逻辑响应 URL；重定向逐次检查，拒绝 localhost、非公网和混合公私地址。该路径关闭环境代理和连接复用，避免代理重新解析域名或不同 TLS 域名共用按 IP 建立的连接。Components 的网页抓取使用这一传输约束；Runtime 不识别网页/论文业务、不登记工件或评价来源。

共用 [compaction.py](src/resagent2_runtime/compaction.py)：完整原生输入超过有效上限 80%，或完整上下文实际装不下时，尝试总结较早完整 turn；保留至少最新完整 turn，近期历史以扣除固定契约、完整任务及完整目录后剩余额度的 20% 为目标。仅支持压缩的客户端调用 summarize_history，OpenAICompatibleClient 复用原 HTTP/trace/计量实现；没有单独摘要 Agent。

摘要生成目标依据上述剩余额度及保留回合占用计算，目标最多按 4096 估算 tokens 换算；这是写短的提示，不是第二个硬预算，**不缩小 Provider 的总输出额度**。Loop 检查完整摘要、近期回合、完整任务、完整目录及工具 schema；略超目标但整包能装下即可完整接受，不截断摘要。摘要与边界 history_checkpoint 验证后一起保存，并留下 compaction 审计事件；空摘要、截断响应或整包真正超限时不推进边界。摘要不是证据，精确代码必须重读。

摘要仍有损、消耗调用和时间；剩余调用不足以摘要后继续、单个巨大 turn 无法容纳、摘要失败或重建后仍超限时明确失败。不自动扩容、不改写原事件，也不保证 Session 磁盘文件永久不增长。详见 [上下文与压缩](../../docs/current/CONTEXT.md#compaction)。

## 安装与测试

```bash
conda activate ResAgent2
python -m pytest tests/runtime
```

稳定导入路径是 `resagent2_runtime`，当前包版本为 `0.1.0`。
