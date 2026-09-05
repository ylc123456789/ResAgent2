# 契约修复与可读性整理

基线：`54dfa99`。分支：`fix/contract-foundations`。本轮按已复现的接口审查问题修复，不增加业务功能，不重写整体架构，不合并 Python 包或迁移旧 Run。

“功能不变”指正常科研工作流、CLI 操作和职责分工不变；过去错误接受、越界读取、失真的指标或过期动作会被正确拒绝，这属于修复而不是功能兼容。每批包含代码、回归测试与同步说明，独立提交 Git；未通过验收不得标完成。

## 分阶段交付

| 阶段 | 范围 | 状态 |
|---|---|---|
| S0 文档基线 | 保存接口卡与模块说明；列出边界及验收计划 | 已完成 |
| S1 身份与作用域 | F01/F02/F05；同 Attempt 恢复 D1 | 待完成 |
| S2 验证与指标 | F03/F04/F11；共享验证有效性判断 | 待完成 |
| S3 返回验收与诊断 | F06/F07/F08/F09/F10；统一证据种类语义 D2 | 待完成 |
| S4 Runtime 边界 | F12；互斥控制信号 D3、可选客户端能力/trace 语义 D4/D5 | 待完成 |
| S5 阅读整理与收尾 | 同名概念收敛、阅读入口和源码注释 D6、文档去除过期缺口、全量回归 | 待完成 |

不同文件的实现可并行，但按明确范围逐批检查并提交；不在同一文件里同时进行无关重构。

## 问题与最小验收

| 编号 | 修复目标 | 必需回归 |
|---|---|---|
| F01 | Session 身份覆盖 Store 的 Run/task/attempt 范围 | 同 Store 不同 Run 同名任务均可执行；长 ID 有界；恢复仍用同 Session |
| F02 | 动态工件读取先核验 Run 作用域 | 同 Run 同 turn 新工件可读；跨 Run 工件读取字节前被拒；CLI/E2E 同规则 |
| F03 | 环境变更不能沿用旧代码验证 | setup 成功/失败均使旧验证失效；策略拒绝未执行不失效；重新 audit 不等于重新测试 |
| F04 | 指标精确匹配、冲突不能静默覆盖 | accuracy 不替代 balanced_accuracy；证据顺序不改变结果；冲突可恢复拒绝 |
| F05 | 每个新用户问题独立身份 | 同 Attempt 连续两问 ID 不同；第一问答案不能回答第二问 |
| F06 | ModulePort 的成功 payload 按 capability 验收 | 错误/空 payload 拒绝；合法暂停/失败无成功 payload 仍可返回 |
| F07 | Scientific 每个返回分支都守约 | 错身份/状态/assessment 引用在消费交付前拒绝；最终 gate 独立验收 required evidence |
| F08 | 工件登记失败保留消费与原始诊断 | 已发生 calls 只记一次；Session/原根因保留，登记错误另可追踪 |
| F09 | Run 保留终止原因 | 有/无 Session 的 Scientific failure 均可从 Run 查询；报告登记失败可诊断 |
| F10 | 隐藏任务 ID 不等于丢掉工作目标 | 同错误但不同业务目标在 Scientific brief 仍可区分 |
| F11 | 控制提示与 finalizer 的验证判据一致 | 验证失败/缺失/过期都不能要求 finish |
| F12 | 已过期模型动作不得启动副作用 | 假时钟越过 deadline 后不执行工具；已发生 LLM 消费仍保留 |

## 小范围语义裁定

- pause/resume 保持同 Attempt；retry 才开新 Attempt。Runtime 也落实这一边界，不做旧 Run 兼容。
- `required_evidence_kinds` 按字段含义表示“引用已登记、已观察的指定种类工件”，不偷偷等价成“本次必须执行某工具”。已读且授权的导入证据也有效；若研究目标明确要求新检索，仍由目标/约束表达。
- `question`、`request_work`、`finish_candidate` 至多一种，复用现有 ToolObservation 加组合校验，不新增一套动作协议。
- 不把 Runtime/provider 的 `action_valid` 当作全部工具参数校验通过；保留其调试含义并写清验收方法，不新建 trace 平台。
- “ResAgent2”表示项目；说明中优先使用“Orchestrator”指编排模块。interpreter 仍只是 Scientific 的上下文辅助函数，不升级成独立服务。

## 验证与提交纪律

1. 每批先跑针对性确定性测试，再检查差异；测试不得依赖真实 LLM 碰巧选择正确分支。
2. 本地全量在独立临时 cwd 运行，避免向仓库写测试状态；包含 `tests` 和 `apps/cli/tests`。
3. 同步 ARCHITECTURE/CONTRACTS/INTERFACES，不再把已修项留成现状缺口，也不把未修项写成保证。
4. 最终独立复核关键边界、全量测试和 `git diff --check`。本地 Git 按阶段提交；push/合并 main 另行说明，不自动执行。
5. 服务器真实验收单独记录，不能用本地 mock 冒充：原有 direct、code-experiment、repair、ask/resume、literature，加同 data-root 连续 Run/连续问答。未运行即注明未运行。

## 验收记录

- S0：保存本轮接口参考文档和修复计划；原有本地基线 501 passed、1 skipped。后续记录随每批更新。
