# schema 27 运行中纠正与路由验收计划

基线分支 `fix/native-agent-receipts`，旧提交 f5fb215；本次根据用户确认的方案实施，不恢复旧 schema 26 Run。离线通过只证明机制，不证明模型必然理解含糊需求或研究质量提高。

## 必须保持的行为

1. Scientific 给明确 Tasks；Compiler 只用唯一 route_tasks 工具匹配 Agent，原指令/共同约束逐字符保留；无旧结构兼容路径。
2. 子方统一 ask_user 问直接父方，根才问用户；父方问题通知只在第三部分，子方上级答复只在第二部分，权限批准独立。
3. 普通输入不取消本次模型回复或已采纳批次；下次模型判断前由父方处理，继续原子会话，不等完整 WorkRequest。
4. Esc/Ctrl-C/interrupt 停止模型本地请求或受管工具及子进程；迟到回复不派发，停止失败不得显示成功暂停。真实文件与完成结果保留。
5. 打断旧 WorkRequest 只取消未完成项，旧子 Session 终止；恢复原 Scientific，由它决定重新委派。问答/批准过期且不能串线。
6. 每次原生调用恰一回执；已 accepted 的请求使用链接原 ID 的运行时通知；问答、控制记录不登记工件。
7. 第二部分按原事件时间排列，源字句、身份、作用域及来源保持；转发旧用户记录不成为新用户指令。未答子问题在压缩后仍可见。
8. 单写入者 owner lock，CLI 仅发布小记录，最终调用附近的新输入先处理再完成。普通输入不是 pending 问题答案。
9. 一条补充影响多任务时，父方能先逐一澄清再恢复；所有相关任务先收到补充，原 Session/Attempt 保持。

## 离线检查

`python -m pytest tests apps/cli/tests -q`、`python -m e2e.mock_e2e`、`python -m pip check`、`git diff --check`。

针对性测试包含真实 HTTP 本地取消、迟到模型回复、进程组/脱离进程组子进程、文件保留、readiness 失效、真实 PTY Esc、跨进程 owner lock、子问父答、同 Attempt 恢复、最终回复收件竞态。报告以实际命令输出为准。

## 真实模型补验

先短任务：不引导模型时自行澄清；子问题由父方解答与升级；普通补充改变后续工作；训练期间显式打断后不遗留进程、文件留存；重新委派能准确复用保留结果。保存 full trace 的 messages/tools、请求账和 Session，并逐项检查来源与 call_id。

L3 需另按研究规程冻结方法、拆分、checkpoint 和唯一 test 访问，独立复算/前向重放后才评分。这里不因机制回归通过推断研究效果。


实际实施与离线结果见 [2026-10-14 验证记录](CORRECTION_ROUTING_IMPLEMENTATION_2026-10-14.md)。

服务器具体场景、命令、证据与独立 L3 验收见 [服务器执行计划](CORRECTION_ROUTING_SERVER_ACCEPTANCE_PLAN_2026-10-14.md)。
