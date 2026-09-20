# agents

三个专业 Agent 共同使用 `invoke(AgentRequest) -> AgentResult` 和 runtime/AgentLoop。
任务语义只有 `instruction + input_artifacts`，业务结果只有 `report + artifacts`。
每个 Agent 只有一套提示词、动作 schema 和完成协议；授权控制具体操作，不选择业务模式。

- Scientific：阅读证据、检索文献、形成科研判断，必要时提问或请求执行工作。
- Coding：理解、解释和修改代码，按需执行验证。
- Experiment：分析已有结果、准备环境并执行实验。

所有 Agent 使用 `finish(report, artifacts)`。问题和工作请求由工具产生内容，Runtime
将内容封装为候选 artifact，控制信号只引用该候选。Controller/Scheduler 登记后，
下游只接收正式 ArtifactRef。原生 Agent 不相互调用。

Scientific 的 Session 属于 Run；Coding/Experiment 的 Session 属于 Task/Attempt。
恢复仍走 invoke，回答和工作反馈从系统指定的 artifact 投影到必需上下文。
