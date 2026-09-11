# runtime

三个专业 Agent 共享的运行底座。

共享机制：

- AgentDefinition；
- AgentLoop；
- provider-neutral LLM client protocol 和 ScriptedLLMClient；
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
计算实际预算，并把 Action schema 计入模型容量。模型能力来自组合根配置，runtime
不查询供应商，也不维护模型名称表。

ContextComposer 对包含标题和分隔符的最终文本统一估算预算，必需段装不下就拒绝，不先调用 LLM。这个值仍是字符估算，不是供应商的精确 token 数。

`user_answers_section(answers)` 将调用方已选定作用域的 UserAnswer 按传入顺序投影为 required `answers` 段；没有回答时不生成段。Coding/Experiment 的 context builder 共用它，Scientific 保留原有答案段。它不读取 Session、不缓存答案、不改变问题路由；答案与其他上下文一起计入 Composer 预算，超限明确失败而非静默遗漏。历史 `ask_user [ok]` 只表示问题已发出，不能代替真实回答或前提已满足的证据。

非循环调用方可用 `PromptLLMClient(client, system_prompt=..., max_context_tokens=...)`：传普通 prompt 和结果 schema，共用 Composer/模型容量/trace/attempt 计量，不需要 Session、Tool 或 AgentLoop。CLI 与 E2E 的 Compiler 都使用它；runtime 不认识编译器业务。

文件/Git/进程/Artifact、环境、仓库 materialization、数据集、硬件和领域策略
均不属于 runtime。

## 关键控制顺序

```text
从本轮 Tool 的 `input_model` 派生必填参数契约
  → 构建并裁剪 Context（契约作为 required section 一并注入）
  → LLM 返回 AgentAction
  → action schema 校验
  → Tool 是否属于 Profile
  → PermissionPolicy
  → Tool input schema 校验
  → 执行并生成 ToolObservation
  → 保存状态快照
  → CompletionCheck
```

`AgentAction.arguments` 保持通用对象，以便同一 Loop 复用不同 Tool 集；Loop 会从每个 Tool 的既有 `input_model` 自动渲染必填顶层参数契约，并让 Context Composer 统一裁剪、计账和记录 trace。模型得到这份短契约后仍由 ToolRegistry 做完整输入模型校验；因此没有为每个 Agent 复制一套参数提示，也不会把未校验的参数直接交给 Tool。

模型正文解析失败以标准 `json.JSONDecodeError` 交还调用方，不在客户端原样重试。AgentLoop 把解析原因送入现有 required `runtime_feedback`，同 Session/Attempt 纠正；与 schema 错误共用连续失败上限、调用预算和超时，不执行非法 JSON 中看似正确的动作前缀。只把简短原因送回模型，原始坏正文和 reasoning 仅在 full trace 保留。网络/响应封装故障仍按原有策略有界重试，每次实际尝试都入账。非循环调用方经 PromptLLMClient 收到相同异常，自行使用其既有纠错边界；适配器不增加隐藏重试。

Tool 不直接修改 AgentState，只返回 `memory_updates` 等结构化结果，由 AgentLoop 统一应用。`FinishTool` 只能产生 FinishCandidate，最终 ModuleStatus 由 CompletionCheck 决定。CompletionCheck 的 `CompletionDecision` 支持三种结果：`complete=True` 得 completed；`failure` 非空得 failed（确定性失败出口，由 finalizer 用真实 Tool observation 验证，LLM 不能自证失败）；两者皆否时继续循环。

`full` trace 还会保存 provider 明确返回的 `reasoning_content`（若有）。它只用于调试，不进入 AgentState、Session 或下一轮上下文；`metadata` 与 `off` 档不保存该内容。

## 安装与测试

```bash
conda activate ResAgent2
python -m pytest tests/runtime
```

稳定导入路径是 `resagent2_runtime`，当前包版本为 `0.1.0`。
