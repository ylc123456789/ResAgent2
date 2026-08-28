# 架构决策记录

本目录只记录重要、长期、难以从代码直接看出的架构决策。

ADR 状态：

- proposed：讨论中；
- accepted：已确认，后续实现必须遵守；
- superseded：被新 ADR 取代；
- rejected：讨论后不采用。

不要为普通实现细节创建 ADR。接口字段写入 `CONTRACTS.md`，当前实施步骤写入 `DEVELOPMENT_PLAN.md`。

当前决策：

- `0001-monorepo-and-module-boundaries.md`；
- `0002-shared-agentic-loop.md`；
- `0003-llm-planning-deterministic-scheduling.md`；
- `0004-workspace-and-process-boundaries.md`；
- `0005-experiment-agent-and-content-addressed-env.md`；
- `0006-runtime-capabilities-boundary.md`；
- `0007-scientific-control-and-workflow-compilation.md`；
- `0008-workspace-unification-and-coding-autonomy.md`。
