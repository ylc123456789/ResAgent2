# ADR-0016：统一 Agent IO 与 Run 预算、授权

- 状态：accepted
- 日期：2026-09-22
- 对应 schema：13.0（统一 IO 自 12.0 引入）

## 背景

三个 Agent 的模式、输入和结果类型曾按业务行为分叉，预算、路径授权与命令确认也分散在不同层。用户要求每个 Agent 只有一种调用和业务模式，任务由自然语言及工件表达，所有内部预算和权限受 Run 上限约束。

## 决定

1. Scientific、Coding、Experiment 共用 `invoke(AgentRequest) -> AgentResult`。任务内容只有 instruction 与 input_artifacts，业务结果只有 report 与 artifacts；身份、预算、权限、工作区、状态和恢复字段保留结构化。可写不强制修改，分析不强制执行或生成额外文件。
2. RunBudget 只限制模型请求次数和有效执行时间。ExecutionLimits 限制累计任务数与每任务尝试数。Controller/Scheduler 向子调用传递同一持久用量及剩余期限，模型 HTTP 重试、纠错和压缩都占用同一余额；恢复不重置预算，不新增动态预算分配器。
3. Run 创建时保存工作区与操作授权。WorkspaceAccess 统一 read_paths/write_paths/denied_paths，内部仅能继承或收紧。Components 的 OperationPermissionPolicy 将授权、固定命令规则和目标检查组合为 allow/ask/deny；批准不能扩大权限。
4. 操作确认复用 question/answer 工件和当前 Session，不建独立审批服务。批准绑定具体动作、参数、上下文及身份，派发前持久消费；不同命令及同一命令再次执行都需新批准。普通授权文件/空目录可删除，非空目录通过准确目标快照确认。
5. 环境与数据集沿用已有机制。环境绑定重建及 prepare/setup 后不沿用旧认证；获准命令执行前自动核验尚未认证的绑定。核验失败不执行命令，显式 audit_env 保留为诊断工具。环境代次仍使旧代码验证过期。

## 取代关系

以下只取代旧记录中的具体规则，其他仍有效的职责和理由保留：

- ADR-0004/0008 的旧工作区 mode/allowed_paths 字段，以及按 Coding 业务模式要求必有修改与验证的协议，改为统一调用及 WorkspaceAccess。
- ADR-0005 的仅正式实验确认标志，改为 Run 确认设置与单次动作快照；环境 audit 不因此变成安全隔离。
- ADR-0009 的模型必须先单独 audit 再验证/实验的顺序，改为获准命令执行前自动核验；共享环境身份与版本约束不变。
- ADR-0011 的按业务 capability 路由和专用结果 payload，改为 coding/experiment 模块路由及统一 AgentResult。
- ADR-0013/0014 的直接资源/答案材料字段交接，改为冻结的 dataset_catalog、answer、work_feedback 等工件；资源缺失需真实补齐和原题配对原则不变。

## 边界与验证

当前仍是单 Run 串行、单执行者。宿主进程执行不是 OS 沙箱；批准消费与外部副作用不构成事务，unknown 请求不退款，旧 schema 不迁移或兼容恢复。缓存不纳入新的通用资源配额系统。

当前规范见 [CONTRACTS](../../current/CONTRACTS.md)、[CONTEXT](../../current/CONTEXT.md) 和 [ARCHITECTURE](../../current/ARCHITECTURE.md)。设计依据与分阶段测试见 [统一 IO 方案](../reviews/UNIFIED_AGENT_IO_V2_PLAN_2026-09-20.md)、[Run 控制方案](../reviews/RUN_CONTROL_SIMPLIFICATION_PLAN_2026-09-22.md)、[统一 IO 验收](../reviews/UNIFIED_AGENT_IO_V2_RETEST_REVIEW_2026-09-21.md) 和 [Run 控制修复轮复核](../reviews/RUN_CONTROL_SERVER_REVIEW_2026-09-22.md#fixed-round)。不同提交的真实流程、确定性回归与证据缺口分别记录，不宣称最终提交重跑所有科研场景。
