# runtime

三个专业 Agent 共享的运行底座。

Phase 4 已实现：

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

Phase 5 曾在本包内孵化 workspace、process、Git、Artifact 读取等可复用实现；
Phase 6.5 已将这些具体能力迁移到 `resagent2_capabilities`。runtime 当前只保留
Agentic Loop、上下文、LLM client、Session、Tool 协议与控制类 Tool。

`ModelProfile` 只描述一个已注入模型的上下文窗口、输出预留和安全余量；
`AgentDefinition.max_context_tokens` 描述当前模块自己的输入上限。Loop 使用两者
计算实际预算，并把 Action schema 计入模型容量。模型能力来自组合根配置，runtime
不查询供应商，也不维护模型名称表。

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
Tool 不直接修改 AgentState，只返回 `memory_updates` 等结构化结果，由 AgentLoop 统一应用。`FinishTool` 只能产生 FinishCandidate，最终 ModuleStatus 由 CompletionCheck 决定。CompletionCheck 的 `CompletionDecision` 支持三种结果：`complete=True` 得 completed；`failure` 非空得 failed（确定性失败出口，由 finalizer 用真实 Tool observation 验证，LLM 不能自证失败）；两者皆否时继续循环。

`full` trace 还会保存 provider 明确返回的 `reasoning_content`（若有）。它只用于调试，不进入 AgentState、Session 或下一轮上下文；`metadata` 与 `off` 档不保存该内容。

## 安装与测试

```bash
conda activate ResAgent2
python -m pytest tests/runtime
```

稳定导入路径是 `resagent2_runtime`，当前包版本为 `0.1.0`。
