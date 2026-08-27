"""Deterministic experiment completion and delivery validation."""

from __future__ import annotations

from pydantic import ValidationError

from resagent2_contracts import (
    ArtifactCandidate,
    ExperimentResult,
    WarningRecord,
)
from resagent2_runtime import (
    AgentState,
    CompletionDecision,
    FinishCandidate,
    WorkspaceBoundary,
    media_type_for,
)

from .models import ExperimentFinish


def _metric_key(value: str) -> str:
    return "".join(char.lower() for char in str(value) if char.isalnum())


def _metric_is_present(expected: str, metrics: dict) -> bool:
    wanted = _metric_key(expected)
    if not wanted:
        return False
    for name in metrics:
        actual = _metric_key(name)
        if actual and (wanted == actual or wanted in actual or actual in wanted):
            return True
    return False


class ExperimentCompletionCheck:
    """Finalize an experiment and downgrade on missing declared deliverables."""

    def __init__(
        self,
        boundary: WorkspaceBoundary,
        *,
        expected_metrics: list[str],
        expected_artifacts: list[str],
        env_id: str,
        repo_url: str,
        commit: str,
    ) -> None:
        self.boundary = boundary
        self.expected_metrics = expected_metrics
        self.expected_artifacts = expected_artifacts
        self.env_id = env_id
        self.repo_url = repo_url
        self.commit = commit

    def evaluate(
        self,
        state: AgentState,
        candidate: FinishCandidate | None,
    ) -> CompletionDecision:
        if candidate is None:
            return CompletionDecision(complete=False)
        try:
            finish = ExperimentFinish.model_validate(candidate.result)
        except ValidationError as error:
            return CompletionDecision(
                complete=False,
                summary=f"Finish result is invalid: {error.errors()[0]['msg']}",
            )

        evidence: list[str] = []
        for path in finish.evidence_files:
            try:
                self.boundary.resolve_read_file(path)
            except (OSError, PermissionError):
                continue  # skip evidence the LLM claimed but did not produce
            if path not in evidence:
                evidence.append(path)

        issues = [
            f"Missing required metric: {name}"
            for name in self.expected_metrics
            if not _metric_is_present(name, finish.metrics)
        ]
        for name in self.expected_artifacts:
            try:
                self.boundary.resolve_read_file(name)
            except (OSError, PermissionError):
                issues.append(f"Missing required artifact: {name}")
            else:
                if name not in evidence:
                    evidence.append(name)

        payload = ExperimentResult(
            metrics=finish.metrics,
            parameters=finish.parameters,
            evidence_files=evidence,
            repo_url=self.repo_url,
            commit=self.commit,
            env_id=self.env_id,
            delivery_issues=issues,
            residual_risks=finish.residual_risks,
        )
        artifacts = [
            ArtifactCandidate(
                kind="experiment_result",
                path=path,
                media_type=media_type_for(path),
                summary=f"Experiment evidence: {path}",
            )
            for path in evidence
        ]
        if issues:
            return CompletionDecision(
                complete=True,
                summary=finish.summary,
                payload=payload.model_dump(mode="json"),
                artifacts=artifacts,
                warnings=[
                    WarningRecord(
                        code="delivery_not_met",
                        message="; ".join(f"[NOT MET] {issue}" for issue in issues),
                    )
                ],
            )
        return CompletionDecision(
            complete=True,
            summary=finish.summary,
            payload=payload.model_dump(mode="json"),
            artifacts=artifacts,
        )
