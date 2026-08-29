from pydantic import BaseModel

from resagent2_contracts import (
    AgentOwner,
    Capability,
    CodeModifyInput,
    CodeUnderstandInput,
    ErrorCode,
    ModuleError,
    ModuleStatus,
    ModuleTaskRequest,
    TaskBudget,
)
from resagent2_runtime import (
    AgentAction,
    AgentDefinition,
    AgentLoop,
    AllowListPermissionPolicy,
    CompletionDecision,
    ContextSection,
    FinishCandidate,
    FinishTool,
    InMemorySessionStore,
    ReadValueTool,
    ScriptedLLMClient,
    WriteValueTool,
    AskUserTool,
)


class AcceptFinish:
    """Test finalizer that accepts only an explicit finish candidate."""

    def evaluate(self, state, candidate: FinishCandidate | None) -> CompletionDecision:
        if candidate is None:
            return CompletionDecision(complete=False)
        return CompletionDecision(
            complete=True,
            summary="Deterministic completion check passed",
            payload=candidate.result,
        )


class NeverFinish:
    """Test finalizer that rejects every proposed finish."""

    def evaluate(self, state, candidate: FinishCandidate | None) -> CompletionDecision:
        return CompletionDecision(complete=False)


class VerifiedResult(BaseModel):
    value: int


def build_context(request, state) -> list[ContextSection]:
    return [
        ContextSection(
            name="task",
            content=f"Goal: {request.goal}",
            priority=100,
            required=True,
        ),
        ContextSection(
            name="memory",
            content=str(state.memory),
            priority=50,
        ),
    ]


def request(capability: Capability) -> ModuleTaskRequest:
    if capability == Capability.CODE_MODIFY:
        inputs = CodeModifyInput(instructions="Store the verified value")
    else:
        inputs = CodeUnderstandInput(question="Read the reference value")
    return ModuleTaskRequest(
        run_id="run_runtime",
        task_id="task_runtime",
        attempt_number=1,
        capability=capability,
        goal="Exercise the shared loop",
        inputs=inputs,
        budget=TaskBudget(max_steps=5, max_llm_calls=5, timeout_seconds=60),
    )


def definition(
    *,
    name: str,
    llm: ScriptedLLMClient,
    tools: tuple,
    allowed_tools: set[str],
    completion_check=None,
    result_type=None,
) -> AgentDefinition:
    return AgentDefinition(
        name=name,
        owner=AgentOwner.CODING,
        system_prompt="Follow the typed task and use only the provided tools.",
        tools=tools,
        llm_client=llm,
        context_builder=build_context,
        permission_policy=AllowListPermissionPolicy(allowed_tools),
        completion_check=completion_check or AcceptFinish(),
        result_type=result_type,
    )


def test_same_loop_runs_read_only_and_writable_profiles() -> None:
    store = InMemorySessionStore()
    loop = AgentLoop(store=store)
    read_definition = definition(
        name="reader",
        llm=ScriptedLLMClient(
            [
                AgentAction(tool="read_value", arguments={"key": "answer"}),
                AgentAction(
                    tool="finish",
                    arguments={
                        "proposed_status": "failed",
                        "result": {"answer": 42},
                    },
                ),
            ]
        ),
        tools=(ReadValueTool(), FinishTool()),
        allowed_tools={"read_value", "finish"},
    )
    write_definition = definition(
        name="writer",
        llm=ScriptedLLMClient(
            [
                AgentAction(
                    tool="write_value",
                    arguments={"key": "verified", "value": True},
                ),
                AgentAction(
                    tool="finish",
                    arguments={"result": {"written": True}},
                ),
            ]
        ),
        tools=(WriteValueTool(), FinishTool()),
        allowed_tools={"write_value", "finish"},
    )

    read_result = loop.run(
        read_definition,
        request(Capability.CODE_UNDERSTAND),
        session_id="session_reader",
        initial_memory={"answer": 42},
    )
    write_result = loop.run(
        write_definition,
        request(Capability.CODE_MODIFY),
        session_id="session_writer",
    )

    assert read_result.status == ModuleStatus.COMPLETED
    assert read_result.payload == {"answer": 42}
    assert write_result.status == ModuleStatus.COMPLETED
    assert store.load("session_writer").memory["verified"] is True
    assert type(loop) is AgentLoop


def test_ask_user_returns_signal_without_reading_a_terminal() -> None:
    store = InMemorySessionStore()
    loop = AgentLoop(store=store)
    ask_definition = definition(
        name="needs-input",
        llm=ScriptedLLMClient(
            [
                AgentAction(
                    tool="ask_user",
                    arguments={
                        "text": "Which dataset should be used?",
                        "requested_fields": ["dataset"],
                        "reason": "The task has no dataset selection.",
                    },
                )
            ]
        ),
        tools=(AskUserTool(),),
        allowed_tools={"ask_user"},
        completion_check=NeverFinish(),
    )

    result = loop.run(
        ask_definition,
        request(Capability.CODE_UNDERSTAND),
        session_id="session_question",
    )

    assert result.status == ModuleStatus.NEEDS_USER_INPUT
    assert result.question is not None
    assert result.question.requested_fields == ["dataset"]
    assert result.session is not None
    assert result.session.status.value == "paused"


def test_finalizer_does_not_trust_llm_proposed_status() -> None:
    loop = AgentLoop(store=InMemorySessionStore())
    accepted = definition(
        name="accepted",
        llm=ScriptedLLMClient(
            [
                AgentAction(
                    tool="finish",
                    arguments={
                        "proposed_status": "failed",
                        "result": {"valid": True},
                    },
                )
            ]
        ),
        tools=(FinishTool(),),
        allowed_tools={"finish"},
    )

    result = loop.run(
        accepted,
        request(Capability.CODE_UNDERSTAND),
        session_id="session_finalizer",
    )

    assert result.status == ModuleStatus.COMPLETED


def test_final_payload_must_match_profile_result_schema() -> None:
    loop = AgentLoop(store=InMemorySessionStore())
    profile = definition(
        name="typed-result",
        llm=ScriptedLLMClient(
            [AgentAction(tool="finish", arguments={"result": {"wrong": True}})]
        ),
        tools=(FinishTool(),),
        allowed_tools={"finish"},
        result_type=VerifiedResult,
    )

    result = loop.run(
        profile,
        request(Capability.CODE_UNDERSTAND),
        session_id="session_typed_result",
    )

    assert result.status == ModuleStatus.FAILED
    assert result.error is not None
    assert result.error.code.value == "contract_error"


class RequireWriteBeforeFinish:
    """Reject finish until memory has a ``ready`` key, with an actionable summary."""

    def evaluate(self, state, candidate: FinishCandidate | None) -> CompletionDecision:
        if candidate is None:
            return CompletionDecision(complete=False)
        if not state.memory.get("ready"):
            return CompletionDecision(
                complete=False,
                summary="Run the write_value tool before finishing",
            )
        return CompletionDecision(complete=True, summary="done", payload=candidate.result)


def test_completion_rejected_then_corrective_action_then_finish_succeeds() -> None:
    store = InMemorySessionStore()
    loop = AgentLoop(store=store)
    profile = definition(
        name="recover-finish",
        llm=ScriptedLLMClient(
            [
                AgentAction(tool="finish", arguments={"result": {"ok": True}}),
                AgentAction(tool="write_value", arguments={"key": "ready", "value": True}),
                AgentAction(tool="finish", arguments={"result": {"ok": True}}),
            ]
        ),
        tools=(WriteValueTool(), FinishTool()),
        allowed_tools={"write_value", "finish"},
        completion_check=RequireWriteBeforeFinish(),
    )

    result = loop.run(
        profile,
        request(Capability.CODE_MODIFY),
        session_id="session_recover_finish",
    )

    assert result.status == ModuleStatus.COMPLETED
    assert store.load("session_recover_finish").memory["ready"] is True


def test_runtime_feedback_survives_an_intervening_observation() -> None:
    """A rejection must remain visible even after an ordinary observation
    (read_value) overwrites last_observation; the durable slot is separate."""
    store = InMemorySessionStore()
    loop = AgentLoop(store=store)
    llm = ScriptedLLMClient(
        [
            AgentAction(tool="finish", arguments={"result": {"ok": True}}),
            AgentAction(tool="read_value", arguments={"key": "missing"}),
            AgentAction(tool="write_value", arguments={"key": "ready", "value": True}),
            AgentAction(tool="finish", arguments={"result": {"ok": True}}),
        ]
    )
    profile = definition(
        name="feedback-persists",
        llm=llm,
        tools=(ReadValueTool(), WriteValueTool(), FinishTool()),
        allowed_tools={"read_value", "write_value", "finish"},
        completion_check=RequireWriteBeforeFinish(),
    )

    result = loop.run(
        profile,
        request(Capability.CODE_MODIFY),
        session_id="session_feedback_persists",
    )

    assert result.status == ModuleStatus.COMPLETED
    assert "runtime_feedback" in llm.contexts[1].included_sections
    assert "runtime_feedback" in llm.contexts[2].included_sections
    assert "runtime_feedback" in llm.contexts[3].included_sections
    assert "Run the write_value tool before finishing" in llm.contexts[2].text
    assert store.load("session_feedback_persists").runtime_feedback is None


def test_recoverable_tool_error_sets_feedback_and_continues() -> None:
    """A Tool that raises (recoverable) must not kill the session immediately:
    it becomes runtime_feedback and the next scripted action still runs."""
    from resagent2_runtime import ToolObservation

    class FailingOnceTool:
        name = "flaky"
        input_model = FinishTool.input_model  # reuse a valid schema

        def __init__(self) -> None:
            self.calls = 0

        def execute(self, state, arguments):
            self.calls += 1
            if self.calls == 1:
                raise ValueError("transient failure")
            return ToolObservation(summary="recovered", value={"ok": True})

    flaky = FailingOnceTool()
    store = InMemorySessionStore()
    loop = AgentLoop(store=store)
    profile = definition(
        name="recover-tool",
        llm=ScriptedLLMClient(
            [
                AgentAction(tool="flaky", arguments={"result": {"ok": True}}),
                AgentAction(tool="flaky", arguments={"result": {"ok": True}}),
                AgentAction(tool="finish", arguments={"result": {"ok": True}}),
            ]
        ),
        tools=(flaky, FinishTool()),
        allowed_tools={"flaky", "finish"},
    )

    result = loop.run(
        profile,
        request(Capability.CODE_MODIFY),
        session_id="session_recover_tool",
    )

    assert result.status == ModuleStatus.COMPLETED
    assert flaky.calls == 2
    # The first failure was recorded as durable feedback before the recovery.
    assert store.load("session_recover_tool").runtime_feedback is None


def test_consecutive_failures_stop_before_budget() -> None:
    from resagent2_runtime import ToolObservation

    class AlwaysFailTool:
        name = "always_fail"
        input_model = FinishTool.input_model

        def execute(self, state, arguments):
            return ToolObservation(summary="failed", ok=False, value={})

    store = InMemorySessionStore()
    loop = AgentLoop(store=store)
    profile = definition(
        name="always-fail",
        llm=ScriptedLLMClient(
            [AgentAction(tool="always_fail", arguments={"result": {}})] * 50
        ),
        tools=(AlwaysFailTool(), FinishTool()),
        allowed_tools={"always_fail", "finish"},
    )
    req = ModuleTaskRequest(
        run_id="run_x",
        task_id="task_x",
        attempt_number=1,
        capability=Capability.CODE_MODIFY,
        goal="g",
        inputs=CodeModifyInput(instructions="i"),
        budget=TaskBudget(max_steps=50, max_llm_calls=50, timeout_seconds=60),
    )
    result = loop.run(profile, req, session_id="session_fail")

    assert result.status == ModuleStatus.FAILED
    assert result.error.code == ErrorCode.TOOL_FAILED
    # It stopped at the recoverable-failure limit, not by exhausting 50 steps.
    assert store.load("session_fail").step < 50


def test_recent_observations_are_injected() -> None:
    store = InMemorySessionStore()
    loop = AgentLoop(store=store)
    llm = ScriptedLLMClient(
        [
            AgentAction(tool="write_value", arguments={"key": "a", "value": 1}),
            AgentAction(tool="finish", arguments={"result": {"ok": True}}),
        ]
    )
    profile = definition(
        name="recent",
        llm=llm,
        tools=(WriteValueTool(), FinishTool()),
        allowed_tools={"write_value", "finish"},
    )
    loop.run(profile, request(Capability.CODE_MODIFY), session_id="session_recent")

    # The second turn's context carries the recent tool history.
    assert "recent_observations" in llm.contexts[1].included_sections


class RejectWithReason:
    """Reject every proposed finish with an actionable summary."""

    def evaluate(self, state, candidate: FinishCandidate | None) -> CompletionDecision:
        return CompletionDecision(
            complete=False, summary="Missing required evidence; keep working"
        )


def test_completion_rejection_counts_as_failure() -> None:
    """A model that keeps proposing finish while the completion check keeps
    rejecting must be stopped by the consecutive-failure guard, not the budget."""
    store = InMemorySessionStore()
    loop = AgentLoop(store=store)
    profile = definition(
        name="reject-finish",
        llm=ScriptedLLMClient(
            [AgentAction(tool="finish", arguments={"result": {"ok": True}})] * 50
        ),
        tools=(FinishTool(),),
        allowed_tools={"finish"},
        completion_check=RejectWithReason(),
    )
    req = ModuleTaskRequest(
        run_id="run_x",
        task_id="task_x",
        attempt_number=1,
        capability=Capability.CODE_MODIFY,
        goal="g",
        inputs=CodeModifyInput(instructions="i"),
        budget=TaskBudget(max_steps=50, max_llm_calls=50, timeout_seconds=60),
    )
    result = loop.run(profile, req, session_id="session_reject_finish")

    assert result.status == ModuleStatus.FAILED
    assert result.error.code == ErrorCode.TOOL_FAILED
    state = store.load("session_reject_finish")
    assert state.step < 50
    # Every rejection was persisted as a failed observation, not ok=True.
    rejections = [
        event.data for event in state.events
        if event.type == "observation" and event.tool == "completion_check"
    ]
    assert rejections
    assert all(not data.get("ok") for data in rejections if isinstance(data, dict))


def test_recent_observations_preserve_error_bodies() -> None:
    """Two distinct command errors must both remain readable in the next turn,
    even when a long value forces the recent-history trim."""
    from resagent2_runtime import ToolObservation

    pad = "x" * 600
    errors = ["AlphaError: alpha broke", "BravoError: bravo broke"]

    class FailingTool:
        name = "flaky"
        input_model = FinishTool.input_model

        def __init__(self) -> None:
            self.calls = 0

        def execute(self, state, arguments):
            err = errors[min(self.calls, len(errors) - 1)]
            self.calls += 1
            return ToolObservation(
                summary="command failed",
                ok=False,
                value={"stdout_tail": pad, "stderr_tail": err},
            )

    llm = ScriptedLLMClient(
        [
            AgentAction(tool="flaky", arguments={"result": {}}),
            AgentAction(tool="flaky", arguments={"result": {}}),
            AgentAction(tool="flaky", arguments={"result": {}}),
        ]
    )
    profile = definition(
        name="errors",
        llm=llm,
        tools=(FailingTool(), FinishTool()),
        allowed_tools={"flaky", "finish"},
        completion_check=NeverFinish(),
    )
    loop = AgentLoop(store=InMemorySessionStore())
    loop.run(profile, request(Capability.CODE_MODIFY), session_id="session_errors")

    assert "recent_observations" in llm.contexts[2].included_sections
    assert errors[0] in llm.contexts[2].text
    assert errors[1] in llm.contexts[2].text


class VerifiedFailure:
    """Finalizer that returns a deterministic verified failure (not completion)."""

    def evaluate(self, state, candidate: FinishCandidate | None) -> CompletionDecision:
        return CompletionDecision(
            complete=False,
            failure=ModuleError(
                code=ErrorCode.TOOL_FAILED,
                message="Experiment command failed with exit code 1",
                retryable=False,
                details={
                    "command": "python train.py",
                    "exit_code": 1,
                    "stderr_tail": "NameError: totla",
                },
            ),
        )


def test_deterministic_failure_exit_returns_failed() -> None:
    store = InMemorySessionStore()
    profile = definition(
        name="verified-failure",
        llm=ScriptedLLMClient(
            [
                AgentAction(
                    tool="finish",
                    arguments={"proposed_status": "failed", "result": {}},
                )
            ]
        ),
        tools=(FinishTool(),),
        allowed_tools={"finish"},
        completion_check=VerifiedFailure(),
    )

    result = AgentLoop(store=store).run(
        profile,
        request(Capability.CODE_MODIFY),
        session_id="session_verified_failure",
    )

    assert result.status == ModuleStatus.FAILED
    assert result.error is not None
    assert result.error.code == ErrorCode.TOOL_FAILED
    assert result.error.message == "Experiment command failed with exit code 1"
    assert result.error.details["stderr_tail"] == "NameError: totla"
    assert store.load("session_verified_failure").status.value == "failed"


def test_failure_details_keep_both_feedback_and_observation() -> None:
    """An older completion rejection must not mask the newest command failure."""
    from datetime import UTC, datetime

    from resagent2_runtime import ToolObservation
    from resagent2_runtime.models import AgentState

    now = datetime.now(UTC)
    state = AgentState(
        session_id="session_details",
        agent_name="test",
        owner=AgentOwner.CODING,
        run_id="run_details",
        created_at=now,
        updated_at=now,
    )
    state.runtime_feedback = ToolObservation(
        summary="Run a successful experiment command before finishing", ok=False
    )
    state.last_observation = ToolObservation(
        summary="Command exited with code 1",
        ok=False,
        value={
            "command": "python train.py",
            "exit_code": 1,
            "stderr_tail": "NameError: totla",
        },
    )

    details = AgentLoop._failure_details(state)

    assert "runtime_feedback" in details
    assert "last_observation" in details
    assert details["last_observation"]["value"]["stderr_tail"] == "NameError: totla"
