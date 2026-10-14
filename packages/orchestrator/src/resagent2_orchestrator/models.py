"""Persisted orchestration state for one research run."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from resagent2_components.literature import PreparedLiteratureImport

from resagent2_contracts import (
    ArtifactId,
    ArtifactImport,
    ArtifactRef,
    AgentResult,
    AgentRequest,
    BusinessInput,
    DatasetRef,
    ModuleError,
    PendingQuestion,
    QuestionId,
    RecordedAnswer,
    ResearchRequest,
    RunId,
    RunStatus,
    ScientificAssessment,
    ScientificOpinion,
    SessionRef,
    Workflow,
    WorkflowProposal,
    WorkflowPatch,
    WorkRequest,
    WorkRequestStatus,
    WorkspaceRecord,
    WorkResult,
)


class OrchestratorModel(BaseModel):
    """Base for strict orchestrator-owned state."""

    model_config = ConfigDict(extra="forbid")


class CompletionViolationCode(StrEnum):
    """Stable categories for deterministic completion failures."""

    INVALID_SESSION = "invalid_session"
    ACTIVE_CONTROL_STATE = "active_control_state"
    INVALID_OPINION = "invalid_opinion"
    MISSING_EVIDENCE_KIND = "missing_evidence_kind"
    REQUIRED_ARTIFACT_MISSING = "required_artifact_missing"
    MISSING_LIMITATIONS = "missing_limitations"
    INCONSISTENT_TASK_RESULT = "inconsistent_task_result"


class CompletionViolation(OrchestratorModel):
    """One persisted machine-labelled reason a Run cannot complete."""

    code: CompletionViolationCode
    message: str = Field(min_length=1)
    subject: str | None = None
    related_ids: list[str] = Field(default_factory=list)


class RunUsage(OrchestratorModel):
    """Durable reservations; unknown outcomes remain consumed after recovery."""

    requests: dict[str, Literal["succeeded", "failed", "unknown"]] = Field(default_factory=dict)

    @property
    def used(self) -> int:
        return len(self.requests)

    @property
    def outcomes(self) -> dict[str, int]:
        return {outcome: sum(value == outcome for value in self.requests.values())
                for outcome in ("succeeded", "failed", "unknown")}


class PendingRegistration(OrchestratorModel):
    """Allocated identity and expected frozen facts until registration commits."""

    ref: ArtifactRef
    candidate_path: str
    source_path: str | None = None
    await_receipt: bool = False


class RunInitialization(OrchestratorModel):
    """Original inputs and progress saved before the first file is frozen."""

    imports: list[ArtifactImport] = Field(default_factory=list)
    literature: list[PreparedLiteratureImport] = Field(default_factory=list)
    next_import: int = Field(default=0, ge=0)
    next_literature: int = Field(default=0, ge=0)
    complete: bool = False


class PendingModuleResult(OrchestratorModel):
    """Result already returned by a port, awaiting its receiving gate and registration."""

    request: AgentRequest
    result: AgentResult
    previous_artifact_ids: list[ArtifactId] = Field(default_factory=list)


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
    child_questions: dict[str, PendingQuestion] = Field(default_factory=dict)
    upstream_inputs: list[BusinessInput] = Field(default_factory=list)
    task_inputs: dict[str, list[BusinessInput]] = Field(default_factory=dict)
    consumed_control_ids: list[str] = Field(default_factory=list)
    delivered_input_ids: list[str] = Field(default_factory=list)
    runtime_notices: list[dict] = Field(default_factory=list)
    parent_attention: bool = False
    manually_paused_at: datetime | None = None
    conclusion_requirements_ref: ArtifactRef | None = None
    dataset_catalog_ref: ArtifactRef | None = None
    initialization: RunInitialization = Field(default_factory=RunInitialization)
    pending_registrations: dict[str, PendingRegistration] = Field(default_factory=dict)
    pending_module_results: dict[str, PendingModuleResult] = Field(default_factory=dict)
    pending_compilation: WorkflowProposal | WorkflowPatch | None = None
    received_controls: dict[str, str] = Field(default_factory=dict)
    scientific_artifact_ids: list[ArtifactId] = Field(default_factory=list)
    scientific_report: str = ""
    answers: list[RecordedAnswer] = Field(default_factory=list)
    workflow_history: list[Workflow] = Field(default_factory=list)
    scientific_session: SessionRef | None = None
    latest_scientific_assessment: ScientificAssessment | None = None
    work_requests: list[WorkRequest] = Field(default_factory=list)
    final_opinion: ScientificOpinion | None = None
    final_report_artifact_id: ArtifactId | None = None
    delivered_answer_ids: list[QuestionId] = Field(default_factory=list)
    usage: RunUsage = Field(default_factory=RunUsage)
    # Settled ask_user pauses only; the currently open pause is derived below.
    user_wait_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    completion_violations: list[CompletionViolation] = Field(default_factory=list)
    terminal_error: ModuleError | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def llm_calls_used(self) -> int:
        return self.usage.used

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
