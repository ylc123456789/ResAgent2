"""Adapter for the old CodingAgent module.

DELETION CONDITION: delete once Phase 5 ``Coding Agent vNext`` replaces it with a
native Agent that reuses the shared runtime. This adapter only translates the
public request/result shapes; it must not copy CodingAgent's edit/verify logic.
"""

from __future__ import annotations

from resagent2_contracts import (
    ErrorCode,
    ModuleError,
    ModuleResult,
    ModuleStatus,
    ModuleTaskRequest,
    QuestionDraft,
)

_STATUS = {
    "completed": ModuleStatus.COMPLETED,
    "failed": ModuleStatus.FAILED,
    "blocked": ModuleStatus.BLOCKED,
    "needs_user_input": ModuleStatus.NEEDS_USER_INPUT,
}


class LegacyCodingAdapter:
    """Map ModuleTaskRequest <-> CodingAgent CodeTaskSpec / PatchReport."""

    def invoke(self, request: ModuleTaskRequest) -> ModuleResult:
        """Run the old CodingAgent.

        Deferred to Phase 4 step 6: lazy-import ``coding_agent.run_code_task``
        (with ``<CodingAgent>/src`` on ``sys.path``), call it with ``to_spec``,
        and feed ``from_result`` the returned ``PatchReport``.
        """

        raise RuntimeError(
            "LegacyCodingAdapter is not wired to a real CodingAgent module; "
            "wire it in DEVELOPMENT_PLAN Phase 4 step 6."
        )

    @staticmethod
    def to_spec(request: ModuleTaskRequest) -> dict:
        """Translate a request into the fields of CodingAgent's CodeTaskSpec."""
        inputs = request.inputs  # CodeModifyInput
        return {
            "task_goal": request.goal,
            "workspace_path": request.workspace.root if request.workspace else None,
            "allowed_paths": list(inputs.allowed_paths),
            "verify_commands": list(inputs.verification_commands),
            "output_dir": None,  # assigned by invoke during step 6
        }

    @staticmethod
    def from_result(raw: dict) -> ModuleResult:
        """Map a PatchReport-shaped dict into a ModuleResult."""
        status = raw["status"]
        summary = raw.get("summary") or status
        if status == "completed":
            return ModuleResult(
                status=ModuleStatus.COMPLETED,
                summary=summary,
                payload={"changed_files": raw.get("changed_files", [])},
            )
        if status == "blocked":
            return ModuleResult(
                status=ModuleStatus.BLOCKED,
                summary=summary,
                error=ModuleError(
                    code=ErrorCode.ENVIRONMENT_UNAVAILABLE,
                    message=raw.get("message", summary),
                    retryable=False,
                ),
            )
        if status == "needs_user_input":
            return ModuleResult(
                status=ModuleStatus.NEEDS_USER_INPUT,
                summary=summary,
                question=QuestionDraft(
                    text=summary,
                    reason="legacy coding module requested user input",
                ),
            )
        return ModuleResult(
            status=ModuleStatus.FAILED,
            summary=summary,
            error=ModuleError(
                code=ErrorCode.TOOL_FAILED,
                message=raw.get("message", summary),
                retryable=True,
            ),
        )
