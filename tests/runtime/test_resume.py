
from resagent2_contracts import AgentPermissions
from datetime import UTC, datetime

from resagent2_contracts import (AgentOwner, ModuleStatus, AgentRequest, TaskBudget, SessionStatus)
from resagent2_runtime import (
    AgentAction,
    AgentDefinition,
    AgentLoop,
    AllowListPermissionPolicy,
    AskUserTool,
    CompletionDecision,
    ContextSection,
    FinishTool,
    InMemorySessionStore,
    ScriptedLLMClient,
    AgentState,
)


def _context(request, state, max_context_tokens) -> list[ContextSection]:
    return [
        ContextSection(name="task", content=request.instruction, priority=100, required=True)
    ]


class _AcceptFinish:
    def evaluate(self, state, candidate) -> CompletionDecision:
        return CompletionDecision(
            complete=True,
            report="done",

        )


def _request(*, attempt: int, parent: str | None = None) -> AgentRequest:
    return AgentRequest(run_id='run_resume', task_id='task_experiment', attempt_number=attempt, agent=AgentOwner.CODING, instruction='Which dataset?', budget=TaskBudget(max_llm_calls=5, timeout_seconds=60), parent_session_id=parent, permissions=AgentPermissions(execute_commands=True, prepare_environment=True))


def test_ask_user_resume_reuses_session_and_resets_budget() -> None:
    store = InMemorySessionStore()
    loop = AgentLoop(store=store)
    definition = AgentDefinition(
        name="needs-input",
        owner=AgentOwner.SCIENTIFIC,
        system_prompt="Ask then finish.",
        tools=(AskUserTool(), FinishTool()),
        llm_client=ScriptedLLMClient(
            [
                AgentAction(
                    tool="ask_user",
                    arguments={
                        "text": "Which dataset?",
                        "requested_fields": ["dataset"],
                    },
                ),
                AgentAction(tool="finish", arguments={'report': '{"dataset": "demo"}'}),
            ]
        ),
        context_builder=_context,
        permission_policy=AllowListPermissionPolicy({"ask_user", "finish"}),
        completion_check=_AcceptFinish(),
    )

    first = loop.run(definition, _request(attempt=1), session_id="session_child")
    assert first.status == ModuleStatus.NEEDS_USER_INPUT
    assert first.session is not None and first.session.id == "session_child"

    resumed = loop.run(
        definition,
        _request(attempt=1, parent="session_child"),
        session_id="session_child",
    )

    assert resumed.status == ModuleStatus.COMPLETED
    assert resumed.session is not None and resumed.session.id == "session_child"
    assert resumed.report == "done"

    state = store.load("session_child")
    assert state.attempt_number == 1
    assert state.step == 2  # cumulative across resumed calls in this Attempt


def test_resume_rejects_another_attempt_without_mutating_checkpoint() -> None:
    store = InMemorySessionStore()
    now = datetime.now(UTC)
    state = AgentState(
        session_id="session_same_attempt",
        agent_name="finisher",
        owner=AgentOwner.SCIENTIFIC,
        run_id="run_resume",
        task_id="task_experiment",
        attempt_number=1,
        status=SessionStatus.PAUSED,
        created_at=now,
        updated_at=now,
    )
    store.save(state)
    client = ScriptedLLMClient([])
    definition = AgentDefinition(
        name="finisher", owner=AgentOwner.SCIENTIFIC,
        system_prompt="unused", tools=(FinishTool(),), llm_client=client,
        context_builder=_context,
        permission_policy=AllowListPermissionPolicy({"finish"}),
        completion_check=_AcceptFinish(),
    )
    result = AgentLoop(store=store).run(
        definition, _request(attempt=2, parent=state.session_id),
        session_id=state.session_id,
    )
    assert result.status == ModuleStatus.FAILED
    assert result.error.code.value == "contract_error"
    assert store.load(state.session_id) == state
    assert client.contexts == []


def test_resume_unknown_session_fails_cleanly() -> None:
    loop = AgentLoop(store=InMemorySessionStore())
    definition = AgentDefinition(
        name="needs-input",
        owner=AgentOwner.SCIENTIFIC,
        system_prompt="unused",
        tools=(FinishTool(),),
        llm_client=ScriptedLLMClient([]),
        context_builder=_context,
        permission_policy=AllowListPermissionPolicy({"finish"}),
        completion_check=_AcceptFinish(),
    )

    result = loop.run(
        definition,
        _request(attempt=2, parent="session_missing"),
        session_id="session_missing",
    )

    assert result.status == ModuleStatus.FAILED
    assert result.error is not None
    assert result.error.code.value == "contract_error"


def test_resume_rejects_non_paused_session() -> None:
    store = InMemorySessionStore()
    loop = AgentLoop(store=store)
    definition = AgentDefinition(
        name="finisher",
        owner=AgentOwner.SCIENTIFIC,
        system_prompt="finish only",
        tools=(FinishTool(),),
        llm_client=ScriptedLLMClient([AgentAction(tool="finish", arguments={'report': '{}'})]),
        context_builder=_context,
        permission_policy=AllowListPermissionPolicy({"finish"}),
        completion_check=_AcceptFinish(),
    )

    first = loop.run(definition, _request(attempt=1), session_id="session_done")
    assert first.status == ModuleStatus.COMPLETED

    result = loop.run(
        definition,
        _request(attempt=2, parent="session_done"),
        session_id="session_done",
    )

    assert result.status == ModuleStatus.FAILED
    assert result.error is not None
    assert result.error.code.value == "contract_error"


def test_resume_recovers_active_session_after_interruption() -> None:
    """ACTIVE is a persisted checkpoint after a process interruption, not a finish."""
    store = InMemorySessionStore()
    now = datetime.now(UTC)
    store.save(
        AgentState(
            session_id="session_interrupted",
            agent_name="finisher",
            owner=AgentOwner.SCIENTIFIC,
            run_id="run_resume",
            task_id="task_experiment",
            attempt_number=1,
            status=SessionStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
    )
    loop = AgentLoop(store=store)
    definition = AgentDefinition(
        name="finisher",
        owner=AgentOwner.SCIENTIFIC,
        system_prompt="finish only",
        tools=(FinishTool(),),
        llm_client=ScriptedLLMClient(
            [AgentAction(tool="finish", arguments={'report': '{}'})]
        ),
        context_builder=_context,
        permission_policy=AllowListPermissionPolicy({"finish"}),
        completion_check=_AcceptFinish(),
    )

    result = loop.run(
        definition,
        _request(attempt=1, parent="session_interrupted"),
        session_id="session_interrupted",
    )

    assert result.status == ModuleStatus.COMPLETED
    assert store.load("session_interrupted").status == SessionStatus.COMPLETED


def test_resume_rejects_mismatched_task() -> None:
    store = InMemorySessionStore()
    loop = AgentLoop(store=store)
    definition = AgentDefinition(
        name="needs-input",
        owner=AgentOwner.SCIENTIFIC,
        system_prompt="ask then finish",
        tools=(AskUserTool(), FinishTool()),
        llm_client=ScriptedLLMClient(
            [
                AgentAction(
                    tool="ask_user",
                    arguments={"text": "Which?", "requested_fields": ["x"]},
                ),
                AgentAction(tool="finish", arguments={'report': '{}'}),
            ]
        ),
        context_builder=_context,
        permission_policy=AllowListPermissionPolicy({"ask_user", "finish"}),
        completion_check=_AcceptFinish(),
    )

    first = loop.run(definition, _request(attempt=1), session_id="session_child")
    assert first.status == ModuleStatus.NEEDS_USER_INPUT

    other = AgentRequest(run_id='run_resume', task_id='task_other', attempt_number=2, agent=AgentOwner.CODING, instruction='Which dataset?', budget=TaskBudget(max_llm_calls=5, timeout_seconds=60), parent_session_id='session_child', permissions=AgentPermissions(execute_commands=True, prepare_environment=True))
    result = loop.run(definition, other, session_id="session_child")

    assert result.status == ModuleStatus.FAILED
    assert result.error is not None
    assert result.error.code.value == "contract_error"
