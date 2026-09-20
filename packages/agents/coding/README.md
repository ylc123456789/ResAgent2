# Coding Agent

`NativeCodingAgent.invoke(AgentRequest) -> AgentResult` 使用一套 CodingAction、
CODING_PROMPT 和 CodingCompletionCheck。解释、调查、修改均由 instruction 表达。

读写权限由 WorkspaceGrant 约束；只读源码仍可以通过受控输出通道提交报告。
可写不表示必须修改，无修改也可结束本次调用。

共享 capabilities 提供文件、Git、artifact 和环境工具。
[verification.py](src/resagent2_coding/verification.py) 保留 shell-free 验证命令策略：
测试进程需要执行授权、可写工作区和已认证环境。环境发生变化后，旧验证不会被
表示为覆盖当前环境；后续编辑或 Git diff 变化也会使记录失效。

完成时从实际 Git 增量生成 code_patch/code_change，从真实验证记录生成
verification_result，记录其是否覆盖当前工作区。模型不能提交伪造的执行记录。
失败仍保留真实诊断 patch。显式交付要求由 Scheduler 对登记后的 artifact 检查。
