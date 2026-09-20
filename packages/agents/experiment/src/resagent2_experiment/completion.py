"""Experiment completion validates execution facts, not inferred requirements."""

from __future__ import annotations

import json
from pathlib import Path

from resagent2_contracts import ArtifactCandidate, ErrorCode, ModuleError, VerificationResult, SYSTEM_GENERATED_ARTIFACT_KINDS
from resagent2_components import WorkspaceObserver
from resagent2_runtime import AgentState, CompletionDecision, FinishCandidate


class ExperimentCompletionCheck:
    def __init__(self, observer: WorkspaceObserver, *, output_dir: str | None = None) -> None:
        self.observer = observer
        self.output_dir = Path(output_dir).resolve() if output_dir is not None else None

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
            artifacts.append(ArtifactCandidate(
                kind="execution_record", path="execution_record.json",
                media_type="application/json", summary="Recorded experiment command outcomes",
                content=json.dumps({"results": records}),
            ))
        evidence = self._last_failed_command(state)
        if evidence is not None:
            message = ("Experiment command timed out" if evidence["timed_out"]
                       else f"Experiment command failed with exit code {evidence['exit_code']}")
            return CompletionDecision(
                complete=False, report=candidate.report, artifacts=artifacts,
                failure=ModuleError(
                    code=ErrorCode.TOOL_FAILED, message=message, retryable=False, details=evidence,
                ),
            )
        for item in candidate.artifacts:
            if getattr(item, "content", None) is None and hasattr(item, "path"):
                workspace_file = self.observer.boundary.root / item.path
                output_file = (self.output_dir / item.path).resolve() if self.output_dir else None
                if output_file is not None and output_file.is_file():
                    if not output_file.is_relative_to(self.output_dir) or workspace_file.exists():
                        raise PermissionError("Output artifact is outside its root or ambiguous")
                else:
                    self.observer.boundary.resolve_read_file(item.path)
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

    @staticmethod
    def _last_failed_command(state: AgentState) -> dict | None:
        for event in reversed(state.events):
            if event.type != "observation" or event.tool != "run_command":
                continue
            if not isinstance(event.data, dict):
                continue
            value = event.data.get("value")
            if not isinstance(value, dict):
                continue
            exit_code = value.get("exit_code")
            timed_out = bool(value.get("timed_out", False))
            if exit_code is None:
                continue
            if exit_code == 0 and not timed_out:
                return None
            if not value.get("stdout_path") and not value.get("stderr_path"):
                continue
            return {
                "command": value.get("command") or "", "exit_code": exit_code,
                "timed_out": timed_out, "stdout_path": value.get("stdout_path") or "",
                "stderr_path": value.get("stderr_path") or "",
                "stderr_tail": value.get("stderr_tail") or "",
            }
        return None
