"""Deterministic experiment completion and delivery validation."""

from __future__ import annotations

import json

from pydantic import ValidationError

from resagent2_contracts import (
    ArtifactCandidate,
    ErrorCode,
    ExperimentResult,
    ModuleError,
    WarningRecord,
)
from resagent2_capabilities import (
    WorkspaceObserver,
    WorkspaceSnapshot,
    media_type_for,
)
from resagent2_runtime import (
    AgentState,
    CompletionDecision,
    FinishCandidate,
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
    """Finalize an experiment, requiring a successful command and fresh evidence."""

    def __init__(
        self,
        observer: WorkspaceObserver,
        *,
        expected_metrics: list[str],
        expected_artifacts: list[str],
        env_id: str,
        repo_url: str,
        commit: str,
    ) -> None:
        self.observer = observer
        self.boundary = observer.boundary
        self.expected_metrics = expected_metrics
        self.expected_artifacts = expected_artifacts
        self.env_id = env_id
        self.repo_url = repo_url
        self.commit = commit

    def _resolve_path(self, path: str) -> str | None:
        """Return the normalized relative path for a readable file, else None."""
        try:
            resolved = self.boundary.resolve_read_file(path)
        except (OSError, PermissionError):
            return None
        return self.boundary.relative(resolved)

    def _metrics_from_evidence(self, evidence: list[str]) -> dict:
        """Read top-level numeric fields from the Agent's JSON evidence files.

        The typed ``metrics`` in the payload come only from evidence the Agent
        actually produced; the LLM cannot self-certify a number (ADR-0011 §5.2).
        """
        metrics: dict = {}
        for path in evidence:
            if not path.lower().endswith(".json"):
                continue
            try:
                resolved = self.boundary.resolve_read_file(path)
                data = json.loads(resolved.read_text(encoding="utf-8"))
            except (OSError, PermissionError, ValueError, UnicodeDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            for key, value in data.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    metrics[_metric_key(key)] = value
        return metrics

    @staticmethod
    def _workspace_snapshot(state: AgentState) -> WorkspaceSnapshot | None:
        raw = state.memory.get("workspace_snapshot")
        if raw is None:
            return None
        try:
            return WorkspaceSnapshot.from_memory(raw)
        except ValueError:
            return None

    def evaluate(
        self,
        state: AgentState,
        candidate: FinishCandidate | None,
    ) -> CompletionDecision:
        if candidate is None:
            return CompletionDecision(complete=False)
        if candidate.proposed_status == "failed":
            return self._evaluate_failure(state)
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

        snapshot = self._workspace_snapshot(state)
        if snapshot is None:
            return CompletionDecision(
                complete=False,
                summary="Workspace baseline is missing; cannot verify evidence ownership",
            )
        changed = set(self.observer.changed_paths(snapshot))

        evidence: list[str] = []
        for path in finish.evidence_files:
            normalized = self._resolve_path(path)
            if normalized is None:
                continue
            if normalized in changed and normalized not in evidence:
                evidence.append(normalized)

        # Fix the complete evidence set before deriving metrics: a required
        # artifact the Agent produced but did not list is still evidence, and
        # its JSON must feed the typed metrics (ADR-0011 §5.2).
        artifact_issues: list[str] = []
        for name in self.expected_artifacts:
            normalized = self._resolve_path(name)
            if normalized is None:
                artifact_issues.append(f"Missing required artifact: {name}")
                continue
            if normalized not in changed:
                artifact_issues.append(
                    f"Required artifact {normalized} is unchanged from this attempt"
                )
                continue
            if normalized not in evidence:
                evidence.append(normalized)

        metrics = self._metrics_from_evidence(evidence)
        issues = [
            f"Missing required metric: {name}"
            for name in self.expected_metrics
            if not _metric_is_present(name, metrics)
        ]
        issues.extend(artifact_issues)

        # A run that delivered none of the explicitly required evidence did not
        # actually produce results; reject it instead of completing with
        # warnings that could mask a total failure.
        metrics_delivered = any(
            _metric_is_present(name, metrics) for name in self.expected_metrics
        )
        if (
            (self.expected_metrics or self.expected_artifacts)
            and not metrics_delivered
            and not evidence
        ):
            return CompletionDecision(
                complete=False,
                summary="No required metric or artifact was produced; rerun the experiment",
            )

        payload = ExperimentResult(
            metrics=metrics,
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

    def _evaluate_failure(self, state: AgentState) -> CompletionDecision:
        """Accept a proposed ``failed`` finish only with verified command evidence.

        The LLM may propose ``proposed_status="failed"``, but it cannot
        self-declare failure: the deterministic finalizer only accepts it when a
        real experiment command was observed to fail (non-zero exit or timeout)
        with persistent stdout/stderr logs. Otherwise the proposal is rejected
        like any other unverified finish.
        """
        evidence = self._last_failed_command(state)
        if evidence is None:
            return CompletionDecision(
                complete=False,
                summary=(
                    "Proposed 'failed' status but no failed experiment command "
                    "was observed; rerun the command or report its real error"
                ),
            )
        if evidence["timed_out"]:
            message = "Experiment command timed out"
        else:
            message = (
                f"Experiment command failed with exit code {evidence['exit_code']}"
            )
        return CompletionDecision(
            complete=False,
            failure=ModuleError(
                code=ErrorCode.TOOL_FAILED,
                message=message,
                retryable=False,
                details=evidence,
            ),
        )

    @staticmethod
    def _last_failed_command(state: AgentState) -> dict | None:
        """Find the most recent ``run_command`` observation that actually failed.

        Returns the structured evidence (command, exit code, log paths and a
        bounded stderr tail) for the failure exit, or None when no failed
        experiment command was observed this session.
        """
        for event in reversed(state.events):
            if event.type != "observation" or event.tool != "run_command":
                continue
            data = event.data if isinstance(event.data, dict) else {}
            if data.get("ok", True):
                continue
            value = data.get("value")
            if not isinstance(value, dict):
                continue
            exit_code = value.get("exit_code")
            timed_out = bool(value.get("timed_out", False))
            if exit_code is None or (exit_code == 0 and not timed_out):
                continue
            if not value.get("stdout_path") and not value.get("stderr_path"):
                continue
            return {
                "command": value.get("command") or "",
                "exit_code": exit_code,
                "timed_out": timed_out,
                "stdout_path": value.get("stdout_path") or "",
                "stderr_path": value.get("stderr_path") or "",
                "stderr_tail": value.get("stderr_tail") or "",
            }
        return None
