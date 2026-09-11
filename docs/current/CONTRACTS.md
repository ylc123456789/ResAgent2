# 模块接口与契约

这份参考把**谁调用谁、传什么、返回后怎么办**放在同一个边界下。原字段契约与六张接口卡合并于此，不再要求在两个文件之间来回找规则。

接口是调用入口，契约是它的输入输出和行为约定；不是两个子系统。当前主要是进程内 Python 方法，不是已实现的 RPC、MCP 或 A2A 服务。

职责看 [架构](ARCHITECTURE.md)，例子看 [理解一次研究任务](../guides/UNDERSTANDING.md)。公共模型见 [models.py](../../packages/contracts/src/resagent2_contracts/models.py)；运行时类型仍归 runtime。文档与代码不一致必须核对修正，类型合法不等于业务正确。

<a id="boundaries"></a>

## 按调用边界查找

| 调用方向 | 入口 | 请求 / 返回 | 位置 |
|---|---|---|---|
| 用户入口 → Controller | create_run / answer_question / run_until_stable | ResearchRequest / UserAnswer → ResearchRun | [用户与控制](#entry) |
| Controller → Scientific | ScientificPort.run | ScientificTurnRequest → 四态 ScientificTurnResult | [科学决策](#scientific) |
| Controller → Compiler | WorkflowCompiler.compile | WorkRequest + 图/能力/预算 → CompilationResult | [工作编译](#compiler) |
| Scheduler → Coding / Experiment | ModulePort.invoke | ModuleTaskRequest → ModuleResult | [执行任务](#module) |
| AgentLoop → ToolRegistry → Tool | dispatch / execute | arguments → ToolObservation | [工具与运行](#tools) |
| AgentLoop → 领域完成检查 | CompletionCheck.evaluate | 真实记录 + 提议 → CompletionDecision | [完成与报告](#completion) |
| 生产方 → Registry；Agent → reader | register / read_text | Candidate → Ref；授权 Ref → 内容 | [工件](#artifacts) |

共用 [身份](#identities)、[状态](#states)、[工作区](#workspace)、[数据集与环境](#resources)、[版本规则](#schema) 在文末。调用方不需要知道下游内部实现。

<a id="conventions"></a>

## 阅读约定

- 公共模型继承 ContractModel，extra="forbid"，当前 schema_version="6.0"。以下代码是字段示意，省略继承字段及部分 validator，不是可直接复制的完整类。
- 方法签名解决“能否调用”，接收校验、所有权、恢复和计量解决“是否守约”。替换实现两者都要满足。
- 机器状态用结构字段判断，不解析 summary。说明、数字投影、冻结证据各有用途，不能互相替代。
- runtime 的 AgentDefinition、ToolObservation、ContextSection 等不是公共 wire 类型，不全部搬入 contracts。
- ResearchRun 归 orchestrator；它持有跨模块对象，不代表 contracts 拥有运行控制。上层不读下游私有 Session 来作调度判断。

<a id="entry"></a>

## 1. 用户入口 → 研究控制

```python
ResearchController.create_run(run_id, request: ResearchRequest) -> ResearchRun
ResearchController.answer_question(run_id, answer: UserAnswer) -> ResearchRun
ResearchController.run_until_stable(run_id) -> ResearchRun
```

CLI 或其他组合根调用 Controller，它是唯一 Run 业务入口。create_run 保存后执行到稳定，非只创建记录；answer_question 保存当前问题答案再继续；run_until_stable 不制造答案、不越过 paused。查看状态使用 RunStore.load，不调用推进接口。

Controller 管 Run、工作请求和问题路由；Scheduler 管 Task/Attempt，Agent/runtime 管 Session。任务级回答在同一 Run 对象内记录答案并恢复 Task 后保存，继续同一 Attempt。子任务约束来自已编译 task.constraints，不广播原始研究控制约束。

RunStore 原子 JSON 保存不构成跨 Run/Session/文件事务，也不是多写入者锁。新 Run ID 不复用已有身份；重复答案按持久化 QuestionId 识别，不能自动套给下一题。

**源码与测试**：[Controller](../../packages/orchestrator/src/resagent2_orchestrator/controller.py)、[Controller 测试](../../tests/orchestrator/test_controller.py)、[跨 Run 隔离](../../tests/e2e/test_run_scopes.py)。

<a id="research-request"></a>

### ResearchRequest

```python
class ResearchRequest:
    goal: NonEmptyStr
    hypothesis: NonEmptyStr | None = None
    context: str = ""
    constraints: list[NonEmptyStr] = []
    input_artifacts: list[ArtifactImport] = []
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
| required_evidence_kinds | 必须被已登记、已观察且最终引用的 Artifact.kind 满足；约束证据种类，不强制某次 Tool 调用，导入证据也可满足 |
| budget | max_tasks、max_attempts_per_task、max_llm_calls、timeout_seconds |

ResearchRequest 只表达研究意图和调用方约束，不承载数据集目录、catalog 路径或依赖缓存配置。部署目录由组合根注入；内部资源引用归 ResearchRun，见 [资源](#resources)。

`timeout_seconds` 是 Run 墙钟额度，但扣除显式 `ask_user` 等待。安装、LLM 延迟、执行和普通进程中断仍计时；不是 CPU 时间，也不抢占已运行工具。Controller/Scheduler 共用 `ResearchRun.remaining_timeout_seconds(now)`。
`user_wait_seconds` 记录已结束暂停的累计秒数；当前暂停从 PendingQuestion.created_at 推导。回答时按系统时钟结算，与答案、任务恢复一次保存，不使用用户的 answered_at 延长预算；重复答案不会重复增加等待时长或重置 LLM 计数。

<a id="questions"></a>

### 提问与回答

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

<a id="scientific"></a>

## 2. Controller → Scientific：科学决策

```python
ScientificPort.run(request: ScientificTurnRequest) -> ScientificTurnResult
```

Port 由 orchestrator 声明，ScientificAgent 或替代实现提供。输入权威是 Controller 给出的目标、授权工件、当前交付结果/答案和预算；Scientific 不直接改 Run/Workflow。

| 返回 status | Controller 动作 |
|---|---|
| request_work | 保存 assessment，分配 WorkRequest 身份，编译执行 |
| needs_user_input | 保存问题，Run paused |
| completed | 进入最终 gate，通过并登记报告后才完成 Run |
| failed | 原始错误保留到 terminal_error，Run failed |

Scientific 可检索文献、读工件，通过注入的 registration port 冻结检索结果，不直接调用执行 Agent。prompt 要求自有检索/阅读经已有重试仍失败时询问用户，不派代码或实验任务绕路；这是行为指引，不是确定性路由保证。

interpreter.render_work_brief 投影目的、结果、解释性 narrative、warnings、失败诊断和授权证据指针。未解决任务来自完整 workflow 权威集合；有界 stderr 摘录标为 execution_diagnosis_only，非科学证据。模型不接收 raw 执行对象或内部 Task ID，不凭 narrative 自证结果。

接收方确认交付前验证四个分支、Session 归属/状态、观察与引用集合。非法回复不能替换 Session、消费 stable WorkOutcome 或标记答案已交付；已确认消费仍计账。原生实现按 work_request_id/question_id 去重交付，避免重复事件/调用；替换实现必须测试这项约定，不因满足 Protocol 自动获得它。Scientific 转译模块失败时结算其所属 Session，不能让 failed 和 completed 状态矛盾。

**源码与测试**：[ScientificAgent](../../packages/agents/scientific/src/resagent2_scientific/agent.py)、[interpreter](../../packages/agents/scientific/src/resagent2_scientific/interpreter.py)、[四态接收](../../tests/orchestrator/test_scientific_turn_boundary.py)、[简报](../../tests/scientific/test_interpreter.py)。

<a id="work-request"></a>

### 观点与工作请求

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

<a id="work-outcome"></a>

### 执行结果

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

<a id="opinion"></a>

### 科学意见

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

<a id="scientific-turn"></a>

### 科学回合

```python
class ScientificTurnRequest:
    run_id: RunId
    research: ResearchRequest
    dataset_refs: list[DatasetRef] = []  # Controller 提供的系统目录引用
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

<a id="compiler"></a>

## 3. Controller → Compiler：工作编译

```python
WorkflowCompiler.compile(
    request: WorkRequest, *, current: Workflow | None,
    registry: CapabilityRegistry, budget: RunBudget,
    workspaces: list[WorkspaceDescriptor] | None = None,
    remaining_calls: int | None = None,
) -> CompilationResult
```

output 是 WorkflowProposal（新图）或 WorkflowPatch（只追加），llm_calls 是本次真实消费。这是内部计量包装，不是新 wire 消息。失败抛 CompilationError，保留 cause 并报告本次消费；不读取实现隐藏属性补账。其它异常受控结束并标 compiler_usage_known=False，账目是已知下界，不是确定零次。

**前置**：Controller 检查新编译的剩余任务名额；已接受图优先恢复，即使名额已满也不能重编。WorkRequest 给本轮目的，registry 给可用能力，workspace descriptor 是逻辑摘要而非物理授权。

**过程**：LLM 给局部 CompilationDraft，代码分配 Run 内身份、绑定工作请求、解析依赖/workspace。draft/review 共用能力职责；review 看 goal、depends_on、constraints 和与物化器一致的 inputs。BaseModel 返回值也投影后重新校验。结构与语义拒绝共用一次纠错，最多两版 draft，各最多一次 review；不能证明自然语言需求必然完整。

**接收**：workflow_validation.validate_workflow_candidate 是 Compiler/Scheduler 共用的非空及本轮依赖纯判据。Scheduler 持久化前另查 binding、workspace、预算与 revision；返回候选不等于已接受。“Workflow Validator”不是额外独立服务。

**职责**：Compiler 不扫描代码或执行。LLMCompiler 清空猜测的 suggested_paths、expected_metrics/expected_artifacts，将实验语义留在本 Task.instructions；公开精确字段服务可信调用方。不能为任务名额让 code_modify 承担正式训练。成功依赖不是失败分支，条件修复等真实失败后由 Scientific 发新工作请求。

**副作用与恢复**：不修改 Run/Workflow，不保存 Session，但会调用外部 LLM、写 trace 并计数，不是纯函数或保证并发的实例。COMPILING 未接受图可重编，已接受则继续，不保证重复生成相同语义图。

**源码与测试**：[Compiler](../../packages/orchestrator/src/resagent2_orchestrator/compiler.py)、[Scheduler](../../packages/orchestrator/src/resagent2_orchestrator/scheduler.py)、[Compiler 测试](../../tests/orchestrator/test_compiler.py)、[repair 图](../../tests/orchestrator/test_repair_flow.py)。

<a id="workflow"></a>

### Workflow 执行图

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

- `WorkflowProposal` 是 Compiler 产生的初始图候选；`WorkflowPatch` 是**只追加**的修订。修复模型是「新 WorkRequest 增加新 Task、保留旧历史」，不更新或抹去旧任务。设计理由见 [ADR-0011](../history/decisions/0011-stabilization-schema-3.md)。
- `TaskProposal`/`WorkflowTask` 不再有 `required`、`rationale` 或 `success_criteria`；编译理由只在 proposal/patch 级保留为 `compilation_rationale`。
- `capability` 必须与 discriminated `inputs.capability` 一致；图必须无环；Attempt number 必须从 1 连续递增；Task 的 `work_request_id` 必须等于所属 Proposal/Patch 的 `work_request_id`。图候选接收时必须非空，`depends_on` 只能引用本 Proposal/Patch 新增的 TaskId，不得依赖历史 WorkRequest 的 Task（无论旧 Task 成功还是失败）；共享判据在 Compiler 纠错与 Scheduler 接收两处调用。

<a id="capabilities"></a>

### Capability 与路由

顶层 WorkflowTask 只保留真正由 Scheduler 执行的能力：

| capability | owner | 说明 |
|---|---|---|
| code_understand | Coding | 授权范围内只读理解代码 |
| code_modify | Coding | 授权范围内修改并验证代码 |
| experiment_run | Experiment | 准备环境、执行实验、派生指标并提交候选证据；由 Scheduler 调 Registry 登记冻结 |

Literature Search 是 Scientific Agent 的 Tool；ask-user 是 control signal；实验准备属于 `experiment_run` 内部流程。这些都不是顶层 task capability。

`CapabilityDefinition` 只保留能力标识、owner 和说明；`CapabilityRegistry` 拒绝同一 capability 出现两次，从而保证每个已声明 capability 恰有一个 owner。同一个 owner 可以拥有多个不同 capability。

| 字段 | 类型 | 含义 |
|---|---|---|
| capability | Capability | 已声明的能力标识 |
| owner | AgentOwner | 该能力的唯一归属模块 |
| description | str，默认空字符串 | 向 Compiler 提供的能力说明 |

输入类型由 `CapabilityInput` 判别联合定义，成功 payload 由 Scheduler 按 capability 校验；工作区授权、工具权限和完成证据由各自执行边界强制检查。Registry 不重复声明模型名称、权限策略、副作用或完成证据要求，也不把这些执行规则变成动态配置。

<a id="module"></a>

## 4. Scheduler → 执行模块：执行任务

```python
ModulePort.invoke(request: ModuleTaskRequest) -> ModuleResult
# ModuleBinding(owner, port) 由组合根提供实现。
```

Scheduler 从已接受 Task 与 Run 组装请求并保存 running 意图；Coding/Experiment 操作授权工作区和环境，返回结果/候选工件。Agent/runtime 管 Session 正文，Scheduler 不读它驱动调度。

| 返回状态 | 接收动作与执行含义 |
|---|---|
| completed / completed_with_warnings | 验收 payload，登记工件，结束 Attempt；warnings 必须保留 |
| failed | 保存错误；retryable 且预算允许才以新 Attempt 重试 |
| blocked | 结束当前 Attempt，保留阻塞，不自动当成功重试 |
| needs_user_input | 保存问题、暂停；回答后同 Attempt、Session、输出目录和基线继续 |
| request_work | 子模块不允许，拒绝；只有 Scientific 能提出研究工作需求 |

一次 invoke 是执行区间，不一定结束整个 Attempt；llm_calls 只报此次新增 HTTP 尝试，不重报整个 Session。任务 constraints、Run 数据集、WorkspaceSpec Python 要求、WorkspaceGrant 物理授权各有唯一来源。

Scheduler 重验外壳、capability 对应成功 payload 和身份。空/错 payload 变为不可自动重试的 contract_error；结构验证不代替 finalizer 的真实执行校验。失败保留消费、Session、原始诊断和工件，登记失败不能吞掉这些事实。

中断恢复结算旧尝试，再按预算重试；任意 invoke、修改、安装、命令不承诺幂等，也不自动回滚。原生 Coding 尽量保留失败 patch。不同 Run 的同名 Task 不串 Session，同 Attempt 新问题也有独立 QuestionId。

**源码与测试**：[Scheduler](../../packages/orchestrator/src/resagent2_orchestrator/scheduler.py)、[结果边界](../../tests/orchestrator/test_module_result_boundary.py)、[工作流](../../tests/orchestrator/test_workflows.py)。

<a id="module-request"></a>

### ModuleTaskRequest

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
| dataset_refs | Scheduler 从 ResearchRun 提供的已知目录引用，非用户要求、非 LLM 生成，也不表示都要用；Agent 检查哪些目录实际可用 |
| answers | 只包含属于本 Task 的已持久化回答，可含该 Task 的较早回答；Coding/Experiment 每一步经共享 user_answers_section 按传入顺序呈现为 required answers 段，不从 Session 另取一份 |
| workspace / workspace_id / workspace_spec | 此 Attempt 的物理授权范围、逻辑工作区 id、来源声明（Agent 在 loop 前确定性 materialize） |
| environment_spec | 上游声明的环境硬约束（`EnvironmentSpec.python_version`） |
| output_dir | code_modify / experiment_run 的输出目录 |
| parent_session_id | 仅显式 resume 使用；普通 retry 为空 |

`workspace_spec` 存在时要求 `workspace` 非空、`workspace_id` 与 `workspace_spec.workspace_id` 一致、`workspace.source` 与 `workspace_spec.source_kind` 一致。ask-user 后的 resume 产生 `parent_session_id`；runtime AgentLoop 在 resume 时加载该 Session 并校验 run/task/attempt/agent/owner 和可恢复状态；执行会话的 ID 也按 Run/Task/Attempt 隔离。普通 retry 不复用 Session。

<a id="module-result"></a>

### ModuleResult

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

Scheduler 同时校验通用外壳和该 capability 的成功 payload 模型；不合规结果转为 contract_error，而非完成任务。Artifact 注册失败也保留新增 `llm_calls`、Session 和已取得的结果；已有 ModuleError 的根因保留，注册错误附在 details 中。原生 finalizer 负责真实工具证据，接收端负责公共结果约束，两者不是互相替代，见 [接口](#module)。

<a id="payloads"></a>

### 领域 payload

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
- `CodeModifyResult` 要求 changed_files 与 deleted_files 至少一项非空且不重叠；`verification_results` 至少一条且 `verification_passed` 必须等于「所有 VerificationResult 均为 exit_code 0 且未 timeout」（model validator 强制，ADR-0011 [身份](#identities)）。`patch_path` 指向 Coding finalizer 通过 Git 能力生成的 Attempt patch。
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

<a id="attempt-session"></a>

### Attempt 与 SessionRef

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

Attempt 属于 Orchestrator 历史，Session 属于子 Agent。retry（failed/blocked 后重试）是同一 Task 的新 Attempt，默认新 Session；pause/resume 是**同一 Attempt** 的暂停与继续，不增加 Attempt number，复用 Session/output_dir/workspace snapshot（ADR-0011 [调用边界](#boundaries)）；repair 是新 WorkflowTask。进程中断不是第三种 AttemptStatus：恢复时把遗留 running Attempt 结算为 `failed + ErrorCode.interrupted + retryable=True`，再按原有 Attempt 预算决定 Task 是否回到 pending（ADR-0012）。

`running` 与 `needs_user_input` 都是非终态：不能有 finished_at/error；终态（completed/completed_with_warnings/failed/blocked）必须有 finished_at；failed/blocked 必须有 error；其他终态不能有 error。`payload` 是模块返回的能力专属结构化结果，随 Attempt 持久化，不被静默丢弃；失败时可为空；若完成结果在后续 Artifact 登记阶段失败，已验过的 payload 可保留作诊断，但不把 failed 状态变成成功。

<a id="tools"></a>

## 5. AgentLoop → 工具与运行机制

ToolRegistry 按动作名找 Tool，以 input_model 完整校验 arguments，再调用 `Tool.execute(state, parsed_arguments) -> ToolObservation`。工具不直接写 AgentState，返回 memory_updates / 候选工件 / 控制信号，由 Loop 应用；但可实际写文件、运行命令或改变 EnvironmentBinding，并非纯函数。

模型每轮收到从各 Tool.input_model 派生的必填顶层参数与 guidance，作为 required tool_contracts 计入 Composer。它不是完整嵌套 schema，也不替代执行前校验。

| 可注入入口 | 约定 |
|---|---|
| AgentLoop.run(definition, request, *, session_id, initial_memory=None) | 循环、观测、反馈、Session，不调度 Workflow |
| ContextBuilder(request, state) | 返回领域 sections，Runtime 统一补工具契约/反馈/历史 |
| ContextComposer.compose(...) | 组合最终文本、估算预算，required 装不下明确失败 |
| LLMClient.next_action(context, action_type) | 必需方法；返回候选 dict/模型，不代表动作有效 |
| PromptLLMClient.next_action(prompt, action_type) | 普通提示复用 Composer/计量，无 Tool/Session/Loop |
| PermissionPolicy.check(action, state, request) | 派发前确定性允许/拒绝，不是 OS 沙箱或人工审批 UI |
| SessionStore | 内部状态/事件持久化；上层仅持有引用 |

LoopRequest 只要求身份、预算、父 Session 等运行信息；Scientific 的 task/attempt 可为空。领域 inputs 和授权由注入的 builder、工具、finalizer 使用。EnvironmentBinding、WorkspaceSnapshot 留在 capabilities，不变成 wire 消息。

参数错误、ok=False 和执行时 PermissionError 等可恢复错误进反馈；PermissionPolicy 明确拒绝则立即 permission_denied。未知工具走既有拒绝策略，不放宽 schema。Action 校验前只移除旧 reasoning_summary 字段，不忽略其它未知字段。

读取通常可重复；写入和外部命令不承诺 exactly-once。read_file/read_artifact 共用行切片，Artifact 先核对整份 hash。search_text 是大小写不敏感字面子串，非正则，a|b 按原文匹配。

**容量**：ModelProfile 声明窗口、输出预留、安全余量，模块声明输入上限；有效额度取模块与剩余模型容量之小值。Composer 对含标题/分隔符的最终文本估算，estimated_tokens == estimate_tokens(text)，仍是字符/4 的近似，不是供应商 tokenizer。required 保持顺序，optional 按优先级稳定选入；大可选段放不下不阻挡后续小段。Action schema 等容量由适配另扣，不查询或按模型名猜容量。

<a id="trace"></a>

### LLM 计量与 trace

最小客户端只有 next_action；context_budget、set_attempt_limit、last_attempts、set_trace_context、record_validation 等 hooks 按存在与否使用。内部有重试应提供计量与限制；无 hook 按每请求一次计数，不宣称获知隐藏重试。

OpenAICompatibleClient 的 trace 按 call_id 关联逻辑调用和后续校验记录。attempts 保留各次 finish_reason/usage/错误，顶层响应对应最后一次；retry_number+1 是 HTTP 尝试数，不能再加 attempts 长度。request_max_tokens 是实际输出上限，null 表示未指定。

off 不记录；metadata 不保存请求/响应/源码正文；full 保存原始 request/response 和 provider 提供时的 reasoning_content，包括可取得的失败响应。reasoning 仅用于调试，不进 Session/下一轮上下文。目录/文件按 0700/0600 管理，但 full 仍可能含源码和用户输入，不是可公开上传的日志。

空 JSON 只是一种现象，先查 finish_reason、usage、输出额度，不直接定性模型漂移；未返回的信息为未知。action_valid 不证明参数、执行或结论正确，应结合校验补充记录与 Session 观测。部署配置见 [CLI README](../../apps/cli/README.md#6-模型与上下文预算)。

**源码与测试**：[ToolRegistry](../../packages/runtime/src/resagent2_runtime/tools.py)、[Loop](../../packages/runtime/src/resagent2_runtime/loop.py)、[Context](../../packages/runtime/src/resagent2_runtime/context.py)、[工具契约](../../tests/runtime/test_tool_contracts.py)、[guards](../../tests/runtime/test_guards.py)、[恢复](../../tests/runtime/test_resume.py)。

<a id="runtime-context"></a>

### 运行时反馈与上下文

`ToolObservation.ok` 是机器可读的成功标志：成功读取/命令为 True，失败命令（非零退出）、参数拒绝、路径缺失等可恢复失败为 False。下游不得靠解析 `summary` 文本判断失败。AgentLoop 的反馈语义：

- 可恢复失败落为持久 `runtime_feedback`（`ok=False`），并在后续每轮作为最高优先级 required 上下文注入；普通 observation 不覆盖它；
- 用户答案由调用方限定作用域，经 Agent 的 context builder 进入同一 ContextComposer。Coding/Experiment 共用 `user_answers_section`，Scientific 保留已有 `answers` 段；不重复注入、不缓存或静默裁掉答案，必需段装不下时沿用 ContextBudgetExceeded。`ask_user` 的成功观测只代表已发问，不代表已收到答案或前提已经满足；历史工具结果也可能早于当前回答与资源刷新；
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

<a id="completion"></a>

## 6. 完成提议 → 确定性验收

```python
CompletionCheck.evaluate(state: AgentState, candidate: FinishCandidate | None) -> CompletionDecision
```

AgentLoop 调用注入的领域 finalizer。candidate 是模型提议，真实判据来自请求、工具观测、基线和证据，不追加 LLM 推理。

| Decision | Runtime 行为 |
|---|---|
| complete=True | 验配置的 result_type 后返回 payload/候选工件；有 warnings 为 completed_with_warnings |
| failure 非空 | 立即返回带 ModuleError 的 failed |
| 两者都没有 | 继续；summary 可作反馈，拒绝完成候选计连续失败 |

complete=True 与 failure 互斥。check 不自己改 Run/Task，Runtime 结算 Session。finalizer 可能读文件/Git、生成 patch，并非全是无 IO 纯函数。

- Coding understand 要求实际观察且不改代码；modify 验证非空、全通过，覆盖当前 edit revision 与已认证环境 generation。重新 audit 不替代重新验证。
- Experiment 要求成功命令、本 Attempt 新建/变化的证据和完整 JSON 集合派生 metrics。无精确要求也不能零证据放行；要求部分缺失带 warnings，同名不同值拒绝不覆盖。真实失败命令与日志可支持确定性 failure。
- Scientific 原生 check 验已读引用、required_evidence_kinds 和执行局限，不持有完整 ResearchRun。Controller 最终 gate 从完整 Run 作独立验收，两者不可互代。

**源码与测试**：[Coding completion](../../packages/agents/coding/src/resagent2_coding/completion.py)、[Experiment completion](../../packages/agents/experiment/src/resagent2_experiment/completion.py)、[Scientific completion](../../packages/agents/scientific/src/resagent2_scientific/completion.py)、[Coding 控制规则](../../tests/coding/test_control_state.py)、[Experiment 完成测试](../../tests/experiment/test_completion.py)。

<a id="final-report"></a>

### 最终验收与报告

Validator 是 orchestrator 内部纯验证器，不调用 LLM，不读取 Session 私有 event。输入 ResearchRun 与 ScientificCompletedResult，内部构造经校验的 Run 深拷贝；输出 CompletionValidation（结构化 violations 或 FinalReportData）。检查包括：completed result/session/run 绑定正确 → 无 active WorkRequest/PendingQuestion/**pending、running 或 needs_user_input Task** → opinion 通过组合约束 → 每个 evidence Artifact 属于本 Run，且同时出现在 result.observed_artifact_ids 与 Run 已复核集合 → 从 Run 确定性对账 failed/blocked Task，要求存在错误记录及 opinion.limitations → 每个 completed Task 有合法终态 Attempt、无 error、artifact producer 与 binding owner 一致 → 拒绝未知/重复/跨 Run 的 ID。Scientific 不再上报 acknowledged_task_ids，执行问题的精确身份由 Validator 生成。

原生 ScientificCompletionCheck 先根据 Session 工具记录、未解决工作及证据要求检查候选；它不持有完整 ResearchRun。最终 gate 再独立验收完整 Run。两处复用证据种类的纯判据：从本 Run 已登记 Artifact 中确认所需 kind 同时被观察和引用，导入证据与新生成证据遵循同一规则；不能把两处描述为输入完全相同，见 [接口](#scientific)。

通过时 Validator 产出 `FinalReportData`（run_id、goal、opinion、evidence、execution_issues），纯 renderer 只消费 FinalReportData 生成 `kind=final_report`、`media_type=text/markdown` 的 ArtifactCandidate。RunStatus 只有在验证、渲染、Artifact 登记和 Run 字段写入全部成功后才改 completed。Validator 不判断 statement 是否科学正确。

<a id="artifacts"></a>

## 7. 生产方 → Registry → 读取方：工件

```text
执行 Agent Candidate → Scheduler → ArtifactRegistry.register → ArtifactRef
Scientific Tool Candidate → 注入的 ArtifactRegistrationPort → 同一 Registry
用户输入 / 最终报告 → Controller → Registry 专用入口
读取 → RegisteredArtifactReader.read_text(artifact_id, ...) → 内容观测
```

Candidate 不自证身份/hash/来源。Registry 从调用上下文绑定 Run/Task/Attempt 或 Session/orchestrator provenance，校验并冻结；Controller/Scheduler 将 Ref 收入 Run。reader 先核对 Run 授权、Ref 身份和整份 hash 再切片，不接受任意文件路径。

ScientificArtifactRegistration 是共享适配器，冻结后更新 Run 索引及同轮动态查找；键为 (run_id, artifact_id)，不是第二个持久仓库。重启由持久化 Ref 提供授权，不跨 Run 共享观察事实。

任务 register 以单工件完整 staging 目录 rename 提交；import/scientific/final_report 专用入口是先建目录、临时文件替换，不一概承诺整目录原子性。单工件提交不保证批量或 Run+Session+Artifact 是一个事务；后续登记失败保留此前工件、消费、Session、原始诊断，已有原错误则追加 artifact_registration_error，禁止因登记失败自动重试。

register_scientific 冻结前将自产 JSON 写为 indent=2 多行，再按实际字节 hash，便于按行读；不改旧工件，reader 不重新格式化。已登记不等于已观察，正文省略可按范围再取，full trace 不自动成为 Artifact 或科学证据。失败工件可作诊断，不包装成成功实验；各登记入口重复保护不代表所有副作用 exactly-once。

**源码与测试**：[Registry / 共享适配](../../packages/orchestrator/src/resagent2_orchestrator/artifacts.py)、[reader](../../packages/capabilities/src/resagent2_capabilities/artifacts.py)、[持久化与工件](../../tests/orchestrator/test_persistence_and_artifacts.py)、[最终报告](../../tests/orchestrator/test_scientific_completion.py)。

<a id="artifact-models"></a>

### 工件模型

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
## 8. 共用参考：身份、状态、资源和版本

这些对象被多条边界使用，在此维护一次。各模块内部数据不需要为了“统一”都搬进公共契约。

<a id="identities"></a>

### 身份

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

<a id="states"></a>

### 状态与所有权

#### RunStatus

```text
pending | running | paused | completed | failed
```

由 Orchestrator 写入。planning、replanning、interrupted 不是 RunStatus。

#### TaskStatus

```text
pending | running | completed | failed | blocked | needs_user_input
```

由 Scheduler 写入。

#### AttemptStatus 与 ModuleStatus

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

<a id="state-mapping"></a>

### 状态映射

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

<a id="workspace"></a>

### 工作区

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

capabilities 提供一个内部 `WorkspaceSnapshot`（Git workspace 用 `GitBaseline` 的 tree hash，非 Git workspace 用有界 file-hash fallback）表达 Attempt 起点；GitDiffTool、Coding finalizer、failed patch 与 Experiment evidence ownership 都消费同一个 snapshot，删除 HEAD-relative legacy diff API（ADR-0011 [状态与所有权](#states)）。

<a id="resources"></a>

### 数据集与环境

```python
class DatasetRef:
    dataset_id: NonEmptyStr
    relative_path: str

class EnvironmentSpec:
    python_version: str | None = None
```

`dataset_root`（`ResourceLayout.dataset_root`）是所有数据集的共享根，不是某个数据集目录。部署者在根下 `catalog.json` 维护 `dataset_id → relative_path`；CLI/E2E 各自把 DatasetCatalog 经现有 DatasetRefSource Port 注入 Controller。调用方不提交目录路径或完整表。Controller 在推进回合及回答后的恢复入口读取目录，将新增引用保存到 `ResearchRun.dataset_refs`，不改写 ResearchRequest。

Run 中保存的是本 Run 累计发现的目录引用，不是实际使用清单、完整数据内容或数据版本快照。已有同名引用不允许改路径，不因后来删掉 catalog 条目而撤销 Run 中已有引用；但实际目录的存在性会重新检查。数据内容不复制、不做整库 hash，不承诺目录内部数据未被外部修改。

Controller 经 ScientificTurnRequest、Scheduler 经 ModuleTaskRequest 传递这些内部引用；三个 Agent 注入同一 ResourceLayout，各次调用通过共享 `resolve_dataset_refs` 检查。它返回一个轻量 DatasetAvailability：available 为 `{dataset_id, path, access="read_only"}` 列表，unavailable_ids 为目录暂不存在的 ID。上下文与命令环境映射使用同一个检查结果，不维护第二份可用性状态。

- catalog 缺失意味着当前没有新登记；已登记目录缺失标为不可用，不阻塞无关工作。
- 共享上下文明确区分 `available_dataset_ids`（已登记且目录存在）与 `unavailable_dataset_ids`（已登记但目录不存在）；两边都没有的 ID 在当前视图中未登记，不表示可用。当前任务需要的 ID 不在 available 列表时，应先 ask_user 再做依赖该数据的工作，不能只检查 unavailable 列表。
- 非法 JSON/登记格式、重复 ID 引用、绝对或越界路径（含软链逃逸）仍明确报错。
- 只有可用目录进入 `RESAGENT2_DATASETS_JSON` 的 ID→路径映射；该变量是 JSON 内容，不是 catalog 文件路径；`catalog.json` 固定在 dataset_root 下。Coding 验证与 Experiment 正式命令均获得该映射及 `RESAGENT2_DATASET_ROOT`。
- Agent 在运行中判断需要什么；缺少所需数据时用已有 ask_user，用户放置并登记后回答。恢复时重新检查，口头“已准备”不使目录自动变为可用；所需数据仍不在 available 列表时应再次询问，曾经问过不等于可以继续依赖该数据的工作。旧命令结果描述当时的资源状态，不覆盖本轮重新检查的视图。
- 目录存在只证明可定位；内部文件缺失或内容错误仍需从实际读取诊断。prompt 要求请求用户处理，不下载、不猜路径、不替代数据；这是行为指引，不是 OS 沙箱或强制资源选择器。

不新增资源 Agent、通用 Resource 类、自动扫描或下载器。当前目录规模用精简 ID 上下文；不宣称大规模资源检索或实时热更新。实际恢复路径和验证见 [资源验收单](../history/reviews/RUNTIME_RESOURCES_ACCEPTANCE.md)。

环境能力由 Coding 与 Experiment 共用（ADR-0009）：

依赖需求可由代码和运行时反馈发现，沿用 prepare_environment → run_setup → audit_env。镜像与 pip/conda 包缓存属于部署/包管理器配置，不新增到 ResearchRequest，也不与数据集登记表合并；同名依赖或缓存命中不代替环境审计。

- `EnvironmentSpec.python_version` 有值表示硬约束，Agent 不得静默覆盖；为空表示 Agent 依据项目自行判断；
- 环境归属 `run_id + workspace_id`：同 Run 同 Workspace 共用（Coding/Experiment 共用、Task 重试复用），不同 Workspace/Run 隔离；`env_id = resenv_<sha256(run_id + "\0" + workspace_id)[:12]>`；
- 三个共享 Tool（capabilities 的公开 Python API）：`prepare_environment` / `run_setup` / `audit_env`。新绑定或真正开始 prepare/setup 时，`EnvironmentBinding.generation` 更新且 `certified=False`；执行成功、失败或抛异常都不能保留旧认证，参数/策略拒绝则不改变代次；
- Coding 的成功验证还须属于最新 edit revision、当前已审计的 generation。setup 后或新进程恢复后，只重新 audit 不会让旧验证复活，必须再验证；
- Python 版本优先级、硬约束不可覆盖、每 Attempt 最多两次版本切换：见 ADR-0009。

<a id="schema"></a>

### schema 版本

- Python 包版本和 wire schema 版本独立演进；
- 增加可选字段至少需要 schema 小版本和迁移说明；
- 删除字段、改字段含义或改变必填性需要不兼容版本；
- 每个版本必须有 round-trip 和非法组合 contract tests；
- metadata 不得长期承载本应成为正式字段的状态；
- schema 版本策略发生改变时必须先写 ADR。

历史字段增删记录见 [开发历程](../history/DEVELOPMENT_PLAN.md) 和 [schema 3.0 矩阵](../history/reviews/SCHEMA_3_DELTA.md)；当前接口不要求同时维护旧 schema 路径。

schema 6.0 删除 ResearchRequest.dataset_refs，将内部引用移到 ResearchRun/ScientificTurnRequest，并明确问答等待不计入 Run 超时（[ADR-0013](../history/decisions/0013-runtime-resources.md)）。不新增迁移或兼容实现。`ResearchRun` 顶层没有 schema_version，但必填 request 等公共契约带版本；JsonRunStore.load 重新校验整个 Run，正常保存的 5.0 及更早 Run 因版本不符被拒绝。读取失败不改写旧文件，继续工作应发起新 Run。

`AgentState` 继承不带版本字段的 `RuntimeModel`，`JsonSessionStore.load` 按该模型校验，不能据此宣称所有旧 Session 文件都会解析失败。`memory` 和 `events.data` 是 JSON 值；`last_observation` 或 `runtime_feedback` 中若含旧版 `QuestionDraft` 等强类型公共契约，则会在对应嵌套校验处被拒绝。部分旧 Session 可单独解析，不等于承诺其兼容恢复，更不提供旧 Run 的续跑路径。schema 6.0 没有改变 Session 顶层结构，也不重写或清理任何既有 state/session/trace。

<a id="exports"></a>

### 公共导出

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
