# ADR-0002：三个专业 Agent 共享一个 Agentic Loop

**状态**：accepted
**日期**：2026-08-26

## 背景

旧模块分别实现相似的 LLM 调用、上下文、工具循环、状态、错误和完成流程，造成重复和行为漂移。

## 决策

Scientific、Coding、Experiment Agent 使用同一个 `AgentLoop`。差异通过 `AgentDefinition` 注入：

- prompt；
- tools；
- context builder；
- action/result schema；
- permission policy；
- completion check。

共享 Loop 的固定步骤：

```text
context → LLM action → schema/permission → tool → observation → persist → completion
```

## 不采用

- 三份复制粘贴的 loop；
- 一个包含所有模块特殊分支的超级 loop；
- 只靠 prompt 约束权限和完成状态；
- 强迫 ResAgent Workflow Scheduler 使用同一自由循环。

## 后果

- 第一个 runtime 实现必须保持无具体 Agent import；
- 若某模块需要特殊流程，应先尝试扩展明确 hook/protocol；
- 只有真实差异无法表达时才允许专用 loop，并需新 ADR。
