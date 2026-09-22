"""Experiment completion accepts analysis and preserves actual failure evidence.

Exact metric/path requirements are checked at Scheduler artifact acceptance.
"""

from datetime import UTC, datetime

import pytest

from resagent2_contracts import AgentOwner, ArtifactCandidate, ErrorCode, WorkspaceGrant, WorkspaceAccess, WorkspaceSourceKind
from resagent2_components import WorkspaceBoundary, WorkspaceObserver
from resagent2_runtime import AgentEvent, AgentState, FinishCandidate
from resagent2_experiment.completion import ExperimentCompletionCheck


def state():
    now = datetime.now(UTC)
    return AgentState(
        session_id="session_test", agent_name="experiment", owner=AgentOwner.EXPERIMENT,
        run_id="run_test", task_id="task_test", attempt_number=1,
        created_at=now, updated_at=now,
    )


def check(root):
    return ExperimentCompletionCheck(WorkspaceObserver(WorkspaceBoundary(WorkspaceGrant(root=str(root), source=WorkspaceSourceKind.LOCAL, access=WorkspaceAccess(read_paths=['.'], write_paths=[])))))


def evidence(path="metrics.json", **extra):
    return ArtifactCandidate(
        kind="experiment_result", path=path, media_type="application/json",
        summary="Experimental measurements", **extra,
    )


def test_analysis_does_not_require_execution_or_new_evidence(tmp_path):
    decision = check(tmp_path).evaluate(state(), FinishCandidate(report="Existing results are inconclusive"))
    assert decision.complete
    assert decision.artifacts == []


@pytest.mark.parametrize("filename", ["metrics.json", "./metrics.json"])
def test_authorized_preexisting_file_can_be_analyzed(tmp_path, filename):
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.9}')
    decision = check(tmp_path).evaluate(state(), FinishCandidate(
        report="Analyzed the existing measurement", artifacts=[evidence(filename)],
    ))
    assert decision.complete
    assert decision.artifacts[0].path == filename


def test_report_preserves_limits_without_claiming_measurement(tmp_path):
    report = "Prior result lacks repeated seeds, so uncertainty is unknown."
    decision = check(tmp_path).evaluate(state(), FinishCandidate(
        report=report, artifacts=[evidence(content='{"analysis": "uncertainty unknown"}')],
    ))
    assert decision.complete
    assert decision.report == report
    assert decision.artifacts[0].content == '{"analysis": "uncertainty unknown"}'


def test_missing_file_is_not_delivered(tmp_path):
    with pytest.raises((OSError, PermissionError)):
        check(tmp_path).evaluate(state(), FinishCandidate(report="Done", artifacts=[evidence()]))


def test_output_directory_file_is_checked_without_requiring_writable_source(tmp_path):
    workspace = tmp_path / "source"
    output = tmp_path / "out"
    workspace.mkdir()
    output.mkdir()
    (output / "metrics.json").write_text('{"accuracy": 0.9}')
    finalizer = check(workspace)
    finalizer.output_dir = output
    candidate = FinishCandidate(report="Measured", artifacts=[evidence()])
    assert finalizer.evaluate(state(), candidate).complete
    (output / "metrics.json").unlink()
    (tmp_path / "outside.json").write_text("{}")
    (output / "metrics.json").symlink_to(tmp_path / "outside.json")
    with pytest.raises(PermissionError):
        finalizer.evaluate(state(), candidate)


def test_report_does_not_self_certify_command_failure(tmp_path):
    decision = check(tmp_path).evaluate(state(), FinishCandidate(report="It failed"))
    assert decision.complete
    assert decision.failure is None


@pytest.mark.parametrize("exit_code,timed_out", [(1, False), (-9, True)])
def test_failed_finish_preserves_verified_execution_error(tmp_path, exit_code, timed_out):
    current = state()
    current.events.append(AgentEvent(
        sequence=1, step=1, type="observation", tool="run_command", created_at=current.created_at,
        data={"ok": False, "value": {
            "command": "python train.py", "exit_code": exit_code, "timed_out": timed_out,
            "stdout_path": "out.stdout", "stderr_path": "out.stderr", "stderr_tail": "real error",
            "duration_seconds": 0.1,
        }},
    ))
    candidate = FinishCandidate(report="Training failed")
    decision = check(tmp_path).evaluate(current, candidate)
    assert decision.failure.code == ErrorCode.TOOL_FAILED
    assert decision.failure.details["stderr_tail"] == "real error"
    assert decision.failure.details["exit_code"] == exit_code
    assert not decision.complete


def test_unexecuted_failure_text_is_not_command_evidence(tmp_path):
    current = state()
    current.events.append(AgentEvent(
        sequence=1, step=1, type="observation", tool="run_command", created_at=current.created_at,
        data={"ok": False, "value": {"blocked": True, "reason": "No environment"}},
    ))
    assert check(tmp_path)._last_failed_command(current) is None
