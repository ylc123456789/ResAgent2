# ResAgent2 跨模块契约

**文档角色**：跨模块 wire 对象的字段、类型、组合约束和版本的唯一事实来源

**语义上级**：`ARCHITECTURE.md`；本文件不得改变其中的模块职责和控制流

**当前实现**：`resagent2-contracts 0.1.0`，wire schema `3.0`（`SCHEMA_VERSION="3.0"`，Stabilization 3.0 起；§17 记录 1.0/2.0 演进）

**Phase 7 目标**：§20 的 7.1—7.7 已实现（Scientific control 类型、ArtifactRef 三态 provenance、work_request_id 追溯、ResearchRun 字段、ScientificCompletionValidator、确定性 final report，以及 7.7 删除旧 PlanningPort/deprecated 类型后 production 切到唯一 Scientific 路径）；schema 2.0 在服务器真实 E2E 通过后冻结

## 1. 使用规则

本文件回答“模块之间传什么、字段准确表示什么”。§3—§19 描述 schema 1.1 的历史类型（其中 scientific/planning 相关类型已在 7.7 删除，仅留历史说明）；§20 描述 Phase 7 的 2.0 目标契约。7.1—7.7 已落地；新路径已切为唯一 production composition root。

- 架构概念和谁调用谁，以 `ARCHITECTURE.md` 为准；
- Python 字段必须与 `packages/contracts/src/resagent2_contracts/models.py` 一致；
- 代码与本文字段不一致时，视为 contract bug；
- 本文写了目标语义但代码尚未强制时，必须明确标为“未实现约束”；
- runtime 内部的 `AgentDefinition`、`AgentAction`、`FinishCandidate`、Tool 和 Context 类型不属于跨模块 wire contract，通常不在本文件定义；§20.12 只锁定 Phase 7 evidence Tool 与 Artifact/observation 边界的最小实现形状，不把它们加入 contracts 公共导出。

所有公共模型：

- 继承严格 `ContractModel`；
- 拒绝未知字段；
- 序列化 `schema_version: "2.0"`；
- 以下示意代码省略每个模型继承得到的 `schema_version`，但 wire 数据不能省略其版本语义。

## 2. 跨模块对象范围

| 边界 | 请求 | 响应/状态 |
|---|---|---|
| 用户 → ResAgent | ResearchRequest、UserAnswer | PendingQuestion、`final_report` ArtifactRef（内容由内部 FinalReportData 确定性渲染） |
| ResAgent → Scientific Agent | ScientificTurnRequest | ScientificTurnResult |
| Scheduler → 专业模块 | ModuleTaskRequest | ModuleResult |
| 专业模块 → Artifact Registry | ArtifactCandidate | ArtifactRef |
| ResAgent 持久化 | Workflow、WorkflowTask、Attempt、PendingQuestion | ResearchRun 属于 orchestrator 内部模型 |

Phase 7 目标边界改为：ResAgent ↔ Scientific Agent 使用 `ScientificTurnRequest` / `ScientificTurnResult`；Scientific Agent 只提出 `WorkRequestDraft`，WorkflowProposal/Patch 改由 ResAgent 内部 WorkflowCompiler 产生。准确字段见 §20。

禁止跨模块读取另一个模块的内部 Session state、私有目录或 prompt；禁止从 summary 文本推断机器状态；禁止把任意 dict 作为长期接口。

## 3. ID 命名空间

| 类型 | 格式示例 | 范围 |
|---|---|---|
| RunId | `run_example` | 全局唯一 |
| TaskId | `task_experiment` | Run 内唯一且跨 revision 稳定 |
| SessionId | `session_coding_1` | 子模块内唯一 |
| ArtifactId | `artifact_metrics` | Run 内唯一，不跨 Attempt 复用 |
| QuestionId | `question_dataset` | Run 内唯一 |

前缀后由字母或数字开头，只允许字母、数字、下划线和连字符，总长度由实现中的约束限制。ID 不能互换：TaskId 不是 SessionId，Attempt number 也不是 Agent step number。

本文示例必须使用这些类型和前缀，不能用裸的 `run_treatment`、`implement_method` 或 `code_patch_001` 代替 TaskId/ArtifactId。

## 4. 状态与所有权

### 4.1 RunStatus

```text
pending | running | paused | completed | failed
```

由 ResAgent 写入。planning、replanning 和 interrupted 不是 schema 1.1 的 RunStatus。

### 4.2 TaskStatus

```text
pending | running | completed | failed | blocked | needs_user_input | superseded
```

由 Scheduler 写入。`superseded` 表示尚未开始的 Task 被新 Workflow revision 取代；schema 中不存在 `skipped`。

### 4.3 AttemptStatus 与 ModuleStatus

ModuleStatus 的五个结果值是 AttemptStatus 的子集：

```text
completed | completed_with_warnings | failed | blocked | needs_user_input
```

AttemptStatus 另外包含 `running`，因此是六个值，不应说两个枚举“值相同”。ModuleStatus 由模块返回；AttemptStatus 由 Scheduler 根据已校验的 ModuleResult 记录。

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
| input_artifacts | 用户提供的**最小输入**（`ArtifactImport`），由 Controller 创建 Run 时验证本地 URI、冻结复制、校验 hash 并生成 `orchestrator/import` ArtifactRef（ADR-0011 §4） |
| dataset_refs | 整个 Run 的唯一数据集注册表 |
| budget | max_tasks、max_attempts_per_task、max_llm_calls、timeout_seconds |

## 6. 当前 Planning Port 契约（schema 1.1，Phase 7 将取代）

### 6.1 WorkflowProposal

```python
class WorkflowProposal:
    summary: NonEmptyStr
    tasks: list[TaskProposal]
    questions: list[QuestionDraft] = []
    scientific_rationale: NonEmptyStr

class TaskProposal:
    id: TaskId
    capability: Capability
    goal: NonEmptyStr
    rationale: NonEmptyStr
    depends_on: list[TaskId] = []
    required: bool = True
    inputs: CapabilityInput
    success_criteria: list[SuccessCriterion]
```

Proposal 已在 schema 层检查重复 ID、未知依赖和环，也由 validator 拒绝控制面 capability；仍需 ResAgent 做架构级校验：是否存在唯一 binding、预算和系统约束是否满足。

schema 1.1 中 `questions` 的唯一语义是“在创建 Workflow 前仍需用户澄清”。非空 Proposal 不能执行，回答后重新规划。`create_run` 会拒绝非空 questions。schema 2.0 由 Scientific control loop 统一提出用户问题，WorkflowCompiler 不再产出 questions。

### 6.2 WorkflowPatch

```python
class WorkflowPatch:
    based_on_revision: int
    reason: NonEmptyStr
    add_tasks: list[TaskProposal] = []
    supersede_task_ids: list[TaskId] = []
    pending_task_updates: list[PendingTaskUpdate] = []

class PendingTaskUpdate:
    task_id: TaskId
    inputs: CapabilityInput | None = None
    depends_on: list[TaskId] | None = None
```

Patch 只基于当前 revision；只能 supersede 或更新 pending Task；成功应用后 revision +1，旧 Workflow 进入 history。它不能修改已执行历史、running Task 或 Artifact。

## 7. Workflow 与 WorkflowTask

```python
class Workflow:
    run_id: RunId
    revision: int
    tasks: list[WorkflowTask]
    created_from: NonEmptyStr

class WorkflowTask:
    id: TaskId
    capability: Capability
    goal: NonEmptyStr
    inputs: CapabilityInput
    depends_on: list[TaskId] = []
    required: bool = True
    workspace_id: WorkspaceId | None = None
    status: TaskStatus = TaskStatus.PENDING
    input_artifacts: list[ArtifactId] = []
    success_criteria: list[SuccessCriterion]
    attempts: list[Attempt] = []
    warnings: list[WarningRecord] = []
```

`WorkflowTask` 是唯一顶层任务模型。`capability` 必须与 discriminated `inputs.capability` 相同。depends_on 只能引用同图中的 TaskId，图必须无环；Attempt number 必须从 1 连续递增。

合法示例：

```python
WorkflowTask(
    id="task_run_treatment",
    capability="experiment_run",
    goal="运行 treatment 并记录验证集指标",
    inputs={
        "capability": "experiment_run",
        "instructions": "运行 treatment 配置",
        "expected_metrics": ["validation_accuracy"],
    },
    depends_on=["task_implement_method"],
    input_artifacts=["artifact_code_patch_001"],
    success_criteria=[{
        "description": "产生 validation_accuracy",
        "verification": "automatic",
        "evidence_key": "validation_accuracy",
    }],
)
```

### SuccessCriterion 当前语义

```python
class SuccessCriterion:
    description: NonEmptyStr
    verification: VerificationMode
    evidence_key: NonEmptyStr | None = None
```

automatic criterion 在 schema 层要求 evidence_key。但 schema 1.1 尚未定义 evidence_key 指向哪个 payload/artifact 结构，Scheduler 也不求值 criteria。当前它是持久化的计划意图，Task 是否 completed 仍由模块 finalizer 返回的 ModuleStatus 决定。

以上只描述 schema 1.1 现状。ADR-0007 已裁定 schema 2.0 删除 SuccessCriterion/evidence_key，不再定义通用求值器；目标完成证据归 capability finalizer，见 §20.10。

## 8. 当前 Capability 与路由（schema 1.1）

| capability | owner | 架构位置 | 当前说明 |
|---|---|---|---|
| scientific_plan | Scientific | 控制面 Planning Port | schema 保留；不得成为 WorkflowTask |
| scientific_analyze | Scientific | 任务面 | 分析已登记证据 |
| literature_search | Scientific | 任务面 | 有边界的文献检索 |
| code_understand | Coding | 任务面 | 只读代码理解 |
| code_modify | Coding | 任务面 | 授权范围内修改和验证 |
| experiment_prepare | Experiment | 任务面 | 准备/审计 repo 和 env |
| experiment_run | Experiment | 任务面 | 执行实验并收集证据 |
| ask_user | Orchestrator | 控制信号 | schema 过渡保留；不得成为 WorkflowTask |

schema 1.1 的 `Capability` / `CapabilityInput` 联合类型仍保留这两个控制面类型作为过渡，Workflow validator 已拒绝 `scientific_plan` 与 `ask_user` 作为 Task。它们是否移除的历史问题已由 ADR-0007 解决：schema 2.0 最终切换时删除，见 §20.8 与 §20.13。

Phase 7 已裁定在 schema 2.0 移除 `scientific_plan`、`scientific_analyze`、`literature_search`、`experiment_prepare` 和 `ask_user` 的顶层 task capability。前三者转入 Scientific control loop / Tool，`experiment_prepare` 合并在 `experiment_run` 内，ask-user 始终是控制信号。目标注册表见 §20.8。

`CapabilityDefinition` / `CapabilityRegistry` 描述 owner、request/result model、side effects、permission policy 和 completion evidence。Registry 拒绝同一 capability 出现两次，从而保证每个 capability 恰有一个 owner；同一个 owner 可以拥有多个不同 capability。它是注册表数据，不改变架构中的控制面/任务面区分。

## 9. ModuleTaskRequest

```python
class ModuleTaskRequest:
    run_id: RunId
    task_id: TaskId
    attempt_number: int
    capability: Capability
    goal: NonEmptyStr
    inputs: CapabilityInput
    input_artifacts: list[ArtifactRef] = []
    constraints: list[NonEmptyStr] = []
    answers: list[UserAnswer] = []
    budget: TaskBudget
    workspace: WorkspaceGrant | None = None
    workspace_id: str | None = None
    workspace_spec: WorkspaceSpec | None = None
    parent_session_id: SessionId | None = None
```

| 字段 | 控制流语义 |
|---|---|
| run/task/attempt | provenance 与幂等边界 |
| capability + inputs | 选择模块 profile；二者 discriminator 必须一致 |
| input_artifacts | 已登记且已授权给本 Task 的输入证据 |
| answers | 只包含属于本 Task 的已持久化回答 |
| workspace | 此 Attempt 的最大物理访问范围（由 WorkspaceRecord 派生） |
| workspace_id | 此 Attempt 操作的逻辑工作区 id，指向 `ResearchRun.workspaces` |
| workspace_spec | 该工作区的来源声明（source_kind + location），供 Agent 在 loop 前确定性 materialize |
| parent_session_id | 仅显式 resume 使用；普通 retry 为空 |

当前 orchestrator 在 ask-user 后的新 Attempt 产生 `parent_session_id`；runtime AgentLoop 在 resume 时加载该 Session 并校验 run/task/agent/owner/paused 一致，端到端 resume 已接通。普通 retry 不复用 Session。

## 10. ModuleResult

```python
class ModuleResult[PayloadT]:
    status: ModuleStatus
    summary: NonEmptyStr
    payload: PayloadT | None = None
    artifacts: list[ArtifactCandidate] = []
    session: SessionRef | None = None
    question: QuestionDraft | None = None
    error: ModuleError | None = None
    warnings: list[WarningRecord] = []
```

组合约束：

- needs_user_input：必须有 question，不能有 error；
- failed / blocked：必须有 ModuleError，不能有 question；
- completed：不能有 error、question 或 warnings；
- completed_with_warnings：至少有一条 WarningRecord；
- ArtifactCandidate 可随失败结果作为诊断输出登记，但不能让失败状态变成功。

`summary` 只用于人类展示；`payload` 是 capability 专有数据。Scheduler 将通过校验的 payload 原样持久化到对应 Attempt，供审计和恢复后读取，但不解释其中的领域语义，也不从 payload 推断状态。

### 10.1 当前 payload 持久化与消费策略

Workflow Core 只对 ModuleResult 的外层控制字段执行调度语义：status、artifacts、session、question、error 和 warnings。schema 1.1 的裸 `ModuleResult` 只校验 payload 可以被 Pydantic 接受，不执行 capability 专有结果校验；Scheduler 把 payload 保存到 `Attempt.payload`，不将其提升为独立的 Run 控制状态。

因此当前稳定规则是：

- 跨 Task、重启后或最终报告仍需使用的信息，必须通过 ArtifactCandidate 登记为 ArtifactRef；
- payload 不能作为 Task 依赖、finish gate 或 provenance 的唯一依据；
- legacy adapter 的 dict payload 只作为过渡审计数据；原生专业 Agent 接入时必须定义 `ModuleResult[具体结果模型]` 以及领域消费方；
- 如果某个结构化结果需要成为 Run 状态的一部分，应新增明确契约，而不是让 Scheduler 静默保存任意 payload。

| task capability | 目标 payload/消费方 | 当前行为 | 原生模型定义阶段 |
|---|---|---|---|
| scientific_analyze | ScientificConclusion → 科学闭环/final report | legacy payload 保存到 Attempt；Phase 7 由 ScientificTurnResult 取代 | Phase 7 删除 task capability |
| literature_search | 有界文献结果 → Scientific Agent | 无 production binding；Phase 7 改为 Scientific Tool | Phase 7 删除 task capability |
| code_understand | CodeUnderstandResult → 调用方 | Phase 5 原生 Coding Agent 生产并持久化到 Attempt | Phase 5 |
| code_modify | CodeModifyResult → 调用方；代码变化 → ArtifactRef | Phase 5 原生 Coding Agent 生产并持久化到 Attempt | Phase 5 |
| experiment_prepare | 环境/仓库准备结果 → Experiment 流程 | 无 production binding；模型待定义 | Phase 6 |
| experiment_run | `ExperimentResult` → Scientific Agent；证据 → ArtifactRef | Phase 6 原生 Experiment Agent 生产并持久化到 Attempt | Phase 6 |

scientific_plan 和 ask_user 不在表中，因为它们不是 task capability。

### 10.2 Coding Agent vNext payload

```python
class VerificationResult:
    command: NonEmptyStr
    exit_code: int
    timed_out: bool = False
    stdout_path: str
    stderr_path: str
    duration_seconds: float

class CodeUnderstandResult:
    answer: NonEmptyStr
    evidence_files: list[str]
    uncertainty: str = ""

class CodeModifyResult:
    changed_files: list[str]
    deleted_files: list[str] = []
    patch_path: str
    verification_results: list[VerificationResult] = []
    verification_passed: bool
    residual_risks: list[NonEmptyStr] = []
```

`CodeUnderstandResult.evidence_files` 至少包含一个实际通过 Coding read/search Tool 观察过的 workspace 相对路径。`CodeModifyResult.changed_files` 包含本 Attempt 新增或内容改变且仍存在的文件；`deleted_files` 单独记录删除；二者不能重叠。`patch_path` 指向 Coding finalizer 通过 capabilities Git 能力生成的 Attempt patch。

`verification_passed` 必须等于所有 `VerificationResult` 均为 exit_code 0 且未 timeout。没有声明 verification command 时结果列表为空且该字段为 true，但这只表示“没有失败的声明验证”，不等价于更强的测试充分性声明。

这些类型是既有 `ModuleResult.payload` 扩展点的新命名形状，没有改变任何现有模型字段，因此 Phase 5 引入时未触发 schema 升级（到 Phase 6 给 `ExperimentRunInput` 加字段时才升到 1.1）。Coding Agent 用强类型 finalizer 生成它们；Scheduler 仍只原样持久化，不基于 payload 改状态。

```python
class ModuleError:
    code: ErrorCode
    message: NonEmptyStr
    retryable: bool
    details: dict[str, JsonValue] = {}
```

ErrorCode 是固定枚举：invalid_input、permission_denied、tool_failed、timeout、budget_exhausted、contract_error、environment_unavailable、artifact_missing。

### 10.3 Experiment Agent vNext payload

```python
class ExperimentResult:
    metrics: dict[str, JsonValue] = {}
    parameters: dict[str, JsonValue] = {}
    evidence_files: list[str] = []
    repo_url: str = ""
    commit: str = ""
    env_id: NonEmptyStr
    delivery_issues: list[NonEmptyStr] = []
    residual_risks: list[NonEmptyStr] = []
```

`evidence_files` 是 workspace 相对路径，指向本 Attempt 实际产生的证据文件；每个文件由 finalizer 校验存在后才进入 payload，并作为 `experiment_result` ArtifactCandidate 登记。`repo_url` + `commit` 是 repo identity（不依赖 basename）；`env_id` 是 `run_id + workspace_id` 绑定的基础环境 id（见 §23）。`delivery_issues` 记录 `expected_metrics`/`expected_artifacts` 缺失项；非空时 finalizer 返回 completed_with_warnings，其 WarningRecord（code=`delivery_not_met`）的 message 记录 `[NOT MET] Missing required ...`。

`ExperimentRunInput` 在 7.7 Hardening 后只保留实验本身所需的输入字段：

```python
confirm_before_experiment: bool = False
```

Python 版本改由 `ModuleTaskRequest.environment_spec`（`EnvironmentSpec.python_version`）承载，不再由 `ExperimentRunInput` 携带（见 §23）。

仓库来源（`repository_url`/`copy_from`/`external_repo_path`）已删除，改由统一工作区上下文（`ModuleTaskRequest.workspace_spec`）提供，`RepoMaterializer` 在 Agent loop 前确定性 materialize；数据集/环境目录来自 `ResourceLayout`，实验输出写入 Attempt 目录或 ArtifactRegistry，不写入共享缓存。`ExperimentResult` 是既有 `ModuleResult.payload` 扩展点的新命名形状。Scheduler 仍只原样持久化 payload，不基于 payload 改状态。

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

class SessionRef:
    id: SessionId
    module: AgentOwner
    state_uri: NonEmptyStr
    status: SessionStatus
    created_at: datetime
    updated_at: datetime
```

Attempt 属于 ResAgent 历史，Session 属于子 Agent。retry（failed/blocked 后重试）是同一 Task 的新 Attempt，默认新 Session；pause/resume 是**同一 Attempt** 的暂停与继续，不增加 Attempt number，复用 Session/output_dir/workspace baseline（ADR-0011 §2）；repair 是新 WorkflowTask。

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

子 Agent 只生成 QuestionDraft；ResAgent 分配 ID、持久化 PendingQuestion、暂停 Run、校验 Answer 并恢复。`reason` 是必填字段，任何 JSON 示例都不能省略。

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
    task_id: TaskId
    attempt_number: int
    uri: NonEmptyStr
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    media_type: NonEmptyStr
    summary: NonEmptyStr
    metadata: dict[str, JsonValue] = {}
```

Candidate 的 path 必须是 workspace root 下无 `..` 的相对路径。Candidate 故意没有 id、URI、hash 或 provenance；这些只能由 ResAgent 登记时产生。`content` 只用于传递派生的小型文本 Artifact（如 patch，其字节已随 Candidate 携带、源文件位于 Run 数据目录而非源码仓库）；普通大文件仍走 workspace path + ArtifactRegistry 冻结。

capabilities 中的 Tool 负责访问时的路径/权限检查；ResAgent 在登记时独立复核存在、containment、symlink、hash 和 Attempt 绑定。ArtifactRef 是冻结证据，不能被后续 Attempt 覆盖。

## 14. WorkspaceGrant

```python
class WorkspaceGrant:
    root: NonEmptyStr
    mode: WorkspaceMode
    allowed_paths: list[str] = []
    denied_paths: list[str] = []
    source: WorkspaceSourceKind
```

Grant 表示授权，不表示 repo identity 或 Artifact。它由 `WorkspaceRecord` 派生（见 §21），不再由 `ModuleBinding` 固定携带。allowed/denied path 只接受相对 root 的路径。contracts 做词法约束；capabilities 的真实 filesystem 实现做 resolve/symlink/物理边界检查；Artifact Registry 登记时再次复核输出。

## 15. 历史 ScientificConclusion（schema 1.1，已于 Phase 7.7 删除）

```python
class ScientificConclusion:
    verdict: ScientificVerdict
    summary: NonEmptyStr
    evidence_artifact_ids: list[ArtifactId]
    limitations: list[NonEmptyStr] = []
    recommended_next_steps: list[NonEmptyStr] = []
```

该类型连同 `scientific_analyze` task capability 已于 Phase 7.7 原子切换时删除，此处仅保留历史说明。schema 2.0 使用 `ScientificAssessment` / `ScientificOpinion`，并由 `ScientificTurnResult` 区分 request_work、needs_user_input、completed 和 failed。

## 16. 状态映射

| ModuleStatus | Scheduler 写入的 TaskStatus | Run 行为 |
|---|---|---|
| completed | completed | 继续依赖图；其 Artifact 可自动传给依赖任务 |
| completed_with_warnings | completed，并追加 warnings | 继续依赖图；warnings 被持久化，未来报告消费者必须展示 |
| failed | retryable 且预算允许时 pending，否则 failed | 可重试项重新进入调度；稳定后由 required Task gate 决定 Run completed/failed |
| blocked | blocked | ResAgent 只能通过显式 WorkflowPatch/recovery 和 retry 恢复；稳定后由 required Task gate 决定 Run completed/failed |
| needs_user_input | needs_user_input | ResAgent 保存 PendingQuestion，并把 Run 置为 paused |

这张表描述 Scheduler 对一次 ModuleResult 的确定性映射。专业模块不能直接写 TaskStatus 或 RunStatus。ScientificVerdict 与运行状态独立：一次执行成功的 scientific_analyze 可以得出 refutes 或 inconclusive。

## 17. schema 版本规则

- Python 包版本和 wire schema 版本独立演进；
- 增加可选字段至少需要 schema 小版本和迁移说明；
- 删除字段、改字段含义或改变必填性需要不兼容版本；
- 每个版本必须有 round-trip 和非法组合 contract tests；
- metadata 不得长期承载本应成为正式字段的状态；
- schema 版本策略发生改变时必须先写 ADR。

Phase 4 完成起 `1.0` 冻结；Phase 6 给 `ExperimentRunInput` 增加可选字段时按规则发布 `1.1`（迁移说明见 §10.3 与 ADR-0005），并新增 round-trip 与非法组合测试。Phase 7 删除旧 capability/input、改变 Proposal 所有权并新增科学控制对象，因此目标版本定为不兼容的 `2.0`，而不是伪装成只加可选字段的 `1.2`。

Phase 7.1 已把 `SCHEMA_VERSION` 切到 `"2.0"` 并在同一原子变更中删除 `SuccessCriterion`/`VerificationMode`/`success_criteria`/`WorkflowProposal.questions`、把 `scientific_rationale` 改名 `compilation_rationale`、给 Proposal/Patch/Task 加 `work_request_id`；不维护 1.1 的旧格式兼容层。`2.0` 只有到 7.7 删除全部临时兼容符号、全仓/E2E 通过后才冻结（§20.13.6）。旧 scientific/planning 类型在此期间仍保留并标记 deprecated，供唯一旧 production 路径使用。

## 18. 当前公共导出核对表

| 类别 | Python 公共类型 |
|---|---|
| ID/版本 | SCHEMA_VERSION、RunId、TaskId、SessionId、ArtifactId、QuestionId、WorkRequestId |
| 状态/路由 | Capability、AgentOwner、RunStatus、TaskStatus、AttemptStatus、ModuleStatus、WorkRequestStatus |
| 错误/授权 | ErrorCode、WorkspaceMode、SessionStatus |
| 科学枚举 | ScientificVerdict |
| 通用结果 | ModuleError、WarningRecord、SessionRef |
| 入口/预算 | RunBudget、TaskBudget、ResearchRequest |
| 人机交互 | QuestionDraft、PendingQuestion、UserAnswer |
| 证据 | ArtifactCandidate、ArtifactRef |
| capability 输入 | CodeUnderstandInput、CodeModifyInput、ExperimentRunInput、CapabilityInput |
| 数据集引用 | DatasetRef（`ExperimentRunInput.dataset_refs`、`ResearchRequest.dataset_refs`） |
| Coding payload | VerificationResult、CodeUnderstandResult、CodeModifyResult |
| Experiment payload | ExperimentResult |
| 工作流 | TaskProposal、WorkflowProposal、Attempt、WorkflowTask、Workflow、PendingTaskUpdate、WorkflowPatch |
| 模块边界 | WorkspaceGrant、ModuleTaskRequest、ModuleResult |
| 工作区 | WorkspaceSourceKind、WorkspaceSpec、WorkspaceRecord |
| 注册 | CapabilityDefinition、CapabilityRegistry |
| 科学控制（2.0） | ScientificAssessment、WorkRequestDraft、WorkRequest、WorkTaskOutcome、WorkOutcome、ScientificOpinion、ScientificTurnRequest、ScientificTurnResult |

## 19. 已知契约对齐项

这些是已确认缺口，不是隐含设计：

1. schema 1.1 的 `success_criteria/evidence_key` 仍未求值；Phase 7 不再扩建通用求值语言，而是在 schema 2.0 删除该字段，由 capability finalizer 判断任务完成证据；
2. Scientific capability 的 legacy payload 将由 §20 的 Scientific control contract 整体取代；
3. Phase 7 按 §17 发布 2.0，不维护 1.1/2.0 双生产路径。

这些工作的阶段、顺序和验收见 `DEVELOPMENT_PLAN.md`。

## 20. Phase 7 目标契约（schema 2.0）

7.1 已把公共类型落入 `models.py` 并导出（`SCHEMA_VERSION="2.0"`）；§20.10.1（ResearchRun 内部字段）已随 7.5 落地，§20.10.2（ScientificCompletionValidator/FinalReportData）已随 7.6 落入 orchestrator 内部并从 orchestrator 包导出。它们不是 contracts wire 公共类型。

### 20.1 新 ID

```python
WorkRequestId = Annotated[str, StringConstraints(pattern=r"^work_[A-Za-z0-9][A-Za-z0-9_-]*$")]
```

WorkRequestId 在一个 Run 内唯一。它只标识“为什么需要这一轮执行”，不能当作 TaskId、Workflow revision 或 SessionId。

### 20.2 ScientificAssessment

```python
class ScientificAssessment:
    statement: NonEmptyStr
    evidence_artifact_ids: list[ArtifactId] = []
    limitations: list[NonEmptyStr] = []
    unresolved_questions: list[NonEmptyStr] = []
```

`statement` 是当前科学观点，不是执行摘要。每次 `request_work` 都必须携带 assessment，确保 Scientific Agent 先说明已有判断，再说明缺少什么。`evidence_artifact_ids` 只能引用本 Run 中已授权且 Scientific Session 确实通过 `read_artifact` 或 `literature_search` Tool 观察过的 Artifact；finalizer 和 ResAgent gate 都要复核。

### 20.3 WorkRequestDraft 与 WorkRequest

```python
class WorkRequestStatus(StrEnum):
    REQUESTED = "requested"
    COMPILING = "compiling"
    EXECUTING = "executing"
    STABLE = "stable"
    CONSUMED = "consumed"
    FAILED = "failed"

class WorkRequestDraft:
    objective: NonEmptyStr
    expected_evidence: list[NonEmptyStr]
    constraints: list[NonEmptyStr] = []

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

`objective` 描述需要完成的工作目的；`expected_evidence` 描述 Scientific Agent 希望随后观察到的证据。它们都是自然语言语义，不是任务图。

WorkRequestDraft 严禁包含 capability、owner、task_id、depends_on、workspace、path、env、retry、status 或 Attempt 字段。持久化 WorkRequest 只增加本节列出的 ID、绑定、生命周期、outcome/error 和时间字段，仍不能携带任务图或物理执行字段。

`expected_evidence` 至少一项。ResAgent 分配 ID、绑定 run/session 并持久化后，Draft 才成为 status=requested 的 WorkRequest。一个 Run 同时最多有一个 active WorkRequest；active 指 requested/compiling/executing/stable，consumed/failed 为终态。

状态和字段组合：

| status | 含义 | 必须字段 | 禁止字段 |
|---|---|---|---|
| requested | Draft 已持久化，尚未调用 Compiler | 无附加字段 | workflow_revision/outcome/error |
| compiling | Compiler 调用已开始；崩溃后可按同一 work_request_id 重试 | 无附加字段 | workflow_revision/outcome/error |
| executing | Proposal/Patch 已接受，Scheduler 正在执行 | workflow_revision | outcome/error |
| stable | 对应任务已经稳定，WorkOutcome 已持久化，等待恢复 Scientific Session | workflow_revision、outcome | error |
| consumed | ScientificPort 已按 work_request_id 幂等接收 WorkOutcome | workflow_revision、outcome | error |
| failed | 编译/控制契约发生不可恢复错误；Task failed/blocked 不进入此状态 | error | 无；已有 workflow_revision/outcome 必须保留 |

唯一合法转换是 `requested → compiling → executing → stable → consumed`，任一步可在不可恢复控制错误时进入 failed。failed 若带 outcome 必须同时带 workflow_revision，不能删除已形成的执行历史。普通 Task failed/blocked 仍产生 stable WorkOutcome。stable→consumed 的 Scientific resume 使用 work_request_id 作为幂等键：重复投递返回同一已持久化结果，不能把同一 observation 追加两次。

### 20.3.1 Scientific Artifact provenance

schema 2.0 的 ArtifactRef 将生产边界改为互斥 union：

```python
class ArtifactRef:
    id: ArtifactId
    kind: NonEmptyStr
    producer: AgentOwner
    run_id: RunId
    task_id: TaskId | None = None
    attempt_number: int | None = None
    session_id: SessionId | None = None
    uri: NonEmptyStr
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    media_type: NonEmptyStr
    summary: NonEmptyStr
    metadata: dict[str, JsonValue] = {}
```

合法 provenance 只有三种：

- 执行 Artifact：producer 为 coding/experiment，task_id 与正整数 attempt_number 同时存在，session_id 为空；
- Scientific Tool Artifact：producer 为 scientific，session_id 存在，task_id 与 attempt_number 同时为空；
- Orchestrator Artifact：producer 为 orchestrator，三者都为空；metadata.source_type 必须是 `import` 或 `final_report`。import 的原始来源由 Registry 校验后记录，final_report 只允许 deterministic renderer 产生。

model validator 必须按 producer 选择上述唯一分支；混合字段、缺半个 task/attempt、非正 attempt、scientific 带 task、orchestrator 带 session 等全部拒绝。所有 Artifact 仍必须有当前 run_id、Registry 计算的 sha256 和冻结 uri。

`ArtifactCandidate` 仍不含 id/hash/provenance。Scientific `literature_search` Tool 将规范化结果作为 Candidate，连同当前 run/session 的 registration context 交给 ResAgent Artifact Registry；Registry 复核、冻结并返回 ArtifactRef。Tool/Agent 不得自行分配 ID 或 hash。该 registration port 由 composition root 注入，capabilities 不 import orchestrator。

### 20.4 WorkTaskOutcome 与 WorkOutcome

```python
class WorkTaskOutcome:
    task_id: TaskId
    status: Literal["completed", "failed", "blocked", "superseded"]
    summary: NonEmptyStr
    artifact_ids: list[ArtifactId] = []
    error: ModuleError | None = None
    warnings: list[WarningRecord] = []

class WorkOutcome:
    work_request_id: WorkRequestId
    workflow_revision: int
    summary: NonEmptyStr
    tasks: list[WorkTaskOutcome]
```

tasks 至少一项且 TaskId 不重复。每个 Task 必须属于对应 Workflow revision，且 Task 的 `work_request_id` 等于本 WorkOutcome 的 work_request_id。failed/blocked 必须有 error；completed/superseded 不能有 error。artifact_ids 只能包含该 Task Attempt 已登记的 Artifact，包括成功证据和明确标记的诊断证据；失败 Task 不能因为产出诊断 Artifact 就被写成 completed。

WorkOutcome 是执行事实摘要，不判断实验是否支持假设。`summary` 不能覆盖结构化 status/error/warnings。即使含 failed/blocked Task，也可以返回 Scientific Agent；由 Scientific Agent 决定请求替代工作、修改观点或以局限形式结束。

### 20.5 ScientificOpinion

```python
class ScientificOpinion:
    verdict: ScientificVerdict
    statement: NonEmptyStr
    evidence_artifact_ids: list[ArtifactId]
    limitations: list[NonEmptyStr] = []
    unresolved_questions: list[NonEmptyStr] = []
    recommended_next_steps: list[NonEmptyStr] = []
    acknowledged_task_ids: list[TaskId] = []
```

`statement` 是面向用户的最终科学观点。verdict 与 RunStatus 独立：`inconclusive` 可以是一个成功完成的科学闭环；Run failed 表示系统未能形成符合 gate 的可靠意见。

最终 evidence 可以为空，只允许观点明确说明当前没有可用证据且 verdict 为 inconclusive 或 not_applicable；任何 supports/refutes 意见必须至少引用一个 ArtifactId。

`acknowledged_task_ids` 不是“已成功任务”列表，而是 Scientific Agent 已明确纳入判断的 failed/blocked Task。最终 gate 要求 Run 中所有仍 failed/blocked 的 TaskId 都出现在该列表；列表非空时 limitations 也必须非空。completed/superseded Task 不得出现在该列表。

### 20.6 ScientificTurnRequest

```python
class ScientificTurnRequest:
    run_id: RunId
    research: ResearchRequest
    authorized_artifacts: list[ArtifactRef] = []
    work_outcome: WorkOutcome | None = None
    unresolved_task_outcomes: list[WorkTaskOutcome] = []
    answers: list[UserAnswer] = []
    budget: TaskBudget
    parent_session_id: SessionId | None = None
```

首次调用 `parent_session_id=None` 且 `work_outcome=None`。恢复必须给出同一 Run 的 paused Scientific Session；WorkOutcome 和 answers 只包含自上次暂停后新增的 observation。`unresolved_task_outcomes` 是截至本轮仍 failed/blocked 的结构化事实，供 completion check 验证 acknowledged_task_ids。`authorized_artifacts` 是本轮可读 allowlist，不表示 Agent 已经读取。

组合约束：首次调用必须同时没有 parent_session_id/work_outcome/answers；恢复调用必须有 parent_session_id，并且 work_outcome 与 answers 至多一种非空。work_outcome.work_request_id 必须属于该 Run，且对应 WorkRequest.status=stable；answers 必须全部匹配该 Run 的 PendingQuestion。authorized_artifacts 的 ID 不重复且 run_id 全部匹配；unresolved_task_outcomes 只允许 failed/blocked 且 TaskId 不重复。

### 20.7 ScientificTurnResult

结果使用 discriminated union，外部只有一个 Scientific port，不是四套 Agent 模式：

```python
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

ScientificTurnResult = Annotated[
    ScientificWorkRequestResult
    | ScientificQuestionResult
    | ScientificCompletedResult
    | ScientificFailedResult,
    Field(discriminator="status"),
]

class ScientificPort(Protocol):
    def run(self, request: ScientificTurnRequest) -> ScientificTurnResult: ...
```

`llm_calls` 是本轮 ScientificPort 实际新增的 LLM 调用数，必须为非负整数。ResearchController 只传剩余预算并把每轮该值累计到 `ResearchRun.llm_calls_used`；累计超过 RunBudget 时不能写 completed。该字段不统计 Coding/Experiment 子 Agent 调用，Phase 7 不在此处扩建跨 Agent 统一计费系统。

组合约束：

- request_work 必须同时有 assessment、work_request 和 paused session；
- needs_user_input 必须有 assessment、question 和 paused session；
- completed 必须有 opinion 和 completed session，且 Scientific deterministic completion check 已通过；
- failed 必须有 error，不能附带 opinion/work_request/question；session 若存在必须是 failed；
- `observed_artifact_ids` 由 ScientificPort finalizer 从整个 Session 的成功 Tool observation 累积派生，不能来自 LLM action payload；
- assessment/opinion 的 evidence_artifact_ids 必须是 observed_artifact_ids 的子集；
- failed 无 session 时 observed_artifact_ids 必须为空；
- 这些 status 是一次 agent turn 的控制结果，不是 RunStatus 或 TaskStatus。

ScientificPort 是唯一 Scientific Agent 边界。首次 request 创建 Session；parent_session_id 恢复 Session。work_outcome 按 work_request_id、answers 按 question_id 幂等，重复 request 必须返回已持久化结果，不能重复追加 observation 或重复调用 LLM。

### 20.8 schema 2.0 的 Workflow capability

顶层 WorkflowTask 只保留真正由 Scheduler 执行的能力：

| capability | owner | 说明 |
|---|---|---|
| code_understand | Coding | 授权范围内只读理解代码 |
| code_modify | Coding | 授权范围内修改并验证代码 |
| experiment_run | Experiment | 准备环境、执行实验、冻结结果证据 |

最终 schema 2.0 删除 `scientific_plan`、`scientific_analyze`、`literature_search`、`experiment_prepare`、`ask_user` 及对应 CapabilityInput。Literature Search 是 Scientific Agent 的 Tool；ask-user 是 control signal；实验准备属于 experiment_run 内部流程。原子迁移期间的临时保留规则见 §20.13。

### 20.9 WorkflowCompiler 边界

WorkflowCompiler 的输入是 `WorkRequest`、CapabilityRegistry、Run 约束和当前 Workflow 摘要；输出只有 `WorkflowProposal` 或 `WorkflowPatch`。它是 orchestrator 内部 Port，不另造跨包“第三套任务模型”。schema 2.0 的完整目标形状为：

```python
class TaskProposal:
    id: TaskId
    work_request_id: WorkRequestId
    capability: Capability
    goal: NonEmptyStr
    rationale: NonEmptyStr
    depends_on: list[TaskId] = []
    required: bool = True
    workspace_id: WorkspaceId | None = None
    constraints: list[NonEmptyStr] = []
    inputs: CapabilityInput

class WorkflowProposal:
    work_request_id: WorkRequestId
    summary: NonEmptyStr
    tasks: list[TaskProposal]
    compilation_rationale: NonEmptyStr

class PendingTaskUpdate:
    task_id: TaskId
    inputs: CapabilityInput | None = None
    depends_on: list[TaskId] | None = None

class WorkflowPatch:
    work_request_id: WorkRequestId
    based_on_revision: int
    reason: NonEmptyStr
    add_tasks: list[TaskProposal] = []
    supersede_task_ids: list[TaskId] = []
    pending_task_updates: list[PendingTaskUpdate] = []

class WorkflowTask:
    id: TaskId
    work_request_id: WorkRequestId
    capability: Capability
    goal: NonEmptyStr
    inputs: CapabilityInput
    depends_on: list[TaskId] = []
    required: bool = True
    workspace_id: WorkspaceId | None = None
    constraints: list[NonEmptyStr] = []
    status: TaskStatus = TaskStatus.PENDING
    input_artifacts: list[ArtifactId] = []
    attempts: list[Attempt] = []
    warnings: list[WarningRecord] = []

class Workflow:
    run_id: RunId
    revision: int
    tasks: list[WorkflowTask]
    created_from: WorkRequestId
```

WorkflowProposal 不再有 questions，`scientific_rationale` 改名为 `compilation_rationale`，因为图由 WorkflowCompiler 而不是 Scientific Agent 产生。TaskProposal/WorkflowTask 不再有 success_criteria。

production `LLMWorkflowCompiler` 不直接让 LLM 输出上面的 Proposal/Patch（ADR-0010）。LLM 只输出 orchestrator 内部的 `CompilationDraft`（顶层 `summary`/`rationale` + 每任务 `key`/`capability`/`goal`/`rationale`/`depends_on`/`workspace_id`/`inputs`，不进入公共 contracts，也不输出代码细节——`code_modify` 的 `suggested_paths` 由物化器强制清空），再由确定性 `_materialize_draft` 分配全局 TaskId、绑定 `work_request_id = request.id`、解析 workspace、转换局部依赖并产出上表的 Proposal（首轮）或只追加 Patch（修复轮）。结构校验通过后再做一次短小的语义完整性审查（`CompilationReview.accepted/missing_requirements`，判断是否漏了请求明确要求的前置任务）；结构拒绝与语义不完整各自最多带精确反馈重编译一次，两次都失败才把 WorkRequest/Run 置为 failed。

Proposal 中每个 Task 的 work_request_id 必须等于 Proposal.work_request_id。Patch 新增/更新/supersede 的 Task 必须是 pending 且属于 Patch.work_request_id；旧 revision 中未被修改的历史 Task 保持原 work_request_id。Workflow.created_from 等于创建当前 revision 的 Proposal/Patch.work_request_id。编译器不得产生 §20.8 以外的 capability；validator 必须检查 run、revision、work_request_id、DAG、能力注册表、预算和 inputs discriminator。

### 20.10 为什么删除 success_criteria/evidence_key

schema 1.1 的通用 `SuccessCriterion` 与 `evidence_key` 从未被运行期求值，并且与 Coding/Experiment 已有的强类型输入、payload 和 finalizer 重复。Phase 7 不再设计一个通用路径表达式求值器：

- Coding 完成证据由 Coding finalizer 检查 Git diff 和 verification result；
- Experiment 完成证据由 Experiment finalizer 检查命令、metrics 和 expected_artifacts；
- Scientific 完成证据由 Scientific finalizer + ResAgent gate 检查 opinion 和 Artifact provenance；
- 需要人工确认时使用 QuestionDraft/PendingQuestion/UserAnswer。

因此规则变成“谁产生领域结果，谁按强类型语义验证结果”；ResAgent 只消费统一外层状态和 Artifact，不解释任意领域 payload。

### 20.10.1 ResearchRun 的 Phase 7 内部字段

ResearchRun 仍是 orchestrator 内部模型，不加入 contracts 公共导出；但为避免实现自行裁决，目标增量固定为：

```python
class ResearchRun:
    # 既有 request/workflow/history/status/pending_question 等字段保持
    scientific_session: SessionRef | None = None
    latest_scientific_assessment: ScientificAssessment | None = None
    work_requests: list[WorkRequest] = []
    scientific_observed_artifact_ids: list[ArtifactId] = []
    final_opinion: ScientificOpinion | None = None
    final_report_artifact_id: ArtifactId | None = None
    delivered_answer_ids: list[QuestionId] = []
    llm_calls_used: int = 0
    completion_violations: list[CompletionViolation] = []
```

`scientific_observed_artifact_ids` 是 ResearchController 对每次 ScientificTurnResult.observed_artifact_ids 做 Registry/run 复核后的稳定去重并集，不是原始 Session event 副本。active WorkRequest 从 work_requests 中唯一的 requested/compiling/executing/stable 项派生，不另存第二个可漂移字段。

`delivered_answer_ids` 只记录已成功投递给 ScientificPort 的 question_id，恢复时仅发送新增 UserAnswer，避免重启后重复注入历史回答。`llm_calls_used` 只累计 ScientificTurnResult.llm_calls；字段语义和不覆盖其他子 Agent 的限制见 §20.7。

`completion_violations` 仅在 ResAgent 复核 ScientificCompletedResult 失败时写入，保留结构化 contract_error 证据；验证通过时必须为空。它不保存 Session 私有 event，也不作为下一次 LLM 输入。

final_opinion 只在 ScientificCompletionValidator 通过后写入；final report 随后由 deterministic renderer 产生并以 producer=orchestrator、metadata.source_type=final_report 登记，最后写 final_report_artifact_id。RunStatus 只有在上述写入全部成功后才改 completed。

### 20.10.2 ScientificCompletionValidator

它是 orchestrator 内部纯验证器，不调用 LLM，不读取 Session 私有 event。`validate` 输入同一个不可变 ResearchRun snapshot 和 ScientificCompletedResult；snapshot 中的 `run.artifacts` 就是 Artifact Registry 已持久化的只读索引，CapabilityRegistry 在构造 Validator 时注入以复核 capability owner。输出 CompletionValidation：有 report 且 violations 为空表示通过，否则按 contract_error 处理。

```python
class CompletionViolationCode(StrEnum):
    INVALID_SESSION = "invalid_session"
    ACTIVE_CONTROL_STATE = "active_control_state"
    INVALID_OPINION = "invalid_opinion"
    UNKNOWN_EVIDENCE = "unknown_evidence"
    UNOBSERVED_EVIDENCE = "unobserved_evidence"
    UNACKNOWLEDGED_TASK = "unacknowledged_task"
    MISSING_LIMITATIONS = "missing_limitations"
    INCONSISTENT_TASK_RESULT = "inconsistent_task_result"

class CompletionViolation:
    code: CompletionViolationCode
    message: NonEmptyStr
    related_ids: list[NonEmptyStr] = []

class FinalReportData:
    run_id: RunId
    goal: NonEmptyStr
    opinion: ScientificOpinion
    evidence: list[ArtifactRef]
    execution_issues: list[WorkTaskOutcome] = []

class CompletionValidation:
    violations: tuple[CompletionViolation, ...] = ()
    report: FinalReportData | None = None
```

验证顺序固定为：

1. completed result/session/run 绑定正确，SessionStatus=completed；
2. 无 active WorkRequest、PendingQuestion、running Task；
3. opinion 通过 verdict/evidence/acknowledged_task_ids 组合约束；
4. 每个 evidence Artifact 属于本 Run、Registry 可查，并同时出现在 result.observed_artifact_ids 与 run.scientific_observed_artifact_ids；
5. 所有 failed/blocked Task 被 acknowledged，且存在 limitations；
6. 每个 completed Task 都有最后一个 completed/completed-with-warnings Attempt、无 error，Attempt.artifact_ids 全部可在 Registry 查到且 producer 与 binding owner 一致；
7. 不接受未知、重复或跨 Run 的 ID。

通过时 Validator 产出 FinalReportData；evidence 顺序与 opinion.evidence_artifact_ids 一致，execution_issues 只含仍 failed/blocked 且已 acknowledged 的 Task。纯 renderer 只消费 FinalReportData，生成 `kind=final_report`、`media_type=text/markdown` 的 ArtifactCandidate，不接受额外自由文本输入。

Validator 不重跑 Coding/Experiment finalizer，也不解释其领域 payload。Scheduler 只有在已绑定原生 ModulePort 的 finalizer 返回并通过 ModuleResult 外层校验后才能写 Task completed；finalizer 自身正确性由对应 Agent 单元/E2E 测试保证。Validator 这里只复核持久化 Task/Attempt/Artifact 没有绕过该状态路径。

**owner 单一来源约束**：gate 6 的「producer 与 binding owner 一致」中，`binding owner` 由 Validator 从 `CapabilityRegistry.definitions[capability].owner` 读取；而 Artifact 的 `producer` 由 Scheduler 从 `ModuleBinding.owner` 写入。两者目前没有机制保证一致——若 composition root 把 Registry 的 owner 与 ModuleBinding 的 owner 配成不同值，合法 completed Task 会被误判为 `inconsistent_task_result`。7.7 切 production 时，ModuleBinding.owner 必须从同一个 CapabilityRegistry 派生（单一来源），否则验收会因此产生假失败。

Validator 也不判断 statement 是否科学正确，或证据语义上是否足以支持 verdict。那属于 Scientific Agent 能力与离线/在线 eval，而不是 Run 状态机。

**实现状态（7.6）**：`orchestrator/completion.py` 已实现结构化 violation、固定顺序复核、FinalReportData 和纯 Markdown renderer；`ArtifactRegistry.register_final_report` 使用稳定 ArtifactId、内容 hash 与原子替换，支持“文件已落盘但 Run 尚未保存”后的同内容幂等重试。幂等仅依赖磁盘内容 hash 一致（`destination.exists() 且 _sha256 == expected_digest` 时复用），不接受调用方传入“上次结果”引用——controller 注册前 `run.artifacts` 尚未含 final_report，故不设 `existing` 参数，避免一个恒为 None 的死参数及其跨进程 URI 相等比较的隐患。ResearchController 只有在验证、渲染、Artifact 登记和 Run 字段写入全部成功后才写 completed。production composition root 的切换仍属于 7.7。

### 20.11 ScientificTurnResult → RunStatus 映射

| ScientificTurnResult.status | SessionStatus | ResearchController 行为 | RunStatus |
|---|---|---|---|
| request_work | paused | 复核 observed trace，持久化 assessment，创建 status=requested 的 WorkRequest | running |
| needs_user_input | paused | 复核 observed trace，持久化 assessment/PendingQuestion | paused |
| completed | completed | 复核并合并 observed trace，调用 ScientificCompletionValidator；通过后保存 opinion/报告 | completed |
| failed | failed 或空 | 复核可用 observed trace，保存 ModuleError；AgentLoop 内部可恢复机会已耗尽 | failed |

completed 若未通过 ScientificCompletionValidator，不得写 Run completed。因为 ScientificPort completion check 应先检查同一 snapshot，这种不一致属于 `contract_error`，Run failed 并保留两层验证证据；不允许从 completed Session 静默继续。

WorkRequest status 不映射为新的 RunStatus：requested/compiling/executing/stable/consumed 期间 Run 都是 running。只有 PendingQuestion 使 Run paused。

### 20.12 Scientific Tool 与 observation trace（非 wire 公共模型）

这些类型属于 capabilities/Scientific Tool 实现，不进入 `resagent2_contracts` 公共导出；写在这里是为了锁定跨边界语义，避免实现自行裁决。

```python
class ReadArtifactToolInput:
    artifact_id: ArtifactId

class ReadArtifactToolResult:
    artifact_id: ArtifactId
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    media_type: NonEmptyStr
    content: str
    truncated: bool

class LiteratureSearchToolInput:
    query: NonEmptyStr
    max_results: int  # 1..20
    start_year: int | None = None
    end_year: int | None = None

class LiteraturePaper:
    paper_id: NonEmptyStr
    title: NonEmptyStr
    authors: list[NonEmptyStr]
    published_at: date | None
    abstract: str
    source_url: NonEmptyStr

class LiteratureSearchToolResult:
    artifact: ArtifactRef
    papers: list[LiteraturePaper]

class ArtifactRegistrationPort(Protocol):
    def register_scientific(
        self,
        candidate: ArtifactCandidate,
        *,
        run_id: RunId,
        session_id: SessionId,
    ) -> ArtifactRef: ...
```

`read_artifact` 只能读取 ScientificTurnRequest.authorized_artifacts，读取前按 Registry 记录复核 run_id/hash；只支持配置允许的 text/json 媒体类型，二进制或超限内容返回明确 Tool error，不能返回空字符串冒充成功。截断上限由 composition root 配置，不由 LLM 扩大。

`literature_search` 成功时将规范化 papers JSON 作为 ArtifactCandidate，经注入的 ArtifactRegistrationPort 以当前 run/session 登记，再返回 ArtifactRef；网络错误、限流和注册失败都返回 Tool error。该 Protocol 只依赖 contracts 类型，capabilities 不 import orchestrator；composition root 注入由 ArtifactRegistry 实现的 adapter。成功的 ReadArtifactToolResult.artifact_id 与 LiteratureSearchToolResult.artifact.id 被 runtime 记录在 trusted Tool observation；ScientificPort finalizer 只从这两种成功结果生成 observed_artifact_ids。

### 20.13 schema 2.0 原子迁移规则

1. Phase 7 是一个未发布迁移单元；7.1—7.6 的中间包不得发布或合并到对外稳定分支；
2. 7.1 的同一个原子变更先增加 2.0 新类型，再删除 success_criteria、改名 compilation_rationale、收紧 Workflow.created_from，并同步修改 planning.py、scheduler.py、所有 fixture/tests；旧 PlanningPort 临时填 `work_legacy_initial`，repair fixture 使用 `work_legacy_repair_<n>`，这些 ID 不对应 WorkRequest、不得进入新 ResearchController；该提交结束时全仓必须可导入、测试可运行；
3. ScientificPlanInput、ScientificAnalyzeInput、LiteratureSearchInput、ExperimentPrepareInput、AskUserInput、ScientificConclusion 和旧 capability enum value 在 7.1 只标记 deprecated，保留给唯一旧 production 路径；WorkflowProposal.questions 在 7.1 与旧 create_run questions 分支同步删除；
4. 7.2—7.6 的新组件只能由测试/composition fixture 装配，production composition root 仍只走旧路径，不能同时路由新旧 Scientific；
5. 7.7 在一个原子切换中启用新 ResearchController/ScientificPort，删除 PlanningPort、DeterministicPlanningPort、`LegacyScientificAnalyzeAdapter`、上述 deprecated 类型/枚举/字段和旧 binding，并更新所有调用者；
6. `SCHEMA_VERSION` 在 7.1 原子提交中切到 `"2.0"`，但 2.0 只有在 7.7 删除临时兼容符号、全仓/E2E 通过后才冻结并可发布；
7. schema 2.0 loader 明确拒绝 1.1 wire/JsonRunStore，返回版本错误；不做静默字段丢弃；
8. 如需保留 schema 1.1 历史，只提供独立只读导出脚本，不把双版本迁移逻辑塞进 Scheduler；
9. 公共导出核对表在 7.7 原子切换后更新为 2.0；任何中间提交都不得留下 import error 或红色全仓测试。

**7.7 状态（2026-08-28）**：第 5 条的原子切换已完成——`ResearchController` + 原生 `ScientificAgent` + `LLMWorkflowCompiler` 成为唯一 production composition root，PlanningPort/`DeterministicPlanningPort`/`LegacyScientificAnalyzeAdapter` 及全部 deprecated scientific/planning/ask_user/experiment_prepare 类型与 enum 值已删除，公共导出核对表（§18）已更新为 2.0。全仓本地测试通过；schema 2.0 仍待服务器真实 E2E 通过后冻结（第 6 条）。

## 21. 工作区与缓存契约（7.7 Hardening）

本阶段把「代码细节归 CodingAgent」与「统一工作区」落为契约，见 ADR-0008。

### 21.1 工作区来源与记录

```python
class WorkspaceSourceKind(StrEnum):
    GIT = "git"              # location 是 Git URL，clone 到受管目录
    LOCAL = "local"          # 直接绑定已有本地目录，managed=False，不搬运
    COPY = "copy"            # 复制已有本地 Git 工作树到受管目录
    GENERATED = "generated"  # 创建空的受管工作区

class WorkspaceSpec(ContractModel):
    workspace_id: str
    source_kind: WorkspaceSourceKind
    location: str | None = None

class WorkspaceRecord(ContractModel):
    workspace_id: str
    root: NonEmptyStr                # 已解析的物理目录
    source: WorkspaceSpec
    managed: bool                    # True = ResAgent2 创建并管理
    initial_commit: str | None = None
```

`WorkspaceSource`（EXISTING/CLONE）废弃，其语义并入 LOCAL/GIT。`WorkspaceSpec` 是逻辑来源声明，不保存物理路径；`WorkspaceRecord` 是解析后的记录。一个 Run 可以有多个工作区，同一个 `workspace_id` 可被多个 Task/Attempt 复用。

### 21.2 Task 的工作区归属

`TaskProposal` 与 `WorkflowTask` 各增加 `workspace_id: str`（在 TaskProposal 中允许 Compiler 省略，由确定性校验层在单一工作区时自动填入）。Compiler 只能从给定的逻辑 `workspace_id` 集合中选择，不能编造；validator 检查 workspace_id 是否存在于 `ResearchRun.workspaces`。

### 21.3 CodeModifyInput 简化

```python
class CodeModifyInput(ContractModel):
    capability: Literal[Capability.CODE_MODIFY]
    instructions: NonEmptyStr
    suggested_paths: list[str] = []   # 仅提示，不是权限
```

删除 `allowed_paths`（与 `WorkspaceGrant.allowed_paths` 重复）和 `verification_commands`（与 Compiler 职责重叠）。最终权限以 `WorkspaceGrant` 为准。Coding Agent 根据项目实际自行选择 shell-free 验证命令，经 `VerificationCommandPolicy`（默认只允许 pytest/py_compile 等测试运行器，拒绝破坏/包管理/网络/Shell 命令）与 `ProcessRunner` 的结构化解析执行。

### 21.4 Run 数据与共享缓存目录

`RunLayout` 只负责根据 `data_root` + `run_id` 返回标准目录（`runs/{run_id}/state`、`runs/{run_id}/workspaces/{ws}/`、`runs/{run_id}/attempts/{task}/attempt_{n}/`、`scientific/sessions/`、`artifacts/`），归 orchestrator；`ResourceLayout` 负责共享缓存目录（`resource_root`、`dataset_root`、`env_root`，`models/` 预留），归 capabilities。contracts 只保留跨模块数据模型，不读取环境变量、不决定物理目录。它们不承载调度逻辑，RunLayout 不管数据集，ResourceLayout 不管 Run 状态。

`RepoMaterializer` 的来源元数据不再写进目标源码仓库（删除 `.resagent2/materialized_source.json`），改写到 `runs/{run_id}/workspaces/{workspace_id}/workspace.json`。

### 21.5 数据集引用

`dataset_root`（`ResourceLayout.dataset_root`）永远表示「所有数据集的公共根目录」，不表示某个具体数据集。任务级数据集用 `DatasetRef(dataset_id, relative_path)` 声明：运行时把 `relative_path` 解析到 `dataset_root` 下，拒绝 `..`/绝对路径逃逸、检查存在、默认只读，再把解析结果传给 Experiment Agent。`ResearchRequest.dataset_refs` 是**唯一数据集注册表**，Scheduler 经 `ModuleTaskRequest.dataset_refs` 传给 Experiment Agent；`ExperimentRunInput` 不再携带 `dataset_refs`，避免静默覆盖（ADR-0011 §4）。

解析结果是 `{dataset_id, path, access="read_only"}` 列表；重复的 `dataset_id` 直接拒绝（同一 id 不得解析到两个路径）。Experiment Agent 用通用环境变量把映射交给脚本——`RESAGENT2_DATASET_ROOT`（公共根目录）与 `RESAGENT2_DATASETS_JSON`（`{dataset_id: 绝对路径}` 的 JSON）——核心代码不绑定任何框架，也不假定存在「第一个/默认数据集」；脚本按 `dataset_id` 查表取用所需数据集。

## 22. 运行时反馈与连续失败保护（recovery-loop hardening）

`ToolObservation.ok` 是机器可读的成功标志：成功读取/命令为 True，失败命令（非零退出）、参数拒绝、路径缺失等可恢复失败为 False。下游不得靠解析 `summary` 文本判断失败。

AgentLoop 的反馈语义：

- 可恢复失败（工具抛异常、参数校验失败、completion check 拒绝、未观察证据拒绝）落为持久 `runtime_feedback`（`ok=False`），并在后续每轮作为最高优先级 required 上下文注入；普通 observation 不覆盖它（`last_observation` 是独立槽位）。
- `recent_observations` 是有界最近历史（默认 6 条），用 head+tail 截断序列化值，保证末尾错误字段（如 `stderr_tail`）不丢；每条标注 ok/FAILED。
- 连续失败计数：成功的非 finish 工具（`read_file`/`list_files`/`read_artifact` 等）重置；`ok=False` 累加；finish 工具的 `ok` 不重置（是否成功由 completion check 决定）；completion check 拒绝的 finish 也累加。连续 5 次失败返回 `TOOL_FAILED`，先于 step 预算。

## 23. 共享环境契约（Agent 自主选型）

环境能力由 Coding 与 Experiment 共用（ADR-0009）。契约要点：

- `EnvironmentSpec.python_version: str | None = None`：有值表示用户/上游硬约束，Agent 不得静默覆盖；为空表示 Agent 依据项目自行判断。
- `ModuleTaskRequest.environment_spec` 承载上游声明；删除 `ExperimentRunInput.python_version`（Experiment 专属字段收归通用 `environment_spec`）。
- 环境归属 `run_id + workspace_id`：同 Run 同 Workspace 共用（Coding/Experiment 共用、Task 重试复用）；不同 Workspace/Run 隔离；不跨 Run 复用可变环境。`env_id = resenv_<sha256(run_id + "\0" + workspace_id)[:12]>`。
- `EnvironmentManager` 接口 `inspect`/`prepare`/`audit`；`PreparedEnvironment(env_id, prefix, python_version)` 不进入跨模块 contracts，属 capabilities 的公开 Python API。
- 三个共享 Tool（capabilities 的公开 Python API，不进入跨模块 contracts）：`prepare_environment`（校验版本、创建/复用并绑定环境，切换版本真实删除重建）、`run_setup`（允许 `pip install -r`/`-e .` 与 `conda env update -f`——conda 由工具注入 `-p <绑定 prefix>`；禁止 `sudo`、`conda create/remove`、指定其它 `--prefix/-p/--name/-n`；暂不支持 uv/poetry）、`audit_env`（证明基础环境正确：sys.prefix 匹配 + pip 可用 + 实际版本满足绑定版本，不硬编码具体框架）。
- Python 版本优先级、硬约束不可覆盖、每 Attempt 最多两次版本切换：见 ADR-0009。
- 半成品基础环境：目录不存在 → 创建；基础检查失败 → 确认在受管 env_root 内 → 删除 → 重建。marker `.resagent2_base_ready` 只表示基础 Python 健康，不代表项目依赖装完。
- 任何被允许并开始执行的 `run_setup` 命令（无论成功或失败）都使旧 audit 失效（`env_certified=False`），须重新 `audit_env`。
