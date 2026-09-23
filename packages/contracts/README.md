# contracts

跨模块稳定类型和接口。当前 wire schema 为 `15.0`；不支持直接恢复旧版本 Run 或 Session，不修改旧记录。

三个 Agent 共同使用 `invoke(AgentRequest) -> AgentResult`。任务内容只有 `instruction` 和 `input_artifacts`；预算、权限、工作区和恢复定位保留明确控制字段。结果业务内容只有 `report + artifacts`，状态、控制动作、Session、错误和实际调用计量独立保存。

主要契约：

- `AgentRequest`、`AgentResult`、`ControlSignal`；
- `RunBudget`、`ExecutionLimits`、`RunPermissions`、`AgentPermissions`、`WorkspaceAccess`；
- `WorkflowAgentKind`、`WorkflowAgentRegistry`、`WorkflowTask`、`TaskProposal`、`WorkflowPatch`；
- `ArtifactCandidate`、`ArtifactRef`、`Attempt`；
- `TaskAcceptanceSpec`、`ConclusionRequirements`；
- `QuestionDraft`、`PendingQuestion`、`UserAnswer`、`RecordedAnswer`、`ActionSnapshot`；
- `WorkFeedback`、`ScientificOpinion`、`ObservationTrace` 等结构化 artifact 内容。

图节点只允许 Coding 和 Experiment，Scientific 由 Controller 调用。任务提交阶段的验收要求及逻辑输出名称登记为一份 `acceptance_requirements`，已接受任务和 Attempt 只保存同一正式引用。

问题和工作请求以 artifact 传递，`control` 仅引用正式 artifact ID 或本次结果中的候选下标。回答内容保存问题快照、请求字段、可选选项和准确的 Run/Task/Attempt/Session 归属。回答键使用 `AnswerFieldName`：ASCII 字母开头，后续为字母、数字或下划线，总长 1-64。

本包只定义数据形状与不变量，不执行 LLM、文件、进程、Git 或工作流，也不依赖其他项目包。

```bash
conda activate ResAgent2
python -m pip install -e 'packages/contracts[test]'
python -m pytest tests/contracts
```

稳定导入路径为 `resagent2_contracts`。完整规则见[模块接口与契约](../../docs/current/CONTRACTS.md)。
