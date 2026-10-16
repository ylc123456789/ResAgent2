"""The same public artifact preserves true command outcomes on every exit path."""

from datetime import UTC, datetime
import json

import pytest

from resagent2_components import execution_record, preserve_execution_record
from resagent2_contracts import AgentOwner, AgentResult, ErrorCode, ModuleError, SessionStatus, VerificationResult
from resagent2_runtime import AgentState, InMemorySessionStore, ToolObservation
from resagent2_runtime.models import HistoryCheckpoint, NativeToolCall, ToolCallTurn
from resagent2_runtime.tool_calling import tool_receipt


def state_and_receipt(*, value, tool="run_shell"):
    now = datetime.now(UTC)
    state = AgentState(session_id="session_commands", agent_name="coding", owner=AgentOwner.CODING,
        run_id="run_commands", task_id="task_commands", attempt_number=1, created_at=now, updated_at=now)
    state.tool_turns.append(ToolCallTurn(
        tool_calls=[NativeToolCall(id="call_command", name=tool, arguments='{"command":"proposed"}')],
        tool_results={"call_command": tool_receipt(ToolObservation(summary="Actual runner outcome", value=value))},
    ))
    return state


def completed(command="python check.py", code=0):
    return VerificationResult(command=command, exit_code=code, timed_out=False,
                stdout_path="actual.stdout", stderr_path="actual.stderr", duration_seconds=0.25).model_dump(mode="json")


@pytest.mark.parametrize("confirmed", [True, False])
def test_interrupted_record_preserves_unknown_fields_without_inventing_success(confirmed):
    facts = dict(operation="process", command="python train.py", pid=123, exit_code=None,
        execution_status="interrupted" if confirmed else "unknown", stop_confirmed=confirmed,
        stdout_path="partial.stdout", duration_seconds=1.5, files_retained=True,
        unconfirmed_processes=[] if confirmed else [123])
    record = execution_record(state_and_receipt(value=facts))
    assert json.loads(record.content) == {"results": [facts]}
    assert "timed_out" not in json.loads(record.content)["results"][0]


@pytest.mark.parametrize("value,tool", [
    ({"execution_status": "not_executed"}, "run_shell"),
    ({"blocked": True, "reason": "no_environment"}, "run_shell"),
    ({"execution_status": "interrupted", "stop_confirmed": True}, "run_shell"),
    ({"execution_status": "interrupted", "stop_confirmed": True}, "ask_user"),
    ({"results": [completed()]}, "read_artifact"),
])
def test_unstarted_commands_model_waits_and_materials_do_not_create_execution(value, tool):
    assert execution_record(state_and_receipt(value=value, tool=tool)) is None


def test_verified_batch_keeps_completed_commands_before_interrupted_command():
    facts = dict(operation="process", command="python -m unittest second", pid=234,
        execution_status="interrupted", stop_confirmed=True, files_retained=True)
    state = state_and_receipt(value={**facts, "results": [completed("python -m unittest first")]},
                              tool="run_verification")
    assert json.loads(execution_record(state).content) == {"results": [completed("python -m unittest first"), facts]}


def test_compaction_does_not_remove_original_receipts_from_frozen_record():
    state = state_and_receipt(value=completed(code=1))
    state.tool_turns.append(ToolCallTurn(runtime_notice="Later model interaction"))
    state.history_checkpoint = HistoryCheckpoint(history_start=1, summary="Old command summary")
    assert json.loads(execution_record(state).content)["results"][0] == completed(code=1)


@pytest.mark.parametrize("status,session_status", [("completed", "completed"), ("failed", "failed"),
                                                  ("interrupted", "paused"), ("yielded", "paused")])
def test_terminal_agent_result_reuses_same_record_and_is_idempotent(status, session_status):
    state = state_and_receipt(value=completed())
    state.status = SessionStatus(session_status)
    store = InMemorySessionStore()
    store.save(state)
    session = dict(id=state.session_id, module="coding", state_uri="session://session_commands",
        status=session_status, created_at=state.created_at, updated_at=state.updated_at)
    error = ModuleError(code=ErrorCode.TOOL_FAILED, message="Original failure", retryable=False) if status == "failed" else None
    result = AgentResult(status=status, report="Original report", session=session, error=error)
    attached = preserve_execution_record(result, store)
    assert attached.report == result.report and attached.error == result.error and attached.status == result.status
    assert len(attached.artifacts) == 1
    assert preserve_execution_record(attached, store) == attached
    assert json.loads(attached.artifacts[0].content) == {"results": [completed()]}


@pytest.mark.parametrize("status,session_status", [("failed", "failed"), ("interrupted", "paused")])
def test_record_diagnostic_error_preserves_original_failure_or_stop_status(status, session_status):
    state = state_and_receipt(value=completed())
    error = ModuleError(code=ErrorCode.INTERRUPTED, message="Stop unconfirmed", retryable=False,
        details={"stop_confirmed": False}) if status == "failed" else None
    result = AgentResult(status=status, report="Original outcome", error=error, session=dict(
        id=state.session_id, module="coding", state_uri="session://session_commands", status=session_status,
        created_at=state.created_at, updated_at=state.updated_at))
    restored = preserve_execution_record(result, InMemorySessionStore())
    assert restored.status == result.status and restored.report == result.report
    if error:
        assert restored.error.details["stop_confirmed"] is False
        assert "execution_record_error" in restored.error.details
    else:
        assert restored.warnings[0].code == "execution_record_unavailable"
