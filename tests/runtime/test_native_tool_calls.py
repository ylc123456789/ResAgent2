"""Native wire protocol, Session continuation and existing execution boundaries."""

import json
from dataclasses import replace
from io import BytesIO
from urllib.error import URLError

import pytest

from resagent2_contracts import (
    AgentOwner, Capability, CodeUnderstandInput, ErrorCode, ModuleStatus,
    ModuleTaskRequest, SessionStatus, TaskBudget,
)
from resagent2_runtime import (
    AgentDefinition, AgentLoop, AllowListPermissionPolicy, AskUserTool,
    CompletionDecision, ContextSection, FinishTool, InMemorySessionStore,
    JsonSessionStore, ModelProfile, OpenAICompatibleClient, WriteValueTool,
)
from resagent2_runtime.context import ContextComposer
from resagent2_runtime.models import NativeToolCall, ToolCallTurn, ToolObservation
from resagent2_runtime.tool_calling import (
    native_input_text, native_tool_schemas, tool_messages,
)


def _call(name="finish", arguments=None, *, call_id="call_finish"):
    return {"id": call_id, "type": "function", "function": {
        "name": name,
        "arguments": json.dumps({"result": {}} if arguments is None else arguments),
    }}


def _reply(calls=None, *, content=None, reasoning="model reasoning", finish="tool_calls"):
    return {"choices": [{"finish_reason": finish, "message": {
        "content": content, "reasoning_content": reasoning,
        "tool_calls": [_call()] if calls is None else calls,
    }}], "usage": {"completion_tokens": 12}}


class _Completion:
    def evaluate(self, state, candidate):
        return CompletionDecision(
            complete=candidate is not None,
            summary="accepted" if candidate is not None else "",
            payload=candidate.result if candidate is not None else None,
        )


def _context(request, state, limit):
    return [ContextSection(name="task", content=request.goal, required=True)]


def _request(*, parent=None, calls=10, steps=10, goal="current task"):
    return ModuleTaskRequest(
        run_id="run_native", task_id="task_native", attempt_number=1,
        capability=Capability.CODE_UNDERSTAND, goal=goal,
        inputs=CodeUnderstandInput(question="inspect"), parent_session_id=parent,
        budget=TaskBudget(max_steps=steps, max_llm_calls=calls, timeout_seconds=60),
    )


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_NATIVE_KEY", "private-api-key")
    monkeypatch.setattr("resagent2_runtime.llm.time.sleep", lambda _: None)
    client = OpenAICompatibleClient(
        model="native-test", api_base="https://example.invalid/v1", api_key_env="TEST_NATIVE_KEY",
        trace_level="full", trace_dir=tmp_path / "traces",
    )
    definition = AgentDefinition(
        name="native", owner=AgentOwner.CODING, system_prompt="Use tools to complete the task.",
        tools=(WriteValueTool(), AskUserTool(), FinishTool()), llm_client=client,
        context_builder=_context,
        permission_policy=AllowListPermissionPolicy({"write_value", "ask_user", "finish"}),
        completion_check=_Completion(),
    )
    requests = []

    def install(responses):
        queue = iter(responses)

        def respond(request, **kwargs):
            body = json.loads(request.data)
            requests.append(body)
            item = next(queue)
            if isinstance(item, BaseException):
                raise item
            if callable(item):
                item = item(body)
            return BytesIO(json.dumps(item).encode())

        monkeypatch.setattr("resagent2_runtime.llm.urlopen", respond)

    return definition, InMemorySessionStore(), requests, install


def _rows(definition):
    return [json.loads(line) for line in
            (definition.llm_client.trace_dir / "llm_traces.jsonl").read_text().splitlines()]


def test_native_schema_calls_receipts_and_reasoning_reach_next_request(setup):
    definition, store, requests, install = setup
    install([
        _reply([_call("write_value", {"key": "x", "value": 7}, call_id="call_write")],
               content="I will update the value; this is not JSON.", reasoning="PRIVATE_REASONING"),
        _reply(),
    ])
    result = AgentLoop(store=store).run(definition, _request(), session_id="session_native")
    assert result.status == ModuleStatus.COMPLETED
    state = store.load("session_native")
    assert state.memory["x"] == 7
    assert result.llm_calls == state.llm_calls_used == state.step == 2
    schema = requests[0]["tools"][0]["function"]
    assert schema["name"] == "write_value"
    assert schema["parameters"] == WriteValueTool.input_model.model_json_schema()
    assert "response_format" not in requests[0]
    assert "tool_choice" not in requests[0]  # provider-specific force mode is not assumed
    messages = requests[1]["messages"]
    assert [m["role"] for m in messages] == ["system", "assistant", "tool", "user"]
    assert messages[1]["reasoning_content"] == "PRIVATE_REASONING"
    assert messages[2]["tool_call_id"] == "call_write"
    receipt = json.loads(messages[2]["content"])
    assert receipt["ok"] and receipt["value"] == 7
    assert "memory_updates" not in receipt
    assert "recent_observations" not in messages[-1]["content"]
    assert "tool_contracts" not in messages[-1]["content"]
    assert state.tool_turns[0].tool_results.keys() == {"call_write"}
    records = _rows(definition)
    for body, record in zip(requests, records):
        wire = json.dumps({"messages": body["messages"], "tools": body["tools"]}, ensure_ascii=False)
        assert record["request_text"] == wire
        assert record["estimated_tokens"] == ContextComposer.estimate_tokens(wire)
        assert "native_tools" in record["included_sections"]
    assert records[-1]["parsed_action"] == {"tool": "finish", "arguments": {"result": {}}}
    assert "tool_history" in records[-1]["included_sections"]


@pytest.mark.parametrize("failure", ["bad_arguments", "prose", "multiple", "schema", "duplicate_ids", "missing_id", "length"])
def test_rejected_native_response_never_executes_and_recovers_in_same_session(setup, failure):
    definition, store, requests, install = setup
    call = _call("write_value", {"key": "unsafe", "value": 99}, call_id="call_bad")
    bad = _reply([call])
    if failure == "bad_arguments":
        call["function"]["arguments"] += "\n<DSML>second action</DSML>"
    elif failure == "prose":
        bad = _reply([], content=json.dumps({"tool": "write_value", "arguments": {"key": "unsafe", "value": 99}}))
    elif failure == "multiple":
        bad = _reply([call, _call(call_id="call_other")])
    elif failure == "schema":
        call["function"]["arguments"] = '{"key":"unsafe","unexpected":99}'
    elif failure == "duplicate_ids":
        bad = _reply([call, _call(call_id="call_bad")])
    elif failure == "missing_id":
        del call["id"]
    elif failure == "length":
        bad["choices"][0]["finish_reason"] = "length"
    install([bad, _reply()])
    result = AgentLoop(store=store).run(definition, _request(), session_id="session_native")
    state = store.load("session_native")
    assert result.status == ModuleStatus.COMPLETED
    assert result.llm_calls == state.llm_calls_used == 2
    assert "unsafe" not in state.memory
    assert requests[1]["messages"][-1]["content"].count("## runtime_feedback") == 1
    # Every retained native call has exactly one receipt, even a rejected batch.
    for turn in state.tool_turns:
        assert set(turn.tool_results) == {c.id for c in turn.tool_calls}
    tool_messages(state.tool_turns)  # all pairs are serializable
    if failure in {"bad_arguments", "multiple", "schema"}:
        assert all(not json.loads(value)["ok"] for value in state.tool_turns[0].tool_results.values())
    if failure == "multiple":
        assert len(state.tool_turns[0].tool_results) == 2


def test_empty_native_replies_exhaust_existing_failure_limit_without_steps(setup):
    definition, store, requests, install = setup
    install([_reply([], content="", reasoning=None)] * 5)
    result = AgentLoop(store=store).run(definition, _request(), session_id="session_native")
    assert result.error.code == ErrorCode.TOOL_FAILED
    assert result.llm_calls == len(requests) == 5
    state = store.load("session_native")
    assert state.step == 0 and state.tool_turns == []
    assert all(not row["action_valid"] and row["validation_error"] for row in _rows(definition))


def test_http_retry_and_invalid_arguments_share_total_call_budget(setup):
    definition, store, requests, install = setup
    bad = _call(call_id="call_bad")
    bad["function"]["arguments"] = "{"  # never copied into a valid action
    install([URLError("offline"), _reply([bad]), _reply()])
    result = AgentLoop(store=store).run(definition, _request(calls=3), session_id="session_native")
    assert result.status == ModuleStatus.COMPLETED
    assert result.llm_calls == len(requests) == 3
    rows = _rows(definition)
    assert len(rows) == 2
    assert [len(r["attempts"]) for r in rows] == [2, 1]
    assert sum(r["retry_number"] + 1 for r in rows) == 3
    assert rows[0]["attempts"][0]["raw_response_text"] is None
    assert rows[0]["attempts"][1]["raw_tool_calls"] == [bad]


def test_permission_check_still_precedes_dispatch(setup):
    definition, store, requests, install = setup
    definition = replace(definition, permission_policy=AllowListPermissionPolicy({"finish"}))
    install([_reply([_call("write_value", {"key": "unsafe", "value": 1}, call_id="call_denied")])])
    result = AgentLoop(store=store).run(definition, _request(), session_id="session_native")
    assert result.error.code == ErrorCode.PERMISSION_DENIED
    state = store.load("session_native")
    assert "unsafe" not in state.memory
    assert not json.loads(state.tool_turns[0].tool_results["call_denied"])["ok"]


@pytest.mark.parametrize("trace_level", ["full", "metadata", "off"])
def test_disk_pause_resume_uses_session_not_trace(setup, tmp_path, trace_level):
    definition, _, requests, install = setup
    definition.llm_client.trace_level = trace_level
    disk = JsonSessionStore(tmp_path / "sessions")
    install([
        _reply([_call("ask_user", {"text": "Which mode?", "requested_fields": ["mode"], "reason": "need mode"},
                     call_id="call_question")], reasoning="PRIVATE_CONTINUATION"),
        _reply(),
    ])
    first = AgentLoop(store=disk).run(definition, _request(goal="OLD_GOAL"), session_id="session_native")
    assert first.status == ModuleStatus.NEEDS_USER_INPUT
    # Fresh client/store/loop simulate process restart; no in-client history.
    next_client = OpenAICompatibleClient(
        model="native-test", api_base="https://example.invalid/v1", api_key_env="TEST_NATIVE_KEY", trace_level="off",
    )
    second = AgentLoop(store=JsonSessionStore(disk.root)).run(
        replace(definition, llm_client=next_client),
        _request(parent="session_native", goal="NEW_GOAL answer=mul"), session_id="session_native",
    )
    assert second.status == ModuleStatus.COMPLETED
    assert second.llm_calls == first.llm_calls == 1
    messages = requests[1]["messages"]
    assert messages[1]["reasoning_content"] == "PRIVATE_CONTINUATION"
    assert messages[2]["tool_call_id"] == "call_question"
    assert json.loads(messages[2]["content"])["control"] == "question_issued_not_answered"
    assert "NEW_GOAL answer=mul" in messages[-1]["content"]
    assert "OLD_GOAL" not in json.dumps(messages)  # no repeated old full context
    assert (disk.root.stat().st_mode & 0o777) == 0o700
    path = disk.root / "session_native.json"
    assert (path.stat().st_mode & 0o777) == 0o600
    assert JsonSessionStore(disk.root).load("session_native").llm_calls_used == 2
    if trace_level == "off":
        assert not definition.llm_client.trace_dir.exists()
    elif trace_level == "metadata":
        assert "PRIVATE_" not in json.dumps(_rows(definition))
        assert "Which mode" not in json.dumps(_rows(definition))


def test_interrupted_call_is_not_replayed_and_gets_unknown_outcome_receipt(setup):
    definition, store, requests, install = setup
    install([_reply([_call("ask_user", {"text": "Which?", "requested_fields": ["answer"], "reason": "need"},
                         call_id="call_ask")]), _reply()])
    AgentLoop(store=store).run(definition, _request(), session_id="session_native")
    state = store.load("session_native")
    state.status = SessionStatus.ACTIVE
    state.tool_turns.append(ToolCallTurn(tool_calls=[NativeToolCall(
        id="call_interrupted", name="write_value", arguments='{"key":"unsafe","value":99}',
    )], reasoning_content="pending reasoning"))
    store.save(state)
    result = AgentLoop(store=store).run(
        definition, _request(parent="session_native"), session_id="session_native",
    )
    assert result.status == ModuleStatus.COMPLETED
    assert "unsafe" not in store.load("session_native").memory
    receipt = next(m for m in requests[1]["messages"] if m.get("tool_call_id") == "call_interrupted")
    assert "unknown" in receipt["content"] and "not replayed" in receipt["content"]


def test_native_history_is_scoped_even_when_client_is_shared(setup):
    definition, store, requests, install = setup
    install([_reply(reasoning="SESSION_ONE_ONLY"), _reply(reasoning="SESSION_TWO_ONLY")])
    for session in ["session_one", "session_two"]:
        result = AgentLoop(store=store).run(definition, _request(), session_id=session)
        assert result.status == ModuleStatus.COMPLETED
    assert "SESSION_ONE_ONLY" not in json.dumps(requests[1])


def test_native_history_and_schema_cannot_escape_input_budget(setup):
    definition, store, requests, install = setup
    definition = replace(definition, max_context_tokens=30)
    install([])
    result = AgentLoop(store=store).run(definition, _request(), session_id="session_native")
    assert result.error.code == ErrorCode.BUDGET_EXHAUSTED
    assert result.llm_calls == 0 and requests == []


def test_composer_accounts_for_json_escaping_in_native_request(setup):
    definition, store, requests, install = setup
    schemas = native_tool_schemas(definition.tools)
    required = "## system\n" + definition.system_prompt + "\n\n## task\ncurrent task"
    required_cost = ContextComposer.estimate_tokens(native_input_text(required, schemas, []))

    def build(request, state, limit):
        return [*_context(request, state, limit), ContextSection(name="optional", content='"\\\n' * 100)]

    definition = replace(definition, context_builder=build, max_context_tokens=required_cost + 10)
    install([_reply()])
    result = AgentLoop(store=store).run(definition, _request(), session_id="session_native")
    assert result.status == ModuleStatus.COMPLETED
    assert _rows(definition)[0]["omitted_sections"] == ["optional"]
    assert _rows(definition)[0]["estimated_tokens"] <= required_cost + 10


def test_native_model_profile_reserves_output_before_input(setup):
    definition, store, requests, install = setup
    definition.llm_client.model_profile = ModelProfile(
        context_window=5000, reserved_output_tokens=4000, safety_margin_tokens=500,
    )
    install([])
    result = AgentLoop(store=store).run(definition, _request(goal="large" * 1000), session_id="session_native")
    assert result.error.code == ErrorCode.BUDGET_EXHAUSTED
    assert result.llm_calls == 0 and requests == []


def test_completed_tool_values_reach_history_without_preview_truncation(setup):
    definition, store, requests, install = setup
    value = "a" * 1500 + "EXACT_MIDDLE_SENTINEL" + "z" * 1500
    install([_reply([_call("write_value", {"key": "text", "value": value}, call_id="call_text")]), _reply()])
    result = AgentLoop(store=store).run(definition, _request(), session_id="session_native")
    assert result.status == ModuleStatus.COMPLETED
    receipt = next(m for m in requests[1]["messages"] if m["role"] == "tool")
    assert json.loads(receipt["content"])["value"] == value


def test_request_work_pause_is_not_a_completed_execution(setup):
    definition, store, requests, install = setup

    class WorkTool(FinishTool):
        name = "request_work"

        def execute(self, state, arguments):
            return ToolObservation(summary="Requested work", request_work=arguments.result)

    definition = replace(
        definition, tools=(WorkTool(), FinishTool()),
        permission_policy=AllowListPermissionPolicy({"request_work", "finish"}),
    )
    install([_reply([_call("request_work", {"result": {"objective": "measure"}}, call_id="call_work")]), _reply()])
    first = AgentLoop(store=store).run(definition, _request(), session_id="session_native")
    assert first.status == ModuleStatus.REQUEST_WORK
    assert first.request_work == {"objective": "measure"}
    second = AgentLoop(store=store).run(
        definition, _request(parent="session_native", goal="Fresh work outcome"), session_id="session_native",
    )
    assert second.status == ModuleStatus.COMPLETED
    receipt = next(m for m in requests[1]["messages"] if m.get("tool_call_id") == "call_work")
    assert json.loads(receipt["content"])["control"] == "work_requested_not_executed"
    assert "Fresh work outcome" in requests[1]["messages"][-1]["content"]


def test_finish_rejection_remains_visible_until_real_completion(setup):
    definition, store, requests, install = setup

    class VerifiedCompletion(_Completion):
        def evaluate(self, state, candidate):
            if candidate is not None and state.memory.get("verified") is not True:
                return CompletionDecision(complete=False, summary="Verify before finish")
            return super().evaluate(state, candidate)

    definition = replace(definition, completion_check=VerifiedCompletion())
    install([
        _reply([_call(call_id="call_rejected_finish")]),
        _reply([_call("write_value", {"key": "verified", "value": True}, call_id="call_verify")]),
        _reply(),
    ])
    result = AgentLoop(store=store).run(definition, _request(), session_id="session_native")
    assert result.status == ModuleStatus.COMPLETED and result.llm_calls == 3
    for body in requests[1:]:
        assert "Verify before finish" in body["messages"][-1]["content"]
    receipt = next(m for m in requests[1]["messages"] if m.get("tool_call_id") == "call_rejected_finish")
    assert json.loads(receipt["content"])["control"] == "finish_proposed_not_yet_accepted"


def test_crash_after_side_effect_before_receipt_does_not_replay(setup, tmp_path):
    definition, _, requests, install = setup
    effects = []

    class ProcessInterrupted(BaseException):
        pass

    class InterruptedWrite(WriteValueTool):
        def execute(self, state, arguments):
            effects.append(arguments.value)
            raise ProcessInterrupted()

    definition = replace(definition, tools=(InterruptedWrite(), FinishTool()))
    disk = JsonSessionStore(tmp_path / "sessions")
    install([_reply([_call("write_value", {"key": "x", "value": 7}, call_id="call_interrupted")]), _reply()])
    with pytest.raises(ProcessInterrupted):
        AgentLoop(store=disk).run(definition, _request(), session_id="session_native")
    before = JsonSessionStore(disk.root).load("session_native")
    assert effects == [7] and before.tool_turns[0].tool_results == {}
    assert before.llm_calls_used == 1
    result = AgentLoop(store=JsonSessionStore(disk.root)).run(
        definition, _request(parent="session_native"), session_id="session_native",
    )
    assert result.status == ModuleStatus.COMPLETED
    assert effects == [7]  # no replay even though the durable receipt was absent
    receipt = next(m for m in requests[1]["messages"] if m.get("tool_call_id") == "call_interrupted")
    assert "unknown" in receipt["content"]


def test_history_exhaustion_stops_before_next_http_request(setup):
    definition, store, requests, install = setup
    definition = replace(definition, max_context_tokens=2000)
    install([_reply([_call("write_value", {"key": "large", "value": "x" * 10000}, call_id="call_large")])])
    result = AgentLoop(store=store).run(definition, _request(), session_id="session_native")
    assert result.error.code == ErrorCode.BUDGET_EXHAUSTED
    assert result.llm_calls == len(requests) == 1
    turn = store.load("session_native").tool_turns[0]
    assert len(json.loads(turn.tool_results["call_large"])["value"]) == 10000


def test_historical_call_id_is_rejected_without_duplicate_execution(setup):
    definition, store, requests, install = setup
    install([
        _reply([_call("write_value", {"key": "x", "value": 1}, call_id="call_same")]),
        _reply([_call("write_value", {"key": "x", "value": 2}, call_id="call_same")]),
        _reply(),
    ])
    result = AgentLoop(store=store).run(definition, _request(), session_id="session_native")
    assert result.status == ModuleStatus.COMPLETED
    state = store.load("session_native")
    assert state.memory["x"] == 1 and state.step == 2 and state.llm_calls_used == 3
    assert "historical tool call ID" in requests[2]["messages"][-1]["content"]
    assert sum(c.id == "call_same" for t in state.tool_turns for c in t.tool_calls) == 1


def test_native_session_cannot_resume_with_json_only_client(setup):
    definition, store, requests, install = setup
    install([_reply([_call("ask_user", {"text": "Which?", "requested_fields": ["answer"], "reason": "need"})])])
    first = AgentLoop(store=store).run(definition, _request(), session_id="session_native")
    assert first.status == ModuleStatus.NEEDS_USER_INPUT
    from resagent2_runtime import ScriptedLLMClient
    scripted = ScriptedLLMClient([])
    result = AgentLoop(store=store).run(
        replace(definition, llm_client=scripted), _request(parent="session_native"), session_id="session_native",
    )
    assert result.error.code == ErrorCode.CONTRACT_ERROR and not scripted.contexts
    assert len(requests) == 1


@pytest.mark.parametrize("changed", ["endpoint", "model", "old_json"])
def test_session_rejects_silent_protocol_or_provider_migration(setup, changed):
    definition, store, requests, install = setup
    install([_reply([_call("ask_user", {"text": "Which?", "requested_fields": ["answer"], "reason": "need"})])])
    first = AgentLoop(store=store).run(definition, _request(), session_id="session_native")
    assert first.status == ModuleStatus.NEEDS_USER_INPUT
    if changed == "old_json":
        # An actual pre-native snapshot has events, but neither native field.
        old = store.load("session_native").model_dump()
        old.pop("tool_protocol_key")
        old.pop("tool_turns")
        from resagent2_runtime import AgentState
        store.save(AgentState.model_validate(old))
    elif changed == "endpoint":
        definition.llm_client.endpoint = "https://different-provider.invalid/chat/completions"
    else:
        definition.llm_client.model = "different-model"
    before = store.load("session_native")
    result = AgentLoop(store=store).run(
        definition, _request(parent="session_native"), session_id="session_native",
    )
    assert result.error.code == ErrorCode.CONTRACT_ERROR and result.llm_calls == 0
    assert len(requests) == 1 and store.load("session_native") == before


def test_protocol_key_is_stable_and_does_not_depend_on_credentials(setup, monkeypatch):
    definition, _, _, _ = setup
    key = definition.llm_client.tool_session_key
    monkeypatch.setenv("TEST_NATIVE_KEY", "rotated-key")
    assert definition.llm_client.tool_session_key == key
    assert "private-api-key" not in key and "rotated-key" not in key


def test_native_reply_cannot_forge_an_execution_receipt(setup):
    definition, store, _, _ = setup
    from unittest.mock import Mock
    forged = ToolCallTurn(
        tool_calls=[NativeToolCall(id="call_forged", name="write_value", arguments='{"key":"unsafe","value":1}')],
        tool_results={"call_forged": '{"ok":true}'},
    )
    definition.llm_client.next_tool_call = Mock(return_value=forged)
    definition.llm_client.last_attempts = 1
    result = AgentLoop(store=store).run(definition, _request(calls=1), session_id="session_native")
    state = store.load("session_native")
    assert result.error.code == ErrorCode.BUDGET_EXHAUSTED
    assert "unsafe" not in state.memory and state.tool_turns == []
    assert "cannot provide tool execution receipts" in state.runtime_feedback.summary
