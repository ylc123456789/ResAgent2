"""Persisted orchestration state for one research run."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from resagent2_contracts import (
    ArtifactId,
    ArtifactRef,
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
    UNACKNOWLEDGED_TASK = "unacknowledged_task"
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
    status: RunStatus
    workflow: Workflow | None = None
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
    completion_violations: list[CompletionViolation] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
