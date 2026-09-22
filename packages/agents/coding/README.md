# Coding Agent

`NativeCodingAgent.invoke(AgentRequest) -> AgentResult` 使用一套 CodingAction、
CODING_PROMPT 和 CodingCompletionCheck。解释、调查、修改均由 instruction 表达。

读写权限由 WorkspaceGrant 约束；只读源码仍可以通过受控输出通道提交报告。
可写不表示必须修改，无修改也可结束本次调用。

共享 capabilities 提供文件、Git、artifact 和环境工具。
[verification.py](src/resagent2_coding/verification.py) 保留 shell-free 验证命令策略：
测试进程需要执行授权、完整可读写且无用户排除路径的可信工作区，以及已准备的环境。
操作获准后，run_verification 在执行前自动核验尚未认证的环境；批准恢复不会信任旧认证，
也不要求模型先单独 audit_env。核验失败或 Run 期限耗尽时不执行验证命令。
环境发生变化后，旧验证不会被表示为覆盖当前环境；后续编辑或 Git diff 变化也会使记录失效。

delete_path 只装配到 Coding：普通授权文件或空目录可直接删除，非空目录需确认准确目标快照。
批准绑定单次操作，目标变化不能复用旧批准；删除部分内容仍使用 replace_text。

完成时从实际 Git 增量生成 code_patch/code_change，从真实验证记录生成
verification_result，记录其是否覆盖当前工作区。模型不能提交伪造的执行记录。
失败仍保留真实诊断 patch。显式交付要求由 Scheduler 对登记后的 artifact 检查。
