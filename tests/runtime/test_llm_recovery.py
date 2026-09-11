"""Malformed output uses existing bounded feedback, not a new Task Attempt."""

import json
from datetime import UTC, datetime
from unittest import mock
from urllib.error import URLError

import pytest

from resagent2_contracts import (
    AgentOwner,
    Capability,
    CodeUnderstandInput,
    ErrorCode,
    ModuleStatus,
    ModuleTaskRequest,
    ResearchRequest,
    RunBudget,
    RunStatus,
    TaskProposal,
    TaskStatus,
    TaskBudget,
    WorkflowProposal,
)
from resagent2_orchestrator import (
    InMemoryRunStore,
    ModuleBinding,
    ResearchRun,
    WorkflowScheduler,
)
from resagent2_runtime import (
    AgentDefinition,
    AgentLoop,
    AllowListPermissionPolicy,
    CompletionDecision,
    ContextSection,
    FinishCandidate,
    FinishTool,
    InMemorySessionStore,
    OpenAICompatibleClient,
    WriteValueTool,
)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class _AcceptFinish:
    def evaluate(self, state, candidate: FinishCandidate | None) -> CompletionDecision:
        if candidate is None:
            return CompletionDecision(complete=False)
        return CompletionDecision(complete=True, summary="done", payload=candidate.result)


def _context(request, state) -> list[ContextSection]:
    return [
        ContextSection(name="task", content=request.goal, priority=100, required=True)
    ]


@pytest.mark.parametrize("call_budget, expected_code, calls", [
    (50, ErrorCode.TOOL_FAILED, 5),
    (2, ErrorCode.BUDGET_EXHAUSTED, 2),
])
def test_bad_json_stops_at_existing_limits(
    monkeypatch, tmp_path, call_budget, expected_code, calls,
) -> None:
    monkeypatch.setenv("TEST_LLM_KEY", "dummy")
    client = OpenAICompatibleClient(
        model="test-model",
        api_base="https://example.com/v1",
        api_key_env="TEST_LLM_KEY",
        trace_dir=tmp_path / "traces",
        trace_level="full",
    )
    bad = _FakeResponse({"choices": [{"message": {"content": "not valid json"}}]})
    definition = AgentDefinition(
        name="recovery",
        owner=AgentOwner.CODING,
        system_prompt="Use the provided tools only.",
        tools=(FinishTool(),),
        llm_client=client,
        context_builder=_context,
        permission_policy=AllowListPermissionPolicy({"finish"}),
        completion_check=_AcceptFinish(),
    )
    request = ModuleTaskRequest(
        run_id="run_r",
        task_id="task_r",
        attempt_number=1,
        capability=Capability.CODE_UNDERSTAND,
        goal="exercise recovery",
        inputs=CodeUnderstandInput(question="q"),
        budget=TaskBudget(max_steps=50, max_llm_calls=call_budget, timeout_seconds=60),
    )

    with (
        mock.patch("resagent2_runtime.llm.time.sleep"),
        mock.patch("resagent2_runtime.llm.urlopen", return_value=bad),
    ):
        result = AgentLoop(store=InMemorySessionStore()).run(
            definition, request, session_id="session_recovery"
        )

    assert result.status == ModuleStatus.FAILED
    assert result.error is not None
    assert result.error.code == expected_code
    assert result.error.retryable is False
    assert result.llm_calls == calls
    assert client.last_attempts == 1
    assert "not valid JSON" in result.error.details["runtime_feedback"]["summary"]

    trace_file = tmp_path / "traces" / "llm_traces.jsonl"
    record = json.loads(trace_file.read_text(encoding="utf-8").splitlines()[-1])
    assert record["raw_response_text"] == "not valid json"
    assert record["action_valid"] is False
    assert len(trace_file.read_text().splitlines()) == calls


class _LoopPort:
    def __init__(self, definition: AgentDefinition) -> None:
        self.definition = definition
        self.loop = AgentLoop(store=InMemorySessionStore())
        self.requests: list[ModuleTaskRequest] = []

    def invoke(self, request: ModuleTaskRequest):
        self.requests.append(request)
        return self.loop.run(
            self.definition, request, session_id=f"session_{request.attempt_number}"
        )


@pytest.mark.parametrize("network_failure", [False, True])
def test_scheduler_keeps_attempt_for_json_but_retries_transport(
    monkeypatch, tmp_path, network_failure,
) -> None:
    """Correct JSON in-place; unchanged transport exhaustion retries the Task."""
    monkeypatch.setenv("TEST_LLM_KEY", "dummy")
    client = OpenAICompatibleClient(
        model="test-model",
        api_base="https://example.com/v1",
        api_key_env="TEST_LLM_KEY",
        trace_dir=tmp_path / "traces",
        trace_level="full",
    )
    definition = AgentDefinition(
        name="recovery",
        owner=AgentOwner.CODING,
        system_prompt="Use the provided tools only.",
        tools=(FinishTool(),),
        llm_client=client,
        context_builder=_context,
        permission_policy=AllowListPermissionPolicy({"finish"}),
        completion_check=_AcceptFinish(),
    )
    port = _LoopPort(definition)
    store = InMemoryRunStore()
    engine = WorkflowScheduler(
        bindings={
            Capability.CODE_UNDERSTAND: ModuleBinding(
                owner=AgentOwner.CODING,
                port=port,
            )
        },
        store=store,
        data_root=tmp_path / "data",
    )
    request = ResearchRequest(
        goal="exercise retry recovery",
        budget=RunBudget(
            max_tasks=1,
            max_attempts_per_task=2,
            max_llm_calls=10,
            timeout_seconds=60,
        ),
    )
    now = datetime.now(UTC)
    store.save(
        ResearchRun(
            run_id="run_recovery_chain",
            request=request,
            status=RunStatus.RUNNING,
            created_at=now,
            updated_at=now,
        )
    )
    engine.accept_proposal(
        "run_recovery_chain",
        WorkflowProposal(
            work_request_id="work_legacy_initial",
            summary="recover after malformed provider content",
            compilation_rationale="exercise the bounded retry chain",
            tasks=[
                TaskProposal(
                    id="task_recovery",
                    work_request_id="work_legacy_initial",
                    capability=Capability.CODE_UNDERSTAND,
                    goal="Finish after a provider failure",
                    inputs=CodeUnderstandInput(question="q"),
                )
            ],
        ),
    )
    bad = _FakeResponse({"choices": [{"message": {"content": "not valid json"}}]})
    if network_failure:
        bad = URLError("unavailable")
    valid = _FakeResponse(
        {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"tool": "finish", "arguments": {"result": {
                                "answer": "Recovered", "evidence_files": ["train.py"],
                            }}}
                        )
                    }
                }
            ]
        }
    )

    with (
        mock.patch("resagent2_runtime.llm.time.sleep"),
        mock.patch(
            "resagent2_runtime.llm.urlopen",
            side_effect=[bad, bad, bad, valid],
        ),
    ):
        run = engine.run_until_stable("run_recovery_chain")

    workflow_task = run.workflow.tasks[0]
    assert workflow_task.status == TaskStatus.COMPLETED
    assert [item.status.value for item in workflow_task.attempts] == (
        ["failed", "completed"] if network_failure else ["completed"]
    )
    assert [item.attempt_number for item in port.requests] == (
        [1, 2] if network_failure else [1]
    )
    assert run.llm_calls_used == 4

    records = [
        json.loads(line)
        for line in (tmp_path / "traces" / "llm_traces.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [record["action_valid"] for record in records] == (
        [False, True] if network_failure else [False, False, False, True]
    )
    assert sum(len(record["attempts"]) for record in records) == 4
    assert records[-1]["parsed_action"]["tool"] == "finish"
    if not network_failure:
        assert len({record["session_id"] for record in records}) == 1
        assert "runtime_feedback" in records[-1]["included_sections"]


@pytest.fixture
def recovery(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_KEY", "dummy")
    monkeypatch.setattr("resagent2_runtime.llm.time.sleep", lambda _: None)
    client = OpenAICompatibleClient(
        model="test-model", api_base="https://example.invalid/v1",
        api_key_env="TEST_LLM_KEY", trace_dir=tmp_path / "traces", trace_level="full",
    )
    definition = AgentDefinition(
        name="recovery", owner=AgentOwner.CODING,
        system_prompt="Use the provided tools only.",
        tools=(WriteValueTool(), FinishTool()), llm_client=client,
        context_builder=_context,
        permission_policy=AllowListPermissionPolicy({"write_value", "finish"}),
        completion_check=_AcceptFinish(),
    )
    request = ModuleTaskRequest(
        run_id="run_r", task_id="task_r", attempt_number=1,
        capability=Capability.CODE_UNDERSTAND, goal="exercise recovery",
        inputs=CodeUnderstandInput(question="q"),
        budget=TaskBudget(max_steps=10, max_llm_calls=10, timeout_seconds=60),
    )
    return definition, request, InMemorySessionStore()


def _response(content):
    return _FakeResponse({
        "choices": [{"finish_reason": "stop", "message": {
            "content": content, "reasoning_content": "PRIVATE_REASONING",
        }}],
        "usage": {"completion_tokens": 20},
    })


_FINISH = '{"tool":"finish","arguments":{"result":{}}}'
_WRITE = '{"tool":"write_value","arguments":{"key":"kept","value":7}}'


@pytest.mark.parametrize("bad", [
    "   ", "PRIVATE_BROKEN_JSON",
    _WRITE + "\n<DSML>PRIVATE_SECOND_ACTION</DSML>",
    _WRITE + "\nLet me continue.", _WRITE + "\n" + _FINISH,
])
def test_bad_json_feedback_preserves_state_and_never_executes_prefix(recovery, bad):
    definition, request, store = recovery
    with mock.patch("resagent2_runtime.llm.urlopen", side_effect=[
        _response(bad), _response(_WRITE), _response(_FINISH),
    ]):
        result = AgentLoop(store=store).run(definition, request, session_id="session_r")

    state = store.load("session_r")
    assert result.status == ModuleStatus.COMPLETED
    assert result.llm_calls == state.llm_calls_used == 3
    assert state.step == 2
    assert state.memory["kept"] == 7
    assert [e.tool for e in state.events if e.type == "action"] == ["write_value", "finish"]
    assert state.runtime_feedback is None
    assert "PRIVATE_" not in state.model_dump_json()
    records = [json.loads(line) for line in
               (definition.llm_client.trace_dir / "llm_traces.jsonl").read_text().splitlines()]
    assert len(records) == 3
    assert records[0]["raw_response_text"] == bad
    assert records[0]["action_valid"] is False
    assert records[0]["finish_reason"] == "stop"
    assert records[0]["usage"] == {"completion_tokens": 20}
    assert records[1]["included_sections"].count("runtime_feedback") == 1
    assert "Return exactly one JSON object" in records[1]["request_text"]
    assert "No tool was executed" in records[1]["request_text"]
    assert "PRIVATE_" not in records[1]["request_text"]
    assert "runtime_feedback" not in records[2]["included_sections"]


@pytest.mark.parametrize("call_budget", [2, 3])
def test_transport_then_bad_json_counts_all_attempts_without_exceeding_budget(
    recovery, call_budget,
):
    definition, request, store = recovery
    request = request.model_copy(update={"budget": TaskBudget(
        max_steps=10, max_llm_calls=call_budget, timeout_seconds=60,
    )})
    with mock.patch("resagent2_runtime.llm.urlopen", side_effect=[
        URLError("transient"), _response("bad JSON"), _response(_FINISH),
    ]) as provider:
        result = AgentLoop(store=store).run(definition, request, session_id="session_r")
    assert result.llm_calls == provider.call_count == call_budget
    assert store.load("session_r").llm_calls_used == call_budget
    if call_budget == 2:
        assert result.error.code == ErrorCode.BUDGET_EXHAUSTED
    else:
        assert result.status == ModuleStatus.COMPLETED
    records = [json.loads(line) for line in
               (definition.llm_client.trace_dir / "llm_traces.jsonl").read_text().splitlines()]
    assert len(records[0]["attempts"]) == 2
    assert records[0]["attempts"][0]["raw_response_text"] is None
    assert records[0]["attempts"][1]["raw_response_text"] == "bad JSON"
    assert sum(r["retry_number"] + 1 for r in records) == result.llm_calls


def test_json_and_schema_errors_share_feedback_and_failure_limit(recovery):
    definition, request, store = recovery
    bad_schema = '{"tool":"finish","result":{}}'
    with mock.patch("resagent2_runtime.llm.urlopen", side_effect=[
        _response("bad JSON"), _response(bad_schema), _response("bad JSON"),
        _response(bad_schema), _response("bad JSON"), _response(_FINISH),
    ]) as provider:
        result = AgentLoop(store=store).run(definition, request, session_id="session_r")
    assert result.error.code == ErrorCode.TOOL_FAILED
    assert result.error.retryable is False
    assert result.llm_calls == provider.call_count == 5
    records = [json.loads(line) for line in
               (definition.llm_client.trace_dir / "llm_traces.jsonl").read_text().splitlines()]
    assert sum("model" in r for r in records) == 5
    assert sum("schema_validation_error" in r for r in records) == 2


def test_json_correction_still_respects_wall_clock(recovery):
    definition, request, store = recovery
    ticks = iter([0, 0, 61])  # start, first call, next iteration
    with mock.patch("resagent2_runtime.llm.urlopen", return_value=_response("bad JSON")) as provider:
        result = AgentLoop(store=store, clock=lambda: next(ticks)).run(
            definition, request, session_id="session_r",
        )
    assert result.error.code == ErrorCode.TIMEOUT
    assert result.llm_calls == provider.call_count == 1
    assert store.load("session_r").step == 0


def test_json_feedback_is_provider_neutral(recovery):
    from dataclasses import replace

    class OtherClient:
        def __init__(self):
            self.contexts = []

        def next_action(self, context, action_type):
            self.contexts.append(context)
            return json.loads("bad JSON" if len(self.contexts) == 1 else _FINISH)

    definition, request, store = recovery
    client = OtherClient()
    result = AgentLoop(store=store).run(
        replace(definition, llm_client=client), request, session_id="session_r",
    )
    assert result.status == ModuleStatus.COMPLETED
    assert result.llm_calls == 2
    assert "runtime_feedback" in client.contexts[1].included_sections


def test_correction_keeps_previously_completed_work(recovery):
    definition, request, store = recovery
    with mock.patch("resagent2_runtime.llm.urlopen", side_effect=[
        _response(_WRITE), _response("bad JSON"), _response(_FINISH),
    ]):
        result = AgentLoop(store=store).run(definition, request, session_id="session_r")
    state = store.load("session_r")
    assert result.status == ModuleStatus.COMPLETED
    assert result.llm_calls == state.llm_calls_used == 3
    assert state.memory["kept"] == 7
    assert [e.tool for e in state.events if e.type == "action"] == ["write_value", "finish"]
