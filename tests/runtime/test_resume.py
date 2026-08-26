from resagent2_contracts import (
    AgentOwner,
    Capability,
    CodeUnderstandInput,
    ModuleStatus,
    ModuleTaskRequest,
    TaskBudget,
)
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
)


def _context(request, state) -> list[ContextSection]:
    return [
        ContextSection(name="task", content=request.goal, priority=100, required=True)
    ]


class _AcceptFinish:
    def evaluate(self, state, candidate) -> CompletionDecision:
        return CompletionDecision(
            complete=True,
            summary="done",
            payload=candidate.result if candidate else None,
        )


def _request(*, attempt: int, parent: str | None = None) -> ModuleTaskRequest:
    return ModuleTaskRequest(
        run_id="run_resume",
        task_id="task_experiment",
        attempt_number=attempt,
        capability=Capability.CODE_UNDERSTAND,
        goal="Pick a dataset",
        inputs=CodeUnderstandInput(question="Which dataset?"),
        budget=TaskBudget(max_steps=1, max_llm_calls=5, timeout_seconds=60),
        parent_session_id=parent,
    )


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
                        "reason": "No dataset selected.",
                    },
                ),
                AgentAction(tool="finish", arguments={"result": {"dataset": "demo"}}),
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
        _request(attempt=2, parent="session_child"),
        session_id="session_child",
    )

    assert resumed.status == ModuleStatus.COMPLETED
    assert resumed.session is not None and resumed.session.id == "session_child"
    assert resumed.payload == {"dataset": "demo"}

    state = store.load("session_child")
    assert state.attempt_number == 2
    assert state.step == 2  # cumulative across attempts, not reset


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
