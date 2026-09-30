"""Scientific control tools; the runtime packages their content as artifacts."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel

from resagent2_contracts import QuestionDraft
from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.tools import AskUserTool as RuntimeAskUserTool, FinishTool

from .completion import _observed_artifact_ids, unobserved_artifact_ids
from .models import AskUserInput, RequestWorkInput


def _unobserved_evidence(state: AgentState, cited_ids: list[str]) -> list[str]:
    return unobserved_artifact_ids(cited_ids, _observed_artifact_ids(state))


class RequestWorkTool:
    """Request the next necessary round of implementation or experiment work."""

    model_guidance = (
        "assessment contains statement, evidence_artifact_ids, limitations, and "
        "unresolved_questions. Cite only observed evidence IDs. work_request contains "
        "objective, expected_evidence, constraints, and input_artifact_ids. Describe "
        "the needed work and evidence semantically; do not invent task IDs, executable "
        "paths, or routing fields. Preserve explicit requirements and logical output "
        "names. Use input_artifact_ids to supply registered materials needed for the work."
    )
    name = "request_work"
    input_model = RequestWorkInput

    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        if not self.allowed:
            raise PermissionError("Requesting execution work is not authorized")
        args = cast(RequestWorkInput, arguments)
        unobserved = _unobserved_evidence(state, args.assessment.evidence_artifact_ids)
        if unobserved:
            return ToolObservation(
                summary="Read the cited evidence before requesting work: " + ", ".join(unobserved),
                ok=False, value={"unobserved_artifact_ids": unobserved},
            )
        return ToolObservation(
            summary=args.assessment.statement,
            request_work=args.model_dump(mode="json"),
            memory_updates={"latest_assessment": args.assessment.model_dump(mode="json")},
        )


class AskUserTool:
    """Ask for a research decision with the current scientific assessment."""

    model_guidance = (
        RuntimeAskUserTool.model_guidance
        + " assessment contains statement, evidence_artifact_ids, limitations, and "
        "unresolved_questions; cite only observed evidence IDs."
    )
    name = "ask_user"
    input_model = AskUserInput

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(AskUserInput, arguments)
        unobserved = _unobserved_evidence(state, args.assessment.evidence_artifact_ids)
        if unobserved:
            return ToolObservation(
                summary="Read the cited evidence before asking the user: " + ", ".join(unobserved),
                ok=False, value={"unobserved_artifact_ids": unobserved},
            )
        return ToolObservation(
            summary=args.assessment.statement,
            question=QuestionDraft(text=args.text, requested_fields=args.requested_fields, options=args.options),
            memory_updates={"latest_assessment": args.assessment.model_dump(mode="json")},
        )
