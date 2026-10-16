"""Public data contracts shared by every ResAgent2 package.

The declarations are grouped by the boundary they describe:

1. contract foundations and identifiers;
2. routing and lifecycle vocabulary;
3. shared diagnostics, sessions, and artifacts;
4. run entry, budgets, and user interaction;
5. resource declarations and execution evidence;
6. task attempts and workflow graphs;
7. workspaces and the uniform child-module boundary;
8. workflow Agent registry;
9. scientific lifecycle and artifact content.

This file defines valid data shapes and cross-field invariants. State-transition
policy and execution behavior remain in the orchestrator and Agent packages.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)


# ---------------------------------------------------------------------------
# 1. Contract foundations and stable identifiers
# ---------------------------------------------------------------------------


SCHEMA_VERSION = "29.0"

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _nonblank_text(value: str) -> str:
    if not value.strip():
        raise ValueError("text must contain non-whitespace characters")
    return value


VerbatimText = Annotated[
    str, StringConstraints(min_length=1), AfterValidator(_nonblank_text)
]
AnswerFieldName = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$"),
    Field(
        description="Short machine-readable answer key, e.g. mode or file_choice. "
        "Use 1-64 ASCII letters, digits or underscores, starting with a letter. "
        "Put the question, options and explanations in text, not in this key."
    ),
]
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
WorkRequestId = Annotated[
    str, StringConstraints(pattern=r"^work_[A-Za-z0-9][A-Za-z0-9_-]*$")
]
WorkspaceId = Annotated[
    str, StringConstraints(pattern=r"^ws_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
OutputName = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
]


class ContractModel(BaseModel):
    """Base for versioned contracts that reject undocumented fields."""

    model_config = ConfigDict(extra="forbid", revalidate_instances="always")

    schema_version: Literal["29.0"] = SCHEMA_VERSION


# ---------------------------------------------------------------------------
# 2. Routing, ownership, and lifecycle vocabulary
# ---------------------------------------------------------------------------


class WorkflowAgentKind(StrEnum):
    """Execution modules allowed in a workflow graph."""

    CODING = "coding"
    EXPERIMENT = "experiment"


class AgentOwner(StrEnum):
    """Trusted producer or session owner."""

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
    CANCELLED = "cancelled"


class AttemptStatus(StrEnum):
    """Lifecycle of a task attempt, possibly spanning paused module calls."""

    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_USER_INPUT = "needs_user_input"
    CANCELLED = "cancelled"


class ModuleStatus(StrEnum):
    """Machine-readable result returned by a child module."""

    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_USER_INPUT = "needs_user_input"
    REQUEST_WORK = "request_work"
    YIELDED = "yielded"
    INTERRUPTED = "interrupted"
    REPLY_TO_TASK = "reply_to_task"
    ABANDON_WORK = "abandon_work"


class ErrorCode(StrEnum):
    """Stable error categories that policy code may branch on."""

    INVALID_INPUT = "invalid_input"
    PERMISSION_DENIED = "permission_denied"
    TOOL_FAILED = "tool_failed"
    AGENT_REPORTED_FAILURE = "agent_reported_failure"
    TIMEOUT = "timeout"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CONTRACT_ERROR = "contract_error"
    ARTIFACT_MISSING = "artifact_missing"
    INTERRUPTED = "interrupted"


class WorkspaceSourceKind(StrEnum):
    """Logical source kind of a workspace (declared, not repository identity).

    - GIT: ``location`` is a Git URL, cloned into a managed directory;
    - LOCAL: bind an existing local directory in place (managed=False);
    - COPY: copy an existing local Git worktree into a managed directory;
    - GENERATED: create an empty managed workspace.
    """

    GIT = "git"
    LOCAL = "local"
    COPY = "copy"
    GENERATED = "generated"


class SessionStatus(StrEnum):
    """Persisted state of a resumable child-agent session."""

    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class ScientificVerdict(StrEnum):
    """Scientific relation between collected evidence and a hypothesis."""

    SUPPORTS = "supports"
    REFUTES = "refutes"
    INCONCLUSIVE = "inconclusive"
    NOT_APPLICABLE = "not_applicable"


# ---------------------------------------------------------------------------
# 3. Shared diagnostics, resumable sessions, and immutable evidence
# ---------------------------------------------------------------------------


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


SYSTEM_ARTIFACT_PROVENANCE = {
    "acceptance_requirements": ("task_requirement", frozenset({"task"})),
    "conclusion_requirements": ("conclusion_requirement", frozenset({"run"})),
    "dataset_catalog": ("dataset_catalog", frozenset({"run"})),
    "module_report": ("task_report", frozenset({"attempt"})),

}
SYSTEM_ARTIFACT_KINDS = frozenset(SYSTEM_ARTIFACT_PROVENANCE) - {"module_report"}
SCIENTIFIC_ARTIFACT_KINDS = frozenset({
    "literature_paper", "literature_pdf", "literature_fulltext",
    "web_page",
    "scientific_opinion", "scientific_assessment",
    "observation_trace", "module_report",
})
SYSTEM_GENERATED_ARTIFACT_KINDS = SYSTEM_ARTIFACT_KINDS | frozenset({
    "code_patch", "execution_record", "observation_trace",
    "web_page",
    "literature_paper", "literature_pdf", "literature_fulltext", "final_report",
})


class ArtifactRef(ContractModel):
    """Immutable, registered output with complete production provenance."""

    id: ArtifactId
    kind: NonEmptyStr
    producer: AgentOwner
    run_id: RunId
    task_id: TaskId | None = None
    attempt_number: int | None = Field(default=None, ge=1)
    session_id: SessionId | None = None
    uri: NonEmptyStr
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    media_type: NonEmptyStr
    summary: NonEmptyStr
    output_name: OutputName | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_provenance(self) -> ArtifactRef:
        """Validate kind-specific producer and ownership shapes.

        Runtime artifacts still use task+attempt or Scientific session scope.
        System artifacts use explicit, kind-scoped orchestrator shapes so that
        the registry and contract layer cannot disagree about provenance.
        """
        has_task = self.task_id is not None
        has_attempt = self.attempt_number is not None
        has_session = self.session_id is not None
        source_type = self.metadata.get("source_type")

        if self.producer != AgentOwner.ORCHESTRATOR:
            if self.kind in SYSTEM_ARTIFACT_KINDS:
                raise ValueError(f"{self.kind} requires orchestrator producer")
            if has_session:
                if has_task or has_attempt or self.producer != AgentOwner.SCIENTIFIC:
                    raise ValueError(
                        "session-bound non-orchestrator artifact requires scientific producer "
                        "and no task/attempt"
                    )
                if self.kind not in SCIENTIFIC_ARTIFACT_KINDS:
                    raise ValueError(f"unsupported scientific artifact kind: {self.kind}")
                return self
            if has_task != has_attempt:
                raise ValueError(
                    "execution artifact requires both task_id and attempt_number"
                )
            if not has_task:
                raise ValueError(
                    "non-orchestrator artifact requires task/attempt or scientific session scope"
                )
            return self

        policy = SYSTEM_ARTIFACT_PROVENANCE.get(self.kind)
        if policy is not None:
            expected_source, scopes = policy
            scope = {
                (False, False, False): "run", (True, False, False): "task",
                (True, True, False): "attempt", (False, False, True): "session",
            }.get((has_task, has_attempt, has_session))
            if source_type != expected_source or scope not in scopes:
                raise ValueError(f"{self.kind} requires orchestrator scope {sorted(scopes)} and source_type={expected_source}")
            return self

        if has_task or has_attempt or has_session:
            raise ValueError(
                "unknown orchestrator artifact kind cannot carry task, attempt, or session scope"
            )
        if source_type not in {"import", "final_report"}:
            raise ValueError(
                "run-only orchestrator artifact requires metadata.source_type "
                "in {import, final_report}"
            )
        return self


def _validate_relative_path(value: str) -> str:
    path = value.strip()
    posix = PurePosixPath(path)
    windows = PureWindowsPath(path)
    if (
        not path
        or posix.is_absolute()
        or windows.is_absolute()
        or ".." in posix.parts
        or ".." in windows.parts
    ):
        raise ValueError("path must be a non-empty relative path without '..'")
    return path


class ArtifactCandidate(ContractModel):
    """Child-produced output awaiting orchestrator validation and registration.

    A candidate is normally a workspace file (``path`` is workspace-relative).
    When ``content`` is set, the candidate carries its bytes directly and
    ``path`` is only the filename used inside the artifact store; this lets a
    child register a derived artifact (e.g. a patch) that lives in the Run data
    directory rather than inside the source repository.
    """

    kind: NonEmptyStr
    path: str
    media_type: NonEmptyStr
    summary: NonEmptyStr
    output_name: OutputName | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    content: str | None = None

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        """Keep candidate paths relative to the granted workspace."""

        return _validate_relative_path(value)


# ---------------------------------------------------------------------------
# 4. Run entry, budgets, imported inputs, and user interaction
# ---------------------------------------------------------------------------


class RunBudget(ContractModel):
    """Run limits; timeout counts wall time except explicit ask_user pauses."""

    max_llm_calls: int = Field(ge=1)
    timeout_seconds: int = Field(ge=1)


class ExecutionLimits(ContractModel):
    """Fixed workflow growth limits, separate from consumed Run resources."""

    max_tasks: int = Field(default=8, ge=1)
    max_attempts_per_task: int = Field(default=2, ge=1)


class RunPermissions(ContractModel):
    """Trusted caller grants and operation-review selection."""

    execute_commands: bool = False
    approval_mode: Literal["manual", "auto_review"] = "manual"


class TaskBudget(ContractModel):
    """Hard limits for one child-module invocation."""

    max_llm_calls: int = Field(ge=1)
    timeout_seconds: int = Field(ge=1)


class TaskAcceptanceSpec(ContractModel):
    """Optional exact delivery requirements for one execution Agent."""

    required_metric_keys: list[NonEmptyStr] = Field(default_factory=list)
    required_artifact_paths: list[NonEmptyStr] = Field(default_factory=list)
    required_artifact_kinds: list[NonEmptyStr] = Field(default_factory=list)
    required_output_names: list[OutputName] = Field(default_factory=list)

    @field_validator("required_artifact_paths")
    @classmethod
    def validate_artifact_paths(cls, values: list[str]) -> list[str]:
        return [_validate_relative_path(value) for value in values]


class FutureArtifactBinding(ContractModel):
    """Logical reference to one output of a direct dependency task."""

    source_task: TaskId
    output_selector: OutputName


class ArtifactImport(ContractModel):
    """Minimal caller-supplied input that becomes a frozen orchestrator Artifact.

    Unlike an ``ArtifactRef`` it carries no provenance or hash yet: the
    ResearchController validates the local URI, freezes a copy, verifies the
    optional hash and emits an ``orchestrator/import`` ArtifactRef (ADR-0011 §4).
    """

    uri: NonEmptyStr
    kind: NonEmptyStr
    media_type: NonEmptyStr
    summary: NonEmptyStr
    expected_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


RequiredEvidenceKind = Literal["literature_paper", "literature_fulltext"]


class ResearchRequest(ContractModel):
    """User-confirmed research objective and run-wide boundaries."""

    goal: NonEmptyStr
    hypothesis: NonEmptyStr | None = None
    context: str = ""
    constraints: list[NonEmptyStr] = Field(default_factory=list)
    input_artifacts: list[ArtifactImport] = Field(default_factory=list)
    required_evidence_kinds: list[RequiredEvidenceKind] = Field(default_factory=list)
    # Exact registered logical output names, never inferred from task prose.
    required_artifacts: list[OutputName] = Field(default_factory=list)
    budget: RunBudget
    execution_limits: ExecutionLimits = Field(default_factory=ExecutionLimits)
    permissions: RunPermissions


class ActionSnapshot(ContractModel):
    """Immutable public identity of a system-prepared approval request."""

    action_id: NonEmptyStr
    tool: NonEmptyStr
    arguments: dict[str, JsonValue]
    context: dict[str, JsonValue] = Field(default_factory=dict)
    run_id: RunId
    session_id: SessionId
    task_id: TaskId | None = None
    attempt_number: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_scope(self) -> ActionSnapshot:
        if (self.task_id is None) != (self.attempt_number is None):
            raise ValueError("action task_id and attempt_number must be paired")
        return self


class QuestionDraft(ContractModel):
    """Self-contained user question, including any background needed to answer."""

    related_question_id: QuestionId | None = None
    text: NonEmptyStr
    requested_fields: list[AnswerFieldName] = Field(min_length=1)
    options: dict[AnswerFieldName, list[NonEmptyStr]] | None = None
    action: ActionSnapshot | None = None


class PendingQuestion(ContractModel):
    """Orchestrator-owned question that pauses a run until answered."""

    id: QuestionId
    run_id: RunId
    session_id: SessionId
    recipient_session_id: SessionId | None = None
    task_id: TaskId | None = None
    attempt_number: int | None = Field(default=None, ge=1)
    related_question_id: QuestionId | None = None
    text: NonEmptyStr
    requested_fields: list[AnswerFieldName] = Field(min_length=1)
    options: dict[AnswerFieldName, list[NonEmptyStr]] | None = None
    created_at: datetime
    action: ActionSnapshot | None = None


class BusinessInput(ContractModel):
    """Original upstream text, ordered by its original event time and identity."""

    event_id: NonEmptyStr
    text: VerbatimText
    source: Literal["user", "parent"]
    created_at: datetime
    source_session_id: SessionId | None = None
    original_event_id: str | None = None


class UserAnswer(ContractModel):
    """Validated user values supplied for one persisted question."""

    question_id: QuestionId
    values: dict[AnswerFieldName, str] = Field(min_length=1)
    answered_at: datetime
    comment: str | None = None


class RecordedAnswer(UserAnswer):
    """System-paired reply with the immutable question and recovery scope."""

    source: Literal["user", "parent"] = "user"
    source_session_id: SessionId | None = None
    original_question_id: QuestionId | None = None
    question_text: NonEmptyStr
    requested_fields: list[AnswerFieldName] = Field(min_length=1)
    options: dict[AnswerFieldName, list[NonEmptyStr]] | None = None
    run_id: RunId
    session_id: SessionId | None = None
    task_id: TaskId | None = None
    attempt_number: int | None = Field(default=None, ge=1)
    action: ActionSnapshot | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> RecordedAnswer:
        if self.comment is not None and self.action is None:
            raise ValueError("operation comments require an approval question; business feedback belongs in values")
        if (self.task_id is None) != (self.attempt_number is None):
            raise ValueError("answer task_id and attempt_number must be paired")
        if self.task_id is None and self.session_id is None:
            raise ValueError("answer requires task/attempt or session scope")
        if set(self.values) != set(self.requested_fields):
            raise ValueError("answer values must match the recorded requested_fields")
        return self


# ---------------------------------------------------------------------------
# 5. Resource declarations and execution evidence
# ---------------------------------------------------------------------------


class VerificationResult(ContractModel):
    """Auditable command outcome; exit zero does not establish task correctness.

    ``stdout_path``/``stderr_path`` may be workspace-relative or absolute; they
    locate a durable log file produced by the ProcessRunner (which may live in
    the Run data directory rather than inside the source repository).
    """

    command: str = Field(min_length=1)
    exit_code: int
    timed_out: bool = False
    stdout_path: NonEmptyStr
    stderr_path: NonEmptyStr
    duration_seconds: float = Field(ge=0)
    cwd: NonEmptyStr | None = None
    shell: NonEmptyStr | None = None
    shell_args: list[str] | None = None
    pipefail: bool | None = None
    environment_fingerprint: NonEmptyStr | None = None


class DatasetRef(ContractModel):
    """System-supplied catalog entry, not a caller requirement or usage record.

    ``relative_path`` is resolved against the ResourceLayout dataset root at
    runtime; it is the dataset's directory relative to that root (never an
    absolute path, never containing ``..``).
    """

    dataset_id: NonEmptyStr
    relative_path: str

    @field_validator("relative_path")
    @classmethod
    def validate_relative(cls, value: str) -> str:
        return _validate_relative_path(value)


# ---------------------------------------------------------------------------
# 6. Attempt history and revisioned workflow graphs
# ---------------------------------------------------------------------------


class Attempt(ContractModel):
    """Immutable history entry for one real child-module invocation boundary."""

    number: int = Field(ge=1)
    status: AttemptStatus
    started_at: datetime
    finished_at: datetime | None = None
    session: SessionRef | None = None
    artifact_ids: list[ArtifactId] = Field(default_factory=list)
    error: ModuleError | None = None
    report: str = ""
    acceptance_ref: ArtifactRef | None = None
    report_artifact_id: ArtifactId | None = None
    agent_started: bool = False
    warnings: list[WarningRecord] = Field(default_factory=list)
    reception_error: ModuleError | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> Attempt:
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at cannot be earlier than started_at")
        # A running attempt and one paused for user input are both non-terminal:
        # neither carries finished_at or error. The paused attempt resumes on the
        # same Attempt number (ADR-0011 §2), so it is not a terminal outcome.
        if self.status in {AttemptStatus.RUNNING, AttemptStatus.NEEDS_USER_INPUT}:
            if self.finished_at is not None or self.error is not None:
                raise ValueError(
                    f"{self.status.value} attempt cannot have finished_at or error"
                )
            return self
        if self.finished_at is None:
            raise ValueError("terminal attempt requires finished_at")
        if self.status in {AttemptStatus.FAILED, AttemptStatus.BLOCKED, AttemptStatus.CANCELLED}:
            if self.error is None:
                raise ValueError("failed or blocked attempt requires error")
        elif self.error is not None:
            raise ValueError(f"{self.status.value} attempt cannot have error")
        return self


class TaskProposal(ContractModel):
    """Compiler-produced proposal for one logical task, before scheduler acceptance."""

    id: TaskId
    work_request_id: WorkRequestId
    workflow_agent_kind: WorkflowAgentKind
    instruction: VerbatimText
    shared_goal: VerbatimText | None = None
    shared_constraints: list[VerbatimText] = Field(default_factory=list)
    depends_on: list[TaskId] = Field(default_factory=list)
    workspace_id: WorkspaceId | None = None
    acceptance_spec: TaskAcceptanceSpec | None = None
    output_names: list[OutputName] = Field(default_factory=list)
    input_artifacts: list[ArtifactId] = Field(default_factory=list)
    input_artifact_bindings: list[FutureArtifactBinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_outputs(self) -> TaskProposal:
        if len(self.output_names) != len(set(self.output_names)):
            raise ValueError("output_names must be unique")
        return self


class WorkflowTask(ContractModel):
    """The single scheduler-owned top-level unit of work."""

    id: TaskId
    work_request_id: WorkRequestId
    workflow_agent_kind: WorkflowAgentKind
    instruction: VerbatimText
    shared_goal: VerbatimText | None = None
    shared_constraints: list[VerbatimText] = Field(default_factory=list)
    depends_on: list[TaskId] = Field(default_factory=list)
    workspace_id: WorkspaceId | None = None
    acceptance_ref: ArtifactRef | None = None
    status: TaskStatus = TaskStatus.PENDING
    input_artifacts: list[ArtifactId] = Field(default_factory=list)
    input_artifact_bindings: list[FutureArtifactBinding] = Field(default_factory=list)
    attempts: list[Attempt] = Field(default_factory=list)
    warnings: list[WarningRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_task(self) -> WorkflowTask:
        numbers = [attempt.number for attempt in self.attempts]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError("attempt numbers must be contiguous and start at 1")
        return self


def _validate_task_graph(tasks: list[TaskProposal] | list[WorkflowTask]) -> None:
    ids = [task.id for task in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate task id")
    known = set(ids)
    by_id = {task.id: task for task in tasks}
    for task in tasks:
        for dependency in task.depends_on:
            if dependency not in known:
                raise ValueError(
                    f"task {task.id!r} depends on unknown task {dependency!r}"
                )
        for binding in task.input_artifact_bindings:
            if binding.source_task not in known:
                raise ValueError(
                    f"task {task.id!r} binds output of unknown task {binding.source_task!r}"
                )
            if binding.source_task == task.id:
                raise ValueError(f"task {task.id!r} cannot bind its own future output")
            if binding.source_task not in task.depends_on:
                raise ValueError(
                    f"task {task.id!r} future artifact binding requires direct dependency "
                    f"on {binding.source_task!r}"
                )
            source = by_id[binding.source_task]
            if isinstance(source, TaskProposal) and binding.output_selector not in source.output_names:
                raise ValueError("future artifact binding selects an undeclared output name")

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
    """Compiler-produced task graph candidate that needs orchestrator validation."""

    work_request_id: WorkRequestId
    tasks: list[TaskProposal]

    @model_validator(mode="after")
    def validate_graph(self) -> WorkflowProposal:
        _validate_task_graph(self.tasks)
        for task in self.tasks:
            if task.work_request_id != self.work_request_id:
                raise ValueError(
                    "task work_request_id must match its proposal work_request_id"
                )
        return self


class Workflow(ContractModel):
    """Accepted, revisioned task graph persisted by the orchestrator."""

    run_id: RunId
    revision: int = Field(ge=1)
    tasks: list[WorkflowTask]
    created_from: WorkRequestId

    @model_validator(mode="after")
    def validate_graph(self) -> Workflow:
        _validate_task_graph(self.tasks)
        return self


class WorkflowPatch(ContractModel):
    """Append-only revision-bound proposal for extending the workflow.

    A patch only ever adds new tasks; existing tasks are immutable history, so
    supersede/update fields were removed in schema 3.0 (ADR-0011 §5).
    """

    work_request_id: WorkRequestId
    based_on_revision: int = Field(ge=1)
    add_tasks: list[TaskProposal] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_graph(self) -> WorkflowPatch:
        _validate_task_graph(self.add_tasks)
        for task in self.add_tasks:
            if task.work_request_id != self.work_request_id:
                raise ValueError(
                    "task work_request_id must match its patch work_request_id"
                )
        return self


# ---------------------------------------------------------------------------
# 7. Workspace boundaries and the uniform child-module request/result envelope
# ---------------------------------------------------------------------------


class WorkspaceAccess(ContractModel):
    """Relative prefix grants with deny taking precedence over read and write."""

    read_paths: list[str] = Field(default_factory=list)
    write_paths: list[str] = Field(default_factory=list)
    denied_paths: list[str] = Field(default_factory=list)

    @field_validator("read_paths", "write_paths", "denied_paths")
    @classmethod
    def validate_paths(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            path = value.strip().replace("\\", "/")
            if "\x00" in path or PureWindowsPath(path).drive:
                raise ValueError("workspace paths must be relative")
            normalized.append(PurePosixPath(_validate_relative_path(path)).as_posix())
        return list(dict.fromkeys(normalized))

    @staticmethod
    def contains(path: str, prefixes: list[str]) -> bool:
        return any(prefix == "." or path == prefix or path.startswith(prefix + "/")
                   for prefix in prefixes)

    @model_validator(mode="after")
    def validate_write_scope(self) -> WorkspaceAccess:
        if any(not self.contains(path, self.read_paths) for path in self.write_paths):
            raise ValueError("workspace write paths must be inside read paths")
        return self

    @property
    def unrestricted(self) -> bool:
        return "." in self.read_paths and "." in self.write_paths and not self.denied_paths

    def is_subset_of(self, parent: WorkspaceAccess) -> bool:
        return (all(self.contains(path, parent.read_paths) for path in self.read_paths)
                and all(self.contains(path, parent.write_paths) for path in self.write_paths)
                and all(self.contains(path, self.denied_paths) for path in parent.denied_paths))


class WorkspaceGrant(ContractModel):
    """Explicit filesystem boundary granted to one module invocation."""

    root: NonEmptyStr
    access: WorkspaceAccess
    source: WorkspaceSourceKind


class WorkspaceSpec(ContractModel):
    """Declared source of one logical workspace; never a physical path."""

    workspace_id: WorkspaceId
    source_kind: WorkspaceSourceKind
    location: str | None = None
    access: WorkspaceAccess

    @model_validator(mode="after")
    def validate_location(self) -> WorkspaceSpec:
        """GIT/LOCAL/COPY require a location; GENERATED must not carry one."""
        if self.source_kind == WorkspaceSourceKind.GENERATED:
            if self.location is not None:
                raise ValueError("GENERATED workspace must not have a location")
        elif self.location is None:
            raise ValueError(f"{self.source_kind.value} workspace requires a location")
        return self


class WorkspaceRecord(ContractModel):
    """A workspace resolved to a physical root, plus its source declaration."""

    workspace_id: WorkspaceId
    root: NonEmptyStr
    source: WorkspaceSpec
    managed: bool = False

    @model_validator(mode="after")
    def validate_consistency(self) -> WorkspaceRecord:
        if self.workspace_id != self.source.workspace_id:
            raise ValueError("record and source workspace_id must match")
        expect_managed = self.source.source_kind != WorkspaceSourceKind.LOCAL
        if self.managed != expect_managed:
            raise ValueError(
                "managed must be True for non-LOCAL sources and False for LOCAL"
            )
        return self


class WorkspaceDescriptor(ContractModel):
    """Minimal workspace summary the Compiler may see; never physical paths."""

    workspace_id: WorkspaceId
    source_kind: WorkspaceSourceKind
    description: str = ""


class AgentPermissions(RunPermissions):
    """System-granted operations; filesystem writes still require a writable grant."""

    request_work: bool = False


class AgentRequest(ContractModel):
    """One Agent invocation; task content enters only as instruction or artifacts."""

    run_id: RunId
    task_id: TaskId | None = None
    attempt_number: int | None = Field(default=None, ge=1)
    agent: Literal[AgentOwner.CODING, AgentOwner.EXPERIMENT, AgentOwner.SCIENTIFIC]
    instruction: VerbatimText
    input_artifacts: list[ArtifactRef] = Field(default_factory=list)
    budget: TaskBudget
    permissions: AgentPermissions
    workspace: WorkspaceGrant | None = None
    workspace_id: WorkspaceId | None = None
    workspace_spec: WorkspaceSpec | None = None
    output_dir: NonEmptyStr | None = None
    parent_session_id: SessionId | None = None
    upstream_session_id: SessionId | None = None
    upstream_inputs: list[BusinessInput] = Field(default_factory=list)
    runtime_notices: list[dict[str, JsonValue]] = Field(default_factory=list)
    child_questions: list[PendingQuestion] = Field(default_factory=list)
    release_control: bool = False
    task_context: dict[str, JsonValue] = Field(default_factory=dict)
    user_answers: list[RecordedAnswer] = Field(default_factory=list)
    resume_answers: list[RecordedAnswer] = Field(default_factory=list)
    work_results: list[WorkResult] = Field(default_factory=list)
    control_ack: ControlOrigin | None = None

    @model_validator(mode="after")
    def validate_invocation(self) -> AgentRequest:
        if self.agent == AgentOwner.SCIENTIFIC:
            if self.task_id is not None or self.attempt_number is not None:
                raise ValueError("Scientific invocation must be run-scoped")
        elif self.task_id is None or self.attempt_number is None:
            raise ValueError("execution Agent requires task_id and attempt_number")
        if self.permissions.request_work and self.agent != AgentOwner.SCIENTIFIC:
            raise ValueError("only Scientific may request work")
        ids = [artifact.id for artifact in self.input_artifacts]
        if len(ids) != len(set(ids)):
            raise ValueError("input_artifacts must have unique ids")
        if any(artifact.run_id != self.run_id for artifact in self.input_artifacts):
            raise ValueError("input_artifacts must belong to the same run")
        if (self.resume_answers or self.control_ack) and self.parent_session_id is None:
            raise ValueError("resume control data requires a parent session")
        if self.control_ack and self.control_ack.session_id != self.parent_session_id:
            raise ValueError("control acknowledgment belongs to another session")
        for answers in (self.user_answers, self.resume_answers):
            if len({answer.question_id for answer in answers}) != len(answers):
                raise ValueError("answer question ids must be unique")
            for answer in answers:
                if (answer.run_id, answer.task_id, answer.attempt_number) != (
                    self.run_id, self.task_id, self.attempt_number
                ):
                    raise ValueError("answer does not belong to this invocation")
                if self.parent_session_id and answer.session_id != self.parent_session_id:
                    raise ValueError("answer belongs to another session")
        if any(answer.action is not None for answer in self.user_answers):
            raise ValueError("operation approvals are not business answers")
        if self.work_results and self.agent != AgentOwner.SCIENTIFIC:
            raise ValueError("work results are Scientific scoped")
        for result in self.work_results:
            if result.run_id != self.run_id or result.session_id != self.parent_session_id:
                raise ValueError("work result belongs to another run or session")
            if not {ref.id for ref in result.artifacts} <= set(ids):
                raise ValueError("work result artifacts require explicit input authorization")
        return self

    @model_validator(mode="after")
    def validate_workspace(self) -> AgentRequest:
        """Keep the grant, logical id and declared source mutually consistent."""
        if self.workspace_spec is not None:
            if self.workspace is None:
                raise ValueError("workspace_spec requires a workspace grant")
            if (
                self.workspace_id is not None
                and self.workspace_id != self.workspace_spec.workspace_id
            ):
                raise ValueError("workspace_id must match workspace_spec.workspace_id")
            if self.workspace.source != self.workspace_spec.source_kind:
                raise ValueError("workspace.source must match workspace_spec.source_kind")
            if not self.workspace.access.is_subset_of(self.workspace_spec.access):
                raise ValueError("workspace grant exceeds its declared access")
        return self


ArtifactOutput = ArtifactCandidate | ArtifactRef


class ControlOrigin(ContractModel):
    """Identity of the original native control call, stable across recovery."""

    session_id: SessionId
    turn: int = Field(ge=0)
    call_id: NonEmptyStr


class ControlSignal(ContractModel):
    """Persisted control handoff; never an artifact or a model body projection."""

    action: Literal["ask_user", "request_work", "reply_to_task", "abandon_work"]
    source_session_id: SessionId
    source_turn: int = Field(ge=0)
    source_call_id: NonEmptyStr
    question: PendingQuestion | None = None
    work_request: WorkRequest | None = None
    assessment: ScientificAssessment | None = None
    task_reply: dict[str, JsonValue] | None = None
    abandon_work_id: WorkRequestId | None = None
    reason: str | None = None

    @property
    def origin(self) -> ControlOrigin:
        return ControlOrigin(session_id=self.source_session_id,
                             turn=self.source_turn, call_id=self.source_call_id)

    @model_validator(mode="after")
    def validate_payload(self) -> ControlSignal:
        if self.action in {"reply_to_task", "abandon_work"}:
            if self.question is not None or self.work_request is not None:
                raise ValueError("parent control cannot carry a question or new work")
            if self.action == "reply_to_task" and (self.task_reply is None or self.abandon_work_id is not None):
                raise ValueError("reply control requires only task_reply")
            if self.action == "abandon_work" and (self.abandon_work_id is None or self.task_reply is not None):
                raise ValueError("abandon control requires only work id")
            return self
        if self.task_reply is not None or self.abandon_work_id is not None:
            raise ValueError("unexpected parent control payload")
        if self.action == "ask_user":
            if self.question is None or self.work_request is not None:
                raise ValueError("ask_user requires only question")
            if self.question.session_id != self.source_session_id:
                raise ValueError("question belongs to another session")
        elif self.work_request is None or self.question is not None:
            raise ValueError("request_work requires only work_request")
        elif self.work_request.scientific_session_id != self.source_session_id:
            raise ValueError("work request belongs to another session")
        return self


class AgentResult(ContractModel):
    """Common result; report and artifacts are the only business outputs."""

    status: ModuleStatus
    report: Annotated[str, StringConstraints(min_length=1)]
    artifacts: list[ArtifactOutput] = Field(default_factory=list)
    session: SessionRef | None = None
    control: ControlSignal | None = None
    error: ModuleError | None = None
    warnings: list[WarningRecord] = Field(default_factory=list)
    llm_calls: int = Field(default=0, ge=0, strict=True)

    @model_validator(mode="after")
    def validate_status_fields(self) -> AgentResult:
        actions = {
            ModuleStatus.NEEDS_USER_INPUT: "ask_user",
            ModuleStatus.REQUEST_WORK: "request_work",
            ModuleStatus.REPLY_TO_TASK: "reply_to_task",
            ModuleStatus.ABANDON_WORK: "abandon_work",
        }
        if self.status in actions:
            if self.control is None or self.control.action != actions[self.status] or self.error is not None:
                raise ValueError(f"{self.status.value} requires matching control and no error")
            if self.session is None or self.session.status != SessionStatus.PAUSED:
                raise ValueError(f"{self.status.value} result requires a paused session")
            if self.status == ModuleStatus.REQUEST_WORK and self.session.module != AgentOwner.SCIENTIFIC:
                raise ValueError("only Scientific may return request_work")
        elif self.status in {ModuleStatus.YIELDED, ModuleStatus.INTERRUPTED}:
            if self.control is not None or self.error is not None or self.session is None:
                raise ValueError("suspended invocation requires session and no control/error")
        elif self.status in {ModuleStatus.FAILED, ModuleStatus.BLOCKED}:
            if self.error is None or self.control is not None:
                raise ValueError("failed or blocked result requires error and no control")
        elif self.error is not None or self.control is not None:
            raise ValueError("completed result cannot have error or control")
        expected_session_status = {
            ModuleStatus.COMPLETED: SessionStatus.COMPLETED,
            ModuleStatus.COMPLETED_WITH_WARNINGS: SessionStatus.COMPLETED,
            ModuleStatus.FAILED: SessionStatus.FAILED,
            ModuleStatus.BLOCKED: SessionStatus.BLOCKED,
            ModuleStatus.NEEDS_USER_INPUT: SessionStatus.PAUSED,
            ModuleStatus.REQUEST_WORK: SessionStatus.PAUSED,
            ModuleStatus.REPLY_TO_TASK: SessionStatus.PAUSED,
            ModuleStatus.ABANDON_WORK: SessionStatus.PAUSED,
            ModuleStatus.YIELDED: SessionStatus.PAUSED,
            ModuleStatus.INTERRUPTED: SessionStatus.PAUSED,
        }[self.status]
        if self.session is not None and self.session.status != expected_session_status:
            raise ValueError("result status and session status disagree")
        if self.control is not None and self.session.id != self.control.source_session_id:
            raise ValueError("control belongs to another session")
        if (
            self.status == ModuleStatus.COMPLETED_WITH_WARNINGS
            and not self.warnings
        ):
            raise ValueError("completed_with_warnings result requires warnings")
        if self.status == ModuleStatus.COMPLETED and self.warnings:
            raise ValueError("completed result cannot contain warnings")
        return self


# ---------------------------------------------------------------------------
# 8. Workflow Agent registry
# ---------------------------------------------------------------------------


class WorkflowAgentDefinition(ContractModel):
    """Execution module available to the Compiler and Scheduler."""

    workflow_agent_kind: WorkflowAgentKind
    description: str = ""


class WorkflowAgentRegistry(ContractModel):
    """Validated table with exactly one entry per execution module."""

    definitions: list[WorkflowAgentDefinition]

    @model_validator(mode="after")
    def reject_duplicates(self) -> WorkflowAgentRegistry:
        kinds = [item.workflow_agent_kind for item in self.definitions]
        if len(kinds) != len(set(kinds)):
            raise ValueError("duplicate workflow Agent definition")
        return self


# ---------------------------------------------------------------------------
# 9. Scientific control-loop requests, outcomes, and opinions
# ---------------------------------------------------------------------------


class WorkRequestStatus(StrEnum):
    """Lifecycle of one persisted work request within a run."""

    REQUESTED = "requested"
    COMPILING = "compiling"
    EXECUTING = "executing"
    STABLE = "stable"
    CONSUMED = "consumed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ScientificAssessment(ContractModel):
    """Scientific Agent's current judgment before it requests more work."""

    statement: NonEmptyStr
    evidence_artifact_ids: list[ArtifactId] = Field(default_factory=list)
    limitations: list[NonEmptyStr] = Field(default_factory=list)
    unresolved_questions: list[NonEmptyStr] = Field(default_factory=list)


TaskKey = Annotated[
    str, StringConstraints(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
]


class WorkTaskArtifactBinding(ContractModel):
    """A future output selected from a direct dependency's local task key."""

    source_task: TaskKey
    output_selector: OutputName


class WorkTaskDraft(ContractModel):
    """Scientific's original task instruction and resource/handoff controls."""

    key: TaskKey
    instruction: VerbatimText
    depends_on: list[TaskKey] = Field(default_factory=list)
    workspace_id: WorkspaceId | None = None
    input_artifact_ids: list[ArtifactId] = Field(default_factory=list)
    input_artifact_bindings: list[WorkTaskArtifactBinding] = Field(default_factory=list)
    output_names: list[OutputName] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_controls(self) -> WorkTaskDraft:
        for name in ("depends_on", "input_artifact_ids", "output_names"):
            values = getattr(self, name)
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must be unique")
        return self


class WorkRequestDraft(ContractModel):
    """Explicit tasks and shared scientific requirements, before Agent routing."""

    objective: VerbatimText
    constraints: list[VerbatimText] = Field(default_factory=list)
    tasks: list[WorkTaskDraft] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_tasks(self) -> WorkRequestDraft:
        keys = [task.key for task in self.tasks]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate task key")
        by_key = {task.key: task for task in self.tasks}
        visiting, visited = set(), set()

        def visit(key: str) -> None:
            if key in visiting:
                raise ValueError("task dependency cycle")
            if key in visited:
                return
            visiting.add(key)
            task = by_key[key]
            for dependency in task.depends_on:
                if dependency not in by_key:
                    raise ValueError("task dependency references an unknown key")
                visit(dependency)
            for binding in task.input_artifact_bindings:
                if binding.source_task not in task.depends_on:
                    raise ValueError("future artifact source must be a direct dependency")
                if binding.output_selector not in by_key[binding.source_task].output_names:
                    raise ValueError("future artifact selector must name a declared output")
            visiting.remove(key)
            visited.add(key)

        for key in keys:
            visit(key)
        return self


class WorkRequest(ContractModel):
    """ResAgent-persisted work request bound to a run and scientific session."""

    id: WorkRequestId
    run_id: RunId
    scientific_session_id: SessionId
    request: WorkRequestDraft
    status: WorkRequestStatus = WorkRequestStatus.REQUESTED
    workflow_revision: int | None = None
    outcome: WorkResult | None = None
    error: ModuleError | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_status_fields(self) -> WorkRequest:
        if self.outcome is not None and (
            self.outcome.run_id != self.run_id
            or self.outcome.session_id != self.scientific_session_id
            or self.outcome.work_request_id != self.id
            or self.outcome.workflow_revision != self.workflow_revision
        ):
            raise ValueError("work outcome does not match its request identity")
        if self.status in {
            WorkRequestStatus.REQUESTED,
            WorkRequestStatus.COMPILING,
        }:
            if (
                self.workflow_revision is not None
                or self.outcome is not None
                or self.error is not None
            ):
                raise ValueError(
                    f"{self.status.value} work request cannot carry execution fields"
                )
        elif self.status == WorkRequestStatus.EXECUTING:
            if self.workflow_revision is None or self.outcome is not None or self.error is not None:
                raise ValueError(
                    "executing work request requires workflow_revision and no outcome/error"
                )
        elif self.status == WorkRequestStatus.STABLE:
            if self.workflow_revision is None or self.outcome is None or self.error is not None:
                raise ValueError(
                    "stable work request requires workflow_revision and outcome, no error"
                )
        elif self.status == WorkRequestStatus.CONSUMED:
            if self.workflow_revision is None or self.outcome is None or self.error is not None:
                raise ValueError(
                    "consumed work request requires workflow_revision and outcome, no error"
                )
        elif self.status in {WorkRequestStatus.FAILED, WorkRequestStatus.CANCELLED}:
            if self.error is None:
                raise ValueError("failed work request requires error")
        return self


class WorkAttemptResult(ContractModel):
    """Navigation facts for one original terminal Agent report and its outputs."""

    number: int = Field(ge=1)
    status: AttemptStatus
    report_artifact_id: ArtifactId | None = None
    artifact_ids: list[ArtifactId] = Field(default_factory=list)
    error: ModuleError | None = None
    warnings: list[WarningRecord] = Field(default_factory=list)
    reception_error: ModuleError | None = None


class WorkTaskResult(ContractModel):
    """Task navigation, without reprocessing the Agent's report body."""

    task_id: TaskId
    workflow_agent_kind: WorkflowAgentKind
    instruction: VerbatimText
    status: TaskStatus
    attempts: list[WorkAttemptResult] = Field(default_factory=list)
    error: ModuleError | None = None
    warnings: list[WarningRecord] = Field(default_factory=list)


class WorkResult(ContractModel):
    """Terminal WorkRequest facts and the actual authorization increment."""

    run_id: RunId
    session_id: SessionId
    work_request_id: WorkRequestId
    objective: VerbatimText
    status: WorkRequestStatus
    workflow_revision: int | None = Field(default=None, ge=1)
    error: ModuleError | None = None
    tasks: list[WorkTaskResult] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_result(self) -> WorkResult:
        if self.status not in {WorkRequestStatus.STABLE, WorkRequestStatus.FAILED, WorkRequestStatus.CANCELLED}:
            raise ValueError("work result must describe a terminal outcome")
        if self.status in {WorkRequestStatus.FAILED, WorkRequestStatus.CANCELLED} and self.error is None:
            raise ValueError("failed work result requires error")
        if self.status == WorkRequestStatus.STABLE and self.error is not None:
            raise ValueError("stable work result cannot carry error")
        if len({task.task_id for task in self.tasks}) != len(self.tasks):
            raise ValueError("work result task ids must be unique")
        if len({ref.id for ref in self.artifacts}) != len(self.artifacts):
            raise ValueError("work result artifact ids must be unique")
        if any(ref.run_id != self.run_id for ref in self.artifacts):
            raise ValueError("work result artifacts belong to another run")
        return self


class ScientificOpinion(ContractModel):
    """Scientific Agent's final view with evidence and limitations."""

    verdict: ScientificVerdict
    statement: NonEmptyStr
    evidence_artifact_ids: list[ArtifactId] = Field(default_factory=list)
    limitations: list[NonEmptyStr] = Field(default_factory=list)
    unresolved_questions: list[NonEmptyStr] = Field(default_factory=list)
    recommended_next_steps: list[NonEmptyStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_evidence(self) -> ScientificOpinion:
        if self.verdict in {ScientificVerdict.SUPPORTS, ScientificVerdict.REFUTES}:
            if not self.evidence_artifact_ids:
                raise ValueError(
                    f"{self.verdict.value} opinion requires at least one evidence artifact"
                )
        return self


class ArtifactIndexEntry(ContractModel):
    """Safe direct navigation projected from one authoritative ArtifactRef."""

    artifact_id: ArtifactId
    kind: NonEmptyStr
    media_type: NonEmptyStr
    summary: NonEmptyStr
    producer: AgentOwner
    output_name: OutputName | None = None
    task_id: TaskId | None = None
    attempt_number: int | None = Field(default=None, ge=1)
    session_id: SessionId | None = None
    source_artifact_id: ArtifactId | None = None
    source_type: str | None = None
    agent_started: bool | None = None
    source_url: str | None = None
    final_url: str | None = None
    paper_id: str | None = None

    @classmethod
    def from_ref(cls, ref: ArtifactRef):
        return cls(artifact_id=ref.id, kind=ref.kind, media_type=ref.media_type,
                   summary=ref.summary, producer=ref.producer,
                   output_name=ref.output_name, task_id=ref.task_id,
                   attempt_number=ref.attempt_number, session_id=ref.session_id,
                   source_artifact_id=ref.metadata.get("source_artifact_id"),
                   source_type=ref.metadata.get("source_type"),
                   agent_started=ref.metadata.get("agent_started"),
                   source_url=ref.metadata.get("source_url"),
                   final_url=ref.metadata.get("final_url"), paper_id=ref.metadata.get("paper_id"))


class ConclusionRequirements(ContractModel):
    """Run-bound final citation and output requirements, stored as artifact content."""

    required_evidence_kinds: list[RequiredEvidenceKind] = Field(default_factory=list)
    # Case-sensitive ArtifactRef.output_name values, not file paths or kinds.
    required_artifacts: list[OutputName] = Field(default_factory=list)


class ObservationTrace(ContractModel):
    """Observed evidence IDs projected from persisted tool events."""

    observed_artifact_ids: list[ArtifactId] = Field(default_factory=list)


AgentRequest.model_rebuild()
ControlSignal.model_rebuild()
WorkRequest.model_rebuild()
