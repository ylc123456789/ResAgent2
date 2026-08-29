# ADR-0010：语义草图 + 确定性物化 + 一次纠错重编译

**状态**：accepted

**日期**：2026-08-29

**取代**：ADR-0007 中“WorkflowCompiler 直接输出 WorkflowProposal / WorkflowPatch”的实现细节

## 背景

ADR-0007 已把 WorkflowProposal / WorkflowPatch 归入 ResAgent 内部，由 WorkflowCompiler 生成。但 Phase 7 的 repair 场景（场景 3）暴露了“让 LLM 直接输出完整执行图”的根因缺陷：

LLM 被要求一次性生成包含全局 TaskId、WorkRequestId、workflow revision、status、supersede/update 以及跨 WorkRequest 依赖的完整 `WorkflowProposal` / `WorkflowPatch`。这些字段里，真正需要语义判断的只有“做什么、任务之间有什么关系”；其余全部是运行时身份、作用域和状态，本应由代码决定。把两者塞进同一个输出，导致模型反复犯两类错误：

- **空图**：修复轮输出 `tasks=[]`，或输出不产生任何任务的图；
- **跨 WorkRequest 依赖 / 变更**：新增任务 `depends_on` 指向旧 WorkRequest 的失败 Task，或对旧 Task 做 supersede/update，使新任务永久 blocked。

这些错误被确定性 validator 正确拒绝（fail fast），但 Compiler 没有重试，于是整个 Run 直接 failed，而不是“报错 → 带反馈重编 → 恢复”。

本 ADR 借鉴 LLMCompiler 的 Planner/Executor 分层、LangGraph 的结构化语义计划、Magentic-One 的有界恢复，但不引入它们的框架和组件，只在现有 `orchestrator/compiler.py` 内收敛。

## 决策

### 1. LLM 只输出语义草图

Compiler LLM 的返回类型从 `WorkflowProposal | WorkflowPatch` 改为内部 `CompilationDraft`：

```python
class CompilationTaskDraft(BaseModel):
    key: str                 # 仅在本 Draft 内有效的局部标识，如 fix_code、rerun
    capability: Capability
    goal: str
    rationale: str
    depends_on: list[str] = []   # 只能引用本 Draft 的 key
    workspace_id: WorkspaceId | None = None
    inputs: CapabilityInput

class CompilationDraft(BaseModel):
    summary: str
    rationale: str
    tasks: list[CompilationTaskDraft]  # min_length=1
```

这两个类型**只定义在 `compiler.py` 内，不进公共 contracts，不加新包**。`depends_on` 的准确语义是“被引用任务必须成功完成后才能执行”，不是时间顺序或因果描述。

LLM **不再输出**：全局 TaskId、WorkRequestId、workflow revision、status、Attempt、旧 Task ID、supersede/update、全局依赖，以及单工作区情况下的 workspace_id。这些一律由代码生成。

### 2. 确定性物化负责所有运行时身份

新增纯函数 `_materialize_draft`，把草图变成合法的 `WorkflowProposal`（首轮）或只追加的 `WorkflowPatch`（修复轮），并依次检查：

1. 至少一个任务；
2. 任务数不超过剩余 Task 预算；
3. 局部 key 唯一；
4. 依赖只能引用本 Draft 的 key；
5. 局部依赖无环；
6. capability 已在注册表中声明；
7. capability 与 inputs discriminator 一致；
8. 解析 workspace：单一工作区自动填入，多个工作区必须显式选择，不得编造；
9. 确定性分配全局 TaskId（`task_<key>`），并检查不与既有图冲突；
10. 把局部依赖转换为全局 TaskId；
11. 统一绑定 `work_request_id = request.id`；
12. 生成最终契约对象。

`current is None` 时生成 `WorkflowProposal`；否则生成 `WorkflowPatch(add_tasks=[...], supersede_task_ids=[], pending_task_updates=[])`——production Compiler 永远只产生 append-only patch。

### 3. 一次有界纠错重编译

`LLMWorkflowCompiler.compile()` 最多调用两次 LLM。第一次草稿被 validator 拒绝（`ValidationError` 或 `CompilationError`）时，把**精确的拒绝原因**作为反馈注入 prompt，重新编译一次；第二次仍被拒绝才失败。

约束：

- 最多两次；
- 第二次必须携带第一次的精确校验错误；
- 不执行任何 Task 后再重编，不投票、不生成多候选、不无限反思；
- HTTP/超时重试与“输出纠错”分开；
- 最终仍失败时，WorkRequest 与 Run 按现有路径进入 failed，不扩展 ResearchRun schema。

### 4. 修复轮不向 LLM 暴露旧 Task ID

修复轮 Compiler 只被告知：这是追加工作、剩余 Task 预算、旧执行是不可修改历史、本轮只能生成新的局部任务。不再提供 `task_initial_run(failed, work_request=work_1)` 这类旧 Task 列表——WorkRequest 已包含 Scientific Agent 对失败的语义总结，旧 Task ID 对任务分解无帮助，只会诱导模型生成跨 WorkRequest 依赖。

### 5. 保留防御层

现有 `_reject_undeclared_capabilities`、空图检查、workspace 检查、跨 WorkRequest mutation 检查继续保留，并在物化后的输出上再跑一次；Scheduler 的同类检查（budget、Workflow Pydantic DAG、binding、跨 WorkRequest mutation）也保持不变。物化器防止错误进入调度器，调度器则不信任任何调用者。

## 为什么不用其它方案

- **不引入 LLMCompiler / LangGraph / Magentic-One 框架**：它们的价值在分层思想，不在组件本身；这里只需 2 个内部模型 + 1 个纯函数 + 1 个最多两次的循环。
- **不做多候选投票或反思**：成本/复杂度远超收益；一次精确反馈重编足以覆盖空图与跨请求依赖这两类可被 validator 精确描述的失败。
- **不新增 Agent / 服务 / 状态机 / 包**：问题在编译器输出契约与重试，不在控制流架构。

## 不变的原则

- Workflow 的接受、状态转换和 Artifact 登记仍由确定性代码决定；
- Scientific / Coding / Experiment Agent、Agentic Loop、Scheduler 状态机、ArtifactRegistry、Workspace/Environment、公共 schema 版本均不改；
- WorkflowProposal / WorkflowPatch 仍存在，仍是 typed boundary；
- LLM 不直接修改 RunStatus、TaskStatus 或历史 Attempt。

## 后果

正面：

- 空图和跨 WorkRequest 依赖从根源上被消除：LLM 只输出本轮的局部语义，代码保证身份和范围；
- 偶发的非法草稿通过一次反馈重编恢复，而不是让整个 Run 失败；
- Compiler 的输出契约变窄，更易测试（物化器是纯函数）；
- 全局 TaskId / WorkRequestId 的 traceability 成为代码保证，不依赖模型。

代价：

- Compiler 内部多一层“草图 → 物化”的转换；
- 需要一套针对草图与物化器的定向测试（重复 key、未知依赖、环、能力/inputs 不一致、预算、工作区、重试）；
- 保留的 `_reject_*` 与物化器存在一定冗余（这是刻意为之的 defense-in-depth）。

## 明确不做

- 不把 `CompilationDraft` 提升为公共 contract 或新包；
- 不做投票、树搜索、多候选或无限反思；
- 不为 Compiler 建立 Session 或持久化其 LLM 调用历史；
- 不修改 `llm_calls_used` 的统计语义（CONTRACTS 只统计 ScientificPort）；
- 不在 Phase 7 内继续扩大 Compiler 职责（不扫描源码、不指定文件、不生成验证命令）。

## 参考的设计原则

- [LLMCompiler](https://github.com/SqueezeAILab/LLMCompiler)：Planner（语义任务 DAG）与 Executor（确定性调度）分离；
- [LangGraph](https://langchain-ai.github.io/langgraph/)：结构化状态与任务图作为可校验的中间表示；
- [Magentic-One](https://www.microsoft.com/en-us/research/articles/magentic-one-a-generalist-multi-agent-system-for-solving-complex-tasks/)：有界恢复而非无限重试；
- ADR-0007：WorkflowCompiler 归 ResAgent 内部，Scientific Agent 只作科学判断。
