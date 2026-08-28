import pytest

from resagent2_contracts import (
    AgentOwner,
    Capability,
    CodeUnderstandInput,
    ErrorCode,
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
    ContextComposer,
    ContextBudgetExceeded,
    ContextSection,
    FinishCandidate,
    FinishTool,
    InMemorySessionStore,
    ScriptedLLMClient,
    WriteValueTool,
)


class NeverFinish:
    def evaluate(self, state, candidate: FinishCandidate | None) -> CompletionDecision:
        return CompletionDecision(complete=False)


def context_builder(request, state) -> list[ContextSection]:
    return [ContextSection(name="goal", content=request.goal, required=True)]


def request(*, max_steps: int = 2, timeout_seconds: int = 60) -> ModuleTaskRequest:
    return ModuleTaskRequest(
        run_id="run_guard",
        task_id="task_guard",
        attempt_number=1,
        capability=Capability.CODE_UNDERSTAND,
        goal="Verify runtime boundaries",
        inputs=CodeUnderstandInput(question="Can the action run?"),
        budget=TaskBudget(
            max_steps=max_steps,
            max_llm_calls=max_steps,
            timeout_seconds=timeout_seconds,
        ),
    )


def definition(actions, *, allowed_tools: set[str]) -> AgentDefinition:
    return AgentDefinition(
        name="guard-test",
        owner=AgentOwner.CODING,
        system_prompt="Test runtime guards.",
        tools=(WriteValueTool(), FinishTool()),
        llm_client=ScriptedLLMClient(actions),
        context_builder=context_builder,
        permission_policy=AllowListPermissionPolicy(allowed_tools),
        completion_check=NeverFinish(),
    )


def test_permission_is_checked_before_tool_execution() -> None:
    store = InMemorySessionStore()
    result = AgentLoop(store=store).run(
        definition(
            [
                AgentAction(
                    tool="write_value",
                    arguments={"key": "forbidden", "value": True},
                )
            ],
            allowed_tools=set(),
        ),
        request(),
        session_id="session_denied",
    )

    assert result.status == ModuleStatus.FAILED
    assert result.error is not None
    assert result.error.code == ErrorCode.PERMISSION_DENIED
    assert "forbidden" not in store.load("session_denied").memory


def test_tool_arguments_are_validated_before_execution() -> None:
    store = InMemorySessionStore()
    result = AgentLoop(store=store).run(
        definition(
            [AgentAction(tool="write_value", arguments={"key": "missing-value"})],
            allowed_tools={"write_value"},
        ),
        request(),
        session_id="session_invalid_input",
    )

    assert result.status == ModuleStatus.FAILED
    assert result.error is not None
    # Invalid arguments are recoverable within the loop. This scripted client
    # has no corrective action left, so its exhaustion becomes TOOL_FAILED while
    # the original actionable rejection remains persisted for diagnosis.
    assert result.error.code == ErrorCode.TOOL_FAILED
    assert store.load("session_invalid_input").memory == {}
    assert store.load("session_invalid_input").runtime_feedback is not None


def test_llm_action_must_match_the_typed_action_schema() -> None:
    result = AgentLoop(store=InMemorySessionStore()).run(
        definition(
            [
                {
                    "tool": "finish",
                    "arguments": {"result": {}},
                    "undocumented_field": True,
                }
            ],
            allowed_tools={"finish"},
        ),
        request(),
        session_id="session_invalid_action",
    )

    assert result.status == ModuleStatus.FAILED
    assert result.error is not None
    assert result.error.code == ErrorCode.INVALID_INPUT
    assert result.error.details["validation_errors"][0]["loc"] == [
        "undocumented_field"
    ]


def test_rejected_finish_exhausts_budget_instead_of_completing() -> None:
    result = AgentLoop(store=InMemorySessionStore()).run(
        definition(
            [
                AgentAction(
                    tool="finish",
                    arguments={"proposed_status": "completed", "result": {}},
                )
            ],
            allowed_tools={"finish"},
        ),
        request(max_steps=1),
        session_id="session_rejected_finish",
    )

    assert result.status == ModuleStatus.FAILED
    assert result.error is not None
    assert result.error.code == ErrorCode.BUDGET_EXHAUSTED


def test_state_is_saved_incrementally_for_each_step() -> None:
    store = InMemorySessionStore()
    AgentLoop(store=store).run(
        definition(
            [
                AgentAction(
                    tool="write_value",
                    arguments={"key": "saved", "value": True},
                ),
                AgentAction(tool="finish", arguments={"result": {}}),
            ],
            allowed_tools={"write_value", "finish"},
        ),
        request(max_steps=2),
        session_id="session_persisted",
    )

    history = store.history("session_persisted")
    assert len(history) >= 5
    assert any(item.memory.get("saved") is True for item in history)
    assert history[-1].status.value == "failed"


def test_timeout_is_returned_as_structured_error() -> None:
    ticks = iter([0.0, 10.0])
    loop = AgentLoop(store=InMemorySessionStore(), clock=lambda: next(ticks))

    result = loop.run(
        definition([], allowed_tools=set()),
        request(timeout_seconds=5),
        session_id="session_timeout",
    )

    assert result.status == ModuleStatus.FAILED
    assert result.error is not None
    assert result.error.code == ErrorCode.TIMEOUT


def test_context_composer_respects_total_budget_and_priority() -> None:
    composer = ContextComposer()
    context = composer.compose(
        "system",
        [
            ContextSection(name="required", content="must keep", required=True),
            ContextSection(name="high", content="important", priority=10),
            ContextSection(name="low", content="x" * 100, priority=1),
        ],
        max_tokens=12,
    )

    assert context.included_sections == ["system", "required", "high"]
    assert context.omitted_sections == ["low"]


def test_required_context_cannot_silently_overflow_budget() -> None:
    with pytest.raises(ContextBudgetExceeded, match="required"):
        ContextComposer().compose(
            "system prompt that is too long",
            [],
            max_tokens=1,
        )
