"""Internal runtime models used by the shared Agentic Loop."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from resagent2_contracts import (
    AgentOwner,
    ArtifactCandidate,
    QuestionDraft,
    RunId,
    SessionId,
    SessionStatus,
    TaskId,
    WarningRecord,
)


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class RuntimeModel(BaseModel):
    """Base for strict runtime-internal structured values."""

    model_config = ConfigDict(extra="forbid")


class AgentAction(RuntimeModel):
    """One typed Tool request selected by an LLM."""

    tool: NonEmptyStr
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    reasoning_summary: str = ""


class ContextSection(RuntimeModel):
    """Named context fragment with deterministic inclusion priority."""

    name: NonEmptyStr
    content: str
    priority: int = 0
    required: bool = False


class ComposedContext(RuntimeModel):
    """Budgeted prompt context and an audit of omitted sections."""

    text: str
    included_sections: list[str]
    omitted_sections: list[str]
    estimated_tokens: int = Field(ge=0)


class FinishCandidate(RuntimeModel):
    """LLM-proposed result that still requires deterministic finalization."""

    proposed_status: NonEmptyStr = "completed"
    result: JsonValue
    artifact_paths: list[NonEmptyStr] = Field(default_factory=list)
    unresolved_items: list[NonEmptyStr] = Field(default_factory=list)


class ToolObservation(RuntimeModel):
    """Normalized output from one Tool without direct state mutation.

    ``ok`` is a machine-readable success flag: it is True for a successful
    read/command and False for a failed command (non-zero exit), rejected
    argument, missing path, or other recoverable failure. Downstream code must
    not infer failure by parsing ``summary`` text.
    """

    summary: NonEmptyStr
    value: JsonValue | None = None
    ok: bool = True
    memory_updates: dict[str, JsonValue] = Field(default_factory=dict)
    finish_candidate: FinishCandidate | None = None
    question: QuestionDraft | None = None
    request_work: JsonValue | None = None


class CompletionDecision(RuntimeModel):
    """Deterministic finalizer decision for the current Agent state."""

    complete: bool
    summary: str = ""
    payload: JsonValue | None = None
    artifacts: list[ArtifactCandidate] = Field(default_factory=list)
    warnings: list[WarningRecord] = Field(default_factory=list)


class PermissionDecision(RuntimeModel):
    """Permission result produced before a Tool can execute."""

    allowed: bool
    reason: str = ""


class AgentEvent(RuntimeModel):
    """Append-only audit event persisted inside an Agent session snapshot."""

    sequence: int = Field(ge=1)
    step: int = Field(ge=0)
    type: Literal["action", "observation", "error"]
    tool: str | None = None
    data: JsonValue
    created_at: datetime


class AgentState(RuntimeModel):
    """Persisted generic state owned by one child Agent session."""

    session_id: SessionId
    agent_name: NonEmptyStr
    owner: AgentOwner
    run_id: RunId
    task_id: TaskId | None = None
    attempt_number: int | None = Field(default=None, ge=1)
    status: SessionStatus = SessionStatus.ACTIVE
    step: int = Field(default=0, ge=0)
    memory: dict[str, JsonValue] = Field(default_factory=dict)
    last_observation: ToolObservation | None = None
    runtime_feedback: ToolObservation | None = None
    runtime_feedback_source: Literal["completion_check", "tool_error"] | None = None
    events: list[AgentEvent] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
