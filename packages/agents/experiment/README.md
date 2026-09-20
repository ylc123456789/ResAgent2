# Experiment Agent

`NativeExperimentAgent.invoke(AgentRequest) -> AgentResult` 使用一套 ExperimentAction、
EXPERIMENT_PROMPT 和 ExperimentCompletionCheck。

同一入口可以分析已有结果，也可以准备环境、执行实验并整理结果。
只读分析不要求新命令或新产物；显式交付要求来自正式要求 artifact，由 Scheduler 检查。

环境按 Run/Workspace 绑定，准备或安装后需要重新 audit。
run_command 只接受 shell-free 实验命令，在执行入口检查进程授权、可写工作区、
环境认证和系统提供的实验确认状态。确认不能通过答案文字自行推断。

finish 只有 report 和 artifacts。真实命令观察生成 execution_record；
最近一次实际命令失败时，完成检查返回含原始诊断的失败。
已有失败被随后成功命令解决后，不继续沿用旧失败。模型不能伪造执行记录。

WorkspaceBoundary 约束文件工具和操作入口，当前进程执行没有 OS 沙箱；
实验/安装命令仍可能运行项目代码。环境 audit 是执行正确性检查，不是安全隔离。
子 Agent 不直接相互调用，修复需求由 Scientific、Compiler 和 Scheduler 协调。
