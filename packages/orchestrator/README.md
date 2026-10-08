# orchestrator

ResAgent2 的顶层控制模块。

负责：

- ResearchRun；
- Scientific Session 引用、WorkRequest 与 WorkOutcome；
- WorkRequest → WorkflowProposal/Patch 的 WorkflowCompiler；
- 执行事实与原报告 → 科研目录及本轮工作反馈的 WorkInterpreter；
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
- 外部论文元信息和本地 PDF 导入，复用 Run 登记表和科研目录；paused 追加不回答或恢复 Run；
- revision-bound WorkflowPatch 和旧 revision 历史；
- finish gate：通过 ArtifactRegistry 检查冻结的 Run 级 `required_artifacts`，只接受本 Run 精确 `output_name` 对应的已登记产物；
- 内存 RunStore 和原子 JSON RunStore；Run 创建时固定授权，RunUsagePort 在模型发送前保存请求占用；
- WorkflowCompiler：`WorkflowCompiler` Protocol + `DeterministicWorkflowCompiler`（测试 fixture）+ `LLMWorkflowCompiler`（注入 `CompilerLLM`，最多两版 draft）。成功返回 CompilationResult，失败抛 CompilationError，两者报告本次调用消费，详见 [接口](../../docs/current/CONTRACTS.md#compiler)。

Scientific、Coding、Experiment 都以 `invoke(AgentRequest) -> AgentResult` 注入 ModulePort；orchestrator 不 import 具体 Agent。三个模块各有一种业务模式，返回 report 和 artifacts，控制动作只引用结果工件。JSON Store 适合本地单进程恢复，不宣称支持并发写入或分布式事务。

Port 与原生 finalizer 属于可信进程内实现；LLM 不能自行提交执行、验证或观察记录。Controller/Scheduler 验证公开结果、身份、hash、归属及工件内容，不通过读取下游私有 Session 取证。当前 schema 为 24.0，旧 Run/Session 保留但不迁移或恢复。Scientific 将当前输入与工具产物投影到唯一完整 artifact_index，ResearchIndex 分组只引用其中的工件 ID。

Controller 创建 Run 时将 `ResearchRequest.required_artifacts` 冻结到 `conclusion_requirements`。最终缺失诊断为 `required_artifact_missing`，message 为 `required artifact was not produced`，subject 为要求的名称；不能完成 Run，保留已有证据。此要求不额外要求观察或引用，原有 `required_evidence_kinds` 单独检查。

Controller/Scheduler 给三个 Agent 和 Compiler 绑定同一请求用量和剩余期限；固定 Interpreter 不调用模型，工作交接仍受 Run 时间边界限制。AgentResult.llm_calls 只用于诊断，不重复扣费；预算耗尽与超时按对应错误终止。操作批准沿用公开问答工件，不能改变 Run 权限。详见[运行控制契约](../../docs/current/CONTRACTS.md#research-request)。

production composition root 走 `ResearchController`：`create_run(run_id, request)` 进入科学控制循环，`ScientificAgent` 提出 `WorkRequestDraft`，`WorkflowCompiler` 生成 Proposal/Patch，Scheduler 执行 Coding/Experiment 图，`WorkOutcome` 回传后形成最终 `ScientificOpinion` 并经 `ScientificCompletionValidator` 写 completed。旧 PlanningPort 路径已删除，不保留两套总控逻辑。

Interpreter 通过固定代码生成累计目录，并按原 WorkRequest 和任务身份组织最新尝试的已记录报告、实际状态、错误、累计警告和产物入口。`WorkInterpreter.interpret(record: WorkRecord) -> str` 的生产实现为 `DeterministicWorkInterpreter`，不读取实验正文重新解释。Controller 冻结 `WorkRecord` 和含 `report` 的 `WorkFeedback`，保存后按原恢复链复用。Scientific 收到完整目录及可按预算展开的工作报告；成对问答按归属入目录，底层登记表仍是唯一来源。Interpreter 不拥有 Run/Session，也不调度任务。目录版本不可变，当前指针保存在 Run；失败尝试与原证据沿用原 ID，后台组织和反馈展示不冒充工具访问记录，observed 不再是控制动作或完成的前置门槛；引用身份、授权和冻结 hash 检查继续生效。详见[反向交接契约](../../docs/current/CONTRACTS.md#interpreter)。

## 最小使用方式

```python
controller = ResearchController(
    scientific_port=scientific,
    compiler=compiler,
    interpreter=DeterministicWorkInterpreter(),
    scheduler=WorkflowScheduler(bindings={...}, store=JsonRunStore("state")),
    registry=registry,
)
run = controller.create_run("run_demo", request)  # 唯一 production 入口
```

`ResearchController.create_run` 是唯一 production 入口：调用 Scientific Agent，接收 WorkRequest，由 WorkflowCompiler 产生 Proposal/Patch，再由 Scheduler 执行 Coding/Experiment 图。Scheduler 自身只执行确定性的 ready Task（`run_until_stable`），不再提供 `create_run`，也不决定 Run 完成（ADR-0011 §1）。

工件登记统一生成 `artifact_<64位SHA256>`，在同一 Run 内唯一、稳定；任务槽位、导入去重、材料快照及最终报告的身份规则保持原职责。由同一个私有函数编码已有身份，不新增编号服务；ID 不依赖磁盘绝对路径。schema 23 拒绝恢复旧 Run，原记录保留。详见 [ID 契约](../../docs/current/CONTRACTS.md#artifacts)。
