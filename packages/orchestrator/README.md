# orchestrator

ResAgent2 的顶层控制模块。

负责：

- ResearchRun；
- Scientific Session 引用、WorkRequest 与 WorkOutcome；
- WorkRequest → WorkflowProposal/Patch 的 WorkflowCompiler；
- 执行事实 → 科研目录及带引用简报的 WorkInterpreter；
- Workflow validation/revision；
- Task/Attempt 状态；
- coding / experiment 模块路由；
- Module Port/Adapter；
- retry、Ask User 和 finish gate；
- ScientificCompletionValidator 与 deterministic final report renderer；
- 唯一产物登记表及由其派生的科研目录。

它不直接实现科学判断、代码修改和实验执行。普通调度由确定性代码完成；WorkflowCompiler 生成一个任务草图，解析或结构错误最多纠正一次，无额外语义复审调用。LLM 不直接修改状态。

## 当前已实现

- ResearchController 根据 ResearchRequest 创建 ResearchRun；已有 Run 中的工作请求再编译、接受为 WorkflowProposal/Patch；
- 按原始任务顺序稳定计算 ready Task 集合；
- WorkflowAgentKind → ModuleBinding → ModulePort 路由；
- Task/Attempt 状态机和自动 retry；
- 失败任务按既有策略自动重试；Scientific 可通过后续 WorkRequest 安排修复；
- PendingQuestion、UserAnswer 和 Session resume；
- AgentRequest 的 instruction 与 input_artifacts；未来输入用 output_name 显式绑定上游成功 Attempt 的唯一产物；
- answer、work_feedback、dataset_catalog 及验收要求的冻结工件交接；
- ArtifactCandidate 的 workspace 边界检查、hash、复制和 provenance 登记；
- revision-bound WorkflowPatch 和旧 revision 历史；
- finish gate；
- 内存 RunStore 和原子 JSON RunStore；Run 创建时固定授权，RunUsagePort 在模型发送前保存请求占用；
- WorkflowCompiler：`WorkflowCompiler` Protocol + `DeterministicWorkflowCompiler`（测试 fixture）+ `LLMWorkflowCompiler`（注入 `CompilerLLM`，最多两版 draft）。成功返回 CompilationResult，失败抛 CompilationError，两者报告本次调用消费，详见 [接口](../../docs/current/CONTRACTS.md#compiler)。

Scientific、Coding、Experiment 都以 `invoke(AgentRequest) -> AgentResult` 注入 ModulePort；orchestrator 不 import 具体 Agent。三个模块各有一种业务模式，返回 report 和 artifacts，控制动作只引用结果工件。JSON Store 适合本地单进程恢复，不宣称支持并发写入或分布式事务。

Port 与原生 finalizer 属于可信进程内实现；LLM 不能自行提交执行、验证或观察记录。Controller/Scheduler 验证公开结果、身份、hash、归属及工件内容，不通过读取下游私有 Session 取证。当前 schema 为 16.0，旧 Run 保留但不迁移或恢复。

Controller/Scheduler 给三个 Agent、Compiler 与 Interpreter 绑定同一请求用量和剩余期限。AgentResult.llm_calls 只用于诊断，不重复扣费；预算耗尽与超时按对应错误终止。操作批准沿用公开问答工件，不能改变 Run 权限。详见[运行控制契约](../../docs/current/CONTRACTS.md#research-request)。

production composition root 走 `ResearchController`：`create_run(run_id, request)` 进入科学控制循环，`ScientificAgent` 提出 `WorkRequestDraft`，`WorkflowCompiler` 生成 Proposal/Patch，Scheduler 执行 Coding/Experiment 图，`WorkOutcome` 回传后形成最终 `ScientificOpinion` 并经 `ScientificCompletionValidator` 写 completed。旧 PlanningPort 路径已删除，不保留两套总控逻辑。

Interpreter 的固定代码生成累计目录，LLM 生成本轮带引用简报；Controller 保存和重用反馈。Scientific 收到完整目录正文，成对问答按归属入目录并可读取；底层登记表仍是唯一来源。它不拥有 Run/Session，也不调度任务。目录版本不可变，当前指针保存在 Run；失败尝试与原证据沿用原 ID。详见[反向交接契约](../../docs/current/CONTRACTS.md#interpreter)。

## 最小使用方式

```python
controller = ResearchController(
    scientific_port=scientific,
    compiler=compiler,
    interpreter=interpreter,
    scheduler=WorkflowScheduler(bindings={...}, store=JsonRunStore("state")),
    registry=registry,
)
run = controller.create_run("run_demo", request)  # 唯一 production 入口
```

`ResearchController.create_run` 是唯一 production 入口：调用 Scientific Agent，接收 WorkRequest，由 WorkflowCompiler 产生 Proposal/Patch，再由 Scheduler 执行 Coding/Experiment 图。Scheduler 自身只执行确定性的 ready Task（`run_until_stable`），不再提供 `create_run`，也不决定 Run 完成（ADR-0011 §1）。
