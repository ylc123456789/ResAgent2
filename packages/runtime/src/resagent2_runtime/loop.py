"""Shared Agentic Loop and injectable profile definition."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Callable, Protocol

from pydantic import BaseModel, ValidationError

from resagent2_contracts import (
    AgentOwner,
    ErrorCode,
    ModuleError,
    ModuleResult,
    ModuleStatus,
    ModuleTaskRequest,
    SessionRef,
    SessionStatus,
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
)
from .store import InMemorySessionStore, SessionStore
from .tools import Tool, ToolNotFoundError, ToolRegistry


class ContextBuilder(Protocol):
    """Build Agent-specific context sections from request and generic state."""

    def __call__(
        self,
        request: ModuleTaskRequest,
        state: AgentState,
    ) -> list[ContextSection]:
        """Return named sections without composing the final prompt."""


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
        request: ModuleTaskRequest,
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

    def run(
        self,
        definition: AgentDefinition,
        request: ModuleTaskRequest,
        *,
        session_id: str,
        initial_memory: dict | None = None,
    ) -> ModuleResult:
        """Run one new Agent session until completion, pause, or structured failure."""

        now = datetime.now(UTC)
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
        llm_calls = 0
        while (
            state.step < request.budget.max_steps
            and llm_calls < request.budget.max_llm_calls
        ):
            if self.clock() - started >= request.budget.timeout_seconds:
                return self._failure(
                    state,
                    ErrorCode.TIMEOUT,
                    "Agent session exceeded timeout",
                    retryable=True,
                )

            try:
                sections = definition.context_builder(request, state)
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

            try:
                raw_action = definition.llm_client.next_action(
                    context,
                    definition.action_type,
                )
                llm_calls += 1
                action = definition.action_type.model_validate(raw_action)
            except ValidationError as error:
                return self._failure(
                    state,
                    ErrorCode.INVALID_INPUT,
                    "LLM action did not match action schema",
                    retryable=True,
                    details=self._validation_details(error),
                )
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
                return self._failure(
                    state,
                    ErrorCode.INVALID_INPUT,
                    "Tool arguments did not match input schema",
                    retryable=True,
                    details=self._validation_details(error),
                )
            except ToolNotFoundError:
                return self._failure(
                    state,
                    ErrorCode.INVALID_INPUT,
                    f"unknown tool: {action.tool}",
                    retryable=True,
                )
            except Exception as error:
                return self._failure(
                    state,
                    ErrorCode.TOOL_FAILED,
                    f"Tool execution failed: {error}",
                    retryable=True,
                    details={"tool": action.tool},
                )

            state.memory.update(observation.memory_updates)
            state.last_observation = observation
            self._append_event(
                state,
                event_type="observation",
                tool=action.tool,
                data=observation.model_dump(mode="json"),
            )
            self._save(state)

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
                )

        return self._failure(
            state,
            ErrorCode.BUDGET_EXHAUSTED,
            "Agent session exhausted step or LLM-call budget",
            retryable=False,
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
