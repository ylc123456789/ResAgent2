"""Adapter for the old reproagent module (experiment executor).

DELETION CONDITION: delete once Phase 6 ``Experiment Agent vNext`` replaces it.
This adapter only translates request/result shapes; it must not copy reproagent's
environment/process logic, and it must not hardcode conda/GPU details.
"""

from __future__ import annotations

from resagent2_contracts import (
    ErrorCode,
    ModuleError,
    ModuleResult,
    ModuleStatus,
    ModuleTaskRequest,
    WarningRecord,
)


class LegacyExperimentAdapter:
    """Map ModuleTaskRequest <-> reproagent ReproTask / AgentState."""

    def invoke(self, request: ModuleTaskRequest) -> ModuleResult:
        """Run the old reproagent.

        Deferred to Phase 4 step 6: lazy-import ``reproagent.run_task``, call it
        with ``to_spec``, and feed ``from_result`` the returned ``AgentState``.
        """

        raise RuntimeError(
            "LegacyExperimentAdapter is not wired to a real reproagent module; "
            "wire it in DEVELOPMENT_PLAN Phase 4 step 6."
        )

    @staticmethod
    def to_spec(request: ModuleTaskRequest) -> dict:
        """Translate a request into the fields of reproagent's ReproTask."""
        inputs = request.inputs  # ExperimentRunInput
        return {
            "workspace_dir": request.workspace.root if request.workspace else None,
            "experiment_goal": request.goal,
            "expected_metrics": list(inputs.expected_metrics),
            "expected_artifacts": list(inputs.expected_artifacts),
            "parent_run": {
                "module": "resagent",
                "run_id": request.run_id,
                "task_id": request.task_id,
            },
        }

    @staticmethod
    def from_result(raw: dict) -> ModuleResult:
        """Map an AgentState-shaped dict into a ModuleResult."""
        status = raw["status"]
        summary = raw.get("final_summary") or raw.get("summary") or status
        payload = {
            "metrics": raw.get("metrics"),
            "parameters": raw.get("parameters"),
        }
        if status == "completed":
            return ModuleResult(status=ModuleStatus.COMPLETED, summary=summary, payload=payload)
        if status == "completed_with_failures":
            return ModuleResult(
                status=ModuleStatus.COMPLETED_WITH_WARNINGS,
                summary=summary,
                payload=payload,
                warnings=[
                    WarningRecord(
                        code="unverified",
                        message=raw.get("warning", "delivery check reported missing items"),
                    )
                ],
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
        return ModuleResult(
            status=ModuleStatus.FAILED,
            summary=summary,
            error=ModuleError(
                code=ErrorCode.TOOL_FAILED,
                message=raw.get("message", summary),
                retryable=True,
            ),
        )
