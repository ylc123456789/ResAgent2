# Scientific Agent

科学顾问。

Phase 7 目标输入：自然语言研究目标、当前 Run 摘要、明确授权的 Artifact、最新 WorkOutcome 和用户回答。

Phase 7 目标输出：

- `request_work`：当前 ScientificAssessment + 语义化 WorkRequestDraft；
- `ask_user`：需要用户补充信息；
- `finish`：最终 ScientificOpinion。

允许：只读 Artifact、文献检索、科学推理。
禁止：输出 WorkflowProposal/Patch、选择 capability/依赖/物理环境、修改 TaskStatus、调用其他子 Agent、把建议描述成已执行事实。

Scientific Agent 只有一套可恢复 Agentic Loop，不区分 plan/analyze 模式。证据不足时，`request_work` 对它相当于异步 Tool；ResAgent 完成执行后把 WorkOutcome 返回同一个 Session。

当前代码尚未实现上述目标，仍通过 Phase 4 `LegacyScientificAnalyzeAdapter` 调用旧 ExpAgent。准确目标字段见 `docs/CONTRACTS.md` §20，实施顺序见 `docs/DEVELOPMENT_PLAN.md` §10。
