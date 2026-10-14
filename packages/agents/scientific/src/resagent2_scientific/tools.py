"""Scientific control tools; the runtime persists stable request identities."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel

from resagent2_contracts import QuestionDraft
from resagent2_components import RegisteredArtifactReader
from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.tools import AskUserTool as RuntimeAskUserTool, FinishTool

from .completion import cited_artifacts
from .models import AskUserInput, RequestWorkInput


def _citation_error(reader, state: AgentState, cited_ids: list[str]) -> ToolObservation | None:
    try:
        cited_artifacts(cited_ids, run_id=state.run_id, reader=reader)
    except (ValueError, OSError) as error:
        return ToolObservation(summary=f"Invalid evidence citation: {error}", ok=False)
    return None


class RequestWorkTool:
    """Request the next necessary round of implementation or experiment work."""

    model_guidance = (
        "assessment contains statement, evidence_artifact_ids, limitations, and "
        "unresolved_questions. Cite registered evidence IDs. work_request contains "
        "objective, shared constraints, and explicit tasks. Each task has a local key, "
        "natural instruction, optional depends_on local keys, input_artifact_ids, "
        "input_artifact_bindings, output_names, and logical workspace_id. Keep tasks "
        "unambiguous and self-contained. Preserve explicit requirements and declared "
        "output names. Do not select an Agent, invent runtime task IDs or physical "
        "paths, or prescribe unspecified implementation details. The Compiler only "
        "routes your existing tasks; it cannot rewrite, add, split, or repair them."
    )
    name = "request_work"
    input_model = RequestWorkInput

    def __init__(self, *, allowed: bool = True, reader: RegisteredArtifactReader | None = None) -> None:
        self.allowed = allowed
        self.reader = reader

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        if not self.allowed:
            raise PermissionError("Requesting execution work is not authorized")
        args = cast(RequestWorkInput, arguments)
        error = _citation_error(self.reader, state, args.assessment.evidence_artifact_ids)
        if error is not None:
            return error
        try:
            cited_artifacts(
                [artifact_id for task in args.work_request.tasks for artifact_id in task.input_artifact_ids],
                run_id=state.run_id, reader=self.reader,
            )
        except (ValueError, OSError) as error:
            return ToolObservation(summary=f"Invalid work input artifacts: {error}", ok=False)
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
        "unresolved_questions; cite registered evidence IDs."
    )
    name = "ask_user"
    input_model = AskUserInput

    def __init__(self, reader: RegisteredArtifactReader | None = None) -> None:
        self.reader = reader

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(AskUserInput, arguments)
        error = _citation_error(self.reader, state, args.assessment.evidence_artifact_ids)
        if error is not None:
            return error
        return ToolObservation(
            summary=args.assessment.statement,
            question=QuestionDraft(text=args.text, requested_fields=args.requested_fields, options=args.options, related_question_id=args.related_question_id),
            memory_updates={"latest_assessment": args.assessment.model_dump(mode="json")},
        )
