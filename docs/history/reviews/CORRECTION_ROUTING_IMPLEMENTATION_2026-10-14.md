# schema 27 纠正链路、纯路由与打断：实施与离线验证

日期：2026-10-14。用户已确认方案并授权在原分支实施。

基线：`fix/native-agent-receipts` @ `f5fb215c48a0a7a5090814cd497344b9fe8f891d`。本记录与实现、测试及当前文档一起纳入本轮开发方冻结提交；完整 SHA 由 Git 提交身份给出，未推送。当前 schema 为 **27.0**，拒绝旧 Run/Session；旧记录不迁移、不删除。

## 已实施的行为

- Scientific 提供明确 Task 列表；Compiler 的 LLM 只有 `route_tasks`，只匹配现有 Agent。固定代码校验并原样传递 Task 指令、共同目标/约束、依赖及材料绑定。
- 统一 `ask_user` 问直接上级，根 Scientific 才问真实用户。父方下级问题/自身答复属于第三部分；子方上级答复属于第二部分。问题身份绑定准确 Task、Attempt、Session，回复保持原子会话；用户答案/补充可按真实记录 ID 原样转交。
- 第二部分只投影一份完整按时间排列的业务记录，保留原时间、来源和身份。第三部分是唯一可压缩部分，未答下级问题仍有当前投影；第四部分保持完整授权工件目录，不收问答或控制记录。
- 普通输入立即持久化，保留当前模型回复及已采纳批次，下一次模型判断前唤醒 Scientific。多任务澄清可先 `resume_work=false`，最后再恢复；普通输入不自动回答待答问题。
- 显式打断取消本地模型等待，丢弃未采纳迟到回复，停止受管进程组/子进程，确认停止后才 paused。不能确认则明确失败。已完成事实、真实回执、冻结工件和文件保留，不自动回滚。
- 活动 WorkRequest 打断后结束未完成 Task/Attempt，旧子 Session 不恢复；继续恢复原 Scientific，由它按需新委派。普通业务澄清继续原 Session/Attempt。
- 每个原生调用保持一个回执；已 accepted 的 ask/work 后续状态用第三部分关联通知，不伪造第二个 tool 结果。权限批准仍绑定准确操作，不进入第二/第四部分，拒绝说明仍交回原工具调用。
- CLI 支持运行中普通输入、`/interrupt`、真实 POSIX Esc、Ctrl-C，以及一次性 `input/interrupt/resume`。只有 Controller 写 Run/Session，CLI 发布不可变小记录。

## 收尾复核补齐的边界

多任务补充先全部传递再恢复；父进程先退出但独立子进程仍持有输出管道时，按本次执行的进程身份与独有输出管道识别并停止，清理失败不掩盖原取消事实。

完成检查期间收到输入时，保留已冻结的候选结果并恢复 Scientific 重新判断。输入发布和最终完成提交共用发布边界，终态之后的提交明确拒绝。每次完成候选按其不可变 Session 快照区分登记操作；旧候选保留为历史，最后接受的一份意见形成唯一最终报告。

## 实际验证

| 检查 | 结果 |
| --- | --- |
| `python -m pytest tests apps/cli/tests -q --tb=short` | **2237 passed, 1 skipped**，88.04s |
| `python -m e2e.mock_e2e` | `run_golden completed`，8 工件；Coding/Experiment 各 1 completed Attempt |
| `git diff --check` | clean |
| `python -m pip check` | rc=1：本地既有 `pdfminer-six 20260107` 缺少 `cryptography`；未把本地写为 clean |

测试包含真实本地 HTTP 取消、迟到回复不执行、进程树/脱离进程组子进程/父先退出的输出管道、文件保留、环境 readiness 失效、原生回执配对、有序来源、多任务澄清、真实 PTY Esc、跨进程 owner 排他及完成提交时的收件竞态。

本次未连接服务器、调用真实模型或运行 L3。离线通过证明已验证的机制，不能据此推断研究质量改善；真实模型的需求理解、纠正效果和科研结果仍按验收计划另测。

## 文档

当前文档四份均已同步：[架构](../../current/ARCHITECTURE.md)、[上下文](../../current/CONTEXT.md)、[契约](../../current/CONTRACTS.md)、[设计原则](../../current/DESIGN_PRINCIPLES.md)。设计取舍见 [ADR-0029](../decisions/0029-upstream-correction-routing-interruption.md)，后续测试见 [本轮计划](CORRECTION_ROUTING_TEST_PLAN_2026-10-14.md)，确认方案见 [完整方案](CORRECTION_AND_TASK_ROUTING_CHANGE_PROPOSAL_2026-10-13.md)。
