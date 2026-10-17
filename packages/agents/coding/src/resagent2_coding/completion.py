"""Derive Coding artifacts from actual changes and execution records."""

from __future__ import annotations

from resagent2_contracts import ArtifactCandidate, SYSTEM_GENERATED_ARTIFACT_KINDS
from resagent2_components import (
    GitBaseline, GitWorkspace, WorkspaceBoundary, execution_record, media_type_for,
)
from resagent2_components.artifacts import ArtifactCandidateError, check_task_output_artifacts
from resagent2_runtime import AgentState, CompletionDecision, FinishCandidate


class CodingCompletionCheck:
    def __init__(
        self, repository: GitWorkspace, boundary: WorkspaceBoundary, *,
        baseline: GitBaseline,
        output_dir: str | None = None,
    ) -> None:
        self.repository = repository
        self.boundary = boundary
        self.baseline = baseline
        self.output_dir = output_dir

    def evaluate(self, state: AgentState, candidate: FinishCandidate | None) -> CompletionDecision:
        if candidate is None:
            return CompletionDecision(complete=False)
        changed = self.repository.changed_paths_since(self.baseline)
        for path in changed:
            self.boundary.resolve_write_file(path)
        artifacts = list(candidate.artifacts)
        reserved = SYSTEM_GENERATED_ARTIFACT_KINDS
        if any(item.kind in reserved for item in artifacts):
            return CompletionDecision(
                complete=False,
                report="Patches and execution records are generated from actual changes and command observations",
            )
        record = execution_record(state)
        if record is not None:
            artifacts.insert(0, record)
        if changed:
            deleted = self.repository.deleted_paths_since(self.baseline)
            artifacts.append(ArtifactCandidate(
                kind="code_patch", path="changes.patch", media_type="text/x-diff",
                summary="Git patch from this Coding attempt",
                content=self.repository.diff_since(self.baseline),
            ))
            submitted_paths = {item.path for item in artifacts if isinstance(item, ArtifactCandidate)}
            artifacts.extend(
                ArtifactCandidate(
                    kind="code_change", path=path, media_type=media_type_for(path),
                    summary=f"Changed code file: {path}",
                )
                for path in changed if path not in deleted and path not in submitted_paths
            )
        try:
            check_task_output_artifacts(
                artifacts, grant=self.boundary.grant, output_dir=self.output_dir,
            )
        except ArtifactCandidateError as error:
            return CompletionDecision(complete=False, report=f"{error.code}: {error}")
        return CompletionDecision(complete=True, report=candidate.report, artifacts=artifacts)
