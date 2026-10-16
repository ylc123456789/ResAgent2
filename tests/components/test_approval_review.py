"""One-shot reviewer protocol, faithful authority inputs and shared execution controls."""

import json
from datetime import UTC, datetime, timedelta
from io import BytesIO

import httpx
import pytest
from pydantic import ValidationError

from resagent2_components.approval_review import LLMPermissionReviewer
from resagent2_contracts import (
    ActionSnapshot, AgentPermissions, AgentRequest, BusinessInput, RecordedAnswer,
    TaskBudget, WorkspaceAccess, WorkspaceGrant, WorkspaceSourceKind,
)
from resagent2_runtime import (
    ApprovalReviewVerdict, NativeToolCall, OpenAICompatibleClient, ScriptedLLMClient,
    ToolCallTurn,
)
from resagent2_runtime.budget import (
    BudgetExhaustedError, DeadlineExceededError, MemoryUsage, execution_budget,
)
from resagent2_runtime.cancellation import (
    CancellationToken, OperationCancelled, bind_cancellation,
)
from resagent2_runtime.context import ContextBudgetExceeded
from resagent2_runtime.tool_calling import NativeToolCallError


def _turn(value=None, *, name="review_operation", extra_call=False):
    value = value if value is not None else {"decision": "allow", "reason": "Within the stated task and scope"}
    calls = [NativeToolCall(id="review_1", name=name, arguments=json.dumps(value))]
    if extra_call:
        calls.append(NativeToolCall(id="review_2", name="review_operation", arguments=json.dumps(value)))
    return ToolCallTurn(tool_calls=calls)


@pytest.fixture
def operation():
    return ActionSnapshot(
        action_id="action_review", tool="run_shell", run_id="run_review",
        session_id="session_worker", task_id="task_review", attempt_number=1,
        arguments={"command": "printf '%s' 'literal; $(do-not-run)'", "timeout_seconds": 12},
        context={"cwd": "/workspace/granted", "launch_env_sha256": "a" * 64,
                 "shell": "/bin/bash", "argv": ["/bin/bash", "-lc"],
                 "visible_side_effects": ["writes only the declared workspace"]},
    )


@pytest.fixture
def agent_request(tmp_path):
    return AgentRequest(
        run_id="run_review", task_id="task_review", attempt_number=1,
        agent="coding", instruction="  Keep this original instruction.\nUse the granted workspace only.  ",
        budget=TaskBudget(max_llm_calls=10, timeout_seconds=30),
        permissions=AgentPermissions(execute_commands=True),
        workspace=WorkspaceGrant(root=str(tmp_path), source=WorkspaceSourceKind.LOCAL,
            access=WorkspaceAccess(read_paths=["."], write_paths=["outputs"], denied_paths=["secrets"])),
        upstream_session_id="session_parent",
        task_context={"shared_goal": "Respect the original goal", "shared_constraints": ["No external transfer"]},
    )


def _review(client, operation, agent_request, **kwargs):
    return LLMPermissionReviewer(client, max_context_tokens=20_000, **kwargs).review(operation, agent_request)


@pytest.mark.parametrize("decision", ["allow", "ask"])
def test_sole_native_output_tool_and_stateless_history(operation, agent_request, decision):
    client = ScriptedLLMClient([_turn({"decision": decision, "reason": "Exact operation reviewed"}), _turn()])
    reviewer = LLMPermissionReviewer(client, max_context_tokens=20_000)
    verdict = reviewer.review(operation, agent_request)
    assert verdict == ApprovalReviewVerdict(decision=decision, reason="Exact operation reviewed")
    reviewer.review(operation, agent_request)
    assert client.histories == [[], []]
    for schemas in client.schemas:
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "review_operation"
        schema = schemas[0]["function"]["parameters"]
        assert schema["additionalProperties"] is False
        assert set(schema["properties"]) == {"decision", "reason"}
        assert schema["properties"]["decision"]["enum"] == ["allow", "ask"]


def test_current_task_and_snapshot_are_lossless_without_lower_history_or_approvals(operation, agent_request, tmp_path):
    now = datetime(2026, 10, 16, tzinfo=UTC)
    original = "  Later user correction\nkeep whitespace.  "
    agent_request.upstream_inputs = [
        BusinessInput(event_id="input_later", text=original, source="user", created_at=now + timedelta(seconds=2)),
        BusinessInput(event_id="input_old", text="Parent guidance", source="parent", source_session_id="session_parent", created_at=now),
    ]
    answer = RecordedAnswer(
        question_id="question_business", values={"choice": "Use the original boundary"},
        answered_at=now + timedelta(seconds=1), question_text="Which boundary?",
        requested_fields=["choice"], source="parent", source_session_id="session_parent",
        run_id=agent_request.run_id, session_id="session_worker", task_id=agent_request.task_id, attempt_number=1,
    )
    agent_request.user_answers = [answer]
    agent_request.resume_answers = [answer.model_copy(update={
        "question_id": "question_approval", "action": operation,
        "values": {"choice": "APPROVAL_NOT_AUTHORIZATION"}, "comment": "SECRET_APPROVAL_COMMENT",
    })]
    agent_request.runtime_notices = [{"message": "LOWER_HISTORY_MUST_NOT_ENTER"}]
    agent_request.child_questions = []
    (tmp_path / "unread.txt").write_text("UNREAD_FILE_CANNOT_AUTHORIZE")
    client = ScriptedLLMClient([_turn()])
    _review(client, operation, agent_request)
    context = client.contexts[0]
    payload = json.loads(context.task_context.split("\n", 1)[1])
    task = payload["current_task"]["requirements"]
    assert task["instruction"] == agent_request.instruction
    assert task["task_context"] == agent_request.task_context
    assert task["workspace"] == agent_request.workspace.model_dump(mode="json")
    assert [(item["type"], item["event_id"]) for item in task["business_dialogue"]] == [
        ("input", "input_old"), ("answer", "question_business"), ("input", "input_later"),
    ]
    assert task["business_dialogue"][2]["record"]["text"] == original
    assert task["business_dialogue"][1]["record"] == answer.model_dump(mode="json", exclude_none=True)
    assert payload["proposed_operation"] == operation.model_dump(mode="json")
    assert payload["current_task"]["invocation"] == {
        "agent": "coding", "upstream_session_id": "session_parent", "authorized_input_artifact_ids": [],
    }
    for canary in ["APPROVAL_NOT_AUTHORIZATION", "SECRET_APPROVAL_COMMENT"]:
        assert canary not in json.dumps(payload["current_task"])
        assert canary in json.dumps(payload["prior_operation_decisions"])
    assert payload["prior_operation_decisions"]["records"] == [agent_request.resume_answers[0].model_dump(mode="json")]
    for canary in ["LOWER_HISTORY_MUST_NOT_ENTER", "UNREAD_FILE_CANNOT_AUTHORIZE"]:
        assert canary not in context.text
    assert payload["root_user_context"]["requirements"] is None


def test_root_context_provider_keeps_original_sources_and_is_refreshed_per_review(operation, agent_request):
    original = {"goal": "  User original\n", "constraints": ["do not evaluate withheld data"],
                "business_dialogue": [{"source": "user", "event_id": "user_1", "text": "new boundary", "created_at": "2026-10-16T00:00:00+00:00"}]}
    calls = []

    def provide(run_id):
        calls.append(run_id)
        return original

    client = ScriptedLLMClient([_turn(), _turn()])
    reviewer = LLMPermissionReviewer(client, max_context_tokens=20_000, context_provider=provide)
    reviewer.review(operation, agent_request)
    original["constraints"].append("later requirement")
    reviewer.review(operation, agent_request)
    assert calls == [agent_request.run_id, agent_request.run_id]
    payloads = [json.loads(context.task_context.split("\n", 1)[1]) for context in client.contexts]
    assert payloads[0]["root_user_context"]["requirements"]["constraints"] == ["do not evaluate withheld data"]
    assert payloads[1]["root_user_context"]["requirements"] == original
    assert payloads[1]["current_task"]["requirements"]["instruction"] == agent_request.instruction


@pytest.mark.parametrize("value", [None, {}, [], "not a mapping"])
def test_missing_root_context_never_silently_becomes_empty_authorization(operation, agent_request, value):
    client = ScriptedLLMClient([_turn()])
    with pytest.raises(ValueError, match="root user review context is unavailable"):
        _review(client, operation, agent_request, context_provider=lambda _: value)
    assert not client.contexts


def test_provider_failure_propagates_before_model_call(operation, agent_request):
    client = ScriptedLLMClient([_turn()])

    def fail(_):
        raise OSError("root store unavailable")

    with pytest.raises(OSError, match="root store unavailable"):
        _review(client, operation, agent_request, context_provider=fail)
    assert not client.contexts


def test_exact_denial_and_reason_are_separate_history_not_new_authorization(operation, agent_request):
    original_reason = "  No.\nThis operation exceeds my allowed boundary.  "
    agent_request.resume_answers = [RecordedAnswer(
        question_id="question_denied", values={"approve": "no"}, answered_at=datetime(2026, 10, 16, tzinfo=UTC),
        question_text="Approve this exact operation?", requested_fields=["approve"],
        run_id=agent_request.run_id, session_id="session_worker", task_id=agent_request.task_id,
        attempt_number=1, action=operation, comment=original_reason,
    )]
    client = ScriptedLLMClient([_turn()])
    _review(client, operation, agent_request)
    context = client.contexts[0]
    payload = json.loads(context.task_context.split("\n", 1)[1])
    record = payload["prior_operation_decisions"]["records"][0]
    assert record["action"] == operation.model_dump(mode="json")
    assert record["values"] == {"approve": "no"}
    assert record["comment"] == original_reason
    assert "question_denied" not in json.dumps(payload["current_task"])


def test_cancellation_during_context_loading_stops_before_provider_dispatch(operation, agent_request):
    token = CancellationToken()
    client = ScriptedLLMClient([_turn()])

    def provide(_):
        token.cancel("interrupted while loading review context")
        return {"goal": "User goal"}

    with bind_cancellation(token), pytest.raises(OperationCancelled):
        _review(client, operation, agent_request, context_provider=provide)
    assert not client.contexts


def test_root_operation_history_keeps_cross_task_denials_separate_from_requirements(operation, agent_request):
    historical = {
        "question_id": "question_other_task", "source": "user", "task_id": "task_other",
        "action": operation.model_copy(update={"task_id": "task_other"}).model_dump(mode="json"),
        "values": {"approve": "no"}, "decision": "no", "comment": "  Original refusal\n",
        "answered_at": "2026-10-16T00:00:00Z",
    }
    root_context = {"initial_request": {"goal": "Root goal"}, "business_dialogue": [],
                    "prior_operation_decisions": [historical]}
    client = ScriptedLLMClient([_turn()])
    _review(client, operation, agent_request, context_provider=lambda _: root_context)
    payload = json.loads(client.contexts[0].task_context.split("\n", 1)[1])
    assert payload["prior_operation_decisions"]["root_records"] == [historical]
    assert payload["prior_operation_decisions"]["records"] == []
    assert payload["root_user_context"]["requirements"] == {
        "initial_request": {"goal": "Root goal"}, "business_dialogue": [],
    }
    assert "prior_operation_decisions" in root_context  # the provider's source object is untouched


@pytest.mark.parametrize("value", [
    {"decision": "deny", "reason": "unsupported"},
    {"decision": "allow", "reason": "   "},
    {"decision": "allow"},
    {"decision": "allow", "reason": "ok", "execute": True},
])
def test_invalid_verdict_is_rejected_without_repair_or_allow_fallback(operation, agent_request, value):
    client = ScriptedLLMClient([_turn(value), _turn()])
    with pytest.raises(ValidationError):
        _review(client, operation, agent_request)
    assert len(client.contexts) == 1


@pytest.mark.parametrize("turn", [_turn(name="run_shell"), _turn(extra_call=True), ToolCallTurn(content="allow")])
def test_wrong_tool_multiple_calls_and_plain_text_never_authorize(operation, agent_request, turn):
    client = ScriptedLLMClient([turn])
    with pytest.raises(NativeToolCallError):
        _review(client, operation, agent_request)
    assert len(client.contexts) == 1


def test_context_overflow_preserves_complete_inputs_and_makes_no_model_call(operation, agent_request):
    client = ScriptedLLMClient([_turn()])
    reviewer = LLMPermissionReviewer(client, max_context_tokens=1)
    with pytest.raises(ContextBudgetExceeded):
        reviewer.review(operation, agent_request)
    assert not client.contexts


def test_shared_budget_is_not_reset_or_given_a_separate_wallet(operation, agent_request):
    client = ScriptedLLMClient([_turn(), _turn(), _turn()])
    reviewer = LLMPermissionReviewer(client, max_context_tokens=20_000)
    usage = MemoryUsage()
    with execution_budget(max_llm_calls=2, timeout_seconds=30, usage=usage):
        reviewer.review(operation, agent_request)
        reviewer.review(operation, agent_request)
        with pytest.raises(BudgetExhaustedError):
            reviewer.review(operation, agent_request)
    assert usage.used == len(client.contexts) == 2
    assert set(usage.requests.values()) == {"succeeded"}
    assert '"llm_calls_used": 1' in client.contexts[1].runtime_status
    assert '"max_llm_calls": 2' in client.contexts[1].runtime_status


def test_shared_deadline_is_not_extended(operation, agent_request):
    client = ScriptedLLMClient([_turn()])
    with execution_budget(max_llm_calls=2, timeout_seconds=0):
        with pytest.raises(DeadlineExceededError):
            _review(client, operation, agent_request)
    assert not client.contexts


def test_cancelled_scope_does_not_call_provider_or_model(operation, agent_request):
    client = ScriptedLLMClient([_turn()])
    provider_calls = []
    token = CancellationToken()
    token.cancel()
    with bind_cancellation(token), pytest.raises(OperationCancelled):
        _review(client, operation, agent_request, context_provider=lambda run_id: provider_calls.append(run_id))
    assert provider_calls == client.contexts == []


def _wire_response():
    return BytesIO(json.dumps({"choices": [{"finish_reason": "tool_calls", "message": {
        "content": None, "tool_calls": [{"id": "review_wire", "type": "function", "function": {
            "name": "review_operation", "arguments": '{"decision":"allow","reason":"Within scope"}',
        }}],
    }}]}).encode())


def _http_client(monkeypatch, tmp_path):
    monkeypatch.setenv("REVIEW_TEST_KEY", "test-review-secret")
    return OpenAICompatibleClient(model="review-model", api_base="https://example.invalid/v1",
        api_key_env="REVIEW_TEST_KEY", trace_dir=tmp_path / "trace", trace_level="full")


@pytest.mark.parametrize("failure", ["transport", "http500"])
def test_each_operation_allows_only_one_http_attempt_even_on_reusable_client(operation, agent_request, monkeypatch, tmp_path, failure):
    client = _http_client(monkeypatch, tmp_path)
    reviewer = LLMPermissionReviewer(client, max_context_tokens=20_000)
    requests = []

    def fail(wire, **kwargs):
        requests.append(wire)
        if failure == "http500":
            raise httpx.HTTPStatusError("server error", request=wire, response=httpx.Response(500))
        raise httpx.TransportError("connection failed")

    monkeypatch.setattr("resagent2_runtime.llm.send_request", fail)
    with execution_budget(max_llm_calls=8, timeout_seconds=30) as budget:
        for _ in range(2):
            with pytest.raises(RuntimeError, match="after 1 attempts"):
                reviewer.review(operation, agent_request)
        assert budget.usage.used == 2
    assert len(requests) == 2
    records = [json.loads(line) for line in (client.trace_dir / "llm_traces.jsonl").read_text().splitlines()]
    assert [len(row["attempts"]) for row in records] == [1, 1]


def test_trace_correlates_exact_operation_and_late_cancelled_verdict_cannot_return(operation, agent_request, monkeypatch, tmp_path):
    client = _http_client(monkeypatch, tmp_path)
    reviewer = LLMPermissionReviewer(client, max_context_tokens=20_000)
    reviewer.set_trace_context(agent="permission_reviewer", run_id=agent_request.run_id,
        task_id=agent_request.task_id, session_id=operation.session_id, action_id=operation.action_id)
    token = CancellationToken()

    def respond(wire, **kwargs):
        token.cancel("interrupted during review")
        return _wire_response()

    monkeypatch.setattr("resagent2_runtime.llm.send_request", respond)
    with bind_cancellation(token), execution_budget(max_llm_calls=3, timeout_seconds=30) as budget:
        with pytest.raises(OperationCancelled, match="interrupted during review"):
            reviewer.review(operation, agent_request)
        assert budget.usage.used == 1
    record = json.loads((client.trace_dir / "llm_traces.jsonl").read_text().splitlines()[0])
    assert record["agent"] == "permission_reviewer"
    assert record["action_id"] == operation.action_id
    assert record["run_id"] == agent_request.run_id
    assert len(record["attempts"]) == 1
    wire = json.loads(record["request_text"])
    assert [tool["function"]["name"] for tool in wire["tools"]] == ["review_operation"]
    assert "test-review-secret" not in json.dumps(record)
