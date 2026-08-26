# ADR-0003：LLM 生成计划，代码确定性调度

**状态**：accepted
**日期**：2026-08-26

## 背景

科研计划无法完全预先写死，需要 LLM 根据目标和新证据生成、修订任务图。但把每一步状态转换也交给 LLM，会导致依赖、失败、预算、Artifact 和完成状态不可预测。

## 决策

Scientific Agent 输出 `WorkflowProposal` 或 `WorkflowPatch`。ResAgent 使用代码完成：

- schema 校验；
- DAG 校验；
- capability 路由；
- ready Task 计算；
- Attempt 生命周期；
- retry/blocked/user-input 状态；
- Artifact 登记；
- finish gate。

LLM 不直接修改运行状态。

## 动态性

工作流可以在运行时修订，但必须经过显式事件和 WorkflowPatch validator。普通调度不需要每步请求 LLM 决定“下一 Task 是谁”。

## 后果

- 同一持久化 state 必须得到相同 ready Task 集合；
- 科学策略仍然灵活；
- 安全和执行语义可确定性测试；
- 需要同时维护 Proposal schema 和运行时 Task state，但二者职责明确，禁止再增加第三套顶层任务模型。
