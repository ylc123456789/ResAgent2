# Experiment Agent

实验员/操作员。

输入：实验目标、WorkspaceGrant、环境/资源要求、expected metrics/artifacts。
输出：ExperimentResult、命令证据、参数、指标、环境和 repo identity。

复用 Coding 阶段已经验证的 filesystem、process、Git 和 permission 机制。环境管理、GPU、dataset cache 和 experiment evidence finalizer 属于本模块。

遇到代码问题时返回 blocked + structured issue，由 orchestrator 创建 Coding Task；禁止直接调用 Coding Agent。
