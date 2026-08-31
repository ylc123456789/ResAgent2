"""Shared Agentic Loop and injectable profile definition."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, ValidationError

from resagent2_contracts import (
    AgentOwner,
    ErrorCode,
    ModuleError,
    ModuleResult,
    ModuleStatus,
    ModuleTaskRequest,
    RunId,
    SessionId,
    SessionRef,
    SessionStatus,
    TaskBudget,
    TaskId,
)

from .context import ContextBudgetExceeded, ContextComposer
from .llm import LLMClient, LLMExhaustedError
from .models import (
    AgentAction,
    AgentEvent,
    AgentState,
    CompletionDecision,
    ContextSection,
    FinishCandidate,
    PermissionDecision,
    ToolObservation,
)
from .store import InMemorySessionStore, SessionStore
from .tools import Tool, ToolNotFoundError, ToolRegistry

_CONSECUTIVE_FAILURE_LIMIT = 5
_RECENT_OBSERVATION_LIMIT = 6


def _trim_json(value, limit: int) -> str:
    """Serialize ``value`` to a bounded string for context injection.

    Keeps the head and tail when truncating, so a trailing error field (e.g. a
    command's ``stderr_tail``) is not dropped from a long value.
    """
    if value is None:
        return ""
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > limit:
        half = max(limit // 2 - 1, 1)
        text = text[:half] + " … " + text[-half:]
    return text


class ContextBuilder(Protocol):
    """Build Agent-specific context sections from request and generic state."""

    def __call__(
        self,
        request: Any,
        state: AgentState,
    ) -> list[ContextSection]:
        """Return named sections without composing the final prompt."""


class LoopRequest(Protocol):
    """The request surface the AgentLoop reads directly.

    ModuleTaskRequest satisfies this for task-scoped Agents; the run-scoped
    Scientific Agent supplies its own adapter with ``task_id``/``attempt_number``
    left ``None``. The loop never inspects capability-specific fields (goal,
    inputs, workspace); those stay the injected context builder's concern.
    """

    run_id: RunId
    task_id: TaskId | None
    attempt_number: int | None
    budget: TaskBudget
    parent_session_id: SessionId | None


class CompletionCheck(Protocol):
    """Agent-specific deterministic completion boundary."""

    def evaluate(
        self,
        state: AgentState,
        candidate: FinishCandidate | None,
    ) -> CompletionDecision:
        """Decide completion independently of the proposed LLM status."""


class PermissionPolicy(Protocol):
    """Check whether one validated action may execute in the current task."""

    def check(
        self,
        action: AgentAction,
        state: AgentState,
        request: Any,
    ) -> PermissionDecision:
        """Return a structured allow or deny decision."""


class AllowListPermissionPolicy:
    """Minimal policy that permits only an explicit set of Tool names."""

    def __init__(self, allowed_tools: set[str]) -> None:
        self._allowed_tools = frozenset(allowed_tools)

    def check(
        self,
        action: AgentAction,
        state: AgentState,
        request: ModuleTaskRequest,
    ) -> PermissionDecision:
        if action.tool in self._allowed_tools:
            return PermissionDecision(allowed=True)
        return PermissionDecision(
            allowed=False,
            reason=f"tool {action.tool!r} is not allowed by this Agent profile",
        )


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    """Injected differences that let several Agents share one AgentLoop."""

    name: str
    owner: AgentOwner
    system_prompt: str
    tools: tuple[Tool, ...]
    llm_client: LLMClient
    context_builder: ContextBuilder
    permission_policy: PermissionPolicy
    completion_check: CompletionCheck
    action_type: type[AgentAction] = AgentAction
    result_type: type[BaseModel] | None = None
    max_context_tokens: int = 4096


class AgentLoop:
    """Run typed actions with validation, permission, persistence and finalization."""

    def __init__(
        self,
        *,
        store: SessionStore | None = None,
        context_composer: ContextComposer | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.store = store or InMemorySessionStore()
        self.context_composer = context_composer or ContextComposer()
        self.clock = clock
        self._run_llm_calls = 0

    def run(
        self,
        definition: AgentDefinition,
        request: LoopRequest,
        *,
        session_id: str,
        initial_memory: dict | None = None,
    ) -> ModuleResult:
        """Run one new Agent session until completion, pause, or structured failure."""

        self._run_llm_calls = 0
        now = datetime.now(UTC)
        resume_id = request.parent_session_id
        if resume_id is not None:
            if not self.store.exists(resume_id):
                error = ModuleError(
                    code=ErrorCode.CONTRACT_ERROR,
                    message="cannot resume unknown session",
                    retryable=False,
                )
                return ModuleResult(
                    status=ModuleStatus.FAILED,
                    summary=error.message,
                    error=error,
                )
            state = self.store.load(resume_id)
            if (
                state.status != SessionStatus.PAUSED
                or state.run_id != request.run_id
                or state.task_id != request.task_id
                or state.owner != definition.owner
                or state.agent_name != definition.name
            ):
                error = ModuleError(
                    code=ErrorCode.CONTRACT_ERROR,
                    message="resume target does not match request or is not paused",
                    retryable=False,
                )
                return ModuleResult(
                    status=ModuleStatus.FAILED,
                    summary=error.message,
                    error=error,
                )
            state.status = SessionStatus.ACTIVE
            state.attempt_number = request.attempt_number
            state.updated_at = now
        else:
            state = AgentState(
                session_id=session_id,
                agent_name=definition.name,
                owner=definition.owner,
                run_id=request.run_id,
                task_id=request.task_id,
                attempt_number=request.attempt_number,
                memory=initial_memory or {},
                created_at=now,
                updated_at=now,
            )
            if self.store.exists(state.session_id):
                error = ModuleError(
                    code=ErrorCode.CONTRACT_ERROR,
                    message="session_id already exists",
                    retryable=False,
                )
                return ModuleResult(
                    status=ModuleStatus.FAILED,
                    summary=error.message,
                    error=error,
                )
            self._save(state)

        try:
            registry = ToolRegistry(definition.tools)
        except ValueError as error:
            return self._failure(
                state,
                ErrorCode.CONTRACT_ERROR,
                str(error),
                retryable=False,
            )

        started = self.clock()
        attempt_steps = 0
        consecutive_failures = 0
        while (
            attempt_steps < request.budget.max_steps
            and self._run_llm_calls < request.budget.max_llm_calls
        ):
            if self.clock() - started >= request.budget.timeout_seconds:
                return self._failure(
                    state,
                    ErrorCode.TIMEOUT,
                    "Agent session exceeded timeout",
                    retryable=True,
                )

            try:
                sections = list(definition.context_builder(request, state))
                recent = self._recent_observations_section(state)
                if recent is not None:
                    sections.insert(0, recent)
                if state.runtime_feedback is not None:
                    content = (
                        "Your previous action was rejected. Address this "
                        "before retrying the same action:\n"
                        f"{state.runtime_feedback.summary}"
                    )
                    if state.runtime_feedback.value is not None:
                        content += (
                            "\nRejection details:\n"
                            + _trim_json(state.runtime_feedback.value, 800)
                        )
                    sections.insert(
                        0,
                        ContextSection(
                            name="runtime_feedback",
                            content=content,
                            priority=1000,
                            required=True,
                        ),
                    )
                context = self.context_composer.compose(
                    definition.system_prompt,
                    sections,
                    max_tokens=definition.max_context_tokens,
                )
            except ContextBudgetExceeded as error:
                return self._failure(
                    state,
                    ErrorCode.BUDGET_EXHAUSTED,
                    str(error),
                    retryable=False,
                )
            except Exception as error:
                return self._failure(
                    state,
                    ErrorCode.CONTRACT_ERROR,
                    f"context builder failed: {error}",
                    retryable=False,
                )

            tracer = getattr(definition.llm_client, "set_trace_context", None)
            if tracer is not None:
                tracer(
                    run_id=request.run_id,
                    session_id=session_id,
                    task_id=request.task_id,
                    agent=definition.name,
                    step=state.step,
                )
            try:
                raw_action = definition.llm_client.next_action(
                    context,
                    definition.action_type,
                )
                self._run_llm_calls += 1
                state.llm_calls_used += 1
                action = definition.action_type.model_validate(raw_action)
            except ValidationError as error:
                validator = getattr(definition.llm_client, "record_validation", None)
                if validator is not None:
                    validator(str(self._validation_details(error)))
                # A malformed action is recoverable: record it as durable
                # feedback and let the LLM correct it, bounded by the same
                # consecutive-failure limit as any other recoverable error.
                self._feedback(
                    state,
                    "LLM action did not match the action schema: "
                    + str(self._validation_details(error)),
                    tool="llm",
                    value={
                        "validation_errors": self._validation_details(error)[
                            "validation_errors"
                        ]
                    },
                )
                failure = self._note_failure(state, consecutive_failures)
                if failure is not None:
                    return failure
                consecutive_failures += 1
                continue
            except LLMExhaustedError as error:
                return self._failure(
                    state,
                    ErrorCode.TOOL_FAILED,
                    str(error),
                    retryable=False,
                    details={"component": "llm"},
                )
            except Exception as error:
                return self._failure(
                    state,
                    ErrorCode.TOOL_FAILED,
                    f"LLM call failed: {error}",
                    retryable=True,
                    details={"component": "llm"},
                )

            state.step += 1
            attempt_steps += 1
            self._append_event(
                state,
                event_type="action",
                tool=action.tool,
                data=action.model_dump(mode="json"),
            )
            self._save(state)

            if not registry.contains(action.tool):
                return self._failure(
                    state,
                    ErrorCode.INVALID_INPUT,
                    f"unknown tool: {action.tool}",
                    retryable=True,
                )

            try:
                permission = definition.permission_policy.check(action, state, request)
            except Exception as error:
                return self._failure(
                    state,
                    ErrorCode.CONTRACT_ERROR,
                    f"permission policy failed: {error}",
                    retryable=False,
                )
            if not permission.allowed:
                return self._failure(
                    state,
                    ErrorCode.PERMISSION_DENIED,
                    permission.reason or "Tool execution denied",
                    retryable=False,
                )

            try:
                observation = registry.dispatch(
                    action.tool,
                    action.arguments,
                    state,
                )
            except ValidationError as error:
                self._feedback(
                    state,
                    "Tool arguments did not match the input schema: "
                    + str(self._validation_details(error)),
                    tool=action.tool,
                )
                failure = self._note_failure(state, consecutive_failures)
                if failure is not None:
                    return failure
                consecutive_failures += 1
                continue
            except ToolNotFoundError:
                self._feedback(
                    state,
                    f"unknown tool: {action.tool}",
                    tool=action.tool,
                )
                failure = self._note_failure(state, consecutive_failures)
                if failure is not None:
                    return failure
                consecutive_failures += 1
                continue
            except PermissionError as error:
                self._feedback(
                    state,
                    f"Tool execution denied: {error}",
                    tool=action.tool,
                )
                failure = self._note_failure(state, consecutive_failures)
                if failure is not None:
                    return failure
                consecutive_failures += 1
                continue
            except Exception as error:
                self._feedback(
                    state,
                    f"Tool execution failed: {error}",
                    tool=action.tool,
                )
                failure = self._note_failure(state, consecutive_failures)
                if failure is not None:
                    return failure
                consecutive_failures += 1
                continue

            state.memory.update(observation.memory_updates)
            state.last_observation = observation
            if state.runtime_feedback_source == "tool_error":
                state.runtime_feedback = None
                state.runtime_feedback_source = None
            self._append_event(
                state,
                event_type="observation",
                tool=action.tool,
                data=observation.model_dump(mode="json"),
            )
            self._save(state)

            # A successful non-finish tool (read_file, list_files, ...) resets
            # the failure streak. A finish tool's ``ok`` only means "finish was
            # proposed", not "the task is done"; that is decided by the
            # completion check below, so do not reset here.
            is_finish = observation.finish_candidate is not None
            if observation.ok and not is_finish:
                consecutive_failures = 0
            elif not observation.ok:
                failure = self._note_failure(state, consecutive_failures)
                if failure is not None:
                    return failure
                consecutive_failures += 1

            if self.clock() - started >= request.budget.timeout_seconds:
                return self._failure(
                    state,
                    ErrorCode.TIMEOUT,
                    "Agent session exceeded timeout",
                    retryable=True,
                )

            if observation.question is not None:
                state.status = SessionStatus.PAUSED
                self._save(state)
                return ModuleResult(
                    status=ModuleStatus.NEEDS_USER_INPUT,
                    summary=observation.summary,
                    question=observation.question,
                    session=self._session_ref(state),
                    llm_calls=self._run_llm_calls,
                )

            if observation.request_work is not None:
                state.status = SessionStatus.PAUSED
                self._save(state)
                return ModuleResult(
                    status=ModuleStatus.REQUEST_WORK,
                    summary=observation.summary,
                    request_work=observation.request_work,
                    session=self._session_ref(state),
                    llm_calls=self._run_llm_calls,
                )

            try:
                decision = definition.completion_check.evaluate(
                    state,
                    observation.finish_candidate,
                )
            except Exception as error:
                return self._failure(
                    state,
                    ErrorCode.CONTRACT_ERROR,
                    f"completion check failed: {error}",
                    retryable=False,
                )

            if decision.failure is not None:
                return self._failure(
                    state,
                    decision.failure.code,
                    decision.failure.message,
                    retryable=decision.failure.retryable,
                    details=decision.failure.details,
                )

            if decision.complete:
                payload = decision.payload
                if definition.result_type is not None:
                    try:
                        payload = definition.result_type.model_validate(payload).model_dump(
                            mode="json"
                        )
                    except ValidationError as error:
                        return self._failure(
                            state,
                            ErrorCode.CONTRACT_ERROR,
                            "completion payload did not match result schema",
                            retryable=False,
                            details=self._validation_details(error),
                        )
                state.status = SessionStatus.COMPLETED
                state.runtime_feedback = None
                state.runtime_feedback_source = None
                self._save(state)
                status = (
                    ModuleStatus.COMPLETED_WITH_WARNINGS
                    if decision.warnings
                    else ModuleStatus.COMPLETED
                )
                return ModuleResult(
                    status=status,
                    summary=decision.summary or "Completion check passed",
                    payload=payload,
                    artifacts=decision.artifacts,
                    warnings=decision.warnings,
                    session=self._session_ref(state),
                    llm_calls=self._run_llm_calls,
                )
            if decision.summary:
                self._feedback(
                    state,
                    decision.summary,
                    tool="completion_check",
                    value={"completion_check": "rejected"},
                    source="completion_check",
                )
            if observation.finish_candidate is not None:
                # A proposed finish was rejected: count it so a model that keeps
                # proposing the same finish is eventually stopped.
                failure = self._note_failure(state, consecutive_failures)
                if failure is not None:
                    return failure
                consecutive_failures += 1

        return self._failure(
            state,
            ErrorCode.BUDGET_EXHAUSTED,
            "Agent session exhausted step or LLM-call budget",
            retryable=False,
            details=self._failure_details(state),
        )

    def _append_event(
        self,
        state: AgentState,
        *,
        event_type: str,
        tool: str | None,
        data,
    ) -> None:
        state.events.append(
            AgentEvent(
                sequence=len(state.events) + 1,
                step=state.step,
                type=event_type,
                tool=tool,
                data=data,
                created_at=datetime.now(UTC),
            )
        )

    def _save(self, state: AgentState) -> None:
        state.updated_at = datetime.now(UTC)
        self.store.save(state)

    def _note_failure(
        self, state: AgentState, count: int
    ) -> ModuleResult | None:
        """Return a failure result once the recoverable-failure limit is hit."""
        if count + 1 < _CONSECUTIVE_FAILURE_LIMIT:
            return None
        return self._failure(
            state,
            ErrorCode.TOOL_FAILED,
            "consecutive tool failures exceeded the recoverable limit",
            retryable=False,
            details=self._failure_details(state),
        )

    @staticmethod
    def _failure_details(state: AgentState) -> dict:
        """Aggregate the durable feedback and the newest observation together.

        Neither key may mask the other: an older completion rejection (kept in
        ``runtime_feedback``) must not hide the newest command failure (in
        ``last_observation``, which carries the real stderr), so both are always
        preserved when present.
        """
        details: dict = {}
        if state.runtime_feedback is not None:
            details["runtime_feedback"] = {
                "summary": state.runtime_feedback.summary,
                "value": state.runtime_feedback.value,
            }
        if state.last_observation is not None:
            details["last_observation"] = state.last_observation.model_dump(
                mode="json"
            )
        return details

    @staticmethod
    def _recent_observations_section(
        state: AgentState,
        *,
        limit: int = _RECENT_OBSERVATION_LIMIT,
        value_chars: int = 400,
    ) -> ContextSection | None:
        """Build a bounded recent tool history for the LLM context."""
        observations = [e for e in state.events if e.type == "observation"]
        if not observations:
            return None
        recent = observations[-limit:]
        lines: list[str] = []
        for index, event in enumerate(recent, start=1):
            data = event.data if isinstance(event.data, dict) else {}
            summary = data.get("summary", "")
            ok = data.get("ok", True)
            lines.append(
                f"{index}. {event.tool}: {summary} [{'ok' if ok else 'FAILED'}]"
                f"\n   {_trim_json(data.get('value'), value_chars)}"
            )
        return ContextSection(
            name="recent_observations",
            content="Recent tool history (oldest first):\n" + "\n".join(lines),
            priority=950,
            required=False,
        )

    def _feedback(
        self,
        state: AgentState,
        summary: str,
        *,
        tool: str | None = None,
        value=None,
        source: Literal["completion_check", "tool_error"] = "tool_error",
    ) -> None:
        """Persist a recoverable rejection as durable runtime feedback.

        Unlike ``last_observation``, this is injected as the highest-priority
        required context section on every later iteration, so an ordinary
        observation (read_file, list_files, ...) cannot overwrite it. The LLM
        keeps seeing why its previous action was rejected and can act on it.
        """
        feedback = ToolObservation(summary=summary, value=value, ok=False)
        state.last_observation = feedback
        state.runtime_feedback = feedback
        state.runtime_feedback_source = source
        self._append_event(
            state,
            event_type="observation",
            tool=tool,
            data=feedback.model_dump(mode="json"),
        )
        self._save(state)

    def _failure(
        self,
        state: AgentState,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool,
        details: dict | None = None,
    ) -> ModuleResult:
        error = ModuleError(
            code=code,
            message=message,
            retryable=retryable,
            details=details or {},
        )
        self._append_event(
            state,
            event_type="error",
            tool=None,
            data=error.model_dump(mode="json"),
        )
        state.status = SessionStatus.FAILED
        self._save(state)
        return ModuleResult(
            status=ModuleStatus.FAILED,
            summary=message,
            error=error,
            session=self._session_ref(state),
            llm_calls=self._run_llm_calls,
        )

    @staticmethod
    def _session_ref(state: AgentState) -> SessionRef:
        return SessionRef(
            id=state.session_id,
            module=state.owner,
            state_uri=f"memory://sessions/{state.session_id}",
            status=state.status,
            created_at=state.created_at,
            updated_at=state.updated_at,
        )

    @staticmethod
    def _validation_details(error: ValidationError) -> dict:
        """Convert Pydantic errors to the stable JSON subset used by contracts."""

        return {
            "validation_errors": [
                {
                    "type": item["type"],
                    "loc": list(item["loc"]),
                    "message": item["msg"],
                }
                for item in error.errors(include_url=False)
            ]
        }
