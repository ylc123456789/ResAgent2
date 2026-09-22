# Experiment Agent

`NativeExperimentAgent.invoke(AgentRequest) -> AgentResult` 使用一套 ExperimentAction、
EXPERIMENT_PROMPT 和 ExperimentCompletionCheck。

同一入口可以分析已有结果，也可以准备环境、执行实验并整理结果。
只读分析不要求新命令或新产物；显式交付要求来自正式要求 artifact，由 Scheduler 检查。

环境按 Run/Workspace 绑定。run_command 只接受 shell-free 实验命令，需要执行授权、
完整可读写且无用户排除路径的可信工作区。操作获准后，执行前自动核验尚未认证的绑定，
包括安装后和批准恢复后的新绑定；核验失败或 Run 期限耗尽时不执行命令。
audit_env 可用于显式诊断，不是每次命令前必须由模型单独调用的步骤。
需要确认时匹配本次恢复的结构化答案与准确动作快照；第二条命令或同一命令再次执行均需新批准。

finish 只有 report 和 artifacts。真实命令观察生成 execution_record；
按解析后的 argv 保留每条命令的最新结果，其中仍有失败时，完成检查返回含原始诊断的失败。
只有同一命令成功重跑才能解决其旧失败，随后成功的诊断命令不能掩盖失败；完整执行记录始终保留。
模型不能伪造执行记录。

WorkspaceBoundary 约束文件工具和操作入口，当前进程执行没有 OS 沙箱；
实验/安装命令仍可能运行项目代码。环境 audit 是执行正确性检查，不是安全隔离。
子 Agent 不直接相互调用，修复需求由 Scientific、Compiler 和 Scheduler 协调。
