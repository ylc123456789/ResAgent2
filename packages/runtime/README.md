# runtime

三个专业 Agent 共享的运行底座。

Phase 4 已实现：

- AgentDefinition；
- AgentLoop；
- provider-neutral LLM client protocol 和 ScriptedLLMClient；
- 有总 token 预算的 Context Composer；
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

文件/Git/进程/Artifact、环境、仓库 materialization、数据集、硬件和领域策略
均不属于 runtime。

## 关键控制顺序

```text
构建并裁剪 Context
  → LLM 返回 AgentAction
  → action schema 校验
  → Tool 是否属于 Profile
  → PermissionPolicy
  → Tool input schema 校验
  → 执行并生成 ToolObservation
  → 保存状态快照
  → CompletionCheck
```

Tool 不直接修改 AgentState，只返回 `memory_updates` 等结构化结果，由 AgentLoop 统一应用。`FinishTool` 只能产生 FinishCandidate，最终 ModuleStatus 由 CompletionCheck 决定。

## 安装与测试

```bash
conda activate ResAgent2
python -m pytest tests/runtime
```

稳定导入路径是 `resagent2_runtime`，当前包版本为 `0.1.0`。
