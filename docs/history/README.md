# 决策与历史

这里回答“为什么改、当时做了什么、怎样验收”。**不是当前规范，也不是入门必读列表。** 当前行为查 [架构](../current/ARCHITECTURE.md) / [接口与契约](../current/CONTRACTS.md)。

## 最近已完成的主线

运行期资源主线已完成分阶段验收，公共 schema 为 6.0：调用方不预填数据集，缺少所需资源沿用问答恢复，显式人工等待不消耗 Run 超时。见 [实施记录](reviews/RUNTIME_RESOURCES_PLAN.md)、[ADR-0013](decisions/0013-runtime-resources.md) 和 [服务器验收单](reviews/RUNTIME_RESOURCES_ACCEPTANCE.md)。最终产品提交 `f3179e5` 的 §9 原始 trace、Session 与实际指标已于 2026-09-11 复核；用户回答现经共享上下文进入执行 Agent 并影响动作，805 passed、1 skipped。收尾仅同步文档，不改变该产品提交。

历史范围必须分开：`577b8489` 跑完整回归；`d03abee` 补验仍有缺数据先运行的失败；`f3179e5` 做针对性回答上下文补验，不能称为最终提交重跑了完整 GPU 矩阵。两次 schema 错误由 AgentLoop 反馈纠正；[JSON 输出专项](reviews/LLM_JSON_OUTPUT_FOLLOWUP.md) 仍开放，不能因本轮零解析错误而关闭。详见 [最终复核记录](reviews/RUNTIME_RESOURCES_PLAN.md#2026-09-11-最终复核与收尾)。

截至文档整理基线 `808e8f1`：接口契约优化 P0–P5 与后续收尾已合入 main，公共 schema 为 5.0。最后一轮模型输出配置验收对应产品代码 `ab5066f`，随后是文档收尾；不要把分阶段验收误写成最终每个提交都重新跑过完整矩阵。

- [接口优化计划与完成情况](reviews/INTERFACE_OPTIMIZATION_PLAN.md)：阶段、范围和提交。
- [任务职责验收](reviews/INTERFACE_SCOPE_ACCEPTANCE.md)：编译职责、review 可见性和执行链。
- [LLM 失败诊断验收](reviews/LLM_DIAGNOSTICS_ACCEPTANCE.md)：逐尝试 trace、空正文和输出截断。
- [模型输出默认配置与验收](reviews/MODEL_OUTPUT_DEFAULTS.md)：依据、取舍和结果；该轮确定性基线为 775 passed、1 skipped，不是无限输出或永久稳定保证。

这些是明确时间点的记录；后续变化形成新记录，不覆盖原失败现场或结论。

## 历史材料怎么用

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
