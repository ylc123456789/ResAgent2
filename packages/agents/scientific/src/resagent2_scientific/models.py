"""Scientific actions and content-bearing control tools."""

from typing import Literal

from resagent2_contracts import ScientificAssessment, WorkRequestDraft
from resagent2_runtime import AgentAction
from resagent2_runtime.models import RuntimeModel
from resagent2_runtime.tools import AskUserToolInput


class ScientificAction(AgentAction):
    tool: Literal["read_artifact", "literature_search", "request_work", "finish", "ask_user"]


class RequestWorkInput(RuntimeModel):
    assessment: ScientificAssessment
    work_request: WorkRequestDraft


class AskUserInput(AskUserToolInput):
    assessment: ScientificAssessment
