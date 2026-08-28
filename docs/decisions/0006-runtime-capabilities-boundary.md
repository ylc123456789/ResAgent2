# ADR-0006：Runtime 与 Capabilities 边界

- 状态：accepted
- 日期：2026-08-28

## 背景

Phase 5/6 在 shared runtime 中逐步加入 workspace、Git、process、Artifact、仓库、环境、数据集和硬件组件。它们可以被多个 Agent 复用，但与 Agentic Loop 不在同一抽象层级，继续放入 runtime 会让运行机制和具体能力混杂。

## 决定

- `resagent2_runtime` 只回答“Agent 怎么运行”：Agentic Loop、状态、上下文、LLM、Session、Tool 协议、权限/完成检查和 finish/ask-user 控制信号。
- `resagent2_capabilities` 回答“Agent 能做什么”：文件、Git、process、Artifact、仓库、环境、数据集、硬件，以及后续论文检索等可装配能力。
- 具体 Agent 通过 Tool Profile 选择能力；依赖 capabilities 不自动授予全部能力。
- 代码依赖为 `contracts ← runtime ← capabilities ← agents`；orchestrator 只依赖 contracts 和自身 Port，composition root 把具体 Agent 注入 orchestrator。
- 当前不建立正式 Skill 框架。跨 Agent 可复用的操作流程出现前，Skill 由 Agent 的 Prompt、Tool Profile 与 CompletionCheck 表达。

## 后果

- runtime 不得 import capabilities、orchestrator 或具体 Agent。
- capabilities 不得 import orchestrator 或具体 Agent。
- ADR-0004/0005 的安全与资源语义继续有效，但其中“组件位于 runtime”的位置决定由本 ADR 取代。
- 不保留从 `resagent2_runtime` 转发 capabilities 类的兼容导入，避免形成两套公开路径。
