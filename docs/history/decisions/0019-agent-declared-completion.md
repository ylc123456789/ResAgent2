# ADR-0019：复用既有状态表达任务完成

- 状态：accepted
- 日期：2026-09-27
- 对应 schema：18.0

统一 IO 保留了 AgentResult.status，但内部 finish 移除了模型声明入口。随后根据命令退出码及同 argv 重跑推断任务失败，混淆单次操作事实与任务语义。

恢复同一个 finish 的 status 控制字段，只接受既有 completed/failed，业务说明仍只有 report 和 artifacts。Coding/Experiment 判断目标完成程度；CompletionCheck 检查候选事实，AgentLoop 接受后映射已有终态。模型声明失败使用 AGENT_REPORTED_FAILURE，与系统观察的技术故障区分，沿原调度、依赖阻断及科研反馈流程返回。

共享文件、名称、权限、归属、hash 和明确产物校验保留。Scientific 只接受完成的合法意见，证据不足用 inconclusive、请求工作或提问，不允许 failed 绕过 Run 要求。没有新增 Agent、模式、验证器或控制循环。

移除 TaskAcceptanceSpec.require_successful_execution，而非静默改成任意命令成功；客观名称、类型、路径、数值字段要求保留。Coding 分开验证时效与通过结果。公共契约升级，旧记录原样保留且不迁移恢复。

本决定仅替代此前命令规则对任务完成的推断；ADR-0016 的统一 IO 和状态权威、已完成的 validation 事实检查及暂缓的前置 validation 决策继续有效。
