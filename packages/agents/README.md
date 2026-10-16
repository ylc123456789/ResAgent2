# agents

三个专业 Agent 共同使用 `invoke(AgentRequest) -> AgentResult` 和 runtime/AgentLoop。
任务语义只有 `instruction + input_artifacts`，业务结果只有 `report + artifacts`。
每个 Agent 只有一套提示词、动作 schema 和完成协议；授权控制具体操作，不选择业务模式。

- Scientific：阅读证据、检索文献、形成科研判断，必要时提问或请求执行工作。
- Coding：理解、解释和修改代码，按需执行验证。
- Experiment：分析已有结果、准备环境并执行实验。

所有 Agent 使用 `finish(report, artifacts)`。Scientific 使用 request_work 提出明确任务，
Compiler 仅匹配 Agent；Controller/Scheduler 处理控制、派发和工件登记。ask_user 问答不产生
工件，按直接上下级路由。原生 Agent 不相互调用。

Coding/Experiment 命令执行只使用 run_shell。环境发现、选择、创建和依赖安装由模型按
任务及授权自行完成，没有固定环境绑定或认证；模型明确使用目标解释器，每次 Shell 独立。
固定组件负责进程、日志、超时、中断与执行事实，公共 AgentResult 交接 execution_record。

Scientific 的 Session 属于 Run；Coding/Experiment 的 Session 属于 Task/Attempt。
恢复仍走 invoke，业务回答按来源进入第二部分；工作反馈进入第三部分通知和第四部分完整工件目录。操作批准由待执行动作及权限策略处理，不累积为任务需求。
