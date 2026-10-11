# contracts

跨模块稳定数据契约，当前 wire schema **25.0**；旧 Run/Session 明确拒绝恢复，不迁移、不留兼容别名。

三个 Agent 共用 AgentRequest → AgentResult：一个自然语言 instruction/report，加身份、权限、预算、输入/输出工件及直接控制字段。业务问答累计保存在 user_answers，当前恢复使用 resume_answers/control_ack。WorkResult 只组织任务状态与实际 Ref，不拼接总体报告。

Artifact ID 由唯一 new_artifact_id() 分配 UUID，sha256 校验冻结字节；目录从授权和实际来源投影。问题、答案、搜索结果及目录不再作为控制工件保存。完整字段和不变量见 [CONTRACTS](../../docs/current/CONTRACTS.md)。本包不依赖其他项目包，不执行模型、IO、命令或调度。
