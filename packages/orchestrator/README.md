# orchestrator

ResAgent2 的顶层控制模块。

负责：

- ResearchRun；
- Workflow validation/revision；
- Task/Attempt 状态；
- capability 路由；
- Module Port/Adapter；
- retry、Ask User 和 finish gate；
- Artifact index。

它不直接实现科学分析、代码修改和实验执行。普通调度必须由确定性代码完成；LLM 只用于计划提出/修订及需要智能判断的有界任务。

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

当前 ModulePort 可以注入原生 Coding Agent；orchestrator 自身仍不 import 具体 Agent。Phase 4 的 Experiment/Scientific legacy adapters 继续作为过渡实现，Coding legacy adapter 已在 Phase 5 删除。JSON Store 适合本地单进程恢复，不宣称支持并发写入或分布式事务。

## 最小使用方式

```python
scheduler = WorkflowScheduler(bindings={...}, store=JsonRunStore("state"))
scheduler.create_run("run_demo", request, proposal)
run = scheduler.run_until_stable("run_demo")
```

`run_until_stable` 只执行确定性的 ready Task。科学计划仍必须通过 WorkflowProposal 或 WorkflowPatch 显式进入，Scheduler 不调用 LLM 决定普通状态转换。
