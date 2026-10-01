# Experiment Agent

`NativeExperimentAgent.invoke(AgentRequest) -> AgentResult` 使用一套 ExperimentAction、
EXPERIMENT_PROMPT 和 ExperimentCompletionCheck。

同一入口可以分析已有结果，也可以准备环境、执行实验并整理结果。
只读分析不要求新命令或新产物；显式交付要求来自正式要求 artifact，由 Scheduler 检查。

环境按 Run/Workspace 绑定。共享 run_shell 执行 Linux Bash 脚本，替代旧 run_command，需要执行授权、
完整可读写且无用户排除路径的可信工作区。操作获准后，执行前自动核验尚未认证的绑定，
包括安装后和批准恢复后的新绑定；核验失败或 Run 期限耗尽时不执行命令。
脚本执行前使旧认证和关联验证失效，不假定脚本没有修改环境。
audit_env 可用于显式诊断，不是每次命令前必须由模型单独调用的步骤。
每次执行前匹配本次恢复的结构化批准与准确动作快照，即使 confirm_commands=False；第二条命令或同一命令再次执行均需新批准。

finish 统一提交 status、report 和 artifacts，复用 completed/failed 状态。
Agent 按目标与证据声明任务是否完成；代码检查候选文件、授权与输出事实。
历史命令失败不自动判整个任务失败，完整 execution_record 由真实观察生成并保留。
声明 failed 也须通过产物事实检查，再沿现有失败交接与依赖阻断流程返回。
模型不能伪造执行记录，科学充分性仍由 Scientific 判断。

WorkspaceBoundary 约束文件工具和操作入口，当前进程执行没有 OS 沙箱；
实验/安装命令仍可能运行项目代码。环境 audit 是执行正确性检查，不是安全隔离。
子 Agent 不直接相互调用，修复需求由 Scientific、Compiler 和 Scheduler 协调。
