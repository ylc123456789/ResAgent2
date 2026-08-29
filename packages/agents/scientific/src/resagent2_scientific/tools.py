"""Scientific-only Tools that produce control signals."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel

from resagent2_contracts import QuestionDraft
from resagent2_runtime import AgentState, FinishCandidate, ToolObservation

from .completion import _observed_artifact_ids
from .models import AskUserInput, RequestWorkInput, ScientificFinish


def _unobserved_evidence(state: AgentState, cited_ids: list[str]) -> list[str]:
    """Return the cited artifact ids that were not observed by any Tool."""
    observed = set(_observed_artifact_ids(state))
    return sorted(set(cited_ids) - observed)


class FinishTool:
    """Create a Scientific finish candidate, rejecting unread evidence first.

    RequestWorkTool/AskUserTool already refuse an assessment that cites unread
    artifacts; finish now applies the same rule at submit time, so the model is
    told immediately which artifacts it must read (or drop) instead of only
    discovering it at the deterministic completion gate.
    """

    name = "finish"
    input_model = ScientificFinish

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(ScientificFinish, arguments)
        unobserved = _unobserved_evidence(state, args.opinion.evidence_artifact_ids)
        if unobserved:
            return ToolObservation(
                summary=(
                    "Cannot finish yet: the opinion cites evidence artifacts not "
                    "observed by any Tool: " + ", ".join(unobserved)
                    + ". Call read_artifact first, or remove these ids if the "
                    "judgment does not rely on their contents."
                ),
                ok=False,
                value={"unobserved_artifact_ids": unobserved},
                memory_updates={"pending_citation_artifact_ids": unobserved},
            )
        return ToolObservation(
            summary="Produced a scientific finish candidate",
            finish_candidate=FinishCandidate(
                proposed_status="completed",
                result=args.model_dump(mode="json"),
            ),
            memory_updates={"pending_citation_artifact_ids": []},
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
        unobserved = _unobserved_evidence(
            state, args.assessment.evidence_artifact_ids
        )
        if unobserved:
            return ToolObservation(
                summary=(
                    "Cannot submit this WorkRequest yet. The following evidence "
                    "artifacts have not been observed: " + ", ".join(unobserved)
                    + ". Call read_artifact first, or remove these ids if the "
                    "assessment does not rely on their contents."
                ),
                ok=False,
                value={"unobserved_artifact_ids": unobserved},
            )
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
        unobserved = _unobserved_evidence(
            state, args.assessment.evidence_artifact_ids
        )
        if unobserved:
            return ToolObservation(
                summary=(
                    "Cannot ask the user yet: the assessment cites evidence not "
                    "observed by any Tool: " + ", ".join(unobserved)
                ),
                ok=False,
                value={"unobserved_artifact_ids": unobserved},
            )
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
