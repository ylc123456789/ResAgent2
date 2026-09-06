# Experiment Agent

实验员/操作员。

输入：实验目标、WorkspaceGrant、环境/资源要求。`instructions` 承载实验及证据语义；`expected_metrics` / `expected_artifacts` 只用于可信调用方已知的精确 JSON 键和相对路径。LLMCompiler 不猜这些名称，需求留在 instructions。
输出：ExperimentResult、命令证据、文件派生指标、环境和 repo identity；summary 是说明，不代替证据。

复用 runtime 的 permission、context 和 AgentLoop，并装配 capabilities 的 filesystem、process、Artifact 与 provisioning 组件（`RepoMaterializer`/`EnvironmentManager`/`HardwareAudit`）。实验策略和 experiment evidence finalizer 属于本模块。

原生实现：

- 用 RepoMaterializer 准备仓库，环境按 run_id + workspace_id 绑定/复用；Python 硬约束来自统一请求；
- 通过共享 workspace_context 投影实时环境认证和文件/工件工作集；恢复绑定需重新 audit，不能信旧 Session 中的认证记录；
- `run_command` 只接受 shell-free 命令，实验命令在 `audit_env` 通过前被拒绝（命令分类不依赖 LLM stage）；
- `confirm_before_experiment` 复用 ask_user 机制；
- finalizer 要求成功实验命令和至少一份本 Attempt 新建/变化的真实 evidence；即使未预先指定精确名称也不能空交付。预存未变文件不登记为本 Attempt Artifact；已知精确要求部分缺失时降级 completed_with_warnings；
- 原始 stdout/stderr 由 run_command 保存。根据 metrics 生成的说明报告不是独立执行日志，不能冒充原始输出。

模块不接受 LLM 自报的 metrics、evidence 路径或命令分类作为最终事实。

## 已知限制（非安全保证）

- `run_command` 在无 OS 沙箱的真实子进程里运行，WorkspaceBoundary 只约束文件 Tool、不约束子进程；
- `audit_env` 是实验流程正确性检查，不是安全隔离；`confirm_before_experiment` 只在正式 experiment command 执行前询问用户；setup 命令仍可能执行构建代码或产生副作用；setup/experiment 是工作流分类，不是安全分类。

入口脚本真实失败时可通过确定性 failure 出口返回 failed + 原始诊断，再由 Scientific 决定是否请求修复，Compiler/Scheduler 编译执行下一轮。禁止直接调用 Coding Agent。
