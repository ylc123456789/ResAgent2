# runtime

三个专业 Agent 共享的运行底座。

共享机制：

- AgentDefinition；
- AgentLoop；
- provider-neutral LLM client protocol、OpenAI-compatible 原生工具调用和 ScriptedLLMClient；
- 有总 token 预算的 Context Composer，以及显式配置的 ModelProfile；
- Tool registry/dispatcher；
- PermissionPolicy；
- action/observation/error event 和 session 快照持久化协议；
- 确定性 completion check；
- timeout、step/LLM-call budget 和结构化错误；
- `needs_user_input` 信号。

runtime 提供机制，不包含科研、代码修改或实验策略，也不包含 ResAgent Workflow Scheduler。

workspace、process、Git、Artifact 读取等具体能力位于 `resagent2_capabilities`。runtime 只保留
Agentic Loop、上下文、LLM client、Session、Tool 协议与控制类 Tool。

`ModelProfile` 只描述一个已注入模型的上下文窗口、输出预留和安全余量；
`AgentDefinition.max_context_tokens` 描述当前模块自己的输入上限。Loop 使用两者
计算实际预算，并把正文 JSON 的 Action schema，或原生请求的 `messages + tools` 完整序列化结果计入模型容量。模型能力来自组合根配置，runtime
不查询供应商，也不维护模型名称表。

ContextComposer 对包含标题和分隔符的最终文本统一估算预算，必需段装不下就拒绝，不先调用 LLM。这个值仍是字符估算，不是供应商的精确 token 数。

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
  → 每轮取得且只取得一个候选动作
  → action schema 校验
  → Tool 是否属于 Profile
  → PermissionPolicy
  → Tool input schema 校验
  → 执行并生成 ToolObservation
  → 保存状态快照
  → CompletionCheck
```

`AgentAction.arguments` 保持通用对象，以便同一 Loop 复用不同 Tool 集。`OpenAICompatibleClient` 的 AgentLoop 走 `next_tool_call`，把每个 Tool 既有 `input_model` 的完整 JSON Schema 放进原生 `tools` 参数；Compiler 仍经 `PromptLLMClient.next_action` 从正文读取 JSON。没有 `next_tool_call` 的测试或注入客户端继续使用 `next_action`，Loop 为它们从同一 `input_model` 渲染简短必填参数契约。两条路径最终都由 ToolRegistry 做完整输入模型校验，不改变 Tool、AgentAction 或 Compiler 的业务接口。

原生回复每轮接受 1–8 个 tool calls，按数组顺序串行执行；整批先校验参数和权限，逐个执行前仍复核权限及超时。`finish`、`ask_user`、`request_work` 必须单独调用；零个、超量或混入控制工具的批次整体拒绝，assistant `content` 不作为备用动作。中途失败时保留已执行结果，取消剩余调用，不回滚或自动重放。8 是共享的单批安全上限，不是任务步数预算。

Session 创建时固定 `tool_protocol_key`：正文 JSON 为 `None`；OpenAICompatibleClient 使用不含 API key 的协议、endpoint、model 身份 hash；当前串行检查点协议为 `openai-compatible-tools/v2`。其他原生客户端必须提供非空、稳定且能标识协议配置的 `tool_session_key`。恢复不允许协议/endpoint/model 切换，没有隐式迁移。

`tool_turns` 保存 assistant 的 `content`、`reasoning_content`、原始 `tool_calls` 和按调用 ID 配对的 `tool_results`。整批先保存，每个工具派发前记录 `executing_call_id`，结果和执行标记的清除一起保存。进程重启时已完成回执不变；当时正在执行但无回执的调用记为 unknown outcome；其余缺回执调用记为未执行。绝不自动重放；这是进程重启 checkpoint，不保证掉电持久化，也不是跨外部副作用的 exactly-once 事务。

下一轮原生请求重放已配对的 assistant/tool 协议消息，并追加这一次重新构建的业务 Context；不会保存或重放每轮旧的完整业务 prompt，也没有新增压缩记忆系统。Session 文件始终按目录 `0700`、文件 `0600` 保存，和 trace 档位无关。`reasoning_content` 只为同一 Session 的供应商协议续传，不进入 memory、事件证据或业务完成判据。

模型正文解析失败以标准 `json.JSONDecodeError` 交还调用方，不在客户端原样重试。AgentLoop 把解析原因送入现有 required `runtime_feedback`，同 Session/Attempt 纠正；与 schema 错误共用连续失败上限、调用预算和超时，不执行非法 JSON 中看似正确的动作前缀。只把简短原因送回模型；正文 JSON 的原始坏正文只在 full trace 保留，原生回复则按上述协议边界进入 Session。网络/响应封装故障仍按原有策略有界重试，每次实际尝试都入账。非循环调用方经 PromptLLMClient 收到相同异常，自行使用其既有纠错边界；适配器不增加隐藏重试。

Tool 不直接修改 AgentState，只返回 `memory_updates` 等结构化结果，由 AgentLoop 统一应用。`FinishTool` 只能产生 FinishCandidate，最终 ModuleStatus 由 CompletionCheck 决定。CompletionCheck 的 `CompletionDecision` 支持三种结果：`complete=True` 得 completed；`failure` 非空得 failed（确定性失败出口，由 finalizer 用真实 Tool observation 验证，LLM 不能自证失败）；两者皆否时继续循环。

`full` trace 还会保存 provider 明确返回的 `raw_reasoning_text` 与 `raw_tool_calls`（若有）；`metadata` 对请求、响应、动作和 tool calls 的内容只保存 hash。metadata 不保存这些原文，不代表 Session 不保存续传所需的 `tool_turns`；Session 与 trace 是两个独立持久化边界。

## 安装与测试

```bash
conda activate ResAgent2
python -m pytest tests/runtime
```

稳定导入路径是 `resagent2_runtime`，当前包版本为 `0.1.0`。
