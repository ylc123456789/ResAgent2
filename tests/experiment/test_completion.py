from datetime import UTC, datetime
from pathlib import Path

from resagent2_contracts import (
    AgentOwner,
    ExperimentResult,
    WorkspaceGrant,
    WorkspaceMode,
    WorkspaceSource,
)
from resagent2_runtime import AgentState, FinishCandidate, WorkspaceBoundary

from resagent2_experiment.completion import ExperimentCompletionCheck


def _state() -> AgentState:
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
    )


def _check(root: Path, *, expected_metrics=None, expected_artifacts=None) -> ExperimentCompletionCheck:
    boundary = WorkspaceBoundary(
        WorkspaceGrant(
            root=str(root),
            mode=WorkspaceMode.READ_WRITE,
            allowed_paths=["."],
            source=WorkspaceSource.EXISTING,
        )
    )
    return ExperimentCompletionCheck(
        boundary,
        expected_metrics=expected_metrics or [],
        expected_artifacts=expected_artifacts or [],
        env_id="resenv_x",
        repo_url="https://example.com/repo.git",
        commit="abc",
    )


def test_delivery_golden_case_completes_with_evidence(tmp_path) -> None:
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.9}', encoding="utf-8")
    check = _check(tmp_path, expected_metrics=["accuracy"], expected_artifacts=["metrics.json"])
    candidate = FinishCandidate(
        result={
            "summary": "trained and evaluated",
            "metrics": {"accuracy": 0.9},
            "evidence_files": ["metrics.json"],
        }
    )

    decision = check.evaluate(_state(), candidate)

    assert decision.complete is True
    assert decision.warnings == []
    payload = ExperimentResult.model_validate(decision.payload)
    assert payload.metrics == {"accuracy": 0.9}
    assert payload.evidence_files == ["metrics.json"]
    assert payload.delivery_issues == []
    assert {artifact.kind for artifact in decision.artifacts} == {"experiment_result"}


def test_missing_metric_and_artifact_downgrades_with_not_met(tmp_path) -> None:
    check = _check(tmp_path, expected_metrics=["accuracy"], expected_artifacts=["metrics.json"])
    candidate = FinishCandidate(
        result={"summary": "ran something", "metrics": {}, "evidence_files": []}
    )

    decision = check.evaluate(_state(), candidate)

    assert decision.complete is True
    assert len(decision.warnings) == 1
    message = decision.warnings[0].message
    assert "Missing required metric: accuracy" in message
    assert "Missing required artifact: metrics.json" in message
    payload = ExperimentResult.model_validate(decision.payload)
    assert payload.delivery_issues
    assert decision.artifacts == []


def test_expected_artifact_is_added_to_evidence(tmp_path) -> None:
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.9}', encoding="utf-8")
    check = _check(tmp_path, expected_metrics=["accuracy"], expected_artifacts=["metrics.json"])
    candidate = FinishCandidate(
        result={"summary": "done", "metrics": {"accuracy": 0.9}, "evidence_files": []}
    )

    decision = check.evaluate(_state(), candidate)

    payload = ExperimentResult.model_validate(decision.payload)
    assert payload.evidence_files == ["metrics.json"]
