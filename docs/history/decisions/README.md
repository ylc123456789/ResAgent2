# 架构决策记录

这里保存重要设计取舍及其理由，不代替 [当前架构](../../current/ARCHITECTURE.md) 和 [接口与契约](../../current/CONTRACTS.md)。

proposed / accepted / superseded / rejected 是各记录自己的决策状态；迁入 history 不等于把所有 accepted 决策作废。已接受决定需要变更时追加新 ADR 并说明取代关系，不把旧正文重写成新规则。普通实现细节不必写 ADR。

## 决策索引

- [ADR-0001：使用 Monorepo，保留逻辑模块边界](0001-monorepo-and-module-boundaries.md)
- [ADR-0002：三个专业 Agent 共享一个 Agentic Loop](0002-shared-agentic-loop.md)
- [ADR-0003：LLM 生成计划，代码确定性调度](0003-llm-planning-deterministic-scheduling.md)
- [ADR-0004：Workspace 与进程执行边界](0004-workspace-and-process-boundaries.md)
- [ADR-0005：Experiment Agent vNext 与内容寻址环境](0005-experiment-agent-and-content-addressed-env.md)
- [ADR-0006：Runtime 与 Capabilities 边界](0006-runtime-capabilities-boundary.md)
- [ADR-0007：科学判断与任务图编译分离](0007-scientific-control-and-workflow-compilation.md)
- [ADR-0008：统一工作区、Coding 自主代码细节与 Run/缓存分离](0008-workspace-unification-and-coding-autonomy.md)
- [ADR-0009：共享环境能力与 Agent 自主选型](0009-shared-environment-capability.md)
- [ADR-0010：语义草图 + 确定性物化 + 一次纠错重编译](0010-semantic-compilation-draft.md)
- [ADR-0011：Stabilization 3.0 —— 控制面、Attempt、机器语义、资源权威与契约收敛的最终边界](0011-stabilization-schema-3.md)
- [ADR-0012：最小状态恢复边界](0012-state-recovery-boundaries.md)

阶段实施与验收保存在 [reviews](../reviews/)；[DEVELOPMENT_PLAN](../DEVELOPMENT_PLAN.md) 是早期开发历程，不再用作当前入口说明。
