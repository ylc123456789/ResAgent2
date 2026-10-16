"""Scientific actions and content-bearing control tools."""

from resagent2_contracts import ScientificAssessment, WorkRequestDraft
from resagent2_runtime.models import RuntimeModel
from resagent2_runtime.tools import AskUserToolInput


class RequestWorkInput(RuntimeModel):
    assessment: ScientificAssessment
    work_request: WorkRequestDraft


class AskUserInput(AskUserToolInput):
    assessment: ScientificAssessment
