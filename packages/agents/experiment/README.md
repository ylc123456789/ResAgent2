# Experiment Agent

实验员/操作员。

输入：实验目标、WorkspaceGrant、repo source、环境/资源要求、expected metrics/artifacts。
输出：ExperimentResult、命令证据、参数、指标、环境和 repo identity。

复用 runtime 的 filesystem、process、permission、context 和 AgentLoop；provisioning 组件（`RepoMaterializer`/`EnvironmentManager`/`DatasetCache`/`HardwareAudit`）、环境管理、GPU、dataset cache 和 experiment evidence finalizer 属于本模块。

本模块是第二个完成重写的专业 Agent。Phase 6 的原生实现：

- 自己克隆/复制/就地绑定仓库，按内容寻址创建/复用 conda env（`env_id = resenv_<slug>_<sha256(repo identity + env spec)[:12]>`）；
- `run_command` 只接受 shell-free 命令，实验命令在 `audit_env` 通过前被拒绝（命令分类不依赖 LLM stage）；
- `confirm_before_experiment` 复用 ask_user 机制；
- finalizer 校验 expected_metrics/expected_artifacts，缺失则降级 completed_with_warnings，WarningRecord 记录 `[NOT MET]`。

模块不接受 LLM 自报的 metrics、evidence 路径或命令分类作为最终事实。

遇到代码问题时返回 blocked + structured issue，由 orchestrator 创建 Coding Task；禁止直接调用 Coding Agent。
