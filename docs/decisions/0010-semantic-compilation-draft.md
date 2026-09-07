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
    depends_on: list[str] = []   # 只能引用本 Draft 的 key
    workspace_id: WorkspaceId | None = None
    constraints: list[str] = []
    inputs: CapabilityInput

class CompilationDraft(BaseModel):
    summary: str
    rationale: str
    tasks: list[CompilationTaskDraft]  # min_length=1
```

这两个类型**只定义在 `compiler.py` 内，不进公共 contracts，不加新包**。`depends_on` 的准确语义是“被引用任务必须成功完成后才能执行”，不是时间顺序或因果描述。

LLM **不再输出**：全局 TaskId、WorkRequestId、workflow revision、status、Attempt、旧 Task ID、supersede/update、全局依赖，以及单工作区情况下的 workspace_id。这些一律由代码生成。

LLM **也不输出代码细节**：具体文件路径（如 `models/selayer.py`）、函数位置、CLI 参数、验证命令。Compiler 没有读过工作区，不可能知道这些；它只描述“要实现/修复/运行什么”的语义目标。物化器对 `code_modify` 强制清空 `suggested_paths`（该公开字段保留给确实提供可信提示的调用方），Coding Agent 自己进入工作区决定“在哪里、怎么做”。

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
9. 确定性分配全局 TaskId（`task_<key>`）。跨 WorkRequest 复用同一 `key` 时，按 `request.id` 确定性加后缀消歧（`task_<key>_<N>`），**绝不因 key 复用而拒绝**——LLM 看不到旧 Task ID（§4），不能要求它避开旧 key；
10. 把局部依赖转换为全局 TaskId；
11. 统一绑定 `work_request_id = request.id`；
12. 生成最终契约对象。

`current is None` 时生成 `WorkflowProposal`；否则生成 `WorkflowPatch(add_tasks=[...])`——production Compiler 永远只产生 append-only patch。schema 3.0 已删除 `supersede_task_ids` 和 `pending_task_updates`。`code_modify` 任务的 `inputs.suggested_paths` 在物化时被强制清空（§1），Compiler 不会把它未读过的路径传给 Coding Agent。

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

### 5. 一次有界语义完整性审查

结构校验只能判断图“格式合法”，不能判断“有没有漏事”。例如 `experiment_run` 单独一张图格式完全合法，但对“先实现 SE，再做实验”这种请求，它在语义上不完整。

因此 Compiler 在结构校验通过后，再做**一次短小的语义审查**（evaluator-optimizer 压缩进 Compiler 内部，不是新 Agent、不加新模块）：

- 输入是 WorkRequest 的 objective/evidence/constraints、与 draft 阶段相同的注册能力说明，以及每项任务的 capability/goal/depends_on/constraints/inputs；inputs 使用物化器同一个纯函数投影，避免审查已经丢弃的路径猜测或误放的精确验收字段；不暴露全局执行身份；
- 审查完整任务语义，不要求 inputs/constraints 中已存在的要求重复写进 goal；同时检查 capability 是否承担了职责之外的工作，而不只是看目标是否声称能够完成；
- `code_modify` 的代码正确性验证不替代 `experiment_run` 的正式实验与指标/工件交付；任务预算紧张不扩大能力职责。职责规则和能力说明由同一提示构造函数提供给 draft/review，不新增审查 Agent；
- 一个草图只描述当前可执行的一轮；不得提前加入依赖本轮失败才需要的 diagnose/fix/rerun，失败事实返回 Scientific 后另建 WorkRequest；
- 输出只有 `CompilationReview(accepted: bool, issues: list[str])`；
- `accepted=false` 时，把遗漏前置条件或多余条件任务写入 `issues`，交给现有的“一次纠错重编译”；
- 第二次仍不完整才失败。

为什么用 LLM 审查：判断“是否漏了科学/代码前置条件”是语义问题，固定代码难以可靠判断；不能写 `if "implement" in request: require_code_modify()` 这类会无限膨胀的关键词规则。确定性代码检查结构，LLM 检查语义完整性，最多纠错一次。

### 6. Coding Agent 自主探索 + 确定性控制状态

Compiler 不越权决定代码细节（§1），Coding Agent 自己探索工作区、决定改哪里、怎么验证。但 Coding Agent 容易在大量 observation 中丢失“代码已改、还欠一次验证”这个关键义务，导致反复 list/read 而不验证。

因此给 Coding Agent 增加一个**由代码派生、每轮持续注入**的 `CodeControlState`（不放进跨模块 contracts）：

- `workspace_changed = edit_revision > verification_revision`；
- `verification_required = workspace_changed`；
- `environment_certified = environment_binding.certified`；
- `required_next_action`：未改→改代码；已改未审计→`audit_env`；已改已审计未验证→`run_verification`；最新修改已验证→`finish`。

这些值不由 LLM 填写，而是从 Git 编辑 revision、验证 revision、环境绑定状态派生，作为最高优先级 context 每轮注入。`CompletionCheck` 仍是最终硬 gate，不替 Agent 自动执行验证。这是把“修改后必须验证”从提示文本升级为确定性状态，不给 Coding 加固定工作流。

### 7. 边界检查（接口优化后）

Controller 在调用任何 Compiler 之前检查是否还有新任务名额。名额为零时，把当前 WorkRequest 和 Run 标为失败，保留 `budget_exhausted` 原因，既有任务/Attempt 不变，Compiler 调用数为零。不请求模型在“必须非空”和“最多零项”之间纠错。此检查位于已接受图的恢复分支之后：已经接受的任务占满预算仍可继续执行，不会被误拦截。正数名额不足以容纳必要任务时，仍由物化器的预算检查拒绝，不通过合并跨能力职责来凑数。

物化器检查 capability/workspace 声明并分配身份；图模型负责结构。非空及本轮依赖政策提取为 `workflow_validation.validate_workflow_candidate`，Compiler 在 review 前调用（拒绝可进入一次纠错），Scheduler 在接受前同样调用（也约束替代 Compiler）。删除 Compiler 内四个重复的 `_reject_*` 最终函数；Scheduler 仍核预算、revision、实际 binding/workspace 与完整图。复用判据实现，不删除接收边界。

## 为什么不用其它方案

- **不引入 LLMCompiler / LangGraph / Magentic-One 框架**：它们的价值在分层思想，不在组件本身；这里只需几个内部模型 + 一个纯函数物化器 + 有界的“草图 + 审查”循环。
- **不做多候选投票或无限反思**：成本/复杂度远超收益；结构拒绝与语义不完整共享一次纠错机会，总计最多两版 draft，各最多一次 review。
- **语义审查不是新 Agent**：它是 Compiler 内部的一次短 LLM 调用，输出只有 `accepted`/`issues`，不新增 Planner/Reviewer Agent、不加包、不改状态机。
- **Coding 控制状态不是固定工作流**：确定性代码只告诉 Coding 当前还有“必须验证最新修改”这一项责任，不限制它看哪些文件、怎么改、用什么验证命令。

## 不变的原则

- Workflow 的接受、状态转换和 Artifact 登记仍由确定性代码决定；
- Scientific / Experiment Agent、Agentic Loop、Scheduler 状态机、ArtifactRegistry、Workspace/Environment、公共 schema 版本均不改；Coding Agent 的自主性（自己探索、自己验证）和最终硬 gate 不变，只新增由确定性代码派生的控制状态；
- WorkflowProposal / WorkflowPatch 仍存在，仍是 typed boundary；
- LLM 不直接修改 RunStatus、TaskStatus 或历史 Attempt。

## 后果

正面：

- 空图和跨 WorkRequest 依赖从根源上被消除：LLM 只输出本轮的局部语义，代码保证身份和范围；
- 偶发的非法草稿通过一次反馈重编恢复，而不是让整个 Run 失败；
- Compiler 的输出契约变窄，更易测试（物化器是纯函数）；
- 全局 TaskId / WorkRequestId 的 traceability 成为代码保证，不依赖模型；
- “漏编前置任务”和能力越界可经语义审查反馈后重编；这仍依赖模型判断，不保证每次都识别或修正，第二版仍不合格则失败；
- Coding 的“修改后必须验证”从提示文本变成每轮注入的确定性状态，不再依赖模型临时记忆。

代价：

- Compiler 内部多一层“草图 → 物化”的转换；
- 每次编译多一次语义审查 LLM 调用（有界，最多两轮）；
- 需要一套针对草图、物化器与语义审查的定向测试；
- 同一判据在生成与接受两个边界调用；检查实现共享，不能只相信原生 Compiler。

## 明确不做

- 不把 `CompilationDraft` / `CompilationReview` 提升为公共 contract 或新包；
- 不做投票、树搜索、多候选或无限反思；
- 不为 Compiler 建立 Session 或持久化其 LLM 调用历史；
- 不另建预算系统；当前 Compiler 的成功和失败调用均进入 Run 总账（以当前 CONTRACTS 的计量约定为准）；
- 不让 Compiler 扫描源码、指定文件、决定验证命令或把工作区文件列表塞给它；
- 不新增 Planner/Reviewer Agent、不新增停滞检测器、不自动替 Coding 执行验证、不为 SE/CIFAR/train.py 写规则、不调大 Coding 步数或任务总预算。

## 参考的设计原则

- [LLMCompiler](https://github.com/SqueezeAILab/LLMCompiler)：Planner（语义任务 DAG）与 Executor（确定性调度）分离；
- [LangGraph](https://langchain-ai.github.io/langgraph/)：结构化状态与任务图作为可校验的中间表示；
- [Magentic-One](https://www.microsoft.com/en-us/research/articles/magentic-one-a-generalist-multi-agent-system-for-solving-complex-tasks/)：有界恢复而非无限重试；
- ADR-0007：WorkflowCompiler 归 ResAgent 内部，Scientific Agent 只作科学判断。
