import pytest
from pydantic import ValidationError

from resagent2_contracts import (
    ExperimentResult,
    ExperimentRunInput,
)


def test_experiment_run_input_round_trips_fields() -> None:
    inputs = ExperimentRunInput(
        instructions="Run train.py",
        expected_metrics=["accuracy"],
        expected_artifacts=["metrics.json"],
        confirm_before_experiment=True,
    )

    restored = ExperimentRunInput.model_validate_json(inputs.model_dump_json())

    assert restored == inputs
    assert restored.confirm_before_experiment is True


def test_experiment_run_input_rejects_repository_source_fields() -> None:
    # The repository source moved to the unified workspace context
    # (ModuleTaskRequest.workspace_spec); it is no longer an input field.
    with pytest.raises(ValidationError):
        ExperimentRunInput(
            instructions="Run train.py",
            repository_url="https://example.com/repo.git",
        )


def test_experiment_result_round_trips() -> None:
    result = ExperimentResult(
        metrics={"accuracy": 0.9},
        evidence_files=["metrics.json"],
        repo_url="https://example.com/repo.git",
        commit="abc123",
        env_id="resenv_repo_0123456789ab",
        delivery_issues=["Missing required metric: f1"],
    )

    restored = ExperimentResult.model_validate_json(result.model_dump_json())

    assert restored == result
    assert restored.metrics == {"accuracy": 0.9}
    assert restored.delivery_issues == ["Missing required metric: f1"]


def test_experiment_result_evidence_paths_remain_relative() -> None:
    with pytest.raises(ValidationError, match="relative"):
        ExperimentResult(
            env_id="resenv_repo_0123456789ab",
            evidence_files=["../secret.txt"],
        )
