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
- timeout、唯一的 LLM-call budget 和结构化错误（step 仅为动作序号）；
- `needs_user_input` 信号。

runtime 提供机制，不包含科研、代码修改或实验策略，也不包含 ResAgent Workflow Scheduler。

workspace、process、Git、Artifact 读取等具体能力位于 `resagent2_capabilities`。runtime 只保留
Agentic Loop、上下文、LLM client、Session、Tool 协议与控制类 Tool。

`ModelProfile` 只描述一个已注入模型的上下文窗口、输出预留和安全余量；
`AgentDefinition.max_context_tokens` 描述当前模块自己的输入上限。Loop 使用两者
计算实际预算，并把正文 JSON 的 Action schema，或原生请求的 `messages + tools` 完整序列化结果计入模型容量。模型能力来自组合根配置，runtime
不查询供应商，也不维护模型名称表。

ContextComposer 对包含标题、分隔符及原生协议开销的完整请求统一估算预算。固定 `ContextSection` 原样保留；可伸缩的 `ContextMaterial(name, render, weight, priority, required)` 仅是本轮纯渲染描述，不持久化。先放固定段和材料最小导航框，再给材料相对起始份额，空余按优先级借用。材料扩展到整包80%软水位，与历史压缩触发点同源；固定必需输入仍可使用到100%硬上限。过大的材料因此缩减，而不是因为局部比例直接报错。

`render(chars)` 负责来源、截断/省略语义；Composer 不自行切开代码、JSON或工具调用/回执。文件、工件、诊断、目录通过 capabilities 共用此机制，不给三个Agent分别写分配器。最终仍放不下就走现有超限失败，不增加自动暂停或摘要重试。字符估算不是供应商的精确token数。规则与例子见[上下文预算](../../docs/current/CONTEXT.md#budgets)。

`user_answers_section(answers)` 将调用方已选定作用域的 RecordedAnswer 按传入顺序投影为 required `answers` 段，包含由 Controller 配对的 question_text 与用户 values；没有回答时不生成段。Coding/Experiment 的 context builder 共用它，Scientific 保留原有答案段。它不读取 Session、不缓存答案、不改变问题路由；答案与其他上下文一起计入 Composer 预算，超限明确失败而非静默遗漏。历史 `ask_user [ok]` 只表示问题已发出，不能代替原题、真实回答或前提已满足的证据。

非循环调用方可用 `PromptLLMClient(client, system_prompt=..., max_context_tokens=...)`：传普通 prompt 和结果 schema，共用 Composer/模型容量/trace/attempt 计量，不需要 Session、Tool 或 AgentLoop。CLI 与 E2E 的 Compiler 都使用它；runtime 不认识编译器业务。

文件/Git/进程/Artifact、环境、仓库 materialization、数据集、硬件和领域策略
均不属于 runtime。

## 关键控制顺序

```text
选择客户端支持的调用协议
  → OpenAICompatibleClient：从 Tool.input_model 派生完整原生 tools schema
    或无原生能力的测试/注入客户端：注入简短 tool_contracts，正文返回 AgentAction JSON
  → 构建并裁剪 Context
  → 每轮取得 1–8 个原生候选动作，或一个正文 JSON 候选
  → action schema 校验
  → Tool 是否属于 Profile
  → PermissionPolicy
  → Tool input schema 校验
  → 原生批次整体预检，再串行执行并逐项生成 ToolObservation
  → 保存状态快照
  → CompletionCheck
```

`AgentAction.arguments` 保持通用对象，以便同一 Loop 复用不同 Tool 集。`OpenAICompatibleClient` 的 AgentLoop 走 `next_tool_call`，把每个 Tool 既有 `input_model` 的完整 JSON Schema 放进原生 `tools` 参数；Compiler 仍经 `PromptLLMClient.next_action` 从正文读取 JSON。没有 `next_tool_call` 的测试或注入客户端继续使用 `next_action`，Loop 为它们从同一 `input_model` 渲染简短必填参数契约。两条路径最终都由 ToolRegistry 做完整输入模型校验，不改变 Tool、AgentAction 或 Compiler 的业务接口。

原生回复每轮接受 1–8 个 tool calls，按数组顺序串行执行；整批先校验参数和权限，逐个执行前仍复核权限及超时。`finish`、`ask_user`、`request_work` 必须单独调用；零个、超量或混入控制工具的批次整体拒绝，assistant `content` 不作为备用动作。中途失败时保留已执行结果，取消剩余调用，不回滚或自动重放。8 是共享的单批安全上限，不是任务步数预算。

Session 创建时固定 `tool_protocol_key`：正文 JSON 为 `None`；OpenAICompatibleClient 使用不含 API key 的协议、endpoint、model 身份 hash；当前串行检查点协议为 `openai-compatible-tools/v2`。其他原生客户端必须提供非空、稳定且能标识协议配置的 `tool_session_key`。恢复不允许协议/endpoint/model 切换，没有隐式迁移。

`tool_turns` 保存 assistant 的 `content`、`reasoning_content`、原始 `tool_calls` 和按调用 ID 配对的 `tool_results`。整批先保存，每个工具派发前记录 `executing_call_id`，结果和执行标记的清除一起保存。进程重启时已完成回执不变；当时正在执行但无回执的调用记为 unknown outcome；其余缺回执调用记为未执行。绝不自动重放；这是进程重启 checkpoint，不保证掉电持久化，也不是跨外部副作用的 exactly-once 事务。

下一轮原生请求发送检查点摘要、近期已配对的 assistant/tool 协议消息，以及重新构建的业务 Context；不会累积旧的完整业务 prompt。较早完整交互可在输入压力下总结，原始 tool_turns/events 不删除。Session 文件始终按目录 `0700`、文件 `0600` 保存，和 trace 档位无关。`reasoning_content` 只为同一 Session 的供应商协议续传，不进入 memory、事件证据或业务完成判据。

模型正文解析失败以标准 `json.JSONDecodeError` 交还调用方，不在客户端原样重试。AgentLoop 把解析原因送入现有 required `runtime_feedback`，同 Session/Attempt 纠正；与 schema 错误共用连续失败上限、调用预算和超时，不执行非法 JSON 中看似正确的动作前缀。只把简短原因送回模型；正文 JSON 的原始坏正文只在 full trace 保留，原生回复则按上述协议边界进入 Session。网络/响应封装故障仍按原有策略有界重试，每次实际尝试都入账。非循环调用方经 PromptLLMClient 收到相同异常，自行使用其既有纠错边界；适配器不增加隐藏重试。

Tool 不直接修改 AgentState，只返回 `memory_updates` 等结构化结果，由 AgentLoop 统一应用。`FinishTool` 只能产生 FinishCandidate，最终 ModuleStatus 由 CompletionCheck 决定。CompletionCheck 的 `CompletionDecision` 支持三种结果：`complete=True` 得 completed；`failure` 非空得 failed（确定性失败出口，由 finalizer 用真实 Tool observation 验证，LLM 不能自证失败）；两者皆否时继续循环。

`full` trace 还会保存 provider 明确返回的 `raw_reasoning_text` 与 `raw_tool_calls`（若有）；`metadata` 对请求、响应、动作和 tool calls 的内容只保存 hash。metadata 不保存这些原文，不代表 Session 不保存续传所需的 `tool_turns`；Session 与 trace 是两个独立持久化边界。

## 预算与最小压缩

schema 8.0 的 TaskBudget 只含 max_llm_calls 和 timeout_seconds；Controller/Scheduler 下发 Run 当前余额，没有隐藏的 50 步/50 次上限。step 是已尝试动作序号，一次原生回复可产生多个动作；模型请求、HTTP 重试、格式失败、摘要调用均共用调用账本。非法 last_attempts 不伪造为 1。正常暂停恢复沿用 Run 余额；跨 Run/Session/外部请求没有事务级 exactly-once 计量。

共用 [compaction.py](src/resagent2_runtime/compaction.py)：完整原生输入超过有效上限 80%，或必需上下文实际装不下时，尝试总结较早完整 turn；保留至少最新完整 turn，近期历史以 20% 额度为目标。仅支持压缩的客户端调用 summarize_history，OpenAICompatibleClient 复用原 HTTP/trace/计量实现；没有单独摘要 Agent。

摘要生成目标由输入额度的 5% 派生，目标最多按 4096 估算 tokens 换算；这是写短的提示，不是第二个硬预算，**不缩小 Provider 的总输出额度**。原 Composer 检查完整摘要、近期回合、当前领域上下文及工具 schema；略超目标但整包能装下即可完整接受，不截断摘要。摘要与边界 history_checkpoint 验证后一起保存，并留下 compaction 审计事件；空摘要、截断响应或整包真正超限时不推进边界。当前请求和领域状态仍由原 builder 构造，摘要不是证据，精确代码必须重读。

摘要仍有损、消耗调用和时间；剩余调用不足以摘要后继续、单个巨大 turn 无法容纳、摘要失败或重建后仍超限时明确失败。不自动扩容、不改写原事件，也不保证 Session 磁盘文件永久不增长。详见 [上下文与压缩](../../docs/current/CONTEXT.md#compaction)。

## 安装与测试

```bash
conda activate ResAgent2
python -m pytest tests/runtime
```

稳定导入路径是 `resagent2_runtime`，当前包版本为 `0.1.0`。
