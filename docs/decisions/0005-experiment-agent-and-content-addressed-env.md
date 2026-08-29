# ADR-0005：Experiment Agent vNext 与内容寻址环境

> 部分被 [ADR-0006](0006-runtime-capabilities-boundary.md) 取代：provisioning 语义不变，具体组件从 runtime 迁移到 capabilities。
> 部分被 [ADR-0009](0009-shared-environment-capability.md) 取代：环境从「内容寻址复用」改为「run_id + workspace_id 绑定 + Agent 自主选型」。

**状态**：accepted
**日期**：2026-08-27

## 背景

Phase 6 用原生 Experiment Agent 替换 legacy reproagent adapter。Experiment 需要自己克隆仓库、创建虚拟环境、共享数据集缓存，这些能力被抽成 runtime 组件供 Coding 与 Experiment 共享。同时给 `ExperimentRunInput` 增加可选字段，触发 wire schema 从 1.0 升到 1.1。

## 决策

- 仓库、环境、数据集、硬件四个 provisioning 组件放 runtime：`RepoMaterializer` / `EnvironmentManager` / `DatasetCache` / `HardwareAudit`。本阶段只有 Experiment 一个真实使用者，Coding 在 Phase 8 的内容寻址环境复用成为第二个使用者；
- 环境按内容寻址复用：`env_id = resenv_<slug>_<sha256(repo identity + env spec)[:12]>`，只做简单核心（无 manifest、锁或 drift 检测），drift 检测留待后续；
- repo identity 用（repo source + commit hash），不依赖 basename；
- 给已冻结的 `ExperimentRunInput` 加 5 个可选字段（repo source 三个 + python_version + confirm_before_experiment），wire schema 升 1.1；
- 删除 LegacyExperimentAdapter。

## 不采用

- 复用 reproagent 的完整 ENVIRONMENT_*_V1 manifest/锁/drift 检测机制（过度设计，本阶段无两个真实使用者）；
- 把 provisioning 组件留在 experiment 包内（违背"尽可能复用 runtime 组件"的方向）。

## 后果

- schema 1.1 从本阶段起冻结；后续再加字段必须发布 1.2 并提供迁移说明；
- 环境复用只在内容不变时生效；内容变了会新建环境（无 drift 检测意味着旧环境不会被标记漂移，只会被绕过）；
- Coding 尚未使用 provisioning 组件，"两个真实使用者"在本阶段靠 Phase 8 计划满足；
- certification/confirmation 不是安全沙箱：`audit_env` 是实验流程正确性检查，`confirm_before_experiment` 只在正式 experiment command 前询问用户，setup 命令仍可能执行构建代码或产生副作用，setup/experiment 是工作流分类而非安全分类。
