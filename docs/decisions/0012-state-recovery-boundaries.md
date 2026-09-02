# ADR-0012：最小状态恢复边界

**状态**：accepted

**日期**：2026-09-02

## 背景

Stabilization 3.0 已统一正常路径的 Run、Task、Attempt 和 Session 语义，但审查发现多个进程中断窗口仍会留下无法解释的状态：Task/Attempt 可能永久停在 `running`；Scientific 首次 Session 可能已经落盘但尚未绑定到 Run；完成 gate 只拒绝 running Task。

问题不是缺少更多状态枚举，而是没有明确规定「running 的含义」和「恢复由谁执行」。

## 决策

1. `ResearchRun` 由 orchestrator 拥有；`WorkflowTask` 表示一项工作义务；`Attempt` 表示一次真实模块调用；Session 由 runtime/Agent 拥有，Run 只保存 `SessionRef`；Artifact/Workspace 记录外部事实。
2. `RUNNING` 是已持久化的执行意图，不等同于活进程。在当前单进程同步执行模型中，新 Controller 入口看到遗留 running Attempt 时，必须将其结算为 `FAILED + ErrorCode.INTERRUPTED + retryable=True`，保留 Attempt 历史；Task 仅在原有 retry budget 允许时回到 `PENDING`。
3. 不增加 `TaskStatus.interrupted` 或第二套恢复状态机；`interrupted` 是 ErrorCode，不是新的生命周期状态。
4. `ResearchController.run_until_stable()` 是唯一恢复入口。它先整理遗留 Attempt，再继续 WorkRequest/Scientific/Task 调度。Scheduler 不猜测旧进程是否存活。
5. Controller 在第一次 Scientific turn 前持久化确定性的 `session_scientific_<run_id>` 引用。runtime 可重新打开身份匹配的 `ACTIVE` checkpoint；`PAUSED` 仍表示正常 ask-user/request-work 恢复；`COMPLETED`/`FAILED` Session 不可恢复。
6. Scientific completion gate 必须拒绝所有非终态 Task：`PENDING`、`RUNNING`、`NEEDS_USER_INPUT`。`FAILED`/`BLOCKED` 由既有 acknowledged-task 规则处理。
7. `ModuleStatus.REQUEST_WORK` 仅属于 ScientificPort；Task Module 返回它是 contract error。Task `NEEDS_USER_INPUT` 必须同时提供 `QuestionDraft` 与 `SessionStatus.PAUSED` 的 SessionRef。
8. 不引入事件溯源、Temporal、分布式锁、worker lease、心跳或通用状态机框架。它们解决的是当前系统尚未具备的分布式执行问题。

## 恢复顺序

```text
load ResearchRun
  → close stale running Attempt as interrupted
  → persist corrected Run snapshot
  → reopen the bound Scientific checkpoint when needed
  → recompute ready Tasks / active WorkRequest
  → execute only the next legal transition
```

该策略是至少一次执行语义：文件、命令和 LLM 调用无法跨进程做到全局 exactly-once。Artifact 的内容寻址、Attempt baseline 与 retry history 使重复副作用可见、可审计；系统不得通过静默重置伪装为 exactly-once。

## 验收

- running Attempt 的故障注入恢复保留第一次 Attempt，并受正常 retry budget 限制；
- 首次 Scientific Session 在 Controller 尚未收到结果时崩溃，重启后复用同一 deterministic session id；
- 已耗尽 Run LLM 预算时不得持久化新的 running Attempt；
- Task 的 `request_work`、无 Session 的 `needs_user_input` 均被拒绝；
- 任一非终态 Task 存在时，final completion 被拒绝。

