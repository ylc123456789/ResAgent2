"""Adapter for the old ExpAgent module (scientific analysis).

DELETION CONDITION: delete once Phase 7 ``Scientific Agent vNext`` replaces it.
This adapter only translates request/result shapes; it must not copy ExpAgent's
scientific reasoning or planning logic.
"""

from __future__ import annotations

from resagent2_contracts import (
    ModuleResult,
    ModuleStatus,
    ModuleTaskRequest,
    QuestionDraft,
)


class LegacyScientificAnalyzeAdapter:
    """Map ModuleTaskRequest <-> ExpAgent AdvisorContext / ScientificDecision."""

    def invoke(self, request: ModuleTaskRequest) -> ModuleResult:
        """Run the old ExpAgent.

        Deferred to Phase 4 step 6: lazy-import ``experiment_designer.agent.advise``,
        call it with ``to_spec``, and feed ``from_result`` the returned decision.
        """

        raise RuntimeError(
            "LegacyScientificAnalyzeAdapter is not wired to a real ExpAgent module; "
            "wire it in DEVELOPMENT_PLAN Phase 4 step 6."
        )

    @staticmethod
    def to_spec(request: ModuleTaskRequest) -> dict:
        """Translate a request into the fields of ExpAgent's AdvisorContext."""
        return {
            "situation": request.goal,
            "artifacts": [artifact.model_dump(mode="json") for artifact in request.input_artifacts],
        }

    @staticmethod
    def from_result(raw: dict) -> ModuleResult:
        """Map a ScientificDecision-shaped dict into a ModuleResult."""
        summary = raw.get("summary") or "analysis complete"
        needs = raw.get("needs_user_input") or []
        if needs:
            return ModuleResult(
                status=ModuleStatus.NEEDS_USER_INPUT,
                summary=summary,
                question=QuestionDraft(
                    text=needs[0],
                    reason="scientific analysis requires user input",
                ),
            )
        return ModuleResult(
            status=ModuleStatus.COMPLETED,
            summary=summary,
            payload={
                "conclusion": raw.get("conclusion"),
                "confidence": raw.get("confidence"),
            },
        )
