# ResAgent2 跨模块契约

**文档角色**：跨模块 wire 对象的字段、类型、组合约束和版本的唯一事实来源。

**语义上级**：`ARCHITECTURE.md`；本文件不得改变其中的模块职责和控制流。

**当前实现**：`resagent2-contracts 0.1.0`，wire schema `4.0`（`SCHEMA_VERSION="4.0"`）。schema 1.0/1.1/2.0/3.0 的历史演进记录在 `DEVELOPMENT_PLAN.md`、`docs/decisions/` 与 `docs/reviews/`，本文件只描述当前 4.0。schema 4.0 是一次 clean break：3.0 及更早的 `state/`、`sessions/` 是开发/测试产物，升级后不可恢复，应删除或归档后重新发起 Run（不实现迁移或 fallback）。

## 1. 使用规则

本文件回答「模块之间传什么、字段准确表示什么」。

从模块理解接口，请先看 [ARCHITECTURE §8](ARCHITECTURE.md#8-模块职责)；调用、返回、所有权、恢复和副作用见 [六张接口说明卡](INTERFACES.md)。替换实现既要满足类型，也要满足接口卡中的调用和接收规则；测试是实际边界行为的证据。

- 架构概念和谁调用谁，以 `ARCHITECTURE.md` 为准；
- Python 字段必须与 `packages/contracts/src/resagent2_contracts/models.py` 一致；
- 代码与本文字段不一致时，视为 contract bug；
- 本文写了目标语义但代码尚未强制时，必须明确标为「未实现约束」；
- runtime 内部的 `AgentDefinition`、`AgentAction`、`FinishCandidate`、Tool 和 Context 类型不属于跨模块 wire contract，不在本文件定义；Scientific 的 `ReadArtifactTool` / `LiteratureSearchTool` 的 Tool 形状属于 capabilities 实现，只在 §16 锁定跨边界语义，不加入 contracts 公共导出。

所有公共模型：

- 继承严格 `ContractModel`（`extra="forbid"`）；
- 序列化 `schema_version: "4.0"`（`ContractModel.schema_version: Literal["4.0"]`）；
- 以下示意代码省略每个模型继承得到的 `schema_version`，但 wire 数据不能省略其版本语义。

## 2. 跨模块对象范围

| 边界 | 请求 | 响应/状态 |
|---|---|---|
| 用户 → Orchestrator | ResearchRequest、UserAnswer | PendingQuestion、`final_report` ArtifactRef |
| Orchestrator → Scientific Agent | ScientificTurnRequest | ScientificTurnResult |
| Scheduler → 专业模块 | ModuleTaskRequest | ModuleResult |
| 专业模块 → Artifact Registry | ArtifactCandidate | ArtifactRef |
| Orchestrator 持久化 | Workflow、WorkflowTask、Attempt、PendingQuestion | ResearchRun 属于 orchestrator 内部模型 |

Orchestrator ↔ Scientific Agent 使用 `ScientificTurnRequest` / `ScientificTurnResult`；Scientific Agent 只提出 `WorkRequestDraft`，`WorkflowProposal`/`WorkflowPatch` 由 Orchestrator 内部的 WorkflowCompiler 产生。禁止跨模块读取另一个模块的内部 Session state、私有目录或 prompt；禁止从 summary 文本推断机器状态；禁止把任意 dict 作为长期接口。

## 3. ID 命名空间

| 类型 | 格式示例 | 范围 |
|---|---|---|
| RunId | `run_example` | 全局唯一 |
| TaskId | `task_experiment` | Run 内唯一且跨 revision 稳定 |
| SessionId | `session_coding_1` | SessionStore 内唯一；执行 Session 身份包含 Run、Task、Attempt |
| ArtifactId | `artifact_metrics` | Run 内唯一；执行工件保留 Task/Attempt 归属，科学与导入工件可按内容幂等 |
| QuestionId | `question_dataset` | Run 内唯一 |
| WorkRequestId | `work_x` | Run 内唯一 |
| WorkspaceId | `ws_main` | Run 内唯一 |

前缀后由字母或数字开头，只允许字母、数字、下划线和连字符，总长度由实现约束限制。ID 不能互换：TaskId 不是 SessionId，Attempt number 也不是 Agent step number。

## 4. 状态与所有权

### 4.1 RunStatus

```text
pending | running | paused | completed | failed
```

由 Orchestrator 写入。planning、replanning、interrupted 不是 RunStatus。

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

ModuleStatus 的六个结果值中，前五个是 AttemptStatus 的子集；`request_work` 只由 ScientificPort 产生，不是 Attempt 状态。AttemptStatus 另外包含 `running`（共六个值）。ModuleStatus 由模块返回；AttemptStatus 由 Scheduler 根据已校验的 ModuleResult 记录。

## 5. ResearchRequest

```python
class ResearchRequest:
    goal: NonEmptyStr
    hypothesis: NonEmptyStr | None = None
    context: str = ""
    constraints: list[NonEmptyStr] = []
    input_artifacts: list[ArtifactImport] = []
    dataset_refs: list[DatasetRef] = []
    required_evidence_kinds: list[RequiredEvidenceKind] = []  # 当前仅 literature_search
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
| required_evidence_kinds | 必须被已登记、已观察且最终引用的 Artifact.kind 满足；约束证据种类，不强制某次 Tool 调用，导入证据也可满足 |
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
| dataset_refs | 本 Task 继承的 Run 已注册数据集集合（Scheduler 确定性写入，非 LLM 生成；当前不提前设计任务级子集） |
| answers | 只包含属于本 Task 的已持久化回答 |
| workspace / workspace_id / workspace_spec | 此 Attempt 的物理授权范围、逻辑工作区 id、来源声明（Agent 在 loop 前确定性 materialize） |
| environment_spec | 上游声明的环境硬约束（`EnvironmentSpec.python_version`） |
| output_dir | code_modify / experiment_run 的输出目录 |
| parent_session_id | 仅显式 resume 使用；普通 retry 为空 |

`workspace_spec` 存在时要求 `workspace` 非空、`workspace_id` 与 `workspace_spec.workspace_id` 一致、`workspace.source` 与 `workspace_spec.source_kind` 一致。ask-user 后的 resume 产生 `parent_session_id`；runtime AgentLoop 在 resume 时加载该 Session 并校验 run/task/attempt/agent/owner 和可恢复状态；执行会话的 ID 也按 Run/Task/Attempt 隔离。普通 retry 不复用 Session。

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

`summary` 是解释性文字，可供人类展示，也会经 WorkOutcome/interpreter 投影给 Scientific 模型参考；**不能用于机器状态判定或替代真实证据**。`payload` 是 capability 专有数据。`llm_calls` 是本次模块返回对应的实际新增 LLM 调用数（含 HTTP 重试），供 Run 使用账本累计；同 Attempt 多次问答续跑时不能重复报告 Session 历史总数。Scheduler 将 payload 持久化到对应 Attempt，供审计和恢复后读取，但不解释其中的领域语义，也不从 payload 推断状态。

Scheduler 同时校验通用外壳和该 capability 的成功 payload 模型；不合规结果转为 contract_error，而非完成任务。Artifact 注册失败也保留新增 `llm_calls`、Session 和已取得的结果；已有 ModuleError 的根因保留，注册错误附在 details 中。原生 finalizer 负责真实工具证据，接收端负责公共结果约束，两者不是互相替代，见 [I3](INTERFACES.md#i3-执行任务)。

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
- 期望指标按规范化后的完整名称匹配，不做子串匹配：`accuracy` 不能满足 `balanced_accuracy` 或 `baseline_accuracy`。完整 JSON 证据集中，同一规范名出现不同数值会拒绝本次 finish，要求区分指标键；重复同值可接受，缺失交付项仍走既有 warnings。
- `ExperimentRunInput` 仍保留 `parameters`（实验配置参数），但 `ExperimentResult` 不再有 `parameters` 字段（删除，无 production 消费者）。
- **实验输入的两种语义**：`instructions` 表达实验及证据要求，也包括失败时才需交付的诊断；`expected_metrics` 只放已知精确 JSON 指标键（规范化后完整匹配），`expected_artifacts` 只放已知实际 workspace 相对路径。"metrics output file" 这类描述不是精确路径。LLMCompiler 无 typed 精确名称上游，确定性物化时清空草图中的两个数组，把错放的非空描述降为当前任务 instructions 中的语义说明；公开字段仍供可信直接/确定性调用方使用。
- **最低交付门槛不因空数组而消失**：原生 Experiment finish 必须有成功实验命令、有效 Attempt 基线，以及至少一个本次新增/变化的真实 evidence 文件。没有精确名称不是可以只给 summary 的豁免。机器门槛不自动证明自然语言目标完整达成；Scientific 根据已读证据形成判断。
- 原始执行日志是 `run_command` 实际记录的 stdout/stderr。Agent 根据 metrics 写的报告是派生说明，不能作为一份独立的原始日志佐证；此用法在 prompt 中明确，不宣称可确定性判别任意文件的全部来源。

```python
class ModuleError:
    code: ErrorCode
    message: NonEmptyStr
    retryable: bool
    details: dict[str, JsonValue] = {}
```

ErrorCode 固定枚举：invalid_input、permission_denied、tool_failed、timeout、budget_exhausted、contract_error、environment_unavailable、artifact_missing、interrupted。`interrupted` 只由 Orchestrator 恢复逻辑写入，表示 Attempt 已持久化为 running、但进程在 ModuleResult 写回前退出；它是 retryable failure，不是新的 TaskStatus。

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

Attempt 属于 Orchestrator 历史，Session 属于子 Agent。retry（failed/blocked 后重试）是同一 Task 的新 Attempt，默认新 Session；pause/resume 是**同一 Attempt** 的暂停与继续，不增加 Attempt number，复用 Session/output_dir/workspace snapshot（ADR-0011 §2）；repair 是新 WorkflowTask。进程中断不是第三种 AttemptStatus：恢复时把遗留 running Attempt 结算为 `failed + ErrorCode.interrupted + retryable=True`，再按原有 Attempt 预算决定 Task 是否回到 pending（ADR-0012）。

`running` 与 `needs_user_input` 都是非终态：不能有 finished_at/error；终态（completed/completed_with_warnings/failed/blocked）必须有 finished_at；failed/blocked 必须有 error；其他终态不能有 error。`payload` 是模块返回的能力专属结构化结果，随 Attempt 持久化，不被静默丢弃；失败时可为空；若完成结果在后续 Artifact 登记阶段失败，已验过的 payload 可保留作诊断，但不把 failed 状态变成成功。

## 12. Question 与 Answer

```python
class QuestionDraft:
    text: NonEmptyStr
    requested_fields: list[NonEmptyStr] = Field(min_length=1)
    reason: NonEmptyStr

class PendingQuestion:
    id: QuestionId
    run_id: RunId
    task_id: TaskId | None = None
    text: NonEmptyStr
    requested_fields: list[NonEmptyStr] = Field(min_length=1)
    created_at: datetime

class UserAnswer:
    question_id: QuestionId
    values: dict[NonEmptyStr, str] = Field(min_length=1)
    answered_at: datetime
```

子 Agent 只生成 QuestionDraft；Orchestrator 分配 ID、持久化 PendingQuestion、暂停 Run、校验 Answer 并恢复。同一 Attempt 的连续两个新问题使用不同 ID；重复提交已处理问题的答案不能满足下一问题。`reason` 是必填字段。问题必须声明至少一个答案字段（`requested_fields` 非空）：开放问题用 `["answer"]`、确认问题用 `["confirmation"]`、指标选择用 `["primary_evaluation_metric"]`；`UserAnswer.values` 同样非空——不允许「问了问题却不知道把回答保存在哪里」。

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

Candidate 的 path 必须是无 `..` 的相对路径；普通文件相对于授权 workspace root，带 `content` 的派生文本（如 patch）则以 path 作为工件内文件名。Candidate 故意没有 id、URI、hash 或 provenance；这些由 Orchestrator 登记时产生。普通大文件仍走 workspace path + ArtifactRegistry 冻结。

ArtifactRef 的 provenance 有三种互斥形状：

- **执行 Artifact**：`task_id` 与正整数 `attempt_number` 同时存在，`session_id` 为空；登记/接收端将 producer 与任务 binding owner 对齐，当前执行 owner 是 coding/experiment；
- **Scientific Tool Artifact**：producer 为 scientific，`session_id` 存在，`task_id` 与 `attempt_number` 同时为空；
- **Orchestrator Artifact**：producer 为 orchestrator，三者都为空，且 `metadata.source_type` 必须是 `import` 或 `final_report`。

模型拒绝混合 Session 与 Task 字段、缺半个 task/attempt、非正 attempt 等非法形状；任务 owner 与当前 Run 的一致性还由登记和接收边界检查。所有 Artifact 必须有所属 run_id、Registry 计算的 sha256 和冻结 uri。读取器对静态授权和动态解析的 Ref 都在读文件前核对请求的 ArtifactId 与当前 run_id，再检查本地 URI 和 hash；不能先把跨 Run 内容交给模型，再等最终 gate 拒绝。

`register_scientific` 生成的 JSON 按 `indent=2` 编码后计算 SHA256 并冻结，避免结构化文献列表整体变成超长单行、后文无法按行取回。数据含义不变；格式变化产生的新字节拥有对应的新 hash，旧 Artifact 不重写，reader 不在 hash 校验前后悄悄改写内容。

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

`WorkspaceSpec` 是逻辑来源声明，`location` 可包含仓库 URL 或本地来源路径，但不是 Attempt 的物理授权；`environment` 是 workspace 级的环境约束（上游指定 Python 版本时为硬约束）。`WorkspaceRecord` 是解析后的记录，`managed` 由 source_kind 派生（非 LOCAL 为 True）。`WorkspaceDescriptor` 是 Compiler 可见的最小工作区摘要，不含物理路径。

capabilities 提供一个内部 `WorkspaceSnapshot`（Git workspace 用 `GitBaseline` 的 tree hash，非 Git workspace 用有界 file-hash fallback）表达 Attempt 起点；GitDiffTool、Coding finalizer、failed patch 与 Experiment evidence ownership 都消费同一个 snapshot，删除 HEAD-relative legacy diff API（ADR-0011 §4）。

## 15. Dataset 与环境

```python
class DatasetRef:
    dataset_id: NonEmptyStr
    relative_path: str

class EnvironmentSpec:
    python_version: str | None = None
```

`dataset_root`（`ResourceLayout.dataset_root`）永远表示「所有数据集的公共根目录」。部署者只在根目录的 `catalog.json` 中维护一个 `dataset_id → relative_path` JSON 对象；CLI 组合根把 `DatasetCatalog` 作为只读 Port 注入 `ResearchController`，Controller 在创建/恢复执行时把新注册资源合入 Run 的 `ResearchRequest.dataset_refs`，调用 CLI 的普通用户不逐次提供物理路径。catalog 缺失表示未注册数据集；格式错误、越界路径或已注册目录不存在会在 Run 执行前失败。已有同名绑定不可在 Run 中改写，目录新增则可在 `ask_user` 后继续同一 Run。

`DatasetRef` 把 `relative_path` 解析到 `dataset_root` 下，拒绝 `..`/绝对路径逃逸，默认只读。`ResearchRequest.dataset_refs` 仍是 Run 内**唯一**数据集注册表，Scheduler 经 `ModuleTaskRequest.dataset_refs` 同时传给 Coding 与 Experiment；`ExperimentRunInput` 不携带 `dataset_refs`。三个 Agent 看到同一份不允许下载/替换、缺失时 `ask_user` 的目录上下文；Coding 验证命令与 Experiment 实验命令都获得 `RESAGENT2_DATASET_ROOT` / `RESAGENT2_DATASETS_JSON`。解析结果仍是 `{dataset_id, path, access="read_only"}` 列表，重复 `dataset_id` 拒绝。

环境能力由 Coding 与 Experiment 共用（ADR-0009）：

- `EnvironmentSpec.python_version` 有值表示硬约束，Agent 不得静默覆盖；为空表示 Agent 依据项目自行判断；
- 环境归属 `run_id + workspace_id`：同 Run 同 Workspace 共用（Coding/Experiment 共用、Task 重试复用），不同 Workspace/Run 隔离；`env_id = resenv_<sha256(run_id + "\0" + workspace_id)[:12]>`；
- 三个共享 Tool（capabilities 的公开 Python API）：`prepare_environment` / `run_setup` / `audit_env`。新绑定或真正开始 prepare/setup 时，`EnvironmentBinding.generation` 更新且 `certified=False`；执行成功、失败或抛异常都不能保留旧认证，参数/策略拒绝则不改变代次；
- Coding 的成功验证还须属于最新 edit revision、当前已审计的 generation。setup 后或新进程恢复后，只重新 audit 不会让旧验证复活，必须再验证；
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

ScientificPort 是唯一 Scientific Agent 边界。Controller 在消费响应前，按四种结果分支复核结构、Session 的 owner/status/已绑定身份、观察集合和 assessment/opinion 的引用关系；失败分支可没有 Session，但必须保留 error。非法响应不会替换已有 Session、消费待交付 WorkOutcome 或把答案标为已交付；可确认的实际新增调用数仍计入预算。

work_outcome 按 work_request_id、answers 按 question_id 幂等：重复投递返回已持久化结果，不能重复追加 observation 或重复调用 LLM。

### 16.5 WorkflowCompiler 边界

WorkflowCompiler 的输入是 `WorkRequest`、CapabilityRegistry、预算、当前 Workflow 与逻辑工作区摘要；返回内部 `CompilationResult(output, llm_calls)`，其中 output 是 `WorkflowProposal` 或 `WorkflowPatch`。这层计量包装不新增跨模块 wire 类型。

production `LLMWorkflowCompiler` 不让 LLM 直接输出 Proposal/Patch：LLM 只输出 orchestrator 内部的 `CompilationDraft`（顶层 summary/rationale + 每任务 key/capability/goal/depends_on/workspace_id/constraints/inputs），再由确定性 `_materialize_draft` 分配全局 TaskId、绑定 `work_request_id`、解析 workspace、转换局部依赖并产出 Proposal（首轮）或只追加 Patch（修复轮）。每个草图只表示当前可执行的一轮，不预编译依赖本轮失败才需要的条件任务；失败经 WorkOutcome 返回 Scientific 后另建修复 WorkRequest。

结构校验通过后再做一次语义审查（`CompilationReview`），同时检查遗漏前置条件和多余条件任务。结构拒绝与语义拒绝共享一次带精确反馈的纠错重编，总计最多两版草图，每版最多一次 review；仍失败则 Compiler 抛 CompilationError，由 Controller 将 WorkRequest/Run 置为 failed。图模型、Compiler 与 Scheduler 分担身份、revision、DAG、能力、预算和 inputs 检查，不是三套完全相同的 validator。精确入口和失败计量约定见 [I2](INTERFACES.md#i2-工作编译)。

### 16.6 ScientificCompletionValidator 与 final report

Validator 是 orchestrator 内部纯验证器，不调用 LLM，不读取 Session 私有 event。输入 ResearchRun 与 ScientificCompletedResult，内部构造经校验的 Run 深拷贝；输出 CompletionValidation（结构化 violations 或 FinalReportData）。检查包括：completed result/session/run 绑定正确 → 无 active WorkRequest/PendingQuestion/**pending、running 或 needs_user_input Task** → opinion 通过组合约束 → 每个 evidence Artifact 属于本 Run，且同时出现在 result.observed_artifact_ids 与 Run 已复核集合 → 从 Run 确定性对账 failed/blocked Task，要求存在错误记录及 opinion.limitations → 每个 completed Task 有合法终态 Attempt、无 error、artifact producer 与 binding owner 一致 → 拒绝未知/重复/跨 Run 的 ID。Scientific 不再上报 acknowledged_task_ids，执行问题的精确身份由 Validator 生成。

原生 ScientificCompletionCheck 先根据 Session 工具记录、未解决工作及证据要求检查候选；它不持有完整 ResearchRun。最终 gate 再独立验收完整 Run。两处复用证据种类的纯判据：从本 Run 已登记 Artifact 中确认所需 kind 同时被观察和引用，导入证据与新生成证据遵循同一规则；不能把两处描述为输入完全相同，见 [I1](INTERFACES.md#i1-科学决策)。

通过时 Validator 产出 `FinalReportData`（run_id、goal、opinion、evidence、execution_issues），纯 renderer 只消费 FinalReportData 生成 `kind=final_report`、`media_type=text/markdown` 的 ArtifactCandidate。RunStatus 只有在验证、渲染、Artifact 登记和 Run 字段写入全部成功后才改 completed。Validator 不判断 statement 是否科学正确。

## 17. 状态映射

| ModuleStatus | Scheduler 写入的 TaskStatus | Run 行为 |
|---|---|---|
| completed | completed | 继续依赖图；其 Artifact 可自动传给依赖任务 |
| completed_with_warnings | completed，并追加 warnings | 继续依赖图；warnings 被持久化 |
| failed | retryable 且预算允许时 pending，否则 failed | 可重试项重新进入调度；稳定后进入 WorkOutcome |
| blocked | blocked | Orchestrator 只能通过显式 WorkflowPatch/recovery 和 retry 恢复 |
| needs_user_input | needs_user_input | Orchestrator 保存 PendingQuestion，Run 置为 paused |

`request_work` 不是 Task 状态，只由 ScientificPort 产生，映射到「持久化 assessment + 创建 WorkRequest」：

| ScientificTurnResult.status | SessionStatus | ResearchController 行为 | RunStatus |
|---|---|---|---|
| request_work | paused | 复核 observed trace，持久化 assessment，创建 requested WorkRequest | running |
| needs_user_input | paused | 复核 observed trace，持久化 assessment/PendingQuestion | paused |
| completed | completed | 复核并合并 observed trace，调用 ScientificCompletionValidator；通过后保存 opinion/报告 | completed |
| failed | failed 或空 | 结束 Run，将原 ModuleError 保存到 ResearchRun.terminal_error（见 I1） | failed |

completed 若未通过 Validator，不得写 Run completed；这种不一致属于 `contract_error`，Run failed 并保留 `completion_violations` 和 `terminal_error`。Scientific 接收校验与最终报告失败也保存终止原因；这些属于 orchestrator 的持久状态，不新增跨模块 wire 类型。WorkRequest 状态不映射为新的 RunStatus：requested/compiling/executing/stable/consumed 期间 Run 都是 running。

## 18. schema 版本规则

- Python 包版本和 wire schema 版本独立演进；
- 增加可选字段至少需要 schema 小版本和迁移说明；
- 删除字段、改字段含义或改变必填性需要不兼容版本；
- 每个版本必须有 round-trip 和非法组合 contract tests；
- metadata 不得长期承载本应成为正式字段的状态；
- schema 版本策略发生改变时必须先写 ADR。

历史字段增删矩阵保留在 `DEVELOPMENT_PLAN.md` 和 ADR-0011；当前接口不要求同时维护旧 schema 路径。

## 19. 运行时反馈与连续失败保护

`ToolObservation.ok` 是机器可读的成功标志：成功读取/命令为 True，失败命令（非零退出）、参数拒绝、路径缺失等可恢复失败为 False。下游不得靠解析 `summary` 文本判断失败。AgentLoop 的反馈语义：

- 可恢复失败落为持久 `runtime_feedback`（`ok=False`），并在后续每轮作为最高优先级 required 上下文注入；普通 observation 不覆盖它；
- `recent_observations` 是有界最近历史（默认 6 条），以原始事件编号从旧到新呈现；value 明确是最多 400 字符的短预览，用 head+tail 截断序列化值，不保证所有字段完整，预览省略不等于工具返回值自身不完整；需要精确正文时使用下面的专门工作集，完整观察仍留在 Session；
- Agent 需要保留文件正文等领域观察时，统一使用 runtime 的 `recent_tool_snippets`（以 (path, start_line, end_line) 为片段身份、最新片段优先完整装入，仅截断装箱的最后一段；选入后按原始事件顺序从旧到新呈现）或 `recent_tool_listing`（保留最近有界目录清单，按条目数与字符数上限、不截断单个路径）作为 required context；不得给每个文件分别套上限后生成可能被整体省略的超大 section；
- 片段 `observed_at` 复用 AgentEvent.sequence；`truncated` 表示呈现正文是否不完整，`context_truncated=true` 另标记工作集预算截断。capabilities 仅对有后续同路径成功内置写入的文件片段附 `modified_after_read_at`，不清空旧片段、不标记冻结 Artifact、不把失败动作当修改。无标记不保证文件仍是磁盘当前版本。这些是上下文投影字段，不修改 ToolObservation、跨模块契约或 Session 原记录；
- 三个 Agent 通过 capabilities.workspace_context 复用该机制：Coding/Experiment 的文件正文与工件正文分别限 6000 字符，不相互淘汰；Scientific 不传环境绑定，仅把已有工件读取投影为总共 6000 字符的 required 工作集。三者默认模块输入上限均为 8192 tokens，Compiler 保持 4096。字符额度不含 JSON 元数据，完整 section 仍由 ContextComposer 计量。显式更小的模块/模型预算仍优先，不动态扩容；required 内容过大时明确报预算错误；
- 工件正文只从 Session 工具观测投影，不再生产或消费 read_artifact_summaries 正文前缀副本。已读 ID、工件说明和检索短预览不是完整正文，也不是当前论断的支持证明；需要精确内容时按工件行范围读取。冻结工件、原始观测与 full trace 不因工作集淘汰而删除；
- 共享客户端的每次 HTTP 尝试（含重试）都计入 `llm_calls`；AgentLoop/Compiler 通过可选的 `set_attempt_limit`/`last_attempts` hooks 限制并计量实际尝试。最小 LLM 客户端只须有 `next_action`，无计数 hook 时一次调用按一次计；自带内部重试的实现应提供这两个 hooks；
- 工具派发前重新检查 wall-clock 余量；LLM 或权限检查已用尽时间时，不再派发工具，已发生调用仍入账。这不等于能撤销或抢占已经执行的外部操作；
- 一条 ToolObservation 的 `question`、`request_work`、`finish_candidate` 至多一个非空；普通观察可以全为空；
- 连续失败计数：成功的非 finish 工具重置；`ok=False` 累加；completion check 拒绝的 finish 也累加；连续 5 次失败返回 `TOOL_FAILED`，先于 step 预算。

LLM trace 的 `action_valid` 仅表示 provider 已解析出 action 候选；外层 Action schema 错误另以同一 `call_id` 记录。它不证明 Tool 参数通过校验、执行成功或科学结论有效；须结合 validation 记录和 Session 中的 observation/completion 结果阅读。

## 20. 公共导出核对表

| 类别 | Python 公共类型/纯函数 |
|---|---|
| ID/版本 | SCHEMA_VERSION、RunId、TaskId、SessionId、ArtifactId、QuestionId、WorkRequestId、WorkspaceId |
| 状态/路由 | Capability、AgentOwner、RunStatus、TaskStatus、AttemptStatus、ModuleStatus、WorkRequestStatus、SessionStatus |
| 错误/授权 | ErrorCode、WorkspaceMode |
| 科学枚举 | ScientificVerdict、RequiredEvidenceKind |
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
| 科学控制 | ScientificAssessment、WorkRequestDraft、WorkRequest、WorkTaskOutcome、WorkOutcome、ScientificOpinion、ScientificTurnRequest、ScientificTurnResult 及其四种结果分支 |
| 共享边界判据 | scientific_session_id、task_session_id、missing_required_evidence_kinds（纯函数，不新增 wire 消息） |
