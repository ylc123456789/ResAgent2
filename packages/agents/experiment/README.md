# Experiment Agent

实验员/操作员。

输入：实验目标、WorkspaceGrant、repo source、环境/资源要求、expected metrics/artifacts。
输出：ExperimentResult、命令证据、参数、指标、环境和 repo identity。

复用 runtime 的 filesystem、process、permission、context 和 AgentLoop；provisioning 组件（`RepoMaterializer`/`EnvironmentManager`/`DatasetCache`/`HardwareAudit`）、环境管理、GPU、dataset cache 和 experiment evidence finalizer 属于本模块。

本模块是第二个完成重写的专业 Agent。Phase 6 的原生实现：

- 自己克隆/复制/就地绑定仓库，按内容寻址创建/复用 conda env（`env_id = resenv_<slug>_<sha256(repo identity + env spec)[:12]>`）；`RepoMaterializer` 用运行时 metadata 文件校验复用来源，不会静默复用其它仓库；
- `run_command` 只接受 shell-free 命令，实验命令在 `audit_env` 通过前被拒绝（命令分类不依赖 LLM stage）；
- `confirm_before_experiment` 复用 ask_user 机制；
- finalizer 要求至少一次 experiment command 成功，且 evidence 文件相对本 Attempt 基线新建或内容变化；预存未变的文件不登记为本 Attempt Artifact；缺 metrics/artifacts 降级 completed_with_warnings，WarningRecord 记录 `[NOT MET]`。

模块不接受 LLM 自报的 metrics、evidence 路径或命令分类作为最终事实。

## 已知限制（非安全保证）

- `run_command` 在无 OS 沙箱的真实子进程里运行，WorkspaceBoundary 只约束文件 Tool、不约束子进程；
- `audit_env` 是实验流程正确性检查，不是安全隔离；`confirm_before_experiment` 只在正式 experiment command 执行前询问用户；setup 命令仍可能执行构建代码或产生副作用；setup/experiment 是工作流分类，不是安全分类。

遇到代码问题时返回 blocked + structured issue，由 orchestrator 创建 Coding Task；禁止直接调用 Coding Agent。
