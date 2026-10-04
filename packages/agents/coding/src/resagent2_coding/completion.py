"""Derive Coding artifacts from actual changes and execution records."""

from __future__ import annotations

import hashlib
import json

from pydantic import ValidationError

from resagent2_contracts import ArtifactCandidate, VerificationResult, SYSTEM_GENERATED_ARTIFACT_KINDS
from resagent2_components import EnvironmentBinding, GitBaseline, GitWorkspace, WorkspaceBoundary, media_type_for
from resagent2_components.artifacts import ArtifactCandidateError, check_task_output_artifacts
from resagent2_runtime import AgentState, CompletionDecision, FinishCandidate


class CodingCompletionCheck:
    def __init__(
        self, repository: GitWorkspace, boundary: WorkspaceBoundary, *,
        baseline: GitBaseline, env_binding: EnvironmentBinding | None = None,
        output_dir: str | None = None,
    ) -> None:
        self.repository = repository
        self.boundary = boundary
        self.baseline = baseline
        self.env_binding = env_binding
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
                report="Patches and verification records are generated from actual execution",
            )
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
        results, issue = _verification_status(state, self.env_binding)
        if results:
            fresh = issue is None and state.memory.get("verification_diff_sha256") == hashlib.sha256(
                self.repository.diff_since(self.baseline).encode("utf-8")
            ).hexdigest()
            unrecorded = state.memory.get("verification_unrecorded_commands", [])
            artifacts.append(ArtifactCandidate(
                kind="verification_result", path="verification_result.json",
                media_type="application/json",
                summary="Recorded verification commands and freshness",
                content=json.dumps({
                    "results": [item.model_dump(mode="json") for item in results],
                    "covers_current_workspace": fresh,
                    "passed": not unrecorded and all(item.exit_code == 0 and not item.timed_out for item in results),
                    "unrecorded_commands": unrecorded,
                    "issue": issue if issue else (None if fresh else "Workspace changed after verification"),
                }),
            ))
        try:
            check_task_output_artifacts(
                artifacts, grant=self.boundary.grant, output_dir=self.output_dir,
            )
        except ArtifactCandidateError as error:
            return CompletionDecision(complete=False, report=f"{error.code}: {error}")
        return CompletionDecision(complete=True, report=candidate.report, artifacts=artifacts)


def _verification_status(
    state: AgentState, binding: EnvironmentBinding | None,
) -> tuple[list[VerificationResult], str | None]:
    """Return recorded outcomes and any freshness issue, independently of pass/fail."""
    try:
        results = [VerificationResult.model_validate(item)
                   for item in state.memory.get("verification_results", [])]
    except (ValidationError, TypeError):
        return [], "Stored verification results are invalid"
    if not results:
        return results, "No verification executed"
    if state.memory.get("verification_revision") != int(state.memory.get("edit_revision", 0)):
        return results, "Verification records belong to a different edit revision"
    if binding is not None and not binding.certified:
        return results, "The current environment binding is not certified"
    if binding is not None and state.memory.get("verification_environment_generation") != binding.generation:
        return results, "The environment binding changed or was restored after verification"
    if state.memory.get("verification_unrecorded_commands"):
        return results, "Some requested verification commands have no recorded outcome: " + str(
            state.memory.get("verification_execution_error") or "Verification was interrupted"
        )
    if state.memory.get("verification_workspace_error"):
        return results, "Verification freshness could not be confirmed: " + str(
            state.memory["verification_workspace_error"]
        )
    if not state.memory.get("verification_workspace_unchanged", False):
        return results, "Workspace changed during verification"
    return results, None


def derive_control_state(state: AgentState, binding: EnvironmentBinding | None) -> dict:
    """Expose execution facts without imposing a business mode or mandatory edit."""
    results, issue = _verification_status(state, binding)
    passed = (not state.memory.get("verification_unrecorded_commands")
              and all(item.exit_code == 0 and not item.timed_out for item in results)) if results else None
    return {
        "edit_revision": int(state.memory.get("edit_revision", 0)),
        "verification_revision": state.memory.get("verification_revision"),
        "verification_issue": issue,
        "verification_passed": passed,
        "environment_certified": bool(binding and binding.certified),
        "edited_since_verification": int(state.memory.get("edit_revision", 0)) > int(state.memory.get("verification_revision") or 0),
        "verification_stale": bool(results) and issue is not None,
    }
