# agents

三个专业 Agent 使用相同 runtime/AgentLoop，但拥有独立职责、工具集合、权限、状态和结果模型。

```text
scientific = 科学顾问
coding     = 程序员
experiment = 实验员/操作员
```

子 Agent 之间禁止直接调用。Scientific 通过 ScientificTurnResult 提出 WorkRequestDraft；Controller 接收后交 Compiler 编译，再由 Scheduler 执行。Coding/Experiment 通过 ModuleResult 返回任务结果或问题，不自行创建下一轮研究任务。

当前三个原生 Agent 均已实现；模块输入、返回分支及替代实现要求见 [模块接口与契约](../../docs/current/CONTRACTS.md)。
