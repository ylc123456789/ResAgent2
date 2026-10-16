# Experiment Agent

`NativeExperimentAgent.invoke(AgentRequest) -> AgentResult` 使用统一原生工具协议、
EXPERIMENT_PROMPT 和 ExperimentCompletionCheck。

同一入口可以分析已有结果、准备环境、执行实验并整理结果。只读分析不要求新命令或
新产物；显式交付要求来自正式任务要求，由 Scheduler 检查。

命令执行只装配共享 run_shell，执行 Linux Bash 脚本。环境发现、选择、复用、创建和
依赖安装交给模型，没有专用环境管理器或强制绑定。模型遵守版本与授权要求，明确使用
目标解释器，或在同一脚本中激活并执行；activate/export/cd 不跨调用保留。
run_shell 需要执行授权和完整可信工作区。每次执行前匹配本次恢复的结构化批准与准确动作
快照，即使 confirm_commands=False；第二条命令或同一命令再次执行均需新批准。

finish 统一提交 status、report 和 artifacts。Agent 按目标与证据声明任务是否完成；代码
检查候选文件、授权与输出事实。历史命令失败不自动判整个任务失败，退出零也不证明实验
或结论充分。Coding/Experiment 共用组件从原生工具回执按实际顺序生成 execution_record，
正常、失败和中断沿同一 report + artifacts 交接。未执行的拒绝回执不冒充执行事实，
中断文件保留不证明成果有效。模型不能伪造系统执行记录，科学充分性仍由 Scientific 判断。

WorkspaceBoundary 约束文件工具和操作入口，当前进程执行没有 OS 沙箱；环境隔离依赖，
不构成文件、网络或进程安全隔离。子 Agent 不直接相互调用，代码修复需求由 Scientific、
Compiler 和 Scheduler 协调。
