"""Experiment completion validates execution facts, not inferred requirements."""

from __future__ import annotations

from resagent2_contracts import (
    SYSTEM_GENERATED_ARTIFACT_KINDS,
)
from resagent2_components import WorkspaceBoundary, execution_record
from resagent2_components.artifacts import ArtifactCandidateError, check_task_output_artifacts
from resagent2_runtime import AgentState, CompletionDecision, FinishCandidate


class ExperimentCompletionCheck:
    def __init__(self, boundary: WorkspaceBoundary, *, output_dir: str | None = None) -> None:
        self.boundary = boundary
        self.output_dir = output_dir

    def evaluate(self, state: AgentState, candidate: FinishCandidate | None) -> CompletionDecision:
        if candidate is None:
            return CompletionDecision(complete=False)
        if any(item.kind in SYSTEM_GENERATED_ARTIFACT_KINDS for item in candidate.artifacts):
            return CompletionDecision(
                complete=False, report="Execution records are generated from actual command observations",
            )
        artifacts = list(candidate.artifacts)
        record = execution_record(state)
        if record is not None:
            artifacts.insert(0, record)
        try:
            check_task_output_artifacts(
                artifacts, grant=self.boundary.grant, output_dir=self.output_dir,
            )
        except ArtifactCandidateError as error:
            return CompletionDecision(complete=False, report=f"{error.code}: {error}")
        return CompletionDecision(
            complete=True, report=candidate.report, artifacts=artifacts,
        )
