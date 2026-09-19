# contracts

跨模块稳定类型和接口。

当前已实现：

- Workflow、WorkflowTask、WorkflowPatch；
- ModuleTaskRequest、AcceptanceSpec、ModuleResult；
- Attempt；
- ArtifactRef、ArtifactCandidate；
- QuestionDraft、PendingQuestion、UserAnswer 与系统配对的 RecordedAnswer；
- Capability；
- CodeUnderstandResult、CodeModifyResult、VerificationResult；
- 公共 status 和 error code。

本包只表达语义，不执行 LLM、文件、进程、Git 或工作流。它不得依赖 runtime、orchestrator 或任何具体 Agent。

方法、字段和接收规则见 [模块接口与契约](../../docs/current/CONTRACTS.md)。

## 安装与测试

从仓库根目录执行：

```bash
conda activate ResAgent2
python -m pip install -e 'packages/contracts[test]'
python -m pytest tests/contracts
```

稳定导入路径是 `resagent2_contracts`。包版本为 `0.1.0`，当前 wire schema 版本为 `11.0`。ResearchRequest 不含部署资源；dataset_refs 只在系统状态和内部调用中传递。ModuleTaskRequest 与 ScientificTurnRequest 都用单一 `instruction` 表达本次语义任务；预算、工作区、Session、资源、答案等仍使用结构化字段。Experiment 的精确交付要求使用 AcceptanceSpec，确认行为使用 confirm_before_experiment。UserAnswer 仍是调用方提交的答案；RecordedAnswer 由 Controller 配上已持久化的 question_text，供 Run 和内部调用保存、传递。旧 10.0 及更早 Run 不支持恢复，原件保留、不迁移。

问题背景写进 QuestionDraft.text，不另填 reason。WorkflowProposal/WorkflowPatch 只携带任务图与身份/修订，不另填图级 summary/rationale/reason；任务图内部仍保留目标、约束及 typed inputs，Scheduler 执行时将其物化为 ModuleTaskRequest.instruction。完整边界见 [接口与契约](../../docs/current/CONTRACTS.md)。

提问的 requested_fields 与回答的 values 键共用 AnswerFieldName：ASCII 字母开头，后续为字母、数字或下划线，总长 1–64。它是问题内的机器键，不是自然语言说明或全局身份；问题正文与答案值仍可用自然语言。Runtime 提问工具直接复用该类型，没有每个 Agent 各自维护的规则。
