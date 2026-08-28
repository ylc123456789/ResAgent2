# ADR-0004：Workspace 与进程执行边界

> 部分被 [ADR-0006](0006-runtime-capabilities-boundary.md) 取代：安全规则不变，具体实现从 runtime 迁移到 capabilities。

**状态**：accepted
**日期**：2026-08-27

## 背景

Coding 和 Experiment 都需要访问文件、运行命令和收集证据。如果各模块分别用字符串路径和 shell 命令实现，会再次产生重复逻辑，也无法可靠证明权限检查发生在副作用之前。

## 决策

- runtime 以 WorkspaceGrant 构造物理 `WorkspaceBoundary`，统一处理相对路径、allowed/denied paths、resolve 和 symlink containment；
- LLM 文件 Tool 不得访问 runtime 保留的 `.git` 和 `.resagent2`；Git 只能通过只读 `GitWorkspace` 接口观察；
- `ProcessRunner` 只接受解析后的 argv 并使用 `shell=False`；
- verification command 必须由 ModuleTaskRequest 预先声明，LLM 不能提交新的任意命令；
- command logs 由 runtime 写入保留目录，领域 finalizer 只消费结构化退出状态；
- Coding finalizer 从 Git 和进程结果决定完成，不从 prompt、summary 或 LLM result 推断事实。

## 不采用

- 依靠 system prompt 禁止越权；
- 将任意 shell 暴露为通用 Tool；
- 在 Coding 与 Experiment 内各复制路径和进程安全逻辑；
- 让 runtime 决定代码修改是否满足领域目标。

## 后果

- Phase 5 不支持管道、重定向、命令替换或复合 verification command；需要时拆成多个声明命令；
- 无 OS sandbox 时，受信任 verification command 内部程序仍可能产生副作用，因此 read-only profile 完全不提供进程 Tool；
- 支持脏 Git workspace 需要额外 baseline 契约，不能通过猜测变化归属实现。
