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
- 内存 RunStore 和原子 JSON RunStore。

当前 ModulePort 可以注入原生 Coding/Experiment Agent；orchestrator 自身仍不 import 具体 Agent。Coding 和 Experiment legacy adapter 已分别在 Phase 5/6 删除，只剩 `LegacyScientificAnalyzeAdapter` 作为过渡。JSON Store 适合本地单进程恢复，不宣称支持并发写入或分布式事务。

上面“WorkRequest/WorkflowCompiler/Scientific Session”的职责是 Phase 7 目标，当前代码尚未实现。当前 production 仍使用 PlanningPort + WorkflowProposal 创建 Run，Scientific 仍走 `LegacyScientificAnalyzeAdapter`；Phase 7 切换后删除旧路径，不长期维护两套总控逻辑。

## 最小使用方式

```python
scheduler = WorkflowScheduler(bindings={...}, store=JsonRunStore("state"))
scheduler.create_run("run_demo", request, proposal)
run = scheduler.run_until_stable("run_demo")
```

`run_until_stable` 只执行确定性的 ready Task。这是 schema 1.1 的当前用法。Phase 7 目标入口改为自然语言 `create_run(request)`：ResearchController 调用 Scientific Agent，接收 WorkRequest，再由 WorkflowCompiler 产生 Proposal/Patch；Scheduler 仍不调用 LLM 决定普通状态转换。
