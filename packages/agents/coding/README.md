# Coding Agent

`NativeCodingAgent.invoke(AgentRequest) -> AgentResult` 使用统一原生工具协议、
CODING_PROMPT 和 CodingCompletionCheck。解释、调查、修改均由 instruction 表达。

读写权限由 WorkspaceGrant 约束；只读源码仍可以通过受控输出通道提交报告。
可写不表示必须修改，无修改也可结束本次调用。

命令执行仅装配共享 capabilities 的 run_shell：检查、依赖安装、环境创建和正确性验证
均由模型在任务及授权范围内提交普通命令。系统不创建、绑定或认证环境；模型检查需求、
选择环境并明确使用解释器。每次 Shell 独立，activate/export/cd 不跨调用保留。
run_shell 每次审批，需要执行授权及完整可信工作区；进程管理、超时、中断与实际日志由组件负责。
代码执行成功不证明验证充分，模型根据目标、实际检查与局限报告完成情况。

delete_path 只装配到 Coding：普通授权文件或空目录可直接删除，非空目录需确认准确目标快照。
批准绑定单次操作，目标变化不能复用旧批准；删除部分内容仍使用 replace_text。

完成时从实际 Git 增量生成 code_patch/code_change。正常完成、失败和中断均通过共享组件
从原生 run_shell 回执生成 execution_record，随原 report + artifacts 交接；不再生成专用
verification_result 或环境认证状态。模型不能提交伪造的系统记录。
中断记录保留实际停止确认和已有日志，不把保留的部分文件自动登记为完成交付。
失败在剩余期限内尽力收集真实诊断 patch；期限耗尽或 Git 诊断失败时，保留原错误、Session、
调用计量与已有工件，在 error.details.diagnostic_patch_error 说明未能收集的原因，并禁止自动重试。
显式交付要求由 Scheduler 对登记后的 artifact 检查。
