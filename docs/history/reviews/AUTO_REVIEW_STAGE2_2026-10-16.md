# 第二阶段：自动准确操作审核，本地实施与验收

日期：2026-10-16。分支：`feat/unified-run-shell`。阶段一基线：`7c6c5c05069821ab12bca9e53df2442d57a9f2b6`。公共 Run/Session schema：29.0。本记录只描述本地实现和离线验证，不是服务器或真实模型验收报告。

## 实现

- `RunPermissions.approval_mode` 为 manual（默认）或 auto_review；CLI `--approval-mode` 持久保存并向执行 Agent 继承，移除已失效的 confirm_commands 字段与旗标，不保留兼容别名。
- Capabilities 仍只有 run_shell 执行命令。OperationPermissionPolicy 保持确定性；Runtime 只对实际派发前的 ask 调用审核接口，批次预检和人工恢复不重复审核。
- Components 的 LLMPermissionReviewer 复用 PromptLLMClient 和原生单输出 review_operation，无执行/读取/网络/委派工具，无独立 Session。结果仅 allow/ask 和非空原因；每次最多一次模型尝试，计入同一 Run 总用量和期限。
- 输入分别保留准确操作、当前任务及上级对话、权威 Run 的根用户目标/假设/背景/限制/精确交付要求和有序业务修订、历史真实人工批准/拒绝及原说明。业务要求与历史操作决定分区，父解释不冒充用户，旧批准不复用、新调用不消除拒绝原因。
- started 与审核结果先写原 Session 事件，再标记原调用派发。唯一工具回执引用同一自动审核来源/原因/事件序号；不生成 RecordedAnswer、审批工件或第二份授权账本。自动允许后重验原硬授权和上下文，实际执行使用原 prepared 快照；漂移则未执行。
- 无审核器、无效结果、容量/供应商异常转准确人工批准。预算耗尽、期限、取消、权威用量保存失败停止，不转成可绕过的人工流程。异常提示只含故障类型，不复制供应商可能携带敏感信息的正文。
- 同时修复派发后未知结果的记录条件：是否派发由 executing_call_id 决定，不能要求存在 user_comment；自动审核没有人工说明时，派发后截止异常仍保存 unknown，不误写 not_executed、不重放。

架构边界保持：Runtime 定义接口与运行机制，Components 实现模型审阅，Capabilities 提供工具/确定性权限适配，CLI 组合根读取权威根记录和注入实现，Orchestrator 持有 Run 用量。四部分上下文规则继续沿用，操作审核仅进入第三部分及原始审计。

## 验证结果

权威代码和环境：WSL Ubuntu-D `/home/cyl/ResAgent2`，`/home/cyl/miniconda3/envs/ResAgent2/bin/python`。证据目录：`E:\dshWorkSpace\implementation\auto-review-stage2-20261016\checks`。

| 检查 | 结果与范围 |
| --- | --- |
| 全量 `pytest tests apps/cli/tests` | `full-02`：2216 passed，1 skipped，rc=0 |
| 原生权限/恢复定向 | `targeted-02`：133 passed，包含两执行 Agent 的真实 Bash marker 文件、每命令独立审核、唯一回执和同一预算；这是脚本模型客户端，不是真实供应商判断 |
| 用量持久化与预算 | RunStore 读/写失败不发送；完成保存失败只发送一次、磁盘保持 unknown；新错误不回退人工。见 tests/orchestrator/test_run_usage.py 与 tests/runtime/test_permission_review.py |
| 旧版本边界 | `schema29-boundary`：52 passed，包含旧28 Run/Session拒绝且文件字节不变 |
| mock 全链路 | `mock-01`：completed，8 工件，rc=0 |
| 依赖 | `pip-01`：No broken requirements found，rc=0 |
| 差异 | diff --check 通过；最终 staged 检查另保存于本地证据 |

首次全量 `full-01` 为 2215 passed、1 failed、1 skipped，唯一失败是工具表面指纹仍为旧版本。逐项将新 schema29 常量与 run_shell 说明还原为旧值后，全部指纹与原基线完全相同，证明没有额外工具表面变化；随后仅更新这五项已核实指纹，再完整复跑通过。开发中测试夹具错误不记为产品失败，原检查日志保留不覆盖。

新增/强化边界包括：人工模式不调 reviewer；硬拒绝不送 reviewer；失效/故障转人工；人工 yes/no 沿原调用继续且不再审核；审核时授权/启动配置变化不执行；副本防修改；共享预算/期限/取消；审计保存失败阻止派发；派发后崩溃不重放；派发后截止不冒充未执行；原始用户与历史拒绝来源/时间/正文保真；旧 Run/Session（包括28）拒绝且不改旧文件。

## 尚待真实验证

本轮未使用真实模型或服务器，未跑 L3，未实现沙箱，不宣称自动放行率、科研质量或指令遵循成功率已经提高。审核无法从输入确认被引用脚本的效果时应转人工；语义审核可能误判，宿主执行边界不变。

下一步先在独立服务器验收目录同步本提交，重复完整回归与 mock，再用有限预算跑真实短场景：manual 对照；明确安全的内联脚本；不确定/越界意图升级人工及附原因的拒绝后重试；审核中取消与恢复；核对 trace/总账/唯一回执/真实文件结果。产品需保留总预算用于审核、纠错和最终交付，不能把 reviewer 视为额外钱包。L3 留到短场景通过后按实际需要决定。

相关决策：[ADR-0031](../decisions/0031-automatic-operation-review.md)。当前接口与上下文以 [CONTRACTS](../../current/CONTRACTS.md) / [CONTEXT](../../current/CONTEXT.md) 为准。
