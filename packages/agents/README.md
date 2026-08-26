# agents

三个专业 Agent 使用相同 runtime/AgentLoop，但拥有独立职责、工具集合、权限、状态和结果模型。

```text
scientific = 科学顾问
coding     = 程序员
experiment = 实验员/操作员
```

子 Agent 之间禁止直接调用。跨模块需求通过 ModuleResult 返回给 orchestrator，由 Workflow 创建或调度新的 Task。
