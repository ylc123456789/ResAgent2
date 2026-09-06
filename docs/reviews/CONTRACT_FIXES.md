# 契约修复与可读性整理

基线：`54dfa99`。分支：`fix/contract-foundations`。本轮按已复现的接口审查问题修复，不增加业务功能，不重写整体架构，不合并 Python 包或迁移旧 Run。

“功能不变”指正常科研工作流、CLI 操作和职责分工不变；过去错误接受、越界读取、失真的指标或过期动作会被正确拒绝，这属于修复而不是功能兼容。每批包含代码、回归测试与同步说明，独立提交 Git；未通过验收不得标完成。

## 分阶段交付

| 阶段 | 范围 | 状态 |
|---|---|---|
| S0 文档基线 | 保存接口卡与模块说明；列出边界及验收计划 | 已完成 |
| S1 身份与作用域 | F01/F02/F05；同 Attempt 恢复 D1 | 已完成 |
| S2 验证与指标 | F03/F04/F11；共享验证有效性判断 | 已完成 |
| S3 返回验收与诊断 | F06/F07/F08/F09/F10；统一证据种类语义 D2 | 已完成 |
| S4 Runtime 边界 | F12；互斥控制信号 D3、可选客户端能力/trace 语义 D4/D5 | 已完成 |
| S5 阅读整理与收尾 | 同名概念收敛、阅读入口和源码注释 D6、文档去除过期缺口、全量回归 | 本地完成；8f809cf 服务器 8/9，默认 Flash 黄金链未通过 |
| S6 服务器反馈收敛 | 实时环境投影、共享文件/工件读取、交付名称语义对齐 | 时序收尾本地 694 passed、1 skipped；新增 Scientific 工件工作集收敛，本地 701 passed、1 skipped，mock completed；本轮服务器待验收 |

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

- S6 Scientific 读取闭环（2026-09-06）：Scientific build_context 复用 workspace_context(state)，不传环境绑定；工件正文从原始 Session 事件按来源/行范围投影，总共 6000 字符、required，移除 read_artifact_summaries 正文前缀生产缓存。Native/CLI 默认 Scientific 输入上限调整为 8192，Compiler 保持 4096，Coding/Experiment 不变。仅补职责提示：检索服务故障不得派代码/实验任务绕路，需要外部材料或决定时走已有 ask_user；replace_text 的“唯一匹配”指每次调用，可多次调用。未新增状态机、Compiler review、正文缓存、长期记忆或摘要 LLM。本地隔离 cwd 全量 **701 passed、1 skipped**，mock E2E completed、diff 检查干净；本轮服务器尚未验收，不能沿用之前 completed 或本地测试宣称真实引用质量/故障策略已通过。另记录既有 Composer 未计标题/分隔符的估算偏差，本轮不扩范围修改。最新交接与边界见 [Scientific 上下文验收单](SCIENTIFIC_CONTEXT_ACCEPTANCE.md)。

- S6 时序收尾（2026-09-06）：核读 `/root/autodl-tmp/e2e-context-b2b7056-20260906/` full trace。Flash 黄金链三次、Pro、repair、direct、跨进程问答通过，但不能据此宣称全绿：literature 遇 timeout/429 后错误转发不适用的工作，最终失败。慢轮 Coding 25 次读取中 19 次在编辑前，旧片段共存并不能解释所有重复读取。按用户裁定保留历史而非修改后清空：复用事件编号展示先后，标注后续已成功的文件修改；区分工作集/短预览截断；明确 search_text 字面搜索。没有新增文件版本库、缓存、LLM 调用、预算扩容或状态机。新代码本地及服务器验收见 [上下文时序验收单](CONTEXT_ORDER_ACCEPTANCE.md)。literature 服务失败后的错误路由尚未修复，不在本次改动中宣称解决。

- S6（2026-09-06）：核读 `/root/autodl-tmp/e2e-contracts-20260906/` 原始请求/响应/reasoning、Session 与 Run。Flash Experiment 50 次调用无 run_command；前 36 次 environment={}，工件仅经短历史呈现；Pro 成功不能排除框架缺口。另有 Compiler 把描述填进精确 expected_* 的确定性语义错位。实现共享 workspace_context（绑定真值 + 文件/工件正文工作集及有界来源索引）、工件分段/整文件 hash、文件或目录搜索；LLMCompiler 不生成精确输出名，错放描述留当前 instructions，finalizer 即使无精确要求仍拒绝零证据交付。不宣称真实模型已稳定，不用旧服务器结果覆盖新提交验收。repair-3 的派生日志不算独立原始 stdout；文档/prompt 明确来源等级，无新日志系统。
- S6 预算小调整（同日）：按用户裁定，文件与工件分别使用同一片段函数，正文各限 **6000 字符**，避免两类相互淘汰；不做动态分配或额度借用。Coding/Experiment 的 Native 与 CLI 默认输入上限同步为 **8192 tokens**，Scientific/Compiler 不变；显式配置与模型容量继续限制总输入。新增实际 Native Agent + AgentLoop 容量测试，两类放满并带真实 tool contracts、最近历史、拒绝反馈仍完整可见；显式 1024/4096 时在 LLM 调用前明确拒绝，不静默扩容。全量 **677 passed、1 skipped**，mock E2E completed；真实服务器回归待执行。

- S5：最终本地 **629 passed、1 skipped**，mock E2E completed（Coding + Experiment + 最终报告）；`git diff --check` 干净。README 171→96 行，导览先讲主链、统一 Orchestrator 名称，并明确 interpreter/构建文件不构成新层。ARCHITECTURE/CONTRACTS/INTERFACES 与源码注释同步；未增业务功能、外部协议或历史迁移层。主分支未改，本轮未 push，也未运行真实 LLM/服务器验收。
- S4：工具派发前再次检查 deadline；ToolObservation 三种控制信号互斥。新增 13 条假时钟/组合负例，Runtime 78 passed；客户端仅 next_action 必需，文档明确可选 hooks 和 action_valid 不代表执行或科学验收。
- S3：成功 payload 按 capability 验证，登记失败保留消费/Session/原诊断/warnings；Scientific 四分支先验收再确认交付，最终 gate 独立验完整响应与证据要求；Run 持久化 terminal_error。导入与新检索证据采用共享种类判据，失败 brief 保留 objective。针对测试覆盖四分支错误 Session、未观察引用、STABLE/答案不提前消费、失败原因落盘、JSON 恢复链真实 payload；独立复核补齐类型转换后的计账、独立 gate 入口重验、Native 转译失败与 Session 状态一致。最终全量见 S5。
- S2：EnvironmentBinding 的 generation 在 prepare/setup 开始前失效，重新创建绑定也失效；Coding 控制提示与完成 gate 共用验证规则，必须覆盖当前编辑与环境。指标采用规范化后精确匹配，同名不同值拒绝，不按文件顺序覆盖。Coding/capabilities 149 passed、1 skipped；Experiment 36 passed。第一轮集成全量 542 passed、1 skipped，另两条标准库白名单测试失败已补齐（hashlib/uuid），最终全量见 S5。
- S0：保存本轮接口参考文档和修复计划；原有本地基线 501 passed、1 skipped。后续记录随每批更新。
- S1：Session ID 包含所属 Run 且长度有界；动态工件索引按 Run 隔离并在读字节前校验；每次新问题分配独立 ID；Runtime 拒绝跨 Attempt 恢复。新增跨 Run/读取/长 ID 测试 12 条，相关回归 86 passed；问答与 Runtime 恢复 24 passed。两组范围有交集，不相加冒充全量测试数。

## 服务器交接（历史 S6 要求；最新 Scientific 补验收见独立验收单）

最新验收范围与故障注入要求见 [SCIENTIFIC_CONTEXT_ACCEPTANCE.md](SCIENTIFIC_CONTEXT_ACCEPTANCE.md)；文件片段时序的既有检查继续见 [CONTEXT_ORDER_ACCEPTANCE.md](CONTEXT_ORDER_ACCEPTANCE.md)。历史结果不得覆盖本次未运行的服务器验收。

8f809cf 的真实结果已保存，上述 8/9 不是本次新代码验收。新代码重点跑 Flash code-experiment（保留每次结果，不重跑到绿再抹去失败），Pro code-experiment 回归、repair 真实 typo 修复。查看 environment.prepared/certified、workspace_reads 的文件/工件行范围、是否实际执行入口、JSON 实际键/值与 Scientific 引用是否一致。明确原始日志与派生说明，不能只看 completed 或 action_valid。

1. 在同一个明确 Git 提交、干净代码树上运行原有 direct、code-experiment、repair、ask-start/ask-resume、literature，保留 full trace 与状态/工件；不把历史结果当本轮验收。
2. 除 clean-workdir 主流程外，用同一 data-root 跑不同 Run，并核对同名 Task 的 Session 不冲突；问答恢复保持 Attempt，再问新问题时旧答案被拒。
3. 重点看 repair 原始 stderr 是否穿过失败登记与 brief，最终报告是否保留执行问题；Coding 的 setup/audit 后是否真的重验；指标是否按明确名称交付，不把 warnings 当完全交付。
4. 对照 Run 状态、Session 与 observation/validation 记录，不只统计 trace.action_valid。固定的格式和跨 Run 拒绝路径已有本地负例，无需靠真实模型碰巧生成坏响应。
5. 全部通过后再决定推送/合并与清理服务器；本轮没有清理或覆盖任何旧服务器产物。
