from datetime import UTC, datetime
from pathlib import Path

from resagent2_contracts import (
    AgentOwner,
    ErrorCode,
    ExperimentResult,
    WorkspaceGrant,
    WorkspaceMode,
    WorkspaceSourceKind,
)
from resagent2_capabilities import WorkspaceBoundary
from resagent2_runtime import AgentEvent, AgentState, FinishCandidate

from resagent2_experiment.completion import ExperimentCompletionCheck, snapshot_workspace


def _state(memory=None) -> AgentState:
    now = datetime.now(UTC)
    return AgentState(
        session_id="session_test",
        agent_name="experiment-run",
        owner=AgentOwner.EXPERIMENT,
        run_id="run_test",
        task_id="task_test",
        attempt_number=1,
        created_at=now,
        updated_at=now,
        memory=memory or {"experiment_success_count": 1, "workspace_baseline": {}},
    )


def _boundary(root: Path) -> WorkspaceBoundary:
    return WorkspaceBoundary(
        WorkspaceGrant(
            root=str(root),
            mode=WorkspaceMode.READ_WRITE,
            allowed_paths=["."],
            source=WorkspaceSourceKind.LOCAL,
        )
    )


def _check(root: Path, *, expected_metrics=None, expected_artifacts=None) -> ExperimentCompletionCheck:
    return ExperimentCompletionCheck(
        _boundary(root),
        expected_metrics=expected_metrics or [],
        expected_artifacts=expected_artifacts or [],
        env_id="resenv_x",
        repo_url="https://example.com/repo.git",
        commit="abc",
    )


def _finish(*, evidence_files=None) -> FinishCandidate:
    return FinishCandidate(
        result={"summary": "done", "evidence_files": evidence_files or []}
    )


def test_golden_case_new_evidence_completes(tmp_path) -> None:
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.9}', encoding="utf-8")
    check = _check(tmp_path, expected_metrics=["accuracy"], expected_artifacts=["metrics.json"])

    decision = check.evaluate(
        _state(), _finish(evidence_files=["metrics.json"])
    )

    assert decision.complete is True
    assert decision.warnings == []
    payload = ExperimentResult.model_validate(decision.payload)
    assert payload.evidence_files == ["metrics.json"]
    assert payload.delivery_issues == []
    assert {artifact.kind for artifact in decision.artifacts} == {"experiment_result"}


def test_missing_all_evidence_is_rejected(tmp_path) -> None:
    check = _check(tmp_path, expected_metrics=["accuracy"], expected_artifacts=["metrics.json"])

    decision = check.evaluate(_state(), _finish())

    # No metric and no artifact at all: reject instead of completing with
    # warnings that would mask a total failure.
    assert decision.complete is False
    assert "No required metric or artifact" in decision.summary


def test_partial_delivery_downgrades_to_warnings(tmp_path) -> None:
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.9}', encoding="utf-8")
    baseline = snapshot_workspace(_boundary(tmp_path))
    # This attempt changes metrics.json (so the metric is derivable from
    # evidence) but never produces the second required artifact.
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.95}', encoding="utf-8")
    check = _check(
        tmp_path,
        expected_metrics=["accuracy"],
        expected_artifacts=["metrics.json", "report.json"],
    )
    state = _state({"experiment_success_count": 1, "workspace_baseline": baseline})

    decision = check.evaluate(state, _finish(evidence_files=["metrics.json"]))

    assert decision.complete is True
    assert len(decision.warnings) == 1
    assert "report.json" in decision.warnings[0].message


def test_expected_artifact_is_added_to_evidence(tmp_path) -> None:
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.9}', encoding="utf-8")
    check = _check(tmp_path, expected_metrics=["accuracy"], expected_artifacts=["metrics.json"])

    decision = check.evaluate(
        _state(), _finish(evidence_files=[])
    )

    payload = ExperimentResult.model_validate(decision.payload)
    assert payload.evidence_files == ["metrics.json"]


def test_no_experiment_run_cannot_complete(tmp_path) -> None:
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.9}', encoding="utf-8")
    check = _check(tmp_path, expected_metrics=["accuracy"], expected_artifacts=["metrics.json"])
    state = _state({"experiment_success_count": 0, "workspace_baseline": {}})

    decision = check.evaluate(
        state, _finish(evidence_files=["metrics.json"])
    )

    assert decision.complete is False
    assert "experiment command" in decision.summary


def test_preexisting_unchanged_evidence_is_not_claimable(tmp_path) -> None:
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.9}', encoding="utf-8")
    baseline = snapshot_workspace(_boundary(tmp_path))
    check = _check(tmp_path, expected_metrics=["accuracy"], expected_artifacts=["metrics.json"])
    state = _state({"experiment_success_count": 1, "workspace_baseline": baseline})

    decision = check.evaluate(
        state, _finish(evidence_files=["metrics.json"])
    )

    # Unchanged evidence yields no derivable metric, so it cannot be claimed;
    # the run is rejected rather than completed with an empty claim.
    assert decision.complete is False
    assert "No required metric or artifact" in decision.summary


def test_changed_evidence_file_completes(tmp_path) -> None:
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.5}', encoding="utf-8")
    baseline = snapshot_workspace(_boundary(tmp_path))
    # The current attempt updates the file.
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.9}', encoding="utf-8")
    check = _check(tmp_path, expected_metrics=["accuracy"], expected_artifacts=["metrics.json"])
    state = _state({"experiment_success_count": 1, "workspace_baseline": baseline})

    decision = check.evaluate(
        state, _finish(evidence_files=["metrics.json"])
    )

    assert decision.complete is True
    assert decision.warnings == []
    assert {artifact.kind for artifact in decision.artifacts} == {"experiment_result"}


def test_leftover_from_previous_attempt_is_not_claimable(tmp_path) -> None:
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.9}', encoding="utf-8")
    baseline = snapshot_workspace(_boundary(tmp_path))
    check = _check(tmp_path, expected_metrics=["accuracy"], expected_artifacts=["metrics.json"])
    state = _state({"experiment_success_count": 0, "workspace_baseline": baseline})

    decision = check.evaluate(
        state, _finish(evidence_files=["metrics.json"])
    )

    assert decision.complete is False


def test_preexisting_evidence_cannot_be_claimed_via_path_alias(tmp_path) -> None:
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.9}', encoding="utf-8")
    baseline = snapshot_workspace(_boundary(tmp_path))
    check = _check(tmp_path, expected_metrics=["accuracy"], expected_artifacts=["metrics.json"])
    state = _state({"experiment_success_count": 1, "workspace_baseline": baseline})

    decision = check.evaluate(
        state, _finish(evidence_files=["./metrics.json"])
    )

    assert decision.complete is False
    assert "No required metric or artifact" in decision.summary


def test_missing_baseline_cannot_verify_evidence(tmp_path) -> None:
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.9}', encoding="utf-8")
    check = _check(tmp_path, expected_metrics=["accuracy"], expected_artifacts=["metrics.json"])
    state = _state({"experiment_success_count": 1})  # no workspace_baseline key

    decision = check.evaluate(
        state, _finish(evidence_files=["metrics.json"])
    )

    assert decision.complete is False
    assert "baseline" in decision.summary


def _failed_command_event() -> AgentEvent:
    return AgentEvent(
        sequence=1,
        step=1,
        type="observation",
        tool="run_command",
        data={
            "summary": "Command exited with code 1",
            "ok": False,
            "value": {
                "command": "python train.py",
                "exit_code": 1,
                "timed_out": False,
                "stdout_path": ".resagent2/experiment/commands/1.stdout.log",
                "stderr_path": ".resagent2/experiment/commands/1.stderr.log",
                "stderr_tail": "NameError: name 'totla' is not defined",
            },
        },
        created_at=datetime.now(UTC),
    )


def test_failed_finish_with_verified_command_returns_failure(tmp_path) -> None:
    check = _check(
        tmp_path, expected_metrics=["accuracy"], expected_artifacts=["metrics.json"]
    )
    state = _state({"experiment_success_count": 0, "workspace_baseline": {}})
    state.events.append(_failed_command_event())

    decision = check.evaluate(
        state,
        FinishCandidate(proposed_status="failed", result={"summary": "train.py failed"}),
    )

    assert decision.complete is False
    assert decision.failure is not None
    assert decision.failure.code == ErrorCode.TOOL_FAILED
    assert decision.failure.retryable is False
    assert decision.failure.details["command"] == "python train.py"
    assert decision.failure.details["exit_code"] == 1
    assert decision.failure.details["stderr_path"].endswith("1.stderr.log")
    assert decision.failure.details["stderr_tail"] == "NameError: name 'totla' is not defined"


def test_failed_finish_without_evidence_is_rejected(tmp_path) -> None:
    check = _check(tmp_path)
    state = _state({"experiment_success_count": 0, "workspace_baseline": {}})

    decision = check.evaluate(
        state,
        FinishCandidate(proposed_status="failed", result={"summary": "I think it failed"}),
    )

    assert decision.complete is False
    assert decision.failure is None
    assert "no failed experiment command" in decision.summary
