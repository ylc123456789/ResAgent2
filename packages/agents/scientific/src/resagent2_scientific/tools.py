"""Scientific-only Tools that produce control signals."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel

from resagent2_contracts import QuestionDraft
from resagent2_runtime import AgentState, FinishCandidate, ToolObservation

from .models import AskUserInput, RequestWorkInput, ScientificFinish


class FinishTool:
    """Create a Scientific finish candidate without deciding completion."""

    name = "finish"
    input_model = ScientificFinish

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(ScientificFinish, arguments)
        return ToolObservation(
            summary="Produced a scientific finish candidate",
            finish_candidate=FinishCandidate(
                proposed_status="completed",
                result=args.model_dump(mode="json"),
            ),
        )


class RequestWorkTool:
    """Validate a request for more work and pause the Session for execution.

    The assessment and work request draft are schema-validated by the input
    model; the loop pauses the session and the ScientificPort turns the
    ``request_work`` signal into a ScientificWorkRequestResult.
    """

    name = "request_work"
    input_model = RequestWorkInput

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(RequestWorkInput, arguments)
        if not args.work_request.expected_evidence:
            raise ValueError("request_work requires at least one expected_evidence")
        return ToolObservation(
            summary="Requesting more execution work",
            request_work={
                "assessment": args.assessment.model_dump(mode="json"),
                "work_request": args.work_request.model_dump(mode="json"),
            },
            memory_updates={
                "latest_assessment": args.assessment.model_dump(mode="json"),
            },
        )


class AskUserTool:
    """Ask the user while carrying the current scientific assessment."""

    name = "ask_user"
    input_model = AskUserInput

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(AskUserInput, arguments)
        return ToolObservation(
            summary="User input is required",
            question=QuestionDraft(
                text=args.text,
                requested_fields=args.requested_fields,
                reason=args.reason,
            ),
            memory_updates={
                "latest_assessment": args.assessment.model_dump(mode="json"),
            },
        )
