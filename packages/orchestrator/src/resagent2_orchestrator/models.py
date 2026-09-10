"""Persisted orchestration state for one research run."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from resagent2_contracts import (
    ArtifactId,
    ArtifactRef,
    DatasetRef,
    ModuleError,
    PendingQuestion,
    QuestionId,
    ResearchRequest,
    RunId,
    RunStatus,
    ScientificAssessment,
    ScientificOpinion,
    SessionRef,
    UserAnswer,
    Workflow,
    WorkRequest,
    WorkRequestStatus,
    WorkspaceRecord,
    TaskId,
)


class OrchestratorModel(BaseModel):
    """Base for strict orchestrator-owned state."""

    model_config = ConfigDict(extra="forbid")


class CompletionViolationCode(StrEnum):
    """Stable categories for deterministic completion failures."""

    INVALID_SESSION = "invalid_session"
    ACTIVE_CONTROL_STATE = "active_control_state"
    INVALID_OPINION = "invalid_opinion"
    UNKNOWN_EVIDENCE = "unknown_evidence"
    UNOBSERVED_EVIDENCE = "unobserved_evidence"
    MISSING_EVIDENCE_KIND = "missing_evidence_kind"
    MISSING_LIMITATIONS = "missing_limitations"
    INCONSISTENT_TASK_RESULT = "inconsistent_task_result"


class CompletionViolation(OrchestratorModel):
    """One persisted machine-labelled reason a Run cannot complete."""

    code: CompletionViolationCode
    message: str = Field(min_length=1)
    related_ids: list[str] = Field(default_factory=list)


class ResearchRun(OrchestratorModel):
    """Complete persisted state owned by the Research Orchestrator."""

    run_id: RunId
    request: ResearchRequest
    # Catalog references discovered by the system, not datasets actually used.
    dataset_refs: list[DatasetRef] = Field(default_factory=list)
    status: RunStatus
    workflow: Workflow | None = None
    workspaces: dict[str, WorkspaceRecord] = Field(default_factory=dict)
    artifacts: dict[ArtifactId, ArtifactRef] = Field(default_factory=dict)
    pending_question: PendingQuestion | None = None
    answers: list[UserAnswer] = Field(default_factory=list)
    answer_task_ids: dict[QuestionId, TaskId | None] = Field(default_factory=dict)
    workflow_history: list[Workflow] = Field(default_factory=list)
    scientific_session: SessionRef | None = None
    latest_scientific_assessment: ScientificAssessment | None = None
    work_requests: list[WorkRequest] = Field(default_factory=list)
    scientific_observed_artifact_ids: list[ArtifactId] = Field(default_factory=list)
    final_opinion: ScientificOpinion | None = None
    final_report_artifact_id: ArtifactId | None = None
    delivered_answer_ids: list[QuestionId] = Field(default_factory=list)
    llm_calls_used: int = Field(default=0, ge=0)
    # Settled ask_user pauses only; the currently open pause is derived below.
    user_wait_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    completion_violations: list[CompletionViolation] = Field(default_factory=list)
    terminal_error: ModuleError | None = None
    created_at: datetime
    updated_at: datetime

    def remaining_timeout_seconds(self, now: datetime) -> float:
        """One Run clock for Controller and Scheduler: wall time minus user wait.

        Installation, provider latency and ordinary process downtime still count.
        No user-supplied answer timestamp is used to extend this budget.
        """
        waited = self.user_wait_seconds
        if self.status == RunStatus.PAUSED and self.pending_question is not None:
            waited += max(0.0, (now - self.pending_question.created_at).total_seconds())
        elapsed = max(0.0, (now - self.created_at).total_seconds() - waited)
        return max(0.0, self.request.budget.timeout_seconds - elapsed)

    @model_validator(mode="after")
    def validate_active_work_requests(self) -> "ResearchRun":
        """A run may have at most one active work request (ADR-0011 §1)."""
        active = [
            item.id
            for item in self.work_requests
            if item.status
            in {
                WorkRequestStatus.REQUESTED,
                WorkRequestStatus.COMPILING,
                WorkRequestStatus.EXECUTING,
                WorkRequestStatus.STABLE,
            }
        ]
        if len(active) > 1:
            raise ValueError(f"at most one active work request is allowed: {active}")
        return self
