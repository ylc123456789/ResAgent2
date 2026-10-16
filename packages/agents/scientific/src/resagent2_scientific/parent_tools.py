"""Directional parent controls; never execution authorizations."""
from pydantic import BaseModel, ConfigDict, Field, model_validator
from resagent2_runtime import ToolObservation

class ReplyToTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str | None = Field(default=None, description="Exact child Task ID; omit only to continue current work without a clarification")
    question_id: str | None = None
    values: dict[str, str] | None = None
    clarification: str | None = None
    user_answer_question_id: str | None = Field(default=None, description="Reference an authoritative user business answer; code copies its original words/values")
    user_input_event_id: str | None = Field(default=None, description="Forward an original user-input record verbatim; code preserves source and original time")
    resume_work: bool = Field(default=True, description="Release pending child work after this reply; false keeps child execution paused while you clarify other Tasks")

    @model_validator(mode="after")
    def shape(self):
        if self.task_id is None and any(x is not None for x in (self.question_id, self.values, self.clarification, self.user_input_event_id, self.user_answer_question_id)):
            raise ValueError("a directional reply requires its child Task")
        if self.question_id is not None and not (self.values or self.user_answer_question_id):
            raise ValueError("a question reply needs all requested answer values")
        if self.values is not None and self.user_answer_question_id is not None:
            raise ValueError("forwarded user answer cannot be overwritten with model values")
        return self

class ReplyToTaskTool:
    """Answer your direct child's business question or clarify its assigned task.

    Parent judgment is labelled as such. Reference a real user input to forward its
    exact words; never label your own explanation as a user's instruction. This
    grants no execution permission. When a new requirement affects several Tasks,
    set resume_work=false while sending intermediate clarifications and true on
    the final reply so no child resumes with incomplete guidance. With no task_id,
    resume_work=true continues pending work unchanged.
    """
    name = "reply_to_task"
    input_model = ReplyToTaskInput

    def __init__(self, request):
        self.request = request

    def execute(self, state, arguments):
        allowed = {item["task_id"] for item in self.request.task_context.get("child_tasks", [])}
        if arguments.task_id is not None and arguments.task_id not in allowed:
            raise PermissionError("reply target is not a live direct child Task")
        if arguments.question_id:
            question = next((q for q in self.request.child_questions if q.id == arguments.question_id), None)
            if question is None or question.task_id != arguments.task_id or question.recipient_session_id != state.session_id:
                raise PermissionError("question does not belong to this parent and child")
            values = arguments.values
            if arguments.user_answer_question_id:
                answer = next((a for a in self.request.user_answers if a.question_id == arguments.user_answer_question_id and a.source == "user" and a.action is None), None)
                if answer is None:
                    raise PermissionError("user answer reference is not authorized")
                values = answer.values
            if set(values) != set(question.requested_fields):
                raise ValueError("reply must supply the exact requested fields")
        if arguments.user_answer_question_id and not any(a.question_id == arguments.user_answer_question_id and a.source == "user" and a.action is None for a in self.request.user_answers):
            raise PermissionError("user answer reference is not authorized")
        if arguments.user_input_event_id:
            if not any(e.event_id == arguments.user_input_event_id and e.source == "user" for e in self.request.upstream_inputs):
                raise PermissionError("user input reference is not authorized")
        return ToolObservation(summary="Parent reply submitted", task_reply=arguments.model_dump(mode="json"))

class AbandonWorkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_request_id: str
    reason: str = Field(min_length=1)

class AbandonWorkTool:
    """End your own pending delegation when its original work is no longer needed.

    Already completed outputs and current files remain. This does not replace the
    work automatically; request new work separately if needed.
    """
    name = "abandon_work"
    input_model = AbandonWorkInput

    def __init__(self, request):
        self.request = request

    def execute(self, state, arguments):
        if arguments.work_request_id != self.request.task_context.get("active_work_request_id"):
            raise PermissionError("can end only your own active delegation")
        return ToolObservation(summary="Delegation termination requested", abandon_work_id=arguments.work_request_id,
                               control_reason=arguments.reason)
