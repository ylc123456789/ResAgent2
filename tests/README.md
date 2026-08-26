# Tests

测试按系统边界组织，而不是按实现文件堆放。

计划结构：

```text
tests/
  contracts/       schema、ID、状态和字段不变量
  runtime/         AgentLoop、Tool、权限、Context、持久化
  orchestrator/    DAG、Task/Attempt、retry、Ask User、finish gate
  agents/          三个 Agent 的领域行为
  integration/     Port/Adapter 和 Artifact 传递
  e2e/             黄金科研闭环
```

测试规则：

- 新行为先有失败测试；
- 普通单测不依赖真实 LLM、网络、GPU 或服务器；
- mock LLM 返回固定类型化动作；
- 真实 E2E 记录 commit、配置、命令和 Artifact；
- 文档完成标准必须能映射到测试；
- 安全、状态和完成条件必须由确定性测试覆盖，不能只测试 prompt 文本。
