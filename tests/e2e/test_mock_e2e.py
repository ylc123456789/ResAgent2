from resagent2_contracts import Capability, RunStatus

from e2e.mock_e2e import run_mock_e2e


def test_mock_e2e_runs_the_golden_loop_to_completion(tmp_path) -> None:
    run = run_mock_e2e(workdir=tmp_path)

    assert run.status == RunStatus.COMPLETED
    assert [task.capability for task in run.workflow.tasks] == [
        Capability.CODE_MODIFY,
        Capability.EXPERIMENT_RUN,
        Capability.SCIENTIFIC_ANALYZE,
    ]
    assert len(run.artifacts) == 3
    assert all(task.attempts for task in run.workflow.tasks)
