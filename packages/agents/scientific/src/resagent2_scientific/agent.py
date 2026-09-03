"""Native Scientific Agent: the scientific brain of one research run.

It implements the ScientificPort boundary (CONTRACTS §20.7): one
``run(ScientificTurnRequest)`` returns a ``ScientificTurnResult`` in one of four
statuses. It reuses the shared AgentLoop; the loop only needs a run-scoped
request surface, so the Scientific Agent adapts the turn into a minimal
``LoopRequest`` and captures the real turn in the context builder closure.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import TypeAdapter, ValidationError

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

from .completion import (
    ScientificCompletionCheck,
    _observed_artifact_ids,
    unobserved_artifact_ids,
)
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
        max_context_tokens: int = 4096,
    ) -> None:
        if max_context_tokens < 1:
            raise ValueError("max_context_tokens must be positive")
        self.llm_client = llm_client
        self.literature_backend = literature_backend
        self.registration_port = registration_port
        self.store = store or InMemorySessionStore()
        self.max_context_tokens = max_context_tokens
        self.loop = AgentLoop(store=self.store)

    def run(self, request: ScientificTurnRequest) -> ScientificTurnResult:
        reader = RegisteredArtifactReader(
            request.authorized_artifacts,
            resolve=getattr(self.registration_port, "resolve", None),
        )
        tools: list = [
            ReadArtifactTool(reader),
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
                list(request.unresolved_task_outcomes),
                list(request.research.required_evidence_kinds),
            ),
            action_type=ScientificAction,
            max_context_tokens=self.max_context_tokens,
        )

        session_id = request.parent_session_id or f"session_scientific_{request.run_id}"

        # Idempotency: repeated delivery of the same work_outcome or the same
        # answers returns the persisted result instead of re-running the loop
        # (CONTRACTS §20.7: work_outcome keyed by work_request_id, answers keyed
        # by question_id).
        idem_key = self._idempotency_key(request)
        cached = self._cached_turn_result(session_id, idem_key)
        if cached is not None:
            return cached

        # The controller durably binds the deterministic scientific session id
        # before its first turn. A crash can therefore leave an ACTIVE session
        # on disk while no ScientificTurnResult reached the controller yet.
        # Re-open that stable checkpoint rather than trying to create the same
        # deterministic id again. A normal first turn still creates a session.
        resume_id = request.parent_session_id
        if resume_id is None and self.store.exists(session_id):
            resume_id = session_id

        loop_request = _LoopRequest(
            run_id=request.run_id,
            budget=request.budget,
            parent_session_id=resume_id,
        )
        result = self.loop.run(
            definition,
            loop_request,
            session_id=session_id,
            initial_memory={},
        )
        llm_calls = result.llm_calls
        turn_result = self._to_turn_result(request, result, session_id, llm_calls)
        self._cache_turn_result(session_id, idem_key, turn_result)
        return turn_result

    @staticmethod
    def _idempotency_key(request: ScientificTurnRequest) -> tuple:
        if request.work_outcome is not None:
            return ("work", request.work_outcome.work_request_id)
        if request.answers:
            return ("answers", tuple(sorted(a.question_id for a in request.answers)))
        if request.parent_session_id is None:
            return ("first",)
        return ("resume",)

    def _to_turn_result(
        self,
        request: ScientificTurnRequest,
        result: ModuleResult,
        session_id: str,
        llm_calls: int,
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
                    llm_calls=llm_calls,
                )
            return ScientificCompletedResult(
                status="completed",
                opinion=opinion,
                session=result.session,
                observed_artifact_ids=observed,
                llm_calls=llm_calls,
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
                    llm_calls=llm_calls,
                )
            if self._unobserved_evidence(assessment.evidence_artifact_ids, observed):
                return ScientificFailedResult(
                    status="failed",
                    error=ModuleError(
                        code=ErrorCode.CONTRACT_ERROR,
                        message="assessment cites evidence not observed by any Tool",
                        retryable=False,
                    ),
                    session=result.session,
                    observed_artifact_ids=observed,
                    llm_calls=llm_calls,
                )
            return ScientificWorkRequestResult(
                status="request_work",
                assessment=assessment,
                work_request=work_request,
                session=result.session,
                observed_artifact_ids=observed,
                llm_calls=llm_calls,
            )

        if result.status == ModuleStatus.NEEDS_USER_INPUT:
            assessment = self._latest_assessment(session_id)
            question = result.question or QuestionDraft(
                text="Input required", reason="Scientific Agent paused for input"
            )
            if self._unobserved_evidence(assessment.evidence_artifact_ids, observed):
                return ScientificFailedResult(
                    status="failed",
                    error=ModuleError(
                        code=ErrorCode.CONTRACT_ERROR,
                        message="assessment cites evidence not observed by any Tool",
                        retryable=False,
                    ),
                    session=result.session,
                    observed_artifact_ids=observed,
                    llm_calls=llm_calls,
                )
            return ScientificQuestionResult(
                status="needs_user_input",
                assessment=assessment,
                question=question,
                session=result.session,
                observed_artifact_ids=observed,
                llm_calls=llm_calls,
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
            llm_calls=llm_calls,
        )

    def _observed(self, session_id: str) -> list[str]:
        try:
            state = self.store.load(session_id)
        except Exception:
            return []
        return _observed_artifact_ids(state)

    _turn_result_adapter = TypeAdapter(ScientificTurnResult)

    @staticmethod
    def _idem_key_label(key: tuple) -> str:
        return "/".join(str(part) for part in key)

    def _cached_turn_result(
        self, session_id: str, key: tuple
    ) -> ScientificTurnResult | None:
        try:
            state = self.store.load(session_id)
        except Exception:
            return None
        raw = state.memory.get("_turn_results", {}).get(self._idem_key_label(key))
        if raw is None:
            return None
        try:
            return self._turn_result_adapter.validate_python(raw)
        except ValidationError:
            return None

    def _cache_turn_result(
        self, session_id: str, key: tuple, turn_result: ScientificTurnResult
    ) -> None:
        try:
            state = self.store.load(session_id)
        except Exception:
            return
        results = dict(state.memory.get("_turn_results", {}))
        results[self._idem_key_label(key)] = turn_result.model_dump(mode="json")
        state.memory["_turn_results"] = results
        self.store.save(state)

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
    def _unobserved_evidence(cited: list[str], observed: list[str]) -> bool:
        """Return True when an assessment cites evidence no Tool observed."""
        return bool(unobserved_artifact_ids(cited, observed))

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
