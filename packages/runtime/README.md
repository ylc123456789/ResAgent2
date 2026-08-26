# runtime

三个专业 Agent 共享的运行底座。

计划包含：

- AgentDefinition；
- AgentLoop；
- LLM client；
- Context Composer；
- Tool registry/dispatcher；
- PermissionPolicy；
- event/session persistence；
- Artifact IO；
- 通用 filesystem/process/Git 能力（在真实第二使用者出现后逐步提取）。

runtime 提供机制，不包含科研、代码修改或实验策略，也不包含 ResAgent Workflow Scheduler。
