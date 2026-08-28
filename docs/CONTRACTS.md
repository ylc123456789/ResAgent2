# ResAgent2 跨模块契约

**文档角色**：跨模块 wire 对象的字段、类型、组合约束和版本的唯一事实来源

**语义上级**：`ARCHITECTURE.md`；本文件不得改变其中的模块职责和控制流

**当前实现**：`resagent2-contracts 0.1.0`，wire schema `1.1`

## 1. 使用规则

本文件回答“模块之间传什么、字段准确表示什么”。

- 架构概念和谁调用谁，以 `ARCHITECTURE.md` 为准；
- Python 字段必须与 `packages/contracts/src/resagent2_contracts/models.py` 一致；
- 代码与本文字段不一致时，视为 contract bug；
- 本文写了目标语义但代码尚未强制时，必须明确标为“未实现约束”；
- runtime 内部的 `AgentDefinition`、`AgentAction`、`FinishCandidate`、Tool 和 Context 类型不属于跨模块 wire contract，不在本文件定义。

所有公共模型：

- 继承严格 `ContractModel`；
- 拒绝未知字段；
- 序列化 `schema_version: "1.1"`；
- 以下示意代码省略每个模型继承得到的 `schema_version`，但 wire 数据不能省略其版本语义。

## 2. 跨模块对象范围

| 边界 | 请求 | 响应/状态 |
|---|---|---|
| 用户 → ResAgent | ResearchRequest、UserAnswer | PendingQuestion、最终报告（尚未建模） |
| Planning Port | ScientificPlanInput/ResearchRequest | WorkflowProposal、WorkflowPatch |
| Scheduler → 专业模块 | ModuleTaskRequest | ModuleResult |
| 专业模块 → Artifact Registry | ArtifactCandidate | ArtifactRef |
| ResAgent 持久化 | Workflow、WorkflowTask、Attempt、PendingQuestion | ResearchRun 属于 orchestrator 内部模型 |

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
    input_artifacts: list[ArtifactRef] = []
    budget: RunBudget
```

| 字段 | 语义 |
|---|---|
| goal | Run 要解决的问题，不是执行步骤 |
| hypothesis | 要被证据支持或反对的命题；可为空 |
| context | 已确认背景，不包含未授权文件内容 |
| constraints | 整个 Run 必须遵守的限制 |
| input_artifacts | 用户明确授权的已登记输入 |
| budget | max_tasks、max_attempts_per_task、max_llm_calls、timeout_seconds |

## 6. Planning Port 契约

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

`questions` 的唯一语义是“在创建 Workflow 前仍需用户澄清”。非空 Proposal 不能执行，回答后重新规划。`create_run` 会拒绝非空 questions。

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

在定义求值器、证据路径和责任方前，不得把 success_criteria 写成已生效的 finish gate。

## 8. Capability 与路由

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

`Capability` / `CapabilityInput` 联合类型仍保留这两个控制面类型作为过渡，但 Workflow validator 已拒绝 `scientific_plan` 与 `ask_user` 作为 Task。是否在下个 schema 版本移除这两个 task input 类型另行决定。

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
    parent_session_id: SessionId | None = None
```

| 字段 | 控制流语义 |
|---|---|
| run/task/attempt | provenance 与幂等边界 |
| capability + inputs | 选择模块 profile；二者 discriminator 必须一致 |
| input_artifacts | 已登记且已授权给本 Task 的输入证据 |
| answers | 只包含属于本 Task 的已持久化回答 |
| workspace | 此 Attempt 的最大物理访问范围 |
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
| scientific_analyze | ScientificConclusion → 科学闭环/final report | legacy payload 保存到 Attempt；结论另登记 `scientific_decision` Artifact | Phase 7 |
| literature_search | 有界文献结果 → Scientific Agent | 无 production binding；持久结果必须登记 Artifact | Phase 7 |
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

`evidence_files` 是 workspace 相对路径，指向本 Attempt 实际产生的证据文件；每个文件由 finalizer 校验存在后才进入 payload，并作为 `experiment_result` ArtifactCandidate 登记。`repo_url` + `commit` 是 repo identity（不依赖 basename）；`env_id` 是内容寻址环境 id。`delivery_issues` 记录 `expected_metrics`/`expected_artifacts` 缺失项；非空时 finalizer 返回 completed_with_warnings，其 WarningRecord（code=`delivery_not_met`）的 message 记录 `[NOT MET] Missing required ...`。

`ExperimentRunInput` 在 schema 1.1 新增可选字段：

```python
repository_url: NonEmptyStr | None = None
copy_from: NonEmptyStr | None = None
external_repo_path: NonEmptyStr | None = None
python_version: str = "3.12"
confirm_before_experiment: bool = False
```

三个 repo source 字段互斥，至少给一个或全部留空（resume 语义复用已有 repo）。`ExperimentResult` 是既有 `ModuleResult.payload` 扩展点的新命名形状；给 `ExperimentRunInput` 加字段属于对已冻结 wire 模型的小版本演进，因此 wire schema 从 1.0 升到 1.1（见 §17）。Scheduler 仍只原样持久化 payload，不基于 payload 改状态。

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

Attempt 属于 ResAgent 历史，Session 属于子 Agent。retry 是同一 Task 的新 Attempt，默认新 Session；resume 是新 Attempt 引用旧 Session；repair 是新 WorkflowTask。

running Attempt 不能有 finished_at/error；终态必须有 finished_at；failed/blocked 必须有 error；其他终态不能有 error。`payload` 是模块返回的能力专属结构化结果，随 Attempt 持久化，不被静默丢弃；失败/契约错误路径天然为 None。

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

Candidate 的 path 必须是 workspace root 下无 `..` 的相对路径。Candidate 故意没有 id、URI、hash 或 provenance；这些只能由 ResAgent 登记时产生。

capabilities 中的 Tool 负责访问时的路径/权限检查；ResAgent 在登记时独立复核存在、containment、symlink、hash 和 Attempt 绑定。ArtifactRef 是冻结证据，不能被后续 Attempt 覆盖。

## 14. WorkspaceGrant

```python
class WorkspaceGrant:
    root: NonEmptyStr
    mode: WorkspaceMode
    allowed_paths: list[str] = []
    denied_paths: list[str] = []
    source: WorkspaceSource
```

Grant 表示授权，不表示 repo identity 或 Artifact。allowed/denied path 只接受相对 root 的路径。contracts 做词法约束；capabilities 的真实 filesystem 实现做 resolve/symlink/物理边界检查；Artifact Registry 登记时再次复核输出。

## 15. ScientificConclusion

```python
class ScientificConclusion:
    verdict: ScientificVerdict
    summary: NonEmptyStr
    evidence_artifact_ids: list[ArtifactId]
    limitations: list[NonEmptyStr] = []
    recommended_next_steps: list[NonEmptyStr] = []
```

Scientific verdict 与执行状态独立：一次成功的 scientific_analyze 可以得出 refutes 或 inconclusive。当前 Scheduler 没有把它接入最终 Run gate，属于后续闭环工作。

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

当前唯一支持版本为 `1.1`，不维护旧格式兼容层。Phase 4 完成起 `1.0` 冻结；Phase 6 给 `ExperimentRunInput` 增加可选字段时按规则发布 `1.1`（迁移说明见 §10.3 与 ADR-0005），并新增 round-trip 与非法组合测试。从 `1.1` 起，后续增加可选字段必须发布至少 `1.2` 并提供迁移说明和 round-trip 测试。

## 18. 当前公共导出核对表

| 类别 | Python 公共类型 |
|---|---|
| ID/版本 | SCHEMA_VERSION、RunId、TaskId、SessionId、ArtifactId、QuestionId |
| 状态/路由 | Capability、AgentOwner、RunStatus、TaskStatus、AttemptStatus、ModuleStatus |
| 错误/授权 | ErrorCode、WorkspaceMode、WorkspaceSource、SessionStatus |
| 科学枚举 | VerificationMode、ScientificVerdict |
| 通用结果 | ModuleError、WarningRecord、SessionRef |
| 入口/预算 | RunBudget、TaskBudget、ResearchRequest |
| 人机交互 | QuestionDraft、PendingQuestion、UserAnswer |
| 证据 | ArtifactCandidate、ArtifactRef |
| capability 输入 | ScientificPlanInput、ScientificAnalyzeInput、LiteratureSearchInput、CodeUnderstandInput、CodeModifyInput、ExperimentPrepareInput、ExperimentRunInput、AskUserInput、CapabilityInput |
| Coding payload | VerificationResult、CodeUnderstandResult、CodeModifyResult |
| Experiment payload | ExperimentResult |
| 工作流 | SuccessCriterion、TaskProposal、WorkflowProposal、Attempt、WorkflowTask、Workflow、PendingTaskUpdate、WorkflowPatch |
| 模块边界 | WorkspaceGrant、ModuleTaskRequest、ModuleResult |
| 注册/结论 | CapabilityDefinition、CapabilityRegistry、ScientificConclusion |

## 19. 已知契约对齐项

这些是已确认缺口，不是隐含设计：

1. 实现 success_criteria/evidence_key 的正式求值器（方向已裁定：保留可执行语义，求值器留待 Phase 7）；
2. Scientific capability 的强类型 ModuleResult payload model 及其领域消费方仍待 Phase 7 定义；Coding（Phase 5）与 Experiment（Phase 6）payload 已完成；
3. 在引入下一次字段变化时按 §17 发布 1.2 及迁移说明。

这些工作的阶段、顺序和验收见 `DEVELOPMENT_PLAN.md`。
