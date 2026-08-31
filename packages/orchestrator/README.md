# orchestrator

ResAgent2 的顶层控制模块。

负责：

- ResearchRun；
- Scientific Session 引用、WorkRequest 与 WorkOutcome；
- WorkRequest → WorkflowProposal/Patch 的 WorkflowCompiler；
- Workflow validation/revision；
- Task/Attempt 状态；
- capability 路由；
- Module Port/Adapter；
- retry、Ask User 和 finish gate；
- ScientificCompletionValidator 与 deterministic final report renderer（Phase 7 目标）；
- Artifact index。

它不直接实现科学判断、代码修改和实验执行。普通调度必须由确定性代码完成；Phase 7 允许内部 WorkflowCompiler 用一次有界结构化 LLM 调用把语义工作请求翻译成任务图，但 LLM 不直接修改状态。

## 当前已实现

- WorkflowProposal 校验后创建 ResearchRun；
- 按原始任务顺序稳定计算 ready Task 集合；
- capability → ModuleBinding → ModulePort 路由；
- Task/Attempt 状态机和自动 retry；
- blocked/failed 后显式 repair 与 retry；
- PendingQuestion、UserAnswer 和 Session resume；
- 依赖任务 Artifact 自动传给下游 ModuleTaskRequest；
- ArtifactCandidate 的 workspace 边界检查、hash、复制和 provenance 登记；
- revision-bound WorkflowPatch 和旧 revision 历史；
- finish gate；
- 内存 RunStore 和原子 JSON RunStore；
- WorkflowCompiler：`WorkflowCompiler` Protocol + `DeterministicWorkflowCompiler`（测试 fixture）+ `LLMWorkflowCompiler`（注入 `CompilerLLM`，一次结构化调用）。

当前 ModulePort 可以注入原生 Coding/Experiment Agent；orchestrator 自身仍不 import 具体 Agent。Coding/Experiment/Scientific 三个 legacy adapter 已分别在 Phase 5/6/7 删除，全部由原生 Agent 取代。JSON Store 适合本地单进程恢复，不宣称支持并发写入或分布式事务。

Phase 7.7 原子切换后，production composition root 走 `ResearchController`：自然语言 `create_run(request)` 进入科学控制循环，`ScientificAgent` 提出 `WorkRequestDraft`，`WorkflowCompiler` 生成 Proposal/Patch，Scheduler 执行 Coding/Experiment 图，`WorkOutcome` 回传后形成最终 `ScientificOpinion` 并经 `ScientificCompletionValidator` 写 completed。旧 PlanningPort 路径已删除，不保留两套总控逻辑。

## 最小使用方式

```python
controller = ResearchController(
    scientific_port=scientific,
    compiler=compiler,
    scheduler=WorkflowScheduler(bindings={...}, store=JsonRunStore("state")),
    registry=registry,
)
run = controller.create_run("run_demo", request)  # 唯一 production 入口
```

`ResearchController.create_run` 是唯一 production 入口：调用 Scientific Agent，接收 WorkRequest，由 WorkflowCompiler 产生 Proposal/Patch，再由 Scheduler 执行 Coding/Experiment 图。Scheduler 自身只执行确定性的 ready Task（`run_until_stable`），不再提供 `create_run`，也不决定 Run 完成（ADR-0011 §1）。
