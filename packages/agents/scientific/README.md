# Scientific Agent

科学顾问。

实现 ScientificPort 边界（CONTRACTS §20.7）：`ScientificAgent.run(ScientificTurnRequest)` 返回四态 `ScientificTurnResult`。

- `request_work`：当前 `ScientificAssessment` + 语义化 `WorkRequestDraft`；
- `ask_user`：带 assessment 的用户问题；
- `finish`：最终 `ScientificOpinion`。

允许：只读 Artifact、文献检索、科学推理。禁止：输出 WorkflowProposal/Patch、选择 capability/依赖/物理环境、修改 TaskStatus、调用其他子 Agent、把建议描述成已执行事实。

复用共享 AgentLoop：`AgentState.task_id/attempt_number` 放宽为可选（Scientific session 是 run 级），`AgentLoop.run` 的 request 参数放宽为 `LoopRequest` Protocol，`ToolObservation`/`ModuleResult` 增加 `request_work` 暂停通道。Tool 集：`read_artifact`（allowlist）、`literature_search`（注入 backend/registration port）、`request_work`、`ask_user`（带 assessment）、`finish`。finalizer 交叉检查 evidence 引用、派生 `observed_artifact_ids`；failed/blocked Task 由 Validator 从 Run 对账，并要求 Scientific 通过 `limitations` 说明其影响。

Phase 7.7 原子切换后，`ScientificAgent` 已是 production composition root 的唯一 Scientific 路径；旧 `LegacyScientificAnalyzeAdapter` 已删除。字段见 `docs/CONTRACTS.md` §20，实施顺序见 `docs/DEVELOPMENT_PLAN.md` §10。
