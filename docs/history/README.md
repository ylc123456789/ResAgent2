# 决策与历史

这里回答“为什么改、当时做了什么、怎样验收”。**不是当前规范，也不是入门必读列表。** 当前行为查 [架构](../current/ARCHITECTURE.md) / [接口与契约](../current/CONTRACTS.md) / [模型上下文](../current/CONTEXT.md)。

## 科研目录与 Interpreter（未合并）

2026-09-23 在同一 `fix/code-health` 分支实现科研目录与带引用的反向简报，schema **15.0**。产品 `b31648d`、专项回归 `42efaa1`，本地 **1269 passed、1 skipped**，mock completed。原产物登记表保持唯一权威，Interpreter 与 Compiler 并列，权限、预算和 Agent 单入口规则保留。服务器 `36360f85` 已完成两条公开整链（27/132 次请求）和 CUDA 完整训练，核心目录/简报/引用通过独立复核；后续 CLI 定向补测已验证 Experiment 任务问答和完整 Controller 续跑（14 次调用、completed），GPU 采样封存、脚本留存和核验整理完成，三处验收缺口关闭。候选文件名错误的失败处理、任务答案的跨层可发现性作为后续设计事项保留。见[服务器复核与最终收尾](reviews/RESEARCH_HANDOFF_SERVER_REVIEW_2026-09-24.md#verified-closeout)，不把测试方通过计数等同于计划全部覆盖。设计见 [ADR-0017](decisions/0017-research-index-and-work-interpreter.md)，测试步骤与证据要求见[本轮交接](reviews/RESEARCH_HANDOFF_TEST_2026-09-23.md)。此前 schema 14 的通过结论只适用于原测试提交。

## 主线健康审查

2026-09-22 对合并后的 `main@5fe2c7f` 做代码健康审查，在 `fix/code-health` 分阶段修复并清理遗留代码，schema 14.0。原产品基线 `51c3238` / 服务器实测 `b6258c7` 的回归 **1224 passed、1 skipped** 与 mock 通过，两条真实模型整链分别预算耗尽和最终工件契约错误。复核发现批准恢复语义缺口、Compiler 能力说明陈旧及 Scientific 完成检查不完整；2026-09-23 已按原机制修复，产品与测试基线 `9b425f2`，本地与服务器 **1237 passed、1 skipped**、mock 通过；服务器 `2bad2d9a` 的真实公开入口整链也通过，跨进程批准、Experiment 任务内问答和最终工件完成，20 次调用与账本一致。原始证据已复核，验收范围内具备合并条件；分支尚未合并。Scientific verdict 曾反馈纠正、非法 data 纠正由确定性覆盖及中间算式文字瑕疵均保留于[收尾与边界](reviews/CODE_HEALTH_SERVER_REVIEW_2026-09-23.md#verified-closeout)。原始发现见[主线审查](reviews/MAIN_CODE_HEALTH_REVIEW_2026-09-22.md)，前轮实施见[原交接](reviews/CODE_HEALTH_TEST_HANDOFF_2026-09-22.md)，本轮证据勘误、实现及测试步骤见[服务器复核与根因修复复测](reviews/CODE_HEALTH_SERVER_REVIEW_2026-09-23.md)。

2026-09-23 又对当前分支做[架构与流程不变量复核](reviews/ARCHITECTURE_INVARIANTS_REVIEW_2026-09-23.md)：检查模块独立、依赖倒置、开闭原则的适用边界及控制/恢复/预算/权限/证据流程，未发现本分支破坏核心设计；补齐 Scientific 包边界测试和 TaskProposal 旧说明，汇总[设计原则](../current/DESIGN_PRINCIPLES.md)。本地 1238 passed / 1 skipped，mock 通过；执行逻辑未变，服务器实测仍为上段提交，分支继续不合并。

## 最近已完成的主线

Run 控制简化已完成修复轮定向功能复核，并将 `refactor/run-control@84063e4` 快进合入 **main**（schema **13.0**，产品 `602ffee`，实测 `8b071e6a`）：Coding 删除动作、批准恢复时环境核验两处缺陷关闭，本地/服务器 **1153 passed、1 skipped**。4 个真实模型场景覆盖 5 项功能；固定 Task 执行不等于 Scientific 完整 Run E2E。原始 Session 拒绝快照未保存、只读探针重跑覆盖旧状态等限制保留在[修复轮复核](reviews/RUN_CONTROL_SERVER_REVIEW_2026-09-22.md#fixed-round)。合并保留分段提交与开发分支，无需为记录勘误再跑模型/GPU。服务器仍使用固定 `projects/ResAgent2`。设计见[方案](reviews/RUN_CONTROL_SIMPLIFICATION_PLAN_2026-09-22.md)，复现步骤见[测试交接](reviews/RUN_CONTROL_TEST_HANDOFF_2026-09-22.md)。

统一 Agent IO V2 已完成分阶段服务器验收（schema **12.0**，产品实测 `22347c5`，开发分支 `refactor/unified-agent-entry`）：三个 Agent 共用单一 invoke 和报告/工件协议；复测 **1071 passed、1 skipped**，完整 CUDA 场景与分析探针通过。repair 保留“自动谓词 FAIL、包装命令人工复核通过”，命令确认采用既有真实流程与新增确定性拒绝测试的组合证据；详见 [最终复核](reviews/UNIFIED_AGENT_IO_V2_RETEST_REVIEW_2026-09-21.md)。该实现已随 `refactor/run-control` 一并合入 main。最初本地结果另保留于[本地验收记录](reviews/UNIFIED_AGENT_IO_V2_ACCEPTANCE_2026-09-20.md)。

Tool / Components 职责整理已完成服务器四个小探针及原始证据复核（实测 `6672376`，产品 `4cdb725` / `54fab5c`），schema 10.0 不变。新增 Components，Capabilities 聚焦模型 Tool，Runtime 引擎不改；当前每个模型可调用 Tool 都有独立实现文件，`__init__.py` 只负责公开导出。`refactor/tool-components` 的实现已包含于 main 的 `f771a70e` 基线。设计见 [ADR-0015](decisions/0015-tool-components-boundary.md)，本地验证、29 次模型调用、文献阅读范围和报告勘误见 [最终复核与收尾](reviews/TOOL_COMPONENTS_ACCEPTANCE.md#verified-closeout)。四探针不代表重跑科研 L3。

自然语言字段精简与答案键规范已完成阶段验收（产品 `c03115b`、`3476222`，最终实测 `ee7d821`，schema **10.0**）：Scientific 完成摘要从 opinion.statement 派生，提问背景进 text，Compiler 不再重复填写图级说明；三个 Agent 与公共问答模型共用 AnswerFieldName。旧版 Compiler 新旧六探针通过，新版三问答补齐 Coding 自问及 Experiment 经 CLI 回答，结果 222/6/F1；本地/服务器 1052 passed、1 skipped。总计 21 次调用，单探针均未超过 20。Coding 测试驱动恢复时重给完整额度的偏差和后续复用要求见 [最终复核与收尾](reviews/SEMANTIC_FIELD_SLIMMING.md#verified-closeout)，不能写成预算扣减已验证；本轮无须额外付费重跑，不改写旧报告/失败现场。

Compiler 默认额度与 L3 已完成收尾（产品 `e6688f3`，schema **8.0**）：Compiler 与三个 Agent 共用 128000 输入默认值，独立覆盖入口保留，CLI/E2E 同源；不改变 JSON-only 编译/审查或增加 Compiler 压缩。旧失败工作请求的编译探针通过，新 L3 自主完成四次 200-epoch 训练并交付负结果，50 次调用与账本一致。通过不等于全程零错误；原始 trace 中的恢复、指标身份限制、安装成本和报告勘误见 [验收收尾与待办](reviews/COMPILER_CONTEXT_L3_ACCEPTANCE.md)。

前一阶段原生工具调用、串行批次、统一调用预算和上下文分配已完成阶段验收（产品 `f98b6fd`，schema **8.0**）：三个Agent共用Tool、Loop、Session与Composer，Compiler仍用正文JSON；移除TaskBudget.max_steps及隐藏50步上限，材料按权重起步、空余按优先级借用，历史压缩保存检查点，整包真正不足仍报错。本地/服务器全量1014 passed、1 skipped；六个真实小探针完成。主开发复核并纠正了报告的累计计量和暂停状态，详见[最终结果、勘误与边界](reviews/CONTEXT_ALLOCATION_REVIEW.md#verified-closeout)、[分阶段实施](reviews/RUNTIME_CONTINUATION_PLAN.md)。旧L3保持暂停、不迁移；该轮小探针不是科研级长任务通过声明。

上下文语义、128K 预算与文献呈现已完成阶段验收（产品 `3efce21`，服务器实测 `ba84547`，schema 7.0 不变）。状态/历史语义、共享失败诊断、相关风险提示和预算均在原始请求中核对；本地及服务器全量 859 passed、1 skipped。实时文献两次因 arXiv timeout/429 暂停，另用历史真实检索记录加真实 Flash 模型补齐文献消费链验证，不能写成实时检索已恢复。见 [最终结果、勘误与边界](reviews/CONTEXT_128K_ACCEPTANCE.md#verified-closeout)、[原始审查与演变](reviews/CONTEXT_REVIEW_2026-09-13.md)；当前规则见 [CONTEXT](../current/CONTEXT.md)。未新增记忆系统、动态预算分配器或 JSON 专项修复。

前一阶段语义交接（产品 `704dbd9`、`0065088`，实测 `f2d4421`）一并收尾：模块解释通过既有工件读取链交付，Controller 把原题与回答配对后传给对应 Agent。代码理解和三个 Agent 的短回答已有真实消费证据；当时风险报告未读、文献翻页和 Coding 上下文缺口转入上述上下文阶段，不能把后续补验倒写为旧提交全绿。见 [ADR-0014](decisions/0014-semantic-handoffs.md)、[分阶段结果](reviews/SEMANTIC_HANDOFFS_ACCEPTANCE.md#verified-closeout)。旧 schema、原报告、失败现场、环境和缓存原样保留。

JSON 格式反馈与编译字段语义修复已验收：产品提交 `8cfd373` 接入既有有界反馈，`dd770f8` 让生成和评审共用字段解释；不新增组件、不改变 schema 6.0 或预算。服务器 828 passed、1 skipped；三次仅编译与两个标准库注入均完成，原始消息和 Session 已复核。见 [最终结果、报告勘误与边界](reviews/LLM_JSON_OUTPUT_FOLLOWUP.md#verified-closeout) 和 [可复跑验收单](reviews/JSON_OUTPUT_ACCEPTANCE.md)。非法输出仍可能发生，完成的是安全有界恢复，不是上游可靠性保证；未重跑完整 GPU 矩阵。

运行期资源主线已完成分阶段验收，公共 schema 为 6.0：调用方不预填数据集，缺少所需资源沿用问答恢复，显式人工等待不消耗 Run 超时。见 [实施记录](reviews/RUNTIME_RESOURCES_PLAN.md)、[ADR-0013](decisions/0013-runtime-resources.md) 和 [服务器验收单](reviews/RUNTIME_RESOURCES_ACCEPTANCE.md)。最终产品提交 `f3179e5` 的 §9 原始 trace、Session 与实际指标已于 2026-09-11 复核；用户回答现经共享上下文进入执行 Agent 并影响动作，805 passed、1 skipped。收尾仅同步文档，不改变该产品提交。

历史范围必须分开：`577b8489` 跑完整回归；`d03abee` 补验仍有缺数据先运行的失败；`f3179e5` 做针对性回答上下文补验，不能称为最终提交重跑了完整 GPU 矩阵。两次 schema 错误由 AgentLoop 反馈纠正；资源主线当时未关闭 [JSON 输出专项](reviews/LLM_JSON_OUTPUT_FOLLOWUP.md)，后续独立修复结果见上段，不能以资源补验零解析错误替代其验收。详见 [最终复核记录](reviews/RUNTIME_RESOURCES_PLAN.md#2026-09-11-最终复核与收尾)。

截至文档整理基线 `808e8f1`：接口契约优化 P0–P5 与后续收尾已合入 main，公共 schema 为 5.0。最后一轮模型输出配置验收对应产品代码 `ab5066f`，随后是文档收尾；不要把分阶段验收误写成最终每个提交都重新跑过完整矩阵。

- [接口优化计划与完成情况](reviews/INTERFACE_OPTIMIZATION_PLAN.md)：阶段、范围和提交。
- [任务职责验收](reviews/INTERFACE_SCOPE_ACCEPTANCE.md)：编译职责、review 可见性和执行链。
- [LLM 失败诊断验收](reviews/LLM_DIAGNOSTICS_ACCEPTANCE.md)：逐尝试 trace、空正文和输出截断。
- [模型输出默认配置与验收](reviews/MODEL_OUTPUT_DEFAULTS.md)：依据、取舍和结果；该轮确定性基线为 775 passed、1 skipped，不是无限输出或永久稳定保证。

这些是明确时间点的记录；后续变化形成新记录，不覆盖原失败现场或结论。

## 历史材料怎么用

[L3 基准与自进化调研（2026-09-15）](reviews/L3_BENCHMARKS_AND_SELF_IMPROVEMENT_2026-09-15.md) 对比可借用的外部任务与优化实现；它是下一步测试建议，不表示已接入或已验收。

| 材料 | 保存什么 | 入口 |
|---|---|---|
| ADR | 长期设计取舍、理由和替代方案 | [决策索引](decisions/README.md) |
| 开发历程 | 早期阶段目标、推进与验收 | [DEVELOPMENT_PLAN](DEVELOPMENT_PLAN.md) |
| 审查与计划 | 一次审查发现、修复范围和步骤 | [资料目录](reviews/) |
| 验收记录 | 某提交的复现要求、成功/失败与证据 | [资料目录](reviews/) |

## 保存规则

- 保留历史原文。“当前”“待执行”、旧字段和旧命令属于当时语境，不自动成为当前要求。
- 已接受 ADR 变更时追加新决定并说明取代关系，不把旧理由改成后来才知道的结论。
- 本次只搬移历史文件、修正导航链接、加归档提示。命令、服务器路径和提交号不因整理而改写。
- 新规则落地后更新 current，示例受影响则更新 guides；历史索引不复制完整字段和接口表。
