"""Experiment completion validates execution facts, not inferred requirements."""

from __future__ import annotations

import json

from resagent2_contracts import (
    ArtifactCandidate, VerificationResult, SYSTEM_GENERATED_ARTIFACT_KINDS,
)
from resagent2_components import WorkspaceBoundary
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
        records = self._execution_records(state)
        if records:
            artifacts.insert(0, ArtifactCandidate(
                kind="execution_record", path="execution_record.json",
                media_type="application/json", summary="Recorded experiment command outcomes",
                content=json.dumps({"results": records}),
            ))
        try:
            check_task_output_artifacts(
                artifacts, grant=self.boundary.grant, output_dir=self.output_dir,
            )
        except ArtifactCandidateError as error:
            return CompletionDecision(complete=False, report=f"{error.code}: {error}")
        return CompletionDecision(
            complete=True, report=candidate.report, artifacts=artifacts,
        )

    @staticmethod
    def _execution_records(state: AgentState) -> list[dict]:
        records = []
        for event in state.events:
            if event.type != "observation" or event.tool != "run_command" or not isinstance(event.data, dict):
                continue
            value = event.data.get("value")
            if not isinstance(value, dict) or "exit_code" not in value:
                continue
            record = {key: value[key] for key in VerificationResult.model_fields if key in value}
            try:
                records.append(VerificationResult.model_validate(record).model_dump(mode="json"))
            except ValueError:
                continue
        return records
