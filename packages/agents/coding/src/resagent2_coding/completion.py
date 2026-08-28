"""Deterministic completion checks for both Coding profiles."""

from __future__ import annotations

import hashlib

from pydantic import ValidationError

from resagent2_contracts import (
    ArtifactCandidate,
    CodeModifyResult,
    CodeUnderstandResult,
    VerificationResult,
)
from resagent2_capabilities import (
    GitWorkspace,
    WorkspaceBoundary,
    media_type_for,
)
from resagent2_runtime import (
    AgentState,
    CompletionDecision,
    FinishCandidate,
)

from .models import CodeModifyFinish


class CodeUnderstandCompletionCheck:
    def __init__(self, repository: GitWorkspace) -> None:
        self.repository = repository

    def evaluate(
        self,
        state: AgentState,
        candidate: FinishCandidate | None,
    ) -> CompletionDecision:
        if candidate is None:
            return CompletionDecision(complete=False)
        try:
            payload = CodeUnderstandResult.model_validate(candidate.result)
        except ValidationError as error:
            return CompletionDecision(
                complete=False,
                summary=f"Read-only result is invalid: {error.errors()[0]['msg']}",
            )
        observed = set(state.memory.get("read_paths", []))
        missing = [path for path in payload.evidence_files if path not in observed]
        if missing:
            return CompletionDecision(
                complete=False,
                summary="Read the claimed evidence files before finishing: "
                + ", ".join(missing),
            )
        if self.repository.changed_paths():
            return CompletionDecision(
                complete=False,
                summary="Read-only Coding profile detected workspace changes",
            )
        return CompletionDecision(
            complete=True,
            summary="Code understanding completed with observed file evidence",
            payload=payload.model_dump(mode="json"),
        )


class CodeModifyCompletionCheck:
    def __init__(
        self,
        repository: GitWorkspace,
        boundary: WorkspaceBoundary,
        verification_commands: list[str],
        *,
        output_root: str,
    ) -> None:
        self.repository = repository
        self.boundary = boundary
        self.verification_commands = verification_commands
        self.output_root = output_root

    def evaluate(
        self,
        state: AgentState,
        candidate: FinishCandidate | None,
    ) -> CompletionDecision:
        if candidate is None:
            return CompletionDecision(complete=False)
        try:
            finish = CodeModifyFinish.model_validate(candidate.result)
        except ValidationError as error:
            return CompletionDecision(
                complete=False,
                summary=f"Finish result is invalid: {error.errors()[0]['msg']}",
            )

        changed = self.repository.changed_paths()
        if not changed:
            return CompletionDecision(
                complete=False,
                summary="No Git workspace change was produced",
            )
        deleted = self.repository.deleted_paths()
        existing = [path for path in changed if path not in deleted]
        for path in changed:
            self.boundary.resolve_write_file(path)

        raw_results = state.memory.get("verification_results", [])
        try:
            results = [VerificationResult.model_validate(item) for item in raw_results]
        except ValidationError:
            return CompletionDecision(
                complete=False,
                summary="Stored verification results are invalid",
            )
        edit_revision = int(state.memory.get("edit_revision", 0))
        verification_revision = state.memory.get("verification_revision")
        if self.verification_commands:
            if verification_revision != edit_revision:
                return CompletionDecision(
                    complete=False,
                    summary="Run verification after the latest file edit",
                )
            if [item.command for item in results] != self.verification_commands:
                return CompletionDecision(
                    complete=False,
                    summary="Verification did not run the complete declared command set",
                )
            current_digest = hashlib.sha256(
                self.repository.diff().encode("utf-8")
            ).hexdigest()
            if (
                not state.memory.get("verification_workspace_unchanged", False)
                or state.memory.get("verification_diff_sha256") != current_digest
            ):
                return CompletionDecision(
                    complete=False,
                    summary=(
                        "Workspace changed during or after verification; "
                        "review the diff and rerun verification"
                    ),
                )
            if any(item.exit_code != 0 or item.timed_out for item in results):
                return CompletionDecision(
                    complete=False,
                    summary="Verification failed; inspect the latest command observation",
                )
        else:
            results = []

        patch_path = self.repository.write_patch(f"{self.output_root}/changes.patch")
        payload = CodeModifyResult(
            changed_files=existing,
            deleted_files=deleted,
            patch_path=patch_path,
            verification_results=results,
            verification_passed=True,
            residual_risks=finish.residual_risks,
        )
        artifacts = [
            ArtifactCandidate(
                kind="code_patch",
                path=patch_path,
                media_type="text/x-diff",
                summary="Complete Git patch produced by Coding Agent",
            ),
            *[
                ArtifactCandidate(
                    kind="code_change",
                    path=path,
                    media_type=media_type_for(path),
                    summary=f"Changed code file: {path}",
                )
                for path in existing
            ],
        ]
        return CompletionDecision(
            complete=True,
            summary=finish.summary,
            payload=payload.model_dump(mode="json"),
            artifacts=artifacts,
        )
