# Scientific Agent

科学顾问。

实现 ScientificPort 边界（CONTRACTS §20.7）：`ScientificAgent.run(ScientificTurnRequest)` 返回四态 `ScientificTurnResult`。

- `request_work`：当前 `ScientificAssessment` + 语义化 `WorkRequestDraft`；
- `ask_user`：带 assessment 的用户问题；
- `finish`：最终 `ScientificOpinion`。

允许：只读 Artifact、文献检索、科学推理。禁止：输出 WorkflowProposal/Patch、选择 capability/依赖/物理环境、修改 TaskStatus、调用其他子 Agent、把建议描述成已执行事实。

复用共享 AgentLoop：`AgentState.task_id/attempt_number` 放宽为可选（Scientific session 是 run 级），`AgentLoop.run` 的 request 参数放宽为 `LoopRequest` Protocol，`ToolObservation`/`ModuleResult` 增加 `request_work` 暂停通道。Tool 集：`read_artifact`（allowlist）、`literature_search`（注入 backend/registration port）、`request_work`、`ask_user`（带 assessment）、`finish`。finalizer 交叉检查 evidence 引用、派生 `observed_artifact_ids`、验证 `acknowledged_task_ids`。

当前尚未接 production composition root（7.7 切换时接入）；旧 `LegacyScientificAnalyzeAdapter` 仍是 production 路径。字段见 `docs/CONTRACTS.md` §20，实施顺序见 `docs/DEVELOPMENT_PLAN.md` §10。
