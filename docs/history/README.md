# 决策与历史

这里回答“为什么改、当时做了什么、怎样验收”。**不是当前规范，也不是入门必读列表。** 当前行为查 [架构](../current/ARCHITECTURE.md) / [接口与契约](../current/CONTRACTS.md)。

## 最近已完成的主线

后续分支 `fix/runtime-resources` 的 schema 6.0 资源与等待预算调整，见 [实施记录](reviews/RUNTIME_RESOURCES_PLAN.md)、[ADR-0013](decisions/0013-runtime-resources.md) 和 [服务器验收要求](reviews/RUNTIME_RESOURCES_ACCEPTANCE.md)。`577b8489` 基线与 `d03abee` 小收尾已复核；后者仍有 Experiment 未先询问便执行缺数据命令的行为失败。复核发现执行 Agent 未把已传入的用户回答放进上下文，现补齐共享答案投影；本轮真实模型补验按 [§9](reviews/RUNTIME_RESOURCES_ACCEPTANCE.md#9-用户回答上下文补验) 执行，不沿用前一轮通过结论。另有 [JSON 输出专项记录](reviews/LLM_JSON_OUTPUT_FOLLOWUP.md)，已复现但未在资源分支修复。

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
