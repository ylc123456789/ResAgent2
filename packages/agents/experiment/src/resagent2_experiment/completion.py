"""Deterministic experiment completion and delivery validation."""

from __future__ import annotations

import hashlib
from pathlib import Path

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


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_workspace(boundary: WorkspaceBoundary) -> dict[str, str]:
    """Hash every readable workspace file to form an Attempt baseline."""
    snapshot: dict[str, str] = {}
    for relative in boundary.iter_files():
        try:
            resolved = boundary.resolve_read_file(relative)
        except (OSError, PermissionError):
            continue
        snapshot[relative] = _sha256_file(resolved)
    return snapshot


class ExperimentCompletionCheck:
    """Finalize an experiment, requiring a successful command and fresh evidence."""

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

    def _is_fresh_evidence(self, path: str, baseline: dict[str, str]) -> bool:
        """Return whether a file exists and differs from the Attempt baseline."""
        try:
            resolved = self.boundary.resolve_read_file(path)
        except (OSError, PermissionError):
            return False
        return _sha256_file(resolved) != baseline.get(path)

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

        if int(state.memory.get("experiment_success_count", 0)) < 1:
            return CompletionDecision(
                complete=False,
                summary="Run at least one successful experiment command before finishing",
            )

        baseline = state.memory.get("workspace_baseline", {})

        evidence: list[str] = []
        for path in finish.evidence_files:
            if self._is_fresh_evidence(path, baseline) and path not in evidence:
                evidence.append(path)

        issues = [
            f"Missing required metric: {name}"
            for name in self.expected_metrics
            if not _metric_is_present(name, finish.metrics)
        ]
        for name in self.expected_artifacts:
            if self._is_fresh_evidence(name, baseline):
                if name not in evidence:
                    evidence.append(name)
                continue
            try:
                self.boundary.resolve_read_file(name)
                issues.append(f"Required artifact {name} is unchanged from this attempt")
            except (OSError, PermissionError):
                issues.append(f"Missing required artifact: {name}")

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
