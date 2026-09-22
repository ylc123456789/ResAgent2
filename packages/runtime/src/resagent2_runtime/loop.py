"""Shared Agentic Loop and injectable profile definition."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Callable, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from resagent2_contracts import (
    AgentOwner,
    ActionSnapshot,
    AgentPermissions,
    ArtifactCandidate,
    ArtifactOutput,
    ControlSignal,
    ErrorCode,
    ModuleError,
    AgentResult,
    ModuleStatus,
    AgentRequest,
    RunId,
    SessionId,
    SessionRef,
    SessionStatus,
    TaskBudget,
    TaskId,
    QuestionDraft,
)

from .context import DEFAULT_AGENT_CONTEXT_TOKENS, ContextBudgetExceeded, ContextComposer, ContextMaterial
from .budget import (
    BudgetExhaustedError, DeadlineExceededError, current_budget, execution_budget, invoke_model,
)
from .compaction import plan_compaction
from .llm import LLMClient, LLMExhaustedError
from .models import (
    AgentAction,
    AgentEvent,
    AgentState,
    CompletionDecision,
    ContextSection,
    FinishCandidate,
    HistoryCheckpoint,
    PermissionDecision,
    ToolCallTurn,
    ToolObservation,
)
from .store import InMemorySessionStore, SessionStore
from .tools import Tool, ToolRegistry, tool_contracts_text
from .tool_calling import (
    NativeToolCallError, complete_pending_turn, native_actions,
    native_context_budget, native_input_text, native_tool_schemas, tool_receipt,
    record_tool_result, recover_pending_turn,
)

_CONSECUTIVE_FAILURE_LIMIT = 5
_RECENT_OBSERVATION_LIMIT = 6


def _trim_json(value, limit: int) -> str:
    """Serialize ``value`` to a bounded string for context injection.

    Keeps the head and tail when truncating, but cannot preserve every field.
    Capabilities provide a separate projection for actionable command errors.
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
        max_context_tokens: int,
    ) -> list[ContextSection | ContextMaterial]:
        """Pack sections against the effective module/model input limit."""


class LoopRequest(Protocol):
    """The request surface the AgentLoop reads directly.

    AgentRequest supplies this surface for task-scoped and run-scoped Agents.
    The loop leaves materials and operation grants to the injected context
    builder and permission policy.
    """

    run_id: RunId
    task_id: TaskId | None
    attempt_number: int | None
    budget: TaskBudget
    parent_session_id: SessionId | None
    permissions: AgentPermissions


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
        """Return a structured allow, ask or deny decision."""


class AllowListPermissionPolicy:
    """Minimal policy that permits only an explicit set of Tool names."""

    def __init__(self, allowed_tools: set[str]) -> None:
        self._allowed_tools = frozenset(allowed_tools)

    def check(
        self,
        action: AgentAction,
        state: AgentState,
        request: AgentRequest,
    ) -> PermissionDecision:
        if action.tool in self._allowed_tools:
            return PermissionDecision(outcome="allow")
        return PermissionDecision(
            outcome="deny",
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
    max_context_tokens: int = DEFAULT_AGENT_CONTEXT_TOKENS


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
        self._active_call_id: str | None = None

    def run(
        self,
        definition: AgentDefinition,
        request: LoopRequest,
        *,
        session_id: str,
        initial_memory: dict | None = None,
    ) -> AgentResult:
        """Run one new Agent session until completion, pause, or structured failure."""

        with execution_budget(max_llm_calls=request.budget.max_llm_calls,
                              timeout_seconds=request.budget.timeout_seconds, clock=self.clock):
            return self._run(definition, request, session_id=session_id, initial_memory=initial_memory)

    def _run(self, definition, request, *, session_id, initial_memory):

        self._run_llm_calls = 0
        self._initial_usage = current_budget().usage.used
        self._active_call_id = None
        now = datetime.now(UTC)
        native_call = getattr(definition.llm_client, "next_tool_call", None)
        protocol_key = (
            getattr(definition.llm_client, "tool_session_key", None)
            if native_call is not None else None
        )
        if native_call is not None and (not isinstance(protocol_key, str) or not protocol_key.strip()):
            return AgentResult(
                status=ModuleStatus.FAILED,
                report="native client must provide a stable tool_session_key",
                error=ModuleError(
                    code=ErrorCode.CONTRACT_ERROR,
                    message="native client must provide a stable tool_session_key",
                    retryable=False,
                ),
            )
        resume_id = request.parent_session_id
        if resume_id is not None:
            if not self.store.exists(resume_id):
                error = ModuleError(
                    code=ErrorCode.CONTRACT_ERROR,
                    message="cannot resume unknown session",
                    retryable=False,
                )
                return AgentResult(
                    status=ModuleStatus.FAILED,
                    report=error.message,
                    error=error,
                )
            state = self.store.load(resume_id)
            if (
                state.status not in {SessionStatus.PAUSED, SessionStatus.ACTIVE}
                or state.run_id != request.run_id
                or state.task_id != request.task_id
                or state.attempt_number != request.attempt_number
                or state.owner != definition.owner
                or state.agent_name != definition.name
                or state.tool_protocol_key != protocol_key
            ):
                error = ModuleError(
                    code=ErrorCode.CONTRACT_ERROR,
                    message=(
                        "resume target does not match request or is not "
                        "recoverable (including model/provider protocol identity)"
                    ),
                    retryable=False,
                )
                return AgentResult(
                    status=ModuleStatus.FAILED,
                    report=error.message,
                    error=error,
                )
            state.status = SessionStatus.ACTIVE
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
                tool_protocol_key=protocol_key,
                created_at=now,
                updated_at=now,
            )
            if self.store.exists(state.session_id):
                error = ModuleError(
                    code=ErrorCode.CONTRACT_ERROR,
                    message="session_id already exists",
                    retryable=False,
                )
                return AgentResult(
                    status=ModuleStatus.FAILED,
                    report=error.message,
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

        try:
            schemas = native_tool_schemas(definition.tools) if native_call is not None else []
        except Exception as error:
            return self._failure(
                state, ErrorCode.CONTRACT_ERROR,
                f"tool schema construction failed: {error}", retryable=False,
            )
        if state.tool_turns and native_call is None:
            return self._failure(
                state, ErrorCode.CONTRACT_ERROR,
                "native Session requires a tool-calling client", retryable=False,
            )
        interrupted = False
        for turn in state.tool_turns:
            if len(turn.tool_results) < len(turn.tool_calls):
                recover_pending_turn(turn)
                interrupted = True
        if interrupted:
            self._feedback(
                state, "An interrupted tool batch was not replayed. Inspect its receipts and current state before retrying.",
                tool="runtime",
            )

        consecutive_failures = 0
        while self._run_llm_calls < request.budget.max_llm_calls:
            if current_budget().expired:
                return self._failure(
                    state,
                    ErrorCode.TIMEOUT,
                    "Agent session exceeded timeout",
                    retryable=True,
                )

            tracer = getattr(definition.llm_client, "set_trace_context", None)
            if tracer is not None:
                tracer(run_id=request.run_id, session_id=state.session_id,
                       task_id=request.task_id, agent=definition.name, step=state.step)
            try:
                context_limit = definition.max_context_tokens
                if native_call is not None:
                    budgeter = getattr(definition.llm_client, "tool_input_limit", None)
                    if budgeter is not None:
                        context_limit = min(context_limit, budgeter(context_limit))
                else:
                    budgeter = getattr(definition.llm_client, "context_budget", None)
                    if budgeter is not None:
                        context_limit = min(context_limit, budgeter(definition.action_type, context_limit))
                if context_limit < 1:
                    raise ContextBudgetExceeded("effective context limit must be positive")
                context = None
                context_error = None
                try:
                    context = self._compose_context(
                        definition, request, state, schemas, context_limit, native=native_call is not None,
                    )
                except ContextBudgetExceeded as error:
                    context_error = error
                if native_call is not None and callable(getattr(definition.llm_client, "summarize_history", None)):
                    checkpoint = state.history_checkpoint
                    plan = plan_compaction(
                        current_context=context.text if context is not None else "",
                        schemas=schemas, turns=state.tool_turns,
                        history_start=checkpoint.history_start if checkpoint else 0,
                        previous_summary=checkpoint.summary if checkpoint else None,
                        max_input_tokens=context_limit, force=context is None,
                    )
                    if plan is not None:
                        failure = self._compact_history(
                            definition, request, state, schemas, context_limit, plan,
                        )
                        if failure is not None:
                            return failure
                        context = self._compose_context(
                            definition, request, state, schemas, context_limit, native=True,
                        )
                if context is None:
                    raise context_error or ContextBudgetExceeded("current context does not fit")
            except (BudgetExhaustedError, DeadlineExceededError) as error:
                self._sync_usage(state)
                return self._failure(state,
                                     ErrorCode.TIMEOUT if isinstance(error, DeadlineExceededError) else ErrorCode.BUDGET_EXHAUSTED,
                                     str(error), retryable=False)
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

            if current_budget().expired:
                return self._failure(state, ErrorCode.TIMEOUT, "Agent session exceeded timeout", retryable=True)
            limiter = getattr(definition.llm_client, "set_attempt_limit", None)
            if limiter is not None:
                limiter(request.budget.max_llm_calls - self._run_llm_calls)
            charged = False
            try:
                if native_call is not None:
                    reply = invoke_model(definition.llm_client, "next_tool_call", context, schemas,
                                         self._active_turns(state), max_input_tokens=context_limit)
                else:
                    reply = invoke_model(definition.llm_client, "next_action", context, definition.action_type)
                self._sync_usage(state)
                charged = True
                if self._run_llm_calls > request.budget.max_llm_calls:
                    return self._failure(state, ErrorCode.BUDGET_EXHAUSTED,
                                         "Client exceeded the allocated LLM-call budget", retryable=False)
                if native_call is not None:
                    turn = ToolCallTurn.model_validate(reply)
                    used_ids = {call.id for old in state.tool_turns for call in old.tool_calls}
                    rejection = None
                    if turn.tool_results or turn.executing_call_id is not None:
                        rejection = "A model reply cannot provide tool execution receipts"
                    if any(call.id in used_ids for call in turn.tool_calls):
                        rejection = "Provider reused a historical tool call ID; no tool was executed"
                    if rejection is not None:
                        validator = getattr(definition.llm_client, "record_validation", None)
                        if validator is not None:
                            validator(rejection)
                        raise NativeToolCallError(rejection)
                    if turn.tool_calls or turn.content or turn.reasoning_content:
                        state.tool_turns.append(turn)
                        # Checkpoint before any dispatch. Missing receipts on
                        # resume are unknown outcomes, never replay instructions.
                        self._save(state)
                    raw_actions = native_actions(turn)
                else:
                    raw_actions = [reply]
                actions = []
                for raw_action in raw_actions:
                    actions.append(definition.action_type.model_validate(raw_action))
                if len(actions) > 1:
                    failure = self._preflight_batch(definition, registry, request, state, actions)
                    if failure is not None:
                        return failure
            except (json.JSONDecodeError, ValidationError, NativeToolCallError, PermissionError) as error:
                if not charged:
                    self._sync_usage(state)
                if isinstance(error, json.JSONDecodeError):
                    # next_action raised before the success-path accounting.
                    # Count any preceding transport retries as well.
                    summary = (
                        f"Native tool arguments were not valid JSON: {error}. "
                        "Return native tool calls with JSON objects of arguments. "
                        "No tool was executed."
                    ) if native_call is not None else (
                        f"LLM output was not valid JSON: {error}. "
                        "Return exactly one JSON object matching the action "
                        "schema and tool contracts, with no surrounding text "
                        "or additional actions. No tool was executed."
                    )
                    # Feedback never quotes invalid output. Native calls remain
                    # in the paired protocol history, not domain memory; JSON-only
                    # clients keep the raw response exclusively in full trace.
                    details = None
                elif isinstance(error, PermissionError):
                    summary = f"Tool execution denied: {error}. No tool was executed."
                    details = None
                elif isinstance(error, NativeToolCallError):
                    summary = str(error) + " Correct the native tool batch. No tool was executed."
                    details = None
                else:
                    details = self._validation_details(error)
                    validator = getattr(definition.llm_client, "record_validation", None)
                    if validator is not None:
                        validator(str(details))
                    summary = "LLM action did not match the action schema: " + str(details)
                # A malformed action is recoverable: record it as durable
                # feedback and let the LLM correct it, bounded by the same
                # consecutive-failure limit as any other recoverable error.
                self._feedback(
                    state,
                    summary,
                    tool="llm",
                    value=details,
                )
                failure = self._note_failure(state, consecutive_failures)
                if failure is not None:
                    return failure
                consecutive_failures += 1
                continue
            except (BudgetExhaustedError, DeadlineExceededError) as error:
                self._sync_usage(state)
                return self._failure(state,
                                     ErrorCode.TIMEOUT if isinstance(error, DeadlineExceededError) else ErrorCode.BUDGET_EXHAUSTED,
                                     str(error), retryable=False)
            except ContextBudgetExceeded as error:
                return self._failure(
                    state, ErrorCode.BUDGET_EXHAUSTED, str(error), retryable=False,
                )
            except LLMExhaustedError as error:
                self._sync_usage(state)
                return self._failure(
                    state,
                    ErrorCode.TOOL_FAILED,
                    str(error),
                    retryable=False,
                    details={"component": "llm"},
                )
            except Exception as error:
                # The transport failed after one or more real HTTP attempts;
                # those attempts still count toward the Run ledger.
                if not charged:
                    self._sync_usage(state)
                return self._failure(
                    state,
                    ErrorCode.TOOL_FAILED,
                    f"LLM call failed: {error}",
                    retryable=True,
                    details={"component": "llm"},
                )

            for index, action in enumerate(actions):
                self._active_call_id = turn.tool_calls[index].id if native_call is not None else None
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
                    registry.validate(action.tool, action.arguments)
                    permission = definition.permission_policy.check(action, state, request)
                except DeadlineExceededError as error:
                    return self._failure(state, ErrorCode.TIMEOUT, str(error), retryable=False)
                except (ValidationError, PermissionError, ValueError) as error:
                    self._feedback(state, f"Tool request rejected: {error}", tool=action.tool)
                    failure = self._note_failure(state, consecutive_failures)
                    if failure is not None:
                        return failure
                    consecutive_failures += 1
                    break
                except Exception as error:
                    return self._failure(
                        state,
                        ErrorCode.CONTRACT_ERROR,
                        f"permission policy failed: {error}",
                        retryable=False,
                    )
                if permission.outcome == "deny":
                    if permission.approval_id:
                        state.pending_action = None
                    self._feedback(state, permission.reason or "Tool execution denied", tool=action.tool)
                    failure = self._note_failure(state, consecutive_failures)
                    if failure is not None:
                        return failure
                    consecutive_failures += 1
                    break

                # Model calls and permission checks can consume the remaining
                # deadline. Their completed work is already accounted for, but an
                # expired action must not start a new tool or its side effects.
                if current_budget().expired:
                    return self._failure(
                        state,
                        ErrorCode.TIMEOUT,
                        "Agent session exceeded timeout before tool dispatch",
                        retryable=True,
                    )

                try:
                    current_budget().remaining_timeout()
                    if permission.outcome == "ask":
                        state.pending_action = ActionSnapshot(
                            action_id=f"action_{uuid4().hex}", tool=action.tool,
                            arguments=registry.validate(action.tool, action.arguments).model_dump(mode="json"),
                            context=permission.context, run_id=request.run_id,
                            session_id=state.session_id, task_id=request.task_id,
                            attempt_number=request.attempt_number,
                        )
                        observation = ToolObservation(
                            summary=permission.reason or "Operation confirmation required",
                            question=QuestionDraft(
                                text=(permission.reason + "\nTool: " + action.tool
                                      + "\nArguments: " + json.dumps(state.pending_action.arguments, ensure_ascii=False)
                                      + "\nContext: " + json.dumps({key: value for key, value in permission.context.items()
                                                                    if key != "deletion"}, ensure_ascii=False)),
                                requested_fields=["approve"], options={"approve": ["yes", "no"]},
                                action=state.pending_action,
                            ),
                        )
                    else:
                        if permission.approval_id:
                            if state.pending_action is None or state.pending_action.action_id != permission.approval_id:
                                raise PermissionError("approval is not pending")
                            state.pending_action = None
                        if native_call is not None:
                            state.tool_turns[-1].executing_call_id = self._active_call_id
                        self._save(state)
                        observation = registry.dispatch(action.tool, action.arguments, state,
                                                        prepared=permission.prepared)
                except DeadlineExceededError as error:
                    return self._failure(state, ErrorCode.TIMEOUT, str(error), retryable=False)
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
                    break
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
                    break
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
                    break

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

                if current_budget().expired:
                    return self._failure(
                        state,
                        ErrorCode.TIMEOUT,
                        "Agent session exceeded timeout",
                        retryable=True,
                    )

                if observation.question is not None:
                    self._cancel_pending_calls(state)
                    state.status = SessionStatus.PAUSED
                    self._save(state)
                    return AgentResult(
                        status=ModuleStatus.NEEDS_USER_INPUT,
                        report=observation.summary,
                        artifacts=[ArtifactCandidate(
                            kind="question", path="question.json",
                            media_type="application/json", summary=observation.question.text,
                            content=observation.question.model_dump_json(),
                        )],
                        control=ControlSignal(action="ask_user", candidate_index=0),
                        session=self._session_ref(state),
                        llm_calls=self._run_llm_calls,
                    )

                if observation.request_work is not None:
                    if definition.owner != AgentOwner.SCIENTIFIC or not request.permissions.request_work:
                        return self._failure(
                            state, ErrorCode.PERMISSION_DENIED,
                            "this invocation is not permitted to request work", retryable=False,
                        )
                    self._cancel_pending_calls(state)
                    state.status = SessionStatus.PAUSED
                    self._save(state)
                    return AgentResult(
                        status=ModuleStatus.REQUEST_WORK,
                        report=observation.summary,
                        artifacts=[ArtifactCandidate(
                            kind="work_request", path="work_request.json",
                            media_type="application/json", summary=observation.summary,
                            content=json.dumps(observation.request_work),
                        )],
                        control=ControlSignal(action="request_work", candidate_index=0),
                        session=self._session_ref(state),
                        llm_calls=self._run_llm_calls,
                    )

                try:
                    decision = definition.completion_check.evaluate(
                        state,
                        observation.finish_candidate,
                    )
                except DeadlineExceededError as error:
                    return self._failure(state, ErrorCode.TIMEOUT, str(error), retryable=False)
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
                        artifacts=decision.artifacts,
                        report=decision.report or decision.failure.message,
                    )

                if decision.complete:
                    self._cancel_pending_calls(state)
                    state.status = SessionStatus.COMPLETED
                    state.runtime_feedback = None
                    state.runtime_feedback_source = None
                    self._save(state)
                    status = (
                        ModuleStatus.COMPLETED_WITH_WARNINGS
                        if decision.warnings
                        else ModuleStatus.COMPLETED
                    )
                    return AgentResult(
                        status=status,
                        report=decision.report or "Completion check passed",
                        artifacts=decision.artifacts,
                        warnings=decision.warnings,
                        session=self._session_ref(state),
                        llm_calls=self._run_llm_calls,
                    )
                if decision.report:
                    self._feedback(
                        state,
                        decision.report,
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

                if not observation.ok:
                    break

            self._cancel_pending_calls(state)
            self._active_call_id = None

        return self._failure(
            state,
            ErrorCode.BUDGET_EXHAUSTED,
            "Agent session exhausted LLM-call budget",
            retryable=False,
            details=self._failure_details(state),
        )


    @staticmethod
    def _active_turns(state: AgentState) -> list[ToolCallTurn]:
        start = state.history_checkpoint.history_start if state.history_checkpoint else 0
        return state.tool_turns[start:]

    def _compose_context(self, definition, request, state, schemas, context_limit, *, native):
        """Use the same domain builder with a bounded, paired protocol suffix."""
        turns = self._active_turns(state)
        material_limit = native_context_budget(context_limit, schemas, turns) if native else context_limit
        sections = list(definition.context_builder(request, state, material_limit))
        if state.history_checkpoint is not None:
            sections.insert(0, ContextSection(
                name="history_checkpoint",
                content=(
                    "Lossy handoff of older completed tool interactions, not evidence or current "
                    "workspace/verification state. Current checked context and user answers take "
                    "precedence. Re-read sources for exact code or evidence.\n"
                    + state.history_checkpoint.summary
                ),
                required=True, priority=900,
            ))
        recent = self._recent_observations_section(state) if not native else None
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
        if not native:
            sections.append(
                ContextSection(
                    name="tool_contracts",
                    content=tool_contracts_text(definition.tools),
                    priority=990,
                    required=True,
                )
            )
        compose_options = {}
        if native:
            compose_options["measure"] = lambda text: self.context_composer.estimate_tokens(
                native_input_text(text, schemas, turns)
            )
        return self.context_composer.compose(
            definition.system_prompt,
            sections,
            max_tokens=context_limit,
            **compose_options,
        )

    def _compact_history(self, definition, request, state, schemas, context_limit, plan) -> AgentResult | None:
        """Charge one bounded summary request and atomically publish a usable checkpoint."""
        client = definition.llm_client
        remaining = request.budget.max_llm_calls - self._run_llm_calls
        if remaining < 2:
            return self._failure(state, ErrorCode.BUDGET_EXHAUSTED,
                                 "Insufficient calls for compaction and continuation", retryable=False)
        limiter = getattr(client, "set_attempt_limit", None)
        if limiter is not None:
            limiter(remaining - 1)
        try:
            summary = invoke_model(client, "summarize_history", plan.prompt, max_input_tokens=context_limit)
        except (BudgetExhaustedError, DeadlineExceededError):
            raise
        except Exception as error:
            self._sync_usage(state)
            return self._failure(state, ErrorCode.TOOL_FAILED,
                                 f"History compaction failed: {error}", retryable=False,
                                 details={"component": "compaction"})
        self._sync_usage(state)
        if self._run_llm_calls >= request.budget.max_llm_calls:
            return self._failure(state, ErrorCode.BUDGET_EXHAUSTED,
                                 "Compaction consumed the remaining call budget", retryable=False)
        if not isinstance(summary, str) or not summary.strip():
            return self._failure(state, ErrorCode.CONTRACT_ERROR,
                                 "Compaction handoff must be nonempty text",
                                 retryable=False, details={"component": "compaction"})
        checkpoint = HistoryCheckpoint(history_start=plan.history_start, summary=summary)
        # Compose against the proposed checkpoint before publishing it. A failed
        # summary or still-oversized request never advances the durable boundary.
        # The writing target is not a second hard budget: retain the complete
        # summary when it fits with current required context and recent turns.
        proposed = state.model_copy(update={"history_checkpoint": checkpoint})
        self._compose_context(definition, request, proposed, schemas, context_limit, native=True)
        state.history_checkpoint = checkpoint
        self._append_event(state, event_type="compaction", tool=None,
                           data=checkpoint.model_dump(mode="json"))
        self._save(state)
        return None

    def _append_event(
        self,
        state: AgentState,
        *,
        event_type: str,
        tool: str | None,
        data,
    ) -> None:
        if event_type == "observation" and state.tool_turns:
            turn = state.tool_turns[-1]
            receipt = tool_receipt(ToolObservation.model_validate(data), len(state.events) + 1)
            if self._active_call_id is None:
                # Batch preflight/protocol rejection: nothing was dispatched.
                complete_pending_turn(turn, receipt)
            elif self._active_call_id not in turn.tool_results:
                record_tool_result(turn, self._active_call_id, receipt)
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

    def _sync_usage(self, state: AgentState) -> None:
        """Project authoritative request reservations into Session diagnostics."""
        used = current_budget().usage.used - self._initial_usage
        state.llm_calls_used += used - self._run_llm_calls
        self._run_llm_calls = used

    def _preflight_batch(self, definition, registry, request, state, actions) -> AgentResult | None:
        """Validate the whole batch before its first side effect; recheck at dispatch."""
        for action in actions:
            if not registry.contains(action.tool):
                return self._failure(state, ErrorCode.INVALID_INPUT,
                                     f"unknown tool: {action.tool}", retryable=True)
            registry.validate(action.tool, action.arguments)
            try:
                permission = definition.permission_policy.check(action, state, request)
            except DeadlineExceededError:
                raise
            except (PermissionError, ValueError) as error:
                raise PermissionError(str(error)) from error
            except Exception as error:
                return self._failure(state, ErrorCode.CONTRACT_ERROR,
                                     f"permission policy failed: {error}", retryable=False)
            if permission.outcome == "deny":
                if permission.approval_id:
                    state.pending_action = None
                raise PermissionError(permission.reason or "Tool execution denied")
        return None

    def _cancel_pending_calls(self, state: AgentState, reason: str = "an earlier call stopped the batch") -> None:
        if state.tool_turns and len(state.tool_turns[-1].tool_results) < len(state.tool_turns[-1].tool_calls):
            complete_pending_turn(state.tool_turns[-1], tool_receipt(ToolObservation(
                ok=False, summary=f"Not executed: {reason}.",
            )))
            self._save(state)

    def _save(self, state: AgentState) -> None:
        state.updated_at = datetime.now(UTC)
        self.store.save(state)

    def _note_failure(
        self, state: AgentState, count: int
    ) -> AgentResult | None:
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
        """Build bounded history previews with their durable event sequence."""
        observations = [e for e in state.events if e.type == "observation"]
        if not observations:
            return None
        recent = observations[-limit:]
        lines: list[str] = []
        for event in recent:
            data = event.data if isinstance(event.data, dict) else {}
            summary = data.get("summary", "")
            ok = data.get("ok", True)
            lines.append(
                f"Event {event.sequence}. {event.tool}: {summary} "
                f"[{'ok' if ok else 'FAILED'}]"
                f"\n   Value preview: {_trim_json(data.get('value'), value_chars)}"
            )
        return ContextSection(
            name="recent_observations",
            content=(
                "Recent tool history (oldest first; session event sequence). "
                "Values are bounded history previews, not complete tool results. "
                "An ellipsis here means preview truncation; it does not change "
                "the original read result's truncated flag.\n" + "\n".join(lines)
            ),
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
        artifacts: list[ArtifactOutput] | None = None,
        report: str | None = None,
    ) -> AgentResult:
        error = ModuleError(
            code=code,
            message=message,
            retryable=retryable,
            details=details or {},
        )
        self._cancel_pending_calls(state, message)
        self._append_event(
            state,
            event_type="error",
            tool=None,
            data=error.model_dump(mode="json"),
        )
        state.status = SessionStatus.FAILED
        self._save(state)
        return AgentResult(
            status=ModuleStatus.FAILED,
            report=report or message,
            artifacts=artifacts or [],
            error=error,
            session=self._session_ref(state),
            llm_calls=self._run_llm_calls,
        )

    @staticmethod
    def _session_ref(state: AgentState) -> SessionRef:
        return SessionRef(
            id=state.session_id,
            module=state.owner,
            state_uri=f"session://{state.session_id}",
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
