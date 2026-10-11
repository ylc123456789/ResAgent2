"""Shared Agentic Loop and injectable profile definition."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Callable, Literal, Protocol
from uuid import uuid4

from pydantic import ValidationError

from resagent2_contracts import (
    AgentOwner,
    ActionSnapshot,
    AgentPermissions,
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
    QuestionDraft, PendingQuestion, ScientificAssessment, WorkRequest, WorkRequestDraft,
)

from .context import (
    DEFAULT_AGENT_CONTEXT_TOKENS, ContextBudgetExceeded, ContextComposer,
)
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
from .store import InMemorySessionStore, SessionLoadError, SessionStore
from .tools import Tool, ToolRegistry
from .tool_calling import (
    NativeToolCallError, complete_pending_turn, native_actions,
    contract_fingerprint, native_input_text, native_tool_schemas, tool_receipt,
    record_tool_result, recover_pending_turn,
)

_CONSECUTIVE_FAILURE_LIMIT = 5


class ContextBuilder(Protocol):
    """Build Agent-specific context sections from request and generic state."""

    def __call__(
        self,
        request: Any,
        state: AgentState,
        max_context_tokens: int,
    ) -> list[ContextSection]:
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
        """Validate the candidate and execution constraints, not task semantics."""


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
    max_context_tokens: int = DEFAULT_AGENT_CONTEXT_TOKENS
    acknowledge_registration: Callable[..., None] | None = None
    recover_registration: Callable[..., list] | None = None


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
        if not callable(getattr(definition.llm_client, "next_tool_call", None)):
            return AgentResult(
                status=ModuleStatus.FAILED,
                report="Agent client must implement native tool calls",
                error=ModuleError(
                    code=ErrorCode.CONTRACT_ERROR,
                    message="Agent client must implement native tool calls",
                    retryable=False,
                ),
            )
        protocol_key = getattr(definition.llm_client, "tool_session_key", None)
        if not isinstance(protocol_key, str) or not protocol_key.strip():
            return AgentResult(
                status=ModuleStatus.FAILED,
                report="native client must provide a stable tool_session_key",
                error=ModuleError(
                    code=ErrorCode.CONTRACT_ERROR,
                    message="native client must provide a stable tool_session_key",
                    retryable=False,
                ),
            )
        try:
            schemas = native_tool_schemas(definition.tools)
        except Exception as error:
            return AgentResult(status=ModuleStatus.FAILED, report=str(error), error=ModuleError(
                code=ErrorCode.CONTRACT_ERROR, message="tool schema construction failed: " + str(error), retryable=False,
            ))
        fingerprint = contract_fingerprint(definition.system_prompt, schemas)
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
            try:
                state = self.store.load(resume_id)
            except SessionLoadError as error:
                failure = ModuleError(
                    code=ErrorCode.CONTRACT_ERROR,
                    message=f"cannot resume session: {error}",
                    retryable=False,
                )
                return AgentResult(
                    status=ModuleStatus.FAILED,
                    report=failure.message,
                    error=failure,
                )
            if (
                state.status not in {SessionStatus.PAUSED, SessionStatus.ACTIVE, SessionStatus.COMPLETED, SessionStatus.FAILED}
                or state.run_id != request.run_id
                or state.task_id != request.task_id
                or state.attempt_number != request.attempt_number
                or state.owner != definition.owner
                or state.agent_name != definition.name
                or state.tool_protocol_key != protocol_key
                or state.contract_fingerprint != fingerprint
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
            if state.status in {SessionStatus.COMPLETED, SessionStatus.FAILED}:
                if state.terminal_result is None:
                    return AgentResult(status=ModuleStatus.FAILED, report="Terminal Session has no saved result",
                        error=ModuleError(code=ErrorCode.CONTRACT_ERROR,
                                          message="Terminal Session has no saved result", retryable=False))
                return state.terminal_result.model_copy(update={"llm_calls": 0})
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
                tool_protocol_key=protocol_key, contract_fingerprint=fingerprint,
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

        resumed = self._resume_control(definition, registry, request, state)
        if resumed is not None:
            return resumed
        interrupted = False
        for ordinal, turn in enumerate(state.tool_turns):
            if definition.acknowledge_registration is not None:
                # The Session receipt is the authority for this association.
                # A previous process may have stopped immediately before ack.
                for call_id in turn.tool_results:
                    definition.acknowledge_registration(
                        run_id=state.run_id,
                        operation_key=f"session:{state.session_id}:turn:{ordinal}:call:{call_id}",
                    )
            if len(turn.tool_results) < len(turn.tool_calls):
                operation_key = None
                if turn.executing_call_id is not None and definition.recover_registration is not None:
                    call_id = turn.executing_call_id
                    operation_key = f"session:{state.session_id}:turn:{ordinal}:call:{call_id}"
                    recovered = definition.recover_registration(run_id=state.run_id, operation_key=operation_key)
                    if recovered:
                        for ref in recovered:
                            if ref.run_id != state.run_id or ref.session_id != state.session_id:
                                raise ValueError("Recovered registration belongs to another invocation")
                            if ref.id not in state.authorized_artifact_ids:
                                state.authorized_artifact_ids.append(ref.id)
                        record_tool_result(turn, call_id, tool_receipt(ToolObservation(
                            ok=False,
                            summary="Materials were registered before interruption; the original tool result was not saved",
                            value={"artifact_ids": [ref.id for ref in recovered], "execution_status": "interrupted"},
                        )))
                recover_pending_turn(turn)
                self._save(state)
                if operation_key is not None and definition.acknowledge_registration is not None:
                    definition.acknowledge_registration(run_id=state.run_id, operation_key=operation_key)
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
                budgeter = getattr(definition.llm_client, "tool_input_limit", None)
                if budgeter is not None:
                    context_limit = min(context_limit, budgeter(context_limit))
                if context_limit < 1:
                    raise ContextBudgetExceeded("effective context limit must be positive")
                context = None
                context_error = None
                try:
                    context = self._compose_context(
                        definition, request, state, schemas, context_limit,
                    )
                except ContextBudgetExceeded as error:
                    context_error = error
                if callable(getattr(definition.llm_client, "summarize_history", None)):
                    checkpoint = state.history_checkpoint
                    plan = plan_compaction(
                        current_context=self._fixed_context(definition, request, state, context_limit),
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
                            definition, request, state, schemas, context_limit,
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
                reply = invoke_model(definition.llm_client, "next_tool_call", context, schemas,
                                     self._active_turns(state), max_input_tokens=context_limit)
                self._sync_usage(state)
                charged = True
                if self._run_llm_calls > request.budget.max_llm_calls:
                    return self._failure(state, ErrorCode.BUDGET_EXHAUSTED,
                                         "Client exceeded the allocated LLM-call budget", retryable=False)
                turn = ToolCallTurn.model_validate(reply)
                used_ids = {call.id for old in state.tool_turns for call in old.tool_calls}
                rejection = None
                if turn.tool_results or turn.executing_call_id is not None or turn.protocol_error is not None:
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
                    # Checkpoint before dispatch. Missing receipts on resume
                    # are unknown outcomes, never replay instructions.
                    self._save(state)
                actions = [AgentAction.model_validate(raw) for raw in native_actions(turn)]
                if len(actions) > 1:
                    failure = self._preflight_batch(definition, registry, request, state, actions)
                    if failure is not None:
                        return failure
            except (json.JSONDecodeError, ValidationError, NativeToolCallError, PermissionError) as error:
                if not charged:
                    self._sync_usage(state)
                if isinstance(error, json.JSONDecodeError):
                    # Count preceding transport retries as well. Invalid argument
                    # JSON never executes; calls remain in paired protocol history.
                    summary = (
                        f"Native tool arguments were not valid JSON: {error}. "
                        "Return native tool calls with JSON objects of arguments. "
                        "No tool was executed."
                    )
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
                self._active_call_id = turn.tool_calls[index].id
                state.current_operation_key = (
                    f"session:{state.session_id}:turn:{len(state.tool_turns) - 1}:call:{self._active_call_id}"
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
                    self._feedback(state, f"Unknown tool: {action.tool}. Use the provided tools.",
                                   tool=action.tool)
                    failure = self._note_failure(state, consecutive_failures)
                    if failure is not None:
                        return failure
                    consecutive_failures += 1
                    break

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
                        self._pause_approval(registry, request, state, action, permission)
                        return self._control_result(state)
                    else:
                        if permission.approval_id:
                            if state.pending_action is None or state.pending_action.action_id != permission.approval_id:
                                raise PermissionError("approval is not pending")
                            state.pending_action = None
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
                for ref in observation.artifacts:
                    if ref.run_id != state.run_id or ref.session_id != state.session_id:
                        return self._failure(state, ErrorCode.CONTRACT_ERROR,
                                             "Tool artifact belongs to another invocation", retryable=False)
                    if ref.id not in state.authorized_artifact_ids:
                        state.authorized_artifact_ids.append(ref.id)
                state.last_observation = observation

                if observation.question is not None or observation.request_work is not None:
                    if observation.request_work is not None and (
                        definition.owner != AgentOwner.SCIENTIFIC or not request.permissions.request_work
                    ):
                        return self._failure(state, ErrorCode.PERMISSION_DENIED,
                                             "This invocation may not request work", retryable=False)
                    self._accept_control(state, request, observation)
                    return self._control_result(state)

                is_finish = observation.finish_candidate is not None
                if not is_finish:
                    self._record_observation(state, action.tool, observation)
                    self._acknowledge_registration(definition, state)
                    if observation.ok:
                        consecutive_failures = 0
                    else:
                        failure = self._note_failure(state, consecutive_failures)
                        if failure is not None:
                            return failure
                        consecutive_failures += 1
                    if current_budget().expired:
                        return self._failure(state, ErrorCode.TIMEOUT,
                                             "Agent session exceeded timeout", retryable=True)

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

                if is_finish:
                    final_receipt = ToolObservation(
                        ok=decision.complete and decision.failure is None,
                        summary=decision.report or (
                            "Completion accepted" if decision.complete else "Completion rejected"
                        ),
                        value={"completion_check": "accepted" if decision.complete else "rejected",
                               "status": observation.finish_candidate.status.value},
                    )
                    self._record_observation(state, action.tool, final_receipt)

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
                    candidate = observation.finish_candidate
                    if candidate is not None and candidate.status == ModuleStatus.FAILED:
                        return self._failure(
                            state, ErrorCode.AGENT_REPORTED_FAILURE,
                            "Agent reported that the assigned work could not be completed",
                            retryable=False, artifacts=decision.artifacts,
                            report=decision.report or candidate.report,
                        )
                    self._cancel_pending_calls(state)
                    state.status = SessionStatus.COMPLETED
                    status = (
                        ModuleStatus.COMPLETED_WITH_WARNINGS
                        if decision.warnings
                        else ModuleStatus.COMPLETED
                    )
                    state.terminal_result = AgentResult(
                        status=status,
                        report=decision.report or "Completion check passed",
                        artifacts=decision.artifacts,
                        warnings=decision.warnings,
                        session=self._session_ref(state),
                        llm_calls=self._run_llm_calls,
                    )
                    self._save(state)
                    return state.terminal_result
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

    def _fixed_context(self, definition, request, state, context_limit):
        sections = list(definition.context_builder(request, state, context_limit))
        return self.context_composer.compose(definition.system_prompt, sections, max_tokens=context_limit)

    def _compose_context(self, definition, request, state, schemas, context_limit):
        context = self._fixed_context(definition, request, state, context_limit)
        if state.history_checkpoint is not None:
            context = context.model_copy(update={"history_summary": state.history_checkpoint.summary})
        tokens = self.context_composer.estimate_tokens(
            native_input_text(context, schemas, self._active_turns(state))
        )
        if tokens > context_limit:
            raise ContextBudgetExceeded("complete required context and native history exceed input budget")
        return context.model_copy(update={"estimated_tokens": tokens})

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
        self._compose_context(definition, request, proposed, schemas, context_limit)
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
                raise NativeToolCallError(f"Unknown tool: {action.tool}. Use the provided tools.")
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
        return {"last_observation": state.last_observation.model_dump(mode="json")} if state.last_observation else {}

    def _record_observation(self, state, tool, observation):
        state.last_observation = observation
        self._append_event(state, event_type="observation", tool=tool,
                           data=observation.model_dump(mode="json"))
        self._save(state)

    def _feedback(self, state, summary, *, tool=None, value=None) -> None:
        """A rejection is a historical receipt, never a second context projection."""
        observation = ToolObservation(summary=summary, value=value, ok=False)
        if self._active_call_id is None and (
            not state.tool_turns or len(state.tool_turns[-1].tool_results) == len(state.tool_turns[-1].tool_calls)
        ):
            state.tool_turns.append(ToolCallTurn(protocol_error=summary))
        self._record_observation(state, tool, observation)

    def _control_result(self, state) -> AgentResult:
        control = state.pending_control
        if control is None:
            raise ValueError("paused control is missing")
        return AgentResult(
            status=(ModuleStatus.REQUEST_WORK if control.action == "request_work"
                    else ModuleStatus.NEEDS_USER_INPUT),
            report=(state.last_observation.summary if state.last_observation else "Operation confirmation required"),
            control=control, session=self._session_ref(state), llm_calls=self._run_llm_calls,
        )

    def _accept_control(self, state, request, observation) -> None:
        """Save the stable control identity before saving its accepted receipt."""
        now = datetime.now(UTC)
        payload = {
            "source_session_id": state.session_id,
            "source_turn": len(state.tool_turns) - 1,
            "source_call_id": self._active_call_id,
        }
        assessment = observation.memory_updates.get("latest_assessment")
        if assessment is not None:
            payload["assessment"] = ScientificAssessment.model_validate(assessment)
        if observation.question is not None:
            control = ControlSignal(action="ask_user", question=PendingQuestion(
                id=f"question_{uuid4().hex}", run_id=request.run_id, session_id=state.session_id,
                task_id=request.task_id, attempt_number=request.attempt_number, created_at=now,
                **observation.question.model_dump(),
            ), **payload)
        else:
            work = WorkRequestDraft.model_validate(observation.request_work["work_request"])
            control = ControlSignal(action="request_work", work_request=WorkRequest(
                id=f"work_{uuid4().hex}", run_id=request.run_id,
                scientific_session_id=state.session_id, request=work, created_at=now, updated_at=now,
            ), **payload)
        state.pending_control = control
        state.control_history.append(control)
        state.status = SessionStatus.PAUSED
        self._save(state)
        self._record_control_receipt(state)

    def _record_control_receipt(self, state) -> None:
        control = state.pending_control
        turn = state.tool_turns[control.source_turn]
        self._active_call_id = control.source_call_id
        if self._active_call_id in turn.tool_results:
            return
        if control.action == "request_work":
            value = {"status": "accepted", "work_request_id": control.work_request.id}
            summary = "Work request accepted; results will appear under this work ID in the complete directory"
        else:
            value = {"status": "question_issued", "question_id": control.question.id}
            summary = "Question issued; the answer will be included in the complete task dialogue"
        self._record_observation(state, "request_work" if control.action == "request_work" else "ask_user",
                                 ToolObservation(summary=summary, value=value))

    def _pause_approval(self, registry, request, state, action, permission) -> None:
        state.pending_action = ActionSnapshot(
            action_id=f"action_{uuid4().hex}", tool=action.tool,
            arguments=registry.validate(action.tool, action.arguments).model_dump(mode="json"),
            context=permission.context, run_id=request.run_id, session_id=state.session_id,
            task_id=request.task_id, attempt_number=request.attempt_number,
        )
        now = datetime.now(UTC)
        question = PendingQuestion(
            id=f"question_{uuid4().hex}", run_id=request.run_id, session_id=state.session_id,
            task_id=request.task_id, attempt_number=request.attempt_number, created_at=now,
            text=(permission.reason + "\nTool: " + action.tool
                  + "\nArguments: " + json.dumps(state.pending_action.arguments, ensure_ascii=False)
                  + "\nContext: " + json.dumps({key: value for key, value in permission.context.items()
                                                if key != "deletion"}, ensure_ascii=False)),
            requested_fields=["approve"], options={"approve": ["yes", "no"]}, action=state.pending_action,
        )
        state.pending_control = ControlSignal(
            action="ask_user", source_session_id=state.session_id,
            source_turn=len(state.tool_turns) - 1, source_call_id=self._active_call_id, question=question,
        )
        state.control_history.append(state.pending_control)
        turn = state.tool_turns[-1]
        # No receipt for the pending operation. Every unstarted tail call receives
        # a final, truthful receipt now and will never be automatically replayed.
        for call in turn.tool_calls:
            if call.id != self._active_call_id and call.id not in turn.tool_results:
                record_tool_result(turn, call.id, tool_receipt(ToolObservation(
                    summary="Not executed: an earlier operation is awaiting approval", ok=False,
                    value={"execution_status": "not_executed"},
                )))
        turn.executing_call_id = None
        state.status = SessionStatus.PAUSED
        self._save(state)

    def _resume_control(self, definition, registry, request, state) -> AgentResult | None:
        control = state.pending_control
        if control is None:
            return None
        if control.source_session_id != state.session_id or control.source_turn >= len(state.tool_turns):
            return self._failure(state, ErrorCode.CONTRACT_ERROR, "Stored control has an invalid source", retryable=False)
        ack = request.control_ack
        if ack is not None and (ack.session_id, ack.turn, ack.call_id) != (
            control.source_session_id, control.source_turn, control.source_call_id
        ):
            return self._failure(state, ErrorCode.CONTRACT_ERROR, "Resume acknowledges a different control", retryable=False)
        if control.action == "request_work":
            self._record_control_receipt(state)
            available = any(result.work_request_id == control.work_request.id for result in request.work_results)
        else:
            answers = [answer for answer in request.resume_answers if answer.question_id == control.question.id]
            if any(
                (answer.question_text, answer.requested_fields, answer.options, answer.action,
                 answer.run_id, answer.session_id, answer.task_id, answer.attempt_number)
                != (control.question.text, control.question.requested_fields, control.question.options,
                    control.question.action, control.question.run_id, control.question.session_id,
                    control.question.task_id, control.question.attempt_number)
                for answer in answers
            ):
                return self._failure(state, ErrorCode.CONTRACT_ERROR,
                                     "Answer does not match the pending question", retryable=False)
            available = bool(answers)
            if control.question.action is None:
                self._record_control_receipt(state)
            elif available:
                return self._resume_approved_operation(definition, registry, request, state)
        if not available:
            state.status = SessionStatus.PAUSED
            self._save(state)
            return self._control_result(state)
        state.pending_control = None
        state.status = SessionStatus.ACTIVE
        self._active_call_id = None
        self._save(state)
        return None

    def _resume_approved_operation(self, definition, registry, request, state) -> AgentResult | None:
        pending = state.pending_action
        control = state.pending_control
        if pending is None or pending != control.question.action:
            return self._failure(state, ErrorCode.CONTRACT_ERROR, "Stored approval has no matching operation", retryable=False)
        action = AgentAction(tool=pending.tool, arguments=pending.arguments)
        self._active_call_id = control.source_call_id
        state.current_operation_key = (
            f"session:{state.session_id}:turn:{control.source_turn}:call:{control.source_call_id}"
        )
        try:
            registry.validate(action.tool, action.arguments)
            permission = definition.permission_policy.check(action, state, request)
            if permission.outcome == "allow" and permission.approval_id != pending.action_id:
                raise PermissionError("Permission policy did not consume the matching approval")
        except Exception as error:
            return self._failure(state, ErrorCode.CONTRACT_ERROR,
                                 "Cannot recheck approved operation: " + str(error), retryable=False)
        if permission.outcome == "ask":
            # The operation's target or environment changed after the question.
            # The old answer cannot authorize this newly checked snapshot.
            self._pause_approval(registry, request, state, action, permission)
            return self._control_result(state)
        state.pending_action = None
        state.pending_control = None
        state.status = SessionStatus.ACTIVE
        if permission.outcome == "deny":
            observation = ToolObservation(ok=False, summary=permission.reason or "Operation denied",
                                          value={"execution_status": "not_executed"})
        else:
            state.tool_turns[control.source_turn].executing_call_id = self._active_call_id
            self._save(state)
            try:
                current_budget().remaining_timeout()
                observation = registry.dispatch(action.tool, action.arguments, state, prepared=permission.prepared)
            except DeadlineExceededError as error:
                return self._failure(state, ErrorCode.TIMEOUT, str(error), retryable=False)
            except Exception as error:
                observation = ToolObservation(ok=False, summary="Approved operation failed: " + str(error))
        state.memory.update(observation.memory_updates)
        for ref in observation.artifacts:
            if ref.run_id != state.run_id or ref.session_id != state.session_id:
                return self._failure(state, ErrorCode.CONTRACT_ERROR,
                                     "Tool artifact belongs to another invocation", retryable=False)
            if ref.id not in state.authorized_artifact_ids:
                state.authorized_artifact_ids.append(ref.id)
        self._record_observation(state, action.tool, observation)
        self._acknowledge_registration(definition, state)
        self._active_call_id = None
        return None

    def _acknowledge_registration(self, definition, state) -> None:
        if definition.acknowledge_registration is not None and self._active_call_id is not None:
            definition.acknowledge_registration(
                run_id=state.run_id,
                operation_key=f"session:{state.session_id}:turn:{len(state.tool_turns) - 1}:call:{self._active_call_id}",
            )

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
        state.terminal_result = AgentResult(
            status=ModuleStatus.FAILED,
            report=report or message,
            artifacts=artifacts or [],
            error=error,
            session=self._session_ref(state),
            llm_calls=self._run_llm_calls,
        )
        self._save(state)
        return state.terminal_result

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
