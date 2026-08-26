# contracts

跨模块稳定类型和接口。

计划包含：

- Workflow、WorkflowTask、WorkflowPatch；
- ModuleTaskRequest、ModuleResult；
- Attempt；
- ArtifactRef；
- Question/Answer；
- Capability；
- 公共 status 和 error code。

本包只表达语义，不执行 LLM、文件、进程、Git 或工作流。它不得依赖 runtime、orchestrator 或任何具体 Agent。

权威字段说明见 `docs/CONTRACTS.md`。
