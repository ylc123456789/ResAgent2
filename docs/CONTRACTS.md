# ResAgent2 跨模块契约

**状态**：wire schema 1.0 已由 `resagent2-contracts 0.1.0` 实现
**原则**：字段不仅说明格式，还必须说明用途、所有者和控制流语义。

## 1. 契约边界

模块之间只交换以下对象：

```text
ResearchRequest
WorkflowProposal / WorkflowPatch
WorkflowTask
ModuleTaskRequest
ModuleResult
ArtifactRef
Question / Answer
ScientificConclusion
```

禁止跨模块：

- 直接读取或修改另一模块的内部 state；
- 依赖另一模块的私有目录结构；
- 从 summary 文本猜状态；
- 把任意 dict 当作长期接口；
- 子 Agent 直接调用另一个子 Agent。

## 2. ID 命名空间

| ID | 含义 | 范围 |
|---|---|---|
| `run_id` | 一次顶层科研运行 | 全局唯一 |
| `workflow_revision` | Run 中任务图的版本 | Run 内单调递增 |
| `task_id` | Workflow 中一个完整工作单元 | Run 内唯一且稳定 |
| `attempt_number` | Task 的第几次真实调用 | Task 内从 1 递增 |
| `session_id` | 子 Agent 可恢复工作过程 | 模块内唯一 |
| `artifact_id` | 冻结输出身份 | Run 内唯一，不跨 Attempt 复用 |
| `question_id` | 一个等待用户回答的问题 | Run 内唯一 |

这些 ID 不能互换。`task_id` 不是 `session_id`，`attempt_number` 也不是 Agent 内部 step number。

wire 格式使用可读前缀实现运行时命名空间校验：

| 类型 | 格式示例 |
|---|---|
| RunId | `run_example` |
| TaskId | `task_plan` |
| SessionId | `session_coding_1` |
| ArtifactId | `artifact_metrics` |
| QuestionId | `question_dataset` |

前缀后的内容由字母或数字开始，只允许字母、数字、下划线和连字符。前缀是契约的一部分，不能靠字段名暗示类型。

## 3. ResearchRequest

表示用户已经确认要执行的研究目标。

```python
class ResearchRequest:
    goal: str
    hypothesis: str | None
    context: str
    constraints: list[str]
    input_artifacts: list[ArtifactRef]
    budget: RunBudget
```

| 字段 | 语义 |
|---|---|
| goal | 这次 Run 要解决的问题，不是执行步骤 |
| hypothesis | 需要证据支持或反对的命题；可为空 |
| context | 已确认背景，不包含未授权文件内容 |
| constraints | 整个 Run 必须遵守的限制 |
| input_artifacts | 用户或历史 Run 明确提供的冻结输入 |
| budget | Run 总体时间、调用、任务和资源边界 |

## 4. WorkflowProposal

Scientific Agent 对“应该做什么”的建议。它尚未成为可执行 Workflow。

```python
class WorkflowProposal:
    summary: str
    tasks: list[TaskProposal]
    questions: list[QuestionDraft]
    scientific_rationale: str
```

```python
class TaskProposal:
    id: str
    capability: Capability
    goal: str
    rationale: str
    depends_on: list[str]
    required: bool
    inputs: CapabilityInput
    success_criteria: list[SuccessCriterion]
```

| 字段 | 语义 |
|---|---|
| id | Proposal 内稳定逻辑 ID；校验后成为 task_id |
| capability | 完成任务所需能力，不是模块名 |
| goal | 任务完成后应得到的结果 |
| rationale | 为什么需要这个任务；不驱动状态机 |
| depends_on | 同一 Proposal 中前置 task ID |
| required | 是否属于当前 Run 的完成前置 |
| inputs | capability 对应的类型化输入；实现时不保留任意 dict |
| success_criteria | 可验证完成条件；自然语言不可判定项必须标记 |

Proposal 必须经过 ResAgent validator：

- ID 唯一；
- capability 已注册且 owner 唯一；
- depends_on 存在且图无环；
- capability 专有输入通过 schema；
- required 分支能够形成完成路径；
- 不包含路径、环境和状态等不属于 Scientific Agent 的字段。

## 5. Workflow 与 WorkflowTask

Workflow 是 ResAgent 接受并持久化的任务图。

```python
class Workflow:
    run_id: str
    revision: int
    tasks: list[WorkflowTask]
    created_from: str
```

```python
class WorkflowTask:
    id: str
    capability: Capability
    goal: str
    depends_on: list[str]
    required: bool
    inputs: CapabilityInput
    status: TaskStatus
    input_artifacts: list[str]
    success_criteria: list[SuccessCriterion]
    attempts: list[Attempt]
    warnings: list[str]
```

`WorkflowTask` 是唯一顶层任务模型。不再另外建立 AgentTask、ScientificAction、PlannedAction 等平行任务模型。

`inputs` 必须保留在持久化的 WorkflowTask 中，否则调度器无法从已接受的 Workflow 重建 ModuleTaskRequest。`capability` 必须与 `inputs.capability` 完全一致。

## 6. WorkflowPatch

运行中显式修改任务图的建议。

```python
class WorkflowPatch:
    based_on_revision: int
    reason: str
    add_tasks: list[TaskProposal]
    supersede_task_ids: list[str]
    pending_task_updates: list[PendingTaskUpdate]
```

规则：

- 只基于当前 revision 应用；
- 只允许修改 pending Task 的输入和依赖；
- completed/failed Attempt 历史不可修改；
- running Task 不可原地改输入；
- supersede 只影响尚未开始的任务；
- 每次成功应用后 revision +1，并保存旧 revision 的审计摘要。

## 7. Capability

第一阶段固定 capability：

| capability | owner | 意义 |
|---|---|---|
| `scientific_plan` | Scientific Agent | 从 ResearchRequest 生成 WorkflowProposal |
| `scientific_analyze` | Scientific Agent | 分析已有证据并形成 ScientificConclusion/后续建议 |
| `literature_search` | Scientific Agent | 检索和解释文献 |
| `code_understand` | Coding Agent | 只读代码问答与定位 |
| `code_modify` | Coding Agent | 修改代码并验证 |
| `experiment_prepare` | Experiment Agent | 准备并审计 repo/env，不运行主要实验 |
| `experiment_run` | Experiment Agent | 运行实验并收集证据 |
| `ask_user` | ResAgent | 获取阻塞继续执行所需的人类输入 |

新增 capability 必须在同一变更中定义：

- owner；
- request schema；
- result schema；
- side effects；
- permission policy；
- completion evidence；
- contract tests。

实现中的 `CapabilityDefinition` 保存上述 owner、request/result model、side effects、permission policy 和 completion evidence；`CapabilityRegistry` 禁止同一 capability 出现两次。

### CapabilityInput

`CapabilityInput` 是以 `capability` 为 discriminator 的联合类型，不接受任意 dict：

| capability | 输入模型 | 关键语义 |
|---|---|---|
| scientific_plan | ScientificPlanInput | 包含用户确认的 ResearchRequest |
| scientific_analyze | ScientificAnalyzeInput | 科学问题和已登记证据 ID |
| literature_search | LiteratureSearchInput | 有界检索 query 和最大结果数 |
| code_understand | CodeUnderstandInput | 只读问题和 workspace 相对路径 |
| code_modify | CodeModifyInput | 修改要求、授权相对路径和验证命令 |
| experiment_prepare | ExperimentPrepareInput | repo 来源、输入 Artifact 和准备要求 |
| experiment_run | ExperimentRunInput | 实验说明、参数和预期证据 |
| ask_user | AskUserInput | 一个尚未持久化的 QuestionDraft |

## 8. ModuleTaskRequest

ResAgent 调用子 Agent 的统一外层请求。

```python
class ModuleTaskRequest:
    run_id: str
    task_id: str
    attempt_number: int
    capability: Capability
    goal: str
    inputs: CapabilityInput
    input_artifacts: list[ArtifactRef]
    constraints: list[str]
    budget: TaskBudget
    workspace: WorkspaceGrant | None
    parent_session_id: str | None
```

| 字段 | 语义 |
|---|---|
| run_id/task_id/attempt_number | provenance 和幂等边界 |
| capability | 选择 Agent profile 和 request schema |
| goal | 本次完整任务，不是单步 Tool 指令 |
| inputs | capability 专有的类型化参数 |
| input_artifacts | 已授权、可追溯的输入 |
| constraints | 本次 Task 的附加限制 |
| budget | 本次调用的 step、时间、token/usage 边界 |
| workspace | 允许读写的物理范围和访问模式 |
| parent_session_id | 仅用于显式 resume；普通 retry 默认为空 |

## 9. ModuleResult

所有子 Agent 通过同一外层结果返回状态，但 payload 使用各自类型。

```python
class ModuleResult[T]:
    status: ModuleStatus
    summary: str
    payload: T | None
    artifacts: list[ArtifactCandidate]
    session: SessionRef | None
    question: QuestionDraft | None
    error: ModuleError | None
    warnings: list[WarningRecord]
```

### ModuleStatus

| 值 | 准确含义 |
|---|---|
| completed | 确定性 finalizer 检查通过，核心输出完整 |
| completed_with_warnings | 核心输出完成，但存在明确未验证项 |
| failed | 本 Attempt 没有得到可接受核心结果；retry 可能有效 |
| blocked | 缺少外部前置条件，仅重复本 Attempt 不会改变 |
| needs_user_input | 唯一缺口是用户输入，并携带 question |

`summary` 只用于展示，禁止解析它判断状态。

字段组合由 schema 强制：

- `needs_user_input` 必须有 question，不能有 error；
- `failed` / `blocked` 必须有 error，不能有 question；
- `completed` 不能有 error、question 或 warnings；
- `completed_with_warnings` 至少有一条 WarningRecord；
- ArtifactCandidate 可以随失败结果返回用于诊断，但登记权仍属于 ResAgent。

### ModuleError

```python
class ModuleError:
    code: str
    message: str
    retryable: bool
    details: dict
```

`code` 使用稳定机器值，例如：

```text
invalid_input
permission_denied
tool_failed
timeout
budget_exhausted
contract_error
environment_unavailable
artifact_missing
```

## 10. Attempt

```python
class Attempt:
    number: int
    status: AttemptStatus
    started_at: datetime
    finished_at: datetime | None
    session: SessionRef | None
    artifact_ids: list[str]
    error: ModuleError | None
```

规则：

- 调用模块前创建；
- 开始后不可删除；
- retry 创建新 Attempt；
- resume 可继续同一 Session，但仍由 ResAgent 明确记录新的调用边界；
- 新 Attempt 不能覆盖旧 Artifact；
- Agent 内部 step 不进入顶层 Attempt 编号。

终态 Attempt 必须有 `finished_at`；`failed` / `blocked` 还必须有 error。Running Attempt 不能提前携带 `finished_at` 或 error，同一 WorkflowTask 的 Attempt number 必须从 1 连续递增。

## 11. ArtifactRef

```python
class ArtifactRef:
    id: str
    kind: str
    producer: str
    run_id: str
    task_id: str
    attempt_number: int
    uri: str
    sha256: str
    media_type: str
    summary: str
    metadata: dict
```

| 字段 | 语义 |
|---|---|
| id | 不可复用的冻结输出身份 |
| kind | 内容用途，如 code_change、experiment_result、scientific_decision |
| producer | 实际生成内容的模块 |
| run/task/attempt | provenance |
| uri | 稳定存储位置，不等于任意 workspace 路径 |
| sha256 | 内容完整性 |
| media_type | 机器读取格式 |
| summary | 人类预览，不是机器接口 |
| metadata | 扩展 provenance；禁止隐藏 status 等核心字段 |

ArtifactCandidate 只有经 ResAgent 校验存在性、路径边界、hash 和 Attempt 绑定后才能成为 ArtifactRef。

ArtifactCandidate 只包含 `kind/path/media_type/summary/metadata`。它故意不包含 id、run/task/attempt、URI 或 hash，避免子 Agent 自行伪造已登记 provenance。

## 12. WorkspaceGrant

```python
class WorkspaceGrant:
    root: str
    mode: Literal["read_only", "read_write"]
    allowed_paths: list[str]
    denied_paths: list[str]
    source: WorkspaceSource
```

它表示授权，不表示 Artifact 或 repo identity。

所有路径必须 resolve 后检查；禁止依赖当前工作目录、HOME 或未经校验的 symlink。

契约层只接受相对于 root 的 `allowed_paths` / `denied_paths`，并拒绝绝对路径和 `..`。真正的 resolve、symlink 和物理边界检查属于后续 runtime。

## 13. Question 与 Answer

```python
class QuestionDraft:
    text: str
    requested_fields: list[str]
    reason: str
```

```python
class PendingQuestion:
    id: str
    run_id: str
    task_id: str | None
    text: str
    requested_fields: list[str]
    created_at: datetime
```

```python
class UserAnswer:
    question_id: str
    values: dict[str, str]
    answered_at: datetime
```

子 Agent 只生成 QuestionDraft。ResAgent 负责持久化 PendingQuestion、暂停 Run、验证 Answer 并恢复。

## 14. Agent 内部动作

共享 Agentic Loop 使用统一外壳：

```python
class AgentAction:
    tool: str
    arguments: dict
    reasoning_summary: str
```

`tool` 必须来自 AgentDefinition 的允许 Tool 集合。`arguments` 必须通过该 Tool 的 schema，不能直接作为 shell 字符串逃逸。

结束使用显式候选：

```python
class FinishCandidate[T]:
    proposed_status: str
    result: T
    artifact_paths: list[str]
    unresolved_items: list[str]
```

最终 ModuleResult 由模块的确定性 completion check 生成，不直接信任 `proposed_status`。

## 15. Agent Profile

```python
class AgentDefinition[StateT, ActionT, ResultT]:
    name: str
    system_prompt: PromptProvider
    tools: list[Tool]
    context_builder: ContextBuilder[StateT]
    action_type: type[ActionT]
    result_type: type[ResultT]
    permission_policy: PermissionPolicy
    completion_check: CompletionCheck[StateT, ResultT]
```

三个 Agent 共享 Loop，不共享 system prompt、领域 state 和 completion check。

## 16. SessionRef

```python
class SessionRef:
    id: str
    module: str
    state_uri: str
    status: str
    created_at: datetime
    updated_at: datetime
```

Session 由子 Agent 写，ResAgent 只保存引用。Retry 和 Resume 语义：

- retry：同一 Task 的新 Attempt，默认新 Session；
- resume：用户或策略明确要求在原现场继续，使用 parent_session_id；
- repair：新的 WorkflowTask，不是 retry 或 resume。

## 17. 状态映射

| ModuleStatus | TaskStatus | Run 行为 |
|---|---|---|
| completed | completed | 继续依赖图 |
| completed_with_warnings | completed + warnings | 继续，最终报告显示警告 |
| failed | failed，或 retry 后 pending | 按 RetryPolicy |
| blocked | blocked | 创建显式 recovery/replan 或失败 |
| needs_user_input | needs_user_input | 保存问题并 paused |

科学结论状态与上述运行状态独立：一个执行成功的分析可以得出“不支持假设”的结论。

## 18. 契约版本

- 初始阶段不维护旧格式兼容层；
- 公共类型进入实现后增加 `schema_version`；
- 改变字段语义必须更新本文件、迁移说明和 contract tests；
- 只增加可选字段可以小版本演进；
- 删除/改义字段需要显式版本升级；
- 不允许通过 metadata 长期绕过正式 schema。

当前规则：

- 所有公共 BaseModel 都显式序列化 `schema_version: "1.0"`；
- 模型拒绝未知字段，防止拼写错误或未评审字段被静默吞掉；
- Python 包版本和 wire schema 版本分别演进，二者不能混为一谈。

## 19. 已实现公共类型清单

本节是代码导出与文档覆盖的核对表。稳定 Python 导入路径为 `resagent2_contracts`。

| 类别 | 公共类型 |
|---|---|
| 版本与 ID | SCHEMA_VERSION、RunId、TaskId、SessionId、ArtifactId、QuestionId |
| 路由与状态 | Capability、AgentOwner、RunStatus、TaskStatus、AttemptStatus、ModuleStatus |
| 错误与授权枚举 | ErrorCode、WorkspaceMode、WorkspaceSource、SessionStatus |
| 科学与验证枚举 | VerificationMode、ScientificVerdict |
| 通用结果 | ModuleError、WarningRecord、SessionRef |
| 预算与入口 | RunBudget、TaskBudget、ResearchRequest |
| 人机交互 | QuestionDraft、PendingQuestion、UserAnswer |
| 证据 | ArtifactCandidate、ArtifactRef |
| capability 输入 | ScientificPlanInput、ScientificAnalyzeInput、LiteratureSearchInput、CodeUnderstandInput、CodeModifyInput、ExperimentPrepareInput、ExperimentRunInput、AskUserInput、CapabilityInput |
| 工作流 | SuccessCriterion、TaskProposal、WorkflowProposal、Attempt、WorkflowTask、Workflow、PendingTaskUpdate、WorkflowPatch |
| 模块边界 | WorkspaceGrant、ModuleTaskRequest、ModuleResult |
| 注册表与结论 | CapabilityDefinition、CapabilityRegistry、ScientificConclusion |
