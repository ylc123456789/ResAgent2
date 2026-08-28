"""Native Scientific Agent: the scientific brain of one research run.

It implements the ScientificPort boundary (CONTRACTS §20.7): one
``run(ScientificTurnRequest)`` returns a ``ScientificTurnResult`` in one of four
statuses. It reuses the shared AgentLoop; the loop only needs a run-scoped
request surface, so the Scientific Agent adapts the turn into a minimal
``LoopRequest`` and captures the real turn in the context builder closure.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from resagent2_contracts import (
    AgentOwner,
    ErrorCode,
    ModuleError,
    ModuleResult,
    ModuleStatus,
    QuestionDraft,
    RunId,
    ScientificAssessment,
    ScientificCompletedResult,
    ScientificFailedResult,
    ScientificOpinion,
    ScientificQuestionResult,
    ScientificTurnRequest,
    ScientificTurnResult,
    ScientificWorkRequestResult,
    SessionId,
    TaskBudget,
    WorkRequestDraft,
)
from resagent2_capabilities import (
    ArtifactRegistrationPort,
    LiteratureSearchBackend,
    LiteratureSearchTool,
    ReadArtifactTool,
    RegisteredArtifactReader,
)
from resagent2_runtime import (
    AgentDefinition,
    AgentLoop,
    AllowListPermissionPolicy,
    InMemorySessionStore,
    LLMClient,
    SessionStore,
)

from .completion import ScientificCompletionCheck, _observed_artifact_ids
from .context import SCIENTIFIC_PROMPT, build_context
from .models import ScientificAction
from .tools import AskUserTool, FinishTool, RequestWorkTool


@dataclass(frozen=True, slots=True)
class _LoopRequest:
    """Run-scoped request surface the AgentLoop reads directly."""

    run_id: RunId
    budget: TaskBudget
    parent_session_id: SessionId | None = None
    task_id: None = None
    attempt_number: None = None


class ScientificAgent:
    """Implement the ScientificPort boundary on top of the shared AgentLoop."""

    def __init__(
        self,
        llm_client: LLMClient,
        *,
        literature_backend: LiteratureSearchBackend | None = None,
        registration_port: ArtifactRegistrationPort | None = None,
        store: SessionStore | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.literature_backend = literature_backend
        self.registration_port = registration_port
        self.store = store or InMemorySessionStore()
        self.loop = AgentLoop(store=self.store)

    def run(self, request: ScientificTurnRequest) -> ScientificTurnResult:
        tools: list = [
            ReadArtifactTool(RegisteredArtifactReader(request.authorized_artifacts)),
            RequestWorkTool(),
            AskUserTool(),
            FinishTool(),
        ]
        if self.literature_backend is not None and self.registration_port is not None:
            tools.append(
                LiteratureSearchTool(self.literature_backend, self.registration_port)
            )
        tools = tuple(tools)

        definition = AgentDefinition(
            name="scientific",
            owner=AgentOwner.SCIENTIFIC,
            system_prompt=SCIENTIFIC_PROMPT,
            tools=tools,
            llm_client=self.llm_client,
            context_builder=lambda _loop_request, state: build_context(request, state),
            permission_policy=AllowListPermissionPolicy({tool.name for tool in tools}),
            completion_check=ScientificCompletionCheck(
                list(request.unresolved_task_outcomes)
            ),
            action_type=ScientificAction,
        )

        session_id = request.parent_session_id or f"session_scientific_{request.run_id}"
        loop_request = _LoopRequest(
            run_id=request.run_id,
            budget=request.budget,
            parent_session_id=request.parent_session_id,
        )
        result = self.loop.run(
            definition,
            loop_request,
            session_id=session_id,
            initial_memory={},
        )
        return self._to_turn_result(request, result, session_id)

    def _to_turn_result(
        self,
        request: ScientificTurnRequest,
        result: ModuleResult,
        session_id: str,
    ) -> ScientificTurnResult:
        observed = self._observed(session_id)

        if result.status == ModuleStatus.COMPLETED:
            opinion = self._validated_opinion(result.payload)
            if opinion is None:
                return ScientificFailedResult(
                    status="failed",
                    error=ModuleError(
                        code=ErrorCode.CONTRACT_ERROR,
                        message="completion payload did not contain a valid opinion",
                        retryable=False,
                    ),
                    session=result.session,
                    observed_artifact_ids=observed,
                )
            return ScientificCompletedResult(
                status="completed",
                opinion=opinion,
                session=result.session,
                observed_artifact_ids=observed,
            )

        if result.status == ModuleStatus.REQUEST_WORK:
            assessment = self._assessment_from_signal(result.request_work)
            work_request = self._work_request_from_signal(result.request_work)
            if assessment is None or work_request is None:
                return ScientificFailedResult(
                    status="failed",
                    error=ModuleError(
                        code=ErrorCode.CONTRACT_ERROR,
                        message="request_work signal did not carry a valid assessment/work_request",
                        retryable=False,
                    ),
                    session=result.session,
                    observed_artifact_ids=observed,
                )
            return ScientificWorkRequestResult(
                status="request_work",
                assessment=assessment,
                work_request=work_request,
                session=result.session,
                observed_artifact_ids=observed,
            )

        if result.status == ModuleStatus.NEEDS_USER_INPUT:
            assessment = self._latest_assessment(session_id)
            question = result.question or QuestionDraft(
                text="Input required", reason="Scientific Agent paused for input"
            )
            return ScientificQuestionResult(
                status="needs_user_input",
                assessment=assessment,
                question=question,
                session=result.session,
                observed_artifact_ids=observed,
            )

        error = result.error or ModuleError(
            code=ErrorCode.TOOL_FAILED,
            message=result.summary,
            retryable=False,
        )
        return ScientificFailedResult(
            status="failed",
            error=error,
            session=result.session,
            observed_artifact_ids=observed,
        )

    def _observed(self, session_id: str) -> list[str]:
        try:
            state = self.store.load(session_id)
        except Exception:
            return []
        return _observed_artifact_ids(state)

    def _latest_assessment(self, session_id: str) -> ScientificAssessment:
        try:
            state = self.store.load(session_id)
        except Exception:
            return ScientificAssessment(statement="No assessment recorded")
        raw = state.memory.get("latest_assessment")
        if raw is None:
            return ScientificAssessment(statement="No assessment recorded")
        try:
            return ScientificAssessment.model_validate(raw)
        except ValidationError:
            return ScientificAssessment(statement="No assessment recorded")

    @staticmethod
    def _validated_opinion(payload) -> ScientificOpinion | None:
        if not isinstance(payload, dict):
            return None
        raw = payload.get("opinion")
        if raw is None:
            return None
        try:
            return ScientificOpinion.model_validate(raw)
        except ValidationError:
            return None

    @staticmethod
    def _assessment_from_signal(signal) -> ScientificAssessment | None:
        if not isinstance(signal, dict):
            return None
        try:
            return ScientificAssessment.model_validate(signal["assessment"])
        except (KeyError, ValidationError):
            return None

    @staticmethod
    def _work_request_from_signal(signal) -> WorkRequestDraft | None:
        if not isinstance(signal, dict):
            return None
        try:
            return WorkRequestDraft.model_validate(signal["work_request"])
        except (KeyError, ValidationError):
            return None
