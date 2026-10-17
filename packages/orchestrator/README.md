# orchestrator

ResAgent2 的顶层控制模块。

负责：

- ResearchRun；
- Scientific Session 引用、WorkRequest 与 WorkResult；
- WorkRequest → WorkflowProposal/Patch 的 WorkflowCompiler；
- 执行事实与原报告 → WorkRequest/Task 产物导航的 WorkInterpreter；
- Workflow validation/revision；
- Task/Attempt 状态；
- coding / experiment 模块路由；
- Module Port/Adapter；
- retry、Ask User 和 finish gate；
- ScientificCompletionValidator 与 deterministic final report renderer；
- 唯一产物登记表及由授权 Ref 派生的完整目录。

它不直接实现科学判断、代码修改和实验执行。普通调度由确定性代码完成；Scientific 明确生成 Tasks，WorkflowCompiler 的 LLM 只给原任务匹配注册 Agent，固定代码原文组装 Proposal/Patch。路由输出的解析或结构错误最多纠正一次，无额外语义复审调用。LLM 不直接修改状态。

## 当前已实现

- ResearchController 根据 ResearchRequest 创建 ResearchRun；已有 Run 中的工作请求再编译、接受为 WorkflowProposal/Patch；
- 按原始任务顺序稳定计算 ready Task 集合；
- WorkflowAgentKind → ModuleBinding → ModulePort 路由；
- Task/Attempt 状态机和自动 retry；
- 失败任务按既有策略自动重试；Scientific 可通过后续 WorkRequest 安排修复；
- PendingQuestion、UserAnswer 和 Session resume；
- AgentRequest 的 instruction 与 input_artifacts；未来输入用 output_name 显式绑定上游成功 Attempt 的唯一产物；
- 业务问答和操作批准直接持久保存；WorkResult 按真实任务和 Attempt 交付原报告与产物；
- dataset_catalog 及验收要求的冻结工件交接；
- ArtifactCandidate 的 workspace 边界检查、UUID 分配、hash 校验、冻结和 provenance 登记；
- 外部论文元信息和本地 PDF 导入，复用 Run 登记表和科研目录；paused 追加不回答或恢复 Run；
- revision-bound WorkflowPatch 和旧 revision 历史；
- finish gate：通过 ArtifactRegistry 检查冻结的 Run 级 `required_artifacts`，只接受本 Run 精确 `output_name` 对应的已登记产物；
- 内存 RunStore 和原子 JSON RunStore；Run 创建时固定授权，RunUsagePort 在模型发送前保存请求占用；
- WorkflowCompiler：`WorkflowCompiler` Protocol + `DeterministicWorkflowCompiler`（测试 fixture）+ `LLMWorkflowCompiler`（注入 `CompilerLLM`，只路由原 Tasks，最多一次格式纠正）。成功返回 CompilationResult，失败抛 CompilationError，两者报告本次调用消费，详见 [接口](../../docs/current/CONTRACTS.md#compiler)。

Scientific、Coding、Experiment 都以 `invoke(AgentRequest) -> AgentResult` 注入 ModulePort；orchestrator 不 import 具体 Agent。三个模块各有一种业务模式，返回 report 和 artifacts；控制动作携带已分配身份的问题或工作请求，直接保存，不创建控制工件。JSON Store 适合本地单进程恢复，不宣称支持并发写入或分布式事务。


Controller 创建 Run 时将 `ResearchRequest.required_artifacts` 冻结到 `conclusion_requirements`。最终缺失诊断为 `required_artifact_missing`，message 为 `required artifact was not produced`，subject 为要求的名称；不能完成 Run，保留已有证据。此要求不额外要求观察或引用，原有 `required_evidence_kinds` 单独检查。

Controller/Scheduler 给三个 Agent 和 Compiler 绑定同一请求用量和剩余期限；固定 Interpreter 不调用模型，工作交接仍受 Run 时间边界限制。AgentResult.llm_calls 只用于诊断，不重复扣费；预算耗尽与超时按对应错误终止。操作批准只供对应的原调用消费，不能改变 Run 权限，也不进入任务需求。详见[运行控制契约](../../docs/current/CONTRACTS.md#research-request)。

production composition root 走 `ResearchController`：`create_run(run_id, request)` 进入科学控制循环，`ScientificAgent` 提出 `WorkRequestDraft`，`WorkflowCompiler` 生成 Proposal/Patch，Scheduler 执行 Coding/Experiment 图，`WorkResult` 回传后形成最终 `ScientificOpinion` 并经 `ScientificCompletionValidator` 写 completed。旧 PlanningPort 路径已删除，不保留两套总控逻辑。


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

工件登记统一生成 `artifact_<32位UUID4十六进制>`；ID 表示身份，SHA256 单独校验内容。登记前把逻辑操作、ID 和预期快照写入 Run，冻结后登记 Ref，所属接收结果提交后清理未完成登记事实；中断恢复复用同一个 ID。当前 schema 拒绝恢复旧版 Run，原记录保留；版本规则见 [CONTRACTS](../../docs/current/CONTRACTS.md)。详见 [ID 契约](../../docs/current/CONTRACTS.md#artifacts)。

当前接入规则：文献和网页搜索只返回临时线索；fetch_literature(source)/web_fetch 显式保存材料，read_artifact 按需读取。工具回执只进原生历史，实际 Ref 增量由共享 Loop 摄入完整目录。工作结果按 WorkRequest → Task → 原报告和实际产物展开，不生成总体报告。业务问答直接保存并累计，操作批准续执行原调用。规则以 [CONTEXT](../../docs/current/CONTEXT.md) 和 [CONTRACTS](../../docs/current/CONTRACTS.md) 为准。
