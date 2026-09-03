# ResAgent2 跨模块契约

**文档角色**：跨模块 wire 对象的字段、类型、组合约束和版本的唯一事实来源。

**语义上级**：`ARCHITECTURE.md`；本文件不得改变其中的模块职责和控制流。

**当前实现**：`resagent2-contracts 0.1.0`，wire schema `3.0`（`SCHEMA_VERSION="3.0"`，Stabilization 3.0 / ADR-0011 起）。schema 1.0/1.1/2.0 的历史演进记录在 `DEVELOPMENT_PLAN.md`、`docs/decisions/` 与 `docs/reviews/`，本文件只描述当前 3.0。

## 1. 使用规则

本文件回答「模块之间传什么、字段准确表示什么」。

- 架构概念和谁调用谁，以 `ARCHITECTURE.md` 为准；
- Python 字段必须与 `packages/contracts/src/resagent2_contracts/models.py` 一致；
- 代码与本文字段不一致时，视为 contract bug；
- 本文写了目标语义但代码尚未强制时，必须明确标为「未实现约束」；
- runtime 内部的 `AgentDefinition`、`AgentAction`、`FinishCandidate`、Tool 和 Context 类型不属于跨模块 wire contract，不在本文件定义；Scientific 的 `ReadArtifactTool` / `LiteratureSearchTool` 的 Tool 形状属于 capabilities 实现，只在 §16 锁定跨边界语义，不加入 contracts 公共导出。

所有公共模型：

- 继承严格 `ContractModel`（`extra="forbid"`）；
- 序列化 `schema_version: "3.0"`（`ContractModel.schema_version: Literal["3.0"]`）；
- 以下示意代码省略每个模型继承得到的 `schema_version`，但 wire 数据不能省略其版本语义。

## 2. 跨模块对象范围

| 边界 | 请求 | 响应/状态 |
|---|---|---|
| 用户 → ResAgent | ResearchRequest、UserAnswer | PendingQuestion、`final_report` ArtifactRef |
| ResAgent → Scientific Agent | ScientificTurnRequest | ScientificTurnResult |
| Scheduler → 专业模块 | ModuleTaskRequest | ModuleResult |
| 专业模块 → Artifact Registry | ArtifactCandidate | ArtifactRef |
| ResAgent 持久化 | Workflow、WorkflowTask、Attempt、PendingQuestion | ResearchRun 属于 orchestrator 内部模型 |

ResAgent ↔ Scientific Agent 使用 `ScientificTurnRequest` / `ScientificTurnResult`；Scientific Agent 只提出 `WorkRequestDraft`，`WorkflowProposal`/`WorkflowPatch` 由 ResAgent 内部的 WorkflowCompiler 产生。禁止跨模块读取另一个模块的内部 Session state、私有目录或 prompt；禁止从 summary 文本推断机器状态；禁止把任意 dict 作为长期接口。

## 3. ID 命名空间

| 类型 | 格式示例 | 范围 |
|---|---|---|
| RunId | `run_example` | 全局唯一 |
| TaskId | `task_experiment` | Run 内唯一且跨 revision 稳定 |
| SessionId | `session_coding_1` | 子模块内唯一 |
| ArtifactId | `artifact_metrics` | Run 内唯一，不跨 Attempt 复用 |
| QuestionId | `question_dataset` | Run 内唯一 |
| WorkRequestId | `work_x` | Run 内唯一 |
| WorkspaceId | `ws_main` | Run 内唯一 |

前缀后由字母或数字开头，只允许字母、数字、下划线和连字符，总长度由实现约束限制。ID 不能互换：TaskId 不是 SessionId，Attempt number 也不是 Agent step number。

## 4. 状态与所有权

### 4.1 RunStatus

```text
pending | running | paused | completed | failed
```

由 ResAgent 写入。planning、replanning、interrupted 不是 RunStatus。

### 4.2 TaskStatus

```text
pending | running | completed | failed | blocked | needs_user_input
```

由 Scheduler 写入。

### 4.3 AttemptStatus 与 ModuleStatus

```python
class ModuleStatus(StrEnum):
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_USER_INPUT = "needs_user_input"
    REQUEST_WORK = "request_work"
```

ModuleStatus 的六个结果值中，前五个是 AttemptStatus 的子集；`request_work` 只由 ScientificPort 产生，不是 Attempt 状态。AttemptStatus 另外包含 `running`（七个值）。ModuleStatus 由模块返回；AttemptStatus 由 Scheduler 根据已校验的 ModuleResult 记录。

## 5. ResearchRequest

```python
class ResearchRequest:
    goal: NonEmptyStr
    hypothesis: NonEmptyStr | None = None
    context: str = ""
    constraints: list[NonEmptyStr] = []
    input_artifacts: list[ArtifactImport] = []
    dataset_refs: list[DatasetRef] = []
    budget: RunBudget
```

| 字段 | 语义 |
|---|---|
| goal | Run 要解决的问题，不是执行步骤 |
| hypothesis | 要被证据支持或反对的命题；可为空 |
| context | 已确认背景，不包含未授权文件内容 |
| constraints | 整个 Run 必须遵守的限制 |
| input_artifacts | 用户提供的**最小输入**（`ArtifactImport`），由 Controller 创建 Run 时验证本地 URI、冻结复制、校验 hash 并生成 `orchestrator/import` ArtifactRef |
| dataset_refs | 整个 Run 的**唯一**数据集注册表（§15） |
| budget | max_tasks、max_attempts_per_task、max_llm_calls、timeout_seconds |

## 6. Workflow 执行图契约

```python
class TaskProposal:
    id: TaskId
    work_request_id: WorkRequestId
    capability: Capability
    goal: NonEmptyStr
    depends_on: list[TaskId] = []
    workspace_id: WorkspaceId | None = None
    constraints: list[NonEmptyStr] = []
    inputs: CapabilityInput

class WorkflowProposal:
    work_request_id: WorkRequestId
    summary: NonEmptyStr
    tasks: list[TaskProposal]
    compilation_rationale: NonEmptyStr

class WorkflowPatch:
    work_request_id: WorkRequestId
    based_on_revision: int
    reason: NonEmptyStr
    add_tasks: list[TaskProposal] = []

class Workflow:
    run_id: RunId
    revision: int
    tasks: list[WorkflowTask]
    created_from: WorkRequestId

class WorkflowTask:
    id: TaskId
    work_request_id: WorkRequestId
    capability: Capability
    goal: NonEmptyStr
    inputs: CapabilityInput
    depends_on: list[TaskId] = []
    workspace_id: WorkspaceId | None = None
    constraints: list[NonEmptyStr] = []
    status: TaskStatus = TaskStatus.PENDING
    input_artifacts: list[ArtifactId] = []
    attempts: list[Attempt] = []
    warnings: list[WarningRecord] = []
```

- `WorkflowProposal` 是 Compiler 产生的初始图候选；`WorkflowPatch` 是**只追加**的修订（schema 3.0 删除 `supersede_task_ids`/`pending_task_updates`/`PendingTaskUpdate`，修复模型是「新 WorkRequest 增加新 Task、保留旧历史」，见 ADR-0011 §5）。
- `TaskProposal`/`WorkflowTask` 不再有 `required`、`rationale` 或 `success_criteria`；编译理由只在 proposal/patch 级保留为 `compilation_rationale`。
- `capability` 必须与 discriminated `inputs.capability` 一致；`depends_on` 只能引用同图中的 TaskId，图必须无环；Attempt number 必须从 1 连续递增；Task 的 `work_request_id` 必须等于所属 Proposal/Patch 的 `work_request_id`。

## 7. Capability 与路由

顶层 WorkflowTask 只保留真正由 Scheduler 执行的能力：

| capability | owner | 说明 |
|---|---|---|
| code_understand | Coding | 授权范围内只读理解代码 |
| code_modify | Coding | 授权范围内修改并验证代码 |
| experiment_run | Experiment | 准备环境、执行实验、冻结结果证据 |

Literature Search 是 Scientific Agent 的 Tool；ask-user 是 control signal；实验准备属于 `experiment_run` 内部流程。这些都不是顶层 task capability。

`CapabilityDefinition` / `CapabilityRegistry` 描述 owner、request/result model、side effects、permission policy 和 completion evidence。Registry 拒绝同一 capability 出现两次，从而保证每个 capability 恰有一个 owner；同一个 owner 可以拥有多个不同 capability。

## 8. ModuleTaskRequest

```python
class ModuleTaskRequest:
    run_id: RunId
    task_id: TaskId
    attempt_number: int
    capability: Capability
    goal: NonEmptyStr
    inputs: CapabilityInput
    input_artifacts: list[ArtifactRef] = []
    dataset_refs: list[DatasetRef] = []
    constraints: list[NonEmptyStr] = []
    answers: list[UserAnswer] = []
    budget: TaskBudget
    workspace: WorkspaceGrant | None = None
    workspace_id: WorkspaceId | None = None
    workspace_spec: WorkspaceSpec | None = None
    environment_spec: EnvironmentSpec = EnvironmentSpec()
    output_dir: NonEmptyStr | None = None
    parent_session_id: SessionId | None = None
```

| 字段 | 控制流语义 |
|---|---|
| run/task/attempt | provenance 与幂等边界 |
| capability + inputs | 选择模块 profile；二者 discriminator 必须一致 |
| input_artifacts | 已登记且已授权给本 Task 的输入证据 |
| dataset_refs | 本 Task 从 Run 唯一数据集注册表继承的数据集子集（Scheduler 确定性写入，非 LLM 生成） |
| answers | 只包含属于本 Task 的已持久化回答 |
| workspace / workspace_id / workspace_spec | 此 Attempt 的物理授权范围、逻辑工作区 id、来源声明（Agent 在 loop 前确定性 materialize） |
| environment_spec | 上游声明的环境硬约束（`EnvironmentSpec.python_version`） |
| output_dir | code_modify / experiment_run 的输出目录 |
| parent_session_id | 仅显式 resume 使用；普通 retry 为空 |

`workspace_spec` 存在时要求 `workspace` 非空、`workspace_id` 与 `workspace_spec.workspace_id` 一致、`workspace.source` 与 `workspace_spec.source_kind` 一致。ask-user 后的 resume 产生 `parent_session_id`；runtime AgentLoop 在 resume 时加载该 Session 并校验 run/task/agent/owner/paused 一致。普通 retry 不复用 Session。

## 9. ModuleResult

```python
class ModuleResult[PayloadT]:
    status: ModuleStatus
    summary: NonEmptyStr
    payload: PayloadT | None = None
    artifacts: list[ArtifactCandidate] = []
    session: SessionRef | None = None
    question: QuestionDraft | None = None
    request_work: JsonValue | None = None
    error: ModuleError | None = None
    warnings: list[WarningRecord] = []
    llm_calls: int = 0
```

组合约束：

- `needs_user_input`：必须有 question 和 paused session，不能有 error 或 request_work；
- `request_work`：必须有 request_work 和 paused session，不能有 error 或 question；
- `failed` / `blocked`：必须有 ModuleError，不能有 question 或 request_work；
- `completed`：不能有 error、question、request_work 或 warnings；
- `completed_with_warnings`：至少有一条 WarningRecord；
- ArtifactCandidate 可随失败结果作为诊断输出登记，但不能让失败状态变成功。

`summary` 只用于人类展示；`payload` 是 capability 专有数据。`llm_calls` 是本 Attempt 实际新增的 LLM 调用数（含 HTTP 重试），供 Run 使用账本累计。Scheduler 将通过校验的 payload 原样持久化到对应 Attempt，供审计和恢复后读取，但不解释其中的领域语义，也不从 payload 推断状态。

## 10. 领域 payload

```python
class VerificationResult:
    command: NonEmptyStr
    exit_code: int
    timed_out: bool = False
    stdout_path: NonEmptyStr
    stderr_path: NonEmptyStr
    duration_seconds: float

class CodeUnderstandResult:
    answer: NonEmptyStr
    evidence_files: list[str]          # min_length=1
    uncertainty: str = ""

class CodeModifyResult:
    changed_files: list[str]
    deleted_files: list[str] = []
    patch_path: NonEmptyStr
    verification_results: list[VerificationResult]   # min_length=1
    verification_passed: bool
    residual_risks: list[NonEmptyStr] = []

class ExperimentResult:
    metrics: dict[str, JsonValue] = {}
    evidence_files: list[str] = []
    repo_url: str = ""
    commit: str = ""
    env_id: NonEmptyStr
    delivery_issues: list[NonEmptyStr] = []
    residual_risks: list[NonEmptyStr] = []
```

- `CodeUnderstandResult.evidence_files` 至少包含一个实际通过 Coding read/search Tool 观察过的 workspace 相对路径。
- `CodeModifyResult` 要求 changed_files 与 deleted_files 至少一项非空且不重叠；`verification_results` 至少一条且 `verification_passed` 必须等于「所有 VerificationResult 均为 exit_code 0 且未 timeout」（model validator 强制，ADR-0011 §3）。`patch_path` 指向 Coding finalizer 通过 Git 能力生成的 Attempt patch。
- `ExperimentResult.metrics` 由 Experiment finalizer 从完整 JSON evidence 集合读取顶层数值字段得到，LLM 不能自证数字（ADR-0011 §5.2）。该集合包含 Agent 声明、且相对 WorkspaceSnapshot 基线在本 Attempt 改变的 evidence 文件，以及满足同一条件的 `expected_artifacts`；后者即使 Agent 漏报也会被自动补入。`evidence_files` 是这个完整集合中的 workspace 相对路径。`repo_url` + `commit` 是 repo identity；`env_id` 是 `run_id + workspace_id` 绑定的基础环境 id。`delivery_issues` 记录 `expected_metrics`/`expected_artifacts` 缺失项；非空时 finalizer 返回 completed_with_warnings（code=`delivery_not_met`）。
- `ExperimentRunInput`（§18）仍保留 `parameters`（实验配置参数），但 `ExperimentResult` 不再有 `parameters` 字段（删除，无 production 消费者）。

```python
class ModuleError:
    code: ErrorCode
    message: NonEmptyStr
    retryable: bool
    details: dict[str, JsonValue] = {}
```

ErrorCode 固定枚举：invalid_input、permission_denied、tool_failed、timeout、budget_exhausted、contract_error、environment_unavailable、artifact_missing、interrupted。`interrupted` 只由 ResAgent 恢复逻辑写入，表示 Attempt 已持久化为 running、但进程在 ModuleResult 写回前退出；它是 retryable failure，不是新的 TaskStatus。

## 11. Attempt 与 SessionRef

```python
class Attempt:
    number: int
    status: AttemptStatus
    started_at: datetime
    finished_at: datetime | None = None
    session: SessionRef | None = None
    artifact_ids: list[ArtifactId] = []
    error: ModuleError | None = None
    payload: JsonValue | None = None
    summary: NonEmptyStr | None = None

class SessionRef:
    id: SessionId
    module: AgentOwner
    state_uri: NonEmptyStr
    status: SessionStatus
    created_at: datetime
    updated_at: datetime
```

Attempt 属于 ResAgent 历史，Session 属于子 Agent。retry（failed/blocked 后重试）是同一 Task 的新 Attempt，默认新 Session；pause/resume 是**同一 Attempt** 的暂停与继续，不增加 Attempt number，复用 Session/output_dir/workspace snapshot（ADR-0011 §2）；repair 是新 WorkflowTask。进程中断不是第三种 AttemptStatus：恢复时把遗留 running Attempt 结算为 `failed + ErrorCode.interrupted + retryable=True`，再按原有 Attempt 预算决定 Task 是否回到 pending（ADR-0012）。

`running` 与 `needs_user_input` 都是非终态：不能有 finished_at/error；终态（completed/completed_with_warnings/failed/blocked）必须有 finished_at；failed/blocked 必须有 error；其他终态不能有 error。`payload` 是模块返回的能力专属结构化结果，随 Attempt 持久化，不被静默丢弃；失败/契约错误路径天然为 None。

## 12. Question 与 Answer

```python
class QuestionDraft:
    text: NonEmptyStr
    requested_fields: list[NonEmptyStr] = []
    reason: NonEmptyStr

class PendingQuestion:
    id: QuestionId
    run_id: RunId
    task_id: TaskId | None = None
    text: NonEmptyStr
    requested_fields: list[NonEmptyStr] = []
    created_at: datetime

class UserAnswer:
    question_id: QuestionId
    values: dict[NonEmptyStr, str]
    answered_at: datetime
```

子 Agent 只生成 QuestionDraft；ResAgent 分配 ID、持久化 PendingQuestion、暂停 Run、校验 Answer 并恢复。`reason` 是必填字段。

## 13. Artifact 契约

```python
class ArtifactCandidate:
    kind: NonEmptyStr
    path: str
    media_type: NonEmptyStr
    summary: NonEmptyStr
    metadata: dict[str, JsonValue] = {}
    content: str | None = None

class ArtifactRef:
    id: ArtifactId
    kind: NonEmptyStr
    producer: AgentOwner
    run_id: RunId
    task_id: TaskId | None = None
    attempt_number: int | None = None
    session_id: SessionId | None = None
    uri: NonEmptyStr
    sha256: str                          # ^[0-9a-f]{64}$
    media_type: NonEmptyStr
    summary: NonEmptyStr
    metadata: dict[str, JsonValue] = {}

class ArtifactImport:
    uri: NonEmptyStr
    kind: NonEmptyStr
    media_type: NonEmptyStr
    summary: NonEmptyStr
    expected_sha256: str | None = None
```

Candidate 的 path 必须是 workspace root 下无 `..` 的相对路径。Candidate 故意没有 id、URI、hash 或 provenance；这些只能由 ResAgent 登记时产生。`content` 只用于传递派生的小型文本 Artifact（如 patch）；普通大文件仍走 workspace path + ArtifactRegistry 冻结。

ArtifactRef 的 provenance 是互斥三态（model validator 强制）：

- **执行 Artifact**：producer 为 coding/experiment，`task_id` 与正整数 `attempt_number` 同时存在，`session_id` 为空；
- **Scientific Tool Artifact**：producer 为 scientific，`session_id` 存在，`task_id` 与 `attempt_number` 同时为空；
- **Orchestrator Artifact**：producer 为 orchestrator，三者都为空，且 `metadata.source_type` 必须是 `import` 或 `final_report`。

混合字段、缺半个 task/attempt、非正 attempt、scientific 带 task、orchestrator 带 session 等全部拒绝。所有 Artifact 仍必须有当前 run_id、Registry 计算的 sha256 和冻结 uri。

`ArtifactImport` 是用户提供的最小输入，不含 provenance 或 hash；Controller 验证本地 URI、冻结复制、校验 `expected_sha256` 后生成 `orchestrator/import` ArtifactRef。

## 14. Workspace 契约

```python
class WorkspaceGrant:
    root: NonEmptyStr
    mode: WorkspaceMode
    allowed_paths: list[str] = []
    denied_paths: list[str] = []
    source: WorkspaceSourceKind

class WorkspaceSpec:
    workspace_id: WorkspaceId
    source_kind: WorkspaceSourceKind
    location: str | None = None
    environment: EnvironmentSpec | None = None

class WorkspaceRecord:
    workspace_id: WorkspaceId
    root: NonEmptyStr
    source: WorkspaceSpec
    managed: bool = False

class WorkspaceDescriptor:
    workspace_id: WorkspaceId
    source_kind: WorkspaceSourceKind
    description: str = ""
```

`WorkspaceSourceKind`：GIT（clone 到受管目录）/ LOCAL（原地绑定，managed=False）/ COPY（复制已有本地 Git 工作树）/ GENERATED（创建空受管工作区）。

`WorkspaceSpec` 是逻辑来源声明，不保存物理路径；`environment` 是 workspace 级的环境约束（上游指定 Python 版本时为硬约束）。`WorkspaceRecord` 是解析后的记录，`managed` 由 source_kind 派生（非 LOCAL 为 True）。`WorkspaceDescriptor` 是 Compiler 可见的最小工作区摘要，不含物理路径。

capabilities 提供一个内部 `WorkspaceSnapshot`（Git workspace 用 `GitBaseline` 的 tree hash，非 Git workspace 用有界 file-hash fallback）表达 Attempt 起点；GitDiffTool、Coding finalizer、failed patch 与 Experiment evidence ownership 都消费同一个 snapshot，删除 HEAD-relative legacy diff API（ADR-0011 §4）。

## 15. Dataset 与环境

```python
class DatasetRef:
    dataset_id: NonEmptyStr
    relative_path: str

class EnvironmentSpec:
    python_version: str | None = None
```

`dataset_root`（`ResourceLayout.dataset_root`）永远表示「所有数据集的公共根目录」。`DatasetRef` 把 `relative_path` 解析到 `dataset_root` 下，拒绝 `..`/绝对路径逃逸，默认只读。`ResearchRequest.dataset_refs` 是**唯一**数据集注册表，Scheduler 经 `ModuleTaskRequest.dataset_refs` 传给 Experiment Agent；`ExperimentRunInput` 不再携带 `dataset_refs`。解析结果是 `{dataset_id, path, access="read_only"}` 列表，重复 `dataset_id` 拒绝；Experiment Agent 用 `RESAGENT2_DATASET_ROOT` / `RESAGENT2_DATASETS_JSON` 环境变量把映射交给脚本。

环境能力由 Coding 与 Experiment 共用（ADR-0009）：

- `EnvironmentSpec.python_version` 有值表示硬约束，Agent 不得静默覆盖；为空表示 Agent 依据项目自行判断；
- 环境归属 `run_id + workspace_id`：同 Run 同 Workspace 共用（Coding/Experiment 共用、Task 重试复用），不同 Workspace/Run 隔离；`env_id = resenv_<sha256(run_id + "\0" + workspace_id)[:12]>`；
- 三个共享 Tool（capabilities 的公开 Python API）：`prepare_environment` / `run_setup` / `audit_env`；任何被允许并开始执行的 `run_setup` 命令都使旧 audit 失效（`env_certified=False`），须重新 `audit_env`；
- Python 版本优先级、硬约束不可覆盖、每 Attempt 最多两次版本切换：见 ADR-0009。

## 16. 科学控制契约

### 16.1 ScientificAssessment 与 WorkRequest

```python
class ScientificAssessment:
    statement: NonEmptyStr
    evidence_artifact_ids: list[ArtifactId] = []
    limitations: list[NonEmptyStr] = []
    unresolved_questions: list[NonEmptyStr] = []

class WorkRequestDraft:
    objective: NonEmptyStr
    expected_evidence: list[NonEmptyStr]     # min_length=1
    constraints: list[NonEmptyStr] = []

class WorkRequestStatus(StrEnum):
    REQUESTED = "requested"
    COMPILING = "compiling"
    EXECUTING = "executing"
    STABLE = "stable"
    CONSUMED = "consumed"
    FAILED = "failed"

class WorkRequest:
    id: WorkRequestId
    run_id: RunId
    scientific_session_id: SessionId
    request: WorkRequestDraft
    status: WorkRequestStatus = WorkRequestStatus.REQUESTED
    workflow_revision: int | None = None
    outcome: WorkOutcome | None = None
    error: ModuleError | None = None
    created_at: datetime
    updated_at: datetime
```

`assessment.statement` 是当前科学观点，不是执行摘要；每次 `request_work` 都必须携带 assessment。`evidence_artifact_ids` 只能引用本 Run 已授权且 Scientific Session 确实通过 `read_artifact`/`literature_search` Tool 观察过的 Artifact。`WorkRequestDraft` 严禁包含 capability、owner、task_id、depends_on、workspace、path、env、retry、status 或 Attempt 字段；`expected_evidence` 至少一项。一个 Run 同时最多有一个 active WorkRequest（requested/compiling/executing/stable）。唯一合法转换是 `requested → compiling → executing → stable → consumed`，任一步可在不可恢复控制错误时进入 failed；Task failed/blocked 仍产生 stable WorkOutcome，不进入 failed。

### 16.2 WorkTaskOutcome 与 WorkOutcome

```python
class WorkTaskOutcome:
    task_id: TaskId
    status: Literal["completed", "failed", "blocked"]
    summary: NonEmptyStr
    artifact_ids: list[ArtifactId] = []
    error: ModuleError | None = None
    warnings: list[WarningRecord] = []

class WorkOutcome:
    work_request_id: WorkRequestId
    workflow_revision: int
    summary: NonEmptyStr
    tasks: list[WorkTaskOutcome]          # min_length=1
```

tasks 至少一项且 TaskId 不重复；failed/blocked 必须有 error；completed 不能有 error；artifact_ids 只能包含该 Task Attempt 已登记的 Artifact。WorkOutcome 是执行事实摘要，不判断实验是否支持假设；即使含 failed/blocked Task，也返回 Scientific Agent，由它决定下一步。

### 16.3 ScientificOpinion

```python
class ScientificOpinion:
    verdict: ScientificVerdict
    statement: NonEmptyStr
    evidence_artifact_ids: list[ArtifactId] = []
    limitations: list[NonEmptyStr] = []
    unresolved_questions: list[NonEmptyStr] = []
    recommended_next_steps: list[NonEmptyStr] = []
```

verdict 与 RunStatus 独立：`inconclusive` 可以是一个成功完成的科学闭环。`supports`/`refutes` 必须至少引用一个 ArtifactId。failed/blocked Task 是 Controller/Validator 的执行账务：最终 gate 直接从 Run 对账并把它们确定性写进 final report；只要仍有此类 Task，ScientificOpinion 的 `limitations` 必须非空，说明其对科学结论的影响。Scientific 不回传内部 TaskId。

### 16.4 ScientificTurnRequest 与 ScientificTurnResult

```python
class ScientificTurnRequest:
    run_id: RunId
    research: ResearchRequest
    authorized_artifacts: list[ArtifactRef] = []
    work_outcome: WorkOutcome | None = None
    previous_work_request: WorkRequestDraft | None = None
    unresolved_task_outcomes: list[WorkTaskOutcome] = []
    answers: list[UserAnswer] = []
    budget: TaskBudget
    parent_session_id: SessionId | None = None

class ScientificWorkRequestResult:
    status: Literal["request_work"]
    assessment: ScientificAssessment
    work_request: WorkRequestDraft
    session: SessionRef
    observed_artifact_ids: list[ArtifactId] = []
    llm_calls: int = 0

class ScientificQuestionResult:
    status: Literal["needs_user_input"]
    assessment: ScientificAssessment
    question: QuestionDraft
    session: SessionRef
    observed_artifact_ids: list[ArtifactId] = []
    llm_calls: int = 0

class ScientificCompletedResult:
    status: Literal["completed"]
    opinion: ScientificOpinion
    session: SessionRef
    observed_artifact_ids: list[ArtifactId] = []
    llm_calls: int = 0

class ScientificFailedResult:
    status: Literal["failed"]
    error: ModuleError
    session: SessionRef | None = None
    observed_artifact_ids: list[ArtifactId] = []
    llm_calls: int = 0

ScientificTurnResult = Union[
    ScientificWorkRequestResult,
    ScientificQuestionResult,
    ScientificCompletedResult,
    ScientificFailedResult,
]  # discriminator="status"
```

组合约束：

- 首次调用 `parent_session_id=None`、`work_outcome=None`、`answers=[]`；恢复调用必须有 `parent_session_id`，且 work_outcome 与 answers 至多一种非空；`previous_work_request` 与 `work_outcome` 必须成对（model validator 强制）；
- request_work / needs_user_input 必须有 assessment 和 paused session；completed 必须有 opinion 和 completed session；failed 必须有 error，不能附带 opinion/work_request/question，failed 无 session 时 observed_artifact_ids 必须为空；
- `observed_artifact_ids` 由 ScientificPort finalizer 从整个 Session 的成功 Tool observation 累积派生，不能来自 LLM action payload；assessment/opinion 的 evidence_artifact_ids 必须是 observed_artifact_ids 的子集；
- `llm_calls` 是本轮 ScientificPort 实际新增的 LLM 调用数（非负整数）。

ScientificPort 是唯一 Scientific Agent 边界。work_outcome 按 work_request_id、answers 按 question_id 幂等：重复投递返回已持久化结果，不能重复追加 observation 或重复调用 LLM。

### 16.5 WorkflowCompiler 边界

WorkflowCompiler 的输入是 `WorkRequest`、CapabilityRegistry、Run 约束和当前 Workflow 摘要；输出只有 `WorkflowProposal` 或 `WorkflowPatch`。production `LLMWorkflowCompiler` 不让 LLM 直接输出 Proposal/Patch：LLM 只输出 orchestrator 内部的 `CompilationDraft`（顶层 summary/rationale + 每任务 key/capability/goal/depends_on/workspace_id/inputs），再由确定性 `_materialize_draft` 分配全局 TaskId、绑定 `work_request_id`、解析 workspace、转换局部依赖并产出 Proposal（首轮）或只追加 Patch（修复轮）。每个草图只表示当前可执行的一轮，不预编译依赖本轮失败才需要的条件任务；失败经 WorkOutcome 返回 Scientific 后另建修复 WorkRequest。结构校验通过后再做一次语义审查（`CompilationReview`），同时检查遗漏前置条件和多余条件任务；结构拒绝与语义拒绝各自最多带精确反馈重编译一次，两次都失败才把 WorkRequest/Run 置为 failed。Compiler 不产生 §7 以外的 capability；validator 检查 run、revision、work_request_id、DAG、能力注册表、预算和 inputs discriminator。

### 16.6 ScientificCompletionValidator 与 final report

Validator 是 orchestrator 内部纯验证器，不调用 LLM，不读取 Session 私有 event。输入同一个不可变 ResearchRun snapshot 和 ScientificCompletedResult；输出结构化 violation（invalid_session / active_control_state / invalid_opinion / unknown_evidence / unobserved_evidence / unacknowledged_task / missing_limitations / inconsistent_task_result）。验证顺序固定为：completed result/session/run 绑定正确 → 无 active WorkRequest/PendingQuestion/**pending、running 或 needs_user_input Task** → opinion 通过组合约束 → 每个 evidence Artifact 属于本 Run、Registry 可查、且同时出现在 result.observed_artifact_ids 与 run 的已复核 trace → 所有 failed/blocked Task 被 acknowledged 且存在 limitations → 每个 completed Task 都有合法终态 Attempt、无 error、artifact producer 与 binding owner 一致 → 不接受未知/重复/跨 Run 的 ID。

通过时 Validator 产出 `FinalReportData`（run_id、goal、opinion、evidence、execution_issues），纯 renderer 只消费 FinalReportData 生成 `kind=final_report`、`media_type=text/markdown` 的 ArtifactCandidate。RunStatus 只有在验证、渲染、Artifact 登记和 Run 字段写入全部成功后才改 completed。Validator 不判断 statement 是否科学正确。

## 17. 状态映射

| ModuleStatus | Scheduler 写入的 TaskStatus | Run 行为 |
|---|---|---|
| completed | completed | 继续依赖图；其 Artifact 可自动传给依赖任务 |
| completed_with_warnings | completed，并追加 warnings | 继续依赖图；warnings 被持久化 |
| failed | retryable 且预算允许时 pending，否则 failed | 可重试项重新进入调度；稳定后进入 WorkOutcome |
| blocked | blocked | ResAgent 只能通过显式 WorkflowPatch/recovery 和 retry 恢复 |
| needs_user_input | needs_user_input | ResAgent 保存 PendingQuestion，Run 置为 paused |

`request_work` 不是 Task 状态，只由 ScientificPort 产生，映射到「持久化 assessment + 创建 WorkRequest」：

| ScientificTurnResult.status | SessionStatus | ResearchController 行为 | RunStatus |
|---|---|---|---|
| request_work | paused | 复核 observed trace，持久化 assessment，创建 requested WorkRequest | running |
| needs_user_input | paused | 复核 observed trace，持久化 assessment/PendingQuestion | paused |
| completed | completed | 复核并合并 observed trace，调用 ScientificCompletionValidator；通过后保存 opinion/报告 | completed |
| failed | failed 或空 | 保存 ModuleError；AgentLoop 内部可恢复机会已耗尽 | failed |

completed 若未通过 Validator，不得写 Run completed；这种不一致属于 `contract_error`，Run failed 并保留两层验证证据。WorkRequest 状态不映射为新的 RunStatus：requested/compiling/executing/stable/consumed 期间 Run 都是 running。

## 18. schema 版本规则

- Python 包版本和 wire schema 版本独立演进；
- 增加可选字段至少需要 schema 小版本和迁移说明；
- 删除字段、改字段含义或改变必填性需要不兼容版本；
- 每个版本必须有 round-trip 和非法组合 contract tests；
- metadata 不得长期承载本应成为正式字段的状态；
- schema 版本策略发生改变时必须先写 ADR。

当前 `3.0`（Stabilization 3.0 / ADR-0011）相对 2.0 的改动：删除 `TaskProposal/WorkflowTask.required`、per-task `rationale`、`PendingTaskUpdate`/`supersede_task_ids`/`pending_task_updates`、`ExperimentResult.parameters`、`ExperimentRunInput.dataset_refs`，并收敛 `WorkspaceRecord`（去 `initial_commit`）；新增 `ArtifactImport`、`WorkspaceSpec.environment`、`ModuleTaskRequest.dataset_refs`、`ModuleResult.llm_calls`、`Attempt.summary`，并把 `CodeModifyResult.verification_results` 收紧为 min_length=1。`success_criteria`/`evidence_key` 及旧 scientific/planning task capability 已在 2.0 删除。1.0/1.1/2.0 演进见 `DEVELOPMENT_PLAN.md` 与 ADR-0007/0011。

## 19. 运行时反馈与连续失败保护

`ToolObservation.ok` 是机器可读的成功标志：成功读取/命令为 True，失败命令（非零退出）、参数拒绝、路径缺失等可恢复失败为 False。下游不得靠解析 `summary` 文本判断失败。AgentLoop 的反馈语义：

- 可恢复失败落为持久 `runtime_feedback`（`ok=False`），并在后续每轮作为最高优先级 required 上下文注入；普通 observation 不覆盖它；
- `recent_observations` 是有界最近历史（默认 6 条），用 head+tail 截断序列化值，保证末尾错误字段（如 `stderr_tail`）不丢；
- Agent 需要保留文件正文等领域观察时，统一使用 runtime 的 `recent_tool_text_values` 或 `recent_tool_snippets`，按整个 section 的总字符数限长并作为 required context；`recent_tool_snippets` 以 (path, start_line, end_line) 为片段身份、最新片段优先完整装入（仅截断最后一段），供 Coding 保留精确代码片段；不得给每个文件分别套上限后生成可能被整体省略的超大 section；
- provider transport retry 每次真实 HTTP 尝试都计入 `llm_calls`；AgentLoop/Compiler 必须把剩余调用数传给共享 client，client 的下一次尝试数不得超过该值；
- 连续失败计数：成功的非 finish 工具重置；`ok=False` 累加；completion check 拒绝的 finish 也累加；连续 5 次失败返回 `TOOL_FAILED`，先于 step 预算。

## 20. 公共导出核对表

| 类别 | Python 公共类型 |
|---|---|
| ID/版本 | SCHEMA_VERSION、RunId、TaskId、SessionId、ArtifactId、QuestionId、WorkRequestId、WorkspaceId |
| 状态/路由 | Capability、AgentOwner、RunStatus、TaskStatus、AttemptStatus、ModuleStatus、WorkRequestStatus、SessionStatus |
| 错误/授权 | ErrorCode、WorkspaceMode |
| 科学枚举 | ScientificVerdict |
| 通用结果 | ModuleError、WarningRecord、SessionRef |
| 入口/预算 | RunBudget、TaskBudget、ResearchRequest、ArtifactImport |
| 人机交互 | QuestionDraft、PendingQuestion、UserAnswer |
| 证据 | ArtifactCandidate、ArtifactRef |
| capability 输入 | CodeUnderstandInput、CodeModifyInput、ExperimentRunInput、CapabilityInput |
| 数据集/环境 | DatasetRef、EnvironmentSpec |
| Coding payload | VerificationResult、CodeUnderstandResult、CodeModifyResult |
| Experiment payload | ExperimentResult |
| 工作流 | TaskProposal、WorkflowProposal、WorkflowPatch、Workflow、WorkflowTask、Attempt |
| 模块边界 | WorkspaceGrant、ModuleTaskRequest、ModuleResult |
| 工作区 | WorkspaceSourceKind、WorkspaceSpec、WorkspaceRecord、WorkspaceDescriptor |
| 注册 | CapabilityDefinition、CapabilityRegistry |
| 科学控制 | ScientificAssessment、WorkRequestDraft、WorkRequest、WorkTaskOutcome、WorkOutcome、ScientificOpinion、ScientificTurnRequest、ScientificTurnResult |
