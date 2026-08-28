from datetime import UTC, datetime
from pathlib import Path

from resagent2_contracts import (
    AgentOwner,
    ExperimentResult,
    WorkspaceGrant,
    WorkspaceMode,
    WorkspaceSource,
)
from resagent2_capabilities import WorkspaceBoundary
from resagent2_runtime import AgentState, FinishCandidate

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
            source=WorkspaceSource.EXISTING,
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


def _finish(*, metrics=None, evidence_files=None) -> FinishCandidate:
    return FinishCandidate(
        result={"summary": "done", "metrics": metrics or {}, "evidence_files": evidence_files or []}
    )


def test_golden_case_new_evidence_completes(tmp_path) -> None:
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.9}', encoding="utf-8")
    check = _check(tmp_path, expected_metrics=["accuracy"], expected_artifacts=["metrics.json"])

    decision = check.evaluate(
        _state(), _finish(metrics={"accuracy": 0.9}, evidence_files=["metrics.json"])
    )

    assert decision.complete is True
    assert decision.warnings == []
    payload = ExperimentResult.model_validate(decision.payload)
    assert payload.evidence_files == ["metrics.json"]
    assert payload.delivery_issues == []
    assert {artifact.kind for artifact in decision.artifacts} == {"experiment_result"}


def test_missing_metric_and_artifact_downgrades(tmp_path) -> None:
    check = _check(tmp_path, expected_metrics=["accuracy"], expected_artifacts=["metrics.json"])

    decision = check.evaluate(_state(), _finish())

    assert decision.complete is True
    assert len(decision.warnings) == 1
    message = decision.warnings[0].message
    assert "Missing required metric: accuracy" in message
    assert "Missing required artifact: metrics.json" in message
    assert decision.artifacts == []


def test_expected_artifact_is_added_to_evidence(tmp_path) -> None:
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.9}', encoding="utf-8")
    check = _check(tmp_path, expected_metrics=["accuracy"], expected_artifacts=["metrics.json"])

    decision = check.evaluate(
        _state(), _finish(metrics={"accuracy": 0.9}, evidence_files=[])
    )

    payload = ExperimentResult.model_validate(decision.payload)
    assert payload.evidence_files == ["metrics.json"]


def test_no_experiment_run_cannot_complete(tmp_path) -> None:
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.9}', encoding="utf-8")
    check = _check(tmp_path, expected_metrics=["accuracy"], expected_artifacts=["metrics.json"])
    state = _state({"experiment_success_count": 0, "workspace_baseline": {}})

    decision = check.evaluate(
        state, _finish(metrics={"accuracy": 0.9}, evidence_files=["metrics.json"])
    )

    assert decision.complete is False
    assert "experiment command" in decision.summary


def test_preexisting_unchanged_evidence_is_not_claimable(tmp_path) -> None:
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.9}', encoding="utf-8")
    baseline = snapshot_workspace(_boundary(tmp_path))
    check = _check(tmp_path, expected_metrics=["accuracy"], expected_artifacts=["metrics.json"])
    state = _state({"experiment_success_count": 1, "workspace_baseline": baseline})

    decision = check.evaluate(
        state, _finish(metrics={"accuracy": 0.9}, evidence_files=["metrics.json"])
    )

    assert decision.artifacts == []
    payload = ExperimentResult.model_validate(decision.payload)
    assert payload.evidence_files == []
    assert any("unchanged" in issue for issue in payload.delivery_issues)


def test_changed_evidence_file_completes(tmp_path) -> None:
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.5}', encoding="utf-8")
    baseline = snapshot_workspace(_boundary(tmp_path))
    # The current attempt updates the file.
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.9}', encoding="utf-8")
    check = _check(tmp_path, expected_metrics=["accuracy"], expected_artifacts=["metrics.json"])
    state = _state({"experiment_success_count": 1, "workspace_baseline": baseline})

    decision = check.evaluate(
        state, _finish(metrics={"accuracy": 0.9}, evidence_files=["metrics.json"])
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
        state, _finish(metrics={"accuracy": 0.9}, evidence_files=["metrics.json"])
    )

    assert decision.complete is False


def test_preexisting_evidence_cannot_be_claimed_via_path_alias(tmp_path) -> None:
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.9}', encoding="utf-8")
    baseline = snapshot_workspace(_boundary(tmp_path))
    check = _check(tmp_path, expected_metrics=["accuracy"], expected_artifacts=["metrics.json"])
    state = _state({"experiment_success_count": 1, "workspace_baseline": baseline})

    decision = check.evaluate(
        state, _finish(metrics={"accuracy": 0.9}, evidence_files=["./metrics.json"])
    )

    assert decision.artifacts == []
    payload = ExperimentResult.model_validate(decision.payload)
    assert payload.evidence_files == []
    assert any("unchanged" in issue for issue in payload.delivery_issues)


def test_missing_baseline_cannot_verify_evidence(tmp_path) -> None:
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.9}', encoding="utf-8")
    check = _check(tmp_path, expected_metrics=["accuracy"], expected_artifacts=["metrics.json"])
    state = _state({"experiment_success_count": 1})  # no workspace_baseline key

    decision = check.evaluate(
        state, _finish(metrics={"accuracy": 0.9}, evidence_files=["metrics.json"])
    )

    assert decision.complete is False
    assert "baseline" in decision.summary
