# runtime

三个专业 Agent 共享的运行底座。

当前已实现：

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

Phase 2 只提供 `read_value`、`write_value`、`finish`、`ask_user` 四个内存 Tool。Artifact IO、真实 LLM provider、filesystem/process/Git 和持久化数据库尚未实现，等出现对应阶段的真实使用者再增加。

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
