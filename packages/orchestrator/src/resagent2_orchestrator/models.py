"""Persisted orchestration state for one research run."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from resagent2_contracts import (
    ArtifactId,
    ArtifactRef,
    PendingQuestion,
    QuestionId,
    ResearchRequest,
    RunId,
    RunStatus,
    UserAnswer,
    Workflow,
    TaskId,
)


class OrchestratorModel(BaseModel):
    """Base for strict orchestrator-owned state."""

    model_config = ConfigDict(extra="forbid")


class ResearchRun(OrchestratorModel):
    """Complete persisted state owned by the Research Orchestrator."""

    run_id: RunId
    request: ResearchRequest
    status: RunStatus
    workflow: Workflow
    artifacts: dict[ArtifactId, ArtifactRef] = Field(default_factory=dict)
    pending_question: PendingQuestion | None = None
    answers: list[UserAnswer] = Field(default_factory=list)
    answer_task_ids: dict[QuestionId, TaskId | None] = Field(default_factory=dict)
    workflow_history: list[Workflow] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
