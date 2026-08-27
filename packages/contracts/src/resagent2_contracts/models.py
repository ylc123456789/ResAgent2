"""Public data contracts shared by every ResAgent2 package."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Generic, Literal, TypeVar, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)


SCHEMA_VERSION = "1.1"

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
RunId = Annotated[
    str, StringConstraints(pattern=r"^run_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
TaskId = Annotated[
    str, StringConstraints(pattern=r"^task_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
SessionId = Annotated[
    str, StringConstraints(pattern=r"^session_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
ArtifactId = Annotated[
    str, StringConstraints(pattern=r"^artifact_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
QuestionId = Annotated[
    str, StringConstraints(pattern=r"^question_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]


class ContractModel(BaseModel):
    """Base for versioned contracts that reject undocumented fields."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"] = SCHEMA_VERSION


class Capability(StrEnum):
    """Stable capability names used for routing, not module names."""

    SCIENTIFIC_PLAN = "scientific_plan"
    SCIENTIFIC_ANALYZE = "scientific_analyze"
    LITERATURE_SEARCH = "literature_search"
    CODE_UNDERSTAND = "code_understand"
    CODE_MODIFY = "code_modify"
    EXPERIMENT_PREPARE = "experiment_prepare"
    EXPERIMENT_RUN = "experiment_run"
    ASK_USER = "ask_user"


class AgentOwner(StrEnum):
    """Module that owns a capability implementation."""

    SCIENTIFIC = "scientific"
    CODING = "coding"
    EXPERIMENT = "experiment"
    ORCHESTRATOR = "orchestrator"


class RunStatus(StrEnum):
    """Lifecycle state of a complete research run."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskStatus(StrEnum):
    """Scheduler-owned lifecycle state of one workflow task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_USER_INPUT = "needs_user_input"
    SUPERSEDED = "superseded"


class AttemptStatus(StrEnum):
    """Outcome of one real module invocation."""

    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_USER_INPUT = "needs_user_input"


class ModuleStatus(StrEnum):
    """Machine-readable result returned by a child module."""

    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_USER_INPUT = "needs_user_input"


class ErrorCode(StrEnum):
    """Stable error categories that policy code may branch on."""

    INVALID_INPUT = "invalid_input"
    PERMISSION_DENIED = "permission_denied"
    TOOL_FAILED = "tool_failed"
    TIMEOUT = "timeout"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CONTRACT_ERROR = "contract_error"
    ENVIRONMENT_UNAVAILABLE = "environment_unavailable"
    ARTIFACT_MISSING = "artifact_missing"


class WorkspaceMode(StrEnum):
    """Maximum access granted inside a workspace."""

    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class WorkspaceSource(StrEnum):
    """How a workspace was obtained; this is not repository identity."""

    EXISTING = "existing"
    CLONE = "clone"
    COPY = "copy"
    GENERATED = "generated"


class SessionStatus(StrEnum):
    """Persisted state of a resumable child-agent session."""

    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    PAUSED = "paused"


class VerificationMode(StrEnum):
    """Whether a success criterion is machine-checkable or human-reviewed."""

    AUTOMATIC = "automatic"
    MANUAL = "manual"


class ScientificVerdict(StrEnum):
    """Scientific relation between collected evidence and a hypothesis."""

    SUPPORTS = "supports"
    REFUTES = "refutes"
    INCONCLUSIVE = "inconclusive"
    NOT_APPLICABLE = "not_applicable"


class ModuleError(ContractModel):
    """Structured failure information used by retry and recovery policies."""

    code: ErrorCode
    message: NonEmptyStr
    retryable: bool
    details: dict[str, JsonValue] = Field(default_factory=dict)


class WarningRecord(ContractModel):
    """A non-fatal, machine-labelled limitation of a successful result."""

    code: NonEmptyStr
    message: NonEmptyStr
    details: dict[str, JsonValue] = Field(default_factory=dict)


class SessionRef(ContractModel):
    """Reference to child-owned resumable state; it does not contain that state."""

    id: SessionId
    module: AgentOwner
    state_uri: NonEmptyStr
    status: SessionStatus
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_timestamps(self) -> SessionRef:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        return self


class ArtifactRef(ContractModel):
    """Immutable, registered output with complete production provenance."""

    id: ArtifactId
    kind: NonEmptyStr
    producer: AgentOwner
    run_id: RunId
    task_id: TaskId
    attempt_number: int = Field(ge=1)
    uri: NonEmptyStr
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    media_type: NonEmptyStr
    summary: NonEmptyStr
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


def _validate_relative_path(value: str) -> str:
    path = value.strip()
    posix = PurePosixPath(path)
    windows = PureWindowsPath(path)
    if not path or posix.is_absolute() or windows.is_absolute() or ".." in posix.parts:
        raise ValueError("path must be a non-empty relative path without '..'")
    return path


class ArtifactCandidate(ContractModel):
    """Child-produced output awaiting orchestrator validation and registration."""

    kind: NonEmptyStr
    path: str
    media_type: NonEmptyStr
    summary: NonEmptyStr
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        """Keep candidate paths relative to the granted workspace."""

        return _validate_relative_path(value)


class RunBudget(ContractModel):
    """Hard orchestration limits for one research run."""

    max_tasks: int = Field(ge=1)
    max_attempts_per_task: int = Field(ge=1)
    max_llm_calls: int = Field(ge=1)
    timeout_seconds: int = Field(ge=1)


class TaskBudget(ContractModel):
    """Hard limits for one child-module invocation."""

    max_steps: int = Field(ge=1)
    max_llm_calls: int = Field(ge=1)
    timeout_seconds: int = Field(ge=1)


class ResearchRequest(ContractModel):
    """User-confirmed research objective and run-wide boundaries."""

    goal: NonEmptyStr
    hypothesis: NonEmptyStr | None = None
    context: str = ""
    constraints: list[NonEmptyStr] = Field(default_factory=list)
    input_artifacts: list[ArtifactRef] = Field(default_factory=list)
    budget: RunBudget


class QuestionDraft(ContractModel):
    """Question proposed by a child module but not yet persisted by ResAgent."""

    text: NonEmptyStr
    requested_fields: list[NonEmptyStr] = Field(default_factory=list)
    reason: NonEmptyStr


class PendingQuestion(ContractModel):
    """Orchestrator-owned question that pauses a run until answered."""

    id: QuestionId
    run_id: RunId
    task_id: TaskId | None = None
    text: NonEmptyStr
    requested_fields: list[NonEmptyStr] = Field(default_factory=list)
    created_at: datetime


class UserAnswer(ContractModel):
    """Validated user values supplied for one persisted question."""

    question_id: QuestionId
    values: dict[NonEmptyStr, str]
    answered_at: datetime


class SuccessCriterion(ContractModel):
    """Observable condition used to decide whether a task is complete."""

    description: NonEmptyStr
    verification: VerificationMode
    evidence_key: NonEmptyStr | None = None

    @model_validator(mode="after")
    def require_automatic_evidence_key(self) -> SuccessCriterion:
        if self.verification == VerificationMode.AUTOMATIC and not self.evidence_key:
            raise ValueError("automatic success criteria require evidence_key")
        return self


class ScientificPlanInput(ContractModel):
    """Inputs for producing or revising a scientific workflow proposal."""

    capability: Literal[Capability.SCIENTIFIC_PLAN] = Capability.SCIENTIFIC_PLAN
    request: ResearchRequest


class ScientificAnalyzeInput(ContractModel):
    """Inputs for drawing a conclusion from registered evidence."""

    capability: Literal[Capability.SCIENTIFIC_ANALYZE] = Capability.SCIENTIFIC_ANALYZE
    question: NonEmptyStr
    evidence_artifact_ids: list[ArtifactId]


class LiteratureSearchInput(ContractModel):
    """Inputs for a bounded literature search."""

    capability: Literal[Capability.LITERATURE_SEARCH] = Capability.LITERATURE_SEARCH
    query: NonEmptyStr
    max_results: int = Field(default=10, ge=1)


class CodeUnderstandInput(ContractModel):
    """Inputs for read-only code inspection and explanation."""

    capability: Literal[Capability.CODE_UNDERSTAND] = Capability.CODE_UNDERSTAND
    question: NonEmptyStr
    paths: list[str] = Field(default_factory=list)

    @field_validator("paths")
    @classmethod
    def validate_paths(cls, values: list[str]) -> list[str]:
        """Keep requested code paths inside the workspace grant."""

        return [_validate_relative_path(value) for value in values]


class CodeModifyInput(ContractModel):
    """Inputs for an authorized code change and its verification."""

    capability: Literal[Capability.CODE_MODIFY] = Capability.CODE_MODIFY
    instructions: NonEmptyStr
    allowed_paths: list[str] = Field(default_factory=list)
    verification_commands: list[NonEmptyStr] = Field(default_factory=list)

    @field_validator("allowed_paths")
    @classmethod
    def validate_paths(cls, values: list[str]) -> list[str]:
        """Keep edit paths relative to the granted workspace."""

        return [_validate_relative_path(value) for value in values]


class VerificationResult(ContractModel):
    """Auditable outcome of one caller-declared verification command."""

    command: NonEmptyStr
    exit_code: int
    timed_out: bool = False
    stdout_path: str
    stderr_path: str
    duration_seconds: float = Field(ge=0)

    @field_validator("stdout_path", "stderr_path")
    @classmethod
    def validate_log_path(cls, value: str) -> str:
        return _validate_relative_path(value)


class CodeUnderstandResult(ContractModel):
    """Typed payload returned by the read-only Coding profile."""

    answer: NonEmptyStr
    evidence_files: list[str] = Field(min_length=1)
    uncertainty: str = ""

    @field_validator("evidence_files")
    @classmethod
    def validate_evidence_paths(cls, values: list[str]) -> list[str]:
        return [_validate_relative_path(value) for value in values]


class CodeModifyResult(ContractModel):
    """Typed payload derived from Git state and verification evidence."""

    changed_files: list[str]
    deleted_files: list[str] = Field(default_factory=list)
    patch_path: str
    verification_results: list[VerificationResult] = Field(default_factory=list)
    verification_passed: bool
    residual_risks: list[NonEmptyStr] = Field(default_factory=list)

    @field_validator("changed_files", "deleted_files")
    @classmethod
    def validate_changed_paths(cls, values: list[str]) -> list[str]:
        return [_validate_relative_path(value) for value in values]

    @field_validator("patch_path")
    @classmethod
    def validate_patch_path(cls, value: str) -> str:
        return _validate_relative_path(value)

    @model_validator(mode="after")
    def validate_result(self) -> CodeModifyResult:
        if not self.changed_files and not self.deleted_files:
            raise ValueError("code modification result requires a workspace change")
        if set(self.changed_files) & set(self.deleted_files):
            raise ValueError("a path cannot be both changed and deleted")
        passed = all(
            item.exit_code == 0 and not item.timed_out
            for item in self.verification_results
        )
        if self.verification_passed != passed:
            raise ValueError("verification_passed must match verification_results")
        return self


class ExperimentPrepareInput(ContractModel):
    """Inputs for preparing and auditing an experimental repository."""

    capability: Literal[Capability.EXPERIMENT_PREPARE] = Capability.EXPERIMENT_PREPARE
    repository_url: NonEmptyStr | None = None
    source_artifact_ids: list[ArtifactId] = Field(default_factory=list)
    requirements: list[NonEmptyStr] = Field(default_factory=list)


class ExperimentRunInput(ContractModel):
    """Inputs for running an experiment and collecting named evidence."""

    capability: Literal[Capability.EXPERIMENT_RUN] = Capability.EXPERIMENT_RUN
    instructions: NonEmptyStr
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    expected_metrics: list[NonEmptyStr] = Field(default_factory=list)
    expected_artifacts: list[NonEmptyStr] = Field(default_factory=list)
    repository_url: NonEmptyStr | None = None
    copy_from: NonEmptyStr | None = None
    external_repo_path: NonEmptyStr | None = None
    python_version: str = "3.12"
    confirm_before_experiment: bool = False

    @model_validator(mode="after")
    def validate_single_repository_source(self) -> ExperimentRunInput:
        """Require at most one repository source for a fresh experiment."""
        sources = [
            name
            for name, value in (
                ("repository_url", self.repository_url),
                ("copy_from", self.copy_from),
                ("external_repo_path", self.external_repo_path),
            )
            if value is not None
        ]
        if len(sources) > 1:
            raise ValueError(
                "repository_url, copy_from, and external_repo_path are mutually exclusive"
            )
        return self


class ExperimentResult(ContractModel):
    """Typed payload returned by the native Experiment Agent."""

    metrics: dict[str, JsonValue] = Field(default_factory=dict)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    evidence_files: list[str] = Field(default_factory=list)
    repo_url: str = ""
    commit: str = ""
    env_id: NonEmptyStr
    delivery_issues: list[NonEmptyStr] = Field(default_factory=list)
    residual_risks: list[NonEmptyStr] = Field(default_factory=list)

    @field_validator("evidence_files")
    @classmethod
    def validate_evidence_paths(cls, values: list[str]) -> list[str]:
        return [_validate_relative_path(value) for value in values]


class AskUserInput(ContractModel):
    """Inputs for the orchestrator-owned human interaction capability."""

    capability: Literal[Capability.ASK_USER] = Capability.ASK_USER
    question: QuestionDraft


CapabilityInput = Annotated[
    Union[
        ScientificPlanInput,
        ScientificAnalyzeInput,
        LiteratureSearchInput,
        CodeUnderstandInput,
        CodeModifyInput,
        ExperimentPrepareInput,
        ExperimentRunInput,
        AskUserInput,
    ],
    Field(discriminator="capability"),
]


class Attempt(ContractModel):
    """Immutable history entry for one real child-module invocation boundary."""

    number: int = Field(ge=1)
    status: AttemptStatus
    started_at: datetime
    finished_at: datetime | None = None
    session: SessionRef | None = None
    artifact_ids: list[ArtifactId] = Field(default_factory=list)
    error: ModuleError | None = None
    payload: JsonValue | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> Attempt:
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at cannot be earlier than started_at")
        if self.status == AttemptStatus.RUNNING:
            if self.finished_at is not None or self.error is not None:
                raise ValueError("running attempt cannot have finished_at or error")
            return self
        if self.finished_at is None:
            raise ValueError("terminal attempt requires finished_at")
        if self.status in {AttemptStatus.FAILED, AttemptStatus.BLOCKED}:
            if self.error is None:
                raise ValueError("failed or blocked attempt requires error")
        elif self.error is not None:
            raise ValueError(f"{self.status.value} attempt cannot have error")
        return self


def _validate_capability_input(capability: Capability, inputs: CapabilityInput) -> None:
    if capability != inputs.capability:
        raise ValueError(
            f"capability {capability.value!r} does not match inputs "
            f"{inputs.capability.value!r}"
        )


def _require_task_capability(capability: Capability) -> None:
    """Reject control-plane capabilities that are never scheduled as tasks."""
    if capability in {Capability.SCIENTIFIC_PLAN, Capability.ASK_USER}:
        raise ValueError(
            f"capability {capability.value!r} is control-plane and cannot be a task"
        )


class TaskProposal(ContractModel):
    """Scientific suggestion for one logical task, before scheduler acceptance."""

    id: TaskId
    capability: Capability
    goal: NonEmptyStr
    rationale: NonEmptyStr
    depends_on: list[TaskId] = Field(default_factory=list)
    required: bool = True
    inputs: CapabilityInput
    success_criteria: list[SuccessCriterion]

    @model_validator(mode="after")
    def validate_input_type(self) -> TaskProposal:
        _validate_capability_input(self.capability, self.inputs)
        _require_task_capability(self.capability)
        return self


class WorkflowTask(ContractModel):
    """The single scheduler-owned top-level unit of work."""

    id: TaskId
    capability: Capability
    goal: NonEmptyStr
    inputs: CapabilityInput
    depends_on: list[TaskId] = Field(default_factory=list)
    required: bool = True
    status: TaskStatus = TaskStatus.PENDING
    input_artifacts: list[ArtifactId] = Field(default_factory=list)
    success_criteria: list[SuccessCriterion]
    attempts: list[Attempt] = Field(default_factory=list)
    warnings: list[WarningRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_task(self) -> WorkflowTask:
        _validate_capability_input(self.capability, self.inputs)
        _require_task_capability(self.capability)
        numbers = [attempt.number for attempt in self.attempts]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError("attempt numbers must be contiguous and start at 1")
        return self


def _validate_task_graph(tasks: list[TaskProposal] | list[WorkflowTask]) -> None:
    ids = [task.id for task in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate task id")
    known = set(ids)
    for task in tasks:
        for dependency in task.depends_on:
            if dependency not in known:
                raise ValueError(
                    f"task {task.id!r} depends on unknown task {dependency!r}"
                )

    dependencies = {task.id: task.depends_on for task in tasks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise ValueError("workflow dependency cycle detected")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in dependencies[task_id]:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in ids:
        visit(task_id)


class WorkflowProposal(ContractModel):
    """Scientific Agent recommendation that requires orchestrator validation."""

    summary: NonEmptyStr
    tasks: list[TaskProposal]
    questions: list[QuestionDraft] = Field(default_factory=list)
    scientific_rationale: NonEmptyStr

    @model_validator(mode="after")
    def validate_graph(self) -> WorkflowProposal:
        _validate_task_graph(self.tasks)
        return self


class Workflow(ContractModel):
    """Accepted, revisioned task graph persisted by the orchestrator."""

    run_id: RunId
    revision: int = Field(ge=1)
    tasks: list[WorkflowTask]
    created_from: NonEmptyStr

    @model_validator(mode="after")
    def validate_graph(self) -> Workflow:
        _validate_task_graph(self.tasks)
        return self


class PendingTaskUpdate(ContractModel):
    """Requested input or dependency replacement for one pending task."""

    task_id: TaskId
    inputs: CapabilityInput | None = None
    depends_on: list[TaskId] | None = None

    @model_validator(mode="after")
    def require_change(self) -> PendingTaskUpdate:
        if self.inputs is None and self.depends_on is None:
            raise ValueError("pending task update must change inputs or depends_on")
        return self


class WorkflowPatch(ContractModel):
    """Revision-bound proposal to extend or supersede pending workflow work."""

    based_on_revision: int = Field(ge=1)
    reason: NonEmptyStr
    add_tasks: list[TaskProposal] = Field(default_factory=list)
    supersede_task_ids: list[TaskId] = Field(default_factory=list)
    pending_task_updates: list[PendingTaskUpdate] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_local_ids(self) -> WorkflowPatch:
        added = [task.id for task in self.add_tasks]
        superseded = self.supersede_task_ids
        updated = [update.task_id for update in self.pending_task_updates]
        if len(added) != len(set(added)):
            raise ValueError("duplicate task id in add_tasks")
        if len(superseded) != len(set(superseded)):
            raise ValueError("duplicate task id in supersede_task_ids")
        if len(updated) != len(set(updated)):
            raise ValueError("duplicate task id in pending_task_updates")
        if set(added) & set(superseded):
            raise ValueError("new task cannot also be superseded")
        return self


class WorkspaceGrant(ContractModel):
    """Explicit filesystem boundary granted to one module invocation."""

    root: NonEmptyStr
    mode: WorkspaceMode
    allowed_paths: list[str] = Field(default_factory=list)
    denied_paths: list[str] = Field(default_factory=list)
    source: WorkspaceSource

    @field_validator("allowed_paths", "denied_paths")
    @classmethod
    def validate_paths(cls, values: list[str]) -> list[str]:
        """Require every grant path to be relative to root."""

        return [_validate_relative_path(value) for value in values]


class ModuleTaskRequest(ContractModel):
    """Uniform orchestrator request envelope for one child-module attempt."""

    run_id: RunId
    task_id: TaskId
    attempt_number: int = Field(ge=1)
    capability: Capability
    goal: NonEmptyStr
    inputs: CapabilityInput
    input_artifacts: list[ArtifactRef] = Field(default_factory=list)
    constraints: list[NonEmptyStr] = Field(default_factory=list)
    answers: list[UserAnswer] = Field(default_factory=list)
    budget: TaskBudget
    workspace: WorkspaceGrant | None = None
    parent_session_id: SessionId | None = None

    @model_validator(mode="after")
    def validate_input_type(self) -> ModuleTaskRequest:
        _validate_capability_input(self.capability, self.inputs)
        return self


PayloadT = TypeVar("PayloadT")


class ModuleResult(ContractModel, Generic[PayloadT]):
    """Uniform child-module result envelope with a capability-specific payload."""

    status: ModuleStatus
    summary: NonEmptyStr
    payload: PayloadT | None = None
    artifacts: list[ArtifactCandidate] = Field(default_factory=list)
    session: SessionRef | None = None
    question: QuestionDraft | None = None
    error: ModuleError | None = None
    warnings: list[WarningRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status_fields(self) -> ModuleResult[PayloadT]:
        if self.status == ModuleStatus.NEEDS_USER_INPUT:
            if self.question is None or self.error is not None:
                raise ValueError(
                    "needs_user_input result requires question and cannot have error"
                )
        elif self.status in {ModuleStatus.FAILED, ModuleStatus.BLOCKED}:
            if self.error is None or self.question is not None:
                raise ValueError(
                    "failed or blocked result requires error and cannot have question"
                )
        elif self.error is not None or self.question is not None:
            raise ValueError("completed result cannot have error or question")
        if (
            self.status == ModuleStatus.COMPLETED_WITH_WARNINGS
            and not self.warnings
        ):
            raise ValueError("completed_with_warnings result requires warnings")
        if self.status == ModuleStatus.COMPLETED and self.warnings:
            raise ValueError("completed result cannot contain warnings")
        return self


class CapabilityDefinition(ContractModel):
    """Public registry entry describing ownership and completion evidence."""

    capability: Capability
    owner: AgentOwner
    request_model: NonEmptyStr
    result_model: NonEmptyStr
    side_effects: list[NonEmptyStr] = Field(default_factory=list)
    permission_policy: NonEmptyStr
    completion_evidence: list[NonEmptyStr]


class CapabilityRegistry(ContractModel):
    """Validated capability table with exactly one owner per capability."""

    definitions: list[CapabilityDefinition]

    @model_validator(mode="after")
    def reject_duplicate_capabilities(self) -> CapabilityRegistry:
        capabilities = [item.capability for item in self.definitions]
        if len(capabilities) != len(set(capabilities)):
            raise ValueError("duplicate capability definition")
        return self


class ScientificConclusion(ContractModel):
    """Scientific interpretation of evidence, independent of execution success."""

    verdict: ScientificVerdict
    summary: NonEmptyStr
    evidence_artifact_ids: list[ArtifactId]
    limitations: list[NonEmptyStr] = Field(default_factory=list)
    recommended_next_steps: list[NonEmptyStr] = Field(default_factory=list)
