"""Scientific action, request-work, and finish-candidate schemas."""

from __future__ import annotations

from typing import Literal

from resagent2_contracts import (
    ScientificAssessment,
    ScientificOpinion,
    WorkRequestDraft,
)
from resagent2_runtime import AgentAction
from resagent2_runtime.models import RuntimeModel
from resagent2_runtime.tools import AskUserToolInput


class ScientificAction(AgentAction):
    """Tool names available to the native Scientific Agent."""

    tool: Literal[
        "read_artifact",
        "literature_search",
        "request_work",
        "finish",
        "ask_user",
    ]


class RequestWorkInput(RuntimeModel):
    """LLM-proposed request for more evidence, validated before pausing."""

    assessment: ScientificAssessment
    work_request: WorkRequestDraft


class AskUserInput(AskUserToolInput):
    """A user question paired with the current scientific assessment."""

    assessment: ScientificAssessment


class ScientificFinish(RuntimeModel):
    """LLM-proposed final opinion; the finalizer derives the observed ids."""

    opinion: ScientificOpinion
